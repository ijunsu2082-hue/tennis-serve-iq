"""Scouting statistics, insights, tactical recommendations and manual
corrections, computed from PointRecord dicts (see contracts.PointRecord).

Missing data policy: every rate is ``{"k", "n", "pct", "lo", "hi"}``, where
``n`` counts only points where all needed fields are known (not None and
not "unknown"). ``pct`` is None when ``n == 0``, and ``lo``/``hi`` give the
95% Wilson interval in percent. ``stats["coverage"]`` reports, per field,
how many points have it known and the mean confidence.

Field confidence: ``point["conf"][key]`` (0..1). A field counts as fully
confident (1.0) if the point was reviewed (``review.status == "reviewed"``)
or the field is in ``review.edited_fields``. If the conf is missing, the
field is excluded from conf averages.

Serve conventions: ``serve_number_won_on`` is read as "the serve number the
point was played on" (1 = first serve in, 2 = second serve in, None =
double fault or unknown). The serve zone of a point is the zone of the serve
that went in.

Corrections format (``corrections.json``), keyed by point id (string or int):
    {"12": {"winner": "B", "serve_number_won_on": 2, "serve_zone": "T",
            "serve1_result": "fault", "serve1_fault": "net", "serve1_zone": "wide",
            "serve2_result": "in", "serve2_zone": "T",
            "return_wing": "bh", "rally_len": 5, "ending": "unforced_error",
            "ending_by": "A", "last_shot_wing": "fh", "last_shot_stroke": "groundstroke",
            "reviewed": true}}
Any other key is set on the point as-is (normally a PointRecord field; the eye check also
writes "lets" and "serves_number_unclear", lists the stats do not use).
More serve keys: "serve1_t", "serve1_bounce_xy", "serve1_speed_kmh" (same for serve 2)
set those parts of the serve; "serve2": null removes that serve's record.
"checked": [field, ...] marks fields whose value a check confirmed without changing
it; they go to review.checked_fields with conf 1.0 (edits go to review.edited_fields).
A key with "add": true and an id that no point has adds a point the pipeline missed;
it needs "t_end", "score_before" (A-B order) and "server", and its receiver, side,
flags and set number come from that score (review.status "added").
A top-level "_meta" object is copied to meta.review (for example where the checked
values came from). When it has "points_key" (see points_key) and the points differ
from the ones it was made for, nothing is applied and meta.corrections_stale says so.
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict
from typing import Optional

from .contracts import MatchFormat, PointRecord, other

PLAYERS = ("A", "B")
ZONES = ("wide", "body", "T")
SIDES = ("deuce", "ad")
WINGS = ("fh", "bh")
ENDINGS = ("ace", "service_winner", "double_fault", "winner", "unforced_error",
           "forced_error", "unknown")
FAULTS = ("net", "long", "wide", "long_wide")
RALLY_BUCKETS = (("1-4", 1, 4), ("5-8", 5, 8), ("9+", 9, 10 ** 6))
MIN_N = 5
SMALL_N = 10
EFFECT_PP = 15.0
CONF_MIN = 0.6

# conf-dict keys to look for, per logical field (first hit wins)
CONF_KEYS = {
    "winner": ("winner",),
    "serves": ("serves", "serve", "serve_result"),
    "serve_zone": ("serve_zone", "zone", "serves"),
    "return_wing": ("return_wing",),
    "rally_len": ("rally_len",),
    "ending": ("ending",),
    "last_shot_wing": ("last_shot_wing", "ending"),
    "net_approaches": ("net_approaches",),
    "player_dist_m": ("player_dist_m",),
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def known(v) -> bool:
    return v is not None and v != "unknown" and v != ""


def wilson(k: int, n: int, z: float = 1.96):
    if n <= 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h) * 100, min(1.0, c + h) * 100)


def _hyper(k: int, n1: int, n2: int, m: int) -> float:
    return math.comb(n1, k) * math.comb(n2, m - k) / math.comb(n1 + n2, m)


def fisher_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided Fisher exact test of k1 of n1 against k2 of n2: the chance of a split
    at least this uneven if both groups had the same real rate."""
    if not (0 <= k1 <= n1 and 0 <= k2 <= n2):
        raise ValueError(f"bad counts {k1} of {n1}, {k2} of {n2}")
    if n1 <= 0 or n2 <= 0:
        return 1.0
    m = k1 + k2
    obs = _hyper(k1, n1, n2, m)
    lo, hi = max(0, m - n2), min(n1, m)
    p = sum(q for q in (_hyper(k, n1, n2, m) for k in range(lo, hi + 1)) if q <= obs * (1 + 1e-9))
    return min(1.0, p)


def binom_p(k: int, n: int, p0: float = 0.5) -> float:
    """Two-sided exact binomial test of k of n against a rate p0."""
    if not 0 <= k <= n:
        raise ValueError(f"bad count {k} of {n}")
    if n <= 0:
        return 1.0
    pr = [math.comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(n + 1)]
    obs = pr[k]
    return min(1.0, sum(q for q in pr if q <= obs * (1 + 1e-9)))


# How sure a scouting note is, from the chance test of its gap (chance_p):
#   high   chance would give a gap this big less than 1 time in 100
#   medium less than 1 time in 20
#   low    more often than that, or under SMALL_N points, or low tracking confidence
P_HIGH, P_MEDIUM = 0.01, 0.05


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "pct": (round(100.0 * k / n, 1) if n else None),
            "lo": (round(lo, 1) if lo is not None else None),
            "hi": (round(hi, 1) if hi is not None else None)}


def _reviewed(p: dict, field: str) -> bool:
    rv = p.get("review") or {}
    return rv.get("status") == "reviewed" or field in (rv.get("edited_fields") or [])


def field_conf(p: dict, field: str) -> Optional[float]:
    """Confidence of a logical field on a point (1.0 when reviewed/edited)."""
    if _reviewed(p, field) or (field == "serve_zone" and _reviewed(p, "serves")):
        return 1.0
    c = p.get("conf") or {}
    for key in CONF_KEYS.get(field, (field,)):
        v = c.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    if field in ("serves", "serve_zone"):
        vals = [s.get("conf") for s in (p.get("serves") or []) if isinstance(s.get("conf"), (int, float))]
        if vals:
            return float(min(vals))
    if field == "winner" and p.get("winner_source") in ("ocr", "manual"):
        return 0.98 if p.get("winner_source") == "ocr" else 1.0
    return None


