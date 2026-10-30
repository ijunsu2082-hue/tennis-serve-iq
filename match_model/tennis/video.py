"""Fast video access via ffmpeg rawvideo pipes.

Decoding is the main cost on long matches, so every reader uses the fast
h264 flags (-skip_loop_filter all -flags2 fast), which run ~70x realtime for
720p on an M1 when only a few frames per second are kept. Frames are streamed;
nothing holds the whole video in memory.
"""
from __future__ import annotations

import json
import subprocess
from typing import Iterator, Optional

import numpy as np

from .contracts import VideoInfo


def probe(path: str) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return VideoInfo(path=path, width=int(st["width"]), height=int(st["height"]),
                     fps=float(num) / float(den), duration=float(d["format"]["duration"]))


def iter_frames(path: str, fps: Optional[float] = None, size: Optional[tuple[int, int]] = None,
                start: Optional[float] = None, end: Optional[float] = None,
                crop: Optional[tuple[int, int, int, int]] = None,
                fast: bool = True, gray: bool = False) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (t, frame) with frame as HxWx3 uint8 **BGR** (or HxW if gray).

    fps:   output rate (None = native rate, every frame)
    size:  (w, h) output size after crop (None = keep)
    start/end: seconds (accurate seek)
    crop:  (x0, y0, x1, y1) in source pixels, applied before scaling
    fast:  use fast/inexact decoding flags (skip deblocking) - fine for analysis
    """
    info = probe(path)
    src_fps = info.fps
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if fast:
        cmd += ["-skip_loop_filter", "all", "-flags2", "fast"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if end is not None:
        cmd += ["-t", f"{max(0.0, end - (start or 0.0)):.3f}"]
    vf = []
    w, h = info.width, info.height
    if crop:
        # yuv420 sources can only be cropped at even offsets/sizes; odd values make
        # ffmpeg round silently and the raw frames shear. Snap outward to even.
        x0, y0, x1, y1 = (int(v) for v in crop)
        x0, y0 = max(0, x0 - (x0 & 1)), max(0, y0 - (y0 & 1))
        x1, y1 = min(info.width, x1 + (x1 & 1)), min(info.height, y1 + (y1 & 1))
        if (x1 - x0) & 1:
            x1 -= 1
        if (y1 - y0) & 1:
            y1 -= 1
        vf.append(f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0}")
        w, h = x1 - x0, y1 - y0
    if fps:
        vf.append(f"fps={fps}")
    if size:
        vf.append(f"scale={size[0]}:{size[1]}:flags=area")
        w, h = size
    if vf:
        cmd += ["-vf", ",".join(vf)]
    pix = "gray" if gray else "bgr24"
    cmd += ["-fps_mode", "passthrough" if not fps else "cfr", "-f", "rawvideo", "-pix_fmt", pix, "-"]
    ch = 1 if gray else 3
    nbytes = w * h * ch
    rate = fps or src_fps
    t0 = start or 0.0
    # stderr is discarded: stopping early (break / kill) makes ffmpeg log a
    # harmless "Broken pipe" for every reader
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=nbytes * 4)
    try:
        i = 0
        while True:
            buf = proc.stdout.read(nbytes)
            if len(buf) < nbytes:
                break
            arr = np.frombuffer(buf, np.uint8)
            frame = arr.reshape(h, w) if gray else arr.reshape(h, w, 3)
            yield t0 + i / rate, frame
            i += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def read_frame(path: str, t: float, size: Optional[tuple[int, int]] = None) -> np.ndarray:
    """Single accurate frame at time t (BGR)."""
    info = probe(path)
    for _, f in iter_frames(path, fps=None, size=size, start=t, end=t + max(0.2, 2.0 / info.fps), fast=False):
        return f.copy()
    raise ValueError(f"no frame at t={t}")
