"""Bounce-led serve (no video): when the serve hit itself was not seen, a bounce in the
receiver's half with no hit before it stands for the serve (events._bounce_led_serves).

The picture here is a made-up camera: court x = (px - 640) / 100, court y = (400 - py) / 20
(near end at the bottom of the picture, negative y; far end at the top, positive y)."""
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tennis import events as E
from tennis.contracts import PlayerObs, ShotEvent

H = np.array([[0.01, 0.0, -6.4], [0.0, -0.05, 20.0], [0.0, 0.0, 1.0]])
HAND = {"near": "R", "far": "R"}


def px(x, y):
    return 640.0 + 100.0 * x, 400.0 - 20.0 * y


def players(srv_y=-12.5, srv_end="near", t1=20.0):
    """Server at the centre mark, receiver across the net, 5 per second."""
    out, t = [], 0.0
    while t < t1:
        if srv_end == "near":
            out.append(PlayerObs(t, "A", "near", (680.0, 400.0, 720.0, 650.0), (0.6, srv_y)))
            out.append(PlayerObs(t, "B", "far", (280.0, 110.0, 300.0, 150.0), (-3.5, 12.5)))
        else:
            out.append(PlayerObs(t, "A", "far", (300.0, 110.0, 320.0, 150.0), (-0.6, -srv_y)))
            out.append(PlayerObs(t, "B", "near", (900.0, 400.0, 940.0, 650.0), (3.5, -12.5)))
        t = round(t + 0.2, 6)
    return out


def bounce(t, x, y):
    return ShotEvent(t=t, kind="bounce", court_xy=(x, y), conf=0.5)


def hit(t, end, y):
    ev = ShotEvent(t=t, kind="hit", court_xy=(0.5, y), conf=0.5)
    ev._end = end
    return ev


def cands(evs, ok=True, pl=None, end="near"):
    return E._bounce_led_serves(evs, pl if pl is not None else players(), H, end, HAND,
                                lambda h: ok)


class BounceLedServe(unittest.TestCase):
    def test_bounce_with_no_hit_gives_a_serve_only_when_the_check_says_yes(self):
        b = bounce(10.0, 2.6, 7.5)
        out = cands([b], ok=True)
        self.assertEqual(len(out), 1)
        c = out[0]
        self.assertAlmostEqual(c.t, 10.0 - E.BL_FLIGHT_S)
        self.assertEqual((c.kind, c._end), ("hit", "near"))
        self.assertTrue(c._bounce_led)
        self.assertAlmostEqual(c.court_xy[1], -12.5)
        self.assertEqual(cands([b], ok=None), [])      # "no answer" is not enough
        self.assertEqual(cands([b], ok=False), [])

    def test_a_hit_before_the_bounce_means_the_serve_was_seen(self):
        b = bounce(10.0, 2.6, 7.5)
        self.assertEqual(cands([hit(9.3, "far", 12.0), b]), [])     # 0.7 s before, either end
        self.assertEqual(cands([hit(9.3, "near", -12.0), b]), [])
        self.assertEqual(len(cands([hit(8.7, "far", 12.0), b])), 1)  # 1.3 s before: too early

    def test_a_server_hit_right_after_the_bounce_blocks_but_a_fast_return_does_not(self):
        b = bounce(10.0, 2.6, 7.5)
        self.assertEqual(cands([b, hit(10.2, "near", -12.0)]), [])
        self.assertEqual(len(cands([b, hit(10.2, "far", 12.0)])), 1)

    def test_a_bounce_inside_the_servers_reach_is_his_racket_not_a_bounce(self):
        x, y = 0.6, 2.0          # 40 px above the server's box in the picture
        self.assertLess(E._box_dist((680.0, 400.0, 720.0, 650.0), *px(x, y)), E._hit_lim("near", None))
        self.assertEqual(cands([bounce(10.0, x, y)]), [])

    def test_the_racket_above_the_servers_head_is_inside_his_reach(self):
        # 90 px above his box (more than the 70 px hit limit from the box itself) but
        # within half a body height above it, where the racket meets the serve
        x, y = 0.6, 4.5
        self.assertGreater(E._box_dist((680.0, 400.0, 720.0, 650.0), *px(x, y)), E._hit_lim("near", None))
        self.assertEqual(cands([bounce(10.0, x, y)]), [])

    def test_bounces_outside_the_target_area_give_nothing(self):
        for x, y in ((2.6, -3.0),      # server's own half
                     (2.6, 0.5),       # on the net
                     (2.6, 10.0),      # deeper than the service line plus the slack
                     (5.0, 5.0)):      # outside the singles side line plus 0.5 m
            self.assertEqual(cands([bounce(10.0, x, y)]), [], (x, y))

    def test_server_inside_the_court_is_not_serving(self):
        self.assertEqual(cands([bounce(10.0, 2.6, 7.5)], pl=players(srv_y=-9.0)), [])

    def test_far_server_mirrors_the_halves(self):
        pl = players(srv_end="far")
        out = cands([bounce(10.0, -1.5, -5.0)], pl=pl, end="far")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]._end, "far")
        self.assertEqual(cands([bounce(10.0, -1.5, 5.0)], pl=pl, end="far"), [])

    def test_a_candidate_not_picked_as_a_serve_is_dropped(self):
        serve, ret = hit(1.0, "near", -12.0), hit(4.5, "far", 11.0)   # a rally hit, not a knock-back
        b = bounce(10.0, 2.6, 7.5)
        cand = cands([serve, ret, b])[0]
        evs = sorted([serve, ret, b, cand], key=lambda e: e.t)
        E.mark_serves(evs, "near", False, serve_ok=lambda h: True)
        self.assertEqual(cand.kind, "hit")          # the receiver's hit ended the search
        self.assertEqual(E._drop_unpicked(evs), [serve, ret, b])
        cand = cands([b])[0]
        evs = [cand, b]
        E.mark_serves(evs, "near", False, serve_ok=lambda h: True)
        self.assertEqual(E._drop_unpicked(evs), [cand, b])
        self.assertEqual(cand.kind, "serve")

    def test_only_charting_with_a_check_and_a_server_end_looks_for_them(self):
        fit = types.SimpleNamespace(H=H.tolist())
        with mock.patch.object(E, "_bounce_led_serves", return_value=[]) as m:
            E.detect_events([], players(), fit, server_end="near", serve_ok=None)   # the vote pass
            E.detect_events([], players(), fit, server_end=None, serve_ok=lambda h: True)
            self.assertEqual(m.call_count, 0)
            E.detect_events([], players(), fit, server_end="near", serve_ok=lambda h: True)
            self.assertEqual(m.call_count, 1)


if __name__ == "__main__":
    unittest.main()