def field_checked(p: dict, field: str) -> bool:
    """True when the value of a logical field on a point was set or confirmed in a
    review: the point is fully reviewed, or the field is in review.edited_fields or
    review.checked_fields. "serves" counts as checked when every serve record passes
    patterns.serve_source (or, with no records, when the serve the point was won on
    or a double fault was checked); "serve_zone" when the zone of the serve the point
    was played on was."""
    rv = p.get("review") or {}
    if rv.get("status") == "reviewed":
        return True
    ck = set(rv.get("edited_fields") or []) | set(rv.get("checked_fields") or [])
    if field in ck:
        return True
    if field == "serves":
        from .patterns import serve_source
        recs = [s for s in (p.get("serves") or []) if isinstance(s, dict)]
        if recs:
            return all(serve_source(p, s) == "checked" for s in recs)
        return "serve_number_won_on" in ck or ("ending" in ck and p.get("ending") == "double_fault")
    if field == "serve_zone":
        sn = p.get("serve_number_won_on") or 1
        return ("serve%s_zone" % sn) in ck
    return False


def checked_status(points: list, fields: tuple) -> dict:
    """How many of these points have every one of these fields checked in a review:
    {"k": checked points, "n": points, "status": "all" | "some" | "none"}."""
    n = len(points)
    k = sum(1 for p in points if p and all(field_checked(p, f) for f in fields))
    status = "all" if n and k == n else ("some" if k else "none")
    return {"k": k, "n": n, "status": status}


def score_filled(p: dict) -> bool:
    """A point the score board skipped: the engine worked out who won it from the
    next reading (gap_before set on the point itself, no video window of its own).
    Such a point is not missing; its winner just was not seen on the board."""
    return bool(p.get("gap_before")) and p.get("t_start") is None


def point_time(p: dict):
    """When the point starts in the video, in seconds: its first serve (lets and
    serves of unknown number included), else the start of its video window, else the
    time its score change was seen. The report and the chat use the same rule."""
    ts = [s.get("t") for s in (p.get("serves") or []) if isinstance(s, dict) and isinstance(s.get("t"), (int, float))]
    ts += [x.get("t") for k in ("lets", "serves_number_unclear") for x in (p.get(k) or [])
           if isinstance(x, dict) and isinstance(x.get("t"), (int, float))]
    if ts:
        return min(ts)
    if isinstance(p.get("t_start"), (int, float)):
        return p["t_start"]
    return p.get("t_end")


def _name(players: dict, p: str) -> str:
    try:
        return (players or {}).get(p, {}).get("name") or p
    except AttributeError:
        return p


def _hand(players: dict, p: str) -> str:
    h = str(((players or {}).get(p) or {}).get("hand") or "R").strip().upper()
    return "L" if h.startswith("L") else "R"


def _pressure(fl: dict) -> bool:
    fl = fl or {}
    return bool(fl.get("break_point") or fl.get("set_point") or fl.get("match_point"))


# ---------------------------------------------------------------------------
# per-point normalisation
# ---------------------------------------------------------------------------
def point_attrs(p: dict) -> dict:
    """Normalised, derived view of one PointRecord dict (unknown -> None)."""
    server = p.get("server")
    recv = p.get("receiver") or (other(server) if server in PLAYERS else None)
    winner = p.get("winner") if p.get("winner") in PLAYERS else None
    ending = p.get("ending") if known(p.get("ending")) else None
    serves = sorted([s for s in (p.get("serves") or []) if isinstance(s, dict)],
                    key=lambda s: s.get("n", 0))
    s1 = next((s for s in serves if s.get("n") == 1), None)
    s2 = next((s for s in serves if s.get("n") == 2), None)

    def res(s):
        if not s:
            return None
        r = s.get("result")
        return r if r in ("in", "fault") else None

    played_on = p.get("serve_number_won_on")
    played_on = played_on if played_on in (1, 2) else None
    df = None
    if ending == "double_fault":
        df, played_on = True, None
    first_in = second_in = None
    if played_on == 1:
        first_in = True
    elif played_on == 2:
        first_in, second_in = False, True
    elif df:
        first_in, second_in = False, False
    else:
        r1, r2 = res(s1), res(s2)
        if r1 == "in":
            first_in, played_on = True, 1
        elif r1 == "fault":
            first_in = False
            if r2 == "in":
                second_in, played_on = True, 2
            elif r2 == "fault" and not (winner and winner == server):
                # two faults lose the point; when the score board says the server
                # won it, one of the two reads is wrong: leave both unknown
                second_in, df = False, True
        elif ending in ("ace", "service_winner") and not s1:
            pass
    if df is None and played_on in (1, 2):
        df = False
    in_serve = (s1 if played_on == 1 else s2 if played_on == 2 else None)
    zone = in_serve.get("zone") if in_serve and in_serve.get("zone") in ZONES else None
    ending_by = p.get("ending_by") if p.get("ending_by") in PLAYERS else None
    if ending_by is None and ending and winner:
        if ending in ("ace", "service_winner", "double_fault"):
            ending_by = server
        elif ending == "winner":
            ending_by = winner
        elif ending in ("unforced_error", "forced_error"):
            ending_by = other(winner)
    rally = p.get("rally_len") if isinstance(p.get("rally_len"), int) and p.get("rally_len") > 0 else None
    if rally is None and ending in ("ace", "service_winner"):
        rally = 1
    if ending == "double_fault":
        rally = 0
    # return outcome (only when the serve was in)
    ret_in = None
    if played_on in (1, 2) or ending in ("ace", "service_winner"):
        if ending in ("ace", "service_winner") or rally == 1:
            ret_in = False
        elif rally is not None and rally >= 3:
            ret_in = True
        elif rally == 2 and ending and ending_by == recv:
            ret_in = ending == "winner"
        elif rally == 2 and ending and ending_by == server:
            ret_in = True
    ret_wing = p.get("return_wing") if p.get("return_wing") in WINGS else None
    bucket = None
    if rally is not None and rally >= 1:
        for name, lo, hi in RALLY_BUCKETS:
            if lo <= rally <= hi:
                bucket = name
    fl = p.get("flags") or {}
    return {
        "id": p.get("id"), "server": server, "receiver": recv, "winner": winner,
        "side": p.get("side") if p.get("side") in SIDES else None,
        "first_in": first_in, "second_in": second_in, "played_on": played_on, "df": df,
        "zone": zone, "s1": s1, "s2": s2, "ending": ending, "ending_by": ending_by,
        "ace": (ending == "ace") if ending else None,
        "rally": rally, "bucket": bucket, "ret_in": ret_in, "ret_wing": ret_wing,
        "last_wing": p.get("last_shot_wing") if p.get("last_shot_wing") in WINGS else None,
        "last_stroke": p.get("last_shot_stroke") if known(p.get("last_shot_stroke")) else None,
        "bp": bool(fl.get("break_point")), "pressure": _pressure(fl),
        "net": p.get("net_approaches") or {}, "dist": p.get("player_dist_m") or {},
        "gap": bool(p.get("gap_before")) and not score_filled(p),
        "filled": score_filled(p),
    }


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def _coverage(points: list) -> dict:
    total = len(points)
    checks = {
        "winner": lambda p, a: a["winner"] is not None,
        "serves": lambda p, a: a["first_in"] is not None,
        "serve_zone": lambda p, a: a["zone"] is not None,
        "return_wing": lambda p, a: a["ret_wing"] is not None,
        "rally_len": lambda p, a: a["rally"] is not None,
        "ending": lambda p, a: a["ending"] is not None,
        "last_shot_wing": lambda p, a: a["last_wing"] is not None,
        "net_approaches": lambda p, a: bool(a["net"]),
        "player_dist_m": lambda p, a: bool(a["dist"]),
    }
    out = {}
    attrs = [point_attrs(p) for p in points]
    for f, fn in checks.items():
        k = sum(1 for p, a in zip(points, attrs) if fn(p, a))
        confs = [field_conf(p, f) for p, a in zip(points, attrs) if fn(p, a)]
        confs = [c for c in confs if c is not None]
        low = sum(1 for c in confs if c < CONF_MIN)
        out[f] = {"known": k, "total": total, "pct": round(100.0 * k / total, 1) if total else None,
                  "mean_conf": round(sum(confs) / len(confs), 3) if confs else None,
                  "low_conf": low}
    return out


