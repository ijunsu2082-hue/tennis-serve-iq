"""Score board styles other than the two real matches in the other tests, on a drawn video.

The style drawn here is one seen on a college match: the names carry a team code in
brackets ("LANE (UNI)"), the server is marked by an arrow right of the name, the points
come first in a grey cell and the games of each set follow (current set last), and the
board is centred, so it grows on both sides and moves sideways when a set column is
added. Everything is drawn with OpenCV, so these tests check the reading logic, not any
real broadcast graphics.

Run:  .venv/bin/python -m unittest tests.test_scoreboard_layouts -v
The reading tests need Apple Vision and ffmpeg and are skipped without them.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tennis import scoreboard as sb  # noqa: E402
from tennis.contracts import BugLayout, MatchFormat  # noqa: E402
from tennis.scoring import reconcile  # noqa: E402
from tennis.vision_ocr import HAVE_VISION  # noqa: E402

W, H, FPS = 1280, 720, 4
NAMES = ("MORGAN (TECH)", "LANE (UNI)")
GREEN, GREY, BLACK, WHITE = (60, 215, 190), (165, 165, 165), (20, 20, 20), (255, 255, 255)
ROW_Y = (54, 90, 125)          # top, middle line, bottom of the board
NAME_W, ARROW_W, CELL_W, CX = 236, 30, 34, 1085
FONT = cv2.FONT_HERSHEY_DUPLEX

# (finished sets, current games, points, server row), row 0 = MORGAN, row 1 = LANE (made-up names).
# Each state is shown for 3 s. The board grows by one column at the second set.
STATES = [
    ([], [0, 0], ["0", "0"], 1),
    ([], [0, 0], ["0", "15"], 1),
    ([], [0, 0], ["0", "30"], 1),
    ([], [0, 0], ["15", "30"], 1),
    ([], [0, 0], ["15", "40"], 1),
    ([], [0, 1], ["0", "0"], 0),
    ([], [0, 1], ["15", "0"], 0),
    ([], [0, 1], ["30", "0"], 0),
    ([], [0, 1], ["40", "0"], 0),
    ([], [1, 1], ["0", "0"], 1),
    ([], [5, 2], ["40", "30"], 0),
    ([[6, 2]], [0, 0], ["0", "0"], 1),
    ([[6, 2]], [0, 0], ["15", "0"], 1),
    ([[6, 2]], [0, 0], ["15", "15"], 1),
    ([[6, 2]], [0, 0], ["30", "15"], 1),
    ([[6, 2]], [0, 0], ["40", "15"], 1),
    ([[6, 2]], [1, 0], ["0", "0"], 0),
    ([[6, 2]], [1, 0], ["0", "15"], 0),
    ([[6, 2]], [1, 0], ["0", "30"], 0),
    ([[6, 2]], [1, 0], ["15", "30"], 0),
    ([[6, 2]], [4, 3], ["40", "40"], 0),
]
HOLD_S = 3


def background(seed: int = 7) -> np.ndarray:
    """Trees and sky above, a blue court below: texture, so the board's edges are not
    the only edges in the picture."""
    rng = np.random.default_rng(seed)
    noise = cv2.GaussianBlur(rng.integers(0, 255, (H, W), dtype=np.uint8), (0, 0), 3)
    img = np.zeros((H, W, 3), np.uint8)
    img[..., 0] = 40 + noise // 4
    img[..., 1] = 90 + noise // 3
    img[..., 2] = 50 + noise // 5
    img[240:] = (200, 150, 70)
    return img


def text_centered(img, txt, cx, cy, color, scale=0.95, thick=2):
    (tw, th), _ = cv2.getTextSize(txt, FONT, scale, thick)
    cv2.putText(img, txt, (int(cx - tw / 2), int(cy + th / 2)), FONT, scale, color, thick, cv2.LINE_AA)


def draw_board(img, sets, games, points, server) -> tuple[int, int]:
    """Draws one board state; returns the box's left and right x."""
    ncols = 1 + len(sets) + 1
    width = 3 + NAME_W + ARROW_W + ncols * CELL_W + 3
    x0 = CX - width // 2
    x1 = x0 + width
    y0, ym, y1 = ROW_Y
    cv2.rectangle(img, (x0, y0), (x1, y1), BLACK, -1)
    cv2.rectangle(img, (x0, y0), (x1, y1), GREEN, 3)
    cv2.line(img, (x0, ym), (x1, ym), GREEN, 2)
    cx0 = x0 + 3 + NAME_W + ARROW_W                       # first score cell
    for r, (a, b) in enumerate([(y0 + 3, ym - 1), (ym + 1, y1 - 3)]):
        cv2.putText(img, NAMES[r], (x0 + 9, b - 8), FONT, 0.8, WHITE, 2, cv2.LINE_AA)
        if r == server:                                   # arrow pointing left, at the scores
            ax = cx0 - 8
            yc = (a + b) // 2
            pts = np.array([[ax, yc - 8], [ax - 12, yc], [ax, yc + 8], [ax, yc + 3], [ax - 5, yc], [ax, yc - 3]])
            cv2.fillPoly(img, [pts], GREEN, cv2.LINE_AA)
        cv2.rectangle(img, (cx0, a), (cx0 + CELL_W - 2, b), GREY, -1)
        text_centered(img, points[r], cx0 + CELL_W / 2 - 1, (a + b) / 2, BLACK, scale=0.75)
        for k, g in enumerate([s[r] for s in sets] + [games[r]]):
            text_centered(img, str(g), cx0 + (k + 1.5) * CELL_W, (a + b) / 2, WHITE, scale=1.0)
    for k in range(ncols):
        x = cx0 + k * CELL_W - 1
        cv2.line(img, (x, y0), (x, y1), GREEN, 2)
    return x0, x1


