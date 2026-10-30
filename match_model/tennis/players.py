"""Player detection, near/far assignment, identity and movement stats.

YOLO11n (person class) runs on a court crop of the frame at ~5 fps on MPS.
Each detection's foot point (bottom centre of the bbox) is projected to court
metres with the CourtFit homography; the near and far player are the best
in-zone candidates on each half (officials, line judges and ball kids are
rejected by position: outside the playing zone, at the net posts, behind the
back wall). Tracks are smoothed and short gaps interpolated.

IdentityModel tells the two players apart by clothing colour (HSV histograms
of torso and shorts), which survives motion blur far better than shape cues.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

import cv2
import numpy as np

from .contracts import (CourtFit, PlayerObs, HALF_LEN, SERVICE_LINE, SINGLES_HALF_W)
from .video import iter_frames

_MODEL = None
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "models", "yolo11n.pt")
LAST_STATS: dict = {}


def _no_nms_time_limit():
    """Ultralytics abandons NMS after ~2 s per batch and returns no boxes for the
    rest of the batch, so on a busy machine players silently go missing and the
    chart depends on system load. Lift that limit."""
    from ultralytics.utils import nms
    if getattr(nms.non_max_suppression, "_unlimited", False):
        return
    orig = nms.non_max_suppression

    def unlimited(*a, **k):
        k["max_time_img"] = 1e6
        return orig(*a, **k)
    unlimited._unlimited = True
    nms.non_max_suppression = unlimited


def _model():
    global _MODEL
    if _MODEL is None:
        from ultralytics import YOLO
        _no_nms_time_limit()
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        if not os.path.exists(MODEL_PATH):
            cwd = os.getcwd()
            try:  # ultralytics downloads into cwd
                os.chdir(os.path.dirname(MODEL_PATH))
                _MODEL = YOLO("yolo11n.pt")
            finally:
                os.chdir(cwd)
        else:
            _MODEL = YOLO(MODEL_PATH)
    return _MODEL


def _device():
    try:
        import torch
        return "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:  # pragma: no cover
        return "cpu"


@dataclass
class PlayerDet(PlayerObs):
    """PlayerObs plus raw detection info. pid is None until identity/ends assign it."""
    det_conf: float = 0.0
    feat: Optional[np.ndarray] = field(default=None, repr=False)
    interp: bool = False


def to_court(fit: CourtFit, x: float, y: float) -> tuple[float, float]:
    H = np.asarray(fit.H, float)
    q = H @ np.array([x, y, 1.0])
    return float(q[0] / q[2]), float(q[1] / q[2])


def court_crop(fit: CourtFit, margin_x: float = 2.5, margin_y: float = 3.5) -> tuple[int, int, int, int]:
    """Image crop (full-frame px, even sizes) covering the court plus margins and
    room above the far baseline for the far player's body."""
    Hi = np.linalg.inv(np.asarray(fit.H, float))
    xm, ym = 5.485 + margin_x, HALF_LEN + margin_y
    pts = np.array([[-xm, -ym], [xm, -ym], [xm, ym], [-xm, ym]], float).reshape(-1, 1, 2)
    img = cv2.perspectiveTransform(pts, Hi).reshape(-1, 2)
    x0, x1 = img[:, 0].min(), img[:, 0].max()
    y0, y1 = img[:, 1].min() - 90, fit.frame_h  # near player's feet can reach the bottom edge
    x0, y0 = max(0, int(x0)) // 2 * 2, max(0, int(y0)) // 2 * 2
    x1 = min(fit.frame_w, int(x1)) // 2 * 2
    y1 = min(fit.frame_h, int(y1)) // 2 * 2
    return x0, y0, x1, y1


