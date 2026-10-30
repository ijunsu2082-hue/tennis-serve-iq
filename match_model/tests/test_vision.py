"""Vision evaluation on videoplayback.mp4 (players + ball + events + chart).

Run as a script for the full report:
    .venv/bin/python tests/test_vision.py
or with pytest (asserts loose regression floors, prints the same report with -s):
    .venv/bin/python -m pytest -s tests/test_vision.py

Fixtures (hand-made, see their "description" fields):
    tests/fixtures/court_manual.json      manual homography of the main camera
    tests/fixtures/rally_windows.json     main-camera rally windows
    tests/fixtures/gt_ball.json           hand-verified ball positions
    tests/fixtures/gt_points_vision.json  hand-charted points, every shot (null = not knowable)
"""
from __future__ import annotations

import json
import os
import resource
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tennis.contracts import CourtFit  # noqa: E402
from tennis import ball as B, players as P, chart as C  # noqa: E402

VIDEO = os.path.join(ROOT, "videoplayback.mp4")
FIX = os.path.join(ROOT, "tests", "fixtures")
BALL_TOL_PX = 10.0


def load_fit() -> CourtFit:
    d = json.load(open(os.path.join(FIX, "court_manual.json")))
    return CourtFit(H=d["H"], frame_w=d["frame_w"], frame_h=d["frame_h"],
                    err_px=d["err_px"], conf=d["conf"])


def _have_video() -> bool:
    return os.path.exists(VIDEO)


# --------------------------------------------------------------------------- ball
def eval_ball(fit: CourtFit, verbose: bool = True) -> dict:
    """Precision/recall vs gt_ball.json. A labelled frame counts as TP if the tracker
    output is within BALL_TOL_PX, 'wrong' if it outputs another position, 'miss' if it
    outputs nothing. Output on unlabelled frames is reported separately (not FP:
    GT does not list ball-absent frames)."""
    G = json.load(open(os.path.join(FIX, "gt_ball.json")))
    fps = G.get("fps", 25)
    tot = dict(tp=0, wrong=0, miss=0, unl=0, frames=0, sec=0.0)
    for w, (st, en) in G["windows"].items():
        pl = P.detect_players(VIDEO, st, en, fit, with_features=False)
        b = B.track_ball(VIDEO, st, en, fit, players=pl)
        s = B.LAST_STATS
        tot["frames"] += s["frames"]
        tot["sec"] += s["seconds"]
        out = {int(round((x.t - st) * fps)): x.xy_px for x in b}
        gt = {r["frame"]: r["xy"] for r in G["labels"] if r["window"] == w}
        tp = wrong = miss = 0
        for f, xy in gt.items():
            if f in out:
                if np.hypot(out[f][0] - xy[0], out[f][1] - xy[1]) <= BALL_TOL_PX:
                    tp += 1
                else:
                    wrong += 1
            else:
                miss += 1
        unl = sum(1 for f in out if f not in gt)
        if verbose:
            print(f"  ball {w}: gt={len(gt)} tp={tp} wrong={wrong} miss={miss} "
                  f"P={tp / max(1, tp + wrong):.2f} R={tp / max(1, len(gt)):.2f} "
                  f"out_on_unlabelled={unl} {s['fps']:.0f} fps")
        for k, v in (("tp", tp), ("wrong", wrong), ("miss", miss), ("unl", unl)):
            tot[k] += v
    n = tot["tp"] + tot["wrong"] + tot["miss"]
    res = dict(precision=tot["tp"] / max(1, tot["tp"] + tot["wrong"]), recall=tot["tp"] / max(1, n),
               fps=tot["frames"] / max(tot["sec"], 1e-6), n_gt=n, out_on_unlabelled=tot["unl"])
    if verbose:
        print(f"  ball ALL: P={res['precision']:.3f} R={res['recall']:.3f} n_gt={n} "
              f"speed={res['fps']:.0f} fps (incl. decode, 640x360 near half + full-res far half)")
    return res


# --------------------------------------------------------------------------- chart
# serve_* = the serve played inside the rally window (the last GT serve: n=2 when
# the 1st serve was a fault shown in a separate earlier shot, see fault_windows)
FIELDS = ["serve_result", "serve_zone", "return_wing", "rally_len", "last_shot_by",
          "last_shot_wing", "last_shot_stroke", "last_shot_result", "ending", "winner"]
CONF_KEY = {"winner": "inferred_winner"}


