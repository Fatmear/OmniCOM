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

import json
import os
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
        "enabled": True,
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


class CommandStore(QObject):
    changed = Signal()

    def __init__(self, path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.path = path or os.path.join(paths.data_dir(), "commands.json")
        self.items: list[dict] = []
        self.groups: list[dict] = []
        self._load()

    # -------------------------------------------------------------- storage
    def _load(self) -> None:
        """Read either the legacy bare list or the newer envelope."""
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self._seed()
            return

        if isinstance(raw, list):
            entries, raw_groups = raw, []
        elif isinstance(raw, dict):
            entries = raw.get("items") or []
            raw_groups = raw.get("groups") or []
        else:
            self._seed()
            return

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

        self.items = items
        self.groups = groups
        if not self.items:
            self._seed()

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
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"version": EXPORT_VERSION, "groups": self.groups,
                           "items": self.items}, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

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
            self.items = [item for item in self.items if item.get("group") != group_id]
        else:
            for item in self.items:
                if item.get("group") == group_id:
                    item["group"] = UNGROUPED
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

    def remove_many(self, item_ids) -> int:
        """Delete several commands at once (R-092)."""
        wanted = set(item_ids)
        before = len(self.items)
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
        return [item for item in self.items if item.get("enabled", True)]

    def split_lines(self, raw: str) -> list[str]:
        out = []
        for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                out.append(stripped)
        return out
