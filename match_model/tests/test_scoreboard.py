"""Score-bug tests against hand-labelled ground truth (tests/fixtures/gt_scorebug.json).

Run:  .venv/bin/python -m unittest tests.test_scoreboard -v
  or  .venv/bin/python tests/test_scoreboard.py
(pytest also collects these unittest classes if it is installed.)

Needs videoplayback.mp4 in the project root and macOS Vision; skipped otherwise.
Set TENNIS_SLOW_TESTS=1 to also run the generic-locate tests, which re-encode two
10-minute clips with ffmpeg (bug masked out / bug moved) - about 30 s extra.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

VIDEO = os.path.join(ROOT, "videoplayback.mp4")
GT_PATH = os.path.join(ROOT, "tests", "fixtures", "gt_scorebug.json")

try:
    from tennis import scoreboard as sb
    from tennis.contracts import BugLayout
    from tennis.video import read_frame
    from tennis.vision_ocr import HAVE_VISION
except Exception:  # pragma: no cover
    HAVE_VISION = False

HAVE_VIDEO = os.path.exists(VIDEO)


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua else 0.0


def obs_tuple(o):
    return (tuple(tuple(s) for s in o.sets), tuple(o.games), tuple(o.points), o.server)


def gt_tuple(d):
    return (tuple(tuple(s) for s in d["sets"]), tuple(d["games"]), tuple(d["points"]), d["server"])


@unittest.skipUnless(HAVE_VIDEO and HAVE_VISION, "needs videoplayback.mp4 and Apple Vision")
class TestScoreBug(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GT_PATH) as f:
            cls.gt = json.load(f)
        t0 = time.time()
        cls.layout = sb.locate_bug(VIDEO)
        cls.t_locate = time.time() - t0
        print(f"\n[scoreboard] locate_bug: {cls.t_locate:.1f}s -> {cls.layout}")

    def test_locate_box_and_names(self):
        L = self.layout
        self.assertIsNotNone(L)
        box = (L.x0, L.y0, L.x1, L.y1)
        v = iou(box, self.gt["box"])
        print(f"[scoreboard] box {box} vs GT {tuple(self.gt['box'])}: IoU {v:.3f}")
        self.assertGreaterEqual(v, 0.8)
        self.assertEqual([n.upper() for n in sb.stream_names(L)], self.gt["names"])
        self.assertEqual(len(L.rows), 2)
        self.assertEqual(len(L.dot_rois), 2)

    def test_per_frame_accuracy(self):
        """Exact (sets, games, points, server) on every GT timestamp, starting from a
        reader state with no templates (bootstrapped from scratch)."""
        L = self.layout
        sb._STATES.pop(sb._key(L), None)
        ok, wrong = 0, []
        for d in self.gt["frames"]:
            o = sb.read_bug(read_frame(VIDEO, d["t"]), L)
            if not d["present"]:
                good = o is None
                got = None if o is None else obs_tuple(o)
            else:
                good = o is not None and obs_tuple(o) == gt_tuple(d) and o.in_tb == d["in_tb"]
                got = None if o is None else (obs_tuple(o), o.in_tb)
            ok += good
            if not good:
                wrong.append((d["t"], got, None if not d["present"] else gt_tuple(d)))
        n = len(self.gt["frames"])
        acc = ok / n
        print(f"[scoreboard] per-frame exact accuracy {ok}/{n} = {acc:.3f}; vision calls "
              f"{sb._state(L).vision_calls}; wrong: {wrong}")
        self.assertGreaterEqual(n, 60)
        self.assertGreaterEqual(acc, 0.97)

    def test_stream_sequence(self):
        L = self.layout
        t0 = time.time()
        seq = sb.read_stream(VIDEO, L, fps=2.0)
        dt = time.time() - t0
        print(f"[scoreboard] read_stream: {dt:.1f}s, {len(seq)} states")
        for o in seq:
            print(f"   t={o.t:7.2f} sets={o.sets} games={o.games} points={o.points} "
                  f"server={o.server} in_tb={o.in_tb} conf={o.conf}  raw={o.raw}")
        got = [obs_tuple(o) for o in seq]
        want = [gt_tuple(s) for s in self.gt["sequence"]]
        self.assertEqual(got, want)            # same states, same order, nothing spurious
        for o, s in zip(seq, self.gt["sequence"]):
            # stream timestamps are sample labels (2 fps, iter_frames labels lag ~0.25 s)
            self.assertGreaterEqual(o.t, s["t_lo"] - 1.0, (o.t, s))
            self.assertLessEqual(o.t, s["t_hi"] + 1.0, (o.t, s))
        self.assertLess(dt, 30.0)

    def test_read_bug_absent_on_blank_frame(self):
        import numpy as np
        blank = np.full((720, 1280, 3), 120, np.uint8)
        self.assertIsNone(sb.read_bug(blank, self.layout))


@unittest.skipUnless(HAVE_VIDEO and HAVE_VISION, "needs videoplayback.mp4 and Apple Vision")
class TestSyntheticLayouts(unittest.TestCase):
    """Cases this video never shows, built by editing a real frame (t=60 s, score
    7-6(6) 4-6 6-6, TB 2-0, Alcaraz serving). SYNTHETIC: they exercise the column /
    cell logic, not real broadcast rendering; the AD glyphs use an OpenCV font."""

    W = 35   # width of one set column in px

    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.np = np
        f = read_frame(VIDEO, 60.0)
        w = cls.W
        g = f.copy()                                   # insert a copy of the set-2 column
        g[:, 320 + w:] = f[:, 320:1280 - w]
        g[600:670, 320:320 + w] = f[600:670, 285:320]
        cls.three_sets = g
        cls.layout3 = BugLayout(x0=128, y0=604, x1=379 + w, y1=666, names=["SINNER", "ALCARAZ"],
                                rows=[(604, 635), (635, 666)], cols=[(255, 278)],
                                dot_rois=[(129, 604, 151, 635), (129, 635, 151, 666)])

    def _ad_frame(self):
        np = self.np
        import cv2
        w = self.W
        h = self.three_sets.copy()
        bg = h[640:642, 352 + w:356 + w].reshape(-1, 3).mean(0)
        for (y0, y1) in [(607, 633), (638, 663)]:      # blank both points cells
            h[y0:y1, 351 + w:377 + w] = bg
        big = np.zeros((120, 160), np.uint8)
        cv2.putText(big, "AD", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 3.2, 255, 9, cv2.LINE_AA)
        ys, xs = np.where(big > 0)
        a = cv2.resize(big[ys.min():ys.max() + 1, xs.min():xs.max() + 1], (22, 17),
                       interpolation=cv2.INTER_AREA).astype(np.float32)[..., None] / 255
        y0, x0 = 612, 353 + w                          # "AD" in the top row, bottom row blank
        reg = h[y0:y0 + 17, x0:x0 + 22].astype(np.float32)
        h[y0:y0 + 17, x0:x0 + 22] = (reg * (1 - a) + 255 * a).astype(np.uint8)
        return h

    def test_three_completed_sets(self):
        o = sb.read_bug(self.three_sets, self.layout3)
        self.assertIsNotNone(o, sb.last_debug)
        self.assertEqual(obs_tuple(o), (((7, 6), (4, 6), (4, 6)), (6, 6), ("2", "0"), 1))
        self.assertIn("6^6", o.raw)                    # superscript kept apart from the games

    def test_advantage_and_blank_cell(self):
        o = sb.read_bug(self._ad_frame(), self.layout3)
        self.assertIsNotNone(o, sb.last_debug)
        self.assertEqual(o.points, ["A", "40"])
        self.assertEqual(o.sets, [[7, 6], [4, 6], [4, 6]])


@unittest.skipUnless(HAVE_VIDEO and HAVE_VISION and shutil.which("ffmpeg")
                     and os.environ.get("TENNIS_SLOW_TESTS") == "1",
                     "set TENNIS_SLOW_TESTS=1 (re-encodes clips with ffmpeg)")
class TestLocateGeneric(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="scorebug_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _make(self, name, vf):
        out = os.path.join(self.tmp, name)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", VIDEO, "-t", "600", "-vf", vf,
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-g", "25", "-an", out],
                       check=True)
        return out

    def test_no_bug_returns_none(self):
        """Bug painted over: the in-arena scoreboard (main camera) must not be chosen."""
        p = self._make("nobug.mp4", "fps=5,drawbox=x=120:y=596:w=270:h=76:color=0x3a6aa0:t=fill")
        self.assertIsNone(sb.locate_bug(p))

    def test_moved_bug(self):
        """Frame shifted by (-40, -10): the bug must be found at its new place and read."""
        p = self._make("moved.mp4", "fps=5,crop=1180:640:100:40,pad=1280:720:60:30:color=gray")
        L = sb.locate_bug(p)
        self.assertIsNotNone(L)
        want = (128 - 40, 604 - 10, 378 - 40, 665 - 10)
        self.assertGreaterEqual(iou((L.x0, L.y0, L.x1, L.y1), want), 0.8)
        with open(GT_PATH) as f:
            gt = json.load(f)
        got = [obs_tuple(o) for o in sb.read_stream(p, L)]
        # the clip is cut at 600 s, before the final 6-7 state
        self.assertEqual(got, [gt_tuple(s) for s in gt["sequence"][:-1]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
