"""Fixes from the review of Nov 7, 2026: a test for each.

- serve tendencies are judged against the next most common spot, not an even split
- small samples are listed after the other notes
- each note, plan line and statistics row says whether its values were checked in a review
- points the score board skipped are not called missing, and their video time is the serve
- the chat's side rule knows that no-ad matches have no advantage scores, and the prompt
  does not call body serves "aimed at the receiver"
- the review server refuses requests from other sites

Run:  .venv/bin/python -m unittest tests.test_review_fixes -v
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from tennis import analytics as an  # noqa: E402
from tennis import chat  # noqa: E402
from tennis import report as rp  # noqa: E402
from test_analytics import P  # noqa: E402

PLAYERS = {"A": {"name": "Ann", "hand": "R"}, "B": {"name": "Bea", "hand": "R"}}


def _serves(spots, server="A", side="deuce", start=0, review=None):
    return [P(start + i, server, server, played_on=1, zone=z, side=side, rally=3, ending="winner",
              by=server, last_w="fh", review=review) for i, z in enumerate(spots)]


def _ins(pts):
    return an.insights(an.compute_stats(pts, PLAYERS), pts, PLAYERS)


class TestServeTendency(unittest.TestCase):
    def test_near_even_split_is_not_a_tendency(self):
        """19 T, 17 wide, 3 body of 39 (the review's example): 49% against 44% is a gap of
        5 points, so no note. Against an even split (33%) it used to be one."""
        ins = _ins(_serves(["T"] * 19 + ["wide"] * 17 + ["body"] * 3))
        self.assertFalse([i for i in ins if i["key"] == "zone_pref_1_deuce"])

    def test_clear_tendency_names_the_next_spot(self):
        ins = _ins(_serves(["T"] * 20 + ["wide"] * 8 + ["body"] * 2))
        r = [i for i in ins if i["key"] == "zone_pref_1_deuce"]
        self.assertEqual(len(r), 1)
        self.assertIn("Ann served 67% of 1st serves down the T on the deuce side (20 of 30); "
                      "the next most common spot was wide, 8 of 30 (27%).", r[0]["text"])
        self.assertAlmostEqual(r[0]["effect"], 40.0, places=1)
        self.assertAlmostEqual(r[0]["comparison_pct"], 26.7, places=1)

    def test_small_samples_come_last(self):
        # a 6-serve note with a big gap and a 30-serve note with a smaller one
        pts = _serves(["T"] * 6, side="ad") + _serves(["T"] * 20 + ["wide"] * 8 + ["body"] * 2, start=10)
        ins = [i for i in _ins(pts) if i["key"].startswith("zone_pref")]
        self.assertEqual([i["key"] for i in ins], ["zone_pref_1_deuce", "zone_pref_1_ad"])
        self.assertTrue(ins[1]["small_sample"])
        self.assertGreater(ins[1]["score"], ins[0]["score"])   # ranked higher by score, yet listed last


class TestCheckedMarks(unittest.TestCase):
    def test_note_checked_or_not(self):
        spots = ["T"] * 20 + ["wide"] * 8 + ["body"] * 2
        auto = [i for i in _ins(_serves(spots)) if i["key"] == "zone_pref_1_deuce"][0]
        self.assertEqual(auto["checked"], {"k": 0, "n": 20, "status": "none"})
        rv = {"status": "auto", "edited_fields": [], "checked_fields": ["serve1_result", "serve1_zone"]}
        ck = [i for i in _ins(_serves(spots, review=rv)) if i["key"] == "zone_pref_1_deuce"][0]
        self.assertEqual(ck["checked"]["status"], "all")
        mixed = _serves(spots)
        for p in mixed[:5]:
            p["review"] = dict(rv)
        mx = [i for i in _ins(mixed) if i["key"] == "zone_pref_1_deuce"][0]
        self.assertEqual(mx["checked"], {"k": 5, "n": 20, "status": "some"})

    def test_field_checked_rules(self):
        p = P(0, "A", "A", played_on=2, zone="T", ending="winner", by="A", last_w="fh")
        self.assertFalse(an.field_checked(p, "serves"))
        p["review"] = {"status": "auto", "edited_fields": [], "checked_fields": ["serve1_result", "serve2_result",
                                                                               "serve2_zone", "winner"]}
        self.assertTrue(an.field_checked(p, "serves"))
        self.assertTrue(an.field_checked(p, "serve_zone"))          # the serve the point was played on
        self.assertTrue(an.field_checked(p, "winner"))
        self.assertFalse(an.field_checked(p, "last_shot_wing"))
        p["review"] = {"status": "reviewed", "edited_fields": []}
        self.assertTrue(an.field_checked(p, "last_shot_wing"))

    def test_statistics_rows_say_what_was_checked(self):
        pts = _serves(["T", "wide", "body", "T"])
        for p in pts[:2]:
            p["review"] = {"status": "auto", "edited_fields": [], "checked_fields": ["winner"]}
        st = an.compute_stats(pts, PLAYERS)
        rows = {r["label"]: r for r in rp.key_stat_rows(st, pts)}
        self.assertEqual(rows["Points won"]["checked"], "partly, 2 of 4 points")
        self.assertEqual(rows["Winners"]["checked"], "no, computer only")
        self.assertEqual(rows["Distance run per point"]["checked"], "no, computer only")
        for p in pts:
            p["review"] = {"status": "reviewed", "edited_fields": []}
        rows = {r["label"]: r for r in rp.key_stat_rows(st, pts)}
        self.assertEqual(rows["Winners"]["checked"], "yes, all winners")
        self.assertNotIn("checked", rp.key_stat_rows(st)[0])          # no points given: no column value

    def test_ending_rows_count_only_their_own_ending(self):
        """Match 1's review checked the ending of 9 points (7 double faults, 2 unknown)
        and of no ace or winner, so those rows must not say "partly checked"."""
        pts = _serves(["T", "wide", "body", "T"])
        pts[0]["ending"] = "double_fault"
        pts[0]["review"] = {"status": "auto", "edited_fields": [], "checked_fields": ["ending"]}
        pts[1]["ending"] = pts[2]["ending"] = "ace"
        pts[2]["review"] = {"status": "auto", "edited_fields": [], "checked_fields": ["ending"]}
        st = an.compute_stats(pts, PLAYERS)
        rows = {r["label"]: r for r in rp.key_stat_rows(st, pts)}
        self.assertEqual(rows["Winners"]["checked"], "no, computer only")
        self.assertEqual(rows["Aces (per point served)"]["checked"], "partly, 1 of 2 aces")
        self.assertEqual(rows["Unforced errors"]["checked"], "no, computer only")

    def test_report_marks_unchecked_notes_and_plain_sources(self):
        spots = ["T"] * 20 + ["wide"] * 8 + ["body"] * 2
        pts = _serves(spots)
        pts[0]["winner_source"] = "manual"
        m = {"meta": {"players": PLAYERS, "has_scorebug": True}, "points": pts, "segments": []}
        with tempfile.TemporaryDirectory() as d:
            rp.render(m, d)
            with open(os.path.join(d, "report.html"), encoding="utf-8") as f:
                html = f.read()
        self.assertIn("computer only, not checked", html)
        self.assertIn("No, computer only".lower(), html.lower())
        self.assertIn("read from the score board: 29", html)
        self.assertIn("set in a review: 1", html)
        self.assertNotIn("manual 1", html)
        self.assertNotIn("score bug", html)
        self.assertNotIn("serve +1", html)
        self.assertNotIn("–", html)          # no en dashes


def _fill_match():
    """Point 1 and 2 were skipped by the score board (filled by the engine: gap_before on
    the point itself, no window of its own); point 4 follows a real jump."""
    pts = [P(i, "A", "A", played_on=1, zone="T", rally=3, ending="winner", by="A") for i in range(5)]
    for i in (1, 2):
        pts[i]["gap_before"] = True
        pts[i]["t_start"] = None
        pts[i]["t_end"] = 100.0
        pts[i]["winner_source"] = "engine"
    pts[1]["serves"][0]["t"] = 41.5
    pts[2]["serves"] = []
    pts[4]["gap_before"] = True
    return {"meta": {"players": PLAYERS, "has_scorebug": True}, "points": pts, "segments": []}


class TestSkippedPoints(unittest.TestCase):
    def test_filled_points_are_not_gaps(self):
        m = _fill_match()
        self.assertTrue(an.score_filled(m["points"][1]))
        self.assertFalse(an.score_filled(m["points"][4]))
        st = an.compute_stats(m["points"], PLAYERS)
        self.assertEqual((st["n_gaps"], st["n_filled"]), (1, 2))

    def test_point_rows_and_chart(self):
        m = _fill_match()
        rows = rp.point_rows(m, "v.mp4")
        self.assertEqual([r["gap"] for r in rows], [False, False, False, False, True])
        self.assertEqual([r["fill_run"] for r in rows], [0, 2, 0, 0, 0])
        # video time: the serve when known, else the window start, else the score change
        self.assertEqual(rows[1]["t_label"], "0:41")
        self.assertAlmostEqual(rows[1]["t"], 38.5)
        self.assertEqual(rows[2]["t_label"], "1:40")
        self.assertEqual(rows[0]["t_label"], "0:00")
        svg = rp.svg_momentum(m)
        self.assertEqual(svg.count('class="gapline"'), 1)
        self.assertIn("points may be missing before #4", svg)

    def test_report_text(self):
        m = _fill_match()
        with tempfile.TemporaryDirectory() as d:
            rp.render(m, d)
            with open(os.path.join(d, "report.html"), encoding="utf-8") as f:
                html = f.read()
        self.assertIn("the score board skipped the next 2 points; they are in the video", html)
        self.assertEqual(html.count("points may be missing here"), 1)
        self.assertIn("with the winner worked out from the score: 2", html)
        self.assertNotIn("points missing from the video", html)

    def test_chat_uses_the_same_time(self):
        p = _fill_match()["points"][1]
        self.assertEqual(an.point_time(p), 41.5)
        p["lets"] = [{"t": 39.0, "n": 1}]
        self.assertEqual(an.point_time(p), 39.0)


class TestChatWording(unittest.TestCase):
    def test_no_ad_has_no_advantage(self):
        for sit, score in (("adv_server", "any"), ("adv_receiver", "any"), ("all", "AD-40"), ("all", "40-AD")):
            r = chat.side_rule(sit, score, no_ad=True)
            self.assertEqual(r["possible_sides"], [], (sit, score))
            self.assertIn("no-ad", r["note"])
            self.assertIn("never happens", r["note"])
        self.assertEqual(chat.side_rule("adv_server", "any", no_ad=False)["possible_sides"], ["ad"])
        self.assertEqual(chat.side_rule("deuce", "any", no_ad=True)["possible_sides"], ["deuce", "ad"])

    def test_body_is_where_it_landed(self):
        self.assertNotIn("at the receiver", chat.DEFINITIONS["body"])
        self.assertIn("middle third", chat.DEFINITIONS["body"])
        self.assertNotIn("middle third of the box, at the receiver", chat.SYSTEM_PROMPT)
        self.assertIn("Never say a serve was aimed", chat.SYSTEM_PROMPT)


class TestReviewServerGuards(unittest.TestCase):
    def setUp(self):
        from tennis import review_server as rs
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        m = an.refresh({"meta": {"players": PLAYERS}, "points": _serves(["T"]), "segments": []})
        with open(os.path.join(d, "match.json"), "w") as f:
            json.dump(m, f)
        self.app = rs.ReviewApp(d)
        self.saved = []
        self.app.save = lambda c: (self.saved.append(c), {"ok": True})[1]
        self.httpd = rs.ThreadingHTTPServer(("127.0.0.1", 0), rs.make_handler(self.app))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def _req(self, method, path, headers, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest(method, path, skip_host=True)
        for k, v in headers.items():
            c.putheader(k, v)
        data = body.encode() if body is not None else None
        if data is not None:
            c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def test_get_needs_a_local_host_name(self):
        here = "127.0.0.1:%d" % self.port
        self.assertEqual(self._req("GET", "/match.json", {"Host": here}), 200)
        self.assertEqual(self._req("GET", "/match.json", {"Host": "localhost:%d" % self.port}), 200)
        self.assertEqual(self._req("GET", "/match.json", {"Host": "evil.example:%d" % self.port}), 403)

    def test_save_from_another_site_is_refused(self):
        here = "127.0.0.1:%d" % self.port
        body = json.dumps({"corrections": {}})
        self.assertEqual(self._req("POST", "/save", {"Host": "evil.example", "Content-Type": "application/json"},
                                   body), 403)
        self.assertEqual(self._req("POST", "/save", {"Host": here, "Content-Type": "text/plain",
                                                     "Origin": "http://evil.example"}, body), 415)
        self.assertEqual(self._req("POST", "/save", {"Host": here, "Content-Type": "application/json",
                                                     "Origin": "http://evil.example"}, body), 403)
        self.assertEqual(self._req("POST", "/save", {"Host": here, "Content-Type": "application/json",
                                                     "Origin": "http://127.0.0.1:1"}, body), 403)
        self.assertEqual(self.saved, [])
        # the review page itself: same host and port, JSON
        self.assertEqual(self._req("POST", "/save", {"Host": here, "Content-Type": "application/json",
                                                     "Origin": "http://" + here}, body), 200)
        self.assertEqual(self.saved, [{}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
