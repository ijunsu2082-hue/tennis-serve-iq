"""Serve habits per player: where the serve landed, by side, serve number and
score situation, plus first serve in and double faults.

Works on the PointRecord dicts of any match (``match["points"]``). Nothing here
knows a video's names, positions, colors or times.

Words used in the report
  T        the serve landed near the middle line (the inner third of the box)
  body     the middle third of the box, where the receiver usually stands
  wide     near the side line (the outer third of the box)
  unknown  the serve went in, but where it landed was not found

Rules
- Serve number is the serve record's ``n`` (1 or 2). A point can also carry two
  optional lists (the eye check writes them): ``lets`` ([{t, n}], a serve that
  touched the net and was taken again; counted apart, never as in or fault) and
  ``serves_number_unclear`` ([{t, result, zone}], serves whose number could not be
  told; their own group, "number_unknown").
- Direction counts only for serves that went in (result "in"). A zone on a fault
  or on a serve whose result is unknown is not used.
- Side is the point's ``side`` (deuce or ad); anything else is "side_unknown".
  "both" adds up every side.
- Situations come from the score before the point (server's score first) and the
  point's flags. Break point (against the server), game point (for the server)
  and the score groups (40-0, 0-40, 30-30, deuce, advantage either way, first
  point of a game) are regular games only; tiebreak points are their own group.
  A point whose score is not known is only in "all".
- A direction share is the count over direction_seen (T + body + wide), kept in
  the group with its percent (``pct``, one decimal, None when nothing was seen).
  First serve in and double faults are analytics.rate dicts (k, n, pct, lo, hi).
  A share over fewer than SMALL (10) serves is marked too_small: too few serves to
  call it a habit.
- Checked and automatic values are never mixed. A serve is "checked" when its
  point was marked reviewed, or when the review touched this serve and every
  known value used from it (in or fault, and the direction when it went in and
  the direction is known) was set or confirmed in the review
  (review.edited_fields or review.checked_fields). Every other serve is "auto".
  An unknown value needs no check: "the check could not tell" stays unknown.
  Each source has its own counts and shares.
- Stored form (match["serve_patterns"]): players -> P -> sources -> "checked" or
  "auto" (only a source that has serves or lets) -> groups -> situation -> serve
  ("1", "2", "number_unknown") -> side -> counts. Groups with no serve and no let
  are left out; group() returns an empty group for them.
- Double faults: a second serve that was a fault. When the point's winner is the
  server, the two faults cannot both be right, so that serve is counted under
  "score_disagrees" instead (the same guard as analytics.point_attrs).
"""
from __future__ import annotations

from typing import Optional

from .analytics import PLAYERS, SIDES, SMALL_N, ZONES, _name, rate

SMALL = SMALL_N
DIRS = ("T", "body", "wide")                      # report order
SLOTS = ("1", "2", "number_unknown")
SIDE_KEYS = ("deuce", "ad", "side_unknown", "both")
SOURCES = ("checked", "auto")
COUNT_KEYS = ("lets", "in", "fault", "result_unknown", "T", "body", "wide", "direction_unknown")

SITUATIONS = (
    ("all", "All points"),
    ("break_point", "Break point against the server"),
    ("game_point", "Game point for the server"),
    ("40-0", "40-0, server ahead"),
    ("0-40", "0-40, server behind"),
    ("30-30", "30-30"),
    ("deuce", "Deuce (40-40)"),
    ("adv_server", "Advantage server"),
    ("adv_receiver", "Advantage receiver"),
    ("tiebreak", "Tiebreak points"),
    ("first_point", "First point of a game"),
)
SIT_WORDS = dict(SITUATIONS)
# server's score first -> situation key (regular games only)
_SCORE_SITS = {"40-0": "40-0", "0-40": "0-40", "30-30": "30-30", "40-40": "deuce",
               "AD-40": "adv_server", "40-AD": "adv_receiver", "0-0": "first_point"}
_AD = {"A": "AD", "ADV": "AD", "AV": "AD"}


# ---------------------------------------------------------------------------
# per point
# ---------------------------------------------------------------------------
def server_view(score_before: dict, server: str) -> Optional[str]:
    """Points score before the point with the server's score first ("40-15"),
    or None when it cannot be read."""
    lab = (score_before or {}).get("points")
    if server not in PLAYERS or lab is None:
        return None
    parts = [x.strip().upper() for x in str(lab).split("-")]
    if len(parts) != 2 or not all(parts):
        return None
    a, b = parts if server == "A" else parts[::-1]
    return "%s-%s" % (_AD.get(a, a), _AD.get(b, b))


