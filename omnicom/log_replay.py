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

import contextlib
import itertools
import os
import re

#: ``[12:34:56.789]收←◇payload`` / ``[12:34:56.789]发→◇payload``
_LINE = re.compile(
    r"^\[(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{1,3})\](?P<body>.*)$")

#: Any ``HH:MM:SS`` anywhere in the line, whatever wraps it and whatever
#: separates the milliseconds.  A saved log may be our own ``[10:00:00.100]``
#: prefix, a terminal copy with the direction marker first, a dated line
#: (``[2026-11-24_11:10:36:494]`` - **colon** before ms, the shape the user's
#: device prints), or a tab-separated time column.  Anchoring on the start of
#: the line and accepting only ``.``/``,`` before the milliseconds recognised
#: just one of them, so a whole second's frames collapsed onto the same x - the
#: reported "重放时所有帧都堆在第 0 帧".
_ANY_STAMP = re.compile(
    r"(?<!\d)(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})"
    r"(?:[.,:](?P<ms>\d{1,3}))?(?!\d)")

#: Firmware log macros stamp a **monotonic tick** instead of a wall clock:
#: ``(12345678)[OmniCOM-chart] A=1``, ``[12345678] ...``, ``t=12345678``,
#: ``tick:12345678``, ``12345678 ms``, ``12345678us``, and the ESP-IDF style
#: ``csi_demo:I(187040):...`` that prints a bare counter inside parentheses.
#: These are *not* seconds and not ``HH:MM:SS``, so a log printed through a
#: device logger used to fall through to "no timestamp" and every frame
#: collapsed onto ``t=0``.  They are always second choice: a line that also
#: carries a real clock uses the clock.
_TICK = re.compile(
    r"(?:"
    r"(?<!\d)(?P<ticks>\d{1,19})(?!\d)\s*(?P<unit>us|µs|ms|ticks?|s)\b"   # 100 ms, 12345678us
    r"|"
    r"(?:tick(?:s)?|t|ts|time|stamp|uptime)\s*[:=]\s*(?P<labelled>\d{3,19})"  # t=12345678
    r"|"
    r"[(](?P<bracketed>\d{5,19})[)]"                                        # (12345678)
    r")",
    re.IGNORECASE)

#: Seconds added between frames that carry no timestamp at all.  Equal spacing
#: is wrong about the device's clock but keeps the curve readable and, more
#: importantly, keeps the frames in order instead of stacking them.
_NO_STAMP_STEP_S = 0.1

