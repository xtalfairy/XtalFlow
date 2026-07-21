from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt5.QtCore import QRectF, QStandardPaths, QStringListModel, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QMouseEvent, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from xtalflow.application import (
    CalibrationDetectionError,
    ProjectController,
    ReviewController,
    ReviewPersistenceError,
    WellCalibrationService,
)
from xtalflow.domain import (
    PLATE_FORMATS,
    ImageCalibration,
    ImageFilter,
    PlateFormat,
    PlateImages,
    TargetPoint,
    plate_format_by_id,
)
from xtalflow.infrastructure import (
    PlateImagesNotFoundError,
    OpenCVWellDetector,
    RockMakerImageRepository,
    SQLiteReviewStore,
)
from xtalflow.presentation import AspectFitTransform, ProjectImageSetListModel


class ImageCanvas(QWidget):
    image_clicked = pyqtSignal(float, float, int)
    previous_requested = pyqtSignal()
    next_requested = pyqtSignal()
    previous_plate_requested = pyqtSignal()
    next_plate_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._targets: tuple[TargetPoint, ...] = ()
        self._calibration: ImageCalibration | None = None
        self._calibration_points: tuple[tuple[float, float], ...] = ()
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.StrongFocus)
        self._previous_shortcut = QShortcut(QKeySequence("Left"), self)
        self._next_shortcut = QShortcut(QKeySequence("Right"), self)
        self._previous_plate_shortcut = QShortcut(QKeySequence("Up"), self)
        self._next_plate_shortcut = QShortcut(QKeySequence("Down"), self)
        self._previous_shortcut.setContext(Qt.WidgetShortcut)
        self._next_shortcut.setContext(Qt.WidgetShortcut)
        self._previous_plate_shortcut.setContext(Qt.WidgetShortcut)
        self._next_plate_shortcut.setContext(Qt.WidgetShortcut)
        self._previous_shortcut.activated.connect(self.previous_requested)
        self._next_shortcut.activated.connect(self.next_requested)
        self._previous_plate_shortcut.activated.connect(self.previous_plate_requested)
        self._next_plate_shortcut.activated.connect(self.next_plate_requested)


    def set_image(self, pixmap: QPixmap, targets: tuple[TargetPoint, ...]) -> None:
        self._pixmap = pixmap
        self._targets = targets
        self.update()

    def set_targets(self, targets: tuple[TargetPoint, ...]) -> None:
        self._targets = targets
        self.update()

    def set_calibration(self, calibration: ImageCalibration | None) -> None:
        self._calibration = calibration
        self.update()

    def set_calibration_points(self, points: tuple[tuple[float, float], ...]) -> None:
        self._calibration_points = points
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

        if self._calibration is not None:
            calibration = self._calibration
            center_x, center_y = transform.image_to_viewport(
                calibration.center_x_px, calibration.center_y_px
            )
            radius_x = calibration.radius_x_px * transform.scale
            radius_y = calibration.radius_y_px * transform.scale
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#36d17c"), 2))
            painter.drawEllipse(
                QRectF(
                    center_x - radius_x,
                    center_y - radius_y,
                    radius_x * 2,
                    radius_y * 2,
                )
            )
            painter.drawLine(round(center_x) - 14, round(center_y), round(center_x) + 14, round(center_y))
            painter.drawLine(round(center_x), round(center_y) - 14, round(center_x), round(center_y) + 14)

        painter.setPen(QPen(QColor("#ffd43b"), 3))
        for point_x, point_y in self._calibration_points:
            x, y = transform.image_to_viewport(point_x, point_y)
            painter.drawEllipse(round(x) - 5, round(y) - 5, 10, 10)

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


class ImagePathStatusLabel(QLabel):
    """Permanent status item for the current source image."""

    def __init__(self) -> None:
        super().__init__("Image: —")
        self._full_text = "Image: —"
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setToolTip("No image loaded")
        self.setMinimumWidth(220)

    def set_image_path(self, path: Path | None) -> None:
        if path is None:
            self._full_text = "Image: —"
            self.setToolTip("No image loaded")
        else:
            absolute_path = str(path.resolve())
            self._full_text = f"Image: {absolute_path}"
            self.setToolTip(absolute_path)
        self._refresh_text()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        available_width = max(self.width() - 8, 80)
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.ElideLeft, available_width
            )
        )


