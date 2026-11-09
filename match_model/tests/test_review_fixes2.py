"""Fixes from the second review of the run (no video is read except a tiny drawn clip).

- match.json names the video without this computer's folders; the full path stays in cache/
- the ball and shot rules give the same result on a 1080p or 4K copy of a scene as at 720p
- scouting notes: a chance test decides high, medium or low; first serves are compared
  with first serves; plan lines only from notes that are unlikely to be chance
- the report says when a playing hand was not entered, counts every fault and every
  serve that went in, explains its words, and says what its run time is
- the review page answers this computer only; the chat text does not assume a man
- score board warnings, and the serve dot test

Run:  .venv/bin/python -m unittest tests.test_review_fixes2 -v
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tennis import analytics as an  # noqa: E402
from tennis import ball as B  # noqa: E402
from tennis import chart, pipeline, report, review_server, scoreboard  # noqa: E402
from tennis.contracts import BallObs, BugLayout, CourtFit, ScoreObs, PointBoundary, px_unit  # noqa: E402
from tennis.players import PlayerDet  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


# ---------------------------------------------------------------------------
# local paths
# ---------------------------------------------------------------------------
class TestNoLocalPaths(unittest.TestCase):
    def test_public_meta_drops_full_paths(self):
        meta = {"video": "/srv/clips/m.mp4", "video_abs": "/srv/clips/m.mp4",
                "video_info": {"path": "/srv/clips/m.mp4", "width": 1280}, "players": {}}
        m = pipeline.public_meta(meta)
        self.assertNotIn("video_abs", m)
        self.assertEqual(m["video"], "m.mp4")
        self.assertEqual(m["video_info"], {"path": "m.mp4", "width": 1280})
        self.assertEqual(meta["video_abs"], "/srv/clips/m.mp4")     # input left alone
        rel = {"video": "inbox/m.mp4", "video_info": {"path": "inbox/m.mp4"}}
        self.assertEqual(pipeline.public_meta(rel), rel)

    def test_rel_outside_the_project_is_the_file_name(self):
        self.assertEqual(pipeline._rel("/somewhere/else/match.mp4", Path("out")), "match.mp4")
        inside = os.path.join(ROOT, "inbox", "x.mp4")
        self.assertEqual(pipeline._rel(inside, Path("out")), os.path.join("inbox", "x.mp4"))

    def test_render_writes_no_full_path_and_finds_the_video_from_cache(self):
        tmp = tempfile.mkdtemp(prefix="t_paths_")
        try:
            out = Path(tmp) / "out" / "m"
            (out / "cache").mkdir(parents=True)
            video = Path(tmp) / "videos" / "m.mp4"
            video.parent.mkdir()
            video.write_bytes(b"not a real video")
            (out / "cache" / pipeline.VIDEO_PATH_FILE).write_text(str(video))
            match = json.load(open(os.path.join(FIX, "match_sample.json")))
            match["meta"]["video"] = str(video)
            match["meta"]["video_abs"] = str(video)
            match["meta"].setdefault("video_info", {})["path"] = str(video)
            report.render(match, out)
            txt = (out / "match.json").read_text()
            self.assertNotIn(tmp, txt)
            self.assertNotIn("video_abs", txt)
            self.assertEqual(json.loads(txt)["meta"]["video"], "m.mp4")
            self.assertEqual(pipeline.local_video_path(out), video)
            html = (out / "report.html").read_text()
            self.assertIn("videos/m.mp4", html)            # the link still finds the video
            app = review_server.ReviewApp(str(out), None)
            self.assertEqual(app.video_path(), video)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# frame size
# ---------------------------------------------------------------------------
def _load_window():
    z = np.load(os.path.join(FIX, "ball_window_720p.npz"))
    info = json.load(open(os.path.join(FIX, "ball_window_720p.json")))
    times = [float(t) for t in z["times"]]
    cands, k = [], 0
    for n in z["counts"]:
        cands.append(z["rows"][k:k + n].copy())
        k += n
    dets = [PlayerDet(t=float(z["d_t"][j]), pid=None, end="near" if z["d_end"][j] == 0 else "far",
                      bbox=tuple(float(v) for v in z["d_bbox"][j]),
                      court_xy=tuple(float(v) for v in z["d_xy"][j]), conf=float(z["d_conf"][j]),
                      det_conf=float(z["d_det_conf"][j]), feat=None, interp=bool(z["d_interp"][j]))
            for j in range(len(z["d_t"]))]
    fw, fh = (int(v) for v in z["frame"])
    fit = CourtFit(H=z["H"].tolist(), frame_w=fw, frame_h=fh, err_px=float(z["err_px"][0]),
                   conf=float(z["err_px"][1]))
    return info, times, cands, dets, fit


def _scaled(fit, times, cands, dets, s):
    """The scene a video s times bigger would give: pixels times s, blob areas times s^2,
    the same court positions in metres."""
    H = (np.asarray(fit.H, float) @ np.diag([1.0 / s, 1.0 / s, 1.0])).tolist()
    fit_s = CourtFit(H=H, frame_w=int(round(fit.frame_w * s)), frame_h=int(round(fit.frame_h * s)),
                     err_px=fit.err_px * s, conf=fit.conf)
    c2 = []
    for c in cands:
        c = np.asarray(c, float).copy()
        if len(c):
            c[:, 0] *= s
            c[:, 1] *= s
            c[:, 3] *= s * s
        c2.append(c)
    d2 = [PlayerDet(t=d.t, pid=None, end=d.end, bbox=tuple(v * s for v in d.bbox), court_xy=d.court_xy,
                    conf=d.conf, det_conf=d.det_conf, feat=None, interp=d.interp) for d in dets]
    return fit_s, times, c2, d2


def _chart(info, fit, times, cands, dets):
    ball = chart.track_window(times, cands, dets, px_unit(fit.frame_h))
    old = chart.POSE_WING
    chart.POSE_WING = False          # no video here
    try:
        pv = chart.chart_tracked("", info["t_start"], info["t_end"], fit, info["server"], info["side"],
                                 info["ends"], dets, ball, known_winner=info["winner"])
    finally:
        chart.POSE_WING = old
    return ball, pv


class TestFrameSize(unittest.TestCase):
    """A real 720p window of match 1 (saved detections, no pictures) and the same scene
    as a 1080p and a 4K video would give it."""

    @classmethod
    def setUpClass(cls):
        cls.info, cls.times, cls.cands, cls.dets, cls.fit = _load_window()
        cls.ball, cls.pv = _chart(cls.info, cls.fit, cls.times, cls.cands, cls.dets)

    def test_px_unit(self):
        self.assertEqual(px_unit(720), 1.0)
        self.assertEqual(px_unit(1080), 1.5)
        self.assertEqual(px_unit(2160), 3.0)
        self.assertEqual(px_unit(None), 1.0)

    def test_window_has_serves(self):
        self.assertGreaterEqual(len(self.pv.serves), 1)
        self.assertTrue(any(e.kind == "bounce" for e in self.pv.shots))

    def test_same_track_and_chart_at_1080p_and_4k(self):
        for s in (1.5, 3.0):
            fit_s, t2, c2, d2 = _scaled(self.fit, self.times, self.cands, self.dets, s)
            ball_s, pv_s = _chart(self.info, fit_s, t2, c2, d2)
            self.assertEqual(len(ball_s), len(self.ball))
            for a, b in zip(self.ball, ball_s):
                self.assertAlmostEqual(a.t, b.t)
                self.assertAlmostEqual(a.xy_px[0] * s, b.xy_px[0], places=6)
                self.assertAlmostEqual(a.xy_px[1] * s, b.xy_px[1], places=6)
            self.assertEqual([(round(x.t, 3), x.n, x.result, x.zone, x.fault) for x in self.pv.serves],
                             [(round(x.t, 3), x.n, x.result, x.zone, x.fault) for x in pv_s.serves], s)
            self.assertEqual([(round(e.t, 3), e.kind, e.by, e.wing) for e in self.pv.shots],
                             [(round(e.t, 3), e.kind, e.by, e.wing) for e in pv_s.shots], s)
            self.assertEqual(self.pv.rally_len, pv_s.rally_len)
            # pixel values kept on the events are in the bigger video's own pixels
            for e1, e2 in zip(self.pv.shots, pv_s.shots):
                if getattr(e1, "_bbox", None) is not None:
                    np.testing.assert_allclose(np.array(e1._bbox) * s, e2._bbox, rtol=1e-9, atol=1e-6)

    def test_detect_events_alone_is_size_free(self):
        from tennis import events as E
        fit_s, t2, c2, d2 = _scaled(self.fit, self.times, self.cands, self.dets, 1.5)
        ev1 = E.detect_events(self.ball, self.dets, self.fit, fps=chart.FPS_BALL, server_end=None)
        ball_s = chart.track_window(t2, c2, d2, 1.5)
        ev2 = E.detect_events(ball_s, d2, fit_s, fps=chart.FPS_BALL, server_end=None)
        self.assertEqual([(round(e.t, 3), e.kind) for e in ev1], [(round(e.t, 3), e.kind) for e in ev2])
        self.assertGreater(len(ev1), 3)

    def test_old_behaviour_would_differ(self):
        """Without the unit the bigger scene's track differs: the test can see the bug."""
        fit_s, t2, c2, d2 = _scaled(self.fit, self.times, self.cands, self.dets, 3.0)
        raw = B.track_from_candidates(t2, B.suppress_players(t2, c2, d2))   # unit left at 1
        self.assertNotEqual([round(b.xy_px[0] / 3.0, 3) for b in raw],
                            [round(b.xy_px[0], 3) for b in self.ball])


