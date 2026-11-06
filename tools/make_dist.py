"""Build a self-contained OmniCOM folder: no installs, no pip, double-click to run.

    python tools/make_dist.py            # build dist/OmniCOM
    python tools/make_dist.py --check    # build, then run the packaged --selftest
    python tools/make_dist.py --zip      # also produce dist/OmniCOM-<version>.zip

Why this and not a packaged single binary: a self-extracting bootstrap is what
endpoint tooling objects to, and it also unpacks ~200 MB into %TEMP% on every
launch.  The folder built here contains

    runtime\\python.exe, pythonw.exe      official CPython "embeddable" build
    runtime\\Lib\\site-packages\\         PySide6 + shiboken6 + pyserial, trimmed
    OmniCOM.pyz                          our own code (readable zip)
    assets\\  logo.png                    icons and logos
    OmniCOM.cmd / console.cmd            launchers

No ``plugins/\\`` folder is shipped (R-174): every plugin - the data dashboard
included - is installed from the plugin store, so an update can never shadow the
copy the user installed or reinstall one they removed.

The embeddable runtime has a ``._pth`` file, which switches off ``site`` and all
user/site-packages paths, so the app can only import what ships in the folder.
That is what makes the result dependency-free *and* reproducible: the same folder
behaves the same on a machine that never installed Python or PySide6.

Trimming: the Qt modules this app never touches (Quick/QML/Designer/3D/WebEngine
/Multimedia/Charts/…) are dropped together with their ``.pyd`` wrappers.  The
build ends with a real ``--selftest`` run inside the bundle, so a missing module
fails here instead of on the user's desk.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

NAME = "OmniCOM"
CACHE = ROOT / ".artifacts" / "cache"

#: Versioned official CPython embeddable build (same minor as the dev machine).
EMBED_VERSION = "3.13.14"
EMBED_URL = (f"https://www.python.org/ftp/python/{EMBED_VERSION}/"
             f"python-{EMBED_VERSION}-embed-amd64.zip")

#: Qt modules the app never imports.  Their ``.pyd`` wrappers and the matching
#: ``Qt6*.dll`` are skipped, which is where most of the size sits.
#: QtWebEngine (and the QtQml/QtQuick core it links against) must stay: the
#: chart plugin renders its dashboard with offline ECharts inside a
#: QWebEngineView (R-161), so dropping them broke the plugin at runtime.
SKIP_MODULES = (
    "designer", "3d",
    "websockets", "multimedia", "multimediawidgets", "charts", "datavisualization",
    "graphs", "sql", "test", "bluetooth", "nfc", "positioning", "location",
    "serialbus", "serialport", "sensors", "remoteobjects", "spatialaudio",
    "texttospeech", "help", "uitools", "statemachine", "scxml", "pdf",
    "pdfwidgets", "svgwidgets", "languageserver", "qmllocalstorage",
)
#: DLL prefixes that belong to those modules (they are shipped top-level).
SKIP_DLL_PREFIXES = (
    "Qt6Designer",
    "Qt6Labs", "Qt6Lottie",
    "Qt63D", "Qt6Charts", "Qt6DataVisualization", "Qt6Graphs", "Qt6Sql", "Qt6Test",
    "Qt6Bluetooth", "Qt6Nfc", "Qt6Positioning", "Qt6Location", "Qt6SerialBus",
    "Qt6Sensors", "Qt6RemoteObjects", "Qt6Spatial", "Qt6TextToSpeech",
    "Qt6Help", "Qt6UiTools", "Qt6StateMachine", "Qt6Scxml", "Qt6Pdf",
    "Qt6SvgWidgets",
)
#: Qt plugin folders that must stay (platform integration, styles, images).
KEEP_PLUGINS = ("platforms", "styles", "imageformats", "iconengines",
                "platformthemes", "webview")

#: Chromium resources QtWebEngine cannot start without.  The ``*.debug.*``
#: variants are ~40 MB of symbols, so they stay out of the portable folder.
WEBENGINE_RESOURCES = ("icudtl.dat", "qtwebengine_resources.pak",
                       "qtwebengine_resources_100p.pak",
                       "qtwebengine_resources_200p.pak",
                       "v8_context_snapshot.bin")

LAUNCHER = """@echo off
rem OmniCOM - self-contained build.  Nothing to install: the Python runtime and
rem every dependency live in the runtime\\ folder next to this file.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONHOME=
set PYTHONPATH=
rem Agents call `OmniCOM.cmd cli ...` and expect the JSON on stdout.  `start`
rem detaches the process, so pythonw.exe had nowhere to print and the command
rem looked like it silently did nothing (L-146): the cli sub-command runs
rem python.exe in the foreground instead and passes the exit code through.
if /i "%~1"=="cli" goto cli
start "" "runtime\\pythonw.exe" "OmniCOM.pyz" %*
goto :eof
:cli
"runtime\\python.exe" "OmniCOM.pyz" %*
exit /b %ERRORLEVEL%
"""

CONSOLE = """@echo off
rem Same as OmniCOM.cmd but keeps a console, so errors stay visible.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONHOME=
set PYTHONPATH=
"runtime\\python.exe" "OmniCOM.pyz" %*
echo.
echo [OmniCOM] exited with code %ERRORLEVEL%
pause
endlocal
"""

SHORTCUT = """@echo off
rem Create a desktop shortcut named OmniCOM that uses the app icon (R-097).
rem The shortcut points at runtime\\pythonw.exe, so no console window appears.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONHOME=
set PYTHONPATH=
"runtime\\python.exe" "OmniCOM.pyz" shortcut %*
echo.
pause
endlocal
"""

README = """OmniCOM {version} - 免安装版 / portable build
============================================

