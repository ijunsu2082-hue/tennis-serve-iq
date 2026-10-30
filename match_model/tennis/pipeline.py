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

import inspect
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .contracts import (BugLayout, CourtFit, MatchFormat, PointRecord, ScoreObs, Segment,
                        PointBoundary, PointVision, ServeVision, ShotEvent, other)

PIPELINE_VERSION = "1.0"
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Options:
    a_name: Optional[str] = None
    b_name: Optional[str] = None
    a_hand: str = "R"
    b_hand: str = "R"
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
    players = {"A": {"name": names["A"], "hand": opts.a_hand},
               "B": {"name": names["B"], "hand": opts.b_hand}}

    # 4. points ---------------------------------------------------------------
    has_bug = bool(layout and obs)
    if has_bug:
        bounds = scoring.reconcile(obs, fmt)
        log(f"points from score bug: {len(bounds)} "
            f"({sum(b.source == 'ocr' for b in bounds)} seen, {sum(b.source != 'ocr' for b in bounds)} inferred)")
    else:
        bounds = []

    # 5. vision -----------------------------------------------------------------
    hands = {"A": opts.a_hand, "B": opts.b_hand}
    visions: dict[int, PointVision] = {}
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
        if has_bug:
            ends_by, ident = (resolve_ends(vpath, fit, todo) if todo else ({}, None))
        else:
            ends_by, ident = {}, ident_nb
        for n, (i, (a, bnd), server, side, ends) in enumerate(jobs):
            if i in cached:
                visions[i] = _pv_from_dict(cached[i])
                continue
            if i in ends_by:
                ends = ends_by[i][0]
            kw = {}
            if has_bug and "known_winner" in inspect.signature(chart.chart_point).parameters:
                kw["known_winner"] = bounds[i].winner
            try:
                pv = chart.chart_point(vpath, a, bnd, fit, server=server, side=side,
                                       ends=ends or {"A": "near", "B": "far"},
                                       players_hand=hands, identity=ident, **kw)
                if i in ends_by:
                    pv.conf["ends"] = round(max(pv.conf.get("ends", 0.0), ends_by[i][1]), 2)
                tries = []
                for (sa, sb) in attempts_of.get(i, []):
                    apv = chart.chart_point(vpath, sa, sb, fit, server=server, side=side,
                                            ends=pv.ends or ends or {"A": "near", "B": "far"},
                                            players_hand=hands, identity=ident)
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
    orient_engine_ends(bounds, visions)
    points = []
    for i, b in enumerate(bounds):
        engine_ends = b.score_before.get("ends")
        points.append(asdict(merge_point(b, visions.get(i), i, engine_ends)))

    match = {
        "meta": {"video": _rel(vpath, out), "video_abs": str(Path(vpath).resolve()),
                 "video_info": asdict(info), "players": players, "format": asdict(fmt),
                 "pipeline_version": PIPELINE_VERSION,
                 "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "runtime_s": round(time.time() - t0, 1), "has_scorebug": has_bug,
                 "quick": opts.quick,
                 "court": asdict(fit) if fit else None},
        "segments": [asdict(s) for s in segs],
        "points": points,
    }
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
    log(f"rebuilt with {len(corr)} corrected points -> {paths['html']}")
    return m


# ---------------------------------------------------------------------------
def _title(name: str) -> str:
    name = name.replace("•", "").strip()
    return name.title() if name.isupper() else name


def _rel(vpath: str, out: Path) -> str:
    try:
        return str(Path(vpath).resolve().relative_to(ROOT))
    except ValueError:
        return str(Path(vpath).resolve())


def _pv_from_dict(d: dict) -> PointVision:
    d = dict(d)
    d["serves"] = [ServeVision(**s) for s in d.get("serves", [])]
    d["shots"] = [ShotEvent(**s) for s in d.get("shots", [])]
    return PointVision(**d)
