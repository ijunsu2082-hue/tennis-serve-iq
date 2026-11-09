"""Tests for tennis.patterns (serve habits by side, serve number and situation)
and its place in analytics.refresh and report.html.

Run: .venv/bin/python -m unittest tests.test_patterns
"""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tennis import analytics as an  # noqa: E402
from tennis import patterns as pt  # noqa: E402
from tennis import report as rp  # noqa: E402
from tennis.contracts import MatchFormat  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "match_sample.json")


def flags(bp=False, gp=False):
    return {"break_point": bp, "game_point": gp, "set_point": False, "match_point": False,
            "break_point_for": [], "game_point_for": [], "set_point_for": [], "match_point_for": []}


def S(n, result, zone=None):
    return {"n": n, "t": 1.0, "result": result, "zone": zone, "fault": None, "bounce_xy": None,
            "speed_kmh": None, "conf": 0.5}


def P(pid, server, points, side, serves, winner=None, in_tb=False, fl=None, review=None, **extra):
    p = {"id": pid, "t_end": float(pid), "server": server, "receiver": "B" if server == "A" else "A",
         "side": side, "score_before": {"sets": [], "games": [0, 0], "points": points, "in_tb": in_tb},
         "flags": fl if fl is not None else flags(), "winner": winner, "serves": serves}
    if review is not None:
        p["review"] = review
    p.update(extra)
    return p


def G(sp, player, src, sit, slot, side):
    return pt.group(sp, player, src, sit, slot, side)


class TestSituations(unittest.TestCase):
    def test_server_view_swaps_for_player_b(self):
        self.assertEqual(pt.server_view({"points": "40-0"}, "A"), "40-0")
        self.assertEqual(pt.server_view({"points": "40-0"}, "B"), "0-40")
        self.assertEqual(pt.server_view({"points": "AD-40"}, "B"), "40-AD")
        self.assertEqual(pt.server_view({"points": "A-40"}, "A"), "AD-40")
        self.assertIsNone(pt.server_view({"points": None}, "A"))
        self.assertIsNone(pt.server_view({"points": "40"}, "A"))
        self.assertIsNone(pt.server_view({"points": "15-0"}, None))

    def test_score_groups_server_first(self):
        self.assertEqual(pt.situations(P(1, "A", "40-0", "ad", [])), ["all", "40-0"])
        self.assertEqual(pt.situations(P(1, "B", "40-0", "ad", [])), ["all", "0-40"])
        self.assertEqual(pt.situations(P(1, "B", "15-15", "deuce", [])), ["all"])
        self.assertEqual(pt.situations(P(1, "A", "0-0", "deuce", [])), ["all", "first_point"])
        self.assertEqual(pt.situations(P(1, "A", "40-40", "deuce", [])), ["all", "deuce"])
        self.assertEqual(pt.situations(P(1, "B", "AD-40", "ad", [])), ["all", "adv_receiver"])
        self.assertEqual(pt.situations(P(1, "B", "40-AD", "ad", [])), ["all", "adv_server"])

    def test_break_and_game_point_from_flags(self):
        self.assertEqual(pt.situations(P(1, "A", "30-40", "ad", [], fl=flags(bp=True))),
                         ["all", "break_point"])
        self.assertEqual(pt.situations(P(1, "A", "40-15", "ad", [], fl=flags(gp=True))),
                         ["all", "game_point"])

    def test_flags_worked_out_when_missing(self):
        p = P(1, "B", "40-30", "ad", [])
        del p["flags"]
        self.assertEqual(pt.situations(p, MatchFormat()), ["all", "break_point"])
        p = P(1, "B", "0-40", "ad", [])
        del p["flags"]
        self.assertEqual(pt.situations(p, MatchFormat()), ["all", "game_point", "40-0"])

    def test_tiebreak_is_its_own_group(self):
        # even flags that say break point are ignored inside a tiebreak, and 0-0 is not "first point"
        p = P(1, "A", "0-0", "deuce", [], in_tb=True, fl=flags(bp=True, gp=True))
        self.assertEqual(pt.situations(p), ["all", "tiebreak"])

    def test_unknown_score(self):
        self.assertIsNone(pt.situations(P(1, "A", None, "deuce", [])))