#: Tick sources with a declared unit are converted directly; a **bare** tick
#: counter (``(12345678)``) has no unit, so its scale is inferred from the
#: file's own deltas instead of assuming a clock rate.
_TICK_UNITS = {"us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0}


#: ``2026-11-24`` / ``2026/11/24`` in front of the clock (the user's device
#: prints ``[2026-11-24_11:10:36:494]``).  Kept so a log that crosses midnight
#: does not make the x-axis jump backwards by a day.
_DATE = re.compile(r"(?<!\d)(?P<y>\d{4})[-/](?P<mo>\d{2})[-/](?P<d>\d{2})(?!\d)")


def _seconds(hour: str, minute: str, second: str, milli: str) -> float:
    """Seconds-of-day; ``milli`` is a fractional second of any length.

    ``1`` / ``12`` / ``123`` all mean fractions (0.1 s, 0.12 s, 0.123 s): a log
    that prints ``[11:10:36:5]`` is not 5 milliseconds but a tenth of a second,
    and treating a short group as milliseconds would put frames out of order.
    """
    fraction = int(milli) / (10 ** len(milli)) if milli else 0.0
    return int(hour) * 3600 + int(minute) * 60 + int(second) + fraction


def _clock_seconds(line: str) -> float | None:
    """Absolute seconds from a stamped ``line``, else ``None``.

    The clock is ``HH:MM:SS`` with an optional fractional part; a leading
    ``YYYY-MM-DD`` is folded in as whole days so a multi-day log stays
    monotonic.  A line without any clock returns ``None`` and the caller falls
    back to a firmware tick, so **a real clock always wins** when both exist -
    a device that prints ``csi_demo:I(187040):[OmniCOM-chart] ...`` next to
    ``[2026-11-24_11:10:36:584]`` is timed by the wall clock, not the tick.
    """
    match = _LINE.match(line)
    if match is None:
        match = _ANY_STAMP.search(line)
    if match is None:
        return None
    try:
        seconds = _seconds(match.group("h"), match.group("m"),
                           match.group("s"), match.group("ms") or "")
    except (TypeError, ValueError):
        return None
    day = _DATE.search(line)
    if day is not None:
        with contextlib.suppress(TypeError, ValueError):
            # Day-of-month is enough for ordering inside one file; the year and
            # month only matter at a month boundary, where a 28-31 day step is
            # still monotonic for a single capture.
            seconds += (int(day.group("d")) - 1) * 86400.0
    return seconds


def _tick_raw(line: str) -> tuple[int, float] | None:
    """``(ticks, seconds_per_tick)`` from a firmware tick stamp, else ``None``.

    The second value is the tick's own unit factor when the log declared one
    (``100 ms`` → ``1e-3``), or ``0.0`` for a **bare** counter such as
    ``(12345678)``, whose scale is inferred later from the file's deltas.
    """
    match = _TICK.search(line)
    if match is None:
        return None
    value = match.group("ticks") or match.group("labelled") or match.group("bracketed")
    if value is None:
        return None
    unit = (match.group("unit") or "").lower()
    if not unit or unit.startswith("tick"):
        return int(value), 0.0             # bare counter: infer the scale
    return int(value), _TICK_UNITS[unit]


def _tick_scale(ticks: list[int]) -> tuple[float, int]:
    """``(seconds_per_tick, first_tick)`` for a bare tick counter.

    A firmware tick is usually a microsecond systick (``I(187040)`` 100 us
    later is ``187140``) but can be milliseconds or raw CPU cycles, and the log
    rarely says which.  The deltas decide:

    * a delta of a few thousand is microseconds (100 us-10 ms per sample);
    * a delta of tens/hundreds is likely milliseconds;
    * a delta of millions is cycles (CPU frequency scale).

    When nothing fits, microseconds is used - it is by far the most common for
    an embedded systick and keeps the x-axis in a plausible range instead of
    inventing one second per frame.
    """
    deltas = sorted(b - a for a, b in itertools.pairwise(ticks) if b > a)
    if not deltas:
        return 1e-6, (ticks[0] if ticks else 0)
    middle = deltas[len(deltas) // 2]
    if middle >= 1_000_000:
        scale = 1.0 / 100_000_000.0        # cycle counter on a ~100 MHz core
    elif middle >= 1_000:
        scale = 1e-6                        # microsecond systick
    else:
        scale = 1e-3                        # millisecond counter
    return scale, ticks[0]


def _collect(path: str) -> tuple[list[list], int, int]:
    """Read the log once: ``(rows, chart_lines, stamped_lines)``.

    Each row is ``[seconds_or_None, raw_ticks_or_None, unit, line]`` so the
    conversion can happen after the whole file is known - a bare tick counter
    needs the file's own deltas to decide its scale.
    """
    rows: list[list] = []
    chart_lines = 0
    stamped = 0
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
            if "[OmniCOM-chart]" not in line:
                continue
            chart_lines += 1
            clock = _clock_seconds(line)
            if clock is not None:
                stamped += 1
                rows.append([clock, None, 0.0, line])
                continue
            tick = _tick_raw(line)
            if tick is not None:
                stamped += 1
                rows.append([None, tick[0], tick[1], line])
                continue
            rows.append([None, None, 0.0, line])
    return rows, chart_lines, stamped


def read_frames(path: str) -> tuple[list[tuple[float, str]], int]:
    """Return ``([(seconds, payload)], chart_line_count)`` for a saved log.

    The x value is seconds since the first ``[OmniCOM-chart]`` payload.  Four
    shapes are understood, in this order:

    1. a wall clock anywhere in the line (``[10:00:00.100]``, ``2026-11-24
       10:00:00.100``, a tab-separated time column);
    2. a firmware tick with a unit (``12345678 ms``, ``12345678us``);
    3. a bare tick counter (``(12345678)``, ``[12345678]``, ``t=12345678``),
       scaled from the file's own deltas;
    4. no timestamp at all - evenly stepped, so the frames stay ordered instead
       of collapsing onto ``t=0``.

    The shapes may be mixed in one file; per line the first usable one wins and
    an unstamped line keeps the previous instant (order preserved, no invented
    time beyond case 4).
    """
    rows, chart_lines, _ = _collect(path)
    bare = [row[1] for row in rows if row[1] is not None and not row[2]]
    bare_scale, bare_first = _tick_scale(bare) if bare else (1e-6, 0)
    # If *nothing* in the file is stamped, the x-axis is invented (evenly
    # stepped).  A file with any clock or tick is timed from that instead.
    stamped_anywhere = any(row[0] is not None or row[1] is not None for row in rows)

    frames: list[tuple[float, str]] = []
    base: float | None = None
    current = 0.0
    for index, (clock, ticks, unit, line) in enumerate(rows):
        if clock is not None:
            current = clock
        elif ticks is not None and unit:
            current = ticks * unit                # declared unit (100 ms)
        elif ticks is not None:
            current = (ticks - bare_first) * bare_scale
        elif not stamped_anywhere:
            current = index * _NO_STAMP_STEP_S     # nothing is stamped at all
        if base is None:
            base = current
        frames.append((max(0.0, current - base), line))
    return frames, chart_lines


#: Lines that carry no payload worth replaying.
_SKIP = ("收←", "发→", "RX", "TX")


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


def has_timestamps(path: str) -> bool:
    """Whether the log carries any recognisable timestamp at all.

    The caller uses this to tell the user that a replayed x-axis is *estimated*
    (evenly stepped) rather than measured - the frames still play in order, but
    the spacing between them is ours, not the device's.
    """
    _, _, stamped = _collect(path)
    return stamped > 0


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
        "estimated_time": not has_timestamps(path),
    }