def _pred_fields(pv) -> dict:
    s = pv.serves[-1] if pv.serves else None
    return {
        "serve_result": s.result if s else None,
        "serve_zone": s.zone if s else None,
        "return_wing": pv.return_wing,
        "rally_len": pv.rally_len,
        "last_shot_by": pv.last_shot_by,
        "last_shot_wing": pv.last_shot_wing,
        "last_shot_stroke": pv.last_shot_stroke,
        "last_shot_result": pv.last_shot_result,
        "ending": pv.ending,
        "winner": pv.inferred_winner,
    }


def _pred_conf(pv, f) -> float:
    if f.startswith("serve_"):
        return pv.serves[-1].conf if pv.serves else 0.0
    return float(pv.conf.get(CONF_KEY.get(f, f), 0.0))


def _gt_fields(g) -> dict:
    s = g["serves"][-1] if g.get("serves") else {}
    return {
        "serve_result": s.get("result"), "serve_zone": s.get("zone"),
        "return_wing": g.get("return_wing"), "rally_len": g.get("rally_len"),
        "last_shot_by": g.get("last_shot_by"), "last_shot_wing": g.get("last_shot_wing"),
        "last_shot_stroke": g.get("last_shot_stroke"), "last_shot_result": g.get("last_shot_result"),
        "ending": g.get("ending"), "winner": g.get("winner"),
    }


def eval_chart(fit: CourtFit, verbose: bool = True, known_winner: bool = True) -> dict:
    """known_winner=True passes the score-bug winner like the pipeline does (the
    'winner' field is then given, not measured)."""
    G = json.load(open(os.path.join(FIX, "gt_points_vision.json")))["points"]
    C.chart_point(VIDEO, G[0]["t_start"], G[0]["t_start"] + 1.0, fit, G[0]["server"],
                  G[0]["side"], G[0]["ends"])        # warm-up (model load, MPS init)
    stats = {f: {"n": 0, "ok": 0, "abstain": 0} for f in FIELDS}
    calib = []                                       # (field, conf, correct) for answered fields
    timings = []
    faults = {"n": 0, "fault": 0, "type_ok": 0, "rally": 0}
    for g in G:
        t0 = time.time()
        kw = {"known_winner": g["winner"]} if known_winner else {}
        pv = C.chart_point(VIDEO, g["t_start"], g["t_end"], fit, g["server"], g["side"], g["ends"], **kw)
        dt = time.time() - t0
        timings.append((g["id"], g["t_end"] - g["t_start"], dt))
        pr, gt = _pred_fields(pv), _gt_fields(g)
        row = []
        for f in FIELDS:
            if gt[f] is None:
                continue
            st = stats[f]
            st["n"] += 1
            p = pr[f]
            if p in (None, "unknown"):
                st["abstain"] += 1
                row.append(f"{f}=?/{gt[f]}")
            else:
                if f != "winner":
                    calib.append((f, _pred_conf(pv, f), p == gt[f]))
                if p == gt[f]:
                    st["ok"] += 1
                    row.append(f"{f}=OK")
                else:
                    row.append(f"{f}={p}/{gt[f]}")
        if verbose:
            print(f"  {g['id']} ({g['t_end'] - g['t_start']:.1f}s -> {dt:.2f}s wall): " + ", ".join(row))
            print(f"      pred: serves={[(s.n, s.result, s.zone, s.fault, s.conf) for s in pv.serves]} "
                  f"rally={pv.rally_len} ret={pv.return_wing} last={pv.last_shot_by}/{pv.last_shot_wing}/"
                  f"{pv.last_shot_stroke}/{pv.last_shot_result} ending={pv.ending} winner={pv.inferred_winner} "
                  f"dist={pv.player_dist_m} net={pv.net_approaches}")
        # separate short shots showing a 1st-serve fault (serve-only windows)
        for fw in g.get("fault_windows", []):
            pf = C.chart_point(VIDEO, fw["t_start"], fw["t_end"], fit, g["server"], g["side"], g["ends"])
            s0 = pf.serves[0] if pf.serves else None
            gt_fault = g["serves"][0].get("fault")
            faults["n"] += 1
            faults["fault"] += int(bool(s0) and s0.result == "fault")
            faults["type_ok"] += int(bool(s0) and s0.result == "fault" and s0.fault == gt_fault)
            faults["rally"] += int((pf.rally_len or 0) > 2)
            if verbose:
                print(f"      fault shot {fw['t_start']}-{fw['t_end']}: GT {gt_fault}, pred "
                      f"{(s0.result, s0.fault, s0.bounce_xy) if s0 else None} rally={pf.rally_len}")
    if verbose:
        print("  per-field accuracy (ok / charted GT; abstain = None/unknown):")
        for f in FIELDS:
            st = stats[f]
            if st["n"]:
                ans = st["n"] - st["abstain"]
                print(f"    {f:18s} {st['ok']}/{st['n']} = {st['ok'] / st['n']:.2f}   "
                      f"coverage {ans}/{st['n']}; accuracy when answering {st['ok']}/{ans}"
                      + (f" = {st['ok'] / ans:.2f}" if ans else "")
                      + ("   (given: known_winner)" if f == "winner" and known_winner else ""))
        print("  calibration (answered fields excl. winner): conf bin -> correct/answered")
        for lo, hi in ((0.0, 0.3), (0.3, 0.45), (0.45, 0.6), (0.6, 0.75), (0.75, 1.01)):
            A = [c for c in calib if lo <= c[1] < hi]
            print(f"    [{lo:.2f},{hi:.2f}) {sum(c[2] for c in A)}/{len(A)}")
        A = [c for c in calib if c[1] >= 0.6]
        print(f"    conf>=0.60: {sum(c[2] for c in A)}/{len(A)}")
        print(f"  fault shots: called fault {faults['fault']}/{faults['n']}, fault type right "
              f"{faults['type_ok']}/{faults['n']}, invented rallies (>2 shots) {faults['rally']}")
        per10 = [dt / max(d, 1e-6) * 10 for _, d, dt in timings]
        print(f"  timing: wall per 10 s of window: mean {np.mean(per10):.2f}s, max {np.max(per10):.2f}s; "
              f"peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.0f} MB")
    return {"fields": stats, "timings": timings, "calib": calib, "faults": faults}


