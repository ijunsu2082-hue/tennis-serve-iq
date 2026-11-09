"""Tests for the review keys of analytics.apply_corrections: confirmed fields
("checked"), more serve keys, removing a serve, adding a missed point and
"_meta". Run:  .venv/bin/python -m unittest discover -s tests
"""
import copy
import csv
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tennis import analytics as an  # noqa: E402
from tennis import report as rp  # noqa: E402
from tennis.contracts import other  # noqa: E402


def _pt(pid, server, winner, points, serves, *, t_end=None, sn=None, ending=None, games=(0, 0)):
    t_end = t_end if t_end is not None else 100.0 + 30.0 * pid
    return {"id": pid, "t_start": t_end - 20.0, "t_end": t_end, "set_no": 1,
            "score_before": {"sets": [], "games": list(games), "points": points, "in_tb": False,
                             "ends": {"A": "near", "B": "far"}},
            "server": server, "receiver": other(server), "side": "deuce",
            "flags": {"break_point": False, "game_point": False, "set_point": False, "match_point": False},
            "winner": winner, "winner_source": "ocr", "serves": serves, "serve_number_won_on": sn,
            "return_wing": None, "rally_len": 3, "ending": ending, "ending_by": None,
            "last_shot_wing": None, "last_shot_stroke": None, "last_shot_result": None,
            "ends": {"A": "near", "B": "far"}, "net_approaches": {}, "player_dist_m": {},
            "gap_before": False, "conf": {"winner": 0.9}, "mcp": "",
            "review": {"status": "auto", "edited_fields": []}}


def _sv(n, t, result, zone=None, fault=None, xy=None, speed=None, conf=0.6):
    return {"n": n, "t": t, "result": result, "zone": zone, "fault": fault, "bounce_xy": xy,
            "speed_kmh": speed, "conf": conf}


def _match():
    pts = [
        _pt(0, "A", "A", "0-0", [_sv(1, 110.0, "in", "T", xy=[0.5, 5.0], speed=180.0)], sn=1),
        _pt(1, "A", "B", "15-0", [_sv(1, 140.0, "fault", fault="net"), _sv(2, 150.0, "in", "wide")], sn=2),
        _pt(2, "A", "A", "15-15", []),
    ]
    return {"meta": {"players": {"A": {"name": "Ann"}, "B": {"name": "Bea"}}, "has_scorebug": True,
                     "format": {"best_of": 3}},
            "segments": [], "points": pts}


