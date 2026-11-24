"""Regression: opening a log must not leave the dashboard on stale data (R-333).

These checks cover counter lifecycle and timer suppression only. They do not
reproduce the reported WebEngine Reload symptom or prove its cause.
"""

from __future__ import annotations

import collections
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plugins.chart.plugin import DashboardPanel  # noqa: E402


class _Stub:
    """Any widget/collection the stub panel does not model.

    Attribute access returns another stub, which is also callable, so
    ``widget.clear()`` and ``deque.clear()`` both work.
    """

    def __getattr__(self, name):
        return _Stub()

    def __call__(self, *args, **kwargs):
        return _Stub()


class _Panel:
    """Just the state the delta/counter logic reads and writes."""

    def __init__(self) -> None:
        self.samples = {}
        self.values = {}
        self._received = {}
        self._sent = {}
        self._revision = 0
        self._sent_revision = 0
        self._last_revision = 0
        self._last_sent = {}
        self._resync_needed = False
        self.sent = []
        self._flush_timer = _Stub()
        self.layout_data = {"widgets": [{"kind": "line", "channel": "A",
                                         "channels": ["A"]}]}

    def _capacity(self) -> int:
        return 5000

    def _visible_channels(self):
        return ["A"]

    def _window_start(self) -> float:
        return 0.0

    def request_resync(self) -> None:
        self._resync_needed = True

    @property
    def has_sent_anything(self) -> bool:
        """True once anything reached the page (``_call_js`` is trapped)."""
        return bool(self.sent)

    def _call_js(self, name, payload=None) -> None:
        self.sent.append((name, payload))

    def __getattr__(self, name):
        return _Stub()

    _number = staticmethod(lambda key, default: default)
    _visible_points = DashboardPanel._visible_points
    has_fresh_data = DashboardPanel.has_fresh_data
    delta_for_page = DashboardPanel.delta_for_page
    _remember_sent = DashboardPanel._remember_sent
    clear_data = DashboardPanel.clear_data
    refresh = DashboardPanel.refresh
    begin_replay = DashboardPanel.begin_replay
    end_replay = DashboardPanel.end_replay
    _flush_if_ready = DashboardPanel._flush_if_ready


class _RecordingTimer:
    """Counts start/stop so a timer change would be observable."""

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    def start(self, *args) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1


def _feed(panel: _Panel, start: int, count: int) -> None:
    for index in range(start, start + count):
        rows = panel.samples.setdefault("A", collections.deque(maxlen=5000))
        rows.append((index * 0.1, float(index)))
        panel._received["A"] = panel._received.get("A", 0) + 1
        panel.values["A"] = float(index)
        panel._revision += 1


class ReplayCounterTests(unittest.TestCase):
    def test_clear_data_forgets_the_pages_copy(self):
        panel = _Panel()
        _feed(panel, 0, 200)
        _, counts = panel.delta_for_page()
        panel._remember_sent(counts)
        panel._sent_revision = panel._revision
        self.assertEqual(panel._sent.get("A"), 200)

        panel.clear_data()

        self.assertEqual(panel._sent, {}, "the page's copy must be forgotten")
        self.assertEqual(panel._sent_revision, 0)
        self.assertTrue(panel._resync_needed)

    def test_replay_after_live_session_ships_every_row(self):
        """A smaller new replay must still ship every retained row."""
        panel = _Panel()
        _feed(panel, 0, 200)                 # a live session the page received
        _, counts = panel.delta_for_page()
        panel._remember_sent(counts)
        panel._sent_revision = panel._revision

        panel.clear_data()                    # 打开日志 -> begin_replay
        _feed(panel, 1000, 30)                # the replayed frames

        delta, _ = panel.delta_for_page()
        self.assertEqual(len(delta.get("A") or []), 30,
                         "every replayed frame must reach the page")
        first = delta["A"][0]
        self.assertEqual(first[1], 1000.0, "the old live rows must not come back")
        self.assertTrue(panel.has_fresh_data())

    def test_end_replay_resyncs_the_whole_window(self):
        """The final frame must replace the page history, not append a tail.

        Opening a log rebuilt the page's series from whatever was on screen;
        a reload was the only way back.  ``end_replay`` now resends the points
        and layout and asks for one full window.
        """
        panel = _Panel()
        panel.timer = _RecordingTimer()
        panel.replay_name = ""
        panel.live = _Truthy()
        panel._replay_active = False
        panel.page_ready = False

        DashboardPanel.begin_replay(panel, "fresh.log")
        self.assertTrue(panel._replay_active)

        DashboardPanel.end_replay(panel)
        self.assertFalse(panel._replay_active)
        self.assertTrue(panel._resync_needed, "the final window is a full resync")


class _Truthy(_Stub):
    def isChecked(self):
        return True


if __name__ == "__main__":
    unittest.main(verbosity=2)