def _homography(scale: float):
    """Image px -> court m for a broadcast-like view of a 1280x720 frame times scale."""
    import cv2
    img = np.float32([[430, 150], [850, 150], [1030, 620], [250, 620]]) * scale
    court = np.float32([[-5.485, 11.885], [5.485, 11.885], [5.485, -11.885], [-5.485, -11.885]])
    return cv2.getPerspectiveTransform(img, court).tolist()


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class TestFarHalfAtAnySize(unittest.TestCase):
    """A drawn ball crossing the far half, at 720p and at 1080p: the candidates and the
    track agree (the 1080p far half is read at the size it would have at 720p)."""

    def _clip(self, path, s):
        import cv2
        W, H = int(1280 * s), int(720 * s)
        proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                                 "-s", f"{W}x{H}", "-r", "30", "-i", "-", "-c:v", "libx264", "-preset",
                                 "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
        truth = []
        for i in range(40):
            f = np.zeros((H, W, 3), np.uint8)
            f[:] = (150, 90, 40)                         # blue court colour
            x, y = 500 + 7 * i, 180 + 2.5 * i            # far half, moving right and down
            truth.append((i / 30.0, x, y))
            cv2.circle(f, (int(round(x * s)), int(round(y * s))), max(1, int(round(2 * s))), (60, 230, 220), -1,
                       cv2.LINE_AA)
            proc.stdin.write(f.tobytes())
        proc.stdin.close()
        proc.wait()
        return truth

    def test_ball_found_at_both_sizes(self):
        tmp = tempfile.mkdtemp(prefix="t_far_")
        try:
            got = {}
            for s in (1.0, 1.5):
                p = os.path.join(tmp, f"c{s}.mp4")
                truth = self._clip(p, s)
                fit = CourtFit(H=_homography(s), frame_w=int(1280 * s), frame_h=int(720 * s), err_px=0.0, conf=1.0)
                times, cands = B.detect_candidates(p, 0.0, 40 / 30.0, fit)
                track = B.track_from_candidates(times, cands, px_unit(fit.frame_h))
                tt = {round(t, 3): (x, y) for t, x, y in truth}
                near = 0
                for b in track:
                    x, y = tt.get(round(b.t, 3), (None, None))
                    if x is not None and np.hypot(b.xy_px[0] / s - x, b.xy_px[1] / s - y) < 3.0:
                        near += 1
                got[s] = near
            self.assertGreaterEqual(got[1.0], 30)
            self.assertGreaterEqual(got[1.5], 30)
            self.assertLessEqual(abs(got[1.0] - got[1.5]), 3)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# scouting notes