class TestReviewMarks(unittest.TestCase):
    def test_checked_marks_without_editing(self):
        m = _match()
        out = an.apply_corrections(m, {"0": {"checked": ["winner", "serve1_result", "serve1_zone", "winner"]}})
        q = out["points"][0]
        self.assertEqual(q["review"]["checked_fields"], ["winner", "serve1_result", "serve1_zone"])
        self.assertEqual(q["review"]["edited_fields"], [])
        self.assertEqual(q["review"]["status"], "auto")
        self.assertEqual(q["conf"]["winner"], 1.0)
        self.assertEqual(q["conf"]["serves"], 1.0)
        self.assertEqual(q["conf"]["serve_zone"], 1.0)
        self.assertEqual(q["serves"], m["points"][0]["serves"])
        self.assertEqual(out["stats"]["n_checked"], 1)
        self.assertEqual(out["stats"]["n_edited"], 0)

    def test_more_serve_keys_and_conf(self):
        m = _match()
        out = an.apply_corrections(m, {"0": {"serve1_zone": "wide", "serve1_bounce_xy": None,
                                             "serve1_speed_kmh": None, "serve1_t": 109.5}})
        s = out["points"][0]["serves"][0]
        self.assertEqual((s["zone"], s["bounce_xy"], s["speed_kmh"], s["t"]), ("wide", None, None, 109.5))
        q = out["points"][0]
        self.assertEqual(q["conf"].get("serve_zone"), 1.0)
        self.assertNotIn("serves", q["conf"])        # time and position say nothing about in or fault
        self.assertEqual(sorted(q["review"]["edited_fields"]),
                         ["serve1_bounce_xy", "serve1_speed_kmh", "serve1_t", "serve1_zone"])

    def test_remove_a_serve(self):
        m = _match()
        out = an.apply_corrections(m, {"1": {"serve_number_won_on": 1, "serve1_result": "in",
                                             "serve1_fault": None, "serve1_zone": "T", "serve2": None}})
        q = out["points"][1]
        self.assertEqual([s["n"] for s in q["serves"]], [1])
        self.assertEqual((q["serves"][0]["result"], q["serves"][0]["zone"]), ("in", "T"))
        self.assertIn("serve2", q["review"]["edited_fields"])
        a = an.point_attrs(q)
        self.assertEqual((a["first_in"], a["played_on"], a["zone"]), (True, 1, "T"))
        # a value other than null does not remove the serve
        out2 = an.apply_corrections(m, {"1": {"serve2": {"n": 2}}})
        self.assertEqual([s["n"] for s in out2["points"][1]["serves"]], [1, 2])

    def test_add_missed_point(self):
        m = _match()
        sb = {"sets": [], "games": [0, 0], "points": "40-30", "in_tb": False}
        corr = {"7": {"add": True, "t_start": 150.0, "t_end": 155.0, "score_before": sb, "server": "A",
                      "ends": {"A": "near", "B": "far"}, "winner": "A", "serve_number_won_on": 1,
                      "serve1_result": "in", "serve1_zone": "body", "serve1_t": 151.0,
                      "checked": ["winner", "serve1_result", "serve1_zone"], "note": "added"}}
        out = an.apply_corrections(m, corr)
        ids = [p["id"] for p in out["points"]]
        self.assertEqual(ids, [0, 1, 7, 2])                      # placed by time, other ids kept
        q = out["points"][2]
        self.assertEqual(q["review"]["status"], "added")
        self.assertEqual((q["server"], q["receiver"], q["side"], q["set_no"]), ("A", "B", "ad", 1))
        self.assertTrue(q["flags"]["game_point"])
        self.assertFalse(q["flags"]["break_point"])
        self.assertEqual(q["score_before"]["points"], "40-30")
        self.assertEqual(q["ends"], {"A": "near", "B": "far"})
        self.assertEqual((q["winner"], q["winner_source"]), ("A", "manual"))
        self.assertEqual([(s["n"], s["t"], s["result"], s["zone"]) for s in q["serves"]], [(1, 151.0, "in", "body")])
        self.assertIn("score_before", q["review"]["edited_fields"])
        self.assertEqual(q["review"]["note"], "added")
        self.assertEqual(out["stats"]["n_added"], 1)
        self.assertEqual(out["stats"]["n_points"], 4)
        # the score board drives the scores, so the other points keep theirs
        self.assertEqual([p["score_before"] for p in out["points"] if p["id"] != 7],
                         [p["score_before"] for p in m["points"]])
        # input untouched
        self.assertEqual(len(m["points"]), 3)

    def test_add_never_edits_a_found_point_and_needs_its_keys(self):
        m = _match()
        out = an.apply_corrections(m, {"1": {"add": True, "t_end": 1.0, "server": "B", "winner": "A",
                                             "score_before": {"sets": [], "games": [0, 0], "points": "0-0"}},
                                       "9": {"add": True, "t_end": 5.0, "server": "A"},
                                       "10": {"add": True, "t_end": 5.0, "server": "C",
                                              "score_before": {"points": "0-0"}}})
        self.assertEqual(out["points"][1]["winner"], "B")
        self.assertEqual([p["id"] for p in out["points"]], [0, 1, 2])

    def test_added_point_without_ends_gets_none(self):
        m = _match()
        out = an.apply_corrections(m, {"5": {"add": True, "t_end": 999.0, "server": "B",
                                             "score_before": {"sets": [], "games": [1, 0], "points": "0-0"}}})
        q = out["points"][-1]
        self.assertIsNone(q["ends"])
        self.assertNotIn("ends", q["score_before"])

    def test_meta_and_csv_columns(self):
        m = _match()
        corr = {"_meta": {"source": "a check"}, "2": {"winner": "B", "checked": ["winner"]}}
        out = an.apply_corrections(m, corr)
        self.assertEqual(out["meta"]["review"], {"source": "a check"})
        with tempfile.TemporaryDirectory() as d:
            paths = rp.render(out, d)
            with open(paths["csv"], newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(list(rows[0].keys()), rp.CSV_COLS)
            self.assertEqual(rows[2]["edited_fields"], "winner")
            self.assertEqual(rows[2]["checked_fields"], "winner")
            self.assertEqual(rows[0]["checked_fields"], "")
            with open(paths["html"], encoding="utf-8") as f:
                html = f.read()
            self.assertIn("Where the review values come from: a check", html)
            self.assertIn("with some values checked in a review: 1", html)

    def test_points_key_guards_against_other_points(self):
        m = _match()
        key = an.points_key(m["points"])
        corr = {"_meta": {"source": "x", "points_key": key}, "0": {"winner": "B"}}
        out = an.apply_corrections(m, corr)
        self.assertEqual(out["points"][0]["winner"], "B")
        self.assertNotIn("corrections_stale", out["meta"])
        m2 = copy.deepcopy(m)
        m2["points"][1]["t_end"] += 7.0                     # analysed again: points moved
        out2 = an.apply_corrections(m2, corr)
        self.assertEqual(out2["points"][0]["winner"], "A")
        self.assertIn("corrections_stale", out2["meta"])
        self.assertNotIn("review", out2["meta"])
        self.assertNotIn("corrections_applied", out2["meta"])

    def test_old_corrections_unchanged(self):
        m = _match()
        corr = {"1": {"serve1_zone": "body", "winner": "A", "reviewed": True}}
        out = an.apply_corrections(copy.deepcopy(m), corr)
        q = out["points"][1]
        self.assertEqual(q["review"]["status"], "reviewed")
        self.assertEqual(sorted(q["review"]["edited_fields"]), ["serve1_zone", "winner"])
        self.assertNotIn("checked_fields", q["review"])
        self.assertEqual(q["conf"]["serve_zone"], 1.0)

    def test_review_ui_keeps_the_automatic_base_after_a_rebuild(self):
        import json
        from tennis.review_server import ReviewApp
        m = an.refresh(_match())
        corr = {"0": {"serve1_zone": "wide"}, "7": {"add": True, "t_end": 155.0, "server": "A", "winner": "A",
                "score_before": {"sets": [], "games": [0, 0], "points": "40-30", "in_tb": False}}}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "match_auto.json"), "w") as f:
                json.dump(m, f)
            with open(os.path.join(d, "corrections.json"), "w") as f:
                json.dump(corr, f)
            rp.render(an.apply_corrections(m, corr), d)          # what "tennis rebuild" does
            with open(os.path.join(d, "match.json")) as f:
                self.assertEqual(json.load(f)["meta"]["corrections_applied"], 2)
            ReviewApp(d)
            with open(os.path.join(d, "match_auto.json")) as f:
                base = json.load(f)
            self.assertEqual(len(base["points"]), 3)                # base not replaced by the corrected file
            self.assertEqual(base["points"][0]["serves"][0]["zone"], "T")
            with open(os.path.join(d, "match.json")) as f:
                cur = json.load(f)
            self.assertEqual([p["id"] for p in cur["points"]], [0, 1, 7, 2])   # point added once

    def test_rebuild_log_counts_points_and_warns_when_stale(self):
        import contextlib
        import io
        import json
        from tennis import pipeline
        m = an.refresh(_match())
        key = an.points_key(m["points"])
        corr = {"_meta": {"source": "x", "points_key": key}, "0": {"serve1_zone": "wide"},
                "1": {"note": "only a note"},
                "7": {"add": True, "t_end": 155.0, "server": "A", "winner": "A",
                      "score_before": {"sets": [], "games": [0, 0], "points": "40-30", "in_tb": False}}}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "match_auto.json"), "w") as f:
                json.dump(m, f)
            with open(os.path.join(d, "corrections.json"), "w") as f:
                json.dump(corr, f)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pipeline.rebuild(d)
            log = buf.getvalue()
            self.assertIn("rebuilt with corrections on 3 points (1 added)", log)   # "_meta" is not a point
            self.assertNotIn("WARNING", log)
            corr["_meta"]["points_key"] = "made for other points"
            with open(os.path.join(d, "corrections.json"), "w") as f:
                json.dump(corr, f)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pipeline.rebuild(d)
            log = buf.getvalue()
            self.assertIn("WARNING: corrections.json was made for a different set of points", log)
            self.assertIn("rebuilt with corrections on 0 points", log)


if __name__ == "__main__":
    unittest.main()
