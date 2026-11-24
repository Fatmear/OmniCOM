"""R-146/147/148 regression and optional real chart throughput benchmark."""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import math
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["OMNICOM_DATA_DIR"] = str(ROOT / ".artifacts" / "test-data" / "plugins")
os.environ["OMNICOM_SETTINGS_SUFFIX"] = "-plugin-selftest"

from omnicom.plugin_store import (  # noqa: E402
    Cancelled,
    PluginStore,
    StoreError,
    atomic_json,
    select_version,
    validate_catalog,
)
from omnicom.plugins import API_VERSION  # noqa: E402

#: Widget kinds the dashboard must be able to place (mirrors the plugin's
#: own palette order; the panel sorts nothing, so order matters).
KINDS = ("line", "gauge", "value", "bar", "lamp")

CHECKS = []


def check(name, condition, detail="") -> None:
    """Record one assertion.

    ``detail`` is optional so both call styles work: most checks fold the value
    into the name, but a few (R-182's zeroed-lamp case) pass it separately.  A
    call with three arguments used to raise ``TypeError`` and abort the whole
    suite before the remaining checks ran.
    """
    CHECKS.append({"name": name, "ok": bool(condition)})
    if not condition:
        raise AssertionError(f"{name} [{detail}]" if detail else name)
    print("PASS", name + (f" [{detail}]" if detail else ""))


