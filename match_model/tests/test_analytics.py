"""Tests for tennis.analytics and tennis.report.

Run:  .venv/bin/python -m unittest discover -s tests
Regenerate the fixture:  .venv/bin/python tests/test_analytics.py --write-fixture
"""
import copy
import csv
import json
import os
import random
import sys
import tempfile
import unittest
from dataclasses import asdict
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tennis.contracts import MatchFormat, ScoreState, PointRecord, other  # noqa: E402
from tennis import scoring as sc  # noqa: E402
from tennis import analytics as an  # noqa: E402
from tennis import report as rp  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "match_sample.json")


# ---------------------------------------------------------------------------
# synthetic match generator (scores come from scoring.replay)
# ---------------------------------------------------------------------------
def _sim_point(rng, S, pressure):
    """Simulate one point's vision fields for server S. Returns dict incl. winner."""
    R = other(S)
    first_in_p = {"A": 0.64, "B": 0.6}[S]
    if S == "B" and pressure:
        first_in_p = 0.3
    zone_w = {("A", "deuce"): (0.2, 0.1, 0.7), ("A", "ad"): (0.55, 0.15, 0.3),
              ("B", "deuce"): (0.4, 0.2, 0.4), ("B", "ad"): (0.35, 0.3, 0.35)}
    d = {"serves": [], "serve_number_won_on": None, "return_wing": None, "rally_len": None,
         "ending": None, "ending_by": None, "last_shot_wing": None, "last_shot_stroke": None,
         "last_shot_result": None, "net": {"A": 0, "B": 0}}
    serve_no = None
    for n in (1, 2):
        p_in = first_in_p if n == 1 else 0.9
        if rng.random() < p_in:
            serve_no = n
            d["serves"].append({"n": n, "result": "in"})
            break
        d["serves"].append({"n": n, "result": "fault",
                            "fault": rng.choice(["net", "net", "long", "wide", "long_wide"])})
    if serve_no is None:
        d.update(ending="double_fault", ending_by=S, rally_len=0, winner=R)
        return d
    d["serve_number_won_on"] = serve_no
    d["_zone_w"] = zone_w
    r = rng.random()
    if r < (0.09 if serve_no == 1 else 0.02):
        d.update(ending="ace", ending_by=S, rally_len=1, winner=S)
        return d
    if r < (0.17 if serve_no == 1 else 0.05):
        d.update(ending="service_winner", ending_by=S, rally_len=1, winner=S)
        return d
    wing = "bh" if rng.random() < 0.5 else "fh"
    d["return_wing"] = wing
    miss = {("B", "bh"): 0.55, ("B", "fh"): 0.12, ("A", "bh"): 0.18, ("A", "fh"): 0.14}[(R, wing)]
    if rng.random() < miss:
        d.update(ending="forced_error" if serve_no == 1 else "unforced_error", ending_by=R,
                 rally_len=2, last_shot_wing=wing, last_shot_stroke="groundstroke",
                 last_shot_result=rng.choice(["net", "out_long", "out_wide"]), winner=S)
        return d
    rally = 3 + int(rng.expovariate(1 / 4.0))
    if rng.random() < 0.2:
        appr = rng.choice(["A", "A", "B"])
        d["net"][appr] = 1
    p_server = 0.56
    if rally >= 9:
        p_A = 0.72
        winner = "A" if rng.random() < p_A else "B"
    elif d["net"]["A"]:
        winner = "A" if rng.random() < 0.7 else "B"
    else:
        winner = S if rng.random() < p_server else R
    loser = other(winner)
    e = rng.random()
    ending = "winner" if e < 0.35 else ("unforced_error" if e < 0.75 else "forced_error")
    by = winner if ending == "winner" else loser
    # parity: odd shot numbers are hit by the server
    if (rally % 2 == 1) != (by == S):
        rally += 1
    if by == "B" and ending == "unforced_error":
        lw = "bh" if rng.random() < 0.8 else "fh"
    else:
        lw = rng.choice(["fh", "bh"])
    stroke = "volley" if d["net"][by] else "groundstroke"
    d.update(rally_len=rally, ending=ending, ending_by=by, last_shot_wing=lw, last_shot_stroke=stroke,
             last_shot_result=(None if ending == "winner" else rng.choice(["net", "out_long", "out_wide"])),
             winner=winner)
    return d