class TestCounting(unittest.TestCase):
    def setUp(self):
        self.pts = [
            P(1, "A", "0-0", "deuce", [S(1, "in", "T")], winner="A"),
            P(2, "A", "15-0", "ad", [S(1, "fault", "wide"), S(2, "in", "body")], winner="B"),
            P(3, "A", "40-0", "ad", [S(1, "in", "wide")], winner="A"),
            P(4, "A", "30-40", "ad", [S(1, "unknown", "T")], winner="B", fl=flags(bp=True)),
            P(5, "A", "15-15", "deuce", [S(1, "in", None)], winner="A"),
            P(6, "B", "0-0", "deuce", [S(1, "fault"), S(2, "fault")], winner="A"),
            P(7, "B", "15-0", "ad", [], winner="B"),
            P(8, "B", "3-2", "ad", [S(1, "in", "T")], winner="B", in_tb=True),
        ]
        self.sp = pt.compute(self.pts)

    def test_points_and_situations(self):
        a, b = self.sp["players"]["A"], self.sp["players"]["B"]
        self.assertEqual((a["points_served"], b["points_served"]), (5, 3))
        self.assertEqual(b["points_without_serve"], 1)
        self.assertEqual(a["situation_points"]["all"], {"deuce": 2, "ad": 3, "side_unknown": 0, "both": 5})
        self.assertEqual(a["situation_points"]["break_point"]["both"], 1)
        self.assertEqual(a["situation_points"]["40-0"]["ad"], 1)
        self.assertEqual(b["situation_points"]["tiebreak"]["both"], 1)
        self.assertEqual(b["situation_points"]["first_point"]["deuce"], 1)

    def test_direction_by_side_and_serve(self):
        g = G(self.sp, "A", "auto", "all", "1", "both")
        self.assertEqual((g["in"], g["fault"], g["result_unknown"]), (3, 1, 1))
        # direction only for serves that went in: the fault's and the unknown serve's zones are not used
        self.assertEqual((g["T"], g["body"], g["wide"], g["direction_unknown"]), (1, 0, 1, 1))
        self.assertEqual((g["serves"], g["direction_seen"]), (5, 2))
        self.assertEqual(G(self.sp, "A", "auto", "all", "1", "deuce")["T"], 1)
        self.assertEqual(G(self.sp, "A", "auto", "all", "1", "ad")["wide"], 1)
        g2 = G(self.sp, "A", "auto", "all", "2", "ad")
        self.assertEqual((g2["in"], g2["body"]), (1, 1))
        self.assertEqual(G(self.sp, "A", "auto", "40-0", "1", "ad")["wide"], 1)
        self.assertEqual(G(self.sp, "A", "auto", "break_point", "1", "ad")["result_unknown"], 1)
        # the tiebreak serve is in the tiebreak group only (and all points)
        self.assertEqual(G(self.sp, "B", "auto", "tiebreak", "1", "ad")["T"], 1)
        self.assertEqual(G(self.sp, "B", "auto", "first_point", "1", "deuce")["fault"], 1)

    def test_first_serve_in_and_double_faults(self):
        a = self.sp["players"]["A"]["sources"]["auto"]
        self.assertEqual((a["first_serve_in"]["in"], a["first_serve_in"]["fault"],
                          a["first_serve_in"]["unknown"]), (3, 1, 1))
        self.assertEqual((a["first_serve_in"]["rate"]["k"], a["first_serve_in"]["rate"]["n"]), (3, 4))
        self.assertTrue(a["first_serve_in"]["too_small"])
        b = self.sp["players"]["B"]["sources"]["auto"]
        self.assertEqual((b["double_faults"]["count"], b["double_faults"]["second_serves_known"]), (1, 1))

    def test_double_fault_the_score_rules_out(self):
        sp = pt.compute([P(1, "A", "0-0", "deuce", [S(1, "fault"), S(2, "fault")], winner="A")])
        df = sp["players"]["A"]["sources"]["auto"]["double_faults"]
        self.assertEqual((df["count"], df["score_disagrees"], df["second_serves_known"]), (0, 1, 1))

    def test_shares_and_too_small_line(self):
        pts = [P(i, "A", "15-15", "deuce", [S(1, "in", "T" if i < 7 else "wide")]) for i in range(9)]
        g = G(pt.compute(pts), "A", "auto", "all", "1", "deuce")
        self.assertEqual((g["direction_seen"], g["too_small"]), (9, True))
        self.assertEqual((g["pct"]["T"], g["pct"]["wide"], g["pct"]["body"]), (77.8, 22.2, 0.0))
        pts.append(P(9, "A", "15-15", "deuce", [S(1, "in", "body")]))
        g = G(pt.compute(pts), "A", "auto", "all", "1", "deuce")
        self.assertEqual((g["direction_seen"], g["too_small"]), (10, False))

    def test_lets_and_number_unclear(self):
        p = P(1, "A", "0-0", "deuce", [S(1, "fault"), S(2, "in", "T")], winner="A",
              lets=[{"t": 1.0, "n": 2}],
              serves_number_unclear=[{"t": 9.0, "result": "in", "zone": "wide"},
                                     {"t": 9.5, "result": "unknown", "zone": "T"}])
        sp = pt.compute([p])
        self.assertEqual(G(sp, "A", "auto", "all", "2", "both")["lets"], 1)
        nu = G(sp, "A", "auto", "all", "number_unknown", "both")
        self.assertEqual((nu["in"], nu["wide"], nu["result_unknown"], nu["T"]), (1, 1, 1, 0))
        S_ = sp["players"]["A"]["sources"]["auto"]
        self.assertEqual((S_["serves"], S_["lets"]), (4, 1))

    def test_side_unknown_and_score_unknown(self):
        sp = pt.compute([P(1, "A", None, None, [S(1, "in", "T")])])
        st = sp["players"]["A"]
        self.assertEqual(st["points_score_unknown"], 1)
        self.assertEqual(st["situation_points"]["all"]["side_unknown"], 1)
        self.assertEqual(G(sp, "A", "auto", "all", "1", "side_unknown")["T"], 1)
        self.assertEqual(sp["points_server_unknown"], 0)
        self.assertEqual(pt.compute([P(1, None, "0-0", "deuce", [])])["points_server_unknown"], 1)

    def test_empty_groups_and_sources_left_out(self):
        self.assertNotIn("checked", self.sp["players"]["A"]["sources"])
        self.assertNotIn("40-0", self.sp["players"]["B"]["sources"]["auto"]["groups"])
        g = G(self.sp, "B", "checked", "40-0", "1", "ad")
        self.assertEqual((g["serves"], g["too_small"], g["pct"]["T"]), (0, True, None))


