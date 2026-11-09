"""Integration-logic tests for tennis/pipeline.py (no video decoding)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tennis.contracts import MatchFormat, PointBoundary, PointVision, ScoreObs, Segment, ServeVision, ShotEvent
from tennis.pipeline import (infer_winner, merge_point, merge_serve_attempts, orient_by_votes,
                             orient_engine_ends, rally_window, score_stretches, serve_attempt_windows,
                             serve_like_hit, setup_before)
from tennis import scoring
from tennis.chart import _unseen_reply


def seg(a, b, kind="main"):
    return Segment(a, b, kind)


SEGS = [seg(0, 5, "other"), seg(5, 8), seg(8, 12, "other"), seg(12, 25), seg(25, 30, "other"),
        seg(30, 31), seg(31, 40, "other")]


def boundary(winner="A", server="A"):
    return PointBoundary(index=0, t_end=27.0, t_start=None, winner=winner, server=server,
                         score_before={"sets": [], "games": [0, 0], "points": "0-0", "in_tb": False},
                         score_after={}, side="deuce", flags={}, source="ocr", conf=0.98)


class RallyWindows(unittest.TestCase):
    def test_last_main_stretch_is_the_rally(self):
        self.assertEqual(rally_window(SEGS, 0.0, 27.0), (12, 25))

    def test_window_clipped_to_previous_score_change(self):
        self.assertEqual(rally_window(SEGS, 14.0, 27.0), (14.0, 25))

    def test_too_short_main_shot_is_not_a_rally(self):
        self.assertIsNone(rally_window(SEGS, 26.0, 31.0))

    def test_earlier_main_shots_are_serve_attempts(self):
        self.assertEqual(serve_attempt_windows(SEGS, 0.0, (12, 25)), [(5, 8)])
        self.assertEqual(serve_attempt_windows(SEGS, 9.0, (12, 25)), [])


class ServeAttempts(unittest.TestCase):
    def test_earlier_serve_becomes_first_serve_fault(self):
        rally = PointVision(t_start=12, t_end=25, serves=[ServeVision(1, 12.5, "in", zone="T", conf=0.6)])
        attempt = PointVision(t_start=5, t_end=8,
                              serves=[ServeVision(1, 5.5, "in", zone="wide", conf=0.5)])
        pv = merge_serve_attempts(rally, [attempt])
        self.assertEqual([(s.n, s.result, s.zone) for s in pv.serves],
                         [(1, "fault", "wide"), (2, "in", "T")])
        self.assertEqual(pv.t_start, 5)
        rec = merge_point(boundary(), pv, 0, None)
        self.assertEqual(rec.serve_number_won_on, 2)

    def test_fault_type_kept_only_when_seen_as_fault(self):
        rally = PointVision(t_start=12, t_end=25, serves=[ServeVision(1, 12.5, "in")])
        attempt = PointVision(t_start=5, t_end=8, serves=[ServeVision(1, 5.5, "fault", fault="net")])
        pv = merge_serve_attempts(rally, [attempt])
        self.assertEqual(pv.serves[0].fault, "net")

    def test_no_attempts_is_a_no_op(self):
        rally = PointVision(t_start=12, t_end=25, serves=[ServeVision(1, 12.5, "in")])
        self.assertIs(merge_serve_attempts(rally, []), rally)
        self.assertEqual(rally.serves[0].n, 1)


class MergeConsistency(unittest.TestCase):
    def test_unknown_ending_is_not_attributed(self):
        pv = PointVision(t_start=12, t_end=25, ending="unknown", last_shot_by="B")
        self.assertIsNone(merge_point(boundary(), pv, 0, None).ending_by)

    def test_ace_won_by_receiver_is_rejected(self):
        pv = PointVision(t_start=12, t_end=25, ending="ace", serves=[ServeVision(1, 12.5, "in")])
        rec = merge_point(boundary(winner="B", server="A"), pv, 0, None)
        self.assertEqual(rec.ending, "unknown")

    def test_error_by_point_winner_is_rejected(self):
        pv = PointVision(t_start=12, t_end=25, ending="unforced_error", last_shot_by="A")
        rec = merge_point(boundary(winner="A"), pv, 0, None)
        self.assertEqual(rec.ending, "unknown")

    def test_consistent_winner_kept(self):
        pv = PointVision(t_start=12, t_end=25, ending="winner", last_shot_by="A")
        rec = merge_point(boundary(winner="A"), pv, 0, None)
        self.assertEqual((rec.ending, rec.ending_by), ("winner", "A"))


class NoBugWinner(unittest.TestCase):
    def test_last_shot_in_means_hitter_won(self):
        pv = PointVision(t_start=0, t_end=5, last_shot_by="A", last_shot_result="in",
                         conf={"last_shot_by": 0.6, "last_shot_result": 0.6})
        self.assertEqual(infer_winner(pv)[0], "A")

    def test_last_shot_out_means_other_won(self):
        pv = PointVision(t_start=0, t_end=5, last_shot_by="A", last_shot_result="net",
                         conf={"last_shot_by": 0.6, "last_shot_result": 0.6})
        self.assertEqual(infer_winner(pv)[0], "B")

    def test_no_evidence_no_winner(self):
        self.assertEqual(infer_winner(PointVision(t_start=0, t_end=5)), (None, 0.0))


class EngineEnds(unittest.TestCase):
    def test_engine_ends_flipped_when_vision_disagrees(self):
        b0, b1 = boundary(), boundary()
        b0.score_before = dict(b0.score_before, ends={"A": "near", "B": "far"})
        b1.score_before = dict(b1.score_before, ends={"A": "far", "B": "near"})
        far = PointVision(t_start=0, t_end=5, ends={"A": "far", "B": "near"}, conf={"ends": 1.0})
        orient_engine_ends([b0, b1], {0: far})
        self.assertEqual(b0.score_before["ends"], {"A": "far", "B": "near"})
        self.assertEqual(b1.score_before["ends"], {"A": "near", "B": "far"})

    def test_low_confidence_vision_leaves_engine_alone(self):
        b0 = boundary()
        b0.score_before = dict(b0.score_before, ends={"A": "near", "B": "far"})
        weak = PointVision(t_start=0, t_end=5, ends={"A": "far", "B": "near"}, conf={"ends": 0.5})
        orient_engine_ends([b0], {0: weak})
        self.assertEqual(b0.score_before["ends"]["A"], "near")


def hit(t, end):
    ev = ShotEvent(t=t, kind="hit")
    ev._end = end
    return ev


def bounce(t, x, y):
    return ShotEvent(t=t, kind="bounce", court_xy=(x, y), conf=0.8)


class UnseenReply(unittest.TestCase):
    """The known loser hit the last detected ball but it came back into play."""

    def test_deep_bounce_on_hitters_own_half_means_a_reply(self):
        # near player's shot, then a bounce deep on the near half 0.8 s later
        self.assertTrue(_unseen_reply(hit(10.0, "near"), bounce(10.8, -1.9, -5.1), "net", 0.48, True))

    def test_netted_ball_drops_close_to_the_net(self):
        self.assertFalse(_unseen_reply(hit(10.0, "near"), bounce(10.8, 0.5, -1.5), "net", 0.48, None))

    def test_bounce_too_soon_after_contact_is_not_a_reply(self):
        self.assertFalse(_unseen_reply(hit(10.0, "near"), bounce(10.42, 1.3, 3.4), "in", 0.78, True))
        self.assertFalse(_unseen_reply(hit(10.0, "near"), bounce(10.42, 1.5, -4.8), "net", 0.48, True))

    def test_ball_landed_in_on_the_winners_half(self):
        self.assertTrue(_unseen_reply(hit(10.0, "near"), bounce(11.8, 3.6, 11.1), "in", 0.4, True))
        self.assertFalse(_unseen_reply(hit(10.0, "near"), bounce(11.8, 3.6, 11.1), "in", 0.2, True))

    def test_ball_that_never_cleared_the_net_is_an_error(self):
        self.assertFalse(_unseen_reply(hit(10.0, "far"), bounce(12.0, 2.7, 2.3), "net", 0.29, False))


def pt(games, sets=(), ends=None, server="A", source="ocr", gap=False, in_tb=False):
    sb = {"sets": [list(x) for x in sets], "games": list(games), "points": "0-0", "in_tb": in_tb,
          "ends": dict(ends or {"A": "near", "B": "far"})}
    return PointBoundary(index=0, t_end=0.0, t_start=None, winner="A", server=server,
                         score_before=sb, score_after=dict(sb, ends=dict(sb["ends"])), side="deuce",
                         flags={}, gap_before=gap, source=source)


def vote(end, conf=0.9):
    return {"hit_t": 1.0, "end": end, "conf": conf}


class EndsVote(unittest.TestCase):
    """Ends from the engine's schedule, turned the right way round by a vote."""

    def test_filled_points_and_in_set_jumps_keep_the_stretch(self):
        b = [pt([0, 0]), pt([0, 0], source="engine", gap=True), pt([2, 1], gap=True)]
        self.assertEqual(score_stretches(b), [0, 0, 0])

    def test_jump_into_another_set_or_backwards_starts_a_stretch(self):
        b = [pt([3, 2]), pt([1, 0], sets=[(6, 4)], gap=True), pt([0, 0], sets=[(6, 4)], gap=True)]
        self.assertEqual(score_stretches(b), [0, 1, 2])

    def test_votes_against_the_engine_flip_every_point_of_the_stretch(self):
        b = [pt([0, 0]) for _ in range(8)]
        for k in range(3, 8):                                        # an end change inside
            b[k].score_before["ends"] = {"A": "far", "B": "near"}
        votes = {k: vote("far") for k in (0, 1, 2)}                  # A serves from the far end
        votes.update({k: vote("near") for k in (4, 5)})
        votes[6] = vote("near", conf=0.5)                            # too weak to vote
        voted, _ = orient_by_votes(b, votes)
        self.assertEqual(set(voted), set(range(8)))
        self.assertEqual(voted[0], 1.0)
        self.assertEqual(b[0].score_before["ends"], {"A": "far", "B": "near"})
        self.assertEqual(b[3].score_before["ends"], {"A": "near", "B": "far"})

    def test_agreeing_votes_keep_the_engine_ends(self):
        b = [pt([0, 0]) for _ in range(5)]
        voted, _ = orient_by_votes(b, {k: vote("near") for k in range(5)})
        self.assertEqual(len(voted), 5)
        self.assertEqual(b[2].score_before["ends"]["A"], "near")

    def test_split_votes_leave_the_stretch_undecided(self):
        b = [pt([0, 0]) for _ in range(6)]
        votes = {0: vote("near"), 1: vote("near"), 2: vote("near"), 3: vote("far"), 4: vote("far")}
        voted, _ = orient_by_votes(b, votes)
        self.assertEqual(voted, {})
        self.assertEqual(b[0].score_before["ends"]["A"], "near")

    def test_short_stretch_takes_the_whole_match_decision(self):
        b = [pt([0, 0]) for _ in range(6)] + [pt([1, 0], sets=[(6, 4)], gap=True), pt([1, 0], sets=[(6, 4)])]
        votes = {k: vote("far") for k in range(6)}
        votes[7] = vote("near")                     # one stray vote in the short stretch
        voted, _ = orient_by_votes(b, votes)
        self.assertEqual(set(voted), set(range(8)))
        self.assertEqual(b[7].score_before["ends"]["A"], "far")

    def test_too_few_votes_decide_nothing(self):
        b = [pt([0, 0]) for _ in range(4)]
        voted, _ = orient_by_votes(b, {0: vote("far"), 1: vote("far")})
        self.assertEqual(voted, {})
        self.assertEqual(b[0].score_before["ends"]["A"], "near")