def package(root, version="1.0.0", extra=None):
    path = root / ("demo-" + version + ".zip")
    manifest = {"id": "demo", "version": version, "api": 2, "entry": "plugin.py"}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("demo/plugin.json", json.dumps(manifest))
        archive.writestr("demo/plugin.py", "def create(api): return None\n")
        if extra:
            archive.writestr(*extra)
    return {**manifest, "url": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def update_core_checks() -> None:
    from omnicom.update_worker import apply_files

    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      source, target = root / "source", root / "target"
      target.mkdir()
      for identifier in ("terminal", "transfer"):
        shutil.copytree(ROOT / "plugins" / identifier,
                    source / "plugins" / identifier,
                    ignore=shutil.ignore_patterns("__pycache__"))
      optional = source / "plugins" / "launcher"
      optional.mkdir()
      (optional / "plugin.json").write_text("{}", encoding="utf-8")
      saved = target / "omnicom-data" / "plugin-data" / "terminal" / "settings.ini"
      saved.parent.mkdir(parents=True)
      saved.write_bytes(b"user settings")
      written = apply_files(source, target)
      for identifier in ("terminal", "transfer"):
        expected = source / "plugins" / identifier
        actual = target / "plugins" / identifier
        check(f"R-293 upgrade installs missing core plugin {identifier}",
            all((actual / path.relative_to(expected)).read_bytes() == path.read_bytes()
                for path in expected.rglob("*") if path.is_file()))
      check("R-174 upgrade does not install optional store plugins",
          not (target / "plugins" / "launcher").exists())
      check("R-108 core plugin upgrade preserves user settings",
          saved.read_bytes() == b"user settings" and bool(written))
      existing = target / "plugins" / "terminal"
      (existing / "plugin.py").write_bytes(b"custom plugin")
      (existing / "text.py").unlink()
      second = apply_files(source, target)
      check("R-293 existing core plugin directory is preserved as a whole",
          not second and (existing / "plugin.py").read_bytes() == b"custom plugin"
          and not (existing / "text.py").exists())


def uninstall_cycle_checks(app, mode="dual") -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

    from omnicom.plugins import PluginHost
    from omnicom.ui.plugin_store import PluginStoreDialog

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = PluginStore(root / "data", root / "bundle")
        store.root.mkdir(parents=True)
        store.bundled.mkdir(parents=True)
        directories = (store.root, store.bundled) if mode == "dual" else (
            store.root if mode == "user" else store.bundled,)
        for directory in directories:
            folder = directory / "demo"
            folder.mkdir(parents=True)
            atomic_json(folder / "plugin.json", {
                "id": "demo", "version": "1.0.0", "api": API_VERSION,
                "entry": "plugin.py", "name": "Demo",
            })
            (folder / "plugin.py").write_text(
                "from PySide6.QtWidgets import QWidget\n"
                "def create(api): return QWidget()\n", encoding="utf-8")
        saved = store.data / "plugin-data" / "demo" / "settings.ini"
        saved.parent.mkdir(parents=True)
        saved.write_bytes(b"preserve")

        class Window(QWidget):
            def __init__(self):
                super().__init__()
                self.plugins = PluginHost()
                self.plugins.search_paths = lambda: [str(store.root), str(store.bundled)]
                self.plugins.load()

            def suspend_plugins(self):
                self.plugins.unload()

            def reload_plugins(self):
                self.plugins.load()

        window = Window()
        dialog = PluginStoreDialog(window, store)
        dialog.show()

        def settle():
            deadline = time.perf_counter() + 10
            while dialog.busy() and time.perf_counter() < deadline:
                app.processEvents()
            app.processEvents()
            check("R-327 cycle task completes", not dialog.busy(), dialog.status.text())

        try:
            for cycle in range(6):
                app.processEvents()
                entry = dialog.rows["demo"]
                remove = next(button for button in entry["widget"].findChildren(QPushButton)
                              if button.text() == "卸载")
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                    QTest.mouseClick(remove, Qt.MouseButton.LeftButton)
                settle()
                entry = dialog.rows["demo"]
                labels = [button.text() for button in entry["widget"].findChildren(QPushButton)]
                check(f"R-327 cycle {cycle} removes every active copy",
                      not (store.root / "demo").exists()
                      and not (store.bundled / "demo").exists(), str(store.installed()))
                check(f"R-327 cycle {cycle} is removed not disabled or installed",
                      entry["item"]["removed"] and not entry["item"]["installed_version"]
                      and "demo" not in store.disabled() and labels == ["安装"], str(labels))
                check(f"R-327 cycle {cycle} host unloads plugin",
                      not window.plugins.loaded())
                before = store._sources()
                store.uninstall("demo")
                check(f"R-327 cycle {cycle} repeated uninstall retains recovery", store._sources() == before)
                dialog.store = PluginStore(store.data, store.bundled)
                dialog.rebuild()
                QTest.mouseClick(dialog.rows["demo"]["install"], Qt.MouseButton.LeftButton)
                settle()
                check(f"R-327 cycle {cycle} reinstall restores host and clears records",
                      "demo" in store.installed() and not store.removed()
                      and len(window.plugins.loaded()) == 1)
                check(f"R-327 cycle {cycle} retains user data", saved.read_bytes() == b"preserve")
        finally:
            window.plugins.unload()
            dialog.close()
            window.close()
            app.processEvents()


def uninstall_rollback_checks() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = PluginStore(root / "data", root / "bundle")
        for directory in (store.root, store.bundled):
            atomic_json(directory / "demo" / "plugin.json",
                        {"id": "demo", "version": "1.0.0"})
        store.set_disabled("demo", True)
        original_move = shutil.move
        calls = []

        def fail_second(source, destination):
            calls.append(source)
            if len(calls) == 2:
                raise OSError("injected second-copy failure")
            return original_move(source, destination)

        with patch("omnicom.plugin_store.shutil.move", side_effect=fail_second):
            try:
                store.uninstall("demo")
            except OSError:
                pass
            else:
                raise AssertionError("R-327 injected uninstall failure was ignored")
        check("R-327 failed dual-copy uninstall restores both folders and state",
              all((directory / "demo" / "plugin.json").exists()
                  for directory in (store.root, store.bundled))
              and store.disabled() == {"demo"} and not store.removed())
        import errno

        with patch("shutil.os.rename", side_effect=OSError(errno.EXDEV, "cross-drive")):
            store.uninstall("demo")
        check("R-327 uninstall handles cross-drive rename fallback",
              not store.installed() and store.removed() == {"demo"}
              and not store.disabled())
        previous_sources = store._sources()
        downloaded = package(root)
        downloaded["url"] = (root / downloaded["url"]).as_uri()
        fail_once = [True]

        def fail_record(path, value):
            if path == store.sources_path and fail_once[0]:
                fail_once[0] = False
                raise OSError("injected record failure")
            atomic_json(path, value)

        with patch("omnicom.plugin_store.atomic_json", side_effect=fail_record):
            try:
                store.install(downloaded)
            except OSError:
                pass
            else:
                raise AssertionError("R-327 injected install failure was ignored")
        check("R-327 failed reinstall preserves removed state and recovery",
              not store.installed() and store._sources() == previous_sources
              and not store.disabled())
        store.install(downloaded)
        check("R-327 downloaded reinstall clears removal and disable records",
              "demo" in store.installed() and not store.removed() and not store.disabled())


def chart_capacity_checks() -> None:
    from plugins.chart.plugin import DashboardPanel

    class Panel:
        _number = staticmethod(lambda key, default: {"points": 100, "visible": 5000}.get(key, default))
        _visible_points = DashboardPanel._visible_points

    panel = Panel()
    check("R-301 explicit points stay the retention cap regardless of drawn setting",
          DashboardPanel._capacity(panel) == 100, DashboardPanel._capacity(panel))
    check("R-164 drawn setting cannot raise retention above the explicit points",
          DashboardPanel._capacity(panel) <= panel._visible_points())


def source_dialog_checks(app) -> None:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QPalette, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    from omnicom.ui import theme
    from plugins.chart.plugin import SourcesDialog

    class Api:
        @staticmethod
        def t(zh, en):
            return zh

        @staticmethod
        def theme():
            return theme

    dialog = SourcesDialog(Api(), ["AA", "BB", "CC"] +
                           [f"channel_{index}" for index in range(40)], ["AA"])
    palette = dialog.channels.palette()
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
    dialog.channels.setPalette(palette)
    dialog.show()
    app.processEvents()
    try:
        for index, name in enumerate(("AA", "BB", "CC")):
            item = dialog.channels.item(index)
            rect = dialog.channels.visualItemRect(item)
            check("R-325 source row has readable height", rect.height() >= 32, str(rect.height()))
            before = name in dialog.sources()
            QTest.mouseClick(dialog.channels.viewport(), Qt.MouseButton.LeftButton,
                             pos=QPoint(rect.left() + 60, rect.center().y()))
            check("R-325 clicking row text toggles source", (name in dialog.sources()) != before)
            QTest.mouseClick(dialog.channels.viewport(), Qt.MouseButton.LeftButton,
                             pos=QPoint(rect.left() + 16, rect.center().y()))
            check("R-325 clicking checkbox toggles exactly once", (name in dialog.sources()) == before)
        dialog.channels.setCurrentRow(1)
        QTest.keyClick(dialog.channels, Qt.Key.Key_Space)
        check("R-325 keyboard space selects source", "BB" in dialog.sources())
        QTest.keyClick(dialog.channels, Qt.Key.Key_Space)
        check("R-325 keyboard space deselects source", "BB" not in dialog.sources())
        dialog.names.setText("BB, FUTURE")
        rect = dialog.channels.visualItemRect(dialog.channels.item(1))
        for repeat in range(6):
            QTest.mouseClick(dialog.channels.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
            check("R-325 repeated selection does not bounce",
                  ("BB" in dialog.sources()) == bool(repeat % 2)
                  and "FUTURE" in dialog.sources())
        dialog.names.clear()
        check("R-325 empty sources cannot confirm", not dialog.confirm.isEnabled())
        dialog.names.setText("AA, CC")
        image = dialog.channels.viewport().grab().toImage()
        rect = dialog.channels.visualItemRect(dialog.channels.item(1))
        ink = sum(image.pixelColor(x, y).lightness() < 100
              for x in range(rect.left() + 44, rect.left() + 90)
              for y in range(rect.top(), rect.bottom()))
        check("R-325 selected row text remains dark on its light background", ink > 5)
        before = dialog.sources()
        cursor = QPoint(80, 80)
        viewport = dialog.channels.viewport()
        QTest.mouseMove(viewport, cursor)
        for delta in (-120, -120, 120):
            wheel = QWheelEvent(QPointF(cursor), QPointF(viewport.mapToGlobal(cursor)),
                                QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            app.sendEvent(viewport, wheel)
            app.processEvents()
            item = dialog.channels.itemAt(cursor)
            dialog.channels.setCurrentItem(item)
            image = viewport.grab().toImage()
            rect = dialog.channels.visualItemRect(item)
            check("R-325 hovered row stays light after scrolling with black OS highlight",
                  image.pixelColor(rect.right() - 15, rect.center().y()).lightness() > 170)
        check("R-325 scrolling does not change checked sources", dialog.sources() == before)
        # R-341: the row background is painted by hand from theme tokens, and
        # ``theme.WASH_HOVER`` / ``theme.LINE_STRONG`` are Qt *stylesheet*
        # ``rgba(r, g, b, a)`` strings.  ``QColor`` cannot parse that form: it
        # returns an invalid colour and Qt paints opaque black, so a hovered row
        # used to turn black.  Sample the composited dialog (the list itself is
        # transparent) and require a light wash, not just "not the OS highlight".
        dialog.channels.clearSelection()
        dialog.channels.setCurrentItem(None)
        app.processEvents()
        hover_rect = dialog.channels.visualItemRect(dialog.channels.item(2))
        QTest.mouseMove(viewport, QPoint(hover_rect.right() - 20, hover_rect.center().y()))
        app.processEvents()
        composed = dialog.grab().toImage()
        origin = viewport.mapTo(dialog, QPoint(0, 0))
        hovered = composed.pixelColor(origin.x() + hover_rect.right() - 20,
                                      origin.y() + hover_rect.center().y())
        check("R-341 hovered source row is a light wash, never opaque black",
              hovered.lightness() > 200, f"{hovered.name()} lightness={hovered.lightness()}")
        border = composed.pixelColor(origin.x() + hover_rect.left() + 8,
                                     origin.y() + hover_rect.center().y())
        check("R-341 unchecked source checkbox border is light, not black",
              border.lightness() > 150, f"{border.name()} lightness={border.lightness()}")
        dialog.grab().save(str(ROOT / ".artifacts" / "sources-dialog-fixed.png"))
        dialog.reject()
        check("R-325 cancel rejects source edits", dialog.result() == QDialog.DialogCode.Rejected)
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def store_checks() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bundle = root / "bundled"
        bundle.mkdir()
        first = package(root)
        latest = package(root, "1.1.0")
        catalog = {"schema": 1, "plugins": [first, {"id": "extra", "version": "1.0.0", "api": 2}]}
        atomic_json(root / "catalog.json", catalog)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/"
        store = PluginStore(root / "data", bundle, base + "catalog.json")
        try:
            entries = store.check()
            check("R-148 HTTP catalog includes uninstalled plugins", len(store.rows(entries)) == 2)
            store.install(entries[0])
            check("R-148 HTTP download and install", store.installed()["demo"]["version"] == "1.0.0")
            saved = store.data / "plugin-data" / "demo" / "settings.ini"
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"user-data")
            latest["url"] = base + latest["url"]
            row = next(item for item in store.rows([latest]) if item["id"] == "demo")
            check("R-148 installed/latest and update availability", row["update_available"] and row["installed_version"] == "1.0.0" and row["latest_version"] == "1.1.0")
            store.install(latest)
            check("R-148 update retains user settings", store.installed()["demo"]["version"] == "1.1.0" and saved.read_bytes() == b"user-data")

            def reject(item, expected):
                try:
                    store.install(item)
                except StoreError as exc:
                    check("R-148 rejects " + expected, exc.code == expected)
                else:
                    raise AssertionError("unsafe installation accepted: " + expected)
                check("R-148 previous version survives " + expected, store.installed()["demo"]["version"] == "1.1.0")

            reject(dict(latest, sha256="0" * 64), "hash")
            reject(dict(latest, api=999), "api")
            reject(dict(latest, version="9.0.0"), "package")
            traversal = package(root, "2.0.0", ("demo/../../escaped", "bad"))
            traversal["url"] = base + traversal["url"]
            reject(traversal, "path")
            link = zipfile.ZipInfo("demo/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            symbolic = package(root, "3.0.0", (link, "../../escaped"))
            symbolic["url"] = base + symbolic["url"]
            reject(symbolic, "path")
            check("R-148 extraction never escapes staging", not (root / "escaped").exists())
            cancelled = threading.Event()
            cancelled.set()
            try:
                store.install(latest, cancelled)
            except Cancelled:
                check("R-148 cancellation retains installed plugin", store.installed()["demo"]["version"] == "1.1.0")
            else:
                raise AssertionError("cancel ignored")
            real_replace = os.replace

            def fail_disabled(source, destination):
                if Path(destination) == store.disabled_path:
                    raise OSError("simulated metadata commit failure")
                return real_replace(source, destination)

            try:
                with patch("omnicom.plugin_store.os.replace", side_effect=fail_disabled):
                    store.install(entries[0])
            except OSError:
                check("R-148 commit failure restores prior package", store.installed()["demo"]["version"] == "1.1.0")
            else:
                raise AssertionError("commit failure fixture did not execute")
            store.uninstall("demo")
            check("R-148 uninstall preserves plugin data", "demo" not in store.installed() and saved.read_bytes() == b"user-data")
            store.install(entries[0])
            check("R-148 retry reinstalls plugin", "demo" in store.installed())
            check("R-148 offline cache retains catalog", len(store.cached_catalog()) == 2)

            # ------------------------------------------------ R-323 disabled
            # A bundled plugin cannot be deleted from the program folder, so
            # "uninstall" only records a flag.  That flag used to also drop the
            # entry out of ``installed()``, and the store's rows are
            # ``installed | remote catalog`` - while ``terminal`` / ``transfer``
            # are deliberately absent from the remote catalog (R-174).  The
            # user therefore saw them disappear from the store with no way to
            # switch them back on.
            core_bundle = root / "core-bundle"
            core_bundle.mkdir()
            for name in ("terminal", "transfer"):
                folder = core_bundle / name
                folder.mkdir()
                atomic_json(folder / "plugin.json",
                            {"id": name, "name": name, "version": "1.0.0",
                             "api": API_VERSION, "entry": "plugin.py"})
                (folder / "plugin.py").write_text("def create(api):\n    return None\n",
                                                  encoding="utf-8")
            core = PluginStore(root / "data", core_bundle, base + "catalog.json")
            visible = {row["id"] for row in core.rows(core.cached_catalog())}
            check("R-323 bundled core plugins appear in the store",
                  {"terminal", "transfer"} <= visible, sorted(visible))
            core.set_disabled("terminal", True)
            rows = {row["id"]: row for row in core.rows(core.cached_catalog())}
            check("R-323 disabling keeps the plugin visible and tagged",
                  "terminal" in rows and rows["terminal"]["disabled"] is True,
                  sorted(rows))
            check("R-323 a disabled plugin still reports its installed version",
                  rows["terminal"]["installed_version"] == "1.0.0",
                  rows["terminal"]["installed_version"])
            core.set_disabled("terminal", False)
            rows = {row["id"]: row for row in core.rows(core.cached_catalog())}
            check("R-323 enabling clears the flag without reinstalling",
                  rows["terminal"]["disabled"] is False and core.disabled() == set(),
                  (rows["terminal"]["disabled"], core.disabled()))

            # ------------------------------------------------- R-178 versions
            older = package(root, "1.0.5")
            older["url"] = base + older["url"]
            # ``latest["url"]`` was absolutised earlier in this test, so use it
            # as-is: prefixing again produced a 404 that looked like a bug here.
            history = dict(latest, url=latest["url"],
                           versions=[{"version": "1.1.0", "api": 2, "url": latest["url"],
                                      "sha256": latest["sha256"]},
                                     {"version": "1.0.5", "api": 2, "url": older["url"],
                                      "sha256": older["sha256"]}])
            parsed = validate_catalog({"schema": 1, "plugins": [dict(history)]}, base)[0]
            check("R-178 multi-version catalog parses newest first",
                  [record["version"] for record in parsed["versions"]] == ["1.1.0", "1.0.5"])
            flat = validate_catalog({"schema": 1, "plugins": [{"id": "solo", "version": "2.0.0",
                                                             "api": 1}]}, base)[0]
            check("R-178 a flat entry still yields one version",
                  [record["version"] for record in flat["versions"]] == ["2.0.0"])
            for label, broken in (
                ("a head that is not the newest version",
                 dict(history, version="1.0.5")),
                ("an older record without a package url",
                 dict(history, versions=[{"version": "1.1.0", "api": 2, "sha256": latest["sha256"]},
                                         {"version": "1.0.5", "api": 2, "sha256": older["sha256"]}])),
                ("duplicate version records",
                 dict(history, versions=[{"version": "1.1.0", "api": 2, "url": base + latest["url"],
                                          "sha256": latest["sha256"]}] * 2)),
            ):
                try:
                    validate_catalog({"schema": 1, "plugins": [broken]}, base)
                except StoreError as exc:
                    check("R-178 rejects " + label, exc.code == "catalog")
                else:
                    raise AssertionError("inconsistent history accepted: " + label)
            chosen = select_version(parsed, "1.0.5")
            check("R-178 select_version picks the requested build",
                  chosen["version"] == "1.0.5" and chosen["sha256"] == older["sha256"])
            try:
                select_version(parsed, "0.0.1")
            except StoreError as exc:
                check("R-178 select_version refuses an unknown version", exc.code == "package")
            else:
                raise AssertionError("unknown version accepted")
            try:
                select_version(
                    {"id": "demo", "version": "1.1.0",
                     "versions": [{"version": "1.1.0", "api": 2},
                                  {"version": "0.9.0", "api": 2, "local": True}]}, "0.9.0")
            except StoreError as exc:
                check("R-178 a local-only build offers nothing to download", exc.code == "package")
            else:
                raise AssertionError("local build accepted for install")
            rows = store.rows([parsed])
            check("R-178 the picker history carries every catalog version",
                  [record["version"] for record in rows[0]["versions"]][:2] == ["1.1.0", "1.0.5"]
                  and rows[0]["versions"][-1].get("local"))
            store.install(select_version(parsed, "1.1.0"))
            rows = store.rows([parsed])
            check("R-178 an installed newest build still offers an older one",
                  rows[0]["installed_version"] == "1.1.0" and rows[0]["installed_downgradable"])
            store.install(select_version(parsed, "1.0.5"))
            check("R-178 downgrade install reaches the older build "
                  f"({store.installed()['demo']['version']})",
                  store.installed()["demo"]["version"] == "1.0.5")
            check("R-178 downgrade keeps user data", saved.read_bytes() == b"user-data")
            upgraded = store.rows(validate_catalog({"schema": 1, "plugins": [
                dict(history)]}, base))
            check("R-178 after a downgrade an update is offered again",
                  upgraded[0]["installed_version"] == "1.0.5"
                  and upgraded[0]["update_available"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def chart_checks(app, kinds):
    """R-147 protocol and R-161 dashboard behaviour, every number printed."""
    from omnicom import webengine
    from omnicom.plugins import PluginHost

    check(f"R-161 QtWebEngine package present (available={webengine.available()})",
          webengine.available())

    sent = []
    notes = []
    # Seed the *stored* pause state before the plugin is loaded, so the
    # "fresh dashboard is already running" check below is a real regression
    # test: the build that broke R-176 read ``running=0`` here and opened on a
    # frozen picture with a grey 继续 button (L-240).
    from omnicom import paths
    seeded = os.path.join(paths.data_dir(), "plugin-data", "chart", "settings.ini")
    os.makedirs(os.path.dirname(seeded), exist_ok=True)
    with open(seeded, "w", encoding="utf-8") as handle:
        handle.write("[General]\nrunning=0\n")
    host = PluginHost(notify=lambda text, level="info": notes.append((text, level)),
                      log_line=lambda text: None,
                      send_bytes=lambda data: sent.append(bytes(data)) or True)
    host.load()
    info = next(item for item in host.loaded() if item.id == "chart")
    chart = info.widget
    chart_mod = info.module           # the plugin module: constants + parser
    chart.resize(1050, 650)
    chart.show()
    parser = info.module.AsciiParser()
    check("R-147 split and coalesced frames",
          parser.feed(b"[OmniCOM-cha") == []
          and parser.feed(b"rt] AA=11 BB=1.1\r\n[OmniCOM-chart] CC=-3e-4\n")
          == [{"AA": 11, "BB": 1.1}, {"CC": -0.0003}])
    # The device prints the payload through its own log macro, so the real line
    # is decorated in front of the marker.  Requiring the marker at column 0
    # discarded every frame from real firmware (L-192).
    decorated = (b"csi_demo:I(266466):[OmniCOM-chart] T1=2.07 NEEDE=10.00\r\n"
                 b"csi_demo:I(266466):[CSI-DBG] result=NOBODY src=t1 raw=1778\r\n")
    check("R-147 frame parses behind a firmware prefix",
          parser.feed(decorated) == [{"T1": 2.07, "NEEDE": 10.0}])
    parser.feed(b"[OmniCOM-chart] AA=nan\n[OmniCOM-chart] AA=1 AA=2\n"
                b"[OmniCOM-chart] AA=1e999\n")
    check("R-147 invalid and nonfinite frames counted", parser.invalid == 3)
    parser.feed(b"[OmniCOM-chart] " + b"A" * 70000)
    recovery = parser.feed(b"discarded\n[OmniCOM-chart] NEW=4\n")
    check("R-147 overlong input bounded and recovers",
          parser.oversized == 1 and not parser.pending and recovery == [{"NEW": 4}])

    # ---------------------------------------------------------------- channels
    host.publish_rx(1000, b"[OmniCOM-chart] AA=11 BB=1.1\n")
    host.publish_rx(1100, b"[OmniCOM-chart] AA=12 CC=3\n")
    chart.refresh()
    app.processEvents()
    check(f"R-147 dynamic channel discovery reaches the palette "
          f"({sorted(chart.samples)})",
          sorted(chart.samples) == ["AA", "BB", "CC"]
          and chart.channel_combo.count() == 3)

    # --------------------------------------------------------------- the page
    deadline = time.perf_counter() + 30
    while not chart.page_ready and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)
    check(f"R-161 dashboard page loads inside QtWebEngine "
          f"(ready={chart.page_ready}, error={chart.surface_error})",
          chart.page_ready)
    js_ready = _js(app, chart, "omniDashboard.ready()")
    check(f"R-161 bundled ECharts runtime is usable offline (ready={js_ready})",
          js_ready is True)

    ids = _add_all_kinds(chart, kinds)
    _wait(app, chart, len(kinds))
    described = _describe(app, chart)
    check(f"R-161 every widget kind is mounted "
          f"({[(w['kind'], w['id']) for w in described['widgets']]})",
          [w["id"] for w in described["widgets"]] == ids
          and {w["kind"] for w in described["widgets"]} == set(kinds))
    check(f"R-161 only line and gauge create ECharts instances "
          f"(charts={described['charts']})", described["charts"] == 2)
    zoom_config = _js(app, chart, "JSON.stringify(echarts.getInstanceByDom("
                      "document.querySelector('.card[data-kind=line] .plot'))"
                      ".getOption().dataZoom || [])")
    zooms = json.loads(zoom_config)
    check("R-170 curve exposes independent horizontal and vertical zoom controls",
          len(zooms) == 2 and any(item.get("xAxisIndex") == [0] for item in zooms)
          and any(item.get("yAxisIndex") == [0] for item in zooms))
    # Pick a range on each axis, then let a data frame repaint the card: the
    # option is rewritten ~30 times a second, and an option that carries
    # ``start``/``end`` resets them on every frame unless the range is owned by
    # the page (R-170).
    applied = _js(app, chart, """(function () {
      var host = document.querySelector('.card[data-kind=line] .plot');
      var instance = echarts.getInstanceByDom(host);
      if (!instance) { return 'no-instance'; }
      instance.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0,
                                start: 20, end: 60 });
      instance.dispatchAction({ type: 'dataZoom', dataZoomIndex: 1,
                                start: 30, end: 70 });
      return 'ok';
    })()""")
    _js(app, chart, "omniDashboard.setValues({values:{}, history:{}})")
    kept = json.loads(_js(app, chart, "JSON.stringify(echarts.getInstanceByDom("
                          "document.querySelector('.card[data-kind=line] .plot'))"
                          ".getOption().dataZoom.map("
                          "z => [z.xAxisIndex ? 'x' : 'y', z.start, z.end]))") or "[]")
    ranges = {item[0]: (item[1], item[2]) for item in kept}
    check(f"R-170 each axis keeps the range the user chose across a refresh "
          f"({applied}, {kept})",
          applied == "ok" and len(ranges) == 2
          and abs(ranges.get("x", (0, 100))[0] - 20) < 1.5
          and abs(ranges.get("x", (0, 100))[1] - 60) < 1.5
          and abs(ranges.get("y", (0, 100))[0] - 30) < 1.5
          and abs(ranges.get("y", (0, 100))[1] - 70) < 1.5)
    # Pressing on a zoom strip must zoom, not start a card drag: the sliders sit
    # inside the plot, which is part of the drag handle since R-169.  The press
    # is dispatched on the renderer canvas, which is what a real mouse hits.
    strip = _js(app, chart, """(function () {
      var card = document.querySelector('.card[data-kind=line]');
      var plot = card.querySelector('.plot');
      var target = plot.querySelector('canvas') || plot;
      var box = plot.getBoundingClientRect();
      var init = { bubbles: true, cancelable: true, button: 0, pointerId: 7,
                   clientX: box.left + 24, clientY: box.top + box.height - 4,
                   pointerType: 'mouse', isPrimary: true };
      target.dispatchEvent(new PointerEvent('pointerdown', init));
      var dragging = card.classList.contains('dragging');
      target.dispatchEvent(new PointerEvent('pointercancel', init));
      card.classList.remove('dragging');
      return JSON.stringify({ dragging: dragging,
                              onCanvas: target.tagName.toLowerCase(),
                              height: Math.round(box.height) });
    })()""")
    pressed = json.loads(strip or "{}")
    check(f"R-170 a press on an axis slider does not drag the card ({strip})",
          pressed.get("dragging") is False and pressed.get("onCanvas") == "canvas")
    # The sliders are positioned relative to the card body, not to the grid, so
    # the grid has to be inset by hand - otherwise the x slider sits on top of
    # the axis labels.  Measure the plot rect instead of trusting the numbers.
    layout = json.loads(_js(app, chart, """(function () {
      var host = document.querySelector('.card[data-kind=line] .plot');
      var instance = echarts.getInstanceByDom(host);
      var rect = instance.getModel().getComponent('grid').coordinateSystem.getRect();
      return JSON.stringify({ plotBottom: Math.round(rect.y + rect.height),
                              plotRight: Math.round(rect.x + rect.width),
                              height: host.clientHeight, width: host.clientWidth });
    })()""") or "{}")
    check(f"R-170 the axis sliders do not cover the plot itself ({layout})",
          layout.get("plotBottom", 1e9) <= layout.get("height", 0) - 14
          and layout.get("plotRight", 1e9) <= layout.get("width", 0) - 12)
    # Ctrl+wheel must not scale the whole dashboard any more (R-171).  Chromium
    # owns that gesture below the page, so the proof is the Qt-side guard: the
    # counter grows *and* the view keeps zoom factor 1.0.
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    before_blocked = chart.wheel_zoom_blocked
    target = chart.view.focusProxy() or chart.view
    centre = QPointF(target.width() / 2.0, target.height() / 2.0)
    for _ in range(2):
        app.sendEvent(target, QWheelEvent(
            centre, target.mapToGlobal(QPoint(int(centre.x()), int(centre.y()))),
            QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.ControlModifier,
            Qt.NoScrollPhase, False))
    app.processEvents()
    zoom_factor = round(float(chart.view.zoomFactor()), 4)
    check(f"R-171 Ctrl+wheel no longer zooms the whole page "
          f"(blocked={chart.wheel_zoom_blocked - before_blocked}, "
          f"factor={zoom_factor})",
          chart.wheel_zoom_blocked - before_blocked == 2 and zoom_factor == 1.0)
    # A plain wheel must still scroll the dashboard: the guard watches the
    # modifier, not the widget, so an unmodified notch has to pass through.
    steady = chart.wheel_zoom_blocked
    app.sendEvent(target, QWheelEvent(
        centre, target.mapToGlobal(QPoint(int(centre.x()), int(centre.y()))),
        QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
        Qt.NoScrollPhase, False))
    app.processEvents()
    check(f"R-171 a plain wheel still scrolls (blocked={chart.wheel_zoom_blocked})",
          chart.wheel_zoom_blocked == steady)

    # Real pixels, not a flag: the two chart kinds must have drawn something.
    for widget_id, kind in zip(ids, kinds, strict=True):
        if kind not in ("line", "gauge"):
            continue
        pixels = _painted(app, chart, widget_id, floor=100)
        check(f"R-161 {kind} widget paints real pixels (painted={pixels})",
              pixels > 100)

    # ------------------------------------------------------- drag persistence
    before = {w["id"]: [w["x"], w["y"], w["w"], w["h"]] for w in described["widgets"]}
    # R-173: the 16 px grid belongs to the drawing surface, so it is painted
    # from the moment the page mounts.  R-313 moved the grid one level in:
    # ``#canvas`` is now the scroll viewport and ``#surface`` the content box
    # that grows with the cards, so the grid lives on the surface (a grid on the
    # scroller would stay glued to the viewport while the cards move).
    grid = json.loads(_js(app, chart, """(function () {
      var cs = getComputedStyle(document.getElementById('surface'));
      return JSON.stringify({ image: cs.backgroundImage,
                              size: cs.backgroundSize });
    })()""") or "{}")
    # ``background-size`` reports one value per painted layer, and the grid is
    # drawn by two gradients (vertical + horizontal), so the computed value is
    # "16px 16px, 16px 16px" - comparing against a single "16px 16px" asserted
    # the *layer count*, not the step (L-235).
    grid_size = [part.strip() for part in str(grid.get("size")).split(",")]
    check(f"R-173 the snap grid is always painted (size={grid.get('size')})",
          bool(grid_size) and all(part == "16px 16px" for part in grid_size)
          and "linear-gradient" in str(grid.get("image")))
    check("R-173 there is no edit mode left to toggle",
          _js(app, chart, "typeof omniDashboard.setEditing") == "undefined")
    first = ids[0]
    _js(app, chart, "omniDashboard.updateWidget("
                    + json.dumps({"id": first, "x": 128, "y": 96,
                                  "w": 512, "h": 256}) + ")")
    moved = None
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        app.processEvents()
        moved = {w["id"]: [w["x"], w["y"], w["w"], w["h"]]
                 for w in _describe(app, chart)["widgets"]}
        if moved.get(first) == [128, 96, 512, 256]:
            break
        time.sleep(0.02)
    check(f"R-161 geometry change is reported back "
          f"({before[first]} -> {moved.get(first)})",
          moved is not None and moved.get(first) == [128, 96, 512, 256])
    persisted = json.loads(str(chart.api.settings.value("layout", "{}")))
    check(f"R-161 layout persists to the plugin INI "
          f"(widgets={len(persisted.get('widgets') or [])})",
          len(persisted.get("widgets") or []) == len(kinds))

    # ------------------------------------------ R-169 a real drag always works
    # ``updateWidget`` above only proves the *API* moves a card.  The user's
    # complaint was that a press-and-drag on the card did nothing, so drive the
    # actual Qt mouse sequence through the real widget and require the geometry
    # to change - there is no edit mode at all any more (R-173), so dragging
    # must never be gated on a switch.
    # The probe installs its own one-card layout, so the real one is parked here
    # and pushed back afterwards: ``persist()`` would otherwise overwrite the
    # five widgets the following checks still need.
    parked_layout = chart.layout_data
    area = chart.view.focusProxy() or chart.view
    probe = {"id": "dragprobe", "kind": "value", "channel": "AA", "title": "drag",
             "x": 16, "y": 16, "w": 208, "h": 128}
    _js(app, chart, "omniDashboard.setLayout(" + json.dumps({"widgets": [probe]}) + ")")
    _wait(app, chart, 1)
    start_geometry = _geometry_of(app, chart, "dragprobe")

    def _mouse(kind, x, y):
        """Send one real Qt mouse event to the page surface.

        ``button`` and ``buttons`` are *not* the same thing: a release carries
        ``button == LeftButton`` with ``buttons == NoButton``.  Passing
        ``NoButton`` for both makes Chromium drop the ``pointerup`` entirely, so
        the card visibly moves but the drag never ends, ``persist()`` is never
        called and nothing is stored - which looks exactly like "the drag does
        nothing" (L-235).
        """
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        point = QPointF(float(x), float(y))
        action, button, buttons = {
            "press": (QEvent.MouseButtonPress, Qt.LeftButton, Qt.LeftButton),
            "move": (QEvent.MouseMove, Qt.NoButton, Qt.LeftButton),
            "release": (QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton),
        }[kind]
        app.sendEvent(area, QMouseEvent(action, point, point, point,
                                        button, buttons, Qt.NoModifier))
        app.processEvents()

    _mouse("press", 90, 100)            # the card *body*, not the header
    for step in range(1, 7):
        _mouse("move", 90 + step * 16, 100 + step * 16)
        time.sleep(0.02)
    _mouse("release", 186, 196)
    app.processEvents()
    deadline = time.perf_counter() + 10
    dragged = start_geometry
    while time.perf_counter() < deadline:
        app.processEvents()
        dragged = _geometry_of(app, chart, "dragprobe")
        if dragged and start_geometry and dragged[:2] != start_geometry[:2]:
            break
        time.sleep(0.02)
    check(f"R-169 a mouse drag moves the card with no edit mode at all "
          f"({start_geometry} -> {dragged})",
          bool(dragged) and start_geometry is not None
          and dragged[:2] != start_geometry[:2])
    check(f"R-169 the drag keeps the card's size "
          f"({start_geometry} -> {dragged})",
          bool(dragged) and start_geometry is not None
          and dragged[2:] == start_geometry[2:])
    # ``persist()`` goes through QWebChannel, which is asynchronous: poll the INI
    # instead of reading it on the next line.
    deadline = time.perf_counter() + 10
    stored = []
    while time.perf_counter() < deadline:
        app.processEvents()
        saved = json.loads(str(chart.api.settings.value("layout", "{}")))
        stored = [(w.get("id"), w.get("x"), w.get("y")) for w in saved.get("widgets") or []]
        if stored and stored[0][0] == "dragprobe" and stored[0][1] != 16:
            break
        time.sleep(0.02)
    check(f"R-169 the drag is reported back through the bridge and stored "
          f"({stored})",
          bool(stored) and stored[0][0] == "dragprobe" and stored[0][1] != 16)
    cursor = _js(app, chart, "getComputedStyle("
                 "document.querySelector('.card')).cursor")
    check(f"R-169 the card advertises that it can be dragged (cursor={cursor})",
          cursor == "move")

    # ---------------------------------------- R-337 rubber-band selection
    # Windows-desktop behaviour: drag on the blank canvas to select the cards
    # the band touches, then drag any selected card to move them as a group.
    # Driven through real DOM pointer events, not the API.
    band_layout = {"widgets": [
        {"id": "sel-a", "kind": "value", "channel": "AA", "title": "a",
         "x": 32, "y": 32, "w": 208, "h": 128},
        {"id": "sel-b", "kind": "value", "channel": "AA", "title": "b",
         "x": 288, "y": 32, "w": 208, "h": 128},
        {"id": "sel-c", "kind": "value", "channel": "AA", "title": "c",
         "x": 32, "y": 220, "w": 208, "h": 128},
    ]}
    _js(app, chart, "omniDashboard.setLayout(" + json.dumps(band_layout) + ")")
    _wait(app, chart, 3)
    banded = json.loads(_js(app, chart, """(function () {
      var canvas = document.getElementById('canvas');
      canvas.scrollLeft = 0; canvas.scrollTop = 0;
      var surface = document.getElementById('surface');
      var box = surface.getBoundingClientRect();
      function fire(kind, x, y) {
        canvas.dispatchEvent(new PointerEvent(kind, {
          bubbles: true, cancelable: true, button: 0, pointerId: 11,
          clientX: box.left + x, clientY: box.top + y,
          pointerType: 'mouse', isPrimary: true, buttons: kind === 'pointerup' ? 0 : 1
        }));
      }
      /* Start on blank space to the right of the cards and sweep left over a
       * and b only: their rects span y 32..160 while c starts at y 220. */
      fire('pointerdown', 600, 180);
      fire('pointermove', 320, 120);
      fire('pointermove', 20, 20);
      var band = document.querySelector('.marquee');
      var during = !!band;
      fire('pointerup', 20, 20);
      return JSON.stringify({ during: during,
                              selection: JSON.parse(omniDashboard.describe()).selection });
    })()""") or "{}")
    check(f"R-337 空白处拖出选框可多选卡片 ({banded})",
          banded.get("during") is True
          and sorted(banded.get("selection") or []) == ["sel-a", "sel-b"],
          str(banded))

    # Dragging one selected card moves the whole selection by the same delta.
    before = {widget["id"]: (widget["x"], widget["y"])
              for widget in _describe(app, chart)["widgets"]}
    grouped = json.loads(_js(app, chart, """(function () {
      var card = document.querySelector('.card[data-id="sel-a"]');
      var box = card.getBoundingClientRect();
      function fire(kind, dx, dy) {
        card.dispatchEvent(new PointerEvent(kind, {
          bubbles: true, cancelable: true, button: 0, pointerId: 12,
          clientX: box.left + 40 + dx, clientY: box.top + 40 + dy,
          pointerType: 'mouse', isPrimary: true, buttons: kind === 'pointerup' ? 0 : 1
        }));
      }
      fire('pointerdown', 0, 0);
      fire('pointermove', 96, 64);
      fire('pointerup', 96, 64);
      var described = JSON.parse(omniDashboard.describe());
      var out = {};
      described.widgets.forEach(function (item) { out[item.id] = [item.x, item.y]; });
      return JSON.stringify({ moved: out, selection: described.selection });
    })()""") or "{}")
    moved = grouped.get("moved") or {}
    delta_a = (moved.get("sel-a", (0, 0))[0] - before["sel-a"][0],
               moved.get("sel-a", (0, 0))[1] - before["sel-a"][1])
    delta_b = (moved.get("sel-b", (0, 0))[0] - before["sel-b"][0],
               moved.get("sel-b", (0, 0))[1] - before["sel-b"][1])
    check(f"R-337 拖动已选卡片时整组同步移动 ({grouped})",
          len(grouped.get("selection") or []) == 2
          and delta_a == delta_b and delta_a != (0, 0)
          and moved.get("sel-c") == list(before["sel-c"]),
          f"{delta_a} {delta_b} {moved}")

    chart.layout_data = parked_layout
    chart._save_layout()
    _js(app, chart, "omniDashboard.setLayout("
        + json.dumps(parked_layout, ensure_ascii=False) + ")")
    _wait(app, chart, len(kinds))

    # ------------------------------------------------- R-165 × really deletes
    # The × sits inside the card header, and the header starts a drag on
    # pointerdown *and captures the pointer*, so a plain click listener is not
    # proof: drive the real DOM sequence (pointerdown -> pointerup -> click) on
    # the button and prove the widget disappears and the layout shrinks.
    # The victim is the ``value`` card, not ``ids[-1]``: the lamp (R-166) and
    # the bar (R-172) are measured further down and must survive this check.
    victim = ids[kinds.index("value")]
    before_count = len(_describe(app, chart)["widgets"])
    fired = _js(app, chart, """(function () {
      var card = document.querySelector('.card[data-id="' + __ID__ + '"]');
      if (!card) { return 'no-card'; }
      var button = card.querySelector('.del');
      if (!button) { return 'no-button'; }
      var box = button.getBoundingClientRect();
      var init = { bubbles: true, cancelable: true, button: 0, pointerId: 1,
                   clientX: box.left + box.width / 2,
                   clientY: box.top + box.height / 2,
                   pointerType: 'mouse', isPrimary: true };
      button.dispatchEvent(new PointerEvent('pointerdown', init));
      button.dispatchEvent(new PointerEvent('pointerup', init));
      button.dispatchEvent(new MouseEvent('click', init));
      return 'clicked';
    })()""".replace("__ID__", json.dumps(victim)))
    deadline = time.perf_counter() + 10
    after_ids = [w["id"] for w in _describe(app, chart)["widgets"]]
    while time.perf_counter() < deadline and victim in after_ids:
        app.processEvents()
        time.sleep(0.02)
        after_ids = [w["id"] for w in _describe(app, chart)["widgets"]]
    check(f"R-165 the × button removes its widget ({fired})",
          fired == "clicked" and victim not in after_ids
          and len(after_ids) == before_count - 1)
    # The page persists through QWebChannel, which is asynchronous: poll the INI
    # instead of reading it on the next line, or the check races the bridge.
    saved_ids = []
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        app.processEvents()
        saved = json.loads(str(chart.api.settings.value("layout", "{}")))
        saved_ids = [w.get("id") for w in (saved.get("widgets") or [])]
        if victim not in saved_ids:
            break
        time.sleep(0.02)
    check(f"R-165 the deletion is persisted (saved={len(saved_ids)})",
          victim not in saved_ids)

    # ------------------------------------------------- R-166 lamp geometry
    # ``runJavaScript`` only marshals primitives back reliably, so the page
    # returns a JSON string here; a bare object arrives as ``None``.
    lamp_id = next((widget["id"] for widget in chart.layout_data["widgets"]
                    if widget.get("kind") == "lamp"), None)
    measured = None
    if lamp_id:
        raw = _js(app, chart, """(function () {
          var card = document.querySelector('.card[data-id="' + __ID__ + '"]');
          var dot = card && card.querySelector('.lamp-dot');
          if (!dot) { return ''; }
          var cs = getComputedStyle(dot);
          return JSON.stringify({ w: parseFloat(cs.width),
                                  h: parseFloat(cs.height),
                                  radius: cs.borderRadius });
        })()""".replace("__ID__", json.dumps(lamp_id)))
        measured = json.loads(raw) if raw else None
    check(f"R-166 the lamp indicator is twice the old size ({measured})",
          bool(measured) and measured["w"] >= 44 and measured["h"] >= 44)
    check("R-166 the enlarged indicator stays round",
          bool(measured) and "50%" in str(measured["radius"]))

    # ------------------------------------------------- R-172 progress bar card
    # The bar's whole job is to turn a number into a proportion, so the proof
    # has to be the *measured* pixel width of the fill, not a flag: publish
    # 7.5 on a 0..10 range and require the fill to cover three quarters of the
    # track.  ``formatValue`` is exercised by the readout text in the same
    # snapshot, so a bar that draws correctly but prints the wrong number fails.
    bar_id = next((widget["id"] for widget in chart.layout_data["widgets"]
                   if widget.get("kind") == "bar"), None)

    def _bar():
        """Measured state of the progress-bar card, or ``None``."""
        if not bar_id:
            return None
        raw = _js(app, chart, """(function () {
          var card = document.querySelector('.card[data-id="' + __ID__ + '"]');
          var fill = card && card.querySelector('.bar-fill');
          var track = card && card.querySelector('.bar-track');
          var number = card && card.querySelector('.bar-number');
          if (!fill || !track) { return ''; }
          var cs = getComputedStyle(fill);
          var box = track.getBoundingClientRect();
          return JSON.stringify({
            track: box.width,
            fill: parseFloat(cs.width),
            inline: fill.style.width,
            over: fill.classList.contains('over'),
            text: number ? number.textContent : '',
            scale: (card.querySelector('.bar-scale') || {}).textContent || ''
          });
        })()""".replace("__ID__", json.dumps(bar_id)))
        return json.loads(raw) if raw else None

    def _publish_bar(value):
        """Push one frame and wait for the 120 ms width transition to settle.

        The value travels through ``chart.values`` and ``chart.flush()``, not a
        bare ``omniDashboard.setValues()`` call: the panel repaints from its own
        state every ``FRAME_MS``, so a direct page call would be overwritten by
        the very next timer tick and the measurement would race the repaint.
        """
        if value is None:
            chart.values.pop("AA", None)
        else:
            chart.values["AA"] = float(value)
        chart.flush()
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.02)
            state = _bar()
            if state and state["track"] > 0:
                expected = None if value is None else max(
                    0.0, min(1.0, (value - 0.0) / (10.0 - 0.0)))
                want = 0.0 if expected is None else expected * state["track"]
                if abs(state["fill"] - want) <= max(2.0, state["track"] * 0.015):
                    return state
        return _bar()

    chart.live.setChecked(True)
    mounted = _bar()
    check(f"R-172 the progress bar card builds a track and a fill ({mounted})",
          bool(mounted) and mounted["track"] > 0 and mounted["inline"] != "")
    check(f"R-172 an unbound channel shows an empty bar and '--' ({mounted})",
          bool(mounted) and mounted["text"] == "--"
          and abs(mounted["fill"]) < 1.0)
    half = _publish_bar(5.0)
    check(f"R-172 half of the range fills half of the track ({half})",
          bool(half) and half["track"] > 0
          and abs(half["fill"] / half["track"] - 0.5) <= 0.02)
    full = _publish_bar(7.5)
    check(f"R-172 the fill is proportional to the reading "
          f"(7.5 of 0..10 -> {full})",
          bool(full) and full["track"] > 0
          and abs(full["fill"] / full["track"] - 0.75) <= 0.02)
    check(f"R-172 the readout and the range captions are formatted "
          f"(text={full.get('text') if full else None}, "
          f"scale={full.get('scale') if full else None})",
          bool(full) and full["text"] == "7.50" and full["scale"].startswith("0.00"))
    under = _publish_bar(-4.0)
    check(f"R-172 a reading below the minimum clamps to an empty bar ({under})",
          bool(under) and abs(under["fill"]) < 1.0 and under["text"] == "-4.00")
    over = _publish_bar(13.0)
    check(f"R-172 a reading above the maximum clamps but is flagged red ({over})",
          bool(over) and over["track"] > 0
          and abs(over["fill"] / over["track"] - 1.0) <= 0.02
          and over["over"] is True and over["text"] == "13.00")
    check(f"R-172 the bar needs no ECharts instance ({_bar()})",
          _describe(app, chart)["charts"] == 2)
    chart.clear_data()
    chart.refresh()

    # ------------------------------------------------- R-167/R-176 run button
    # The dashboard refreshes from the moment it opens, so the accent 暂停 face
    # is what a fresh window shows; a check that asserted "idle means Ghost"
    # would now be asserting the bug (R-176, re-based here in the same edit).
    app.processEvents()
    default_text, default_object = chart.live.text(), chart.live.objectName()
    check(f"R-176 a fresh dashboard is already running "
          f"({default_text!r}/{default_object!r}/{chart.live.isChecked()})",
          chart.live.isChecked() and default_object == "Primary")
    check(f"R-176 the running face is 暂停 ({default_text!r})",
          default_text == "暂停")

    chart.live.setChecked(False)
    app.processEvents()
    idle_text, idle_object = chart.live.text(), chart.live.objectName()
    check(f"R-176 paused face is 继续 in the neutral style "
          f"({idle_text!r}/{idle_object!r})",
          idle_object == "Ghost" and idle_text == "继续")
    check(f"R-176 pausing and resuming swap the face ({default_text!r}/{idle_text!r})",
          idle_text != default_text)

    chart.live.setChecked(True)
    app.processEvents()
    check("R-176 resuming returns to the accent 暂停 face",
          chart.live.objectName() == "Primary" and chart.live.text() == "暂停")
    check("R-167 the two run button faces stay visually distinct",
          chart.live.text() != idle_text)

    # A stale ``running=0`` in settings.ini is exactly what made a fresh window
    # open frozen (R-176, revised): the old build restored it, so "default" was
    # whatever the last session left behind.  The seed above proved the load
    # path ignores it; saving must also drop the key (L-240).
    run_settings = chart.api.settings
    check(f"R-176 a stored running=0 is not restored (checked="
          f"{chart.live.isChecked()}, group={run_settings.group()!r})",
          chart.live.isChecked())
    run_settings.setValue("running", "0")
    chart._save_ui()
    run_settings.sync()
    check(f"R-176 saving drops the stale running key "
          f"(keys={run_settings.allKeys()})",
          "running" not in run_settings.allKeys())

    # --------------------------------------------------------------- run/stop
    chart.live.setChecked(False)
    count = chart.render_count
    host.publish_rx(1200, b"[OmniCOM-chart] AA=13\n")
    chart.refresh()
    check("R-147 stop freezes rendering only",
          chart.render_count == count and chart.samples["AA"][-1][1] == 13)
    chart.live.setChecked(True)
    chart.refresh()
    check("R-147 run resumes rendering retained samples",
          chart.render_count > count)

    # ------------------------------------------------------------------ limits
    chart.clear_data()
    chart.clear_widgets()
    app.processEvents()
    host.publish_rx(1300, b"[OmniCOM-chart] AA=1\n" * 5100)
    check(f"R-147 history cap is bounded and counted "
          f"(points={len(chart.samples['AA'])}, evicted={chart.evicted})",
          len(chart.samples["AA"]) == 5000 and chart.evicted > 0)

    # ------------------------------------------------- R-164 history depth
    # Verify both the retained history and the data reaching the page.
    check(f"R-164 history keeps at least 5000 frames "
          f"(MAX_POINTS={chart_mod.MAX_POINTS}, held={len(chart.samples['AA'])})",
          chart_mod.MAX_POINTS >= 5000
            and len(chart.samples["AA"]) == chart_mod.MAX_POINTS
            and chart_mod.DEFAULT_WINDOW_S == 0)
    oldest = chart.samples["AA"][0][1]
    newest = chart.samples["AA"][-1][1]
    host.publish_rx(1301, b"[OmniCOM-chart] AA=777\n")
    check(f"R-164 the newest sample is the one kept at the cap "
          f"(newest={chart.samples['AA'][-1][1]:.0f}, "
          f"oldest_in_ring={oldest:.0f}, before={newest:.0f})",
          chart.samples["AA"][-1][1] == 777 and newest == 1)
    # One line widget on screen: it must receive the full history, not a
    # down-sampled subset (that thinning is what lost the detail).
    chart.channel_combo.clear()
    chart.channel_combo.addItem("AA")
    chart.channel_combo.setCurrentText("AA")
    chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("line"))
    chart.title_edit.setText("history-line")
    chart.unit_edit.setText("V")
    chart.min_edit.setText("0")
    chart.max_edit.setText("10")
    line_id = chart.add_widget_from_palette()
    app.processEvents()
    history = chart.history_for_page().get("AA") or []
    check(f"R-164 a single visible curve receives the whole history (pushed={len(history)})",
          len(history) >= 5000)
    check(f"R-164 the pushed history ends on the newest frame "
          f"(last={history[-1] if history else None})",
          bool(history) and history[-1][1] == 777)
    chart.clear_data()
    chart.layout_data["widgets"] = [
        {"id": "history-multi", "kind": "line", "channels": ["AA", "BB"]}]
    chart.seconds.setValue(0)
    for index in range(5000):
        host.publish_rx(2000 + index * 1000,
                        f"[OmniCOM-chart] AA={index} BB={-index}\n".encode())
    history = chart.history_for_page()
    check("R-164 all history shows 5000 distinct timestamps per channel",
          all(len(history.get(name, [])) == 5000
              and history[name][0][0] == 0
              and history[name][-1][0] == 4999 for name in ("AA", "BB")))
    multi_layout = {"widgets": [{"id": "history-multi", "kind": "line",
                                 "channels": ["AA", "BB"]}]}
    _js(app, chart, "omniDashboard.setLayout(" + json.dumps(multi_layout) + ")")
    chart.layout_data = multi_layout
    # ``flush()`` is incremental now (R-301): it ships only what the page does
    # not have yet.  This check is about the *full* window, so it has to ask for
    # a resync explicitly - exactly what a layout/points change does in the app.
    chart.request_resync()
    started = time.perf_counter()
    chart.flush()
    flush_ms = (time.perf_counter() - started) * 1000
    rendered = _js(app, chart, "JSON.stringify(echarts.getInstanceByDom("
                   "document.querySelector('.plot')).getOption().series.map("
                   "s => [s.data.length, s.data[0], s.data[s.data.length-1]]))")
    series = json.loads(rendered)
    # R-164 keeps 5000 samples *retained* per channel; the page draws only the
    # 展示帧数 window (default 300) because 5000 points cost ~22 ms per card per
    # frame and made the dashboard stutter.  Both halves are asserted: nothing
    # was thrown away, and what ECharts holds is the newest slice.
    drawn = chart._visible_points()
    check(f"R-164 every curve draws the newest 展示帧数 window "
          f"(drawn={drawn}, flush={flush_ms:.1f} ms, series={series})",
          len(series) == 2 and all(item[0] == drawn for item in series)
          and all(item[2][0] == 4999 for item in series))
    check("R-164 the retained history behind that window is still 5000 points",
          all(len(chart.samples[name]) == 5000 for name in ("AA", "BB")))
    chart.seconds.setValue(10)
    history = chart.history_for_page()
    check("R-164 selected time window still filters history",
          all(len(history.get(name, [])) == 11 for name in ("AA", "BB")))
    settings = chart.api.settings
    settings.remove("history_window_migrated")
    settings.setValue("window", "10")
    check("R-164 legacy 10 second window migrates to all",
          chart._initial_window() == 0 and str(settings.value("window")) == "0")
    settings.setValue("window", "10")
    check("R-164 an explicitly selected 10 second window remains after migration",
          chart._initial_window() == 10)
    settings.remove("history_window_migrated")
    settings.setValue("window", "60")
    check("R-164 other existing time windows are preserved",
          chart._initial_window() == 60)
    if line_id:
        chart.layout_data["widgets"] = [widget for widget
                                        in chart.layout_data["widgets"]
                                        if widget.get("id") != line_id]
    chart.clear_data()
    chart.clear_widgets()
    app.processEvents()
    for index in range(130):
        host.publish_rx(1400, f"[OmniCOM-chart] CHANNEL_{index}=1\n".encode())
    check(f"R-147 channel cap is visible and counted "
          f"(channels={len(chart.samples)}, refused={chart.refused})",
          len(chart.samples) == 128 and chart.refused > 0)

    # ------------------------------------------------------------------ export
    chart.clear_data()
    for index in range(250):
        host.publish_rx(1000 + index * 10,
                        f"[OmniCOM-chart] AA={math.sin(index / 15):.4f} "
                        f"BB={math.cos(index / 20):.4f}\n".encode())
    chart.refresh()
    app.processEvents()
    host.apply_theme()
    with tempfile.TemporaryDirectory() as temporary:
        export = Path(temporary) / "chart.csv"
        chart.export_csv(str(export))
        lines = export.read_text(encoding="utf-8-sig").splitlines()
        check(f"R-147 CSV exports retained values (rows={len(lines)})",
              len(lines) == 501)
        shot = Path(temporary) / "dashboard.png"
        chart.save_image(str(shot))
        size = shot.stat().st_size if shot.exists() else 0
        check(f"R-161 dashboard image export ({size} bytes)", size > 1000)
    chart.resize(1050, 650)
    app.processEvents()
    chart.grab().save(str(ROOT / ".artifacts" / "chart.png"))
    chart_scope_checks(app, chart, kinds)
    return host, chart, info.module.AsciiParser


def _js(app, chart, code):
    """Run JavaScript in the dashboard page synchronously and return its value."""
    from PySide6.QtCore import QEventLoop, QTimer

    if chart.view is None or not chart.page_ready:
        return None
    result = {"value": None}
    loop = QEventLoop()

    def done(value):
        result["value"] = value
        loop.quit()

    chart.view.page().runJavaScript(code, 0, done)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(5000)
    loop.exec()
    guard.stop()
    app.processEvents()
    return result["value"]


def _describe(app, chart) -> dict:
    raw = _js(app, chart, "omniDashboard.describe()")
    if not raw:
        return {"selected": None, "widgets": [], "charts": 0}
    return json.loads(raw)


def _painted(app, chart, widget_id, floor=100, tries=60) -> int:
    """Poll ``paintedPixels`` until the widget drew something (or we give up).

    ``runJavaScript`` hands every JS number back as a Python ``float``, so the
    comparison must never assume ``int`` -- ``-1`` (no node) and ``-2``
    (``getImageData`` refused) are the only negative answers.
    """
    pixels = -1.0
    for _ in range(tries):
        app.processEvents()
        value = _js(app, chart, "omniDashboard.paintedPixels("
                    + json.dumps(widget_id) + ")")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            pixels = float(value)
            if pixels > floor:
                break
        time.sleep(0.05)
    return int(pixels)


def _wait(app, chart, count) -> None:
    deadline = time.perf_counter() + 15
    while time.perf_counter() < deadline:
        app.processEvents()
        if len(_describe(app, chart)["widgets"]) >= count:
            return
        time.sleep(0.02)


def _geometry_of(app, chart, widget_id):
    """``[x, y, w, h]`` of one card as the page currently has it, or ``None``."""
    for widget in _describe(app, chart)["widgets"]:
        if widget.get("id") == widget_id:
            return [int(widget.get("x") or 0), int(widget.get("y") or 0),
                    int(widget.get("w") or 0), int(widget.get("h") or 0)]
    return None


def _add_all_kinds(chart, kinds) -> list:
    """Add one widget of every kind through the panel's own palette path."""
    chart.clear_widgets()
    chart.clear_data()
    chart.channel_combo.clear()
    for name in ("AA", "BB"):
        chart.channel_combo.addItem(name)
    chart.channel_combo.setCurrentText("AA")
    ids = []
    for kind in kinds:
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData(kind))
        chart.title_edit.setText("widget-" + kind)
        chart.unit_edit.setText("V")
        chart.min_edit.setText("0")
        chart.max_edit.setText("10")
        ids.append(chart.add_widget_from_palette())
    chart.refresh()
    return ids


def chart_scope_checks(app, chart, kinds) -> None:
    """R-151 / R-161: pixels must come from the received data.

    R-151 is the older promise "the trace is really drawn: a firmware-prefixed
    line parses, and the curve is not hidden behind the grid"; R-161 is the
    drag-and-drop dashboard that superseded the pyqtgraph panel.  The pixel
    proof is shared, so both requirements are asserted here instead of R-151
    being left without any emitting check (which makes the acceptance suite
    report it as uncovered and exit non-zero).
    """
    chart.clear_widgets()
    chart.clear_data()
    widget_id = _add_all_kinds(chart, kinds)[kinds.index("line")]
    _wait(app, chart, len(kinds))
    chart.feed(1000, b"[OmniCOM-chart] AA=1\n")
    chart.flush()
    # The line arrives with a firmware banner in front of it; parsing it at all
    # is the first half of R-151.
    check(f"R-151 firmware-prefixed line reaches the channel store "
          f"(channels={sorted(chart.samples)})",
          "AA" in chart.samples and chart.values.get("AA") == 1.0)
    empty = _painted(app, chart, widget_id, floor=10 ** 6, tries=20)
    for index in range(120):
        chart.feed(1000 + index * 20,
                   f"[OmniCOM-chart] AA={5 + 4 * math.sin(index / 8):.4f}\n".encode())
    chart.flush()
    filled = _painted(app, chart, widget_id, floor=100)
    check(f"R-161 trace pixels grow with the data (empty={empty}, "
          f"with_data={filled})",
          isinstance(empty, int) and isinstance(filled, int) and filled > empty)
    # The trace is painted on top of the grid: a real curve adds hundreds of
    # pixels that the empty grid does not have (R-151, second half).
    check(f"R-151 curve is drawn above the grid (empty={empty}, "
          f"with_data={filled})",
          isinstance(empty, int) and isinstance(filled, int)
          and filled - empty > 200)

    # A channel whose value never changes must still be visible (L-191).
    chart.clear_widgets()
    chart.clear_data()
    widget_id = _add_all_kinds(chart, kinds)[kinds.index("line")]
    _wait(app, chart, len(kinds))
    for index in range(30):
        chart.feed(1000 + index * 100, b"[OmniCOM-chart] AA=3.87\n")
    chart.flush()
    flat = _painted(app, chart, widget_id, floor=100)
    check(f"R-161 flat trace stays visible (painted={flat})",
          isinstance(flat, int) and flat > 100)

def launcher_checks(app) -> None:
    """R-183: the launcher's two rows come from data, not from an empty grid.

    The launcher had no test coverage at all, and its screenshot is quoted in
    both READMEs, so the lower row silently going empty (no window matched any
    shortcut) would have shipped unseen.
    """
    from omnicom import paths
    from omnicom.plugins import PluginHost

    folder = Path(paths.data_dir()) / "plugin-data" / "launcher"
    folder.mkdir(parents=True, exist_ok=True)
    apps = [
        {"name": "甲", "path": "C:/Windows/System32/notepad.exe"},
        {"name": "乙", "path": "C:/Windows/System32/calc.exe"},
        {"name": "丙", "path": "C:/Windows/System32/mspaint.exe"},
    ]
    (folder / "apps.json").write_text(json.dumps(apps, ensure_ascii=False),
                                      encoding="utf-8")
    host = PluginHost(notify=lambda text, level="info": None,
                      log_line=lambda text: None)
    host.load()
    info = next((item for item in host.loaded() if item.id == "launcher"), None)
    check(f"R-183 launcher plugin loads (found={info is not None})", info is not None)
    if info is None:
        host.unload()
        return
    page = info.widget
    # The real scan enumerates this machine's windows on a worker thread and
    # would overwrite the synthetic snapshot below at an unpredictable time.
    # The timer start/stop behaviour is still exercised, only the scan is
    # replaced, so the assertions stay deterministic.
    page.refresh_running = lambda: None
    page._scan_processes = lambda: None
    page.resize(1000, 620)
    page.show()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    shortcuts = [tile for tile in page.tiles if tile.kind == "shortcut"]
    check(f"R-183 every stored shortcut is drawn (tiles={len(shortcuts)}, "
          f"apps={len(page.apps)})",
          len(page.apps) == 3 and len(shortcuts) == len(page.apps))
    check(f"R-183 the count pill summarises both rows ({page.count_label.text()!r})",
          "3" in page.count_label.text() and "·" in page.count_label.text())

    # The running row must react to the scanned windows, not to the store: feed
    # a fake snapshot through the same slot the worker thread emits to.
    page._apply_processes({
        "processes": {"notepad.exe": 2},
        "windows": [
            {"path": "C:/Windows/System32/notepad.exe", "title": "甲窗口",
             "pid": 101, "hwnd": 5001},
            {"path": "C:/Windows/System32/notepad.exe", "title": "乙窗口",
             "pid": 102, "hwnd": 5002},
        ],
    })
    running = [tile for tile in page.tiles if tile.kind == "running"]
    check(f"R-183 running windows become tiles (tiles={len(running)})",
          len(running) == 2)
    check("R-183 the running row replaces the empty hint",
          page.running_host.isVisible() and not page.running_none.isVisible())
    check("R-183 the count pill counts the running windows too "
          f"({page.count_label.text()!r})",
          "2 个窗口" in page.count_label.text())

    # Two windows of one program must be tellable apart by their own labels:
    # the alias is keyed by the window, not by the process (R-095's cousin).
    window_key = info.module._window_key
    app_entry = page.apps[0]
    windows = page._windows_of(app_entry)
    app_entry.setdefault("aliases", {})[window_key(windows[1])] = "A"
    page._rebuild()
    captions = [tile.title for tile in page.tiles if tile.kind == "running"]
    check(f"R-183 a renamed window keeps its own caption (captions={captions})",
          captions == ["甲窗口", "A"])

    # Everything the picture shows has to survive the panel being hidden: the
    # 4 s scan stops when the page is off screen (L-130) and must restart.
    page.hide()
    app.processEvents()
    check("R-183 hiding the page stops the window scan", not page._timer.isActive())
    page.show()
    app.processEvents()
    check("R-183 showing it again restarts the scan", page._timer.isActive())
    page.hide()
    host.unload()


def chart_readout_checks(app, chart) -> None:
    """R-182: every widget kind renders a real value, never ``--``.

    Both READMEs print these five cards side by side (``plugin-chart.png``), so
    a kind that stops receiving its value would make the documentation lie while
    every existing test still passed - the earlier checks asserted *pixels* on
    the line and gauge cards only.
    """
    chart.clear_widgets()
    chart.clear_data()
    chart.channel_combo.clear()
    for name in ("AA", "BB", "CC", "DD", "EE"):
        chart.channel_combo.addItem(name)
    chart.layout_data["widgets"] = [
        {"id": "r182-line", "kind": "line", "channel": "AA", "channels": ["AA"],
         "x": 16, "y": 16, "w": 300, "h": 200},
        {"id": "r182-gauge", "kind": "gauge", "channel": "BB",
         "min": 0, "max": 10, "x": 332, "y": 16, "w": 200, "h": 200},
        {"id": "r182-value", "kind": "value", "channel": "CC",
         "unit": "A", "x": 16, "y": 232, "w": 200, "h": 150},
        {"id": "r182-bar", "kind": "bar", "channel": "DD",
         "min": 0, "max": 100, "unit": "%", "x": 232, "y": 232,
         "w": 300, "h": 150},
        {"id": "r182-lamp", "kind": "lamp", "channel": "EE",
         "x": 548, "y": 232, "w": 200, "h": 150},
    ]
    chart._push_layout()
    _wait(app, chart, len(KINDS))
    chart.feed(1000, b"[OmniCOM-chart] AA=1 BB=2 CC=3.25 DD=42 EE=1\n")
    chart.flush()

    code = """(() => {
      const out = {};
      document.querySelectorAll('#canvas .card').forEach(card => {
        const role = (name) => {
          const node = card.querySelector('[data-role="' + name + '"]');
          return node ? node.textContent.trim() : null;
        };
        const fill = card.querySelector('.bar-fill');
        out[card.dataset.kind] = {
          number: role('number'),
          lamp: role('lampText'),
          fill: fill ? fill.style.width : null
        };
      });
      return JSON.stringify(out);
    })()"""
    raw = _js(app, chart, code)
    state = json.loads(raw) if raw else {}
    check(f"R-182 all five kinds are on the page (kinds={sorted(state)})",
          sorted(state) == sorted(KINDS))
    check(f"R-182 the value card shows the parsed number "
          f"({state.get('value', {}).get('number')!r})",
          state.get("value", {}).get("number") == "3.25")
    check(f"R-182 the bar card shows number and fill "
          f"({state.get('bar')!r})",
          state.get("bar", {}).get("number") == "42.00"
          and state.get("bar", {}).get("fill") == "42%")
    check(f"R-182 the lamp card reads ON for a non-zero value "
          f"({state.get('lamp', {}).get('lamp')!r})",
          state.get("lamp", {}).get("lamp") == "ON")
    check("R-182 no card is left showing --",
          all((card.get("number") or "-") != "--" for card in state.values())
          and all((card.get("lamp") or "-") != "--" for card in state.values()))
    check(f"R-182 the line and gauge cards painted "
          f"(line={_painted(app, chart, 'r182-line')}, "
          f"gauge={_painted(app, chart, 'r182-gauge')})",
          _painted(app, chart, "r182-line") > 100
          and _painted(app, chart, "r182-gauge") > 100)

    # Zero is data, not absence: a lamp must read OFF rather than -- (L-192).
    chart.feed(1100, b"[OmniCOM-chart] AA=1 BB=2 CC=3.25 DD=0 EE=0\n")
    chart.flush()
    raw = _js(app, chart, code)
    zero = json.loads(raw) if raw else {}
    check(f"R-182 a zeroed lamp reads OFF, not -- "
          f"({zero.get('lamp', {}).get('lamp')!r})",
          zero.get("lamp", {}).get("lamp") == "OFF")
    check(f"R-182 a zeroed bar is empty but still numbered "
          f"({zero.get('bar')!r})",
          zero.get("bar", {}).get("number") == "0.00"
          and zero.get("bar", {}).get("fill") == "0%")
    chart.clear_widgets()


def search_checks(app) -> None:
    from PySide6.QtCore import Qt

    from omnicom.ui.search_panel import SearchPanel

    panel = SearchPanel()
    panel.resize(700, 500)
    panel.show()
    text = "Long search result " + "A1B2" * 400 + " FINAL-END-MARKER"
    panel._append_row(42, text)
    panel._append_row(43, "Short result")
    app.processEvents()
    check("R-146 sidebar results stay single-line", not panel.results.wordWrap() and panel.results.uniformItemSizes() and panel.results.visualItemRect(panel.results.item(0)).height() == panel.results.visualItemRect(panel.results.item(1)).height())
    check("R-144 search has no follow switch or latest button", not hasattr(panel, "row_scroll") and not hasattr(panel, "scroll_pill"))
    panel.set_fullscreen(True)
    app.processEvents()
    delegate = panel.results.itemDelegate()
    from PySide6.QtWidgets import QStyleOptionViewItem

    option = QStyleOptionViewItem()
    option.font = panel.results.font()
    index = panel.results.model().index(0, 0)
    document = delegate.document(option, index)
    wide = document.size().height()
    panel.resize(340, 500)
    app.processEvents()
    document = delegate.document(option, index)
    check("R-146 full result text retained", panel.results.item(0).text().endswith("FINAL-END-MARKER"))
    check("R-146 narrow resize reflows height", document.size().height() > wide)
    # Scoped to full screen on purpose: the sidebar deliberately gets a
    # horizontal bar instead (R-158), because a log line is far wider than the
    # column and wrapping there was rejected (R-146).
    check("R-146 full screen has no ellipsis or horizontal scroll", panel.results.textElideMode() == Qt.TextElideMode.ElideNone and panel.results.horizontalScrollBar().maximum() == 0)
    check("R-146 row contains whole wrapped document", panel.results.visualItemRect(panel.results.item(0)).height() >= document.size().height())
    panel.grab().save(str(ROOT / ".artifacts" / "search-wrap.png"))
    panel.set_fullscreen(False)
    app.processEvents()
    check("R-146 returning to sidebar restores single-line without data loss", not panel.results.wordWrap() and panel.results.visualItemRect(panel.results.item(0)).height() == panel.results.visualItemRect(panel.results.item(1)).height() and panel.results.item(0).text().endswith("FINAL-END-MARKER") and panel.results.item(0).data(Qt.ItemDataRole.UserRole) == 42)
    # ------------------------------------------------ R-158 sidebar scrolling
    bar = panel.results.horizontalScrollBar()
    check("R-158 sidebar shows a horizontal scrollbar for over-long rows "
          f"(policy={panel.results.horizontalScrollBarPolicy().name} "
          f"max={bar.maximum()})",
          panel.results.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
          and bar.maximum() > 0)
    bar.setValue(bar.maximum())
    app.processEvents()
    check("R-158 the bar really reaches the tail of the text "
          f"({bar.value()}/{bar.maximum()})",
          bar.value() == bar.maximum() > 0)
    panel.set_fullscreen(True)
    app.processEvents()
    check("R-146 full screen keeps the horizontal bar at zero "
          f"({panel.results.horizontalScrollBar().maximum()})",
          panel.results.horizontalScrollBar().maximum() == 0)
    panel.set_fullscreen(False)
    app.processEvents()
    bar.setValue(0)
    app.processEvents()
    panel.grab().save(str(ROOT / ".artifacts" / "search-sidebar.png"))
    panel.search_selection("FINAL-END-MARKER")
    check("R-050 selected-text search works without scroll controls", panel.input.text() == "FINAL-END-MARKER" and panel._needles() == ["FINAL-END-MARKER"] and panel.match_mode() == "plain")
    panel.close()


def multi_source_checks() -> None:
    from omnicom.plugin_store import CATALOG_URL, catalog_sources

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        urls = []
        for number in (1, 2):
            folder = root / str(number)
            folder.mkdir()
            atomic_json(folder / "catalog.json", {"schema": 1, "plugins": [
                {"id": "shared", "version": f"1.{number}.0", "url": "package.zip", "sha256": "0" * 64},
                {"id": "tie", "version": "1.0.0", "name": str(number)},
                {"id": f"only{number}", "version": "1.0.0"}]})
            urls.append((folder / "catalog.json").as_uri())
        missing = (root / "missing.json").as_uri()
        store = PluginStore(root / "data", root / "bundle", catalog_urls=[missing, *urls])
        result = {item["id"]: item for item in store.check()}
        check("R-149 queries every catalog with failure isolation", set(result) == {"shared", "tie", "only1", "only2"} and len(store.failures) == 1)
        check("R-149 newer same-ID version wins", result["shared"]["version"] == "1.2.0")
        check("R-149 relative package URL belongs to originating catalog", result["shared"]["url"] == (root / "2/package.zip").as_uri())
        check("R-149 equal versions prefer first configured source", result["tie"]["name"] == "1")
        (root / "2/catalog.json").unlink()
        check("R-149 failed source retains only its own cache", any(item["id"] == "only2" for item in store.check()) and len(store.failures) == 2)
        reduced = PluginStore(root / "data", root / "bundle", catalog_urls=urls[:1])
        check("R-149 removed sources never leak through cache", {item["id"] for item in reduced.cached_catalog()} == {"shared", "tie", "only1"})
        check("R-149 source normalization preserves order and deduplicates", catalog_sources(f" {urls[0]}\n\n{urls[0]}\n{urls[1]} ") == urls)
        check("R-149 default JSON lives in repository plugins directory", CATALOG_URL.endswith("/main/plugins/catalog.json") and "/releases/" not in CATALOG_URL)
        legacy = PluginStore(root / "legacy", root / "bundle", catalog_urls=urls[:1])
        atomic_json(legacy.cache, {"schema": 1, "plugins": [{"id": "unknownsource", "version": "9.0.0"}]})
        check("R-149 legacy originless cache cannot impersonate configured source", legacy.cached_catalog() == [])
        cancelled = threading.Event()
        cancelled.set()
        try:
            store.check(cancelled)
        except Cancelled:
            check("R-149 cancelling multi-source refresh aborts remaining sources", True)
        else:
            check("R-149 cancelling multi-source refresh aborts remaining sources", False)


def catalog_build_checks() -> None:
    import make_plugins

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        destination = root / "repository" / "plugins" / "catalog.json"
        catalog = make_plugins.build(root / "packages", "https://example.test/packages/", destination)
        check("R-149 catalog can be generated separately from release ZIPs", json.loads(destination.read_text(encoding="utf-8")) == catalog and not (root / "packages" / "catalog.json").exists())
        with patch.object(sys, "argv", ["make_plugins.py", "--out", str(root / "packages")]), patch.object(make_plugins, "build") as build:
            make_plugins.main()
        check("R-149 builder defaults JSON to repository plugins directory", build.call_args.args[2] == ROOT / "plugins" / "catalog.json")


def curve_sources_checks(app, chart, kinds) -> None:
    """R-325: one curve card, several named sources, editable afterwards."""
    import json as json_module

    chart.clear_widgets()
    for _ in range(20):
        app.processEvents()
    chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("line"))
    for name, base in (("AA", 1), ("BB", 2)):
        for step in range(6):
            chart.replay_line(step + (0.5 if name == "BB" else 0.0),
                              f"[OmniCOM-chart] {name}={base + step}")
    chart.channel_combo.setEditText("AA, BB")
    identifier = chart.add_widget_from_palette()
    check("R-325 a curve card accepts several comma-separated sources",
          identifier is not None, str(chart.layout_data["widgets"][-1:]))
    chart.flush_live()
    for _ in range(20):
        app.processEvents()
    described = next((w for w in _describe(app, chart)["widgets"]
                      if w["id"] == identifier), {})
    check("R-325 both sources are stored on the card",
          described.get("channels") == ["AA", "BB"],
          str(described.get("channels")))
    plot = ("echarts.getInstanceByDom(document.querySelector("
            "'.card[data-kind=line] .plot'))")
    names = _js(app, chart, f"JSON.stringify({plot}.getOption().series.map(s => s.name))")
    check("R-325 the plot really carries one series per source",
          sorted(json_module.loads(names or "[]")) == ["AA", "BB"], str(names))
    lengths = _js(app, chart, f"JSON.stringify({plot}.getOption().series.map(s => s.data.length))")
    check("R-325 each series has its own samples",
          all(int(n) > 0 for n in json_module.loads(lengths or "[]")), str(lengths))
    legend = _js(app, chart, f"JSON.stringify({plot}.getOption().legend || [])")
    legend = json_module.loads(legend or "[]")
    check("R-325 a multi-source curve shows its legend",
          bool(legend) and legend[0].get("show") is True, str(legend))
    geometry = _geometry_of(app, chart, identifier)

    # The gear button must reach the host through the page -> Python bridge.
    # Opening the real dialog would block (it is modal), so the signal is
    # observed instead; the click itself is driven with a real DOM event.
    seen: list = []
    chart.bridge.sourcesRequested.disconnect(chart.edit_widget_sources)
    chart.bridge.sourcesRequested.connect(seen.append)
    try:
        clicked = _js(app, chart, "(function () {"
                      "var button = document.querySelector("
                      "'.card[data-kind=line] .sources');"
                      "if (!button) { return 'missing'; }"
                      "button.click(); return 'ok'; })()")
        for _ in range(20):
            app.processEvents()
    finally:
        chart.bridge.sourcesRequested.disconnect(seen.append)
        chart.bridge.sourcesRequested.connect(chart.edit_widget_sources)
    check("R-325 the card gear reaches the host bridge",
          clicked == "ok" and seen == [identifier], f"{clicked} / {seen}")

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    def edit_sources(accept):
        errors = []

        def interact():
            try:
                dialog = app.activeModalWidget()
                if not isinstance(dialog, sys.modules[type(chart).__module__].SourcesDialog):
                    raise AssertionError("source editor did not open")
                check("R-325 reopening the editor retains checked sources",
                      dialog.sources() == ["AA", "BB"])
                for name in ("AA", "CC"):
                    item = next(dialog.channels.item(index) for index in range(dialog.channels.count())
                                if dialog.channels.item(index).text() == name)
                    rect = dialog.channels.visualItemRect(item)
                    QTest.mouseClick(dialog.channels.viewport(), Qt.MouseButton.LeftButton,
                                     pos=rect.center())
                if accept:
                    QTest.mouseClick(dialog.confirm, Qt.MouseButton.LeftButton)
                else:
                    dialog.reject()
            except BaseException as exc:
                errors.append(exc)
                modal = app.activeModalWidget()
                if isinstance(modal, QDialog):
                    modal.reject()

        QTimer.singleShot(0, interact)
        chart.edit_widget_sources(identifier)
        if errors:
            raise errors[0]

    chart.replay_line(20.0, "[OmniCOM-chart] CC=9")
    before_edit = chart.config_payload()
    edit_sources(False)
    check("R-325 cancelling the real editor leaves card configuration unchanged",
          chart.config_payload() == before_edit)
    edit_sources(True)
    check("R-325 editing a card replaces its sources",
          next(item for item in chart.layout_data["widgets"] if item["id"] == identifier)
          ["channels"] == ["BB", "CC"])
    chart.flush_live()
    for _ in range(20):
        app.processEvents()
    described = next((w for w in _describe(app, chart)["widgets"]
                      if w["id"] == identifier), {})
    check("R-325 the edited sources are stored",
          described.get("channels") == ["BB", "CC"],
          str(described.get("channels")))
    check("R-325 editing keeps the card geometry",
          _geometry_of(app, chart, identifier) == geometry,
          f"{geometry} vs {_geometry_of(app, chart, identifier)}")
    names = _js(app, chart, f"JSON.stringify({plot}.getOption().series.map(s => s.name))")
    check("R-325 the plot drops the old source and keeps the new ones",
          sorted(json_module.loads(names or "[]")) == ["BB", "CC"], str(names))
    check("R-325 an empty source list is refused",
          chart.update_widget_sources(identifier, ["  ", ""]) is False)

    payload = chart.config_payload()
    restored = next((raw for raw in payload["widgets"] if raw["id"] == identifier), {})
    check("R-325 the sources travel with the exported layout",
          restored.get("channels") == ["BB", "CC"], str(restored.get("channels")))
    chart.clear_widgets()
    for _ in range(10):
        app.processEvents()


