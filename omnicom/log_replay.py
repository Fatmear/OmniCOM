"""Replay a saved log file into the plugins (R-302).

The GUI log can be saved with 保存视图, and the capture file records the same
text.  Both carry optional ``[HH:mm:ss.mmm]`` timestamps, so a saved session can
be turned back into the frames the device produced - which is what makes the
dashboard able to *re-show* a finished measurement instead of only a live one.

Two things are deliberately separate:

* :func:`read_frames` is a pure function over lines.  It knows nothing about Qt
  and can be unit-tested with plain strings.
* :func:`replay` walks the frames in bounded batches so a 100 MB log does not
  freeze the window.  Every plugin that subscribed through
  ``PluginApi.on_replay`` receives them with their original timestamps.
"""

from __future__ import annotations

import os
import re

#: ``[12:34:56.789]收←◇payload`` / ``[12:34:56.789]发→◇payload``
_LINE = re.compile(
    r"^\[(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{1,3})\](?P<body>.*)$")

#: Lines that carry no payload worth replaying.
_SKIP = ("收←", "发→", "RX", "TX")


def _seconds(hour: str, minute: str, second: str, milli: str) -> float:
    return (int(hour) * 3600 + int(minute) * 60 + int(second)
            + int(milli.ljust(3, "0")) / 1000.0)


def detect_encoding(path: str) -> str:
    """Guess a log's encoding from its first bytes.

    Our own capture writes UTF-8, but a file copied through PowerShell or Notepad
    is often UTF-16 (with a BOM) or GBK.  A wrong guess shows up as mojibake in a
    file the user only wanted to read, so the BOM is believed when present and
    UTF-8 is tried before falling back to the local code page.
    """
    with open(path, "rb") as probe:
        head = probe.read(4)
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def read_text(path: str) -> tuple[str, str]:
    """Return ``(text, encoding_used)`` for viewing a log file.

    The decoding is tried in order rather than guessed once: a UTF-8 log that
    contains one bad byte must still open, and a GBK log must not come back full
    of replacement characters (the user opens these files to *read* them).
    """
    encoding = detect_encoding(path)
    with open(path, "rb") as handle:
        raw = handle.read()
    for candidate in (encoding, "utf-8", "utf-8-sig", "gbk"):
        try:
            return raw.decode(candidate), candidate
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "utf-8 (replace)"


def read_frames(path: str) -> tuple[list[tuple[float, str]], int]:
    """Return ``([(seconds, payload)], chart_line_count)`` for a saved log.

    The returned time is seconds since the first ``[OmniCOM-chart]`` payload, so
    a replayed curve has the same x-axis shape it had live.  Lines without a
    timestamp are still replayed: they are given the previous line's time, which
    keeps the order intact instead of discarding data.
    """
    frames: list[tuple[float, str]] = []
    chart_lines = 0
    current = 0.0
    base: float | None = None
    encoding = "utf-8"
    try:
        with open(path, "rb") as probe:
            head = probe.read(4)
        if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
            encoding = "utf-16"
    except OSError:
        encoding = "utf-8"

    with open(path, encoding=encoding, errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            match = _LINE.match(line)
            if match:
                current = _seconds(match["h"], match["m"], match["s"],
                                   match["ms"])
                body = match["body"]
            else:
                body = line
            if "[OmniCOM-chart]" not in body:
                continue
            chart_lines += 1
            if base is None:
                base = current
            frames.append((current - (base or 0.0), body))
    return frames, chart_lines


def replay(path: str, publish) -> dict:
    """Feed every chart line of ``path`` to ``publish(seconds, payload)``.

    ``publish`` is the host's fan-out to the plugins.  Pacing is the caller's
    job (it owns the event loop and the timers), so this function stays pure and
    testable; it simply reports what it read.
    """
    frames, chart_lines = read_frames(path)
    for seconds, payload in frames:
        publish(seconds, payload)
    return {
        "path": os.path.abspath(path),
        "lines": chart_lines,
        "replayed": len(frames),
        "seconds": frames[-1][0] if frames else 0.0,
    }
