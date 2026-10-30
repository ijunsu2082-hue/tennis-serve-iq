"""Classical tennis court detection: white-line mask -> Hough lines ->
combinatorial homography search against the ITF model -> sub-pixel refinement.

Conventions (see contracts.py): CourtFit.H maps image pixels (in a frame of
size frame_w x frame_h) to court metres; origin at the net centre, +x to the
right as seen from the near end, +y towards the far baseline. In the main
broadcast camera the far baseline is at the top of the image.
"""
from __future__ import annotations

from itertools import combinations
from typing import Optional, Union

import cv2
import numpy as np

from .contracts import CourtFit, HALF_LEN, SINGLES_HALF_W, DOUBLES_HALF_W, SERVICE_LINE

L, S, D, SL = HALF_LEN, SINGLES_HALF_W, DOUBLES_HALF_W, SERVICE_LINE

# Model line segments in metres: (x0, y0, x1, y1)
MODEL_LINES = np.array([
    (-D, L, D, L),        # 0 far baseline
    (-D, -L, D, -L),      # 1 near baseline
    (-S, SL, S, SL),      # 2 far service line
    (-S, -SL, S, -SL),    # 3 near service line
    (0.0, -SL, 0.0, SL),  # 4 centre service line
    (-D, -L, -D, L),      # 5 left doubles sideline
    (D, -L, D, L),        # 6 right doubles sideline
    (-S, -L, -S, L),      # 7 left singles sideline
    (S, -L, S, L),        # 8 right singles sideline
], dtype=np.float64)

# Infinite model lines used to build hypotheses (top -> bottom, left -> right in the main camera)
_MODEL_H = np.array([L, SL, -SL, -L])           # y = const
_MODEL_V = np.array([-D, -S, 0.0, S, D])        # x = const

WORK_W = 640          # resolution of the combinatorial search
REFINE_W = 1280       # resolution of the sub-pixel refinement


# ---------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------
def project(H, pts_xy_px) -> np.ndarray:
    """Image pixels -> court metres. pts: (N,2) or (2,)."""
    return _apply(np.asarray(H, np.float64), pts_xy_px)


def unproject(H, pts_m) -> np.ndarray:
    """Court metres -> image pixels."""
    return _apply(np.linalg.inv(np.asarray(H, np.float64)), pts_m)


def corners_px(fit_or_H) -> np.ndarray:
    """Doubles-court outer corners in image px: far-left, far-right, near-right, near-left."""
    H = fit_or_H.H if isinstance(fit_or_H, CourtFit) else fit_or_H
    return unproject(H, np.array([(-D, L), (D, L), (D, -L), (-D, -L)]))


def scale_H(H, src_wh, dst_wh) -> np.ndarray:
    """Re-express an image->court H defined in a src_wh frame for a dst_wh frame."""
    sx = src_wh[0] / dst_wh[0]
    sy = src_wh[1] / dst_wh[1]
    return np.asarray(H, np.float64) @ np.diag([sx, sy, 1.0])


def white_line_mask(frame_bgr: np.ndarray, thr: Optional[float] = None) -> np.ndarray:
    """Binary mask (uint8 0/255) of thin bright low-saturation structures."""
    resp = line_response(frame_bgr)
    if thr is None:
        thr = 30.0
    return (resp > thr).astype(np.uint8) * 255


def line_response(frame_bgr: np.ndarray) -> np.ndarray:
    """Top-hat response of the per-pixel channel minimum (white is high in all
    channels, the blue/green court is not). Kernel scales with frame width."""
    if frame_bgr.ndim == 3:
        b, g, r = cv2.split(frame_bgr)
        mn = cv2.min(cv2.min(b, g), r)
    else:
        mn = frame_bgr
    w = mn.shape[1]
    k = max(3, int(round(w / 1280 * 11)) | 1)
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    return cv2.morphologyEx(mn, cv2.MORPH_TOPHAT, kern)