def _bounce(rng, side, end_of_server, result, zone, fault):
    sg = 1.0 if end_of_server == "near" else -1.0          # near server hits toward +y
    if result == "in":
        lo, hi = an_zone_band(zone)
        u, v = rng.uniform(lo + 0.05, hi - 0.05), rng.uniform(3.6, 6.3)
    else:
        if fault == "net":
            return None
        if fault == "long":
            u, v = rng.uniform(0.3, 3.9), rng.uniform(6.5, 7.6)
        elif fault == "wide":
            u, v = rng.uniform(4.2, 4.9), rng.uniform(3.0, 6.2)
        else:
            u, v = rng.uniform(4.2, 4.8), rng.uniform(6.5, 7.4)
    x = (-u * sg) if side == "deuce" else (u * sg)
    return [round(x, 2), round(v * sg, 2)]


def an_zone_band(zone):
    return rp.ZONE_BANDS[zone]


def build_sample_match(seed=7):
    rng = random.Random(seed)
    fmt = MatchFormat(best_of=3, no_ad=True, final_set="match_tb10")
    segments_spec = [
        (sc.initial_state(fmt, "A"), 40, False),
        (ScoreState(sets=[], games=[6, 6], server="A", in_tb=True, tb_first_server="A",
                    ends={"A": "near", "B": "far"}), 26, True),
    ]
    points = []
    t = 12.0
    pid = 0
    for init, n_pts, gap in segments_spec:
        if gap:
            t += 600.0
        state = sc._clone(init)
        sims, winners = [], []
        for _ in range(n_pts):
            if state.match_over:
                break
            fl = sc.flags(state, fmt)
            sim = _sim_point(rng, state.server, fl["break_point"] or fl["set_point"] or fl["match_point"])
            sims.append(sim)
            winners.append(sim["winner"])
            state = sc.apply(state, sim["winner"], fmt)
        durs = [4.0 + 1.3 * max(1, s["rally_len"] or 1) for s in sims]
        t_starts, t_ends = [], []
        for dur in durs:
            t_starts.append(round(t, 2))
            t_ends.append(round(t + dur, 2))
            t += dur + rng.uniform(18, 28)
        bounds = sc.replay(winners, fmt, init, t_ends=t_ends, t_starts=t_starts,
                           confs=[round(rng.uniform(0.82, 0.99), 2) for _ in winners], source="vision")
        for j, (b, sim) in enumerate(zip(bounds, sims)):
            S = b.server
            ends = b.score_before["ends"]
            conf = {"winner": b.conf}
            serves = []
            for s in sim["serves"]:
                zone = None
                if s["result"] == "in":
                    zw = sim["_zone_w"][(S, b.side)]
                    zone = rng.choices(["wide", "body", "T"], weights=zw)[0]
                else:
                    zone = rng.choices(["wide", "body", "T", None], weights=[3, 2, 3, 2])[0]
                bxy = _bounce(rng, b.side, ends[S], s["result"], zone or "body", s.get("fault"))
                sconf = round(rng.uniform(0.45, 0.97), 2)
                serves.append({"n": s["n"], "t": round(b.t_start + 0.5 + 6 * (s["n"] - 1), 2),
                               "result": s["result"], "zone": zone, "fault": s.get("fault"),
                               "bounce_xy": bxy, "speed_kmh": round(rng.uniform(150, 195) if s["n"] == 1
                                                                    else rng.uniform(120, 150), 0),
                               "conf": sconf})
            conf["serves"] = min([s["conf"] for s in serves]) if serves else None
            conf["serve_zone"] = round(rng.uniform(0.55, 0.95), 2)
            ret_wing = sim["return_wing"]
            rally = sim["rally_len"]
            ending = sim["ending"]
            last_w = sim["last_shot_wing"]
            # some unknowns
            if ret_wing and rng.random() < 0.12:
                ret_wing = None
            if rally and rally > 2 and rng.random() < 0.1:
                rally = None
            if ending in ("winner", "unforced_error", "forced_error") and rng.random() < 0.08:
                ending, last_w = "unknown", None
            if serves and rng.random() < 0.08:
                serves[-1]["zone"] = None
            conf["return_wing"] = round(rng.uniform(0.5, 0.9), 2) if ret_wing else None
            conf["rally_len"] = round(rng.uniform(0.45, 0.9), 2) if rally is not None else None
            conf["ending"] = round(rng.uniform(0.35, 0.8), 2) if ending and ending != "unknown" else None
            conf["last_shot_wing"] = round(rng.uniform(0.4, 0.85), 2) if last_w else None
            conf = {k: v for k, v in conf.items() if v is not None}
            dist = {"A": round(rng.uniform(6, 14) + (rally or 2) * 2.1, 1),
                    "B": round(rng.uniform(6, 14) + (rally or 2) * 2.3, 1)}
            rec = PointRecord(
                id=pid, t_start=b.t_start, t_end=b.t_end, set_no=len(b.score_before["sets"]) + 1,
                score_before=b.score_before, server=S, receiver=other(S), side=b.side, flags=b.flags,
                winner=b.winner, winner_source="vision", serves=serves,
                serve_number_won_on=sim["serve_number_won_on"], return_wing=ret_wing, rally_len=rally,
                ending=ending, ending_by=sim["ending_by"] if ending != "unknown" else None,
                last_shot_wing=last_w, last_shot_stroke=sim["last_shot_stroke"] if last_w else None,
                last_shot_result=sim["last_shot_result"], ends=dict(ends),
                net_approaches=sim["net"], player_dist_m=dist, gap_before=(gap and j == 0),
                conf=conf)
            d = asdict(rec)
            d["mcp"] = rp.mcp_code(d)
            points.append(d)
            pid += 1
    match = {
        "meta": {"video": "inbox/sample_match.mp4",
                 "video_info": {"path": "inbox/sample_match.mp4", "width": 1280, "height": 720,
                                "fps": 30.0, "duration": round(t + 30, 1)},
                 "players": {"A": {"name": "Maya Chen", "hand": "R"}, "B": {"name": "Lena Ortiz", "hand": "L"}},
                 "format": asdict(fmt), "pipeline_version": "synthetic-fixture-1",
                 "generated_at": "2026-10-29T12:00:00", "runtime_s": 0.0, "has_scorebug": False,
                 "title": "Synthetic sample (generated by tests/test_analytics.py)"},
        "segments": [{"start": 0.0, "end": round(t + 30, 1), "kind": "main", "conf": 1.0}],
        "points": points,
    }
    return an.refresh(match)


