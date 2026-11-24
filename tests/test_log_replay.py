"""Regression: replay timestamps (R-331/R-332).

The dashboard used to put every replayed frame on ``t=0`` because only one
timestamp shape was recognised.  These cases pin the shapes a real device
prints, and the priority rule (a wall clock always beats a firmware tick).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from omnicom import log_replay  # noqa: E402

USER_LOG = """\
[2026-11-24_11:10:36:494]STATE=2 MAN=0 PRESENT=1 RESYNC=0
[2026-11-24_11:10:36:584]csi_demo:I(187138):[OmniCOM-chart] T1=0.01 M=0.0543
[2026-11-24_11:10:36:676]csi_demo:I(187244):[OmniCOM-chart] T1=0.02 M=0.1014
[2026-11-24_11:10:36:676]csi_demo:I(187244):[CSI-DBG] result=STILL
"""

TICK_ONLY = """\
csi_demo:I(187040):[OmniCOM-chart] T1=0.01
csi_demo:I(187140):[OmniCOM-chart] T1=0.02
csi_demo:I(187240):[OmniCOM-chart] T1=0.03
"""

NO_STAMP = """\
[OmniCOM-chart] T1=0.01
[OmniCOM-chart] T1=0.02
[OmniCOM-chart] T1=0.03
"""

MS_UNIT = """\
100 ms [OmniCOM-chart] T1=0.01
250 ms [OmniCOM-chart] T1=0.02
"""


class ReplayTimestampTests(unittest.TestCase):
    def frames(self, text: str):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "log.txt"
            path.write_text(text, encoding="utf-8")
            frames, count = log_replay.read_frames(str(path))
            stamped = log_replay.has_timestamps(str(path))
        return [round(seconds, 4) for seconds, _ in frames], count, stamped

    def test_user_device_log_uses_its_clock(self):
        """``[date_hh:mm:ss:mmm]`` is the device's real format."""
        times, count, stamped = self.frames(USER_LOG)
        self.assertEqual(count, 2)
        self.assertTrue(stamped, "a dated clock must count as a timestamp")
        self.assertGreater(len(set(times)), 1, f"frames stacked: {times}")
        self.assertAlmostEqual(times[0], 0.0, places=4)
        self.assertAlmostEqual(times[1], 0.092, places=4)

    def test_clock_wins_over_firmware_tick(self):
        """The same line carries both; the wall clock must be used."""
        times, _, _ = self.frames(USER_LOG)
        # The ticks differ by 106 (0.106 ms at 1 MHz) but the clock says 92 ms.
        self.assertAlmostEqual(times[1], 0.092, places=4)

    def test_bare_tick_systick_is_microseconds(self):
        times, _, stamped = self.frames(TICK_ONLY)
        self.assertTrue(stamped)
        self.assertEqual(len(set(times)), 3, f"frames stacked: {times}")
        for got, want in zip(times, (0.0, 0.1, 0.2), strict=True):
            self.assertAlmostEqual(got, want, places=4)

    def test_unstamped_log_steps_instead_of_stacking(self):
        times, count, stamped = self.frames(NO_STAMP)
        self.assertFalse(stamped, "no timestamp must be reported as estimated")
        self.assertEqual(count, 3)
        self.assertEqual(len(set(times)), 3, f"frames stacked: {times}")

    def test_tick_with_unit_is_converted(self):
        times, _, stamped = self.frames(MS_UNIT)
        self.assertTrue(stamped)
        self.assertAlmostEqual(times[1], 0.15, places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
