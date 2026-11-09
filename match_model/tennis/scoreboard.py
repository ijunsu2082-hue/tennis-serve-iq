"""Broadcast score-bug location and reading.

    layout = locate_bug(path)                 # BugLayout | None (no bug, e.g. fixed-camera college video)
    obs    = read_bug(frame_bgr, layout)      # ScoreObs | None (bug absent/occluded); obs.t = 0, caller fills
    seq    = read_stream(path, layout)        # debounced ScoreObs per distinct stable score state
    names  = stream_names(layout)             # [top_row_name, bottom_row_name]

How it works
------------
* locate_bug: Apple Vision OCR on ~n sampled full frames. A score bug is a pair of
  vertically stacked name tokens with digits to their right that sits at the same
  place in many frames (close-ups included), which is what separates it from an
  in-arena scoreboard (main camera only) or a name banner. The box edges are then
  refined from image structure: straight edges that are consistent across the
  frames showing the bug (nearest to the text), and for the right edge the per-frame
  structural end of the bug (Vision may miss the points digits in every sample).
* read_bug: Vision drops isolated digits, so cells are NOT read by Vision OCR on the
  bug. Each frame of the bug is turned into a "text response" image (bright-on-dark
  text via a horizontal morphological top-hat; dark-on-bright text inside highlighted
  blocks), glyphs are connected components per row, glyphs are grouped into tokens
  and tokens into columns. Highlighted blocks spanning both rows mark the current-set
  games column; columns to its left are completed sets, the column to its right is
  points. A small top-aligned glyph right of a digit is a superscript (tie-break
  points of a finished set). Glyphs are classified by normalised cross-correlation
  against per-label templates bootstrapped online: when any glyph is not a confident
  match, ONE Vision call reads a synthetic black-on-white strip of all the glyphs
  (each rescaled, generously spaced) and its labels are used only if it returns
  exactly one character per glyph.
* The right end of a narrower bug comes from image structure (_right_end), but text
  wins: one more column of score text past that end is still read. Digits joined by
  blur are split, and small bugs (rows under _MIN_ROW_H px) are read at twice the size.
* No points column: bugs hide the points at 0-0, so a bug with a serve dot and no
  points reads 0-0. A bug with no points and no dot has been cleared between games.
  That is not a score, so it reads as absent, unless the games are a finished set.
* read_stream: decodes only the bug crop at ~2 fps, reads a changed crop and the
  sample after it, then keeps that reading while the crop stays the same. It debounces
  (a state must persist >= 2 consecutive samples) and emits one ScoreObs per distinct
  stable state. Templates only learn from a reading the next read agrees with, so a
  bug caught in the middle of an animation teaches nothing. Finished sets take the
  value most samples agree on.

ScoreObs.sets follows the contracts.py example: one [row0_games, row1_games] pair
per completed set, e.g. [[7, 6], [4, 6]].
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

import cv2
import numpy as np

from .contracts import BugLayout, ScoreObs
from .video import iter_frames, probe, read_frame

POINT_STD = {"0", "15", "30", "40", "A"}
_NORM_W, _NORM_H = 14, 22          # glyph feature size
_ACCEPT_NCC = 0.85                 # template match accepted without Vision above this...
_ACCEPT_MARGIN = 0.08              # ...and this margin over the next-best label
_MAX_EXEMPLARS = 12
_MIN_ROW_H = 24.0                  # rows smaller than this (under about 540p) are read at twice the size

# ----------------------------------------------------------------------------
# per-layout reader state (templates, name masks) - not part of the contract
# ----------------------------------------------------------------------------


@dataclass
class _State:
    name_tpl: Optional[list] = None                    # [feat_row0, feat_row1]
    templates: dict = field(default_factory=dict)      # label -> list[np.ndarray feature]
    vision_calls: int = 0
    name_vision_tries: int = 0
    seen_highlight: bool = False                       # bug marks the games cell with a highlight
    pending: list = field(default_factory=list)        # (glyph, label) of the last reading, not learned yet


_STATES: dict = {}
last_debug: dict = {}
last_locate: dict = {}         # what locate_bug saw last, for checks


def _key(layout: BugLayout) -> tuple:
    return (layout.x0, layout.y0, layout.x1, layout.y1, tuple(layout.names))


def _state(layout: BugLayout) -> _State:
    k = _key(layout)
    if k not in _STATES:
        _STATES[k] = _State()
    return _STATES[k]


def _read_scale(layout: BugLayout) -> int:
    return 2 if _row_h(layout) < _MIN_ROW_H else 1


def _reading_layout(layout: BugLayout) -> BugLayout:
    """The layout read_bug really works with: small bugs are read enlarged, because
    digits only a few pixels tall get confused (a 6 for a 9) when read at their own size."""
    f = _read_scale(layout)
    if f == 1:
        return layout
    return BugLayout(x0=layout.x0 * f, y0=layout.y0 * f, x1=layout.x1 * f, y1=layout.y1 * f, names=list(layout.names),
                     rows=[(a * f, b * f) for a, b in layout.rows], cols=[(a * f, b * f) for a, b in layout.cols],
                     dot_rois=[tuple(v * f for v in r) for r in layout.dot_rois], points_first=layout.points_first,
                     points_hl=layout.points_hl)


def stream_names(layout: BugLayout) -> list[str]:
    return list(layout.names)


# ----------------------------------------------------------------------------
# geometry helpers
# ----------------------------------------------------------------------------

def _row_h(layout: BugLayout) -> float:
    return float(np.mean([b - a for a, b in layout.rows]))


def _search_rect(layout: BugLayout, W: int, H: int) -> tuple[int, int, int, int]:
    """Region read per frame: the bug box plus a 2 px margin. The box is the widest
    extent seen while locating; narrower states are handled by _right_end().
    All coordinates are even: ffmpeg's crop on yuv420 video rounds odd offsets /
    sizes, which would shear the rawvideo rows read by iter_frames."""
    x0 = max(0, layout.x0 - 2) & ~1
    y0 = max(0, layout.y0 - 2) & ~1
    x1 = min(W & ~1, (layout.x1 + 3) & ~1)
    y1 = min(H & ~1, (layout.y1 + 3) & ~1)
    return (x0, y0, x1, y1)


def _right_end(g: np.ndarray, by0: int, by1: int, x_from: int, x_to: int) -> Optional[int]:
    """Per-frame right end of the bug (exclusive, local coords): the first column
    (scanning right from x_from) that is uniform and dark - the box's right margin -
    and differs with a consistent sign from the next column over the full box
    height. None = no such edge (e.g. the video next to the bug is as dark).

    x_from is normally the right edge of the highlighted games cell, and video blur
    smears that edge over a few columns. A smeared column is dark, uniform and
    brighter than the next one, which is what a margin looks like, but the bug can
    go on with a points cell after it. So just right of x_from a step down is the
    end of the cell, not the end of the bug. A step up still counts there: it is
    the video starting right after the cell, on bugs that have no points cell."""
    ys = slice(by0 + 3, by1 - 3)
    x_to = min(x_to, g.shape[1] - 1)
    x_from = max(0, x_from)
    x_settled = x_from + max(2, (by1 - by0) // 8)
    for x in range(x_from, x_to):
        cin = g[ys, x].astype(np.int16)
        med = np.median(cin)
        if med > 100 or np.mean(np.abs(cin - med) < 14) < 0.85:
            continue
        d = g[ys, x + 1].astype(np.int16) - cin
        if np.mean(d > 18) >= 0.8 or (x >= x_settled and np.mean(d < -18) >= 0.8):
            return x + 1
    return None


def _score_text_beyond(row_glyphs: list[list["_G"]], resp: np.ndarray, rows: list[tuple[int, int]],
                       x_end: int, x_lim: int, rh: float, thr: float) -> Optional[int]:
    """New right end if the next thing right of x_end (up to x_lim) is one more
    column of score text, else None.

    _right_end only sees image structure, and a graphics style can have a line or a
    change of colour inside the bug that passes for its end. Cutting a points column
    off is the worst mistake this reader can make, because the score then silently
    reads 0-0. So text wins over structure: glyphs in both rows that have the size
    and the height on the row of the score digits already found are part of the bug.
    So are two such glyphs in one row, for bugs that show AD with the other row blank.
    Anything else out there is taken for video and left out."""
    if x_lim - x_end < 2 or not all(row_glyphs):
        return None
    # compared with the last column found (a board can show its points cell in a
    # smaller font than the games)
    tails = [[q for q in gl if q.x1 >= max(p.x1 for p in gl) - rh] for gl in row_glyphs]
    h_ref = float(np.median([q.h for gl in tails for q in gl]))
    found = []
    for r, (a, b) in enumerate(rows):
        yc_ref = float(np.median([(q.y0 + q.y1) / 2 for q in tails[r]]))
        gl = [q for q in _glyphs_in_band(resp, (a, b), (x_end, x_lim), rh, r, thr)
              if 0.75 * h_ref <= q.h <= 1.3 * h_ref and abs((q.y0 + q.y1) / 2 - yc_ref) <= 0.2 * rh]
        toks = _tokens(gl, rh)
        if toks:
            tok = toks[0]                                  # the leftmost is the next column
            if any(q.x1 >= x_lim - 1 for q in tok):        # runs off the search area: not one cell
                return None
            found.append((min(q.x0 for q in tok), max(q.x1 for q in tok), len(tok)))
    if len(found) == 1:
        return found[0][1] + 2 if found[0][2] == 2 else None
    if len(found) != 2:
        return None
    (a0, a1, _), (b0, b1, _) = found
    if abs((a0 + a1) - (b0 + b1)) / 2 > 0.5 * rh:          # the two rows must form one column
        return None
    return max(a1, b1) + 2


def _name_x1(layout: BugLayout) -> int:
    # end of the name area = start of the first score column (minus a margin)
    if layout.cols:
        return layout.cols[0][0] - 2
    return int(layout.x0 + 0.45 * (layout.x1 - layout.x0))


def _marks_left(layout: BugLayout) -> bool:
    """True when the serve marks sit left of the names (a dot by the box edge), False
    when they sit right of them (an arrow next to the scores)."""
    if not layout.dot_rois:
        return True
    return min(r[2] for r in layout.dot_rois) <= (layout.x0 + _name_x1(layout)) / 2


def _name_span(layout: BugLayout) -> tuple[int, int]:
    """x-range of the names: right of a serve dot on the left, left of a serve arrow on
    the right. The mark comes and goes, so it is kept out of the name check."""
    nx1 = _name_x1(layout)
    if not layout.dot_rois:
        return layout.x0, nx1
    if _marks_left(layout):
        return min(r[2] for r in layout.dot_rois), nx1
    return layout.x0, min(nx1, min(r[0] for r in layout.dot_rois))


def _shifted(layout: BugLayout, dx: int) -> BugLayout:
    if not dx:
        return layout
    return BugLayout(x0=layout.x0 + dx, y0=layout.y0, x1=layout.x1 + dx, y1=layout.y1, names=list(layout.names),
                     rows=list(layout.rows), cols=[(a + dx, b + dx) for a, b in layout.cols],
                     dot_rois=[(a + dx, b, c + dx, d) for a, b, c, d in layout.dot_rois],
                     points_first=layout.points_first, points_hl=layout.points_hl)


def _frame_dx(g: np.ndarray, layout: BugLayout, ox: int, oy: int) -> int:
    """How far right (+) or left (-) of the layout the bug sits in this frame (g = the
    dark-base gray crop whose top-left is ox, oy). Some bugs are centred, so when a set
    column is added the whole bug, names and points included, moves by half a column.
    It is measured on the box's left edge: 0 while that edge is where the layout has it.
    Otherwise it is the nearest strong edge (within 1.5 row heights) that has the
    box's dark inside right after it, which skips a bright border's outer edge."""
    rh = _row_h(layout)
    gi = g[max(0, layout.y0 - oy + 2):max(0, layout.y1 - oy - 2)].astype(np.int16)
    if gi.shape[0] < 4 or gi.shape[1] < 8:
        return 0
    prof = (np.abs(np.diff(gi, axis=1)) > 20).mean(axis=0)       # edge between x and x+1
    e = layout.x0 - 1 - ox
    if 0 <= e < len(prof) and prof[max(0, e - 1):e + 2].max() >= 0.7:
        return 0
    rng = int(round(1.5 * rh))
    best = None
    for x in range(max(0, e - rng), min(len(prof) - 4, e + rng + 1)):
        if prof[x] < 0.85 or np.median(gi[:, x + 2:x + 4]) > 100:
            continue
        if best is None or abs(x - e) < abs(best - e):
            best = x
    return 0 if best is None else int(best - e)


# ----------------------------------------------------------------------------
# text response + glyphs
# ----------------------------------------------------------------------------

def _gray_dark_base(img_bgr: np.ndarray, box_gray: Optional[np.ndarray] = None) -> np.ndarray:
    """Grayscale with the bug's base background made dark (inverted for light bugs)."""
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    ref = g if box_gray is None else box_gray
    if np.median(ref) > 128:
        g = 255 - g
    return g