def _player_stats(P: str, attrs: list) -> dict:
    O = other(P)
    serve = [a for a in attrs if a["server"] == P]
    ret = [a for a in attrs if a["server"] == O]
    st = {}
    wk = [a for a in attrs if a["winner"]]
    st["points_won"] = rate(sum(a["winner"] == P for a in wk), len(wk))
    sw = [a for a in serve if a["winner"]]
    st["serve_points_won"] = rate(sum(a["winner"] == P for a in sw), len(sw))
    rw = [a for a in ret if a["winner"]]
    st["return_points_won"] = rate(sum(a["winner"] == P for a in rw), len(rw))
    f1 = [a for a in serve if a["first_in"] is not None]
    st["first_serve_in"] = rate(sum(bool(a["first_in"]) for a in f1), len(f1))
    p1 = [a for a in serve if a["played_on"] == 1 and a["winner"]]
    st["first_serve_won"] = rate(sum(a["winner"] == P for a in p1), len(p1))
    p2 = [a for a in serve if a["first_in"] is False and a["winner"] and (a["played_on"] == 2 or a["df"])]
    st["second_serve_won"] = rate(sum(a["winner"] == P for a in p2), len(p2))
    s2 = [a for a in serve if a["first_in"] is False and a["second_in"] is not None]
    st["second_serve_in"] = rate(sum(bool(a["second_in"]) for a in s2), len(s2))
    ek = [a for a in serve if a["ending"]]
    st["aces"] = rate(sum(a["ending"] == "ace" for a in ek), len(ek))
    st["double_faults"] = rate(sum(a["ending"] == "double_fault" for a in ek), len(ek))

    zones = {}
    for sn in (1, 2):
        zones[str(sn)] = {}
        for sd in SIDES:
            pts = [a for a in serve if a["played_on"] == sn and a["side"] == sd and a["zone"]]
            n = len(pts)
            zd = {"n": n}
            for z in ZONES:
                zp = [a for a in pts if a["zone"] == z]
                zw = [a for a in zp if a["winner"]]
                zd[z] = {"count": len(zp), "share": rate(len(zp), n),
                         "won": rate(sum(a["winner"] == P for a in zw), len(zw))}
            zones[str(sn)][sd] = zd
    st["serve_zones"] = zones

    faults = {}
    for sn in (1, 2):
        key = "s1" if sn == 1 else "s2"
        fs = [a[key] for a in serve if a[key] and a[key].get("result") == "fault"]
        typed = [s for s in fs if s.get("fault") in FAULTS]
        faults[str(sn)] = {"n": len(typed), "faults": len(fs),
                           **{f: sum(1 for s in typed if s.get("fault") == f) for f in FAULTS}}
    st["fault_types"] = faults

    rr = [a for a in ret if a["ret_in"] is not None]
    st["return_in"] = rate(sum(bool(a["ret_in"]) for a in rr), len(rr))
    by_wing = {}
    for w in WINGS:
        ww = [a for a in rr if a["ret_wing"] == w]
        by_wing[w] = {"in": rate(sum(bool(a["ret_in"]) for a in ww), len(ww)),
                      "errors": sum(1 for a in ww if not a["ret_in"])}
    st["return_by_wing"] = by_wing

    endings = {}
    for e in ("winner", "unforced_error", "forced_error"):
        mine = [a for a in attrs if a["ending"] == e and a["ending_by"] == P]
        d = {"total": len(mine), "fh": 0, "bh": 0, "unknown_wing": 0, "by_stroke": {}}
        for a in mine:
            if a["last_wing"]:
                d[a["last_wing"]] += 1
            else:
                d["unknown_wing"] += 1
            sk = a["last_stroke"] or "unknown"
            d["by_stroke"][sk] = d["by_stroke"].get(sk, 0) + 1
        endings[e] = d
    st["endings"] = endings

    rally = {}
    for name, lo, hi in RALLY_BUCKETS:
        bb = [a for a in attrs if a["bucket"] == name and a["winner"]]
        rally[name] = rate(sum(a["winner"] == P for a in bb), len(bb))
    st["rally"] = rally

    bpf = [a for a in serve if a["bp"] and a["winner"]]
    bpc = [a for a in ret if a["bp"] and a["winner"]]
    st["break_points"] = {"saved": rate(sum(a["winner"] == P for a in bpf), len(bpf)),
                          "converted": rate(sum(a["winner"] == P for a in bpc), len(bpc))}

    pr = [a for a in wk if a["pressure"]]
    nr = [a for a in wk if not a["pressure"]]
    sp = [a for a in f1 if a["pressure"]]
    sn_ = [a for a in f1 if not a["pressure"]]
    st["pressure"] = {"won": rate(sum(a["winner"] == P for a in pr), len(pr)),
                      "normal_won": rate(sum(a["winner"] == P for a in nr), len(nr)),
                      "first_serve_in": rate(sum(bool(a["first_in"]) for a in sp), len(sp)),
                      "first_serve_in_normal": rate(sum(bool(a["first_in"]) for a in sn_), len(sn_))}

    nk = [a for a in attrs if P in a["net"] and isinstance(a["net"].get(P), (int, float))]
    appr = [a for a in nk if a["net"][P] > 0 and a["winner"]]
    st["net"] = {"points": rate(sum(a["winner"] == P for a in appr), len(appr)),
                 "approaches": int(sum(a["net"][P] for a in nk)), "n_points_known": len(nk)}

    dk = [float(a["dist"][P]) for a in attrs if isinstance(a["dist"].get(P), (int, float))]
    st["distance"] = {"n": len(dk), "mean_m": round(sum(dk) / len(dk), 1) if dk else None,
                      "total_m": round(sum(dk), 1) if dk else None}
    st["serve_points"] = len(serve)
    st["return_points"] = len(ret)
    return st