# --------------------------------------------------------------------------- features
def appearance_feature(frame: np.ndarray, bbox) -> Optional[np.ndarray]:
    """HSV histograms of torso (20-50% of bbox height) and shorts (50-68%)."""
    x0, y0, x1, y1 = [int(round(v)) for v in bbox]
    h, w = frame.shape[:2]
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    bh, bw = y1 - y0, x1 - x0
    if bh < 12 or bw < 5:
        return None
    # central 60% of the width avoids the background at the sides
    cx0, cx1 = x0 + int(0.2 * bw), x1 - int(0.2 * bw)
    parts = []
    for a, b in ((0.20, 0.50), (0.50, 0.68)):
        roi = frame[y0 + int(a * bh): y0 + int(b * bh), cx0:cx1]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [12, 4], [0, 180, 0, 256]).ravel()
        vh = cv2.calcHist([hsv], [2], None, [4], [0, 256]).ravel()
        v = np.concatenate([hist, vh]).astype(np.float32)
        v /= max(1e-6, v.sum())
        parts.append(v)
    return np.concatenate(parts)


class IdentityModel:
    """Nearest-centroid classifier on appearance features (Bhattacharyya-like distance)."""

    def __init__(self):
        self.centroids: dict = {}

    @staticmethod
    def _feat(s):
        return s.feat if hasattr(s, "feat") else np.asarray(s, np.float32)

    def fit(self, samples: Sequence, labels: Sequence[str]) -> "IdentityModel":
        feats: dict = {}
        for s, l in zip(samples, labels):
            f = self._feat(s)
            if f is None or l not in ("A", "B"):
                continue
            feats.setdefault(l, []).append(f)
        self.centroids = {l: np.median(np.stack(v), axis=0) for l, v in feats.items()}
        return self

    def fit_from_ends(self, dets: Sequence[PlayerDet], ends: dict) -> "IdentityModel":
        """Bootstrap from a window where we know who is at which end."""
        inv = {e: p for p, e in ends.items()}
        sel = [d for d in dets if d.feat is not None and not d.interp]
        return self.fit(sel, [inv[d.end] for d in sel])

    @staticmethod
    def _dist(a, b) -> float:
        return float(1.0 - np.sum(np.sqrt(np.clip(a, 0, None) * np.clip(b, 0, None))) / 2.0)

    def assign(self, obs) -> tuple[Optional[str], float]:
        """Return (pid, margin in 0..1). margin 0 = undecidable."""
        f = self._feat(obs)
        if f is None or len(self.centroids) < 2:
            return None, 0.0
        dA, dB = self._dist(f, self.centroids["A"]), self._dist(f, self.centroids["B"])
        margin = abs(dA - dB) / max(1e-6, dA + dB)
        return ("A" if dA < dB else "B"), float(margin)

    def assign_ends(self, dets: Sequence[PlayerDet]) -> tuple[Optional[dict], float]:
        """Vote over a window: which player is near? Returns (ends, conf)."""
        votes = 0.0
        tot = 0.0
        for d in dets:
            if d.feat is None or d.interp:
                continue
            pid, m = self.assign(d)
            if pid is None:
                continue
            s = 1.0 if (pid == "A") == (d.end == "near") else -1.0
            votes += s * m
            tot += m
        if tot <= 0:
            return None, 0.0
        conf = abs(votes) / tot
        near = "A" if votes > 0 else "B"
        far = "B" if near == "A" else "A"
        return {near: "near", far: "far"}, float(conf)

    def to_dict(self):
        return {k: v.tolist() for k, v in self.centroids.items()}

    @classmethod
    def from_dict(cls, d):
        m = cls()
        m.centroids = {k: np.asarray(v, np.float32) for k, v in d.items()}
        return m


# --------------------------------------------------------------------------- detection
def _zone_score(cx: float, cy: float, end: str) -> float:
    """How plausible a court foot point is for a player at this end (>0 = plausible)."""
    ay = cy if end == "far" else -cy
    if ay < 0.3 or ay > HALF_LEN + 7.0:
        return -1.0
    ax = abs(cx)
    if ax > 8.0:
        return -1.0
    s = 1.0
    if ax > SINGLES_HALF_W + 2.0:
        s -= (ax - SINGLES_HALF_W - 2.0) * 0.5
    if ay < 2.5 and ax > 5.5:      # net-post area: ball kids / umpire chair
        s -= 1.0
    return s