def write_video(path: str) -> None:
    bg = background()
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset",
                             "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    for sets, games, points, server in STATES:
        f = bg.copy()
        draw_board(f, sets, games, points, server)
        for _ in range(HOLD_S * FPS):
            proc.stdin.write(f.tobytes())
    proc.stdin.close()
    assert proc.wait() == 0


class TestNames(unittest.TestCase):
    def test_team_code_and_arrow(self):
        self.assertEqual(sb._is_name("MORGAN (TECH) <"), "MORGAN (TECH)")
        self.assertEqual(sb._is_name("LANE (UNI)"), "LANE (UNI)")
        self.assertEqual(sb._is_name("LANE (UNI) ‹"), "LANE (UNI)")

    def test_plain_names_unchanged(self):
        self.assertEqual(sb._is_name("HALYS"), "HALYS")
        self.assertEqual(sb._is_name("• SINNER"), "SINNER")
        self.assertIsNone(sb._is_name("40"))
        self.assertIsNone(sb._is_name("Lane (uni)"))      # mostly lower case: not a board name

    def test_accented_names(self):
        """Players from anywhere: accented capitals are letters, also as the first letter."""
        self.assertEqual(sb._is_name("BÁEZ"), "BÁEZ")
        self.assertEqual(sb._is_name("ŠVÁBOVÁ"), "ŠVÁBOVÁ")
        self.assertEqual(sb._is_name("ÁLVAREZ"), "ÁLVAREZ")
        self.assertEqual(sb._is_name("• ÖZTÜRK <"), "ÖZTÜRK")
        self.assertTrue(sb._names_match(["ŠVÁBOVÁ", "BÁEZ"], ["ŠVÁBOVÁ", "BÁEZ"]))
        self.assertIsNone(sb._is_name("Švábová"))         # mixed case is still not a board name


class TestBrokenGlyph(unittest.TestCase):
    """A bold 4 that came apart at its thin join is read as one glyph."""

    def _g(self, x0, y0, x1, y1):
        return sb._G(x0, y0, x1, y1, 0, np.full((y1 - y0, x1 - x0), 200, np.uint8))

    def test_piece_left_of_its_stem_is_joined(self):
        out = sb._join_broken([self._g(15, 9, 27, 29), self._g(9, 13, 15, 25)])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].x0, out[0].y0, out[0].x1, out[0].y1), (9, 9, 27, 29))
        self.assertEqual(out[0].patch.shape, (20, 18))

    def test_two_digits_and_a_superscript_stay_apart(self):
        self.assertEqual(len(sb._join_broken([self._g(0, 9, 8, 29), self._g(10, 9, 22, 29)])), 2)   # a gap: "15"
        self.assertEqual(len(sb._join_broken([self._g(0, 9, 12, 29), self._g(12, 9, 20, 21)])), 2)  # superscript, right
        self.assertEqual(len(sb._join_broken([self._g(0, 14, 9, 26), self._g(9, 9, 21, 29)])), 2)   # wider than a digit


