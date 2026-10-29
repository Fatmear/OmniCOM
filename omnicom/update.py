"""Software update: ask GitHub for the newest release, and install it safely.

Three separate jobs, so each can be tested without a window or a network:

``check``
    one HTTPS request to the releases API; anything unexpected (offline, proxy,
    rate limit, malformed JSON) comes back as ``None`` instead of raising - an
    update check must never be able to disturb the app (R-107).
``stage``
    download the release asset and unpack it next to the data folder.  Nothing
    in the running bundle is touched while the program is open.
``apply_pending``
    called at startup *before* any widget exists: copy the staged files over the
    bundle, **skipping ``omnicom-data/``**, so a version upgrade can never lose
    commands, captures, settings or plugins (R-108).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import urllib.error
import urllib.request
import zipfile

from . import __version__, paths

#: Public endpoint - no token needed for an unauthenticated read.
RELEASES_API = "https://api.github.com/repos/Fatmear/OmniCOM/releases/latest"
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
    merged = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    merged.update(headers or {})
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=merged), timeout=timeout)


def check(current: str = __version__, timeout: float = 3.0,
          api: str = RELEASES_API) -> dict | None:
    """Return ``{"available", "version", "url", "notes", "asset", ...}`` or None.

    Never raises: the caller runs this in the background and a failure simply
    means "no news" (R-107).
    """
    try:
        with _request(api, timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not tag:
        return None
    asset_name, asset_url, asset_size = "", "", 0
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".zip"):
            asset_name = name
            asset_url = str(asset.get("browser_download_url") or "")
            asset_size = int(asset.get("size") or 0)
            break
    return {
        "available": is_newer(tag, current),
        "current": current,
        "version": tag.lstrip("vV"),
        "tag": tag,
        "url": str(payload.get("html_url") or RELEASES_PAGE),
        "notes": str(payload.get("body") or "")[:4000],
        "asset": asset_name,
        "asset_url": asset_url,
        "asset_size": asset_size,
    }


def download(url: str, target: str, timeout: float = 30.0,
             progress=None) -> str:
    """Stream the asset to ``target``; ``progress(done, total)`` is optional."""
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    partial = target + ".part"
    with _request(url, timeout) as response, open(partial, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)
    os.replace(partial, target)
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


def stage(zip_path: str, version: str) -> str:
    """Unpack ``zip_path`` into the updates folder and mark it pending."""
    target = os.path.join(updates_dir(), f"staged-{version}")
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        bundle = _safe_extract(archive, target)
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
    same = bool(version) and version != "local" and not is_newer(version)
    bundle = stage(path, version)
    return {"version": version, "source": bundle, "same_version": same,
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
    source = str(data["source"])
    target = os.path.abspath(bundle_dir or paths.app_dir())
    if os.path.abspath(source) == target:
        return []
    written: list[str] = []
    for base, folders, files in os.walk(source):
        folders[:] = [item for item in folders if item.lower() != PROTECTED_DIR]
        for name in files:
            full = os.path.join(base, name)
            relative = os.path.relpath(full, source)
            if _protected(relative):
                continue
            destination = os.path.join(target, relative)
            keep = (relative.replace("\\", "/").lower().startswith("plugins/")
                    and os.path.exists(destination))
            if keep:
                continue
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(full, destination)
            written.append(relative)
    clear_pending()
    shutil.rmtree(source, ignore_errors=True)
    return written