1. 双击 OmniCOM.cmd 启动，不需要安装 Python、PySide6 或任何依赖。
2. 想放桌面：双击「桌面快捷方式.cmd」一步搞定（名字就是 OmniCOM，图标就是软件图标）；
   OmniCOM.cmd 放在哪里都行，快捷方式会跟着走，路径换了重新双击一次即可。
3. 需要看报错时用 console.cmd（会保留命令行窗口）。
4. 首次运行会在本目录生成 omnicom-data\\（收发记录、命令库、插件）。
5. 整个文件夹可以随意拷贝到别的电脑或 U 盘，路径无关。
6. 命令行接口：OmniCOM.cmd cli info（详细说明见 CLI.md 与 AI-GUIDE.md）。

1. Double-click OmniCOM.cmd. No Python, PySide6 or other dependency is needed.
2. Desktop icon: double-click "Create desktop shortcut.cmd" - the shortcut is
   named OmniCOM and uses the application icon; it follows this folder, so just
   double-click it again after moving the folder.
3. Use console.cmd when you need to read a traceback.
4. omnicom-data\\ (captures, commands, plugins) is created on the first run.
5. The folder is relocatable - copy it anywhere, including a USB stick.
6. Command line: OmniCOM.cmd cli info (see CLI.md and AI-GUIDE.md).
"""

SHORTCUT = """@echo off
rem Create a desktop shortcut named OmniCOM that uses the app icon (R-097).
rem The shortcut points at runtime\\pythonw.exe, so no console window appears.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONHOME=
set PYTHONPATH=
"runtime\\python.exe" "OmniCOM.pyz" shortcut %*
echo.
pause
endlocal
"""

CONSOLE = """@echo off
rem Same as OmniCOM.cmd but keeps a console, so errors stay visible.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONHOME=
set PYTHONPATH=
"runtime\\python.exe" "OmniCOM.pyz" %*
echo.
echo [OmniCOM] exited with code %ERRORLEVEL%
pause
endlocal
"""

README = """OmniCOM {version} - 免安装版 / portable build
============================================

1. 双击 OmniCOM.cmd 启动，不需要安装 Python、PySide6 或任何依赖。
2. 想放桌面：双击「桌面快捷方式.cmd」一步搞定（名字就是 OmniCOM，图标就是软件图标）；
   OmniCOM.cmd 放在哪里都行，快捷方式会跟着走，路径换了重新双击一次即可。
3. 需要看报错时用 console.cmd（会保留命令行窗口）。
4. 首次运行会在本目录生成 omnicom-data\\（收发记录、命令库、插件）。
5. 整个文件夹可以随意拷贝到别的电脑或 U 盘，路径无关。
6. 命令行接口：OmniCOM.cmd cli info（详细说明见 CLI.md 与 AI-GUIDE.md）。