# ---------------------------------------------------------------------------
def _pt(i, server, winner, played_on, zone, side="deuce", serves=None):
    return {"id": i, "server": server, "winner": winner, "side": side, "serve_number_won_on": played_on,
            "serves": serves if serves is not None else
            ([{"n": 1, "result": "fault"}] if played_on == 2 else []) +
            [{"n": played_on, "result": "in", "zone": zone}],
            "rally_len": 3, "ending": "winner", "ending_by": winner, "flags": {}, "conf": {},
            "review": {"status": "reviewed", "edited_fields": []}, "score_before": {}}


class TestNotes(unittest.TestCase):
    players = {"A": {"name": "Ruiz", "hand": "R"}, "B": {"name": "Okafor", "hand": "R"}}

    def test_chance_tests(self):
        self.assertAlmostEqual(an.fisher_p(8, 12, 1, 12), 0.00942, places=5)
        self.assertAlmostEqual(an.fisher_p(5, 5, 0, 5), 2 / 252, places=9)
        self.assertEqual(an.fisher_p(3, 10, 3, 10), 1.0)
        self.assertAlmostEqual(an.binom_p(15, 25), 0.4244, places=4)
        self.assertAlmostEqual(an.binom_p(10, 13), 0.0923, places=4)
        with self.assertRaises(ValueError):
            an.fisher_p(5, 4, 0, 4)

    def test_first_serves_are_compared_with_first_serves(self):
        """Wide first serves win 9 of 12 and other first serves 9 of 12; body second serves
        win 2 of 12. Mixed together, wide looked 25 points better than the rest; compared
        serve against serve there is no gap, so no note and no "Keep serving" line."""
        pts, i = [], 0
        for zone, n, won in (("wide", 12, 9), ("T", 12, 9)):
            for j in range(n):
                pts.append(_pt(i, "A", "A" if j < won else "B", 1, zone)); i += 1
        for j in range(12):
            pts.append(_pt(i, "A", "A" if j < 2 else "B", 2, "body")); i += 1
        st = an.compute_stats(pts, self.players)
        ins = an.insights(st, pts, self.players)
        self.assertFalse([x for x in ins if x["key"].startswith("zone_eff_1_deuce_wide")])
        self.assertFalse([x for x in ins if x["key"].startswith("zone_eff") and "wide" in x["key"]])
        recs = an.recommendations(st, ins, self.players)
        self.assertFalse([r for r in recs if "Keep serving" in r["text"]])

    def test_labels_follow_the_chance_test(self):
        """22 of 27 against 12 of 19 is a 19 point gap that chance gives about 1 time in 6:
        low, and no plan line. 15 of 15 against 3 of 15 is not chance: high, with a plan."""
        def make(k1, n1, k2, n2):
            pts, i = [], 0
            for zone, n, won in (("wide", n1, k1), ("T", n2, k2)):
                for j in range(n):
                    pts.append(_pt(i, "A", "A" if j < won else "B", 1, zone)); i += 1
            st = an.compute_stats(pts, self.players)
            ins = an.insights(st, pts, self.players)
            return ins, an.recommendations(st, ins, self.players)
        ins, recs = make(22, 27, 12, 19)
        w = [x for x in ins if x["key"] == "zone_eff_1_deuce_wide"][0]
        self.assertEqual(w["confidence"], "low")
        self.assertGreater(w["chance_p"], 0.05)
        self.assertIn("first serve went in wide on the deuce side (22 of 27)", w["text"])
        self.assertFalse([r for r in recs if "Keep serving" in r["text"]])
        ins, recs = make(15, 15, 3, 15)
        w = [x for x in ins if x["key"] == "zone_eff_1_deuce_wide"][0]
        self.assertEqual(w["confidence"], "high")
        self.assertLess(w["chance_p"], 0.01)
        self.assertTrue([r for r in recs if r["text"] == "Keep serving first serves wide on the deuce side."])

    def test_tendency_uses_a_sign_test(self):
        """15 body against 10 wide of 30: a 17 point gap, but chance gives it often: low."""
        pts = []
        for i in range(30):
            z = "body" if i < 15 else ("wide" if i < 25 else "T")
            pts.append(_pt(i, "A", "A", 2, z))
        ins = an.insights(an.compute_stats(pts, self.players), pts, self.players)
        t = [x for x in ins if x["key"] == "zone_pref_2_deuce"][0]
        self.assertEqual(t["confidence"], "low")
        self.assertAlmostEqual(t["chance_p"], 0.4244, places=3)


