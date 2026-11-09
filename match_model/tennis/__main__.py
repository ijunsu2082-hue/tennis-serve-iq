"""CLI entry point.

    python -m tennis analyze <video> [--a-name NAME --b-name NAME] [--no-ad]
                                     [--final-set tb|match_tb10|adv] [--quick] [--force]
    python -m tennis inbox                 # analyze every new video in ./inbox
    python -m tennis rebuild <out_dir>     # re-apply corrections + re-render (seconds)
    python -m tennis review <out_dir>      # open the review/correction UI
    python -m tennis chat <out_dir> [--web] [--ask "..."]   # ask questions (Claude API)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "chat":
        from .chat import main as chat_main
        return chat_main(argv[1:])
    ap = argparse.ArgumentParser(prog="tennis", description="Tennis match video analytics")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="analyze one video")
    a.add_argument("video")
    a.add_argument("--out", default=None, help="output dir (default out/<video_stem>)")
    a.add_argument("--a-name", default=None, help="name for player A (default: top row of score bug)")
    a.add_argument("--b-name", default=None, help="name for player B")
    a.add_argument("--a-hand", default=None, choices=["R", "L"],
                   help="player A's playing hand; if not given, right is assumed and the report says so")
    a.add_argument("--b-hand", default=None, choices=["R", "L"], help="player B's playing hand")
    a.add_argument("--no-ad", action="store_true", help="no-ad scoring (college dual matches)")
    a.add_argument("--final-set", default="tb", choices=["tb", "match_tb10", "adv"])
    a.add_argument("--best-of", type=int, default=3)
    a.add_argument("--quick", action="store_true", help="score + scoreboard stats only (no ball/player vision)")
    a.add_argument("--force", action="store_true", help="ignore cached stages and recompute")

    i = sub.add_parser("inbox", help="analyze every video in ./inbox that has no results yet")
    i.add_argument("--quick", action="store_true")
    i.add_argument("--no-ad", action="store_true")

    r = sub.add_parser("rebuild", help="apply corrections.json and re-render report")
    r.add_argument("out_dir")

    v = sub.add_parser("review", help="start the review UI")
    v.add_argument("out_dir")
    v.add_argument("--port", type=int, default=8765)

    sub.add_parser("chat", help="ask questions about an analyzed match (see: python -m tennis chat --help)")

    args = ap.parse_args(argv)

    if args.cmd == "analyze":
        from .pipeline import analyze, Options
        opts = Options(a_name=args.a_name, b_name=args.b_name, a_hand=args.a_hand, b_hand=args.b_hand,
                       no_ad=args.no_ad, final_set=args.final_set, best_of=args.best_of,
                       quick=args.quick, force=args.force)
        analyze(args.video, args.out, opts)
    elif args.cmd == "inbox":
        from .pipeline import analyze, Options
        vids = sorted(p for p in (ROOT / "inbox").iterdir() if p.suffix.lower() in VIDEO_EXT)
        if not vids:
            print("inbox/ is empty - drop match videos there")
        for p in vids:
            out = ROOT / "out" / p.stem
            if (out / "report.html").exists():
                print(f"skip {p.name} (already analyzed: {out}/report.html)")
                continue
            analyze(str(p), str(out), Options(quick=args.quick, no_ad=args.no_ad))
    elif args.cmd == "rebuild":
        from .pipeline import rebuild
        rebuild(args.out_dir)
    elif args.cmd == "review":
        from . import review_server
        sys.argv = ["review_server", args.out_dir, "--port", str(args.port)]
        review_server.main()


if __name__ == "__main__":
    sys.exit(main())