1. Double-click OmniCOM.cmd. No Python, PySide6 or other dependency is needed.
2. Desktop icon: double-click "Create desktop shortcut.cmd" - the shortcut is
   named OmniCOM and uses the application icon; it follows this folder, so just
   double-click it again after moving the folder.
3. Use console.cmd when you need to read a traceback.
4. omnicom-data\\ (captures, commands, plugins) is created on the first run.
5. The folder is relocatable - copy it anywhere, including a USB stick.
6. Command line: OmniCOM.cmd cli info (see CLI.md and AI-GUIDE.md).
"""


# --------------------------------------------------------------------- helpers
def _download(url: str, target: Path) -> Path:
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response, \
            open(target, "wb") as handle:
        shutil.copyfileobj(response, handle)
    return target


def _site_packages() -> Path:
    import PySide6

    return Path(PySide6.__file__).resolve().parent.parent


def _skip(name: str, *, is_dll: bool) -> bool:
    lowered = name.lower()
    if is_dll:
        return any(name.startswith(prefix) for prefix in SKIP_DLL_PREFIXES)
    return any(lowered.startswith(f"qt{module}".lower()) for module in SKIP_MODULES)


def _pe_imports(path: Path) -> list[str]:
    """Return the DLL names in a PE file's import table (no external tools)."""
    try:
        data = path.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return []
        coff = pe + 4
        sections = struct.unpack_from("<H", data, coff + 2)[0]
        optional = coff + 20
        magic = struct.unpack_from("<H", data, optional)[0]
        if magic == 0x20B:
            directory = optional + 112
        elif magic == 0x10B:
            directory = optional + 96
        else:
            return []
        count = struct.unpack_from("<I", data, directory - 4)[0]
        if count < 2:
            return []
        import_rva = struct.unpack_from("<I", data, directory + 8)[0]

        headers = []
        for index in range(sections):
            offset = optional + struct.unpack_from("<H", data, coff + 16)[0] + index * 40
            vsize, vaddr = struct.unpack_from("<II", data, offset + 8)
            raw_size, raw = struct.unpack_from("<II", data, offset + 16)
            headers.append((vaddr, max(vsize, raw_size), raw))

        def to_offset(rva: int) -> int | None:
            for vaddr, size, raw in headers:
                if vaddr <= rva < vaddr + size:
                    return raw + (rva - vaddr)
            return None

        offset = to_offset(import_rva)
        if offset is None:
            return []
        names = []
        while True:
            _original, _stamp, _chain, name_rva, _first = struct.unpack_from(
                "<IIIII", data, offset)
            if name_rva == 0:
                break
            name_offset = to_offset(name_rva)
            if name_offset is None:
                break
            names.append(data[name_offset:data.index(b"\0", name_offset)].decode(
                "ascii", "replace"))
            offset += 20
        return names
    except (OSError, struct.error, ValueError):
        return []


def _qt_closure(source: Path) -> tuple[set[str], set[str]]:
    """Which ``.dll`` / ``.pyd`` files the kept modules really need.

    The skip lists above are hand maintained, so they quietly break the moment
    a kept module starts linking against a "dropped" one: QtWebEngineWidgets
    pulls in Qt6Positioning, and without it the bundled ``import
    QtWebEngineWidgets`` died with "DLL load failed" while every file it was
    *thought* to need was present (L-223).  The import table is the truth, so
    the kept set is expanded until it is closed.
    """
    available = {item.name for item in source.glob("*.dll")}
    keep_dll = {name for name in available if not _skip(name, is_dll=True)}
    keep_pyd = {item.name for item in source.glob("*.pyd")
                if not _skip(item.name, is_dll=False)}
    pending = sorted(keep_dll | keep_pyd)
    while pending:
        current = pending.pop()
        for dependency in _pe_imports(source / current):
            if dependency in available and dependency not in keep_dll:
                keep_dll.add(dependency)
                pending.append(dependency)
    return keep_dll, keep_pyd


