"""Score board tests on tiny real crops, no video needed (tests/fixtures/scorebug_crops/).

The crops come from a second match with another graphics style than the main test clip:
ABN AMRO Open 2023 qualifying, Halys against Barrere. Every label was read by eye.
On this style the old reader cut the points box off and said 0-0 almost all the time.

Run:  .venv/bin/python -m unittest tests.test_scoreboard_crops -v

Tests that read digits need Apple Vision (it teaches the reader the digit shapes) and
are skipped without it. The rest only use image structure and run anywhere.
Set TENNIS_MATCH2_VIDEO to the path of that match's video file to also run the whole
match against the scores read by eye (about 2 minutes).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import unittest
from unittest import mock

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tennis import scoreboard as sb  # noqa: E402
from tennis.contracts import BugLayout, MatchFormat, ScoreObs  # noqa: E402
from tennis.scoring import reconcile  # noqa: E402
from tennis.vision_ocr import HAVE_VISION  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
CROPS = os.path.join(FIX, "scorebug_crops")
with open(os.path.join(CROPS, "labels.json")) as _f:
    MAN = json.load(_f)
LAYOUT = BugLayout(**MAN["layout"])
ORIGIN = (MAN["crop_rect"][0], MAN["crop_rect"][1])
# The crops are pieces of video frames, so match_model/.gitignore keeps them out of git and
# a fresh copy of the project has only labels.json: then every test here is skipped.
MISSING = [d["file"] for d in MAN["crops"] if not os.path.isfile(os.path.join(CROPS, d["file"]))]


def setUpModule():
    if MISSING:
        raise unittest.SkipTest("%d of %d score board crops are not on this computer (they are kept out of git)"
                                % (len(MISSING), len(MAN["crops"])))


def crop(item):
    img = cv2.imread(os.path.join(CROPS, item["file"]))
    assert img is not None, item["file"]
    return img


def items(pred=lambda d: True):
    return [d for d in MAN["crops"] if pred(d)]


def has_points(d):
    return d["expect"] is not None and not d["seen"].startswith("no points box")


def obs_dict(o):
    return None if o is None else {"sets": o.sets, "games": o.games, "points": o.points, "server": o.server}


def layout_copy(name_suffix=""):
    """A layout of its own, so that a test starts with an empty reader state."""
    L = BugLayout(**MAN["layout"])
    L.names = [n + name_suffix for n in L.names]
    return L


class TestRightEndStructure(unittest.TestCase):
    """No digits read here: only where the board is said to end."""

    def _end(self, img):
        L = LAYOUT
        ox, oy = ORIGIN
        by0, by1, bx0, bx1 = L.y0 - oy, L.y1 - oy, L.x0 - ox, L.x1 - ox
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        g = sb._gray_dark_base(gray, gray[by0:by1, bx0:bx1])
        rows = [(a - oy, b - oy) for a, b in L.rows]
        _, blocks = sb._response(g, sb._row_h(L), rows, (bx0, by0, bx1, by1))
        gx1 = sb._games_block_x1(blocks, rows)
        self.assertIsNotNone(gx1)
        end = sb._right_end(g, by0, by1, gx1, bx1 + 1)
        return gx1, end

    def test_points_box_is_not_cut_off(self):
        """The bug that made every score 0-0: the smeared edge of the yellow games cell
        passed for the right margin of the board."""
        for d in items(has_points):
            gx1, end = self._end(crop(d))
            if end is not None:             # None: no edge found, the reader goes on to the box end
                self.assertGreater(end - gx1, 20, d["file"])

    def test_falling_edge_right_after_the_bright_cell(self):
        """Same mistake on a made-up picture: cell, one smeared column, dark points box, video."""
        g = np.full((60, 80), 150, np.uint8)     # video
        g[:, :30] = 210                          # bright games cell, ends at x = 30
        g[:, 30] = 82                            # the smear, uniform and darker than the cell
        g[:, 31:60] = 32                         # dark points box
        end = sb._right_end(g, 0, 60, 30, 79)
        self.assertEqual(end, 60)                # the video starts at x = 60

    def test_board_without_points_box_still_ends_at_the_video(self):
        g = np.full((60, 80), 150, np.uint8)     # bright video right after a narrow margin
        g[:, :30] = 210
        g[:, 30:33] = 35
        self.assertEqual(sb._right_end(g, 0, 60, 30, 79), 33)

    def test_step_down_later_on_still_counts(self):
        """Video darker than the board: a step down far from the cell is the end of the board."""
        g = np.full((60, 80), 8, np.uint8)
        g[:, :30] = 210
        g[:, 30:60] = 40
        self.assertEqual(sb._right_end(g, 0, 60, 30, 79), 60)


class TestSmallRules(unittest.TestCase):
    def test_split_touching(self):
        ink = np.zeros((10, 20), np.uint8)
        ink[:, 1:9] = 200                                      # two blobs joined by a thin bridge at x = 9, 10
        ink[:, 11:19] = 200
        ink[4, 9:11] = 200
        parts = sb._split_touching(ink, 10, [])
        self.assertEqual(len(parts), 2)
        self.assertIn(parts[1][0], (9, 10))
        self.assertEqual(parts[0][1].shape[1] + parts[1][1].shape[1], 20)
        self.assertEqual(len(sb._split_touching(np.zeros((17, 12), np.uint8), 17, [11, 10])), 1)   # a digit stays whole
        self.assertEqual(len(sb._split_touching(np.zeros((20, 19), np.uint8), 20, [11])), 2)       # a joined 15
        self.assertEqual(len(sb._split_touching(np.zeros((20, 18), np.uint8), 20, [18, 17])), 1)   # a wide font

    def test_small_boards_are_read_enlarged(self):
        L = BugLayout(x0=64, y0=303, x1=176, y1=333, names=["A", "B"], rows=[(303, 318), (318, 333)],
                      cols=[(120, 130)], dot_rois=[(65, 303, 76, 318), (65, 318, 76, 333)])
        big = sb._reading_layout(L)
        self.assertEqual((big.x0, big.y0, big.x1, big.y1), (128, 606, 352, 666))
        self.assertEqual(big.rows, [(606, 636), (636, 666)])
        self.assertEqual(big.dot_rois[1], (130, 636, 152, 666))
        self.assertIs(sb._reading_layout(LAYOUT), LAYOUT)      # a 720p board stays as it is

    def test_points_text(self):
        for p, other, want in [("0", "15", "0"), ("15", "0", "15"), ("AD", "", "A"), ("A", "", "A"), ("AD", "40", "A"),
                               ("", "AD", "40"), ("", "15", "0"), ("A0", "", "A"), ("4D", "", "A"), ("", "A0", "40"),
                               ("4D", "30", "40"), ("3D", "40", "30"), ("D", "15", "D"), ("1D", "9", "10"), ("A0", "40", "A0")]:
            self.assertEqual(sb._points_text(p, other), want, (p, other))

    def test_can_be_score(self):
        for games, pts in [([1, 2], ["15", "30"]), ([1, 2], ["A", "40"]), ([3, 3], ["40", "A"]), ([6, 6], ["1", "6"]),
                           ([6, 6], ["12", "11"]), ([0, 0], ["0", "0"])]:
            self.assertTrue(sb._can_be_score(games, pts), (games, pts))
        for games, pts in [([1, 2], ["49", "0"]), ([1, 2], ["1", "6"]), ([1, 2], ["A", "0"]), ([1, 2], ["A", "A"])]:
            self.assertFalse(sb._can_be_score(games, pts), (games, pts))

    def test_set_is_finished(self):
        for g in ([6, 4], [7, 5], [6, 7], [0, 6], [4, 6]):
            self.assertTrue(sb._set_is_finished(g), g)
        for g in ([0, 0], [5, 5], [6, 5], [6, 6], [5, 6], [1, 2]):
            self.assertFalse(sb._set_is_finished(g), g)


class TestDebounce(unittest.TestCase):
    @staticmethod
    def _o(sets, pts):
        return ScoreObs(t=0.0, sets=sets, games=[1, 2], points=pts, server=0, conf=1.0)

    def test_finished_sets_take_the_value_most_samples_agree_on(self):
        S, bad = [[7, 6], [4, 6]], [[7, 6], [9, 6]]
        seq = [(0.0, self._o(S, ["0", "15"])), (0.5, self._o(S, ["0", "15"])), (1.0, self._o(bad, ["0", "15"])),
               (1.5, self._o(S, ["0", "15"])), (2.0, self._o(bad, ["0", "30"])), (2.5, self._o(S, ["0", "30"])),
               (3.0, None), (3.5, self._o(S, ["0", "30"]))]
        out = sb._debounce(seq)
        self.assertEqual([(o.sets, o.points) for o in out], [(S, ["0", "15"]), (S, ["0", "30"])])

    def test_a_new_set_is_not_lost_to_the_vote(self):
        one, two = [[7, 6]], [[7, 6], [6, 7]]
        seq = [(float(i), self._o(one, ["15", "0"])) for i in range(6)] + \
              [(float(i), self._o(two, ["30", "0"])) for i in range(6, 8)]
        self.assertEqual([o.sets for o in sb._debounce(seq)], [one, two])


class TestStreamReadsTwice(unittest.TestCase):
    """read_stream with the video and the one-frame reader replaced, so no video and no Vision."""

    def _run(self, frames, answers):
        """frames: one gray level per sample. answers: what read_bug says on each read."""
        L = LAYOUT
        h, w = MAN["crop_rect"][3] - MAN["crop_rect"][1], MAN["crop_rect"][2] - MAN["crop_rect"][0]
        info = mock.Mock(width=1280, height=720)
        seq = [(i * 0.5, np.full((h, w, 3), v, np.uint8)) for i, v in enumerate(frames)]
        calls = iter(answers)
        learned = []
        with mock.patch.object(sb, "probe", return_value=info), \
                mock.patch.object(sb, "iter_frames", return_value=iter(seq)), \
                mock.patch.object(sb, "read_bug", side_effect=lambda *a, **k: next(calls)), \
                mock.patch.object(sb, "_learn_pending", side_effect=lambda st: learned.append(1)):
            out = sb.read_stream("x.mp4", L)
        return out, learned

    @staticmethod
    def _o(pts):
        return ScoreObs(t=0.0, sets=[], games=[1, 2], points=list(pts), server=0, conf=1.0)

    def test_reading_from_an_animation_is_not_kept(self):
        a, wrong, right = self._o(["15", "0"]), self._o(["39", "0"]), self._o(["30", "0"])
        # the crop changes once (level 100 to 140) and then stays the same for a long time
        out, _ = self._run([100, 100, 100, 140, 140, 140, 140, 140], [a, a, wrong, right])
        self.assertEqual([o.points for o in out], [["15", "0"], ["30", "0"]])

    def test_a_steady_crop_is_read_only_twice(self):
        a = self._o(["15", "0"])
        out, _ = self._run([100] * 50, [a, a])        # a third read would raise StopIteration
        self.assertEqual(len(out), 1)

    def test_learns_only_when_two_reads_agree(self):
        a, wrong, right = self._o(["15", "0"]), self._o(["39", "0"]), self._o(["30", "0"])
        _, learned = self._run([100, 100, 100, 140, 140, 140], [a, a, wrong, right])
        self.assertEqual(len(learned), 1)             # only the two reads of a agree


@unittest.skipUnless(HAVE_VISION, "needs Apple Vision")
class TestRealCrops(unittest.TestCase):
    def _read_all(self, L, order):
        wrong = []
        for d in order:
            got = obs_dict(sb.read_bug(crop(d), L, _crop_origin=ORIGIN))
            if got != d["expect"]:
                wrong.append((d["file"], d["seen"], "got", got, sb.last_debug.get("why")))
        return wrong

    def test_every_crop_reads_as_labelled(self):
        """After one look at these pictures, as locate_bug gives the real reader: all right."""
        L = layout_copy()
        sb._STATES.pop(sb._key(L), None)
        self._read_all(L, items())
        self.assertEqual(self._read_all(L, items()), [])

    def test_cold_start_in_any_order_gives_no_wrong_score(self):
        """The reader learns digit shapes as it goes. From nothing, in any order, a picture
        may go unread, because could not tell is allowed, but a wrong score is not."""
        orders = [items(), list(reversed(items()))]
        for seed in range(6):
            o = items()
            np.random.default_rng(seed).shuffle(o)
            orders.append(o)
        L = layout_copy()
        unread = 0
        for order in orders:
            sb._STATES.pop(sb._key(L), None)
            for d in order:
                got = obs_dict(sb.read_bug(crop(d), L, _crop_origin=ORIGIN))
                if got is None and d["expect"] is not None:
                    unread += 1
                else:
                    self.assertEqual(got, d["expect"], d["file"])
        self.assertLessEqual(unread, 10 * len(orders))     # most pictures do get read

    def test_cleared_board_is_not_a_score(self):
        cleared = items(lambda d: d["seen"].startswith("cleared board"))
        self.assertGreaterEqual(len(cleared), 3)
        L = layout_copy()
        sb._STATES.pop(sb._key(L), None)
        self._read_all(L, items())            # learn names and digits first
        for d in cleared:
            self.assertIsNone(sb.read_bug(crop(d), L, _crop_origin=ORIGIN), d["file"])
            self.assertIn("no serve dot", sb.last_debug.get("why", ""), d["file"])

    def test_finished_set_without_a_dot_is_still_reported(self):
        """A cleared board with the final score of a set is kept, it can be the last score
        of a match. This match has no such picture, so the set rule is switched on by hand."""
        L = layout_copy()
        sb._STATES.pop(sb._key(L), None)
        self._read_all(L, items())
        d = items(lambda d: d["seen"].startswith("cleared board"))[0]
        with mock.patch.object(sb, "_set_is_finished", return_value=True):
            o = sb.read_bug(crop(d), L, _crop_origin=ORIGIN)
        self.assertIsNotNone(o)
        self.assertEqual(o.points, ["0", "0"])
        self.assertIsNone(o.server)

    def test_learn_false_leaves_templates_alone_until_asked(self):
        L = layout_copy()
        sb._STATES.pop(sb._key(L), None)
        st = sb._state(L)
        d = items(has_points)[0]
        o = sb.read_bug(crop(d), L, _crop_origin=ORIGIN, learn=False)
        self.assertIsNotNone(o)
        self.assertEqual(st.templates, {})
        self.assertTrue(st.pending)
        sb._learn_pending(st)
        self.assertGreater(len(st.templates), 0)
        self.assertEqual(st.pending, [])


@unittest.skipUnless(HAVE_VISION, "needs Apple Vision")
class TestOtherPictures(unittest.TestCase):
    """Real crops changed the way another video would differ. SYNTHETIC: this is not a
    second real graphics style, it only shows that nothing is tied to this video."""

    @classmethod
    def setUpClass(cls):
        cls.narrow = items(lambda d: d["expect"] is not None and not d["expect"]["sets"])   # first set, narrow board

    def _board_end(self, d):
        """First column right of the board on a first set picture (from the pictures, by eye)."""
        return (280 if has_points(d) else 251) - ORIGIN[0]

    def _check_background(self, painter):
        L = layout_copy()
        sb._STATES.pop(sb._key(L), None)
        wrong = []
        rng = np.random.default_rng(5)
        for d in self.narrow:
            img = crop(d).copy()
            painter(img[:, self._board_end(d):], rng)
            got = obs_dict(sb.read_bug(img, L, _crop_origin=ORIGIN))
            if got != d["expect"]:
                wrong.append((d["file"], d["expect"], got, sb.last_debug.get("why")))
        self.assertEqual(wrong, [])

    def test_bright_video_beside_the_board(self):
        self._check_background(lambda r, rng: r.__setitem__(Ellipsis, 225))

    def test_dark_video_beside_the_board(self):
        self._check_background(lambda r, rng: r.__setitem__(Ellipsis, 12))

    def test_blue_court_beside_the_board(self):
        self._check_background(lambda r, rng: r.__setitem__(Ellipsis, (150, 90, 30)))

    def test_lines_in_the_video_beside_the_board(self):
        def lines(r, rng):
            r[...] = (70, 110, 70)
            r[:, 10:13] = 235
            r[:, 40:42] = 235
        self._check_background(lines)

    def test_noisy_video_beside_the_board(self):
        """Busy video may hide a reading for a moment, but must never give a wrong one."""
        L = layout_copy()
        sb._STATES.pop(sb._key(L), None)
        for d in items():
            sb.read_bug(crop(d), L, _crop_origin=ORIGIN)       # learn digits on clean pictures
        rng = np.random.default_rng(7)
        seen = wrong = 0
        for d in self.narrow:
            for _ in range(4):
                img = crop(d).copy()
                r = img[:, self._board_end(d):]
                r[...] = rng.integers(0, 256, r.shape, dtype=np.uint8)
                got = obs_dict(sb.read_bug(img, L, _crop_origin=ORIGIN, learn=False))
                if got is not None:
                    seen += 1
                    wrong += got != d["expect"]
        self.assertEqual(wrong, 0)
        self.assertGreater(seen, 0)

    def test_other_size_and_place(self):
        """The same pictures at 1.5 times the size and moved: a 1080p style video."""
        f, dx, dy = 1.5, 37, -21
        oxy = (ORIGIN[0] * 3 // 2 + dx, ORIGIN[1] * 3 // 2 + dy)

        def m(x, y):
            return int(round(x * f)) + dx, int(round(y * f)) + dy
        L0 = LAYOUT
        L = BugLayout(x0=m(L0.x0, 0)[0], y0=m(0, L0.y0)[1], x1=m(L0.x1, 0)[0], y1=m(0, L0.y1)[1], names=list(L0.names),
                      rows=[(m(0, a)[1], m(0, b)[1]) for a, b in L0.rows],
                      cols=[(m(a, 0)[0], m(b, 0)[0]) for a, b in L0.cols],
                      dot_rois=[(m(a, 0)[0], m(0, b)[1], m(c, 0)[0], m(0, e)[1]) for a, b, c, e in L0.dot_rois])
        sb._STATES.pop(sb._key(L), None)
        wrong = []
        for d in items():
            img = cv2.resize(crop(d), None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
            got = obs_dict(sb.read_bug(img, L, _crop_origin=oxy))
            if got != d["expect"]:
                wrong.append((d["file"], d["expect"], got, sb.last_debug.get("why")))
        self.assertEqual(wrong, [])

    def _lower_quality(self, change, f=1.0):
        """Read all crops changed by change(), scaled by f. Returns (right, unread, wrong)."""
        def m(v):
            return int(round(v * f))
        L0 = LAYOUT
        L = BugLayout(x0=m(L0.x0), y0=m(L0.y0), x1=m(L0.x1), y1=m(L0.y1), names=list(L0.names),
                      rows=[(m(a), m(b)) for a, b in L0.rows], cols=[(m(a), m(b)) for a, b in L0.cols],
                      dot_rois=[(m(a), m(b), m(c), m(e)) for a, b, c, e in L0.dot_rois])
        sb._STATES.pop(sb._key(sb._reading_layout(L)), None)
        org = (m(ORIGIN[0]), m(ORIGIN[1]))
        for d in items():                                      # one look first, as locate_bug gives
            sb.read_bug(change(crop(d)), L, _crop_origin=org)
        right, unread, wrong = 0, [], []
        for d in items():
            got = obs_dict(sb.read_bug(change(crop(d)), L, _crop_origin=org))
            if got == d["expect"]:
                right += 1
            elif got is None:
                unread.append(d["file"])
            else:
                wrong.append((d["file"], d["expect"], got))
        return right, unread, wrong

    def test_blurred_or_compressed_video(self):
        """Blur joins the two digits of 40, 15 or AD into one blob: never a wrong score."""
        def jpeg(img, q=30):
            return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1], 1)
        for name, change in (("jpeg", jpeg), ("blur", lambda x: cv2.GaussianBlur(x, (3, 3), 0))):
            right, unread, wrong = self._lower_quality(change)
            self.assertEqual(wrong, [], name)
            self.assertLessEqual(len(unread), 6, (name, unread))

    def test_lower_picture_sizes(self):
        """About 540p, 480p and 360p. Small digits are read enlarged: never a wrong score."""
        for f in (0.75, 0.67, 0.5):
            def smaller(img, f=f):
                return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            right, unread, wrong = self._lower_quality(smaller, f)
            self.assertEqual(wrong, [], f)
            self.assertLessEqual(len(unread), 8, (f, unread))

    def test_line_inside_the_board_does_not_cut_the_points_off(self):
        """A style with a black line before a lighter points box: the line looks just like
        the end of the board, and this checks the text rule takes over from there."""
        L = layout_copy()
        sb._STATES.pop(sb._key(L), None)
        wrong, fired = [], 0
        for d in items():
            sb.read_bug(crop(d), L, _crop_origin=ORIGIN)       # learn digits on clean pictures
        for d in items(has_points):
            img = crop(d).copy()
            sets = len(d["expect"]["sets"])
            cell_x1 = {0: 248, 1: 285, 2: 322}[sets] - ORIGIN[0]
            y0, y1 = L.y0 - ORIGIN[1], L.y1 - ORIGIN[1]
            box_x1 = {0: 279, 1: 316, 2: 353}[sets] - ORIGIN[0]
            cell = img[y0:y1, cell_x1:box_x1].astype(np.int16)
            img[y0:y1, cell_x1:box_x1] = np.clip(cell + 45, 0, 255).astype(np.uint8)   # lighter box
            img[y0:y1, cell_x1:cell_x1 + 2] = 4                                        # black line
            got = obs_dict(sb.read_bug(img, L, _crop_origin=ORIGIN))
            fired += bool(sb.last_debug.get("text_past_edge"))
            if got != d["expect"]:
                wrong.append((d["file"], d["expect"], got, sb.last_debug.get("why")))
        self.assertEqual(wrong, [])
        self.assertGreater(fired, 0)                           # the rule really was needed


class TestScoresReadByEye(unittest.TestCase):
    """The score engine on the list of scores read by eye for the whole match (no video)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, "abn2023_scores_by_eye.csv")) as f:
            cls.rows = list(csv.DictReader(f))
        with open(os.path.join(FIX, "abn2023_points_by_eye.csv")) as f:
            cls.points = list(csv.DictReader(f))
        cls.obs = []
        for r in cls.rows:
            if "skip" in r["note"]:               # repeats and cleared boards: not new scores
                continue
            sets = [[int(x) for x in s.split("-")] for s in r["sets"].split()]
            pts = ["A" if r["pA"] == "AD" else r["pA"], "A" if r["pB"] == "AD" else r["pB"]]
            g = [int(r["gA"]), int(r["gB"])]
            cls.obs.append(ScoreObs(t=float(r["t"]), sets=sets, games=g, points=pts,
                                    server={"A": 0, "B": 1}.get(r["dot"]), in_tb=g == [6, 6], conf=1.0))

    def test_every_point_but_the_last_is_found(self):
        b = reconcile(self.obs, MatchFormat())
        self.assertEqual(len(self.obs), 279)
        self.assertEqual(len(self.points), 280)
        self.assertEqual(len(b), 279)             # the last point never shows on this board
        for x, p in zip(b, self.points):
            where = (x.t_end, p["n"])
            self.assertEqual(x.score_before["points"], p["score_before"], where)
            self.assertEqual(x.score_before["games"], [int(p["gA"]), int(p["gB"])], where)
            self.assertEqual(x.winner, p["winner"], where)
            self.assertEqual(x.server, p["server"], where)
            self.assertEqual(x.side, p["side"], where)

    def test_break_points_are_counted_from_the_points(self):
        """Break point count against a plain count from the rules, on the list read by eye."""
        b = reconcile(self.obs, MatchFormat())
        def is_bp(p):
            if p["tiebreak"] == "1":
                return False
            a, c = p["score_before"].split("-")
            srv, ret = (a, c) if p["server"] == "A" else (c, a)
            return ret == "AD" or (ret == "40" and srv in ("0", "15", "30"))
        want = sum(is_bp(p) for p in self.points[:279])
        self.assertEqual(sum(x.flags["break_point"] for x in b), want)
        self.assertGreater(want, 0)


