# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Application look: dark theme only."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

BG = "#14171C"          # window
PANEL = "#1B1F26"       # cards, lists, inputs
RAISED = "#232831"      # buttons, hover
BORDER = "#2F3742"
TEXT = "#E6E9EE"
MUTED = "#98A2B3"
ACCENT = "#4C8DFF"
ACCENT_HI = "#6BA1FF"
SELECT = "#253A60"
GOOD_FG, GOOD_BG = "#7BD88F", "#16301F"
WARN_FG, WARN_BG = "#F2BE63", "#3A2C12"
BAD_FG, BAD_BG = "#FF9385", "#3D1D1A"


def dark_palette() -> QPalette:
    p = QPalette()
    for role, colour in (
            (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, PANEL),
            (QPalette.AlternateBase, RAISED), (QPalette.Text, TEXT), (QPalette.Button, RAISED),
            (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, RAISED), (QPalette.ToolTipText, TEXT),
            (QPalette.Highlight, SELECT), (QPalette.HighlightedText, TEXT), (QPalette.Link, ACCENT_HI),
            (QPalette.LinkVisited, ACCENT_HI), (QPalette.PlaceholderText, "#6F7A8A"), (QPalette.BrightText, "#FFFFFF"),
            (QPalette.Light, "#3A4350"), (QPalette.Midlight, "#313946"), (QPalette.Mid, "#262C36"),
            (QPalette.Dark, "#0E1014"), (QPalette.Shadow, "#000000")):
        p.setColor(role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#5D6673"))
    return p


def apply_dark(app) -> None:
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE)
    try:                                    # dark window frame where the system supports it
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except (AttributeError, TypeError):
        pass


STYLE = f"""
* {{ font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px 7px; }}
QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px 8px; spacing: 4px; }}
QToolBar QToolButton {{ padding: 6px 12px; border-radius: 6px; color: {TEXT}; }}
QToolBar QToolButton:hover {{ background: {RAISED}; }}
QToolBar QToolButton:checked {{ background: {SELECT}; color: #CFE0FF; }}
QToolBar QToolButton:disabled {{ color: #5D6673; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 4px 8px; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {SELECT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[caption="true"] {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
QLabel[warning="true"] {{ color: {WARN_FG}; background: {WARN_BG}; border-radius: 6px; padding: 8px 10px; }}
QLabel[good="true"] {{ color: {GOOD_FG}; background: {GOOD_BG}; border-radius: 6px; padding: 8px 10px; }}
QLabel[bad="true"] {{ color: {BAD_FG}; background: {BAD_BG}; border-radius: 6px; padding: 8px 10px; }}
QLabel#headline {{ font-size: 15pt; font-weight: 600; }}
QLabel#startTitle {{ font-size: 24pt; font-weight: 600; }}
QLabel#startSub {{ font-size: 11.5pt; color: #B6BFCC; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#startCard {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#startCard:hover {{ border-color: {ACCENT}; background: #1F2632; }}
QLabel#cardHead {{ font-size: 12.5pt; font-weight: 600; }}
QPushButton {{ background: {RAISED}; border: 1px solid #3A4350; border-radius: 6px; padding: 6px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: #2B323D; border-color: #4A5564; }}
QPushButton:pressed {{ background: #1D222A; }}
QPushButton:disabled {{ color: #5D6673; border-color: {BORDER}; background: #1A1E25; }}
QPushButton[primary="true"] {{ background: #2F6FE0; border-color: #2F6FE0; color: #FFFFFF; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT}; border-color: {ACCENT}; }}
QPushButton[primary="true"]:disabled {{ background: #223452; border-color: #223452; color: #7F8DA3; }}
QPushButton[link="true"] {{ border: none; background: transparent; color: {ACCENT_HI}; padding: 3px 4px; }}
QPushButton[link="true"]:hover {{ text-decoration: underline; }}
QComboBox, QLineEdit, QSpinBox {{ background: #12151A; border: 1px solid #3A4350; border-radius: 6px;
                                 padding: 5px 8px; color: {TEXT}; selection-background-color: {SELECT}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 6px; background: #2A313B; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: #E6EAF0; width: 18px; margin: -7px 0; border-radius: 9px; }}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}
QPushButton:checked {{ background: {ACCENT}; color: #FFFFFF; border-color: {ACCENT}; }}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled {{ color: #5D6673; border-color: {BORDER}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; color: {TEXT};
                               selection-background-color: {SELECT}; selection-color: {TEXT}; outline: none; }}
QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 7px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: #5D6673; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; border: 1px solid #5A6677;
                                                  background: #12151A; }}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT_HI}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border: 4px solid #12151A; width: 9px; height: 9px;
                                   border-radius: 8px; outline: 1px solid {ACCENT}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {BORDER}; background: #1A1E25; }}
QCheckBox::indicator:checked:disabled {{ background: #2B4470; }}
QTreeWidget, QListWidget, QTextBrowser, QPlainTextEdit {{ background: {PANEL}; border: 1px solid {BORDER};
                                                         border-radius: 6px; color: {TEXT};
                                                         selection-background-color: {SELECT}; }}
QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: #20262F; }}
QHeaderView::section {{ background: #181C22; border: none; border-bottom: 1px solid {BORDER}; padding: 5px 6px;
                        color: {MUTED}; font-weight: 600; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ padding: 7px 14px; color: {MUTED}; border: none; border-bottom: 2px solid transparent;
               background: transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QDockWidget {{ color: {MUTED}; font-weight: 600; }}
QDockWidget::title {{ background: {BG}; padding: 8px 10px; }}
QProgressBar {{ border: 1px solid #3A4350; border-radius: 5px; background: #12151A; height: 10px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QSplitter::handle {{ background: {BG}; width: 6px; }}
QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3A4350; border-radius: 5px; min-height: 28px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #4A5564; }}
QScrollBar:horizontal {{ background: {BG}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #3A4350; border-radius: 5px; min-width: 28px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: #4A5564; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMessageBox, QInputDialog {{ background: {BG}; }}
"""
