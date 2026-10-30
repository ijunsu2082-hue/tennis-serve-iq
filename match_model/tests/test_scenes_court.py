"""Tests for tennis.scenes and tennis.court on the TennisTV test video.

Run:  .venv/bin/python -m pytest tests/test_scenes_court.py   (if pytest is installed)
  or: .venv/bin/python tests/test_scenes_court.py

Set TENNIS_OVERLAY_DIR=<dir> to save draw_court overlays for visual checks.
Set TENNIS_SKIP_PERF=1 to skip wall-clock assertions (e.g. on a loaded machine).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tennis import court, scenes  # noqa: E402
from tennis.contracts import HALF_LEN as L, SINGLES_HALF_W as S, DOUBLES_HALF_W as D  # noqa: E402
from tennis.video import iter_frames  # noqa: E402

VIDEO = os.path.join(ROOT, "videoplayback.mp4")
GT = os.path.join(ROOT, "tests", "fixtures", "gt_main_camera.json")
PERF = os.environ.get("TENNIS_SKIP_PERF") != "1"
OVERLAY_DIR = os.environ.get("TENNIS_OVERLAY_DIR")

# Court corners of the main camera at 1280x720, checked by eye on zoomed
# overlays (far-left, far-right, near-right, near-left).
DOUBLES_CORNERS = np.array([(450, 200), (831, 201), (1007, 562), (266, 559)], float)
SINGLES_CORNERS = np.array([(498, 200), (783, 201), (914, 562), (358, 559)], float)
CORNER_TOL = 5.0

# main-camera timestamps from the GT used for court-fit checks
COURT_TS = [6.4, 46.0, 194.6, 206.2, 244.0, 264.0, 303.6, 320.0, 379.4, 432.2,
            490.0, 504.8, 541.2, 552.8, 597.2]

_cache: dict = {}


def _grab(t, size=None):
    for _, f in iter_frames(VIDEO, start=t, end=t + 0.25, size=size):
        return f.copy()
    raise RuntimeError(f"no frame at {t}")


def _segments():
    if "segs" not in _cache:
        t0 = time.time()
        _cache["segs"] = scenes.segment(VIDEO)
        _cache["seg_time"] = time.time() - t0
    return _cache["segs"]


# ---------------------------------------------------------------- pure math
def test_project_roundtrip():
    H = np.array([[6.0e-02, 7.0e-04, -3.9e+01], [8.0e-04, -2.0e-01, 6.5e+01], [-2e-05, 5.5e-03, 1.0]])
    pts = np.array([(-D, L), (S, -L), (0.0, 0.0), (1.2, 3.4)])
    back = court.project(H, court.unproject(H, pts))
    assert np.abs(back - pts).max() < 1e-9
    H2 = court.scale_H(H, (1280, 720), (640, 360))
    px = court.unproject(H, pts)
    assert np.abs(court.unproject(H2, pts) * 2 - px).max() < 1e-6


# ---------------------------------------------------------------- scenes
def test_gt_fixture():
    d = json.load(open(GT))
    labels = d["labels"]
    assert len(labels) >= 150
    assert {l["label"] for l in labels} == {"main", "other"}


def test_main_camera_f1():
    segs = _segments()
    labels = json.load(open(GT))["labels"]
    ts = np.array([l["t"] for l in labels])
    y = np.array([l["label"] == "main" for l in labels])
    p = scenes.is_main_at(segs, ts)
    tp, fp, fn = int((p & y).sum()), int((p & ~y).sum()), int((~p & y).sum())
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    print(f"main-camera P={prec:.3f} R={rec:.3f} F1={f1:.3f} (tp={tp} fp={fp} fn={fn}); "
          f"FP t={ts[p & ~y].tolist()} FN t={ts[~p & y].tolist()}; segment() {_cache['seg_time']:.1f}s")
    assert f1 >= 0.95, f1
    # segments tile the video and main segments respect the minimum length
    for a, b in zip(segs[:-1], segs[1:]):
        assert abs(a.end - b.start) < 1e-6 and a.kind != b.kind
    for s in segs:
        if s.kind == "main":
            assert s.end - s.start >= scenes.MIN_MAIN_S - 1e-6


def test_segment_runtime():
    _segments()
    print(f"segment() wall time {_cache['seg_time']:.1f}s")
    if PERF:
        assert _cache["seg_time"] < 25.0, _cache["seg_time"]


def test_fixed_camera_no_cuts():
    """A clip made only of main-camera footage must come out as one main segment."""
    with tempfile.TemporaryDirectory() as td:
        clip = os.path.join(td, "fixed.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "312.9", "-t", "19.3", "-i", VIDEO,
                        "-ss", "497.7", "-t", "10.7", "-i", VIDEO, "-filter_complex",
                        "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264",
                        "-preset", "ultrafast", clip], check=True)
        segs = scenes.segment(clip)
    main = sum(s.end - s.start for s in segs if s.kind == "main")
    tot = segs[-1].end - segs[0].start
    print("fixed-camera clip segments:", [(round(s.start, 1), round(s.end, 1), s.kind) for s in segs])
    assert main / tot > 0.97


# ---------------------------------------------------------------- court
def test_court_fit_main_frames():
    errs, times = [], []
    prev = None
    for t in COURT_TS:
        f = _grab(t)
        t0 = time.time()
        fit = court.fit_court(f)
        times.append(time.time() - t0)
        assert fit is not None, t
        assert (fit.frame_w, fit.frame_h) == (1280, 720)
        errs.append(fit.err_px)
        assert fit.err_px < 3.0, (t, fit.err_px)
        assert fit.conf > 0.8, (t, fit.conf)
        dc = court.corners_px(fit)
        sc = court.unproject(fit.H, np.array([(-S, L), (S, L), (S, -L), (-S, -L)]))
        assert np.abs(dc - DOUBLES_CORNERS).max() < CORNER_TOL, (t, dc.round(1).tolist())
        assert np.abs(sc - SINGLES_CORNERS).max() < CORNER_TOL, (t, sc.round(1).tolist())
        # quick refinement path from a previous fit
        if prev is not None:
            t0 = time.time()
            fit2 = court.fit_court(f, init_H=prev)
            assert fit2 is not None and fit2.err_px < 3.0
            assert np.abs(court.corners_px(fit2) - DOUBLES_CORNERS).max() < CORNER_TOL
        prev = fit
        if OVERLAY_DIR:
            os.makedirs(OVERLAY_DIR, exist_ok=True)
            cv2.imwrite(os.path.join(OVERLAY_DIR, f"court_{t:06.1f}.jpg"), court.draw_court(f, fit))
    print(f"court fit on {len(errs)} frames: err_px mean={np.mean(errs):.3f} max={np.max(errs):.3f}; "
          f"fit time mean={np.mean(times):.3f}s max={np.max(times):.3f}s")
    assert len(errs) >= 10
    if PERF:
        assert np.mean(times) < 0.3, np.mean(times)


def test_court_fit_lower_res():
    f = _grab(COURT_TS[3], size=(640, 360))
    fit = court.fit_court(f)
    assert fit is not None and (fit.frame_w, fit.frame_h) == (640, 360)
    assert np.abs(court.corners_px(fit) * 2 - DOUBLES_CORNERS).max() < 2 * CORNER_TOL


def test_reference_court():
    segs = _segments()
    ref = scenes.reference_court(VIDEO, segs)
    assert ref is not None and (ref.frame_w, ref.frame_h) == (1280, 720)
    print("reference H:\n", np.array2string(np.array(ref.H), precision=8), f"err={ref.err_px:.3f} conf={ref.conf:.3f}")
    assert ref.err_px < 3.0
    assert np.abs(court.corners_px(ref) - DOUBLES_CORNERS).max() < CORNER_TOL


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            t0 = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t0:.1f}s)")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if fails else 0)