# ---------------------------------------------------------------------------
# hand-built points
# ---------------------------------------------------------------------------
def P(pid, server, winner, *, played_on=1, zone=None, side="deuce", ending=None, by=None,
      rally=None, ret=None, last_w=None, flags=None, conf=None, review=None, serves=None, net=None):
    if serves is None:
        serves = []
        if played_on == 1:
            serves = [{"n": 1, "result": "in", "zone": zone}]
        elif played_on == 2:
            serves = [{"n": 1, "result": "fault", "fault": "net"}, {"n": 2, "result": "in", "zone": zone}]
        elif ending == "double_fault":
            serves = [{"n": 1, "result": "fault", "fault": "long"}, {"n": 2, "result": "fault", "fault": "net"}]
    return {"id": pid, "t_start": pid * 30.0, "t_end": pid * 30.0 + 8, "set_no": 1,
            "score_before": {"sets": [], "games": [0, 0], "points": "0-0", "in_tb": False},
            "server": server, "receiver": other(server), "side": side,
            "flags": flags or {"break_point": False, "set_point": False, "match_point": False},
            "winner": winner, "winner_source": "ocr", "serves": serves,
            "serve_number_won_on": played_on if played_on in (1, 2) else None, "return_wing": ret,
            "rally_len": rally, "ending": ending, "ending_by": by, "last_shot_wing": last_w,
            "last_shot_stroke": "groundstroke" if last_w else None, "last_shot_result": None,
            "ends": None, "net_approaches": net or {}, "player_dist_m": {}, "gap_before": False,
            "conf": conf or {}, "mcp": "", "review": review or {"status": "auto", "edited_fields": []}}


