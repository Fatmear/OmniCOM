"""Read-only facades handed to plugins (R-322).

A plugin must not ``import omnicom.ui.theme`` or ``omnicom.paths`` directly: that
pins an installed plugin to the host's module layout, so a refactor silently
breaks every plugin already on a user's disk.  These two objects expose only the
tokens and helpers a plugin legitimately needs, and they are read-only - a
plugin cannot repaint every window by assigning to a theme constant.
"""

from __future__ import annotations

from . import theme as _theme

#: Theme tokens a plugin may read.  Deliberately a whitelist: publishing a
#: ``__getattr__`` passthrough would re-create the coupling this file removes.
THEME_TOKENS = (
    "BG", "CARD", "INK", "INK2", "DIM",
    "TEAL", "TEAL_SOFT", "VIOLET", "VIOLET_SOFT", "BLUE",
    "AMBER", "AMBER_SOFT", "DANGER", "DANGER_SOFT", "OK", "OK_SOFT",
    "TERM_BG", "TERM_INK",
    "LINE", "LINE_SOFT", "LINE_STRONG", "WASH", "WASH_HOVER", "TEXT_DISABLED",
    "CTRL_H", "BTN_PAD", "ICON_SM", "ICON_MD", "ICON_SIZE",
    "RADIUS_CTRL", "RADIUS_CARD", "RADIUS_PILL", "CHECK_SIZE", "CHECK_RADIUS",
    "UI_FONT", "MONO_STACK", "MONO_FONT",
    "STATUS_H", "STATUS_GAP", "FONT_PX",
)

#: Theme callables a plugin may call (all pure readers).
THEME_CALLS = (
    "ui_font_px", "font_px", "log_px", "log_family", "ui_family",
    "mono_font", "menu_stylesheet", "accent", "app_stylesheet",
    "lock_ui_font",
)


class ThemeFacade:
    """Read-only view of the host palette and its metrics."""

    __slots__ = ()

    def __getattr__(self, name: str):
        if name in THEME_TOKENS:
            return getattr(_theme, name)
        if name in THEME_CALLS:
            return getattr(_theme, name)
        raise AttributeError(
            f"theme token {name!r} is not part of the plugin API "
            f"(see omnicom/plugins.py, R-322)")

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError("the theme is read-only for plugins")
