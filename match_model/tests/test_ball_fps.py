"""The ball is looked for at about 30 fps whatever the video's frame rate (tennis/ball.py)."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tennis import ball as B  # noqa: E402


class TestWorkFps(unittest.TestCase):
    def test_broadcast_rates_read_every_frame(self):
        for f in (24.0, 25.0, 29.97, 30.0, 40.0):
            self.assertIsNone(B.work_fps(f), f)

    def test_fast_videos_read_at_about_30(self):
        self.assertAlmostEqual(B.work_fps(59.94), 29.97, places=2)
        self.assertAlmostEqual(B.work_fps(60.0), 30.0)
        self.assertAlmostEqual(B.work_fps(50.0), 25.0)
        self.assertAlmostEqual(B.work_fps(120.0), 30.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
