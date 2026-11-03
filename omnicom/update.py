"""Software update: ask GitHub for the newest release, and install it safely.

Three separate jobs, so each can be tested without a window or a network:

``check``
    read the release pages; anything unexpected (offline, proxy,
    malformed response) comes back as ``None`` instead of raising - an
    update check must never be able to disturb the app (R-107).
``stage``
    download the release asset and unpack it next to the data folder.  Nothing
    in the running bundle is touched while the program is open.
``apply_pending``
    transactional file installation for an unlocked bundle; the live app uses
    ``launch_installer`` to wait for its exit in an isolated runtime, install
    without touching user data, and restart automatically (R-108).
"""

from __future__ import annotations

import contextlib
import html as html_unescape_module
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from importlib.resources import files
from pathlib import Path

from . import __version__, paths
from .i18n import tr

#: ``html.unescape`` without the module name clashing with a local variable.
html_unescape = html_unescape_module.unescape

#: Where a human should be sent when the automatic install is not an option.
RELEASES_PAGE = "https://github.com/Fatmear/OmniCOM/releases/latest"
USER_AGENT = "OmniCOM-updater"
#: Everything under this folder is the user's: never overwritten, never removed.
PROTECTED_DIR = "omnicom-data"

PENDING_MARKER = "pending.json"


# ---------------------------------------------------------------- versioning
def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.2.3"``/``"1.2.3-beta"`` -> ``(1, 2, 3)``.

    A plain tuple compare is enough for the numeric part, and it keeps the
    comparison obvious instead of hiding it in a dependency.
    """
    cleaned = str(text or "").strip().lstrip("vV")
    numbers: list[int] = []
    for chunk in cleaned.replace("-", ".").replace("_", ".").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        if not digits:
            break
        numbers.append(int(digits))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


# ------------------------------------------------------------------- network
def _request(url: str, timeout: float, headers: dict | None = None):
    merged = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    merged.update(headers or {})
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=merged), timeout=timeout)


def check(current: str = __version__, timeout: float = 3.0,
          api: str | None = None, atom: str | None = None) -> dict | None:
    """Return ``{"available", "version", "url", "notes", "asset", ...}`` or None.

    Never raises: the caller runs this in the background and a failure simply
    means "no news" (R-107).  Use :func:`check_release` when the reason for a
    failure has to be shown to the user.
    """
    try:
        return _check_web(current, timeout, atom=atom or api or RELEASES_ATOM)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


# --------------------------------------------------- web paths (no rate limit)
#: Unauthenticated GitHub API calls are limited per **IP address** (60 per
#: hour).  One office gateway serves hundreds of people from a single address,
#: so the allowance is gone within minutes and every check came back ``None``
#: with HTTP 403 - and the dialog, which only ever waited for a success, sat on
#: "正在检查新版本…" forever (L-168).  These two pages carry the same facts and
#: avoid that API quota and are now the only check path (R-145).
RELEASES_ATOM = "https://github.com/Fatmear/OmniCOM/releases.atom"
RELEASES_ASSETS = "https://github.com/Fatmear/OmniCOM/releases/expanded_assets"
RELEASES_DOWNLOAD = "https://github.com/Fatmear/OmniCOM/releases/download"
#: ``OmniCOM-1.2.0-win64.zip`` - the name every release uses.
ASSET_TEMPLATE = "OmniCOM-{version}-win64.zip"

_ATOM_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_ATOM_LINK = re.compile(r'<link[^>]*href="([^"]+)"')
_ATOM_CONTENT = re.compile(r"<content[^>]*>(.*?)</content>", re.S)
_ANY_TAG = re.compile(r"<[^>]+>")
_PAGE_ZIP = re.compile(r'href="(/[^"]*?/releases/download/[^"]+?\.zip)"')
_PAGE_SIZE = re.compile(r">\s*([0-9.]+)\s*(B|KB|MB|GB)\s*<")
_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}


def _read(url: str, timeout: float) -> str:
    with _request(url, timeout, {"Accept": "text/html,*/*"}) as response:
        return response.read().decode("utf-8", "replace")


def _atom_release(text: str) -> tuple[str, str]:
    """``(tag, notes)`` from the releases Atom feed; the newest entry wins."""
    entry = _ATOM_ENTRY.search(text or "")
    if entry is None:
        raise ValueError(tr("update.check.no.entries"))
    body = entry.group(1)
    link = _ATOM_LINK.search(body)
    tag = ""
    if link:
        tag = link.group(1).rstrip("/").rsplit("/", 1)[-1]
    if not tag:
        raise ValueError(tr("update.check.no.version"))
    content = _ATOM_CONTENT.search(body)
    notes = ""
    if content:
        notes = _ANY_TAG.sub("", html_unescape(content.group(1))).strip()
        notes = notes.replace("\n\n\n", "\n\n")[:4000]
    return tag, notes


def _page_asset(tag: str, timeout: float) -> tuple[str, str, int]:
    """``(href, name, size)`` of the first zip on the release's asset page."""
    text = _read(f"{RELEASES_ASSETS}/{tag}", timeout)
    href = ""
    match = _PAGE_ZIP.search(text)
    if match:
        href = match.group(1)
        if href.startswith("/"):
            href = "https://github.com" + href
    size = 0
    measured = _PAGE_SIZE.search(text)
    if measured:
        size = int(float(measured.group(1)) * _SIZE_UNITS[measured.group(2)])
    return href, (href.rsplit("/", 1)[-1] if href else ""), size