def compute_stats(points: list, players: Optional[dict] = None) -> dict:
    """Per-player scouting stats. See module docstring for conventions."""
    attrs = [point_attrs(p) for p in points]
    return {
        "n_points": len(points),
        "n_gaps": sum(1 for a in attrs if a["gap"]),
        "n_filled": sum(1 for a in attrs if a["filled"]),
        "n_reviewed": sum(1 for p in points if (p.get("review") or {}).get("status") == "reviewed"),
        "n_edited": sum(1 for p in points if (p.get("review") or {}).get("edited_fields")),
        "n_checked": sum(1 for p in points if (p.get("review") or {}).get("checked_fields")),
        "n_added": sum(1 for p in points if (p.get("review") or {}).get("status") == "added"),
        "coverage": _coverage(points),
        "players": {P: _player_stats(P, attrs) for P in PLAYERS},
        "names": {P: _name(players, P) for P in PLAYERS},
    }


# ---------------------------------------------------------------------------
# insights
# ---------------------------------------------------------------------------
def _mean_conf(points_by_id: dict, ids: list, fields: tuple) -> Optional[float]:
    vals = []
    for i in ids:
        p = points_by_id.get(i)
        if not p:
            continue
        for f in fields:
            c = field_conf(p, f)
            if c is not None:
                vals.append(c)
    return sum(vals) / len(vals) if vals else None


def _pct(k, n):
    return 100.0 * k / n if n else 0.0


def _fmt_pct(x):
    return f"{x:.0f}%"


class _Cand:
    """One possible note. p: two-sided chance test of its gap (fisher_p of the group
    against its comparison group, or binom_p against an even split)."""
    __slots__ = ("text", "player", "n", "effect", "k", "focal_ids", "comp", "fields", "kind",
                 "key", "comp_n", "p")

    def __init__(self, text, player, k, n, effect, ids, comp, fields, kind, key, comp_n=None, p=1.0):
        self.text, self.player, self.k, self.n, self.effect = text, player, k, n, effect
        self.focal_ids, self.comp, self.fields, self.kind, self.key = ids, comp, fields, kind, key
        self.comp_n, self.p = comp_n, p


