"""OmniCOM visual language: light palette, stylesheet and the Windows glass hook.

The whole UI is a light "paper" theme: a single flat backdrop colour (R-020 -
no gradients, no grid, no blobs) with white cards floating on top.  All
overlays are expressed as dark ink with low alpha, which is what keeps borders
and separators readable on a white surface.

Every control metric lives here too.  Widgets must **not** invent their own
padding / font size: mismatched numbers are what made buttons look ragged and
clip their own labels.
"""

from __future__ import annotations

import sys

# ------------------------------------------------------------------ palette
BG = "#EDF1F7"          # flat page backdrop (also the window's dominant colour)
CARD = "#FFFFFF"        # card / bar surface
INK = "#121A2B"         # primary text
INK2 = "#46536B"        # secondary text
DIM = "#5F6A7E"         # hints, captions, gutters (>= 4.8:1 on BG)

TEAL = "#0B7A6E"
TEAL_SOFT = "#DFF7F3"
VIOLET = "#6D4AE0"
VIOLET_SOFT = "#EEE9FE"
BLUE = "#2563EB"
AMBER = "#B45309"
AMBER_SOFT = "#FFF4E2"
DANGER = "#C53434"
DANGER_SOFT = "#FDECEC"
OK = "#0A7A54"
OK_SOFT = "#E4F6EE"

# Terminal surface: deliberately a dark "console" panel inside the light shell,
# which is what makes a linux serial console readable at a glance.
TERM_BG = "#0F1620"
TERM_INK = "#D6E2F0"

# Alpha-blended ink used for every hairline / hover wash.  Keeping them as
# formulas means the light theme stays consistent when a shade is retuned.
LINE = "rgba(18, 26, 43, 34)"
LINE_SOFT = "rgba(18, 26, 43, 18)"
LINE_STRONG = "rgba(18, 26, 43, 60)"
WASH = "rgba(18, 26, 43, 8)"
WASH_HOVER = "rgba(18, 26, 43, 14)"
TEXT_DISABLED = "rgba(70, 83, 107, 110)"

# ------------------------------------------------------------------- metrics
CTRL_H = 32             # every single-line control is exactly this tall
BTN_PAD = "0 14px"
ICON_SM = 24           # dense icon buttons (command rows)
ICON_MD = 26           # panel headers
ICON_SIZE = 28         # toolbar icon buttons
FONT_PX = 13

# Radii: the user asked for two very different feelings.
#   * toggles / checkboxes -> a third of their size (soft, round)
#   * inputs, buttons, cards -> just past square (crisp, boxy)
RADIUS_CTRL = 4         # text boxes, buttons, combo boxes (slightly sharper than
                        # the checkbox, per the request: "文本框圆弧要稍小一些")
RADIUS_CARD = 9         # cards and panels
RADIUS_PILL = 999       # fully rounded pills / badges
CHECK_SIZE = 15         # matches the 13 px UI font; 18 px looked oversized
CHECK_RADIUS = 5        # one third of CHECK_SIZE
RADIUS = RADIUS_CTRL    # kept for older call sites

UI_FONT = '"Segoe UI Variable Text", "Segoe UI", Inter, "Microsoft YaHei UI", sans-serif'
# Preference order for the log/terminal face.  ``resolve_mono()`` picks the
# first family actually installed, so the log never falls back to a
# proportional font.
MONO_STACK = (
    "Cascadia Code",
    "Cascadia Mono",
    "JetBrains Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Courier New",
)
MONO_FONT = '"Cascadia Code", "Cascadia Mono", "JetBrains Mono", Consolas, monospace'