class Det:
    def __init__(self, t, end, x, y, interp=False):
        self.t, self.end, self.court_xy, self.interp = t, end, (x, y), interp


def setup_dets(t0, t1, srv_end="near", fps=5.0):
    out, t = [], t0
    while t < t1 - 1e-9:
        out.append(Det(t, srv_end, 0.3, -12.0 if srv_end == "near" else 12.0))
        out.append(Det(t, "far" if srv_end == "near" else "near", 3.5, 12.0 if srv_end == "near" else -12.0))
        t += 1.0 / fps
    return out


class SetupBeforeHit(unittest.TestCase):
    def test_reads_the_set_up_before_the_hit(self):
        self.assertEqual(setup_before(setup_dets(0.0, 10.0, "far"), 8.0, 0.0)[0], "far")

    def test_never_looks_before_the_window_start(self):
        dets = setup_dets(0.0, 5.0, "near") + setup_dets(5.0, 10.0, "far")
        dets.sort(key=lambda d: d.t)
        self.assertEqual(setup_before(dets, 7.0, 5.0)[0], "far")

    def test_short_window_uses_the_last_frames_before_the_hit(self):
        dets = setup_dets(5.0, 6.0, "near")       # 0.6 s before the hit at 6.0
        self.assertEqual(setup_before(dets, 6.0, 5.0)[0], "near")
        self.assertEqual(setup_before(setup_dets(5.0, 5.3), 6.0, 5.0), (None, 0.0))