def _raw_detections(path, start, end, fit, fps, imgsz, crop):
    model = _model()
    dev = _device()
    frames, times = [], []
    for t, f in iter_frames(path, fps=fps, start=start, end=end, crop=crop):
        frames.append(f)
        times.append(t)
    res = []
    B = 16
    for i in range(0, len(frames), B):
        r = model.predict(frames[i:i + B], device=dev, imgsz=imgsz, classes=[0],
                          conf=0.08, verbose=False)
        for x in r:
            res.append((x.boxes.xyxy.cpu().numpy(), x.boxes.conf.cpu().numpy()))
    return times, frames, res


def detect_players(path: str, start: float, end: float, fit: CourtFit, fps: float = 5,
                   imgsz: int = 480, ends: Optional[dict] = None,
                   with_features: bool = True) -> list[PlayerDet]:
    """Detect the near and far player in [start, end). Returns PlayerDet (PlayerObs
    subclass) for each sampled frame and end (interpolated across short gaps; those
    have interp=True). pid is filled from `ends` ({"A":"near","B":"far"}) if given."""
    t0 = time.time()
    crop = court_crop(fit)
    times, frames, res = _raw_detections(path, start, end, fit, fps, imgsz, crop)
    t1 = time.time()
    ox, oy = crop[0], crop[1]
    per_end = {"near": [None] * len(times), "far": [None] * len(times)}
    prev = {"near": None, "far": None}
    for i, (boxes, confs) in enumerate(res):
        for e in ("near", "far"):
            best, bs = None, -1e9
            for bb, cf in zip(boxes, confs):
                x0, y0, x1, y1 = bb + [ox, oy, ox, oy]
                cx, cy = to_court(fit, (x0 + x1) / 2, y1)
                z = _zone_score(cx, cy, e)
                if z <= 0:
                    continue
                hpx = y1 - y0
                s = z + 1.5 * float(cf) + min(hpx, 120) / 200.0
                if prev[e] is not None:
                    pcx, pcy = prev[e]
                    s -= 0.15 * np.hypot(cx - pcx, cy - pcy)
                if s > bs:
                    bs, best = s, (x0, y0, x1, y1, float(cf), cx, cy)
            if best is not None:
                per_end[e][i] = best
                prev[e] = best[5:7]
    out: list[PlayerDet] = []
    pid_of = {e: p for p, e in (ends or {}).items()}
    for e in ("near", "far"):
        seq = per_end[e]
        idx = [i for i, v in enumerate(seq) if v is not None]
        if not idx:
            continue
        arr = np.array([seq[i] for i in idx], float)
        # smooth court positions & boxes with a small median filter (robust to jumps)
        if len(arr) >= 3:
            sm = arr.copy()
            for k in range(len(arr)):
                lo, hi = max(0, k - 1), min(len(arr), k + 2)
                sm[k, [0, 1, 2, 3, 5, 6]] = np.median(arr[lo:hi][:, [0, 1, 2, 3, 5, 6]], axis=0)
            arr = sm
        full = np.full((len(times), 7), np.nan)
        full[idx] = arr
        interp = np.zeros(len(times), bool)
        maxgap = int(round(fps * 1.0))
        for a, b in zip(idx[:-1], idx[1:]):
            if 1 < b - a <= maxgap + 1:
                for k in range(a + 1, b):
                    w = (k - a) / (b - a)
                    full[k] = (1 - w) * full[a] + w * full[b]
                    interp[k] = True
        for k in range(len(times)):
            if np.isnan(full[k, 0]):
                continue
            x0, y0, x1, y1, cf, cx, cy = full[k]
            feat = None
            if with_features and not interp[k]:
                f = frames[k]
                feat = appearance_feature(f, (x0 - ox, y0 - oy, x1 - ox, y1 - oy))
            out.append(PlayerDet(t=float(times[k]), pid=pid_of.get(e), end=e,
                                 bbox=(float(x0), float(y0), float(x1), float(y1)),
                                 court_xy=(float(cx), float(cy)), conf=float(cf),
                                 det_conf=float(cf), feat=feat, interp=bool(interp[k])))
    out.sort(key=lambda d: (d.t, d.end))
    dt = time.time() - t0
    LAST_STATS.update(frames=len(times), seconds=dt, yolo_s=t1 - t0, crop=crop)
    return out


