"""Classical, fast tennis-ball tracking for the main broadcast camera.

Pipeline (per rally window, every frame at the native 25 fps):
  1. decode at a working size (default 640x360) via tennis.video.iter_frames
  2. 3-frame differencing on a "ball channel" (bright + yellow-ish: max(R,G) - 0.5 B
     blended with luma), so the static court, scoreboard and ads vanish
  3. small-blob filter (area / aspect / contrast), a static-clutter mask learned
     from the window itself (pixels that move in many frames: crowd, LED boards),
     an ROI around the court, and suppression of player bodies (YOLO bboxes)
  4. greedy constant-velocity track linking with gating, track scoring
     (length, smoothness of a local quadratic fit, travel), per-frame selection
     of the best non-conflicting track, and short-gap filling.

Pixel coordinates in the returned BallObs are full-frame (fit.frame_w x fit.frame_h).
"""
from __future__ import annotations

import time
from typing import Optional, Sequence

import cv2
import numpy as np

from .contracts import BallObs, CourtFit, HALF_LEN, DOUBLES_HALF_W
from .video import iter_frames, probe

WORK_W, WORK_H = 640, 360

# last-run diagnostics (frames, seconds, fps) for tests / reports
LAST_STATS: dict = {}


def _court_roi(fit: CourtFit, scale: float, shape: tuple[int, int],
               up_px: float = 150.0) -> np.ndarray:
    """Binary mask (work res) of the region where the ball can appear: the floor
    around the court plus a band above it (balls in the air project upward)."""
    Hi = np.linalg.inv(np.asarray(fit.H, float))
    xm, yn, yf = DOUBLES_HALF_W + 4.5, -(HALF_LEN + 6.5), HALF_LEN + 6.5
    pts = np.array([[-xm, yn], [xm, yn], [xm, yf], [-xm, yf]], float).reshape(-1, 1, 2)
    img = cv2.perspectiveTransform(pts, Hi).reshape(-1, 2) * scale
    h, w = shape
    mask = np.zeros((h, w), np.uint8)
    poly = img.copy()
    # raise the far edge to allow lobs / serve tosses above the far baseline
    top = poly.copy()
    top[2:, 1] -= up_px * scale
    top[:2, 1] -= up_px * scale * 2.0  # near side: high balls project far up the image
    hull = cv2.convexHull(np.vstack([poly, top]).astype(np.float32)).astype(np.int32)
    cv2.fillConvexPoly(mask, hull, 1)
    # broadcast overlays: score bug (bottom-left) and logos (top corners)
    mask[int(0.82 * h):, : int(0.32 * w)] = 0
    mask[: int(0.09 * h), :] = 0
    mask[int(0.90 * h):, :] = 0
    return mask.astype(bool)