class StatusMessageLabel(QLabel):
    """Fixed-width area for transient and persistent operational messages."""

    def __init__(self, width: int = 280) -> None:
        super().__init__()
        self._full_text = ""
        self.setFixedWidth(width)
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(lambda: self.show_message(""))

    def show_message(self, message: str, timeout_ms: int = 0) -> None:
        self._clear_timer.stop()
        self._full_text = message
        self.setToolTip(message)
        self._refresh_text()
        if message and timeout_ms > 0:
            self._clear_timer.start(timeout_ms)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.ElideRight, max(self.width() - 8, 80)
            )
        )


class ImageSetListView(QListView):
    previous_plate_requested = pyqtSignal()
    next_plate_requested = pyqtSignal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key_Up:
            self.previous_plate_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            self.next_plate_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

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
        self.project_controller = ProjectController(
            repository, review_store, auto_advance_target_count
        )
        self.plate: PlateImages | None = None
        self.controller: ReviewController | None = None
        self.calibration_service: WellCalibrationService | None = None
        self.current_calibration: ImageCalibration | None = None
        self._manual_calibration_points: list[tuple[float, float]] | None = None
        self._initial_auto_advance_target_count = auto_advance_target_count
        self.setWindowTitle("XtalFlow Viewer")
        self.resize(1100, 850)

        self.project_selector = QComboBox()
        self.new_project_button = QPushButton("New Project")
        self.rename_project_button = QPushButton("Rename")
        self.plate_input = QLineEdit()
        self.plate_input.setPlaceholderText("Plate codes (e.g. 1070, 1100, 2070)")
        self.plate_format_input = QComboBox()
        self.plate_format_input.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.plate_format_input.setMinimumContentsLength(12)
        self.plate_format_input.addItem("Select plate format…", None)
        for plate_format in PLATE_FORMATS:
            self.plate_format_input.addItem(plate_format.display_name, plate_format)
        self.load_button = QPushButton("Load")
        self.previous_button = QPushButton("◀")
        self.previous_button.setFixedWidth(36)
        self.previous_button.setToolTip("Previous image (Left Arrow)")
        self.previous_button.setAccessibleName("Previous image")
        self.next_button = QPushButton("▶")
        self.next_button.setFixedWidth(36)
        self.next_button.setToolTip("Next image (Right Arrow)")
        self.next_button.setAccessibleName("Next image")
        self.image_filter_input = QComboBox()
        self.image_filter_input.addItem("All images", ImageFilter.ALL)
        self.image_filter_input.addItem("With targets", ImageFilter.WITH_TARGETS)
        self.image_filter_input.addItem("Reviewed, no targets", ImageFilter.WITHOUT_TARGETS)
        self.image_filter_input.addItem("Unreviewed", ImageFilter.UNREVIEWED)
        self.well_input = QLineEdit()
        self.well_input.setMinimumWidth(90)
        self.well_input.setPlaceholderText("A01a")
        self.well_input.setToolTip(
            "Enter a visible subwell address and press Enter. Esc restores the current address."
        )
        self.well_completion_model = QStringListModel(self)
        self.well_completer = QCompleter(self.well_completion_model, self)
        self.well_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.well_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.well_input.setCompleter(self.well_completer)
        self._well_escape_shortcut = QShortcut(QKeySequence("Escape"), self.well_input)
        self._well_escape_shortcut.setContext(Qt.WidgetShortcut)
        self._well_escape_shortcut.activated.connect(self._restore_current_well_address)
        self._well_destinations: dict[str, tuple[str, int]] = {}
        self._current_well_address = ""
        self.auto_advance_input = QSpinBox()
        self.auto_advance_input.setRange(1, 100)
        self.auto_advance_input.setValue(auto_advance_target_count)
        self.auto_advance_input.setPrefix("Targets/img: ")
        self.auto_advance_input.setToolTip(
            "Automatically move to the next image after selecting this many targets. "
            "This is not a required target count."
        )
        self.image_canvas = ImageCanvas()
        self.navigation_label = QLabel("No plate loaded")
        self.review_summary_label = QLabel(
            "Click image to focus · Left add · Right remove · ←/→ navigate"
        )
        self.save_status_label = QLabel("Not loaded")
        self.image_set_list = ImageSetListView()
        self.image_set_list.setMinimumWidth(220)
        self.image_set_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_set_model = ProjectImageSetListModel(self._target_count_for_image_set)
        self.image_set_list.setModel(self.image_set_model)
        self.move_image_set_up_button = QPushButton("Up")
        self.move_image_set_down_button = QPushButton("Down")
        self.archive_image_set_button = QPushButton("Remove")
        self.restore_image_set_button = QPushButton("Restore")
        self.project_progress_label = QLabel("Project: no images")
        self.calibration_label = QLabel("Well calibration: not loaded")
        self.auto_calibration_button = QPushButton("Auto Well")
        self.manual_calibration_button = QPushButton("Set Well (3 points)")
        self.status_message_label = StatusMessageLabel()
        self.image_path_status = ImagePathStatusLabel()

        project_controls = QHBoxLayout()
        project_controls.addWidget(QLabel("Project:"))
        project_controls.addWidget(self.project_selector, 1)
        project_controls.addWidget(self.new_project_button)
        project_controls.addWidget(self.rename_project_button)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        controls.addWidget(self.auto_advance_input)
        controls.addWidget(self.image_filter_input)
        controls.addStretch()
        navigation_controls = QHBoxLayout()
        navigation_controls.setSpacing(6)
        navigation_controls.addWidget(QLabel("Well:"))
        navigation_controls.addWidget(self.well_input)
        navigation_controls.addWidget(self.previous_button)
        navigation_controls.addWidget(self.next_button)
        controls.addLayout(navigation_controls)

        viewer_layout = QVBoxLayout()
        viewer_layout.addLayout(controls)
        viewer_layout.addWidget(self.image_canvas, 1)
        viewer_layout.addWidget(self.navigation_label)
        viewer_layout.addWidget(self.review_summary_label)
        calibration_controls = QHBoxLayout()
        calibration_controls.addWidget(self.calibration_label, 1)
        calibration_controls.addWidget(self.auto_calibration_button)
        calibration_controls.addWidget(self.manual_calibration_button)
        viewer_layout.addLayout(calibration_controls)
        viewer_panel = QWidget()
        viewer_panel.setLayout(viewer_layout)

        sidebar_layout = QVBoxLayout()
        plate_type_row = QHBoxLayout()
        plate_type_row.addWidget(QLabel("Plate type"))
        plate_type_row.addWidget(self.plate_format_input, 1)
        sidebar_layout.addLayout(plate_type_row)
        plate_load_row = QHBoxLayout()
        plate_load_row.addWidget(QLabel("Plate codes"))
        plate_load_row.addWidget(self.plate_input, 1)
        plate_load_row.addWidget(self.load_button)
        sidebar_layout.addLayout(plate_load_row)
        sidebar_layout.addWidget(self.image_set_list, 1)
        sidebar_layout.addWidget(self.project_progress_label)
        image_set_actions = QHBoxLayout()
        image_set_actions.addWidget(self.move_image_set_up_button)
        image_set_actions.addWidget(self.move_image_set_down_button)
        image_set_actions.addWidget(self.archive_image_set_button)
        image_set_actions.addWidget(self.restore_image_set_button)
        sidebar_layout.addLayout(image_set_actions)
        sidebar = QWidget()
        sidebar.setMinimumWidth(230)
        sidebar.setLayout(sidebar_layout)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(sidebar)
        splitter.addWidget(viewer_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 850])

        layout = QVBoxLayout()
        layout.addLayout(project_controls)
        layout.addWidget(splitter, 1)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.statusBar().addWidget(self.status_message_label)
        self.statusBar().addPermanentWidget(self.save_status_label)
        self.statusBar().addPermanentWidget(self.image_path_status, 1)
        self.statusBar().show()

        self.load_button.clicked.connect(self.load_entered_plate)
        self.new_project_button.clicked.connect(self.create_project_interactively)
        self.rename_project_button.clicked.connect(self.rename_project_interactively)
        self.project_selector.currentIndexChanged.connect(self._project_selected)
        self.image_set_list.clicked.connect(self._image_set_selected)
        self.image_set_list.customContextMenuRequested.connect(
            self._show_image_set_context_menu
        )
        self.move_image_set_up_button.clicked.connect(
            lambda: self._move_selected_image_set(-1)
        )
        self.move_image_set_down_button.clicked.connect(
            lambda: self._move_selected_image_set(1)
        )
        self.archive_image_set_button.clicked.connect(self._archive_selected_image_set)
        self.restore_image_set_button.clicked.connect(self._restore_archived_image_set)
        self.plate_input.returnPressed.connect(self.load_entered_plate)
        self.previous_button.clicked.connect(self.show_previous)
        self.next_button.clicked.connect(self.show_next)
        self.image_canvas.image_clicked.connect(self._handle_image_click)
        self.image_canvas.previous_requested.connect(self.show_previous)
        self.image_canvas.next_requested.connect(self.show_next)
        self.image_canvas.previous_plate_requested.connect(
            lambda: self._switch_active_image_set(-1)
        )
        self.image_canvas.next_plate_requested.connect(
            lambda: self._switch_active_image_set(1)
        )
        self.image_set_list.previous_plate_requested.connect(
            lambda: self._switch_active_image_set(-1)
        )
        self.image_set_list.next_plate_requested.connect(
            lambda: self._switch_active_image_set(1)
        )
        self.auto_advance_input.valueChanged.connect(self._change_auto_advance_target_count)
        self.auto_calibration_button.clicked.connect(self._auto_detect_calibration)
        self.manual_calibration_button.clicked.connect(self._start_manual_calibration)
        self.image_filter_input.currentIndexChanged.connect(self._change_image_filter)
        self.well_input.returnPressed.connect(self._go_to_entered_well)
        self.well_input.editingFinished.connect(self._go_to_entered_well)
        self._update_navigation()
        self._initialize_projects()

    def _initialize_projects(self) -> None:
        if self.project_controller.projects:
            project_ids = {project.id for project in self.project_controller.projects}
            project_id = self.project_controller.last_open_project_id
            if project_id not in project_ids:
                project_id = self.project_controller.projects[0].id
            self.project_controller.open_project(project_id)
        else:
            self.project_controller.create_project("Untitled Project")
        self._adopt_active_review()
        self._sync_project_widgets()

    def create_project_interactively(self) -> None:
        name, accepted = QInputDialog.getText(self, "New project", "Project name:")
        if not accepted:
            return
        try:
            if self.controller is not None:
                self.controller.checkpoint_current()
            self.project_controller.create_project(name)
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot create project", str(error))

    def rename_project_interactively(self) -> None:
        project = self.project_controller.active_project
        if project is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename project", "Project name:", text=project.name
        )
        if not accepted:
            return
        try:
            self.project_controller.rename_active_project(name)
            self._sync_project_widgets()
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot rename project", str(error))

    def _project_selected(self, index: int) -> None:
        project_id = self.project_selector.itemData(index)
        if not project_id:
            return
        active = self.project_controller.active_project
        if active is not None and active.id == project_id:
            return
        try:
            self.project_controller.open_project(project_id)
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot open project", str(error))

    def _image_set_selected(self, index) -> None:
        image_set_id = index.data(ProjectImageSetListModel.ImageSetIdRole)
        if not image_set_id:
            return
        try:
            self.project_controller.activate_image_set(image_set_id)
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot open image set", str(error))

    def _selected_image_set_id(self) -> str | None:
        index = self.image_set_list.currentIndex()
        return index.data(ProjectImageSetListModel.ImageSetIdRole) if index.isValid() else None

    def _switch_active_image_set(self, offset: int) -> None:
        project = self.project_controller.active_project
        if project is None or project.active_image_set_id is None:
            return
        image_sets = project.active_image_sets
        current = next(
            index
            for index, image_set in enumerate(image_sets)
            if image_set.id == project.active_image_set_id
        )
        destination = current + offset
        if not 0 <= destination < len(image_sets):
            return
        try:
            self.project_controller.activate_image_set(image_sets[destination].id)
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot open image set", str(error))

    def _move_selected_image_set(self, offset: int) -> None:
        image_set_id = self._selected_image_set_id()
        if image_set_id is None:
            return
        try:
            self.project_controller.move_image_set(image_set_id, offset)
            self._sync_project_widgets()
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot reorder image set", str(error))

    def _archive_selected_image_set(self) -> None:
        image_set_id = self._selected_image_set_id()
        if image_set_id is None:
            return
        target_count = self._target_count_for_image_set(image_set_id)
        message = "Remove this image set from the project? It can be restored from the database."
        if target_count:
            message = (
                f"This image set has {target_count} targets. Remove it from the project view? "
                "Targets will be preserved."
            )
        if (
            QMessageBox.question(
                self,
                "Remove image set",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            self.project_controller.archive_image_set(image_set_id)
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot remove image set", str(error))

    def _restore_archived_image_set(self) -> None:
        project = self.project_controller.active_project
        if project is None:
            return
        archived = [item for item in project.image_sets if item.is_archived]
        if not archived:
            QMessageBox.information(self, "Restore image set", "No archived image sets")
            return
        labels = [
            f"Plate {item.plate_code} · Batch {item.batch_id} · {item.profile}"
            for item in archived
        ]
        label, accepted = QInputDialog.getItem(
            self, "Restore image set", "Archived image set:", labels, 0, False
        )
        if not accepted:
            return
        image_set = archived[labels.index(label)]
        try:
            self.project_controller.restore_image_set(image_set.id)
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot restore image set", str(error))

    def _adopt_active_review(self) -> None:
        self.controller = self.project_controller.review_controller
        self.plate = self.controller.plate if self.controller is not None else None
        if self.controller is None:
            self.calibration_service = None
            self.current_calibration = None
            self.navigation_label.setText("No image set loaded")
            self.review_summary_label.setText("Add a plate to the active project")
            self.save_status_label.setText("Not loaded")
            self.image_path_status.set_image_path(None)
            self._update_navigation()
            return
        self.calibration_service = None
        self.auto_advance_input.blockSignals(True)
        self.auto_advance_input.setValue(
            self.controller.preferences.auto_advance_target_count
        )
        self.auto_advance_input.blockSignals(False)
        self.image_filter_input.blockSignals(True)
        filter_index = self.image_filter_input.findData(self.controller.image_filter)
        self.image_filter_input.setCurrentIndex(filter_index)
        self.image_filter_input.blockSignals(False)
        self.well_input.clear()
        plate_format = self._active_plate_format()
        addresses: list[str] = []
        self._well_destinations.clear()
        for index, image in enumerate(self.controller.plate.images):
            if plate_format is None:
                label = f"{image.well_number}/d{image.drop_number}"
            else:
                label = str(
                    plate_format.address_for(image.well_number, image.drop_number)
                )
            addresses.append(label)
            self._well_destinations[label.casefold()] = (label, index)
        self.well_completion_model.setStringList(addresses)
        self._show_current_image()
        self._set_save_status("saved")
        self.image_canvas.setFocus(Qt.OtherFocusReason)

    def _sync_project_widgets(self) -> None:
        active = self.project_controller.active_project
        self.project_selector.blockSignals(True)
        self.project_selector.clear()
        for project in self.project_controller.projects:
            self.project_selector.addItem(project.name, project.id)
        if active is not None:
            active_index = self.project_selector.findData(active.id)
            self.project_selector.setCurrentIndex(active_index)
        self.project_selector.blockSignals(False)
        self.image_set_model.set_project(active)
        if active is not None and active.active_image_set_id is not None:
            for row, image_set in enumerate(self.image_set_model.image_sets):
                if image_set.id == active.active_image_set_id:
                    self.image_set_list.setCurrentIndex(self.image_set_model.index(row, 0))
                    plate_format = plate_format_by_id(image_set.plate_format_id)
                    format_index = self.plate_format_input.findData(plate_format)
                    self.plate_format_input.setCurrentIndex(max(format_index, 0))
                    break

    def _target_count_for_image_set(self, image_set_id: str) -> int:
        if (
            self.project_controller.active_project is not None
            and self.project_controller.active_project.active_image_set_id == image_set_id
            and self.controller is not None
        ):
            return self.controller.session.target_count
        if self.review_store is not None:
            return self.review_store.target_count_for_image_set(image_set_id)
        return 0

    def load_entered_plate(self) -> None:
        try:
            plate_format = self._selected_plate_format()
            plate_codes = tuple(
                dict.fromkeys(
                    plate_code.strip()
                    for plate_code in self.plate_input.text().split(",")
                    if plate_code.strip()
                )
            )
            if not plate_codes:
                raise ValueError("enter at least one plate code")
            for plate_code in plate_codes:
                if not self._choose_and_add_plate(plate_code, plate_format):
                    return
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot load plate", str(error))

    def _choose_and_add_plate(
        self, plate_code: str, plate_format: PlateFormat
    ) -> bool:
        batches = self.repository.available_batches(plate_code)
        if not batches:
            raise PlateImagesNotFoundError(f"no batches found for plate {plate_code}")
        batch_text, accepted = QInputDialog.getItem(
            self,
            f"Add plate {plate_code}",
            f"Plate {plate_code} batch:",
            [str(batch) for batch in reversed(batches)],
            0,
            False,
        )
        if not accepted:
            return False
        batch_id = int(batch_text)
        profiles = self.repository.available_profiles(plate_code, batch_id)
        if not profiles:
            raise PlateImagesNotFoundError(
                f"no profiles found for plate {plate_code}, batch {batch_id}"
            )
        profile, accepted = QInputDialog.getItem(
            self,
            f"Add plate {plate_code}",
            f"Plate {plate_code} profile:",
            list(profiles),
            0,
            False,
        )
        if not accepted:
            return False
        self.project_controller.add_pinned_image_set(
            plate_code, batch_id, profile, plate_format
        )
        return True

    def load_plate(
        self, plate_code: str, plate_format: PlateFormat, profile: str = "profileID_1"
    ) -> None:
        self.project_controller.add_latest_image_set(plate_code, plate_format, profile)
        self._adopt_active_review()
        self._sync_project_widgets()

    def _selected_plate_format(self) -> PlateFormat:
        plate_format = self.plate_format_input.currentData()
        if plate_format is None:
            raise ValueError("select the plate format before adding a plate")
        return plate_format

    def _active_plate_format(self) -> PlateFormat | None:
        image_set = self.project_controller.active_image_set
        if image_set is None:
            return None
        plate_format = plate_format_by_id(image_set.plate_format_id)
        if (
            plate_format is None
            or image_set.plate_format_version != plate_format.version
        ):
            return None
        return plate_format

    def _show_image_set_context_menu(self, position) -> None:
        index = self.image_set_list.indexAt(position)
        if not index.isValid():
            return
        self.image_set_list.setCurrentIndex(index)
        image_set_id = index.data(ProjectImageSetListModel.ImageSetIdRole)
        if image_set_id is None:
            return
        menu = self._build_image_set_context_menu(image_set_id)
        menu.exec_(self.image_set_list.viewport().mapToGlobal(position))

    def _build_image_set_context_menu(self, image_set_id: str) -> QMenu:
        project = self.project_controller.active_project
        if project is None:
            raise ValueError("no project is open")
        image_set = next(item for item in project.image_sets if item.id == image_set_id)
        menu = QMenu(self.image_set_list)
        format_menu = menu.addMenu("Set format")
        for plate_format in PLATE_FORMATS:
            action = format_menu.addAction(plate_format.display_name)
            action.setCheckable(True)
            action.setChecked(image_set.plate_format_id == plate_format.id)
            action.setEnabled(image_set.plate_format_id != plate_format.id)
            action.triggered.connect(
                lambda checked=False, selected=plate_format: self._change_image_set_format(
                    image_set_id, selected
                )
            )
        return menu

    def _change_image_set_format(
        self, image_set_id: str, plate_format: PlateFormat
    ) -> None:
        try:
            project = self.project_controller.active_project
            image_set = next(
                item for item in project.image_sets if item.id == image_set_id
            )
            if (
                image_set.plate_format_id is not None
                and image_set.plate_format_id != plate_format.id
                and QMessageBox.question(
                    self,
                    "Change plate format",
                    "Changing the format changes well addresses and pixel-to-mm conversion. "
                    "Existing target pixels are preserved. Continue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
            self.project_controller.set_image_set_plate_format(image_set_id, plate_format)
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot set plate format", str(error))

    def show_previous(self) -> bool:
        if self.controller is None:
            return False
        if self.controller.can_move_previous:
            return self._move(self.controller.move_previous)
        return self._move_across_image_sets(-1)

    def show_next(self) -> bool:
        if self.controller is None:
            return False
        if self.controller.can_move_next:
            return self._move(self.controller.move_next)
        return self._move_across_image_sets(1)

    def _move(self, move_command: Callable[[], bool]) -> bool:
        if self.controller is None:
            return False
        try:
            moved = move_command()
        except ReviewPersistenceError as error:
            self._show_persistence_error(error)
            return False
        if not moved:
            return False
        self.status_message_label.show_message("Review checkpoint saved", 2000)
        self._show_current_image()
        self._set_save_status("saved")
        self.image_set_model.refresh_counts()
        return True

    def _move_across_image_sets(self, direction: int) -> bool:
        if self.controller is None:
            return False
        try:
            moved = self.project_controller.move_across_image_sets(
                direction, self.controller.image_filter
            )
        except (PlateImagesNotFoundError, ReviewPersistenceError) as error:
            self._show_persistence_error(error)
            return False
        if not moved:
            return False
        self._adopt_active_review()
        self._sync_project_widgets()
        self.status_message_label.show_message("Moved to another plate", 2000)
        return True

    def _show_current_image(self) -> None:
        if self.controller is None:
            return
        image = self.controller.current_image
        self.image_path_status.set_image_path(image.path)
        pixmap = QPixmap(str(image.path))
        if pixmap.isNull():
            raise ValueError(f"invalid image: {image.path}")
        targets = self.controller.current_targets
        self.image_canvas.set_image(pixmap, targets)
        self._manual_calibration_points = None
        self.image_canvas.set_calibration_points(())
        plate_format = self._active_plate_format()
        if plate_format is None:
            self.calibration_service = None
            self.current_calibration = None
            self.image_canvas.set_calibration(None)
            self.calibration_label.setText("Well calibration unavailable: unsupported format")
            image_label = image.navigation_label
        else:
            lens = plate_format.lens_for(image.drop_number)
            self.calibration_service = WellCalibrationService(
                OpenCVWellDetector(),
                lens.physical_diameter_mm,
                self.controller.store,
            )
            self._load_current_calibration()
            address = str(
                plate_format.address_for(image.well_number, image.drop_number)
            )
            image_label = (
                f"Plate {image.plate_code} · Well {address} "
                f"(RM {image.well_number}/d{image.drop_number})"
            )
        if plate_format is not None:
            self._current_well_address = str(
                plate_format.address_for(image.well_number, image.drop_number)
            )
            self.well_input.setText(self._current_well_address)
        self.navigation_label.setText(
            f"{image_label} · Batch {image.batch_id} · "
            f"{self.controller.image_index + 1}/{len(self.controller.plate.images)}"
        )
        self._update_review_summary()
        self._update_navigation()

    def _handle_image_click(self, x_px: float, y_px: float, button: int) -> None:
        if self.controller is None:
            return
        if self._manual_calibration_points is not None:
            if button == Qt.RightButton:
                self._cancel_manual_calibration()
                return
            if button != Qt.LeftButton:
                return
            self._manual_calibration_points.append((x_px, y_px))
            self.image_canvas.set_calibration_points(
                tuple(self._manual_calibration_points)
            )
            if len(self._manual_calibration_points) == 3:
                self._finish_manual_calibration()
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
        self.image_set_model.refresh_counts()
        if should_auto_advance:
            if not self.show_next():
                try:
                    self.controller.checkpoint_current(mark_reviewed=True)
                    self.status_message_label.show_message(
                        "Review complete; final image saved", 5000
                    )
                    self._set_save_status("saved")
                except ReviewPersistenceError as error:
                    self._show_persistence_error(error)

    def _load_current_calibration(self, force_detection: bool = False) -> None:
        if self.controller is None or self.calibration_service is None:
            return
        try:
            calibration = self.calibration_service.calibration_for(
                self.controller.current_image, force_detection
            )
        except (CalibrationDetectionError, ReviewPersistenceError) as error:
            self.current_calibration = None
            self.image_canvas.set_calibration(None)
            self.calibration_label.setText(f"Well calibration unavailable: {error}")
            return
        self.current_calibration = calibration
        self.image_canvas.set_calibration(calibration)
        scale_um = calibration.physical_diameter_mm * 1000 / (
            calibration.radius_x_px + calibration.radius_y_px
        )
        method = "Manual" if calibration.confirmed else "Auto"
        self.calibration_label.setText(
            f"Well: {method} · confidence {calibration.confidence:.0%} · "
            f"center ({calibration.center_x_px:.1f}, {calibration.center_y_px:.1f}) px · "
            f"{scale_um:.3f} µm/px"
        )

    def _auto_detect_calibration(self) -> None:
        self._cancel_manual_calibration()
        self._load_current_calibration(force_detection=True)

    def _start_manual_calibration(self) -> None:
        if self.controller is None:
            return
        self._manual_calibration_points = []
        self.image_canvas.set_calibration_points(())
        self.calibration_label.setText(
            "Click three separated points on the outer well boundary · right-click cancels"
        )
        self.image_canvas.setFocus(Qt.OtherFocusReason)

    def _finish_manual_calibration(self) -> None:
        if (
            self.controller is None
            or self.calibration_service is None
            or self._manual_calibration_points is None
        ):
            return
        points = tuple(self._manual_calibration_points)
        try:
            calibration = self.calibration_service.save_manual_three_point(
                self.controller.current_image, points
            )
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot calibrate well", str(error))
            self._cancel_manual_calibration()
            return
        self._manual_calibration_points = None
        self.image_canvas.set_calibration_points(())
        self.current_calibration = calibration
        self.image_canvas.set_calibration(calibration)
        self._load_current_calibration()

    def _cancel_manual_calibration(self) -> None:
        self._manual_calibration_points = None
        self.image_canvas.set_calibration_points(())
        if self.current_calibration is not None:
            self.image_canvas.set_calibration(self.current_calibration)
            self._load_current_calibration()

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

    def _change_image_filter(self) -> None:
        if self.controller is None:
            return
        image_filter = self.image_filter_input.currentData()
        try:
            moved = self.controller.change_image_filter(image_filter)
        except ReviewPersistenceError as error:
            self._show_persistence_error(error)
            return
        if not moved and not self.controller.current_matches_filter:
            moved = self._move_across_image_sets(1)
            if not moved:
                moved = self._move_across_image_sets(-1)
            if moved:
                return
        if moved:
            self._show_current_image()
            self._set_save_status("saved")
        else:
            self._update_review_summary()
            self._update_navigation()

    def _go_to_entered_well(self) -> None:
        if self.controller is None:
            return
        normalized = self._normalize_well_address(self.well_input.text())
        destination = self._well_destinations.get(normalized.casefold())
        if destination is None:
            self._reject_well_entry()
            return
        address, image_index = destination
        self.well_input.setText(address)
        self.well_input.setStyleSheet("")
        if image_index == self.controller.image_index:
            self._current_well_address = address
            return
        try:
            moved = self.controller.move_to(image_index)
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot go to well", str(error))
            return
        if moved:
            self._show_current_image()
            self._set_save_status("saved")

    @staticmethod
    def _normalize_well_address(value: str) -> str:
        match = re.fullmatch(r"\s*([A-Ha-h])0?([1-9]|1[0-2])([A-Za-z])\s*", value)
        if match is None:
            return value.strip()
        row, column, suffix = match.groups()
        return f"{row.upper()}{int(column):02d}{suffix.lower()}"

    def _reject_well_entry(self) -> None:
        self.well_input.setText(self._current_well_address)
        self.well_input.setStyleSheet("border: 1px solid #c62828;")
        self.status_message_label.show_message("Invalid or unavailable subwell", 2000)
        QTimer.singleShot(800, lambda: self.well_input.setStyleSheet(""))

    def _restore_current_well_address(self) -> None:
        self.well_input.setText(self._current_well_address)
        self.well_input.setStyleSheet("")

    def _update_review_summary(self) -> None:
        if self.controller is None:
            return
        image_count = len(self.controller.current_targets)
        auto_advance_count = self.controller.preferences.auto_advance_target_count
        reviewed = self.controller.session.reviewed_count
        total = len(self.controller.plate.images)
        filtered = len(self.controller.filtered_indices)
        review_state = (
            "Reviewed"
            if self.controller.session.is_reviewed(self.controller.current_image)
            else "Unreviewed"
        )
        self.review_summary_label.setText(
            f"Selected: {image_count} · Targets/img: {auto_advance_count} · Session total: "
            f"{self.controller.session.target_count} · {review_state} · Reviewed: {reviewed}/{total} "
            f"· Plate matches: {filtered}"
        )
        try:
            statistics = self.project_controller.project_review_statistics()
            self.project_progress_label.setText(
                f"Reviewed {statistics.reviewed_images}/{statistics.total_images} · "
                f"Target images {statistics.target_images} · "
                f"No-target {statistics.reviewed_without_targets} · "
                f"Pending {statistics.unreviewed_images} · Points {statistics.target_points}"
            )
        except (PlateImagesNotFoundError, ReviewPersistenceError):
            self.project_progress_label.setText("Project totals unavailable")
        self._update_navigation()

    def _update_navigation(self) -> None:
        self.previous_button.setEnabled(
            self.controller is not None
            and (
                self.controller.can_move_previous
                or self.project_controller.has_adjacent_image_set(-1)
            )
        )
        self.next_button.setEnabled(
            self.controller is not None
            and (
                self.controller.can_move_next
                or self.project_controller.has_adjacent_image_set(1)
            )
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
        self.status_message_label.show_message(f"Not saved: {error}")
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
        "--plate-format",
        choices=[plate_format.id for plate_format in PLATE_FORMATS],
        help="Plate format ID (required with --plate)",
    )
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
        if not args.plate_format:
            print("xtalflow-viewer: --plate-format is required with --plate", file=sys.stderr)
            window.close()
            return 2
        plate_format = plate_format_by_id(args.plate_format)
        window.plate_input.setText(args.plate)
        window.plate_format_input.setCurrentIndex(
            window.plate_format_input.findData(plate_format)
        )
        try:
            window.load_plate(args.plate, plate_format)
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            print(f"xtalflow-viewer: {error}", file=sys.stderr)
            window.close()
            return 2
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