def _flags_from_score(sb: dict, server: str, fmt) -> Optional[dict]:
    from . import scoring as sc
    try:
        return sc.flags(sc.state_from_score_dict(sb, server, fmt, ends=sb.get("ends")), fmt)
    except (KeyError, TypeError, ValueError):
        return None


def situations(p: dict, fmt=None) -> Optional[list]:
    """Situation keys for a point ("all" first), or None when its score is not known."""
    sb = p.get("score_before") or {}
    server = p.get("server")
    view = server_view(sb, server)
    if view is None:
        return None
    out = ["all"]
    if sb.get("in_tb"):
        out.append("tiebreak")
        return out
    fl = p.get("flags")
    if not isinstance(fl, dict):
        fl = _flags_from_score(sb, server, fmt) or {}
    if fl.get("break_point"):
        out.append("break_point")
    if fl.get("game_point"):
        out.append("game_point")
    k = _SCORE_SITS.get(view)
    if k:
        out.append(k)
    return out


def _review(p: dict):
    rv = p.get("review") or {}
    keys = set(rv.get("edited_fields") or []) | set(rv.get("checked_fields") or [])
    return rv.get("status") == "reviewed", keys


def serve_source(p: dict, s: dict) -> str:
    """"checked" or "auto" for one serve record (see the module docstring)."""
    reviewed, ck = _review(p)
    if reviewed:
        return "checked"
    n = s.get("n")
    res = s.get("result")
    sn = p.get("serve_number_won_on")
    res_ck = (("serve%s_result" % n) in ck
              or ("serve_number_won_on" in ck and sn in (1, 2) and n in (1, 2) and n <= sn)
              or ("ending" in ck and p.get("ending") == "double_fault" and n in (1, 2)))
    zone_ck = ("serve%s_zone" % n) in ck or ("serve_zone" in ck and n == (sn or 1))
    touched = res_ck or zone_ck or any(k.startswith("serve%s_" % n) for k in ck)
    if not touched:
        return "auto"
    if res in ("in", "fault") and not res_ck:
        return "auto"
    if res == "in" and s.get("zone") in ZONES and not zone_ck:
        return "auto"
    return "checked"


def _list_source(p: dict, key: str) -> str:
    reviewed, ck = _review(p)
    return "checked" if reviewed or key in ck else "auto"


def point_serves(p: dict) -> list:
    """(source, slot, result, zone) for every serve of a point, lets included.
    result is "in", "fault", "unknown" or "let"; zone only when result is "in"."""
    out = []
    for s in p.get("serves") or []:
        if not isinstance(s, dict):
            continue
        n = s.get("n")
        res = s.get("result") if s.get("result") in ("in", "fault") else "unknown"
        zone = s.get("zone") if res == "in" and s.get("zone") in ZONES else None
        out.append((serve_source(p, s), str(n) if n in (1, 2) else "number_unknown", res, zone))
    for x in p.get("lets") or []:
        n = x.get("n") if isinstance(x, dict) else None
        out.append((_list_source(p, "lets"), str(n) if n in (1, 2) else "number_unknown", "let", None))
    for x in p.get("serves_number_unclear") or []:
        if not isinstance(x, dict):
            continue
        res = x.get("result") if x.get("result") in ("in", "fault") else "unknown"
        zone = x.get("zone") if res == "in" and x.get("zone") in ZONES else None
        out.append((_list_source(p, "serves_number_unclear"), "number_unknown", res, zone))
    return out


# ---------------------------------------------------------------------------
# counting
# ---------------------------------------------------------------------------
def _new_group() -> dict:
    return {k: 0 for k in COUNT_KEYS}


def _add(g: dict, res: str, zone: Optional[str]) -> None:
    if res == "let":
        g["lets"] += 1
    elif res == "in":
        g["in"] += 1
        g[zone if zone in DIRS else "direction_unknown"] += 1
    elif res == "fault":
        g["fault"] += 1
    else:
        g["result_unknown"] += 1


def _finish(g: dict) -> dict:
    g["serves"] = g["in"] + g["fault"] + g["result_unknown"]
    g["direction_seen"] = g["T"] + g["body"] + g["wide"]
    g["too_small"] = g["direction_seen"] < SMALL
    n = g["direction_seen"]
    g["pct"] = {d: (round(100.0 * g[d] / n, 1) if n else None) for d in DIRS}
    return g


def group(sp: dict, P: str, src: str, sit: str, slot: str, side: str) -> dict:
    """One group's counts; an empty group when nothing was stored for it."""
    g = ((((sp["players"][P]["sources"].get(src) or {}).get("groups") or {}).get(sit) or {})
         .get(slot) or {}).get(side)
    return g if g is not None else _finish(_new_group())