def _check_web(current: str, timeout: float, log=None,
               atom: str = RELEASES_ATOM) -> dict:
    """Newest release read from the release pages.  Raises on any failure."""
    tag, notes = _atom_release(_read(atom, timeout))
    version = tag.lstrip("vV")
    asset_name = ASSET_TEMPLATE.format(version=version)
    # Convention first, so a failure to read the asset list still leaves a
    # working download URL; the page then corrects it if the name differs.
    asset_url = f"{RELEASES_DOWNLOAD}/{tag}/{asset_name}"
    asset_size = 0
    try:
        href, name, size = _page_asset(tag, timeout)
    except Exception as error:              # cosmetic only: the URL above works
        _say(log, tr("update.check.assets", err=_reason(error)))
    else:
        if href:
            asset_url, asset_name, asset_size = href, name, size
    return {
        "available": is_newer(tag, current),
        "current": current,
        "version": version,
        "tag": tag,
        "url": f"https://github.com/Fatmear/OmniCOM/releases/tag/{tag}",
        "notes": notes,
        "asset": asset_name,
        "asset_url": asset_url,
        "asset_size": asset_size,
    }


def _say(log, text: str) -> None:
    if log is not None:
        # A logging callback is never allowed to break the check.
        with contextlib.suppress(Exception):
            log(text)


def check_release(current: str = __version__, timeout: float = 6.0,
                  log=None, progress=None, api: str | None = None,
                  atom: str = RELEASES_ATOM) -> dict:
    """Find the newest release directly from GitHub release pages.

    Returns ``{"ok": bool, "source": "web"|"", "error": str, **info}``.
    Never raises: the whole point is that the dialog can always print a verdict
    instead of waiting forever (R-143).  ``log`` receives progress lines and
    ``progress(done, total)`` drives the bar. ``atom`` is injectable for tests.
    ``api`` is accepted for compatibility but is never contacted.
    """
    errors: list[str] = []

    def step(index: int, text: str) -> None:
        _say(log, text)
        if progress is not None:
            # A progress callback is never allowed to break the check.
            with contextlib.suppress(Exception):
                progress(index, 3)

    step(1, tr("update.check.web"))
    try:
        info = _check_web(current, timeout, log, atom=atom)
    except Exception as error:
        errors.append(tr("update.check.web.failed", err=_reason(error)))
        _say(log, errors[-1])
    else:
        step(3, tr("update.check.web.found", version=info["version"]))
        return {**info, "ok": True, "source": "web", "error": ""}

    if progress is not None:
        # The bar must reach 100% even on failure, otherwise it looks stuck.
        with contextlib.suppress(Exception):
            progress(3, 3)
    return {"ok": False, "source": "", "error": "；".join(errors),
            "current": current, "available": False}