def _candidates(points: list, players: dict) -> list:
    attrs = [point_attrs(p) for p in points]
    C = []
    for P in PLAYERS:
        O = other(P)
        nm = _name(players, P)
        serve = [a for a in attrs if a["server"] == P]
        ret = [a for a in attrs if a["server"] == O]

        # 1. return breakdown by wing
        rr = [a for a in ret if a["ret_in"] is not None and a["ret_wing"]]
        g = {w: [a for a in rr if a["ret_wing"] == w] for w in WINGS}
        miss = {w: [a for a in g[w] if not a["ret_in"]] for w in WINGS}
        if len(g["fh"]) >= MIN_N and len(g["bh"]) >= MIN_N:
            m = {w: _pct(len(miss[w]), len(g[w])) for w in WINGS}
            bad = "bh" if m["bh"] >= m["fh"] else "fh"
            good = other_w(bad)
            eff = m[bad] - m[good]
            wn = {"fh": "forehand", "bh": "backhand"}
            verb = "broke down" if m[bad] >= 40 else "was less reliable"
            text = (f"{nm}'s {wn[bad]} return {verb}: {len(miss[bad])} of {len(g[bad])} "
                    f"{wn[bad]} returns missed ({_fmt_pct(m[bad])}) vs {len(miss[good])} of "
                    f"{len(g[good])} on the {wn[good]} ({_fmt_pct(m[good])}).")
            C.append(_Cand(text, P, len(miss[bad]), len(g[bad]), eff, [a["id"] for a in g[bad]],
                           m[good], ("return_wing", "rally_len", "ending"), "weakness",
                           f"ret_wing_{bad}", len(g[good]),
                           fisher_p(len(miss[bad]), len(g[bad]), len(miss[good]), len(g[good]))))

        # 2. serve zone tendency per serve number and side: the most common spot
        # against the next most common one (not against an even three-way split, which
        # called 19 T against 17 wide of 39 a tendency)
        for sn in (1, 2):
            for sd in SIDES:
                pts = [a for a in serve if a["played_on"] == sn and a["side"] == sd and a["zone"]]
                if len(pts) < MIN_N:
                    continue
                cnt = {z: [a for a in pts if a["zone"] == z] for z in ZONES}
                z = max(ZONES, key=lambda zz: len(cnt[zz]))
                z2 = max((zz for zz in ZONES if zz != z), key=lambda zz: len(cnt[zz]))
                share, share2 = _pct(len(cnt[z]), len(pts)), _pct(len(cnt[z2]), len(pts))
                eff = share - share2
                zw = {"wide": "wide", "body": "into the body", "T": "down the T"}
                text = (f"{nm} served {_fmt_pct(share)} of {'1st' if sn == 1 else '2nd'} serves "
                        f"{zw[z]} on the {sd} side ({len(cnt[z])} of {len(pts)}); the next most common "
                        f"spot was {zw[z2]}, {len(cnt[z2])} of {len(pts)} ({_fmt_pct(share2)}).")
                # chance test: of the serves to these two spots, is the split uneven?
                C.append(_Cand(text, P, len(cnt[z]), len(pts), eff, [a["id"] for a in cnt[z]],
                               share2, ("serve_zone",), "tendency", f"zone_pref_{sn}_{sd}",
                               p=binom_p(len(cnt[z]), len(cnt[z]) + len(cnt[z2]))))

        # 3. serve zone effectiveness per serve number and side: zone vs the other zones
        # of the same serve. First and second serves are not mixed: most body serves are
        # second serves, which win fewer points wherever they land.
        for sn in (1, 2):
            for sd in SIDES:
                pts = [a for a in serve if a["played_on"] == sn and a["side"] == sd and a["zone"]
                       and a["winner"]]
                for z in ZONES:
                    zin = [a for a in pts if a["zone"] == z]
                    zout = [a for a in pts if a["zone"] != z]
                    if len(zin) < MIN_N or len(zout) < MIN_N:
                        continue
                    kin = sum(a["winner"] == P for a in zin)
                    kout = sum(a["winner"] == P for a in zout)
                    r_in, r_out = _pct(kin, len(zin)), _pct(kout, len(zout))
                    eff = r_in - r_out
                    ztxt = {"wide": "wide", "body": "at the body", "T": "down the T"}[z]
                    kind = "strength" if eff > 0 else "weakness"
                    nth = "first" if sn == 1 else "second"
                    text = (f"{nm} won {_fmt_pct(r_in)} of points when the {nth} serve went in {ztxt} "
                            f"on the {sd} side ({kin} of {len(zin)}) vs {_fmt_pct(r_out)} when it went "
                            f"in elsewhere ({kout} of {len(zout)}).")
                    C.append(_Cand(text, P, kin, len(zin), eff, [a["id"] for a in zin], r_out,
                                   ("serve_zone", "winner"), kind, f"zone_eff_{sn}_{sd}_{z}", len(zout),
                                   fisher_p(kin, len(zin), kout, len(zout))))

        # 4. first-serve % under pressure
        f1 = [a for a in serve if a["first_in"] is not None]
        fp = [a for a in f1 if a["pressure"]]
        fn = [a for a in f1 if not a["pressure"]]
        if len(fp) >= MIN_N and len(fn) >= MIN_N:
            kp = sum(bool(a["first_in"]) for a in fp)
            kn = sum(bool(a["first_in"]) for a in fn)
            rp, rn = _pct(kp, len(fp)), _pct(kn, len(fn))
            eff = rp - rn
            verb = "fell" if eff < 0 else "rose"
            text = (f"On pressure points (break, set or match point), the share of {nm}'s first serves "
                    f"that went in {verb} from {_fmt_pct(rn)} to {_fmt_pct(rp)} ({kp} of {len(fp)}).")
            C.append(_Cand(text, P, kp, len(fp), eff, [a["id"] for a in fp], rn, ("serves",),
                           "weakness" if eff < 0 else "strength", "pressure_first_in", len(fn),
                           fisher_p(kp, len(fp), kn, len(fn))))

        # 5. points won under pressure
        wk = [a for a in attrs if a["winner"]]
        pp = [a for a in wk if a["pressure"]]
        pn = [a for a in wk if not a["pressure"]]
        if len(pp) >= MIN_N and len(pn) >= MIN_N:
            kp = sum(a["winner"] == P for a in pp)
            kn = sum(a["winner"] == P for a in pn)
            rp, rn = _pct(kp, len(pp)), _pct(kn, len(pn))
            eff = rp - rn
            text = (f"{nm} won {_fmt_pct(rp)} of pressure points ({kp} of {len(pp)}) vs "
                    f"{_fmt_pct(rn)} of other points.")
            if eff > 0:  # mirror image of the opponent's claim: keep one
                C.append(_Cand(text, P, kp, len(pp), eff, [a["id"] for a in pp], rn, ("winner",),
                           "strength" if eff > 0 else "weakness", "pressure_won", len(pn),
                           fisher_p(kp, len(pp), kn, len(pn))))

        # 6. second-serve points won vs the opponent's
        def ss(X):
            return [a for a in attrs if a["server"] == X and a["first_in"] is False and a["winner"]
                    and (a["played_on"] == 2 or a["df"])]
        mine, theirs = ss(P), ss(O)
        if len(mine) >= MIN_N and len(theirs) >= MIN_N:
            km = sum(a["winner"] == P for a in mine)
            kt = sum(a["winner"] == O for a in theirs)
            rm, rt = _pct(km, len(mine)), _pct(kt, len(theirs))
            eff = rm - rt
            text = (f"{nm} won {_fmt_pct(rm)} of 2nd-serve points ({km} of {len(mine)}) vs "
                    f"{_fmt_pct(rt)} for {_name(players, O)}.")
            if eff > 0:  # the opponent's version is the same fact mirrored
                C.append(_Cand(text, P, km, len(mine), eff, [a["id"] for a in mine], rt,
                           ("serves", "winner"), "strength" if eff > 0 else "weakness",
                           "second_serve_won", len(theirs), fisher_p(km, len(mine), kt, len(theirs))))

        # 7. long vs short rallies
        lg = [a for a in attrs if a["bucket"] == "9+" and a["winner"]]
        sh = [a for a in attrs if a["bucket"] == "1-4" and a["winner"]]
        if len(lg) >= MIN_N and len(sh) >= MIN_N:
            kl = sum(a["winner"] == P for a in lg)
            ks = sum(a["winner"] == P for a in sh)
            rl, rs = _pct(kl, len(lg)), _pct(ks, len(sh))
            eff = rl - rs
            text = (f"{nm} won {_fmt_pct(rl)} of rallies of 9+ shots ({kl} of {len(lg)}) vs "
                    f"{_fmt_pct(rs)} of points of 1-4 shots.")
            if eff > 0:  # the opponent's version is the same fact mirrored
                C.append(_Cand(text, P, kl, len(lg), eff, [a["id"] for a in lg], rs,
                           ("rally_len", "winner"), "strength" if eff > 0 else "weakness",
                           "rally_long", len(sh), fisher_p(kl, len(lg), ks, len(sh))))

        # 8/9. wing concentration of unforced errors and winners (vs 50/50)
        for e, label in (("unforced_error", "unforced errors"), ("winner", "winners")):
            mine_e = [a for a in attrs if a["ending"] == e and a["ending_by"] == P and a["last_wing"]]
            if len(mine_e) < MIN_N:
                continue
            bh = [a for a in mine_e if a["last_wing"] == "bh"]
            fh = [a for a in mine_e if a["last_wing"] == "fh"]
            wing, grp = ("backhand", bh) if len(bh) >= len(fh) else ("forehand", fh)
            share = _pct(len(grp), len(mine_e))
            eff = share - 50.0
            text = (f"{len(grp)} of {nm}'s {len(mine_e)} {label} came off the {wing} "
                    f"({_fmt_pct(share)}).")
            kind = "weakness" if e == "unforced_error" else "strength"
            C.append(_Cand(text, P, len(grp), len(mine_e), eff, [a["id"] for a in grp], 50.0,
                           ("ending", "last_shot_wing"), kind, f"{e}_wing",
                           p=binom_p(len(grp), len(mine_e))))

        # 10. net play vs baseline points
        nk = [a for a in attrs if isinstance(a["net"].get(P), (int, float)) and a["winner"]]
        at = [a for a in nk if a["net"][P] > 0]
        bl = [a for a in nk if a["net"][P] == 0]
        if len(at) >= MIN_N and len(bl) >= MIN_N:
            ka = sum(a["winner"] == P for a in at)
            kb = sum(a["winner"] == P for a in bl)
            ra, rb = _pct(ka, len(at)), _pct(kb, len(bl))
            eff = ra - rb
            text = (f"{nm} won {_fmt_pct(ra)} of points when coming to the net ({ka} of {len(at)}) "
                    f"vs {_fmt_pct(rb)} when staying back.")
            C.append(_Cand(text, P, ka, len(at), eff, [a["id"] for a in at], rb,
                           ("net_approaches", "winner"), "strength" if eff > 0 else "weakness",
                           "net", len(bl), fisher_p(ka, len(at), kb, len(bl))))
    return C


