from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from PyQt5.QtCore import QRectF, QStandardPaths, QStringListModel, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCheckBox,
    QCompleter,
    QDockWidget,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSpinBox,
    QStackedWidget,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xtalflow.application import (
    CalibrationDetectionError,
    ProjectController,
    ReviewController,
    ReviewPersistenceError,
    TargetValidationIssue,
    WellCalibrationService,
)
from xtalflow.domain import (
    PLATE_FORMATS,
    CalibrationMethod,
    ImageCalibration,
    ImageFilter,
    PlateFormat,
    PlateImages,
    TargetPoint,
    plate_format_by_id,
)
from xtalflow.domain.fragment_screening import (
    AssignmentOrder,
    FragmentLibrary,
    SelectedCrystal,
    build_fragment_screen_plan,
)
from xtalflow.domain.experiment_naming import suggest_experiment_id
from xtalflow.domain.raw_crystal import RawCrystalPlan, build_raw_crystal_plan
from xtalflow.domain.labwork import (
    LABWORK_COLUMNS,
    build_fragment_labworks,
    build_raw_crystal_labworks,
)
from xtalflow.domain.plan_lifecycle import (
    PlanningDraft,
    PlanRevision,
    WebDBUploadEvent,
    WorksheetExportEvent,
)
from xtalflow.domain.mxlive import MxLivePartialWriteError, MxLiveWriteError
from xtalflow.domain.worksheets import (
    ECHO_HEADER,
    SHIFTER_HEADER,
    build_echo_worksheet,
    build_shifter_worksheet,
)
from xtalflow.infrastructure import (
    LegacyMxLiveWriteClient,
    PlateImagesNotFoundError,
    OpenCVWellDetector,
    RockMakerImageRepository,
    SQLiteReviewStore,
)
from xtalflow.infrastructure.fragment_library_csv import (
    FragmentLibraryCsvError,
    load_fragment_library,
)
from xtalflow.infrastructure.worksheet_exporter import (
    WorksheetDestinationUnavailable,
    WorksheetExporter,
)
from xtalflow.infrastructure.mxlive_config import (
    MxLiveAccount,
    MxLiveConfigurationError,
    resolve_mxlive_account,
)
from xtalflow.infrastructure.user_preferences import (
    JsonUserPreferencesStore,
    UserPreferences,
)
from xtalflow.presentation import AspectFitTransform, ProjectImageSetListModel
from xtalflow.settings import ApplicationSettings, DEFAULT_SETTINGS