class ServeLikeHit(unittest.TestCase):
    def test_first_hit_with_a_bounce_in_the_other_service_area(self):
        h0, h1 = hit(1.0, "near"), hit(5.0, "near")
        h0.court_xy = (0.5, -10.0)                 # not behind the baseline
        h1.court_xy = (0.5, -12.2)
        self.assertIs(serve_like_hit([h0, h1, bounce(5.6, -2.0, 5.0)]), h1)

    def test_bounce_on_the_own_half_or_none_is_not_serve_like(self):
        h1 = hit(5.0, "far")
        h1.court_xy = (0.5, 12.2)
        self.assertIsNone(serve_like_hit([h1, bounce(5.6, -2.0, 5.0)]))
        h2 = hit(6.2, "near")
        h2.court_xy = (0.5, 11.0)                  # reply from the other end within 2 s
        self.assertIs(serve_like_hit([h1, h2]), h1)


class FinishedSetOnBug(unittest.TestCase):
    def test_final_set_left_in_games_column_counts_as_completed(self):
        fmt = MatchFormat()
        tb = ScoreObs(t=1, sets=[[7, 6], [4, 6]], games=[6, 6], points=["3", "6"], server=1, in_tb=True)
        end = ScoreObs(t=2, sets=[[7, 6], [4, 6]], games=[6, 7], points=["0", "0"], server=1)
        b = scoring.reconcile([tb, end], fmt)
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0].winner, "B")
        self.assertEqual(b[0].score_after["sets"], [[7, 6], [4, 6], [6, 7]])


if __name__ == "__main__":
    unittest.main()