def _response(g: np.ndarray, rh: float, rows: list[tuple[int, int]],
              box: tuple[int, int, int, int]) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """Text response (0..255, high = ink) and the highlighted blocks (x0,y0,x1,y1),
    all in local coords. rows = local y-range per row, box = local bug box.

    Bright-on-dark ink: horizontal white top-hat with a kernel wider than any glyph
    but narrower than a highlighted cell. Highlighted cells (found per row): bright
    regions that survive a closing (fills the dark digit) and a glyph-sized opening
    (removes bright text), split at dark gap columns (so a cell is not merged with
    bright video next to the bug), and whose border ring is bright. Inside them,
    ink = darker than the cell.
    """
    kw = max(5, int(round(0.62 * rh)) | 1)
    op_h = cv2.morphologyEx(g, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 1)))
    resp = cv2.subtract(g, op_h)
    bx0, by0, bx1, by1 = box
    inner = g[by0:by1, bx0:bx1]
    lo = float(np.percentile(inner, 30))
    hi = float(np.percentile(inner, 99))
    blocks = []
    if hi - lo > 60:
        bright = (g > lo + 0.5 * (hi - lo)).astype(np.uint8)
        kc = max(3, int(round(0.45 * rh)) | 1)
        ko = max(5, int(round(0.5 * rh)) | 1)
        kcl = cv2.getStructuringElement(cv2.MORPH_RECT, (kc, kc))
        kop = cv2.getStructuringElement(cv2.MORPH_RECT, (ko, ko))
        for (ra, rb) in rows:
            band = bright[ra:rb]
            bm = cv2.morphologyEx(band, cv2.MORPH_CLOSE, kcl)
            bm = cv2.morphologyEx(bm, cv2.MORPH_OPEN, kop)
            n, lab, stats, _ = cv2.connectedComponentsWithStats(bm, connectivity=4)
            for i in range(1, n):
                x, y, w, h, a = stats[i]
                if h < 0.55 * rh:
                    continue
                colfrac = band[y:y + h, x:x + w].mean(axis=0)
                runs, start = [], None
                for j, v in enumerate(list(colfrac) + [0.0]):
                    if v >= 0.15 and start is None:
                        start = j
                    elif v < 0.15 and start is not None:
                        runs.append((start, j))
                        start = None
                for (u0, u1) in runs:
                    px, pw = x + u0, u1 - u0
                    if not (0.55 * rh <= pw <= 2.5 * rh):
                        continue
                    # vertical extent of this piece alone, from the raw bright mask
                    # (closing at the image border can grow the opened mask)
                    best_run = _longest_run(band[y:y + h, px:px + pw].mean(axis=1) >= 0.5)
                    if best_run[1] - best_run[0] < 0.55 * rh:
                        # dark text across most of a narrow cell (two digits such as
                        # 40 in a points cell) leaves too few bright rows; measure it
                        # again with the text closed over, inside this piece only
                        piece = cv2.morphologyEx(band[y:y + h, px:px + pw], cv2.MORPH_CLOSE, kcl)
                        best_run = _longest_run(piece.mean(axis=1) >= 0.5)
                    py, ph = y + best_run[0], best_run[1] - best_run[0]
                    if ph < 0.55 * rh:
                        continue
                    ring = np.concatenate([band[py + 1, px + 1:px + pw - 1], band[py + ph - 2, px + 1:px + pw - 1],
                                           band[py + 1:py + ph - 1, px + 1], band[py + 1:py + ph - 1, px + pw - 2]])
                    if ring.size == 0 or ring.mean() < 0.8:
                        continue
                    blocks.append((px, ra + py, px + pw, ra + py + ph))
    for (x0, y0, x1, y1) in blocks:
        cell = g[y0:y1, x0:x1].astype(np.int16)
        level = np.percentile(cell, 75)
        ink = np.clip(level - cell, 0, 255).astype(np.uint8)
        sub = np.zeros_like(ink)
        sub[1:-1, 1:-1] = ink[1:-1, 1:-1]
        resp[y0:y1, x0:x1] = sub
    return resp, blocks


def _longest_run(on) -> tuple[int, int]:
    """(start, end) of the longest run of True values."""
    best_run, rs0 = (0, 0), None
    for j, v in enumerate(list(on) + [False]):
        if v and rs0 is None:
            rs0 = j
        elif not v and rs0 is not None:
            if j - rs0 > best_run[1] - best_run[0]:
                best_run = (rs0, j)
            rs0 = None
    return best_run