class TestStats(unittest.TestCase):
    def setUp(self):
        bp = {"break_point": True, "set_point": False, "match_point": False}
        self.pts = [
            P(0, "A", "A", played_on=1, zone="T", ending="ace", rally=1),
            P(1, "A", "A", played_on=1, zone="wide", rally=5, ending="winner", by="A", ret="bh", last_w="fh"),
            P(2, "A", "B", played_on=2, zone="T", side="ad", rally=2, ending="winner", by="B", ret="fh", last_w="fh"),
            P(3, "A", "B", played_on=None, ending="double_fault", side="ad"),
            P(4, "A", "A", played_on=2, zone="body", rally=2, ending="unforced_error", by="B", ret="bh",
              last_w="bh", flags=bp),
            P(5, "A", "B", played_on=1, zone="T", rally=10, ending="unforced_error", by="A", ret="fh",
              last_w="bh", flags=bp),
            P(6, "B", "B", played_on=1, zone="wide", rally=3, ending="winner", by="B", ret="bh", last_w="fh"),
            P(7, "B", None, played_on=None, serves=[]),                        # all unknown
        ]

    def test_counts(self):
        st = an.compute_stats(self.pts, {"A": {"name": "Ann"}, "B": {"name": "Bea"}})
        A, B = st["players"]["A"], st["players"]["B"]
        self.assertEqual(A["serve_points"], 6)
        self.assertEqual(A["first_serve_in"], an.rate(3, 6))       # 0,1,5 in; 2,3,4 fault
        self.assertEqual(A["first_serve_won"]["k"], 2)
        self.assertEqual(A["first_serve_won"]["n"], 3)
        self.assertEqual((A["second_serve_won"]["k"], A["second_serve_won"]["n"]), (1, 3))  # 2 L,3 DF L,4 W
        self.assertEqual((A["aces"]["k"], A["aces"]["n"]), (1, 6))
        self.assertEqual((A["double_faults"]["k"], A["double_faults"]["n"]), (1, 6))
        self.assertEqual((A["serve_points_won"]["k"], A["serve_points_won"]["n"]), (3, 6))
        self.assertEqual((B["return_points_won"]["k"], B["return_points_won"]["n"]), (3, 6))
        self.assertEqual(st["players"]["A"]["points_won"]["n"], 7)   # point 7 has no winner
        # returns (by B): ace -> not in; p1 in (bh); p2 return winner -> in (fh); p4 return error bh;
        # p5 in (fh). DF has no return.
        self.assertEqual((B["return_in"]["k"], B["return_in"]["n"]), (3, 5))
        self.assertEqual(B["return_by_wing"]["bh"]["in"]["n"], 2)
        self.assertEqual(B["return_by_wing"]["bh"]["errors"], 1)
        self.assertEqual(B["return_by_wing"]["fh"]["in"]["k"], 2)
        # zones: A 1st serve deuce: T(0, won), wide(1, won), T(5, lost)
        z = A["serve_zones"]["1"]["deuce"]
        self.assertEqual(z["n"], 3)
        self.assertEqual(z["T"]["count"], 2)
        self.assertEqual((z["T"]["won"]["k"], z["T"]["won"]["n"]), (1, 2))
        self.assertEqual(A["serve_zones"]["2"]["ad"]["T"]["count"], 1)
        self.assertEqual(A["fault_types"]["1"]["net"], 2)
        self.assertEqual(A["fault_types"]["1"]["long"], 1)
        self.assertEqual(A["fault_types"]["2"]["net"], 1)
        # endings
        self.assertEqual(A["endings"]["winner"]["fh"], 1)
        self.assertEqual(A["endings"]["unforced_error"]["bh"], 1)
        self.assertEqual(B["endings"]["unforced_error"]["bh"], 1)
        self.assertEqual(B["endings"]["winner"]["total"], 2)
        # rally buckets: 1-4: p0(A),p2(B),p4(A),p6(B) -> A 2/4; 5-8: p1 A 1/1; 9+: p5 B -> A 0/1
        self.assertEqual((A["rally"]["1-4"]["k"], A["rally"]["1-4"]["n"]), (2, 4))
        self.assertEqual((A["rally"]["9+"]["k"], A["rally"]["9+"]["n"]), (0, 1))
        # BP: A faced 2 saved 1; B converted 1/2
        self.assertEqual((A["break_points"]["saved"]["k"], A["break_points"]["saved"]["n"]), (1, 2))
        self.assertEqual((B["break_points"]["converted"]["k"], B["break_points"]["converted"]["n"]), (1, 2))
        # coverage: winner known on 7/8
        self.assertEqual(st["coverage"]["winner"]["known"], 7)
        self.assertEqual(st["coverage"]["winner"]["total"], 8)

    def test_wilson_and_rate(self):
        r = an.rate(5, 8)
        self.assertEqual(r["pct"], 62.5)
        self.assertTrue(r["lo"] < 62.5 < r["hi"])
        self.assertEqual(an.rate(0, 0)["pct"], None)
        lo, hi = an.wilson(10, 10)
        self.assertAlmostEqual(hi, 100.0)
        self.assertTrue(65 < lo < 75)