class TestCheckedKeptApart(unittest.TestCase):
    def src(self, p, n=1):
        s = next(x for x in p["serves"] if x["n"] == n)
        return pt.serve_source(p, s)

    def test_rules(self):
        rv = lambda **k: {"status": "edited", "edited_fields": k.get("e", []), "checked_fields": k.get("c", [])}
        self.assertEqual(self.src(P(1, "A", "0-0", "deuce", [S(1, "in", "T")])), "auto")
        self.assertEqual(self.src(P(1, "A", "0-0", "deuce", [S(1, "in", "T")],
                                    review={"status": "reviewed", "edited_fields": []})), "checked")
        self.assertEqual(self.src(P(1, "A", "0-0", "deuce", [S(1, "in", "T")],
                                    review=rv(c=["serve1_result"], e=["serve1_zone"]))), "checked")
        # in or out checked, but the direction was the computer's: not checked
        self.assertEqual(self.src(P(1, "A", "0-0", "deuce", [S(1, "in", "T")],
                                    review=rv(c=["serve1_result"]))), "auto")
        # in or out checked, direction unknown: checked (the check could not tell)
        self.assertEqual(self.src(P(1, "A", "0-0", "deuce", [S(1, "in", None)],
                                    review=rv(c=["serve1_result"]))), "checked")
        # the check found the serve but could not tell in or out
        self.assertEqual(self.src(P(1, "A", "0-0", "deuce", [S(1, "unknown")], review=rv(c=["serve1_t"]))),
                         "checked")
        # a review of other fields of the point does not check the serve
        self.assertEqual(self.src(P(1, "A", "0-0", "deuce", [S(1, "unknown")], review=rv(c=["winner"]))),
                         "auto")
        # results set through serve_number_won_on and the zone through serve_zone
        p = P(1, "A", "0-0", "deuce", [S(1, "fault"), S(2, "in", "wide")], serve_number_won_on=2,
              review=rv(e=["serve_number_won_on", "serve_zone"]))
        self.assertEqual((self.src(p, 1), self.src(p, 2)), ("checked", "checked"))
        # serve 1 checked does not check serve 2
        p = P(1, "A", "0-0", "deuce", [S(1, "fault"), S(2, "in", "T")], review=rv(c=["serve1_result"]))
        self.assertEqual((self.src(p, 1), self.src(p, 2)), ("checked", "auto"))

    def test_sources_never_mix(self):
        rv = {"status": "edited", "edited_fields": ["serve1_result", "serve1_zone", "lets"], "checked_fields": []}
        pts = [P(1, "A", "0-0", "deuce", [S(1, "in", "T")], review=rv, lets=[{"t": 0.5, "n": 1}]),
               P(2, "A", "15-0", "ad", [S(1, "in", "wide")]),
               P(3, "A", "30-0", "deuce", [S(1, "in", "body")], serves_number_unclear=[{"result": "in", "zone": "T"}])]
        sp = pt.compute(pts)
        c, a = sp["players"]["A"]["sources"]["checked"], sp["players"]["A"]["sources"]["auto"]
        self.assertEqual((c["serves"], c["lets"], a["serves"], a["lets"]), (1, 1, 3, 0))
        gc, ga = G(sp, "A", "checked", "all", "1", "both"), G(sp, "A", "auto", "all", "1", "both")
        self.assertEqual((gc["T"], gc["wide"], gc["body"]), (1, 0, 0))
        self.assertEqual((ga["T"], ga["wide"], ga["body"]), (0, 1, 1))
        self.assertEqual(G(sp, "A", "auto", "all", "number_unknown", "both")["T"], 1)