class ImageCanvas(QWidget):
    image_clicked = pyqtSignal(float, float, int)
    previous_requested = pyqtSignal()
    next_requested = pyqtSignal()
    previous_plate_requested = pyqtSignal()
    next_plate_requested = pyqtSignal()
    zoom_changed = pyqtSignal(float)

    def __init__(self) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._targets: tuple[TargetPoint, ...] = ()
        self._calibration: ImageCalibration | None = None
        self._calibration_points: tuple[tuple[float, float], ...] = ()
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._pan_origin: tuple[float, float] | None = None
        self._pan_start = None
        self._space_pressed = False
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.StrongFocus)
        self._previous_shortcut = QShortcut(QKeySequence("Left"), self)
        self._next_shortcut = QShortcut(QKeySequence("Right"), self)
        self._previous_plate_shortcut = QShortcut(QKeySequence("Up"), self)
        self._next_plate_shortcut = QShortcut(QKeySequence("Down"), self)
        self._zoom_in_shortcut = QShortcut(QKeySequence("+"), self)
        self._zoom_out_shortcut = QShortcut(QKeySequence("-"), self)
        self._fit_shortcut = QShortcut(QKeySequence("0"), self)
        self._previous_shortcut.setContext(Qt.WidgetShortcut)
        self._next_shortcut.setContext(Qt.WidgetShortcut)
        self._previous_plate_shortcut.setContext(Qt.WidgetShortcut)
        self._next_plate_shortcut.setContext(Qt.WidgetShortcut)
        self._zoom_in_shortcut.setContext(Qt.WidgetShortcut)
        self._zoom_out_shortcut.setContext(Qt.WidgetShortcut)
        self._fit_shortcut.setContext(Qt.WidgetShortcut)
        self._previous_shortcut.activated.connect(self.previous_requested)
        self._next_shortcut.activated.connect(self.next_requested)
        self._previous_plate_shortcut.activated.connect(self.previous_plate_requested)
        self._next_plate_shortcut.activated.connect(self.next_plate_requested)
        self._zoom_in_shortcut.activated.connect(self.zoom_in)
        self._zoom_out_shortcut.activated.connect(self.zoom_out)
        self._fit_shortcut.activated.connect(self.fit_image)


    def set_image(self, pixmap: QPixmap, targets: tuple[TargetPoint, ...]) -> None:
        self._pixmap = pixmap
        self._targets = targets
        self.fit_image()
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
            self._pixmap.width(),
            self._pixmap.height(),
            self.width(),
            self.height(),
            self._zoom,
            self._pan_x,
            self._pan_y,
        )

    @property
    def zoom(self) -> float:
        return self._zoom

    def fit_image(self) -> None:
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_in(self) -> None:
        self._set_zoom(self._zoom * 1.25, self.rect().center())

    def zoom_out(self) -> None:
        self._set_zoom(self._zoom / 1.25, self.rect().center())

    def _set_zoom(self, zoom: float, anchor) -> None:
        transform = self.transform()
        if transform is None:
            return
        new_zoom = min(max(zoom, 1.0), 8.0)
        if abs(new_zoom - self._zoom) < 1e-9:
            return
        image_point = transform.viewport_to_image(anchor.x(), anchor.y())
        if image_point is None:
            image_point = (self._pixmap.width() / 2, self._pixmap.height() / 2)
            anchor = self.rect().center()
        self._zoom = new_zoom
        unpanned = AspectFitTransform(
            self._pixmap.width(),
            self._pixmap.height(),
            self.width(),
            self.height(),
            self._zoom,
        )
        self._pan_x = anchor.x() - unpanned.offset_x - image_point[0] * unpanned.scale
        self._pan_y = anchor.y() - unpanned.offset_y - image_point[1] * unpanned.scale
        self._clamp_pan()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def _clamp_pan(self) -> None:
        if self._pixmap.isNull():
            self._pan_x = self._pan_y = 0.0
            return
        fit = AspectFitTransform(
            self._pixmap.width(),
            self._pixmap.height(),
            max(self.width(), 1),
            max(self.height(), 1),
            self._zoom,
        )
        max_x = max((fit.rendered_width - self.width()) / 2, 0.0)
        max_y = max((fit.rendered_height - self.height()) / 2, 0.0)
        self._pan_x = min(max(self._pan_x, -max_x), max_x)
        self._pan_y = min(max(self._pan_y, -max_y), max_y)

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
        if event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton and self._space_pressed
        ):
            self._pan_start = event.pos()
            self._pan_origin = (self._pan_x, self._pan_y)
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        transform = self.transform()
        if transform is None:
            return
        image_point = transform.viewport_to_image(event.x(), event.y())
        if image_point is not None:
            self.image_clicked.emit(*image_point, int(event.button()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._pan_start is None or self._pan_origin is None:
            return
        delta = event.pos() - self._pan_start
        self._pan_x = self._pan_origin[0] + delta.x()
        self._pan_y = self._pan_origin[1] + delta.y()
        self._clamp_pan()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._pan_start is not None:
            self._pan_start = None
            self._pan_origin = None
            self.setCursor(Qt.OpenHandCursor if self._space_pressed else Qt.ArrowCursor)
            event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self._set_zoom(self._zoom * (1.25 ** (delta / 120)), event.pos())
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pressed = False
            if self._pan_start is None:
                self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._clamp_pan()


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


class FragmentScreeningEditor(QWidget):
    """Embedded fragment-to-crystal assignment editor."""

    library_refresh_requested = pyqtSignal()
    save_worksheets_requested = pyqtSignal()
    finalize_requested = pyqtSignal()
    draft_changed = pyqtSignal()
    webdb_upload_requested = pyqtSignal()

    def __init__(
        self,
        library: FragmentLibrary | None,
        crystals: tuple[SelectedCrystal, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        initial_library = library
        self.library: FragmentLibrary | None = None
        self.crystals = crystals
        self.current_plan = None
        self.current_experiment_id: str | None = None
        self.mxlive_account: MxLiveAccount | None = None
        self.library_input = QComboBox()
        self.library_input.setMinimumWidth(260)
        self.refresh_libraries_button = QPushButton("Refresh Libraries")
        self.library_label = QLabel("No library imported")
        self.rows_input = QLineEdit()
        self.rows_input.setPlaceholderText("e.g. 1-96, 101, 105-120")
        self.rows_input.setToolTip(
            "One-based CSV data rows. The header and the CSV No column are not counted."
        )
        self.volume_input = QDoubleSpinBox()
        self.volume_input.setRange(2.5, 10000.0)
        self.volume_input.setSingleStep(2.5)
        self.volume_input.setDecimals(1)
        self.volume_input.setSuffix(" nL / image")
        self.volume_input.setValue(25.0)
        self.order_input = QComboBox()
        self.order_input.addItem("Selection order", AssignmentOrder.SELECTION)
        self.order_input.addItem("Plate / well order", AssignmentOrder.PLATE_WELL)
        self.protein_input = QLineEdit()
        self.protein_input.setPlaceholderText("Protein name")
        self.experiment_id_label = QLabel("Experiment ID: —")
        self._experiment_id_provider: Callable[[str], str] | None = None
        self.save_worksheets_button = QPushButton("Save Worksheets…")
        self.finalize_button = QPushButton("Finalize Plan")
        self.lifecycle_label = QLabel("Draft · not saved")
        self.save_worksheets_button.setEnabled(False)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #b00020")
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("Order", "Plate", "Well", "Targets", "Fragment", "Source", "Total")
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.echo_table = QTableWidget(0, len(ECHO_HEADER))
        self.echo_table.setHorizontalHeaderLabels(ECHO_HEADER)
        self.shifter_table = QTableWidget(0, len(SHIFTER_HEADER))
        self.shifter_table.setHorizontalHeaderLabels(SHIFTER_HEADER)
        for preview_table in (self.echo_table, self.shifter_table):
            preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            preview_table.verticalHeader().setVisible(False)
            preview_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeToContents
            )
        self.preview_tabs = QTabWidget()
        self.preview_tabs.addTab(self.table, "Summary")
        self.preview_tabs.addTab(self.echo_table, "ECHO Worksheet")
        self.preview_tabs.addTab(self.shifter_table, "SHIFTER Worksheet")
        self.webdb_status_label = QLabel("MxLive account: not configured")
        self.webdb_table = QTableWidget(0, len(LABWORK_COLUMNS))
        self.webdb_table.setHorizontalHeaderLabels(LABWORK_COLUMNS)
        self.webdb_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.webdb_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.webdb_table.verticalHeader().setVisible(False)
        self.webdb_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.webdb_upload_button = QPushButton("Upload Finalized Revision…")
        self.webdb_upload_button.setEnabled(False)
        webdb_widget = QWidget()
        webdb_layout = QVBoxLayout()
        webdb_layout.addWidget(self.webdb_status_label)
        webdb_layout.addWidget(self.webdb_table, 1)
        webdb_layout.addWidget(self.webdb_upload_button)
        webdb_widget.setLayout(webdb_layout)
        self.preview_tabs.addTab(webdb_widget, "WebDB")

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Library rows:"))
        controls.addWidget(self.rows_input, 1)
        controls.addWidget(QLabel("Vol/Well:"))
        controls.addWidget(self.volume_input)
        controls.addWidget(QLabel("Assign:"))
        controls.addWidget(self.order_input)
        experiment_controls = QHBoxLayout()
        experiment_controls.addWidget(QLabel("Protein:"))
        experiment_controls.addWidget(self.protein_input)
        experiment_controls.addWidget(self.experiment_id_label, 1)
        experiment_controls.addWidget(self.lifecycle_label)
        experiment_controls.addWidget(self.finalize_button)
        experiment_controls.addWidget(self.save_worksheets_button)
        library_controls = QHBoxLayout()
        library_controls.addWidget(QLabel("Library:"))
        library_controls.addWidget(self.library_input, 1)
        library_controls.addWidget(self.refresh_libraries_button)
        layout = QVBoxLayout()
        layout.addLayout(library_controls)
        layout.addWidget(self.library_label)
        layout.addLayout(experiment_controls)
        layout.addLayout(controls)
        layout.addWidget(self.error_label)
        layout.addWidget(self.preview_tabs, 1)
        self.setLayout(layout)

        self.rows_input.textChanged.connect(self.refresh_plan)
        self.volume_input.valueChanged.connect(self.refresh_plan)
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
        self.library_input.currentIndexChanged.connect(self._library_changed)
        self.refresh_libraries_button.clicked.connect(
            self.library_refresh_requested.emit
        )
        self.protein_input.textChanged.connect(self._refresh_experiment_id)
        self.protein_input.textChanged.connect(self.draft_changed.emit)
        self.save_worksheets_button.clicked.connect(
            self.save_worksheets_requested.emit
        )
        self.finalize_button.clicked.connect(self.finalize_requested.emit)
        self.webdb_upload_button.clicked.connect(self.webdb_upload_requested.emit)
        if initial_library is not None:
            self.set_library_choices(
                ((
                    "direct",
                    f"{initial_library.name} · {len(initial_library.fragments)} rows",
                    initial_library,
                ),),
                "direct",
            )
        self.refresh_plan()

    def set_crystals(self, crystals: tuple[SelectedCrystal, ...]) -> None:
        self.crystals = crystals
        self.refresh_plan()

    def set_mxlive_account(self, account: MxLiveAccount) -> None:
        self.mxlive_account = account
        self.refresh_plan()

    def set_experiment_id_provider(
        self, provider: Callable[[str], str]
    ) -> None:
        self._experiment_id_provider = provider
        self._refresh_experiment_id()

    def _refresh_experiment_id(self) -> None:
        if self._experiment_id_provider is None:
            self.current_experiment_id = None
            self.experiment_id_label.setText("Experiment ID: —")
            self.save_worksheets_button.setEnabled(False)
            self._refresh_webdb_preview()
            return
        try:
            experiment_id = self._experiment_id_provider(self.protein_input.text())
        except ValueError as error:
            self.current_experiment_id = None
            self.experiment_id_label.setText(f"Experiment ID: {error}")
            self.save_worksheets_button.setEnabled(False)
            self._refresh_webdb_preview()
            return
        self.current_experiment_id = experiment_id
        self.experiment_id_label.setText(f"Experiment ID: {experiment_id}")
        self.save_worksheets_button.setEnabled(self.current_plan is not None)
        self._refresh_webdb_preview()

    def set_library_choices(
        self,
        choices: tuple[tuple[str, str, FragmentLibrary], ...],
        selected_id: str | None = None,
    ) -> None:
        previous_id = selected_id or self.library_input.currentData(Qt.UserRole)
        self.library_input.blockSignals(True)
        self.library_input.clear()
        self.library_input.addItem("Select imported library…", None)
        for library_id, label, library in choices:
            self.library_input.addItem(label, library_id)
            self.library_input.setItemData(
                self.library_input.count() - 1, library, Qt.UserRole + 1
            )
        selected_index = self.library_input.findData(previous_id, Qt.UserRole)
        self.library_input.setCurrentIndex(max(0, selected_index))
        self.library_input.blockSignals(False)
        self._library_changed(self.library_input.currentIndex())

    def _library_changed(self, index: int) -> None:
        library = self.library_input.itemData(index, Qt.UserRole + 1)
        changed = library is not self.library
        self.library = library
        if library is None:
            self.library_label.setText("No library selected")
            if changed:
                self.rows_input.clear()
        else:
            self.library_label.setText(
                f"{library.name} · {len(library.fragments)} imported data rows"
            )
            if changed:
                self.rows_input.setText(f"1-{len(library.fragments)}")
        self.refresh_plan()

    def refresh_plan(self) -> None:
        try:
            if self.library is None:
                raise ValueError("select or import a fragment library")
            selected_library = self.library.select_rows(self.rows_input.text())
            plan = build_fragment_screen_plan(
                selected_library,
                self.crystals,
                Decimal(str(self.volume_input.value())),
                self.order_input.currentData(),
            )
        except ValueError as error:
            self.current_plan = None
            self.error_label.setText(str(error))
            self.table.setRowCount(0)
            self.echo_table.setRowCount(0)
            self.shifter_table.setRowCount(0)
            self.webdb_table.setRowCount(0)
            self.save_worksheets_button.setEnabled(False)
            self._refresh_webdb_preview()
            self.draft_changed.emit()
            return
        self.current_plan = plan
        self.error_label.setText("")
        self.table.setRowCount(len(plan.assignments))
        for row, assignment in enumerate(plan.assignments):
            values = (
                str(row + 1),
                assignment.crystal.destination_plate,
                assignment.crystal.destination_well,
                str(len(assignment.crystal.targets)),
                assignment.fragment.compound_id,
                f"{assignment.fragment.source_plate} / {assignment.fragment.source_well}",
                f"{assignment.total_volume_nl} nL",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self._set_preview_rows(
            self.echo_table,
            tuple(row.values() for row in build_echo_worksheet(plan)),
        )
        self._set_preview_rows(
            self.shifter_table,
            tuple(row.values() for row in build_shifter_worksheet(plan)),
        )
        self._refresh_experiment_id()
        self._refresh_webdb_preview()
        self.draft_changed.emit()

    def _refresh_webdb_preview(self) -> None:
        account = self.mxlive_account
        if account is None:
            self.webdb_table.setRowCount(0)
            self.webdb_status_label.setText("MxLive account: not configured")
            return
        if self.current_plan is None or not self.current_experiment_id:
            self.webdb_table.setRowCount(0)
            _set_webdb_account_status(self.webdb_status_label, account, 0)
            return
        records = build_fragment_labworks(
            self.current_plan,
            experiment_id=self.current_experiment_id,
            protein_name=self.protein_input.text().strip(),
            username=account.username,
            account_id=account.account_id,
        )
        _populate_webdb_table(self.webdb_table, records)
        _set_webdb_account_status(self.webdb_status_label, account, len(records))

    def restore_draft(self, draft: PlanningDraft) -> None:
        widgets = (self.library_input, self.rows_input, self.protein_input,
                   self.volume_input, self.order_input)
        for widget in widgets:
            widget.blockSignals(True)
        index = self.library_input.findData(draft.library_id, Qt.UserRole)
        self.library_input.setCurrentIndex(max(0, index))
        self.library = self.library_input.itemData(
            self.library_input.currentIndex(), Qt.UserRole + 1
        )
        self.rows_input.setText(draft.library_rows)
        self.protein_input.setText(draft.protein)
        self.volume_input.setValue(float(draft.volume_nl))
        order_index = self.order_input.findData(AssignmentOrder(draft.assignment_order))
        self.order_input.setCurrentIndex(max(0, order_index))
        for widget in widgets:
            widget.blockSignals(False)
        self.refresh_plan()

    @staticmethod
    def _set_preview_rows(
        table: QTableWidget, rows: tuple[tuple[str, ...], ...]
    ) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(value))


class RawCrystalEditor(QWidget):
    """Plan selected crystals for SHIFTER without a soaking step."""

    save_worksheet_requested = pyqtSignal()
    finalize_requested = pyqtSignal()
    draft_changed = pyqtSignal()
    webdb_upload_requested = pyqtSignal()

    def __init__(
        self, crystals: tuple[SelectedCrystal, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.crystals = crystals
        self.current_plan: RawCrystalPlan | None = None
        self.current_experiment_id: str | None = None
        self.mxlive_account: MxLiveAccount | None = None
        self.protein_input = QLineEdit()
        self.protein_input.setPlaceholderText("Protein name")
        self.order_input = QComboBox()
        self.order_input.addItem("Selection order", AssignmentOrder.SELECTION)
        self.order_input.addItem("Plate / well order", AssignmentOrder.PLATE_WELL)
        self.experiment_id_label = QLabel("Experiment ID: —")
        self.lifecycle_label = QLabel("Draft · not saved")
        self.finalize_button = QPushButton("Finalize Plan")
        self.save_worksheet_button = QPushButton("Save SHIFTER Worksheet…")
        self.save_worksheet_button.setEnabled(False)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #b00020")
        self.summary_table = QTableWidget(0, 5)
        self.summary_table.setHorizontalHeaderLabels(
            ("Order", "Plate", "Well", "Target", "Selected at")
        )
        self.shifter_table = QTableWidget(0, len(SHIFTER_HEADER))
        self.shifter_table.setHorizontalHeaderLabels(SHIFTER_HEADER)
        for table in (self.summary_table, self.shifter_table):
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
        self.preview_tabs = QTabWidget()
        self.preview_tabs.addTab(self.summary_table, "Summary")
        self.preview_tabs.addTab(self.shifter_table, "SHIFTER Worksheet")
        self.webdb_status_label = QLabel("MxLive account: not configured")
        self.webdb_table = QTableWidget(0, len(LABWORK_COLUMNS))
        self.webdb_table.setHorizontalHeaderLabels(LABWORK_COLUMNS)
        self.webdb_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.webdb_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.webdb_table.verticalHeader().setVisible(False)
        self.webdb_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.webdb_upload_button = QPushButton("Upload Finalized Revision…")
        self.webdb_upload_button.setEnabled(False)
        webdb_widget = QWidget()
        webdb_layout = QVBoxLayout()
        webdb_layout.addWidget(self.webdb_status_label)
        webdb_layout.addWidget(self.webdb_table, 1)
        webdb_layout.addWidget(self.webdb_upload_button)
        webdb_widget.setLayout(webdb_layout)
        self.preview_tabs.addTab(webdb_widget, "WebDB")
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Protein:"))
        controls.addWidget(self.protein_input)
        controls.addWidget(QLabel("Order:"))
        controls.addWidget(self.order_input)
        controls.addWidget(self.experiment_id_label, 1)
        controls.addWidget(self.lifecycle_label)
        controls.addWidget(self.finalize_button)
        controls.addWidget(self.save_worksheet_button)
        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self.error_label)
        layout.addWidget(self.preview_tabs, 1)
        self.setLayout(layout)
        self._experiment_id_provider: Callable[[str], str] | None = None
        self.protein_input.textChanged.connect(self._refresh_experiment_id)
        self.protein_input.textChanged.connect(self.draft_changed.emit)
        self.order_input.currentIndexChanged.connect(self.refresh_plan)
        self.finalize_button.clicked.connect(self.finalize_requested.emit)
        self.save_worksheet_button.clicked.connect(self.save_worksheet_requested.emit)
        self.webdb_upload_button.clicked.connect(self.webdb_upload_requested.emit)
        self.refresh_plan()

    def set_experiment_id_provider(self, provider: Callable[[str], str]) -> None:
        self._experiment_id_provider = provider
        self._refresh_experiment_id()

    def set_crystals(self, crystals: tuple[SelectedCrystal, ...]) -> None:
        self.crystals = crystals
        self.refresh_plan()

    def set_mxlive_account(self, account: MxLiveAccount) -> None:
        self.mxlive_account = account
        self.refresh_plan()

    def restore_draft(self, draft: PlanningDraft) -> None:
        self.protein_input.blockSignals(True)
        self.order_input.blockSignals(True)
        self.protein_input.setText(draft.protein)
        index = self.order_input.findData(AssignmentOrder(draft.assignment_order))
        self.order_input.setCurrentIndex(max(0, index))
        self.protein_input.blockSignals(False)
        self.order_input.blockSignals(False)
        self.refresh_plan()

    def _refresh_experiment_id(self) -> None:
        if self._experiment_id_provider is None:
            self.current_experiment_id = None
            self.experiment_id_label.setText("Experiment ID: —")
            self.save_worksheet_button.setEnabled(False)
            self._refresh_webdb_preview()
            return
        try:
            experiment_id = self._experiment_id_provider(self.protein_input.text())
        except ValueError as error:
            self.current_experiment_id = None
            self.experiment_id_label.setText(f"Experiment ID: {error}")
            self.save_worksheet_button.setEnabled(False)
            self._refresh_webdb_preview()
            return
        self.current_experiment_id = experiment_id
        self.experiment_id_label.setText(f"Experiment ID: {experiment_id}")
        self.save_worksheet_button.setEnabled(self.current_plan is not None)
        self._refresh_webdb_preview()

    def refresh_plan(self) -> None:
        try:
            plan = build_raw_crystal_plan(
                self.crystals, self.order_input.currentData()
            )
            rows = build_shifter_worksheet(plan)
        except ValueError as error:
            self.current_plan = None
            self.error_label.setText(str(error))
            self.summary_table.setRowCount(0)
            self.shifter_table.setRowCount(0)
            self.webdb_table.setRowCount(0)
            self.save_worksheet_button.setEnabled(False)
            self._refresh_webdb_preview()
            self.draft_changed.emit()
            return
        self.current_plan = plan
        self.error_label.setText("")
        self.summary_table.setRowCount(len(plan.selections))
        for row, selection in enumerate(plan.selections):
            crystal = selection.crystal
            values = (str(row + 1), crystal.destination_plate,
                      crystal.destination_well, selection.target.target_id,
                      selection.target.selected_at.isoformat(timespec="seconds"))
            for column, value in enumerate(values):
                self.summary_table.setItem(row, column, QTableWidgetItem(value))
        FragmentScreeningEditor._set_preview_rows(
            self.shifter_table, tuple(row.values() for row in rows)
        )
        self._refresh_experiment_id()
        self._refresh_webdb_preview()
        self.draft_changed.emit()

    def _refresh_webdb_preview(self) -> None:
        account = self.mxlive_account
        if account is None:
            self.webdb_table.setRowCount(0)
            self.webdb_status_label.setText("MxLive account: not configured")
            return
        if self.current_plan is None or not self.current_experiment_id:
            self.webdb_table.setRowCount(0)
            _set_webdb_account_status(self.webdb_status_label, account, 0)
            return
        records = build_raw_crystal_labworks(
            self.current_plan,
            experiment_id=self.current_experiment_id,
            protein_name=self.protein_input.text().strip(),
            username=account.username,
            account_id=account.account_id,
        )
        _populate_webdb_table(self.webdb_table, records)
        _set_webdb_account_status(self.webdb_status_label, account, len(records))


def _populate_webdb_table(table: QTableWidget, records: tuple) -> None:
    table.setRowCount(len(records))
    for row_index, record in enumerate(records):
        payload = record.to_payload()
        values = tuple(str(payload[column]) for column in LABWORK_COLUMNS)
        tooltip = json.dumps(payload, ensure_ascii=False, indent=2)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
            table.setItem(row_index, column, item)


def _set_webdb_account_status(
    label: QLabel, account: MxLiveAccount, record_count: int
) -> None:
    state = "Ready" if account.upload_ready else "Preview only"
    label.setText(
        f"MxLive account: {account.username} · API project_id: "
        f"{account.account_id} · {record_count} records · {state}"
    )
    label.setToolTip("\n".join(account.upload_blockers))


class FragmentScreeningDialog(QDialog):
    """Compatibility wrapper around the embedded planning editor."""

    def __init__(
        self,
        library: FragmentLibrary,
        crystals: tuple[SelectedCrystal, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fragment Screening Plan")
        self.resize(850, 520)
        self.editor = FragmentScreeningEditor(library, crystals, self)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(self.editor, 1)
        layout.addWidget(buttons)
        self.setLayout(layout)
        for name in (
            "library_label",
            "rows_input",
            "volume_input",
            "order_input",
            "error_label",
            "table",
        ):
            setattr(self, name, getattr(self.editor, name))

    @property
    def current_plan(self):
        return self.editor.current_plan

class ViewerWindow(QMainWindow):
    def __init__(
        self,
        repository: RockMakerImageRepository,
        review_store: SQLiteReviewStore | None = None,
        auto_advance_target_count: int = 1,
        settings: ApplicationSettings | None = None,
        preferences_store: JsonUserPreferencesStore | None = None,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.review_store = review_store
        self.settings = settings or DEFAULT_SETTINGS
        self.preferences_store = preferences_store or JsonUserPreferencesStore()
        self.user_preferences = self.preferences_store.load()
        self._trusted_auto_well_image_sets: set[str] = set()
        self._auto_well_opted_out_image_sets: set[str] = set()
        try:
            self.mxlive_account = resolve_mxlive_account(
                self.settings.mxlive_config_path,
                base_url=self.settings.mxlive_base_url,
                beamline=self.settings.mxlive_beamline,
                key_path=self.settings.mxlive_key_path,
                ca_bundle=self.settings.mxlive_ca_bundle,
            )
            self.mxlive_configuration_error: str | None = None
        except MxLiveConfigurationError as error:
            self.mxlive_account = None
            self.mxlive_configuration_error = str(error)
        self.project_controller = ProjectController(
            repository, review_store, auto_advance_target_count
        )
        self.plate: PlateImages | None = None
        self.controller: ReviewController | None = None
        self.calibration_service: WellCalibrationService | None = None
        self.current_calibration: ImageCalibration | None = None
        self._manual_calibration_points: list[tuple[float, float]] | None = None
        self._initial_auto_advance_target_count = auto_advance_target_count
        self._target_summary_window_expansion = 0
        self._planning_project_id: str | None = None
        self._planning_drafts: dict[
            str, list[tuple[str, FragmentScreeningEditor]]
        ] = {}
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
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fit_button = QPushButton("Fit")
        self.fit_button.setToolTip("Fit image to window (0)")
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
        self._refreshing_target_summary = False
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
        self.target_summary_button = QPushButton("View Target Summary")
        self.calibration_label = QLabel("Well calibration: not loaded")
        self.auto_calibration_button = QPushButton("Auto Well")
        self.accept_calibration_button = QPushButton("Accept Well")
        self.accept_calibration_button.setEnabled(False)
        self.manual_calibration_button = QPushButton("Set Well (3 points)")
        self.auto_confirm_plate_checkbox = QCheckBox("Auto-confirm this plate")
        self.auto_confirm_plate_checkbox.setToolTip(
            "Automatically confirm detected wells on this plate when confidence "
            "meets the selected threshold. Plate trust lasts for this session."
        )
        self.auto_confirm_confidence_input = QSpinBox()
        self.auto_confirm_confidence_input.setRange(0, 100)
        self.auto_confirm_confidence_input.setValue(
            self.user_preferences.auto_confirm_confidence_percent
        )
        self.auto_confirm_confidence_input.setPrefix("≥ ")
        self.auto_confirm_confidence_input.setSuffix("%")
        self.auto_confirm_confidence_input.setToolTip(
            f"Saved per user in {self.preferences_store.path}"
        )
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
        image_info_controls = QHBoxLayout()
        image_info_controls.addWidget(self.navigation_label, 1)
        image_info_controls.addWidget(self.zoom_label)
        image_info_controls.addWidget(self.fit_button)
        viewer_layout.addLayout(image_info_controls)
        viewer_layout.addWidget(self.review_summary_label)
        viewer_layout.addWidget(self.calibration_label)
        calibration_actions = QHBoxLayout()
        calibration_actions.addWidget(self.auto_confirm_plate_checkbox)
        calibration_actions.addWidget(self.auto_confirm_confidence_input)
        calibration_actions.addStretch()
        calibration_actions.addWidget(self.auto_calibration_button)
        calibration_actions.addWidget(self.accept_calibration_button)
        calibration_actions.addWidget(self.manual_calibration_button)
        viewer_layout.addLayout(calibration_actions)
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
        sidebar_layout.addWidget(self.target_summary_button)
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

        review_tab = QWidget()
        review_tab_layout = QVBoxLayout()
        review_tab_layout.setContentsMargins(0, 0, 0, 0)
        review_tab_layout.addWidget(splitter)
        review_tab.setLayout(review_tab_layout)

        self.plan_list = QListWidget()
        self.plan_list.setMinimumWidth(210)
        self.plan_list.setToolTip("Draft and finalized plans in the active project")
        self.new_plan_button = QPushButton("+ New Plan")
        self.plan_list_empty_label = QLabel(
            "No plans yet.\nCreate a plan to begin."
        )
        self.plan_list_empty_label.setAlignment(Qt.AlignCenter)
        self.plan_list_empty_label.setStyleSheet("color: #666")
        plan_sidebar_layout = QVBoxLayout()
        plan_sidebar_layout.addWidget(QLabel("Project plans"))
        plan_sidebar_layout.addWidget(self.new_plan_button)
        plan_sidebar_layout.addWidget(self.plan_list_empty_label)
        plan_sidebar_layout.addWidget(self.plan_list, 1)
        plan_sidebar = QWidget()
        plan_sidebar.setLayout(plan_sidebar_layout)

        self.plan_stack = QStackedWidget()
        planning_placeholder = QLabel(
            "Create a plan to assign treatments to the selected crystals."
        )
        planning_placeholder.setAlignment(Qt.AlignCenter)
        self.plan_stack.addWidget(planning_placeholder)
        planning_splitter = QSplitter(Qt.Horizontal)
        planning_splitter.addWidget(plan_sidebar)
        planning_splitter.addWidget(self.plan_stack)
        planning_splitter.setStretchFactor(1, 1)
        planning_splitter.setSizes([230, 870])
        planning_tab = QWidget()
        planning_tab_layout = QVBoxLayout()
        planning_tab_layout.setContentsMargins(0, 0, 0, 0)
        planning_tab_layout.addWidget(planning_splitter)
        planning_tab.setLayout(planning_tab_layout)

        self.main_tabs = QTabWidget()
        self.image_review_tab_index = self.main_tabs.addTab(
            review_tab, "Image Review"
        )
        self.planning_tab_index = self.main_tabs.addTab(planning_tab, "Planning")

        layout = QVBoxLayout()
        layout.addLayout(project_controls)
        layout.addWidget(self.main_tabs, 1)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.target_summary_table = QTableWidget(0, 7)
        self.target_summary_table.setHorizontalHeaderLabels(
            (
                "Plate",
                "Well",
                "Target",
                "X (mm)",
                "Y (mm)",
                "Calibration",
                "Status",
            )
        )
        self.target_summary_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.target_summary_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.target_summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.target_summary_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.target_summary_table.verticalHeader().setVisible(False)
        self.target_summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.target_summary_table.horizontalHeader().setStretchLastSection(True)
        self.target_summary_status_label = QLabel("Ready 0 · Warnings 0")
        self.target_summary_filter = QComboBox()
        self.target_summary_filter.addItem("All targets", "all")
        self.target_summary_filter.addItem("Warnings only", "warnings")
        target_summary_controls = QHBoxLayout()
        target_summary_controls.addWidget(self.target_summary_status_label, 1)
        target_summary_controls.addWidget(self.target_summary_filter)
        self.remove_targets_button = QPushButton("Remove Selected")
        self.accept_valid_auto_wells_button = QPushButton(
            "Accept Valid Auto Wells"
        )
        target_summary_layout = QVBoxLayout()
        target_summary_layout.setContentsMargins(0, 0, 0, 0)
        target_summary_layout.addLayout(target_summary_controls)
        target_summary_layout.addWidget(self.target_summary_table, 1)
        target_summary_layout.addWidget(self.accept_valid_auto_wells_button)
        target_summary_layout.addWidget(self.remove_targets_button)
        target_summary_panel = QWidget()
        target_summary_panel.setLayout(target_summary_layout)
        self.target_summary_dock = QDockWidget("Target Summary", self)
        self.target_summary_dock.setObjectName("target_summary_dock")
        self.target_summary_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.target_summary_dock.setWidget(target_summary_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.target_summary_dock)
        self.target_summary_dock.hide()
        self.view_menu = self.menuBar().addMenu("View")
        self.target_summary_action = self.target_summary_dock.toggleViewAction()
        self.target_summary_action.setText("Target Summary")
        self.view_menu.addAction(self.target_summary_action)

        self.statusBar().addWidget(self.status_message_label)
        self.statusBar().addPermanentWidget(self.save_status_label)
        self.statusBar().addPermanentWidget(self.image_path_status, 1)
        self.statusBar().show()

        self.load_button.clicked.connect(self.load_entered_plate)
        self.target_summary_button.clicked.connect(self.target_summary_dock.show)
        self.new_plan_button.clicked.connect(self._show_new_plan_menu)
        self.plan_list.currentRowChanged.connect(
            lambda row: self.plan_stack.setCurrentIndex(max(0, row + 1))
        )
        self.main_tabs.currentChanged.connect(self._main_tab_changed)
        self.target_summary_dock.visibilityChanged.connect(
            self._target_summary_visibility_changed
        )
        self.target_summary_table.currentCellChanged.connect(
            self._target_summary_current_cell_changed
        )
        self.target_summary_table.customContextMenuRequested.connect(
            self._show_target_summary_context_menu
        )
        self.remove_targets_button.clicked.connect(self._remove_selected_targets)
        self.accept_valid_auto_wells_button.clicked.connect(
            self._accept_valid_auto_wells
        )
        self.target_summary_filter.currentIndexChanged.connect(
            self._refresh_target_summary
        )
        self._delete_targets_shortcut = QShortcut(
            QKeySequence("Delete"), self.target_summary_table
        )
        self._delete_targets_shortcut.setContext(Qt.WidgetShortcut)
        self._delete_targets_shortcut.activated.connect(self._remove_selected_targets)
        self._backspace_targets_shortcut = QShortcut(
            QKeySequence("Backspace"), self.target_summary_table
        )
        self._backspace_targets_shortcut.setContext(Qt.WidgetShortcut)
        self._backspace_targets_shortcut.activated.connect(
            self._remove_selected_targets
        )
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
        self.fit_button.clicked.connect(self.image_canvas.fit_image)
        self.image_canvas.zoom_changed.connect(self._update_zoom_label)
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
        self.accept_calibration_button.clicked.connect(
            self._accept_current_calibration
        )
        self.manual_calibration_button.clicked.connect(self._start_manual_calibration)
        self.auto_confirm_plate_checkbox.toggled.connect(
            self._toggle_auto_confirm_for_active_plate
        )
        self.auto_confirm_confidence_input.valueChanged.connect(
            self._change_auto_confirm_confidence
        )
        self.image_filter_input.currentIndexChanged.connect(self._change_image_filter)
        self.well_input.returnPressed.connect(self._go_to_entered_well)
        self.well_input.editingFinished.connect(self._go_to_entered_well)
        self._update_navigation()
        self._initialize_projects()

    def _open_fragment_screening(self) -> None:
        crystals = self._crystals_for_new_plan("fragment plan")
        if crystals is not None:
            self._add_fragment_plan(None, crystals)

    def _open_raw_crystal_plan(self) -> None:
        crystals = self._crystals_for_new_plan("raw crystal plan")
        if crystals is not None:
            self._add_raw_crystal_plan(crystals)

    def _crystals_for_new_plan(
        self, plan_label: str
    ) -> tuple[SelectedCrystal, ...] | None:
        try:
            crystals = self.project_controller.selected_crystals_for_plan()
            if not crystals:
                raise ValueError("select at least one crystal target first")
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            try:
                candidate_count = (
                    self.project_controller
                    .valid_unconfirmed_automatic_calibration_count()
                )
                warning_count = sum(
                    not summary.is_ready
                    for summary in self.project_controller.project_target_summaries()
                )
            except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError):
                candidate_count = 0
                warning_count = 0
            if not warning_count:
                QMessageBox.warning(self, f"Cannot create {plan_label}", str(error))
                return None
            action = self._planning_calibration_action(
                candidate_count, warning_count
            )
            if action == "review":
                self._review_target_warnings()
                return None
            if action == "accept":
                try:
                    self.project_controller.confirm_valid_automatic_calibrations()
                    self._adopt_active_review()
                    self._sync_project_widgets()
                    crystals = self.project_controller.selected_crystals_for_plan()
                except (
                    ValueError,
                    PlateImagesNotFoundError,
                    ReviewPersistenceError,
                ) as retry_error:
                    remaining = sum(
                        not summary.is_ready
                        for summary in self.project_controller.project_target_summaries()
                    )
                    if remaining:
                        retry_action = self._planning_calibration_action(0, remaining)
                        if retry_action == "review":
                            self._review_target_warnings()
                    else:
                        QMessageBox.warning(
                            self, f"Cannot create {plan_label}", str(retry_error)
                        )
                    return None
            else:
                return None
        return crystals

    def _show_new_plan_menu(self) -> None:
        menu = QMenu(self.new_plan_button)
        raw_action = menu.addAction("Raw Crystal Plan")
        fragment_action = menu.addAction("Fragment Screening")
        menu.addSeparator()
        for label in (
            "Solvent Duration (coming later)",
            "Cryo Plan (coming later)",
            "Custom Soaking (coming later)",
        ):
            menu.addAction(label).setEnabled(False)
        fragment_action.triggered.connect(self._open_fragment_screening)
        raw_action.triggered.connect(self._open_raw_crystal_plan)
        menu.exec_(
            self.new_plan_button.mapToGlobal(
                self.new_plan_button.rect().bottomLeft()
            )
        )

    def _add_fragment_plan(
        self,
        library: FragmentLibrary | None,
        crystals: tuple[SelectedCrystal, ...],
        restored: PlanningDraft | None = None,
    ) -> None:
        project = self.project_controller.active_project
        if project is None:
            raise ValueError("no project is open")
        existing = self._planning_drafts.setdefault(project.id, [])
        name = restored.name if restored else f"Fragment Screening #{len(existing) + 1}"
        editor = FragmentScreeningEditor(library, crystals, self.plan_stack)
        editor.plan_id = restored.id if restored else str(uuid4())
        editor.project_id = project.id
        editor.plan_name = name
        editor.plan_created_at = restored.created_at if restored else datetime.now(timezone.utc)
        editor.last_revision = None
        editor.last_revision_snapshot = None
        editor.autosave_timer = QTimer(editor)
        editor.autosave_timer.setSingleShot(True)
        editor.autosave_timer.setInterval(750)
        editor.autosave_timer.timeout.connect(
            lambda selected_editor=editor: self._persist_planning_draft(selected_editor)
        )
        editor.set_library_choices(self._fragment_library_choices())
        if restored is not None:
            editor.restore_draft(restored)
        editor.refresh_libraries_button.setToolTip(
            str(self.settings.fragment_library_directory)
        )
        editor.library_refresh_requested.connect(
            self._refresh_fragment_library_choices
        )
        editor.set_experiment_id_provider(self._suggest_fragment_experiment_id)
        if self.mxlive_account is not None:
            editor.set_mxlive_account(self.mxlive_account)
        elif self.mxlive_configuration_error:
            editor.webdb_status_label.setText(self.mxlive_configuration_error)
        editor.save_worksheets_requested.connect(
            lambda selected_editor=editor: self._save_fragment_worksheets(
                selected_editor
            )
        )
        editor.finalize_requested.connect(
            lambda selected_editor=editor: self._finalize_fragment_plan(selected_editor)
        )
        editor.webdb_upload_requested.connect(
            lambda selected_editor=editor: self._upload_fragment_labworks(
                selected_editor
            )
        )
        editor.draft_changed.connect(
            lambda selected_editor=editor: self._planning_draft_changed(selected_editor)
        )
        existing.append((name, editor))
        self.plan_stack.addWidget(editor)
        self.plan_list.addItem(name)
        self.plan_list_empty_label.hide()
        self.plan_list.setCurrentRow(self.plan_list.count() - 1)
        self.main_tabs.setCurrentIndex(self.planning_tab_index)
        if restored is None:
            self._persist_planning_draft(editor)
        else:
            revisions = self.review_store.list_plan_revisions(editor.plan_id) if self.review_store else ()
            if revisions:
                editor.last_revision = revisions[-1]
                editor.last_revision_snapshot = revisions[-1].snapshot_json
                if self._fragment_plan_snapshot(editor) == editor.last_revision_snapshot:
                    editor.lifecycle_label.setText(f"Finalized r{revisions[-1].revision}")
                    self._set_plan_list_status(editor, f"Finalized r{revisions[-1].revision}")
                    self._sync_webdb_upload_state(editor)
                else:
                    editor.lifecycle_label.setText("Draft · restored with changes")
                    self._set_plan_list_status(editor, "Draft")

    def _planning_draft_changed(self, editor: FragmentScreeningEditor) -> None:
        if not hasattr(editor, "autosave_timer"):
            return
        editor.lifecycle_label.setText("Draft · saving…")
        editor.webdb_upload_button.setEnabled(False)
        self._set_plan_list_status(editor, "Draft")
        editor.autosave_timer.start()

    def _add_raw_crystal_plan(
        self, crystals: tuple[SelectedCrystal, ...],
        restored: PlanningDraft | None = None,
    ) -> None:
        project = self.project_controller.active_project
        if project is None:
            raise ValueError("no project is open")
        existing = self._planning_drafts.setdefault(project.id, [])
        name = restored.name if restored else f"Raw Crystal Plan #{sum(isinstance(item[1], RawCrystalEditor) for item in existing) + 1}"
        editor = RawCrystalEditor(crystals, self.plan_stack)
        editor.plan_id = restored.id if restored else str(uuid4())
        editor.project_id = project.id
        editor.plan_name = name
        editor.plan_created_at = restored.created_at if restored else datetime.now(timezone.utc)
        editor.last_revision = None
        editor.last_revision_snapshot = None
        editor.autosave_timer = QTimer(editor)
        editor.autosave_timer.setSingleShot(True)
        editor.autosave_timer.setInterval(750)
        editor.autosave_timer.timeout.connect(
            lambda selected_editor=editor: self._persist_raw_crystal_draft(selected_editor)
        )
        if restored is not None:
            editor.restore_draft(restored)
        editor.set_experiment_id_provider(self._suggest_raw_crystal_experiment_id)
        if self.mxlive_account is not None:
            editor.set_mxlive_account(self.mxlive_account)
        elif self.mxlive_configuration_error:
            editor.webdb_status_label.setText(self.mxlive_configuration_error)
        editor.draft_changed.connect(
            lambda selected_editor=editor: self._raw_crystal_draft_changed(selected_editor)
        )
        editor.finalize_requested.connect(
            lambda selected_editor=editor: self._finalize_raw_crystal_plan(selected_editor)
        )
        editor.webdb_upload_requested.connect(
            lambda selected_editor=editor: self._upload_raw_labworks(selected_editor)
        )
        editor.save_worksheet_requested.connect(
            lambda selected_editor=editor: self._save_raw_crystal_worksheet(selected_editor)
        )
        existing.append((name, editor))
        self.plan_stack.addWidget(editor)
        self.plan_list.addItem(name)
        self.plan_list_empty_label.hide()
        self.plan_list.setCurrentRow(self.plan_list.count() - 1)
        self.main_tabs.setCurrentIndex(self.planning_tab_index)
        if restored is None:
            self._persist_raw_crystal_draft(editor)
        elif self.review_store is not None:
            revisions = self.review_store.list_plan_revisions(editor.plan_id)
            if revisions:
                editor.last_revision = revisions[-1]
                editor.last_revision_snapshot = revisions[-1].snapshot_json
                if self._raw_crystal_snapshot(editor) == editor.last_revision_snapshot:
                    editor.lifecycle_label.setText(f"Finalized r{revisions[-1].revision}")
                    self._set_plan_list_status(editor, f"Finalized r{revisions[-1].revision}")
                    self._sync_webdb_upload_state(editor)
                else:
                    editor.lifecycle_label.setText("Draft · restored with changes")
                    self._set_plan_list_status(editor, "Draft")

    def _raw_crystal_draft_changed(self, editor: RawCrystalEditor) -> None:
        if not hasattr(editor, "autosave_timer"):
            return
        editor.lifecycle_label.setText("Draft · saving…")
        editor.webdb_upload_button.setEnabled(False)
        self._set_plan_list_status(editor, "Draft")
        editor.autosave_timer.start()

    def _persist_raw_crystal_draft(self, editor: RawCrystalEditor) -> None:
        if self.review_store is None:
            editor.lifecycle_label.setText("Draft · memory only")
            return
        now = datetime.now(timezone.utc)
        draft = PlanningDraft(
            editor.plan_id, editor.project_id, "raw_crystal", editor.plan_name,
            None, "", editor.protein_input.text(), "0",
            editor.order_input.currentData().value, editor.plan_created_at, now,
        )
        try:
            self.review_store.save_planning_draft(draft)
        except ReviewPersistenceError as error:
            editor.lifecycle_label.setText("Draft · save failed")
            self._show_persistence_error(error)
            return
        snapshot = self._raw_crystal_snapshot(editor)
        if editor.last_revision is not None and snapshot == editor.last_revision_snapshot:
            editor.lifecycle_label.setText(f"Finalized r{editor.last_revision.revision}")
            self._set_plan_list_status(editor, f"Finalized r{editor.last_revision.revision}")
            self._sync_webdb_upload_state(editor)
        else:
            editor.lifecycle_label.setText("Draft · saved")
            self._set_plan_list_status(editor, "Draft")

    def _raw_crystal_snapshot(self, editor: RawCrystalEditor) -> str | None:
        plan = editor.current_plan
        if plan is None:
            return None
        payload = {
            "schema": 1, "plan_type": "raw_crystal",
            "protein": editor.protein_input.text(),
            "assignment_order": plan.assignment_order.value,
            "selections": [
                {"image_key": selection.crystal.image_key,
                 "image_path": selection.crystal.image_path,
                 "plate": selection.crystal.destination_plate,
                 "well": selection.crystal.destination_well,
                 "plate_format_id": selection.crystal.plate_format_id,
                 "target": {"id": selection.target.target_id,
                            "x_mm": str(selection.target.x_mm),
                            "y_mm": str(selection.target.y_mm),
                            "selected_at": selection.target.selected_at.isoformat()}}
                for selection in plan.selections
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _suggest_raw_crystal_experiment_id(self, protein: str) -> str:
        existing = self._worksheet_exporter().existing_experiment_ids()
        if self.review_store is not None:
            existing.update(self.review_store.reserved_experiment_ids())
        return suggest_experiment_id("RawCrystal", protein, existing)

    def _finalize_raw_crystal_plan(self, editor: RawCrystalEditor) -> PlanRevision | None:
        if self.review_store is None:
            QMessageBox.warning(self, "Cannot finalize plan", "Open a writable review database first.")
            return None
        snapshot = self._raw_crystal_snapshot(editor)
        if snapshot is None:
            QMessageBox.warning(self, "Cannot finalize plan", "The raw crystal plan is not valid.")
            return None
        self._persist_raw_crystal_draft(editor)
        if snapshot == editor.last_revision_snapshot:
            return editor.last_revision
        try:
            revision = self.review_store.finalize_plan_revision(
                PlanRevision(str(uuid4()), editor.plan_id, 0,
                             self._suggest_raw_crystal_experiment_id(editor.protein_input.text()),
                             snapshot, getpass.getuser(), datetime.now(timezone.utc))
            )
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot finalize plan", str(error))
            return None
        editor.last_revision = revision
        editor.last_revision_snapshot = snapshot
        editor.lifecycle_label.setText(f"Finalized r{revision.revision}")
        self._set_plan_list_status(editor, f"Finalized r{revision.revision}")
        self._sync_webdb_upload_state(editor)
        return revision

    def _set_plan_list_status(self, editor: FragmentScreeningEditor, status: str) -> None:
        project = self.project_controller.active_project
        if project is None or project.id != self._planning_project_id:
            return
        for index, (_, candidate) in enumerate(self._planning_drafts.get(project.id, [])):
            if candidate is editor and index < self.plan_list.count():
                self.plan_list.item(index).setText(f"{editor.plan_name} · {status}")
                return

    def _persist_planning_draft(self, editor: FragmentScreeningEditor) -> None:
        if self.review_store is None:
            editor.lifecycle_label.setText("Draft · memory only")
            return
        now = datetime.now(timezone.utc)
        draft = PlanningDraft(
            editor.plan_id, editor.project_id, "fragment_screening", editor.plan_name,
            editor.library_input.currentData(Qt.UserRole), editor.rows_input.text(),
            editor.protein_input.text(), str(editor.volume_input.value()),
            editor.order_input.currentData().value,
            editor.plan_created_at, now,
        )
        try:
            self.review_store.save_planning_draft(draft)
        except ReviewPersistenceError as error:
            editor.lifecycle_label.setText("Draft · save failed")
            self._show_persistence_error(error)
            return
        current_snapshot = self._fragment_plan_snapshot(editor)
        if editor.last_revision is not None and editor.last_revision_snapshot == current_snapshot:
            editor.lifecycle_label.setText(f"Finalized r{editor.last_revision.revision}")
            self._set_plan_list_status(editor, f"Finalized r{editor.last_revision.revision}")
            self._sync_webdb_upload_state(editor)
        else:
            editor.lifecycle_label.setText("Draft · saved")
            self._set_plan_list_status(editor, "Draft")

    def _fragment_plan_snapshot(self, editor: FragmentScreeningEditor) -> str | None:
        plan = editor.current_plan
        if plan is None:
            return None
        payload = {
            "schema": 1,
            "plan_type": "fragment_screening",
            "protein": editor.protein_input.text(),
            "library_name": plan.library.name,
            "library_id": editor.library_input.currentData(Qt.UserRole),
            "library_rows": editor.rows_input.text(),
            "volume_per_crystal_nl": str(plan.volume_per_crystal_nl),
            "assignment_order": plan.assignment_order.value,
            "assignments": [
                {
                    "image_key": item.crystal.image_key,
                    "image_path": item.crystal.image_path,
                    "plate": item.crystal.destination_plate,
                    "well": item.crystal.destination_well,
                    "plate_format_id": item.crystal.plate_format_id,
                    "fragment": {
                        "vendor": item.fragment.vendor,
                        "library": item.fragment.library,
                        "number": item.fragment.number,
                        "compound_id": item.fragment.compound_id,
                        "formula": item.fragment.formula,
                        "molecular_weight": str(item.fragment.molecular_weight),
                        "smiles": item.fragment.smiles,
                        "concentration_mm": str(item.fragment.concentration_mm),
                        "solvent": item.fragment.solvent,
                        "source_plate": item.fragment.source_plate,
                        "source_well": item.fragment.source_well,
                    },
                    "targets": [
                        {"id": transfer.target.target_id,
                         "x_mm": str(transfer.target.x_mm),
                         "y_mm": str(transfer.target.y_mm),
                         "selected_at": transfer.target.selected_at.isoformat(),
                         "volume_nl": str(transfer.volume_nl)}
                        for transfer in item.transfers
                    ],
                }
                for item in plan.assignments
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _finalize_fragment_plan(self, editor: FragmentScreeningEditor) -> PlanRevision | None:
        if self.review_store is None:
            QMessageBox.warning(self, "Cannot finalize plan", "Open a writable review database first.")
            return None
        snapshot = self._fragment_plan_snapshot(editor)
        if snapshot is None:
            QMessageBox.warning(self, "Cannot finalize plan", "The fragment plan is not valid.")
            return None
        self._persist_planning_draft(editor)
        if editor.last_revision_snapshot == snapshot:
            return editor.last_revision
        try:
            experiment_id = self._suggest_fragment_experiment_id(editor.protein_input.text())
            revision = self.review_store.finalize_plan_revision(
                PlanRevision(str(uuid4()), editor.plan_id, 0, experiment_id, snapshot,
                             getpass.getuser(), datetime.now(timezone.utc))
            )
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot finalize plan", str(error))
            return None
        editor.last_revision = revision
        editor.last_revision_snapshot = snapshot
        editor.lifecycle_label.setText(f"Finalized r{revision.revision}")
        self._set_plan_list_status(editor, f"Finalized r{revision.revision}")
        self._sync_webdb_upload_state(editor)
        return revision

    def _sync_webdb_upload_state(self, editor) -> None:
        editor.webdb_upload_button.setEnabled(False)
        account = self.mxlive_account
        revision = getattr(editor, "last_revision", None)
        if account is None or revision is None or not account.upload_ready:
            return
        if isinstance(editor, RawCrystalEditor):
            snapshot = self._raw_crystal_snapshot(editor)
        else:
            snapshot = self._fragment_plan_snapshot(editor)
        if snapshot != editor.last_revision_snapshot:
            return
        if self.review_store is not None:
            prior = self.review_store.list_webdb_uploads(revision.id)
            completed = next(
                (event for event in reversed(prior)
                 if event.status in ("succeeded", "partial")),
                None,
            )
            if completed is not None:
                state = (
                    "Uploaded" if completed.status == "succeeded"
                    else "Partial upload · review MxLive"
                )
                editor.webdb_status_label.setText(
                    editor.webdb_status_label.text() + f" · {state}"
                )
                editor.webdb_upload_button.setToolTip(
                    "This revision is locked to prevent duplicate records."
                )
                return
        editor.webdb_upload_button.setEnabled(True)
        editor.webdb_upload_button.setToolTip(
            "Upload this exact finalized revision to MxLive labworks."
        )

    def _upload_fragment_labworks(self, editor: FragmentScreeningEditor) -> None:
        if editor.current_plan is None:
            return
        self._upload_labworks(
            editor,
            build_fragment_labworks(
                editor.current_plan,
                experiment_id=editor.last_revision.experiment_id
                if editor.last_revision else "",
                protein_name=editor.protein_input.text().strip(),
                username=self.mxlive_account.username if self.mxlive_account else "",
                account_id=self.mxlive_account.account_id if self.mxlive_account else "",
            ),
        )

    def _upload_raw_labworks(self, editor: RawCrystalEditor) -> None:
        if editor.current_plan is None:
            return
        self._upload_labworks(
            editor,
            build_raw_crystal_labworks(
                editor.current_plan,
                experiment_id=editor.last_revision.experiment_id
                if editor.last_revision else "",
                protein_name=editor.protein_input.text().strip(),
                username=self.mxlive_account.username if self.mxlive_account else "",
                account_id=self.mxlive_account.account_id if self.mxlive_account else "",
            ),
        )

    def _upload_labworks(self, editor, records: tuple) -> None:
        account = self.mxlive_account
        revision = getattr(editor, "last_revision", None)
        snapshot = (
            self._raw_crystal_snapshot(editor)
            if isinstance(editor, RawCrystalEditor)
            else self._fragment_plan_snapshot(editor)
        )
        if (
            account is None or revision is None
            or snapshot != editor.last_revision_snapshot
        ):
            QMessageBox.warning(
                self, "Cannot upload", "Finalize the current plan revision first."
            )
            return
        if not account.upload_ready:
            QMessageBox.warning(
                self, "Cannot upload", "\n".join(account.upload_blockers)
            )
            return
        endpoint = (
            f"{account.base_url.rstrip('/')}/upload_labworks/"
            f"{account.beamline}/"
        )
        answer = QMessageBox.question(
            self,
            "Upload finalized revision",
            f"Upload {len(records)} records for {revision.experiment_id}?\n\n"
            f"Account: {account.username}\n"
            f"API project_id: {account.account_id}\n"
            f"Endpoint: {endpoint}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        payload = tuple(record.to_payload() for record in records)
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        attempted_at = datetime.now(timezone.utc)
        response_json = None
        error_message = None
        status = "failed"
        try:
            client = LegacyMxLiveWriteClient(
                account.base_url, account.beamline, account.username,
                account.key_path, ca_bundle=account.ca_bundle,
                timeout_seconds=self.settings.mxlive_timeout_seconds,
            )
            response = client.upload_labworks(payload)
            response_json = json.dumps(
                response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            status = "succeeded"
        except MxLivePartialWriteError as error:
            status = "partial"
            error_message = str(error)
        except (MxLiveWriteError, ValueError) as error:
            error_message = str(error)
        event = WebDBUploadEvent(
            str(uuid4()), revision.id, account.username, account.account_id,
            endpoint, attempted_at, status, len(records), payload_json,
            response_json, error_message,
        )
        if self.review_store is not None:
            try:
                self.review_store.record_webdb_upload(event)
            except ReviewPersistenceError as error:
                QMessageBox.critical(
                    self, "Upload audit could not be saved",
                    "MxLive may have accepted the upload, but its local audit record "
                    f"could not be saved:\n{error}",
                )
                editor.webdb_upload_button.setEnabled(False)
                return
        if status == "succeeded":
            self._sync_webdb_upload_state(editor)
            QMessageBox.information(
                self, "WebDB upload complete",
                f"Uploaded {len(records)} records for {revision.experiment_id}.",
            )
        elif status == "partial":
            self._sync_webdb_upload_state(editor)
            QMessageBox.critical(
                self, "WebDB upload partially completed",
                error_message or "Some records may have been uploaded.",
            )
        else:
            editor.webdb_status_label.setText(
                editor.webdb_status_label.text() + " · Last upload failed"
            )
            QMessageBox.critical(self, "WebDB upload failed", error_message or "Unknown error")

    def _fragment_library_choices(
        self,
    ) -> tuple[tuple[str, str, FragmentLibrary], ...]:
        choices = []
        library_directory = self.settings.fragment_library_directory
        if not library_directory.is_dir():
            return ()
        for path in sorted(
            library_directory.glob("*.csv"),
            key=lambda item: item.name.casefold(),
        ):
            try:
                library = load_fragment_library(path)
            except FragmentLibraryCsvError:
                continue
            choices.append(
                (
                    str(path.resolve()),
                    f"{path.name} · {len(library.fragments)} rows",
                    library,
                )
            )
        return tuple(choices)

    def _refresh_fragment_library_choices(self) -> None:
        choices = self._fragment_library_choices()
        for drafts in self._planning_drafts.values():
            for _, draft_editor in drafts:
                if isinstance(draft_editor, FragmentScreeningEditor):
                    draft_editor.set_library_choices(choices)
        self.status_message_label.show_message(
            f"Found {len(choices)} libraries in "
            f"{self.settings.fragment_library_directory}",
            4000,
        )

    def _worksheet_exporter(self) -> WorksheetExporter:
        return WorksheetExporter(self.settings, getpass.getuser())

    def _suggest_fragment_experiment_id(self, protein: str) -> str:
        exporter = self._worksheet_exporter()
        existing = exporter.existing_experiment_ids()
        if self.review_store is not None:
            existing.update(self.review_store.reserved_experiment_ids())
        return suggest_experiment_id(
            "FragSC", protein, existing
        )

    def _save_fragment_worksheets(
        self, editor: FragmentScreeningEditor
    ) -> None:
        plan = editor.current_plan
        if plan is None:
            QMessageBox.warning(
                self, "Cannot save worksheets", "The fragment plan is not valid."
            )
            return
        assignment_order = self._choose_worksheet_assignment_order()
        if assignment_order is None:
            return
        order_index = editor.order_input.findData(assignment_order)
        if order_index != editor.order_input.currentIndex():
            editor.order_input.setCurrentIndex(order_index)
            plan = editor.current_plan
        if plan is None:
            QMessageBox.warning(
                self, "Cannot save worksheets", "The reordered plan is not valid."
            )
            return
        revision = self._finalize_fragment_plan(editor)
        if revision is None:
            return
        experiment_id = revision.experiment_id
        exporter = self._worksheet_exporter()
        try:
            result = exporter.export(plan, experiment_id)
        except WorksheetDestinationUnavailable as error:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Critical)
            dialog.setWindowTitle("Worksheet destination unavailable")
            dialog.setText(str(error))
            dialog.setInformativeText(
                "The worksheets were not delivered to the instrument folders. "
                "Choose an alternate output root?"
            )
            choose_button = dialog.addButton(
                "Choose Alternate Location…", QMessageBox.ActionRole
            )
            dialog.addButton(QMessageBox.Cancel)
            dialog.exec_()
            if dialog.clickedButton() is not choose_button:
                self._record_worksheet_export(revision, "failed", error=str(error))
                return
            selected = QFileDialog.getExistingDirectory(
                self, "Choose alternate worksheet output root", str(Path.home())
            )
            if not selected:
                self._record_worksheet_export(revision, "cancelled", error=str(error))
                return
            try:
                result = exporter.export_to_alternate_root(
                    plan, experiment_id, Path(selected)
                )
            except WorksheetDestinationUnavailable as fallback_error:
                self._record_worksheet_export(
                    revision, "failed", error=str(fallback_error)
                )
                QMessageBox.critical(
                    self, "Could not save worksheets", str(fallback_error)
                )
                return
        self._record_worksheet_export(revision, "succeeded", result=result)
        editor.experiment_id_label.setText(
            f"Experiment ID: {result.experiment_id} · Saved as {result.file_stem}"
        )
        QMessageBox.information(
            self,
            "Worksheets saved",
            "ECHO:\n"
            f"{result.echo_path}\n\nSHIFTER 1:\n{result.shifter1_path}\n\n"
            f"SHIFTER 2:\n{result.shifter2_path}",
        )

    def _record_worksheet_export(
        self, revision: PlanRevision, status: str, *, result=None, error: str | None = None
    ) -> None:
        if self.review_store is None:
            return
        event = WorksheetExportEvent(
            str(uuid4()), revision.id, getpass.getuser(), datetime.now(timezone.utc), status,
            str(result.echo_path) if result and hasattr(result, "echo_path") else None,
            str(result.shifter1_path) if result else None,
            str(result.shifter2_path) if result else None,
            error,
        )
        try:
            self.review_store.record_worksheet_export(event)
        except ReviewPersistenceError as persistence_error:
            self._show_persistence_error(persistence_error)

    def _save_raw_crystal_worksheet(self, editor: RawCrystalEditor) -> None:
        plan = editor.current_plan
        if plan is None:
            QMessageBox.warning(self, "Cannot save worksheet", "The raw crystal plan is not valid.")
            return
        assignment_order = self._choose_worksheet_assignment_order()
        if assignment_order is None:
            return
        index = editor.order_input.findData(assignment_order)
        if index != editor.order_input.currentIndex():
            editor.order_input.setCurrentIndex(index)
            plan = editor.current_plan
        revision = self._finalize_raw_crystal_plan(editor)
        if revision is None or plan is None:
            return
        exporter = self._worksheet_exporter()
        try:
            result = exporter.export_shifter(plan, revision.experiment_id)
        except WorksheetDestinationUnavailable as error:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Critical)
            dialog.setWindowTitle("SHIFTER destination unavailable")
            dialog.setText(str(error))
            dialog.setInformativeText(
                "No ECHO worksheet is required for a Raw Crystal Plan. "
                "Choose an alternate root for the two SHIFTER worksheets?"
            )
            choose_button = dialog.addButton(
                "Choose Alternate Location…", QMessageBox.ActionRole
            )
            dialog.addButton(QMessageBox.Cancel)
            dialog.exec_()
            if dialog.clickedButton() is not choose_button:
                self._record_worksheet_export(revision, "failed", error=str(error))
                return
            selected = QFileDialog.getExistingDirectory(
                self, "Choose alternate worksheet output root", str(Path.home())
            )
            if not selected:
                self._record_worksheet_export(revision, "cancelled", error=str(error))
                return
            try:
                result = exporter.export_shifter_to_alternate_root(
                    plan, revision.experiment_id, Path(selected)
                )
            except WorksheetDestinationUnavailable as fallback_error:
                self._record_worksheet_export(
                    revision, "failed", error=str(fallback_error)
                )
                QMessageBox.critical(self, "Could not save worksheet", str(fallback_error))
                return
        self._record_worksheet_export(revision, "succeeded", result=result)
        editor.experiment_id_label.setText(
            f"Experiment ID: {result.experiment_id} · Saved as {result.file_stem}"
        )
        QMessageBox.information(
            self, "SHIFTER worksheets saved",
            f"SHIFTER 1:\n{result.shifter1_path}\n\nSHIFTER 2:\n{result.shifter2_path}",
        )

    def _choose_worksheet_assignment_order(self) -> AssignmentOrder | None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Question)
        dialog.setWindowTitle("Worksheet assignment order")
        dialog.setText("How should fragments be assigned in the worksheets?")
        dialog.setInformativeText(
            "Changing the order reassigns fragments and updates all three previews."
        )
        selection_button = dialog.addButton(
            "Selection Order", QMessageBox.AcceptRole
        )
        plate_button = dialog.addButton(
            "Plate / Well Order", QMessageBox.ActionRole
        )
        dialog.addButton(QMessageBox.Cancel)
        dialog.exec_()
        clicked = dialog.clickedButton()
        if clicked is selection_button:
            return AssignmentOrder.SELECTION
        if clicked is plate_button:
            return AssignmentOrder.PLATE_WELL
        return None

    def _main_tab_changed(self, index: int) -> None:
        if index != self.planning_tab_index:
            return
        editor = self.plan_stack.currentWidget()
        if not isinstance(editor, (FragmentScreeningEditor, RawCrystalEditor)):
            return
        try:
            crystals = self.project_controller.selected_crystals_for_plan()
            if not crystals:
                raise ValueError("select at least one crystal target first")
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            editor.current_plan = None
            editor.error_label.setText(
                f"Targets changed: {error}. Review warnings in Image Review."
            )
            if isinstance(editor, FragmentScreeningEditor):
                editor.table.setRowCount(0)
                editor.echo_table.setRowCount(0)
                editor.shifter_table.setRowCount(0)
            else:
                editor.summary_table.setRowCount(0)
                editor.shifter_table.setRowCount(0)
            return
        editor.set_crystals(crystals)

    def _switch_planning_project(self, project_id: str | None) -> None:
        if self._planning_project_id == project_id:
            return
        while self.plan_stack.count() > 1:
            widget = self.plan_stack.widget(1)
            self.plan_stack.removeWidget(widget)
            widget.setParent(None)
        self.plan_list.clear()
        self.plan_list_empty_label.show()
        self._planning_project_id = project_id
        if project_id is None:
            return
        if project_id not in self._planning_drafts and self.review_store is not None:
            self._planning_drafts[project_id] = []
            try:
                crystals = self.project_controller.selected_crystals_for_plan()
            except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError):
                crystals = ()
            for draft in self.review_store.load_planning_drafts(project_id):
                if draft.plan_type == "fragment_screening":
                    self._add_fragment_plan(None, crystals, draft)
                elif draft.plan_type == "raw_crystal":
                    self._add_raw_crystal_plan(crystals, draft)
            return
        for name, editor in self._planning_drafts.get(project_id, []):
            editor.setParent(self.plan_stack)
            self.plan_stack.addWidget(editor)
            self.plan_list.addItem(name)
        if self.plan_list.count():
            self.plan_list_empty_label.hide()
            self.plan_list.setCurrentRow(0)

    def _planning_calibration_action(
        self, acceptable_wells: int, warning_targets: int
    ) -> str:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("Target calibration needs review")
        dialog.setText(f"{warning_targets} target(s) are not ready for planning.")
        if acceptable_wells:
            dialog.setInformativeText(
                f"{acceptable_wells} valid automatically detected well(s) can be "
                "confirmed now. Missing calibrations and targets outside a well "
                "will remain blocked and will not be omitted from the plan."
            )
            accept_button = dialog.addButton(
                f"Accept Valid ({acceptable_wells}) & Continue",
                QMessageBox.AcceptRole,
            )
        else:
            dialog.setInformativeText(
                "Missing calibrations and targets outside a well are not omitted. "
                "Review and resolve every warning before creating a plan."
            )
            accept_button = None
        review_button = dialog.addButton("Review Warnings", QMessageBox.ActionRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.exec_()
        clicked = dialog.clickedButton()
        if clicked is accept_button:
            return "accept"
        if clicked is review_button:
            return "review"
        return "cancel"

    def _review_target_warnings(self) -> None:
        self.main_tabs.setCurrentIndex(self.image_review_tab_index)
        warning_index = self.target_summary_filter.findData("warnings")
        self.target_summary_filter.setCurrentIndex(warning_index)
        self.target_summary_dock.show()
        self.target_summary_dock.raise_()
        self._refresh_target_summary()
        if self.target_summary_table.rowCount():
            self.target_summary_table.setCurrentCell(0, 0)
            self.target_summary_table.setFocus(Qt.OtherFocusReason)

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

    def _target_summary_visibility_changed(self, visible: bool) -> None:
        if visible:
            self._refresh_target_summary()
        if (
            not self.isVisible()
            or self.isMaximized()
            or self.isFullScreen()
            or self.target_summary_dock.isFloating()
        ):
            return
        if visible and self._target_summary_window_expansion == 0:
            self._target_summary_window_expansion = max(
                self.target_summary_dock.width(),
                self.target_summary_dock.sizeHint().width(),
            )
        elif not visible and self._target_summary_window_expansion:
            contraction = self._target_summary_window_expansion
            self._target_summary_window_expansion = 0
            QTimer.singleShot(
                0, lambda amount=contraction: self._shrink_after_summary_close(amount)
            )

    def _shrink_after_summary_close(self, contraction: int) -> None:
        if (
            not self.isVisible()
            or self.target_summary_dock.isVisible()
            or self.isMaximized()
            or self.isFullScreen()
        ):
            return
        target_width = max(
            self.minimumWidth(),
            self.width() - contraction,
        )
        self.resize(target_width, self.height())

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
            self.accept_calibration_button.setEnabled(False)
            self.auto_confirm_plate_checkbox.setEnabled(False)
            self.auto_confirm_plate_checkbox.setChecked(False)
            self._update_navigation()
            return
        self.calibration_service = None
        active_image_set = self.project_controller.active_image_set
        if (
            active_image_set is not None
            and active_image_set.id not in self._auto_well_opted_out_image_sets
        ):
            self._trusted_auto_well_image_sets.add(active_image_set.id)
        self.auto_confirm_plate_checkbox.blockSignals(True)
        self.auto_confirm_plate_checkbox.setEnabled(active_image_set is not None)
        self.auto_confirm_plate_checkbox.setChecked(
            active_image_set is not None
            and active_image_set.id in self._trusted_auto_well_image_sets
        )
        self.auto_confirm_plate_checkbox.blockSignals(False)
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
        self._switch_planning_project(active.id if active is not None else None)
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
        self._refresh_target_summary()

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

    def _refresh_target_summary(self) -> None:
        if not self.target_summary_dock.isVisible():
            return
        current_item = self.target_summary_table.item(
            self.target_summary_table.currentRow(), 0
        )
        current_target_id = (
            current_item.data(Qt.UserRole + 2) if current_item is not None else None
        )
        try:
            all_summaries = self.project_controller.project_target_summaries()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            self.status_message_label.show_message(
                f"Target summary unavailable: {error}"
            )
            return
        ready_count = sum(summary.is_ready for summary in all_summaries)
        warning_count = len(all_summaries) - ready_count
        self.target_summary_status_label.setText(
            f"Ready {ready_count} · Warnings {warning_count}"
        )
        try:
            acceptable_count = (
                self.project_controller
                .valid_unconfirmed_automatic_calibration_count()
            )
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError):
            acceptable_count = 0
        self.accept_valid_auto_wells_button.setText(
            f"Accept Valid Auto Wells ({acceptable_count})"
        )
        self.accept_valid_auto_wells_button.setEnabled(acceptable_count > 0)
        summaries = (
            tuple(summary for summary in all_summaries if not summary.is_ready)
            if self.target_summary_filter.currentData() == "warnings"
            else all_summaries
        )
        project = self.project_controller.active_project
        image_sets = {item.id: item for item in project.image_sets} if project else {}
        self._refreshing_target_summary = True
        restored_row = None
        try:
            self.target_summary_table.setSortingEnabled(False)
            self.target_summary_table.clearContents()
            self.target_summary_table.setRowCount(len(summaries))
            for row, summary in enumerate(summaries):
                image_set = image_sets[summary.image_set_id]
                plate_format = plate_format_by_id(image_set.plate_format_id)
                well = (
                    str(
                        plate_format.address_for(
                            summary.image.well_number, summary.image.drop_number
                        )
                    )
                    if plate_format is not None
                    else f"{summary.image.well_number}/d{summary.image.drop_number}"
                )
                if summary.calibration is None:
                    x_mm = y_mm = "—"
                    calibration_status = "Missing"
                else:
                    x_value, y_value = summary.calibration.pixel_to_mm(
                        summary.target.x_px, summary.target.y_px
                    )
                    x_mm, y_mm = f"{x_value:.3f}", f"{y_value:.3f}"
                    method = (
                        "Manual"
                        if summary.calibration.method
                        is CalibrationMethod.MANUAL_THREE_POINT
                        else "Auto"
                    )
                    confirmation = (
                        "Confirmed"
                        if summary.calibration.confirmed
                        else "Unconfirmed"
                    )
                    calibration_status = (
                        f"{method} {summary.calibration.confidence:.0%} · "
                        f"{confirmation}"
                    )
                issue_labels = {
                    TargetValidationIssue.CALIBRATION_MISSING: "Calibration missing",
                    TargetValidationIssue.CALIBRATION_UNCONFIRMED: "Unconfirmed calibration",
                    TargetValidationIssue.OUTSIDE_WELL: "Outside well",
                }
                validation_status = (
                    " · ".join(
                        issue_labels[issue]
                        for issue in summary.validation_issues
                    )
                    or "Ready"
                )
                values = (
                    summary.image.plate_code,
                    well,
                    str(summary.target_number),
                    x_mm,
                    y_mm,
                    calibration_status,
                    validation_status,
                )
                tooltip = (
                    f"Pixel: ({summary.target.x_px:.1f}, {summary.target.y_px:.1f})\n"
                    f"{summary.image.path.resolve()}"
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(tooltip)
                    if column == 6:
                        item.setForeground(
                            QColor("#2e7d32" if summary.is_ready else "#c62828")
                        )
                    if column == 0:
                        item.setData(Qt.UserRole, summary.image_set_id)
                        item.setData(Qt.UserRole + 1, summary.image.image_key)
                        item.setData(Qt.UserRole + 2, summary.target.id)
                    self.target_summary_table.setItem(row, column, item)
                if summary.target.id == current_target_id:
                    restored_row = row
            if restored_row is not None:
                self.target_summary_table.setCurrentCell(restored_row, 0)
        finally:
            self._refreshing_target_summary = False

    def _accept_valid_auto_wells(self) -> None:
        try:
            count = (
                self.project_controller
                .valid_unconfirmed_automatic_calibration_count()
            )
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot confirm wells", str(error))
            return
        if not count:
            return
        if QMessageBox.question(
            self,
            "Confirm automatic well calibration",
            f"Confirm {count} automatically detected well(s)?\n\n"
            "Only wells whose selected targets are inside the detected boundary "
            "will be confirmed.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            confirmed = self.project_controller.confirm_valid_automatic_calibrations()
            self._adopt_active_review()
            self._sync_project_widgets()
        except (ValueError, PlateImagesNotFoundError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot confirm wells", str(error))
            return
        self.status_message_label.show_message(
            f"Confirmed {confirmed} automatic well calibration(s)", 4000
        )

    def _target_summary_current_cell_changed(
        self, row: int, column: int, previous_row: int, previous_column: int
    ) -> None:
        if self._refreshing_target_summary or row < 0:
            return
        self._target_summary_activated(row, column)

    def _show_target_summary_context_menu(self, position) -> None:
        index = self.target_summary_table.indexAt(position)
        if not index.isValid():
            return
        selected_rows = {
            selected.row()
            for selected in self.target_summary_table.selectionModel().selectedRows()
        }
        if index.row() not in selected_rows:
            self.target_summary_table.selectRow(index.row())
        menu = QMenu(self.target_summary_table)
        remove_action = menu.addAction("Remove selected targets")
        remove_action.triggered.connect(self._remove_selected_targets)
        menu.exec_(self.target_summary_table.viewport().mapToGlobal(position))

    def _remove_selected_targets(self) -> None:
        rows = sorted(
            index.row()
            for index in self.target_summary_table.selectionModel().selectedRows()
        )
        if not rows:
            return
        items = [self.target_summary_table.item(row, 0) for row in rows]
        target_ids = tuple(item.data(Qt.UserRole + 2) for item in items if item)
        descriptions = [
            f"{self.target_summary_table.item(row, 0).text()} "
            f"{self.target_summary_table.item(row, 1).text()} "
            f"target {self.target_summary_table.item(row, 2).text()}"
            for row in rows[:5]
        ]
        detail = "\n".join(descriptions)
        if len(rows) > 5:
            detail += f"\n…and {len(rows) - 5} more"
        if (
            QMessageBox.question(
                self,
                "Remove targets",
                f"Permanently remove {len(target_ids)} selected target(s)?\n\n{detail}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            removed = self.project_controller.remove_project_targets(target_ids)
        except (ValueError, ReviewPersistenceError) as error:
            QMessageBox.warning(self, "Cannot remove targets", str(error))
            return
        if self.controller is not None:
            self.image_canvas.set_targets(self.controller.current_targets)
            self._set_save_status(
                "unsaved" if self.controller.has_unsaved_changes else "saved"
            )
        self._refresh_target_summary()
        self._update_review_summary()
        self.image_set_model.refresh_counts()
        self.status_message_label.show_message(
            f"Removed {removed} target(s)", 3000
        )

    def _target_summary_activated(self, row: int, column: int) -> None:
        identity_item = self.target_summary_table.item(row, 0)
        if identity_item is None:
            return
        image_set_id = identity_item.data(Qt.UserRole)
        image_key = identity_item.data(Qt.UserRole + 1)
        try:
            self.project_controller.activate_image_set(image_set_id)
            self._adopt_active_review()
            destination = next(
                index
                for index, image in enumerate(self.controller.plate.images)
                if image.image_key == image_key
            )
            if destination != self.controller.image_index:
                self.controller.move_to(destination)
                self._show_current_image()
                self._set_save_status("saved")
            self._sync_project_widgets()
            self.target_summary_table.setFocus(Qt.OtherFocusReason)
        except (
            StopIteration,
            ValueError,
            PlateImagesNotFoundError,
            ReviewPersistenceError,
        ) as error:
            QMessageBox.warning(self, "Cannot open target", str(error))

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
            self.accept_calibration_button.setEnabled(False)
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
        self._refresh_target_summary()
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
            self.accept_calibration_button.setEnabled(False)
            return
        active_image_set = self.project_controller.active_image_set
        should_auto_confirm = (
            active_image_set is not None
            and active_image_set.id in self._trusted_auto_well_image_sets
            and calibration.method is CalibrationMethod.AUTO_CIRCLE
            and not calibration.confirmed
            and calibration.confidence
            >= self.auto_confirm_confidence_input.value() / 100
        )
        if should_auto_confirm:
            try:
                calibration = self.calibration_service.confirm(calibration)
            except ReviewPersistenceError as error:
                self._show_persistence_error(error)
        self.current_calibration = calibration
        self.image_canvas.set_calibration(calibration)
        scale_um = calibration.physical_diameter_mm * 1000 / (
            calibration.radius_x_px + calibration.radius_y_px
        )
        method = (
            "Manual"
            if calibration.method is CalibrationMethod.MANUAL_THREE_POINT
            else "Auto"
        )
        confirmation = "Confirmed" if calibration.confirmed else "Unconfirmed"
        self.calibration_label.setText(
            f"Well: {method} · {confirmation} · confidence {calibration.confidence:.0%} · "
            f"center ({calibration.center_x_px:.1f}, {calibration.center_y_px:.1f}) px · "
            f"{scale_um:.3f} µm/px"
        )
        self.accept_calibration_button.setEnabled(not calibration.confirmed)
        self._refresh_target_summary()

    def _toggle_auto_confirm_for_active_plate(self, enabled: bool) -> None:
        image_set = self.project_controller.active_image_set
        if image_set is None:
            return
        if enabled:
            self._auto_well_opted_out_image_sets.discard(image_set.id)
            self._trusted_auto_well_image_sets.add(image_set.id)
            try:
                self.project_controller.confirm_valid_automatic_calibrations(
                    self.auto_confirm_confidence_input.value() / 100,
                    image_set.id,
                )
            except ReviewPersistenceError as error:
                self._trusted_auto_well_image_sets.discard(image_set.id)
                self.auto_confirm_plate_checkbox.blockSignals(True)
                self.auto_confirm_plate_checkbox.setChecked(False)
                self.auto_confirm_plate_checkbox.blockSignals(False)
                self._show_persistence_error(error)
                return
            self._load_current_calibration()
            self.status_message_label.show_message(
                "Automatic well confirmation enabled for this plate", 4000
            )
        else:
            self._trusted_auto_well_image_sets.discard(image_set.id)
            self._auto_well_opted_out_image_sets.add(image_set.id)
            self.status_message_label.show_message(
                "Automatic well confirmation disabled for this plate", 3000
            )
        self._refresh_target_summary()

    def _change_auto_confirm_confidence(self, percent: int) -> None:
        preferences = UserPreferences(percent)
        try:
            self.preferences_store.save(preferences)
        except OSError as error:
            self.status_message_label.show_message(
                f"Could not save user preferences: {error}", 5000
            )
            return
        self.user_preferences = preferences
        self.status_message_label.show_message(
            f"Auto-confirm confidence saved at {percent}%", 3000
        )
        if self.auto_confirm_plate_checkbox.isChecked():
            self._toggle_auto_confirm_for_active_plate(True)

    def _auto_detect_calibration(self) -> None:
        self._cancel_manual_calibration()
        self._load_current_calibration(force_detection=True)

    def _accept_current_calibration(self) -> None:
        if self.calibration_service is None or self.current_calibration is None:
            return
        try:
            self.current_calibration = self.calibration_service.confirm(
                self.current_calibration
            )
        except ReviewPersistenceError as error:
            self._show_persistence_error(error)
            return
        self.image_canvas.set_calibration(self.current_calibration)
        self._load_current_calibration()
        self.status_message_label.show_message("Well calibration confirmed", 3000)

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
        for drafts in self._planning_drafts.values():
            for _, editor in drafts:
                if hasattr(editor, "autosave_timer"):
                    editor.autosave_timer.stop()
                if isinstance(editor, FragmentScreeningEditor):
                    self._persist_planning_draft(editor)
                elif isinstance(editor, RawCrystalEditor):
                    self._persist_raw_crystal_draft(editor)
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

    def _update_zoom_label(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100:.0f}%")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review crystal images from RMServer")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_SETTINGS.rmserver_root,
        help=f"RMServer image root (default: {DEFAULT_SETTINGS.rmserver_root})",
    )
    parser.add_argument(
        "--library-dir",
        type=Path,
        default=DEFAULT_SETTINGS.fragment_library_directory,
        help="Fragment library CSV directory",
    )
    parser.add_argument(
        "--worksheet-dir",
        type=Path,
        default=DEFAULT_SETTINGS.worksheet_staging_directory,
        help="Local worksheet staging directory",
    )
    parser.add_argument(
        "--echo-dir",
        type=Path,
        default=DEFAULT_SETTINGS.echo_output_directory,
        help="ECHO worksheet output directory",
    )
    parser.add_argument(
        "--shifter1-dir",
        type=Path,
        default=DEFAULT_SETTINGS.shifter1_output_directory,
        help="SHIFTER 1 worksheet output directory",
    )
    parser.add_argument(
        "--shifter2-dir",
        type=Path,
        default=DEFAULT_SETTINGS.shifter2_output_directory,
        help="SHIFTER 2 worksheet output directory",
    )
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
    parser.add_argument("--mxlive-url", default=DEFAULT_SETTINGS.mxlive_base_url)
    parser.add_argument("--mxlive-key", type=Path, default=DEFAULT_SETTINGS.mxlive_key_path)
    parser.add_argument("--mxlive-ca", type=Path, default=DEFAULT_SETTINGS.mxlive_ca_bundle)
    parser.add_argument(
        "--mxlive-config", type=Path, default=DEFAULT_SETTINGS.mxlive_config_path,
        help="external TOML file containing OS-user to MxLive account mappings",
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
        / DEFAULT_SETTINGS.review_database_filename
    )
    settings = replace(
        DEFAULT_SETTINGS,
        rmserver_root=args.root,
        fragment_library_directory=args.library_dir,
        worksheet_staging_directory=args.worksheet_dir,
        echo_output_directory=args.echo_dir,
        shifter1_output_directory=args.shifter1_dir,
        shifter2_output_directory=args.shifter2_dir,
        mxlive_base_url=args.mxlive_url,
        mxlive_key_path=args.mxlive_key,
        mxlive_ca_bundle=args.mxlive_ca,
        mxlive_config_path=args.mxlive_config,
    )
    try:
        review_store = SQLiteReviewStore(database_path)
    except ReviewPersistenceError as error:
        print(f"xtalflow-viewer: {error}", file=sys.stderr)
        return 2
    window = ViewerWindow(
        RockMakerImageRepository(settings.rmserver_root),
        review_store,
        settings=settings,
    )
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
