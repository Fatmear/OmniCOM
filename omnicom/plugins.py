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

    api.tr(key)                 -> translated string (host + registered plugin keys)
    api.register_text(table)    -> add this plugin's own (zh, en) text table
    api.t(name_zh, name_en)     -> pick the right string for the active language
    api.notify(text, level)     -> toast in the main window
    api.data_dir                -> a writable folder unique to this plugin
    api.settings                -> QSettings scoped to this plugin
    api.theme                   -> read-only theme facade (colours + metrics)
    api.widget(kind, **kwargs)  -> host-styled control (button/combo/card/...)
    api.glyph(name)             -> a painted icon button factory
    api.language()              -> "zh" / "en"
    api.on_language(fn)         -> call fn when the language changes
    api.log(text)               -> append a line to the serial log as a system note
    api.send(data)              -> write bytes to the open port (False if closed)
    api.paths                   -> read-only host path facade
    api.terminal_screen()       -> the VT100 screen model (terminal plugin)
    api.transfer_worker()       -> the X/Y/ZMODEM worker class (transfer plugin)

Everything above is a *facade*: no plugin imports ``omnicom.ui.*`` or
``omnicom.i18n`` directly, so the host can refactor those modules without
breaking an installed plugin (R-322).  ``plugin_selftest`` enforces it.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal

from . import __version__, i18n, paths

MANIFEST = "plugin.json"
REQUIRED_FIELDS = ("id", "entry")

#: Plugins the program itself must provide (R-293): without them there is no
#: console page and no transfer page, and the store never offers them (R-174).
#: Kept in sync with ``tools/make_dist.CORE_PLUGINS``.
CORE_PLUGINS = ("terminal", "transfer")
#: API version the host guarantees.  Bump when the shape below changes.
#:
#: 3 - plugins stopped importing ``omnicom.ui.*`` / ``omnicom.i18n`` and use the
#:     facades instead (``api.theme``, ``api.widget``, ``api.paths``,
#:     ``api.terminal_screen``, ``api.transfer_worker``, ``api.register_text``).
API_VERSION = 3


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
    _rx_listeners: list = field(default_factory=list)
    _theme_listeners: list = field(default_factory=list)
    _replay_listeners: list = field(default_factory=list)
    _cleanups: list = field(default_factory=list)
    #: ``name -> factory()`` services this plugin offers to the host/other
    #: plugins while it is loaded (R-218).
    _services: dict = field(default_factory=dict)

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

    def page_key(self) -> str:
        """Key of the top-bar tab this plugin owns (R-326).

        A plugin may *claim* a long-standing page key through ``"page"`` in its
        manifest - ``"console"`` for the terminal, ``"transfer"`` for file
        transfers.  Those keys predate the plugin split and are part of the
        window's public contract (``current_page()``, the CLI and the test
        suite), so honouring them keeps the API stable **without** the host
        hard-coding any plugin id.
        """
        claimed = str(self.manifest.get("page") or "").strip()
        return claimed or f"plugin:{self.id}"

    def page_order(self) -> int:
        """Position of this plugin's tab; lower comes first (R-326)."""
        try:
            return int(self.manifest.get("page_order", 100))
        except (TypeError, ValueError):
            return 100


