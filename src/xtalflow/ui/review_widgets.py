"""Image review widgets: the crystal image canvas and status labels."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QRectF, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt5.QtWidgets import QLabel, QListView, QShortcut, QWidget

from xtalflow.domain import ImageCalibration, TargetPoint
from xtalflow.presentation import AspectFitTransform


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
        self._highlighted_target_id: str | None = None
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
        self._highlighted_target_id = None
        self.fit_image()
        self.update()

    def clear_image(self) -> None:
        self._pixmap = QPixmap()
        self._targets = ()
        self._calibration = None
        self._calibration_points = ()
        self._pan_origin = None
        self._pan_start = None
        self.fit_image()
        self.update()

    def set_targets(self, targets: tuple[TargetPoint, ...]) -> None:
        self._targets = targets
        self.update()

    def set_highlighted_target(self, target_id: str | None) -> None:
        """Emphasise the position selected in the Target Summary."""
        self._highlighted_target_id = target_id
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
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#15181c"))
        transform = self.transform()
        if transform is None:
            painter.setPen(QColor("#d8dee9"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No image")
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
            # Detected boundaries are dashed until accepted, then solid.
            boundary = QPen(QColor("#36d17c" if calibration.confirmed else "#ffb020"), 2)
            if not calibration.confirmed:
                boundary.setStyle(Qt.DashLine)
            painter.setPen(boundary)
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
            if target.id == self._highlighted_target_id:
                painter.setPen(QPen(QColor("#4da3ff"), 3))
                painter.drawEllipse(round(x) - 16, round(y) - 16, 32, 32)
                painter.setPen(QPen(QColor("#ff4057"), 2))

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

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        # The key release is lost when focus leaves while Space is held.
        self._space_pressed = False
        if self._pan_start is None:
            self.setCursor(Qt.ArrowCursor)
        super().focusOutEvent(event)

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
