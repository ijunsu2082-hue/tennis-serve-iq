"""Far-court serve bounces past the service line: in or fault "could not tell",
direction from the side to side position (no video; ball events are made up)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tennis import chart
from tennis.contracts import CourtFit, ShotEvent

FIT = CourtFit(H=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], frame_w=1280, frame_h=720,
               err_px=0.0, conf=1.0)
ENDS = {"A": "near", "B": "far"}


def hit(t, end, y, kind="hit"):
    ev = ShotEvent(t=t, kind=kind, by=("A" if end == "near" else "B"), court_xy=(0.5, y), conf=0.6)
    ev._end, ev._n_out, ev._out, ev._out_end_t = end, 99, 999.0, t + 0.8
    return ev


def bounce(t, x, y):
    return ShotEvent(t=t, kind="bounce", court_xy=(x, y), conf=0.6)


def chart_with(events, server="A", side="deuce"):
    with mock.patch.object(chart.E, "detect_events", return_value=events):
        return chart.chart_from_tracks(0.0, 20.0, FIT, server, side, ENDS, [], [])


class FarDepth(unittest.TestCase):
    def test_far_court_long_bounce_gives_direction_not_a_fault(self):
        # near server, deuce: the target box is x in (-4.115, 0), y in (0, 6.4)
        pv = chart_with([hit(5.0, "near", -12.0, "serve"), bounce(5.6, -1.6, 8.0)])
        sv = pv.serves[0]
        self.assertEqual((sv.result, sv.zone, sv.fault), ("unknown", "body", None))

    def test_a_knocked_back_ball_does_not_make_it_in(self):
        pv = chart_with([hit(5.0, "near", -12.0, "serve"), bounce(5.6, -0.5, 8.0), hit(6.3, "far", 11.0)])
        self.assertEqual((pv.serves[0].result, pv.serves[0].zone), ("unknown", "T"))

    def test_a_rally_still_makes_it_in(self):
        pv = chart_with([hit(5.0, "near", -12.0, "serve"), bounce(5.6, -3.5, 8.0), hit(6.3, "far", 11.0),
                         hit(7.6, "near", -11.0)])
        self.assertEqual((pv.serves[0].result, pv.serves[0].zone), ("in", "wide"))

    def test_wide_of_the_box_is_still_a_fault(self):
        pv = chart_with([hit(5.0, "near", -12.0, "serve"), bounce(5.6, -5.0, 8.0)])
        self.assertEqual((pv.serves[0].result, pv.serves[0].fault), ("fault", "long_wide"))

    def test_near_court_bounce_depth_is_still_used(self):
        # far server, deuce: target box x in (0, 4.115), y in (-6.4, 0); a long bounce is a fault
        pv = chart_with([hit(5.0, "far", 12.0, "serve"), bounce(5.6, 1.6, -8.0)], server="B")
        self.assertEqual((pv.serves[0].result, pv.serves[0].fault), ("fault", "long"))


if __name__ == "__main__":
    unittest.main()
