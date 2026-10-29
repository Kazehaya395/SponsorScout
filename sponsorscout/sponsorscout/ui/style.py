"""Application look & feel for the PySide6 UI.

Ports the original tkinter palette so the Qt app stays visually faithful
to the previous UI concept (dark navy header, light body, white cards,
blue accent) while letting QSS do the heavy lifting.
"""

from pathlib import Path as _Path

from PySide6.QtGui import QColor, QPalette

# F-21 revised (2026-10-22, after user feedback on the solid-block look): the
# tick glyph ships inside the package and its path is resolved from this
# module's own location at import time, so no build can carry a dangling or
# developer-specific path — the original F-21 bug class stays closed.
_TICK_URL = (_Path(__file__).resolve().parent.parent
             / "data" / "icons" / "tick.png").as_posix()

HEADER_BG = "#1d2d44"
HEADER_FG = "#ffffff"
ACCENT = "#3a7bd5"
ACCENT_DARK = "#2f65ad"
BODY_BG = "#f0f2f5"
CARD_BG = "#ffffff"
TEXT_MAIN = "#2c3e50"
TEXT_MUTED = "#7f8c9b"
BORDER = "#d9dee5"
OK_GREEN = "#27ae60"
WARN_AMBER = "#f39c12"
BAD_RED = "#c0392b"

FONT_FAMILY = "Helvetica"  # matches the tkinter original