def _copy_pyside(source: Path, target: Path) -> int:
    """Copy the Qt subset the app needs; returns the number of skipped files."""
    skipped = 0
    target.mkdir(parents=True, exist_ok=True)
    keep_dll, keep_pyd = _qt_closure(source)
    for item in source.iterdir():
        name = item.name
        lowered = name.lower()
        if item.is_dir():
            if name in ("plugins", "glue"):
                continue                       # handled below
            if name == "resources":
                continue                       # whitelisted below (Chromium)
            if name in ("doc", "include", "metatypes", "qml", "typesystems",
                        "__pycache__", "support"):
                skipped += 1
                continue
            shutil.copytree(item, target / name, dirs_exist_ok=True)
            continue
        if item.suffix == ".dll" and name not in keep_dll:
            skipped += 1
            continue
        if item.suffix == ".pyd" and name not in keep_pyd:
            skipped += 1
            continue
        if item.name in ("python.exe", "pythonw.exe"):
            shutil.copy2(item, target / name)
            continue
        # QtWebEngineProcess.exe is mandatory: without it every
        # QWebEngineView stays blank (the dashboard, R-161).
        if name.startswith("QtWebEngineProcess") or (
                "qtwebengine" in lowered and item.suffix == ".exe"):
            shutil.copy2(item, target / name)
            continue
        if item.suffix in (".exe", ".a", ".lib"):
            skipped += 1
            continue
        shutil.copy2(item, target / name)

    resources = source / "resources"
    if resources.is_dir():
        destination = target / "resources"
        destination.mkdir(parents=True, exist_ok=True)
        for name in WEBENGINE_RESOURCES:
            item = resources / name
            if item.is_file():
                shutil.copy2(item, destination / name)
            else:
                print(f"WARNING: missing WebEngine resource {name}")

    glue = source / "glue"
    if glue.is_dir():
        shutil.copytree(glue, target / "glue", dirs_exist_ok=True)
    plugins = source / "plugins"
    for folder in KEEP_PLUGINS:
        origin = plugins / folder
        if origin.is_dir():
            shutil.copytree(origin, target / "plugins" / folder, dirs_exist_ok=True)
    return skipped


def _prepare_runtime(target: Path) -> Path:
    """Unpack the embeddable CPython next to the app and extend its path file."""
    runtime = target / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    archive = _download(EMBED_URL, CACHE / f"python-{EMBED_VERSION}-embed-amd64.zip")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(runtime)

    # ``python313._pth`` replaces the normal path setup: everything the app may
    # import has to be listed here, and ``import site`` must stay commented out so
    # no user site-packages can leak in.
    pth = next(runtime.glob("python*._pth"))
    lines = [
        "python313.zip",
        ".",
        "Lib\\site-packages",
        "# import site",
    ]
    pth.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    print(f"runtime: {runtime} ({pth.name} pinned)")
    return runtime


def _install_dependencies(runtime: Path) -> None:
    site = runtime / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    source = _site_packages()
    skipped = _copy_pyside(source / "PySide6", site / "PySide6")
    for package in ("shiboken6", "serial"):
        shutil.copytree(source / package, site / package, dirs_exist_ok=True)
    for extra in ("pyserial-*.dist-info",):
        for info in source.glob(extra):
            shutil.copytree(info, site / info.name, dirs_exist_ok=True)
    size = sum(item.stat().st_size for item in site.rglob("*") if item.is_file())
    print(f"site-packages: {size / 1048576:.0f} MB ({skipped} unneeded Qt files left out)")


def _size_mb(path: Path) -> float:
    return sum(item.stat().st_size for item in path.rglob("*")
               if item.is_file()) / 1048576