def chart_display_controls_checks(app, chart) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog, QDialog

    module = sys.modules[type(chart).__module__]

    def pick(swatch, color, accept=True):
        errors = []

        def interact():
            try:
                dialog = app.activeModalWidget()
                check("R-328 swatch opens a real palette dialog", isinstance(dialog, QColorDialog))
                check("R-328 palette provides standard and custom colors",
                      dialog.standardColor(0).isValid() and dialog.customCount() > 0)
                dialog.setCurrentColor(QColor(color))
                dialog.grab().save(str(ROOT / ".artifacts" / "lamp-palette.png"))
                if accept:
                    dialog.accept()
                else:
                    dialog.reject()
            except BaseException as exc:
                errors.append(exc)
                modal = app.activeModalWidget()
                if isinstance(modal, QDialog):
                    modal.reject()

        QTimer.singleShot(0, interact)
        swatch.click()
        if errors:
            raise errors[0]

    chart.clear_widgets()
    for kind in KINDS:
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData(kind))
        app.processEvents()
        check(f"R-325 {kind} exposes only appropriate source and color controls",
              chart.sources_button.isHidden() == (kind != "line")
              and chart.lamp_on_color.isHidden() == (kind != "lamp"))
    chart.channel_combo.setEditText("AA, BB")
    identifier = chart.add_widget_from_palette()
    check("R-325 multiple typed names never change a non-line kind",
          chart.kind_combo.currentData() == "lamp"
          and chart.layout_data["widgets"][-1]["kind"] == "lamp"
          and chart.layout_data["widgets"][-1]["channel"] == "AA")
    chart.clear_widgets()
    chart.channel_combo.setEditText("LAMP")
    pick(chart.lamp_on_color, "#d81b60")
    previous = chart.lamp_on_color.color_name
    pick(chart.lamp_on_color, "#123456", False)
    check("R-328 canceling palette keeps the previous color",
          chart.lamp_on_color.color_name == previous)
    pick(chart.lamp_off_color, "#1976d2")
    identifier = chart.add_widget({"kind": "lamp", "channel": "LAMP",
                                   "onColor": chart.lamp_on_color.color_name,
                                   "offColor": chart.lamp_off_color.color_name})
    chart.replay_line(1, "[OmniCOM-chart] LAMP=1")
    chart.flush_live()
    color_query = "getComputedStyle(document.querySelector('.lamp-dot')).backgroundColor"

    def rendered_color(expected):
        deadline = time.perf_counter() + 3
        actual = _js(app, chart, color_query)
        while actual != expected and time.perf_counter() < deadline:
            actual = _js(app, chart, color_query)
        return actual

    check("R-328 lamp really renders its selected on color",
          rendered_color("rgb(216, 27, 96)") == "rgb(216, 27, 96)",
          str(_js(app, chart, color_query)))
    chart.replay_line(2, "[OmniCOM-chart] LAMP=0")
    chart.flush_live()
    check("R-328 lamp really renders its selected off color",
            rendered_color("rgb(25, 118, 210)") == "rgb(25, 118, 210)",
            str(_js(app, chart, color_query)))
    errors = []

    def edit_lamp():
        try:
            dialog = app.activeModalWidget()
            check("R-328 existing lamp opens its color editor",
                  isinstance(dialog, module.LampColorsDialog))
            pick(dialog.swatches["onColor"], "#00aa44")
            dialog.confirm.click()
        except BaseException as exc:
            errors.append(exc)
            modal = app.activeModalWidget()
            if isinstance(modal, QDialog):
                modal.reject()

    QTimer.singleShot(0, edit_lamp)
    chart.edit_widget_sources(identifier)
    if errors:
        raise errors[0]
    _describe(app, chart)
    before = chart.config_payload()

    def cancel_lamp():
        dialog = app.activeModalWidget()
        dialog.swatches["offColor"].set_color("#ff0000")
        dialog.reject()

    QTimer.singleShot(0, cancel_lamp)
    chart.edit_widget_sources(identifier)
    check("R-328 canceling lamp editor preserves configuration",
          chart.config_payload() == before)
    exported = chart.config_payload()
    chart.clear_widgets()
    chart.apply_config(exported)
    described = _describe(app, chart)["widgets"][0]
    check("R-328 lamp color edits survive configuration round trip",
            described["onColor"] == "#00aa44" and described["offColor"] == "#1976d2", str(described))
    check("R-328 invalid lamp colors are rejected",
          chart._sanitize_widget({"kind": "lamp", "channel": "A", "onColor": "bad-color"}) is None)
    check("R-328 older lamps without colors remain valid",
          chart._sanitize_widget({"kind": "lamp", "channel": "A", "onColor": None}) is not None)
    chart.clear_widgets()

    def edit_rules(action, palette=False):
        failures = []

        def interact():
            try:
                dialog = app.activeModalWidget()
                check("R-329 rule settings use the same lamp dialog",
                      isinstance(dialog, module.LampColorsDialog))
                action(dialog)
            except BaseException as exc:
                failures.append(exc)
                modal = app.activeModalWidget()
                if isinstance(modal, QDialog):
                    modal.reject()

        QTimer.singleShot(0, interact)
        if palette:
            chart.lamp_rules_button.click()
        else:
            chart.edit_widget_sources(identifier)
        if failures:
            raise failures[0]

    def configure_three_colors(dialog):
        check("R-329 new lamp starts with numeric matching", dialog.match_values.isChecked())
        dialog.rule_rows[0][1].setText("0")
        pick(dialog.rule_rows[0][2], "#ff0000")
        dialog.add_rule_button.click()
        pick(dialog.rule_rows[1][2], "#ffffff")
        dialog.add_rule_button.click()
        pick(dialog.rule_rows[2][2], "#ffff00")
        dialog.fallback_none.setChecked(True)
        dialog.rule_rows[2][1].setText("1")
        check("R-329 duplicate value prevents confirmation", not dialog.confirm.isEnabled())
        dialog.rule_rows[2][1].setText("nan")
        check("R-329 non-finite value prevents confirmation", not dialog.confirm.isEnabled())
        dialog.rule_rows[2][1].setText("2")
        check("R-329 valid unique values enable confirmation", dialog.confirm.isEnabled())
        dialog.add_rule_button.click()
        holder = dialog.rule_rows[-1][0]
        remove = next(button for button in holder.findChildren(module.QPushButton)
                      if button.toolTip() == chart.api.t("删除数值", "Remove value"))
        remove.click()
        check("R-329 remove button deletes only its rule", len(dialog.rule_rows) == 3)
        dialog.grab().save(str(ROOT / ".artifacts" / "lamp-rules.png"))
        dialog.confirm.click()

    chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("lamp"))
    chart.channel_combo.setEditText("RULE_LAMP")
    edit_rules(configure_three_colors, palette=True)
    identifier = chart.add_widget_from_palette()
    described = _describe(app, chart)["widgets"][0]
    expected_rules = [{"value": 0.0, "color": "#ff0000"},
                      {"value": 1.0, "color": "#ffffff"},
                      {"value": 2.0, "color": "#ffff00"}]
    check("R-329 created lamp carries all numeric rules",
          described["colorRules"] == expected_rules and described["fallbackColor"] is None)
    color_cases = [(0, "rgb(255, 0, 0)"), (1, "rgb(255, 255, 255)"),
                   (2, "rgb(255, 255, 0)"), (3, "rgba(0, 0, 0, 0)")]
    for value, expected_color in color_cases:
        chart.replay_line(3 + value, f"[OmniCOM-chart] RULE_LAMP={value}")
        chart.flush_live()
        actual = rendered_color(expected_color)
        displayed = _js(app, chart, "document.querySelector('[data-role=lampText]').textContent")
        check(f"R-329 value {value} renders its mapped color", actual == expected_color, str(actual))
        check("R-329 numeric lamp displays received value", float(displayed) == value, str(displayed))

    before = chart.config_payload()

    def cancel_rules(dialog):
        dialog.rule_rows[0][1].setText("100")
        dialog.reject()

    edit_rules(cancel_rules)
    check("R-329 canceling rule edits keeps all configuration", chart.config_payload() == before)

    def configure_fallback(dialog):
        dialog.fallback_none.setChecked(False)
        pick(dialog.fallback_swatch, "#1976d2")
        dialog.confirm.click()

    edit_rules(configure_fallback)
    _describe(app, chart)
    chart.replay_line(7, "[OmniCOM-chart] RULE_LAMP=99")
    chart.flush_live()
    check("R-329 unmatched value can render a custom color",
          rendered_color("rgb(25, 118, 210)") == "rgb(25, 118, 210)")
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "lamp.json"
        chart.export_config(str(path))
        exported_rules = json.loads(path.read_text(encoding="utf-8"))
        chart.clear_widgets()
        chart.apply_config(exported_rules)
    restored = _describe(app, chart)["widgets"][0]
    check("R-329 file round trip retains rules and fallback",
          restored["colorRules"] == expected_rules and restored["fallbackColor"] == "#1976d2")
    saved_layout = json.loads(str(chart.api.settings.value("layout")))
    check("R-329 settings persist numeric rules", saved_layout["widgets"][0]["colorRules"] == expected_rules)

    def configure_single(dialog):
        for holder, _, _, _ in list(dialog.rule_rows):
            dialog.remove_rule(holder)
        dialog.add_rule_button.click()
        dialog.rule_rows[0][1].setText("-3.5")
        pick(dialog.rule_rows[0][2], "#ff0000")
        dialog.fallback_none.setChecked(True)
        dialog.confirm.click()

    edit_rules(configure_single)
    _describe(app, chart)
    for value, expected_color in ((-3.5, "rgb(255, 0, 0)"), (0, "rgba(0, 0, 0, 0)")):
        chart.replay_line(8, f"[OmniCOM-chart] RULE_LAMP={value}")
        chart.flush_live()
        check("R-329 single rule supports arbitrary trigger value",
              rendered_color(expected_color) == expected_color)
    single = _describe(app, chart)["widgets"][0]
    check("R-329 single and multicolor lamps share one kind", single["kind"] == "lamp" and len(single["colorRules"]) == 1)

    def configure_colorless_rule(dialog):
        dialog.rule_rows[0][3].setChecked(True)
        check("R-329 no-color rule disables its swatch", not dialog.rule_rows[0][2].isEnabled())
        dialog.fallback_none.setChecked(False)
        pick(dialog.fallback_swatch, "#1976d2")
        dialog.confirm.click()

    edit_rules(configure_colorless_rule)
    _describe(app, chart)
    chart.replay_line(9, "[OmniCOM-chart] RULE_LAMP=-3.5")
    chart.flush_live()
    check("R-329 explicit no-color rule overrides colored fallback", rendered_color("rgba(0, 0, 0, 0)") == "rgba(0, 0, 0, 0)")
    chart.replay_line(10, "[OmniCOM-chart] RULE_LAMP=17")
    chart.flush_live()
    check("R-329 other values still use the colored fallback", rendered_color("rgb(25, 118, 210)") == "rgb(25, 118, 210)")
    chart.clear_data()
    chart.flush()
    check("R-329 missing data is colorless, not a matching zero",
          rendered_color("rgba(0, 0, 0, 0)") == "rgba(0, 0, 0, 0)"
          and _js(app, chart, "document.querySelector('[data-role=lampText]').textContent") == "--")
    for invalid in ([{"value": 0, "color": "red"}, {"value": "0", "color": "white"}],
                    [{"value": float("inf"), "color": "red"}],
                    [{"value": True, "color": "red"}], [{"color": "red"}],
                    [{"value": 1, "color": "bad-color"}]):
        check("R-329 malformed rules are rejected on import",
              chart._sanitize_widget({"kind": "lamp", "channel": "A", "colorRules": invalid}) is None)
    chart.lamp_rule_config = None
    chart._on_kind_changed()
    chart.clear_widgets()


