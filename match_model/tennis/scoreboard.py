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
* read_stream: decodes only the bug crop at ~2 fps, re-reads only when the crop
  changes, debounces (a state must persist >= 2 consecutive samples) and emits one
  ScoreObs per distinct stable state.

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


_STATES: dict = {}
last_debug: dict = {}


def _key(layout: BugLayout) -> tuple:
    return (layout.x0, layout.y0, layout.x1, layout.y1, tuple(layout.names))


def _state(layout: BugLayout) -> _State:
    k = _key(layout)
    if k not in _STATES:
        _STATES[k] = _State()
    return _STATES[k]


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
    height. None = no such edge (e.g. the video next to the bug is as dark)."""
    ys = slice(by0 + 3, by1 - 3)
    x_to = min(x_to, g.shape[1] - 1)
    for x in range(max(0, x_from), x_to):
        cin = g[ys, x].astype(np.int16)
        med = np.median(cin)
        if med > 100 or np.mean(np.abs(cin - med) < 14) < 0.85:
            continue
        d = g[ys, x + 1].astype(np.int16) - cin
        if np.mean(d > 18) >= 0.8 or np.mean(d < -18) >= 0.8:
            return x + 1
    return None


def _name_x1(layout: BugLayout) -> int:
    # end of the name area = start of the first score column (minus a margin)
    if layout.cols:
        return layout.cols[0][0] - 2
    return int(layout.x0 + 0.45 * (layout.x1 - layout.x0))


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
                    rowon = list(band[y:y + h, px:px + pw].mean(axis=1) >= 0.5) + [False]
                    best_run, rs0 = (0, 0), None
                    for j, v in enumerate(rowon):        # longest contiguous run of rows
                        if v and rs0 is None:
                            rs0 = j
                        elif not v and rs0 is not None:
                            if j - rs0 > best_run[1] - best_run[0]:
                                best_run = (rs0, j)
                            rs0 = None
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
    out = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if h < 0.25 * rh or a < 0.02 * rh * rh:
            continue
        if h > 0.95 * rh or w > 1.2 * rh:         # cell borders / separators / blobs
            continue
        if w > 2.5 * h:                            # horizontal lines
            continue
        patch = sub[y:y + h, x:x + w].copy()
        patch[lab[y:y + h, x:x + w] != i] = 0
        out.append(_G(x0 + x, y0 + y, x0 + x + w, y0 + y + h, row, patch))
    return out


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
    glyph stays unlabelled."""
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
            _add_template(g, ch, st)
        return True
    for g, ch in zip(glyphs, txt):
        if g in unsure or g.label != ch:
            # Vision wins over a template unless the template is very sure
            if g in unsure or g.score < 0.95:
                g.label, g.score = ch, max(g.score, 0.9)
        if g.label == ch:
            _add_template(g, ch, st)
    return True


def _token_text(tok: list[_G]) -> tuple[str, str]:
    main = sorted([g for g in tok if not g.sup], key=lambda g: g.x0)
    sup = sorted([g for g in tok if g.sup], key=lambda g: g.x0)
    return "".join(g.label for g in main), "".join(g.label for g in sup)


# ----------------------------------------------------------------------------
# names / dot
# ----------------------------------------------------------------------------

def _name_feats(resp: np.ndarray, layout: BugLayout, ox: int, oy: int) -> list[np.ndarray]:
    feats = []
    nx0 = min(r[2] for r in layout.dot_rois) if layout.dot_rois else layout.x0
    nx1 = _name_x1(layout)
    for (a, b) in layout.rows:
        p = resp[a - oy:b - oy, nx0 - ox:nx1 - ox].astype(np.float32)
        p = cv2.resize(p, (64, 12), interpolation=cv2.INTER_AREA)     # fixed size feature
        p = p - p.mean()
        n = np.linalg.norm(p)
        feats.append((p / n).ravel() if n > 1e-6 else p.ravel())
    return feats


def _names_match(texts: list[str], names: list[str]) -> bool:
    def clean(s):
        return re.sub(r"[^A-Z]", "", s.upper())
    for t, n in zip(texts, names):
        if SequenceMatcher(None, clean(t), clean(n)).ratio() < 0.75:
            return False
    return True


