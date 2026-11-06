"""Build independent plugin ZIPs and a hash-verified catalog (does not publish).

The chart plugin used to bundle NumPy + PyQtGraph in a ``_vendor`` folder and
draw with Qt widgets.  Since R-161 it is a drag-and-drop dashboard rendered by
the bundle's own offline ECharts inside QtWebEngine, so the plugin ZIP is pure
Python + HTML/JS and the runtime check must prove *dashboard* behaviour
(page loads, all five widget kinds mount, real canvas pixels, RX feeds a new
channel, unload stops the timer) instead of inspecting pyqtgraph internals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "https://github.com/Fatmear/OmniCOM/releases/download/plugin-store/"


def version_key(value: str) -> tuple[int, ...]:
    """Numeric version sort key; mirrors ``omnicom.plugin_store.version_key``.

    Duplicated on purpose: this builder runs against a checked-out source tree
    and must not import the application (the ``library`` closet does), so the
    four-part numeric comparison is kept small and identical here (R-178).
    """
    parts = tuple(int(part) for part in str(value).split(".") if part.isdigit())
    return parts + (0,) * (4 - len(parts))


def verify_runtime(target: Path) -> int:
    code = '''
import os,sys,math,json,zipfile,hashlib,time
from pathlib import Path
root=Path(sys.argv[1])
# R-174: the bundle ships no plugins folder any more, so the plugin source for
# the install/uninstall cycle comes from the repository (argv[4]).  Keeping a
# "bundled" mode around made the check reach for a folder that no longer
# exists, and the whole build died on a StopIteration inside next().
source_root=Path(sys.argv[4])
sys.path.insert(0,str(root/'OmniCOM.pyz'))
os.environ['QT_QPA_PLATFORM']='offscreen'
def verify(temporary):
    assert not (root/'plugins').exists(), 'R-174: the bundle must not ship plugins/'
    os.environ['OMNICOM_DATA_DIR']=temporary
    # The plugin has to arrive the way a user gets it: through the store, into
    # the data directory.  That is also the only path that proves the ZIP is
    # self-contained (no _vendor, no imports outside the bundle).
    from omnicom.plugin_store import PluginStore
    source=source_root/'plugins/chart'
    archive_path=Path(temporary)/'chart.zip'
    with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in source.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and '_vendor' not in path.parts:
                archive.write(path,'chart/'+path.relative_to(source).as_posix())
    item=json.loads((source/'plugin.json').read_text(encoding='utf-8'))
    item.update(url=archive_path.as_uri(),sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest())
    store=PluginStore()
    store.install(item)
    # Every WebEngine prerequisite (AA_ShareOpenGLContexts + the Chromium
    # switches) has to be applied before the first QApplication exists, or the
    # page silently never finishes loading.  The app's own helper owns that
    # list, so the check must use it instead of hand-rolling the flags.
    from omnicom import webengine
    assert webengine.prepare(), 'WebEngine module missing from the bundle'
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    app=QApplication([])
    for font in ('segoeui.ttf','msyh.ttc','consola.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
    from omnicom.ui import theme
    app.setStyle('Fusion')
    app.setStyleSheet(theme.app_stylesheet())
    from omnicom.plugins import PluginHost
    host=PluginHost()
    host.load()
    chart=next(info.widget for info in host.loaded() if info.id=='chart')
    assert 'webengine' in chart.__class__.__module__ or hasattr(chart,'page_ready')
    chart.resize(1050,650)
    chart.show()
    deadline=time.time()+30
    while time.time()<deadline and not getattr(chart,'page_ready',False):
        app.processEvents(); time.sleep(0.05)
    assert getattr(chart,'page_ready',False), 'dashboard page never loaded'
    app.processEvents(); time.sleep(0.6); app.processEvents()
    page=chart.view.page()
    def call(code,limit=10.0):
        # QtWebEngine only answers the *callback* form of runJavaScript in
        # this build; the synchronous overload returns None forever, which
        # looked exactly like "the page never loaded" (L-224).
        box={'done':False,'value':None}
        def receive(value):
            box['value']=value; box['done']=True
        page.runJavaScript(code,0,receive)
        stop=time.time()+limit
        while time.time()<stop and not box['done']:
            app.processEvents(); time.sleep(0.02)
        return box['value'] if box['done'] else None
    raw=None
    deadline=time.time()+15
    while time.time()<deadline and not raw:
        raw=call('omniDashboard.ready()')
        time.sleep(0.05)
    assert raw is True, 'bundled ECharts runtime is not usable offline'
    # The default layout is empty, so the five kinds have to be mounted through
    # the panel's own palette path before anything can be described.
    chart.clear_widgets()
    chart.channel_combo.clear()
    chart.channel_combo.addItem('AA')
    chart.channel_combo.setCurrentText('AA')
    for kind in ('line','gauge','value','bar','lamp'):
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData(kind))
        chart.title_edit.setText('widget-'+kind)
        chart.min_edit.setText('0')
        chart.max_edit.setText('10')
        assert chart.add_widget_from_palette(), 'cannot add '+kind
    chart.refresh()
    deadline=time.time()+20
    while time.time()<deadline:
        app.processEvents()
        probe=call('omniDashboard.describe()')
        if probe and len(json.loads(probe)['widgets'])>=5:
            break
        time.sleep(0.05)
    raw=None
    deadline=time.time()+15
    while time.time()<deadline and not raw:
        raw=call('omniDashboard.describe()')
        time.sleep(0.05)
    assert raw, 'describe() returned nothing'
    widgets=json.loads(raw)['widgets']
    kinds={w['kind'] for w in widgets}
    assert kinds=={'line','gauge','value','bar','lamp'}, 'widget kinds: '+str(sorted(kinds))
    pixels={}
    for widget in widgets:
        # value / bar / lamp are plain DOM cards, so only the two
        # ECharts kinds can answer with canvas pixels.
        if widget['kind'] not in ('line','gauge'):
            continue
        deadline=time.time()+10
        drawn=-1
        while time.time()<deadline:
            value=call('omniDashboard.paintedPixels('+json.dumps(widget['id'])+')')
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                drawn=float(value)
                if drawn>100:
                    break
            time.sleep(0.05)
        pixels[widget['kind']]=int(drawn)
        assert drawn>100, 'widget '+widget['kind']+' painted nothing'
    for index in range(200):
        host.publish_rx(1000+index*10,('[OmniCOM-chart] AA='+str(math.sin(index/20))+' BB=1.1'+chr(10)).encode())
    chart.refresh()
    app.processEvents()
    timer=chart.timer
    host.unload()
    assert not timer.isActive()
    saved=Path(temporary)/'plugin-data/chart/preserved.txt'
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text('keep')
    store.install(item)
    host.load()
    chart=next(info.widget for info in host.loaded() if info.id=='chart')
    host.publish_rx(4000,b'[OmniCOM-chart] NEW=3'+bytes([10]))
    assert chart.samples['NEW'][-1][1]==3
    host.unload()
    store.uninstall('chart')
    host.load()
    assert not any(info.id=='chart' for info in host.loaded())
    host.unload()
    # User data must survive an uninstall; a plugin that takes the settings
    # with it is a bug nobody notices until the next install.
    assert saved.read_text()=='keep', 'uninstall dropped plugin-data'
    print('PLUGIN CYCLE OK: store install, five widget kinds painted '
          + json.dumps(pixels) + ', RX, reinstall, data preserved, uninstall')
verify(sys.argv[2])
'''
    with tempfile.TemporaryDirectory(prefix="omnicom-plugin-check-") as temporary:
        result = subprocess.run(
            [str(target / "runtime" / "python.exe"), "-B", "-X", "utf8", "-E", "-s",
             "-c", code, str(target), temporary, "store", str(ROOT)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
        print((result.stdout + result.stderr).strip())
        if result.returncode:
            return result.returncode
    return 0


def history(output: Path, identifier: str, base_url: str = DEFAULT_BASE) -> list[dict]:
    """Version records for packages already sitting in the output folder.

    R-178 lets the store install a *specific* version, which only works if the
    catalog carries a history.  Every build therefore keeps the ZIPs that are
    already published next to it: they are re-hashed and re-read (their own
    ``plugin.json`` supplies the API level and version), so an entry's
    ``versions`` list grows release by release without editing JSON by hand.
    A package whose manifest disagrees with its file name is skipped - the
    store would refuse it anyway.  Records carry the absolute URL, exactly like
    the head build, because the catalog and the packages are *not* on the same
    host (JSON on raw.githubusercontent, ZIPs on the release assets).
    """
    records = []
    for path in sorted(output.glob(identifier + "-*.zip")):
        version = path.stem[len(identifier) + 1:]
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read(f"{identifier}/plugin.json"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
        if str(manifest.get("version")) != version or manifest.get("id") != identifier:
            continue
        records.append({"version": version, "api": manifest.get("api", 1),
                        "url": base_url.rstrip("/") + "/" + path.name,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "size": path.stat().st_size})
    return records


def build(output: Path, base_url: str = DEFAULT_BASE, catalog_path: Path | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    catalog: dict = {"schema": 1, "plugins": []}
    for source in sorted((ROOT / "plugins").iterdir()):
        manifest_path = source / "plugin.json"
        if not source.is_dir() or not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        identifier = manifest["id"]
        filename = f"{identifier}-{manifest['version']}.zip"
        archive_path = output / filename
        with tempfile.TemporaryDirectory(prefix="omnicom-plugin-") as temporary:
            stage = Path(temporary) / identifier
            shutil.copytree(source, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_vendor"))
            shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
            if identifier == "chart":
                # ECharts ships inside the app bundle, so the plugin archive is
                # pure Python + HTML/JS and no longer needs a native vendoring
                # step (or a Windows/CPython lock).
                shutil.copy2(ROOT / "assets" / "logo-64.png", stage / "icon.png")
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(stage.parent).as_posix())
        item = {**manifest, "url": base_url.rstrip("/") + "/" + filename,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                "size": archive_path.stat().st_size}
        # Older builds published beside this one become the picker's history;
        # the current build replaces any same-version record (a rebuild has a
        # new hash, so the old record would fail verification).
        versions = [record for record in history(output, identifier, base_url)
                    if record["version"] != item["version"]]
        versions.append({"version": item["version"], "api": item.get("api", 1),
                         "url": item["url"], "sha256": item["sha256"],
                         "size": item["size"]})
        versions.sort(key=lambda record: version_key(record["version"]), reverse=True)
        item["versions"] = versions
        catalog["plugins"].append(item)
        print(f"{filename}: {item['size']} bytes, SHA256 {item['sha256']}"
              f" (history: {', '.join(record['version'] for record in versions)})")
    catalog_path = catalog_path or output / "catalog.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "plugin-store")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--catalog-out", type=Path, default=ROOT / "plugins" / "catalog.json")
    args = parser.parse_args()
    build(args.out, args.base_url, args.catalog_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
