"""Chart one point from its rally window: players + ball + events -> PointVision.

Conservative by design: every derived field carries a confidence in
PointVision.conf and falls back to None / 'unknown' when the evidence is weak.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

from .contracts import (CourtFit, PlayerId, PointVision, ServeVision, ShotEvent, HALF_LEN,
                        SERVICE_LINE, SINGLES_HALF_W, other, px_unit)
from . import players as P
from . import ball as B
from . import events as E

LAST_STATS: dict = {}
LINE_TOL = 0.12          # metres of slack for homography / bounce-timing error
FAR_DEPTH_UNSURE = True   # far-court serve bounce past the service line: in or fault unknown
FPS_BALL = 25.0


# --------------------------------------------------------------------------- geometry
def service_box(server_end: str, side: str) -> tuple[float, float, float, float]:
    """Target service box (x0, x1, y0, y1) in court metres.

    Near server on the deuce side stands at +x (his right) and serves crosscourt
    into the far deuce box, which is at -x (the far receiver's right = image left).
    """
    if server_end == "near":
        xs = (-SINGLES_HALF_W, 0.0) if side == "deuce" else (0.0, SINGLES_HALF_W)
        ys = (0.0, SERVICE_LINE)
    else:
        xs = (0.0, SINGLES_HALF_W) if side == "deuce" else (-SINGLES_HALF_W, 0.0)
        ys = (-SERVICE_LINE, 0.0)
    return xs[0], xs[1], ys[0], ys[1]


def classify_serve_bounce(xy, server_end: str, side: str):
    """-> (result, zone, fault, conf). result in/fault; zone wide/body/T; fault long/wide/long_wide/net."""
    x, y = xy
    x0, x1, y0, y1 = service_box(server_end, side)
    # ball bounced on the server's own half: it did not clear the net
    if (server_end == "near" and y < -0.3) or (server_end == "far" and y > 0.3):
        return "fault", None, "net", 0.5
    long_ = abs(y) > SERVICE_LINE + LINE_TOL
    wide = x < x0 - LINE_TOL or x > x1 + LINE_TOL
    # distance to the nearest box line (confidence falls near lines)
    dline = min(abs(abs(y) - SERVICE_LINE), abs(x - x0), abs(x - x1))
    conf = float(np.clip(0.45 + 0.5 * dline / 0.6, 0.45, 0.85))
    if long_ or wide:
        fault = "long_wide" if (long_ and wide) else ("long" if long_ else "wide")
        return "fault", None, fault, conf
    ax = abs(x)
    third = SINGLES_HALF_W / 3.0
    zone = "T" if ax < third else ("body" if ax < 2 * third else "wide")
    dz = min(abs(ax - third), abs(ax - 2 * third))
    return "in", zone, None, conf * float(np.clip(0.6 + dz / 0.5, 0.6, 1.0))


def classify_rally_bounce(xy, hitter_end: str):
    """Result of a rally shot from its first bounce: in / out_long / out_wide / net."""
    x, y = xy
    if (hitter_end == "near" and y < -0.3) or (hitter_end == "far" and y > 0.3):
        return "net", 0.45
    long_ = abs(y) > HALF_LEN + LINE_TOL
    wide = abs(x) > SINGLES_HALF_W + LINE_TOL
    d = min(abs(abs(y) - HALF_LEN), abs(abs(x) - SINGLES_HALF_W))
    conf = float(np.clip(0.4 + 0.5 * d / 0.8, 0.4, 0.8))
    if long_:
        return "out_long", conf
    if wide:
        return "out_wide", conf
    return "in", conf


def _end_of(ev) -> Optional[str]:
    return getattr(ev, "_end", None)



def _net_y(H: np.ndarray, x_px: float) -> float:
    """Image row of the net base under image column x_px."""
    Hi = np.linalg.inv(H)
    cx = float((H @ np.array([x_px, 300.0, 1.0]))[0] / (H @ np.array([x_px, 300.0, 1.0]))[2])
    q = Hi @ np.array([cx, 0.0, 1.0])
    return float(q[1] / q[2])


def _crossed(ball, ev, H) -> Optional[bool]:
    """Did the ball leaving contact `ev` clear the net to the other half?
    True / False (stayed on the hitter's side: netted) / None (no evidence)."""
    seg = [b for b in ball if ev.t < b.t <= getattr(ev, "_out_end_t", ev.t) + 0.05 and b.conf > 0.31]
    if len(seg) < 4:
        return None
    ys = np.array([b.xy_px[1] for b in seg])
    ny = _net_y(H, float(np.median([b.xy_px[0] for b in seg])))
    far_sl = float(np.asarray(_proj(H, 0.0, SERVICE_LINE))[1])
    near_sl = float(np.asarray(_proj(H, 0.0, -SERVICE_LINE))[1])
    # image row of the net tape: ground row minus 0.914 m at the horizontal
    # px/m scale of the net line (an upper bound of the vertical scale)
    xl, _ = _proj(H, -SINGLES_HALF_W, 0.0)
    xr, _ = _proj(H, SINGLES_HALF_W, 0.0)
    tape = ny - 0.914 * abs(xr - xl) / (2 * SINGLES_HALF_W)
    if _end_of(ev) == "near":
        if ys.min() < far_sl:            # into the far court (or well above it)
            return True
        # never projected above the tape row: it cannot have cleared the net
        return False if ys.min() > tape else None
    if ys.max() > near_sl:
        return True
    return False if ys.max() < ny + 8 else None


def _proj(H, x, y):
    Hi = np.linalg.inv(H)
    q = Hi @ np.array([x, y, 1.0])
    return q[0] / q[2], q[1] / q[2]


def _reaches_net(ball, ev, H) -> Optional[bool]:
    """Does the ball leaving contact `ev` get at least to the net? Any shot,
    even one into the net, does; a diving miss or a tracker hop near a body does
    not. None when the outgoing flight was not observed."""
    seg = [b for b in ball if ev.t < b.t <= getattr(ev, "_out_end_t", ev.t) + 0.05 and b.conf > 0.31]
    if len(seg) < 3:
        return None
    ny = _net_y(H, float(np.median([b.xy_px[0] for b in seg])))
    ys = np.array([b.xy_px[1] for b in seg])
    if _end_of(ev) == "near":
        return bool(ys.min() <= ny)
    xl, _ = _proj(H, -SINGLES_HALF_W, 0.0)
    xr, _ = _proj(H, SINGLES_HALF_W, 0.0)
    tape = ny - 0.914 * abs(xr - xl) / (2 * SINGLES_HALF_W)
    return bool(ys.max() >= tape)


def _trim_tail(rally: list, ball, H) -> list:
    """Drop contacts after the end of play: a final 'hit' must send the ball off
    on a real flight that reaches the net, and must come within a plausible
    flight time of the previous shot (diving misses, players knocking the ball
    away after the point and tracker hops onto bodies fail this)."""
    out = list(rally)
    while len(out) >= 2:
        last, prev = out[-1], out[-2]
        weak = getattr(last, "_n_out", 99) < 6 or getattr(last, "_out", 999.0) < 2 * E.MIN_FLIGHT_PX
        late = last.t - prev.t > MAX_SHOT_GAP
        short = not getattr(last, "_implied", False) and _reaches_net(ball, last, H) is False
        if weak or late or short:
            out.pop()
            continue
        break
    return out


MAX_SHOT_GAP = 2.6       # s; longest plausible time between a shot and the reply (deep lob ~2.5 s)


SERVE_GATE = True         # a serve must not fail the set-up check (serve_setup_ok)
SETUP_CONF = 0.8          # serve_setup_end confidence the set-up check needs
STILL_MPS = 1.0           # both players' median foot speed before a serve, m/s


def serve_setup_ok(dets, t: float, t_min: float, server_end: str, side: str) -> Optional[bool]:
    """Was the hit at t preceded by a serve set-up? Looks at the players in the
    2.5 s ending 0.5 s before t, inside the window (pipeline.setup_slice). True
    when all three hold, by the rules of tennis:
      - the set-up rule (serve_setup_end) names the server's end with
        confidence >= SETUP_CONF (both players near their baselines, the server
        nearer the centre mark);
      - the server stands on the half the score requires (deuce: his right) and
        the receiver on the other half;
      - both stand nearly still (median foot speed <= STILL_MPS).
    False when one fails. None when the window has too little footage before t."""
    from .pipeline import serve_setup_end, setup_slice
    sub = setup_slice(dets, t, t_min)
    if sub is None:
        return None
    se, c = serve_setup_end(sub)
    if se != server_end or c < SETUP_CONF:
        return False
    sign = 1.0 if side == "deuce" else -1.0
    if server_end == "far":
        sign = -sign
    recv_end = "far" if server_end == "near" else "near"
    for end, want in ((server_end, sign), (recv_end, -sign)):
        seq = sorted((d.t, d.court_xy) for d in sub if d.end == end and not d.interp)
        tt = np.array([q[0] for q in seq])
        xy = np.array([q[1] for q in seq], float)
        if np.sign(np.median(xy[:, 0])) != want:
            return False
        sp = np.hypot(*np.diff(xy, axis=0).T) / np.maximum(np.diff(tt), 1e-3)
        if float(np.median(sp)) > STILL_MPS:
            return False
    return True


def _zone_from_x(ax: float) -> str:
    third = SINGLES_HALF_W / 3.0
    return "T" if ax < third else ("body" if ax < 2 * third else "wide")


# --------------------------------------------------------------------------- main
def chart_point(path: str, t_start: float, t_end: float, fit: CourtFit, server: str,
                side: str, ends: dict, players_hand: Optional[dict] = None,
                identity: Optional[P.IdentityModel] = None, player_fps: float = 5.0,
                known_winner: Optional[PlayerId] = None) -> PointVision:
    """Chart one rally window.

    known_winner: the point winner when it is known from the score bug. It is
    used as a prior on the ending: the point ends with a winner/ace/service
    winner by known_winner or an error/double fault by the other player, so
    the remaining question is who hit the last ball and whether it landed in."""
    t0 = time.time()
    times, cands, dets = detect_window(path, t_start, t_end, fit, player_fps,
                                       with_features=identity is not None)
    t1 = time.time()
    ball = track_window(times, cands, dets, px_unit(fit.frame_h))
    t2 = time.time()
    pv = chart_tracked(path, t_start, t_end, fit, server, side, ends, dets, ball,
                       players_hand=players_hand, identity=identity, known_winner=known_winner)
    LAST_STATS.update(detect_s=t1 - t0, ball_s=t2 - t1, n_ball=len(ball), n_players=len(dets))
    LAST_STATS["total_s"] = time.time() - t0
    return pv


def detect_window(path: str, t_start: float, t_end: float, fit: CourtFit,
                  player_fps: float = 5.0, with_features: bool = False):
    """The only part of charting that reads the video: player boxes at player_fps
    and the raw ball candidates (before the player-box removal) of one window.
    Returns (times, cands, dets)."""
    # YOLO (MPS) and classical ball candidates (CPU) run concurrently; player-body
    # suppression is applied to the candidates afterwards.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(P.detect_players, path, t_start, t_end, fit, player_fps,
                        480, None, with_features)
        times, cands = B.detect_candidates(path, t_start, t_end, fit, players=None)
        dets = fut.result()
    return times, cands, dets


def track_window(times, cands, dets, unit: float = 1.0):
    """Ball track of one window from its detections (no video access, does not
    depend on who is at which end). unit: contracts.px_unit of the video."""
    return B.track_from_candidates(times, B.suppress_players(times, cands, dets), unit=unit)


def chart_tracked(path: str, t_start: float, t_end: float, fit: CourtFit, server: str,
                  side: str, ends: dict, dets, ball, players_hand: Optional[dict] = None,
                  identity: Optional[P.IdentityModel] = None,
                  known_winner: Optional[PlayerId] = None,
                  ends_conf: Optional[float] = None, setup_prev=None) -> PointVision:
    """Chart one window from its detections and ball track. Reads the video only
    for the pose fallback of the wings. With an appearance model (identity), a
    confident appearance vote overrides `ends`."""
    t2 = time.time()
    ends_obs, obs_conf = None, 0.0
    if identity is not None:
        ends_obs, obs_conf = identity.assign_ends(dets)
    use_ends = ends if (ends_obs is None or obs_conf < 0.5) else ends_obs
    if ends_obs is not None:
        ends_conf = obs_conf
    pid_of = {e: p for p, e in use_ends.items()}
    for d in dets:
        d.pid = pid_of.get(d.end)
    pv = chart_from_tracks(t_start, t_end, fit, server, side, use_ends, dets, ball,
                           players_hand=players_hand, known_winner=known_winner,
                           ends_conf=ends_conf, setup_prev=setup_prev)
    t3 = time.time()
    n_pose = 0
    if POSE_WING:
        n_pose = _pose_wings(path, pv, players_hand or {"A": "R", "B": "R"})
    t4 = time.time()
    LAST_STATS.update(total_s=t4 - t2, events_s=t3 - t2, pose_s=t4 - t3, n_pose=n_pose,
                      n_ball=len(ball), n_players=len(dets), n_events=len(pv.shots))
    return pv


POSE_WING = True          # pose fallback for return / last-shot wing when the ball gives no side
POSE_DT = -0.04           # pose is read slightly before the estimated contact (follow-through flips sides)


def _pose_wings(path, pv, players_hand) -> int:
    """Fill a missing return / last-shot wing from yolo11n-pose on the hitter crop.

    Ball-vs-body is the primary wing cue (35/38 on found GT shots); pose alone
    was 24/32 at the GT contact times on this video, so it is only a fallback
    for contacts where the ball side is undecided. Returns the number of pose
    reads."""
    rally = getattr(pv, "_rally", None) or []
    targets = []
    if pv.return_wing is None and len(rally) >= 2 and getattr(rally[1], "_end", None) == getattr(pv, "_recv_end", None):
        targets.append(("return", rally[1]))
    if pv.last_shot_wing is None and rally and rally[-1].kind == "hit" and pv.last_shot_by == rally[-1].by:
        targets.append(("last", rally[-1]))
    n = 0
    for kind, h in targets:
        bb = getattr(h, "_bbox", None)
        if bb is None or h.wing is not None:
            continue
        hand = players_hand.get(h.by, "R") if h.by else "R"
        try:
            crops = P.contact_crops(path, h.t + POSE_DT, bb)
            w, c, _ = P.pose_wing(crops, bb, h._end, hand)
        except Exception:      # pose is optional: never fail the chart
            w, c = None, 0.0
        n += 1
        if w is None:
            continue
        h.wing, h._wconf, h._wing_src = w, 0.8 * c, "pose"
        if kind == "return":
            pv.return_wing = w
            pv.conf["return_wing"] = round(0.8 * c * h.conf, 2)
        if (kind == "last" or h is rally[-1]) and pv.last_shot_by == h.by:
            pv.last_shot_wing = w
            pv.conf["last_shot_wing"] = round(0.8 * c * h.conf, 2)
    return n


def chart_from_tracks(t_start: float, t_end: float, fit: CourtFit, server: str, side: str,
                      use_ends: dict, dets, ball, players_hand: Optional[dict] = None,
                      known_winner: Optional[str] = None, ends_conf: Optional[float] = None,
                      setup_prev=None) -> PointVision:
    """Everything after detection: events, serves, rally, ending (no video access).
    setup_prev: None, or (t_min, player detections) from the end of the window just
    before when it is the same camera shot (pipeline.setup_prev_of); the set-up check
    of a hit early in this window may then read them, from t_min on.
    The pixel rules here and in events.py were set on 720-row video; a video of
    another size is charted in 720-row pixels (events.to_ref_units) and the pixel
    values kept on the events are turned back into its own pixels."""
    u = px_unit(getattr(fit, "frame_h", None))
    if u == 1.0:
        return _chart_from_tracks(t_start, t_end, fit, server, side, use_ends, dets, ball,
                                  players_hand, known_winner, ends_conf, setup_prev)
    fit_r, dets_r, ball_r = E.to_ref_units(fit, dets, ball, u)
    sp_r = None
    if setup_prev is not None:
        sp_r = (setup_prev[0], E.to_ref_units(fit, setup_prev[1], [], u)[1])
    pv = _chart_from_tracks(t_start, t_end, fit_r, server, side, use_ends, dets_r, ball_r,
                            players_hand, known_winner, ends_conf, sp_r)
    E.events_to_px(list(pv.shots or []) + list(getattr(pv, "_rally", None) or []), u)
    return pv


def _chart_from_tracks(t_start: float, t_end: float, fit: CourtFit, server: str, side: str,
                       use_ends: dict, dets, ball, players_hand: Optional[dict] = None,
                       known_winner: Optional[str] = None, ends_conf: Optional[float] = None,
                       setup_prev=None) -> PointVision:
    """chart_from_tracks in 720-row pixels."""
    players_hand = players_hand or {"A": "R", "B": "R"}
    H = np.asarray(fit.H, float)
    server_end = use_ends.get(server)
    recv = other(server)
    recv_end = use_ends.get(recv)
    hand = {e: players_hand.get(p, "R") for p, e in use_ends.items()}
    gate = None
    if SERVE_GATE and server_end in ("near", "far") and side in ("deuce", "ad"):
        g_dets, g_min = dets, t_start
        if setup_prev is not None:
            g_min, prev = setup_prev
            g_dets = list(prev) + list(dets)

        def gate(h):
            return serve_setup_ok(g_dets, h.t, g_min, server_end, side)
    evs = E.detect_events(ball, dets, fit, fps=FPS_BALL, hand=hand, server_end=server_end,
                          serve_ok=gate)

    pv = PointVision(t_start=t_start, t_end=t_end, shots=evs)
    conf: dict = {}
    pv.ends = dict(use_ends)
    conf["ends"] = round(ends_conf if ends_conf is not None else 0.5, 2)

    # ---- movement stats ----------------------------------------------------
    for pid, e in use_ends.items():
        pv.player_dist_m[pid] = round(P.distance_run(dets, e), 1)
        pv.net_approaches[pid] = P.net_approaches(dets, e)
    n_near = sum(1 for d in dets if d.end == "near" and not d.interp)
    n_far = sum(1 for d in dets if d.end == "far" and not d.interp)
    n_fr = max(1, len({round(d.t, 3) for d in dets}))
    conf["player_dist_m"] = round(0.8 * min(n_near, n_far) / n_fr, 2)
    conf["net_approaches"] = conf["player_dist_m"]

    hits = [e for e in evs if e.kind in ("serve", "hit")]
    bounces = [e for e in evs if e.kind == "bounce"]

    def first_bounce_after(t, t_next):
        for b in bounces:
            if t < b.t < (t_next if t_next is not None else 1e9):
                return b
        return None

    # ---- implied serve: the first shot we see is the receiver's return --------
    serve_evs = [e for e in hits if e.kind == "serve"]
    if not serve_evs and hits and _end_of(hits[0]) == recv_end and hits[0].t - t_start < 3.0:
        imp = ShotEvent(t=max(t_start, hits[0].t - 0.9), kind="serve", by=server,
                        stroke="serve", conf=0.3)
        imp._end, imp._implied, imp._wconf = server_end, True, 0.0
        imp._n_out, imp._out, imp._out_end_t = 99, 999.0, hits[0].t
        evs.insert(0, imp)
        hits.insert(0, imp)
        serve_evs = [imp]

    # ---- rally after the last serve, without contacts after the end of play ---
    last_serve_t = serve_evs[-1].t if serve_evs else None
    rally = [h for h in hits if last_serve_t is None or h.t >= last_serve_t]
    trimmed = _trim_tail(rally, ball, H)
    dropped = [h for h in rally if h not in trimmed]
    rally = trimmed
    if dropped:
        drop_ids = {id(h) for h in dropped}
        pv.shots = evs = [e for e in evs if id(e) not in drop_ids]
        hits = [h for h in hits if id(h) not in drop_ids]

    # ---- serves ------------------------------------------------------------
    for k, s in enumerate(serve_evs[:2]):
        nxt = [h for h in rally if h.t > s.t + 0.15] if s is serve_evs[-1] else \
              [h for h in hits if h.t > s.t + 0.15 and h.kind != "serve"]
        t_next = nxt[0].t if nxt else None
        returned = bool(nxt) and _end_of(nxt[0]) == recv_end
        rallied = returned and len(nxt) >= 2 and _end_of(nxt[1]) == server_end
        ret_ev = nxt[0] if returned else None
        if not returned and nxt and _end_of(nxt[0]) == server_end and nxt[0].kind == "hit" \
                and nxt[0].t - s.t > 1.2:
            # the server played another rally shot: the return (missed by us) happened
            returned = rallied = True
        b = first_bounce_after(s.t + 0.25, t_next)
        if b is None and not getattr(s, "_implied", False):
            b = E.tail_bounce(ball, s.t + 0.25, t_next if t_next is not None else s.t + 2.0, fit, FPS_BALL)
            if b is not None:
                rx = b.court_xy[1]
                if (server_end == "near" and rx < 0) or (server_end == "far" and rx > 0):
                    b = None       # only trusted on the receiver's half
        sv = ServeVision(n=k + 1, t=s.t, result="unknown", conf=0.2)
        bounce_zone = None
        depth_unsure = False
        if b is not None:
            res, zone, fault, c = classify_serve_bounce(b.court_xy, server_end, side)
            sv.bounce_xy = tuple(round(v, 2) for v in b.court_xy)
            if res == "in":
                sv.result, sv.zone, sv.conf = "in", zone, round(c * min(1.0, b.conf + 0.3), 2)
                bounce_zone = zone
            else:
                sv.result, sv.fault, sv.conf = "fault", fault, round(c * min(1.0, b.conf + 0.3) * 0.8, 2)
                x0, x1, _, _ = service_box(server_end, side)
                inside_x = x0 - LINE_TOL <= b.court_xy[0] <= x1 + LINE_TOL
                # the box call is unreliable (far-court bounce timing ~1 m), but a
                # receiver often plays a fault back: only a rally (the server
                # hitting again) overrules the bounce
                if rallied:
                    sv.result, sv.fault, sv.conf = "in", None, 0.6
                    if inside_x:
                        bounce_zone = sv.zone = _zone_from_x(abs(b.court_xy[0]))
                elif FAR_DEPTH_UNSURE and server_end == "near" and fault == "long" and inside_x:
                    # In the far court the ball is small and seen through the net, and
                    # the bounce is found late, so it is placed too deep: on the tune
                    # games the found bounces of serves that were in lay a median 7.6 m
                    # from the net, past the 6.4 m service line, and overlapped the
                    # faults almost completely. Depth cannot tell in from long there, so
                    # in or fault is "could not tell". The side to side position is
                    # still good and gives the direction.
                    sv.result, sv.fault = "unknown", None
                    bounce_zone = sv.zone = _zone_from_x(abs(b.court_xy[0]))
                    depth_unsure = True
        if sv.result != "fault" and ret_ev is not None and ret_ev.court_xy is not None:
            # receiver position at the return: pulled outside the singles line ->
            # wide, still near the centre -> T. Used when no bounce was seen and
            # as a consistency check of the bounce zone.
            rx = abs(ret_ev.court_xy[0])
            pos_zone = "wide" if rx >= SINGLES_HALF_W + 0.5 else ("T" if rx <= 2.0 else None)
            if bounce_zone is None and pos_zone is not None:
                sv.zone = pos_zone
                sv.conf = round(max(0.3, min(sv.conf, 0.45)), 2)
            elif bounce_zone is not None and pos_zone is not None and \
                    {bounce_zone, pos_zone} == {"T", "wide"}:
                sv.conf = round(min(sv.conf, 0.4), 2)     # contradictory evidence
            if sv.result == "unknown" and not depth_unsure:
                sv.result, sv.conf = "in", max(sv.conf, 0.35)
        # a return alone does not make it "in": a receiver often knocks a fault back
        if sv.result == "unknown" and returned and not depth_unsure:
            sv.result, sv.conf = "in", (0.55 if rallied else 0.35)
        if sv.result == "in" and rallied:
            sv.conf = round(max(sv.conf, 0.6), 2)
        if getattr(s, "_implied", False):
            sv.conf = round(min(sv.conf, 0.4), 2)
            if b is None and not rallied:
                # serve not seen, only a return: a returned fault looks the same
                sv.result, sv.zone, sv.conf = "unknown", None, 0.2
        pv.serves.append(sv)
    # a later serve after an "in" first serve means our first call was wrong
    if len(pv.serves) == 2 and pv.serves[0].result != "fault":
        pv.serves[0].result, pv.serves[0].zone, pv.serves[0].conf = "fault", None, 0.4

    # a fault that the receiver merely knocked back (common): the point did not
    # go on, so that return is not a rally shot
    if pv.serves and pv.serves[-1].result == "fault" and len(rally) >= 2 and \
            not any(_end_of(h) == server_end for h in rally[1:]):
        rally = rally[:1]

    # ---- rally length --------------------------------------------------------
    # consecutive same-end contacts (> SAME_END_GAP apart) mean the opponent's
    # shot in between was missed: count it
    missed = sum(1 for h0, h1 in zip(rally[:-1], rally[1:]) if _end_of(h0) == _end_of(h1))
    if serve_evs:
        pv.rally_len = len(rally) + missed
        base = float(np.mean([h.conf for h in rally]))
        conf["rally_len"] = round(float(np.clip(base - 0.2 - 0.1 * missed - 0.05 * max(0, len(rally) - 4),
                                                0.1, 0.6)), 2)
    else:
        pv.rally_len = None
        conf["rally_len"] = 0.0

    # ---- return wing ---------------------------------------------------------
    if serve_evs and len(rally) >= 2 and _end_of(rally[1]) == recv_end and rally[1].wing:
        pv.return_wing = rally[1].wing
        conf["return_wing"] = round(getattr(rally[1], "_wconf", 0.4) * rally[1].conf, 2)
    else:
        conf["return_wing"] = 0.0

    # ---- last shot and ending ----------------------------------------------
    _ending(pv, conf, rally, serve_evs, bounces, ball, H, server, recv, server_end, recv_end,
            use_ends, dets, known_winner, t_end, hand)
    pv.conf = conf
    pv._rally, pv._recv_end = rally, recv_end      # internal: used by the pose fallback
    return pv


def _ending(pv, conf, rally, serve_evs, bounces, ball, H, server, recv, server_end, recv_end,
            use_ends, dets, known_winner, t_end, hand=None):
    last = rally[-1] if rally else None
    double_fault = len(pv.serves) == 2 and all(s.result == "fault" for s in pv.serves)
    W = known_winner if known_winner in ("A", "B") else None
    end_of = {p: e for p, e in use_ends.items()}

    def bounce_after(ev):
        for b in bounces:
            if ev.t + 0.15 < b.t:
                return b
        return None

    def result_of(ev):
        """(result, conf) of the shot `ev` from its first bounce / net crossing."""
        if ev.kind == "serve" and pv.serves:
            sv = pv.serves[-1]
            r = ("in" if sv.result == "in" else
                 ("net" if sv.fault == "net" else
                  ("out_wide" if sv.fault in ("wide", "long_wide") else
                   ("out_long" if sv.fault == "long" else "unknown"))))
            return r, sv.conf
        b = bounce_after(ev)
        cr = _crossed(ball, ev, H)
        if b is not None:
            r, c = classify_rally_bounce(b.court_xy, _end_of(ev))
            return r, round(c * b.conf + 0.1, 2)
        if cr is False:
            return "net", 0.4
        return "unknown", 0.1

    if last is None:
        pv.ending, pv.last_shot_result = ("double_fault" if double_fault else "unknown"), "unknown"
        pv.inferred_winner = recv if double_fault else W
        conf["ending"] = 0.4 if double_fault else 0.1
        conf["inferred_winner"] = 0.9 if W else (0.4 if double_fault else 0.0)
        conf["last_shot_result"] = 0.1
        return

    # who hit the last ball: the last validated contact. Rally-length parity
    # (odd = server hit last) is only a consistency check.
    by = last.by
    c_by = 0.5 * last.conf + 0.25 * min(1.0, getattr(last, "_n_out", 0) / 15.0)
    if getattr(last, "_implied", False):
        c_by = 0.3
    if pv.rally_len:
        par_by = server if pv.rally_len % 2 == 1 else recv
        c_by += 0.1 if par_by == by else -0.15
    res, c_res = result_of(last)

    ending, econf, winner = "unknown", 0.1, None
    if double_fault and (W is None or W == recv):
        ending, econf, winner = "double_fault", 0.5 * min(s.conf for s in pv.serves) + 0.2, recv
        by, res = server, ("net" if pv.serves[-1].fault == "net" else res)
    elif W is not None:
        winner = W
        if by == W:
            # the winner's ball was the last: it landed in and was not returned
            res, c_res = "in", max(c_res if res == "in" else 0.0, 0.5 * c_by + 0.2)
            if last.kind == "serve":
                ending = "ace"
            else:
                ending = "winner"
            econf = c_by
        elif last.kind != "serve" and _unseen_reply(last, bounce_after(last), res, c_res,
                                                     _crossed(ball, last, H)):
            # the loser's ball came back into play: the winner's reply (a winner)
            # was not detected. Count it.
            b = bounce_after(last)
            own = (_end_of(last) == "near") == (b.court_xy[1] < 0)
            by, ending = W, "winner"
            t0, t1 = (last.t, b.t) if own else (b.t - 0.2, b.t + MAX_SHOT_GAP)
            reply = _reply_contact(ball, dets, end_of.get(W), t0, t1, hand or {})
            if reply is not None:
                reply.by = W
                rally.append(reply)
                pv.shots = sorted(pv.shots + [reply], key=lambda e: e.t)
                last = reply
            res, c_res = ("in", 0.4) if not own else classify_rally_bounce(b.court_xy, end_of.get(W))
            if res != "in":
                res, c_res = "in", 0.3           # the winner's ball cannot have been out
            pv.rally_len = (pv.rally_len or len(rally)) + 1
            conf["rally_len"] = round(min(conf.get("rally_len", 0.3), 0.4), 2)
            econf = c_by = 0.5
        else:
            # the loser hit the last ball: an error (net / out)
            if res == "in":
                res, c_res = "unknown", 0.1      # contradicts the known winner
            if last.kind == "serve":
                ending = "double_fault" if len(pv.serves) == 2 else "unknown"
            else:
                ending = _forced_or_unforced(last, end_of.get(W), dets, rally)
            econf = c_by * (0.8 if ending != "unknown" else 0.0)
    elif res != "unknown" and len(rally) >= 2:
        if res == "in":
            ending, econf, winner = "winner", 0.3, by
        else:
            ending, econf, winner = _forced_or_unforced(last, end_of.get(other(by)) if by else None, dets, rally), 0.25, \
                (other(by) if by else None)
    elif serve_evs and len(rally) == 1 and pv.serves and pv.serves[-1].result == "in":
        ending, econf, winner = "ace", 0.3, server

    pv.last_shot_by = by
    pv.last_shot_wing = last.wing if by == last.by else None
    pv.last_shot_stroke = last.stroke if by == last.by else None
    pv.last_shot_result = res
    pv.ending = ending
    pv.inferred_winner = winner
    conf["last_shot_by"] = round(float(np.clip(c_by, 0.1, 0.9)), 2)
    conf["last_shot_wing"] = round(getattr(last, "_wconf", 0.0) * last.conf, 2) if pv.last_shot_wing else 0.0
    conf["last_shot_stroke"] = round(0.5 * last.conf + (0.2 if last.stroke == "volley" else 0.0), 2) \
        if pv.last_shot_stroke else 0.0
    conf["last_shot_result"] = round(float(np.clip(c_res, 0.1, 0.9)), 2)
    conf["ending"] = round(float(np.clip(econf, 0.1, 0.9)), 2) if ending != "unknown" else 0.1
    conf["inferred_winner"] = 0.95 if (W and winner == W) else round(econf if winner else 0.0, 2)


NET_DROP_M = 3.0          # a netted ball drops within this distance of the net
REPLY_MIN_S = 0.6         # s from contact to the bounce: shorter is a netted ball or a
                          # bounce misread on the rising ball, and no time for a reply
REPLY_MIN_CONF = 0.35     # bounce confidence needed to say the loser's ball landed in


def _unseen_reply(last, b, res, c_res, crossed) -> bool:
    """The known loser hit the last detected ball, but the ball came back into
    play: it landed in (confidently), or it later bounced deep on the loser's
    own half after clearing the net. Either way the winner replied unseen."""
    if b is None or b.court_xy is None:
        return False
    if b.t - last.t < REPLY_MIN_S or crossed is False:
        return False
    if res == "in":
        return c_res >= REPLY_MIN_CONF
    own = (_end_of(last) == "near") == (b.court_xy[1] < 0)
    return own and abs(b.court_xy[1]) > NET_DROP_M


def _reply_contact(ball, dets, end, t0, t1, hand):
    """Best guess of an undetected contact by the player at `end` between t0
    and t1: the tracked ball position closest to his reach zone. None unless the
    ball actually came within reach."""
    best = None
    for o in ball:
        if not (t0 + 0.3 < o.t < t1 - 0.1) or o.conf <= 0.31:
            continue
        p = E._player_at(dets, end, o.t)
        if p is None:
            continue
        d = E._dist_to_player(end, p["bbox"], *o.xy_px)
        if d <= E._hit_lim(end, p["bbox"]) and (best is None or d < best[0]):
            best = (d, o, p)
    if best is None:
        return None
    _, o, p = best
    ev = E._mk_hit(o.t, end, p, o.xy_px[0], o.xy_px[1], hand, conf=0.4)
    ev._implied = True
    return ev


def _forced_or_unforced(last, opp_end, dets, rally) -> str:
    """Error type of the loser's last shot. Forced when the opponent was at the
    net (volley/approach pressure) or the hitter was stretched wide."""
    if last.court_xy is not None and abs(last.court_xy[0]) > SINGLES_HALF_W + 0.5:
        return "forced_error"
    if opp_end is not None:
        # net pressure: the opponent at (or closing in on) the net around the shot
        ys = [abs(d.court_xy[1]) for d in dets
              if d.end == opp_end and last.t - 0.3 <= d.t <= last.t + 0.6]
        if ys and min(ys) < SERVICE_LINE + 1.0:
            return "forced_error"
    return "unforced_error"