def _new_source() -> dict:
    return {"serves": 0, "lets": 0,
            "groups": {k: {sl: {sd: _new_group() for sd in SIDE_KEYS} for sl in SLOTS}
                       for k, _ in SITUATIONS},
            "_df": 0, "_df_disagree": 0}


def compute(points: list, players: Optional[dict] = None, fmt=None) -> dict:
    """Serve habits for every player. See the module docstring for the rules."""
    out = {"small": SMALL, "situations": [list(x) for x in SITUATIONS],
           "points_server_unknown": 0, "players": {}}
    for P in PLAYERS:
        out["players"][P] = {
            "name": _name(players, P), "points_served": 0, "points_score_unknown": 0,
            "points_without_serve": 0,
            "situation_points": {k: {sd: 0 for sd in SIDE_KEYS} for k, _ in SITUATIONS},
            "sources": {src: _new_source() for src in SOURCES},
        }
    for p in points or []:
        P = p.get("server")
        if P not in PLAYERS:
            out["points_server_unknown"] += 1
            continue
        st = out["players"][P]
        st["points_served"] += 1
        side = p.get("side") if p.get("side") in SIDES else "side_unknown"
        sits = situations(p, fmt)
        if sits is None:
            st["points_score_unknown"] += 1
            sits = ["all"]
        for k in sits:
            st["situation_points"][k][side] += 1
            st["situation_points"][k]["both"] += 1
        items = point_serves(p)
        if not any(res != "let" for _, _, res, _ in items):
            st["points_without_serve"] += 1
        for src, slot, res, zone in items:
            S = st["sources"][src]
            S["lets" if res == "let" else "serves"] += 1
            for k in sits:
                _add(S["groups"][k][slot][side], res, zone)
                _add(S["groups"][k][slot]["both"], res, zone)
            if slot == "2" and res == "fault":
                S["_df_disagree" if p.get("winner") == P else "_df"] += 1
    for P in PLAYERS:
        for src, S in out["players"][P]["sources"].items():
            for k in S["groups"]:
                for sl in SLOTS:
                    for sd in SIDE_KEYS:
                        _finish(S["groups"][k][sl][sd])
            g1 = S["groups"]["all"]["1"]["both"]
            n1 = g1["in"] + g1["fault"]
            S["first_serve_in"] = {"in": g1["in"], "fault": g1["fault"], "unknown": g1["result_unknown"],
                                   "rate": rate(g1["in"], n1), "too_small": n1 < SMALL}
            g2 = S["groups"]["all"]["2"]["both"]
            n2 = g2["in"] + g2["fault"]
            df = S.pop("_df")
            S["double_faults"] = {"count": df, "score_disagrees": S.pop("_df_disagree"),
                                  "second_serves_known": n2, "second_serves_unknown": g2["result_unknown"],
                                  "rate": rate(df, n2), "too_small": n2 < SMALL}
        _prune(out["players"][P]["sources"])
    return out


def _prune(sources: dict) -> None:
    """Drop sources with no serve and no let, and groups with no serve and no let."""
    for src in list(sources):
        S = sources[src]
        if not (S["serves"] or S["lets"]):
            del sources[src]
            continue
        for k in list(S["groups"]):
            for sl in list(S["groups"][k]):
                for sd in list(S["groups"][k][sl]):
                    g = S["groups"][k][sl][sd]
                    if not (g["serves"] or g["lets"]):
                        del S["groups"][k][sl][sd]
                if not S["groups"][k][sl]:
                    del S["groups"][k][sl]
            if not S["groups"][k]:
                del S["groups"][k]


