from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt5.QtCore import QStandardPaths, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QMouseEvent, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from xtalflow.application import ReviewController, ReviewPersistenceError
from xtalflow.domain import (
    PlateImages,
    ReviewPreferences,
    ReviewProgress,
    ReviewSession,
    TargetPoint,
)
from xtalflow.infrastructure import (
    PlateImagesNotFoundError,
    RockMakerImageRepository,
    SQLiteReviewStore,
)
from xtalflow.presentation import AspectFitTransform


class ImageCanvas(QWidget):
    image_clicked = pyqtSignal(float, float, int)
    previous_requested = pyqtSignal()
    next_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._targets: tuple[TargetPoint, ...] = ()
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.StrongFocus)
        self._previous_shortcut = QShortcut(QKeySequence("Left"), self)
        self._next_shortcut = QShortcut(QKeySequence("Right"), self)
        self._previous_shortcut.setContext(Qt.WidgetShortcut)
        self._next_shortcut.setContext(Qt.WidgetShortcut)
        self._previous_shortcut.activated.connect(self.previous_requested)
        self._next_shortcut.activated.connect(self.next_requested)

    def set_image(self, pixmap: QPixmap, targets: tuple[TargetPoint, ...]) -> None:
        self._pixmap = pixmap
        self._targets = targets
        self.update()

    def set_targets(self, targets: tuple[TargetPoint, ...]) -> None:
        self._targets = targets
        self.update()

    def pixmap(self) -> QPixmap:
        return self._pixmap

    def transform(self) -> AspectFitTransform | None:
        if self._pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return None
        return AspectFitTransform(
            self._pixmap.width(), self._pixmap.height(), self.width(), self.height()
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#15181c"))
        transform = self.transform()
        if transform is None:
            painter.setPen(QColor("#d8dee9"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Enter a plate code")
            return

        target_rect = self.rect()
        target_rect.setX(round(transform.offset_x))
        target_rect.setY(round(transform.offset_y))
        target_rect.setWidth(round(transform.rendered_width))
        target_rect.setHeight(round(transform.rendered_height))
        painter.drawPixmap(target_rect, self._pixmap)

        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#ff4057"), 2))
        for target in self._targets:
            x, y = transform.image_to_viewport(target.x_px, target.y_px)
            painter.drawEllipse(round(x) - 7, round(y) - 7, 14, 14)
            painter.drawLine(round(x) - 11, round(y), round(x) + 11, round(y))
            painter.drawLine(round(x), round(y) - 11, round(x), round(y) + 11)

        if self.hasFocus():
            painter.setPen(QPen(QColor("#4da3ff"), 3))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self.setFocus(Qt.MouseFocusReason)
        transform = self.transform()
        if transform is None:
            return
        image_point = transform.viewport_to_image(event.x(), event.y())
        if image_point is not None:
            self.image_clicked.emit(*image_point, int(event.button()))

class ViewerWindow(QMainWindow):
    def __init__(
        self,
        repository: RockMakerImageRepository,
        review_store: SQLiteReviewStore | None = None,
        auto_advance_target_count: int = 1,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.review_store = review_store
        self.plate: PlateImages | None = None
        self.controller: ReviewController | None = None
        self._initial_auto_advance_target_count = auto_advance_target_count
        self.setWindowTitle("XtalFlow Viewer")
        self.resize(1100, 850)

        self.plate_input = QLineEdit()
        self.plate_input.setPlaceholderText("Plate code")
        self.load_button = QPushButton("Load")
        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.auto_advance_input = QSpinBox()
        self.auto_advance_input.setRange(1, 100)
        self.auto_advance_input.setValue(auto_advance_target_count)
        self.auto_advance_input.setPrefix("Auto-next at: ")
        self.image_canvas = ImageCanvas()
        self.navigation_label = QLabel("No plate loaded")
        self.review_summary_label = QLabel(
            "Click image to focus · Left add · Right remove · ←/→ navigate"
        )
        self.save_status_label = QLabel("Not loaded")

        controls = QHBoxLayout()
        controls.addWidget(self.plate_input)
        controls.addWidget(self.load_button)
        controls.addWidget(self.auto_advance_input)
        controls.addStretch()
        controls.addWidget(self.previous_button)
        controls.addWidget(self.next_button)

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self.image_canvas, 1)
        layout.addWidget(self.navigation_label)
        layout.addWidget(self.review_summary_label)
        layout.addWidget(self.save_status_label)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.load_button.clicked.connect(self.load_entered_plate)
        self.plate_input.returnPressed.connect(self.load_entered_plate)
        self.previous_button.clicked.connect(self.show_previous)
        self.next_button.clicked.connect(self.show_next)
        self.image_canvas.image_clicked.connect(self._handle_image_click)
        self.image_canvas.previous_requested.connect(self.show_previous)
        self.image_canvas.next_requested.connect(self.show_next)
        self.auto_advance_input.valueChanged.connect(self._change_auto_advance_target_count)
        self._update_navigation()

    def load_entered_plate(self) -> None:
        try:
            self.load_plate(self.plate_input.text())
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot load plate", str(error))

    def load_plate(self, plate_code: str, profile: str = "profileID_1") -> None:
        if self.controller is not None:
            self.controller.checkpoint_current()
        self.plate = self.repository.load_plate(plate_code, profile)
        session = ReviewSession()
        if self.review_store is not None:
            image_keys = tuple(image.image_key for image in self.plate.images)
            session.restore_targets(self.review_store.load_images(image_keys))
        plan_key = f"{self.plate.plate_code}:{self.plate.batch_id}:{self.plate.profile}"
        state = (
            self.review_store.load_review_state(plan_key)
            if self.review_store is not None
            else None
        )
        if state is None:
            progress = ReviewProgress.create(
                self.plate.plate_code,
                self.plate.batch_id,
                self.plate.profile,
                self.plate.images[0].image_key,
            )
            preferences = ReviewPreferences(self._initial_auto_advance_target_count)
        else:
            progress, preferences = state
        self.controller = ReviewController(
            self.plate, progress, preferences, session, self.review_store
        )
        self.auto_advance_input.blockSignals(True)
        self.auto_advance_input.setValue(
            self.controller.preferences.auto_advance_target_count
        )
        self.auto_advance_input.blockSignals(False)
        self.controller.persist_state()
        self._show_current_image()
        self._set_save_status("saved")
        self.image_canvas.setFocus(Qt.OtherFocusReason)

    def show_previous(self) -> None:
        if self.controller is None or not self.controller.can_move_previous:
            return
        self._move(self.controller.move_previous)

    def show_next(self) -> None:
        if self.controller is None or not self.controller.can_move_next:
            return
        self._move(self.controller.move_next)

    def _move(self, move_command: Callable[[], bool]) -> None:
        if self.controller is None:
            return
        try:
            moved = move_command()
        except ReviewPersistenceError as error:
            self._show_persistence_error(error)
            return
        if not moved:
            return
        self.statusBar().showMessage("Review checkpoint saved", 2000)
        self._show_current_image()
        self._set_save_status("saved")

    def _show_current_image(self) -> None:
        if self.controller is None:
            return
        image = self.controller.current_image
        pixmap = QPixmap(str(image.path))
        if pixmap.isNull():
            raise ValueError(f"invalid image: {image.path}")
        targets = self.controller.current_targets
        self.image_canvas.set_image(pixmap, targets)
        self.navigation_label.setText(
            f"{image.navigation_label} · Batch {image.batch_id} · "
            f"{self.controller.image_index + 1}/{len(self.controller.plate.images)}"
        )
        self._update_review_summary()
        self._update_navigation()

    def _handle_image_click(self, x_px: float, y_px: float, button: int) -> None:
        if self.controller is None:
            return
        if button == Qt.LeftButton:
            pixmap = self.image_canvas.pixmap()
            should_auto_advance = self.controller.add_target(
                x_px, y_px, pixmap.width(), pixmap.height()
            )
        elif button == Qt.RightButton:
            should_auto_advance = False
            transform = self.image_canvas.transform()
            if transform is not None:
                self.controller.remove_nearest_target(x_px, y_px, 18 / transform.scale)
        else:
            return
        self.image_canvas.set_targets(self.controller.current_targets)
        self._update_review_summary()
        if self.controller.has_unsaved_changes:
            self._set_save_status("unsaved")
        if should_auto_advance:
            if self.controller.can_move_next:
                self.show_next()
            else:
                try:
                    self.controller.checkpoint_current()
                    self.statusBar().showMessage(
                        "Review complete; final image saved", 5000
                    )
                    self._set_save_status("saved")
                except ReviewPersistenceError as error:
                    self._show_persistence_error(error)

    def _change_auto_advance_target_count(self, count: int) -> None:
        if self.controller is None:
            return
        try:
            self.controller.change_auto_advance_target_count(count)
        except ReviewPersistenceError as error:
            self.auto_advance_input.blockSignals(True)
            self.auto_advance_input.setValue(
                self.controller.preferences.auto_advance_target_count
            )
            self.auto_advance_input.blockSignals(False)
            self._show_persistence_error(error)
            return
        self._update_review_summary()

    def _update_review_summary(self) -> None:
        if self.controller is None:
            return
        image_count = len(self.controller.current_targets)
        auto_advance_count = self.controller.preferences.auto_advance_target_count
        self.review_summary_label.setText(
            f"Selected: {image_count} · Auto-next at: {auto_advance_count} · Session total: "
            f"{self.controller.session.target_count} · Click image, then ←/→ to navigate"
        )

    def _update_navigation(self) -> None:
        self.previous_button.setEnabled(
            self.controller is not None and self.controller.can_move_previous
        )
        self.next_button.setEnabled(
            self.controller is not None and self.controller.can_move_next
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        while self.controller is not None:
            try:
                self.controller.checkpoint_current()
                self._set_save_status("saved")
                break
            except ReviewPersistenceError as error:
                self._set_save_status("failed")
                answer = QMessageBox.warning(
                    self,
                    "Review was not saved",
                    f"{error}\n\nRetry, close without saving, or cancel closing?",
                    QMessageBox.Retry | QMessageBox.Discard | QMessageBox.Cancel,
                    QMessageBox.Retry,
                )
                if answer == QMessageBox.Retry:
                    continue
                if answer == QMessageBox.Cancel:
                    event.ignore()
                    return
                break
        if self.review_store is not None:
            self.review_store.close()
        super().closeEvent(event)

    def _show_persistence_error(self, error: ReviewPersistenceError) -> None:
        self._set_save_status("failed")
        self.statusBar().showMessage(f"Not saved: {error}")
        QMessageBox.warning(self, "Review was not saved", str(error))

    def _set_save_status(self, state: str) -> None:
        styles = {
            "saved": ("Saved", "#2e7d32"),
            "unsaved": ("Unsaved changes", "#ad6800"),
            "failed": ("Save failed", "#c62828"),
        }
        text, color = styles[state]
        self.save_status_label.setText(text)
        self.save_status_label.setStyleSheet(f"font-weight: 600; color: {color};")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review crystal images from RMServer")
    parser.add_argument("--root", type=Path, required=True, help="RMServer image root")
    parser.add_argument("--plate", help="Plate code to load at startup")
    parser.add_argument(
        "--review-db",
        type=Path,
        help="SQLite review database (default: application data directory)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = QApplication.instance() or QApplication(
        sys.argv if argv is None else [sys.argv[0], *argv]
    )
    app.setApplicationName("XtalFlow")
    app.setOrganizationName("XtalFlow")
    database_path = args.review_db or (
        Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        / "reviews.sqlite3"
    )
    try:
        review_store = SQLiteReviewStore(database_path)
    except ReviewPersistenceError as error:
        print(f"xtalflow-viewer: {error}", file=sys.stderr)
        return 2
    window = ViewerWindow(RockMakerImageRepository(args.root), review_store)
    if args.plate:
        window.plate_input.setText(args.plate)
        try:
            window.load_plate(args.plate)
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            print(f"xtalflow-viewer: {error}", file=sys.stderr)
            window.close()
            return 2
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
