"""Plate cards for the Image Review plate list."""

from __future__ import annotations

from PyQt5.QtCore import QRect, QSize, Qt
from PyQt5.QtGui import QColor, QFont, QPainter
from PyQt5.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from xtalflow.presentation import ProjectImageSetListModel
from xtalflow.ui import theme


class PlateCardDelegate(QStyledItemDelegate):
    """Plate code first, then format/batch, then review progress."""

    PADDING = theme.SPACING_M
    ACCENT_WIDTH = 3

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        line = option.fontMetrics.height()
        return QSize(option.rect.width(), line * 3 + self.PADDING * 2 + 4)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        active = bool(index.data(ProjectImageSetListModel.ActiveRole))
        painter.fillRect(rect, QColor(theme.FOCUS_SOFT if selected else theme.SURFACE))
        if active:
            painter.fillRect(
                QRect(rect.left(), rect.top() + 4, self.ACCENT_WIDTH, rect.height() - 8),
                QColor(theme.FOCUS),
            )
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        line = option.fontMetrics.height()
        text_left = rect.left() + self.PADDING + self.ACCENT_WIDTH
        text_width = rect.width() - self.PADDING * 2 - self.ACCENT_WIDTH
        top = rect.top() + self.PADDING

        heading = QFont(option.font)
        heading.setBold(True)
        painter.setFont(heading)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRect(text_left, top, text_width, line), Qt.AlignLeft | Qt.AlignVCenter,
            f"Plate {index.data(ProjectImageSetListModel.PlateCodeRole)}",
        )
        painter.setFont(option.font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        for row, role in (
            (1, ProjectImageSetListModel.ProgressRole),
            (2, ProjectImageSetListModel.DetailRole),
        ):
            text = option.fontMetrics.elidedText(
                str(index.data(role) or ""), Qt.ElideRight, text_width
            )
            painter.drawText(
                QRect(text_left, top + line * row + 2, text_width, line),
                Qt.AlignLeft | Qt.AlignVCenter, text,
            )
        painter.restore()