# ---------------------------------------------------------------------------
# report text
# ---------------------------------------------------------------------------
class TestReportText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t_rep_")
        m = json.load(open(os.path.join(FIX, "match_sample.json")))
        for P in ("A", "B"):
            m["meta"]["players"][P].pop("hand_given", None)
        m["meta"]["runtime_s"] = 12.0
        cls.match = m
        report.render(m, cls.tmp)
        with open(os.path.join(cls.tmp, "report.html"), encoding="utf-8") as f:
            cls.html = f.read()
        with open(os.path.join(cls.tmp, "match.json"), encoding="utf-8") as f:
            cls.m = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_hand_not_entered_is_said(self):
        name = self.m["meta"]["players"]["A"]["name"]
        self.assertIn(f"{name}'s playing hand was not entered, so the program takes this player as "
                      f"right-handed (its default, not checked)", self.html)
        self.assertNotIn(f"{name} right-handed.", self.html)
        m = copy.deepcopy(self.match)
        m["meta"]["players"]["A"]["hand_given"] = True
        m["meta"]["players"]["A"]["hand"] = "L"
        tmp = tempfile.mkdtemp(prefix="t_rep2_")
        try:
            report.render(m, tmp)
            with open(os.path.join(tmp, "report.html"), encoding="utf-8") as f:
                html = f.read()
            self.assertIn(f"{name} left-handed (entered when the video was analyzed).", html)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_options_and_cli_leave_the_hand_unset(self):
        self.assertIsNone(pipeline.Options().a_hand)
        self.assertIsNone(pipeline.Options().b_hand)
        import argparse
        from tennis import __main__ as cli
        with open(cli.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('a.add_argument("--a-hand", default=None', src)

    def test_confidence_words_are_defined(self):
        self.assertIn("How sure a note is.", self.html)
        self.assertIn("<b>High confidence</b>: less than 1 time in 100.", self.html)
        self.assertIn("not that the pattern or the plan was tested", self.html)
        self.assertNotIn("Scouting notes", self.html)
        self.assertIn("Words used in this report", self.html)
        for w in ("Unforced error", "Forced error", "Net points won", "Distance run per point",
                  "Percentage point", "Advantage, no-ad, tiebreak", "Forehand (FH)"):
            self.assertIn(w, self.html)
        self.assertIn("a run that reuses saved stages is much shorter than a full analysis", self.html)

    def test_serve_caption_counts_every_fault_and_every_serve_in(self):
        sc = report.serve_courts(self.m, self.m["stats"])
        for P in ("A", "B"):
            for sn in ("1", "2"):
                d = sc[P][sn]
                n_fault = sum(1 for p in self.m["points"] if p.get("server") == P
                              for s in p.get("serves") or [] if str(s.get("n")) == sn and s.get("result") == "fault")
                n_in = sum(1 for p in self.m["points"] if p.get("server") == P
                           for s in p.get("serves") or [] if str(s.get("n")) == sn and s.get("result") == "in")
                self.assertEqual(d["n_fault"], n_fault)
                self.assertEqual(sum(d["went_in"].values()), n_in)
                if n_fault:
                    self.assertIn(f"Faults: {n_fault}", d["caption"])
                self.assertIn(f"deuce side {d['went_in']['deuce']} (", d["caption"])

    def test_fault_kind_marked_computer_only(self):
        pts = [{"id": 1, "server": "A", "side": "deuce", "serves": [{"n": 1, "result": "fault", "fault": "long"},
                                                                   {"n": 2, "result": "in", "zone": "T"}],
                "review": {"status": "partial", "checked_fields": ["serve2_zone"]}},
               {"id": 2, "server": "A", "side": "ad", "serves": [{"n": 1, "result": "fault"},
                                                                {"n": 2, "result": "fault"}]}]
        stats = {"players": {P: {"serve_zones": {"1": {}, "2": {}}} for P in ("A", "B")}}
        sc = report.serve_courts({"points": pts, "meta": {}}, stats)
        cap = sc["A"]["1"]["caption"]
        self.assertIn("Faults: 2; the kind of miss is known for 1 (long 1), found by the computer alone, not checked.",
                      cap)
        self.assertIn("No faults found.", sc["B"]["1"]["caption"])
        pts[0]["review"]["checked_fields"].append("serve1_fault")
        cap = report.serve_courts({"points": pts, "meta": {}}, stats)["A"]["1"]["caption"]
        self.assertIn("(long 1), checked in a review.", cap)

    def test_chance_words(self):
        self.assertEqual(report.chance_words(0.086), "about 1 time in 12")
        self.assertEqual(report.chance_words(0.0004), "less than 1 time in 1,000")
        self.assertEqual(report.chance_words(0.9), "more than 1 time in 2")


# ---------------------------------------------------------------------------
# review page, chat
# ---------------------------------------------------------------------------
class TestReviewHost(unittest.TestCase):
    def test_only_this_computer(self):
        ok = review_server._host_ok
        for h in ("127.0.0.1:8765", "[::1]:8765", "localhost:8765", "a.localhost:1", "127.0.0.2"):
            self.assertTrue(ok(h), h)
        for h in ("8.8.8.8:8765", "192.168.1.73:8765", "10.0.0.2", "attacker.example:8765", "", None,
                  "[2001:db8::1]:8765"):
            self.assertFalse(ok(h), h)

    def test_no_network_binding(self):
        with self.assertRaises(ValueError):
            review_server.serve(tempfile.gettempdir(), 0, "0.0.0.0")


class TestChatWords(unittest.TestCase):
    def test_no_he(self):
        import re
        from tennis import chat
        text = json.dumps(chat.DEFINITIONS) + open(chat.__file__, encoding="utf-8").read()
        self.assertFalse(re.findall(r"[\"'][^\"']*\b(he|his|him)\b[^\"']*[\"']", json.dumps(chat.DEFINITIONS)))
        for phrase in ("if he wins", "what he did", "his clearest", "where he aimed"):
            self.assertNotIn(phrase, text)


# ---------------------------------------------------------------------------
# score board
# ---------------------------------------------------------------------------
class TestBoardWarnings(unittest.TestCase):
    L = BugLayout(x0=0, y0=0, x1=10, y1=10, names=["LANE", "MORGAN"], rows=[(0, 5), (5, 10)], cols=[(5, 10)])

    @staticmethod
    def _b(i, gap):
        return PointBoundary(index=i, t_end=float(i), t_start=None, winner="A", server="A", score_before={},
                             score_after={}, side="deuce", flags={}, gap_before=gap)

    def _obs(self, server):
        return ScoreObs(t=0.0, sets=[], games=[0, 0], points=["0", "0"], server=server, in_tb=False, conf=1.0, raw="")

    def test_none_for_a_good_board(self):
        b = [self._b(i, False) for i in range(20)]
        self.assertEqual(pipeline.scorebug_warnings(self.L, [self._obs(0)], b), [])

    def test_no_board_found(self):
        # gap close step: before, no board at all gave no warning (only the report's facts line said it)
        for layout in (None, False):
            w = pipeline.scorebug_warnings(layout, [], [])
            self.assertEqual(w, [pipeline.NO_BOARD_WARNING])
        self.assertIn("No score board was found", w[0])
        self.assertIn("not read", w[0])
        self.assertIn("capital", w[0])
        for ch in ("—", "–"):
            self.assertNotIn(ch, w[0])

    def test_board_without_readings(self):
        w = pipeline.scorebug_warnings(self.L, [], [])
        self.assertEqual(len(w), 1)
        self.assertIn("no score could be read", w[0])

    def test_no_serve_marker_and_many_jumps(self):
        b = [self._b(i, i % 3 == 0) for i in range(20)]
        w = pipeline.scorebug_warnings(self.L, [self._obs(None)] * 4, b)
        self.assertEqual(len(w), 2)
        self.assertIn("serve marker on the score board was not read in any of 4 readings", w[0])
        self.assertIn("LANE (top row) served first", w[0])
        self.assertIn("7 times in 20 points", w[1])


class TestServeDot(unittest.TestCase):
    def _g(self, w, h, fill_disc=True):
        import cv2
        p = np.zeros((h, w), np.uint8)
        if fill_disc:
            cv2.ellipse(p, (w // 2, h // 2), (max(1, w // 2), max(1, h // 2)), 0, 0, 360, 255, -1)
        else:                      # a ring, like an O
            cv2.ellipse(p, (w // 2, h // 2), (max(1, w // 2 - 1), max(1, h // 2 - 1)), 0, 0, 360, 255, 2)
        return scoreboard._G(0, 0, w, h, 0, p)

    def test_dot_and_letters(self):
        self.assertTrue(scoreboard._dot_like(self._g(11, 11), 17.0, 60))       # a dot, 0.65 of the letters
        self.assertFalse(scoreboard._dot_like(self._g(15, 17), 17.0, 60))      # letter-height blob
        self.assertFalse(scoreboard._dot_like(self._g(11, 11, False), 17.0, 60))   # a small ring
        self.assertFalse(scoreboard._dot_like(self._g(4, 11), 17.0, 60))       # thin: not round
        self.assertFalse(scoreboard._dot_like(self._g(2, 2), 17.0, 60))        # a speck


if __name__ == "__main__":
    unittest.main()