def app_stylesheet() -> str:
    return f"""
* {{
  font-family: {UI_FONT};
  font-size: {FONT_PX}px;
  color: {INK};
  outline: none;
}}
QWidget#Root {{ background: transparent; }}
QToolTip {{
  background: {CARD};
  color: {INK};
  border: 1px solid {LINE};
  border-radius: 8px;
  padding: 6px 9px;
}}

/* ------------------------------------------------------------- text inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: {CARD};
  border: 1px solid {LINE};
  border-radius: {RADIUS_CTRL}px;
  padding: 0 10px;
  min-height: {CTRL_H - 2}px;
  selection-background-color: rgba(11, 122, 110, 60);
  selection-color: {INK};
}}
QPlainTextEdit, QTextEdit {{ padding: 6px 10px; }}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
  border-color: {LINE_STRONG};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus, QComboBox:on {{
  border: 1px solid {TEAL};
}}
QLineEdit:disabled, QComboBox:disabled {{ color: {TEXT_DISABLED}; background: {WASH}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox QLineEdit {{
  padding: 0; border: 0; min-height: 0; background: transparent;
}}
QComboBox::drop-down {{ border: 0; width: 26px; }}
/* the chevron is painted by widgets.Combo so it matches the icons elsewhere */
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
  background: {CARD};
  border: 1px solid {LINE};
  border-radius: {RADIUS_CARD}px;
  padding: 6px;
  outline: none;
  selection-background-color: {TEAL_SOFT};
  selection-color: {INK};
}}
QComboBox QAbstractItemView::item {{
  min-height: 26px;
  padding: 4px 10px;
  border-radius: {RADIUS_CTRL}px;
}}
QComboBox QAbstractItemView::item:hover {{ background: {WASH_HOVER}; }}
QComboBox QAbstractItemView::item:selected {{ background: {TEAL_SOFT}; color: {INK}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
  width: 16px; border: 0; background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
  image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
  border-bottom: 5px solid {DIM}; width: 0; height: 0;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
  image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
  border-top: 5px solid {DIM}; width: 0; height: 0;
}}

/* ----------------------------------------------------------------- buttons */
QPushButton {{
  background: {CARD};
  border: 1px solid {LINE};
  border-radius: {RADIUS_CTRL}px;
  padding: {BTN_PAD};
  min-height: {CTRL_H - 2}px;
  color: {INK};
  font-weight: 500;
}}
QPushButton:hover {{ background: #F6F9FD; border-color: {LINE_STRONG}; }}
QPushButton:pressed {{ background: {WASH_HOVER}; }}
QPushButton:disabled {{ color: {TEXT_DISABLED}; background: {WASH}; }}
QPushButton#Primary {{
  color: #FFFFFF;
  font-weight: 650;
  border: 1px solid rgba(11, 122, 110, 170);
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 #10A794, stop:0.55 #4884F0, stop:1 #7455F5);
}}
QPushButton#Primary:hover {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 #12BB9F, stop:0.55 #5A94F8, stop:1 #8A6CFF);
}}
QPushButton#Primary:disabled {{ color: rgba(255, 255, 255, 180); border-color: {LINE}; }}
QPushButton#Danger {{
  color: {DANGER}; border-color: rgba(197, 52, 52, 110);
  background: {DANGER_SOFT};
}}
QPushButton#Ghost {{
  background: {CARD};
  color: {INK2};
  border-color: {LINE};
}}
QPushButton#Ghost:hover {{ color: {INK}; background: #F6F9FD; }}
/* square icon buttons: identical box, no text, no padding surprises */
QPushButton#IconBtn {{
  min-width: {ICON_SIZE}px; max-width: {ICON_SIZE}px;
  min-height: {ICON_SIZE}px; max-height: {ICON_SIZE}px;
  padding: 0; border: 0; border-radius: {RADIUS_CTRL}px;
  background: transparent;
}}
QPushButton#IconBtn:hover {{ background: {WASH_HOVER}; }}
QPushButton#IconBtn:pressed {{ background: rgba(18, 26, 43, 26); }}
QPushButton#TopIcon {{
  min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
  padding: 0; border: 0; border-radius: {RADIUS_CTRL}px; background: transparent;
}}
QPushButton#TopIcon:hover {{ background: {WASH_HOVER}; }}
QPushButton#TopIcon:pressed {{ background: rgba(18, 26, 43, 28); }}
QPushButton#TopIconClose {{
  min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
  padding: 0; border: 0; border-radius: {RADIUS_CTRL}px; background: transparent;
}}
QPushButton#TopIconClose:hover {{ background: rgba(197, 52, 52, 30); }}
QPushButton#TopIconClose:pressed {{ background: rgba(197, 52, 52, 60); }}
QPushButton#Seg {{
  background: transparent; border: 0; border-radius: {RADIUS_CTRL}px; color: {DIM};
  padding: 0 12px; min-height: 26px;
}}
QPushButton#Seg:hover {{ color: {INK}; background: {WASH}; }}
QPushButton#Seg:checked {{
  color: {INK}; background: {CARD};
  border: 1px solid {LINE};
}}
QPushButton#Proto {{
  text-align: left; padding: 0 12px; border-radius: {RADIUS_CARD}px;
  background: {CARD}; border: 1px solid {LINE};
  color: {INK2}; min-height: 44px;
}}
QPushButton#Proto:hover {{ background: #F6F9FD; }}
QPushButton#Proto:checked {{
  color: {INK};
  background: {TEAL_SOFT};
  border: 1px solid rgba(11, 122, 110, 130);
}}

/* ---------------------------------------------------------------- scrolling */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
  background: rgba(18, 26, 43, 46); border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(18, 26, 43, 82); }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
  background: rgba(18, 26, 43, 46); border-radius: 4px; min-width: 28px;
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
  background: transparent; border: 0; width: 0; height: 0;
}}

/* ------------------------------------------------------------------ labels */
QLabel#Dim {{ color: {DIM}; }}
QLabel#SectionTitle {{
  color: {DIM};
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1.4px;
}}
QLabel#Badge {{
  color: {INK2};
  background: {WASH};
  border: 1px solid {LINE_SOFT};
  border-radius: 9px;
  padding: 1px 8px;
  font-size: 11px;
}}
QLabel#BadgeWarn {{
  color: {AMBER};
  background: {AMBER_SOFT};
  border: 1px solid rgba(180, 83, 9, 90);
  border-radius: 9px;
  padding: 2px 9px;
  font-size: 11px;
}}

/* ------------------------------------------------------------------- lists */
QListWidget, QTreeWidget, QTableWidget {{
  background: transparent; border: 0; outline: none;
}}
QListWidget::item {{ border: 0; padding: 0; margin: 0; }}
QCheckBox {{ spacing: 8px; color: {INK2}; }}
QCheckBox::indicator {{
  width: {CHECK_SIZE}px; height: {CHECK_SIZE}px;
  border-radius: {CHECK_RADIUS}px;
  border: 1px solid {LINE_STRONG}; background: {CARD};
}}
QCheckBox::indicator:hover {{ border-color: {TEAL}; }}
QCheckBox::indicator:checked {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #10A794, stop:1 #7455F5);
  border-color: rgba(11, 122, 110, 160);
}}
QProgressBar {{
  background: {WASH_HOVER}; border: 0; border-radius: 5px; height: 9px;
  text-align: center; color: transparent;
}}
QProgressBar::chunk {{
  border-radius: 5px;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10A794, stop:1 #7455F5);
}}
QMenu {{
  background: {CARD}; border: 1px solid {LINE};
  border-radius: {RADIUS_CARD}px; padding: 6px;
}}
QMenu::item {{ padding: 7px 20px; border-radius: {RADIUS_CTRL}px; }}
QMenu::separator {{ height: 1px; background: {LINE_SOFT}; margin: 5px 8px; }}
QMenu::item:selected {{ background: {TEAL_SOFT}; }}
QDialog {{ background: {CARD}; }}
QSplitter::handle {{ background: transparent; }}
"""