def draw_court(frame: np.ndarray, fit: Optional[CourtFit], color=(0, 0, 255), thickness=1) -> np.ndarray:
    """Overlay the projected model lines and the outer corners on a copy of frame."""
    out = frame.copy()
    if fit is None:
        cv2.putText(out, "no court", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        return out
    h, w = out.shape[:2]
    H = scale_H(fit.H, (fit.frame_w, fit.frame_h), (w, h))
    for x0, y0, x1, y1 in MODEL_LINES:
        pts = np.linspace((x0, y0), (x1, y1), 40)
        px = unproject(H, pts)
        cv2.polylines(out, [np.round(px * 16).astype(np.int32)], False, color, thickness,
                      cv2.LINE_AA, shift=4)
    for p in corners_px(H):
        cv2.circle(out, (int(round(p[0])), int(round(p[1]))), 6, (0, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(out, f"err {fit.err_px:.2f}px conf {fit.conf:.2f}", (10, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    return out


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------
def fit_court(frame_bgr: np.ndarray, init_H: Union[None, np.ndarray, list, CourtFit] = None) -> Optional[CourtFit]:
    """Fit the ITF court model to a main-camera frame.

    init_H: optional previous fit (CourtFit, or a 3x3 image->court H in this
    frame's pixel size). When given, only a quick refinement is run; if that
    fails the full search is used.
    Returns None when no plausible court is found.
    """
    fh, fw = frame_bgr.shape[:2]
    # refinement image (at most REFINE_W wide)
    rs = min(1.0, REFINE_W / fw)
    big = frame_bgr if rs == 1.0 else cv2.resize(frame_bgr, (int(round(fw * rs)), int(round(fh * rs))),
                                                 interpolation=cv2.INTER_AREA)
    bh, bw = big.shape[:2]
    resp = cv2.GaussianBlur(line_response(big).astype(np.float32), (0, 0), 0.8)

    Hc = None  # court -> refinement-image px
    if init_H is not None:
        if isinstance(init_H, CourtFit):
            Hi = scale_H(init_H.H, (init_H.frame_w, init_H.frame_h), (bw, bh))
        else:
            Hi = scale_H(init_H, (fw, fh), (bw, bh))
        Hc0 = np.linalg.inv(Hi)
        Hc = _refine(Hc0, resp, windows=(10, 6, 4, 3))
        if Hc is not None:
            err, cov = _evaluate(Hc, resp)
            if not (cov > 0.5 and err < 3.0):
                Hc = None
    if Hc is None:
        ws = WORK_W / fw
        small = cv2.resize(frame_bgr, (WORK_W, int(round(fh * ws))), interpolation=cv2.INTER_AREA)
        cands = _search(small)
        best = None
        k = (bw / small.shape[1])
        Sk = np.diag([k, k, 1.0])
        for Hs in cands:
            Hr = _refine(Sk @ Hs, resp, windows=(8, 5, 3))
            if Hr is None:
                continue
            err, cov = _evaluate(Hr, resp)
            q = cov - 0.05 * err
            if best is None or q > best[0]:
                best = (q, Hr)
            if cov > 0.85 and err < 1.0:      # unambiguous fit: skip remaining candidates
                break
        if best is None:
            return None
        Hc = best[1]
    if not _plausible(Hc, bw, bh):
        return None
    err, cov = _evaluate(Hc, resp)
    if cov < 0.35:
        return None
    H_img = np.linalg.inv(Hc) @ np.diag([rs, rs, 1.0])   # frame px -> court
    H_img = H_img / H_img[2, 2]
    err_frame = err / rs
    conf = float(np.clip(cov * np.exp(-max(0.0, err - 1.0) / 3.0), 0.0, 1.0))
    return CourtFit(H=H_img.tolist(), frame_w=fw, frame_h=fh, err_px=float(err_frame), conf=conf)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _apply(M, pts):
    p = np.asarray(pts, np.float64)
    one = p.ndim == 1
    p = p.reshape(-1, 2)
    q = p @ M[:, :2].T + M[:, 2]
    q = q[:, :2] / q[:, 2:3]
    return q[0] if one else q


def _model_samples(step: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Points along the model lines, with unit direction and line id."""
    P, T, I = [], [], []
    for i, (x0, y0, x1, y1) in enumerate(MODEL_LINES):
        n = max(2, int(np.hypot(x1 - x0, y1 - y0) / step) + 1)
        t = np.linspace(0, 1, n)
        P.append(np.stack([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t], 1))
        d = np.array([x1 - x0, y1 - y0]) / np.hypot(x1 - x0, y1 - y0)
        T.append(np.repeat(d[None], n, 0))
        I.append(np.full(n, i))
    return np.concatenate(P), np.concatenate(T), np.concatenate(I)


_SAMPLES_COARSE = _model_samples(0.8)[0]
_SAMPLES_FINE = _model_samples(0.15)


def _hough_lines(mask: np.ndarray):
    """Detect and merge line segments. Returns list of (a, b, c, weight) with
    a*x + b*y + c = 0, (a,b) unit normal."""
    h, w = mask.shape
    segs = cv2.HoughLinesP(mask, 1, np.pi / 360, threshold=max(15, w // 32),
                           minLineLength=w // 20, maxLineGap=max(3, w // 128))
    if segs is None:
        return []
    segs = np.asarray(segs).reshape(-1, 4).astype(np.float64)
    lens = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    order = np.argsort(-lens)
    clusters = []   # each: dict(line=(a,b,c), pts=list, wt)
    for i in order:
        x0, y0, x1, y1 = segs[i]
        placed = False
        for cl in clusters:
            a, b, c = cl["line"]
            if abs(a * x0 + b * y0 + c) < 3.0 and abs(a * x1 + b * y1 + c) < 3.0:
                cl["pts"].extend([(x0, y0), (x1, y1)])
                cl["wt"] += lens[i]
                placed = True
                break
        if not placed:
            dx, dy = (x1 - x0) / lens[i], (y1 - y0) / lens[i]
            a, b = -dy, dx
            clusters.append({"line": (a, b, -(a * x0 + b * y0)), "pts": [(x0, y0), (x1, y1)], "wt": lens[i]})
    out = []
    for cl in clusters:
        pts = np.array(cl["pts"], np.float32)
        vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        a, b = -vy, vx
        out.append((float(a), float(b), float(-(a * x0 + b * y0)), cl["wt"]))
    return out


def _search(small: np.ndarray, top_k: int = 4) -> list[np.ndarray]:
    """Combinatorial hypothesis search at WORK_W. Returns up to top_k court->px homographies."""
    h, w = small.shape[:2]
    mask = white_line_mask(small)
    lines = _hough_lines(mask)
    if len(lines) < 4:
        return []
    hor, ver = [], []
    for a, b, c, wt in lines:
        # (a, b) is the unit normal; angle of the line direction from horizontal
        dang = np.degrees(np.arctan2(abs(a), abs(b)))
        if dang < 20:
            hor.append((a, b, c, wt))
        elif dang > 25:
            ver.append((a, b, c, wt))
    hor = sorted(hor, key=lambda l: -l[3])[:8]
    ver = sorted(ver, key=lambda l: -l[3])[:8]
    if len(hor) < 2 or len(ver) < 2:
        return []
    # sort horizontal by y at image centre, vertical by x at image mid-height
    hor = sorted(hor, key=lambda l: -(l[0] * w / 2 + l[2]) / l[1])
    ver = sorted(ver, key=lambda l: -(l[1] * h / 2 + l[2]) / l[0])
    Lh = np.array([l[:3] for l in hor])
    Lv = np.array([l[:3] for l in ver])
    # intersections X[i,j] of hor i with ver j
    X = np.cross(Lh[:, None, :], Lv[None, :, :])
    with np.errstate(divide="ignore", invalid="ignore"):
        X = X[..., :2] / X[..., 2:3]

    # image quads (top-left, top-right, bottom-right, bottom-left)
    hp = np.array(list(combinations(range(len(hor)), 2)))
    vp = np.array(list(combinations(range(len(ver)), 2)))
    i1, i2 = np.repeat(hp[:, 0], len(vp)), np.repeat(hp[:, 1], len(vp))
    j1, j2 = np.tile(vp[:, 0], len(hp)), np.tile(vp[:, 1], len(hp))
    Q = np.stack([X[i1, j1], X[i1, j2], X[i2, j2], X[i2, j1]], 1)       # (M,4,2)
    ok = np.isfinite(Q).all((1, 2))
    ok &= (np.abs(Q[..., 0] - w / 2) <= 1.5 * w).all(1) & (np.abs(Q[..., 1] - h / 2) <= 1.5 * h).all(1)
    Q = Q[ok]
    if len(Q) == 0:
        return []
    Hq = _square_to_quad(Q)                                             # (M,3,3)
    # model rectangles: unit square -> (xa..xb) x (ya..yb); we need its inverse
    mh = list(combinations(range(4), 2))
    mv = list(combinations(range(5), 2))
    Ainv = []
    for (a1, a2) in mh:
        for (b1, b2) in mv:
            ya, yb = _MODEL_H[a1], _MODEL_H[a2]
            xa, xb = _MODEL_V[b1], _MODEL_V[b2]
            Ainv.append([[1 / (xb - xa), 0, -xa / (xb - xa)],
                         [0, 1 / (yb - ya), -ya / (yb - ya)],
                         [0, 0, 1]])
    Ainv = np.array(Ainv)                                               # (K,3,3)
    Hs = (Hq[:, None] @ Ainv[None]).reshape(-1, 3, 3)
    ok = np.all(np.isfinite(Hs.reshape(len(Hs), -1)), 1)
    Hs = Hs[ok]
    if len(Hs) == 0:
        return []
    # plausibility of the projected outer court
    corners = np.array([(-D, L, 1), (D, L, 1), (D, -L, 1), (-D, -L, 1)]).T
    C = Hs @ corners
    Cz = C[:, 2, :]
    ok = (Cz > 0).all(1) | (Cz < 0).all(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        C = C[:, :2] / C[:, 2:3]                     # (N,2,4)
    xs, ys = C[:, 0, :], C[:, 1, :]
    top_w = xs[:, 1] - xs[:, 0]
    bot_w = xs[:, 2] - xs[:, 3]
    ok &= (top_w > 0.08 * w) & (bot_w > top_w * 0.98) & (bot_w < 3.5 * w)
    ok &= (ys[:, 3] > ys[:, 0]) & (ys[:, 2] > ys[:, 1])
    ok &= ((ys[:, 2] + ys[:, 3]) / 2 - (ys[:, 0] + ys[:, 1]) / 2) > 0.2 * h
    # centre of court roughly in the image
    cx = xs.mean(1)
    cy = ys.mean(1)
    ok &= (cx > 0) & (cx < w) & (cy > 0) & (cy < h)
    Hs = Hs[ok]
    if len(Hs) == 0:
        return []
    # score
    dt = cv2.distanceTransform(255 - mask, cv2.DIST_L2, 3)
    # three-stage scoring: very coarse on all, coarse on the best 400, fine on the best 60
    idx = np.arange(len(Hs))
    if len(Hs) > 400:
        s0 = _score(Hs, _SAMPLES_COARSE[::4], dt)
        idx = np.argsort(-s0)[:400]
    scores = _score(Hs[idx], _SAMPLES_COARSE, dt)
    idx = idx[np.argsort(-scores)[:60]]
    scores2 = _score(Hs[idx], _SAMPLES_FINE[0][::2], dt)
    order = idx[np.argsort(-scores2)]
    out = []
    for i in order:
        Hn = Hs[i] / Hs[i][2, 2]
        if any(np.abs(unproject_c(Hn) - unproject_c(o)).max() < 3 for o in out):
            continue
        out.append(Hn)
        if len(out) >= top_k:
            break
    return out


def unproject_c(Hc):
    corners = np.array([(-D, L), (D, L), (D, -L), (-D, -L)])
    return _apply(Hc, corners)


def _score(Hs, pts_m, dt, hit_px=1.5, lam=0.5):
    h, w = dt.shape
    P = np.concatenate([pts_m, np.ones((len(pts_m), 1))], 1).T   # (3,S)
    Q = Hs @ P                                                  # (N,3,S)
    z = Q[:, 2, :]
    good = z > 1e-9
    zz = np.where(good, z, 1.0)
    u = Q[:, 0, :] / zz
    v = Q[:, 1, :] / zz
    inimg = good & (u >= 0) & (u < w - 1) & (v >= 0) & (v < h - 1)
    ui = np.clip(u, 0, w - 1, out=u).astype(np.int32)
    vi = np.clip(v, 0, h - 1, out=v).astype(np.int32)
    d = dt[vi, ui]
    hit = inimg & (d <= hit_px)
    miss = inimg & ~hit
    return hit.sum(1) - lam * miss.sum(1)


def _square_to_quad(Q):
    """Closed-form homographies mapping the unit square (0,0),(1,0),(1,1),(0,1)
    to quads Q (M,4,2) given in that corner order."""
    x0, x1, x2, x3 = Q[:, 0, 0], Q[:, 1, 0], Q[:, 2, 0], Q[:, 3, 0]
    y0, y1, y2, y3 = Q[:, 0, 1], Q[:, 1, 1], Q[:, 2, 1], Q[:, 3, 1]
    sx = x0 - x1 + x2 - x3
    sy = y0 - y1 + y2 - y3
    dx1, dx2 = x1 - x2, x3 - x2
    dy1, dy2 = y1 - y2, y3 - y2
    den = dx1 * dy2 - dx2 * dy1
    with np.errstate(divide="ignore", invalid="ignore"):
        g = (sx * dy2 - dx2 * sy) / den
        hh = (dx1 * sy - sx * dy1) / den
    a = x1 - x0 + g * x1
    b = x3 - x0 + hh * x3
    d = y1 - y0 + g * y1
    e = y3 - y0 + hh * y3
    one = np.ones_like(a)
    return np.stack([a, b, x0, d, e, y0, g, hh, one], 1).reshape(-1, 3, 3)


def _profiles(Hc, resp, w, step_idx=None):
    """For fine model samples visible in the image, find the line-centre offset
    along the projected normal within +/- w px. Returns (model_pts, img_pts,
    offsets, found_mask, visible_count)."""
    P, T, _ = _SAMPLES_FINE
    h, wd = resp.shape
    p = _apply(Hc, P)
    p2 = _apply(Hc, P + T * 0.05)
    t = p2 - p
    tn = np.linalg.norm(t, axis=1, keepdims=True)
    good = np.isfinite(p).all(1) & (tn[:, 0] > 1e-6)
    t = t / np.where(tn > 0, tn, 1)
    n = np.stack([-t[:, 1], t[:, 0]], 1)
    m = w + 2
    vis = good & (p[:, 0] >= m) & (p[:, 0] < wd - m) & (p[:, 1] >= m) & (p[:, 1] < h - m)
    P, p, n = P[vis], p[vis], n[vis]
    if len(P) == 0:
        return P, p, np.zeros(0), np.zeros(0, bool), 0
    offs = np.arange(-w, w + 1, dtype=np.float32)
    mx = (p[:, 0:1] + n[:, 0:1] * offs).astype(np.float32)
    my = (p[:, 1:2] + n[:, 1:2] * offs).astype(np.float32)
    prof = cv2.remap(resp, mx, my, cv2.INTER_LINEAR)
    k = prof.argmax(1)
    pk = prof[np.arange(len(k)), k]
    inner = (k > 0) & (k < 2 * w)
    found = (pk > 22.0) & inner
    # sub-pixel parabola
    kk = np.clip(k, 1, 2 * w - 1)
    a = prof[np.arange(len(k)), kk - 1]
    b = prof[np.arange(len(k)), kk]
    c = prof[np.arange(len(k)), kk + 1]
    den = a - 2 * b + c
    with np.errstate(divide="ignore", invalid="ignore"):
        sub = np.where(np.abs(den) > 1e-6, 0.5 * (a - c) / den, 0.0)
    sub = np.clip(sub, -0.5, 0.5)
    off = offs[kk] + sub
    q = p + n * off[:, None]
    return P, q, off, found, int(vis.sum())


def _refine(Hc, resp, windows=(8, 5, 3)):
    Hc = Hc / Hc[2, 2]
    for w in windows:
        P, q, off, found, nvis = _profiles(Hc, resp, w)
        if found.sum() < 12:
            return None
        src = P[found].astype(np.float32)
        dst = q[found].astype(np.float32)
        Hn, inl = cv2.findHomography(src, dst, cv2.RANSAC, max(1.0, w / 3.0), maxIters=200, confidence=0.99)
        if Hn is None:
            return None
        inl = inl.ravel().astype(bool)
        if inl.sum() >= 12:
            Hn2, _ = cv2.findHomography(src[inl], dst[inl], 0)
            if Hn2 is not None:
                Hn = Hn2
        Hc = Hn / Hn[2, 2]
    return Hc


def _evaluate(Hc, resp, w=6):
    """Mean |offset| (px) between projected model lines and detected line
    centres, and coverage = fraction of visible model samples with a line."""
    P, q, off, found, nvis = _profiles(Hc, resp, w)
    if nvis == 0 or found.sum() == 0:
        return 99.0, 0.0
    return float(np.abs(off[found]).mean()), float(found.sum() / nvis)


def _plausible(Hc, w, h) -> bool:
    c = unproject_c(Hc)
    if not np.isfinite(c).all():
        return False
    top_w = c[1, 0] - c[0, 0]
    bot_w = c[2, 0] - c[3, 0]
    return bool(top_w > 0.05 * w and bot_w > 0.9 * top_w and c[3, 1] > c[0, 1] and c[2, 1] > c[1, 1])