def chart_window_checks(app, chart) -> None:
    saved = chart.config_payload()
    try:
        for retention in (100, 5000):
            chart.clear_widgets()
            chart.clear_data()
            chart.seconds.setValue(0)
            chart.drawn.setValue(5000)
            chart.points.setValue(retention)
            chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("line"))
            chart.channel_combo.setEditText("HISTORY_A, HISTORY_B")
            chart.add_widget_from_palette()
            previous = 0
            for total in (128, 1000, 3200, 4999, 5000, 5101, 6200):
                for index in range(previous, total):
                    chart.replay_line(index / 100, f"[OmniCOM-chart] HISTORY_A={index} HISTORY_B={-index}")
                chart.flush_live()
                deadline = time.perf_counter() + 5
                while chart._pending_ack and time.perf_counter() < deadline:
                    app.processEvents()
                check("R-164 history batch is acknowledged", not chart._pending_ack)
                summary = json.loads(_js(app, chart,
                    "JSON.stringify(echarts.getInstanceByDom(document.querySelector('.card[data-kind=line] .plot'))"
                    ".getOption().series.map(s=>({name:s.name,count:s.data.length,first:s.data[0],last:s.data[s.data.length-1]})))"))
                expected = min(total, retention)
                drawn = min(expected, 5000)
                buffers_complete = all(
                    len(chart.samples[name]) == expected
                    and chart.samples[name][0][0] == (total - expected) / 100
                    and chart.samples[name][-1][0] == (total - 1) / 100
                    for name in ("HISTORY_A", "HISTORY_B")
                )
                check("R-164 Python buffers retain the same complete window", buffers_complete)
                page_complete = len(summary) == 2 and all(
                    item["count"] == drawn
                    and item["first"][0] == round((total - drawn) / 100, 4)
                    and item["last"][0] == round((total - 1) / 100, 4)
                    for item in summary
                )
                check(f"R-164 retention={retention} total={total} keeps the retained window and draws its tail", page_complete, str(summary))
                check("R-164 eviction only after the explicit retention is full", total <= retention or chart.evicted >= total - retention)
                previous = total
    finally:
        chart.clear_widgets()
        chart.clear_data()
        chart.apply_config(saved)