def resolve_mono() -> str:
    """First installed family from :data:`MONO_STACK`, else a safe fallback."""
    try:
        from PySide6.QtGui import QFontDatabase

        families = set(QFontDatabase.families())
    except Exception:
        return "Consolas"
    for family in MONO_STACK:
        if family in families:
            return family
    return "Consolas"


def mono_font(pixel_size: int = 13):
    """Monospace font used by the log, the search list and the terminal."""
    from PySide6.QtGui import QFont

    font = QFont(resolve_mono())
    font.setStyleHint(QFont.Monospace)
    font.setFixedPitch(True)
    font.setPixelSize(pixel_size)
    font.setHintingPreference(QFont.PreferFullHinting)
    return font


# --------------------------------------------------------------- real glass
def enable_windows_glass(hwnd: int, tint: int = 0xCCF7F1ED) -> bool:
    """Ask DWM for an acrylic backdrop (Windows 10/11).

    The tint is ABGR; the default is a very light grey-blue with ~80% opacity,
    matching the light theme.  Returns False on any non-Windows platform or
    when the API is unavailable, in which case the painted flat fill is used.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class AccentPolicy(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WindowCompositionAttributeData(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        accent = AccentPolicy()
        accent.AccentState = 4          # ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        accent.GradientColor = tint
        data = WindowCompositionAttributeData()
        data.Attribute = 19             # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        setter = ctypes.windll.user32.SetWindowCompositionAttribute
        setter.argtypes = [wintypes.HWND, ctypes.POINTER(WindowCompositionAttributeData)]
        setter.restype = ctypes.c_int
        return bool(setter(wintypes.HWND(int(hwnd)), ctypes.byref(data)))
    except Exception:
        return False
