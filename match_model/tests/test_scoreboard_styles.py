"""Two more score board styles, drawn with OpenCV (not real broadcast graphics).

- no highlighted cell anywhere: dot | NAME | set games ... | games | points, with the
  points cell showing 0 0 at the start of each game (once read as another set)
- the serve dot drawn close to the name, in the match 1 style (yellow current-set
  games cell): the dot used to be taken as the first letter of the name, so the server
  was never read and every point could get the wrong server

Both use the 21 states of tests/test_scoreboard_layouts.py at the bottom left.

Run:  .venv/bin/python -m unittest tests.test_scoreboard_styles -v
Needs Apple Vision and ffmpeg (skipped without them).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tennis import scoreboard as sb  # noqa: E402
from tennis.contracts import MatchFormat  # noqa: E402
from tennis.scoring import reconcile  # noqa: E402
from tennis.vision_ocr import HAVE_VISION  # noqa: E402
from tests import test_scoreboard_layouts as T  # noqa: E402

YELLOW = (40, 200, 240)
X0, Y0, RH = 60, 600, 34


def draw(img, sets, games, points, server, style, dot_w=22, dot_x=11):
    """dot | NAME | set games ... | current games | gap | points.
    style "plain": no highlight, points in a grey cell with white digits.
    style "games_hl": the current-set games in a yellow cell with dark digits."""
    ncols = len(sets) + 1
    name_w, cw, pw, gap = 200, 40, 50, 12
    width = dot_w + name_w + ncols * cw + gap + pw + 6
    y1 = Y0 + 2 * RH
    cv2.rectangle(img, (X0, Y0), (X0 + width, y1), (35, 25, 20), -1)
    cv2.line(img, (X0, Y0 + RH), (X0 + width, Y0 + RH), (90, 90, 90), 1)
    names = ("MORGAN", "LANE")
    cx = X0 + dot_w + name_w
    px0 = cx + ncols * cw + gap
    if style == "games_hl":
        gx = cx + (ncols - 1) * cw
        cv2.rectangle(img, (gx + 2, Y0 + 2), (gx + cw - 2, y1 - 2), YELLOW, -1)
    for r in range(2):
        a, b = Y0 + r * RH, Y0 + (r + 1) * RH
        if r == server:
            cv2.circle(img, (X0 + dot_x, (a + b) // 2), 5, YELLOW, -1, cv2.LINE_AA)
        cv2.putText(img, names[r], (X0 + dot_w + 4, b - 9), T.FONT, 0.8, T.WHITE, 2, cv2.LINE_AA)
        vals = [s[r] for s in sets] + [games[r]]
        for k, g in enumerate(vals):
            col = (20, 20, 20) if (style == "games_hl" and k == len(vals) - 1) else T.WHITE
            T.text_centered(img, str(g), cx + (k + 0.5) * cw, (a + b) / 2, col, scale=0.85)
        cv2.rectangle(img, (px0, a + 2), (px0 + pw, b - 2), (70, 60, 55), -1)
        T.text_centered(img, points[r], px0 + pw / 2, (a + b) / 2, T.WHITE, scale=0.75)


def write(path, **kw):
    bg = T.background()
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                             "-s", f"{T.W}x{T.H}", "-r", str(T.FPS), "-i", "-", "-c:v", "libx264", "-preset",
                             "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    for sets, games, points, server in T.STATES:
        f = bg.copy()
        draw(f, sets, games, points, server, **kw)
        for _ in range(T.HOLD_S * T.FPS):
            proc.stdin.write(f.tobytes())
    proc.stdin.close()
    proc.wait()


def read(**kw):
    tmp = tempfile.mkdtemp(prefix="sb_style_")
    try:
        v = os.path.join(tmp, "board.mp4")
        write(v, **kw)
        sb._STATES.clear()
        L = sb.locate_bug(v)
        obs = sb.read_stream(v, L) if L else []
        return L, obs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def in_force(obs, k):
    """The reading in force at the middle of drawn state k."""
    tm = k * T.HOLD_S + 1.5
    cur = [o for o in obs if o.t <= tm]
    return cur[-1] if cur else None


@unittest.skipUnless(HAVE_VISION and shutil.which("ffmpeg"), "needs Apple Vision and ffmpeg")
class TestNoHighlightBoard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.L, cls.obs = read(style="plain")

    def test_every_state_read_with_the_right_columns(self):
        self.assertIsNotNone(self.L)
        self.assertFalse(self.L.points_first)
        self.assertFalse(self.L.points_hl)
        for k, (sets, games, points, server) in enumerate(T.STATES):
            o = in_force(self.obs, k)
            self.assertIsNotNone(o, k)
            self.assertEqual((o.sets, o.games, o.points), (sets, games, points), k)

    def test_engine_follows_every_point(self):
        b = reconcile(self.obs, MatchFormat(no_ad=True))
        # 20 real points between the 21 states; two drawn jumps (to 5-2 and 4-3) skip points
        # the engine cannot see, so it follows the 18 it can
        self.assertEqual(len(b), 18)


@unittest.skipUnless(HAVE_VISION and shutil.which("ffmpeg"), "needs Apple Vision and ffmpeg")
class TestDotCloseToTheName(unittest.TestCase):
    """dot_w 18 puts the dot (x 66 to 76) 6 px from the name; it used to be joined to it."""

    def test_server_read_on_every_state(self):
        L, obs = read(style="games_hl", dot_w=18)
        self.assertIsNotNone(L)
        self.assertGreaterEqual(max(r[2] for r in L.dot_rois), 77)      # the dot area reaches past the dot
        for k, (_s, _g, _p, server) in enumerate(T.STATES):
            o = in_force(obs, k)
            self.assertIsNotNone(o, k)
            self.assertEqual(o.server, server, k)
        b = reconcile(obs, MatchFormat(no_ad=True))
        self.assertEqual(b[0].server, "B")          # LANE, bottom row, serves first


if __name__ == "__main__":
    unittest.main()