def store_ui_checks(app) -> None:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QLineEdit, QPushButton, QWidget

    from omnicom.plugin_store import CATALOG_URL, configured_catalogs
    from omnicom.ui.dialogs import PreferencesDialog
    from omnicom.ui.main_window import OmniWindow
    from omnicom.ui.plugin_store import PluginStoreDialog

    class Window(QWidget):
        apply_plugin_sources = OmniWindow.apply_plugin_sources

        def suspend_plugins(self):
            pass

        def reload_plugins(self):
            pass

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bundled = root / "bundle"
        (bundled / "demo").mkdir(parents=True)
        atomic_json(bundled / "demo" / "plugin.json", {"id": "demo", "version": "1.0.0", "name": "Demo"})
        atomic_json(root / "catalog.json", {"schema": 1, "plugins": [{"id": "demo", "version": "1.1.0", "name": "Demo"}, {"id": "extra", "version": "1.0.0", "name": "Extra"}]})
        window = Window()
        window.settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        dialog = PluginStoreDialog(window, PluginStore(root / "data", bundled, (root / "catalog.json").as_uri()))
        dialog.show()
        dialog.check()
        deadline = time.perf_counter() + 10
        while dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        check("R-148 asynchronous catalog completion", not dialog.busy())
        check("R-148 UI includes available uninstalled entries", set(dialog.rows) == {"demo", "extra"} and dialog.rows["extra"]["item"]["installed_version"] is None)
        check("R-148 update red dot at top right", not dialog.rows["demo"]["badge"].isHidden())
        check("R-148 installed and latest versions are explicit", "1.0.0" in dialog.rows["demo"]["version"].text() and "1.1.0" in dialog.rows["demo"]["version"].text())
        app.processEvents()
        dialog.grab().save(str(ROOT / ".artifacts" / "plugin-store.png"))
        from omnicom.i18n import set_language

        set_language("en")
        dialog.retranslate()
        check("R-148 store language refresh includes version and actions", "Installed" in dialog.rows["demo"]["version"].text() and dialog.refresh_button.text() == "Check updates")
        set_language("zh")
        dialog.retranslate()
        # R-178 added a *filter* box, so "no QLineEdit at all" is no longer the
        # intent: the dialog must never carry a catalog-address editor.  Re-based
        # on the actual requirement (R-149) instead of on the widget class.
        from omnicom.i18n import tr

        check("R-149 store has no catalog address editor",
              [edit for edit in dialog.findChildren(QLineEdit)
               if edit is not dialog.filter_edit] == []
              and dialog.filter_edit.placeholderText() == tr("store.filter")
              and not hasattr(dialog, "source"))
        window.settings.setValue("plugins/catalog", (root / "legacy.json").as_uri())
        check("R-149 legacy custom single source is preserved", configured_catalogs(window.settings) == [(root / "legacy.json").as_uri()])
        window.settings.setValue("plugins/catalog", "https://github.com/Fatmear/OmniCOM/releases/download/plugin-store/catalog.json")
        check("R-149 legacy official Release JSON migrates to repository", configured_catalogs(window.settings) == [CATALOG_URL])
        window._plugin_store_dialog = dialog
        sources = [(root / "catalog.json").as_uri(), (root / "other.json").as_uri()]
        preferences = PreferencesDialog(window, plugin_sources="\n".join(configured_catalogs(window.settings)))
        preferences.pluginSourcesChanged.connect(window.apply_plugin_sources)
        preferences.plugin_sources.setPlainText("\n".join(sources))
        window.settings.sync()
        check("R-149 preferences saves multiline sources and removes old key", configured_catalogs(window.settings) == sources and not window.settings.contains("plugins/catalog"))
        check("R-149 open store receives preference changes without network", dialog.store.catalog_urls == sources)
        reopened = PreferencesDialog(window, plugin_sources="\n".join(configured_catalogs(window.settings)))
        check("R-149 preferences restores both sources", reopened.plugin_sources.toPlainText().splitlines() == sources)
        preferences.show()
        preferences.resize(540, 500)
        app.processEvents()
        preferences.preferences_scroll.ensureWidgetVisible(preferences.plugin_sources)
        app.processEvents()
        check("R-149 preferences can reach sources in a small window", preferences.preferences_scroll.verticalScrollBar().maximum() > 0)
        preferences.grab().save(str(ROOT / ".artifacts" / "plugin-preferences.png"))
        set_language("en")
        preferences.render()
        check("R-149 preferences source label translates", preferences.plugin_sources_section.text() == "Plugin catalogs (one URL per line)")
        set_language("zh")
        preferences.close()
        reopened.close()
        dialog.check()
        deadline = time.perf_counter() + 10
        while dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        check("R-149 store reports partial failures without hiding successful catalog", not dialog.busy() and len(dialog.store.failures) == 1 and "1 个失败" in dialog.status.text() and "extra" in dialog.rows)
        dialog.close()

        # ------------------------------------- R-323 disabled row actions
        # A disabled bundled plugin is not gone: it must offer 启用 and it must
        # NOT offer 卸载 - an uninstall button next to an already-disabled
        # plugin is a second, contradictory answer to the same question
        # (2026-11-23: "卸载之后，插件商店会显示 启用和卸载两个按钮").
        core_root = root / "core-ui"
        core_bundle = core_root / "bundle"
        for name in ("terminal", "transfer"):
            folder = core_bundle / name
            folder.mkdir(parents=True)
            atomic_json(folder / "plugin.json",
                        {"id": name, "name": name, "version": "1.0.0",
                         "api": API_VERSION, "entry": "plugin.py"})
            (folder / "plugin.py").write_text(
                "from PySide6.QtWidgets import QWidget\n\n\n"
                "def create(api):\n    return QWidget()\n", encoding="utf-8")
        core_store = PluginStore(core_root / "data", core_bundle, catalog_urls=[])
        core_store.set_disabled("terminal", True)
        core_dialog = PluginStoreDialog(window, core_store)
        core_dialog.show()
        app.processEvents()
        row = core_dialog.rows["terminal"]
        labels = [button.text() for button in row["widget"].findChildren(QPushButton)]
        check("R-323 a disabled plugin only offers 启用, never 卸载",
              "启用" in labels and "卸载" not in labels, str(labels))
        check("R-323 the disabled row still reports its version",
              row["item"]["installed_version"] == "1.0.0"
              and row["item"]["disabled"] is True, str(row["item"]))
        enabled = [button for button in row["widget"].findChildren(QPushButton)
                   if button.text() == "启用"]
        check("R-323 there is exactly one 启用 button", len(enabled) == 1, str(labels))
        enabled[0].click()
        deadline = time.perf_counter() + 10
        while core_dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        app.processEvents()
        check("R-323 clicking 启用 clears the disabled flag",
              core_store.disabled() == set(), str(core_store.disabled()))
        restored = core_dialog.rows["terminal"]
        labels = [button.text() for button in restored["widget"].findChildren(QPushButton)]
        # A core plugin ships with the app, so enabling it must **not** bring a
        # destructive 卸载 action back: the only action stays the on/off toggle
        # (R-293, user 2026-11-24).
        check("R-323 an enabled core plugin offers 禁用 and never 卸载",
              "禁用" in labels and "卸载" not in labels
              and "安装" not in labels, str(labels))
        core_dialog.close()
        core_dialog.deleteLater()
        app.processEvents()

        # ------------------------------------- R-327 a real uninstall
        # "Uninstall" must remove the plugin, not just mark it: the folder
        # leaves the program, the store stops reporting it as installed, the
        # row offers 安装, and installing again brings it back with the user's
        # own data untouched.
        uninstall_root = root / "uninstall-ui"
        uninstall_bundle = uninstall_root / "bundle"
        # A *store* plugin: core plugins (terminal/transfer) are toggled on/off
        # and can no longer be really uninstalled (R-293, user 2026-11-24), so
        # the uninstall contract is exercised on a normal plugin.
        for name in ("gauge", "probe"):
            folder = uninstall_bundle / name
            folder.mkdir(parents=True)
            atomic_json(folder / "plugin.json",
                        {"id": name, "name": name, "version": "1.0.0",
                         "api": API_VERSION, "entry": "plugin.py"})
            (folder / "plugin.py").write_text(
                "from PySide6.QtWidgets import QWidget\n\n\n"
                "def create(api):\n    return QWidget()\n", encoding="utf-8")
        user_data = uninstall_root / "data" / "plugin-data" / "gauge" / "settings.ini"
        user_data.parent.mkdir(parents=True)
        user_data.write_bytes(b"keep me")
        uninstall_store = PluginStore(uninstall_root / "data", uninstall_bundle,
                                      catalog_urls=[])
        placed = sorted(uninstall_store.installed())
        check("R-327 the store plugins start out installed",
              placed == ["gauge", "probe"], str(placed))

        uninstall_store.uninstall("gauge")
        check("R-327 uninstall really removes the plugin folder",
              not (uninstall_bundle / "gauge").is_dir(),
              str(uninstall_bundle / "gauge"))
        check("R-327 a removed plugin is no longer reported as installed",
              sorted(uninstall_store.installed()) == ["probe"],
              str(sorted(uninstall_store.installed())))
        check("R-327 uninstall keeps the user's plugin data",
              user_data.read_bytes() == b"keep me")

        removed_rows = {row["id"]: row for row in
                        uninstall_store.rows(uninstall_store.cached_catalog())}
        removed = removed_rows["gauge"]
        check("R-327 a removed row claims neither installed nor disabled",
              removed["installed_version"] is None
              and removed["removed"] is True
              and removed["disabled"] is False, str(removed))

        removed_dialog = PluginStoreDialog(window, uninstall_store)
        removed_dialog.show()
        app.processEvents()
        removed_row = removed_dialog.rows["gauge"]
        labels = [button.text() for button in
                  removed_row["widget"].findChildren(QPushButton)]
        check("R-327 a removed row offers 安装 and nothing else",
              "安装" in labels and "卸载" not in labels
              and "启用" not in labels, str(labels))
        check("R-327 a removed row installs instead of showing 已安装",
              removed_row["install"].isEnabled()
              and removed_row["install"].text() == "安装",
              f"{removed_row['install'].text()} enabled={removed_row['install'].isEnabled()}")
        removed_row["install"].click()
        deadline = time.perf_counter() + 10
        while removed_dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        app.processEvents()
        check("R-327 installing a removed plugin puts its folder back",
              (uninstall_bundle / "gauge" / "plugin.json").is_file())
        check("R-327 the reinstall clears the removed record",
              uninstall_store.removed() == set(),
              str(uninstall_store.removed()))
        reinstalled = sorted(uninstall_store.installed())
        check("R-327 the reinstalled plugin is installed again",
              reinstalled == ["gauge", "probe"], str(reinstalled))
        check("R-327 reinstalling never touches the user's data",
              user_data.read_bytes() == b"keep me")
        removed_dialog.close()
        removed_dialog.deleteLater()
        app.processEvents()

        # A plugin that lives **only** in the user folder (no remote catalog
        # carries it, e.g. a hand-installed build) must behave the same way:
        # after a real uninstall its row survives and 安装 puts the backup back.
        # ``terminal`` above proves the bundled path; this proves the local one.
        local_folder = uninstall_store.root / "localonly"
        local_folder.mkdir(parents=True)
        atomic_json(local_folder / "plugin.json",
                    {"id": "localonly", "name": "Local only", "version": "2.1.0",
                     "api": API_VERSION, "entry": "plugin.py"})
        (local_folder / "plugin.py").write_text(
            "from PySide6.QtWidgets import QWidget\n\n\n"
            "def create(api):\n    return QWidget()\n", encoding="utf-8")
        uninstall_store.uninstall("localonly")
        local_dialog = PluginStoreDialog(window, uninstall_store)
        local_dialog.show()
        app.processEvents()
        local_row = local_dialog.rows.get("localonly")
        check("R-327 a local-only uninstall keeps a row",
              local_row is not None, str(sorted(local_dialog.rows)))
        check("R-327 a local-only removed row can install its backup",
              local_row is not None and local_row["install"].isEnabled()
              and local_row["install"].text() == "安装",
              f"{local_row and local_row['install'].text()}")
        if local_row is not None:
            check("R-327 a local-only row never offers uninstall",
                  not any(button.text() == "卸载" for button in
                          local_row["widget"].findChildren(QPushButton)))
            local_row["install"].click()
            deadline = time.perf_counter() + 10
            while local_dialog.busy() and time.perf_counter() < deadline:
                app.processEvents()
            app.processEvents()
        check("R-327 a local-only reinstall restores the folder",
              (uninstall_store.root / "localonly" / "plugin.json").is_file())
        local_dialog.close()
        local_dialog.deleteLater()
        app.processEvents()

        # Backup deleted out from under us: the row must stay (so the plugin is
        # not silently gone) but the button may not pretend an install would
        # work, and it explains why (R-327).
        uninstall_store.uninstall("probe")
        for folder in (uninstall_root / "data" / "plugin-updates").iterdir():
            if folder.name.startswith("probe-removed"):
                shutil.rmtree(folder)
        gone_dialog = PluginStoreDialog(window, uninstall_store)
        gone_dialog.show()
        app.processEvents()
        gone_row = gone_dialog.rows.get("probe")
        check("R-327 an uninstalled row survives a deleted backup",
              gone_row is not None, str(sorted(gone_dialog.rows)))
        check("R-327 a deleted backup disables the install button",
              gone_row is not None and not gone_row["install"].isEnabled()
              and "备份" in gone_row["install"].toolTip(),
              f"{gone_row and gone_row['install'].toolTip()}")
        gone_dialog.close()
        gone_dialog.deleteLater()
        app.processEvents()
        uninstall_store.restore("localonly", expect_version="2.1.0")

        # Core plugins are switched on/off, never installed or uninstalled
        # (R-293 + user, 2026-11-24).  The toggle wrote the *inverse* flag once,
        # so 启用 actually disabled the plugin again - the user reported 启用终端
        # 启用不了.  This drives the real button and reads the persisted state.
        atomic_json(uninstall_bundle / "terminal" / "plugin.json",
                    {"id": "terminal", "name": "terminal", "version": "1.0.0",
                     "api": API_VERSION, "entry": "plugin.py"})
        (uninstall_bundle / "terminal" / "plugin.py").write_text(
            "from PySide6.QtWidgets import QWidget\n\n\n"
            "def create(api):\n    return QWidget()\n", encoding="utf-8")
        uninstall_store.set_disabled("terminal", True)
        core_dialog = PluginStoreDialog(window, uninstall_store)
        core_dialog.show()
        app.processEvents()
        core_row = core_dialog.rows["terminal"]
        check("R-293 a core row offers a toggle instead of install/uninstall",
              core_row.get("toggle") is not None
              and core_row.get("install") is None
              and core_row.get("picker") is None
              and not any(button.text() in ("安装", "卸载")
                          for button in core_row["widget"].findChildren(QPushButton)),
              str([b.text() for b in core_row["widget"].findChildren(QPushButton)]))
        check("R-293 a disabled core row reads 启用",
              core_row["toggle"].text() == "启用", core_row["toggle"].text())
        core_row["toggle"].click()
        deadline = time.perf_counter() + 10
        while core_dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        for _ in range(5):
            app.processEvents()
        check("R-293 enabling a core plugin clears its disabled flag",
              "terminal" not in uninstall_store.disabled(),
              str(sorted(uninstall_store.disabled())))
        enabled_row = core_dialog.rows["terminal"]
        check("R-293 an enabled core row reads 禁用",
              enabled_row["toggle"].text() == "禁用", enabled_row["toggle"].text())
        enabled_row["toggle"].click()
        deadline = time.perf_counter() + 10
        while core_dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        for _ in range(5):
            app.processEvents()
        check("R-293 disabling a core plugin sets its disabled flag",
              "terminal" in uninstall_store.disabled(),
              str(sorted(uninstall_store.disabled())))
        check("R-293 the core folder is never moved by the toggle",
              (uninstall_bundle / "terminal" / "plugin.json").is_file())
        core_dialog.close()
        core_dialog.deleteLater()
        app.processEvents()

        # The host must obey the removal immediately: after a real uninstall its
        # plugin is no longer loaded and its page is gone from the window, while
        # the other plugin keeps working (R-327).
        #
        # The whole check runs against a throwaway data folder *and* a
        # throwaway plugin folder.  A real uninstall moves files, so pointing it
        # at the repository's own ``plugins/`` deleted the shipped terminal
        # plugin while this suite ran (recovered with git).
        from omnicom.plugins import PluginHost

        host_data = root / "host-data"
        host_plugins = root / "host-plugins"
        for name in ("gauge", "probe"):
            folder = host_plugins / name
            folder.mkdir(parents=True)
            atomic_json(folder / "plugin.json",
                        {"id": name, "name": name, "version": "1.0.0",
                         "api": API_VERSION, "entry": "plugin.py",
                         "page": "console" if name == "gauge" else "transfer"})
            (folder / "plugin.py").write_text(
                "from PySide6.QtWidgets import QWidget\n\n\n"
                "def create(api):\n    return QWidget()\n", encoding="utf-8")
        host = PluginHost()
        original_paths = host.search_paths
        host.search_paths = lambda: [str(host_plugins)]
        try:
            host.load()
            before_ids = sorted(info.id for info in host.loaded())
            check("R-327 the host loads both plugins from its own folder",
                  before_ids == ["gauge", "probe"], str(before_ids))
            host_store = PluginStore(host_data, host_plugins, catalog_urls=[])
            host_store.uninstall("gauge")
            host.unload()
            host.load()
            after_ids = sorted(info.id for info in host.loaded())
            check("R-327 the host stops loading a removed plugin",
                  "gauge" not in after_ids, str(after_ids))
            check("R-327 the other plugin is unaffected",
                  "probe" in after_ids
                  and (host_plugins / "probe" / "plugin.json").is_file(),
                  str(after_ids))
            check("R-327 the removed folder is really gone from disk",
                  not (host_plugins / "gauge").exists())
        finally:
            host.search_paths = original_paths
            host.unload()

        # ------------------------------------------------------- R-178 picker
        history_root = root / "versions"
        history_root.mkdir()
        # A *fresh* bundle: the shared ``bundled`` folder already carries demo
        # 1.0.0, and an installed build is injected into the history as a
        # ``local`` record, which would make the drop-down 3 items instead of 2.
        (history_root / "bundle").mkdir()
        source = root / "demo-catalog.json"
        atomic_json(source, {"schema": 1, "plugins": [{
            "id": "demo", "version": "1.1.0", "api": 2, "name": "Demo",
            "versions": [{"version": "1.1.0", "api": 2, "url": "http://127.0.0.1:9/demo-1.1.0.zip",
                          "sha256": "0" * 64},
                         {"version": "1.0.5", "api": 2, "url": "http://127.0.0.1:9/demo-1.0.5.zip",
                          "sha256": "1" * 64}]}]})
        picker_dialog = PluginStoreDialog(
            window, PluginStore(history_root / "data", history_root / "bundle",
                                source.as_uri()))
        picker_dialog.show()
        picker_dialog.check()
        deadline = time.perf_counter() + 10
        while picker_dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        entry = picker_dialog.rows["demo"]
        combo = entry["picker"].combo
        check("R-178 every row carries a version drop-down",
              combo.count() == 2
              and [combo.itemData(index) for index in range(combo.count())]
              == ["1.1.0", "1.0.5"])
        check("R-178 the newest build is labelled and pre-selected",
              "最新" in combo.itemText(0) and entry["picked"] == "1.1.0"
              and entry["state"] == "install" and entry["install"].text() == "安装")
        combo.setCurrentIndex(combo.findData("1.0.5"))
        check("R-178 choosing the latest first keeps a plain install",
              entry["picked"] == "1.0.5" and entry["state"] == "install"
              and entry["install"].isEnabled())
        # Pretend 1.1.0 is installed: the button must become a downgrade, and
        # picking the installed build must disable it rather than re-download.
        atomic_json(history_root / "data" / "plugins" / "demo" / "plugin.json",
                    {"id": "demo", "version": "1.1.0", "api": 2, "entry": "plugin.py"})
        (history_root / "data" / "plugins" / "demo" / "plugin.py").write_text(
            "def create(api):\n    return None\n", encoding="utf-8")
        picker_dialog.rebuild()
        entry = picker_dialog.rows["demo"]
        combo = entry["picker"].combo
        # Revised 2026-11-19: the drop-down opens on the version that is
        # *installed*, because that is the state the user is actually in.  It
        # used to jump to the older downloadable build so the button had
        # something to do, which made a current plugin look out of date.
        check("R-178 the drop-down opens on the installed version",
              entry["picked"] == "1.1.0" and entry["state"] == "same"
              and not entry["install"].isEnabled()
              and entry["install"].text() == "已安装")
        combo.setCurrentIndex(combo.findData("1.0.5"))
        check("R-178 only the older downloadable build stays actionable",
              entry["picked"] == "1.0.5" and entry["state"] == "downgrade"
              and entry["install"].text() == "降级安装" and entry["install"].isEnabled())
        combo.setCurrentIndex(combo.findData("1.1.0"))
        check("R-178 the installed version disables the action",
              entry["state"] == "same" and not entry["install"].isEnabled()
              and entry["install"].text() == "已安装"
              and entry["install"].toolTip() == "已安装这个版本，无需操作")
        combo.setCurrentIndex(combo.findData("1.0.5"))
        check("R-178 the action follows the selection",
              entry["state"] == "downgrade" and entry["install"].isEnabled())
        picker_dialog.filter_edit.setText("dem")
        check("R-178 the filter keeps matching plugins",
              list(picker_dialog.rows) == ["demo"] and picker_dialog.count.text() == "共 1 / 1 个插件")
        picker_dialog.filter_edit.setText("nope")
        check("R-178 an empty filter result says so",
              not picker_dialog.rows and picker_dialog.count.text() == "共 0 / 1 个插件")
        picker_dialog.filter_edit.setText("")
        check("R-178 clearing the filter restores every row", list(picker_dialog.rows) == ["demo"])
        entry = picker_dialog.rows["demo"]
        set_language("en")
        picker_dialog.retranslate()
        entry = picker_dialog.rows["demo"]
        check("R-178 the picker and filter translate",
              "latest" in entry["picker"].combo.itemText(0)
              # 1.1.0 is both the newest and the installed build, so it carries
              # both tags; 1.0.5 is the plain older one.
              and "installed" in entry["picker"].combo.itemText(0)
              and "1.0.5" in entry["picker"].combo.itemText(1)
              and picker_dialog.filter_edit.placeholderText() == "Search plugins")
        # The row opens on the installed build (revised 2026-11-19), so the
        # button reads 已安装/installed until the user picks another version.
        entry["picker"].combo.setCurrentIndex(
            entry["picker"].combo.findData("1.0.5"))
        check("R-178 choosing an older build offers the downgrade label",
              entry["install"].text() == "Install older"
              and entry["install"].isEnabled())
        set_language("zh")
        picker_dialog.retranslate()
        entry = picker_dialog.rows["demo"]

        # ------------------------------------------- R-180 row title hierarchy
        # The name and the description used to be two plain labels of the same
        # weight and colour, so a card read as one run-on paragraph.  The name
        # is now ``PluginName`` (bold, one pixel larger, INK) and a hairline
        # ``PluginRule`` separates it from the ``PluginTagline`` description.
        from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

        from omnicom.ui import theme

        name_label = entry["widget"].findChild(QLabel, "PluginName")
        tagline = entry["widget"].findChild(QLabel, "PluginTagline")
        rule = entry["widget"].findChild(QFrame, "PluginRule")
        check("R-180 store rows tag the name, the rule and the description",
              name_label is not None and tagline is not None and rule is not None)
        # A QSS ``font-weight`` only reaches ``QFont`` once the widget has been
        # polished; reading the font here therefore proves the style applies,
        # not merely that the rule exists in a string.
        check("R-180 the plugin name is bold and a step larger",
              name_label.font().weight() == 700
              and tagline.font().weight() < 600
              and name_label.font().pixelSize() > tagline.font().pixelSize())
        row_layout = entry["widget"].layout()
        order = [row_layout.itemAt(index).widget().objectName()
                 if row_layout.itemAt(index).widget() else "layout"
                 for index in range(row_layout.count())]
        # The name lives in the header QHBoxLayout, so the top level never names
        # it: assert that the header *itself* is the first thing in the row and
        # that the rule and the tagline follow it in that order.
        header_item = row_layout.itemAt(0)
        header_holds_name = (header_item.layout() is not None
                             and header_item.layout().indexOf(name_label) >= 0)
        check("R-180 the name is separated from the description",
              header_holds_name
              and order.index("PluginRule") < order.index("PluginTagline"))
        check("R-180 the separator is a 1 px hairline in the theme line colour",
              rule.height() == 1
              and rule.minimumHeight() == 1
              and rule.maximumHeight() == 1
              and theme.LINE_SOFT in rule.styleSheet())
        # Order in the layout is not the same thing as separation on screen: a
        # zero-height header or a collapsed gap would still "pass" the index
        # test.  Measure the rendered boxes instead.
        from PySide6.QtCore import QPoint

        picker_dialog.resize(620, 620)
        app.processEvents()
        row = entry["widget"]
        name_bottom = name_label.mapTo(row, QPoint(0, name_label.height())).y()
        rule_top = rule.mapTo(row, QPoint(0, 0)).y()
        tagline_top = tagline.mapTo(row, QPoint(0, 0)).y()
        check("R-180 the rendered boxes leave a gap above and below the rule",
              name_bottom <= rule_top <= tagline_top
              and tagline_top - name_bottom >= 2 * rule.height()
              and name_label.height() > 0 and tagline.height() > 0)
        sheet = theme.app_stylesheet()
        name_rule = sheet.split("QLabel#PluginName", 1)[-1].split("}", 1)[0]
        tagline_rule = sheet.split("QLabel#PluginTagline", 1)[-1].split("}", 1)[0]
        check("R-180 the title hierarchy is a theme token, not a local colour",
              "font-weight: 700" in name_rule
              and theme.INK in name_rule
              and theme.DIM in tagline_rule)

        # -------------------------------------------------- R-181 no stray window
        # 检查更新 used to flash a blank little window.  Nothing opened a dialog:
        # the row badges were built with ``parent=None`` and ``show_text()``
        # called ``show()`` *before* ``addWidget()`` reparented them, so each
        # one became a real top-level window for a frame (R-181).  Assert both
        # halves of the contract: no badge inside the store is a window, and a
        # badge that is shown before it has a parent stays hidden until then.
        from PySide6.QtWidgets import QWidget

        from omnicom.ui import widgets as widgets_module

        stray = [badge for badge in picker_dialog.findChildren(widgets_module.Badge)
                 if badge.isWindow() or badge.parent() is None]
        check("R-181 no store badge is a top-level window",
              not stray)
        # The update dot is a plain ``QWidget``, not a ``Badge``: checking only
        # the Badge type let an unparented 8x8 ``QWidget`` become a visible
        # top-level window on every rebuild, which the user saw as a small blank
        # window flashing after 启用/安装/卸载 (R-181).
        dots = picker_dialog.findChildren(QWidget, "PluginUpdateDot")
        check("R-181 every store update dot has a parent",
              bool(dots) and all(dot.parent() is not None and not dot.isWindow()
                                 for dot in dots),
              f"{len(dots)} dots")
        # ``parent()`` alone cannot prove this: a parentless widget becomes a
        # top-level window while ``setVisible(True)`` runs, and ``addWidget()``
        # re-parents it later, leaving no trace to inspect afterwards.  Only a
        # widget that is *created with its parent* never shows as a window, so
        # the regression is reproduced on a fresh instance and both orderings
        # are compared (that is what the user saw: a blank window flashing).
        orphan = QWidget()
        orphan.setFixedSize(8, 8)
        orphan.setVisible(True)
        orphan_was_window = orphan.isWindow()
        row_probe = QFrame()
        QVBoxLayout(row_probe).addWidget(orphan)
        fixed_dot = QWidget(row_probe)
        fixed_dot.setFixedSize(8, 8)
        fixed_dot.setVisible(True)
        fixed_was_window = fixed_dot.isWindow()
        row_probe.deleteLater()
        check("R-181 a dot created without a parent really flashes as a window",
              orphan_was_window and not fixed_was_window,
              f"orphan={orphan_was_window} parented={fixed_was_window}")
        check("R-181 store dots are created with their row, not re-parented later",
              all(dot.parent() is not None and not dot.isWindow() for dot in dots))
        # The only construction that *cannot* flash is "parent in the
        # constructor": assert the source keeps it that way, because the
        # runtime state after the rebuild looks identical either way.
        source = (ROOT / "omnicom" / "ui" / "plugin_store.py").read_text(encoding="utf-8")
        check("R-181 the update dot is constructed with its row as parent",
              'badge = QWidget(row)' in source
              and 'badge = QWidget()' not in source)
        detached = widgets_module.Badge()
        detached.show_text("R-181")
        never_shown = not detached.isVisible() and detached.isWindow()
        detached_row = QFrame()
        detached_layout = QVBoxLayout(detached_row)
        detached_layout.addWidget(detached)
        deferred = detached._wants_showing and not detached.isWindow()
        check("R-181 a parentless badge defers show() until it is inserted",
              never_shown and deferred)
        detached_row.deleteLater()
        picker_dialog.close()


