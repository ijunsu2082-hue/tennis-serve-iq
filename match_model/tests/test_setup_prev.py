"""The set-up check of a hit early in a window may read the window before (no video).

A window starts at a score change. When the window before ended at that same moment in
the same camera shot, the serve set-up of a hit early in the window is in the window
before (pipeline.setup_prev_of, chart.chart_from_tracks setup_prev)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tennis import chart, pipeline
from tennis.contracts import CourtFit, Segment, ShotEvent

FIT = CourtFit(H=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], frame_w=1280, frame_h=720,
               err_px=0.0, conf=1.0)
ENDS = {"A": "near", "B": "far"}
ONE_SHOT = [Segment(0.0, 1000.0, "main")]


class Det:
    def __init__(self, t, end, x, y, interp=False):
        self.t, self.end, self.court_xy, self.interp = t, end, (x, y), interp
        self.bbox, self.pid = (0.0, 0.0, 1.0, 1.0), None


def scene(t0, t1, rcv_speed=0.0, fps=5.0):
    """Near server at the centre mark on the deuce side, far receiver across."""
    out, t = [], t0
    while t < t1 - 1e-9:
        out.append(Det(t, "near", 0.6, -12.0))
        out.append(Det(t, "far", -3.5 + rcv_speed * (t - t0), 12.0))
        t = round(t + 1.0 / fps, 6)
    return out


def gate_of(dets, setup_prev):
    got = {}

    def fake(ball, d, fit, fps=None, hand=None, server_end=None, serve_ok=None):
        got["ok"] = serve_ok
        return []

    with mock.patch.object(chart.E, "detect_events", side_effect=fake):
        chart.chart_from_tracks(100.0, 130.0, FIT, "A", "deuce", ENDS, dets, [], setup_prev=setup_prev)
    return got["ok"]


class SetupPrevOf(unittest.TestCase):
    def test_a_window_that_starts_where_the_last_one_ended_reads_it(self):
        prev = scene(90.0, 100.0)
        r = pipeline.setup_prev_of(ONE_SHOT, (100.0, prev), 100.0, 130.0)
        self.assertIsNotNone(r)
        t_min, sub = r
        self.assertAlmostEqual(t_min, 100.0 - pipeline.SETUP_LOOK_S)
        self.assertTrue(sub and all(d.t >= t_min for d in sub))

    def test_never_when_the_window_before_ended_earlier_or_is_missing(self):
        self.assertIsNone(pipeline.setup_prev_of(ONE_SHOT, (95.0, scene(90.0, 95.0)), 100.0, 130.0))
        self.assertIsNone(pipeline.setup_prev_of(ONE_SHOT, None, 100.0, 130.0))

    def test_never_across_a_camera_cut(self):
        two = [Segment(0.0, 100.0, "main"), Segment(100.0, 200.0, "main")]
        self.assertIsNone(pipeline.setup_prev_of(two, (100.0, scene(90.0, 100.0)), 100.0, 130.0))


class EarlyHit(unittest.TestCase):
    def test_a_hit_early_in_the_window_uses_the_set_up_before_it(self):
        dets = scene(100.0, 130.0)
        h = ShotEvent(t=100.3, kind="hit", court_xy=(0.6, -12.0))
        self.assertIsNone(gate_of(dets, None)(h))           # too little footage in the window
        still = pipeline.setup_prev_of(ONE_SHOT, (100.0, scene(90.0, 100.0)), 100.0, 130.0)
        self.assertIs(gate_of(dets, still)(h), True)
        walking = pipeline.setup_prev_of(ONE_SHOT, (100.0, scene(90.0, 100.0, rcv_speed=-2.0)),
                                         100.0, 130.0)
        self.assertIs(gate_of(dets, walking)(h), False)


if __name__ == "__main__":
    unittest.main()
