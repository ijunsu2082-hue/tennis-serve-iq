"""Export the precomputed app tables (app/data/*.csv) to one compact JSON file for the static site.

Reads ONLY app/data/*.csv. Never re-runs src/baseline.py or src/app_data.py, and never reads output/.
Writes site/data/serveiq.json (no indentation). Float precision:
  - league.directions, league.within, league.serial, league.stability: full float64 precision
    (CSV text parsed with float_precision="round_trip", written with repr)
  - everything else: 7 significant figures. player_mix.csv and player_tests.csv are stored with
    5 significant figures, so for their columns 7 sig figs is lossless. Derived values (mix.other
    share, fav_pct, headline shares) are computed at full precision first. 5 sig figs flipped 28
    displayed values by 0.1 point (e.g. 63/163 = 0.386503 -> 0.3865 -> 38.6% instead of 38.7%).

Null rules (JSON null, never a placeholder 0 or 1):
  - any NaN value (share, win, p, q, ...)
  - win, win_lo, win_hi when n == 0 (no serves to that direction: the win rate does not exist;
    app_data.py wrote win NaN with a fake 0 to 1 interval for these rows)
  - share_lo, share_hi when n == 0 (a zero count gives a zero-width interval, not a real CI).
    The share itself stays 0: zero serves out of a nonzero total is an observation.
  - CI bounds when the estimate is NaN, or when the player-side has fewer than 2 matches (G < 2,
    no clustered SE; does not occur with the current 10-match threshold but is guarded anyway)

Run (Python 3.9):  .venv/bin/python src/export_site.py
"""
import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "app" / "data"
OUT = ROOT / "site" / "data" / "serveiq.json"

DIRS = ["wide", "body", "T"]
SIDES = ["deuce", "ad"]
THRESHOLDS = {"min_serves_side": 300, "min_matches": 10, "min_dir_n": 30, "bp_warn_n": 100, "q": 0.05}
Q = THRESHOLDS["q"]

CSV_FILES = ["baseline_cells.csv", "baseline_directions.csv", "baseline_serial.csv",
             "baseline_within_server.csv", "league_cells.csv", "player_mix.csv", "player_tests.csv",
             "players.csv", "stability.csv"]

MIX_FIELDS = ["n", "share", "share_lo", "share_hi", "league_share", "win", "win_lo", "win_hi", "league_win"]
TEST_FIELDS = ["n", "matches", "top_dir", "top_share", "fav_pct", "q_mix", "p_mix", "p_win", "q_win",
               "best_dir", "gain_pp", "n_bp", "bp_wide", "bp_wide_q", "bp_body", "bp_body_q", "bp_T", "bp_T_q"]
PLAYER_FIELDS = ["gender", "server", "hand", "matches", "first", "last", "serves"]

WITHIN_EFFECT_FE = "server x side x matchup x break point"
BP_FE = "match x server x side"


SIG = 7

# Constants NOT derivable from app/data: they come from output/serves.parquet rebuilt with
# src/build_serves.py on 2026-10-29 (2,559,173 charted serves; 3.2% tiebreak serves and 0.18% with no
# recorded direction are excluded, leaving 2,473,821 = league_cells.csv n total).
SERVES_TOTAL = 2559173
TIEBREAK_SHARE = 0.031576
NODIR_SHARE = 0.0017576


def r5(x, full=False):
    """JSON-safe scalar: ints stay ints, floats rounded to SIG significant figures (or kept at full
    precision when full=True), NaN -> None. (Name kept from the 5-sig-fig version.)"""
    if x is None:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        if math.isnan(x) or math.isinf(x):
            return None
        if x == 0:
            return 0.0
        return x if full else float(f"{x:.{SIG}g}")
    return x


def table(df, fields, full=False):
    return {"fields": list(fields),
            "rows": [[r5(v, full) for v in row] for row in df[fields].itertuples(index=False)]}


def sha256(path):
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


def load():
    d = {f: pd.read_csv(DATA / f, float_precision="round_trip") for f in CSV_FILES}
    tests = d["player_tests.csv"]
    # identical to guess_pct in load() of app/streamlit_app.py: share of the other listed players
    # (same gender, serve, side) with a lower favorite-target share
    g = tests.groupby(["gender", "serve_num", "side"]).top_share
    tests["fav_pct"] = (g.rank(method="min") - 1) / (g.transform("count") - 1)
    return d