def _games_block_x1(blocks: list[tuple[int, int, int, int]], rows: list[tuple[int, int]]) -> Optional[int]:
    """Right edge of the leftmost pair of highlighted blocks stacked in both rows."""
    def in_row(b, r):
        yc = (b[1] + b[3]) / 2
        return rows[r][0] <= yc <= rows[r][1]
    best = None
    for b0 in blocks:
        if not in_row(b0, 0):
            continue
        for b1 in blocks:
            if in_row(b1, 1) and min(b0[2], b1[2]) - max(b0[0], b1[0]) > 0.5 * min(b0[2] - b0[0], b1[2] - b1[0]):
                if best is None or b0[0] < best[0]:
                    best = (b0[0], max(b0[2], b1[2]))
    return None if best is None else best[1]


@dataclass
class _G:
    x0: int
    y0: int
    x1: int
    y1: int
    row: int
    patch: np.ndarray          # response crop (uint8)
    sup: bool = False
    label: str = "?"
    score: float = 0.0

    @property
    def h(self):
        return self.y1 - self.y0

    @property
    def w(self):
        return self.x1 - self.x0


def _feat(patch: np.ndarray) -> np.ndarray:
    p = cv2.resize(patch.astype(np.float32), (_NORM_W, _NORM_H), interpolation=cv2.INTER_AREA)
    p = p - p.mean()
    n = np.linalg.norm(p)
    return (p / n).ravel() if n > 1e-6 else p.ravel()


def _glyphs_in_band(resp: np.ndarray, band: tuple[int, int], xr: tuple[int, int], rh: float,
                    row: int, thr: float) -> list[_G]:
    """Connected components of ink in one row band (no superscript marking)."""
    y0, y1 = band
    x0, x1 = xr
    sub = resp[y0:y1, x0:x1]
    if sub.size == 0:
        return []
    m = (sub > thr).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    kept = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if h < 0.25 * rh or a < 0.02 * rh * rh:
            continue
        if h > 0.95 * rh or w > 1.2 * rh:         # cell borders / separators / blobs
            continue
        if h > 0.8 * rh and w <= 0.12 * h:         # a thin line between two cells
            continue
        if w > 2.5 * h:                            # horizontal lines
            continue
        kept.append(i)
    out = []
    for i in kept:
        x, y, w, h, a = stats[i]
        patch = sub[y:y + h, x:x + w].copy()
        patch[lab[y:y + h, x:x + w] != i] = 0
        # widths of the other glyphs of about this height: a board can show the points
        # in a smaller font than the games, and a bold 0 is not two digits
        others = [int(stats[j][2]) for j in kept if j != i and abs(int(stats[j][3]) - h) <= 0.15 * h]
        for dx, part in _split_touching(patch, h, others):
            ys = np.where(part.max(axis=1) > thr)[0]
            if ys.size == 0:
                continue
            ya, yb = int(ys[0]), int(ys[-1]) + 1
            pw = part.shape[1]
            out.append(_G(x0 + x + dx, y0 + y + ya, x0 + x + dx + pw, y0 + y + yb, row, part[ya:yb].copy()))
    return _join_broken(out)


def _join_broken(glyphs: list[_G]) -> list[_G]:
    """A bold 4 can come apart at its thin join (blur, fast decoding): a short piece
    that touches the left side of a taller glyph with no gap, lies within its height and
    makes with it a shape no wider than one digit is part of it. (A tiebreak
    superscript sits on the right of its digit, so it is not joined; two digits have a
    gap between them or make a shape wider than one digit.)"""
    out: list[_G] = []
    for q in sorted(glyphs, key=lambda g: g.x0):
        p = out[-1] if out else None
        if (p is not None and p.row == q.row and q.x0 <= p.x1 and p.h < 0.8 * q.h
                and p.y0 >= q.y0 - 1 and p.y1 <= q.y1 + 1
                and max(p.x1, q.x1) - p.x0 <= 0.95 * (max(p.y1, q.y1) - min(p.y0, q.y0))):
            X0, Y0, X1, Y1 = p.x0, min(p.y0, q.y0), max(p.x1, q.x1), max(p.y1, q.y1)
            patch = np.zeros((Y1 - Y0, X1 - X0), np.uint8)
            for g in (p, q):
                sub = patch[g.y0 - Y0:g.y1 - Y0, g.x0 - X0:g.x1 - X0]
                np.maximum(sub, g.patch, out=sub)
            out[-1] = _G(X0, Y0, X1, Y1, q.row, patch)
        else:
            out.append(q)
    return out


def _split_touching(patch: np.ndarray, h: int, others: list[int]) -> list[tuple[int, np.ndarray]]:
    """A blurred video can join two digits (40, 15, AD) into one blob, which then
    cannot be read. No single digit or letter of a score is wider than it is tall, and
    a blob is also clearly wider than the other glyphs next to it (others = the widths
    of the glyphs of about its height), so such a blob gets split where the least ink
    is, near its middle. A bold 0 or 6 can be 0.86 of its height wide, so a blob has
    to be wider than 0.88 of its height for that second rule."""
    w = patch.shape[1]
    wide = w > h or (w > 0.88 * h and others and w > 1.4 * float(np.median(others)))
    if not wide:
        return [(0, patch)]
    ink = patch.astype(np.float32).sum(axis=0)
    a, b = int(0.3 * w), max(int(0.3 * w) + 1, int(np.ceil(0.7 * w)))
    cut = a + int(np.argmin(ink[a:b]))
    return [(0, patch[:, :cut]), (cut, patch[:, cut:])]


def _mark_superscripts(glyphs: list[_G]) -> list[_G]:
    """Size filter only (drops specks and oversized blobs relative to the row's
    typical glyph height). Superscripts are decided per token in _tokens()."""
    if not glyphs:
        return glyphs
    hmax = float(np.percentile([gl.h for gl in glyphs], 90))
    keep = []
    for gl in glyphs:
        gl.sup = False
        if 0.38 * hmax <= gl.h <= 1.3 * hmax:
            keep.append(gl)
    return keep


def _is_sup(g: _G, m: _G) -> bool:
    """g is a superscript of main glyph m: clearly smaller and top-aligned with it."""
    return g.h < 0.82 * m.h and g.y0 <= m.y0 + 0.35 * m.h and g.y1 <= m.y1 - 2


def _ink_threshold(resp: np.ndarray, region: tuple[int, int, int, int]) -> float:
    """Ink threshold from the name area (always white text on the base colour)."""
    x0, y0, x1, y1 = region
    sub = resp[y0:y1, x0:x1]
    hi = float(np.percentile(sub, 99.5)) if sub.size else 0.0
    return max(40.0, 0.36 * hi)


def _tokens(glyphs: list[_G], rh: float) -> list[list[_G]]:
    """Group a row's glyphs into cell tokens by horizontal gaps and mark
    superscripts (small, top-aligned glyphs right of a main glyph)."""
    gs = sorted(glyphs, key=lambda g: g.x0)
    toks: list[list[_G]] = []
    for g in gs:
        if toks and g.x0 - max(t.x1 for t in toks[-1]) <= 0.2 * rh:
            toks[-1].append(g)
        else:
            toks.append([g])
    fixed: list[list[_G]] = []
    for t in toks:
        mains = []
        for g in t:
            m = max(mains, key=lambda q: q.h) if mains else None
            g.sup = m is not None and _is_sup(g, m)
            if not g.sup:
                mains.append(g)
        # a token of only small glyphs right next to the previous token: its superscript
        if fixed and t[0].x0 - max(g.x1 for g in fixed[-1]) <= 0.45 * rh:
            pm = [g for g in fixed[-1] if not g.sup]
            if pm and all(_is_sup(g, max(pm, key=lambda q: q.h)) for g in t):
                for g in t:
                    g.sup = True
                fixed[-1].extend(t)
                continue
        fixed.append(t)
    return fixed


def _columns(row_tokens: list[list[list[_G]]], rh: float) -> list[dict]:
    """Align tokens of both rows into columns by x overlap."""
    items = []
    for r, toks in enumerate(row_tokens):
        for t in toks:
            main = [g for g in t if not g.sup] or t
            items.append((min(g.x0 for g in main), max(g.x1 for g in main), r, t))
    items.sort(key=lambda it: (it[0] + it[1]) / 2)
    cols: list[dict] = []
    for x0, x1, r, t in items:
        c = (x0 + x1) / 2
        placed = False
        for col in cols:
            if col["x0"] - 0.25 * rh <= c <= col["x1"] + 0.25 * rh and r not in col["tok"]:
                col["tok"][r] = t
                col["x0"] = min(col["x0"], x0)
                col["x1"] = max(col["x1"], x1)
                placed = True
                break
        if not placed:
            cols.append({"x0": x0, "x1": x1, "tok": {r: t}})
    cols.sort(key=lambda c: c["x0"])
    return cols


# ----------------------------------------------------------------------------
# glyph classification (templates bootstrapped from Vision)
# ----------------------------------------------------------------------------

_VISION_MAP = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "D", "I": "1", "l": "1", "|": "1",
                             "i": "1", "!": "1", "]": "1", "[": "1", "S": "5", "s": "5", "B": "8",
                             "Z": "2", "z": "2", "g": "9", "q": "9", "G": "6", "b": "6", "T": "7",
                             "°": "", "º": "", "•": "", ".": "", ",": "", "'": "", " ": ""})