class TestView(unittest.TestCase):
    def test_words_and_rows(self):
        pts = [P(i, "A", "15-15" if i % 2 else "15-0", "deuce" if i % 2 else "ad",
                 [S(1, "in", "wide" if i < 8 else "T")], winner="A") for i in range(24)]
        pts.append(P(30, "A", "0-0", "deuce", [S(1, "fault"), S(2, "fault")], winner="B"))
        v = pt.view(pt.compute(pts))["A"]
        self.assertEqual(v["note"], "Points served: 25.")
        lines = v["sources"][0]["lines"]
        self.assertEqual(v["sources"][0]["key"], "auto")
        self.assertEqual(lines[0], "First serve in: 24 of 25 (96%).")
        self.assertEqual(lines[1], "Double faults: 1 of 1 (100%) second serves whose result is known, "
                                   "too small to call a habit.")
        self.assertIn("First serves, deuce side: T 8 of 12 (67%), wide 4 of 12 (33%), body 0 of 12 (0%).",
                      lines)
        rows = v["sources"][0]["tables"][0]["rows"]
        first_point = [r for r in rows if r["situation"] == "First point of a game"]
        self.assertEqual([r["side"] for r in first_point], ["deuce side"])   # one side, so no "both" row
        allp = [r for r in rows if r["situation"] == "All points"]
        self.assertEqual([r["side"] for r in allp], ["deuce side", "ad side", "both sides"])
        bp = [r for r in rows if r["situation"].startswith("Break point")]
        self.assertEqual(len(bp), 1)
        self.assertEqual((bp[0]["points"], bp[0]["too_small"]), (0, True))

    def test_pct_text_rounds_halves_up(self):
        self.assertEqual(pt.pct_text(1, 8), "1 of 8 (13%)")
        self.assertEqual(pt.pct_text(17, 40), "17 of 40 (43%)")
        self.assertEqual(pt.pct_text(2, 3), "2 of 3 (67%)")
        self.assertEqual(pt.pct_text(0, 0), "0 of 0")

    def test_plurals(self):
        p = P(1, "A", "0-0", "deuce", [S(1, "unknown"), S(2, "unknown")], lets=[{"n": 1}],
              serves_number_unclear=[{"result": "in", "zone": None}])
        lines = pt.view(pt.compute([p]))["A"]["sources"][0]["lines"]
        text = " ".join(lines)
        self.assertIn("On 1 more first serve, in or out is unknown.", text)
        self.assertIn("On 1 more second serve, in or out is unknown.", text)
        self.assertIn("1 serve could not be marked as a first or second serve, so it is not in the rows", text)
        self.assertIn("1 let (a serve that touched the net and was taken again) is not counted", text)


class TestInReport(unittest.TestCase):
    def test_refresh_and_render(self):
        with open(FIXTURE) as f:
            match = json.load(f)
        m = an.refresh(json.loads(json.dumps(match)))
        sp = m["serve_patterns"]
        # every serve record of the fixture is counted once, under its server and number
        for who in ("A", "B"):
            for n in (1, 2):
                recs = [s for p in m["points"] if p["server"] == who for s in p["serves"] if s["n"] == n]
                g = pt.group(sp, who, "auto", "all", str(n), "both")
                self.assertEqual(g["serves"], len(recs))
                self.assertEqual(g["in"], sum(1 for s in recs if s["result"] == "in"))
        with tempfile.TemporaryDirectory() as d:
            paths = rp.render(match, d)
            with open(paths["html"], encoding="utf-8") as f:
                html = f.read()
            with open(paths["json"]) as f:
                self.assertIn("serve_patterns", json.load(f))
        sec = html[html.index('<section id="habits">'):]
        sec = sec[:sec.index("</section>")]
        names = m["meta"]["players"]
        self.assertIn("%s serving" % names["A"]["name"], sec)
        self.assertIn("%s serving" % names["B"]["name"], sec)
        self.assertIn("Automatic values (found by the computer, not checked)", sec)
        self.assertNotIn("Checked values", sec)
        self.assertIn('href="#habits"', html)
        text = re.sub(r"<[^>]+>", " ", sec)
        for ch in ("—", "–"):
            self.assertNotIn(ch, text)
        self.assertEqual(sec.count("<details"), sec.count("</details>"))


if __name__ == "__main__":
    unittest.main()