# --------------------------------------------------------------------------- movement stats
def track_of(dets: Sequence[PlayerObs], end: str):
    seq = [d for d in dets if d.end == end]
    return np.array([d.t for d in seq]), np.array([d.court_xy for d in seq], float).reshape(-1, 2)


def distance_run(dets: Sequence[PlayerObs], end: str, max_speed: float = 9.0) -> float:
    """Metres run by the player at `end`, from the smoothed foot track; steps implying
    > max_speed m/s are treated as detection jumps and ignored."""
    t, xy = track_of(dets, end)
    if len(t) < 2:
        return 0.0
    if len(xy) >= 5:  # light smoothing: moving average of 3
        k = np.ones(3) / 3
        xy = np.stack([np.convolve(np.pad(xy[:, c], 1, mode="edge"), k, "valid") for c in (0, 1)], 1)
    d = 0.0
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        step = float(np.hypot(*(xy[i] - xy[i - 1])))
        if dt <= 0 or dt > 1.5 or step / dt > max_speed:
            continue
        d += step
    return d


def net_approaches(dets: Sequence[PlayerObs], end: str, enter: float = SERVICE_LINE - 0.4,
                   leave: float = SERVICE_LINE + 1.2, min_dur: float = 0.3) -> int:
    """Count moves inside ~the service line (|y| < enter), with hysteresis."""
    t, xy = track_of(dets, end)
    n, inside, t_in = 0, False, None
    for ti, (x, y) in zip(t, xy):
        ay = abs(y)
        if not inside and ay < enter:
            inside, t_in = True, ti
        elif inside and ay > leave:
            if ti - t_in >= min_dur:
                n += 1
            inside = False
    if inside and t is not None and len(t) and t[-1] - t_in >= min_dur:
        n += 1
    return n


def player_at(dets: Sequence[PlayerObs], end: str, t: float, max_dt: float = 0.5):
    """Nearest (interpolated) observation of the player at `end` around time t."""
    seq = [d for d in dets if d.end == end]
    if not seq:
        return None
    ts = np.array([d.t for d in seq])
    i = int(np.searchsorted(ts, t))
    cand = []
    if i > 0:
        cand.append(seq[i - 1])
    if i < len(seq):
        cand.append(seq[i])
    if len(cand) == 2 and cand[1].t - cand[0].t < 1.5:
        a, b = cand
        w = (t - a.t) / max(1e-6, b.t - a.t)
        w = min(1.0, max(0.0, w))
        bb = tuple((1 - w) * np.array(a.bbox) + w * np.array(b.bbox))
        xy = tuple((1 - w) * np.array(a.court_xy) + w * np.array(b.court_xy))
        return PlayerObs(t=t, pid=a.pid, end=end, bbox=bb, court_xy=xy, conf=min(a.conf, b.conf))
    c = min(cand, key=lambda d: abs(d.t - t))
    return c if abs(c.t - t) <= max_dt else None


# --------------------------------------------------------------------------- pose (forehand / backhand)
POSE_PATH = os.path.join(os.path.dirname(MODEL_PATH), "yolo11n-pose.pt")
_POSE = None
KP_LS, KP_RS, KP_LW, KP_RW, KP_LH, KP_RH = 5, 6, 9, 10, 11, 12


def _pose_model():
    """yolo11n-pose (Ultralytics release asset), cached under models/."""
    global _POSE
    if _POSE is None:
        from ultralytics import YOLO
        _no_nms_time_limit()
        os.makedirs(os.path.dirname(POSE_PATH), exist_ok=True)
        if not os.path.exists(POSE_PATH):
            cwd = os.getcwd()
            try:  # ultralytics downloads into cwd
                os.chdir(os.path.dirname(POSE_PATH))
                _POSE = YOLO("yolo11n-pose.pt")
            finally:
                os.chdir(cwd)
        else:
            _POSE = YOLO(POSE_PATH)
    return _POSE