def sustained_chart_checks(app, chart) -> list:
    from PySide6.QtCore import QTimer

    reports = []

    def percentile(values, fraction=0.95):
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)], 2) if ordered else 0

    chart.page_ready = chart.view is not None
    chart.show()
    chart.resize(1350, 900)
    check("R-321 sustained workload uses the live WebEngine page",
        _js(app, chart, "omniDashboard.ready()") is True)
    for cards, drawn in ((4, 300), (6, 300), (6, 5000), (24, 5000)):
        chart.timer.stop()
        timeouts_before = chart.ack_timeouts
        chart.clear_data()
        chart.seconds.setValue(0)
        chart.points.setValue(0)
        chart.drawn.setValue(drawn)
        chart.layout_data = {"widgets": [
            {"id": f"load{index}", "kind": "line", "channel": f"CH{index % 6}",
             "title": f"CH{index % 6}", "x": (index % 3) * 420,
             "y": (index // 3) * 320, "w": 400, "h": 300}
            for index in range(cards)]}
        chart._push_layout()
        chart._push_points()
        _js(app, chart, "document.getElementById('canvas').scrollTo(0,0)")
        for index in range(5000):
            chart.feed(index * 5, ("[OmniCOM-chart] " + " ".join(
                f"CH{channel}={index}" for channel in range(6)) + "\n").encode())
        chart.request_resync()
        chart.flush_live()
        deadline = time.perf_counter() + 10
        while chart._pending_ack and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.001)
        check(f"R-321 load {cards}/{drawn} initial frame completes", not chart._pending_ack)
        heartbeat = []
        completed = []
        interactions = []
        previous = [time.perf_counter()]
        sequence = [5000]
        stalls = []
        stop_watch = threading.Event()
        main_thread = threading.get_ident()

        def watch_stalls(stop_watch=stop_watch, previous=previous, stalls=stalls, main_thread=main_thread):
            while not stop_watch.wait(0.02):
                if time.perf_counter() - previous[0] > 0.08 and len(stalls) < 6:
                    frame = sys._current_frames().get(main_thread)
                    if frame is not None:
                        stalls.append("".join(traceback.format_stack(frame)[-8:]))

        def beat(heartbeat=heartbeat, previous=previous):
            now = time.perf_counter()
            heartbeat.append((now - previous[0]) * 1000)
            previous[0] = now

        def produce(sequence=sequence):
            for _ in range(4):
                index = sequence[0]
                chart.feed(index * 5, ("[OmniCOM-chart] " + " ".join(
                    f"CH{channel}={index}" for channel in range(6)) + "\n").encode())
                sequence[0] += 1

        def acknowledged(epoch, completed=completed):
            if epoch == chart._acked_epoch:
                completed.append((time.perf_counter() - chart._ack_started) * 1000)

        chart.bridge.acked.connect(acknowledged)
        pulse = QTimer()
        pulse.timeout.connect(beat)
        pulse.start(10)
        producer = QTimer()
        producer.timeout.connect(produce)
        producer.start(20)
        _js(app, chart, "window.loadGaps=[];window.loadTick=performance.now();window.loadTimer=setInterval(function(){var n=performance.now();loadGaps.push(n-loadTick);loadTick=n;},10)")
        chart.timer.start()
        watcher = threading.Thread(target=watch_stalls, daemon=True)
        watcher.start()
        started = time.perf_counter()
        next_interaction = started + 0.5
        added = False
        try:
            while time.perf_counter() - started < 5:
                app.processEvents()
                now = time.perf_counter()
                if now >= next_interaction:
                    before = time.perf_counter()
                    _js(app, chart, "document.getElementById('canvas').scrollTop=" +
                        ("320" if cards > 6 and not added else "0") + ";performance.now()")
                    interactions.append((time.perf_counter() - before) * 1000)
                    next_interaction = now + 0.5
                if not added and now - started > 2:
                    before = time.perf_counter()
                    chart.add_widget({"kind": "value", "channel": "CH0", "title": "load add"})
                    menu = chart.build_menu()
                    menu.deleteLater()
                    interactions.append((time.perf_counter() - before) * 1000)
                    added = True
                time.sleep(0.001)
        finally:
            producer.stop()
            pulse.stop()
            stop_watch.set()
            watcher.join()
            chart.bridge.acked.disconnect(acknowledged)
        page_gaps = json.loads(_js(app, chart, "clearInterval(window.loadTimer);JSON.stringify(window.loadGaps)") or "[]")
        deadline = time.perf_counter() + 10
        while (chart._pending_ack or chart.has_fresh_data()) and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.001)
        chart.timer.stop()
        diagnostic = json.loads(_js(app, chart, "omniDashboard.diagnostics()"))
        report = {"cards": cards, "drawn": drawn, "duration_s": 5,
                  "received_per_channel": sequence[0] - 5000,
                  "completed_batches": len(completed),
                  "batch_p95_ms": percentile(completed),
                  "gui_heartbeat_p95_ms": percentile(heartbeat),
                  "gui_heartbeat_max_ms": round(max(heartbeat, default=0), 2),
                  "page_heartbeat_p95_ms": percentile(page_gaps),
                  "page_heartbeat_max_ms": round(max(page_gaps, default=0), 2),
                  "interaction_p95_ms": percentile(interactions),
                  "ack_timeouts": chart.ack_timeouts - timeouts_before,
                  "stalls": stalls,
                  "diagnostic": diagnostic}
        reports.append(report)
        atomic_json(ROOT / ".artifacts" / "chart-sustained.json", reports)
        print("CHART LOAD", json.dumps(report, ensure_ascii=False))
        check(f"R-321 load {cards}/{drawn} continues completing frames", len(completed) >= 10)
        check(f"R-321 load {cards}/{drawn} history stays bounded and reaches newest sample",
              len(diagnostic["retained"]) == min(cards, 6)
              and all(item["count"] == 5000 and item["last"][1] == sequence[0] - 1
                      for item in diagnostic["retained"]))
        check(f"R-320 load {cards}/{drawn} interaction p95 stays below 200ms",
              report["interaction_p95_ms"] < 200, str(report))
        chart.grab().save(str(ROOT / ".artifacts" / f"chart-load-{cards}-{drawn}.png"))
    return reports