# --------------------------------------------------------------------------- identity
def eval_identity(fit: CourtFit, train_id: str = "W02", verbose: bool = True) -> dict:
    """Fit IdentityModel on one window (ends known), then predict ends on all the
    others, including after the change of ends (W07-W10)."""
    G = json.load(open(os.path.join(FIX, "gt_points_vision.json")))["points"]
    # the rally windows run on into the close-ups after the point; identity is
    # about the main-camera play, so stop 1.5 s after the last charted shot
    D = {g["id"]: P.detect_players(VIDEO, g["t_start"],
                                   min(g["t_end"], max(s["t"] for s in g["shots"]) + 1.5), fit)
         for g in G}
    tr = [g for g in G if g["id"] == train_id][0]
    m = P.IdentityModel().fit_from_ends(D[train_id], tr["ends"])
    win_ok = win_n = det_ok = det_n = 0
    for g in G:
        if g["id"] == train_id:
            continue
        e, _ = m.assign_ends(D[g["id"]])
        win_n += 1
        win_ok += int(e == g["ends"])
        inv = {v: k for k, v in g["ends"].items()}
        for d in D[g["id"]]:
            if d.feat is None:
                continue
            pid, _ = m.assign(d)
            det_n += 1
            det_ok += int(pid == inv[d.end])
    if verbose:
        print(f"  identity (trained on {train_id}): window ends {win_ok}/{win_n}, "
              f"per-detection {det_ok}/{det_n} = {det_ok / max(1, det_n):.3f}")
    return {"win_ok": win_ok, "win_n": win_n, "det_acc": det_ok / max(1, det_n)}


# --------------------------------------------------------------------------- pytest
def test_vision_report():
    if not _have_video():
        import pytest
        pytest.skip("videoplayback.mp4 not present")
    fit = load_fit()
    rb = eval_ball(fit)
    rc = eval_chart(fit)
    ri = eval_identity(fit)
    assert ri["win_ok"] == ri["win_n"] and ri["det_acc"] >= 0.95
    # loose regression floors (measured values are higher; see report)
    assert rb["precision"] >= 0.75 and rb["recall"] >= 0.70
    assert rc["fields"]["winner"]["ok"] >= 5
    hi = [c for c in rc["calib"] if c[1] >= 0.6]
    assert sum(c[2] for c in hi) >= 0.85 * len(hi)          # calibration target
    assert rc["fields"]["last_shot_by"]["ok"] >= 7
    assert rc["faults"]["rally"] == 0
    for _, dur, dt in rc["timings"]:
        assert dt <= max(3.0, 0.45 * dur) + 2.0   # generous: machine is shared


if __name__ == "__main__":
    fit = load_fit()
    print("== ball ==")
    eval_ball(fit)
    print("== identity ==")
    eval_identity(fit)
    print("== chart ==")
    eval_chart(fit)