def _render_for_vision(glyphs: list[_G], target_h: int = 64) -> np.ndarray:
    """Black-on-white image of the given glyphs (in order), each scaled to the same
    height, with generous spacing; superscripts are scaled up like main glyphs."""
    parts = []
    for g in glyphs:
        p = g.patch.astype(np.float32)
        p = p / max(1.0, p.max())
        s = target_h / max(1, g.h)
        w = max(2, int(round(g.w * s)))
        p = cv2.resize(p, (w, target_h), interpolation=cv2.INTER_CUBIC)
        parts.append(p)
        parts.append(np.zeros((target_h, int(target_h * 0.12)), np.float32))
    strip = np.hstack(parts[:-1]) if parts else np.zeros((target_h, target_h), np.float32)
    img = (255 - np.clip(strip * 255 * 1.3, 0, 255)).astype(np.uint8)
    pad = target_h
    img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _vision_read(glyphs: list[_G], st: _State) -> Optional[str]:
    from .vision_ocr import ocr
    st.vision_calls += 1
    img = _render_for_vision(glyphs)
    res = ocr(img, fast=False, languages=["en-US"])
    txt = "".join(r[0] for r in sorted(res, key=lambda r: r[2][0]) if r[1] >= 0.3)
    txt = txt.translate(_VISION_MAP).upper()
    txt = re.sub(r"[^0-9AD]", "", txt)
    if len(txt) != len(glyphs):
        return None
    return txt


def _classify(g: _G, st: _State) -> tuple[str, float, float]:
    """Best template label, its NCC, and the margin to the best other label."""
    f = _feat(g.patch)
    scores = {lab: max(float(np.dot(f, e)) for e in exs) for lab, exs in st.templates.items() if exs}
    if not scores:
        return "?", 0.0, 0.0
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, bs = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else -1.0
    return best, bs, bs - second


def _add_template(g: _G, lab: str, st: _State):
    exs = st.templates.setdefault(lab, [])
    f = _feat(g.patch)
    if exs and max(float(np.dot(f, e)) for e in exs) > 0.985:
        return
    if len(exs) < _MAX_EXEMPLARS:
        exs.append(f)
    else:  # replace the most redundant exemplar
        sims = [max(float(np.dot(e, o)) for j, o in enumerate(exs) if j != i) for i, e in enumerate(exs)]
        exs[int(np.argmax(sims))] = f


def _confident(lab: str, score: float, margin: float, st: _State) -> bool:
    return (lab != "?" and score >= _ACCEPT_NCC and margin >= _ACCEPT_MARGIN
            and len(st.templates.get(lab, [])) >= 2)


def _label_glyphs(glyphs: list[_G], st: _State, allow_vision: bool = True) -> bool:
    """Label every glyph (in place). Templates first; if any glyph is not confident,
    one Vision call on a strip of ALL glyphs (reading order); its labels are only
    used when it returns exactly one character per glyph. Returns False if some
    glyph stays unlabelled. What Vision taught goes to st.pending: it is only
    learned once the whole reading has proved good (see _learn_pending)."""
    unsure = []
    for g in glyphs:
        lab, sc, mg = _classify(g, st)
        g.label, g.score = lab, sc
        if not _confident(lab, sc, mg, st):
            unsure.append(g)
    if not unsure:
        return True
    if not allow_vision:
        return False
    txt = _vision_read(glyphs, st)
    if txt is None:
        # retry with only the uncertain glyphs (shorter strips read more reliably)
        txt_u = _vision_read(unsure, st)
        if txt_u is None:
            return False
        for g, ch in zip(unsure, txt_u):
            g.label, g.score = ch, max(g.score, 0.9)
            st.pending.append((g, ch))
        return True
    for g, ch in zip(glyphs, txt):
        if g in unsure or g.label != ch:
            # Vision wins over a template unless the template is very sure
            if g in unsure or g.score < 0.95:
                g.label, g.score = ch, max(g.score, 0.9)
        if g.label == ch:
            st.pending.append((g, ch))
    return True


def _learn_pending(st: _State) -> None:
    """Add the glyphs of the last reading to the templates. One wrong template is
    costly: a rolling digit in a board animation once taught "0" the shape of a "1",
    and later a real 1 read as 0. So this is only called for a reading that has
    proved good."""
    for g, ch in st.pending:
        _add_template(g, ch, st)
    st.pending = []


def _token_text(tok: list[_G]) -> tuple[str, str]:
    main = sorted([g for g in tok if not g.sup], key=lambda g: g.x0)
    sup = sorted([g for g in tok if g.sup], key=lambda g: g.x0)
    return "".join(g.label for g in main), "".join(g.label for g in sup)


# ----------------------------------------------------------------------------
# names / dot
# ----------------------------------------------------------------------------

def _name_feats(resp: np.ndarray, layout: BugLayout, ox: int, oy: int) -> list[np.ndarray]:
    feats = []
    nx0, nx1 = _name_span(layout)
    for (a, b) in layout.rows:
        p = resp[a - oy:b - oy, nx0 - ox:nx1 - ox].astype(np.float32)
        p = cv2.resize(p, (64, 12), interpolation=cv2.INTER_AREA)     # fixed size feature
        p = p - p.mean()
        n = np.linalg.norm(p)
        feats.append((p / n).ravel() if n > 1e-6 else p.ravel())
    return feats


def _names_match(texts: list[str], names: list[str]) -> bool:
    def clean(s):
        return _letters(s.upper())
    for t, n in zip(texts, names):
        if SequenceMatcher(None, clean(t), clean(n)).ratio() < 0.75:
            return False
    return True