def benchmark(app, chart, parser_class) -> dict:
    """Parser throughput and sustained, acknowledged rendering workloads."""
    results = {"python": sys.version, "platform": sys.platform,
               "parser": []}
    for channels in (2, 8, 32):
        line = ("[OmniCOM-chart] "
                + " ".join(f"CH{index}={index + 0.125}" for index in range(channels))
                + "\n").encode()
        payload = line * 1000
        parser = parser_class()
        started = time.perf_counter()
        for _ in range(20):
            parser.feed(payload)
        elapsed = time.perf_counter() - started
        results["parser"].append({"channels": channels, "line_bytes": len(line),
                                  "frames_per_s": 20000 / elapsed,
                                  "MB_per_s": len(payload) * 20 / elapsed / 1e6})
    results["sustained"] = sustained_chart_checks(app, chart)
    return results

def chart_stream_checks(app, chart, api_like=None) -> None:
    """R-301/R-302/R-303: bounded frames, log replay, and the hex viewer.

    These three are one family: they all answer "what happens when the data is
    bigger than one screen".  The dashboard used to re-send the whole retained
    window 30 times a second, which is why a long session lagged further and
    further behind the log; replay and the hex viewer are the two new ways the
    user wants to look at data that is already on disk.
    """
    panel = chart
    chart_mod = sys.modules[type(panel).__module__]
    panel.clear_widgets()
    panel.add_widget({"kind": "line", "channel": "A", "channels": ["A"],
                      "title": "A", "x": 0, "y": 0, "w": 320, "h": 200})
    sent = []
    original = panel._call_js
    panel._call_js = lambda name, payload: sent.append((name, payload))
    try:
        panel.page_ready = True
        panel.begin_replay("replay.log")

        # ---- R-301: the frame cap is honoured and cannot go below the floor
        panel.points.setValue(150)
        check("R-301 曲线点数按设置生效", panel._capacity() == 150,
              panel._capacity())
        for index in range(400):
            panel.replay_line(index * 0.1, f"[OmniCOM-chart] A={index}")
        check("R-301 超过点数后只保留最新的帧",
              len(panel.samples["A"]) == 150, len(panel.samples["A"]))
        newest = list(panel.samples["A"])[-1]
        check("R-301 保留的是最新的一帧而不是最旧的",
              newest[1] == 399.0, newest)
        panel.points.setValue(1)
        check("R-301 过小的点数被夹到下限",
              panel._capacity() == 100, panel._capacity())
        panel.points.setValue(0)

        # ---- R-302: replay preserves the file's own timestamps
        panel.begin_replay("session.log")
        panel.replay_line(0.5, "收←◇[OmniCOM-chart] A=9")
        panel.replay_line(1.5, "收←◇[OmniCOM-chart] A=10")
        stamps = [stamp for stamp, _ in panel.samples["A"]]
        check("R-302 重放使用日志里的原始时间戳",
              stamps == [0.5, 1.5], stamps)
        check("R-302 重放复用同一个解析器与通道缓冲",
              panel.replayed_lines == 2 and panel.values["A"] == 10.0,
              f"{panel.replayed_lines} {panel.values.get('A')}")

        # ---- the delta frame carries only the tail, and back-pressure holds it
        panel.request_resync()
        panel.flush_live()
        name, payload = sent[-1]
        check("R-301 首帧是全量历史",
              name == "setValues" and payload["append"] is False, name)
        busy = len(sent)
        panel.flush_live()
        check("R-301 未收到确认时不再堆积下一帧",
              len(sent) == busy and panel.throttled >= 1,
              f"{len(sent)} {panel.throttled}")
        panel._on_acked(payload["epoch"])
        panel.replay_line(2.5, "[OmniCOM-chart] A=11")
        panel.flush_live()
        name, payload = sent[-1]
        check("R-301 确认之后只发送新增的尾部",
              name == "setValues"
              and payload["append"] is True
              and payload["history"]["A"] == [[2.5, 11.0]],
              payload["history"])

        # ---- R-305: an idle dashboard must not repaint, and the frame interval
        # adapts to what the renderer reported.  Without this the 30 fps loop
        # kept calling setOption (measured 22 ms for 5000 points) and the window
        # stuttered while the log ran on (user, 2026-11-19).
        panel._on_acked(payload["epoch"])
        idle = len(sent)
        panel.refresh()
        check("R-305 没有新数据时刷新不发送任何帧",
              len(sent) == idle, f"{len(sent)} vs {idle}")
        panel._on_paint_cost(0.0)
        check("R-305 页面很快时刷新间隔回到 33 ms",
              panel.frame_ms == 33, panel.frame_ms)
        panel._on_paint_cost(40.0)
        check("R-305 页面耗时 40 ms 时节拍包含绘制时间而非重复等待",
              panel.frame_ms == 48 and panel.timer.interval() == panel.frame_ms,
              f"{panel.frame_ms} / {panel.timer.interval()}")
        panel._on_paint_cost(400.0)
        check("R-305 极慢时不超过 200 ms 上限",
              panel.frame_ms == 200, panel.frame_ms)
        panel._on_paint_cost(0.0)
        panel.begin_replay("fresh.log")
        panel.replay_line(0.1, "[OmniCOM-chart] A=1")
        panel.refresh()
        check("R-305 有新数据时仍然发帧",
              len(sent) > idle, f"{len(sent)} vs {idle}")

        option = _js(app, chart, """JSON.stringify((() => {
          const series = omniDashboard ? true : true;
          const host = document.querySelector('#canvas .card .plot');
          const inst = echarts.getInstanceByDom(host);
          const s = (inst.getOption().series || [])[0] || {};
          return {sampling: s.sampling || null,
                  progressive: s.progressive || null,
                  progressiveThreshold: s.progressiveThreshold || null,
                  large: !!s.large};
        })())""")
        cfg = json.loads(option) if option else {}
        check(f"R-336 曲线不重新抽取历史代表点 ({cfg})",
              cfg.get("sampling") == "none", cfg.get("sampling"))
        check(f"R-307 曲线开启分帧绘制 progressive ({cfg})",
              cfg.get("progressive") == 800
              and cfg.get("progressiveThreshold") == 1500, cfg)
        check(f"R-307 大数据曲线仍走 large 批渲染 ({cfg})",
              cfg.get("large") is True, cfg.get("large"))
        stability = _js(app, chart, """JSON.stringify((() => {
                    const host = document.querySelector('#canvas .card .plot');
                    const inst = echarts.getInstanceByDom(host);
                    const saved = inst.getOption();
                    const width = inst.getWidth(), height = inst.getHeight();
                    const results = [];
                    try {
                        for (const size of [320, 920]) {
                            inst.resize({width: size, height: 250});
                            for (const offset of [0, 1, 300]) {
                                const series = [0, 1, 2, 3].map(channel => Object.assign({}, saved.series[0], {
                                    id: 'stability-' + channel,
                                    name: 'stability-' + channel,
                                    data: Array.from({length: 5000}, (_, index) => {
                                        const tick = index + offset;
                                        return [tick / 10, Math.sin(tick / 3 + channel) +
                                            0.35 * Math.sin(tick / 1.3) + (tick % 137 === 0 ? 3 : 0)];
                                    })
                                }));
                                const started = performance.now();
                                inst.setOption({series: series}, {replaceMerge: ['series'], lazyUpdate: false});
                                const elapsed = performance.now() - started;
                                const exact = series.every((raw, seriesIndex) => {
                                    const drawn = inst.getModel().getSeriesByIndex(seriesIndex).getData();
                                    if (drawn.count() !== raw.data.length) { return false; }
                                    return raw.data.every((row, index) =>
                                        drawn.get('x', index) === row[0] && drawn.get('y', index) === row[1]);
                                });
                                const canvas = host.querySelector('canvas');
                                const image = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
                                let ink = 0;
                                for (let index = 3; index < image.length; index += 4) {
                                    if (image[index] > 8) { ink += 1; }
                                }
                                results.push({width: size, offset: offset, exact: exact, ink: ink, ms: elapsed});
                            }
                        }
                    } finally {
                        inst.resize({width: width, height: height});
                        inst.setOption(saved, {notMerge: true, lazyUpdate: false});
                    }
                    return results;
                })())""")
        frames = json.loads(stability) if stability else []
        check("R-336 两种宽度下四通道5000点跨容量滑动，绘制点逐个等于原始数据",
              len(frames) == 6 and all(frame["exact"] for frame in frames), frames)
        check("R-336 复杂波形在真实画布中非空",
              len(frames) == 6 and all(frame["ink"] > 1000 for frame in frames), frames)
    finally:
        panel._call_js = original
        panel.page_ready = False

    # ---- R-312: an unanswered ack must never freeze the dashboard.
    # Opening a HEX file leaves this tab hidden; Chromium stops compositing it,
    # so a batch can go unacknowledged.  Before the deadline the panel throttles
    # (that is the intended back-pressure), after it the panel must redraw
    # instead of waiting for ever - the reported "看完 HEX 之后曲线就不刷了".
    original = panel._call_js
    shipping: list[dict] = []
    panel._call_js = lambda name, payload: shipping.append(payload)
    try:
        panel.page_ready = True
        panel.layout_data = {"widgets": [
            {"id": "ack1", "kind": "line", "channel": "AA", "x": 16, "y": 16,
             "w": 480, "h": 256},
            {"id": "ack2", "kind": "line", "channel": "BB", "x": 512, "y": 16,
             "w": 480, "h": 256}]}
        panel.samples.clear()
        panel._sent.clear()
        for index in range(4):
            panel.feed(5000.0 + index, b"[OmniCOM-chart] AA=%d BB=%d\n"
                       % (index, index * 2))
        panel.show()
        app.processEvents()
        panel.refresh()
        panel._on_acked(panel._pending_ack)
        panel.flush()

        panel._on_acked(panel._pending_ack)
        panel.feed(5100.0, b"[OmniCOM-chart] AA=7 BB=14\n")
        panel.refresh()
        before = len(shipping)
        panel.feed(5200.0, b"[OmniCOM-chart] AA=8 BB=16\n")
        throttled_before = panel.throttled
        for _ in range(3):
            panel.refresh()
        check("R-312 未确认期间按背压跳过，不堆积帧",
              len(shipping) == before and panel.throttled > throttled_before,
              f"{len(shipping) - before} / {panel.throttled}")

        panel._ack_started = (time.perf_counter()
                              - (chart_mod.ACK_TIMEOUT_S + 0.1))
        panel.feed(5300.0, b"[OmniCOM-chart] AA=9 BB=18\n")
        panel.refresh()
        check("R-312 确认超时后自动重同步，曲线恢复刷新",
              len(shipping) == before + 1 and panel.ack_timeouts == 1,
              f"{len(shipping) - before} timeouts={panel.ack_timeouts}")
        check("R-312 超时恢复送完整窗口而不是空增量",
              shipping[-1].get("append") is False
              and set(shipping[-1].get("history", {})) == {"AA", "BB"},
              str({k: len(v) for k, v in shipping[-1].get("history", {}).items()}))

        panel._on_acked(panel._pending_ack)
        panel._was_visible = False
        panel.feed(5400.0, b"[OmniCOM-chart] AA=10 BB=20\n")
        before = len(shipping)
        panel.refresh()
        check("R-312 从其它标签页切回来会重发完整窗口",
              len(shipping) > before and shipping[-1].get("append") is False,
              str(shipping[-1].get("append")))

        # ---- R-314: adding a card must not re-ship the retained history.
        # Every add used to call request_resync(), which re-serialised the whole
        # window (83 KB for ten channels x 5000 points).  On a long-running
        # session that is what made 添加控件 slow or apparently dead.
        panel._on_acked(panel._pending_ack)
        panel.feed(5500.0, b"[OmniCOM-chart] AA=11 BB=22\n")
        panel.refresh()
        panel._on_acked(panel._pending_ack)
        resyncs_before = panel.resyncs
        resync_flag_before = panel._resync_needed
        calls = []
        panel._call_js = lambda name, payload: calls.append(name)
        added = panel.add_widget({"kind": "line", "channel": "AA",
                                  "title": "another AA"})
        panel._call_js = original
        check("R-314 添加已推送通道的控件不触发全量重发",
              panel.resyncs == resyncs_before
              and panel._resync_needed == resync_flag_before
              and calls == ["addWidget"],
              f"{calls} resyncs+{panel.resyncs - resyncs_before}")
        check("R-314 新卡片立即入库且不需要额外数据帧",
              any(widget["id"] == added
                  for widget in panel.layout_data.get("widgets", [])),
              str(added))

        # A channel the page has never seen still asks for one full window.
        panel.feed(5600.0, b"[OmniCOM-chart] BRAND=1\n")
        panel._on_acked(panel._pending_ack)
        panel.request_resync()               # clean state, as after a frame
        panel.flush()
        check("R-314 全量帧已发送时 resync 标记已清除",
              not panel._resync_needed, str(panel._resync_needed))
        panel.add_widget({"kind": "line", "channel": "BRAND", "title": "brand"})
        check("R-314 绑定全新通道时仍请求一次完整历史",
              panel._resync_needed, str(panel._resync_needed))

        # ---- R-315: the toolbar is compact, the rest lives in one menu.
        toolbar = panel.layout().itemAt(0).layout()
        bar_widgets = [toolbar.itemAt(index).widget()
                       for index in range(toolbar.count())
                       if toolbar.itemAt(index).widget() is not None]
        bar_buttons = [widget.text() for widget in bar_widgets
                       if hasattr(widget, "text")]
        check("R-315 顶栏只剩暂停、清空数据、菜单三个按钮",
              len(bar_buttons) == 3, str(bar_buttons))
        check("R-315 顶栏不再有数字框",
              not any(widget.__class__.__name__ == "QSpinBox"
                      for widget in bar_widgets),
              str([widget.__class__.__name__ for widget in bar_widgets]))
        menu = panel.build_menu()
        from PySide6.QtWidgets import QLabel as _MenuLabel

        entries = [action.text() for action in menu.actions()]
        for action in menu.actions():
            holder = (action.defaultWidget()
                      if hasattr(action, "defaultWidget") else None)
            if holder is not None:
                label = holder.findChild(_MenuLabel)
                if label is not None:
                    entries.append(label.text())
        needed = ("保存截图", "导出 CSV", "导出看板", "导入看板", "显示设置",
                  "时间窗 / 秒", "曲线点数", "展示帧数")
        check("R-315 菜单包含截图/CSV/导入导出/显示设置",
              all(item in entries for item in needed), str(entries))
        check("R-339 清空看板在菜单里、不在顶栏",
              "清空看板" in entries
              and "清空看板" not in bar_buttons
              and panel.clear_widgets_button not in bar_widgets,
              f"{entries} / {bar_buttons}")

        # ---- R-338: 清空数据 is a first-class toolbar action.
        # It must clear the samples/curves and keep every card, which is the
        # opposite contract of 清空控件.  Click its real button, then prove the
        # page's retained series is empty while the cards are still mounted.
        check("R-338 顶栏有清空数据按钮",
              panel.clear_data_button.text() == "清空数据"
              and panel.clear_data_button.isVisibleTo(panel),
              panel.clear_data_button.text())
        cards_before = len(_describe(app, panel)["widgets"])
        panel.feed(6100.0, b"[OmniCOM-chart] AA=7\n")
        panel.request_resync()
        panel.flush()
        _wait(app, panel, cards_before)
        had_data = json.loads(_js(app, panel, """JSON.stringify(
          JSON.parse(omniDashboard.diagnostics()).retained)""") or "[]")
        panel.clear_data_button.click()
        app.processEvents()
        panel.request_resync()
        panel.flush()
        _wait(app, panel, cards_before)
        retained = json.loads(_js(app, panel, """JSON.stringify(
          JSON.parse(omniDashboard.diagnostics()).retained)""") or "[]")
        counts = [item.get("count") for item in retained]
        check(f"R-338 清空数据清掉曲线但保留卡片 ({had_data} -> {retained})",
              cards_before > 0
              and len(_describe(app, panel)["widgets"]) == cards_before
              and bool(had_data) and all(not number for number in counts),
              f"{len(counts)} {counts}")

        # ---- R-313: a small window must be able to scroll to every card.
        # ``#canvas`` is the scroll viewport and ``#surface`` the content box;
        # before this the surface was ``overflow: hidden`` and a board wider
        # than the window was simply unreachable.  Overflow is created on
        # purpose here: new cards are centred now (R-316), so simply resizing
        # would not produce a card outside the view.
        panel._call_js = original
        panel._push_layout()
        actual = _describe(app, panel)
        far_id = actual["widgets"][0]["id"]
        _js(app, panel, "omniDashboard.updateWidget(" + json.dumps(
            {"id": far_id, "x": 2400, "y": 1600, "w": 480, "h": 256}) + ")")
        panel.resize(640, 380)
        app.processEvents()
        deadline = time.perf_counter() + 3
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.02)
        panel._call_js = original
        canvas_metrics = json.loads(_js(app, panel, """(function () {
          var c = document.getElementById('canvas');
          var s = document.getElementById('surface');
          return JSON.stringify({
            sw: c.scrollWidth, sh: c.scrollHeight,
            cw: c.clientWidth, ch: c.clientHeight,
            ox: getComputedStyle(c).overflowX,
            oy: getComputedStyle(c).overflowY,
            surface: !!s,
            cards: document.querySelectorAll('#surface .card').length});
        })()""") or "{}")
        check("R-313 小窗口下画布可横向滚动",
              canvas_metrics.get("ox") == "auto"
              and canvas_metrics.get("sw", 0) > canvas_metrics.get("cw", 1),
              str(canvas_metrics))
        check("R-313 小窗口下画布可纵向滚动",
              canvas_metrics.get("oy") == "auto"
              and canvas_metrics.get("sh", 0) > canvas_metrics.get("ch", 1),
              str(canvas_metrics))
        check("R-313 卡片仍挂在可滚动的内容层上",
              canvas_metrics.get("surface") is True
              and canvas_metrics.get("cards", 0) > 0, str(canvas_metrics))

        # ---- R-316: no stray number box on the toolbar, and new cards land in
        # the middle of what the user is looking at (they used to tile by index,
        # so the tenth card "flew away" several screens down).
        from PySide6.QtWidgets import QSpinBox as _MenuSpinBox

        visible_boxes = [box for box in panel.findChildren(_MenuSpinBox)
                         if box.isVisible()]
        check("R-316 未打开菜单时顶栏没有可见的数字框",
              not visible_boxes, str([box.value() for box in visible_boxes]))
        panel.build_menu().close()
        app.processEvents()
        check("R-316 菜单关闭后数字设置仍存活",
              panel.drawn.value() > 0 and panel.points is not None,
              f"drawn={panel.drawn.value()}")
        panel.resize(1000, 620)
        app.processEvents()
        centred = []
        for attempt in range(3):
            _js(app, panel, "document.getElementById('canvas').scrollTo(" +
                str(attempt * 200) + "," + str(attempt * 120) + ")")
            panel._on_acked(panel._pending_ack)
            new_id = panel.add_widget({"kind": "line", "channel": "AA",
                                       "title": "centre"})
            deadline = time.perf_counter() + 8
            while time.perf_counter() < deadline:
                app.processEvents()
                time.sleep(0.01)
                where = json.loads(_js(app, panel, """(function () {
              var c = document.getElementById('canvas');
              var n = document.querySelector('#surface .card[data-id="%s"]');
              if (!n) { return "{}"; }
              return JSON.stringify({x: parseInt(n.style.left, 10),
                                     y: parseInt(n.style.top, 10),
                                     w: parseInt(n.style.width, 10),
                                     h: parseInt(n.style.height, 10),
                                     vw: c.clientWidth, vh: c.clientHeight,
                                     left: c.scrollLeft, top: c.scrollTop});
                })()""".replace("%s", new_id)) or "{}")
                if where and abs(where["x"] + where["w"] / 2 - where["left"] - where["vw"] / 2) <= 24 and abs(where["y"] + where["h"] / 2 - where["top"] - where["vh"] / 2) <= 24:
                    break
            centred.append(where)
        offscreen = [spot for spot in centred
                     if not (spot.get("x", 0) >= spot.get("left", 0)
                             and spot.get("y", 0) >= spot.get("top", 0)
                             and spot.get("x", 0) + spot.get("w", 0)
                             <= spot.get("left", 0) + spot.get("vw", 1)
                             and spot.get("y", 0) + spot.get("h", 0)
                             <= spot.get("top", 0) + spot.get("vh", 1))]
        check("R-316 新控件落在可视区内（不再飞到列表底部）",
              bool(centred) and not offscreen, str(centred))
        check("R-316 每张新卡片均在当前滚动视口中心附近",
              len(centred) == 3 and all(
                abs(spot["x"] + spot["w"] / 2 - spot["left"] - spot["vw"] / 2) <= 24
                and abs(spot["y"] + spot["h"] / 2 - spot["top"] - spot["vh"] / 2) <= 24
                for spot in centred), str(centred))

        # ---- R-317: a gauge must not inherit the line option.
        panel._on_acked(panel._pending_ack)
        panel.feed(7000.0, b"[OmniCOM-chart] AA=5\n")
        gauge_id = panel.add_widget({"kind": "gauge", "channel": "AA",
                                     "title": "dial"})
        deadline = time.perf_counter() + 8
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.01)
            if _js(app, panel, "!!document.querySelector('#surface .card"
                               "[data-id=\\'" + gauge_id + "\\'] .plot canvas')"):
                break
        gauge_shape = json.loads(_js(app, panel, """(function () {
          var host = document.querySelector('#surface .card[data-id="%s"] .plot');
          var inst = echarts.getInstanceByDom(host);
          var option = inst ? inst.getOption() : null;
          return JSON.stringify({
            xAxis: option ? (option.xAxis || []).length : -1,
            yAxis: option ? (option.yAxis || []).length : -1,
            dataZoom: option ? (option.dataZoom || []).length : -1,
            kinds: option ? (option.series || []).map(function (s) {
              return s.type; }) : []});
        })()""".replace("%s", gauge_id)) or "{}")
        check("R-317 仪表没有 X/Y 轴与缩放拖动条",
              gauge_shape.get("xAxis") == 0
              and gauge_shape.get("yAxis") == 0
              and gauge_shape.get("dataZoom") == 0
              and gauge_shape.get("kinds") == ["gauge"], str(gauge_shape))

        # ---- R-318: a filled curve window must keep moving.
        # ``drawnBuffer`` used to derive "how many rows are already merged" from
        # the *length difference*; once both the source and the drawn buffer sat
        # at 展示帧数 that difference is 0 for ever, so a live curve froze as
        # soon as it had been running long enough - and switching tabs was
        # merely when the user noticed.
        panel._on_acked(panel._pending_ack)
        for index in range(360):
            panel.feed(9000.0 + index * 10, b"[OmniCOM-chart] AA=%d\n" % index)
        # Pin a curve card first: earlier steps may have cleared the board.
        panel.clear_widgets()
        panel.add_widget({"kind": "line", "channel": "AA", "title": "roll"})
        panel.request_resync()
        panel.flush()
        app.processEvents()
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.01)
            if _js(app, panel, "JSON.stringify(omniDashboard.describe())"):
                break
        panel._on_acked(panel._pending_ack)
        tail_before = json.loads(_js(app, panel, """(function () {
          var c = document.getElementById('canvas');
          var n = document.querySelector('#surface .card .plot');
          var i = n ? echarts.getInstanceByDom(n) : null;
          var o = i ? i.getOption() : null;
          var s = o && o.series && o.series[0] ? o.series[0].data : null;
          return JSON.stringify({n: s ? s.length : -1,
                                 last: s && s.length ? s[s.length - 1] : null});
        })()""") or "{}")
        for index in range(360, 460):
            panel.feed(9000.0 + index * 10, b"[OmniCOM-chart] AA=%d\n" % index)
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.02)
        tail_after = json.loads(_js(app, panel, """(function () {
          var n = document.querySelector('#surface .card .plot');
          var i = n ? echarts.getInstanceByDom(n) : null;
          var o = i ? i.getOption() : null;
          var s = o && o.series && o.series[0] ? o.series[0].data : null;
          return JSON.stringify({n: s ? s.length : -1,
                                 last: s && s.length ? s[s.length - 1] : null});
        })()""") or "{}")
        check("R-318 曲线窗口填满后仍继续前进（切标签页后不冻结）",
              tail_after.get("last") is not None
              and tail_before.get("last") is not None
              and tail_after["last"][0] > tail_before["last"][0],
              f"{tail_before.get('last')} -> {tail_after.get('last')}")
        check("R-318 页面对已填满窗口的回传确认仍然到达",
              panel._pending_ack == 0,
              str(panel._pending_ack) + " " + str(_js(app, panel, "omniDashboard.diagnostics()")))

        # ---- R-320: the per-frame drawing call must not block the renderer.
        # Measured with four curve cards: a synchronous ``setOption`` costs
        # 34-42 ms per frame, the lazy form 1.4-2.9 ms.  The live path must
        # therefore pass ``lazyUpdate`` and must not rebuild the whole option.
        lazy_flags = []
        script = """(function () {
          var host = document.querySelector('#surface .card .plot');
          var inst = host ? echarts.getInstanceByDom(host) : null;
          if (!inst) { return "no chart"; }
          window.__omniLazy = [];
          var real = inst.setOption.bind(inst);
          inst.setOption = function (option, settings) {
            window.__omniLazy.push(!!(settings && settings.lazyUpdate));
            return real(option, settings);
          };
          return "hooked";
        })()"""
        check("R-320 能挂上 setOption 观察器", _js(app, panel, script) == "hooked")
        panel._on_acked(panel._pending_ack)
        for index in range(560, 600):
            panel.feed(9000.0 + index * 10, b"[OmniCOM-chart] AA=%d\n" % index)
        deadline = time.perf_counter() + 8
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.01)
        lazy_flags = json.loads(_js(app, panel, "JSON.stringify(window.__omniLazy)")
                                or "[]")
        check("R-320 实时帧都走懒更新（不阻塞渲染线程）",
              bool(lazy_flags) and all(lazy_flags), str(lazy_flags))
    finally:
        panel._call_js = original
        panel.page_ready = False
        panel.hide()

    # ``module`` is also the name used for the hex viewer below; keep that one.
    import importlib.util

    # Mirror how ``PluginHost._instantiate`` loads a folder: the module is
    # registered as a *package* (``submodule_search_locations``), which is what
    # makes the plugin's own ``from .text import TEXT`` work.  Loading it as a
    # bare module made that relative import fail (R-322).
    hex_folder = ROOT / "plugins" / "hexview"
    spec = importlib.util.spec_from_file_location(
        "omnicom_plugin_hexview", hex_folder / "plugin.py",
        submodule_search_locations=[str(hex_folder)])
    module = importlib.util.module_from_spec(spec)
    # ``from .text import TEXT`` resolves through ``sys.modules``, so the module
    # has to be registered before it executes - same as the host loader does.
    sys.modules["omnicom_plugin_hexview"] = module
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "sample.bin"
        path.write_bytes(bytes(range(256)) * 2)
        doc = module.HexDocument()
        doc.open(str(path))
        first = doc.render(0, 1)
        check("R-303 十六进制视图按 8 位偏移 + 16 字节 + ASCII 列渲染",
              first.startswith("00000000  00 01 02 03")
              and first.endswith("|\x00\x01\x02\x03".replace(
                  "\x00", ".").replace("\x01", ".").replace("\x02", ".").replace(
                  "\x03", ".") + "." * 12 + "|"),
              first)
        check("R-303 查找同时支持文本与十六进制",
              doc.find(b"\x41\x42") == 0x41
              and module.parse_hex("41 42") == b"AB"
              and module.parse_hex("zz") is None)
        doc.write(0x10, b"\xAA\xBB")
        doc.save()
        check("R-303 修改后保存真的写回文件",
              path.read_bytes()[0x10:0x12] == b"\xaa\xbb" and not doc.dirty)
        short = Path(temporary) / "short.bin"
        short.write_bytes(b"ABC")
        doc.open(str(short))
        doc.write(5, b"\x01")
        check("R-303 越过文件末尾写入时按零扩展",
              bytes(doc.data) == b"ABC\x00\x00\x01", bytes(doc.data))

        # R-310: the inline editor must show both digits.  It used to inherit
        # the application's 20 px ``QLineEdit`` padding, which left ~20 px for a
        # 26 px wide ``00`` - the first digit was clipped (measured, L-306).
        class _HexApi:
            """Only what :class:`HexPage` touches: messages and a menu factory."""

            def __init__(self) -> None:
                self.messages: list[tuple[str, str]] = []

            def notify(self, text, level="info"):
                self.messages.append((level, str(text)))

            def menu(self, parent=None):
                from PySide6.QtWidgets import QMenu

                return QMenu(parent)

        page = module.HexPage(_HexApi())
        page.resize(900, 600)
        page.show()
        page.doc.open(str(path))
        page.refresh()
        app.processEvents()
        text_w = page.view.fontMetrics().horizontalAdvance("00")
        box_issues = []
        for offset in (0, 15, 38):
            page.begin_inline_edit(offset)
            editor = page._editor
            image = editor.grab().toImage()
            ink = [x for x in range(image.width()) for y in range(image.height())
                   if image.pixelColor(x, y).lightness() < 140]
            box = editor.geometry()
            if not page.view.viewport().rect().contains(box):
                box_issues.append((offset, "outside viewport", box.getRect()))
            if ink and (min(ink) <= 1 or max(ink) >= image.width() - 2):
                box_issues.append((offset, "ink on border", min(ink), max(ink)))
            if ink and max(ink) - min(ink) + 1 < text_w - 2:
                box_issues.append((offset, "clipped", max(ink) - min(ink) + 1, text_w))
            if image.height() < editor.fontMetrics().height():
                box_issues.append((offset, "too short", image.height()))
            page._close_editor()
        check("R-310 双击编辑框完整显示两位十六进制且不越界",
              not box_issues, str(box_issues))


