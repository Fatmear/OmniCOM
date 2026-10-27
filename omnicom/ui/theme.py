"""OmniCOM visual language: light palette, stylesheet and the Windows glass hook.

The whole UI is a light "paper" theme: a single flat backdrop colour (R-020 -
no gradients, no grid, no blobs) with white cards floating on top.  All
overlays are expressed as dark ink with low alpha, which is what keeps borders
and separators readable on a white surface.
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

# Alpha-blended ink used for every hairline / hover wash.  Keeping them as
# formulas means the light theme stays consistent when a shade is retuned.
LINE = "rgba(18, 26, 43, 34)"
LINE_SOFT = "rgba(18, 26, 43, 18)"
LINE_STRONG = "rgba(18, 26, 43, 60)"
WASH = "rgba(18, 26, 43, 8)"
WASH_HOVER = "rgba(18, 26, 43, 14)"
TEXT_DISABLED = "rgba(70, 83, 107, 110)"

UI_FONT = '"Segoe UI Variable Text", "Segoe UI", Inter, "Microsoft YaHei UI", sans-serif'
MONO_FONT = '"Cascadia Code", "Cascadia Mono", Consolas, "Courier New", monospace'


def app_stylesheet() -> str:
    return f"""
* {{
  font-family: {UI_FONT};
  font-size: 13px;
  color: {INK};
  outline: none;
}}
QWidget#Root {{ background: transparent; }}
QToolTip {{
  background: #FFFFFF;
  color: {INK};
  border: 1px solid {LINE};
  border-radius: 8px;
  padding: 6px 9px;
}}

/* ------------------------------------------------------------- text inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: {CARD};
  border: 1px solid {LINE};
  border-radius: 10px;
  padding: 6px 10px;
  selection-background-color: rgba(46, 196, 176, 90);
  selection-color: {INK};
}}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
  border-color: {LINE_STRONG};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{
  border: 1px solid {TEAL};
}}
QLineEdit:disabled, QComboBox:disabled {{ color: {TEXT_DISABLED}; background: {WASH}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{
  image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
  border-top: 5px solid {DIM}; width: 0; height: 0; margin-right: 7px;
}}
QComboBox QAbstractItemView {{
  background: {CARD};
  border: 1px solid {LINE};
  border-radius: 10px;
  padding: 4px;
  selection-background-color: {TEAL_SOFT};
  selection-color: {INK};
  outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
  width: 14px; border: 0; background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
  image: none; border-left: 3px solid transparent; border-right: 3px solid transparent;
  border-bottom: 4px solid {DIM}; width: 0; height: 0;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
  image: none; border-left: 3px solid transparent; border-right: 3px solid transparent;
  border-top: 4px solid {DIM}; width: 0; height: 0;
}}

/* ----------------------------------------------------------------- buttons */
QPushButton {{
  background: {CARD};
  border: 1px solid {LINE};
  border-radius: 10px;
  padding: 7px 13px;
  color: {INK};
  font-weight: 500;
}}
QPushButton:hover {{ background: #F7F9FC; border-color: {LINE_STRONG}; }}
QPushButton:pressed {{ background: {WASH_HOVER}; }}
QPushButton:disabled {{ color: {TEXT_DISABLED}; background: {WASH}; }}
QPushButton#Primary {{
  color: #FFFFFF;
  font-weight: 650;
  border: 1px solid rgba(14, 148, 132, 180);
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 #12B39E, stop:0.55 #4C8BF5, stop:1 #7C5CFA);
}}
QPushButton#Primary:hover {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 #15C6AF, stop:0.55 #5C97F7, stop:1 #8B6DFF);
}}
QPushButton#Primary:disabled {{ color: rgba(255, 255, 255, 170); border-color: {LINE}; }}
QPushButton#Danger {{
  color: {DANGER}; border-color: rgba(197, 52, 52, 110);
  background: {DANGER_SOFT};
}}
QPushButton#Ghost {{
  background: {CARD};
  color: {INK2};
  border-color: {LINE};
}}
QPushButton#Ghost:hover {{ color: {INK}; background: #F7F9FC; }}
QPushButton#IconBtn {{
  padding: 0; border-radius: 9px; font-size: 14px;
  background: transparent; border: 1px solid transparent; color: {INK2};
}}
QPushButton#IconBtn:hover {{ background: {WASH_HOVER}; color: {INK}; }}
QPushButton#TopIcon {{
  padding: 0; border: 0; border-radius: 8px; background: transparent;
}}
QPushButton#TopIcon:hover {{ background: {WASH_HOVER}; }}
QPushButton#TopIcon:pressed {{ background: rgba(18, 26, 43, 28); }}
QPushButton#TopIconClose {{
  padding: 0; border: 0; border-radius: 8px; background: transparent;
}}
QPushButton#TopIconClose:hover {{ background: rgba(197, 52, 52, 30); }}
QPushButton#TopIconClose:pressed {{ background: rgba(197, 52, 52, 60); }}
QPushButton#Seg {{
  background: transparent; border: 0; border-radius: 9px; color: {DIM}; padding: 6px 10px;
}}
QPushButton#Seg:hover {{ color: {INK}; background: {WASH}; }}
QPushButton#Seg:checked {{
  color: {INK}; background: {CARD};
  border: 1px solid {LINE};
}}
QPushButton#Proto {{
  text-align: left; padding: 9px 11px; border-radius: 12px;
  background: {CARD}; border: 1px solid {LINE};
  color: {INK2};
}}
QPushButton#Proto:hover {{ background: #F7F9FC; }}
QPushButton#Proto:checked {{
  color: {INK};
  background: {TEAL_SOFT};
  border: 1px solid rgba(14, 148, 132, 130);
}}

/* ---------------------------------------------------------------- scrolling */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{
  background: rgba(18, 26, 43, 46); border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(18, 26, 43, 80); }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
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
  font-size: 10.5px;
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
QListWidget {{ background: transparent; border: 0; }}
QListWidget::item {{ border: 0; padding: 0; margin: 0; }}
QCheckBox {{ spacing: 6px; color: {INK2}; }}
QCheckBox::indicator {{
  width: 14px; height: 14px; border-radius: 4px;
  border: 1px solid {LINE_STRONG}; background: {CARD};
}}
QCheckBox::indicator:hover {{ border-color: {TEAL}; }}
QCheckBox::indicator:checked {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #12B39E, stop:1 #7C5CFA);
  border-color: rgba(14, 148, 132, 160);
}}
QProgressBar {{
  background: {WASH_HOVER}; border: 0; border-radius: 5px; height: 9px;
  text-align: center; color: transparent;
}}
QProgressBar::chunk {{
  border-radius: 5px;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #12B39E, stop:1 #7C5CFA);
}}
QMenu {{
  background: {CARD}; border: 1px solid {LINE};
  border-radius: 10px; padding: 5px;
}}
QMenu::item {{ padding: 6px 18px; border-radius: 7px; }}
QMenu::item:selected {{ background: {TEAL_SOFT}; }}
QDialog {{ background: {CARD}; }}
QSplitter::handle {{ background: transparent; }}
"""


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