def _return_points(miss_bh, n_bh, miss_fh, n_fh, conf=None, review=None):
    pts, i = [], 0
    for wing, miss, n in (("bh", miss_bh, n_bh), ("fh", miss_fh, n_fh)):
        for j in range(n):
            if j < miss:
                pts.append(P(i, "A", "A", played_on=1, zone="T", rally=2, ending="forced_error", by="B",
                             ret=wing, last_w=wing, conf=conf, review=review))
            else:
                pts.append(P(i, "A", "B", played_on=1, zone="T", rally=4, ending="winner", by="B",
                             ret=wing, last_w="fh", conf=conf, review=review))
            i += 1
    return pts


class TestInsights(unittest.TestCase):
    players = {"A": {"name": "Sinner", "hand": "R"}, "B": {"name": "Alcaraz", "hand": "R"}}

    def _ins(self, pts):
        st = an.compute_stats(pts, self.players)
        return an.insights(st, pts, self.players)

    def test_backhand_return_claim(self):
        ins = self._ins(_return_points(5, 8, 1, 9))
        r = [i for i in ins if i["key"] == "ret_wing_bh"]
        self.assertEqual(len(r), 1)
        r = r[0]
        self.assertEqual(r["player"], "B")
        self.assertIn("5 of 8 backhand returns missed (62%)", r["text"])
        self.assertIn("1 of 9", r["text"])
        self.assertTrue(r["small_sample"])
        self.assertEqual(r["confidence"], "low")
        self.assertEqual(len(r["evidence_point_ids"]), 8)
        self.assertAlmostEqual(r["effect"], 62.5 - 11.1, places=1)
        recs = an.recommendations(None, ins, self.players)
        self.assertTrue(any("Serve to Alcaraz's backhand: down the T on the deuce side" in x["text"]
                            for x in recs if x["for"] == "A"))

    def test_min_sample(self):
        ins = self._ins(_return_points(4, 4, 0, 9))     # bh n=4 < 5
        self.assertFalse([i for i in ins if i["key"].startswith("ret_wing")])

    def test_effect_threshold(self):
        ins = self._ins(_return_points(3, 10, 2, 10))   # 30% vs 20%: 10pp < 15
        self.assertFalse([i for i in ins if i["key"].startswith("ret_wing")])

    def test_low_conf_flag_and_review_override(self):
        low = {"return_wing": 0.3, "rally_len": 0.4, "ending": 0.3}
        ins = self._ins(_return_points(8, 12, 1, 12, conf=low))
        r = [i for i in ins if i["key"] == "ret_wing_bh"][0]
        self.assertTrue(r["low_confidence"])
        self.assertIn("unverified", r["text"])
        self.assertFalse(r["small_sample"])
        ins = self._ins(_return_points(8, 12, 1, 12, conf=low,
                                       review={"status": "reviewed", "edited_fields": []}))
        r = [i for i in ins if i["key"] == "ret_wing_bh"][0]
        self.assertFalse(r["low_confidence"])
        self.assertEqual(r["confidence"], "high")

    def test_zone_tendency(self):
        pts = [P(i, "A", "A", played_on=1, zone="T" if i < 7 else "wide", side="deuce", rally=3,
                 ending="winner", by="A", last_w="fh") for i in range(10)]
        ins = self._ins(pts)
        r = [i for i in ins if i["key"] == "zone_pref_1_deuce"]
        self.assertEqual(len(r), 1)
        self.assertIn("Sinner served 70% of 1st serves down the T on the deuce side (7 of 10)", r[0]["text"])

    def test_pressure_first_serve(self):
        bp = {"break_point": True, "set_point": False, "match_point": False}
        pts = []
        for i in range(10):     # normal: 7/10 in
            pts.append(P(i, "A", "A", played_on=1 if i < 7 else 2, zone="T", rally=3, ending="winner", by="A"))
        for i in range(10, 15):  # pressure: 1/5 in
            pts.append(P(i, "A", "B", played_on=1 if i == 10 else 2, zone="T", rally=4, ending="winner",
                         by="B", flags=bp))
        ins = self._ins(pts)
        r = [i for i in ins if i["key"] == "pressure_first_in" and i["player"] == "A"]
        self.assertEqual(len(r), 1)
        self.assertIn("fell from 70% to 20%", r[0]["text"])
        self.assertEqual(r[0]["effect"], -50.0)


