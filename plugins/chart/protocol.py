"""Streaming, bounded ASCII telemetry parser; independent of Qt and plotting."""

from __future__ import annotations

import math
import re

PREFIX = b"[OmniCOM-chart]"
MAX_LINE = 65536
FIELD = re.compile(
    rb"([A-Za-z_][A-Za-z0-9_.\-/]{0,127})="
    rb"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\Z")


class AsciiParser:
    def __init__(self) -> None:
        self.pending = b""
        self.discarding = False
        self.frames = 0
        self.invalid = 0
        self.oversized = 0
        self.ignored = 0
        self.bytes_received = 0

    def parse_line(self, line: bytes) -> dict[str, float] | None:
        line = line.rstrip(b"\r")
        # The marker may be preceded by the firmware's own decoration
        # (``csi_demo:I(266466):[OmniCOM-chart] ...``): the device prints the
        # payload through its log macro, so requiring the marker at column 0
        # discarded every real frame (L-192).  Search instead of startswith.
        start = line.find(PREFIX)
        if start < 0:
            self.ignored += 1
            return None
        body = line[start + len(PREFIX):]
        if len(body) > MAX_LINE:
            self.oversized += 1
            return None
        fields = body.split()
        if not fields:
            self.invalid += 1
            return None
        values = {}
        for field in fields:
            match = FIELD.fullmatch(field)
            if match is None:
                self.invalid += 1
                return None
            name = match[1].decode("ascii")
            value = float(match[2])
            if name in values or not math.isfinite(value):
                self.invalid += 1
                return None
            values[name] = value
        self.frames += 1
        return values

    def feed(self, data: bytes) -> list[dict[str, float]]:
        self.bytes_received += len(data)
        parts = (self.pending + data).split(b"\n")
        self.pending = parts.pop()
        if self.discarding and parts:
            parts.pop(0)
            self.discarding = False
        result = []
        for line in parts:
            values = self.parse_line(line)
            if values is not None:
                result.append(values)
        if self.discarding:
            self.pending = b""
        elif len(self.pending) > MAX_LINE:
            self.oversized += 1
            self.pending = b""
            self.discarding = True
        return result
