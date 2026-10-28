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

import os
import sys
from typing import Any

from PySide6.QtGui import QPixmap

# ------------------------------------------------------------------ palette
BG = "#EDF1F7"          # flat page backdrop (also the window's dominant colour)
CARD = "#FFFFFF"        # card / bar surface
INK = "#121A2B"         # primary text
INK2 = "#46536B"        # secondary text
DIM = "#4A5670"         # hints, captions, gutters (>= 5.4:1 on the glass floor)

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


# ------------------------------------------------------------ accent themes
# Each entry: key -> (中文, English, ink colour, soft tint).  Every ink colour
# was measured against CARD and against its own soft tint and stays >= 4.5:1,
# which is what keeps `tools/contrast_audit.py` meaningful after a switch.
ACCENTS: dict[str, tuple[str, str, str, str]] = {
    "teal": ("青绿", "Teal", "#0B7A6E", "#DFF7F3"),
    "blue": ("靛蓝", "Indigo", "#1D4ED8", "#E3ECFE"),
    "violet": ("紫罗兰", "Violet", "#6D4AE0", "#EEE9FE"),
    "green": ("松绿", "Green", "#0A7A54", "#E4F6EE"),
    "amber": ("琥珀", "Amber", "#B45309", "#FFF4E2"),
    "rose": ("玫红", "Rose", "#BE1E4E", "#FDECF2"),
    "slate": ("石墨", "Slate", "#334155", "#E8EDF5"),
}
DEFAULT_ACCENT = "teal"
_accent = DEFAULT_ACCENT

#: Wallpaper path chosen by the user ("" / None = flat colour, see R-020).
_wallpaper: str | None = None
_wallpaper_cache: tuple[tuple[str, float], Any] | None = None

# --------------------------------------------------------- frosted glass (R-077)
#: White veil over the page photo.  Lower = the picture reads stronger; the
#: panes deliberately stay lighter and softer than this, which is what makes
#: them look like glass instead of paint.
PAGE_VEIL = 150
#: White baked into the blurred layer the panes sample (0 = the photo as-is).
GLASS_VEIL = 88
#: White wash painted on top of that sample; the pair decides how "milky" a
#: pane looks.  ``glass_floor()`` derives the worst-case colour from these two
#: numbers so ``tools/contrast_audit.py`` keeps proving the captions are legible.
GLASS_ALPHA = 180
GLASS_STRONG_ALPHA = 205
#: Downscale factor used to fake a heavy backdrop blur (cheap: two smooth scales).
GLASS_BLUR_DIV = 16

_glass_cache: tuple[tuple[str, float, int, int], Any] | None = None


def accent() -> str:
    return _accent


def accent_choices() -> list[tuple[str, str, str]]:
    """``[(key, chinese, english)]`` in menu order."""
    return [(key, zh, en) for key, (zh, en, _ink, _soft) in ACCENTS.items()]


def set_accent(name: str) -> str:
    """Retint the accent family in place; unknown names fall back to the default."""
    global _accent, TEAL, TEAL_SOFT, TEAL_RGB, PRIMARY_A, PRIMARY_B
    key = name if name in ACCENTS else DEFAULT_ACCENT
    _accent = key
    _zh, _en, ink, soft = ACCENTS[key]
    TEAL, TEAL_SOFT = ink, soft
    TEAL_RGB = _rgb_triplet(ink)
    PRIMARY_A, PRIMARY_B = _lighten(ink, 0.18), _darken(ink, 0.12)
    return key


def _rgb_triplet(color: str) -> str:
    value = color.lstrip("#")
    return ", ".join(str(int(value[index:index + 2], 16)) for index in (0, 2, 4))


def _mix(color: str, target: int, weight: float) -> str:
    value = color.lstrip("#")
    out = []
    for index in (0, 2, 4):
        channel = int(value[index:index + 2], 16)
        out.append(round(channel + (target - channel) * weight))
    return "#{:02X}{:02X}{:02X}".format(*out)


def _lighten(color: str, weight: float) -> str:
    return _mix(color, 255, weight)


def _darken(color: str, weight: float) -> str:
    return _mix(color, 0, weight)


def wallpaper() -> str | None:
    return _wallpaper


def set_wallpaper(path: str | None) -> str | None:
    """Use ``path`` as the page backdrop; an empty value restores the flat fill."""
    global _wallpaper, _wallpaper_cache
    global _glass_cache
    cleaned = (path or "").strip()
    if not cleaned:
        _wallpaper = None
        _wallpaper_cache = None
        _glass_cache = None
        return None
    absolute = os.path.abspath(os.path.expanduser(cleaned))
    if not os.path.isfile(absolute):
        _wallpaper = None
        _wallpaper_cache = None
        _glass_cache = None
        return None
    _wallpaper = absolute
    _wallpaper_cache = None
    _glass_cache = None
    return absolute