class TestFixtureAndCorrections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE) as f:
            cls.match = json.load(f)

    def test_fixture_scores_consistent(self):
        m = copy.deepcopy(self.match)
        pts = m["points"]
        self.assertGreaterEqual(len(pts), 55)
        before = [(p["score_before"], p["server"], p["side"], p["flags"]) for p in pts]
        an.rescore(pts, an._fmt_from_meta(m["meta"]))
        after = [(p["score_before"], p["server"], p["side"], p["flags"]) for p in pts]
        self.assertEqual(before, after)
        self.assertTrue(any(p["score_before"]["in_tb"] for p in pts))
        self.assertTrue(any(p["gap_before"] for p in pts))
        self.assertTrue(any(p["flags"]["break_point"] for p in pts))
        self.assertTrue(any(p["flags"]["set_point"] for p in pts))
        self.assertTrue(any(p["set_no"] == 2 for p in pts))

    def test_fixture_has_insights(self):
        self.assertTrue(self.match["insights"])
        self.assertTrue(self.match["recommendations"])
        for i in self.match["insights"]:
            self.assertGreaterEqual(i["n"], 5)
            self.assertGreaterEqual(abs(i["effect"]), 15)

    def test_winner_correction_rescores(self):
        m = self.match
        pts = m["points"]
        # first point of the match: flip the winner
        p0 = pts[0]
        new_w = other(p0["winner"])
        out = an.apply_corrections(m, {str(p0["id"]): {"winner": new_w, "reviewed": True}})
        q0, q1 = out["points"][0], out["points"][1]
        self.assertEqual(q0["winner"], new_w)
        self.assertEqual(q0["winner_source"], "manual")
        self.assertEqual(q0["conf"]["winner"], 1.0)
        self.assertEqual(q0["review"]["status"], "reviewed")
        self.assertIn("winner", q0["review"]["edited_fields"])
        self.assertEqual(q1["score_before"]["points"], "15-0" if new_w == "A" else "0-15")
        # original untouched
        self.assertEqual(m["points"][0]["winner"], p0["winner"])
        # still consistent with a full replay
        chk = copy.deepcopy(out["points"])
        an.rescore(chk, an._fmt_from_meta(out["meta"]))
        self.assertEqual([p["score_before"] for p in chk], [p["score_before"] for p in out["points"]])
        # stats changed accordingly
        pa = lambda mm: mm["stats"]["players"]["A"]["points_won"]["k"]  # noqa: E731
        self.assertEqual(pa(out) - pa(m), 1 if new_w == "A" else -1)

    def test_field_corrections_and_derive(self):
        m = self.match
        pid = next(p["id"] for p in m["points"] if p["serve_number_won_on"] == 1 and p["ending"] != "ace")
        out = an.apply_corrections(m, {pid: {"serve_zone": "wide", "return_wing": "bh", "ending": "ace"}})
        q = next(p for p in out["points"] if p["id"] == pid)
        self.assertEqual(q["serves"][0]["zone"], "wide")
        self.assertEqual(q["ending"], "ace")
        self.assertEqual(q["ending_by"], q["server"])
        self.assertEqual(q["rally_len"], 1)
        self.assertEqual(q["mcp"], "4*")
        self.assertEqual(q["review"]["status"], "edited")
        self.assertEqual(sorted(q["review"]["edited_fields"]), ["ending", "return_wing", "serve_zone"])

    def test_ocr_winner_correction_keeps_scores(self):
        m = copy.deepcopy(self.match)
        m["meta"]["has_scorebug"] = True
        for p in m["points"]:
            p["winner_source"] = "ocr"
        p0 = m["points"][0]
        out = an.apply_corrections(m, {p0["id"]: {"winner": other(p0["winner"])}})
        self.assertEqual(out["points"][1]["score_before"], m["points"][1]["score_before"])


