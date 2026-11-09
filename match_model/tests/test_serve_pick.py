"""Serve picking with the set-up check (no video).

mark_serves picks the serve among the window's hits; serve_setup_ok is the check of a
candidate hit from the players before it (rules of tennis: set-up position, the half
the score requires, standing still)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tennis.contracts import ShotEvent
from tennis.events import mark_serves
from tennis.chart import serve_setup_ok


def hit(t, end, y):
    ev = ShotEvent(t=t, kind="hit", court_xy=(0.5, y), conf=0.5)
    ev._end = end
    return ev


class MarkServes(unittest.TestCase):
    def test_without_a_check_only_the_first_hit_can_be_the_serve(self):
        evs = [hit(1.0, "far", 12.0), hit(5.0, "near", -12.0)]
        mark_serves(evs, "near", False)
        self.assertEqual([e.kind for e in evs], ["hit", "hit"])
        evs = [hit(1.0, "near", -12.0), hit(2.0, "far", 11.0)]
        mark_serves(evs, "near", True)
        self.assertEqual(evs[0].kind, "serve")
        self.assertAlmostEqual(evs[0].conf, 0.65)

    def test_a_stray_hit_that_fails_the_check_is_skipped(self):
        stray, serve, ret = hit(1.0, "near", -12.0), hit(9.0, "near", -12.0), hit(10.0, "far", 11.0)
        evs = [stray, serve, ret]
        mark_serves(evs, "near", False, serve_ok=lambda h: h.t > 5.0)
        self.assertEqual([e.kind for e in evs], ["hit", "serve", "hit"])

    def test_no_answer_from_the_check_does_not_block(self):
        evs = [hit(1.0, "near", -12.0), hit(2.0, "far", 11.0)]
        mark_serves(evs, "near", False, serve_ok=lambda h: None)
        self.assertEqual(evs[0].kind, "serve")

    def test_hits_inside_the_court_or_at_the_other_end_are_never_serves(self):
        evs = [hit(1.0, "near", -8.0), hit(3.0, "far", 12.0)]
        mark_serves(evs, "near", False, serve_ok=lambda h: True)
        self.assertEqual([e.kind for e in evs], ["hit", "hit"])

    def test_second_serve_must_pass_the_check(self):
        first, rally, second = hit(1.0, "near", -12.0), hit(4.5, "near", -11.5), hit(12.0, "near", -12.0)
        evs = [first, rally, second]
        mark_serves(evs, "near", False, serve_ok=lambda h: h is not rally)
        self.assertEqual([e.kind for e in evs], ["serve", "hit", "serve"])

    def test_opponent_hit_ends_the_search_for_a_second_serve(self):
        # round 2: the receiver's hit here is 3.5 s after the serve, too late to be a
        # fault knocked back (it was 1.0 s before, which change 3 now allows; see below)
        evs = [hit(1.0, "near", -12.0), hit(4.5, "far", 11.0), hit(12.0, "near", -12.0)]
        mark_serves(evs, "near", False, serve_ok=lambda h: True)
        self.assertEqual([e.kind for e in evs], ["serve", "hit", "hit"])

    def test_without_a_check_any_opponent_hit_ends_the_search(self):
        evs = [hit(1.0, "near", -12.0), hit(2.0, "far", 11.0), hit(12.0, "near", -12.0)]
        mark_serves(evs, "near", False)
        self.assertEqual([e.kind for e in evs], ["serve", "hit", "hit"])

    def test_second_serve_after_a_fault_knocked_back(self):
        evs = [hit(1.0, "near", -12.0), hit(2.0, "far", 11.0), hit(12.0, "near", -12.0)]
        mark_serves(evs, "near", False, serve_ok=lambda h: True)
        self.assertEqual([e.kind for e in evs], ["serve", "hit", "serve"])

    def test_after_a_knock_back_the_check_must_say_yes(self):
        evs = [hit(1.0, "near", -12.0), hit(2.0, "far", 11.0), hit(12.0, "near", -12.0)]
        mark_serves(evs, "near", False, serve_ok=lambda h: True if h.t < 5.0 else None)
        self.assertEqual([e.kind for e in evs], ["serve", "hit", "hit"])

    def test_a_second_opponent_hit_ends_the_search(self):
        evs = [hit(1.0, "near", -12.0), hit(2.0, "far", 11.0), hit(3.5, "far", 11.0),
               hit(12.0, "near", -12.0)]
        mark_serves(evs, "near", False, serve_ok=lambda h: True)
        self.assertEqual([e.kind for e in evs], ["serve", "hit", "hit", "hit"])

    def test_no_serve_within_the_knock_back_time_after_the_knock_back(self):
        # 3.5 s after the first serve but only 2.5 s after the knock-back: not a serve;
        # the server's hit at 12 s is
        evs = [hit(1.0, "near", -12.0), hit(2.0, "far", 11.0), hit(4.5, "near", -12.0),
               hit(12.0, "near", -12.0)]
        mark_serves(evs, "near", False, serve_ok=lambda h: True)
        self.assertEqual([e.kind for e in evs], ["serve", "hit", "hit", "serve"])


class Det:
    def __init__(self, t, end, x, y, interp=False):
        self.t, self.end, self.court_xy, self.interp = t, end, (x, y), interp


def scene(t0, t1, srv=(0.6, -12.0), rcv=(-3.5, 12.0), srv_end="near", rcv_speed=0.0, fps=5.0):
    rcv_end = "far" if srv_end == "near" else "near"
    out, t = [], t0
    while t < t1 - 1e-9:
        out.append(Det(t, srv_end, *srv))
        out.append(Det(t, rcv_end, rcv[0] + rcv_speed * (t - t0), rcv[1]))
        t += 1.0 / fps
    return out


class SetupCheck(unittest.TestCase):
    def test_near_server_on_the_deuce_side(self):
        self.assertIs(serve_setup_ok(scene(0, 10), 8.0, 0.0, "near", "deuce"), True)

    def test_wrong_half_for_the_score_fails(self):
        self.assertIs(serve_setup_ok(scene(0, 10), 8.0, 0.0, "near", "ad"), False)

    def test_far_server_mirrors_the_halves(self):
        d = scene(0, 10, srv=(-0.6, 12.0), rcv=(3.5, -12.0), srv_end="far")
        self.assertIs(serve_setup_ok(d, 8.0, 0.0, "far", "deuce"), True)
        self.assertIs(serve_setup_ok(d, 8.0, 0.0, "far", "ad"), False)

    def test_a_moving_receiver_is_not_a_set_up(self):
        self.assertIs(serve_setup_ok(scene(0, 10, rcv_speed=-2.0), 8.0, 0.0, "near", "deuce"),
                      False)

    def test_the_other_player_at_the_centre_mark_is_not_this_server(self):
        self.assertIs(serve_setup_ok(scene(0, 10), 8.0, 0.0, "far", "deuce"), False)

    def test_too_little_footage_gives_no_answer(self):
        self.assertIsNone(serve_setup_ok(scene(7.6, 10), 8.0, 7.6, "near", "deuce"))


if __name__ == "__main__":
    unittest.main()
