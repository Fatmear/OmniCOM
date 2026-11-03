"""Extension command store (JSON, per-user data dir) and payload encoding.

Data model
----------
``items`` is a flat list of command dicts, each carrying an optional ``group``
id.  ``groups`` is a list of ``{id, name, note, collapsed}``.  A flat list keeps
ordering and "send in order" trivial while still allowing groups, and old
``commands.json`` files (a bare list, no group fields) stay valid.

Every command also carries an optional ``note``, shown under its name and
included in exports.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
import uuid
from collections.abc import Iterable

from PySide6.QtCore import QObject, Signal

from . import paths
from .i18n import tr

LINE_ENDS = {"none": b"", "cr": b"\r", "lf": b"\n", "crlf": b"\r\n"}

#: Bumped when the export envelope changes; importers must handle 1.
EXPORT_VERSION = 1
UNGROUPED = ""
EXPORT_FORMAT = "omnicom.commands"


def _defaults() -> dict:
    return {
        "id": uuid.uuid4().hex[:10],
        "text": "AT",
        "hex": False,
        "line_end": "crlf",
        "delay": 120,
        "interval": 1000,
        "timer": False,
        # Off by default (R-127): a freshly added command must not join
        # 依次发送 until the user ticks it.  Before this, every command the
        # user created was live immediately and 依次发送 fired the whole
        # library - including the one-liners added while debugging.
        "enabled": False,
        "group": UNGROUPED,
        "note": "",
    }


def new_group(name: str = "", note: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name, "note": note, "collapsed": False}


def decode_escapes(text: str) -> str:
    """Turn literal \\r \\n \\t \\0 \\xNN into real characters."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "\\" or index + 1 >= length:
            out.append(char)
            index += 1
            continue
        nxt = text[index + 1]
        simple = {"r": "\r", "n": "\n", "t": "\t", "0": "\0", "\\": "\\"}
        if nxt in simple:
            out.append(simple[nxt])
            index += 2
        elif nxt == "x" and index + 3 < length:
            try:
                out.append(chr(int(text[index + 2:index + 4], 16)))
                index += 4
            except ValueError:
                out.append(char)
                index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)


def parse_hex(text: str) -> bytes | None:
    """Flexible hex input: ``01 03``, ``0x01,0x03`` or ``0103``."""
    cleaned = text.replace("0x", " ").replace("0X", " ")
    tokens: list[str] = []
    current = ""
    for char in cleaned:
        if char.isdigit() or char.upper() in "ABCDEF":
            current += char
        else:
            if current:
                tokens.append(current)
            current = ""
    if current:
        tokens.append(current)
    if not tokens:
        return b""
    joined = "".join(tokens)
    if len(joined) % 2:
        if len(tokens) > 1 and all(len(token) <= 2 for token in tokens):
            return bytes(int(token, 16) & 0xFF for token in tokens)
        return None
    try:
        return bytes.fromhex(joined)
    except ValueError:
        if all(len(token) <= 2 for token in tokens):
            return bytes(int(token, 16) & 0xFF for token in tokens)
        return None


def payload_for(item: dict) -> bytes:
    """Bytes to put on the wire for one command."""
    text = str(item.get("text") or "")
    if item.get("hex"):
        return parse_hex(text) or b""
    body = decode_escapes(text).encode("utf-8")
    return body + LINE_ENDS.get(str(item.get("line_end") or "crlf"), b"\r\n")


def split_comment(line: str) -> tuple[str, str]:
    """Split one pasted line into ``(command, note)`` (R-128).

    ``#`` starts the note: ``net sta enable   # 起 supplicant`` yields the
    command ``net sta enable`` (the padding before ``#`` is dropped with
    ``rstrip``, and indentation is trimmed like it always was) and the note
    ``起 supplicant``.  ``\\#`` is an escaped hash and stays in the command, so a
    payload that really contains ``#`` is never silently cut in half.
    """
    search = 0
    while True:
        cut = line.find("#", search)
        if cut < 0:
            return line.strip().replace("\\#", "#"), ""
        if cut and line[cut - 1] == "\\":
            search = cut + 1
            continue
        command = line[:cut].strip().replace("\\#", "#")
        return command, line[cut + 1:].strip()


