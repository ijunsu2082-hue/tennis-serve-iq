"""HTML / CSV / JSON report rendering.

match.json shape (the single file analytics, report and the review UI share)
-----------------------------------------------------------------------------
{
  "meta": {
    "video": str,                 # the video's path inside the project, else its file name
                                  # (the full path is in <out>/cache/video_path.txt)
    "video_info": {"path","width","height","fps","duration"} | null,   # asdict(VideoInfo)
    "players": {"A": {"name": str, "hand": "R"|"L", "hand_given": bool}, "B": {...}},
    "format": asdict(MatchFormat),  # best_of, no_ad, games_per_set, tb_at, tb_points, final_set
    "pipeline_version": str,
    "generated_at": ISO-8601 str,
    "runtime_s": float,
    "has_scorebug": bool,         # True -> winners/scores come from OCR (scores never rescored)
    # optional: "title", "event", "date", "round", "notes"
  },
  "segments": [asdict(Segment), ...],
  "points": [asdict(PointRecord), ...],        # ordered by time; id unique (int)
  "stats": compute_stats(...) output,           # recomputed by render()
  "insights": [ {text, player, n, k, effect, comparison_pct, comparison_n, pct, ci,
                 confidence, chance_p, mean_conf, small_sample, low_confidence, kind,
                 key, evidence_point_ids, score, checked}, ... ],
  "recommendations": [ {for, against, text, basis, n, confidence}, ... ]
}
PointRecord.score_before carries {"sets","games","points","in_tb"} plus an
optional "ends". The points label is in A-B order. PointRecord.serves are
asdict(ServeVision) dicts.

render(match, out_dir) always recomputes mcp, stats, insights and
recommendations from the points, so the written files are always consistent.
"""
from __future__ import annotations

import copy
import csv
import html
import json
import math
import os
import re
from pathlib import Path
from typing import Optional

from .contracts import other

PLAYERS = ("A", "B")
LOW_CONF = 0.6
ACTION_MAX_P = 0.2              # the player cards leave out gaps chance gives 1 time in 5 or more
CARD_ITEMS = 3                  # most strengths, and most weaknesses, on a player card
PLAN_ITEMS = 4                  # most lines in a game plan
SMALL_MAP = 10                  # serves on one side before its top lane may be shaded as the main one
SERVE_DIGIT = {"wide": "4", "body": "5", "T": "6"}
FAULT_LETTER = {"net": "n", "wide": "w", "long": "d", "long_wide": "x"}
RESULT_ERR = {"net": "n", "out_wide": "w", "out_long": "d", "out_long_wide": "x"}


# ---------------------------------------------------------------------------
# MCP code
# ---------------------------------------------------------------------------
def _shot_letter(wing: Optional[str], stroke: Optional[str]) -> str:
    if stroke == "volley":
        return {"fh": "v", "bh": "z"}.get(wing, "v")
    if stroke == "overhead":
        return {"fh": "o", "bh": "p"}.get(wing, "o")
    return {"fh": "f", "bh": "b"}.get(wing, "q")


def mcp_code(point: dict) -> str:
    """Match Charting Project style code for a point.

    Serve fields: 4 wide / 5 body / 6 T / 0 unknown direction, plus the fault
    letter (n net, w wide, d deep, x wide+deep, e unknown fault type).
    A faulted 1st serve and the 2nd serve are joined with a space
    (e.g. "4n 6fqb*"). No shot directions/depths are coded. Rally shots: f/b groundstrokes,
    v/z volleys, o/p overheads, q unknown. Only the return and the last shot
    are known, so the shots in between are 'q'. Outcomes: * winner,
    @ unforced error, # forced error. An ace is "<dir>*", a service winner
    "<dir>#", a double fault "<1st> <2nd>" with both faults. Unknown ending:
    the code ends after the known part with "?".
    """
    serves = sorted([s for s in (point.get("serves") or []) if isinstance(s, dict)],
                    key=lambda s: s.get("n", 0))
    ending = point.get("ending")
    played_on = point.get("serve_number_won_on")
    server = point.get("server")
    parts = []

    def sfield(s, fault: bool) -> str:
        d = SERVE_DIGIT.get((s or {}).get("zone"), "0")
        if fault:
            return d + FAULT_LETTER.get((s or {}).get("fault"), "e")
        return d

    s1 = next((s for s in serves if s.get("n") == 1), None)
    s2 = next((s for s in serves if s.get("n") == 2), None)
    if ending == "double_fault":
        return f"{sfield(s1, True)} {sfield(s2, True)}"
    if played_on == 2 or (s1 and s1.get("result") == "fault"):
        parts.append(sfield(s1, True))
        in_serve = s2
    else:
        in_serve = s1
    code = sfield(in_serve, False)
    if ending == "ace":
        parts.append(code + "*")
        return " ".join(parts)
    if ending == "service_winner":
        parts.append(code + "#")
        return " ".join(parts)
    rally = point.get("rally_len")
    rally = rally if isinstance(rally, int) and rally >= 1 else None
    ret = point.get("return_wing")
    last_w, last_s = point.get("last_shot_wing"), point.get("last_shot_stroke")
    shots = []
    if rally is not None:
        n_after = rally - 1
        if n_after == 1:
            shots.append(_shot_letter(ret or last_w, last_s))
        elif n_after >= 2:
            shots.append(_shot_letter(ret, None))
            shots.extend("q" * (n_after - 2))
            shots.append(_shot_letter(last_w, last_s))
    else:
        if ret:
            shots.append(_shot_letter(ret, None))
        if last_w and ending in ("winner", "unforced_error", "forced_error"):
            shots.append("q")
            shots.append(_shot_letter(last_w, last_s))
    code += "".join(shots)
    if ending in ("unforced_error", "forced_error"):
        code += RESULT_ERR.get(point.get("last_shot_result"), "")
        code += "@" if ending == "unforced_error" else "#"
    elif ending == "winner":
        code += "*"
    else:
        code += "?"
    parts.append(code)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _name(match: dict, p: str) -> str:
    return (((match.get("meta") or {}).get("players") or {}).get(p) or {}).get("name") or p