def other_w(w: str) -> str:
    return "fh" if w == "bh" else "bh"


def insights(stats: dict, points: list, players: Optional[dict] = None,
             min_n: int = MIN_N, effect_pp: float = EFFECT_PP) -> list:
    """Plain-English scouting insights, strongest first.

    Rules: the focal group needs n >= min_n (and so does the comparison group,
    when there is one), and |effect| >= effect_pp percentage points. Claims
    with n < 10 get small_sample=True. Claims whose fields have mean conf
    < 0.6 (unreviewed) are kept, but marked low_confidence=True, labelled
    'low' and ranked at half weight. "confidence" comes from chance_p, a
    two-sided chance test of the gap (Fisher exact test against the comparison
    group, or a binomial test against an even split): 'high' under P_HIGH,
    'medium' under P_MEDIUM, else 'low' (also for small samples and low tracking
    confidence; 'high' also needs mean conf >= 0.8 when known). Notes are listed
    high, then medium, then low, small samples last, each group by
    rank = |effect| * sqrt(n). "checked" says whether the values behind
    the claim (its fields on its evidence points) were checked in a review:
    {"k", "n", "status": "all" | "some" | "none"} (checked_status).
    """
    if players is None:
        names = (stats or {}).get("names") or {}
        players = {P: {"name": names.get(P, P)} for P in PLAYERS}
    by_id = {p.get("id"): p for p in points}
    out = []
    for c in _candidates(points, players):
        if c.n < min_n or (c.comp_n is not None and c.comp_n < min_n):
            continue
        if abs(c.effect) < effect_pp:
            continue
        mc = _mean_conf(by_id, c.focal_ids, c.fields)
        low_conf = mc is not None and mc < CONF_MIN
        lo, hi = wilson(c.k, c.n)
        small = c.n < SMALL_N
        if low_conf or small or c.p >= P_MEDIUM:
            label = "low"
        elif c.p < P_HIGH and (mc is None or mc >= 0.8):
            label = "high"
        else:
            label = "medium"
        score = abs(c.effect) * math.sqrt(c.n) * (0.5 if low_conf else 1.0)
        text = c.text
        if small:
            text += " (small sample)"
        if low_conf:
            text += " (unverified: low tracking confidence)"
        out.append({
            "text": text, "player": c.player, "n": c.n, "k": c.k,
            "effect": round(c.effect, 1), "comparison_pct": round(c.comp, 1),
            "pct": round(_pct(c.k, c.n), 1),
            "ci": [round(lo, 1), round(hi, 1)] if lo is not None else None,
            "confidence": label, "chance_p": round(c.p, 4), "comparison_n": c.comp_n,
            "mean_conf": round(mc, 2) if mc is not None else None,
            "small_sample": small, "low_confidence": low_conf, "kind": c.kind, "key": c.key,
            "evidence_point_ids": c.focal_ids, "score": round(score, 2),
            "checked": checked_status([by_id.get(i) for i in c.focal_ids], c.fields),
        })
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda d: (d["small_sample"], order[d["confidence"]], -d["score"]))
    return out


