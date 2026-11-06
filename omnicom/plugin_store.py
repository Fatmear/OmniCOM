"""Independent plugin catalog and verified, transactional package management."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from . import paths
from .plugins import API_VERSION

CATALOG_URL = "https://raw.githubusercontent.com/Fatmear/OmniCOM/main/plugins/catalog.json"
PLUGIN_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
VERSION = re.compile(r"\d+(?:\.\d+){1,3}\Z")
HASH = re.compile(r"[a-fA-F0-9]{64}\Z")
MAX_DOWNLOAD = 128 * 1024 * 1024
MAX_UNPACKED = 384 * 1024 * 1024
#: One catalog entry may offer a version history for the picker, but a catalog
#: is parsed on the GUI thread and cached as JSON, so the list stays bounded.
MAX_VERSIONS = 100


class StoreError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class Cancelled(StoreError):
    def __init__(self) -> None:
        super().__init__("cancelled")


def catalog_sources(value=None) -> list[str]:
    values = value.splitlines() if isinstance(value, str) else value or []
    return list(dict.fromkeys(str(url).strip() for url in values if str(url).strip())) or [CATALOG_URL]


def configured_catalogs(settings) -> list[str]:
    value = settings.value("plugins/catalogs", None)
    if value is None:
        legacy = str(settings.value("plugins/catalog", "")).strip()
        if legacy == "https://github.com/Fatmear/OmniCOM/releases/download/plugin-store/catalog.json":
            legacy = ""
        value = legacy or os.environ.get("OMNICOM_PLUGIN_CATALOG", CATALOG_URL)
    return catalog_sources(value)


def version_key(value: str) -> tuple[int, ...]:
    if not VERSION.fullmatch(str(value)):
        return (0, 0, 0, 0)
    parts = tuple(int(part) for part in str(value).split("."))
    return parts + (0,) * (4 - len(parts))


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    local_http = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if parsed.scheme not in ("https", "file") and not local_http:
        raise StoreError("url")
    if parsed.username or parsed.password:
        raise StoreError("url")


def fetch(url: str, limit: int, cancel: threading.Event, progress=None) -> bytes:
    if cancel.is_set():
        raise Cancelled()
    check_url(url)
    chunks = []
    received = 0
    request = urllib.request.Request(url, headers={"User-Agent": "OmniCOM-PluginStore/1", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            check_url(response.geturl())
            total = int(response.headers.get("Content-Length") or 0)
            if total > limit:
                raise StoreError("size")
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > limit:
                    raise StoreError("size")
                chunks.append(chunk)
                if progress:
                    progress(received, total)
            if total and received != total:
                raise StoreError("download")
    except (OSError, ValueError) as exc:
        raise StoreError("network", str(exc)) from exc
    if cancel.is_set():
        raise Cancelled()
    return b"".join(chunks)


#: Fields a catalog entry (or one of its ``versions`` records) may carry.
VERSION_FIELDS = ("version", "api", "url", "sha256", "size", "notes", "date")


def version_entries(item: dict, base_url: str = "") -> list[dict]:
    """Normalise one catalog entry into its version history, newest first.

    A catalog may describe only the latest build (schema 1 does, and every
    existing catalog does) or list a ``versions`` history so the store can
    offer an explicit pick.  Either way the caller sees the same shape, and the
    entry's own ``version`` stays the newest one - a history whose head
    disagrees with it would make the "latest" pointer and the picker lie.
    """
    source = item.get("versions")
    if source is None:
        entries = [{key: item[key] for key in VERSION_FIELDS if key in item}]
        entries[0]["version"] = str(item.get("version", ""))
        entries[0].setdefault("api", item.get("api", 1))
    else:
        if not isinstance(source, list) or not 1 <= len(source) <= MAX_VERSIONS:
            raise StoreError("catalog")
        entries = []
        seen = set()
        for record in source:
            if not isinstance(record, dict):
                raise StoreError("catalog")
            version = str(record.get("version", ""))
            if not VERSION.fullmatch(version) or version in seen:
                raise StoreError("catalog")
            seen.add(version)
            entry = {key: record[key] for key in VERSION_FIELDS if key in record}
            entry["version"] = version
            entry.setdefault("api", item.get("api", 1))
            if not isinstance(entry["api"], int):
                raise StoreError("catalog")
            if entry.get("url"):
                entry["url"] = urllib.parse.urljoin(base_url, str(entry["url"]))
                check_url(entry["url"])
                if not HASH.fullmatch(str(entry.get("sha256", ""))):
                    raise StoreError("catalog")
            entries.append(entry)
    known = {entry["version"] for entry in entries}
    if item.get("version") not in known:
        raise StoreError("catalog")
    entries.sort(key=lambda entry: version_key(entry["version"]), reverse=True)
    if entries[0]["version"] != item.get("version"):
        raise StoreError("catalog")
    for entry in entries:
        if entry is not entries[0] and not entry.get("url"):
            # Older builds are only installable from a package; there is no
            # bundled copy of a previous version to fall back on.
            raise StoreError("catalog")
    return entries


def union_versions(*histories) -> list[dict]:
    """Merge version histories from several catalogs, first source winning."""
    merged: dict[str, dict] = {}
    for history in histories:
        for entry in history or []:
            merged.setdefault(str(entry.get("version")), entry)
    return sorted(merged.values(), key=lambda entry: version_key(entry["version"]), reverse=True)


def select_version(item: dict, version: str) -> dict:
    """Return the catalog record that installs ``version`` of ``item``.

    The row's newest-first history is the only source; a version that is not in
    it (or an installed build that no catalog carries) raises instead of
    silently installing the head, which would turn "go back to 1.0.0" into an
    invisible upgrade.
    """
    for entry in item.get("versions") or []:
        if str(entry.get("version")) == str(version):
            if entry.get("local"):
                # An installed build that no catalog carries - there is nothing
                # to download, and pretending otherwise would reinstall the
                # head instead of the requested version.
                raise StoreError("package")
            record = {key: value for key, value in item.items()
                      if key not in ("versions", "installed_version", "latest_version",
                                     "update_available", "compatible", "installed_downgradable")}
            record.update({key: value for key, value in entry.items() if key != "local"})
            record["version"] = str(entry["version"])
            record.setdefault("api", item.get("api", 1))
            return record
    raise StoreError("package")


def validate_catalog(document, base_url: str = "") -> list[dict]:
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise StoreError("catalog")
    items = document.get("plugins")
    if not isinstance(items, list) or len(items) > 1000:
        raise StoreError("catalog")
    result = []
    seen = set()
    for source in items:
        if not isinstance(source, dict):
            raise StoreError("catalog")
        item = dict(source)
        identifier = str(item.get("id", ""))
        if (not PLUGIN_ID.fullmatch(identifier) or identifier in seen
                or not VERSION.fullmatch(str(item.get("version", "")))):
            raise StoreError("catalog")
        seen.add(identifier)
        if not isinstance(item.get("api", 1), int):
            raise StoreError("catalog")
        if item.get("url"):
            item["url"] = urllib.parse.urljoin(base_url, str(item["url"]))
            check_url(item["url"])
            if not HASH.fullmatch(str(item.get("sha256", ""))):
                raise StoreError("catalog")
        item["versions"] = version_entries(item, base_url)
        result.append(item)
    return result


class PluginStore:
    def __init__(self, data_dir=None, bundled_dir=None, catalog_url=None, *, catalog_urls=None) -> None:
        self.data = Path(data_dir or paths.data_dir())
        self.root = self.data / "plugins"
        self.bundled = Path(bundled_dir or Path(paths.resource_dir()) / "plugins")
        self.catalog_urls = catalog_sources(catalog_urls if catalog_urls is not None else catalog_url or os.environ.get("OMNICOM_PLUGIN_CATALOG", CATALOG_URL))
        self.catalog_url = self.catalog_urls[0]
        self.failures: list[dict[str, str]] = []
        self.cache = self.data / "plugin-catalog.json"
        self.disabled_path = self.data / "plugins-disabled.json"

    def disabled(self) -> set[str]:
        try:
            value = json.loads(self.disabled_path.read_text(encoding="utf-8"))
            return set(value) if isinstance(value, list) else set()
        except (OSError, ValueError, TypeError):
            return set()

    def cached_catalog(self) -> list[dict]:
        cached = self._cached_sources()
        if cached:
            return self._merge(cached)
        for path in (self.bundled / "catalog.json",):
            try:
                return validate_catalog(json.loads(path.read_text(encoding="utf-8")), self.catalog_url)
            except (OSError, ValueError, StoreError):
                continue
        return []

    def _cached_sources(self) -> dict[str, list[dict]]:
        try:
            document = json.loads(self.cache.read_text(encoding="utf-8"))
            catalogs = document.get("catalogs", {})
            return {url: validate_catalog({"schema": 1, "plugins": catalogs[url]}, url)
                    for url in self.catalog_urls if url in catalogs}
        except (OSError, ValueError, TypeError, AttributeError, StoreError):
            return {}

    def _merge(self, catalogs: dict[str, list[dict]]) -> list[dict]:
        merged: dict[str, dict] = {}
        for url in self.catalog_urls:
            for item in catalogs.get(url, []):
                previous = merged.get(item["id"])
                if previous is None:
                    merged[item["id"]] = {**item, "catalog_source": url}
                    continue
                if version_key(item["version"]) > version_key(previous["version"]):
                    # The newer build becomes the head, but the older source may
                    # still offer versions the newer one dropped (R-178): the
                    # picker shows the union, newest first.
                    merged[item["id"]] = {**item, "catalog_source": url,
                                          "versions": union_versions(item.get("versions"),
                                                                     previous.get("versions"))}
                else:
                    previous["versions"] = union_versions(previous.get("versions"),
                                                          item.get("versions"))
        return list(merged.values())

    def check(self, cancel=None, progress=None) -> list[dict]:
        cancel = cancel or threading.Event()
        catalogs = self._cached_sources()
        self.failures = []
        succeeded = 0
        for url in self.catalog_urls:
            try:
                payload = fetch(url, 1024 * 1024, cancel, progress)
                try:
                    document = json.loads(payload)
                except (ValueError, UnicodeError) as exc:
                    raise StoreError("catalog") from exc
                catalogs[url] = validate_catalog(document, url)
                succeeded += 1
            except Cancelled:
                raise
            except StoreError as exc:
                self.failures.append({"url": url, "code": exc.code})
        if not succeeded:
            raise StoreError(self.failures[0]["code"])
        result = self._merge(catalogs)
        atomic_json(self.cache, {"schema": 1, "plugins": result, "catalogs": catalogs})
        return result

    def installed(self) -> dict[str, dict]:
        result = {}
        disabled = self.disabled()
        for root in (self.root, self.bundled):
            if not root.is_dir():
                continue
            for folder in root.iterdir():
                if not folder.is_dir() or folder.is_symlink():
                    continue
                try:
                    item = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
                    identifier = str(item.get("id", ""))
                    if not PLUGIN_ID.fullmatch(identifier) or identifier in disabled:
                        continue
                    if identifier not in result:
                        result[identifier] = {**item, "path": str(folder)}
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
        return result

    def rows(self, catalog: list[dict]) -> list[dict]:
        installed = self.installed()
        remote = {item["id"]: item for item in catalog}
        rows = []
        for identifier in sorted(installed.keys() | remote.keys()):
            current = installed.get(identifier)
            latest = remote.get(identifier)
            item = dict(latest or current)
            current_version = str(current.get("version", "0.0.0")) if current else None
            item["installed_version"] = current_version
            item["latest_version"] = str(latest["version"]) if latest else None
            item["update_available"] = bool(current and latest and version_key(latest["version"]) > version_key(current.get("version", "0")))
            item["compatible"] = int(item.get("api", 1)) <= API_VERSION
            # R-178: the picker needs one flat, newest-first list that always
            # contains what is installed (a locally built copy may not be in any
            # catalog) and always offers at least the latest build.
            versions = list((latest or {}).get("versions") or [])
            if current_version and current_version not in {str(entry["version"]) for entry in versions}:
                versions.append({"version": current_version,
                                 "api": int(current.get("api", 1)), "local": True})
            if not versions and latest is not None:
                versions = [{key: latest[key] for key in VERSION_FIELDS if key in latest}]
            versions.sort(key=lambda entry: version_key(entry["version"]), reverse=True)
            item["versions"] = versions
            item["installed_downgradable"] = bool(
                current and versions and any(
                    version_key(entry["version"]) < version_key(current_version)
                    and entry.get("url") for entry in versions))
            rows.append(item)
        return rows

    @contextlib.contextmanager
    def operation(self):
        self.data.mkdir(parents=True, exist_ok=True)
        handle = (self.data / "plugins.lock").open("a+b")
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                if handle.seek(0, 2) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise StoreError("busy") from exc
            else:
                import fcntl

                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise StoreError("busy") from exc
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    def _target(self, identifier: str) -> Path:
        if not PLUGIN_ID.fullmatch(identifier):
            raise StoreError("package")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / identifier
        if self.root.is_symlink() or self.root.is_junction() or target.is_symlink() or target.is_junction():
            raise StoreError("path")
        return target

    def restore(self, identifier: str) -> None:
        with self.operation():
            self._target(identifier)
            if not (self.bundled / identifier / "plugin.json").is_file():
                raise StoreError("package")
            atomic_json(self.disabled_path, sorted(self.disabled() - {identifier}))

    def install(self, item: dict, cancel=None, progress=None) -> None:
        # ``rows()`` decorates an entry with a derived ``versions`` history and a
        # ``local`` marker; neither belongs to the package that gets verified,
        # and a local record has no package at all, so it must never reach the
        # catalog validator (R-178).
        record = {key: value for key, value in item.items() if key not in ("versions", "local")}
        item = validate_catalog({"schema": 1, "plugins": [record]}, self.catalog_url)[0]
        identifier = item["id"]
        if int(item.get("api", 1)) > API_VERSION:
            raise StoreError("api")
        if (item.get("python") and item["python"] != f"{sys.version_info.major}.{sys.version_info.minor}") or (item.get("platform") and item["platform"] != "win_amd64"):
            raise StoreError("api")
        if not item.get("url"):
            try:
                manifest = json.loads((self.bundled / identifier / "plugin.json").read_text(encoding="utf-8"))
                if manifest.get("version") != item["version"]:
                    raise StoreError("package")
            except (OSError, ValueError) as exc:
                raise StoreError("package") from exc
            self.restore(identifier)
            return
        cancel = cancel or threading.Event()
        payload = fetch(item["url"], MAX_DOWNLOAD, cancel, progress)
        if hashlib.sha256(payload).hexdigest().lower() != item["sha256"].lower():
            raise StoreError("hash")
        with self.operation():
            target = self._target(identifier)
            work = self.data / "plugin-updates"
            work.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="stage-", dir=work) as temporary:
                stage = Path(temporary)
                archive_path = stage / "package.zip"
                archive_path.write_bytes(payload)
                candidate = stage / identifier
                try:
                    with zipfile.ZipFile(archive_path) as archive:
                        self._extract(archive, stage, identifier, cancel)
                    manifest = json.loads((candidate / "plugin.json").read_text(encoding="utf-8"))
                except (OSError, ValueError, zipfile.BadZipFile) as exc:
                    raise StoreError("package") from exc
                if (manifest.get("id") != identifier or manifest.get("version") != item["version"]
                    or manifest.get("api", 1) != item.get("api", 1)
                        or int(manifest.get("api", 1)) > API_VERSION):
                    raise StoreError("package")
                entry = PurePosixPath(str(manifest.get("entry", "")))
                if not entry.parts or entry.is_absolute() or ".." in entry.parts or ":" in str(entry) or "\\" in str(entry):
                    raise StoreError("path")
                if not candidate.joinpath(*entry.parts).is_file():
                    raise StoreError("package")
                if cancel.is_set():
                    raise Cancelled()
                backup = work / (identifier + "-backup-" + uuid.uuid4().hex)
                had_target = target.exists()
                if had_target:
                    target.rename(backup)
                try:
                    candidate.rename(target)
                    atomic_json(self.disabled_path, sorted(self.disabled() - {identifier}))
                except BaseException:
                    if target.exists():
                        shutil.rmtree(target)
                    if had_target:
                        backup.rename(target)
                    raise

    @staticmethod
    def _extract(archive, stage: Path, identifier: str, cancel: threading.Event) -> None:
        members = archive.infolist()
        if len(members) > 20000 or sum(item.file_size for item in members) > MAX_UNPACKED:
            raise StoreError("size")
        seen = set()
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)), *(f"LPT{number}" for number in range(1, 10))}
        for item in members:
            path = PurePosixPath(item.filename)
            if (not path.parts or path.parts[0] != identifier or path.is_absolute()
                    or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or any(part.rstrip(" .") != part or part.split(".")[0].upper() in reserved for part in path.parts)):
                raise StoreError("path")
            key = path.as_posix().lower()
            if key in seen:
                raise StoreError("path")
            seen.add(key)
        for item in members:
            if cancel.is_set():
                raise Cancelled()
            destination = stage.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)

    def uninstall(self, identifier: str) -> None:
        with self.operation():
            target = self._target(identifier)
            old_disabled = self.disabled()
            atomic_json(self.disabled_path, sorted(old_disabled | {identifier}))
            try:
                if target.exists():
                    work = self.data / "plugin-updates"
                    work.mkdir(parents=True, exist_ok=True)
                    target.rename(work / (identifier + "-removed-" + uuid.uuid4().hex))
            except BaseException:
                atomic_json(self.disabled_path, sorted(old_disabled))
                raise