def build_light_palette() -> QPalette:
    """Force the light palette the QSS was designed for.

    Qt >= 6.5 auto-dark-palettes on Windows when the system runs in dark
    mode; without this override the light QSS mixes near-white palette
    text colors with light backgrounds and every unstyled label becomes
    illegibly faint.
    """
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BODY_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_MAIN))
    palette.setColor(QPalette.ColorRole.Base, QColor(CARD_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f7f9fb"))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_MAIN))
    palette.setColor(QPalette.ColorRole.Button, QColor(CARD_BG))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_MAIN))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(CARD_BG))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT_MAIN))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                     QColor("#b0b7bf"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
                     QColor("#b0b7bf"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                     QColor("#b0b7bf"))
    return palette


def build_qss() -> str:
    """Global stylesheet applied to the QApplication."""
    return f"""
QMainWindow, QWidget#AppBody {{ background-color: {BODY_BG}; }}
QFrame#Header {{ background-color: {HEADER_BG}; }}
QFrame#Header QLabel {{ color: {HEADER_FG}; }}
QFrame#Header QLabel#AppSubtitle {{ color: #b8c4d4; font-size: 11px; }}
QFrame#Header QLabel#AppTitle {{ font-size: 20px; font-weight: bold; }}
QLabel {{ color: {TEXT_MAIN}; }}
QLabel#StatusLabel {{ color: {TEXT_MUTED}; font-size: 11px; }}
QLabel#SectionHeader, QLabel#SectionTitle {{ color: {HEADER_BG};
        font-size: 13px; font-weight: bold; }}
QLabel#MutedLabel {{ color: {TEXT_MUTED}; font-size: 11px; }}
QLabel#SectionHelp {{ color: {TEXT_MUTED}; font-size: 11px; }}

QFrame#Card {{ background-color: {CARD_BG}; border: 1px solid {BORDER};
               border-radius: 6px; }}
QLabel#CardTitle {{ color: {TEXT_MUTED}; font-size: 11px; font-weight: bold; }}
QLabel#CardValue {{ color: {TEXT_MAIN}; font-size: 22px; font-weight: bold; }}

QGroupBox {{ background-color: {CARD_BG}; border: 1px solid {BORDER};
             border-radius: 6px; margin-top: 4ex; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left;
        left: 10px; padding: 0 4px; color: {HEADER_BG};
        font-size: 12px; font-weight: bold; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; background: {CARD_BG};
                    border-radius: 4px; }}
QTabBar::tab {{ background: #e6e9ee; color: {TEXT_MUTED}; padding: 8px 18px;
                margin-right: 2px; border: 1px solid {BORDER};
                border-bottom: none; border-top-left-radius: 4px;
                border-top-right-radius: 4px; font-weight: bold; }}
QTabBar::tab:selected {{ background: {CARD_BG}; color: {ACCENT}; }}
QTabBar::tab:hover {{ background: #dde3ea; }}

QPushButton {{ background-color: {CARD_BG}; color: {TEXT_MAIN};
               border: 1px solid {BORDER}; border-radius: 4px;
               padding: 6px 14px; font-weight: bold; }}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:disabled {{ color: #b0b7bf; border-color: {BORDER}; }}
QPushButton#Primary {{ background-color: {ACCENT}; color: white;
                       border: 1px solid {ACCENT_DARK}; }}
QPushButton#Primary:hover {{ background-color: {ACCENT_DARK}; }}
QPushButton#Danger {{ color: {BAD_RED}; }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QDoubleSpinBox {{
    background-color: white; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 4px 6px; color: {TEXT_MAIN}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: white; color: {TEXT_MAIN};
    selection-background-color: {ACCENT}; selection-color: white; }}

QTableWidget {{ background-color: {CARD_BG}; alternate-background-color: #f7f9fb;
                gridline-color: {BORDER}; color: {TEXT_MAIN};
                border: 1px solid {BORDER}; border-radius: 4px; }}
QTableWidget::item:selected {{ background-color: {ACCENT}; color: white; }}
QHeaderView::section {{ background-color: #eef1f5; color: {TEXT_MUTED};
    border: none; border-bottom: 1px solid {BORDER}; border-right: 1px solid {BORDER};
    padding: 6px; font-weight: bold; }}


QCheckBox {{
    color: #1F2937;
    spacing: 8px;
    font-size: 13px;
}}
/* FIX F-21 (2026-10-22, revision 3 — the look the user asked for): the classic
   native checkbox. White box with a dark rim; when checked, a dark tick glyph
   (data/icons/tick.png, path resolved at import time above) on the white box.
   No coloured fill. Revision 1 was invisible (missing asset behind a hard-coded
   developer path); revision 2 was an accent block with a white tick, rejected as
   "way uglier & big"; revision 3 matches the native control in the user's
   reference image. The asset ships in the package and the path is resolved from
   this module, so the original dangling-path bug class stays closed. */
QCheckBox::indicator,
QListView::indicator,
QListWidget::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid #6f7a86;
    border-radius: 2px;
    background: #ffffff;
}}
QListView::indicator, QListWidget::indicator {{
    width: 13px;
    height: 13px;
    margin-right: 6px;
}}
QCheckBox::indicator:hover,
QListView::indicator:hover,
QListWidget::indicator:hover {{
    border: 1px solid {ACCENT};
}}
QCheckBox::indicator:checked,
QListView::indicator:checked,
QListWidget::indicator:checked {{
    border: 1px solid #3f4854;
    background: #ffffff;
    image: url("{_TICK_URL}");
}}
QCheckBox::indicator:checked:hover,
QListView::indicator:checked:hover,
QListWidget::indicator:checked:hover {{
    border: 1px solid {ACCENT};
    background: #ffffff;
    image: url("{_TICK_URL}");
}}
QCheckBox::indicator:disabled,
QListView::indicator:disabled,
QListWidget::indicator:disabled {{
    border: 1px solid {BORDER};
    background: #eef1f5;
}}

QScrollBar:vertical {{ background: #f0f2f5; width: 11px; }}
QScrollBar::handle:vertical {{ background: #c3ccd6; border-radius: 5px;
                               min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: #f0f2f5; height: 11px; }}
QScrollBar::handle:horizontal {{ background: #c3ccd6; border-radius: 5px;
                                 min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QDialog {{ background-color: {BODY_BG}; }}
QDialog QLabel {{ color: {TEXT_MAIN}; }}
QMessageBox {{ background-color: {CARD_BG}; }}
QMessageBox QLabel {{ color: {TEXT_MAIN}; }}
QLabel#FormError {{ color: {BAD_RED}; font-size: 11px; }}
QToolTip {{ color: {TEXT_MAIN}; background-color: {CARD_BG};
    border: 1px solid {BORDER}; }}
"""