def recommendations(stats: dict, ins: list, players: Optional[dict] = None) -> list:
    """Tactical suggestions for each player against the other, made only from the
    insights whose gap is unlikely to be chance (confidence high or medium). They are
    ideas drawn from one match, not tested advice."""
    if players is None:
        names = (stats or {}).get("names") or {}
        players = {P: {"name": names.get(P, P)} for P in PLAYERS}
    recs = []
    by = {(i["player"], i["key"]): i for i in ins if i.get("confidence") in ("high", "medium")}

    def add(for_p, text, basis):
        recs.append({"for": for_p, "against": other(for_p), "text": text,
                     "basis": basis["text"], "n": basis["n"], "confidence": basis["confidence"],
                     "checked": basis.get("checked")})

    for X in PLAYERS:
        Y = other(X)
        ny = _name(players, Y)
        hand = _hand(players, Y)
        # serve to the weaker return wing
        for w in WINGS:
            i = by.get((Y, f"ret_wing_{w}"))
            if i and i["effect"] > 0:
                # right-hander: backhand = T on deuce, wide on ad; left-hander mirrored
                to_bh = (hand == "R") == (w == "bh")
                where = "down the T on the deuce side and wide on the ad side" if to_bh \
                    else "wide on the deuce side and down the T on the ad side"
                wn = "backhand" if w == "bh" else "forehand"
                given = ((players or {}).get(Y) or {}).get("hand_given")
                hw = "right-handed" if hand == "R" else "left-handed"
                note = "" if given else (f" This assumes {ny} is {hw}, which was not entered or checked; "
                                         f"for a player of the other hand the sides swap.")
                add(X, f"Serve to {ny}'s {wn}: {where}.{note}", i)
        i = by.get((Y, "unforced_error_wing"))
        if i and i["effect"] > 0:
            wing = "backhand" if "backhand" in i["text"] else "forehand"
            add(X, f"Build rallies through {ny}'s {wing}; that wing leaks unforced errors.", i)
        i = by.get((X, "second_serve_won"))
        if i and i["effect"] > 0:
            add(X, f"Attack {ny}'s 2nd serve: step in and take the return early.", i)
        i = by.get((Y, "pressure_first_in"))
        if i and i["effect"] < 0:
            add(X, f"Make {ny} play on big points: the 1st serve drops under pressure, "
                   f"so get every return back.", i)
        i = by.get((X, "rally_long"))
        if i and i["effect"] > 0:
            add(X, "Extend rallies past 8 shots: build with depth and margin rather than "
                   "going for early winners.", i)
        i = by.get((Y, "rally_long"))
        if i and i["effect"] > 0:
            add(X, "Keep points short: attack with the serve and the first shot after it.", i)
        for sn in (1, 2):
            for sd in SIDES:
                for z in ZONES:
                    i = by.get((X, f"zone_eff_{sn}_{sd}_{z}"))
                    if i and i["effect"] > 0:
                        ztxt = {"wide": "wide", "body": "into the body", "T": "down the T"}[z]
                        add(X, f"Keep serving {'first' if sn == 1 else 'second'} serves {ztxt} "
                               f"on the {sd} side.", i)
        i = by.get((X, "net"))
        if i and i["effect"] > 0:
            add(X, "Come forward more: net points are paying off.", i)
        elif i and i["effect"] < 0:
            add(X, "Be selective about approaching; net points are going against you.", i)
    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: (order.get(r["confidence"], 3), -r["n"]))
    return recs


# ---------------------------------------------------------------------------
# corrections
# ---------------------------------------------------------------------------
_SERVE_KEYS = {f"serve{n}_{attr}": (n, attr) for n in (1, 2)
               for attr in ("result", "zone", "fault", "t", "bounce_xy", "speed_kmh")}
_SERVE_DROP = {"serve1": 1, "serve2": 2}


def _fmt_from_meta(meta: dict) -> MatchFormat:
    f = (meta or {}).get("format") or {}
    if isinstance(f, MatchFormat):
        return f
    kw = {k: f[k] for k in MatchFormat.__dataclass_fields__ if k in f}
    return MatchFormat(**kw)


def _get_serve(p: dict, n: int) -> dict:
    for s in p.setdefault("serves", []):
        if s.get("n") == n:
            return s
    s = {"n": n, "t": p.get("t_start") or p.get("t_end") or 0.0, "result": "unknown", "zone": None,
         "fault": None, "bounce_xy": None, "speed_kmh": None, "conf": 1.0}
    p["serves"].append(s)
    p["serves"].sort(key=lambda x: x.get("n", 0))
    return s


def derive_fields(p: dict) -> None:
    """Make dependent fields consistent after an edit (in place)."""
    server = p.get("server")
    if server in PLAYERS:
        p["receiver"] = other(server)
    e = p.get("ending")
    w = p.get("winner")
    if e == "double_fault":
        p["serve_number_won_on"] = None
        p["ending_by"] = server
        p["rally_len"] = 0
        _get_serve(p, 1)["result"] = "fault"
        _get_serve(p, 2)["result"] = "fault"
    elif e in ("ace", "service_winner"):
        p["ending_by"] = server
        if e == "ace":
            p["rally_len"] = 1
    elif e == "winner" and w in PLAYERS and p.get("ending_by") not in PLAYERS:
        p["ending_by"] = w
    elif e in ("unforced_error", "forced_error") and w in PLAYERS and p.get("ending_by") not in PLAYERS:
        p["ending_by"] = other(w)
    sn = p.get("serve_number_won_on")
    if sn == 1:
        _get_serve(p, 1)["result"] = "in"
    elif sn == 2:
        _get_serve(p, 1)["result"] = "fault"
        _get_serve(p, 2)["result"] = "in"


def rescore(points: list, fmt: MatchFormat) -> None:
    """Recompute score_before/server/receiver/side/flags/set_no from winners
    (in place). Each gap_before point restarts from its stored score."""
    from . import scoring as sc
    cur = None
    for p in points:
        if cur is None or p.get("gap_before") or cur.match_over:
            sb = p.get("score_before") or {"sets": [], "games": [0, 0], "points": "0-0", "in_tb": False}
            try:
                cur = sc.state_from_score_dict(sb, p.get("server") or "A", fmt,
                                               ends=(sb.get("ends") or p.get("ends")))
            except (ValueError, KeyError):
                cur = sc.initial_state(fmt, p.get("server") or "A")
        p["score_before"] = sc.score_dict(cur)
        p["server"] = cur.server
        p["receiver"] = other(cur.server)
        p["side"] = sc.side(cur, fmt)
        p["flags"] = sc.flags(cur, fmt)
        p["set_no"] = len(cur.sets) + 1
        if p.get("winner") in PLAYERS and not cur.match_over:
            cur = sc.apply(cur, p["winner"], fmt)


def _mark_conf(conf: dict, k: str) -> None:
    """conf 1.0 for a field a review set or confirmed."""
    if k in _SERVE_KEYS:
        attr = _SERVE_KEYS[k][1]
        if attr == "zone":
            conf["serve_zone"] = 1.0
        elif attr in ("result", "fault"):
            conf["serves"] = 1.0
    elif k in _SERVE_DROP:
        return
    else:
        conf[k] = 1.0


# keys an added point is built from (set once when the point is made)
_ADD_KEYS = ("t_start", "t_end", "score_before", "server", "ends")


