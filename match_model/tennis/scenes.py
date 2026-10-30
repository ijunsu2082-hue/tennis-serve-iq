"""Scene segmentation: one fast low-res scan, shot cuts and a per-video
main-camera classifier.

How the main camera is found (no training data needed):
  1. scan(): decode at 5 fps / 160x90 and keep cheap per-frame features,
     including a packed white-line mask.
  2. The densest cluster(s) of 16x9 colour thumbnails are candidate camera
     views (a broadcast keeps returning to the main camera, a fixed camera
     never leaves it). For the medoid of each candidate cluster one frame is
     decoded at 640x360 and fitted with court.fit_court; the first cluster
     whose fit shows the whole court from behind the baseline provides the
     reference homography.
  3. Court-line check: every scanned frame's white-line mask is compared
     with the reference court lines rasterised at 160x90 (on-line hit rate
     minus off-line clutter rate, best over small shifts). Frames above an
     adaptive threshold are 'main'.
  4. Median smoothing, minimum main length, gap merging, and snapping of
     boundaries to detected shot cuts.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from .contracts import CourtFit, Segment
from .court import (MODEL_LINES, fit_court, line_response, scale_H, unproject, corners_px)
from .video import iter_frames, probe

MIN_MAIN_S = 1.5
MERGE_GAP_S = 0.6
SNAP_S = 0.4
LINE_THR = 15        # top-hat threshold for the 160x90 white-line mask


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def scan(path: str, fps: float = 5.0, size: tuple[int, int] = (160, 90)) -> dict:
    """Single decoding pass. Returns a dict of per-frame arrays:
    t (N,), hsv_hist (N,256) [H16 x S4 x V4, fractions], court_frac (N,),
    thumb8 (N,8,8) gray, thumb16 (N,9,16,3) BGR, line_bits (N, ceil(w*h/8))
    packed white-line mask, cut_dist (N,) histogram distance to previous frame.
    Plus scalars fps, size, court_hue_bins.
    """
    w, h = size
    T, HH, TH8, TH16, LB = [], [], [], [], []
    for t, f in iter_frames(path, fps=fps, size=size):
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1, 2], None, [16, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        T.append(t)
        HH.append(hist)
        TH8.append(cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA))
        TH16.append(cv2.resize(f, (16, 9), interpolation=cv2.INTER_AREA))
        LB.append(np.packbits(line_response(f) > LINE_THR))
    n = len(T)
    hist = np.array(HH, np.float32).reshape(n, 256) / float(w * h) if n else np.zeros((0, 256), np.float32)
    # court colour = dominant saturated hue/sat bin over the whole video
    hs = hist.reshape(n, 16, 4, 4).sum(3) if n else np.zeros((0, 16, 4), np.float32)   # (N,H,S)
    tot = hs.sum(0)
    tot[:, 0] = 0          # ignore grey/low-saturation pixels
    if n and tot.sum() > 0:
        hb, sb = np.unravel_index(int(tot.argmax()), tot.shape)
    else:
        hb, sb = 0, 1
    hbins = [(hb - 1) % 16, hb, (hb + 1) % 16]
    court_frac = hs[:, hbins, 1:].sum((1, 2)) if n else np.zeros(0, np.float32)
    cut = np.zeros(n, np.float32)
    if n > 1:
        cut[1:] = 0.5 * np.abs(np.diff(hist, axis=0)).sum(1)
    return {
        "t": np.array(T, np.float64),
        "hsv_hist": hist,
        "court_frac": court_frac.astype(np.float32),
        "thumb8": np.array(TH8, np.uint8).reshape(n, 8, 8),
        "thumb16": np.array(TH16, np.uint8).reshape(n, 9, 16, 3),
        "line_bits": np.array(LB, np.uint8).reshape(n, -1),
        "cut_dist": cut,
        "fps": float(fps),
        "size": (int(w), int(h)),
        "court_hue_bins": hbins,
    }


# ---------------------------------------------------------------------------
# segmentation
# ---------------------------------------------------------------------------
def segment(path: str, scan: Optional[dict] = None, debug: Optional[dict] = None) -> list[Segment]:
    """Split the video into camera segments, kind 'main' or 'other'.

    debug: optional dict that receives intermediate arrays (score, threshold,
    reference H at 160x90, chosen mode time).
    """
    sc = scan if scan is not None else globals()["scan"](path)
    t = sc["t"]
    n = len(t)
    if n == 0:
        return []
    fps = sc["fps"]
    dt = 1.0 / fps
    info = probe(path)

    ref = _find_reference(path, sc, info)
    if ref is None:
        segs = [Segment(float(t[0]), float(t[-1] + dt), "other", 0.5)]
        if debug is not None:
            debug.update(score=np.zeros(n), thr=None, H160=None, mode_t=None)
        return segs
    H160, mode_t, members = ref
    score = line_scores(sc, H160)
    # threshold relative to the typical score of the verified main cluster
    m_main = float(np.median(score[members])) if len(members) else float(np.percentile(score, 90))
    thr = float(np.clip(0.75 * m_main, 0.45, 0.75))
    first = score > thr
    # appearance veto: thumbnails far from the main-camera prototype are not main
    th = sc["thumb16"].astype(np.float32)
    if first.sum() >= 3:
        proto = np.median(th[first], axis=0)
        dist = np.abs(th - proto).mean(axis=(1, 2, 3))
        d_thr = float(max(20.0, 3.0 * np.percentile(dist[first], 90)))
    else:
        dist = np.zeros(n, np.float32)
        d_thr = np.inf
    raw = first & (dist < d_thr)
    sm = _median(raw.astype(np.uint8), 5).astype(bool)
    cuts = _cuts(sc["cut_dist"])

    # runs -> segments with cleanup
    runs = _runs(sm)
    # merge short non-main gaps between main runs (no cut inside the gap)
    max_gap = int(round(MERGE_GAP_S * fps))
    changed = True
    while changed:
        changed = False
        for k in range(1, len(runs) - 1):
            a, b, v = runs[k]
            if not v and b - a <= max_gap and runs[k - 1][2] and runs[k + 1][2] \
                    and not cuts[a:b + 1].any():
                runs[k - 1:k + 2] = [(runs[k - 1][0], runs[k + 1][1], True)]
                changed = True
                break
    min_len = int(np.ceil(MIN_MAIN_S * fps))
    runs = [(a, b, v and (b - a) >= min_len) for a, b, v in runs]
    runs = _merge_same(runs)

    # snap boundaries to the nearest cut within SNAP_S
    snap = int(round(SNAP_S * fps))
    cut_idx = np.flatnonzero(cuts)
    bounds = [r[0] for r in runs[1:]]
    new_b = []
    for bnd in bounds:
        near = cut_idx[np.abs(cut_idx - bnd) <= snap] if len(cut_idx) else []
        new_b.append(int(near[np.argmin(np.abs(near - bnd))]) if len(near) else bnd)
    starts = [0] + new_b
    ends = new_b + [n]
    segs = []
    for (a, b), (_, _, v) in zip(zip(starts, ends), runs):
        if b <= a:
            continue
        s = score[a:b]
        if v:
            conf = float(np.clip(0.5 + (np.median(s) - thr) * 2.5, 0.0, 1.0))
        else:
            conf = float(np.clip(0.5 + (thr - np.median(s)) * 2.5, 0.0, 1.0))
        segs.append(Segment(float(t[a]), float(t[b - 1] + dt), "main" if v else "other", conf))
    segs = _merge_segs(segs)
    if segs:
        segs[-1].end = float(min(segs[-1].end, info.duration))
    if debug is not None:
        debug.update(score=score, thr=thr, H160=H160, mode_t=mode_t, raw=raw, cuts=cuts,
                     dist=dist, d_thr=d_thr, m_main=m_main)
    return segs


def is_main_at(segments: list[Segment], ts) -> np.ndarray:
    """Boolean array: is each time in ts inside a 'main' segment."""
    ts = np.atleast_1d(np.asarray(ts, np.float64))
    out = np.zeros(len(ts), bool)
    for s in segments:
        if s.kind == "main":
            out |= (ts >= s.start - 1e-6) & (ts < s.end - 1e-6)
    return out


# ---------------------------------------------------------------------------
# reference court
# ---------------------------------------------------------------------------
def reference_court(path: str, segments: list[Segment], n_frames: int = 9) -> Optional[CourtFit]:
    """Robust court fit for the main camera at full-frame resolution: fit
    several main frames spread over the video and take the per-keypoint
    median of the projected model points, then re-fit H from the medians."""
    info = probe(path)
    mains = [s for s in segments if s.kind == "main" and s.end - s.start >= 1.0]
    if not mains:
        return None
    mains = sorted(mains, key=lambda s: -(s.end - s.start))
    # spread picks over the video: take longest segments, then sort by time
    picks = sorted(mains[:n_frames], key=lambda s: s.start)
    ts = [0.5 * (s.start + s.end) for s in picks]
    while len(ts) < min(n_frames, 3) and mains:   # few segments: take more frames from the longest
        s = mains[0]
        ts.append(s.start + (len(ts) + 1) * (s.end - s.start) / (n_frames + 1))
    kp_m = _keypoints_m()
    pts, fits = [], []
    prev = None
    for tt in ts:
        f = _grab(path, tt, None)
        if f is None:
            continue
        fit = fit_court(f, init_H=prev) if prev is not None else fit_court(f)
        if fit is None or fit.conf < 0.6 or not _whole_court(fit):
            fit = fit_court(f)
        if fit is None or fit.conf < 0.6 or not _whole_court(fit):
            continue
        prev = fit
        fits.append(fit)
        pts.append(unproject(fit.H, kp_m))
    if not fits:
        return None
    P = np.array(pts)                         # (K, M, 2)
    med = np.median(P, axis=0)
    # drop fits far from the median (camera moved / bad fit), recompute
    dev = np.abs(P - med).max(axis=(1, 2))
    keep = dev < max(4.0, np.median(dev) * 3)
    med = np.median(P[keep], axis=0)
    Hc, _ = cv2.findHomography(kp_m.astype(np.float32), med.astype(np.float32), 0)
    H = np.linalg.inv(Hc)
    H = H / H[2, 2]
    errs = [f.err_px for f, k in zip(fits, keep) if k]
    confs = [f.conf for f, k in zip(fits, keep) if k]
    fw, fh = fits[0].frame_w, fits[0].frame_h
    return CourtFit(H=H.tolist(), frame_w=fw, frame_h=fh, err_px=float(np.median(errs)),
                    conf=float(np.median(confs) * keep.mean()))


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _keypoints_m() -> np.ndarray:
    from .contracts import HALF_LEN as L, SINGLES_HALF_W as S, DOUBLES_HALF_W as D, SERVICE_LINE as SL
    return np.array([(-D, L), (-S, L), (S, L), (D, L), (-S, SL), (0, SL), (S, SL),
                     (-S, -SL), (0, -SL), (S, -SL), (-D, -L), (-S, -L), (S, -L), (D, -L)], np.float64)


def _grab(path: str, t: float, size) -> Optional[np.ndarray]:
    for _, f in iter_frames(path, start=max(0.0, t), end=t + 0.25, size=size):
        return f.copy()
    return None


def _whole_court(fit: CourtFit) -> bool:
    """Main-camera geometry: far baseline at the top, the whole doubles court
    (allowing the near baseline to touch the bottom edge) inside the frame,
    and a reasonably large court."""
    c = corners_px(fit)
    w, h = fit.frame_w, fit.frame_h
    m = 0.02 * w
    far_ok = (c[0:2, 0] > -m).all() and (c[0:2, 0] < w + m).all() and (c[0:2, 1] > -m).all()
    near_ok = (c[2:4, 0] > -m).all() and (c[2:4, 0] < w + m).all() and (c[2:4, 1] < 1.1 * h).all()
    big = (c[2, 0] - c[3, 0]) > 0.3 * w and (c[3, 1] - c[0, 1]) > 0.3 * h
    return bool(far_ok and near_ok and big)


def _find_reference(path: str, sc: dict, info, max_modes: int = 6):
    """Densest thumbnail clusters -> verify by a court fit -> H at scan size."""
    th = sc["thumb16"].reshape(len(sc["t"]), -1).astype(np.float32)
    n = len(th)
    step = max(1, int(np.ceil(n / 2000)))      # cap the pairwise matrix at ~2000^2
    idx = np.arange(0, n, step)
    X = th[idx]
    sq = (X ** 2).sum(1)
    Dm = np.sqrt(np.maximum(sq[:, None] + sq[None] - 2 * X @ X.T, 0)) / np.sqrt(X.shape[1])
    r = max(np.percentile(Dm, 1), 1.0)
    cnt = (Dm < r).sum(1)
    alive = np.ones(len(X), bool)
    sw, sh = sc["size"]
    for _ in range(max_modes):
        c = np.where(alive, cnt, -1)
        i = int(c.argmax())
        if c[i] <= 2:
            break
        alive &= Dm[i] > 2 * r
        # members of the cluster; try the medoid and one more member
        mem = np.flatnonzero(Dm[i] < r)
        tries = [i] + [int(mem[len(mem) // 3])] if len(mem) > 3 else [i]
        for j in tries:
            tt = float(sc["t"][idx[j]])
            f = _grab(path, tt, (640, int(round(640 * info.height / info.width))))
            if f is None:
                continue
            fit = fit_court(f)
            if fit is None or fit.conf < 0.6 or not _whole_court(fit):
                continue
            H160 = scale_H(fit.H, (fit.frame_w, fit.frame_h), (sw, sh))
            return H160, tt, idx[mem]
    return None


def _raster(H160: np.ndarray, size) -> np.ndarray:
    w, h = size
    ras = np.zeros((h, w), np.uint8)
    for x0, y0, x1, y1 in MODEL_LINES:
        p = unproject(H160, np.linspace((x0, y0), (x1, y1), 60))
        cv2.polylines(ras, [np.round(p * 16).astype(np.int32)], False, 255, 1, cv2.LINE_AA, shift=4)
    return ras > 100


def line_scores(sc: dict, H160, shifts=((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1),
                                        (-2, 0), (2, 0), (-1, -1), (1, 1), (-1, 1), (1, -1))) -> np.ndarray:
    """Per-frame court-line agreement with the reference court (scan size):
    hit-rate of the (dilated) white mask on the rasterised model lines minus
    the clutter rate in a band around them; max over small image shifts."""
    w, h = sc["size"]
    ras = _raster(np.asarray(H160), (w, h))
    ras_u8 = ras.astype(np.uint8)
    on_d = cv2.dilate(ras_u8, np.ones((3, 3), np.uint8))
    band = (cv2.dilate(ras_u8, np.ones((9, 9), np.uint8)) > 0) & (on_d == 0)
    n = len(sc["t"])
    # one column per shift: on-line pixels and clutter-band pixels (normalised)
    R_on = np.stack([np.roll(np.roll(ras, dy, 0), dx, 1).ravel() for dx, dy in shifts], 1).astype(np.float32)
    R_band = np.stack([np.roll(np.roll(band, dy, 0), dx, 1).ravel() for dx, dy in shifts], 1).astype(np.float32)
    R_on /= np.maximum(R_on.sum(0, keepdims=True), 1)
    R_band /= np.maximum(R_band.sum(0, keepdims=True), 1)
    best = np.full(n, -1.0, np.float32)
    bits = sc["line_bits"]
    chunk = 1000
    for c0 in range(0, n, chunk):
        m = np.unpackbits(bits[c0:c0 + chunk], axis=1, count=w * h).reshape(-1, h, w)
        # 3x3 dilation of masks (for the on-line hit test), via shifted ORs
        md = m.copy()
        md[:, 1:, :] |= m[:, :-1, :]
        md[:, :-1, :] |= m[:, 1:, :]
        md2 = md.copy()
        md2[:, :, 1:] |= md[:, :, :-1]
        md2[:, :, :-1] |= md[:, :, 1:]
        on = md2.reshape(len(m), -1).astype(np.float32) @ R_on
        off = m.reshape(len(m), -1).astype(np.float32) @ R_band
        best[c0:c0 + chunk] = (on - off).max(1)
    return best


def _cuts(cut_dist: np.ndarray) -> np.ndarray:
    """Shot cuts: histogram distance that is large in absolute terms and a
    local peak relative to its neighbourhood."""
    n = len(cut_dist)
    out = np.zeros(n, bool)
    for i in range(1, n):
        d = cut_dist[i]
        if d < 0.25:
            continue
        lo, hi = max(1, i - 3), min(n, i + 4)
        nb = np.concatenate([cut_dist[lo:i], cut_dist[i + 1:hi]])
        if d >= nb.max(initial=0) and d > 2.0 * (np.median(nb) if len(nb) else 0) :
            out[i] = True
    return out


def _median(x: np.ndarray, k: int) -> np.ndarray:
    if len(x) < k:
        return x.copy()
    pad = k // 2
    xp = np.pad(x, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(xp, k)
    return np.median(win, axis=1).astype(x.dtype)


def _runs(b: np.ndarray) -> list[tuple[int, int, bool]]:
    out = []
    n = len(b)
    i = 0
    while i < n:
        j = i
        while j < n and b[j] == b[i]:
            j += 1
        out.append((i, j, bool(b[i])))
        i = j
    return out


def _merge_same(runs):
    out = []
    for a, b, v in runs:
        if out and out[-1][2] == v:
            out[-1] = (out[-1][0], b, v)
        else:
            out.append((a, b, v))
    return out


def _merge_segs(segs: list[Segment]) -> list[Segment]:
    out: list[Segment] = []
    for s in segs:
        if out and out[-1].kind == s.kind:
            o = out[-1]
            la, lb = o.end - o.start, s.end - s.start
            o.conf = (o.conf * la + s.conf * lb) / max(1e-9, la + lb)
            o.end = s.end
        else:
            out.append(s)
    return out