# ---------------------------------------------------------------- storage io
def _signature(path: str) -> tuple | None:
    """``(mtime_ns, size)`` of the library file, or None when it is missing."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _read_library(path: str) -> tuple[list[dict] | None, list[dict], str]:
    """Read one library file.

    Returns ``(items, groups, error)``.  ``items is None`` (with no error) means
    "there is no file yet", which is the only case that may fall back to the
    factory seed; anything else that fails comes back as ``error`` so the caller
    can keep the damaged file instead of erasing it (R-135).
    """
    if not os.path.exists(path):
        return None, [], ""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as err:
        return [], [], f"{type(err).__name__}: {err}"
    except OSError as err:
        return [], [], f"{type(err).__name__}: {err}"

    if isinstance(raw, list):
        entries, raw_groups = raw, []
    elif isinstance(raw, dict):
        entries = raw.get("items") or []
        raw_groups = raw.get("groups") or []
    else:
        return [], [], f"unexpected JSON root: {type(raw).__name__}"

    items = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        merged = _defaults()
        merged.update(entry)
        merged["id"] = entry.get("id") or merged["id"]
        merged["group"] = entry.get("group") or UNGROUPED
        merged["note"] = entry.get("note") or ""
        items.append(merged)

    groups = []
    for entry in raw_groups:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        groups.append({
            "id": entry["id"],
            "name": entry.get("name") or "",
            "note": entry.get("note") or "",
            "collapsed": bool(entry.get("collapsed")),
        })

    known = {group["id"] for group in groups}
    for item in items:
        if item["group"] and item["group"] not in known:
            item["group"] = UNGROUPED
    return items, groups, ""


class _library_lock:
    """Best-effort exclusive lock around a read-modify-write of the library.

    Same primitive as the CLI's ``PortLock`` (``msvcrt.locking`` on Windows,
    ``flock`` elsewhere) but on a sidecar ``.lock`` file, so the atomic
    ``os.replace`` stays inside the critical section.  When locking is not
    available the library simply behaves as before - a portable build on an
    exotic filesystem must not lose the ability to save.
    """

    def __init__(self, path: str, timeout: float = 3.0) -> None:
        self.path = f"{path}.lock"
        self.timeout = timeout
        self._handle = None

    def __enter__(self):
        try:
            # Held open on purpose: the lock lives on the handle, so it has to
            # stay open until ``__exit__`` closes it.
            handle = open(self.path, "a+")
        except OSError:
            return self
        deadline = time.monotonic() + self.timeout
        while True:
            if _try_lock(handle):
                self._handle = handle
                return self
            if time.monotonic() >= deadline:
                handle.close()
                return self
            time.sleep(0.02)

    def __exit__(self, *_exc) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        with contextlib.suppress(Exception):
            _unlock(handle)
        with contextlib.suppress(Exception):
            handle.close()


def _try_lock(handle) -> bool:
    try:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except ImportError:
        pass
    except OSError:
        return False
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        return True              # no primitive available: stay functional
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    try:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    except ImportError:
        pass
    except OSError:
        return
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass


class CommandStore(QObject):
    changed = Signal()

    def __init__(self, path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.path = path or os.path.join(paths.data_dir(), "commands.json")
        self.items: list[dict] = []
        self.groups: list[dict] = []
        #: Set by any call that changed **which order** the commands are in, so
        #: the merge in ``save()`` knows whose order is the newer intent.
        self._order_dirty = False
        #: ``(mtime_ns, size)`` of the file as this instance last saw it; a
        #: different value means another window wrote in the meantime.
        self._seen: tuple | None = None
        #: Ids this instance deleted, so the merge below never resurrects them
        #: from the copy another window still has.
        self._removed: set[str] = set()
        self._dirty = False
        #: Human-readable note when the file had to be recovered (shown as a
        #: toast by the panel, never swallowed - R-135).
        self.load_warning = ""
        self._load()

    # -------------------------------------------------------------- storage
    def _load(self) -> None:
        """Read either the legacy bare list or the newer envelope."""
        items, groups, error = _read_library(self.path)
        if error:
            self._recover(error)
            return
        if items is None:
            # No file yet: this really is the first run.
            self._seed()
            return
        self.items = items
        self.groups = groups
        self._seen = _signature(self.path)

    def _recover(self, error: str) -> None:
        """Keep a damaged library instead of quietly restoring the defaults.

        ``_load()`` used to fall back to ``_seed()`` on *any* read error, so a
        truncated or half-written ``commands.json`` came back as the three
        factory commands in their factory order - indistinguishable from
        "my ordering was forgotten again" (R-135).
        """
        backup = ""
        with contextlib.suppress(OSError):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = f"{self.path}.bad-{stamp}"
            os.replace(self.path, backup)
        self.load_warning = tr("cmd.load.broken",
                               name=os.path.basename(self.path),
                               backup=os.path.basename(backup) or "—",
                               err=error)
        self._seed()
        self._seen = _signature(self.path)

    def _seed(self) -> None:
        group = new_group("常用 AT", "开机自检与基本信息")
        self.groups = [group]
        self.items = []
        for text, delay, note in (("AT", 120, "模组是否在线"),
                                  ("AT+CGMR", 120, "固件版本"),
                                  ("AT+CSQ", 200, "信号质量")):
            item = _defaults()
            item.update({"text": text, "delay": delay, "note": note,
                         "group": group["id"]})
            self.items.append(item)

    def save(self) -> None:
        """Write the library, merging anything another window wrote meanwhile.

        Two windows (or a window and the CLI) share one ``commands.json``.  This
        used to serialise the whole in-memory list, so the window that loaded
        the file first silently restored its own stale copy and undid the other
        window's work - a dropped re-order reappeared after a restart exactly
        this way (R-135).  The write is now a locked read-modify-write, and a
        merge decides whose order wins.
        """
        with contextlib.suppress(OSError):
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with _library_lock(self.path):
            if _signature(self.path) != self._seen:
                self._merge_with_disk()
            payload = {"version": EXPORT_VERSION, "groups": self.groups,
                       "items": self.items}
            # A unique temp name per write: the fixed ``.tmp`` suffix let two
            # processes write the same temp file and publish a half-written
            # library (the other half of the "order forgotten" symptom).
            handle, tmp = tempfile.mkstemp(prefix=".commands-", suffix=".tmp",
                                           dir=os.path.dirname(self.path) or ".")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            self._seen = _signature(self.path)
        self._order_dirty = False
        self._removed.clear()

    def _merge_with_disk(self) -> None:
        """Fold changes another instance made into our snapshot before writing."""
        disk_items, disk_groups, error = _read_library(self.path)
        if error or disk_items is None:
            return
        known = {group["id"] for group in self.groups}
        for group in disk_groups:
            if group["id"] not in known:
                self.groups.append(group)
                known.add(group["id"])
        ours = {item["id"] for item in self.items}
        # Commands the other window added must survive, right after the
        # neighbour they follow on disk.  Commands *we* deleted must not come
        # back, which is what the tombstones are for.
        for position, item in enumerate(disk_items):
            if item["id"] in ours or item["id"] in self._removed:
                continue
            self.items.insert(self._neighbour_index(disk_items, position), item)
            ours.add(item["id"])
        if not self._order_dirty:
            # Nobody re-ordered here, so the newest order on disk is the truth;
            # this is what keeps a re-order from the other window alive.
            rank = {item["id"]: index for index, item in enumerate(disk_items)}
            self.items.sort(key=lambda item: rank.get(item["id"], len(rank)))

    def _neighbour_index(self, disk_items: list[dict], position: int) -> int:
        """Index in ``self.items`` that puts a disk-only command back in place."""
        for offset in range(position - 1, -1, -1):
            anchor = disk_items[offset]["id"]
            for index, item in enumerate(self.items):
                if item["id"] == anchor:
                    return index + 1
        return len(self.items)

    # ------------------------------------------------------------------ CRUD
    def add(self, texts: Iterable[str], patch: dict | None = None) -> list[dict]:
        added: list[dict] = []
        for text in texts:
            if not text:
                continue
            item = _defaults()
            item["text"] = text
            if patch:
                item.update(patch)
            self.items.append(item)
            added.append(item)
        if added:
            self._commit()
        return added

    def update(self, item_id: str, patch: dict, notify: bool = True) -> None:
        for item in self.items:
            if item["id"] == item_id:
                item.update(patch)
                if notify:
                    self._commit()
                else:
                    self._dirty = True
                return

    def remove(self, item_id: str) -> None:
        # Tombstone first: another window still holds this command in its own
        # snapshot and would otherwise re-add it on the next merge (R-135).
        self._removed.add(item_id)
        self.items = [item for item in self.items if item["id"] != item_id]
        self._commit()

    def duplicate(self, item_id: str) -> None:
        for index, item in enumerate(self.items):
            if item["id"] == item_id:
                clone = dict(item)
                clone["id"] = uuid.uuid4().hex[:10]
                clone["timer"] = False
                self.items.insert(index + 1, clone)
                self._commit()
                return

    def clear(self) -> None:
        self.items = []
        self.groups = []
        self._commit()

    def _commit(self) -> None:
        self.save()
        self.changed.emit()

    # ---------------------------------------------------------------- groups
    def group_by_id(self, group_id: str) -> dict | None:
        for group in self.groups:
            if group["id"] == group_id:
                return group
        return None

    def add_group(self, name: str, note: str = "") -> dict:
        group = new_group(name, note)
        group["name"] = self.validate_group_name(group["name"])
        self.groups.append(group)
        self._commit()
        return group

    def validate_group_name(self, name: str, exclude_id: str = "") -> str:
        name = name.strip()
        if any(group["id"] != exclude_id and group.get("name", "").strip() == name
               for group in self.groups):
            raise ValueError(tr("cmd.group.duplicate", name=name))
        return name

    def update_group(self, group_id: str, patch: dict,
                     notify: bool = True) -> None:
        group = self.group_by_id(group_id)
        if group is None:
            return
        if "name" in patch:
            patch = {**patch, "name": self.validate_group_name(patch["name"], group_id)}
        group.update(patch)
        if notify:
            self._commit()
        else:
            # Collapsing a group only changes how it is drawn.  Emitting
            # `changed` rebuilt every row, and writing the file cost ~30 ms of
            # `os.replace` - together that is the stutter the user reported.
            # The flag is flushed lazily (see `flush`).
            self._dirty = True


    def flush(self) -> None:
        """Write pending cosmetic changes (collapse state) to disk."""
        if getattr(self, "_dirty", False):
            self._dirty = False
            self.save()

    def remove_group(self, group_id: str, drop_commands: bool = False) -> None:
        self.groups = [group for group in self.groups if group["id"] != group_id]
        if drop_commands:
            self._removed |= {item["id"] for item in self.items
                              if item.get("group") == group_id}
            self.items = [item for item in self.items if item.get("group") != group_id]
        else:
            for item in self.items:
                if item.get("group") == group_id:
                    item["group"] = UNGROUPED
        self._order_dirty = True
        self._commit()

    def move_to_group(self, item_id: str, group_id: str) -> None:
        for item in self.items:
            if item["id"] == item_id:
                item["group"] = group_id or UNGROUPED
                self._commit()
                return

    # -------------------------------------------------------------- batches
    def move_many(self, item_ids, group_id: str) -> int:
        """Move several commands at once; one write, one ``changed`` signal.

        Doing it per item (``move_to_group`` in a loop) rewrote the library file
        once per command, which is both slow and a chance to lose the file if the
        process dies in the middle (R-092).
        """
        wanted = set(item_ids)
        target = group_id or UNGROUPED
        moved = 0
        for item in self.items:
            if item["id"] in wanted and item.get("group") != target:
                item["group"] = target
                moved += 1
        if moved:
            self._commit()
        return moved

    def reorder(self, item_ids, group_id: str, index: int = -1) -> int:
        """Move commands into ``group_id`` at position ``index`` (R-130).

        A drag-and-drop is a re-group **and** a re-order at the same time, which
        ``move_many()`` cannot express: it only rewrites ``group`` and leaves the
        item where it was in ``items``.  Calling it and then shuffling the list
        would write the library twice and leave a moment where the dropped
        command sits at its old place inside the new group.

        ``index`` counts the commands already in the target bucket (without the
        dragged ones); a negative or too-large index appends.  Display order
        between groups comes from ``self.groups``, so only the order *inside*
        the target bucket has to be right here.
        """
        wanted = {str(item_id) for item_id in item_ids}
        moving = [item for item in self.items if item["id"] in wanted]
        if not moving:
            return 0
        target = group_id or UNGROUPED
        rest = [item for item in self.items if item["id"] not in wanted]
        bucket = [item["id"] for item in rest
                  if (item.get("group") or UNGROUPED) == target]
        # Insert before the anchor command, or after the last one when the drop
        # landed past the end (or the bucket is empty).
        if bucket and 0 <= index < len(bucket):
            anchor = bucket[index]
            position = next(pos for pos, item in enumerate(rest)
                            if item["id"] == anchor)
        elif bucket:
            anchor = bucket[-1]
            position = next(pos for pos, item in enumerate(rest)
                            if item["id"] == anchor) + 1
        else:
            position = len(rest)
        regrouped = any((item.get("group") or UNGROUPED) != target
                        for item in moving)
        for item in moving:
            item["group"] = target
        rest[position:position] = moving
        order_changed = [item["id"] for item in self.items] != [
            item["id"] for item in rest]
        self.items = rest
        if regrouped or order_changed:
            # This instance is the one that decided the order, so its snapshot
            # must win over the copy on disk (R-135 / R-130).
            self._order_dirty = True
            self._commit()
        return len(moving)

    def remove_many(self, item_ids) -> int:
        """Delete several commands at once (R-092)."""
        wanted = set(item_ids)
        before = len(self.items)
        self._removed |= wanted
        self.items = [item for item in self.items if item["id"] not in wanted]
        removed = before - len(self.items)
        if removed:
            self._commit()
        return removed

    def groups_with_items(self) -> list[tuple[dict | None, list[dict]]]:
        """``[(group, items)]`` in display order; ``None`` is the ungrouped bucket."""
        buckets: dict[str, list[dict]] = {group["id"]: [] for group in self.groups}
        loose: list[dict] = []
        for item in self.items:
            group_id = item.get("group") or UNGROUPED
            if group_id in buckets:
                buckets[group_id].append(item)
            else:
                loose.append(item)
        ordered: list[tuple[dict | None, list[dict]]] = [
            (group, buckets[group["id"]]) for group in self.groups]
        if loose:
            ordered.append((None, loose))
        return ordered

    # ------------------------------------------------------------- transfer
    def export_payload(self, items: list[dict]) -> dict:
        """Self-contained bundle: the commands plus the groups they reference."""
        used = {item.get("group") for item in items if item.get("group")}
        groups = [dict(group) for group in self.groups if group["id"] in used]
        return {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
            "groups": groups,
            # ``timer`` is runtime state, never exported.
            "items": [{key: value for key, value in item.items() if key != "timer"}
                      for item in items],
        }

    def export_to(self, path: str, items: list[dict]) -> int:
        payload = self.export_payload(items)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return len(payload["items"])

    def import_from(self, path: str) -> int:
        """Merge a bundle. Imported ids are always regenerated (no collisions)."""
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, list):
            payload: dict = {"groups": [], "items": raw}
        elif isinstance(raw, dict):
            payload = raw
        else:
            raise ValueError("无法识别的文件格式")

        group_map: dict[str, str] = {}
        for group in payload.get("groups") or []:
            if not isinstance(group, dict) or not group.get("id"):
                continue
            created = new_group((group.get("name") or "").strip(), group.get("note") or "")
            existing = next((item for item in self.groups
                             if item.get("name", "").strip() == created["name"]), None)
            if existing is not None:
                group_map[group["id"]] = existing["id"]
                continue
            self.groups.append(created)
            group_map[group["id"]] = created["id"]

        added = 0
        for entry in payload.get("items") or []:
            if not isinstance(entry, dict) or not entry.get("text"):
                continue
            item = _defaults()
            item.update(entry)
            item["id"] = uuid.uuid4().hex[:10]
            item["timer"] = False
            item["group"] = group_map.get(entry.get("group") or UNGROUPED, UNGROUPED)
            self.items.append(item)
            added += 1
        if added or group_map:
            self._commit()
        return added

    # ------------------------------------------------------------------ help
    def enabled_items(self) -> list[dict]:
        # Default False to match ``_defaults()`` (R-127): a stored item that
        # somehow lost the key is treated as "not ticked", never as on.
        return [item for item in self.items if item.get("enabled", False)]

    def split_lines(self, raw: str) -> list[str]:
        """Command texts of a pasted block, one per line (comments stripped)."""
        return [text for text, _note in self.split_entries(raw)]

    def split_entries(self, raw: str) -> list[tuple[str, str]]:
        """``[(command, note)]`` for a pasted block (R-128).

        A line whose text after the cut-off is empty - a pure ``# comment`` -
        creates nothing, so a paste can carry explanations between commands.
        """
        out: list[tuple[str, str]] = []
        for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            text, note = split_comment(line)
            if text:
                out.append((text, note))
        return out

    def add_entries(self, entries: Iterable[tuple[str, str]],
                    patch: dict | None = None) -> list[dict]:
        """Add ``(text, note)`` pairs, one note per command (R-128).

        ``add()`` applies a single shared patch, so it cannot express "this
        line's tail is this command's note".  Everything else - the group patch
        and the unticked default - behaves exactly like ``add()``.
        """
        added: list[dict] = []
        for text, note in entries:
            if not text:
                continue
            item = _defaults()
            item["text"] = text
            if note:
                item["note"] = note
            if patch:
                item.update(patch)
            self.items.append(item)
            added.append(item)
        if added:
            self._commit()
        return added
