"""Extension command store (JSON, per-user data dir) and payload encoding."""

from __future__ import annotations

import json
import os
import uuid
from typing import Iterable

from PySide6.QtCore import QObject, Signal

from . import paths

LINE_ENDS = {"none": b"", "cr": b"\r", "lf": b"\n", "crlf": b"\r\n"}


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
    }


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
        if nxt == "r":
            out.append("\r"); index += 2
        elif nxt == "n":
            out.append("\n"); index += 2
        elif nxt == "t":
            out.append("\t"); index += 2
        elif nxt == "0":
            out.append("\0"); index += 2
        elif nxt == "\\":
            out.append("\\"); index += 2
        elif nxt == "x" and index + 3 < length:
            try:
                out.append(chr(int(text[index + 2:index + 4], 16)))
                index += 4
            except ValueError:
                out.append(char); index += 1
        else:
            out.append(char); index += 1
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
        # e.g. "0 1 3" -> odd nibbles are a typo, not a byte string
        if len(tokens) > 1 and all(len(token) <= 2 for token in tokens):
            return bytes(int(token, 16) & 0xFF for token in tokens)
        return None
    try:
        return bytes.fromhex(joined)
    except ValueError:
        if all(len(token) <= 2 for token in tokens):
            return bytes(int(token, 16) & 0xFF for token in tokens)
        return None


def payload_of(item: dict) -> bytes:
    if item.get("hex"):
        parsed = parse_hex(item.get("text", ""))
        return parsed if parsed is not None else b""
    return decode_escapes(item.get("text", "")).encode("utf-8") + \
        LINE_ENDS.get(item.get("line_end", "crlf"), b"")


class CommandStore(QObject):
    changed = Signal()

    def __init__(self, path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.path = path or os.path.join(paths.data_dir(), "commands.json")
        self.items: list[dict] = self._load()

    # -------------------------------------------------------------- storage
    def _load(self) -> list[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, list):
                items = []
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    merged = _defaults()
                    merged.update(entry)
                    merged["id"] = entry.get("id") or merged["id"]
                    items.append(merged)
                if items:
                    return items
        except (OSError, json.JSONDecodeError):
            pass
        return [_defaults() | {"text": "AT", "line_end": "crlf"},
                _defaults() | {"text": "AT+CGMR"},
                _defaults() | {"text": "AT+CSQ", "delay": 200}]

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.items, handle, ensure_ascii=False, indent=2)
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

    def update(self, item_id: str, patch: dict) -> None:
        for item in self.items:
            if item["id"] == item_id:
                item.update(patch)
                self._commit()
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
        self._commit()

    def _commit(self) -> None:
        self.save()
        self.changed.emit()

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