def _reason(error: BaseException) -> str:
    """A one-line, human-sized reason for a failed request."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 403:
            return tr("update.check.forbidden")
        return f"HTTP {error.code} {error.reason}"
    if isinstance(error, urllib.error.URLError):
        return tr("update.check.network", err=error.reason)
    text = str(error) or type(error).__name__
    return text[:160]


class DownloadCancelled(Exception):
    """The user cancelled before the update was committed."""


def _check_cancelled(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise DownloadCancelled()


def download(url: str, target: str, timeout: float = 30.0,
             progress=None, cancelled=None) -> str:
    """Stream the asset to ``target``; ``progress(done, total)`` is optional."""
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    partial = target + ".part"
    try:
        _check_cancelled(cancelled)
        with _request(url, timeout) as response, open(partial, "wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                _check_cancelled(cancelled)
                chunk = response.read(65536)
                _check_cancelled(cancelled)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        _check_cancelled(cancelled)
        os.replace(partial, target)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(partial)
        raise
    return target


# -------------------------------------------------------------------- staging
def updates_dir() -> str:
    path = os.path.join(paths.data_dir(), "updates")
    os.makedirs(path, exist_ok=True)
    return path


def _safe_extract(archive: zipfile.ZipFile, target: str) -> str:
    """Unpack, refusing entries that escape ``target``; return the real root."""
    root = os.path.abspath(target)
    for member in archive.infolist():
        destination = os.path.abspath(os.path.join(root, member.filename))
        if not destination.startswith(root + os.sep) and destination != root:
            raise OSError(f"unsafe archive entry: {member.filename}")
    archive.extractall(root)
    # A release zip usually has one top folder; treat that as the bundle root.
    entries = [item for item in os.listdir(root) if not item.startswith(".")]
    if len(entries) == 1:
        single = os.path.join(root, entries[0])
        if os.path.isdir(single) and not os.path.exists(os.path.join(root, "OmniCOM.pyz")):
            return single
    return root


def stage(zip_path: str, version: str, cancelled=None) -> str:
    """Unpack ``zip_path`` into the updates folder and mark it pending."""
    _check_cancelled(cancelled)
    target = os.path.join(updates_dir(), f"staged-{version}")
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        bundle = _safe_extract(archive, target)
    try:
        _check_cancelled(cancelled)
    except DownloadCancelled:
        shutil.rmtree(target, ignore_errors=True)
        raise
    with open(os.path.join(updates_dir(), PENDING_MARKER), "w",
              encoding="utf-8") as handle:
        json.dump({"version": version, "source": bundle}, handle)
    return bundle


# ------------------------------------------------------------ local package
#: The release folder/zip always names the program archive; its presence is
#: how a user-picked file is recognised as an OmniCOM bundle instead of any
#: zip that happened to be selected (R-121).
BUNDLE_MARKER = "OmniCOM.pyz"

#: ``__version__ = "1.2.0"`` inside the program archive.
VERSION_RE = re.compile(r"""__version__\s*=\s*["']([0-9][0-9.]*)["']""")
#: ``OmniCOM-1.2.0-win64.zip`` -> ``1.2.0``.
NAME_VERSION_RE = re.compile(r"([0-9]+\.[0-9]+(?:\.[0-9]+)?)")


def package_version(path: str) -> str:
    """The version inside ``path``, else one parsed from its name.

    Reading ``omnicom/__init__.py`` out of the zipped ``OmniCOM.pyz`` is the
    only trustworthy answer: a renamed file still reports its real version,
    so the dialog can say what the user is about to install.
    """
    found = ""
    try:
        with zipfile.ZipFile(path) as outer:
            names = outer.namelist()
            marker = next((name for name in names
                           if name.replace("\\", "/").endswith("/" + BUNDLE_MARKER)
                           or name == BUNDLE_MARKER), "")
            if marker:
                with outer.open(marker) as handle:
                    inner_bytes = handle.read()
                with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                    source = inner.read("omnicom/__init__.py").decode("utf-8",
                                                                     "replace")
                    match = VERSION_RE.search(source)
                    if match:
                        found = match.group(1)
    except (OSError, KeyError, zipfile.BadZipFile):
        found = ""
    if found:
        return found
    # Fall back to the shipped file name, e.g. OmniCOM-1.2.0-win64.zip.
    match = NAME_VERSION_RE.search(os.path.basename(path))
    return match.group(1) if match else ""


def looks_like_bundle(path: str) -> bool:
    """True when ``path`` is a zip that contains the program archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                clean = name.replace("\\", "/")
                if clean == BUNDLE_MARKER or clean.endswith("/" + BUNDLE_MARKER):
                    return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def stage_local(zip_path: str) -> dict:
    """Stage a release zip the user picked from disk (R-121).

    The file goes through exactly the same :func:`stage` used by the network
    path, so a local upgrade and a downloaded one end up identical - one code
    path, one set of guarantees about ``omnicom-data/`` being untouched
    (R-108).  A file that is not an OmniCOM bundle is refused by name, because
    unpacking a random zip over the program folder would destroy the install.
    """
    path = os.path.abspath(str(zip_path or ""))
    if not os.path.isfile(path):
        raise OSError(f"找不到文件：{path}")
    if not zipfile.is_zipfile(path):
        raise OSError("这不是一个 zip 升级包")
    if not looks_like_bundle(path):
        raise OSError("这个 zip 里没有 OmniCOM.pyz，不是 OmniCOM 升级包")
    version = package_version(path) or "local"
    # Not a blocker - a user may deliberately reinstall or roll back - but the
    # dialog says so, otherwise "updated to the version I already had" looks
    # like a failure.
    same = version != "local" and parse_version(version) == parse_version(__version__)
    bundle = stage(path, version)
    return {"version": version, "source": bundle, "same_version": same,
            "downgrade": version != "local" and parse_version(version) < parse_version(__version__),
            "path": path}


def pending() -> dict | None:
    marker = os.path.join(updates_dir(), PENDING_MARKER)
    if not os.path.isfile(marker):
        return None
    try:
        with open(marker, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    source = str(data.get("source") or "")
    return data if source and os.path.isdir(source) else None


def clear_pending() -> None:
    marker = os.path.join(updates_dir(), PENDING_MARKER)
    with contextlib.suppress(OSError):
        os.remove(marker)


def _protected(relative: str) -> bool:
    parts = relative.replace("\\", "/").split("/")
    return parts[0].lower() == PROTECTED_DIR


def apply_pending(bundle_dir: str | None = None) -> list[str]:
    """Copy the staged release over the bundle.  Returns the files written.

    ``omnicom-data/`` is never read from the archive and never deleted from the
    bundle, so the user's command library, captures, settings and plugins
    survive the upgrade (R-108).  Existing files under ``plugins/`` are kept
    too - a user plugin is as much "their data" as a command is.
    """
    data = pending()
    if data is None:
        return []
    from .update_worker import apply_files

    source = Path(data["source"])
    target = Path(bundle_dir or paths.app_dir())
    written = apply_files(source, target)
    clear_pending()
    shutil.rmtree(source, ignore_errors=True)
    return written


def result_path() -> Path:
    return Path(updates_dir()) / "result.json"


def take_result() -> dict | None:
    path = result_path()
    claimed = path.with_name(f"result-{os.getpid()}.json")
    try:
        os.replace(path, claimed)
    except FileNotFoundError:
        return None
    try:
        result = json.loads(claimed.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("Invalid installation result")
        return result
    finally:
        claimed.unlink()


class Installer:
    def __init__(self, process, workspace: Path, messages):
        self.process = process
        self.workspace = workspace
        self.messages = messages

    @property
    def pid(self):
        return self.process.pid

    def confirm(self):
        self.process.stdin.write(b"INSTALL\n")
        self.process.stdin.flush()
        self.process.stdin.close()
        if self.messages.get(timeout=60).strip() != b"VISIBLE":
            raise OSError("Installer progress window did not become ready")

    def cancel(self):
        if self.process.poll() is None:
            self.process.stdin.write(b"CANCEL\n")
            self.process.stdin.flush()
            self.process.stdin.close()
        self.process.wait(timeout=60)
        shutil.rmtree(self.workspace, ignore_errors=True)


def launch_installer(bundle_dir: str | None = None,
                     restart_command: list[str] | None = None, *,
                     wait_confirmation: bool = False, progress=None,
                     progress_ui: dict | None = None) -> int | Installer:
    """Start an isolated installer and wait for its ready handshake."""
    data = pending()
    if data is None:
        raise OSError("No staged update")
    target = Path(bundle_dir or paths.app_dir()).resolve()
    workspace = Path(tempfile.mkdtemp(prefix="installer-", dir=updates_dir()))
    process = None
    try:
        script = workspace / "worker.py"
        script.write_bytes(
            files("omnicom").joinpath("update_worker.py").read_bytes())
        if progress_ui is not None:
            progress_ui = dict(progress_ui)
            for key, name in (("icon", "omnicom.ico"), ("logo", "omnicom.png")):
                source = target / "assets" / name
                if not source.is_file():
                    source = Path(paths.resource_file("assets", name) or "")
                if not source.is_file():
                    raise OSError(f"Missing installer branding: {name}")
                destination = workspace / name
                shutil.copy2(source, destination)
                progress_ui[key] = str(destination)
        python = Path(sys.executable)
        if python.parent.resolve() == (target / "runtime").resolve():
            runtime = workspace / "runtime"
            runtime.mkdir()
            candidates = list(python.parent.rglob("*") if progress_ui
                              else python.parent.iterdir())
            candidates = [path for path in candidates if path.is_file()]
            for index, path in enumerate(candidates, 1):
                destination = runtime / path.relative_to(python.parent)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                if progress is not None:
                    progress(index, len(candidates))
            python = runtime / "python.exe"
        restart = restart_command
        if restart is None:
            executable = target / "runtime" / "pythonw.exe"
            if not executable.is_file():
                executable = Path(sys.executable)
            entry = target / "OmniCOM.pyz"
            if not entry.is_file():
                entry = target / "run.py"
            restart = [str(executable), "-B", "-X", "utf8", str(entry)]
            if "--demo" in sys.argv:
                restart.append("--demo")
        job = {
            "pid": os.getpid(),
            "source": data["source"],
            "version": data["version"],
            "target": str(target),
            "marker": str(Path(updates_dir()) / PENDING_MARKER),
            "result": str(result_path()),
            "restart": restart,
            "wait_confirmation": wait_confirmation,
            "progress_ui": progress_ui,
            "ready": str(workspace / "window-ready"),
        }
        job_path = workspace / "job.json"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        log_path = workspace / "installer.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(python), "-I", "-B", "-X", "utf8",
                 str(script), str(job_path)],
                cwd=workspace,
                stdin=subprocess.PIPE if wait_confirmation else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log, close_fds=True,
                creationflags=(0x08000000 if sys.platform == "win32" else 0))
        output = process.stdout
        assert output is not None
        ready: queue.Queue[bytes] = queue.Queue()
        def read_output():
            try:
                for line in output:
                    ready.put(line)
            finally:
                ready.put(b"")
                output.close()

        threading.Thread(target=read_output, daemon=True).start()
        while True:
            message = ready.get(timeout=60).strip()
            if message == b"READY":
                break
            if not message:
                raise OSError(log_path.read_text(
                    encoding="utf-8", errors="replace")
                              or "Installer exited before becoming ready")
            details = json.loads(message)
            if progress is not None:
                progress(details["progress"], details["total"])
        if wait_confirmation:
            return Installer(process, workspace, ready)
        return process.pid
    except Exception:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        shutil.rmtree(workspace, ignore_errors=True)
        raise
