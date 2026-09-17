"""Astra visual system: colour tokens, spacing, and the application style sheet."""

from __future__ import annotations

import tempfile
from pathlib import Path

# Follows the ChatGPT app's light theme: white surfaces, neutral greys with no
# tint, and near-black for the main action. A few accents carry meaning only:
# blue for focus and the current selection, green for completed results,
# amber for attention, and red for errors.
BACKGROUND = "#FFFFFF"
SIDEBAR = "#F9F9F9"
SURFACE = "#FFFFFF"
SUBTLE = "#F4F4F4"
SELECTED = "#ECECEC"
TEXT = "#0D0D0D"
TEXT_MUTED = "#5D5D5D"
TEXT_TERTIARY = "#8F8F8F"
BORDER = "#E5E5E5"
BORDER_STRONG = "#D1D1D1"
PRIMARY = "#0D0D0D"
PRIMARY_HOVER = "#303030"
FOCUS = "#0169CC"
FOCUS_SOFT = "#E5F0FA"
SUCCESS = "#10A37F"
SUCCESS_SOFT = "#E6F6F1"
OK = "#0B7A5F"
ATTENTION = "#A15C00"
ATTENTION_SOFT = "#FFF4E0"
ERROR = "#D03B34"
ERROR_SOFT = "#FDECEC"
IMAGE_SURROUND = "#171717"

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

def _chevron(direction: str, color: str) -> str:
    """Path of a small chevron for combo and spin boxes.

    Style sheets cannot embed images, and a styled box loses its native arrow, so
    the SVG is written once to the temporary folder and referenced by path.
    """
    points = {"down": "4,6 8,10 12,6", "up": "4,10 8,6 12,10"}[direction]
    path = Path(tempfile.gettempdir()) / "xtalflow-ui" / f"chevron-{direction}-{color[1:]}.svg"
    if not path.is_file():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
                f'viewBox="0 0 16 16"><polyline points="{points}" fill="none" '
                f'stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
                'stroke-linejoin="round"/></svg>',
                encoding="utf-8",
            )
        except OSError:
            return ""
    return path.as_posix()


def application_style_sheet() -> str:
    """Built from the tokens above when called, so a palette can be tried before import."""
    down = _chevron("down", TEXT_MUTED)
    up = _chevron("up", TEXT_MUTED)
    return f"""
QMainWindow, QDialog {{ background: {BACKGROUND}; color: {TEXT}; }}
QWidget#Panel, QListView, QListWidget, QTableWidget, QTableView {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {SELECTED}; selection-color: {TEXT};
}}
QLabel#SectionTitle {{ color: {TEXT_MUTED}; font-weight: 600; }}
QLabel#Muted {{ color: {TEXT_MUTED}; }}
QLabel#PrimaryHeading {{ font-weight: 600; }}
QFrame#SelectionBar, QFrame#DeliveryBar {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
}}
QFrame#CalibrationInspector {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
}}
QPushButton {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 14px; min-height: 22px; padding: 3px 14px;
}}
QPushButton:hover {{ background: {SUBTLE}; }}
QPushButton:pressed, QPushButton:checked {{ background: {SELECTED}; }}
QPushButton:disabled {{ color: {TEXT_TERTIARY}; background: {SURFACE}; }}
QPushButton:focus {{ border: 1px solid {FOCUS}; }}
QToolButton {{
    background: transparent; color: {TEXT}; border: 1px solid transparent;
    border-radius: 8px; min-height: 22px; padding: 2px 8px;
}}
QToolButton:hover {{ background: {SUBTLE}; }}
QToolButton:pressed, QToolButton:checked {{ background: {SELECTED}; }}
QPushButton#Primary {{
    background: {PRIMARY}; color: white; border: 1px solid {PRIMARY};
}}
QPushButton#Primary:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton#Primary:disabled {{
    background: {BORDER}; border-color: {BORDER}; color: {TEXT_TERTIARY};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 3px 8px; min-height: 20px;
    selection-background-color: {FOCUS_SOFT}; selection-color: {TEXT};
}}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 22px; border: none; background: transparent;
}}
QComboBox::down-arrow {{ image: url("{down}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {SUBTLE}; selection-color: {TEXT}; outline: none;
}}
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border: none; background: transparent; margin-top: 2px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border: none; background: transparent; margin-bottom: 2px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 11px; height: 11px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{down}"); width: 11px; height: 11px;
}}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {TEXT_TERTIARY}; background: {SUBTLE};
}}
QTableWidget, QTableView {{ gridline-color: {BORDER}; }}
QHeaderView::section {{
    background: {SURFACE}; color: {TEXT_MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 5px 8px;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_MUTED}; border: none;
    border-bottom: 2px solid transparent; padding: 6px 12px;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {TEXT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QMenu {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px;
}}
QMenu::item {{ padding: 5px 16px; border-radius: 6px; color: {TEXT}; }}
QMenu::item:selected {{ background: {SUBTLE}; }}
QToolTip {{
    background: {PRIMARY}; color: white; border: none; border-radius: 6px; padding: 4px 8px;
}}
QStatusBar {{ background: {SIDEBAR}; border-top: 1px solid {BORDER}; color: {TEXT_MUTED}; }}
QDockWidget {{ color: {TEXT}; }}
*:focus {{ outline: none; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QListView:focus, QTableWidget:focus, QTableView:focus {{ border: 1px solid {FOCUS}; }}
"""


APPLICATION_STYLE_SHEET = application_style_sheet()


def status_style(kind: str) -> str:
    return f"color: {STATUS_COLORS[kind]}; font-weight: 600;"