def wallpaper_pixmap():
    """Decoded wallpaper (cached by path + mtime); ``None`` when unset/broken."""
    global _wallpaper_cache
    if not _wallpaper:
        return None
    try:
        stamp = os.path.getmtime(_wallpaper)
    except OSError:
        return None
    key = (_wallpaper, stamp)
    if _wallpaper_cache is None or _wallpaper_cache[0] != key:
        pixmap = QPixmap(_wallpaper)
        _wallpaper_cache = (key, None) if pixmap.isNull() else (key, pixmap)
    return _wallpaper_cache[1]


def cover_pixmap(pixmap, width: int, height: int):
    """Scale ``pixmap`` to cover ``width`` x ``height`` and centre-crop it.

    One helper for both the page backdrop and the panes, so the frosted sample
    lines up with the photo painted behind it instead of drifting.
    """
    from PySide6.QtCore import Qt

    if pixmap is None or pixmap.isNull() or width <= 0 or height <= 0:
        return None
    scaled = pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
    return scaled.copy(max(0, (scaled.width() - width) // 2),
                       max(0, (scaled.height() - height) // 2), width, height)


def glass_layer(width: int, height: int):
    """Blurred + veiled wallpaper that the panes sample for their glass (R-077).

    Two smooth scales (down to 1/16, back up) stand in for a real gaussian blur:
    it costs a couple of milliseconds, is cached per size, and at this veil the
    difference from a true blur is invisible.
    """
    global _glass_cache
    pixmap = wallpaper_pixmap()
    if pixmap is None or width <= 0 or height <= 0:
        return None
    try:
        stamp = os.path.getmtime(str(_wallpaper))
    except (OSError, TypeError):
        return None
    key = (str(_wallpaper), stamp, width, height)
    if _glass_cache is not None and _glass_cache[0] == key:
        return _glass_cache[1]

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter

    cover = cover_pixmap(pixmap, width, height)
    if cover is None:
        return None
    tiny = cover.scaled(max(1, width // GLASS_BLUR_DIV), max(1, height // GLASS_BLUR_DIV),
                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    blurred = tiny.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    painter = QPainter(blurred)
    painter.fillRect(blurred.rect(), QColor(255, 255, 255, GLASS_VEIL))
    painter.end()
    _glass_cache = (key, blurred)
    return blurred


def _composite(alpha: int, base: int) -> str:
    wash = alpha / 255.0
    value = round(255.0 * wash + base * (1.0 - wash))
    return f"#{value:02X}{value:02X}{value:02X}"


def glass_floor() -> str:
    """Darkest colour a pane can take (over a black photo) - worst case for text."""
    return _composite(GLASS_ALPHA, round(255.0 * (GLASS_VEIL / 255.0)))


def page_floor() -> str:
    """Darkest the page itself can get: the veil over a black photo.

    The bottom captions sit straight on the page now (no plate, R-082), so this
    is the background they have to stay readable against.
    """
    return f"#{PAGE_VEIL:02X}{PAGE_VEIL:02X}{PAGE_VEIL:02X}"


#: Read by the stylesheet so selection tints and primary buttons follow the
#: chosen accent instead of a literal colour.
TEAL = ACCENTS[DEFAULT_ACCENT][2]
TEAL_SOFT = ACCENTS[DEFAULT_ACCENT][3]
TEAL_RGB = _rgb_triplet(TEAL)
PRIMARY_A = _lighten(TEAL, 0.18)
PRIMARY_B = _darken(TEAL, 0.12)


def app_stylesheet() -> str:
    return f"""
* {{
  font-family: {UI_FONT};
  font-size: {FONT_PX}px;
  color: {INK};
  outline: none;
}}
QWidget#Root {{ background: transparent; }}
/* the caption row paints nothing at all - text straight on the page (R-082) */
QWidget#StatusStrip {{ background: transparent; border: 0; }}
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
  selection-background-color: rgba({TEAL_RGB}, 60);
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
  border: 1px solid rgba({TEAL_RGB}, 170);
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 {PRIMARY_A}, stop:0.55 {TEAL}, stop:1 {PRIMARY_B});
}}
QPushButton#Primary:hover {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 {PRIMARY_A}, stop:0.55 {TEAL}, stop:1 {PRIMARY_B});
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
  border: 1px solid rgba({TEAL_RGB}, 130);
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
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
      stop:0 {PRIMARY_A}, stop:1 {PRIMARY_B});
  border-color: rgba({TEAL_RGB}, 160);
}}
QProgressBar {{
  background: {WASH_HOVER}; border: 0; border-radius: 5px; height: 9px;
  text-align: center; color: transparent;
}}
QProgressBar::chunk {{
  border-radius: 5px;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
      stop:0 {PRIMARY_A}, stop:1 {PRIMARY_B});
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