class TestFrameShift(unittest.TestCase):
    """The board moves left by half a column when a set column is added."""

    def setUp(self):
        self.small, self.big = background(), background()
        sx0, _ = draw_board(self.small, [], [3, 2], ["15", "0"], 0)
        self.bx0, bx1 = draw_board(self.big, [[6, 2]], [3, 2], ["15", "0"], 0)
        # the box starts where its dark inside starts, past the border (as locate_bug has it)
        self.small_in, self.big_in = self._inner(self.small, sx0), self._inner(self.big, self.bx0)
        self.layout = BugLayout(x0=self.big_in, y0=ROW_Y[0], x1=bx1, y1=ROW_Y[2], names=list(NAMES),
                                rows=[(ROW_Y[0], ROW_Y[1]), (ROW_Y[1], ROW_Y[2])], cols=[], dot_rois=[])

    @staticmethod
    def _inner(img, x0):
        row = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[ROW_Y[0] + 10]
        return next(x for x in range(x0, x0 + 20) if row[x] < 60)

    def _dx(self, img):
        g = sb._gray_dark_base(img, cv2.cvtColor(img[ROW_Y[0]:ROW_Y[2], self.bx0:self.bx0 + 200], cv2.COLOR_BGR2GRAY))
        return sb._frame_dx(g, self.layout, 0, 0)

    def test_no_shift_where_the_layout_was_made(self):
        self.assertEqual(self._dx(self.big), 0)

    def test_shift_of_the_smaller_board(self):
        self.assertEqual(self._dx(self.small), self.small_in - self.big_in)
        self.assertEqual(self.small_in - self.big_in, CELL_W // 2)

    def test_no_board(self):
        self.assertEqual(self._dx(background()), 0)


@unittest.skipUnless(HAVE_VISION and shutil.which("ffmpeg"), "needs Apple Vision and ffmpeg")
class TestPointsFirstBoard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="scorebug_layouts_")
        cls.video = os.path.join(cls.tmp, "drawn_board.mp4")
        write_video(cls.video)
        cls.layout = sb.locate_bug(cls.video)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_found_with_names_and_roles(self):
        L = self.layout
        self.assertIsNotNone(L)
        self.assertEqual(L.names, list(NAMES))
        self.assertTrue(L.points_first)
        # the serve mark is looked for right of the names, next to the scores
        self.assertFalse(sb._marks_left(L))
        self.assertGreater(min(r[0] for r in L.dot_rois), L.x0 + NAME_W // 2)

    def test_every_state_read(self):
        got = [(o.sets, o.games, o.points, o.server) for o in sb.read_stream(self.video, self.layout)]
        want = [(s, g, p, srv) for s, g, p, srv in STATES]
        self.assertEqual(got, want)

    def test_points_from_the_engine(self):
        """No-ad: 40-40 is one deciding point, and the readings make legal points."""
        obs = sb.read_stream(self.video, self.layout)
        bounds = reconcile(obs, MatchFormat(no_ad=True))
        self.assertTrue(all(b.winner in ("A", "B") for b in bounds))
        self.assertEqual(bounds[0].server, "B")            # LANE, bottom row, serves first


ACCENT_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ACCENT_NAMES = ("ÁLVAREZ", "ŠVÁBOVÁ")     # made up; an accented first letter, and 3 of 7 letters accented


def write_accent_video(path: str) -> None:
    """The same board, with the names written by PIL (OpenCV cannot draw accents)."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(ACCENT_FONT, 24)
    bg = background()
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset",
                             "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    global NAMES
    keep, NAMES = NAMES, ("", "")
    try:
        for sets, games, points, server in STATES:
            f = bg.copy()
            x0, _ = draw_board(f, sets, games, points, server)
            img = Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
            d = ImageDraw.Draw(img)
            y0, ym, y1 = ROW_Y
            for r, a in enumerate((y0 + 3, ym + 1)):
                d.text((x0 + 9, a + 5), ACCENT_NAMES[r], font=font, fill=WHITE)
            f = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            for _ in range(HOLD_S * FPS):
                proc.stdin.write(f.tobytes())
    finally:
        NAMES = keep
    proc.stdin.close()
    assert proc.wait() == 0


@unittest.skipUnless(HAVE_VISION and shutil.which("ffmpeg") and os.path.exists(ACCENT_FONT),
                     "needs Apple Vision, ffmpeg and the Arial Bold font")
class TestAccentedNamesBoard(unittest.TestCase):
    """Before the fix, names with accented letters were not taken as names, so the board
    was not found at all (or an accented first letter was cut off: "LVAREZ")."""

    def test_board_found_and_read(self):
        tmp = tempfile.mkdtemp(prefix="scorebug_accent_")
        try:
            video = os.path.join(tmp, "accent_board.mp4")
            write_accent_video(video)
            L = sb.locate_bug(video)
            self.assertIsNotNone(L)
            self.assertEqual(L.names, list(ACCENT_NAMES))
            got = [(o.sets, o.games, o.points, o.server) for o in sb.read_stream(video, L)]
            self.assertEqual(got, [(s, g, p, srv) for s, g, p, srv in STATES])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