def chart_transport_checks(panel) -> None:
    original = panel._call_js
    messages = []
    panel.timer.stop()
    panel._call_js = lambda name, payload: messages.append((name, payload))
    try:
        panel.page_ready = True
        panel.layout_data = {"widgets": [{"id": "config", "kind": "line", "channel": "A",
                                          "channels": ["A", "B"], "title": "pair", "unit": "V",
                                          "decimals": 4, "min": -5, "max": 5,
                                          "x": 160, "y": 320, "w": 480, "h": 256}]}
        payload = panel.config_payload()
        with tempfile.TemporaryDirectory() as temporary:
            filename = str(Path(temporary) / "dashboard.json")
            panel.export_config(filename)
            saved = json.loads(Path(filename).read_text(encoding="utf-8"))
        check("R-311 export retains channels, precision and geometry", saved == payload)
        panel.clear_widgets()
        panel.apply_config(saved)
        check("R-311 import round trip retains every card setting",
              panel.config_payload() == saved and saved["widgets"][0]["channels"] == ["A", "B"]
              and saved["widgets"][0]["decimals"] == 4)
        before = json.dumps(panel.layout_data, sort_keys=True)
        invalid = [None, [], {}, dict(saved, version=999), dict(saved, widgets="wrong"),
                   dict(saved, widgets=[dict(saved["widgets"][0], min=float("nan"))]),
                   dict(saved, widgets=saved["widgets"] * 2),
                   dict(saved, widgets=saved["widgets"] * (panel.MAX_WIDGETS + 1))]
        for document in invalid:
            try:
                panel.apply_config(document)
            except ValueError:
                pass
            else:
                raise AssertionError("R-311 invalid import accepted")
        check("R-311 invalid imports leave the current layout unchanged",
              json.dumps(panel.layout_data, sort_keys=True) == before)
        panel.points.setValue(100)
        panel.begin_replay()
        panel.layout_data = {"widgets": [{"id": "transport", "kind": "line", "channel": "A"}]}
        for index in range(100):
            panel.replay_line(index, f"[OmniCOM-chart] A={index}")
        panel.flush_live()
        first = messages[-1][1]["epoch"]
        for index in range(100, 110):
            panel.replay_line(index, f"[OmniCOM-chart] A={index}")
        before_flush = len(messages)
        panel._flush_if_ready()
        check("R-321 completion-driven pump never overlaps a pending batch", len(messages) == before_flush)
        panel._on_acked(first)
        check("R-321 confirmation schedules the waiting tail without polling", panel._flush_timer.isActive())
        panel._ack_started -= 1
        panel._flush_if_ready()
        second = messages[-1][1]
        check("R-321 overflow during acknowledgement retains the entire new tail",
              [row[1] for row in second["history"]["A"]] == list(range(100, 110)))
        check("R-321 batch identities increase monotonically", second["epoch"] > first)
        panel._on_acked(first)
        check("R-321 stale acknowledgement cannot release a newer batch",
              panel._pending_ack == second["epoch"])
        panel._on_acked(second["epoch"])
        panel.request_resync()
        panel.flush_live()
        third = messages[-1][1]["epoch"]
        check("R-321 resync never reuses a batch identity", third > second["epoch"])
        panel._on_acked(second["epoch"])
        check("R-321 stale pre-resync acknowledgement is ignored", panel._pending_ack == third)
        panel._on_acked(third)
        for kind in ("value", "gauge", "bar", "lamp"):
            panel.layout_data = {"widgets": [{"id": "reading", "kind": kind, "channel": "A"}]}
            panel.replay_line(120, "[OmniCOM-chart] A=0")
            check(f"R-321 {kind}-only board detects a new reading", panel.has_fresh_data())
            panel.flush_live()
            panel._on_acked(messages[-1][1]["epoch"])
            check(f"R-321 {kind}-only board becomes idle after confirmation", not panel.has_fresh_data())
    finally:
        panel._call_js = original
        panel.clear_data()
        panel.clear_widgets()
        panel.points.setValue(0)
        panel.timer.start()


def decoupling_checks() -> None:
    """R-322: an installed plugin must not reach into host internals.

    A plugin folder is copied to a user's disk and stays there.  If it imports
    ``omnicom.ui.widgets`` or ``omnicom.i18n`` directly, then renaming or moving
    one host file breaks every already-installed plugin - and nothing in the app
    notices until the user opens the tab.  The API facade exists for exactly
    this, so the rule is checked statically over the shipped source.

    The check deliberately reads the *sources* rather than the imported modules:
    an accidental ``import`` inside a function still pins the plugin, and a
    runtime check would only fire once that branch happens to run.
    """
    import ast

    root = ROOT / "plugins"
    offenders: list[str] = []
    text_tables: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "vendor" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name == "omnicom" or name.startswith("omnicom."):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno} -> {name}")
        if path.name == "text.py":
            text_tables.append(path.relative_to(ROOT).as_posix())
    check("R-322 no plugin imports host internals", not offenders, offenders[:8])
    check("R-322 each decoupled plugin ships its own text table",
          {"plugins/hexview/text.py", "plugins/terminal/text.py",
           "plugins/transfer/text.py"} <= set(text_tables), text_tables)
    # The host table must not carry plugin copy any more: a key that belongs to
    # a plugin would keep working only while that plugin's table is registered.
    from omnicom import i18n

    leftovers = [key for key in i18n._TABLE
                 if key.startswith(("hex.", "term.", "xfer."))]
    check("R-322 the host text table holds no plugin copy", not leftovers, leftovers[:8])
    # Register the shipped tables the way ``create()`` does and prove the keys
    # resolve; the panels may not be loaded in this suite.
    registered = []
    loaded_tables: list[dict] = []
    for plugin_id, relative in (("hexview", "plugins/hexview/text.py"),
                                ("terminal", "plugins/terminal/text.py"),
                                ("transfer", "plugins/transfer/text.py")):
        namespace: dict = {}
        exec(compile((ROOT / relative).read_text(encoding="utf-8"),
                     relative, "exec"), namespace)
        table = namespace["TEXT"]
        loaded_tables.append(table)
        i18n.register_plugin_table(plugin_id, table)
        registered.append((plugin_id, table))
    check("R-322 registered plugin copy resolves through tr()",
          all(i18n.tr(key) not in ("", key)
              for key in ("hex.open", "term.title", "xfer.protocol", "xfer.needport")),
          [i18n.tr(key) for key in ("hex.open", "term.title", "xfer.protocol")])
    for plugin_id, _table in registered:
        i18n.unregister_plugin_table(plugin_id)
    check("R-322 unloading a plugin drops its copy", i18n.tr("hex.open") == "hex.open")
    # ------------------------------------------------------ R-324 key coverage
    # ``tr("menu.help")`` shipped with no entry in any table, so the Help menu
    # literally read "menu.help" on screen.  A missing key is silent by design
    # (it falls back to itself), which means only a static scan can catch it -
    # grepping for the *literal* keys the code passes to ``tr()``.
    source_keys: set[str] = set()
    for base in (ROOT / "omnicom", ROOT / "plugins"):
        for path in sorted(base.rglob("*.py")):
            if "vendor" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                if ast.unparse(node.func) not in ("tr", "i18n.tr", "api.tr", "self.api.tr"):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    source_keys.add(first.value)
    known = set(i18n._TABLE)
    for table in loaded_tables:
        known |= set(table)
    unknown = sorted(key for key in source_keys if key not in known)
    check(f"R-324 every literal tr() key resolves (checked {len(source_keys)})",
          not unknown, unknown[:10])
    for key in ("menu.help", "menu.docs", "menu.about"):
        check(f"R-324 {key} has real text, not the key itself",
              i18n.tr(key) not in ("", key), i18n.tr(key))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--chart-only", action="store_true")
    parser.add_argument("--interaction-only", action="store_true")
    args = parser.parse_args()
    data = Path(os.environ["OMNICOM_DATA_DIR"])
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    from omnicom import webengine
    from omnicom.ui import theme

    # QtWebEngine needs its Chromium switches and AA_ShareOpenGLContexts *before*
    # the first QApplication exists; without them the dashboard page silently
    # never finishes loading and every R-161 check would fail for the wrong
    # reason (L-221).
    webengine.prepare()
    app = QApplication([])
    for font in ("segoeui.ttf", "msyh.ttc", "consola.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.app_stylesheet())
    chart_capacity_checks()
    source_dialog_checks(app)
    if not args.chart_only:
        for mode in ("dual", "user", "bundled"):
            uninstall_cycle_checks(app, mode)
        uninstall_rollback_checks()
        if not args.interaction_only:
            store_checks()
            multi_source_checks()
            catalog_build_checks()
            decoupling_checks()
    host, chart, parser_class = chart_checks(app, KINDS)
    chart_transport_checks(chart)
    chart_readout_checks(app, chart)
    curve_sources_checks(app, chart, KINDS)
    chart_display_controls_checks(app, chart)
    chart_window_checks(app, chart)
    if not args.interaction_only:
        chart_stream_checks(app, chart)
    if not args.chart_only and not args.interaction_only:
        launcher_checks(app)
        search_checks(app)
        store_ui_checks(app)
    if args.benchmark:
        results = benchmark(app, chart, parser_class)
        atomic_json(ROOT / ".artifacts" / "chart-benchmark.json", results)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    timer = chart.timer
    view = chart.view
    host.unload()
    check("R-147 unload stops chart timer", not timer.isActive())
    # The panel owns a QWebEngineView, and deleting that widget without first
    # releasing the page/profile aborts the process at exit (0xC0000005, "Release
    # of profile requested but WebEnginePage still not deleted").  The host calls
    # the optional ``dispose()`` hook before the generic delete; it must clear
    # ``view``/``page_ready`` and destroy the view *synchronously* (L-221).
    import shiboken6

    check("R-161 unload disposes the WebEngine surface",
          chart.view is None and chart.page_ready is False and not chart.timer.isActive())
    check("R-161 WebEngine view destroyed synchronously, not left to deleteLater",
          not shiboken6.isValid(view))
    atomic_json(ROOT / ".artifacts" / "plugin-selftest.json",
                {"checks": CHECKS, "ok": all(item["ok"] for item in CHECKS)})
    print(f"PLUGIN SELFTEST OK: {len(CHECKS)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