class PluginApi(QObject):
    """The only surface a plugin is allowed to touch."""

    language_changed = Signal(str)

    def __init__(self, info: PluginInfo, notify, log_line, send_bytes=None,
                 service_lookup=None, service_register=None,
                 port_lookup=None, host=None) -> None:
        super().__init__()
        self.info = info
        self._host = host
        self._notify = notify
        self._log_line = log_line
        self._send_bytes = send_bytes
        self._service_lookup = service_lookup
        self._service_register = service_register
        self._port_lookup = port_lookup
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

    def register_text(self, table: dict[str, tuple[str, str]]) -> None:
        """Add this plugin's own translation table (R-322).

        A plugin ships its copy with its own folder instead of pushing 58 keys
        into the host's table: the host table stays about the host, and an
        uninstalled plugin leaves no orphaned strings behind.  Keys registered
        here are looked up by :meth:`tr` and are removed on unload.
        """
        i18n.register_plugin_table(self.info.id, table, owner=self._host)
        self.info.manifest.setdefault("text_keys", sorted(table))

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

    def send(self, data: bytes) -> bool:
        """Write bytes to the open port through the host's own send path.

        Deliberately not a serial handle: the host decides whether a port is
        open, prints the TX line and keeps the byte counters honest.  Returns
        ``False`` when nothing was written, so a plugin can tell the user
        instead of assuming the command went out.
        """
        if not self._send_bytes or not data:
            return False
        try:
            return bool(self._send_bytes(bytes(data)))
        except Exception:
            return False

    def port(self):
        """The host's :class:`PortManager`, or ``None`` (R-218).

        Needed by plugins that speak a *protocol* over the port (X/Y/ZMODEM)
        rather than just writing bytes: those engines need ``read``/``write``
        plus a live "is it open" answer.  Ordinary plugins should keep using
        :meth:`send`/``on_rx`` - this is the escape hatch, not the default.
        """
        lookup = self._port_lookup
        return lookup() if lookup else None

    def is_connected(self) -> bool:
        manager = self.port()
        if manager is None:
            return False
        try:
            return bool(manager.status().get("connected"))
        except Exception:
            return False

    def on_language(self, callback) -> None:
        self.info._listeners.append(callback)

    def on_rx(self, callback) -> None:
        self.info._rx_listeners.append(callback)

    def on_theme(self, callback) -> None:
        self.info._theme_listeners.append(callback)

    def on_replay(self, callback) -> None:
        """Call ``callback(seconds, payload)`` for every line of a replayed log.

        Replaying a saved log is a *host* action (File > Open log → Replay), but
        only the plugin knows how to interpret its own payload lines.  Giving
        plugins a subscription keeps that knowledge where it belongs (R-302).
        """
        self.info._replay_listeners.append(callback)

    def on_unload(self, callback) -> None:
        self.info._cleanups.append(callback)

    # ------------------------------------------------- plugin-to-host services
    def service(self, name: str):
        """Ask the host for a named service object, or ``None`` (R-218).

        Used to keep the CLI contract working when a feature is an optional
        plugin: ``cli screen`` asks for the ``"terminal"`` service and reports
        "终端插件未启用" when nothing provides it, instead of returning a blank
        screen.  A plugin may also call this to reach another plugin.
        """
        lookup = self._service_lookup
        return lookup(name) if lookup else None

    def provide(self, name: str, factory) -> None:
        """Expose ``factory()`` under ``name`` while this plugin is loaded.

        The factory is called on demand (never eagerly), so an unused service
        costs nothing.
        """
        self.info.manifest.setdefault("services", {})
        self.info._services[name] = factory
        register = self._service_register
        if register:
            register(self.info.id, name, factory)

    @staticmethod
    def theme():
        """A read-only view of the host palette (colours + metrics).

        Returning the module itself let a plugin set ``theme.CARD`` and repaint
        every window, which is not something an installed plugin should be able
        to do.  The facade exposes the tokens read-only (R-322).
        """
        from .ui.theme_facade import ThemeFacade

        return ThemeFacade()

    @staticmethod
    def paths():
        """Read-only host folder helpers (``received_dir()``, ``data_dir()``...)."""
        from .paths_facade import PathsFacade

        return PathsFacade()

    @staticmethod
    def widget_class(name: str):
        """Return a host-styled widget *class* by stable name (R-322).

        A plugin used to ``from omnicom.ui.widgets import Button, GlassFrame``
        and thereby pinned itself to that module's layout: renaming or moving
        the file broke every plugin already installed on a user's disk.  The
        names below are the contract instead:

        ``button`` themed push button · ``icon`` painted glyph button ·
        ``combo`` drop-down with the painted chevron · ``card`` glass card ·
        ``log`` the log/terminal text view.
        """
        from .ui import widgets as widget_module

        kinds = {
            "button": lambda: widget_module.Button,
            "pushbutton": lambda: widget_module.Button,
            "icon": lambda: widget_module.GlyphButton,
            "glyph": lambda: widget_module.GlyphButton,
            "combo": lambda: widget_module.Combo,
            "dropdown": lambda: widget_module.Combo,
            "card": lambda: widget_module.GlassFrame,
            "frame": lambda: widget_module.GlassFrame,
        }
        key = str(name or "").lower()
        factory = kinds.get(key)
        if factory is None and key in ("log", "logview"):
            from .ui.log_view import LogView

            return LogView
        if factory is None:
            raise ValueError(f"unknown widget kind: {name}")
        return factory()

    @staticmethod
    def terminal_screen():
        """The VT100 screen model plus its colour tables (R-322).

        Small, Qt-free and shared by the terminal plugin: exposing it keeps the
        parser in one place instead of letting a plugin vendor its own copy.
        """
        from .ui import vt100

        return vt100

    @staticmethod
    def transfer_worker():
        """The X/Y/ZMODEM worker ``QThread`` class (R-322)."""
        from .ui.workers import TransferWorker

        return TransferWorker

    @staticmethod
    def combo(parent=None, editable: bool = False):
        """Return the host's themed drop-down (painted chevron, R-018).

        A plugin that builds a bare ``QComboBox`` gets the platform arrow and a
        square popup, which looks foreign next to every other control.  Use this
        factory rather than ``QComboBox`` so a future metric change reaches the
        plugin pages too.
        """
        from .ui.widgets import Combo

        return Combo(parent, editable=editable)

    @staticmethod
    def menu(parent=None):
        """Return a popup menu that already carries the light theme (R-152).

        A ``QMenu`` is a top-level window and is *not* covered by the ``*`` rule
        of the application sheet, so a bare one can render black with white text
        on a dark Windows palette (L-193).  Plugins must build menus through this
        factory and never with ``QMenu()`` directly.
        """
        from PySide6.QtWidgets import QMenu

        from .ui import theme as theme_module

        menu = QMenu(parent)
        menu.setStyleSheet(theme_module.menu_stylesheet())
        return menu

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

    def __init__(self, notify=None, log_line=None, send_bytes=None, parent=None,
                 port=None) -> None:
        super().__init__(parent)
        self.plugins: list[PluginInfo] = []
        #: Identity used as the owner of the plugin text tables, so unloading a
        #: second window cannot erase copy the first window is still drawing
        #: (R-326).
        self._host = self
        self._notify = notify
        self._log_line = log_line
        self._send_bytes = send_bytes
        #: Callable returning the host's PortManager (see :meth:`PluginApi.port`).
        self._port = port
        #: ``service name -> factory()``, cleared on every reload so a disabled
        #: plugin cannot keep answering (R-218).
        self._services: dict[str, Any] = {}
        self._service_owner: dict[str, str] = {}

    # ------------------------------------------------------------- services
    def service(self, name: str):
        """Return a provided service object, or ``None`` (R-218)."""
        factory = self._services.get(name)
        if factory is None:
            return None
        try:
            return factory()
        except Exception:                      # a broken plugin must not crash us
            return None

    def has_service(self, name: str) -> bool:
        return name in self._services

    def _register_service(self, plugin_id: str, name: str, factory) -> None:
        self._services[name] = factory
        self._service_owner[name] = plugin_id

    # ----------------------------------------------------------------- scan
    @staticmethod
    def search_paths() -> list[str]:
        """User-installed updates override bundled copies of the same plugin."""
        found = [os.path.join(paths.data_dir(), "plugins"),
                 os.path.join(paths.resource_dir(), "plugins")]
        keep = []
        for path in found:
            if os.path.isdir(path) and path not in keep:
                keep.append(path)
        return keep

    def discover(self) -> list[PluginInfo]:
        infos: list[PluginInfo] = []
        seen: set[str] = set()
        disabled = set()
        try:
            with open(os.path.join(paths.data_dir(), "plugins-disabled.json"),
                      encoding="utf-8") as handle:
                disabled = set(json.load(handle))
        except (OSError, ValueError, TypeError):
            pass
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
                if info.id in seen or info.id in disabled:
                    continue
                seen.add(info.id)
                infos.append(info)
        self.plugins = infos
        return infos

    # ----------------------------------------------------------------- load
    def restore_core_plugins(self) -> list[str]:
        """Re-ship the bundled core plugins that are missing on disk (R-332).

        ``terminal`` and ``transfer`` ship **inside** the program (R-293); the
        host has fixed tabs for them and nothing else provides those pages.  An
        install can still lose them - an unpacked 1.1.6-era copy, a partial
        copy, a folder deleted by hand - and the user then simply has no
        console and no transfer page, with no way to get them from the store
        (they are not store plugins, R-174).

        Restoring is safe because the bundled copy is the authority for these
        two ids and they are never user-edited in place: a folder that exists
        is left untouched (the user may have disabled it, which is a *state*,
        not a missing file), and ``plugin-data/<id>`` is never touched.
        """
        restored: list[str] = []
        bundled = Path(paths.resource_dir()) / "plugins"
        user_root = Path(paths.data_dir()) / "plugins"
        for name in CORE_PLUGINS:
            source = bundled / name
            if not (source / MANIFEST).is_file():
                continue                       # a trimmed build without it
            targets = [user_root / name, bundled / name]
            if any((target / MANIFEST).is_file() for target in targets):
                continue                       # present: keep whatever is there
            destination = user_root / name
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, destination,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            except OSError:
                continue                       # read-only install: report later
            restored.append(name)
        return restored

    def load(self) -> list[PluginInfo]:
        """Discover + import + instantiate every plugin.  Never raises."""
        # A missing core plugin is repaired first: the pages are decided by
        # discovery, so restoring it afterwards would need a second reload.
        self.restore_core_plugins()
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
                for callback in list(info._cleanups):
                    with contextlib.suppress(Exception):
                        callback()
                for listeners in (info._listeners, info._rx_listeners, info._theme_listeners, info._cleanups):
                    listeners.clear()
                if info.api is not None:
                    info.api.deleteLater()
                    info.api = None
                i18n.unregister_plugin_table(info.id, owner=self._host)
                info.error = f"{type(exc).__name__}: {exc}"
                self._report(info.name(), info.error, traceback.format_exc())
        self.plugins = loaded + [item for item in self.plugins if item.error]
        self.changed.emit()
        return self.plugins

    def _instantiate(self, info: PluginInfo):
        import importlib.util

        if int(info.manifest.get("api", 1)) > API_VERSION:
            raise ValueError(i18n.tr("store.err.api"))
        entry = os.path.abspath(os.path.join(info.path, str(info.manifest.get("entry") or "plugin.py")))
        if os.path.commonpath([os.path.realpath(info.path), os.path.realpath(entry)]) != os.path.realpath(info.path):
            raise ValueError(i18n.tr("store.err.path"))
        if not os.path.isfile(entry):
            raise FileNotFoundError(f"入口文件不存在: {os.path.basename(entry)}")
        module_name = f"omnicom_plugin_{info.id}"
        spec = importlib.util.spec_from_file_location(
            module_name, entry, submodule_search_locations=[info.path])
        if spec is None or spec.loader is None:
            raise ImportError("无法创建模块规格")
        module = importlib.util.module_from_spec(spec)
        # The plugin folder itself must be importable for its own helpers.
        import sys

        sys.modules[module_name] = module
        if info.path not in sys.path:
            sys.path.insert(0, info.path)
        spec.loader.exec_module(module)
        info.module = module

        create = getattr(module, "create", None)
        if create is None:
            raise AttributeError("插件必须定义 create(api) -> QWidget")
        api = PluginApi(info, self._notify, self._log_line, self._send_bytes,
                        service_lookup=self.service,
                        service_register=self._register_service,
                        port_lookup=(lambda: self._port), host=self)
        info.api = api
        widget = create(api)
        from PySide6.QtWidgets import QWidget

        if not isinstance(widget, QWidget):
            raise ValueError("create(api) 返回了 None")
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
        self.unload()
        return self.load()

    def unload(self) -> None:
        import sys

        # Services die with their plugin, or a disabled terminal would keep
        # answering `cli screen` from a half-torn-down widget (R-218).
        self._services.clear()
        self._service_owner.clear()
        for info in self.plugins:
            for callback in list(info._cleanups):
                try:
                    callback()
                except Exception as exc:
                    self._report(info.name(), str(exc))
            for listeners in (info._listeners, info._rx_listeners,
                              info._theme_listeners, info._cleanups):
                listeners.clear()
            widget = info.widget
            if widget is not None:
                # A widget may own something Qt refuses to destroy out of
                # order (a QWebEngineView outliving its profile kills the
                # process at exit with 0xC0000005).  Such a widget exposes
                # ``dispose()`` and tears that down itself, synchronously,
                # before the generic delete below (L-221).
                release = getattr(widget, "dispose", None)
                if callable(release):
                    try:
                        release()
                    except Exception as exc:
                        self._report(info.name(), str(exc))
                try:
                    widget.setParent(None)
                    widget.deleteLater()
                except RuntimeError:
                    pass
            info.widget = None
            info.module = None
            i18n.unregister_plugin_table(info.id, owner=self._host)
            if info.api is not None:
                info.api.deleteLater()
                info.api = None
            prefix = f"omnicom_plugin_{info.id}"
            for name in list(sys.modules):
                if name == prefix or name.startswith(prefix + "."):
                    del sys.modules[name]

    def publish_rx(self, ts: float, data: bytes) -> None:
        for info in self.loaded():
            for callback in list(info._rx_listeners):
                try:
                    callback(ts, data)
                except Exception as exc:
                    info._rx_listeners.remove(callback)
                    self._report(info.name(), str(exc))

    def publish_replay(self, seconds: float, payload: str) -> None:
        """Fan one replayed log line out to every subscribed plugin (R-302)."""
        for info in self.loaded():
            for callback in list(info._replay_listeners):
                try:
                    callback(float(seconds), str(payload))
                except Exception as exc:
                    info._replay_listeners.remove(callback)
                    self._report(info.name(), str(exc))

    def apply_theme(self) -> None:
        for info in self.loaded():
            for callback in list(info._theme_listeners):
                try:
                    callback()
                except Exception as exc:
                    self._report(info.name(), str(exc))

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
