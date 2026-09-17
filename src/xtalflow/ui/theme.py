"""Astra visual system: colour tokens, spacing, and the application style sheet."""

from __future__ import annotations

BACKGROUND = "#F4F6F8"
SURFACE = "#FFFFFF"
TEXT = "#202A35"
TEXT_MUTED = "#596575"
BORDER = "#D8DEE6"
FOCUS = "#245FB5"
FOCUS_SOFT = "#E3ECF8"
OK = "#246B49"
ATTENTION = "#8A5700"
ERROR = "#B42332"
IMAGE_SURROUND = "#171C22"

SPACING_S = 4
SPACING_M = 8
SPACING_L = 12
SPACING_XL = 16

# Status symbols are always shown with text so meaning never depends on colour.
SYMBOL_OK = "✓"
SYMBOL_ATTENTION = "△"
SYMBOL_ERROR = "!"
SYMBOL_UNSAVED = "●"

STATUS_COLORS = {"ok": OK, "attention": ATTENTION, "error": ERROR, "muted": TEXT_MUTED}

APPLICATION_STYLE_SHEET = f"""
QMainWindow, QDialog {{ background: {BACKGROUND}; color: {TEXT}; }}
QWidget#Panel, QListView, QListWidget, QTableWidget, QTableView {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 4px;
}}
QLabel#SectionTitle {{
    color: {TEXT_MUTED}; font-weight: 600; letter-spacing: 1px;
}}
QLabel#Muted {{ color: {TEXT_MUTED}; }}
QLabel#PrimaryHeading {{ font-weight: 600; }}
QFrame#SelectionBar, QFrame#DeliveryBar {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 4px;
}}
QFrame#CalibrationInspector {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
}}
QPushButton, QToolButton {{ min-height: 24px; padding: 2px 10px; }}
QPushButton#Primary {{
    background: {FOCUS}; color: white; border: 1px solid {FOCUS}; border-radius: 4px;
}}
QPushButton#Primary:disabled {{ background: {BORDER}; border-color: {BORDER}; color: {TEXT_MUTED}; }}
QTableWidget, QTableView {{ gridline-color: {BORDER}; }}
QHeaderView::section {{
    background: {BACKGROUND}; color: {TEXT_MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 4px 6px;
}}
*:focus {{ outline: none; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QListView:focus,
QTableWidget:focus, QTableView:focus {{ border: 2px solid {FOCUS}; }}
"""


def status_style(kind: str) -> str:
    return f"color: {STATUS_COLORS[kind]}; font-weight: 600;"