def _dot_server(resp: np.ndarray, layout: BugLayout, ox: int, oy: int) -> tuple[Optional[int], float]:
    if not layout.dot_rois:
        return None, 0.0
    rh = _row_h(layout)
    mass = []
    for (x0, y0, x1, y1) in layout.dot_rois:
        p = resp[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        # area of the largest compact blob (the dot); edge slivers / lines don't count
        n, lab, stats, _ = cv2.connectedComponentsWithStats((p > 60).astype(np.uint8), connectivity=8)
        best = 0.0
        for i in range(1, n):
            x, y, w, h, a = stats[i]
            if w <= 0.6 * rh and h <= 0.6 * rh and a >= 0.4 * w * h and 0.5 <= w / max(1, h) <= 2.0:
                best = max(best, float(a))
        mass.append(best)
    a, b = mass
    need = 0.02 * rh * rh
    if a >= need and a > 3 * b:
        return 0, min(1.0, a / (4 * need))
    if b >= need and b > 3 * a:
        return 1, min(1.0, b / (4 * need))
    return None, 0.0


# ----------------------------------------------------------------------------
# read one frame
# ----------------------------------------------------------------------------

def read_bug(frame_bgr: np.ndarray, layout: BugLayout, *, _crop_origin: Optional[tuple[int, int]] = None,
             allow_vision: bool = True) -> Optional[ScoreObs]:
    """Read the score bug in a full frame (or in a crop whose top-left full-frame
    position is _crop_origin - used by read_stream). Returns None if the bug is
    absent / occluded / unreadable. obs.t is 0.0; the caller fills it."""
    st = _state(layout)
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
    g = _gray_dark_base(gfull, gfull[by0:by1, bx0:bx1])
    rows_l = [(a - oy, b - oy) for (a, b) in layout.rows]
    resp, blocks = _response(g, rh, rows_l, (bx0, by0, bx1, by1))
    sx_start = _name_x1(layout) - ox
    gx1 = _games_block_x1(blocks, rows_l)
    x_end = _right_end(g, by0, by1, gx1 if gx1 is not None else sx_start, bx1 + 1)
    if x_end is None:
        x_end = bx1
    dbg = last_debug
    dbg.clear()
    dbg.update({"blocks": blocks, "x_end": x_end + ox})

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
    nx0 = min(r[2] for r in layout.dot_rois) - ox if layout.dot_rois else bx0
    thr = _ink_threshold(resp, (nx0, by0, sx_start, by1))
    row_glyphs = []
    for r, (a, b) in enumerate(rows_l):
        gl = [q for q in _glyphs_in_band(resp, (a, b), (sx_start, x_end), rh, r, thr) if q.x1 <= x_end - 1]
        row_glyphs.append(_mark_superscripts(gl))
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

    # 4. column roles: a highlighted block in both rows marks the current-set games
    games_ci = None
    for ci, c in enumerate(cols):
        hit_rows = set()
        for (x0, y0, x1, y1) in blocks:
            if x0 - 2 <= c["x0"] and c["x1"] <= x1 + 2:
                for r, (ra, rb) in enumerate(layout.rows):
                    yc = (y0 + y1) / 2 + oy
                    if ra <= yc <= rb:
                        hit_rows.add(r)
        if hit_rows == {0, 1}:
            games_ci = ci
    if games_ci is not None:
        st.seen_highlight = True
    elif st.seen_highlight:
        # this bug highlights the games cell; not seeing it means a partial/animated frame
        dbg["why"] = "no highlighted games cell"
        return None
    else:
        # fallback for bugs without a highlighted current-set cell
        games_ci = len(cols) - 1
        if len(cols) >= 2 and any(v[0] in ("15", "30", "40", "A", "AD") for v in vals[-1]):
            games_ci = len(cols) - 2
    if len(cols) - games_ci > 2:
        dbg["why"] = "too many columns right of games"
        return None

    def as_int(v):
        return int(v) if v.isdigit() else None

    sets = []
    raw_cols = []
    for ci in range(games_ci):
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
    if games_ci + 1 < len(cols):
        (p0, _), (p1, _) = vals[games_ci + 1]
        pts = []
        for p, o in ((p0, p1), (p1, p0)):
            p = "A" if p in ("A", "AD") else p
            if p == "":
                p = "40" if o in ("A", "AD") else "0"
            pts.append(p)
        raw_cols.append((p0, p1))
    else:
        pts = ["0", "0"]
    for p in pts:
        if not (p == "A" or p.isdigit()):
            dbg["why"] = f"bad points {pts}"
            return None

    server, scf = _dot_server(resp, layout, ox, oy)
    in_tb = (games[0] == games[1] and games[0] == 6) or any(p not in POINT_STD for p in pts)
    raw = " ; ".join(
        f"{n}|" + "|".join(rc[r] for rc in raw_cols) for r, n in enumerate(layout.names))
    raw += f" ; srv={server}"
    return ScoreObs(t=0.0, sets=sets, games=games, points=pts, server=server, in_tb=in_tb,
                    conf=round(float(conf), 3), raw=raw)


# ----------------------------------------------------------------------------
# locate
# ----------------------------------------------------------------------------

def _is_name(txt: str) -> Optional[str]:
    s = re.sub(r"^[^A-Za-z]+", "", txt.strip())
    s = s.strip(" .")
    letters = re.sub(r"[^A-Za-z]", "", s)
    if len(letters) < 2 or len(letters) < 0.8 * len(s.replace(" ", "")):
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


def locate_bug(path: str, n: int = 24) -> Optional[BugLayout]:
    """Find the persistent two-row name+score box. None if there is no bug."""
    from .vision_ocr import ocr
    info = probe(path)
    W, H = info.width, info.height
    ts = [info.duration * (i + 0.5) / n for i in range(n)]
    frames, cands = {}, []
    for t in ts:
        f = _grab(path, t, info.fps)
        if f is None:
            continue
        frames[t] = f
        res = ocr(f)
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
                cands.append({"t": t, "a": ra, "b": rb, "na": na, "nb": nb, "h": h, "digits": ndig})
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
        vals = [re.sub(r"[^A-Za-z .'-]", "", c[key]).strip() for c in best]
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
    top = strongest(prof_y, a_y0 - 1.6 * h, a_y0 - 0.05 * h, "inner_high")
    bot = strongest(prof_y, b_y1 + 0.05 * h, b_y1 + 1.6 * h, "inner_low")
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
        g = _gray_dark_base(f[y0:y1, x0:cx1])
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
    name_x0s = []
    for f in pres[:12]:
        g = _gray_dark_base(f[y0:y1, x0:x1])
        rl = [(a - y0, b - y0) for (a, b) in rows]
        resp, _ = _response(g, rh, rl, (0, 0, x1 - x0, y1 - y0))
        thr = _ink_threshold(resp, (0, 0, name_x1 - x0, y1 - y0))
        for r, (a, b) in enumerate(rows):
            gl = _glyphs_in_band(resp, (a - y0, b - y0), (0, name_x1 - x0 + 1), rh, r, thr)
            gl = sorted(gl, key=lambda q: -q.x1)
            if not gl:
                continue
            cur = gl[0]
            for q in gl[1:]:
                if cur.x0 - q.x1 <= 0.2 * rh and q.h >= 0.5 * cur.h:
                    cur = q
                else:
                    break
            name_x0s.append(x0 + cur.x0)
    name_x0 = int(np.percentile(name_x0s, 20)) if name_x0s else int(vis_x0)
    dot_rois = [(x0 + 1, a, max(x0 + 2, name_x0 - 1), b) for (a, b) in rows]

    # --- read the frames with a provisional layout: primes the reader (name template,
    # digit templates) and gives the score columns (the reading with most columns wins)
    prov = BugLayout(x0=x0, y0=y0, x1=x1, y1=y1, names=names, rows=rows,
                     cols=[(name_x1 + 2, x1)], dot_rois=dot_rois)
    _STATES.pop(_key(prov), None)
    best_cols: list = []
    for f in pres:
        if read_bug(f, prov) is not None and len(last_debug.get("cols_x", [])) > len(best_cols):
            best_cols = last_debug["cols_x"]
    cols = [(int(c0 - 0.25 * rh), int(c1 + 0.25 * rh)) for c0, c1 in best_cols]
    layout = BugLayout(x0=x0, y0=y0, x1=x1, y1=y1, names=names, rows=rows, cols=cols, dot_rois=dot_rois)
    # the name area depends on cols: rebuild the name template for the final layout
    # (digit templates are kept; the state is keyed by box + names only)
    _state(layout).name_tpl = None
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
    first sample showing the state."""
    info = probe(path)
    sx0, sy0, sx1, sy1 = _search_rect(layout, info.width, info.height)
    bx0, by0, bx1, by1 = layout.x0 - sx0, layout.y0 - sy0, layout.x1 - sx0, layout.y1 - sy0
    prev_sig, prev_obs = None, None
    samples: list[tuple[float, Optional[ScoreObs]]] = []
    for t, crop in iter_frames(path, fps=fps, crop=(sx0, sy0, sx1, sy1), start=start, end=end, fast=True):
        sig = cv2.resize(cv2.cvtColor(crop[by0:by1, bx0:bx1], cv2.COLOR_BGR2GRAY), None, fx=0.5, fy=0.5,
                         interpolation=cv2.INTER_AREA).astype(np.int16)
        if prev_sig is not None and np.abs(sig - prev_sig).max() < 24:
            obs = prev_obs
        else:
            obs = read_bug(crop, layout, _crop_origin=(sx0, sy0))
            prev_sig, prev_obs = sig, obs
        samples.append((t, obs))
    out = _debounce(samples)
    return (out, samples) if return_samples else out


def _debounce(samples: list[tuple[float, Optional[ScoreObs]]], min_run: int = 2) -> list[ScoreObs]:
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