def other_share(rows, side, d):
    """Copy of other_share() in app/streamlit_app.py, also returning the count.
    Share of direction d on points that are NOT break points, from the integer counts at full
    precision (rounded only when written)."""
    a = rows[(rows.side == side) & (rows.situation == "all")]
    b = rows[(rows.side == side) & (rows.situation == "bp")]
    n_other = int(a.n.sum()) - int(b.n.sum())
    n_d = int(a[a.direction == d].n.sum()) - int(b[b.direction == d].n.sum())
    return n_d, (n_d / n_other if n_other else np.nan)


def ws(within, gender, serve, question, fe):
    r = within[(within.gender == gender) & (within.serve_num == serve) & (within.question == question)
               & (within.fixed_effect == fe)]
    assert len(r) == 1, (gender, serve, question, fe, len(r))
    r = r.iloc[0]
    return {"coef": r5(r.coef_pp), "lo": r5(r.lo_pp), "hi": r5(r.hi_pp)}


def headline(d):
    tests, players, mix = d["player_tests.csv"], d["players.csv"], d["player_mix.csv"]
    within, serial, stab = d["baseline_within_server.csv"], d["baseline_serial.csv"], d["stability.csv"]
    t1, t2 = tests[tests.serve_num == 1], tests[tests.serve_num == 2]
    bp_q = tests[[f"bp_{x}_q" for x in DIRS]]
    h = {
        "players": len(players),
        "men": int((players.gender == "m").sum()),
        "women": int((players.gender == "w").sum()),
        "serves_analysed": int(d["league_cells.csv"].n.sum()),
        "serves_listed": int(players.serves.sum()),
        "mix_differs_n_s1": int((t1.q_mix < Q).sum()),
        "mix_total_s1": len(t1),
        "mix_differs_share_s1": r5((t1.q_mix < Q).sum() / len(t1)),
        "mix_differs_n_s2": int((t2.q_mix < Q).sum()),
        "mix_total_s2": len(t2),
        "mix_differs_share_s2": r5((t2.q_mix < Q).sum() / len(t2)),
        "costly_players_s1": len(t1[t1.q_win < Q][["gender", "server"]].drop_duplicates()),
        "costly_sides_s1": int((t1.q_win < Q).sum()),
        "costly_s2": int((t2.q_win < Q).sum()),
        "bp_sig_tests": int((bp_q < Q).to_numpy().sum()),
        "bp_sig_patterns": int((bp_q < Q).any(axis=1).sum()),
    }
    fav = stab[stab.metric == "guess"]["corr"]
    edge = stab[stab.metric == "edge"]["corr"]
    h.update(fav_r_min=r5(fav.min()), fav_r_max=r5(fav.max()),
             edge_r_min=r5(edge.min()), edge_r_max=r5(edge.max()))
    for g, gl in [("m", "m"), ("w", "w")]:
        for s in [1, 2]:
            h[f"body_win_{gl}{s}"] = ws(within, g, s, "win body minus win wide", WITHIN_EFFECT_FE)
            h[f"body_in_{gl}{s}"] = ws(within, g, s, "serve in body minus serve in wide", WITHIN_EFFECT_FE)
            h[f"bp_wide_{gl}{s}"] = ws(within, g, s, "break_point: change in wide share", BP_FE)
            h[f"bp_body_{gl}{s}"] = ws(within, g, s, "break_point: change in body share", BP_FE)
            r = serial[(serial.gender == g) & (serial.serve_num == s)].iloc[0]
            h[f"serial_{gl}{s}"] = {"coef": r5(r.diff_pp), "lo": r5(r.lo_pp), "hi": r5(r.hi_pp)}
    rows = mix[(mix.gender == "m") & (mix.server == "Rafael Nadal") & (mix.serve_num == 1)]
    bp = rows[(rows.situation == "bp") & (rows.side == "deuce") & (rows.direction == "wide")].share.iloc[0]
    al = rows[(rows.situation == "all") & (rows.side == "deuce") & (rows.direction == "wide")].share.iloc[0]
    h["nadal_deuce_wide_s1"] = {"other": r5(other_share(rows, "deuce", "wide")[1]), "bp": r5(bp), "all": r5(al)}
    return h