def contact_crops(path: str, t: float, bbox, half_s: float = 0.04):
    """Full-resolution crops of one player around a contact time: the frames in
    [t - half_s, t + half_s]. Returns [(t, crop, (ox, oy))]."""
    x0, y0, x1, y1 = bbox
    bw, bh = x1 - x0, y1 - y0
    cx0, cy0 = max(0, int(x0 - 0.8 * bw)), max(0, int(y0 - 0.35 * bh))
    cx1, cy1 = int(x1 + 0.8 * bw), int(y1 + 0.1 * bh)
    out = []
    for tt, f in iter_frames(path, fps=None, start=max(0.0, t - half_s), end=t + half_s + 0.02,
                             crop=(cx0, cy0, cx1, cy1)):
        out.append((tt, f.copy(), (cx0 - (cx0 & 1), cy0 - (cy0 & 1))))
    return out


def pose_wing(crops, bbox, end: str, hand: str = "R") -> tuple[Optional[str], float, dict]:
    """Forehand/backhand from wrist positions at contact.

    The racket wrist at contact is out to the hitter's racket side for a
    forehand and on the other side for a backhand (a two-handed backhand puts
    both wrists together on that side). Sides come from the known facing, not
    from the model's left/right labels (unreliable for players seen from
    behind): the near player faces away from the camera (his right = image
    right), the far player faces it (his right = image left)."""
    if not crops:
        return None, 0.0, {}
    model = _pose_model()
    imgs = [c for _, c, _ in crops]
    res = model.predict(imgs, device=_device(), imgsz=320, conf=0.2, verbose=False)
    bx0, by0, bx1, by1 = bbox
    bw = max(1.0, bx1 - bx0)
    votes, weights = [], []
    for (tt, crop, (ox, oy)), r in zip(crops, res):
        if r.keypoints is None or len(r.boxes) == 0:
            continue
        boxes = r.boxes.xyxy.cpu().numpy() + [ox, oy, ox, oy]
        # the detection that best matches the tracked player box
        ious = []
        for b in boxes:
            ix = max(0.0, min(b[2], bx1) - max(b[0], bx0))
            iy = max(0.0, min(b[3], by1) - max(b[1], by0))
            inter = ix * iy
            ious.append(inter / max(1e-6, (b[2] - b[0]) * (b[3] - b[1]) + bw * (by1 - by0) - inter))
        k = int(np.argmax(ious))
        if ious[k] < 0.2:
            continue
        kp = r.keypoints.xy.cpu().numpy()[k] + [ox, oy]
        kc = r.keypoints.conf.cpu().numpy()[k] if r.keypoints.conf is not None else np.ones(len(kp))
        torso = [i for i in (KP_LS, KP_RS, KP_LH, KP_RH) if kc[i] > 0.3]
        wr = [i for i in (KP_LW, KP_RW) if kc[i] > 0.3]
        if len(torso) < 2 or not wr:
            continue
        cx = float(np.mean(kp[torso, 0]))
        sw = abs(kp[KP_LS, 0] - kp[KP_RS, 0]) if kc[KP_LS] > 0.3 and kc[KP_RS] > 0.3 else 0.0
        ref = max(sw, 0.3 * bw)
        d = [float(kp[i, 0] - cx) for i in wr]
        j = int(np.argmax(np.abs(d)))
        rel = d[j] / ref
        if len(wr) == 2 and abs(d[0] - d[1]) < 0.5 * ref and np.sign(d[0]) == np.sign(d[1]):
            rel *= 1.3              # both hands together on one side: two-handed stroke
        votes.append(rel)
        weights.append(float(kc[wr[j]]))
    if not votes:
        return None, 0.0, {}
    rel = float(np.average(votes, weights=weights))
    if abs(rel) < 0.35:
        return None, 0.0, {"rel": rel}
    image_right = rel > 0
    hitter_right = image_right if end == "near" else not image_right
    fh = hitter_right if hand == "R" else not hitter_right
    conf = float(np.clip(0.4 + 0.25 * (abs(rel) - 0.35), 0.4, 0.85))
    return ("fh" if fh else "bh"), conf, {"rel": rel, "n": len(votes)}