def _new_point(pid, fields: dict, fmt: MatchFormat) -> Optional[dict]:
    """PointRecord dict for a point a review adds, or None when the entry lacks
    what it needs. Receiver, side, flags and set number come from the score."""
    from . import scoring as sc
    try:
        new_id = int(pid)
        t_end = float(fields["t_end"])
        t_start = fields.get("t_start")
        t_start = float(t_start) if t_start is not None else None
        sb = dict(fields["score_before"])
        server = fields["server"]
        ends = fields.get("ends") or sb.get("ends")
        st = sc.state_from_score_dict(sb, server, fmt, ends=ends)
    except (KeyError, TypeError, ValueError):
        return None
    if server not in PLAYERS:
        return None
    score = sc.score_dict(st)
    if not ends:
        score.pop("ends", None)         # never invent the players' ends
    p = asdict(PointRecord(id=new_id, t_start=t_start, t_end=t_end, set_no=len(st.sets) + 1,
                           score_before=score, server=server, receiver=other(server),
                           side=sc.side(st, fmt), flags=sc.flags(st, fmt), winner=None,
                           winner_source="manual", ends=(dict(st.ends) if ends else None)))
    p["review"] = {"status": "added", "edited_fields": []}
    return p


def _insert_by_time(pts: list, p: dict) -> None:
    t = p.get("t_end") or 0.0
    i = next((k for k, q in enumerate(pts) if (q.get("t_end") or 0.0) > t), len(pts))
    pts.insert(i, p)


def points_key(points: list) -> str:
    """Short fingerprint of the points' ids and end times. A corrections file
    can carry it in "_meta" -> "points_key" so it is only applied to the same
    points it was made for."""
    import hashlib
    import json
    ids = [[p.get("id"), round(float(p.get("t_end") or 0.0), 2)] for p in points or []]
    return hashlib.md5(json.dumps(ids).encode()).hexdigest()[:16]


def apply_corrections(match: dict, corrections: dict) -> dict:
    """Return a new match dict with manual corrections applied and all derived
    data (scores if needed, MCP codes, stats, insights, recommendations)
    recomputed. Edited fields get conf 1.0 and are listed in
    review.edited_fields; confirmed fields ("checked") in review.checked_fields.
    'reviewed': true marks the point reviewed. See the module docstring."""
    m = copy.deepcopy(match)
    meta = m.setdefault("meta", {})
    if not isinstance(m.get("points"), list):
        m["points"] = []
    pts = m["points"]
    by_id = {str(p.get("id")): p for p in pts}
    fmt = _fmt_from_meta(meta)
    extra = (corrections or {}).get("_meta")
    if isinstance(extra, dict):
        key = extra.get("points_key")
        if key and key != points_key(pts):
            # made for other points (the video was analysed again): applying by id
            # would put values on the wrong points, so apply nothing
            meta["corrections_stale"] = ("corrections.json was made for a different set of points "
                                         "and was not applied; make the corrections again")
            return refresh(m)
        meta["review"] = copy.deepcopy(extra)
    winners_changed = False
    n_applied = 0
    for pid, fields in (corrections or {}).items():
        if str(pid) == "_meta" or not isinstance(fields, dict):
            continue
        p = by_id.get(str(pid))
        is_new = bool(fields.get("add"))
        if is_new:
            if p is not None:
                continue            # an added point never edits a point the pipeline found
            p = _new_point(pid, fields, fmt)
            if p is None:
                continue
            _insert_by_time(pts, p)
            by_id[str(pid)] = p
        elif p is None:
            continue
        rv = p.setdefault("review", {"status": "auto", "edited_fields": []})
        rv.setdefault("edited_fields", [])
        conf = p.setdefault("conf", {})
        order = ("serve_number_won_on", "serve1_result", "serve2_result")
        items = sorted(fields.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else len(order)))
        for k, v in items:
            if k == "add":
                continue
            if k == "reviewed":
                if v:
                    rv["status"] = "reviewed"
                continue
            if k == "note":
                rv["note"] = v
                continue
            if k == "checked":
                ck = rv.setdefault("checked_fields", [])
                for f in (v if isinstance(v, (list, tuple)) else []):
                    if isinstance(f, str) and f not in ck:
                        ck.append(f)
                        _mark_conf(conf, f)
                continue
            if is_new and k in _ADD_KEYS:
                pass                # already used to build the point
            elif k == "serve_zone":
                sn = p.get("serve_number_won_on") or 1
                _get_serve(p, sn)["zone"] = v
                conf["serve_zone"] = 1.0
            elif k in _SERVE_KEYS:
                n, attr = _SERVE_KEYS[k]
                _get_serve(p, n)[attr] = v
                _mark_conf(conf, k)
            elif k in _SERVE_DROP:
                if v is not None:
                    continue        # only null (remove the serve) is supported
                p["serves"] = [s for s in (p.get("serves") or []) if s.get("n") != _SERVE_DROP[k]]
            else:
                if k == "winner" and p.get("winner") != v:
                    winners_changed = True
                    p["winner_source"] = "manual"
                p[k] = v
                conf[k] = 1.0
            if k not in rv["edited_fields"]:
                rv["edited_fields"].append(k)
        if rv["edited_fields"] and rv.get("status") == "auto":
            rv["status"] = "edited"
        derive_fields(p)
        n_applied += 1
    if n_applied:
        # tells the review UI this match.json is not fresh pipeline output
        meta["corrections_applied"] = n_applied
    if winners_changed:
        has_bug = bool(meta.get("has_scorebug"))
        any_ocr = any(p.get("winner_source") == "ocr" for p in pts)
        if not (has_bug and any_ocr):
            rescore(pts, fmt)
    return refresh(m)


def refresh(match: dict) -> dict:
    """Recompute MCP codes, stats, insights, recommendations and serve habits
    (match["serve_patterns"], see patterns.py) in place."""
    from .patterns import compute as serve_patterns
    from .report import mcp_code
    meta = match.get("meta") or {}
    players = meta.get("players") or {}
    for p in match.get("points") or []:
        p["mcp"] = mcp_code(p)
    st = compute_stats(match.get("points") or [], players)
    ins = insights(st, match.get("points") or [], players)
    match["stats"] = st
    match["insights"] = ins
    match["recommendations"] = recommendations(st, ins, players)
    match["serve_patterns"] = serve_patterns(match.get("points") or [], players, _fmt_from_meta(meta))
    return match
