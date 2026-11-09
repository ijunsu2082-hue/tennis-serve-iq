"""Review UI server (stdlib only).

    python -m tennis.review_server <out_dir> [--port 8765] [--video PATH] [--host 127.0.0.1]

Routes
  GET  /                  review/index.html (self-contained UI)
  GET  /match.json        current (corrected) match.json from out_dir
  GET  /corrections.json  saved corrections ({} if none)
  GET  /video             the match video, with HTTP Range support (seeking)
  GET  /report            the rendered report.html
  POST /save              body {"corrections": {...}} (or the corrections dict itself)
                          -> writes corrections.json, applies them to the
                          uncorrected base, re-renders report.html/points.csv/match.json

Request checks: the server only listens on this computer (a loopback address such
as 127.0.0.1; --host refuses any other), the Host header must name this computer
(localhost or a loopback address, so a web page that points its own name at
127.0.0.1 cannot read or save anything, and no other device can use it), and POST /save needs Content-Type application/json and, when the
browser sends an Origin, the same host and port as the Host header. A web page on
another site can therefore not send a save that overwrites corrections.json.

Base file policy: corrections always apply to the uncorrected pipeline output,
kept as out_dir/match_auto.json, so undoing an edit restores the automatic value.
At startup, a match.json without meta.review_saved_at or meta.corrections_applied
is treated as fresh pipeline output. It is copied to match_auto.json, and any existing
corrections.json is re-applied right away, so re-running the pipeline keeps
manual edits.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
UI_FILE = ROOT / "review" / "index.html"
CHUNK = 256 * 1024


class ReviewApp:
    def __init__(self, out_dir, video: str | None = None):
        self.out = Path(out_dir).resolve()
        self.lock = threading.Lock()
        self.match_path = self.out / "match.json"
        self.base_path = self.out / "match_auto.json"
        self.corr_path = self.out / "corrections.json"
        if not self.match_path.exists():
            raise FileNotFoundError(f"{self.match_path} not found")
        self._video_override = video
        self._init_base()

    # -- data ---------------------------------------------------------------
    def _read(self, p: Path):
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def _init_base(self):
        m = self._read(self.match_path)
        meta = m.get("meta") or {}
        # "python -m tennis rebuild" writes corrections_applied, the UI writes review_saved_at
        fresh = not (meta.get("review_saved_at") or meta.get("corrections_applied"))
        if fresh or not self.base_path.exists():
            shutil.copyfile(self.match_path, self.base_path)
            if fresh and self.corr_path.exists():
                self.save(self.corrections())

    def corrections(self) -> dict:
        if self.corr_path.exists():
            try:
                return self._read(self.corr_path)
            except (OSError, ValueError):
                return {}
        return {}

    def save(self, corrections: dict) -> dict:
        from .analytics import apply_corrections
        from .report import render
        with self.lock:
            clean = {str(k): v for k, v in (corrections or {}).items() if isinstance(v, dict) and v}
            tmp = self.corr_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, indent=1, sort_keys=True)
            os.replace(tmp, self.corr_path)
            base = self._read(self.base_path)
            m = apply_corrections(base, clean)
            m.setdefault("meta", {})["review_saved_at"] = datetime.now().isoformat(timespec="seconds")
            paths = render(m, self.out)
            return {"ok": True, "n_corrections": len(clean), "paths": paths,
                    "n_insights": len(m.get("insights") or [])}

    def video_path(self) -> Path | None:
        from .pipeline import local_video_path
        cands = []
        if self._video_override:
            cands.append(Path(self._video_override))
        lp = local_video_path(self.out)          # saved by analyze in cache/
        if lp is not None:
            cands.append(lp)
        try:
            v = (self._read(self.match_path).get("meta") or {}).get("video")
        except (OSError, ValueError):
            v = None
        if v:
            vp = Path(v)
            if vp.is_absolute():
                cands.append(vp)
            else:
                cands += [Path.cwd() / vp, ROOT / vp, self.out / vp, self.out.parent.parent / vp,
                          ROOT / "inbox" / vp.name, ROOT / vp.name]
        for c in cands:
            if c.is_file():
                return c
        return None


def _is_loopback(name: str) -> bool:
    """localhost, a name under .localhost, or a loopback address (127.x.x.x, ::1)."""
    n = name.strip().strip("[]").lower()
    if n == "localhost" or n.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(n).is_loopback
    except ValueError:
        return False


def _host_ok(host: str | None) -> bool:
    """Host header names this computer: localhost or a loopback address (any port).
    Any other address, a LAN one included, is another device or a renamed page."""
    if not host:
        return False
    h = host.strip()
    if h.startswith("["):                       # [::1]:8765
        name = h[1:h.find("]")] if "]" in h else h[1:]
    else:
        name = h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    return _is_loopback(name)


def make_handler(app: ReviewApp):
    class H(BaseHTTPRequestHandler):
        server_version = "TennisReview/1.0"

        def _guard(self) -> bool:
            """False (and a 403 sent) when the request may come from another site."""
            if not _host_ok(self.headers.get("Host")):
                self._json(403, {"error": "unknown host"})
                return False
            return True

        def log_message(self, fmt, *args):
            if os.environ.get("TENNIS_REVIEW_LOG"):
                super().log_message(fmt, *args)

        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode("utf-8"))

        def _file(self, p: Path, ctype: str):
            if not p.is_file():
                return self._json(404, {"error": f"{p.name} not found"})
            self._send(200, p.read_bytes(), ctype)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlparse(self.path).path
            if not self._guard():
                return
            try:
                if path in ("/", "/index.html"):
                    return self._file(UI_FILE, "text/html; charset=utf-8")
                if path == "/match.json":
                    return self._file(app.match_path, "application/json; charset=utf-8")
                if path == "/corrections.json":
                    return self._json(200, app.corrections())
                if path == "/report":
                    return self._file(app.out / "report.html", "text/html; charset=utf-8")
                if path == "/video":
                    return self._video()
                return self._json(404, {"error": "not found"})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _video(self):
            vp = app.video_path()
            if vp is None:
                return self._json(404, {"error": "video not found; pass --video"})
            size = vp.stat().st_size
            ctype = mimetypes.guess_type(str(vp))[0] or "video/mp4"
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            status = 200
            if rng:
                mt = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
                if not mt or (mt.group(1) == "" and mt.group(2) == ""):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                if mt.group(1) == "":            # suffix range: last N bytes
                    n = int(mt.group(2))
                    start = max(0, size - n)
                else:
                    start = int(mt.group(1))
                    if mt.group(2):
                        end = min(int(mt.group(2)), size - 1)
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(vp, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    buf = f.read(min(CHUNK, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._guard():
                return
            if path != "/save":
                return self._json(404, {"error": "not found"})
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return self._json(415, {"error": "send JSON (Content-Type: application/json)"})
            origin = self.headers.get("Origin")
            if origin is not None and urlparse(origin).netloc != (self.headers.get("Host") or ""):
                return self._json(403, {"error": "request from another site"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                corr = body.get("corrections", body) if isinstance(body, dict) else {}
                if not isinstance(corr, dict):
                    return self._json(400, {"error": "corrections must be an object"})
                self._json(200, app.save(corr))
            except (ValueError, TypeError) as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # report errors to the UI instead of dropping the request
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

    return H


def serve(out_dir, port: int = 8765, host: str = "127.0.0.1", video: str | None = None):
    if not _is_loopback(host):
        # the page can read the match, stream the video and save corrections: it is
        # for this computer only
        raise ValueError(f"--host must be a loopback address such as 127.0.0.1, not {host!r}")
    app = ReviewApp(out_dir, video)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    vp = app.video_path()
    print(f"Review UI: http://{host}:{httpd.server_address[1]}/  (out: {app.out})")
    print(f"Video: {vp if vp else 'NOT FOUND (pass --video PATH)'}")
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tennis.review_server")
    ap.add_argument("out_dir")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="loopback address to listen on (this computer only)")
    ap.add_argument("--video", default=None, help="override the video path from match.json")
    a = ap.parse_args(argv)
    httpd = serve(a.out_dir, a.port, a.host, a.video)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