# ---------------------------------------------------------------------------
# report view
# ---------------------------------------------------------------------------
def pct_text(k: int, n: int) -> str:
    """'7 of 11 (64%)', rounding halves up; '0 of 0' when there is nothing to share."""
    if not n:
        return "0 of 0"
    return "%d of %d (%d%%)" % (k, n, (200 * k + n) // (2 * n))


SIDE_WORDS = {"deuce": "deuce side", "ad": "ad side", "side_unknown": "side unknown", "both": "both sides"}
SOURCE_WORDS = {"checked": "Checked values (set or confirmed in a review)",
                "auto": "Automatic values (found by the computer, not checked)"}
SERVE_WORDS = {"1": "First serves", "2": "Second serves"}


def _row(sit: str, sd: str, pts: int, g: dict) -> dict:
    return {"situation": SIT_WORDS[sit], "side": SIDE_WORDS[sd], "points": pts,
            "seen": g["direction_seen"], **{d: pct_text(g[d], g["direction_seen"]) for d in DIRS},
            "dir_unknown": g["direction_unknown"], "res_unknown": g["result_unknown"],
            "too_small": g["too_small"]}


def _sides_for(sp: dict) -> list:
    """Side rows to show for one situation: the sides that had points, plus both
    when more than one did."""
    sides = [sd for sd in ("deuce", "ad", "side_unknown") if sp[sd]]
    return sides + ["both"] if len(sides) > 1 else (sides or ["both"])


def _most_often(g: dict) -> str:
    order = sorted(DIRS, key=lambda d: (-g[d], DIRS.index(d)))
    return ", ".join("%s %s" % (d, pct_text(g[d], g["direction_seen"])) for d in order)


def _n(k: int, one: str, many: str) -> str:
    return "%d %s" % (k, one if k == 1 else many)


def _more(k: int, one: str, many: str) -> str:
    return " On %d more %s, in or out is unknown." % (k, one if k == 1 else many) if k else ""


SMALL_WORDS = ", too small to call a habit"


def _source_lines(sp: dict, P: str, src: str) -> list:
    """Plain sentences for one player and source."""
    S = sp["players"][P]["sources"][src]
    fs, df = S["first_serve_in"], S["double_faults"]
    out = ["First serve in: %s%s.%s" % (pct_text(fs["in"], fs["in"] + fs["fault"]),
                                        SMALL_WORDS if fs["too_small"] else "",
                                        _more(fs["unknown"], "first serve", "first serves")),
           "Double faults: %s second serves whose result is known%s.%s" % (
               pct_text(df["count"], df["second_serves_known"]), SMALL_WORDS if df["too_small"] else "",
               _more(df["second_serves_unknown"], "second serve", "second serves"))]
    if df["score_disagrees"]:
        out[-1] += (" On %s two faults were read but the server won the point, so %s not counted as a "
                    "double fault." % (_n(df["score_disagrees"], "more point", "more points"),
                                       "it is" if df["score_disagrees"] == 1 else "they are"))
    for sl in ("1", "2"):
        for sd in ("deuce", "ad"):
            g = group(sp, P, src, "all", sl, sd)
            if g["too_small"]:
                continue
            out.append("%s, %s: %s.%s" % (
                SERVE_WORDS[sl], SIDE_WORDS[sd], _most_often(g),
                (" %d more went in with the landing spot unknown." % g["direction_unknown"])
                if g["direction_unknown"] else ""))
    if len(out) == 2:
        out.append("No side has %d or more serves with a known landing spot yet, so no direction is "
                   "shown here; the tables below have the counts." % SMALL)
    nu = group(sp, P, src, "all", "number_unknown", "both")
    if nu["serves"]:
        line = ("%s could not be marked as a first or second serve, so %s not in the rows below." % (
            _n(nu["serves"], "serve", "serves"), "it is" if nu["serves"] == 1 else "they are"))
        if nu["in"]:
            line += " %d of them went in: " % nu["in"]
            parts = []
            if nu["direction_seen"]:
                parts.append("%d with a known landing spot (%s)" % (
                    nu["direction_seen"], ", ".join("%s %d" % (d, nu[d]) for d in DIRS)))
            if nu["direction_unknown"]:
                parts.append("%d with the landing spot unknown" % nu["direction_unknown"])
            line += " and ".join(parts) + "."
        out.append(line)
    if S["lets"]:
        out.append("%s (a serve that touched the net and was taken again) %s not counted as in or fault." % (
            _n(S["lets"], "let", "lets"), "is" if S["lets"] == 1 else "are"))
    return out


def view(sp: dict) -> dict:
    """Sentences and table rows for report.html, per player and source."""
    out = {}
    for P in PLAYERS:
        st = sp["players"][P]
        note = "Points served: %d." % st["points_served"]
        if st["points_without_serve"]:
            note += " No serve was found on %d of them." % st["points_without_serve"]
        if st["points_score_unknown"]:
            note += (" The score is not known on %d of them, so they count only under all points."
                     % st["points_score_unknown"])
        srcs = []
        for src in SOURCES:
            if src not in st["sources"]:
                continue
            tables = []
            for sl in ("1", "2"):
                rows = []
                for k, _ in SITUATIONS:
                    for sd in _sides_for(st["situation_points"][k]):
                        rows.append(_row(k, sd, st["situation_points"][k][sd], group(sp, P, src, k, sl, sd)))
                tables.append({"title": SERVE_WORDS[sl], "rows": rows})
            srcs.append({"key": src, "title": SOURCE_WORDS[src], "lines": _source_lines(sp, P, src),
                         "tables": tables})
        out[P] = {"note": note, "sources": srcs}
    return out