def _ball_channel(frame: np.ndarray) -> np.ndarray:
    f = frame.astype(np.int16)
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    # yellow-green / white ball: high R and G; the court is blue (high B)
    ch = (np.maximum(r, g) * 2 - b + (r + g) // 2) // 2
    return np.clip(ch, 0, 255).astype(np.uint8)


def _player_boxes_at(players, t: float, scale: float, pad: float = 0.30):
    """Player bboxes (work res) interpolated at time t. players: list of PlayerObs-like
    with .t and .bbox and .end."""
    if not players:
        return []
    out = []
    for end in ("near", "far"):
        seq = [p for p in players if getattr(p, "end", None) == end]
        if not seq:
            continue
        ts = np.array([p.t for p in seq])
        i = int(np.searchsorted(ts, t))
        if i == 0:
            if ts[0] - t > 0.5:
                continue
            bb = np.array(seq[0].bbox)
        elif i >= len(seq):
            if t - ts[-1] > 0.5:
                continue
            bb = np.array(seq[-1].bbox)
        else:
            a, b = seq[i - 1], seq[i]
            if b.t - a.t > 1.0:
                continue
            w = (t - a.t) / max(1e-6, b.t - a.t)
            bb = (1 - w) * np.array(a.bbox) + w * np.array(b.bbox)
        x0, y0, x1, y1 = bb * scale
        # racket and feet extend beyond the YOLO box; the far player's racket swings
        # well outside it relative to his size
        bw, bh = (x1 - x0), (y1 - y0)
        pw = bw * (pad * 1.5 if end == "far" else pad)
        ph = bh * pad
        out.append((x0 - pw, y0 - ph, x1 + pw, y1 + 0.15 * bh))
    return out


def _far_split(fit: CourtFit) -> tuple[int, int, int, int]:
    """Full-res box (x0,y0,x1,y1) of the far half (beyond the net) where the ball is
    tiny; this region is processed at full resolution."""
    Hi = np.linalg.inv(np.asarray(fit.H, float))
    pts = np.array([[-DOUBLES_HALF_W - 3.5, 1.0], [DOUBLES_HALF_W + 3.5, 1.0],
                    [-DOUBLES_HALF_W - 3.5, HALF_LEN + 6.5],
                    [DOUBLES_HALF_W + 3.5, HALF_LEN + 6.5]], float).reshape(-1, 1, 2)
    img = cv2.perspectiveTransform(pts, Hi).reshape(-1, 2)
    x0, x1 = int(img[:, 0].min()) // 2 * 2, int(img[:, 0].max() + 1) // 2 * 2
    y1 = int(img[:2, 1].max()) // 2 * 2
    y0 = int(img[2:, 1].min() - 110) // 2 * 2
    return max(0, x0), max(0, y0), min(fit.frame_w, x1), min(fit.frame_h, y1)


def _blobs(m: np.ndarray, valid: np.ndarray, thr: int, max_area: int, max_side: int):
    mk = ((m > thr) & valid).astype(np.uint8)
    if not mk.any():
        return []
    num, lab, stats, cent = cv2.connectedComponentsWithStats(mk, connectivity=8)
    out = []
    for j in range(1, num):
        x, y, bw, bh, area = stats[j]
        if area > max_area or bw > max_side or bh > max_side:
            continue
        asp = max(bw, bh) / max(1, min(bw, bh))
        if asp > 4.5:
            continue
        peak = float(m[y:y + bh, x:x + bw].max())
        out.append((cent[j][0], cent[j][1], peak, area, asp))
    return out


def detect_candidates(path: str, start: float, end: float, fit: CourtFit, players=None,
                      size=(WORK_W, WORK_H), diff_thr: int = 14, max_cands: int = 12,
                      suppress_players: bool = True):
    """Return (times, cands) where cands[i] is an (n,4) array of
    (x, y, score, area) in FULL-FRAME px for frame i.

    Near half: processed at the working size (default 640x360).
    Far half (beyond the net, ball ~3 px): processed at full resolution."""
    w, h = size
    scale = w / float(fit.frame_w)
    roi = _court_roi(fit, scale, (h, w))
    fx0, fy0, fx1, fy1 = _far_split(fit)
    split_y = fy1                      # full-res y separating far / near processing
    # decode only the court band at full res; build both resolutions from it
    cx0, cy0, cx1, cy1 = 0, max(0, fy0) // 2 * 2, fit.frame_w, fit.frame_h
    lo_parts, hi_parts, times = [], [], []
    for t, f in iter_frames(path, fps=None, start=start, end=end, crop=(cx0, cy0, cx1, cy1)):
        hi_parts.append(_ball_channel(f[fy0 - cy0:fy1 - cy0, fx0:fx1]))
        small = cv2.resize(f, (w, int(round((cy1 - cy0) * scale))), interpolation=cv2.INTER_AREA)
        lo_parts.append(_ball_channel(small))
        times.append(t)
    n = len(times)
    if n < 3:
        return times, [np.zeros((0, 4)) for _ in range(n)]
    lo_off = int(round(cy0 * scale))
    roi_lo = roi[lo_off:lo_off + lo_parts[0].shape[0]]
    # far region at full res: its ROI is the full-res ROI rescaled
    roi_hi = cv2.resize(roi.astype(np.uint8), (fit.frame_w, fit.frame_h),
                        interpolation=cv2.INTER_NEAREST)[fy0:fy1, fx0:fx1] > 0
    roi_lo = roi_lo.copy()
    roi_lo[: max(0, int(split_y * scale) - lo_off - 2)] = False   # far part handled at hi-res

    def diffs(parts, i):
        c = parts[i].astype(np.int16)
        return np.minimum(c - parts[i - 1], c - parts[i + 1])

    # pass 1: static clutter (pixels that move in many frames: crowd, LED boards)
    step = max(1, (n - 2) // 60)
    acc_lo = np.zeros(lo_parts[0].shape, np.float32)
    acc_hi = np.zeros(hi_parts[0].shape, np.float32)
    k = 0
    for i in range(1, n - 1, step):
        acc_lo += diffs(lo_parts, i) > diff_thr
        acc_hi += diffs(hi_parts, i) > diff_thr
        k += 1
    ker = np.ones((3, 3), np.uint8)
    valid_lo = roi_lo & ~(cv2.dilate((acc_lo / k > 0.12).astype(np.uint8), ker) > 0)
    valid_hi = roi_hi & ~(cv2.dilate((acc_hi / k > 0.12).astype(np.uint8), ker) > 0)

    cands = [np.zeros((0, 4))]
    inv = 1.0 / scale
    for i in range(1, n - 1):
        t = times[i]
        rows = []
        for (x, y, peak, area, asp) in _blobs(diffs(lo_parts, i), valid_lo, diff_thr, 60, 14):
            X, Y = x * inv, (y + lo_off) * inv
            rows.append((X, Y, peak / 255.0 + min(area, 12) / 24.0 - 0.1 * max(0.0, asp - 2.5), area * inv * inv))
        for (x, y, peak, area, asp) in _blobs(diffs(hi_parts, i), valid_hi, diff_thr - 2, 80, 16):
            X, Y = x + fx0, y + fy0
            rows.append((X, Y, peak / 255.0 + min(area, 24) / 48.0 - 0.1 * max(0.0, asp - 2.5), area))
        if suppress_players and players:
            boxes = _player_boxes_at(players, t, 1.0)
            rows = [r for r in rows if not any(b[0] <= r[0] <= b[2] and b[1] <= r[1] <= b[3] for b in boxes)]
        rows.sort(key=lambda r: -r[2])
        cands.append(np.array(rows[:max_cands], float).reshape(-1, 4))
    cands.append(np.zeros((0, 4)))
    return times, cands


def _link(cands: Sequence[np.ndarray], max_miss: int = 3, gate0: float = 16.0):
    """Greedy constant-velocity linking. Returns list of tracks; each track is a
    list of (frame_idx, x, y, score)."""
    active, done = [], []
    for i, c in enumerate(cands):
        used = np.zeros(len(c), bool)
        # predict
        preds = []
        for tr in active:
            (i1, x1, y1, _), = tr["pts"][-1:]
            if len(tr["pts"]) >= 2:
                i0, x0, y0, _ = tr["pts"][-2]
                vx, vy = (x1 - x0) / (i1 - i0), (y1 - y0) / (i1 - i0)
            else:
                vx = vy = 0.0
            dt = i - i1
            speed = np.hypot(vx, vy)
            gate = (gate0 + 0.6 * speed) * (1 + 0.5 * (dt - 1)) if len(tr["pts"]) >= 2 else 50.0
            preds.append((x1 + vx * dt, y1 + vy * dt, gate))
        # associate greedily by distance
        pairs = []
        for ti, (px, py, gate) in enumerate(preds):
            for ci in range(len(c)):
                dd = np.hypot(c[ci, 0] - px, c[ci, 1] - py)
                if dd < gate:
                    pairs.append((dd - 3 * c[ci, 2], ti, ci))
        pairs.sort()
        tused = set()
        for _, ti, ci in pairs:
            if ti in tused or used[ci]:
                continue
            tused.add(ti)
            used[ci] = True
            active[ti]["pts"].append((i, c[ci, 0], c[ci, 1], c[ci, 2]))
        nxt = []
        for ti, tr in enumerate(active):
            if i - tr["pts"][-1][0] > max_miss:
                done.append(tr)
            else:
                nxt.append(tr)
        active = nxt
        for ci in range(len(c)):
            if not used[ci]:
                active.append({"pts": [(i, c[ci, 0], c[ci, 1], c[ci, 2])]})
    done.extend(active)
    return [tr["pts"] for tr in done]


def _track_score(pts) -> float:
    if len(pts) < 4:
        return -1.0
    a = np.array(pts)
    idx, x, y = a[:, 0], a[:, 1], a[:, 2]
    travel = np.hypot(x[-1] - x[0], y[-1] - y[0])
    steps = np.hypot(np.diff(x), np.diff(y)) / np.diff(idx)
    if travel < 12 or np.median(steps) < 1.2:
        return -1.0  # static-ish: not a ball in play
    # smoothness: residual of local quadratic fit (piecewise in chunks of 7)
    res = []
    for s in range(0, len(a), 5):
        seg = a[s:s + 7]
        if len(seg) < 4:
            continue
        for coord in (seg[:, 1], seg[:, 2]):
            p = np.polyfit(seg[:, 0], coord, 2)
            res.append(np.abs(np.polyval(p, seg[:, 0]) - coord).mean())
    rough = float(np.mean(res)) if res else 3.0
    return len(a) * (1.0 / (1.0 + rough / 2.0)) + 0.5 * a[:, 3].mean() * len(a) ** 0.5


def _select(tracks, n: int):
    scored = sorted(((s, t) for t in tracks if (s := _track_score(t)) > 1.0),
                    key=lambda z: -z[0])
    owner = np.full(n, -1)
    pos = np.full((n, 2), np.nan)
    conf = np.zeros(n)
    for k, (s, tr) in enumerate(scored):
        idx = [p[0] for p in tr]
        overlap = np.sum(owner[idx] >= 0)
        if overlap > 0.3 * len(idx):
            continue
        for p in tr:
            if owner[p[0]] < 0:
                owner[p[0]] = k
                pos[p[0]] = p[1:3]
                conf[p[0]] = min(1.0, 0.3 + s / 30.0)
    return pos, conf, owner


def _fill_gaps(pos: np.ndarray, conf: np.ndarray, max_gap: int = 6):
    n = len(pos)
    ok = ~np.isnan(pos[:, 0])
    idx = np.nonzero(ok)[0]
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= max_gap + 1:
            lo, hi = max(0, a - 3), min(n, b + 4)
            sel = [k for k in range(lo, hi) if ok[k]]
            if len(sel) >= 4:
                for c in (0, 1):
                    p = np.polyfit(sel, pos[sel, c], 2)
                    pos[a + 1:b, c] = np.polyval(p, np.arange(a + 1, b))
            else:
                for c in (0, 1):
                    pos[a + 1:b, c] = np.interp(np.arange(a + 1, b), [a, b], [pos[a, c], pos[b, c]])
            conf[a + 1:b] = 0.3
    return pos, conf


def suppress_players(times, cands, players):
    """Drop candidates inside (padded) player boxes. Lets candidate detection run
    in parallel with YOLO and be filtered afterwards."""
    if not players:
        return cands
    out = []
    for t, c in zip(times, cands):
        boxes = _player_boxes_at(players, t, 1.0)
        if len(c) and boxes:
            keep = np.ones(len(c), bool)
            for (x0, y0, x1, y1) in boxes:
                keep &= ~((c[:, 0] >= x0) & (c[:, 0] <= x1) & (c[:, 1] >= y0) & (c[:, 1] <= y1))
            c = c[keep]
        out.append(c)
    return out


def track_from_candidates(times, cands) -> list[BallObs]:
    n = len(times)
    if n == 0:
        return []
    tracks = _link(cands)
    pos, conf, _ = _select(tracks, n)
    pos, conf = _fill_gaps(pos, conf)
    return [BallObs(t=float(times[i]), xy_px=(float(pos[i, 0]), float(pos[i, 1])),
                    conf=float(conf[i]))
            for i in range(n) if not np.isnan(pos[i, 0])]


def track_ball(path: str, start: float, end: float, fit: CourtFit, players=None,
               size=(WORK_W, WORK_H)) -> list[BallObs]:
    """Track the ball in [start, end) at native fps. Returns BallObs in full-frame px.
    players (PlayerObs-like, optional) are used to suppress body/racket motion."""
    t0 = time.time()
    times, cands = detect_candidates(path, start, end, fit, players=players, size=size)
    t1 = time.time()
    out = track_from_candidates(times, cands)
    n = len(times)
    dt = time.time() - t0
    LAST_STATS.update(frames=n, seconds=dt, detect_s=t1 - t0, fps=n / max(dt, 1e-6),
                      n_obs=len(out))
    return out
