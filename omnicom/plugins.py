"""Plugin host: discover, load and reload self-contained plugin folders.

A plugin is just a folder::

    plugins/
      launcher/
        plugin.json      # metadata (id, name in zh/en, version, entry)
        plugin.py        # defines create(api) -> QWidget
        icon.png         # optional tab icon

Copy that folder to another machine, drop it into ``plugins/`` (next to the repo
root) or ``<data>/plugins/`` and press *Refresh plugins* - that is the whole
install story.  Nothing else in the app needs to change, which is the point of
the decoupling requirement.

The API handed to :func:`create` is deliberately small and stable:

    api.tr(key)                 -> translated string
    api.t(name_zh, name_en)     -> pick the right string for the active language
    api.notify(text, level)     -> toast in the main window
    api.data_dir                -> a writable folder unique to this plugin
    api.settings                -> QSettings scoped to this plugin
    api.theme                   -> the theme module (colour/metric tokens)
    api.glyph(name)             -> a painted icon button class
    api.language()              -> "zh" / "en"
    api.on_language(fn)         -> call fn when the language changes
    api.log(text)               -> append a line to the serial log as a system note
"""

from __future__ import annotations

import json
import os
import traceback
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal

from . import __version__, i18n, paths

MANIFEST = "plugin.json"
REQUIRED_FIELDS = ("id", "entry")
#: API version the host guarantees.  Bump when the shape below changes.
API_VERSION = 1


@dataclass
class PluginInfo:
    """One discoverable plugin folder."""

    path: str
    manifest: dict[str, Any]
    error: str = ""
    module: Any = None
    widget: Any = None
    api: Any = None
    _listeners: list = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.manifest.get("id") or os.path.basename(self.path))

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "0.0.0")

    def name(self, lang: str | None = None) -> str:
        lang = lang or i18n.language()
        names = self.manifest.get("name") or self.manifest.get("id") or "plugin"
        if isinstance(names, dict):
            return str(names.get(lang) or names.get("zh") or names.get("en") or self.id)
        return str(names)

    def icon_path(self) -> str | None:
        icon = self.manifest.get("icon")
        if not icon:
            return None
        path = os.path.join(self.path, icon)
        return path if os.path.isfile(path) else None


class PluginApi(QObject):
    """The only surface a plugin is allowed to touch."""

    language_changed = Signal(str)

    def __init__(self, info: PluginInfo, notify, log_line) -> None:
        super().__init__()
        self.info = info
        self._notify = notify
        self._log_line = log_line
        self._dir = os.path.join(paths.data_dir(), "plugin-data", info.id)
        os.makedirs(self._dir, exist_ok=True)
        # A plugin's state lives next to its own data, not in the registry, so the
        # whole portable folder stays self-contained (R-088).
        self.settings = QSettings(os.path.join(self._dir, "settings.ini"),
                                  QSettings.IniFormat)

    # ------------------------------------------------------------------ data
    @property
    def data_dir(self) -> str:
        return self._dir

    @property
    def plugin_dir(self) -> str:
        return self.info.path

    @property
    def app_version(self) -> str:
        return __version__

    @property
    def api_version(self) -> int:
        return API_VERSION

    # -------------------------------------------------------------- services
    @staticmethod
    def tr(key: str, **kwargs) -> str:
        return i18n.tr(key, **kwargs)

    @staticmethod
    def language() -> str:
        return i18n.language()

    def t(self, chinese: str, english: str) -> str:
        """Inline translation for plugin strings (no key registry needed)."""
        return chinese if i18n.language() == "zh" else english

    def notify(self, text: str, level: str = "info") -> None:
        if self._notify:
            self._notify(text, level)

    def log(self, text: str) -> None:
        if self._log_line:
            self._log_line(text)

    def on_language(self, callback) -> None:
        self.info._listeners.append(callback)

    @staticmethod
    def theme():
        from .ui import theme as theme_module

        return theme_module

    @staticmethod
    def glyph(name: str, size: int | None = None):
        """Return a painted icon-button *class* for the requested glyph."""
        from .ui import theme as theme_module
        from .ui.widgets import GlyphButton

        size = size or theme_module.ICON_MD

        def factory(glyph: str | None = None, parent=None) -> GlyphButton:
            return GlyphButton(glyph or name, parent, size=size, object_name="IconBtn")

        return factory


