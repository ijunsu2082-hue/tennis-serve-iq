"""Shot events (serve / hit / bounce) from a ball track plus player tracks.

Everything works on the image-space ball track of the high main camera:

* "flight direction": a smoothed vertical image velocity. A ball travelling
  towards the far end moves up the image (vy < 0), towards the near end down
  (vy > 0). Hits are reversals of the flight direction next to the player at the
  end the ball was travelling to (near hitter: down -> up; far hitter: up -> down).
  Because ball.py suppresses detections on player bodies, the ball usually
  disappears around contact; reversals are therefore found across short gaps.
* bounces: a sharp *decrease* of image vy inside one flight (the vertical 3-D
  velocity flips from down to up, which always moves the image point upward
  relative to the pre-bounce trend), away from players. The ball is on the ground
  there, so the homography gives the bounce point in court metres.
* serve: the first hit by the server-end player standing behind the baseline,
  preceded by the toss (ball rising/falling above his head).
* wing: side of the ball relative to the hitter's body centre at contact, for a
  right-hander a near player's forehand is on image-right; mirrored for the far
  player (he faces the camera) and for left-handers.
* stroke: overhead (contact above head), volley (hitter inside ~service line and
  no bounce since the opponent's shot), else groundstroke.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from .contracts import BallObs, CourtFit, ShotEvent, HALF_LEN, SERVICE_LINE

# tunables (full-frame px at 1280x720; scaled by frame height)
MIN_FLIGHT_PX = 22.0      # image travel for a run to count as a flight
HIT_DIST_PX = 70.0        # max distance between reversal point and hitter bbox
BOUNCE_DVY = 2.2          # px/frame drop of vy for a bounce
SERVE_BEHIND = HALF_LEN - 1.2


def _to_court(H: np.ndarray, x: float, y: float) -> tuple[float, float]:
    q = H @ np.array([x, y, 1.0])
    return float(q[0] / q[2]), float(q[1] / q[2])


def _player_at(players, end: str, t: float, max_dt: float = 0.6):
    seq = [p for p in (players or []) if p.end == end]
    if not seq:
        return None
    ts = np.array([p.t for p in seq])
    i = int(np.searchsorted(ts, t))
    if 0 < i < len(seq) and seq[i].t - seq[i - 1].t < 1.5:
        a, b = seq[i - 1], seq[i]
        w = float(np.clip((t - a.t) / max(1e-6, b.t - a.t), 0, 1))
        bb = (1 - w) * np.array(a.bbox) + w * np.array(b.bbox)
        xy = (1 - w) * np.array(a.court_xy) + w * np.array(b.court_xy)
        return {"bbox": bb, "court_xy": xy, "pid": a.pid}
    c = seq[min(max(i, 0), len(seq) - 1)] if i < len(seq) else seq[-1]
    if i > 0 and i < len(seq) and abs(seq[i - 1].t - t) < abs(c.t - t):
        c = seq[i - 1]
    if abs(c.t - t) > max_dt:
        return None
    return {"bbox": np.array(c.bbox), "court_xy": np.array(c.court_xy), "pid": c.pid}


def _box_dist(bb, x, y) -> float:
    x0, y0, x1, y1 = bb
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    return float(np.hypot(dx, dy))


def _slope(t, v):
    if len(t) < 2:
        return np.nan
    A = np.vstack([t - t.mean(), np.ones_like(t)]).T
    return float(np.linalg.lstsq(A, v, rcond=None)[0][0])


def ball_runs(ball: Sequence[BallObs], fps: float, max_gap: float = 0.5):
    """Split the track into contiguous pieces (gaps > max_gap seconds break)."""
    if not ball:
        return []
    runs, cur = [], [ball[0]]
    for b in ball[1:]:
        if b.t - cur[-1].t > max_gap:
            runs.append(cur)
            cur = [b]
        else:
            cur.append(b)
    runs.append(cur)
    return runs


def _direction_series(ball: Sequence[BallObs], fps: float, half: int = 3):
    """Per-observation smoothed vy (px/frame) from a local robust slope."""
    t = np.array([b.t for b in ball])
    y = np.array([b.xy_px[1] for b in ball])
    vy = np.full(len(ball), np.nan)
    for i in range(len(ball)):
        lo, hi = max(0, i - half), min(len(ball), i + half + 1)
        sel = np.abs(t[lo:hi] - t[i]) <= (half + 1.5) / fps
        tt, yy = t[lo:hi][sel], y[lo:hi][sel]
        if len(tt) >= 3:
            vy[i] = _slope(tt, yy) / fps
    return vy


def _flights(ball: Sequence[BallObs], fps: float):
    """Segment into flights: maximal runs with a consistent vertical direction
    and enough travel. Returns list of dicts {i0,i1,dir} (dir=+1 toward near)."""
    if len(ball) < 4:
        return []
    vy = _direction_series(ball, fps)
    s = np.sign(np.nan_to_num(vy))
    # fill zeros/nans with the previous sign
    for i in range(1, len(s)):
        if s[i] == 0:
            s[i] = s[i - 1]
    flights, i0 = [], 0
    for i in range(1, len(s) + 1):
        brk = i == len(s) or s[i] != s[i0] or (ball[i].t - ball[i - 1].t) > 1.2
        if brk:
            seg = ball[i0:i]
            travel = abs(seg[-1].xy_px[1] - seg[0].xy_px[1])
            if s[i0] != 0 and len(seg) >= 3 and travel >= MIN_FLIGHT_PX:
                flights.append({"i0": i0, "i1": i - 1, "dir": int(s[i0])})
            i0 = i
    # merge consecutive flights with the same direction (separated by noise)
    merged = []
    for f in flights:
        if merged and merged[-1]["dir"] == f["dir"]:
            merged[-1]["i1"] = f["i1"]
        else:
            merged.append(dict(f))
    return merged


def _bounces_in(ball, f, fps, H, players, k: int = 3):
    """Bounce candidates inside one flight: largest local drop of vy."""
    seg = ball[f["i0"]: f["i1"] + 1]
    n = len(seg)
    if n < 2 * k + 1:
        return []
    t = np.array([b.t for b in seg])
    x = np.array([b.xy_px[0] for b in seg])
    y = np.array([b.xy_px[1] for b in seg])
    real = np.array([b.conf > 0.31 for b in seg])     # gap-filled points have conf 0.3
    dv = np.full(n, 0.0)
    for i in range(k, n - k):
        # slopes from real observations only (gap-filled points are a smooth
        # polynomial and would hide or fake kinks): the nearest k+1 real points
        # on each side, within 2k frames
        if not real[i]:
            continue
        L = [j for j in range(i, max(-1, i - 2 * k - 1), -1) if real[j]][:k + 1][::-1]
        R = [j for j in range(i, min(n, i + 2 * k + 1)) if real[j]][:k + 1]
        if len(L) < 3 or len(R) < 3:
            continue
        if t[R[-1]] - t[L[0]] > (3 * k + 2) / fps:
            continue  # do not measure across long gaps
        vl = _slope(t[L], y[L]) / fps
        vr = _slope(t[R], y[R]) / fps
        dv[i] = vr - vl
    out = []
    i = k
    while i < n - k:
        if dv[i] < -BOUNCE_DVY and dv[i] == dv[max(0, i - k):i + k + 1].min():
            bx, by = float(x[i]), float(y[i])
            near_player = False
            for e in ("near", "far"):
                p = _player_at(players, e, float(t[i]))
                if p is not None and _box_dist(p["bbox"], bx, by) < 12:
                    near_player = True
            cxy = _to_court(H, bx, by)
            plausible = abs(cxy[0]) < 8.0 and abs(cxy[1]) < HALF_LEN + 4.0 and abs(cxy[1]) > 0.8
            if not near_player and plausible:
                conf = float(np.clip(0.35 + 0.08 * (-dv[i] - BOUNCE_DVY), 0.35, 0.85))
                out.append(ShotEvent(t=float(t[i]), kind="bounce", court_xy=cxy, conf=conf))
                i += k
                continue
        i += 1
    return out


def tail_bounce(ball: Sequence[BallObs], t0: float, t1: float, fit: CourtFit, fps: float = 25.0,
                k: int = 3) -> Optional[ShotEvent]:
    """A bounce right at the end of a tracked stretch (the ball is often lost
    just after a far-court bounce, so the regular detector has no right-hand
    window): >= k real points with a steady slope, then 1-2 real points whose
    vy drops by > 3 x BOUNCE_DVY. Searches real observations in (t0, t1)."""
    pts = [b for b in ball if t0 < b.t < t1 and b.conf > 0.31]
    if len(pts) < k + 2:
        return None
    H = np.asarray(fit.H, float)
    for j in range(len(pts) - 2, k - 2, -1):
        L = pts[j - k + 1: j + 1]
        R = pts[j + 1: j + 3]
        if len(L) < k or not R or R[0].t - L[-1].t > 2.5 / fps or L[-1].t - L[0].t > (k + 1) / fps:
            continue
        vl = _slope(np.array([b.t for b in L]), np.array([b.xy_px[1] for b in L])) / fps
        vr = (R[0].xy_px[1] - L[-1].xy_px[1]) / ((R[0].t - L[-1].t) * fps)
        if vr - vl < -3 * BOUNCE_DVY:
            bx, by = L[-1].xy_px
            cxy = _to_court(H, bx, by)
            if abs(cxy[0]) < 8.0 and 0.8 < abs(cxy[1]) < HALF_LEN + 4.0:
                return ShotEvent(t=float(L[-1].t), kind="bounce", court_xy=cxy, conf=0.35)
    return None


def _wing(ball_x: float, body_cx: float, end: str, hand: str, bw: float) -> tuple[Optional[str], float]:
    d = ball_x - body_cx
    if abs(d) < 0.15 * bw:
        return None, 0.0
    right_of_body = d > 0                      # image right
    # near player faces away from camera: his right = image right
    hitter_right = right_of_body if end == "near" else not right_of_body
    fh = hitter_right if hand == "R" else not hitter_right
    conf = float(np.clip(0.45 + 0.25 * abs(d) / max(bw, 1.0), 0.45, 0.85))
    return ("fh" if fh else "bh"), conf


def _is_toss(ball, f, players, fps, server_end: Optional[str] = None) -> bool:
    """A flight that stays above/inside the bbox column of one player with little
    horizontal travel (the serve toss going up or coming down)."""
    seg = ball[f["i0"]: f["i1"] + 1]
    xs = np.array([b.xy_px[0] for b in seg])
    ys = np.array([b.xy_px[1] for b in seg])
    if seg[-1].t - seg[0].t > 1.6:
        return False
    for e in ((server_end,) if server_end else ("near", "far")):
        p = _player_at(players, e, 0.5 * (seg[0].t + seg[-1].t))
        if p is None:
            continue
        x0, y0, x1, y1 = p["bbox"]
        bw, bh = x1 - x0, y1 - y0
        inside_x = np.all((xs > x0 - 0.6 * bw) & (xs < x1 + 0.6 * bw))
        above = np.all(ys < y0 + 0.6 * bh) and np.all(ys > y0 - 4.0 * bh)
        if inside_x and above and (xs.max() - xs.min()) < 1.2 * bw:
            return True
    return False


def _split_toss(ball, flights, players):
    """The toss going up and the serve leaving can merge into one flight (same
    image direction, the falling part of the toss is short/undetected). Split a
    flight at a sharp speed jump when its first part hovers over a player."""
    out = []
    for f in flights:
        seg = ball[f["i0"]: f["i1"] + 1]
        n = len(seg)
        done = False
        if n >= 8:
            xy = np.array([b.xy_px for b in seg])
            t = np.array([b.t for b in seg])
            sp = np.hypot(*np.diff(xy, axis=0).T) / np.maximum(np.diff(t) * 25.0, 1.0)
            for j in range(4, n - 3):
                before = sp[max(0, j - 4):j].mean()
                after = sp[j:j + 3].mean()
                if after > max(6.0, 2.5 * before):
                    head = {"i0": f["i0"], "i1": f["i0"] + j, "dir": f["dir"]}
                    if _is_toss(ball, head, players, 25.0):
                        out.append(head)
                        out.append({"i0": f["i0"] + j, "i1": f["i1"], "dir": f["dir"]})
                        done = True
                    break
        if not done:
            out.append(f)
    return out


MAX_BRIDGE_S = 1.2        # longest suppressed/occluded stretch bridged at a contact
KINK_DV = 4.0             # px/frame change of image velocity for a same-direction far hit
SAME_END_GAP = 1.2        # one player cannot hit twice within this time
MIN_GAP = 0.45            # two contacts closer than this are one contact
CONTACT_SLACK_S = 0.15    # max time between the ball entering/leaving the reach zone and contact


def _mk_hit(t, end, p, ball_x, pc_y, hand, conf, wscale=1.0):
    bb = p["bbox"]
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    wing, wconf = _wing(ball_x, 0.5 * (bb[0] + bb[2]), end, hand.get(end, "R"), bw)
    stroke = "groundstroke"
    if pc_y is not None and pc_y < bb[1] - 0.05 * bh:
        stroke = "overhead"
    ev = ShotEvent(t=float(t), kind="hit", court_xy=tuple(map(float, p["court_xy"])),
                   wing=wing, stroke=stroke, conf=float(conf))
    ev.by = p["pid"]
    ev._end = end            # internal helpers (not part of the contract)
    ev._wconf = wconf * wscale
    ev._bbox = tuple(map(float, bb))
    ev._pc = (float(ball_x), float(pc_y) if pc_y is not None else float("nan"))
    ev._out, ev._n_out, ev._out_end_t = 0.0, 0, float(t)
    return ev


def _hit_lim(end: str, bb) -> float:
    if end == "near":
        return HIT_DIST_PX
    # far player: racket reach ~ his body height in image px (arm + racket ~1.5 m)
    return max(0.6 * HIT_DIST_PX, 0.9 * float(bb[3] - bb[1]))


def _dist_to_player(end, bb, x, y) -> float:
    """Distance to the hitter's reach zone: the bbox, extended upward (overheads,
    serve contact) by half a body height."""
    x0, y0, x1, y1 = bb
    bh = y1 - y0
    return _box_dist((x0, y0 - 0.5 * bh, x1, y1), x, y)


def _seg_fit(ball, idx, deg):
    t = np.array([ball[i].t for i in idx])
    x = np.array([ball[i].xy_px[0] for i in idx])
    y = np.array([ball[i].xy_px[1] for i in idx])
    t0 = float(t.mean())
    deg = min(deg, len(idx) - 1)
    px, py = np.polyfit(t - t0, x, deg), np.polyfit(t - t0, y, deg)
    return lambda tt: (np.polyval(px, tt - t0), np.polyval(py, tt - t0))


def _bridge(ball, fa, fb):
    """Contact estimate between an incoming flight fa and the outgoing flight fb.

    Around the racket the ball is usually lost (suppressed inside the padded
    player box, occluded, or blurred), so the contact lies in the gap between
    the two flights. Both flight ends are extrapolated across the gap and the
    contact is where they meet. Returns (t, (x, y), miss_px)."""
    a1, b0 = ball[fa["i1"]], ball[fb["i0"]]
    gap = b0.t - a1.t
    ia = [i for i in range(max(fa["i0"], fa["i1"] - 6), fa["i1"] + 1) if ball[i].conf > 0.31]
    ib = [i for i in range(fb["i0"], min(fb["i1"], fb["i0"] + 6) + 1) if ball[i].conf > 0.31]
    mid = (0.5 * (a1.xy_px[0] + b0.xy_px[0]), 0.5 * (a1.xy_px[1] + b0.xy_px[1]))
    if gap <= 0.09 or len(ia) < 3 or len(ib) < 3:
        return 0.5 * (a1.t + b0.t), mid, float(np.hypot(a1.xy_px[0] - b0.xy_px[0], a1.xy_px[1] - b0.xy_px[1]))
    deg = 2 if gap <= 0.3 else 1          # long extrapolations: straight lines only
    fA, fB = _seg_fit(ball, ia, deg), _seg_fit(ball, ib, deg)
    ts = np.arange(a1.t, b0.t + 1e-6, 0.02)
    xa, ya = fA(ts)
    xb, yb = fB(ts)
    d = np.hypot(xa - xb, ya - yb)
    k = int(np.argmin(d))
    return float(ts[k]), (float(0.5 * (xa[k] + xb[k])), float(0.5 * (ya[k] + yb[k]))), float(d[k])


def _reach_dist(end, bb, pts) -> float:
    return min(_dist_to_player(end, bb, x, y) for x, y in pts)


MAX_FLIGHT_VY = 45.0      # px/frame: mean image-y speed of a ball coming towards the camera
                          # above this (~60 m/s) is a tracker hop, not a ball


def _plausible(ball, flights, fps):
    """Drop flights whose mean vertical image speed is impossible for a ball
    (the tracker hopping between two objects), then re-merge same-direction
    neighbours."""
    keep = []
    for f in flights:
        a, b = ball[f["i0"]], ball[f["i1"]]
        nfr = max(1.0, (b.t - a.t) * fps)
        # only balls coming towards the camera are bounded: a ball rising steeply
        # off a near racket can legitimately shoot up the image
        if f["dir"] > 0 and abs(b.xy_px[1] - a.xy_px[1]) / nfr > MAX_FLIGHT_VY:
            continue
        keep.append(dict(f))
    merged = []
    for f in keep:
        if merged and merged[-1]["dir"] == f["dir"]:
            merged[-1]["i1"] = f["i1"]
        else:
            merged.append(f)
    return merged


def _kink_hits(ball, f, players, fps, k: int = 3):
    """Far-player contacts that do not reverse the image direction: a dropping
    ball (lob, high bounce, drop shot) already moves down the image, and the
    shot back towards the camera keeps that direction but changes the image
    velocity sharply. The contact is often hidden (suppressed near the body),
    so velocities are measured on the real observations on either side of each
    point/gap. Returns [(t, x, y, dv, index)] next to the far player."""
    if f["dir"] < 0:
        return []
    idx = [i for i in range(f["i0"], f["i1"] + 1) if ball[i].conf > 0.31]
    n = len(idx)
    if n < 2 * k:
        return []
    t = np.array([ball[i].t for i in idx])
    x = np.array([ball[i].xy_px[0] for i in idx])
    y = np.array([ball[i].xy_px[1] for i in idx])
    best = None
    for j in range(k - 1, n - k):
        # left window ends at j, right window starts at j+1 (a gap may lie between)
        L, R = slice(j - k + 1, j + 1), slice(j + 1, j + k + 1)
        if t[L][-1] - t[L][0] > (k + 1) / fps or t[R][-1] - t[R][0] > (k + 1) / fps:
            continue
        if t[j + 1] - t[j] > 0.4:
            continue
        vl, vr = _slope(t[L], y[L]) / fps, _slope(t[R], y[R]) / fps
        ul, ur = _slope(t[L], x[L]) / fps, _slope(t[R], x[R]) / fps
        dv = float(np.hypot(vr - vl, ur - ul))
        if vr < vl and (vl - vr) > abs(ur - ul):
            continue                    # mostly a drop of vy: a bounce, not a racket
        if dv < KINK_DV:
            continue
        tc = 0.5 * (t[j] + t[j + 1])
        xc, yc = 0.5 * (x[j] + x[j + 1]), 0.5 * (y[j] + y[j + 1])
        p = _player_at(players, "far", float(tc))
        if p is None:
            continue
        if _dist_to_player("far", p["bbox"], xc, yc) > _hit_lim("far", p["bbox"]):
            continue
        if np.hypot(x[-1] - xc, y[-1] - yc) < 3 * MIN_FLIGHT_PX:   # the ball must really come back
            continue
        if best is None or dv > best[3]:
            best = (float(tc), float(xc), float(yc), dv, idx[j + 1])
    return [best] if best else []


def detect_events(ball: Sequence[BallObs], players, fit: CourtFit, fps: float = 25.0,
                  hand: Optional[dict] = None, server_end: Optional[str] = None) -> list[ShotEvent]:
    """Detect serve / hit / bounce events.

    ball:     BallObs list (full-frame px), sorted by t
    players:  PlayerObs-like list with .t .end .bbox .court_xy .pid
    hand:     {"near": "R"|"L", "far": "R"|"L"} (default right-handed)
    server_end: if known ("near"/"far"), only that player can serve.
    Internal attributes on hit events: _end (near/far), _wconf, _bbox, _implied,
    _pc (contact px), _out (outgoing image travel px), _n_out (outgoing points),
    _out_end_t (end of the outgoing flight).
    """
    H = np.asarray(fit.H, float)
    hand = hand or {"near": "R", "far": "R"}
    ball = sorted(ball, key=lambda b: b.t)
    flights = _plausible(ball, _flights(ball, fps), fps)
    flights = _split_toss(ball, flights, players)
    toss_t = None
    toss_ends: list = []
    kept = []
    long_seen = False
    for f in flights:
        # a toss only happens before the serve, i.e. before the first long flight
        # (a high ball rising over a player later in the rally looks the same)
        if not long_seen and _is_toss(ball, f, players, fps, server_end):
            if toss_t is None:
                toss_t = ball[f["i0"]].t
            toss_ends.append(ball[f["i1"]].t)
            continue
        if abs(ball[f["i1"]].xy_px[1] - ball[f["i0"]].xy_px[1]) >= 150:
            long_seen = True
        kept.append(f)
    flights = kept
    events: list[ShotEvent] = []
    extra_bounces: list[ShotEvent] = []

    def outgoing(ev, f):
        ev._out = abs(ball[f["i1"]].xy_px[1] - ball[f["i0"]].xy_px[1])
        ev._n_out = f["i1"] - f["i0"] + 1
        ev._out_end_t = ball[f["i1"]].t
        ev._out_dir = f["dir"]

    # ---- hits at direction reversals, bridged across the contact gap ---------
    for fa, fb in zip(flights[:-1], flights[1:]):
        if fa["dir"] == fb["dir"]:
            continue
        a_end, b_start = ball[fa["i1"]], ball[fb["i0"]]
        if b_start.t - a_end.t > MAX_BRIDGE_S:
            continue
        end = "near" if fa["dir"] > 0 else "far"
        tc, pc, miss = _bridge(ball, fa, fb)
        p = _player_at(players, end, tc)
        bb = p["bbox"] if p is not None else None
        lim = _hit_lim(end, bb) if bb is not None else 0.0
        if bb is not None:
            # once the ball is inside the hitter's reach the racket meets it within
            # ~0.15 s (and it leaves the reach within ~0.15 s after contact)
            if _dist_to_player(end, bb, *a_end.xy_px) <= lim:
                tc = min(tc, a_end.t + CONTACT_SLACK_S)
            elif _dist_to_player(end, bb, *b_start.xy_px) <= lim:
                tc = max(tc, b_start.t - CONTACT_SLACK_S)
        dist = _reach_dist(end, bb, (pc, a_end.xy_px, b_start.xy_px)) if bb is not None else 1e9
        if dist > lim:
            # no player there: a ball heading to the near end that turns up the
            # image without a racket is a (high) bounce on the ground
            if fa["dir"] > 0 and b_start.t - a_end.t <= 0.15:
                cxy = _to_court(H, *a_end.xy_px)
                if abs(cxy[0]) < 8.0 and -HALF_LEN - 4.0 < cxy[1] < -0.8:
                    extra_bounces.append(ShotEvent(t=float(a_end.t), kind="bounce", court_xy=cxy, conf=0.4))
            continue
        # the ball must really have come to this player from the other end
        a0 = ball[fa["i0"]]
        in_travel = float(np.hypot(a_end.xy_px[0] - a0.xy_px[0], a_end.xy_px[1] - a0.xy_px[1]))
        if in_travel < (3 if end == "near" else 2) * MIN_FLIGHT_PX:
            continue
        out_travel = abs(ball[fb["i1"]].xy_px[1] - b_start.xy_px[1])
        gap_pen = 0.1 * max(0.0, (b_start.t - a_end.t) - 0.4)
        conf = float(np.clip(0.4 + 0.3 * (1 - dist / lim) + 0.002 * out_travel - gap_pen, 0.3, 0.9))
        # overhead only if the ball is above the head on both sides of contact
        pc_y = max(a_end.xy_px[1], b_start.xy_px[1]) if (b_start.t - a_end.t) < 0.2 else pc[1]
        ev = _mk_hit(tc, end, p, pc[0], pc_y, hand, conf)
        outgoing(ev, fb)
        events.append(ev)
    # ---- far-player contacts inside one image direction ----------------------
    for k, f in enumerate(flights):
        for (tk, xk, yk, dv, ik) in _kink_hits(ball, f, players, fps):
            p = _player_at(players, "far", tk)
            conf = float(np.clip(0.35 + 0.03 * (dv - KINK_DV), 0.35, 0.6))
            ev = _mk_hit(tk, "far", p, xk, yk, hand, conf, 0.8)
            outgoing(ev, {"i0": ik, "i1": f["i1"], "dir": f["dir"]})
            ev._kink = True
            events.append(ev)
    # ---- flights that start next to a player without a detected reversal
    # (first shot seen in the window, or the serve right after a removed toss)
    for k, f in enumerate(flights):
        prev = flights[k - 1] if k > 0 else None
        if prev is not None and prev["dir"] != f["dir"] and ball[f["i0"]].t - ball[prev["i1"]].t <= MAX_BRIDGE_S:
            continue
        end = "near" if f["dir"] < 0 else "far"
        b0 = ball[f["i0"]]
        p = _player_at(players, end, b0.t)
        if p is None:
            continue
        bb = p["bbox"]
        lim = _hit_lim(end, bb) * 1.3
        dist = _dist_to_player(end, bb, *b0.xy_px)
        if dist > lim:
            continue
        if abs(ball[f["i1"]].xy_px[1] - b0.xy_px[1]) < 3 * MIN_FLIGHT_PX:
            continue
        conf = float(np.clip(0.35 + 0.25 * (1 - dist / lim), 0.3, 0.6))
        ev = _mk_hit(b0.t - 1.0 / fps, end, p, b0.xy_px[0], b0.xy_px[1], hand, conf, 0.8)
        outgoing(ev, f)
        ev._start = True
        events.append(ev)
    strong = [e for e in events if not getattr(e, "_kink", False)]
    events = strong + [e for e in events if getattr(e, "_kink", False) and
                       not any(q._end == e._end and abs(q.t - e.t) < SAME_END_GAP for q in strong)]
    events.sort(key=lambda e: e.t)
    for toss_end in toss_ends:
        for h in events:
            if toss_end - 0.15 <= h.t <= toss_end + 0.6 and not getattr(h, "_after_toss", False):
                h.conf = float(min(0.95, h.conf + 0.3))
                h._after_toss = True
    # ---- one contact per player turn ----------------------------------------
    # Two candidate contacts at the same end within SAME_END_GAP: only one is
    # real (the ball cannot come back that fast). The real one sends the ball
    # back over the net, so the longer outgoing flight wins; on a tie the later
    # one (a bounce or a dropping lob in front of the player comes first).
    ded: list = []
    for h in events:
        if ded:
            q = ded[-1]
            if q._end == h._end and h.t - q.t < SAME_END_GAP:
                if h._out >= 0.7 * q._out or (getattr(h, "_after_toss", False) and not getattr(q, "_after_toss", False)):
                    ded[-1] = h
                continue
            if h.t - q.t < MIN_GAP:
                if h.conf > q.conf:
                    ded[-1] = h
                continue
        ded.append(h)
    events = ded
    # ---- bounces ------------------------------------------------------------
    for f in flights:
        events.extend(_bounces_in(ball, f, fps, H, players))
    hit_ts = [e.t for e in events if e.kind == "hit"]
    for b in extra_bounces:
        if all(abs(b.t - t) > 0.2 for t in hit_ts) and all(abs(b.t - e.t) > 0.2 for e in events if e.kind == "bounce"):
            events.append(b)
    events.sort(key=lambda e: e.t)
    # ---- serve: first hit, by the server end, from behind the baseline ---------
    hits = [e for e in events if e.kind == "hit"]
    if hits:
        h0 = hits[0]
        ok_end = server_end is None or h0._end == server_end
        if ok_end and h0.court_xy is not None and abs(h0.court_xy[1]) >= SERVE_BEHIND:
            h0.kind, h0.stroke, h0.wing = "serve", "serve", None
            h0.conf = float(min(0.9, h0.conf + (0.15 if toss_t is not None else 0.0)))
    # second serve: another server hit from behind the baseline > 3 s later with no
    # opponent hit in between (the first serve was a fault)
    serves = [e for e in events if e.kind == "serve"]
    if serves:
        s_end, last_t = serves[0]._end, serves[0].t
        for ev in events:
            if ev.t <= last_t or ev.kind == "bounce":
                continue
            if ev._end != s_end:
                break
            if ev.t - last_t > 3.0 and ev.court_xy is not None and abs(ev.court_xy[1]) >= SERVE_BEHIND:
                ev.kind, ev.stroke, ev.wing = "serve", "serve", None
                last_t = ev.t
    # ---- volleys: hitter inside ~service line, no bounce since the last hit ----
    last_hit_t, bounced = None, True
    for ev in events:
        if ev.kind == "bounce":
            bounced = True
            continue
        if ev.kind == "hit" and ev.stroke == "groundstroke" and ev.court_xy is not None:
            if abs(ev.court_xy[1]) < SERVICE_LINE + 0.5 and not bounced and last_hit_t is not None:
                ev.stroke = "volley"
        last_hit_t, bounced = ev.t, False
    return events
