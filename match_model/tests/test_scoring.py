"""Tests for tennis.scoring. Run: .venv/bin/python -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tennis.contracts import MatchFormat, ScoreState, ScoreObs  # noqa: E402
from tennis import scoring as sc  # noqa: E402


def play(state, seq, fmt):
    for p in seq:
        state = sc.apply(state, p, fmt)
    return state


def obs(t, sets, games, points, server, in_tb=False, conf=1.0):
    return ScoreObs(t=t, sets=sets, games=games, points=[str(x) for x in points],
                    server=server, in_tb=in_tb, conf=conf)


class TestGames(unittest.TestCase):
    def setUp(self):
        self.fmt = MatchFormat()
        self.s0 = sc.initial_state(self.fmt, "A")

    def test_labels_simple_game(self):
        s = self.s0
        labels = []
        for p in "AABAA":
            labels.append(sc.point_label(s))
            s = sc.apply(s, p, self.fmt)
        self.assertEqual(labels, ["0-0", "15-0", "30-0", "30-15", "40-15"])
        self.assertEqual(s.games, [1, 0])
        self.assertEqual(s.points, [0, 0])
        self.assertEqual(s.server, "B")

    def test_deuce_advantage(self):
        s = play(self.s0, "AAABBB", self.fmt)
        self.assertEqual(sc.point_label(s), "40-40")
        self.assertEqual(sc.side(s, self.fmt), "deuce")
        s = sc.apply(s, "B", self.fmt)
        self.assertEqual(sc.point_label(s), "40-AD")
        self.assertEqual(sc.side(s, self.fmt), "ad")
        f = sc.flags(s, self.fmt)
        self.assertTrue(f["break_point"])
        self.assertEqual(f["break_point_for"], ["B"])
        self.assertFalse(f["game_point"])
        s = sc.apply(s, "A", self.fmt)
        self.assertEqual(sc.point_label(s), "40-40")
        self.assertEqual(s.points, [3, 3])  # canonical
        s = play(s, "AB" * 5, self.fmt)       # long deuce game
        self.assertEqual(s.points, [3, 3])
        self.assertEqual(s.games, [0, 0])
        s = play(s, "AA", self.fmt)
        self.assertEqual(s.games, [1, 0])

    def test_game_point_flag(self):
        s = play(self.s0, "AAA", self.fmt)  # 40-0 server A
        f = sc.flags(s, self.fmt)
        self.assertTrue(f["game_point"])
        self.assertFalse(f["break_point"])
        self.assertEqual(f["game_point_for"], ["A"])

    def test_no_ad(self):
        fmt = MatchFormat(no_ad=True)
        s = play(sc.initial_state(fmt, "A"), "AAABBB", fmt)
        self.assertEqual(sc.point_label(s), "40-40")
        self.assertEqual(sc.side(s, fmt), sc.NO_AD_DECIDING_SIDE)
        self.assertEqual(sc.side(s, fmt), "ad")
        self.assertEqual(sc.side(s, fmt, no_ad_side="deuce"), "deuce")
        f = sc.flags(s, fmt)
        self.assertTrue(f["break_point"] and f["game_point"])
        self.assertEqual(sorted(f["game_point_for"]), ["A", "B"])
        s2 = sc.apply(s, "B", fmt)
        self.assertEqual(s2.games, [0, 1])
        self.assertEqual(s2.server, "B")

    def test_no_ad_set_point_deciding(self):
        fmt = MatchFormat(no_ad=True)
        s = ScoreState(games=[5, 4], points=[3, 3], server="B")
        f = sc.flags(s, fmt)
        self.assertEqual(f["set_point_for"], ["A"])
        self.assertTrue(f["break_point"])

    def test_set_transition_and_ends(self):
        fmt = self.fmt
        s = self.s0
        ends_after = []
        for g in range(10):   # A holds, B holds alternately -> but make it 6-4 A
            winner = "A" if g % 2 == 0 or g == 9 else "B"
            if g == 9:
                winner = "A"
            s = play(s, winner * 4, fmt)
            ends_after.append(s.ends["A"])
        # games: A,B,A,B,A,B,A,B,A,A => 6-4
        self.assertEqual(s.sets, [[6, 4]])
        self.assertEqual(s.games, [0, 0])
        # changes after games 1,3,5,7,9 -> near->far after 1, far at 2, near after 3...
        self.assertEqual(ends_after, ["far", "far", "near", "near", "far", "far",
                                      "near", "near", "far", "far"])
        # set total 10 is even: no change at set end, change after game 1 of set 2
        s = play(s, "BBBB", fmt)
        self.assertEqual(s.ends["A"], "near")

    def test_set_odd_total_changes_at_set_end(self):
        fmt = self.fmt
        s = ScoreState(games=[5, 3], server="A")
        before = dict(s.ends)
        s = play(s, "AAAA", fmt)  # 6-3: total 9 -> change
        self.assertEqual(s.sets, [[6, 3]])
        self.assertEqual(s.ends["A"], before["B"])
        s = play(s, "AAAA", fmt)  # game 1 of set 2: odd -> change back
        self.assertEqual(s.ends["A"], before["A"])

    def test_serve_alternates_across_sets(self):
        fmt = self.fmt
        s = ScoreState(games=[5, 3], server="A")
        s = play(s, "AAAA", fmt)
        self.assertEqual(s.server, "B")

    def test_7_5(self):
        fmt = self.fmt
        s = ScoreState(games=[6, 5], server="A")
        s = play(s, "AAAA", fmt)
        self.assertEqual(s.sets, [[7, 5]])

    def test_set_point_match_point(self):
        fmt = self.fmt
        s = ScoreState(sets=[[6, 2]], games=[5, 4], points=[3, 1], server="A")
        f = sc.flags(s, fmt)
        self.assertTrue(f["set_point"] and f["match_point"])
        self.assertEqual(f["match_point_for"], ["A"])
        s = sc.apply(s, "A", fmt)
        self.assertTrue(s.match_over)
        self.assertEqual(s.sets, [[6, 2], [6, 4]])
        with self.assertRaises(ValueError):
            sc.apply(s, "A", fmt)


class TestTiebreak(unittest.TestCase):
    def test_full_tiebreak_server_side_ends(self):
        fmt = MatchFormat()
        # 6-6, B served game 12 so A serves first in the TB
        s = ScoreState(games=[6, 5], server="B", ends={"A": "near", "B": "far"})
        s = play(s, "BBBB", fmt)  # B holds -> 6-6, total 12 even -> no change
        self.assertTrue(s.in_tb)
        self.assertEqual(s.games, [6, 6])
        self.assertEqual(s.server, "A")
        self.assertEqual(s.tb_first_server, "A")
        self.assertEqual(s.ends, {"A": "near", "B": "far"})
        # TB: A wins 7-5 with this sequence (12 points)
        seq = "ABABABABABAA"
        servers, sides, ends_a, labels = [], [], [], []
        for p in seq:
            servers.append(s.server)
            sides.append(sc.side(s, fmt))
            labels.append(sc.point_label(s))
            s = sc.apply(s, p, fmt)
            ends_a.append(s.ends["A"])
        self.assertEqual("".join(servers), "ABBAABBAABBA")
        self.assertEqual(sides, ["deuce", "ad"] * 6)
        self.assertEqual(labels[:4], ["0-0", "1-0", "1-1", "2-1"])
        # change after 6 points; tb ends at 12 points -> exactly one change at end
        self.assertEqual(ends_a[:5], ["near"] * 5)
        self.assertEqual(ends_a[5:11], ["far"] * 6)
        self.assertEqual(ends_a[11], "near")
        self.assertEqual(s.sets, [[7, 6]])
        self.assertFalse(s.in_tb)
        # next set: the player who received first in the TB serves (B)
        self.assertEqual(s.server, "B")

    def test_tiebreak_ends_not_multiple_of_6(self):
        fmt = MatchFormat()
        s = ScoreState(games=[6, 6], in_tb=True, server="A", tb_first_server="A")
        s = play(s, "AAAAAAA", fmt)  # 7-0, change after 6 and at end
        self.assertEqual(s.sets, [[7, 6]])
        self.assertEqual(s.ends["A"], "near")  # two swaps

    def test_tb_win_by_two_and_set_point_flags(self):
        fmt = MatchFormat()
        s = ScoreState(games=[6, 6], in_tb=True, server="A", tb_first_server="A")
        s = play(s, "ABABABABABAB", fmt)  # 6-6
        self.assertEqual(sc.point_label(s), "6-6")
        f = sc.flags(s, fmt)
        self.assertFalse(f["set_point"])
        self.assertFalse(f["break_point"])
        s = sc.apply(s, "B", fmt)
        f = sc.flags(s, fmt)
        self.assertEqual(f["set_point_for"], ["B"])
        self.assertFalse(f["break_point"])
        s = play(s, "AAA", fmt)  # 9-7 A
        self.assertEqual(s.sets, [[7, 6]])

    def test_final_set_adv(self):
        fmt = MatchFormat(final_set="adv")
        s = ScoreState(sets=[[6, 4], [4, 6]], games=[6, 5], server="B")
        s = play(s, "BBBB", fmt)
        self.assertFalse(s.in_tb)
        self.assertEqual(s.games, [6, 6])
        s = play(s, "AAAA", fmt)
        self.assertEqual(s.games, [7, 6])
        s = play(s, "AAAA", fmt)
        self.assertTrue(s.match_over)
        self.assertEqual(s.sets[-1], [8, 6])

    def test_match_tb10(self):
        fmt = MatchFormat(final_set="match_tb10")
        s = ScoreState(sets=[[6, 4]], games=[5, 6], server="A")
        s = play(s, "BBBB", fmt)  # B breaks: 5-7 -> sets 1-1, match TB
        self.assertEqual(s.sets, [[6, 4], [5, 7]])
        self.assertTrue(s.in_tb)
        self.assertEqual(s.games, [0, 0])
        self.assertEqual(s.server, "B")       # alternation continues
        self.assertEqual(s.tb_first_server, "B")
        s = play(s, "A" * 9, fmt)
        self.assertEqual(sc.point_label(s), "9-0")
        self.assertFalse(s.match_over)
        f = sc.flags(s, fmt)
        self.assertEqual(f["match_point_for"], ["A"])
        s = play(s, "B" * 9, fmt)     # 9-9
        self.assertFalse(s.match_over)
        s = play(s, "AA", fmt)        # 11-9
        self.assertTrue(s.match_over)
        self.assertEqual(s.sets[-1], [1, 0])

    def test_match_tb10_from_obs(self):
        fmt = MatchFormat(final_set="match_tb10")
        o = obs(1, [[6, 4], [5, 7]], [0, 0], ["3", "2"], 0)
        st = sc.state_from_obs(o, None, fmt)
        self.assertTrue(st.in_tb)
        self.assertEqual(st.points, [3, 2])


class TestObs(unittest.TestCase):
    def test_parse(self):
        fmt = MatchFormat()
        st = sc.state_from_obs(obs(0, [[7, 6]], [3, 2], ["A", "40"], 1), None, fmt)
        self.assertEqual(st.points, [4, 3])
        self.assertEqual(st.server, "B")
        st = sc.state_from_obs(obs(0, [], [6, 6], ["3", "2"], 0), None, fmt)
        self.assertTrue(st.in_tb)
        self.assertEqual(st.tb_first_server, "B")  # A serves point 5 -> B served first
        with self.assertRaises(ValueError):
            sc.state_from_obs(obs(0, [], [3, 2], ["7", "1"], 0), None, fmt)
        with self.assertRaises(ValueError):
            sc.state_from_obs(obs(0, [], [3, 2], ["A", "15"], 0), None, fmt)

    def test_names_map_swaps_rows(self):
        st = sc.state_from_obs(obs(0, [[6, 3]], [1, 2], ["15", "30"], 0), {0: "B", 1: "A"})
        self.assertEqual(st.sets, [[3, 6]])
        self.assertEqual(st.games, [2, 1])
        self.assertEqual(st.points, [2, 1])
        self.assertEqual(st.server, "B")


class TestReconcile(unittest.TestCase):
    def test_clean_sequence(self):
        fmt = MatchFormat()
        o = [obs(0, [], [0, 0], [0, 0], 0), obs(10, [], [0, 0], [15, 0], 0),
             obs(11, [], [0, 0], [15, 0], 0),
             obs(20, [], [0, 0], [15, 15], 0), obs(30, [], [0, 0], [30, 15], 0),
             obs(40, [], [0, 0], [40, 15], 0), obs(50, [], [1, 0], [0, 0], 1)]
        b = sc.reconcile(o, fmt)
        self.assertEqual([x.winner for x in b], ["A", "B", "A", "A", "A"])
        self.assertEqual([x.t_end for x in b], [10, 20, 30, 40, 50])
        self.assertTrue(all(x.source == "ocr" and x.conf == 0.98 for x in b))
        self.assertFalse(any(x.gap_before for x in b))
        self.assertEqual(b[4].score_before["points"], "40-15")
        self.assertTrue(b[4].flags["game_point"])
        self.assertEqual(b[4].score_after["games"], [1, 0])
        self.assertEqual([x.side for x in b], ["deuce", "ad", "deuce", "ad", "deuce"])

    def test_transient_misread_ignored(self):
        fmt = MatchFormat()
        o = [obs(0, [], [2, 1], [15, 0], 0),
             obs(5, [], [2, 1], [15, 30], 0),      # reverts next -> ignored
             obs(6, [], [2, 1], [15, 0], 0),
             obs(7, [], [2, 1], ["1S", 0], 0),     # unreadable -> ignored
             obs(10, [], [2, 1], [30, 0], 0),
             obs(12, [], [2, 7], [30, 15], 0),     # illegal, next consistent -> ignored
             obs(15, [], [2, 1], [30, 15], 0)]
        b = sc.reconcile(o, fmt)
        self.assertEqual([x.winner for x in b], ["A", "B"])
        self.assertEqual([x.t_end for x in b], [10, 15])
        self.assertTrue(b[0].gap_before)       # first reading wasn't the match start
        self.assertFalse(b[1].gap_before)

    def test_deuce_back_and_forth_is_real(self):
        """40-40, AD-40, 40-40 goes back to an earlier score and that is legal. Readings
        that stay on the board this long are points, not misreads."""
        fmt = MatchFormat()
        o = [obs(0, [], [1, 1], [40, 30], 0),
             obs(30, [], [1, 1], [40, 40], 0),
             obs(60, [], [1, 1], ["A", 40], 0),
             obs(90, [], [1, 1], [40, 40], 0),
             obs(120, [], [1, 1], [40, "A"], 0),
             obs(150, [], [1, 1], [40, 40], 0),
             obs(180, [], [1, 1], ["A", 40], 0),
             obs(210, [], [2, 1], [0, 0], 1)]
        b = sc.reconcile(o, fmt)
        self.assertEqual([x.winner for x in b], ["B", "A", "B", "B", "A", "A", "A"])
        self.assertEqual([x.score_before["points"] for x in b],
                         ["40-30", "40-40", "AD-40", "40-40", "40-AD", "40-40", "AD-40"])
        self.assertEqual([x.side for x in b], ["ad", "deuce", "ad", "deuce", "ad", "deuce", "ad"])
        self.assertEqual([x.flags["break_point"] for x in b], [False, False, False, False, True, False, False])
        self.assertTrue(all(x.source == "ocr" for x in b))

    def test_blink_of_advantage_is_a_misread(self):
        """A one second advantage that goes straight back is not a point."""
        fmt = MatchFormat()
        o = [obs(0, [], [1, 1], [40, 40], 0),
             obs(30, [], [1, 1], ["A", 40], 0),       # reverts after 1 s -> ignored
             obs(31, [], [1, 1], [40, 40], 0),
             obs(60, [], [1, 1], [40, "A"], 0),
             obs(90, [], [1, 2], [0, 0], 1)]
        b = sc.reconcile(o, fmt)
        self.assertEqual([x.score_before["points"] for x in b], ["40-40", "40-AD"])
        self.assertEqual([x.t_end for x in b], [60, 90])

    def test_long_held_score_that_cannot_go_back_is_still_a_misread(self):
        """Away from deuce a score can never go back, however long the wrong one stayed."""
        fmt = MatchFormat()
        o = [obs(0, [], [2, 1], [15, 0], 0),
             obs(5, [], [2, 1], [30, 0], 0),          # stays 40 s, but 30-0 back to 15-0 is impossible
             obs(45, [], [2, 1], [15, 0], 0),
             obs(70, [], [2, 1], [15, 15], 0)]
        b = sc.reconcile(o, fmt)
        self.assertEqual([x.score_before["points"] for x in b], ["15-0"])
        self.assertEqual(b[0].winner, "B")

    def test_gap_unique_fill_and_resync(self):
        fmt = MatchFormat()
        o = [obs(0, [], [0, 0], [0, 0], 0),
             obs(10, [], [0, 0], [15, 0], 0),
             obs(30, [], [0, 0], [40, 0], 0),      # 2 apart, unique (A,A) -> filled
             obs(40, [], [0, 0], [40, 15], 0),
             obs(90, [], [2, 1], [0, 0], 1),       # highlight jump -> resync
             obs(95, [], [2, 1], [0, 15], 1),
             obs(99, [], [2, 1], [15, 30], 1)]     # 2 apart, ambiguous -> resync
        b = sc.reconcile(o, fmt)
        self.assertEqual([x.winner for x in b], ["A", "A", "A", "B", "B"])
        self.assertEqual([x.source for x in b], ["ocr", "engine", "engine", "ocr", "ocr"])
        self.assertEqual([x.gap_before for x in b], [False, True, True, False, True])
        self.assertEqual(b[1].conf, 0.75)
        self.assertEqual(b[4].server, "B")
        self.assertEqual(b[4].score_before["games"], [2, 1])
        # ends carried across the in-set jump: totals 0 -> 3 cross games 1 and 3 -> two swaps
        self.assertEqual(b[4].score_before["ends"], {"A": "near", "B": "far"})

    def test_server_dot_overrides_after_two_reads(self):
        fmt = MatchFormat()
        o = [obs(0, [], [0, 0], [0, 0], None),      # unknown dot -> default A
             obs(10, [], [0, 0], [15, 0], 1),
             obs(11, [], [0, 0], [15, 0], 1),       # second mismatch -> server B
             obs(20, [], [0, 0], [30, 0], 1)]
        b = sc.reconcile(o, fmt)
        self.assertEqual(b[0].server, "B")          # retro-fixed: same game
        self.assertEqual(b[1].server, "B")

    def test_beijing_2024_third_set_tiebreak(self):
        """Highlight-like readings of the Beijing 2024 final's deciding tiebreak.

        Sinner = A (top row), Alcaraz = B. Sets 7-6, 4-6, 6-6; Alcaraz won the
        tiebreak 7-3. The point order and serve dots are illustrative (we only
        know the final 3-7), and points are skipped the way a highlight
        package skips them.
        """
        fmt = MatchFormat()
        S = [[7, 6], [4, 6]]
        G = [6, 6]
        o = [obs(0, S, G, [0, 0], 0),           # A serves point 1
             obs(20, S, G, [0, 1], 1),          # B wins (mini-break)
             obs(40, S, G, [1, 1], 1),
             obs(45, S, G, [7, 1], 1),          # misread, reverts -> ignored
             obs(46, S, G, [1, 1], 1),
             obs(80, S, G, [1, 3], 0),          # skipped 2: unique (B,B) -> filled
             obs(100, S, G, [2, 3], 1),
             obs(130, S, G, [3, 5], 0),         # skipped 3, ambiguous -> resync gap
             obs(150, S, G, [3, 6], 1),         # match point Alcaraz (A to serve?)
             obs(170, S + [[6, 7]], [0, 0], [0, 0], None)]
        b = sc.reconcile(o, fmt)
        self.assertEqual([x.winner for x in b], ["B", "A", "B", "B", "A", "B", "B"])
        self.assertEqual([x.score_before["points"] for x in b],
                         ["0-0", "0-1", "1-1", "1-2", "1-3", "3-5", "3-6"])
        self.assertEqual([x.gap_before for x in b],
                         [True, False, True, True, False, True, False])
        self.assertTrue(all(x.score_before["in_tb"] for x in b))
        # TB server sequence with A first: A B B A A ...
        self.assertEqual([x.server for x in b[:5]], ["A", "B", "B", "A", "A"])
        # point 9 (k=8) -> A serves; point 10 (k=9) -> B serves
        self.assertEqual(b[5].server, "A")
        self.assertEqual(b[6].server, "B")
        last = b[-1]
        self.assertTrue(last.flags["match_point"])
        self.assertEqual(last.flags["match_point_for"], ["B"])
        self.assertTrue(last.flags["set_point"])
        self.assertFalse(last.flags["break_point"])
        self.assertEqual(last.score_after["sets"], [[7, 6], [4, 6], [6, 7]])
        self.assertEqual(last.t_end, 170)
        self.assertEqual(b[5].side, "deuce")   # 8 points played
        self.assertEqual(b[6].side, "ad")


class TestReplay(unittest.TestCase):
    def test_replay_full_set_and_none(self):
        fmt = MatchFormat()
        winners = (["A"] * 4 + ["B"] * 4) * 6 + ["A"] * 7   # 6-6 then TB 7-0
        b = sc.replay(winners, fmt)
        self.assertEqual(len(b), 48 + 7)
        self.assertEqual(b[48].score_before["games"], [6, 6])
        self.assertTrue(b[48].score_before["in_tb"])
        self.assertEqual(b[-1].score_after["sets"], [[7, 6]])
        self.assertTrue(all(x.source == "engine" for x in b))
        b2 = sc.replay(["A", None, "A"], fmt)
        self.assertIsNone(b2[1].winner)
        self.assertTrue(b2[2].gap_before)
        self.assertEqual(b2[2].score_before["points"], "15-0")

    def test_round_trip_score_dict(self):
        fmt = MatchFormat()
        s = ScoreState(sets=[[6, 4]], games=[6, 6], points=[3, 2], in_tb=True,
                       server="B", tb_first_server="A")
        d = sc.score_dict(s)
        r = sc.state_from_score_dict(d, "B", fmt)
        self.assertEqual(sc.state_key(r), sc.state_key(s))
        self.assertEqual(r.tb_first_server, "A")


if __name__ == "__main__":
    unittest.main()