@unittest.skipUnless(HAVE_VISION and os.path.exists(os.environ.get("TENNIS_MATCH2_VIDEO", "")),
                     "set TENNIS_MATCH2_VIDEO to the Halys against Barrere video file")
class TestWholeMatch(unittest.TestCase):
    def test_stream_matches_the_scores_read_by_eye(self):
        video = os.environ["TENNIS_MATCH2_VIDEO"]
        L = sb.locate_bug(video)
        self.assertIsNotNone(L)
        self.assertEqual(L.names, ["HALYS", "BARRERE"])
        got = sb.read_stream(video, L)
        with open(os.path.join(FIX, "abn2023_scores_by_eye.csv")) as f:
            real = [r for r in csv.DictReader(f) if "skip" not in r["note"]]
        self.assertEqual(len(got), len(real))
        for o, r in zip(got, real):
            sets = [[int(x) for x in s.split("-")] for s in r["sets"].split()]
            pts = ["A" if r["pA"] == "AD" else r["pA"], "A" if r["pB"] == "AD" else r["pB"]]
            self.assertLessEqual(abs(o.t - float(r["t"])), 2.0, (o, r))
            self.assertEqual((o.sets, o.games, o.points, o.server),
                             (sets, [int(r["gA"]), int(r["gB"])], pts, {"A": 0, "B": 1}[r["dot"]]), (o, r))


if __name__ == "__main__":
    unittest.main(verbosity=2)