def mix_block(d):
    mix, tests = d["player_mix.csv"], d["player_tests.csv"]
    g_of = tests.set_index(["gender", "server", "serve_num", "side"]).matches.to_dict()
    out = {}
    for (g, name, s), rows in mix.groupby(["gender", "server", "serve_num"], sort=False):
        key = f"{g}|{name}|{s}"
        entry = {}
        for side in SIDES:
            few_matches = g_of.get((g, name, s, side), 0) < 2
            sd = {}
            for sit in ["all", "bp"]:
                sub = rows[(rows.side == side) & (rows.situation == sit)].set_index("direction")
                dd = {}
                for dr in DIRS:
                    r = sub.loc[dr]
                    v = {f: r[f] for f in MIX_FIELDS}
                    v["n"] = int(v["n"])
                    if v["n"] == 0:
                        v["win"] = v["win_lo"] = v["win_hi"] = np.nan
                        v["share_lo"] = v["share_hi"] = np.nan
                    for est, lo, hi in [("share", "share_lo", "share_hi"), ("win", "win_lo", "win_hi")]:
                        if pd.isna(v[est]) or few_matches:
                            v[lo] = v[hi] = np.nan
                    dd[dr] = [r5(v[f]) for f in MIX_FIELDS]
                sd[sit] = dd
            sd["other"] = {}
            for dr in DIRS:
                n_d, sh = other_share(rows, side, dr)
                sd["other"][dr] = [n_d, r5(sh)] + [None] * (len(MIX_FIELDS) - 2)
            entry[side] = sd
        out[key] = entry
    return {"fields": MIX_FIELDS, "data": out}


def tests_block(d):
    tests = d["player_tests.csv"]
    out = {}
    for row in tests.itertuples(index=False):
        key = f"{row.gender}|{row.server}|{row.serve_num}"
        out.setdefault(key, {})[row.side] = [r5(getattr(row, f)) for f in TEST_FIELDS]
    return {"fields": TEST_FIELDS, "data": out}


def main():
    d = load()
    players = d["players.csv"]
    tests = d["player_tests.csv"]
    bp_p = tests[[f"bp_{x}_p" for x in DIRS]].to_numpy().ravel()
    meta = {
        "git_sha": git_sha(),
        "csv_sha256": {f: sha256(DATA / f) for f in CSV_FILES},
        "thresholds": THRESHOLDS,
        "tests": {"mix": int(tests.p_mix.notna().sum()), "win": int(tests.p_win.notna().sum()),
                  "bp": int(np.isfinite(bp_p).sum())},
        "players": len(players),
        "men": int((players.gender == "m").sum()),
        "women": int((players.gender == "w").sum()),
        # not derivable from app/data: see SERVES_TOTAL above (output/serves.parquet, 2026-10-29)
        "serves_total": SERVES_TOTAL,
        "tiebreak_share": TIEBREAK_SHARE,
        "nodir_share": NODIR_SHARE,
    }
    order = players.sort_values(["gender", "serves"], ascending=[True, False], kind="mergesort")
    dirs = d["baseline_directions.csv"]
    obj = {
        "meta": meta,
        "headline": headline(d),
        "players": table(order, PLAYER_FIELDS),
        "mix": mix_block(d),
        "tests": tests_block(d),
        "league": {
            "directions": table(dirs, ["gender", "serve_num", "side", "is_break_point", "matchup", "direction",
                                       "n", "low_n", "share", "share_lo", "share_hi", "win", "win_lo", "win_hi"],
                                full=True),
            "within": table(d["baseline_within_server.csv"], list(d["baseline_within_server.csv"].columns),
                            full=True),
            "serial": table(d["baseline_serial.csv"], list(d["baseline_serial.csv"].columns), full=True),
            "stability": table(d["stability.csv"], list(d["stability.csv"].columns), full=True),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, separators=(",", ":"), allow_nan=False, ensure_ascii=False)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text.encode('utf-8')) / 1024:.1f} KB)")
    for k, v in obj["headline"].items():
        print(f"  {k} = {v}")


if __name__ == "__main__":
    main()