def build(target: Path, version: str) -> Path:
    import make_pyz  # same folder

    target.mkdir(parents=True, exist_ok=True)
    print(f"== {NAME} {version}")
    make_pyz.build(target)                            # pyz + assets + docs
    runtime = _prepare_runtime(target)
    _install_dependencies(runtime)

    (target / f"{NAME}.cmd").write_text(LAUNCHER, encoding="utf-8", newline="\r\n")
    (target / "console.cmd").write_text(CONSOLE, encoding="utf-8", newline="\r\n")
    # One-click desktop shortcut, with the app name and the app icon (R-097).
    (target / "桌面快捷方式.cmd").write_text(SHORTCUT, encoding="utf-8",
                                             newline="\r\n")
    (target / "Create desktop shortcut.cmd").write_text(SHORTCUT, encoding="utf-8",
                                                        newline="\r\n")
    (target / "README.txt").write_text(README.format(version=version),
                                       encoding="utf-8", newline="\r\n")
    for stale in ("HOW-TO-RUN.txt", "start-console.cmd"):
        path = target / stale
        if path.exists():
            path.unlink()

    print(f"== total {_size_mb(target):.0f} MB in {target}")
    return target


def verify(target: Path) -> int:
    """Run the packaged selftest with the bundled runtime only."""
    python = target / "runtime" / "python.exe"
    # Inherited test variables would make the packaged selftest write its
    # report into the bundle (or into the repo), so they are dropped here.
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                          "OMNICOM_DATA_DIR", "OMNICOM_SETTINGS_SUFFIX")}
    env["PYTHONIOENCODING"] = "utf-8"
    # ``-E`` isolates the bundle from this machine's environment - which is
    # exactly why ``PYTHONIOENCODING`` alone cannot be trusted here: ``-E``
    # ignores it, the child fell back to the console code page (cp936), this
    # side decoded those bytes as UTF-8, and re-printing the resulting U+FFFD
    # raised UnicodeEncodeError *before* the verdict was printed.  ``-X utf8``
    # is a command-line switch, so it survives ``-E`` (L-038, L-170).
    result = subprocess.run(
        [str(python), "-X", "utf8", "-E", "-s",
         str(target / f"{NAME}.pyz"), "--selftest"],
        cwd=target, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300, env=env)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    # Never let the *report* decide the verdict: a character the console
    # cannot encode must not abort the run before the pass/fail line.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(errors="replace")
    print(output[-1600:] if output else "(no output)")
    if "SELFTEST OK" not in output or result.returncode != 0:
        print("packaged selftest FAILED")
        return result.returncode or 1
    print("packaged selftest OK (bundled runtime, no external packages)")
    from make_plugins import verify_runtime

    if verify_runtime(target) != 0:
        return 1
    # The updater suite runs five real installer cycles (local / download /
    # locked / defer), each one copying the whole 462 MB bundle and then
    # launching a second interpreter, so it needs ~330 s on a fast machine.
    # The old 300 s cap fired *after* every assertion had passed and reported
    # "packaged selftest FAILED" on a perfectly good build (L-225).
    print("== updater acceptance suite (this takes several minutes)", flush=True)
    installer = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "tools/acceptance_test.py"),
         "--only", "installer", "--bundle", str(target)],
        cwd=ROOT, timeout=1800)
    if installer.returncode != 0:
        print("updater acceptance suite FAILED")
        return installer.returncode or 1
    print("updater acceptance suite OK (installer, restart, downgrade deferred)")
    return 0


def archive(target: Path, version: str) -> Path:
    # 运行期数据（omnicom-data）是首次启动才生成的，打包前必须清掉，
    # 否则会把验证过程中产生的 selftest.json、日志与插件数据一并发出去。
    for stale in ("omnicom-data", ".artifacts"):
        leftover = target / stale
        if leftover.exists():
            shutil.rmtree(leftover)
            print(f"== drop {leftover.relative_to(target.parent)} "
                  f"(created while verifying, not shipped)")
    zip_path = target.parent / f"{NAME}-{version}-win64"
    produced = shutil.make_archive(str(zip_path), "zip", target.parent, target.name)
    output = Path(produced)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"== zip {output} ({output.stat().st_size / 1048576:.0f} MB)")
    print(f"   sha256 {digest}")
    return output


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from omnicom import __version__

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "dist" / NAME))
    parser.add_argument("--check", action="store_true", help="run the packaged --selftest")
    parser.add_argument("--zip", action="store_true", help="also write the release zip")
    options = parser.parse_args()

    target = Path(options.out).resolve()
    if target.exists():
        shutil.rmtree(target)
    build(target, __version__)
    if options.check and verify(target) != 0:
        return 1
    if options.zip:
        archive(target, __version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
