"""OmniCOM visual language: palette, stylesheet and the Windows glass hook."""

from __future__ import annotations

import sys

# ------------------------------------------------------------------ palette
BG = "#04050A"
INK = "#E9EEFB"
INK2 = "#B6C2DC"
DIM = "#7D89A6"
TEAL = "#5EEAD4"
VIOLET = "#A78BFA"
BLUE = "#60A5FA"
AMBER = "#FBBF24"
DANGER = "#F87171"
OK = "#34D399"

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
  background: rgba(16, 20, 30, 235);
  color: {INK};
  border: 1px solid rgba(255, 255, 255, 45);
  border-radius: 8px;
  padding: 6px 9px;
}}

/* ------------------------------------------------------------- text inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: rgba(9, 12, 20, 140);
  border: 1px solid rgba(255, 255, 255, 28);
  border-radius: 11px;
  padding: 6px 10px;
  selection-background-color: rgba(94, 234, 212, 90);
  selection-color: #ffffff;
}}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
  border-color: rgba(255, 255, 255, 52);
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{
  border: 1px solid rgba(94, 234, 212, 150);
  background: rgba(9, 12, 20, 190);
}}
QLineEdit:disabled, QComboBox:disabled {{ color: {DIM}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{
  image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
  border-top: 5px solid {DIM}; width: 0; height: 0; margin-right: 7px;
}}
QComboBox QAbstractItemView {{
  background: #10141e;
  border: 1px solid rgba(255, 255, 255, 40);
  border-radius: 10px;
  padding: 4px;
  selection-background-color: rgba(94, 234, 212, 55);
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
  background: rgba(255, 255, 255, 16);
  border: 1px solid rgba(255, 255, 255, 30);
  border-radius: 10px;
  padding: 7px 13px;
  color: {INK};
  font-weight: 500;
}}
QPushButton:hover {{ background: rgba(255, 255, 255, 26); border-color: rgba(255, 255, 255, 52); }}
QPushButton:pressed {{ background: rgba(255, 255, 255, 10); }}
QPushButton:disabled {{ color: rgba(182, 194, 220, 105); background: rgba(255, 255, 255, 8); }}
QPushButton#Primary {{
  color: #04121A;
  font-weight: 650;
  border: 1px solid rgba(255, 255, 255, 90);
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 #7DF3E0, stop:0.5 #8ED8F0, stop:1 #B6A6FF);
}}
QPushButton#Primary:hover {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 #93F8E8, stop:0.5 #A3E2F5, stop:1 #C6B8FF);
}}
QPushButton#Primary:disabled {{ color: rgba(4, 18, 26, 120); }}
QPushButton#Danger {{
  color: #FFE4E4; border-color: rgba(248, 113, 113, 110);
  background: rgba(248, 113, 113, 40);
}}
QPushButton#Ghost {{
  background: rgba(255, 255, 255, 10);
  color: {INK2};
  border-color: rgba(255, 255, 255, 22);
}}
QPushButton#Ghost:hover {{ color: {INK}; background: rgba(255, 255, 255, 22); }}
QPushButton#IconBtn {{
  padding: 0; border-radius: 9px; font-size: 14px;
  background: transparent; border: 1px solid transparent; color: {INK2};
}}
QPushButton#IconBtn:hover {{ background: rgba(255, 255, 255, 30); color: {INK}; }}
QPushButton#Seg {{
  background: transparent; border: 0; border-radius: 9px; color: {DIM}; padding: 6px 10px;
}}
QPushButton#Seg:hover {{ color: {INK}; background: rgba(255, 255, 255, 18); }}
QPushButton#Seg:checked {{
  color: #FFFFFF; background: rgba(255, 255, 255, 34);
  border: 1px solid rgba(255, 255, 255, 40);
}}
QPushButton#Proto {{
  text-align: left; padding: 9px 11px; border-radius: 12px;
  background: rgba(255, 255, 255, 12); border: 1px solid rgba(255, 255, 255, 24);
  color: {INK2};
}}
QPushButton#Proto:hover {{ background: rgba(255, 255, 255, 24); }}
QPushButton#Proto:checked {{
  color: #FFFFFF;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 rgba(94, 234, 212, 52), stop:1 rgba(167, 139, 250, 46));
  border: 1px solid rgba(94, 234, 212, 130);
}}

/* ---------------------------------------------------------------- scrolling */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{
  background: rgba(255, 255, 255, 38); border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255, 255, 255, 68); }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: rgba(255, 255, 255, 38); border-radius: 4px; min-width: 28px; }}
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
  color: {DIM};
  background: rgba(255, 255, 255, 16);
  border: 1px solid rgba(255, 255, 255, 24);
  border-radius: 9px;
  padding: 1px 8px;
  font-size: 10.5px;
}}
QLabel#BadgeWarn {{
  color: #FFE9C2;
  background: rgba(251, 191, 36, 34);
  border: 1px solid rgba(251, 191, 36, 90);
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
  border: 1px solid rgba(255, 255, 255, 60); background: rgba(255, 255, 255, 12);
}}
QCheckBox::indicator:hover {{ border-color: rgba(94, 234, 212, 150); }}
QCheckBox::indicator:checked {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5EEAD4, stop:1 #A78BFA);
  border-color: rgba(255, 255, 255, 110);
}}
QProgressBar {{
  background: rgba(255, 255, 255, 14); border: 0; border-radius: 5px; height: 9px;
  text-align: center; color: transparent;
}}
QProgressBar::chunk {{
  border-radius: 5px;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5EEAD4, stop:1 #A78BFA);
}}
QMenu {{
  background: #0E121C; border: 1px solid rgba(255, 255, 255, 40);
  border-radius: 10px; padding: 5px;
}}
QMenu::item {{ padding: 6px 18px; border-radius: 7px; }}
QMenu::item:selected {{ background: rgba(94, 234, 212, 50); }}
QDialog {{ background: #0B0F18; }}
QSplitter::handle {{ background: transparent; }}
"""


# --------------------------------------------------------------- real glass
def enable_windows_glass(hwnd: int, tint: int = 0xCC0A0504) -> bool:
    """Ask DWM for an acrylic backdrop (Windows 10/11).

    The tint is ABGR; the default is a very dark blue with ~80% opacity.  This
    is what makes the window genuinely translucent instead of merely dark.
    Returns False on any non-Windows platform or when the API is unavailable,
    in which case the painted aurora fallback is used.
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