class PluginHost(QObject):
    """Owns discovery, instancing and reload of every plugin."""

    changed = Signal()

    def __init__(self, notify=None, log_line=None, parent=None) -> None:
        super().__init__(parent)
        self.plugins: list[PluginInfo] = []
        self._notify = notify
        self._log_line = log_line

    # ----------------------------------------------------------------- scan
    @staticmethod
    def search_paths() -> list[str]:
        """Repo ``plugins/`` first, then the per-user folder."""
        found = [os.path.join(paths.resource_dir(), "plugins"),
                 os.path.join(paths.data_dir(), "plugins")]
        keep = []
        for path in found:
            if os.path.isdir(path) and path not in keep:
                keep.append(path)
        return keep

    def discover(self) -> list[PluginInfo]:
        infos: list[PluginInfo] = []
        seen: set[str] = set()
        for root in self.search_paths():
            for entry in sorted(os.listdir(root)):
                folder = os.path.join(root, entry)
                if not os.path.isdir(folder):
                    continue
                manifest_path = os.path.join(folder, MANIFEST)
                if not os.path.isfile(manifest_path):
                    continue
                try:
                    with open(manifest_path, encoding="utf-8") as handle:
                        manifest = json.load(handle)
                except (OSError, json.JSONDecodeError) as exc:
                    infos.append(PluginInfo(folder, {"id": entry}, error=str(exc)))
                    continue
                missing = [key for key in REQUIRED_FIELDS if not manifest.get(key)]
                if missing:
                    infos.append(PluginInfo(
                        folder, manifest,
                        error=f"{MANIFEST} 缺少字段: {', '.join(missing)}"))
                    continue
                info = PluginInfo(folder, manifest)
                if info.id in seen:
                    continue
                seen.add(info.id)
                infos.append(info)
        self.plugins = infos
        return infos

    # ----------------------------------------------------------------- load
    def load(self) -> list[PluginInfo]:
        """Discover + import + instantiate every plugin.  Never raises."""
        self.discover()
        loaded: list[PluginInfo] = []
        for info in self.plugins:
            if info.error:
                self._report(info.name(), info.error)
                continue
            try:
                info.widget = self._instantiate(info)
                loaded.append(info)
            except Exception as exc:
                info.error = f"{type(exc).__name__}: {exc}"
                self._report(info.name(), info.error, traceback.format_exc())
        self.plugins = loaded + [item for item in self.plugins if item.error]
        self.changed.emit()
        return self.plugins

    def _instantiate(self, info: PluginInfo):
        import importlib.util

        entry = os.path.join(info.path, str(info.manifest.get("entry") or "plugin.py"))
        if not os.path.isfile(entry):
            raise FileNotFoundError(f"入口文件不存在: {os.path.basename(entry)}")
        module_name = f"omnicom_plugin_{info.id}"
        spec = importlib.util.spec_from_file_location(module_name, entry)
        if spec is None or spec.loader is None:
            raise ImportError("无法创建模块规格")
        module = importlib.util.module_from_spec(spec)
        # The plugin folder itself must be importable for its own helpers.
        import sys

        if info.path not in sys.path:
            sys.path.insert(0, info.path)
        spec.loader.exec_module(module)
        info.module = module

        create = getattr(module, "create", None)
        if create is None:
            raise AttributeError("插件必须定义 create(api) -> QWidget")
        api = PluginApi(info, self._notify, self._log_line)
        widget = create(api)
        if widget is None:
            raise ValueError("create(api) 返回了 None")
        info.api = api
        return widget

    def _report(self, name: str, error: str, extra: str = "") -> None:
        text = i18n.tr("plugin.failed", name=name, err=error)
        if self._notify:
            self._notify(text, "error")
        if self._log_line:
            self._log_line(text)
        if extra:
            import sys

            print(extra, file=sys.stderr)

    # --------------------------------------------------------------- reload
    def reload(self) -> list[PluginInfo]:
        for info in self.plugins:
            widget = info.widget
            if widget is not None:
                try:
                    widget.setParent(None)
                    widget.deleteLater()
                except RuntimeError:
                    pass
            info.widget = None
            info.module = None
        return self.load()

    # -------------------------------------------------------------- language
    def apply_language(self) -> None:
        for info in self.plugins:
            for callback in list(info._listeners):
                try:
                    callback(i18n.language())
                except Exception:
                    continue

    def loaded(self) -> list[PluginInfo]:
        return [info for info in self.plugins if info.widget is not None]