class _Checker(HTMLParser):
    VOID = {"meta", "link", "br", "img", "input", "hr", "source", "track", "wbr", "area", "base",
            "col", "embed", "param", "path", "rect", "circle", "line"}

    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.counts = [], [], {}

    def handle_starttag(self, tag, attrs):
        self.counts[tag] = self.counts.get(tag, 0) + 1
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.counts[tag] = self.counts.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> (open: {self.stack[-3:]})")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


class TestReport(unittest.TestCase):
    def test_mcp_codes(self):
        self.assertEqual(rp.mcp_code(P(0, "A", "A", played_on=1, zone="T", ending="ace", rally=1)), "6*")
        self.assertEqual(rp.mcp_code(P(0, "A", "B", played_on=None, ending="double_fault")), "0d 0n")
        p = P(0, "A", "B", played_on=2, zone="wide", rally=4, ending="winner", by="B", ret="bh", last_w="fh")
        self.assertEqual(rp.mcp_code(p), "0n 4bqf*")
        p = P(0, "A", "A", played_on=1, zone="body", rally=2, ending="forced_error", by="B", ret="bh",
              last_w="bh")
        p["last_shot_result"] = "net"
        self.assertEqual(rp.mcp_code(p), "5bn#")
        p = P(0, "A", "A", played_on=1, zone="T", rally=None, ending=None)
        self.assertEqual(rp.mcp_code(p), "6?")

    def test_render_outputs(self):
        with open(FIXTURE) as f:
            match = json.load(f)
        with tempfile.TemporaryDirectory() as d:
            paths = rp.render(match, d)
            for k in ("html", "csv", "json"):
                self.assertTrue(os.path.getsize(paths[k]) > 100, k)
            with open(paths["html"], encoding="utf-8") as f:
                html = f.read()
            ch = _Checker()
            ch.feed(html)
            ch.close()
            self.assertEqual(ch.errors, [])
            self.assertEqual(ch.stack, [])
            self.assertNotIn("http://", html.replace("http://www.w3.org/2000/svg", ""))
            self.assertNotIn("https://", html)
            self.assertIn("Maya Chen", html)
            self.assertEqual(html.count('class="ts"'), len(match["points"]))
            self.assertGreaterEqual(ch.counts.get("svg", 0), 4 + 1)   # 4 serve courts + point flow
            with open(paths["csv"], newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), len(match["points"]))
            self.assertEqual(list(rows[0].keys()), rp.CSV_COLS)
            with open(paths["json"]) as f:
                mj = json.load(f)
            self.assertEqual(set(mj.keys()) >= {"meta", "segments", "points", "stats", "insights",
                                                "recommendations"}, True)


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        m = build_sample_match()
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        with open(FIXTURE, "w") as f:
            json.dump(m, f, indent=1)
        print(f"wrote {FIXTURE}: {len(m['points'])} points, {len(m['insights'])} insights")
    else:
        unittest.main()
