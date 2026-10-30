"""Thin wrapper around Apple Vision text recognition (macOS only, via pyobjc).

    ocr(img_bgr) -> [(text, conf, (x0, y0, x1, y1)), ...]

Boxes are in pixel coordinates of the input image with a top-left origin
(Vision itself returns normalised boxes with a bottom-left origin).
The first call in a process loads the recognition model, which can take a
while; later calls are ~0.05-0.25 s depending on image size and `fast`.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import cv2

try:  # pyobjc frameworks; import lazily-safe so non-mac imports don't crash
    import Vision  # type: ignore
    import Quartz  # type: ignore
    from Foundation import NSData  # type: ignore
    HAVE_VISION = True
except Exception:  # pragma: no cover
    HAVE_VISION = False


def _cgimage_from_bgr(img_bgr: np.ndarray):
    if img_bgr.ndim == 2:
        img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_GRAY2BGR)
    rgba = cv2.cvtColor(np.ascontiguousarray(img_bgr), cv2.COLOR_BGR2RGBA)
    h, w = rgba.shape[:2]
    data = NSData.dataWithBytes_length_(rgba.tobytes(), rgba.nbytes)
    provider = Quartz.CGDataProviderCreateWithCFData(data)
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    return Quartz.CGImageCreate(w, h, 8, 32, w * 4, cs,
                                Quartz.kCGImageAlphaNoneSkipLast | Quartz.kCGBitmapByteOrderDefault,
                                provider, None, False, Quartz.kCGRenderingIntentDefault)


def ocr(img_bgr: np.ndarray, fast: bool = False, languages: Optional[list[str]] = None,
        correction: bool = False, min_height: float = 0.0) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """Recognise text in a BGR (or gray) image.

    fast:       use the fast recognition level instead of accurate
    languages:  e.g. ["en-US"]; None = Vision default
    correction: language correction (off by default - scores are not words)
    min_height: minimum text height as a fraction of image height (0 = default)
    Returns [(text, confidence, (x0, y0, x1, y1))] with px boxes, top-left origin.
    """
    if not HAVE_VISION:
        raise RuntimeError("Apple Vision (pyobjc-framework-Vision) is not available")
    h, w = img_bgr.shape[:2]
    cg = _cgimage_from_bgr(img_bgr)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast if fast
                             else Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(bool(correction))
    if languages:
        req.setRecognitionLanguages_(list(languages))
    if min_height:
        req.setMinimumTextHeight_(float(min_height))
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {err}")
    out = []
    for obs in (req.results() or []):
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        c = cands[0]
        bb = obs.boundingBox()  # normalised, origin bottom-left
        x0 = bb.origin.x * w
        x1 = (bb.origin.x + bb.size.width) * w
        y1 = (1.0 - bb.origin.y) * h
        y0 = (1.0 - bb.origin.y - bb.size.height) * h
        box = (int(round(max(0, x0))), int(round(max(0, y0))),
               int(round(min(w, x1))), int(round(min(h, y1))))
        out.append((str(c.string()), float(c.confidence()), box))
    return out
