"""End-to-end orchestration: video -> match.json + report.html + points.csv.

Stages (each cached as JSON in <out>/cache/ so re-runs take seconds):
  1. scenes    : main-camera segments                      (scenes.segment)
  2. court     : reference homography of the main camera   (scenes.reference_court)
  3. scorebug  : locate + read the broadcast score bug     (scoreboard)
  4. points    : score engine reconciliation -> boundaries (scoring.reconcile)
                 or, without a score bug, one point per rally window + replay
  5. vision    : per-point charting inside rally windows   (chart.chart_point)
  6. merge     : PointBoundary + PointVision -> PointRecord, consistency checks
  7. render    : stats, insights, report                   (report.render)
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .contracts import (BugLayout, CourtFit, MatchFormat, PointRecord, ScoreObs, Segment,
                        PointBoundary, PointVision, ServeVision, ShotEvent, other, px_unit)

PIPELINE_VERSION = "1.0"
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Options:
    a_name: Optional[str] = None
    b_name: Optional[str] = None
    # None: not given. The vision rules then take the player as right-handed, and the
    # report and the chat say that this is an unchecked default.
    a_hand: Optional[str] = None
    b_hand: Optional[str] = None
    no_ad: bool = False
    final_set: str = "tb"
    best_of: int = 3
    quick: bool = False
    force: bool = False


def log(msg: str) -> None:
    print(f"[tennis] {msg}", flush=True)


# ---------------------------------------------------------------------------
# cache helpers
# ---------------------------------------------------------------------------
class Cache:
    def __init__(self, out: Path, video: str, force: bool):
        self.dir = out / "cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        st = Path(video).stat()
        self.key = f"{Path(video).resolve()}|{st.st_size}|{int(st.st_mtime)}"
        self.force = force
        kf = self.dir / "key.txt"
        if kf.exists() and kf.read_text() != self.key:
            for f in self.dir.glob("*.json"):
                f.unlink()
        kf.write_text(self.key)

    def get(self, name: str):
        f = self.dir / f"{name}.json"
        if self.force or not f.exists():
            return None
        return json.loads(f.read_text())

    def put(self, name: str, obj) -> None:
        (self.dir / f"{name}.json").write_text(json.dumps(obj, default=_jd))


def _jd(o):
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(type(o))


def DETECT(vpath: str, a: float, b: float, fit: CourtFit, with_features: bool):
    """Detection for one rally window -> (times, ball candidates, player dets).
    The only step of the vision stage that decodes the window. A module-level
    function so a development script can wrap it (for example to keep the
    detections of a run on disk and re-chart without decoding again)."""
    from . import chart
    return chart.detect_window(vpath, a, b, fit, with_features=with_features)


# ---------------------------------------------------------------------------
# rally windows
# ---------------------------------------------------------------------------
MIN_WINDOW_S = 2.0


def rally_window(segments: list[Segment], t_prev: float, t_end: float,
                 tail: float = 0.0) -> Optional[tuple[float, float]]:
    """The main-camera footage of the point decided at t_end.

    The broadcast bug updates a few seconds after the point is over, usually
    while the director has cut to a close-up. So the point's rally is the last
    main-camera stretch inside (t_prev, t_end]."""
    best = None
    for s in segments:
        if s.kind != "main":
            continue
        a, b = max(s.start, t_prev), min(s.end, t_end + tail)
        if b - a >= MIN_WINDOW_S:
            best = (a, b)
    return best



MIN_SERVE_S = 1.5


def serve_attempt_windows(segments: list[Segment], t_prev: float, rally: tuple[float, float]
                          ) -> list[tuple[float, float]]:
    """Earlier main-camera shots inside the same point (before its rally shot).

    Broadcasts often show a 1st-serve fault on the main camera, cut to the
    server preparing again, then come back for the 2nd serve. The score did not
    change in between, so a serve seen in such a shot was a fault (or a let)."""
    out = []
    for s in segments:
        if s.kind != "main":
            continue
        a, b = max(s.start, t_prev), min(s.end, rally[0])
        if b - a >= MIN_SERVE_S and b <= rally[0] + 1e-6 and a < rally[0]:
            out.append((a, b))
    return out


def merge_serve_attempts(rally_pv: PointVision, attempts: list[PointVision]) -> PointVision:
    """Fold faults seen in earlier shots into the rally's serve list.
    attempts: charted earlier shots that contained a serve, oldest first."""
    if not attempts:
        return rally_pv
    first = attempts[-1]          # a let + fault would show 2 attempts; the last one counts
    s0 = first.serves[0] if first.serves else None
    fault = ServeVision(n=1, t=s0.t if s0 else first.t_start, result="fault",
                        zone=s0.zone if s0 else None,
                        fault=s0.fault if (s0 and s0.result == "fault") else None,
                        bounce_xy=s0.bounce_xy if s0 else None,
                        conf=0.8 if s0 else 0.6)
    later = []
    for sv in rally_pv.serves:
        if sv.n >= 2:
            continue              # the rally shot can only contain the 2nd serve now
        sv.n = 2
        later.append(sv)
    rally_pv.serves = [fault] + later
    rally_pv.t_start = min(rally_pv.t_start, first.t_start)
    rally_pv.conf["serves"] = round(max(rally_pv.conf.get("serves", 0.0), fault.conf * 0.9), 2)
    return rally_pv


# ---------------------------------------------------------------------------
# player identity: which end is A on, per point
# ---------------------------------------------------------------------------
PROBE_S = 2.5


def serve_setup_end(dets) -> tuple[Optional[str], float]:
    """At the serve set-up the server stands near the centre mark and the
    receiver out wide, so the player with the smaller |x| is serving.
    Returns (server_end, conf). conf is 0 when the evidence is unclear (e.g. the
    camera joined mid-rally)."""
    import numpy as np
    med = {}
    for e in ("near", "far"):
        xs = [abs(d.court_xy[0]) for d in dets if d.end == e and not d.interp]
        ys = [abs(d.court_xy[1]) for d in dets if d.end == e and not d.interp]
        if len(xs) < 3:
            return None, 0.0
        med[e] = (float(np.median(xs)), float(np.median(ys)))
    # both players must be around their baselines for this to be a set-up
    if min(med["near"][1], med["far"][1]) < 10.0:
        return None, 0.0
    margin = med["far"][0] - med["near"][0]
    return ("near" if margin > 0 else "far"), float(min(1.0, abs(margin) / 1.5))


def is_serve_setup(vpath: str, fit: CourtFit, a: float, b: float) -> bool:
    """Does this shot open on a serve set-up (both players on their baselines,
    one at the centre mark)?"""
    from . import players as P
    _se, c = serve_setup_end(P.detect_players(vpath, a, min(b, a + PROBE_S), fit, fps=5))
    return c >= 0.5


def resolve_ends(vpath: str, fit: CourtFit, jobs: list) -> tuple[dict, object]:
    """Per job index -> {"A": end, "B": end}, plus the fitted IdentityModel.

    1. probe the first seconds of every window, read who is serving from
       court geometry, and combine with the score-bug server -> ends;
    2. fit an appearance model (shirt/shorts colours) on the confident windows;
    3. final ends per window = agreement of both, else the more confident one."""
    from . import players as P
    probes, rule = {}, {}
    for i, (a, b), server, _side, _e in jobs:
        dets = P.detect_players(vpath, a, min(b, a + PROBE_S), fit, fps=5)
        probes[i] = dets
        se, c = serve_setup_end(dets)
        if se and server in ("A", "B"):
            rule[i] = ({server: se, other(server): "far" if se == "near" else "near"}, c)
    conf_ids = sorted((i for i, (_, c) in rule.items() if c >= 0.8), key=lambda i: -rule[i][1])[:12]
    ident = None
    if conf_ids:
        samples, labels = [], []
        for i in conf_ids:
            inv = {e: p for p, e in rule[i][0].items()}
            for d in probes[i]:
                if d.feat is not None and not d.interp:
                    samples.append(d)
                    labels.append(inv[d.end])
        if len(set(labels)) == 2:
            ident = P.IdentityModel().fit(samples, labels)
    out = {}
    agree = disagree = 0
    for i, *_ in jobs:
        r_ends, r_c = rule.get(i, (None, 0.0))
        a_ends, a_c = (ident.assign_ends(probes[i]) if ident else (None, 0.0))
        if r_ends and a_ends:
            if r_ends == a_ends:
                agree += 1
                out[i] = (r_ends, max(r_c, a_c))
            else:
                disagree += 1
                out[i] = (r_ends, r_c) if r_c > a_c else (a_ends, a_c)
        elif r_ends or a_ends:
            out[i] = (r_ends, r_c) if r_ends else (a_ends, a_c)
    log(f"identity: {len(rule)}/{len(jobs)} windows by serve geometry, appearance model "
        f"{'fitted' if ident else 'not fitted'}; agree {agree}, disagree {disagree}")
    return out, ident


# ---------------------------------------------------------------------------
# ends from the score engine, turned the right way round by a vote
# ---------------------------------------------------------------------------
# The engine changes ends by the rules (after odd games, every 6 tiebreak points,
# after each set as its game total says), so within a stretch of continuous score
# only one thing is unknown: which end each player started at. Each window votes
# on that with the serve set-up seen just before its first serve-like hit.
VOTE_CONF = 0.8           # serve_setup_end confidence a window needs to vote
VOTE_SHARE = 0.75         # share of the votes one way needed to decide
STRETCH_MIN_VOTES = 5     # a stretch with fewer votes takes the whole match's decision
MATCH_MIN_VOTES = 3       # the whole match's decision needs at least this many votes
SETUP_LOOK_S = 3.0        # the set-up check looks at [hit - 3.0 s, hit - 0.5 s]
SETUP_GAP_S = (0.5, 0.2)  # end of that stretch before the hit; 0.2 s when the window is short


def serve_like_hit(evs):
    """First hit from behind a baseline that is followed, before any other hit,
    by a bounce in the other half within 0.25 to 1.5 s (no further than 2 m past
    the service line, 1 m outside the singles line), or by a hit from the other
    end within 2 s. Only used to place the set-up check in time."""
    from .contracts import SERVICE_LINE, SINGLES_HALF_W
    from .events import SERVE_BEHIND
    hits = [e for e in evs if e.kind in ("serve", "hit") and getattr(e, "_end", None) in ("near", "far")]
    bounces = [e for e in evs if e.kind == "bounce" and e.court_xy is not None]
    for h in hits:
        if h.court_xy is None or abs(h.court_xy[1]) < SERVE_BEHIND:
            continue
        nxt = next((x for x in hits if x.t > h.t + 0.15), None)
        lim = nxt.t if nxt is not None else 1e9
        b = next((x for x in bounces if h.t + 0.25 < x.t < min(lim, h.t + 1.5)), None)
        ok = False
        if b is not None:
            bx, by = b.court_xy
            other_half = by > 0 if h._end == "near" else by < 0
            ok = other_half and abs(by) <= SERVICE_LINE + 2.0 and abs(bx) <= SINGLES_HALF_W + 1.0
        if not ok and nxt is not None and nxt._end != h._end and nxt.t - h.t <= 2.0:
            ok = True
        if ok:
            return h
    return None


def setup_prev_of(segments, prev_win, a: float, b: float):
    """What the set-up check of window [a, b] may read from the window charted just
    before it, prev_win = (its end, its player detections): (a - SETUP_LOOK_S, those
    detections from then on) when that window ended exactly at a (a score change
    inside one camera shot) and [a, b] lies in a main-camera shot that started before
    a (no camera cut at a); else None. A serve hit early in the window then still has
    its set-up footage, which is in the window before."""
    if prev_win is None or abs(prev_win[0] - a) > 1e-6:
        return None
    if not any(s.kind == "main" and s.start < a - 1e-6 and b <= s.end + 1e-6 for s in segments):
        return None
    t_min = a - SETUP_LOOK_S
    return t_min, [d for d in prev_win[1] if d.t >= t_min]


def setup_slice(dets, t: float, t_min: float):
    """The player detections in [t - 3.0, t - 0.5], never before t_min (the
    window start: earlier frames can be another camera shot). When the window
    starts too late for 3 real detections a side, the stretch ends 0.2 s before t
    instead. None when there is too little footage."""
    for gap in SETUP_GAP_S:
        sub = [d for d in dets if max(t_min, t - SETUP_LOOK_S) <= d.t <= t - gap]
        if all(sum(1 for d in sub if d.end == e and not d.interp) >= 3 for e in ("near", "far")):
            return sub
    return None


def setup_before(dets, t: float, t_min: float) -> tuple[Optional[str], float]:
    """serve_setup_end on setup_slice(dets, t, t_min); (None, 0.0) when there is
    too little footage."""
    sub = setup_slice(dets, t, t_min)
    return serve_setup_end(sub) if sub is not None else (None, 0.0)


def window_vote(fit: CourtFit, t_start: float, dets, ball) -> Optional[dict]:
    """One window's vote: the end of the player serving at its first serve-like
    hit, by the set-up rule. The ball events are made without a server end, so
    the toss check looks at both players."""
    from . import events as E
    from .chart import FPS_BALL
    h = serve_like_hit(E.detect_events(ball, dets, fit, fps=FPS_BALL, server_end=None))
    if h is None:
        return None
    se, c = setup_before(dets, h.t, t_start)
    return {"hit_t": round(float(h.t), 3), "end": se, "conf": round(float(c), 3)}


def score_stretches(bounds) -> list[int]:
    """Stretch number per point. A new stretch starts after a resync across which
    the engine could not count the end changes (a jump into another set, into or
    out of a tiebreak, or a score going back). Points the engine filled in and
    forward jumps inside one set keep the stretch."""
    sid, out = 0, []
    for k, b in enumerate(bounds):
        if k > 0 and b.gap_before and b.source != "engine":
            p, c = bounds[k - 1].score_after, b.score_before
            carried = (len(p.get("sets", [])) == len(c.get("sets", []))
                       and not p.get("in_tb") and not c.get("in_tb")
                       and sum(c.get("games", [0, 0])) >= sum(p.get("games", [0, 0])))
            if not carried:
                sid += 1
        out.append(sid)
    return out


def orient_by_votes(bounds, votes: dict) -> tuple[dict, list]:
    """Turn the engine's ends the right way round, per stretch of continuous score.

    votes: {point index: window_vote(...) or None}. A vote agrees with the engine
    when the server end it saw is the engine's end for that point's server. A
    stretch with at least STRETCH_MIN_VOTES votes is decided by its own votes when
    VOTE_SHARE of them go one way, and stays undecided when they are split. A
    stretch with fewer votes takes the whole match's decision (at least
    MATCH_MIN_VOTES votes, VOTE_SHARE one way). Flips the engine ends of the
    stretches that disagree, in place. Returns ({point index: share} for the
    points of decided stretches, log lines)."""
    sids = score_stretches(bounds)
    tally: dict = {}
    for i, v in votes.items():
        if not v or v.get("end") not in ("near", "far") or v.get("conf", 0.0) < VOTE_CONF:
            continue
        if not (0 <= i < len(bounds)):
            continue
        eng = (bounds[i].score_before.get("ends") or {}).get(bounds[i].server)
        if eng not in ("near", "far"):
            continue
        tally.setdefault(sids[i], [0, 0])[0 if v["end"] == eng else 1] += 1

    def decide(t):
        n = t[0] + t[1]
        if n and t[1] >= VOTE_SHARE * n:
            return True, t[1] / n
        if n and t[0] >= VOTE_SHARE * n:
            return False, t[0] / n
        return None

    tot = [sum(t[0] for t in tally.values()), sum(t[1] for t in tally.values())]
    whole = decide(tot) if sum(tot) >= MATCH_MIN_VOTES else None
    voted, flip, lines = {}, set(), []
    for s in sorted(set(sids)):
        t = tally.get(s, [0, 0])
        if sum(t) >= STRETCH_MIN_VOTES:
            d, how = decide(t), "own votes"
        else:
            d, how = whole, "whole match"
        n_pts = sum(1 for x in sids if x == s)
        if d is None:
            lines.append(f"ends: stretch {s} ({n_pts} points) votes agree {t[0]}, disagree {t[1]}: "
                         f"undecided, per-window ends")
            continue
        if d[0]:
            flip.add(s)
        lines.append(f"ends: stretch {s} ({n_pts} points) votes agree {t[0]}, disagree {t[1]}: "
                     f"{'flip' if d[0] else 'keep'} the engine's ends ({how}, share {d[1]:.2f})")
        for k, x in enumerate(sids):
            if x == s:
                voted[k] = round(d[1], 2)
    done = set()                       # score dicts may be shared between points
    for k, b in enumerate(bounds):
        ee = b.score_before.get("ends")
        if sids[k] in flip and ee and id(b.score_before) not in done:
            done.add(id(b.score_before))
            b.score_before["ends"] = {p: ("far" if e == "near" else "near") for p, e in ee.items()}
    return voted, lines


def split_rallies(vpath: str, fit: CourtFit, seg: Segment, fps: float = 2.0) -> list[tuple[float, float]]:
    """Fixed camera without cuts: split a long main-camera stretch into rally
    windows by player activity (both players moving fast = point in play;
    walking/towelling between points = slow). Beta: needs review."""
    import numpy as np
    from . import players as P
    dets = P.detect_players(vpath, seg.start, seg.end, fit, fps=fps, with_features=False)
    if not dets:
        return []
    ts = np.arange(seg.start, seg.end, 1.0 / fps)
    speed = np.zeros(len(ts))
    for e in ("near", "far"):
        tr = sorted((d.t, d.court_xy) for d in dets if d.end == e)
        if len(tr) < 2:
            continue
        t = np.array([x[0] for x in tr])
        xy = np.array([x[1] for x in tr])
        v = np.r_[0, np.hypot(*np.diff(xy, axis=0).T) / np.maximum(np.diff(t), 1e-3)]
        speed += np.interp(ts, t, np.minimum(v, 9.0))
    k = max(1, int(2 * fps))
    act = np.convolve(speed, np.ones(k) / k, mode="same")
    on = act > 2.5
    wins, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and (on[j] or (j + int(4 * fps) < len(on) and on[j:j + int(4 * fps)].any())):
                j += 1
            a, b = ts[i] - 2.0, ts[min(j, len(ts) - 1)] + 1.0   # include the serve set-up
            if b - a >= 3.0:
                wins.append((max(seg.start, a), min(seg.end, b)))
            i = j
        i += 1
    return wins


def no_bug_jobs(vpath: str, fit: CourtFit, segs: list[Segment]):
    """Rally windows + server/side/ends for videos without a score bug.
    A = the player at the NEAR end in the first clear serve set-up."""
    from . import players as P
    windows = []
    for s in segs:
        if s.kind != "main" or s.end - s.start < MIN_WINDOW_S:
            continue
        if s.end - s.start > 40:
            windows += split_rallies(vpath, fit, s)
        else:
            windows.append((s.start, s.end))
    log(f"no score bug: {len(windows)} rally windows from player activity")
    probes, setup = [], []
    for a, b in windows:
        d = P.detect_players(vpath, a, min(b, a + PROBE_S), fit, fps=5)
        probes.append(d)
        setup.append(serve_setup_end(d))
    ident = None
    for d, (se, c) in zip(probes, setup):
        if se and c >= 0.8:
            ident = P.IdentityModel().fit_from_ends(d, {"A": "near", "B": "far"})
            break
    jobs = []
    for k, ((a, b), d, (se, c)) in enumerate(zip(windows, probes, setup)):
        ends, ec = (ident.assign_ends(d) if ident else (None, 0.0))
        ends = ends or {"A": "near", "B": "far"}
        if not se:
            continue      # no recognisable serve set-up -> not a point start
        server = next(p for p, e in ends.items() if e == se)
        xs = [x.court_xy[0] for x in d if x.end == se and not x.interp]
        x = sorted(xs)[len(xs) // 2] if xs else 0.0
        # deuce side = server's right: +x seen from the near end, -x for the far server
        side = "deuce" if (x > 0) == (se == "near") else "ad"
        jobs.append((k, (a, b), server, side, ends))
    return jobs, ident


def replay_observed(winners, confs, pvs, servers, fmt) -> list[PointBoundary]:
    """Like scoring.replay, but the server seen at the serve set-up overrides
    the rotation at the start of each game/tiebreak (a highlight or missed
    winner would otherwise make the rotation drift)."""
    from . import scoring as sc
    cur = sc.initial_state(fmt, servers[0] if servers and servers[0] in ("A", "B") else "A")
    out = []
    for w, c, pv, srv in zip(winners, confs, pvs, servers):
        if cur.match_over:
            break
        if srv in ("A", "B") and srv != cur.server and cur.points == [0, 0]:
            sc._set_server(cur, srv)
        out.append(sc._boundary(len(out), cur, w if w in ("A", "B") else None, pv.t_end, fmt,
                                False, c if w in ("A", "B") else 0.0, "vision", pv.t_start))
        if w in ("A", "B"):
            cur = sc.apply(cur, w, fmt)
    return out


def infer_winner(pv: PointVision) -> tuple[Optional[str], float]:
    """Point winner without a score bug: chart's own inference, else the last
    shot (landed in -> its hitter won; out/net -> the other player won)."""
    if pv.inferred_winner in ("A", "B"):
        return pv.inferred_winner, float(pv.conf.get("inferred_winner", 0.4))
    by, res = pv.last_shot_by, pv.last_shot_result
    if by in ("A", "B") and res not in (None, "unknown"):
        c = 0.3 * min(float(pv.conf.get("last_shot_by", 0.5)), float(pv.conf.get("last_shot_result", 0.5))) / 0.5
        return (by if res == "in" else other(by)), round(min(0.3, c), 2)
    return None, 0.0


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------
def _score_points_label(sd: dict) -> str:
    return sd.get("points", "")


def orient_engine_ends(bounds, visions) -> None:
    """The engine tracks end changes but has to guess which end A started at.
    Flip its ends on every point when confident vision ends mostly disagree, so
    score_before.ends also holds for points vision could not chart."""
    agree = disagree = 0
    for i, b in enumerate(bounds):
        pv, ee = visions.get(i), b.score_before.get("ends")
        if pv is None or not pv.ends or not ee or pv.conf.get("ends", 0.0) < 0.8:
            continue
        if pv.ends.get("A") == ee.get("A"):
            agree += 1
        else:
            disagree += 1
    if disagree > agree:
        done = set()                       # score dicts may be shared between points
        for b in bounds:
            ee = b.score_before.get("ends")
            if ee and id(b.score_before) not in done:
                done.add(id(b.score_before))
                b.score_before["ends"] = {p: ("far" if e == "near" else "near") for p, e in ee.items()}


def merge_point(b: PointBoundary, pv: Optional[PointVision], idx: int,
                engine_ends: Optional[dict]) -> PointRecord:
    rec = PointRecord(
        id=idx, t_start=(pv.t_start if pv else b.t_start), t_end=b.t_end,
        set_no=len(b.score_before.get("sets", [])) + 1,
        score_before=b.score_before, server=b.server, receiver=other(b.server),
        side=b.side, flags=b.flags, winner=b.winner, winner_source=b.source,
        gap_before=b.gap_before, ends=engine_ends,
        conf={"winner": b.conf})
    if pv is None:
        return rec

    rec.serves = [asdict(s) for s in pv.serves]
    rec.return_wing = pv.return_wing
    rec.rally_len = pv.rally_len
    rec.ending = pv.ending
    rec.last_shot_wing = pv.last_shot_wing
    rec.last_shot_stroke = pv.last_shot_stroke
    rec.last_shot_result = pv.last_shot_result
    rec.net_approaches = pv.net_approaches or {}
    rec.player_dist_m = pv.player_dist_m or {}
    if pv.ends:
        rec.ends = pv.ends
    for k, v in (pv.conf or {}).items():
        rec.conf.setdefault(k, v)

    ins = [s for s in pv.serves if s.result == "in"]
    rec.serve_number_won_on = ins[0].n if ins else None

    # who hit the ending shot, from vision
    w = b.winner
    e = pv.ending
    by = pv.last_shot_by
    if e in ("ace", "service_winner", "double_fault"):
        by = b.server
    elif e == "winner":
        by = by or w
    elif e in ("unforced_error", "forced_error"):
        by = by or (other(w) if w else None)
    else:
        by = None          # ending unknown -> don't attribute it to anyone
    rec.ending_by = by

    # cross-check vision with the (authoritative) OCR winner
    if w in ("A", "B") and e not in (None, "unknown"):
        consistent = True
        if e in ("ace", "service_winner") and w != b.server:
            consistent = False
        elif e == "double_fault" and w == b.server:
            consistent = False
        elif e == "winner" and by and by != w:
            consistent = False
        elif e in ("unforced_error", "forced_error") and by and by == w:
            consistent = False
        if not consistent:
            rec.ending = "unknown"
            rec.ending_by = None
            rec.conf["ending"] = 0.0
    if rec.ending == "double_fault":
        rec.serve_number_won_on = None
    return rec


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------
def analyze(video: str, out_dir: Optional[str] = None, opts: Optional[Options] = None) -> dict:
    from . import scenes, scoreboard, scoring
    from .report import render
    from .video import probe

    opts = opts or Options()
    t0 = time.time()
    vpath = str(Path(video))
    out = Path(out_dir) if out_dir else ROOT / "out" / Path(video).stem
    out.mkdir(parents=True, exist_ok=True)
    cache = Cache(out, vpath, opts.force)
    info = probe(vpath)
    log(f"{Path(vpath).name}: {info.width}x{info.height} {info.fps:.0f}fps {info.duration/60:.1f} min")
    fmt = MatchFormat(best_of=opts.best_of, no_ad=opts.no_ad, final_set=opts.final_set)

    # 1+3. scenes and score bug are independent decoders -> run them in parallel
    def run_scenes():
        c = cache.get("segments")
        if c is not None:
            return [Segment(**s) for s in c]
        ts = time.time()
        segs = scenes.segment(vpath)
        cache.put("segments", segs)
        log(f"scenes: {len(segs)} shots, {sum(s.kind == 'main' for s in segs)} main-camera "
            f"({time.time() - ts:.1f}s)")
        return segs

    def run_bug():
        c = cache.get("scorebug")
        if c is not None:
            return (BugLayout(**c["layout"]) if c["layout"] else None), [ScoreObs(**o) for o in c["obs"]]
        ts = time.time()
        layout = scoreboard.locate_bug(vpath)
        obs = scoreboard.read_stream(vpath, layout) if layout else []
        cache.put("scorebug", {"layout": layout, "obs": obs})
        log(f"score bug: " + (f"found {layout.names}, {len(obs)} score states" if layout else "none")
            + f" ({time.time() - ts:.1f}s)")
        return layout, obs

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_bug = ex.submit(run_bug)
        segs = run_scenes()
        layout, obs = f_bug.result()
    main_s = sum(s.end - s.start for s in segs if s.kind == "main")
    log(f"main camera: {main_s:.0f}s of {info.duration:.0f}s")

    # 2. court ----------------------------------------------------------------
    fit = None
    if not opts.quick:
        c = cache.get("court")
        if c is None:
            ts = time.time()
            fit = scenes.reference_court(vpath, segs)
            cache.put("court", fit)
            log(f"court: " + (f"err {fit.err_px:.2f}px conf {fit.conf:.2f}" if fit else "NOT FOUND")
                + f" ({time.time() - ts:.1f}s)")
        elif c:
            fit = CourtFit(**c)

    names = {"A": opts.a_name, "B": opts.b_name}
    if layout:
        names["A"] = names["A"] or _title(layout.names[0])
        names["B"] = names["B"] or _title(layout.names[1])
    names["A"] = names["A"] or "Player A"
    names["B"] = names["B"] or "Player B"
    players = {"A": {"name": names["A"], "hand": opts.a_hand or "R", "hand_given": opts.a_hand is not None},
               "B": {"name": names["B"], "hand": opts.b_hand or "R", "hand_given": opts.b_hand is not None}}

    # 4. points ---------------------------------------------------------------
    has_bug = bool(layout and obs)
    if has_bug:
        bounds = scoring.reconcile(obs, fmt)
        log(f"points from score bug: {len(bounds)} "
            f"({sum(b.source == 'ocr' for b in bounds)} seen, {sum(b.source != 'ocr' for b in bounds)} inferred)")
    else:
        bounds = []
    bug_warnings = scorebug_warnings(layout, obs, bounds)
    for w in bug_warnings:
        log(f"WARNING: {w}")

    # 5. vision -----------------------------------------------------------------
    hands = {"A": opts.a_hand or "R", "B": opts.b_hand or "R"}
    visions: dict[int, PointVision] = {}
    voted: dict = {}           # point index -> ends confidence, for points whose ends came from the vote
    if not opts.quick and fit is not None:
        from . import chart
        c = cache.get("vision")
        cached = {int(k): v for k, v in (c or {}).items()}
        ts = time.time()
        attempts_of: dict[int, list] = {}
        if has_bug:
            jobs = []
            t_prev = 0.0
            for i, b in enumerate(bounds):
                if b.source == "ocr":
                    w = rally_window(segs, t_prev, b.t_end)
                    if w:
                        jobs.append((i, w, b.server, b.side, b.score_before.get("ends")))
                        attempts_of[i] = serve_attempt_windows(segs, t_prev, w)
                    t_prev = b.t_end
        else:
            jobs, ident_nb = no_bug_jobs(vpath, fit, segs)
        todo = [j for j in jobs if j[0] not in cached]
        tracked: dict = {}
        if has_bug:
            # pass 1: detect and track every window. This is the only video pass
            # of the vision stage; nothing here depends on who is at which end.
            # No clothing features: the player pictures are dropped as they are
            # detected, which keeps long windows from filling the memory.
            for n, (i, (a, bnd), *_rest) in enumerate(todo):
                try:
                    times, cands, dets = DETECT(vpath, a, bnd, fit, False)
                    tracked[i] = (dets, chart.track_window(times, cands, dets, px_unit(fit.frame_h)))
                    del times, cands
                except Exception as ex:  # one bad window must not kill a 3-hour run
                    log(f"  detection failed on point {i} [{a:.1f}-{bnd:.1f}]: {ex!r}")
                if (n + 1) % 10 == 0 or n + 1 == len(todo):
                    log(f"  detect {n + 1}/{len(todo)} windows ({time.time() - ts:.0f}s)")
            # ends: the engine's end changes, turned the right way round by a vote
            votes = {int(k): v for k, v in (cache.get("ends_votes") or {}).items()}
            for i, (a, bnd), *_rest in todo:
                if i in tracked:
                    votes[i] = window_vote(fit, a, *tracked[i])
            cache.put("ends_votes", votes)
            voted, lines = orient_by_votes(bounds, votes)
            for line in lines:
                log(line)
            rest = [j for j in todo if j[0] in tracked and j[0] not in voted]
            ends_by, ident = (resolve_ends(vpath, fit, rest) if rest else ({}, None))
            if ident is not None:
                # the appearance model needs clothing features, which pass 1 does
                # not make (it keeps no pictures): detect those windows' players again
                from . import players as P
                for i, (a, bnd), *_rest in rest:
                    tracked[i] = (P.detect_players(vpath, a, bnd, fit, 5, 480, None, True), tracked[i][1])
        else:
            voted = {}
            ends_by, ident = {}, ident_nb
        prev_win = None        # (end, player detections) of the window charted just before
        for n, (i, (a, bnd), server, side, ends) in enumerate(jobs):
            if i in cached:
                visions[i] = _pv_from_dict(cached[i])
                continue
            idn, e_conf = ident, None
            if i in voted:
                ends, idn, e_conf = bounds[i].score_before.get("ends"), None, voted[i]
            elif i in ends_by:
                ends = ends_by[i][0]
            try:
                if has_bug:
                    if i not in tracked:
                        continue
                    dets, ball = tracked.pop(i)
                    sp = setup_prev_of(segs, prev_win, a, bnd)
                    prev_win = (bnd, dets)
                else:
                    sp = None
                    times, cands, dets = DETECT(vpath, a, bnd, fit, ident is not None)
                    ball = chart.track_window(times, cands, dets, px_unit(fit.frame_h))
                    del times, cands
                pv = chart.chart_tracked(vpath, a, bnd, fit, server, side,
                                         ends or {"A": "near", "B": "far"}, dets, ball,
                                         players_hand=hands, identity=idn,
                                         known_winner=(bounds[i].winner if has_bug else None),
                                         ends_conf=e_conf, setup_prev=sp)
                if i in ends_by:
                    pv.conf["ends"] = round(max(pv.conf.get("ends", 0.0), ends_by[i][1]), 2)
                tries = []
                for (sa, sb) in attempts_of.get(i, []):
                    apv = chart.chart_point(vpath, sa, sb, fit, server=server, side=side,
                                            ends=pv.ends or ends or {"A": "near", "B": "far"},
                                            players_hand=hands, identity=idn)
                    if apv.serves or is_serve_setup(vpath, fit, sa, sb):
                        tries.append(apv)
                pv = merge_serve_attempts(pv, tries)
            except Exception as ex:  # one bad window must not kill a 3-hour run
                log(f"  vision failed on point {i} [{a:.1f}-{bnd:.1f}]: {ex!r}")
                continue
            visions[i] = pv
            cached[i] = asdict(pv)
            if (n + 1) % 10 == 0 or n + 1 == len(jobs):
                cache.put("vision", cached)
                log(f"  vision {n + 1}/{len(jobs)} points ({time.time() - ts:.0f}s)")
        cache.put("vision", cached)
        log(f"vision: {len(visions)} points charted ({time.time() - ts:.1f}s)")

    if not has_bug and not opts.quick:
        # no score bug: one point per rally window, winners inferred by vision
        order = sorted(visions)
        # a short serve-only shot followed by another serve set-up by the same
        # server is that point's 1st-serve fault, not a point of its own
        server_of = {j[0]: j[2] for j in jobs}
        side_of = {j[0]: j[3] for j in jobs}
        merged, k = [], 0
        while k < len(order):
            i = order[k]
            pv = visions[i]
            nxt = order[k + 1] if k + 1 < len(order) else None
            # same server AND same court side: the next point would be served
            # from the other side, a 2nd serve comes from the same one
            if (nxt is not None and pv.t_end - pv.t_start < 4.5 and (pv.rally_len or 0) <= 2
                    and server_of.get(i) == server_of.get(nxt)
                    and side_of.get(i) == side_of.get(nxt)
                    and visions[nxt].t_start - pv.t_end < 40):
                visions[nxt] = merge_serve_attempts(visions[nxt], [pv])
                k += 1
                continue
            merged.append(i)
            k += 1
        order = merged
        winners, confs = zip(*[infer_winner(visions[i]) for i in order]) if order else ((), ())
        bounds = replay_observed(list(winners), list(confs), [visions[i] for i in order],
                                 [server_of.get(i) for i in order], fmt)
        # An unknown winner is not a skipped point: the score just doesn't advance
        # until the coach fills the winner in the review UI, after which
        # apply_corrections() re-scores the whole match continuously from 0-0.
        for b in bounds[1:]:
            b.gap_before = False
        visions = {k: visions[i] for k, i in enumerate(order)}
        n_known = sum(w in ("A", "B") for w in winners)
        log(f"no score bug: {len(bounds)} points, winner inferred on {n_known} "
            f"(scores reconstructed from 0-0 - set unknown winners in the review UI)")

    # 6. merge --------------------------------------------------------------------
    if not voted:
        orient_engine_ends(bounds, visions)
    points = []
    for i, b in enumerate(bounds):
        engine_ends = b.score_before.get("ends")
        points.append(asdict(merge_point(b, visions.get(i), i, engine_ends)))

    match = {
        "meta": {"video": _rel(vpath, out),
                 "video_info": dict(asdict(info), path=_rel(vpath, out)), "players": players, "format": asdict(fmt),
                 "pipeline_version": PIPELINE_VERSION,
                 "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "runtime_s": round(time.time() - t0, 1), "has_scorebug": has_bug,
                 "scorebug_warning": " ".join(bug_warnings) or None,
                 "quick": opts.quick,
                 "court": asdict(fit) if fit else None},
        "segments": [asdict(s) for s in segs],
        "points": points,
    }
    # match.json can be shared, so it names the video without this computer's folders;
    # the full path is kept in cache/ (never shared), where the report link and the
    # review page look for it
    (cache.dir / VIDEO_PATH_FILE).write_text(str(Path(vpath).resolve()))
    paths = render(match, out)
    # match_auto.json = the uncorrected base the review UI applies corrections to
    (out / "match_auto.json").write_text((out / "match.json").read_text())
    # corrections made earlier in the review UI survive a re-analysis
    if (out / "corrections.json").exists():
        rebuild(str(out))
    log(f"done in {time.time() - t0:.1f}s -> {paths['html']}")
    return match


def rebuild(out_dir: str) -> dict:
    """Re-apply corrections.json onto the auto result and re-render (seconds)."""
    from .analytics import apply_corrections
    from .report import render
    out = Path(out_dir)
    base = out / "match_auto.json"
    src = base if base.exists() else out / "match.json"
    match = json.loads(src.read_text())
    if not base.exists():
        base.write_text(json.dumps(match))
    corr_f = out / "corrections.json"
    corr = json.loads(corr_f.read_text()) if corr_f.exists() else {}
    m = apply_corrections(match, corr)
    paths = render(m, out)
    meta = m.get("meta") or {}
    if meta.get("corrections_stale"):
        log(f"WARNING: {meta['corrections_stale']} ({corr_f})")
    # count the point entries that were applied, not every key ("_meta" is not a point)
    n_added = (m.get("stats") or {}).get("n_added") or 0
    log(f"rebuilt with corrections on {meta.get('corrections_applied') or 0} points"
        f"{f' ({n_added} added)' if n_added else ''} -> {paths['html']}")
    return m


# ---------------------------------------------------------------------------
def _title(name: str) -> str:
    # a team or country code in brackets is not part of the name: "LANE (UNI)" -> "Lane"
    name = re.sub(r"\s*\([^)]*\)", "", name.replace("•", "")).strip()
    return name.title() if name.isupper() else name


NO_BOARD_WARNING = ("No score board was found in the video. Without one, the points and who won them are "
                    "guessed from the video, and every score (and so every break point, game point or 40-0) "
                    "is worked out from those guesses, not read, so check them before you trust any number "
                    "split by score. One known cause: a board whose player names are not written in capital "
                    "letters is not found yet.")


def scorebug_warnings(layout, obs, bounds) -> list[str]:
    """Plain warnings when no score board was found, or when one was found but its
    readings look wrong: no reading at all, the serve marker never read, or the score
    often jumping in a way the engine could not follow (a sign of misread columns)."""
    if not layout:
        return [NO_BOARD_WARNING]
    out = []
    names = " and ".join(str(n) for n in (layout.names or []))
    if not obs:
        return [f"A score board was found ({names}), but no score could be read from it, so the "
                f"points were found from the video alone and the score is a guess."]
    if not any(getattr(o, "server", None) is not None for o in obs):
        top = (layout.names or ["the top-row player"])[0]
        out.append(f"The serve marker on the score board was not read in any of {len(obs)} readings, so "
                   f"who served each point comes from the score alone, starting from a guess that "
                   f"{top} (top row) served first. Check the server of the first points.")
    gaps = sum(1 for b in bounds or [] if getattr(b, "gap_before", False))
    if len(bounds or []) >= 5 and gaps > max(2, 0.1 * len(bounds)):
        out.append(f"The score jumped in a way the program could not follow {gaps} times in {len(bounds)} "
                   f"points. That happens when the board is read wrongly, so check the scores next to "
                   f"the rows of the point list that say points may be missing.")
    return out


VIDEO_PATH_FILE = "video_path.txt"     # in <out>/cache/: the video's full path on this computer


def _rel(vpath: str, out: Path) -> str:
    """The video as match.json names it: relative to the project when it is inside it,
    else the file name alone (a full path would put this computer's folders in a file
    that is meant to be shared)."""
    try:
        return str(Path(vpath).resolve().relative_to(ROOT))
    except ValueError:
        return Path(vpath).name


def local_video_path(out_dir) -> Optional[Path]:
    """The video's full path saved by analyze in <out>/cache/, when that file exists."""
    f = Path(out_dir) / "cache" / VIDEO_PATH_FILE
    try:
        p = Path(f.read_text().strip())
    except OSError:
        return None
    return p if p.is_file() else None


_LOCAL_META_KEYS = ("video_abs",)


def public_meta(meta: dict) -> dict:
    """meta without this computer's folders: drops video_abs and turns a full video path
    into its file name (outputs made before this rule had them)."""
    m = {k: v for k, v in (meta or {}).items() if k not in _LOCAL_META_KEYS}
    v = m.get("video")
    if isinstance(v, str) and Path(v).is_absolute():
        m["video"] = Path(v).name
    vi = m.get("video_info")
    if isinstance(vi, dict) and isinstance(vi.get("path"), str) and Path(vi["path"]).is_absolute():
        m["video_info"] = dict(vi, path=Path(vi["path"]).name)
    return m


def _pv_from_dict(d: dict) -> PointVision:
    d = dict(d)
    d["serves"] = [ServeVision(**s) for s in d.get("serves", [])]
    d["shots"] = [ShotEvent(**s) for s in d.get("shots", [])]
    return PointVision(**d)
