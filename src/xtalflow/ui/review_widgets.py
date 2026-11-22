"""Image review widgets: the crystal image canvas and status labels."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QRect, QRectF, QTimer, Qt, pyqtSignal
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
from xtalflow.ui import theme


class ImageCanvas(QWidget):
    image_clicked = pyqtSignal(float, float, int)
    previous_requested = pyqtSignal()
    next_requested = pyqtSignal()
    previous_plate_requested = pyqtSignal()
    next_plate_requested = pyqtSignal()
    zoom_changed = pyqtSignal(float)
    cancel_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._targets: tuple[TargetPoint, ...] = ()
        # Off by default: see set_position_numbers_visible.
        self._show_position_numbers = False
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
        painter.fillRect(self.rect(), QColor(theme.IMAGE_SURROUND))
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
        order = self.position_numbers()
        for target in self._targets:
            x, y = transform.image_to_viewport(target.x_px, target.y_px)
            painter.drawEllipse(round(x) - 7, round(y) - 7, 14, 14)
            painter.drawLine(round(x) - 11, round(y), round(x) + 11, round(y))
            painter.drawLine(round(x), round(y) - 11, round(x), round(y) + 11)
            if target.id == self._highlighted_target_id:
                painter.setPen(QPen(QColor("#4da3ff"), 3))
                painter.drawEllipse(round(x) - 16, round(y) - 16, 32, 32)
                painter.setPen(QPen(QColor("#ff4057"), 2))
            if self._show_position_numbers:
                self._draw_position_number(painter, x, y, order[target.id])

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

    def set_position_numbers_visible(self, visible: bool) -> None:
        """Turn the numbers on the markers on or off.

        Off to begin with. The number answers a question that is only asked
        while checking a worksheet against a plate - which spot takes the
        biggest drop - and the rest of the time it is ink on top of the
        crystal somebody is trying to look at. The beamline operator asked
        for it behind a view option after seeing it always on.
        """
        if self._show_position_numbers == visible:
            return
        self._show_position_numbers = visible
        self.update()

    def position_numbers(self) -> dict[str, int]:
        """Which position each target is, in the order the dose is divided in.

        Sorted here rather than trusted from the tuple, because the number is
        only worth drawing if it is the order the dose is actually divided
        in. ``position_order`` is built by sorting a well's targets by
        ``selected_at`` (``domain/crystal_selection.py``), and the leftover
        2.5 nL steps go to the lowest orders, so position 1 takes the biggest
        drop and the last may take none. A tuple restored from the database
        arrives in whatever order the query gave, so the sort is done again
        here against the same key.
        """
        return {
            target.id: number
            for number, target in enumerate(
                sorted(self._targets, key=lambda item: item.selected_at), start=1
            )
        }

    def _draw_position_number(
        self, painter: QPainter, x: float, y: float, number: int
    ) -> None:
        """Write which position this is, in the order the dose is divided in.

        The order decides the drops: a dose that will not go round evenly
        gives its leftover 2.5 nL steps to the lowest numbers, so 1 takes
        the biggest share and the last one may take nothing at all. Until
        now that order lived only in the operator's memory of which spot
        they clicked first, which is no way to check a worksheet against a
        plate.

        Drawn as a filled badge with the number reversed out of it, because
        a crystal image is bright in places and dark in others and plain
        text is lost on one or the other. The badge sits up and to the
        right of the cross so it does not cover the spot being marked.
        """
        badge = 9 if number < 10 else 13
        left = round(x) + 8
        top = round(y) - 8 - badge * 2
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ff4057"))
        painter.drawRoundedRect(left, top, badge * 2, badge * 2, badge, badge)
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(
            QRect(left, top, badge * 2, badge * 2), Qt.AlignCenter, str(number)
        )
        painter.restore()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        """Keep a double click from marking a second position.

        Qt's default implementation of this calls ``mousePressEvent``, so a
        double click arrived here as two presses at the same point and left
        two positions behind. A double click is one gesture - often just a
        slow mouse - and never an instruction to mark twice.

        The second one was not merely redundant. With one position per well
        the first press fills the well and the view moves on, so the second
        landed on the next well as its first position, which nothing asks
        about: kept, soaked, and sent to MxLive in that well's dose, on a
        well nobody had looked at.

        Nothing on this canvas wants a double click, so it stops here
        rather than being forwarded.
        """
        event.accept()

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
        if event.key() == Qt.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
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


class ElidingSummaryLabel(QLabel):
    """A status line that ends in an ellipsis rather than mid-word.

    QLabel clips: given too little room it draws what fits and stops, so
    "Add a plate to the active workspace" became "Add a plate to the active
    wo" with no sign that anything was missing. This ends it at a word with
    an ellipsis, and keeps the whole sentence in the tooltip.

    It takes what the row has left, so the controls beside it keep their
    own width - the same bargain the Ignored size policy struck before,
    except the reader can now see that a bargain was struck.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full_text = ""

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = text
        self.setToolTip(text)
        self._refresh_text()

    def full_text(self) -> str:
        """What it would say with room, which is what a test should read."""
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        QLabel.setText(
            self,
            self.fontMetrics().elidedText(
                self._full_text, Qt.ElideRight, max(self.width() - 4, 24)
            ),
        )


class StatusMessageLabel(QLabel):
    """Fixed-width area for transient and persistent operational messages."""

    def __init__(self, width: int = 280) -> None:
        super().__init__()
        self._full_text = ""
        self.setFixedWidth(width)
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        # A bound method, not a lambda. PyQt proxies a bound method of a
        # QObject through a weak reference and drops the connection when the
        # receiver dies; a lambda it keeps alive for as long as the sender.
        # That mattered: a closed window collected on a worker thread leaves
        # its C++ side alive with this timer still running while the garbage
        # collector clears the lambda's closure, so the slot fires with no
        # cells to read and the interpreter dereferences NULL. The crash is a
        # segmentation fault inside processEvents, with no line number to
        # resolve because the frame dies before its first instruction.
        self._clear_timer.timeout.connect(self._clear_message)

    def _clear_message(self) -> None:
        self.show_message("")

    def message(self) -> str:
        """What was said, which is not always what fits.

        ``text()`` is the elided version for the width this label happens to
        have, so it depends on the font the machine is running. Anything
        asking what the window said - a test, or a later read - wants this.
        """
        return self._full_text

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