def _dot_server(resp: np.ndarray, layout: BugLayout, ox: int, oy: int) -> tuple[Optional[int], float]:
    if not layout.dot_rois:
        return None, 0.0
    rh = _row_h(layout)
    dot = _marks_left(layout)
    mass = []
    for (x0, y0, x1, y1) in layout.dot_rois:
        p = resp[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        # area of the largest compact blob (the dot); edge slivers / lines don't count.
        # A mark right of the names has its own tight box, found where it comes and
        # goes, and may be an arrow, which fills less of its box than a dot.
        n, lab, stats, _ = cv2.connectedComponentsWithStats((p > 60).astype(np.uint8), connectivity=8)
        best = 0.0
        for i in range(1, n):
            x, y, w, h, a = stats[i]
            if dot and w <= 0.6 * rh and h <= 0.6 * rh and a >= 0.4 * w * h and 0.5 <= w / max(1, h) <= 2.0:
                best = max(best, float(a))
            elif not dot and w <= 0.8 * rh and 0.25 * rh <= h <= 0.8 * rh and a >= 0.25 * w * h:
                best = max(best, float(a))
        mass.append(best)
    a, b = mass
    need = 0.02 * rh * rh
    if a >= need and a > 3 * b:
        return 0, min(1.0, a / (4 * need))
    if b >= need and b > 3 * a:
        return 1, min(1.0, b / (4 * need))
    return None, 0.0


def _points_text(p: str, other: str) -> str:
    """One points cell as read, tidied with what tennis allows (other = the other row).
    A D only exists in AD, and the digit templates can take a D for a 0 or an A for a 4,
    above all early on when the reader has seen few AD. With the other row blank the
    cell can only be AD. With the other row filled in, a stray D is a 0."""
    if p in ("A", "AD"):
        return "A"
    if other == "" and p in ("A0", "4D"):
        return "A"
    if p == "":
        return "40" if other in ("A", "AD", "A0", "4D") else "0"
    return p.replace("D", "0") if p[0].isdigit() else p


def _can_be_score(games: list[int], pts: list[str]) -> bool:
    """Points that tennis allows: 0/15/30/40, AD against 40, or tiebreak counts at level games."""
    if "A" in pts:
        return sorted(pts) == ["40", "A"]
    return all(p in POINT_STD for p in pts) or (games[0] == games[1] and all(p.isdigit() for p in pts))


def _finished_cell(v) -> bool:
    """One read column ((main, sup) per row) holds the final score of a set."""
    (m0, _), (m1, _) = v
    return m0.isdigit() and m1.isdigit() and _set_is_finished([int(m0), int(m1)])


def _set_is_finished(games: list[int]) -> bool:
    """These games can only be the final score of a set (6-x, 7-5, 7-6 and so on)."""
    hi, lo = max(games), min(games)
    return hi >= 6 and (hi - lo >= 2 or (hi == 7 and lo == 6))


# ----------------------------------------------------------------------------
# read one frame
# ----------------------------------------------------------------------------

def read_bug(frame_bgr: np.ndarray, layout: BugLayout, *, _crop_origin: Optional[tuple[int, int]] = None,
             allow_vision: bool = True, learn: bool = True) -> Optional[ScoreObs]:
    """Read the score bug in a full frame (or in a crop whose top-left full-frame
    position is _crop_origin - used by read_stream). Returns None if the bug is
    absent / occluded / unreadable. obs.t is 0.0; the caller fills it.
    A reading that gets this far can be a real score and teaches the templates. With
    learn=False that is left to the caller, which can call _learn_pending later."""
    f = _read_scale(layout)
    if f > 1:
        org = _crop_origin
        if org is None:
            H, W = frame_bgr.shape[:2]
            sx0, sy0, sx1, sy1 = _search_rect(layout, W, H)
            frame_bgr, org = frame_bgr[sy0:sy1, sx0:sx1], (sx0, sy0)
        big = cv2.resize(frame_bgr, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        obs = read_bug(big, _reading_layout(layout), _crop_origin=(org[0] * f, org[1] * f),
                       allow_vision=allow_vision, learn=learn)
        if "x_end" in last_debug:                 # back to this video's own pixels
            last_debug["x_end"] = last_debug["x_end"] / f
        if "cols_x" in last_debug:
            last_debug["cols_x"] = [(a / f, b / f) for a, b in last_debug["cols_x"]]
        if "dx" in last_debug:
            last_debug["dx"] = last_debug["dx"] / f
        return obs
    st = _state(layout)
    st.pending = []
    rh = _row_h(layout)
    if _crop_origin is None:
        H, W = frame_bgr.shape[:2]
        sx0, sy0, sx1, sy1 = _search_rect(layout, W, H)
        img = frame_bgr[sy0:sy1, sx0:sx1]
        ox, oy = sx0, sy0
    else:
        img = frame_bgr
        ox, oy = _crop_origin
    bx0, by0, bx1, by1 = layout.x0 - ox, layout.y0 - oy, layout.x1 - ox, layout.y1 - oy
    gfull = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # judged on the name area: a narrow bug leaves much of the box to the video
    g = _gray_dark_base(gfull, gfull[by0:by1, max(0, bx0):_name_x1(layout) - ox])
    # a bug that moves sideways when it grows (see _frame_dx) is read where it is now
    dx = _frame_dx(g, layout, ox, oy)
    if dx:
        layout = _shifted(layout, dx)
        bx0, bx1 = layout.x0 - ox, layout.x1 - ox
    rows_l = [(a - oy, b - oy) for (a, b) in layout.rows]
    resp, blocks = _response(g, rh, rows_l, (bx0, by0, bx1, by1))
    sx_start = _name_x1(layout) - ox
    gx1 = _games_block_x1(blocks, rows_l)
    x_end = _right_end(g, by0, by1, gx1 if gx1 is not None else sx_start, bx1 + 1)
    if x_end is None:
        x_end = bx1
    dbg = last_debug
    dbg.clear()
    dbg.update({"blocks": blocks, "x_end": x_end + ox, "dx": dx})

    # 1. names present?
    nf = _name_feats(resp, layout, ox, oy)
    if st.name_tpl is None:
        if st.name_vision_tries > 400:
            return None
        st.name_vision_tries += 1
        from .vision_ocr import ocr
        nx1 = _name_x1(layout)
        texts = []
        for (a, b) in layout.rows:
            crop = img[max(0, a - oy - 2):b - oy + 2, max(0, bx0):nx1 - ox]
            crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            res = ocr(crop)
            texts.append(" ".join(r[0] for r in res))
        if not _names_match(texts, layout.names):
            dbg["why"] = f"names (vision) {texts}"
            return None
        st.name_tpl = nf
    sims = [float(np.dot(a, b)) for a, b in zip(nf, st.name_tpl)]
    dbg["name_sim"] = sims
    if min(sims) < 0.6:
        dbg["why"] = "names mismatch"
        return None

    # 2. glyphs per row in the score area (left of the per-frame right end)
    nx0, nx1 = _name_span(layout)
    thr = _ink_threshold(resp, (nx0 - ox, by0, nx1 - ox, by1))

    def glyphs_left_of(xe):
        return [_mark_superscripts([q for q in _glyphs_in_band(resp, (a, b), (sx_start, xe), rh, r, thr)
                                    if q.x1 <= xe - 1]) for r, (a, b) in enumerate(rows_l)]

    row_glyphs = glyphs_left_of(x_end)
    while True:                # at most a few columns: x_end moves right at every pass
        wider = _score_text_beyond(row_glyphs, resp, rows_l, x_end, bx1 + 1, rh, thr)
        if wider is None:
            break
        x_end = wider
        dbg["x_end"] = x_end + ox
        dbg["text_past_edge"] = True
        row_glyphs = glyphs_left_of(x_end)
    row_tokens = [_tokens(gl, rh) for gl in row_glyphs]
    cols = _columns(row_tokens, rh)
    dbg["ncols"] = len(cols)
    if not cols:
        dbg["why"] = "no columns"
        return None

    # 3. label every glyph of the kept columns (templates, Vision strip fallback)
    allg = []
    for r in (0, 1):
        for c in cols:
            if r in c["tok"]:
                allg.extend(sorted(c["tok"][r], key=lambda q: (q.sup, q.x0)))
    if not _label_glyphs(allg, st, allow_vision):
        dbg["why"] = "unlabelled glyphs"
        return None
    conf = float(min((max(0.0, q.score) for q in allg), default=0.0))
    vals = []   # per column: [(main, sup) row0, row1]
    for c in cols:
        vals.append([_token_text(c["tok"][r]) if r in c["tok"] else ("", "") for r in (0, 1)])
    dbg["vals"] = vals
    dbg["cols_x"] = [(c["x0"] + ox, c["x1"] + ox) for c in cols]

    # 4. column roles: a highlighted block in both rows marks the current-set games, or
    # the points on a points-first bug (layout.points_first, found by locate_bug)
    hl_ci = None
    for ci, c in enumerate(cols):
        hit_rows = set()
        for (x0, y0, x1, y1) in blocks:
            if x0 - 2 <= c["x0"] and c["x1"] <= x1 + 2:
                for r, (ra, rb) in enumerate(layout.rows):
                    yc = (y0 + y1) / 2 + oy
                    if ra <= yc <= rb:
                        hit_rows.add(r)
        if hit_rows == {0, 1}:
            hl_ci = ci
    dbg["hl_ci"] = hl_ci
    if hl_ci is not None:
        st.seen_highlight = True
    elif st.seen_highlight:
        # this bug highlights a cell; not seeing it means a partial/animated frame
        dbg["why"] = "no highlighted cell"
        return None
    if layout.points_first:
        # NAME | points | finished sets ... | current set
        if hl_ci not in (None, 0) or len(cols) < 2:
            dbg["why"] = "points-first bug: points cell not first, or no games column"
            return None
        pts_ci, games_ci = 0, len(cols) - 1
        set_cis = list(range(1, games_ci))
    elif layout.points_hl:
        # NAME | finished sets ... | current set | [points]: the highlight is on the points
        if hl_ci is None or hl_ci == 0 or hl_ci != len(cols) - 1:
            dbg["why"] = "points-last bug: highlighted points cell not last, or no games column"
            return None
        pts_ci, games_ci = hl_ci, hl_ci - 1
        set_cis = list(range(games_ci))
    else:
        games_ci = hl_ci
        if games_ci is None:
            # fallback for bugs without a highlighted current-set cell
            games_ci = len(cols) - 1
            if len(cols) >= 2 and any(v[0] in ("15", "30", "40", "A", "AD") for v in vals[-1]):
                games_ci = len(cols) - 2
            elif len(cols) >= 2 and not all(_finished_cell(vals[ci]) for ci in range(games_ci)):
                # A column left of the games would have to be a finished set, and one is
                # not (0-1, 1-1): the last column is then the points at 0-0 of a new game,
                # and the games are the column before it. (When the columns read as
                # finished sets either way, the old reading stays: it says the same.)
                if all(_finished_cell(vals[ci]) for ci in range(len(cols) - 2)):
                    games_ci = len(cols) - 2
        if len(cols) - games_ci > 2:
            dbg["why"] = "too many columns right of games"
            return None
        pts_ci = games_ci + 1 if games_ci + 1 < len(cols) else None
        set_cis = list(range(games_ci))

    def as_int(v):
        return int(v) if v.isdigit() else None

    server, scf = _dot_server(resp, layout, ox, oy)
    sets = []
    raw_cols = []
    for ci in set_cis:
        (m0, s0), (m1, s1) = vals[ci]
        a, b = as_int(m0), as_int(m1)
        if a is None or b is None:
            dbg["why"] = f"bad set cell {vals[ci]}"
            return None
        sets.append([a, b])
        raw_cols.append((m0 + (f"^{s0}" if s0 else ""), m1 + (f"^{s1}" if s1 else "")))
    (g0, gs0), (g1, gs1) = vals[games_ci]
    games = [as_int(g0), as_int(g1)]
    if None in games or gs0 or gs1:
        dbg["why"] = f"bad games cell {vals[games_ci]}"
        return None
    raw_cols.append((g0, g1))
    if pts_ci is not None:
        (p0, _), (p1, _) = vals[pts_ci]
        pts = [_points_text(p0, p1), _points_text(p1, p0)]
        raw_cols.append((p0, p1))
    else:
        # No points column. With a serve dot this is how a bug shows 0-0. With no dot
        # it has been cleared between games, which says nothing about the points. A
        # finished set is still worth reporting: it can be the last score of a match.
        if server is None and not _set_is_finished(games):
            dbg["why"] = "no points column and no serve dot: bug cleared between games"
            return None
        pts = ["0", "0"]
    if not _can_be_score(games, pts):
        dbg["why"] = f"bad points {pts} at games {games}"
        return None

    in_tb = (games[0] == games[1] and games[0] == 6) or any(p not in POINT_STD for p in pts)
    raw = " ; ".join(
        f"{n}|" + "|".join(rc[r] for rc in raw_cols) for r, n in enumerate(layout.names))
    raw += f" ; srv={server}"
    if learn:
        _learn_pending(st)
    return ScoreObs(t=0.0, sets=sets, games=games, points=pts, server=server, in_tb=in_tb,
                    conf=round(float(conf), 3), raw=raw)


# ----------------------------------------------------------------------------
# locate
# ----------------------------------------------------------------------------

_MARK_CHARS = "<>‹›«»◀▶◄►"   # serve arrows: < > and their look-alikes


def _marks(s: str, above: bool) -> bool:
    """True when a letter of s carries an accent mark above it (Á, Š, Ö) or, with
    above=False, below it (Ç, Ş, Ą)."""
    import unicodedata
    for ch in unicodedata.normalize("NFD", s):
        c = unicodedata.combining(ch)
        if c and ((c == 230) if above else (c in (202, 220))):
            return True
    return False


def _letters(s: str) -> str:
    """The letters of s, accented ones included ("BÁEZ" -> "BÁEZ", "O'NEIL" -> "ONEIL")."""
    return "".join(ch for ch in s if ch.isalpha())


def _is_name(txt: str) -> Optional[str]:
    """The name as read, or None if the text is not a player name. A serve arrow
    that Vision reads together with the name ("LANE <") is dropped, and a team or
    country code in brackets ("LANE (UNI)") counts as part of the name. Accented
    letters count as letters, so "ŠVÁBOVÁ" and "ÁLVAREZ" are names."""
    s = txt.strip()
    while s and not s[0].isalpha():          # leading marks such as a serve dot
        s = s[1:]
    s = s.rstrip(" " + _MARK_CHARS)
    s = s.strip(" .")
    body = s.replace("(", "").replace(")", "")
    letters = _letters(body)
    if len(letters) < 2 or len(letters) < 0.8 * len(body.replace(" ", "")):
        return None
    if sum(ch.isupper() for ch in letters) < 0.6 * len(letters):
        return None
    return s


def _edge_profile(frames: list[np.ndarray], axis: int, thr: float = 20.0) -> np.ndarray:
    """Straight-edge consistency: for each line position, the fraction of pixels
    along the line with |gradient| > thr, averaged over frames. A box edge scores
    ~1 (strong along its whole length in every frame); cell borders that cover
    only part of the line, or background edges seen in a few frames, score low."""
    acc = None
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        d = np.abs(np.diff(g, axis=axis)) > thr
        prof = d.mean(axis=1 - axis)
        acc = prof if acc is None else acc + prof
    return acc / max(1, len(frames))


def _grab(path: str, t: float, fps: float) -> Optional[np.ndarray]:
    """One accurate frame near t (read_frame's 0.2 s window can miss frames in
    low-frame-rate files, so fall back to a window of two frame intervals)."""
    try:
        return read_frame(path, t)
    except ValueError:
        for _, f in iter_frames(path, start=t, end=t + 2.0 / max(1e-3, fps) + 0.05, fast=False):
            return f.copy()
    return None


def _ocr_frame(f: np.ndarray) -> list:
    """Vision OCR on a whole frame. In a small video (under about 540p) the names are
    too tiny for Vision to see, so it gets the frame at twice the size. The boxes come
    back in the frame's own pixels."""
    from .vision_ocr import ocr
    if f.shape[0] >= 540:
        return ocr(f)
    res = ocr(cv2.resize(f, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    return [(txt, c, tuple(int(round(v / 2)) for v in box)) for txt, c, box in res]


def _view(frame_bgr: np.ndarray, layout: BugLayout):
    """The text response of one full frame's bug, as read_bug makes it, with the
    layout moved to where the bug sits in this frame."""
    H, W = frame_bgr.shape[:2]
    ox, oy, sx1, sy1 = _search_rect(layout, W, H)
    gfull = cv2.cvtColor(frame_bgr[oy:sy1, ox:sx1], cv2.COLOR_BGR2GRAY)
    by0, by1 = layout.y0 - oy, layout.y1 - oy
    g = _gray_dark_base(gfull, gfull[by0:by1, max(0, layout.x0 - ox):_name_x1(layout) - ox])
    dx = _frame_dx(g, layout, ox, oy)
    lay = _shifted(layout, dx)
    rows_l = [(a - oy, b - oy) for (a, b) in lay.rows]
    resp, _ = _response(g, _row_h(layout), rows_l, (lay.x0 - ox, by0, lay.x1 - ox, by1))
    return resp, lay, ox, oy, dx


def _marks_seen(frames: list[np.ndarray], layout: BugLayout) -> bool:
    """Does the layout's serve mark show up in any of these frames?"""
    for f in frames:
        resp, lay, ox, oy, _ = _view(f, layout)
        if _dot_server(resp, lay, ox, oy)[0] is not None:
            return True
    return False


def _right_marks(frames: list[np.ndarray], layout: BugLayout) -> Optional[list[tuple[int, int, int, int]]]:
    """Serve mark boxes (one per row) for a bug that marks the server right of the
    names, next to the scores. The mark is the last thing before the scores and sits
    at one spot that shows in the top row in some frames and in the bottom row in
    others, never in both at once; the end of a name is in its row in every frame."""
    rh = _row_h(layout)
    hits = []          # (frame, row, x0, x1) in the layout's own place
    for k, f in enumerate(frames):
        resp, lay, ox, oy, dx = _view(f, layout)
        n0, n1 = lay.x0, _name_x1(lay)
        thr = _ink_threshold(resp, (n0 - ox, lay.y0 - oy, n1 - ox, lay.y1 - oy))
        for r, (a, b) in enumerate(lay.rows):
            gl = _glyphs_in_band(resp, (a - oy, b - oy), (n0 - ox, n1 - ox), rh, r, thr)
            if gl:
                q = max(gl, key=lambda q: q.x1)
                if q.x1 + ox >= n1 - 0.8 * rh:
                    hits.append((k, r, q.x0 + ox - dx, q.x1 + ox - dx))
    best = None
    for (_, _, hx0, hx1) in hits:
        same = [h for h in hits if abs(h[2] - hx0) <= max(2, 0.1 * rh) and abs(h[3] - hx1) <= max(2, 0.1 * rh)]
        frs = [h[0] for h in same]
        if {h[1] for h in same} != {0, 1} or len(frs) != len(set(frs)) or len(frs) < 3:
            continue
        if best is None or len(same) > len(best):
            best = same
    if best is None:
        return None
    mx0, mx1 = int(min(h[2] for h in best)) - 2, int(max(h[3] for h in best)) + 2
    return [(mx0, a, mx1, b) for (a, b) in layout.rows]


def _dot_like(q: "_G", letter_h: float, thr: float) -> bool:
    """A round, filled mark clearly smaller than the letters of the name (letter_h: their
    usual height): a serve dot, not part of the name. A capital letter is as tall as
    the others, and an O or a D has a hole, so neither passes."""
    if q.h > 0.75 * letter_h or q.h < 3:
        return False
    if not 0.7 <= q.w / max(1, q.h) <= 1.4:
        return False
    fill = float((q.patch > thr).mean()) if q.patch.size else 0.0
    return fill >= 0.7


def locate_bug(path: str, n: int = 24) -> Optional[BugLayout]:
    """Find the persistent two-row name+score box. None if there is no bug."""
    info = probe(path)
    W, H = info.width, info.height
    ts = [info.duration * (i + 0.5) / n for i in range(n)]
    frames, cands = {}, []
    for t in ts:
        f = _grab(path, t, info.fps)
        if f is None:
            continue
        frames[t] = f
        res = _ocr_frame(f)
        names = [(r, _is_name(r[0])) for r in res]
        names = [(r, nm) for r, nm in names if nm]
        digits = [r for r in res if re.fullmatch(r"[0-9AD°º ]{1,6}", r[0].strip()) and any(ch.isdigit() for ch in r[0])]
        for (ra, na) in names:
            for (rb, nb) in names:
                if ra is rb:
                    continue
                ax0, ay0, ax1, ay1 = ra[2]
                bx0, by0, bx1, by1 = rb[2]
                ha, hb = ay1 - ay0, by1 - by0
                if not (0.6 < ha / max(1, hb) < 1.6):
                    continue
                h = (ha + hb) / 2
                gap = by0 - ay1
                if not (-0.2 * h <= gap <= 1.6 * h):
                    continue
                if min(abs(ax1 - bx1), abs(ax0 - bx0)) > 1.2 * h and abs(ax1 - bx1) > 2.5 * h:
                    continue
                right = max(ax1, bx1)
                ndig = [d for d in digits if right - 0.3 * h <= d[2][0] <= right + 14 * h
                        and ay0 - 0.5 * h <= (d[2][1] + d[2][3]) / 2 <= by1 + 0.5 * h]
                if not ndig:
                    continue
                marked = any(r[0].rstrip().endswith(tuple(_MARK_CHARS)) for r in (ra, rb))
                cands.append({"t": t, "a": ra, "b": rb, "na": na, "nb": nb, "h": h, "digits": ndig,
                              "marked": marked})
    if not cands:
        return None
    # cluster by top-name position
    clusters: list[list[dict]] = []
    for c in cands:
        for cl in clusters:
            r = cl[0]
            if (abs(c["a"][2][1] - r["a"][2][1]) < 0.6 * r["h"] and abs(c["b"][2][1] - r["b"][2][1]) < 0.6 * r["h"]
                    and abs(c["a"][2][2] - r["a"][2][2]) < 2.0 * r["h"]):
                cl.append(c)
                break
        else:
            clusters.append([c])

    def nframes(cl):
        return len({c["t"] for c in cl})
    clusters.sort(key=lambda cl: (nframes(cl), np.mean([c["a"][2][1] for c in cl])), reverse=True)
    best = clusters[0]
    if nframes(best) < max(3, int(0.2 * len(frames))):
        return None
    # one candidate per frame
    per_t = {}
    for c in best:
        per_t.setdefault(c["t"], c)
    best = list(per_t.values())
    h = float(np.median([c["h"] for c in best]))

    def vote(key):
        vals = ["".join(ch for ch in c[key] if ch.isalpha() or ch in " .'()-").strip() for c in best]
        return max(set(vals), key=vals.count)
    names = [vote("na"), vote("nb")]
    a_y0 = int(np.median([c["a"][2][1] for c in best]))
    b_y1 = int(np.median([c["b"][2][3] for c in best]))
    name_x1 = int(np.max([max(c["a"][2][2], c["b"][2][2]) for c in best]))
    dig_x1 = int(np.max([max(d[2][2] for d in c["digits"]) for c in best]))
    vis_x0 = int(np.median([min(c["a"][2][0], c["b"][2][0]) for c in best]))   # median: robust to odd OCR boxes
    pres = [frames[c["t"]] for c in best]

    # --- refine box edges from edges consistent across all frames showing the bug
    def strongest(prof, lo, hi, prefer):
        lo, hi = max(0, int(lo)), min(len(prof), int(hi))
        if hi <= lo:
            return None
        seg = prof[lo:hi]
        # the consistent edge nearest to the text (static frame borders / pads further
        # out are just as consistent); if none is consistent enough, the best one
        idx = np.where(seg >= max(0.5, 0.6 * seg.max()))[0]
        if idx.size == 0:
            return lo + int(np.argmax(seg))
        i = idx.max() if prefer == "inner_high" else idx.min()
        return lo + int(i)

    ys0, ys1 = int(a_y0 - 0.2 * h), int(b_y1 + 0.2 * h)
    strip_y = [f[ys0:ys1] for f in pres]
    prof_x = _edge_profile(strip_y, axis=1)            # len W-1, edge between x and x+1
    left = strongest(prof_x, vis_x0 - 2.5 * h, vis_x0 - 0.05 * h, "inner_high")
    right = strongest(prof_x, dig_x1 + 0.1 * h, dig_x1 + 1.6 * h, "inner_low")
    x0 = (left + 1) if left is not None else int(vis_x0 - 0.6 * h)
    x1 = (right + 1) if right is not None else int(dig_x1 + 0.6 * h)
    strip_x = [f[:, x0:x1] for f in pres]
    prof_y = _edge_profile(strip_x, axis=0)
    # Vision's box of a name with accents above its letters (Á, Š) starts higher than the
    # letters, and on a tight board it can reach past the inner edge of the border; one
    # with marks below (Ç, Ş) ends lower. Only then does the search also look a quarter
    # of a name height inside the box. Names without such marks keep the old range (a
    # board can have an edge inside the box's lower margin, as on one college board).
    top_in = 0.25 * h if _marks(names[0], above=True) else -0.05 * h
    bot_in = 0.25 * h if _marks(names[1], above=False) else -0.05 * h
    top = strongest(prof_y, a_y0 - 1.6 * h, a_y0 + top_in, "inner_high")
    bot = strongest(prof_y, b_y1 - bot_in, b_y1 + 1.6 * h, "inner_low")
    y0 = (top + 1) if top is not None else int(a_y0 - 0.5 * h)
    y1 = (bot + 1) if bot is not None else int(b_y1 + 0.5 * h)
    ymid = int(round((y0 + y1) / 2))
    rows = [(y0, ymid), (ymid, y1)]
    rh = (y1 - y0) / 2.0

    # --- right edge, again: Vision often drops the isolated points digits in every
    # sampled frame, so dig_x1 may stop at the games column. Measure the bug's right
    # end per frame from image structure (right of the highlighted games cells) and
    # take the widest extent that several frames agree on.
    xs_end = []
    cx1 = min(W, int(dig_x1 + 5 * h))
    rl = [(a - y0, b - y0) for (a, b) in rows]
    for f in pres:
        g = _gray_dark_base(f[y0:y1, x0:cx1], cv2.cvtColor(f[y0:y1, x0:name_x1], cv2.COLOR_BGR2GRAY))
        _, blocks = _response(g, rh, rl, (0, 0, min(x1, cx1) - x0, y1 - y0))
        gx1 = _games_block_x1(blocks, rl)
        if gx1 is None:
            continue
        xe = _right_end(g, 0, y1 - y0, gx1, g.shape[1])
        if xe is not None:
            xs_end.append(x0 + xe)
    if len(xs_end) >= 3:
        xs_end.sort()
        x1 = max(x1, xs_end[-2] if len(xs_end) >= 4 else xs_end[-1])   # 2nd widest: one outlier tolerated

    # --- name start (left edge of the letters) per row from the text response; the
    # serve dot sits between the box edge and the names
    prov = BugLayout(x0=x0, y0=y0, x1=x1, y1=y1, names=names, rows=rows, cols=[], dot_rois=[])
    # each frame's sideways offset against this box (see _frame_dx); measurements are
    # taken back to the box's own place with it
    def gray(f):
        return _gray_dark_base(f, cv2.cvtColor(f[y0:y1, x0:name_x1], cv2.COLOR_BGR2GRAY))
    pres_dx = [_frame_dx(gray(f), prov, 0, 0) for f in pres]
    ref = [gray(f)[y0 + 2:y1 - 2].astype(np.int16) for f, fdx in zip(pres, pres_dx) if fdx == 0]
    if any(pres_dx) and ref and np.median(np.hstack([g[:, x0 + 1:x0 + 3] for g in ref])) > 100:
        # A bug that moves is followed by its left edge, and in a moved frame that is the
        # edge with the box's dark inside right after it (_frame_dx). Here the box's own
        # edge is the outer edge of a bright border: take its inner edge instead.
        prof = np.mean([(np.abs(np.diff(g, axis=1)) > 20).mean(axis=0) for g in ref], axis=0)
        for x in range(x0, min(len(prof) - 4, x0 + int(0.5 * rh))):
            if prof[x] >= 0.85 and np.median(np.hstack([g[:, x + 2:x + 4] for g in ref])) <= 100:
                x0 = x + 1
                prov = BugLayout(x0=x0, y0=y0, x1=x1, y1=y1, names=names, rows=rows, cols=[], dot_rois=[])
                pres_dx = [_frame_dx(gray(f), prov, 0, 0) for f in pres]
                break
    if any(pres_dx) or any(c["marked"] for c in best):
        # The bug moves, or a serve arrow sits between the names and the scores: the
        # scores start at the leftmost score text Vision saw, at the box's own place
        name_x1 = int(min(min(d[2][0] for d in c["digits"]) - fdx for c, fdx in zip(best, pres_dx))) - 3
    name_x0s = []
    for f, c, fdx in list(zip(pres, best, pres_dx))[:12]:
        g = _gray_dark_base(f[y0:y1, x0:x1], cv2.cvtColor(f[y0:y1, x0:name_x1], cv2.COLOR_BGR2GRAY))
        rl = [(a - y0, b - y0) for (a, b) in rows]
        resp, _ = _response(g, rh, rl, (0, 0, x1 - x0, y1 - y0))
        thr = _ink_threshold(resp, (0, 0, name_x1 - x0, y1 - y0))
        for r, (a, b) in enumerate(rows):
            gl = _glyphs_in_band(resp, (a - y0, b - y0), (0, name_x1 - x0 + 1), rh, r, thr)
            gl = sorted(gl, key=lambda q: -q.x1)
            if not gl:
                continue
            cur = gl[0]
            ocr_x0 = min(c["a"][2][0], c["b"][2][0])     # where Vision saw the names start
            letter_h = float(np.median([q.h for q in gl]))
            for q in gl[1:]:
                gap = cur.x0 - q.x1
                if q.h >= 0.5 * cur.h and _dot_like(q, letter_h, thr):
                    break          # the serve dot, drawn close to the name: not a letter
                # a space between two words of one name ("LANE (UNI)") is crossed while
                # Vision says the name starts further left
                if q.h >= 0.5 * cur.h and (gap <= 0.2 * rh or (gap <= 0.8 * rh and x0 + cur.x0 > ocr_x0 + 0.5 * rh)):
                    cur = q
                else:
                    break
            name_x0s.append(x0 + cur.x0 - fdx)
    name_x0 = int(np.percentile(name_x0s, 20)) if name_x0s else int(vis_x0)
    dot_rois = [(x0 + 1, a, max(x0 + 2, name_x0 - 1), b) for (a, b) in rows]

    # --- read the frames with a provisional layout: primes the reader (name template,
    # digit templates) and gives the score columns (the reading with most columns wins)
    prov = BugLayout(x0=x0, y0=y0, x1=x1, y1=y1, names=names, rows=rows,
                     cols=[(name_x1 + 2, x1)], dot_rois=dot_rois)
    _STATES.pop(_key(_reading_layout(prov)), None)
    best_cols: list = []
    pf_votes = std_votes = pl_votes = 0
    for f in pres:
        o = read_bug(f, prov)
        hl, vals = last_debug.get("hl_ci"), last_debug.get("vals")
        if hl is not None and vals:
            # which column holds 15/30/40/AD: the highlighted one, or one right of it.
            # The highlighted one as the last of several columns is the points after
            # the games (points last); otherwise the points come first.
            for ci, v in enumerate(vals):
                if any(m in ("15", "30", "40", "A", "AD") for m, _ in v):
                    if ci == hl and hl > 0 and hl == len(vals) - 1:
                        pl_votes += 1
                    else:
                        pf_votes += ci == hl
                    std_votes += ci > hl
        if o is not None and len(last_debug.get("cols_x", [])) > len(best_cols):
            best_cols = [(a - last_debug["dx"], b - last_debug["dx"]) for a, b in last_debug["cols_x"]]
    points_first = pf_votes >= 2 and pf_votes > 2 * std_votes
    points_hl = not points_first and pl_votes >= 2 and pl_votes > 2 * (std_votes + pf_votes)
    last_locate.clear()
    last_locate.update({"frames": len(pres), "frame_dx": pres_dx, "points_first_votes": pf_votes,
                        "points_right_votes": std_votes, "points_last_hl_votes": pl_votes})
    if points_first or points_hl:
        # the highlighted cell is the points: read again with those column roles
        prov.points_first, prov.points_hl = points_first, points_hl
        _STATES.pop(_key(_reading_layout(prov)), None)
        best_cols = []
        for f in pres:
            if read_bug(f, prov) is not None and len(last_debug.get("cols_x", [])) > len(best_cols):
                best_cols = [(a - last_debug["dx"], b - last_debug["dx"]) for a, b in last_debug["cols_x"]]
    cols = [(int(c0 - 0.25 * rh), int(c1 + 0.25 * rh)) for c0, c1 in best_cols]
    layout = BugLayout(x0=x0, y0=y0, x1=x1, y1=y1, names=names, rows=rows, cols=cols, dot_rois=dot_rois,
                       points_first=points_first, points_hl=points_hl)
    if cols and not _marks_seen(pres, layout):
        # no serve dot left of the names in any frame: look for a mark right of them
        right = _right_marks(pres, layout)
        if right:
            layout.dot_rois = right
    # the name area depends on cols: rebuild the name template for the final layout
    # (digit templates are kept; the state is keyed by box + names only)
    _state(_reading_layout(layout)).name_tpl = None
    for f in pres[:3]:
        if read_bug(f, layout) is not None:
            break
    return layout


# ----------------------------------------------------------------------------
# stream
# ----------------------------------------------------------------------------

def _state_key(o: ScoreObs) -> tuple:
    return (tuple(tuple(s) for s in o.sets), tuple(o.games), tuple(o.points), o.server)


def read_stream(path: str, layout: BugLayout, fps: float = 2.0, start: Optional[float] = None,
                end: Optional[float] = None, return_samples: bool = False):
    """Debounced score states over the video: one ScoreObs per distinct stable
    state (a reading must persist >= 2 consecutive samples). obs.t = time of the
    first sample showing the state. A crop that has not changed keeps its reading
    instead of being read again."""
    info = probe(path)
    sx0, sy0, sx1, sy1 = _search_rect(layout, info.width, info.height)
    bx0, by0, bx1, by1 = layout.x0 - sx0, layout.y0 - sy0, layout.x1 - sx0, layout.y1 - sy0
    prev_sig, prev_obs, settled = None, None, False
    samples: list[tuple[float, Optional[ScoreObs]]] = []
    for t, crop in iter_frames(path, fps=fps, crop=(sx0, sy0, sx1, sy1), start=start, end=end, fast=True):
        sig = cv2.resize(cv2.cvtColor(crop[by0:by1, bx0:bx1], cv2.COLOR_BGR2GRAY), None, fx=0.5, fy=0.5,
                         interpolation=cv2.INTER_AREA).astype(np.int16)
        same = prev_sig is not None and np.abs(sig - prev_sig).max() < 24
        if same and settled:
            obs = prev_obs
        else:
            # A changed crop can be the bug in the middle of an animation, and that
            # reading would be kept until the crop next changes. So the sample after
            # a change is read as well, and only then the reading is kept.
            obs = read_bug(crop, layout, _crop_origin=(sx0, sy0), learn=False)
            if obs is not None and prev_obs is not None and _state_key(obs) == _state_key(prev_obs):
                _learn_pending(_state(_reading_layout(layout)))
            # A failed reading right after a good one can be the last frame of the
            # animation (a cell half drawn): read the next sample too, so a new score
            # is not lost for as long as the crop then stays the same.
            settled = same and not (obs is None and prev_obs is not None)
            prev_sig, prev_obs = sig, obs
        samples.append((t, obs))
    out = _debounce(samples)
    return (out, samples) if return_samples else out


def _vote_on_finished_sets(samples: list[tuple[float, Optional[ScoreObs]]]) -> list[tuple[float, Optional[ScoreObs]]]:
    """The games of a finished set never change, and every sample reads them again. So
    each finished set takes the value most samples agree on. This stops a misread in
    those dim cells, common in small videos, from looking like a jump in the match."""
    votes: dict = {}
    for _, o in samples:
        if o is not None:
            for i, st in enumerate(o.sets):
                c = votes.setdefault(i, {})
                c[tuple(st)] = c.get(tuple(st), 0) + 1
    best = {i: max(c, key=c.get) for i, c in votes.items()}
    out = []
    for t, o in samples:
        if o is not None and any(tuple(st) != best[i] for i, st in enumerate(o.sets)):
            o = ScoreObs(t=o.t, sets=[list(best[i]) for i in range(len(o.sets))], games=o.games, points=o.points,
                         server=o.server, in_tb=o.in_tb, conf=o.conf, raw=o.raw)
        out.append((t, o))
    return out


def _debounce(samples: list[tuple[float, Optional[ScoreObs]]], min_run: int = 2) -> list[ScoreObs]:
    samples = _vote_on_finished_sets(samples)
    # an unknown server (dot not seen) inherits the server of the previous sample
    # with the same score, so it does not create a state of its own
    fixed, prev = [], None
    for t, o in samples:
        if o is not None and o.server is None and prev is not None and \
                _state_key(prev)[:3] == _state_key(o)[:3]:
            o = ScoreObs(t=o.t, sets=o.sets, games=o.games, points=o.points, server=prev.server,
                         in_tb=o.in_tb, conf=o.conf, raw=o.raw)
        if o is not None:
            prev = o
        fixed.append((t, o))
    samples = fixed
    out: list[ScoreObs] = []
    last_key = None
    i, n = 0, len(samples)
    while i < n:
        t, o = samples[i]
        if o is None:
            i += 1
            continue
        k = _state_key(o)
        j = i + 1
        while j < n and samples[j][1] is not None and _state_key(samples[j][1]) == k:
            j += 1
        if j - i >= min_run and k != last_key:
            run = [s[1] for s in samples[i:j]]
            best = max(run, key=lambda q: q.conf)
            out.append(ScoreObs(t=float(t), sets=[list(s) for s in o.sets], games=list(o.games),
                                points=list(o.points), server=o.server, in_tb=o.in_tb,
                                conf=round(float(np.mean([q.conf for q in run])), 3), raw=best.raw))
            last_key = k
        i = j
    return out