def _json_default(o):
    if hasattr(o, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    raise TypeError(f"not JSON serialisable: {type(o)}")


def _final_state(match: dict):
    from . import scoring as sc
    from .analytics import _fmt_from_meta
    pts = match.get("points") or []
    if not pts:
        return None
    fmt = _fmt_from_meta(match.get("meta") or {})
    last = pts[-1]
    try:
        st = sc.state_from_score_dict(last.get("score_before") or {}, last.get("server") or "A", fmt)
        if last.get("winner") in PLAYERS and not st.match_over:
            st = sc.apply(st, last["winner"], fmt)
        return st
    except (ValueError, KeyError):
        return None


def _score_line(match: dict) -> dict:
    st = _final_state(match)
    if st is None:
        return {"sets": [], "current": None, "over": False}
    sets = [list(s) for s in st.sets]
    cur = None
    if not st.match_over and (st.games != [0, 0] or st.points != [0, 0]):
        from .scoring import point_label
        cur = {"games": list(st.games), "points": point_label(st), "in_tb": st.in_tb}
    winner = None
    if st.match_over:
        from .scoring import sets_won
        winner = "A" if sets_won(st, "A") > sets_won(st, "B") else "B"
    return {"sets": sets, "current": cur, "over": st.match_over, "winner": winner}


def _fconf(p: dict, field: str) -> Optional[float]:
    from .analytics import field_conf
    return field_conf(p, field)


def _is_low(p: dict, field: str) -> bool:
    c = _fconf(p, field)
    return c is not None and c < LOW_CONF


def _fmt_t(t) -> str:
    if t is None:
        return ""
    t = float(t)
    h, r = divmod(int(t), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# SVG charts (inline; colours come from CSS classes so dark mode works)
# ---------------------------------------------------------------------------
ZONE_BANDS = {"T": (0.0, 1.372), "body": (1.372, 2.743), "wide": (2.743, 4.115)}
BOX_W, BOX_D = 4.115, 6.4          # service box width / depth (m)


def svg_serve_court(zones: dict, bounces: list, label: str) -> str:
    """Both service boxes of one half-court as the server sees them: net at the
    bottom, deuce box on the left. zones = stats serve_zones[sn] ({"deuce", "ad"});
    bounces: [(x, depth, kind)] with x in metres from the centre line (negative =
    deuce box) and kind 'in'|'fault'. Zone shading uses currentColor, so the
    figure takes the server's colour from its CSS class."""
    s = 24.0
    OUT_SIDE, OUT_LONG = 1.4, 1.6
    PADX, PADT, PADB = 4.0, 4.0, 22.0
    W = 2 * (BOX_W + OUT_SIDE) * s
    H = (BOX_D + OUT_LONG) * s

    def X(x):
        return PADX + (x + BOX_W + OUT_SIDE) * s

    def Y(v):
        return PADT + (BOX_D + OUT_LONG - v) * s

    out = [f'<svg class="court" viewBox="0 0 {W + 2 * PADX:.0f} {H + PADT + PADB:.0f}" role="img" '
           f'aria-label="{_esc(label)}">']
    for side, sg in (("deuce", -1.0), ("ad", 1.0)):
        zd = zones.get(side) or {}
        for z, (u0, u1) in ZONE_BANDS.items():
            d = zd.get(z) or {}
            share = (d.get("share") or {}).get("pct") or 0
            x0, x1 = sorted((X(sg * u0), X(sg * u1)))
            cx = (x0 + x1) / 2
            out.append(f'<rect class="zone" x="{x0:.1f}" y="{Y(BOX_D):.1f}" width="{x1 - x0:.1f}" '
                       f'height="{BOX_D * s:.1f}" style="fill-opacity:{0.04 + 0.5 * share / 100:.2f}"/>')
            out.append(f'<text class="zl" x="{cx:.1f}" y="{Y(BOX_D - 0.6):.1f}">{_esc(z)}</text>')
            if d.get("count"):
                out.append(f'<text class="zc" x="{cx:.1f}" y="{Y(3.6):.1f}">{d["count"]}</text>')
                out.append(f'<text class="zp" x="{cx:.1f}" y="{Y(2.7):.1f}">{share:.0f}%</text>')
            won = d.get("won") or {}
            if won.get("n"):
                out.append(f'<text class="zw" x="{cx:.1f}" y="{Y(1.2):.1f}">{won["k"]}/{won["n"]}'
                           f'<title>server won {won["k"]} of {won["n"]}</title></text>')
    out.append(f'<rect class="line" x="{X(-BOX_W):.1f}" y="{Y(BOX_D):.1f}" width="{2 * BOX_W * s:.1f}" '
               f'height="{BOX_D * s:.1f}"/>')
    out.append(f'<line class="line" x1="{X(0):.1f}" x2="{X(0):.1f}" y1="{Y(BOX_D):.1f}" y2="{Y(0):.1f}"/>')
    out.append(f'<line class="net" x1="{PADX:.1f}" x2="{PADX + W:.1f}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    lim_x, lim_v = BOX_W + OUT_SIDE - 0.1, BOX_D + OUT_LONG - 0.1
    for x, v, kind in bounces:
        px, py = X(max(-lim_x, min(lim_x, x))), Y(max(0.1, min(lim_v, v)))
        if kind == "fault":
            out.append(f'<path class="fx" d="M{px - 3.5:.1f},{py - 3.5:.1f}L{px + 3.5:.1f},{py + 3.5:.1f}'
                       f'M{px - 3.5:.1f},{py + 3.5:.1f}L{px + 3.5:.1f},{py - 3.5:.1f}"/>')
        else:
            out.append(f'<circle class="bin" cx="{px:.1f}" cy="{py:.1f}" r="2.2"/>')
    for side, sg in (("deuce", -1.0), ("ad", 1.0)):
        n = (zones.get(side) or {}).get("n", 0)
        out.append(f'<text class="side" x="{X(sg * BOX_W / 2):.1f}" y="{Y(0) + 15:.1f}">{side} · {n} known</text>')
    out.append("</svg>")
    return "".join(out)


def svg_serve_map(zones: dict, label: str) -> str:
    """The coach's serve picture: both service boxes as the server sees them, deuce box
    on the left, net at the bottom. Each lane shows its share in big type; the side's
    most used lane is shaded most. A side with fewer than SMALL_MAP known serves never
    gets that shading, so a handful of serves cannot look like a habit."""
    LANE, TOP, BOX_H, GAP = 64.0, 2.0, 158.0, 8.0
    W = 6 * LANE + GAP + 4
    H = TOP + BOX_H + 26
    out = [f'<svg class="sm" viewBox="0 0 {W:.0f} {H:.0f}" role="img" aria-label="{_esc(label)}">']
    for s_i, (side, order) in enumerate((("deuce", ("wide", "body", "T")), ("ad", ("T", "body", "wide")))):
        x0 = 2.0 + s_i * (3 * LANE + GAP)
        zd = zones.get(side) or {}
        n = zd.get("n", 0)
        counts = {z: (zd.get(z) or {}).get("count") or 0 for z in order}
        top = max(counts.values()) if n else 0
        hot = {z for z, c in counts.items() if c == top and top} if n >= SMALL_MAP else set()
        out.append(f'<rect class="base" x="{x0:.1f}" y="{TOP}" width="{3 * LANE:.1f}" height="{BOX_H}"/>')
        for i, z in enumerate(order):
            d = zd.get(z) or {}
            share = (d.get("share") or {}).get("pct") or 0
            x, cx = x0 + i * LANE, x0 + (i + 0.5) * LANE
            is_hot = z in hot
            op = 0.42 if is_hot else (0.04 + 0.3 * share / 100 if n else 0)
            out.append(f'<g class="{"hot" if is_hot else ""}">')
            out.append(f'<rect class="lane" x="{x:.1f}" y="{TOP}" width="{LANE:.1f}" height="{BOX_H}" '
                       f'style="fill-opacity:{op:.2f}"/>')
            out.append(f'<text class="z" x="{cx:.1f}" y="{TOP + 19}">{"Body" if z == "body" else z.capitalize() if z == "wide" else "T"}</text>')
            if n:
                out.append(f'<text class="p" x="{cx:.1f}" y="{TOP + 84}">{share:.0f}%</text>')
                out.append(f'<text class="c" x="{cx:.1f}" y="{TOP + 104}">{counts[z]} of {n}</text>')
                won = d.get("won") or {}
                if won.get("n"):
                    out.append(f'<text class="wn" x="{cx:.1f}" y="{TOP + 142}">won {won["k"]}/{won["n"]}'
                               f'<title>server won {won["k"]} of {won["n"]}</title></text>')
            out.append("</g>")
        for i in (1, 2):
            x = x0 + i * LANE
            out.append(f'<line class="sep" x1="{x:.1f}" x2="{x:.1f}" y1="{TOP}" y2="{TOP + BOX_H}"/>')
        out.append(f'<rect class="box" x="{x0:.1f}" y="{TOP}" width="{3 * LANE:.1f}" height="{BOX_H}"/>')
        out.append(f'<line class="net" x1="{x0:.1f}" x2="{x0 + 3 * LANE:.1f}" y1="{TOP + BOX_H}" y2="{TOP + BOX_H}"/>')
        few = "" if n >= SMALL_MAP else ", too few"
        out.append(f'<text class="side{" few" if few else ""}" x="{x0 + 1.5 * LANE:.1f}" y="{H - 5:.1f}">'
                   f'{side.capitalize()} side, {n} serves{few}</text>')
    out.append("</svg>")
    return "".join(out)


def serve_maps(stats: dict, names: dict) -> dict:
    """{player: {"1": svg, "2": svg}} of svg_serve_map."""
    return {P: {sn: svg_serve_map(stats["players"][P]["serve_zones"][sn],
                                  f"{names[P]}, {'1st' if sn == '1' else '2nd'} serve: where serves landed")
                for sn in ("1", "2")} for P in PLAYERS}


def _serve_geometry(p: dict, s: dict):
    """(u, v) of a serve bounce relative to its target box, or None."""
    b = s.get("bounce_xy")
    if not b or len(b) != 2 or b[0] is None or b[1] is None:
        return None
    x, y = float(b[0]), float(b[1])
    if y == 0:
        return None
    sg = 1.0 if y > 0 else -1.0
    u = (-x * sg) if p.get("side") == "deuce" else (x * sg)
    return (u, abs(y))


FAULT_TXT = {"net": "net", "long": "long", "wide": "wide", "long_wide": "long and wide"}


def serve_courts(match: dict, stats: dict) -> dict:
    """{player: {"1": {"svg", "faults", "caption", ...}, "2": {...}}}. faults is the short
    text of the fault kinds; caption gives every serve that went in per side (the court
    only draws those with a known spot), every fault, and how many faults have a kind
    and whether those kinds were checked in a review."""
    from .analytics import field_checked
    out = {}
    pts = match.get("points") or []
    for P in PLAYERS:
        ps = stats["players"][P]
        out[P] = {}
        for sn in ("1", "2"):
            bounces = []
            fcounts = {k: 0 for k in FAULT_TXT}
            n_fault = n_typed = n_typed_checked = 0
            went_in = {"deuce": 0, "ad": 0}
            for p in pts:
                if p.get("server") != P:
                    continue
                for s in p.get("serves") or []:
                    if str(s.get("n")) != sn:
                        continue
                    if s.get("result") == "in" and p.get("side") in went_in:
                        went_in[p["side"]] += 1
                    if s.get("result") == "fault":
                        n_fault += 1
                    if s.get("result") == "fault" and s.get("fault") in fcounts:
                        fcounts[s["fault"]] += 1
                        n_typed += 1
                        n_typed_checked += field_checked(p, f"serve{sn}_fault")
                    g = _serve_geometry(p, s)
                    if g and s.get("result") == "fault" and 0 <= g[0] <= BOX_W and 0 <= g[1] <= BOX_D:
                        g = None       # called a fault by other evidence: the tracked bounce is wrong
                    if g and s.get("result") in ("in", "fault"):
                        x = -g[0] if p.get("side") == "deuce" else g[0]
                        bounces.append((x, g[1], s["result"]))
            label = f"{_name(match, P)}, {'1st' if sn == '1' else '2nd'} serve placement"
            faults = ", ".join(f"{FAULT_TXT[k]} {v}" for k, v in fcounts.items() if v)
            zones = ps["serve_zones"][sn]
            known = {sd: (zones.get(sd) or {}).get("n", 0) for sd in ("deuce", "ad")}
            cap = (f"Went in: deuce side {went_in['deuce']} ({known['deuce']} with a known spot), "
                   f"ad side {went_in['ad']} ({known['ad']} with a known spot). ")
            if not n_fault:
                cap += "No faults found."
            elif not n_typed:
                cap += f"Faults: {n_fault}; the kind of miss was not found for any of them."
            else:
                if n_typed_checked == n_typed:
                    src = "checked in a review"
                elif n_typed_checked:
                    src = f"{n_typed_checked} of them checked in a review"
                else:
                    src = "found by the computer alone, not checked"
                cap += (f"Faults: {n_fault}; the kind of miss is known for {n_typed} "
                        f"({faults}), {src}.")
            out[P][sn] = {"svg": svg_serve_court(zones, bounces, label), "faults": faults,
                          "caption": cap, "n_fault": n_fault, "n_fault_typed": n_typed,
                          "went_in": went_in}
    return out


def chance_words(p) -> str:
    """A chance test result in words: "about 1 time in 12", "less than 1 time in 1,000"."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "unknown"
    if p < 0.001:
        return "less than 1 time in 1,000"
    if p <= 0.5:
        return f"about 1 time in {round(1.0 / p):,}"
    return "more than 1 time in 2"


def svg_momentum(match: dict) -> str:
    pts = match.get("points") or []
    if not pts:
        return ""
    W, H, PADL, PADR, PADT, PADB = 900, 200, 8, 8, 18, 22
    diff, d = [0], 0
    for p in pts:
        if p.get("winner") == "A":
            d += 1
        elif p.get("winner") == "B":
            d -= 1
        diff.append(d)
    lo, hi = min(-3, min(diff)), max(3, max(diff))
    n = len(pts)

    def X(i):
        return PADL + (W - PADL - PADR) * i / max(1, n)

    def Y(v):
        return PADT + (H - PADT - PADB) * (hi - v) / (hi - lo)

    a, b = _esc(_name(match, "A")), _esc(_name(match, "B"))
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Running points difference">']
    out.append(f'<line class="axis" x1="{PADL}" x2="{W - PADR}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    out.append(f'<text class="lab pa" x="{PADL}" y="{PADT - 6}">{a} ahead</text>')
    out.append(f'<text class="lab pb" x="{PADL}" y="{H - 6}">{b} ahead</text>')
    prev_set = None
    for i, p in enumerate(pts):
        sn = p.get("set_no")
        if sn != prev_set:
            x = X(i)
            if i:
                out.append(f'<line class="setline" x1="{x:.1f}" x2="{x:.1f}" y1="{PADT}" y2="{H - PADB}"/>')
            out.append(f'<text class="lab" x="{x + 4:.1f}" y="{PADT + 10}">set {sn}</text>')
            prev_set = sn
        if p.get("gap_before") and not _filled(p):
            x = X(i)
            out.append(f'<line class="gapline" x1="{x:.1f}" x2="{x:.1f}" y1="{PADT}" y2="{H - PADB}">'
                       f'<title>points may be missing before #{p.get("id")}</title></line>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(diff))
    area = f"{path} L{X(n):.1f},{Y(0):.1f} L{X(0):.1f},{Y(0):.1f} Z"
    out.append(f'<clipPath id="mA"><rect x="0" y="0" width="{W}" height="{Y(0):.1f}"/></clipPath>'
               f'<clipPath id="mB"><rect x="0" y="{Y(0):.1f}" width="{W}" height="{H - Y(0):.1f}"/></clipPath>')
    out.append(f'<path class="marea pa" clip-path="url(#mA)" d="{area}"/>'
               f'<path class="marea pb" clip-path="url(#mB)" d="{area}"/>')
    out.append(f'<path class="mline" d="{path}"/>')
    for i, p in enumerate(pts):
        fl = p.get("flags") or {}
        kind = next((k for k, f in (("match point", "match_point"), ("set point", "set_point"),
                                    ("break point", "break_point")) if fl.get(f)), None)
        if kind:
            key = kind.replace(" ", "_")
            holders = fl.get(key + "_for") or ([other(p["server"])] if key == "break_point" and p.get("server")
                                               else [])
            conv = p.get("winner") in holders
            who = " and ".join(_name(match, h) for h in holders) or "?"
            out.append(f'<circle class="{"bpc" if conv else "bps"}" cx="{X(i + 1):.1f}" '
                       f'cy="{Y(diff[i + 1]):.1f}" r="3.5"><title>#{p.get("id")} {kind} for {_esc(who)}: '
                       f'{"won" if conv else "saved"}</title></circle>')
    out.append("</svg>")
    return "".join(out)


def wing_rows(stats: dict) -> list:
    """Winners / errors by wing: [{"label", "A": (fh, bh), "B": (fh, bh)}]."""
    P_ = stats["players"]
    rows = []
    for e, lab in (("winner", "Winners"), ("unforced_error", "Unforced errors"),
                   ("forced_error", "Forced errors")):
        rows.append({"label": lab, **{p: (P_[p]["endings"][e]["fh"], P_[p]["endings"][e]["bh"]) for p in PLAYERS}})
    rows.append({"label": "Return errors", **{p: (P_[p]["return_by_wing"]["fh"]["errors"],
                                                 P_[p]["return_by_wing"]["bh"]["errors"]) for p in PLAYERS}})
    return rows


def rally_rows(stats: dict) -> list:
    """Points won by rally length: [{"label", "n", "A", "B", "pct_a"}]."""
    rows = []
    for bk, r in stats["players"]["A"]["rally"].items():
        n, k = r["n"], r["k"]
        rows.append({"label": bk, "n": n, "A": k, "B": n - k, "pct_a": (100.0 * k / n) if n else None})
    return rows


# ---------------------------------------------------------------------------
# the coach's page: short actions and compact pictures
# ---------------------------------------------------------------------------
_ZW = {"wide": "wide", "body": "to the body", "T": "down the T"}
_ZL = {"wide": "Wide", "body": "Body", "T": "T"}
_TXT_ZONE = {"wide": "wide", "into the body": "body", "down the T": "T"}
_PREF_RE = re.compile(r"serves (wide|into the body|down the T) on the \w+ side \((\d+) of (\d+)\); the next most "
                      r"common spot was (wide|into the body|down the T), (\d+) of (\d+)")
_LEVEL = {"high": (3, "Strong"), "medium": (2, "Likely"), "low": (1, "Early sign")}
_WING_KEYS = ("ret_wing_", "winner_wing", "unforced_error_wing")


def _bar(label: str, pct: float, k=None, n=None) -> dict:
    return {"label": label, "pct": pct, "txt": f"{pct:.0f}%", "of": f"{k} of {n}" if n else ""}


def _cmp_bar(label: str, i: dict) -> dict:
    """The comparison group of a note (its count comes from its percent and size)."""
    cp, cn = i.get("comparison_pct") or 0.0, i.get("comparison_n")
    return _bar(label, cp, round(cp * cn / 100.0), cn) if cn else _bar(label, cp)


def coach_note(i: dict, names: dict, players: Optional[dict] = None) -> Optional[dict]:
    """One note in the coach's short form: {"player", "kind" (strength, weakness or tendency),
    "label", "cap", "bars", "plan", "stat", "dots", "level", "notes"}. "plan" is the short order
    for whoever plays against this player. None when the note has no short form."""
    key, X, eff = i.get("key") or "", i.get("player"), i.get("effect") or 0
    if X not in PLAYERS:
        return None
    nx, no = names[X], names[other(X)]
    k, n, pct = i.get("k"), i.get("n"), i.get("pct") or 0.0
    label, plan, cap, bars = None, None, None, None
    kind = i.get("kind") or "tendency"
    if key.startswith("ret_wing_") and eff > 0:
        w, ow = ("backhand", "forehand") if key.endswith("bh") else ("forehand", "backhand")
        label, plan, cap = f"{w.capitalize()} return", f"Serve to {nx}'s {w}", "Returns missed"
        bars = [_bar(w.capitalize(), pct, k, n), _cmp_bar(ow.capitalize(), i)]
    elif key.startswith("zone_pref_"):
        _, _, sn, side = key.split("_")
        m = _PREF_RE.search(i.get("text") or "")
        if not m:
            return None
        z, z2 = _TXT_ZONE[m.group(1)], _TXT_ZONE[m.group(4)]
        k2, n2 = int(m.group(5)), int(m.group(6))
        plan = f"Expect {'1st' if sn == '1' else '2nd'} serves {_ZW[z]} on the {side} side"
        cap, bars = "Where they landed", [_bar(_ZL[z], pct, k, n), _bar(_ZL[z2], 100.0 * k2 / n2, k2, n2)]
    elif key.startswith("zone_eff_"):
        _, _, sn, side, z = key.split("_")
        word = "1st" if sn == "1" else "2nd"
        label = f"{word} serve {_ZW[z]}, {side} side"
        what = f"{word} serves {_ZW[z]} on the {side} side"
        plan = f"Watch for {what}" if eff > 0 else f"Attack {what}"
        cap, bars = "Points won", [_bar(_ZL[z], pct, k, n), _cmp_bar("Elsewhere", i)]
    elif key == "pressure_first_in":
        label, cap = "First serve on big points", "First serves in"
        plan = f"Expect {nx}'s first serve on big points" if eff > 0 else f"Make {nx} play the big points"
        bars = [_bar("Big points", pct, k, n), _cmp_bar("Other points", i)]
    elif key == "pressure_won" and eff > 0:
        label, plan, cap = "Big points", f"Expect {nx} to lift on big points", "Points won"
        bars = [_bar("Big points", pct, k, n), _cmp_bar("Other points", i)]
    elif key == "second_serve_won" and eff > 0:
        label, plan, cap = "2nd serve points", f"Expect a strong 2nd serve from {nx}", "2nd serve points won"
        bars = [_bar(nx, pct, k, n), _cmp_bar(no, i)]
    elif key == "rally_long" and eff > 0:
        label, plan, cap = "Long rallies", "Keep the points short", "Points won"
        bars = [_bar("9+ shots", pct, k, n), _cmp_bar("1 to 4 shots", i)]
    elif key in ("unforced_error_wing", "winner_wing"):
        w, ow = ("backhand", "forehand") if "backhand" in (i.get("text") or "") else ("forehand", "backhand")
        if key == "winner_wing":
            label, plan, cap = f"{w.capitalize()} winners", f"Keep the ball off {nx}'s {w}", "Winners"
        else:
            label, plan, cap = f"{w.capitalize()} errors", f"Rally to {nx}'s {w}", "Unforced errors"
        bars = [_bar(w.capitalize(), pct, k, n), _bar(ow.capitalize(), 100.0 - pct, n - k, n)]
    elif key == "net":
        label, cap = "Net play", "Points won"
        plan = f"Keep {nx} away from the net" if eff > 0 else f"Draw {nx} to the net"
        bars = [_bar("At the net", pct, k, n), _cmp_bar("Staying back", i)]
    if plan is None:
        return None
    dots, level = _LEVEL.get(i.get("confidence"), _LEVEL["low"])
    notes = []
    if i.get("small_sample"):
        notes.append("small sample")
    if (i.get("checked") or {}).get("status") == "none":
        notes.append("computer only, not checked")
    if key.startswith(_WING_KEYS) and not ((players or {}).get(X) or {}).get("hand_given"):
        hand = ((players or {}).get(X) or {}).get("hand") or "R"
        notes.append(f"assumes {'left' if hand == 'L' else 'right'}-handed")
    stat = cap + ": " + ", ".join(f"{b['label'].lower() if b['label'] not in names.values() and b['label'] != 'T' else b['label']} "
                                  f"{b['txt']}" + (f" ({b['of']})" if b["of"] else "") for b in bars)
    return {"player": X, "kind": kind, "label": label, "plan": plan, "stat": stat, "cap": cap, "bars": bars,
            "dots": dots, "level": level, "notes": notes, "key": key}


def player_cards(insights: list, names: dict, players: Optional[dict] = None) -> dict:
    """{player: {"strengths", "weaknesses", "plan"}}, up to CARD_ITEMS in each, keeping the
    order of the notes. A note whose gap chance gives at least ACTION_MAX_P of the time is
    left out: an order built on a gap that is likely luck would mislead a coach. It stays
    in "Every note"."""
    out = {P: {"strengths": [], "weaknesses": [], "plan": []} for P in PLAYERS}
    for i in insights or []:
        p = i.get("chance_p")
        if p is None or p >= ACTION_MAX_P:
            continue
        c = coach_note(i, names, players)
        if not c:
            continue
        card = out[c["player"]]
        group = {"strength": "strengths", "weakness": "weaknesses"}.get(c["kind"])
        if group and len(card[group]) < CARD_ITEMS:
            card[group].append(c)
        card["plan"].append(c)
    # a weakness is the first thing to go after, so it leads the game plan
    rank = {"weakness": 0, "strength": 1}
    for card in out.values():
        card["plan"] = sorted(card["plan"], key=lambda c: rank.get(c["kind"], 2))[:PLAN_ITEMS]
    return out


def h2h_rows(stats: dict) -> list:
    """Head to head rows: facing bars, a few numbers a coach reads first."""
    A, B = stats["players"]["A"], stats["players"]["B"]
    rows = []

    def pct(label, f, better="high"):
        a, b = f(A), f(B)
        pa, pb = a.get("pct"), b.get("pct")
        lead = ""
        if pa is not None and pb is not None and pa != pb:
            lead = "A" if (pa > pb) == (better == "high") else "B"
        rows.append({"label": label, "a": "-" if pa is None else f"{pa:.0f}%", "b": "-" if pb is None else f"{pb:.0f}%",
                     "a_of": f"{a['k']} of {a['n']}" if a.get("n") else "",
                     "b_of": f"{b['k']} of {b['n']}" if b.get("n") else "",
                     "a_w": pa or 0, "b_w": pb or 0, "lead": lead})

    def count(label, f, better="high"):
        a, b = f(A), f(B)
        top = max(a, b, 1)
        lead = "" if a == b else ("A" if (a > b) == (better == "high") else "B")
        rows.append({"label": label, "a": str(a), "b": str(b), "a_of": "", "b_of": "",
                     "a_w": 100.0 * a / top, "b_w": 100.0 * b / top, "lead": lead})

    pct("Points won", lambda s: s["points_won"])
    pct("1st serve in", lambda s: s["first_serve_in"])
    pct("1st serve won", lambda s: s["first_serve_won"])
    pct("2nd serve won", lambda s: s["second_serve_won"])
    pct("Return in play", lambda s: s["return_in"])
    pct("Break points won", lambda s: s["break_points"]["converted"])
    count("Aces", lambda s: s["aces"].get("k") or 0)
    count("Double faults", lambda s: s["double_faults"].get("k") or 0, better="low")
    count("Winners", lambda s: s["endings"]["winner"]["total"])
    count("Unforced errors", lambda s: s["endings"]["unforced_error"]["total"], better="low")
    return rows


BIG_POINT_ROWS = (("all", "All points"), ("game_point", "Game point"), ("break_point", "Break point"))


def big_point_rows(serve_patterns: dict) -> dict:
    """{player: {"rows", "computer_only"}}: where serves landed in a few score situations.
    Uses one source only (checked values, or computer values when no serve was checked),
    so the two are never added together."""
    out = {}
    small = (serve_patterns or {}).get("small", 10)
    for P in PLAYERS:
        srcs = (((serve_patterns or {}).get("players") or {}).get(P) or {}).get("sources") or {}

        def summed(src, sit):
            g = {"T": 0, "body": 0, "wide": 0}
            for slot in ("1", "2"):
                d = ((srcs.get(src) or {}).get("groups", {}).get(sit, {}).get(slot) or {}).get("both") or {}
                for z in g:
                    g[z] += d.get(z) or 0
            return g

        src = max(("checked", "auto"), key=lambda s: sum(summed(s, "all").values()))
        rows = []
        for sit, label in BIG_POINT_ROWS:
            g = summed(src, sit)
            seen = sum(g.values())
            rows.append({"label": label, "seen": seen, "too_small": seen < small,
                         "pct": {z: (100.0 * c / seen if seen else 0.0) for z, c in g.items()}})
        out[P] = {"rows": rows, "computer_only": src == "auto"}
    return out


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------
def _filled(p: dict) -> bool:
    from .analytics import score_filled
    return score_filled(p)


# which point fields each row of the statistics table is built from (for its
# "checked in a review" column)
STAT_FIELDS = {
    "Points won": ("winner",), "Service points won": ("winner",), "Return points won": ("winner",),
    "1st serve in": ("serves",), "1st serve points won": ("serves", "winner"),
    "2nd serve points won": ("serves", "winner"), "Aces (per point served)": ("ending",),
    "Double faults (per point served)": ("serves",), "Return in play": ("rally_len", "ending"),
    "Break points saved": ("winner",), "Break points converted": ("winner",),
    "Pressure points won": ("winner",), "Net points won": ("net_approaches", "winner"),
    "Winners": ("ending",), "Unforced errors": ("ending",), "Forced errors": ("ending",),
    "Distance run per point": ("player_dist_m",),
}

WINNER_SOURCE_TXT = {"ocr": "read from the score board", "engine": "worked out from the score (the board skipped the point)",
                     "manual": "set in a review", "vision": "guessed from the video", "unknown": "not known"}


# rows that count one kind of ending: their "checked" column looks only at the points
# with that ending, because an ending checked on a double fault says nothing about
# whether any ace or winner was checked
ENDING_ROWS = {"Aces (per point served)": ("ace", "aces"), "Winners": ("winner", "winners"),
               "Unforced errors": ("unforced_error", "unforced errors"),
               "Forced errors": ("forced_error", "forced errors")}


def _checked_txt(points: list, fields: tuple, noun: str = "points") -> str:
    from .analytics import checked_status
    if not fields:
        return ""
    c = checked_status(points, fields)
    if c["status"] == "all":
        return f"yes, all {noun}"
    if c["status"] == "none":
        return "no, computer only"
    return f"partly, {c['k']} of {c['n']} {noun}"


def _r(d: dict) -> str:
    if not d or d.get("pct") is None:
        return "-"
    return f"{d['pct']:.0f}% <small>({d['k']}/{d['n']})</small>"


def key_stat_rows(stats: dict, points: Optional[list] = None) -> list:
    """Rows of the statistics table. With the points, each row also says whether the
    values it is built from were checked in a review ("checked")."""
    A, B = stats["players"]["A"], stats["players"]["B"]
    rows = []

    def add(label, fa, fb, better="high", raw=False):
        va, vb = fa(A), fb(B) if fb else fa(B)
        if raw:
            sa, sb = va, vb
            na = nb = None
        else:
            sa, sb = _r(va), _r(vb)
            na, nb = va.get("pct"), vb.get("pct")
        hl = ""
        if na is not None and nb is not None and na != nb:
            hl = ("A" if (na > nb) == (better == "high") else "B")
        rows.append({"label": label, "a": sa, "b": sb, "hl": hl})

    add("Points won", lambda s: s["points_won"], None)
    add("Service points won", lambda s: s["serve_points_won"], None)
    add("Return points won", lambda s: s["return_points_won"], None)
    add("1st serve in", lambda s: s["first_serve_in"], None)
    add("1st serve points won", lambda s: s["first_serve_won"], None)
    add("2nd serve points won", lambda s: s["second_serve_won"], None)
    add("Aces (per point served)", lambda s: s["aces"], None)
    add("Double faults (per point served)", lambda s: s["double_faults"], None, better="low")
    add("Return in play", lambda s: s["return_in"], None)
    add("Break points saved", lambda s: s["break_points"]["saved"], None)
    add("Break points converted", lambda s: s["break_points"]["converted"], None)
    add("Pressure points won", lambda s: s["pressure"]["won"], None)
    add("Net points won", lambda s: s["net"]["points"], None)
    for e, lab in (("winner", "Winners"), ("unforced_error", "Unforced errors"),
                   ("forced_error", "Forced errors")):
        rows.append({"label": lab, "a": str(A["endings"][e]["total"]), "b": str(B["endings"][e]["total"]),
                     "hl": ""})

    def dist(s):
        d = s["distance"]
        return f"{d['mean_m']:.0f} m <small>(n={d['n']})</small>" if d["mean_m"] is not None else "-"
    rows.append({"label": "Distance run per point", "a": dist(A), "b": dist(B), "hl": ""})
    if points is not None:
        for r in rows:
            fields = STAT_FIELDS.get(r["label"], ())
            if r["label"] in ENDING_ROWS:
                kind, noun = ENDING_ROWS[r["label"]]
                r["checked"] = _checked_txt([p for p in points if p and p.get("ending") == kind], fields, noun)
            else:
                r["checked"] = _checked_txt(points, fields)
    return rows


SERVE_TXT = {"in": "in", "fault": "fault", "unknown": "?"}


def _serve_cell(s: Optional[dict]) -> str:
    if not s:
        return ""
    r = s.get("result") or "unknown"
    z = s.get("zone")
    t = SERVE_TXT.get(r, r)
    if r == "fault":
        t = f"fault{(' ' + s['fault'].replace('_', '+')) if s.get('fault') else ''}"
    elif z:
        t = f"{z} {t}"
    if s.get("speed_kmh") and r == "in":
        t += f" {s['speed_kmh']:.0f}"
    return t


ENDING_TXT = {"ace": "ace", "service_winner": "service winner", "double_fault": "double fault",
              "winner": "winner", "unforced_error": "unforced error", "forced_error": "forced error",
              "unknown": "?"}


def point_rows(match: dict, video_href: Optional[str]) -> list:
    rows = []
    for p in match.get("points") or []:
        sb = p.get("score_before") or {}
        fl = p.get("flags") or {}
        tags = [k for k, f in (("BP", "break_point"), ("SP", "set_point"), ("MP", "match_point")) if fl.get(f)]
        serves = sorted(p.get("serves") or [], key=lambda s: s.get("n", 0))
        s1 = next((s for s in serves if s.get("n") == 1), None)
        s2 = next((s for s in serves if s.get("n") == 2), None)
        ending = p.get("ending")
        eb = p.get("ending_by")
        etxt = ENDING_TXT.get(ending, ending or "")
        if ending in ("winner", "unforced_error", "forced_error"):
            wing = p.get("last_shot_wing")
            etxt = f"{_name(match, eb) if eb else '?'} {(wing.upper() + ' ') if wing else ''}{etxt}"
        from .analytics import point_time
        t0 = point_time(p)
        # start the video a few seconds before the serve, so the toss is seen
        tseek = max(0.0, float(t0) - 3.0) if t0 is not None else None
        rv = p.get("review") or {}
        filled = _filled(p)
        prev_filled = bool(rows) and rows[-1]["filled"]
        rows.append({
            "id": p.get("id"), "set_no": p.get("set_no"),
            "games": "-".join(str(g) for g in sb.get("games", [])),
            "points": sb.get("points", ""), "in_tb": sb.get("in_tb"),
            "server": p.get("server"), "side": p.get("side"), "tags": tags,
            "s1": _serve_cell(s1), "s2": _serve_cell(s2),
            "ret": (p.get("return_wing") or "").upper(),
            "rally": p.get("rally_len") if p.get("rally_len") is not None else "",
            "ending": etxt, "winner": p.get("winner") or "?",
            "winner_src": WINNER_SOURCE_TXT.get(p.get("winner_source") or "unknown", p.get("winner_source")),
            "mcp": p.get("mcp") or "",
            "gap": bool(p.get("gap_before")) and not filled, "filled": filled,
            "fill_start": filled and not prev_filled, "fill_run": 0, "t": tseek, "t_label": _fmt_t(t0),
            "href": f"{video_href}#t={tseek:.1f}" if (video_href and tseek is not None) else None,
            "low": {"winner": _is_low(p, "winner"), "serves": _is_low(p, "serves") or _is_low(p, "serve_zone"),
                    "ret": _is_low(p, "return_wing"), "rally": _is_low(p, "rally_len"),
                    "ending": _is_low(p, "ending")},
            "reviewed": rv.get("status") == "reviewed", "edited": bool(rv.get("edited_fields")),
            "checked": bool(rv.get("checked_fields")), "added": rv.get("status") == "added",
        })
    for i, r in enumerate(rows):          # length of each run of skipped points, on its first row
        if r["fill_start"]:
            j = i
            while j < len(rows) and rows[j]["filled"]:
                j += 1
            r["fill_run"] = j - i
    return rows


def quality_rows(stats: dict) -> list:
    labels = {"winner": "Point winner", "serves": "Serve in/fault", "serve_zone": "Serve zone",
              "return_wing": "Return wing", "rally_len": "Rally length", "ending": "Point ending",
              "last_shot_wing": "Last-shot wing", "net_approaches": "Net approaches",
              "player_dist_m": "Distance run"}
    out = []
    for f, c in (stats.get("coverage") or {}).items():
        out.append({"field": labels.get(f, f), "known": c["known"], "total": c["total"],
                    "pct": c["pct"], "mean_conf": c["mean_conf"], "low": c["low_conf"],
                    "warn": (c["pct"] or 0) < 50 or (c["mean_conf"] is not None and c["mean_conf"] < LOW_CONF)})
    return out


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
CSV_COLS = ["id", "t_start", "t_end", "set_no", "sets", "games", "points", "in_tb", "server", "receiver",
            "side", "break_point", "game_point", "set_point", "match_point", "winner", "winner_source",
            "serve1_result", "serve1_zone", "serve1_fault", "serve1_bounce_x", "serve1_bounce_y",
            "serve1_speed_kmh", "serve2_result", "serve2_zone", "serve2_fault", "serve2_bounce_x",
            "serve2_bounce_y", "serve2_speed_kmh", "serve_number_won_on", "return_wing", "rally_len",
            "ending", "ending_by", "last_shot_wing", "last_shot_stroke", "last_shot_result",
            "net_A", "net_B", "dist_A_m", "dist_B_m", "gap_before", "review_status", "edited_fields",
            "checked_fields", "mcp", "conf_min", "conf_json"]


def flatten_point(p: dict) -> dict:
    sb = p.get("score_before") or {}
    fl = p.get("flags") or {}
    row = {"id": p.get("id"), "t_start": p.get("t_start"), "t_end": p.get("t_end"),
           "set_no": p.get("set_no"),
           "sets": " ".join(f"{a}-{b}" for a, b in sb.get("sets", [])),
           "games": "-".join(str(g) for g in sb.get("games", [])), "points": sb.get("points"),
           "in_tb": sb.get("in_tb"), "server": p.get("server"), "receiver": p.get("receiver"),
           "side": p.get("side"), "winner": p.get("winner"), "winner_source": p.get("winner_source")}
    for k in ("break_point", "game_point", "set_point", "match_point"):
        row[k] = bool(fl.get(k))
    for n in (1, 2):
        s = next((x for x in (p.get("serves") or []) if x.get("n") == n), None) or {}
        b = s.get("bounce_xy") or [None, None]
        row[f"serve{n}_result"] = s.get("result")
        row[f"serve{n}_zone"] = s.get("zone")
        row[f"serve{n}_fault"] = s.get("fault")
        row[f"serve{n}_bounce_x"] = b[0] if len(b) > 0 else None
        row[f"serve{n}_bounce_y"] = b[1] if len(b) > 1 else None
        row[f"serve{n}_speed_kmh"] = s.get("speed_kmh")
    for k in ("serve_number_won_on", "return_wing", "rally_len", "ending", "ending_by", "last_shot_wing",
              "last_shot_stroke", "last_shot_result", "gap_before", "mcp"):
        row[k] = p.get(k)
    na, dm = p.get("net_approaches") or {}, p.get("player_dist_m") or {}
    row.update({"net_A": na.get("A"), "net_B": na.get("B"), "dist_A_m": dm.get("A"), "dist_B_m": dm.get("B")})
    rv = p.get("review") or {}
    row["review_status"] = rv.get("status")
    row["edited_fields"] = ";".join(rv.get("edited_fields") or [])
    row["checked_fields"] = ";".join(rv.get("checked_fields") or [])
    confs = [v for v in (p.get("conf") or {}).values() if isinstance(v, (int, float))]
    row["conf_min"] = round(min(confs), 3) if confs else None
    row["conf_json"] = json.dumps(p.get("conf") or {}, sort_keys=True)
    return row


def write_csv(points: list, path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for p in points:
            w.writerow({k: ("" if v is None else v) for k, v in flatten_point(p).items()})


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
def _video_href(match: dict, out_dir: Path) -> Optional[str]:
    from .pipeline import local_video_path
    v = (match.get("meta") or {}).get("video")
    if not v:
        return None
    vp = Path(v)
    if not vp.is_absolute():
        # the full path analyze saved in cache/ first (match.json only names the file)
        cand = [local_video_path(out_dir), Path.cwd() / vp, out_dir.parent.parent / vp, out_dir / vp]
        found = next((c for c in cand if c is not None and c.exists()), None)
        if found is None:
            return "../../" + str(v).replace(os.sep, "/")
        vp = found
    try:
        rel = os.path.relpath(vp.resolve(), out_dir.resolve())
        return rel.replace(os.sep, "/")
    except ValueError:
        return vp.resolve().as_uri()


def _fmt_desc(fmt: dict) -> str:
    if not fmt:
        return ""
    s = f"Best of {fmt.get('best_of', 3)}"
    s += ", no-ad" if fmt.get("no_ad") else ", advantage"
    fs = fmt.get("final_set", "tb")
    s += {"tb": ", tiebreak in final set", "match_tb10": ", match tiebreak (10) for final set",
          "adv": ", advantage final set"}.get(fs, "")
    return s


def _bare_text(t: str) -> str:
    """Insight text without the sample/confidence suffixes; the report shows those
    on their own line."""
    for suf in (" (unverified: low tracking confidence)", " (small sample)"):
        t = t.replace(suf, "")
    return t


def build_context(match: dict, out_dir: Path) -> dict:
    meta = match.get("meta") or {}
    stats = match["stats"]
    players = meta.get("players") or {}
    href = _video_href(match, out_dir)
    ins = match.get("insights") or []
    recs = match.get("recommendations") or []
    winners_src = {}
    for p in match.get("points") or []:
        k = WINNER_SOURCE_TXT.get(p.get("winner_source") or "unknown", p.get("winner_source"))
        winners_src[k] = winners_src.get(k, 0) + 1
    vi = meta.get("video_info") or {}
    names = {P: _name(match, P) for P in PLAYERS}
    return {
        "meta": meta, "names": names,
        "hands": {P: (players.get(P) or {}).get("hand") or "" for P in PLAYERS},
        # outputs made before hand_given existed never had a hand entered either
        "hands_given": {P: bool((players.get(P) or {}).get("hand_given")) for P in PLAYERS},
        "chance_words": chance_words,
        "score": _score_line(match), "format_desc": _fmt_desc(meta.get("format") or {}),
        "video_name": os.path.basename(str(meta.get("video") or "")), "video_href": href,
        "duration": _fmt_t(vi.get("duration")) if vi.get("duration") else "",
        "stats": stats, "key_rows": key_stat_rows(stats, match.get("points") or []),
        "insights": {P: [dict(i, text=_bare_text(i["text"])) for i in ins if i["player"] == P] for P in PLAYERS},
        "recs": {P: [r for r in recs if r["for"] == P] for P in PLAYERS},
        "serve": serve_courts(match, stats), "momentum_svg": svg_momentum(match),
        "wing_rows": wing_rows(stats), "rally_rows": rally_rows(stats),
        "rows": point_rows(match, href), "quality": quality_rows(stats),
        "winner_sources": winners_src, "low_conf": LOW_CONF,
        "n_points": len(match.get("points") or []),
        "habits": _habits(match),
        "cards": player_cards(ins, names, players), "maps": serve_maps(stats, names),
        "h2h": h2h_rows(stats), "big": big_point_rows(match.get("serve_patterns")
                                                      or _pattern_data(match)),
    }


def _pattern_data(match: dict) -> dict:
    """match["serve_patterns"], computed from the points when it is missing."""
    from . import patterns
    from .analytics import _fmt_from_meta
    sp = match.get("serve_patterns")
    if not sp:
        meta = match.get("meta") or {}
        sp = patterns.compute(match.get("points") or [], meta.get("players"), _fmt_from_meta(meta))
    return sp


def _habits(match: dict) -> dict:
    """Serve habits rows for the report (patterns.view of match["serve_patterns"])."""
    from . import patterns
    sp = _pattern_data(match)
    return {"small": sp["small"], "players": patterns.view(sp)}


def render(match: dict, out_dir) -> dict:
    """Write report.html, points.csv and match.json into out_dir.
    Returns {"html": path, "csv": path, "json": path}."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from markupsafe import Markup
    from .analytics import refresh

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    m = json.loads(json.dumps(match, default=_json_default))   # plain JSON types, deep copy
    from .pipeline import public_meta
    m["meta"] = public_meta(m.get("meta") or {})      # no full paths of this computer
    m.setdefault("segments", [])
    m.setdefault("points", [])
    refresh(m)

    paths = {"html": str(out / "report.html"), "csv": str(out / "points.csv"),
             "json": str(out / "match.json")}
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, default=_json_default)
    write_csv(m["points"], paths["csv"])

    env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["safe_svg"] = lambda s: Markup(s)
    ctx = build_context(m, out)
    html_text = env.get_template("report.html.j2").render(**ctx)
    with open(paths["html"], "w", encoding="utf-8") as f:
        f.write(html_text)
    return paths
