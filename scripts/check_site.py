"""Pre-deploy checks for the Serve IQ static site and the project copy.

Run (Python 3.9):  .venv/bin/python scripts/check_site.py [--strict] [--site DIR]
Exit code 1 if any check FAILs. Prints PASS / FAIL / WARN per check, with file:line for every hit.
--site DIR  the static site folder to check (default: site), e.g. --site site_b. Its data file is
            DIR/data/serveiq.json.
--strict    turns "file missing" WARNs into FAILs; use it before deploy. Independently of --strict,
            once DIR/index.html exists a missing DIR/app.js or DIR/styles.css is a FAIL.

Checks
  0. Site files: DIR/index.html, DIR/app.js, DIR/styles.css, DIR/data/serveiq.json exist.
  1. Data. The JSON is parsed with NaN / Infinity tokens rejected. Then, independently of
     src/export_site.py (csv module, not pandas), everything is recomputed from app/data/*.csv and
     compared field by field, by field name, with the JSON:
       - every headline value (sign-aware, rel tol 1e-6 or abs tol 1e-9)
       - every players, mix (all / bp / other), tests, league.directions / within / serial /
         stability value. Strings and integers exact, floats rel tol 1e-6 or abs tol 1e-9, booleans
         exact. A JSON null is allowed only where the export rules give null: the CSV value is NaN,
         n == 0 (win and CI bounds), the estimate is NaN (its CI bounds), or the player-side has
         fewer than 2 matches (CI bounds). Any CI bound whose estimate is null is flagged.
       - the q < 0.05 decision for every test family (mix, win, bp wide / body / T)
       - fields lists (names and order) and key sets
     Also: the verified numbers as the copy prints them (68%, 24, 0, ...), CSV hashes in meta,
     thresholds, the named constants meta.serves_total / tiebreak_share / nodir_share.
  2. Copy bans (characters and phrases from the COPY RULES) in DIR/index.html, DIR/app.js,
     DIR/styles.css, README.md, docs/SUBMISSION.md, app/streamlit_app.py.
     Skipped as KaTeX/LaTeX source:
       index.html : elements whose class list contains "tex", <script type="math/tex">,
                    data-tex="..." attribute values, $$...$$, \\(...\\), \\[...\\]
       app.js     : String.raw`...` template literals and the object literal after FORMULAS = { / FORMULAS: {
       *.md       : $$...$$, $...$ and ```math fenced blocks
       streamlit  : only string literals and comments are checked (code may use "x" etc.);
                    raw strings (r"...") are treated as LaTeX source and skipped
     British spellings are reported as WARN (not FAIL).
  3. DIR/index.html has no numbers in prose: text nodes outside class "tex" elements, the element
     with id="license", <script>, <style> and attributes must not match \\d+(\\.\\d+)?\\s?% or \\d+\\.\\d+
     (4-digit years 1990 to 2030 are allowed). Other integers above 10 in prose are WARN only.
  4. Every headline (and meta) key the site reads exists in the JSON. Patterns checked in DIR/app.js:
       headline.KEY  H.KEY  headline["KEY"]  H["KEY"]   (optionally followed by .SUB or ["SUB"])
       meta.KEY
     and in DIR/index.html: data-h="KEY" / data-h="KEY.SUB" / data-headline="KEY".
  5. README.md, docs/SUBMISSION.md and the string literals of app/streamlit_app.py (docstrings,
     comments, raw strings, f-string {...} fields, plotly %{...} fields, hex colors and rgba(...)
     skipped): every number token must be one of the explicitly NAMED values in allowed_numbers().
     They are built from the CSV recomputation, not from the JSON. A token written with a sign
     (-0.02, +9.9) must match the value with that sign; an unsigned token may match the absolute
     value. Ignored: numbers inside URLs, link targets, HTML tags/comments, LaTeX math ($...$, $$...$$,
     ```math), equation labels such as "Equation (12)", and ordinals (1st, 2nd).
"""
import argparse
import ast
import csv
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import tokenize
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "app" / "data"

ap = argparse.ArgumentParser(description="Pre-deploy checks for the Serve IQ static site and copy.")
ap.add_argument("--strict", action="store_true", help="missing files are FAILs, not WARNs")
ap.add_argument("--site", default="site", help="static site folder, relative to the repo root (default: site)")
ap.add_argument("--json", default=None, help=argparse.SUPPRESS)  # hidden: check another JSON (mutation tests)
ARGS = ap.parse_args()
STRICT = ARGS.strict
SITE = Path(ARGS.site) if Path(ARGS.site).is_absolute() else ROOT / ARGS.site
try:
    SITE_REL = SITE.resolve().relative_to(ROOT).as_posix()
except ValueError:
    SITE_REL = SITE.as_posix()
JSON_PATH = Path(ARGS.json).resolve() if ARGS.json else SITE / "data" / "serveiq.json"

Q = 0.05
DIRS = ["wide", "body", "T"]
SIDES = ["deuce", "ad"]
THRESHOLDS = {"min_serves_side": 300, "min_matches": 10, "min_dir_n": 30, "bp_warn_n": 100, "q": 0.05}

# Named constants that are NOT derivable from app/data. They come from output/serves.parquet as rebuilt
# by src/build_serves.py on 2026-10-29 (re-verified by the orchestrator): 2,559,173 charted serves in
# total, 3.2% tiebreak serves and 0.18% serves with no recorded direction, which are excluded, leaving
# 2,473,821 serves (= the n total of app/data/league_cells.csv).
SERVES_TOTAL = 2559173
TIEBREAK_SHARE = 0.031576
NODIR_SHARE = 0.0017576

# Schema the two site builders read. Must not change.
MIX_FIELDS = ["n", "share", "share_lo", "share_hi", "league_share", "win", "win_lo", "win_hi", "league_win"]
TEST_FIELDS = ["n", "matches", "top_dir", "top_share", "fav_pct", "q_mix", "p_mix", "p_win", "q_win",
               "best_dir", "gain_pp", "n_bp", "bp_wide", "bp_wide_q", "bp_body", "bp_body_q", "bp_T", "bp_T_q"]
PLAYER_FIELDS = ["gender", "server", "hand", "matches", "first", "last", "serves"]
DIRECTION_FIELDS = ["gender", "serve_num", "side", "is_break_point", "matchup", "direction",
                    "n", "low_n", "share", "share_lo", "share_hi", "win", "win_lo", "win_hi"]
TEST_STR = {"top_dir", "best_dir"}
TEST_INT = {"n", "matches", "n_bp"}
Q_FAMILIES = [("mix", "q_mix"), ("win", "q_win"), ("bp wide", "bp_wide_q"), ("bp body", "bp_body_q"),
              ("bp T", "bp_T_q")]

results = []  # (status, check, message)


def report(status, check, msg):
    results.append((status, check, msg))
    print(f"{status} [{check}] {msg}")


def rows(name):
    with open(DATA / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def header(name):
    with open(DATA / name, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def fnum(s):
    return float("nan") if s in ("", None) else float(s)


def isnan(x):
    return isinstance(x, float) and math.isnan(x)


def close(a, b):
    """Sign-aware float comparison: rel tol 1e-6 or abs tol 1e-9. None only matches None / NaN."""
    if a is None or isnan(a):
        return b is None
    if b is None or isinstance(b, bool) or not isinstance(b, (int, float)):
        return False
    return abs(a - b) <= max(1e-6 * max(abs(a), abs(b)), 1e-9)


def same(want, got):
    """want: the CSV-derived value (str, bool, int, float, NaN or None). got: the JSON value."""
    if want is None or isnan(want):
        return got is None
    if isinstance(want, bool):
        return isinstance(got, bool) and got == want
    if isinstance(want, str):
        return isinstance(got, str) and got == want
    if isinstance(want, int):
        return isinstance(got, int) and not isinstance(got, bool) and got == want
    return close(want, got)


def parse_cell(s):
    """CSV text to a typed value, for tables written as-is (league.*)."""
    if s == "":
        return float("nan")
    if s in ("True", "False"):
        return s == "True"
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    try:
        return float(s)
    except ValueError:
        return s


def reject_constant(name):
    raise ValueError(f"non-JSON constant {name!r} in the data file")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


# ---------------------------------------------------------------- check 0: site files

def site_started():
    return (SITE / "index.html").exists()


def missing(check, rel):
    """A missing file is a FAIL with --strict, or when it belongs to a site whose index.html exists."""
    in_site = rel.startswith(SITE_REL + "/")
    report("FAIL" if STRICT or (in_site and site_started()) else "WARN", check, f"{rel} does not exist yet")


def check_files():
    C = "0 files"
    bad = 0
    for name in ["index.html", "app.js", "styles.css", "data/serveiq.json"]:
        if not (SITE / name).exists():
            missing(C, f"{SITE_REL}/{name}")
            bad += 1
    if not bad:
        report("PASS", C, f"{SITE_REL}/index.html, app.js, styles.css and data/serveiq.json exist")


# ---------------------------------------------------------------- check 1: data

def recompute():
    """Every headline value, from the CSVs, with the csv module only."""
    h = {}
    players = rows("players.csv")
    h["players"] = len(players)
    h["men"] = sum(1 for r in players if r["gender"] == "m")
    h["women"] = sum(1 for r in players if r["gender"] == "w")
    h["serves_analysed"] = sum(int(r["n"]) for r in rows("league_cells.csv"))
    h["serves_listed"] = sum(int(r["serves"]) for r in players)

    tests = rows("player_tests.csv")
    t1 = [r for r in tests if r["serve_num"] == "1"]
    t2 = [r for r in tests if r["serve_num"] == "2"]
    for s, t in [("1", t1), ("2", t2)]:
        k = sum(1 for r in t if fnum(r["q_mix"]) < Q)
        h[f"mix_differs_n_s{s}"] = k
        h[f"mix_total_s{s}"] = len(t)
        h[f"mix_differs_share_s{s}"] = k / len(t)
    h["costly_players_s1"] = len({(r["gender"], r["server"]) for r in t1 if fnum(r["q_win"]) < Q})
    h["costly_sides_s1"] = sum(1 for r in t1 if fnum(r["q_win"]) < Q)
    h["costly_s2"] = sum(1 for r in t2 if fnum(r["q_win"]) < Q)
    h["bp_sig_tests"] = sum(1 for r in tests for d in DIRS if fnum(r[f"bp_{d}_q"]) < Q)
    h["bp_sig_patterns"] = sum(1 for r in tests if any(fnum(r[f"bp_{d}_q"]) < Q for d in DIRS))

    stab = rows("stability.csv")
    fav = [float(r["corr"]) for r in stab if r["metric"] == "guess"]
    edge = [float(r["corr"]) for r in stab if r["metric"] == "edge"]
    h.update(fav_r_min=min(fav), fav_r_max=max(fav), edge_r_min=min(edge), edge_r_max=max(edge))

    within = rows("baseline_within_server.csv")
    serial = rows("baseline_serial.csv")

    def w(g, s, q, fe):
        hit = [r for r in within if r["gender"] == g and r["serve_num"] == s and r["question"] == q
               and r["fixed_effect"] == fe]
        assert len(hit) == 1
        r = hit[0]
        return {"coef": float(r["coef_pp"]), "lo": float(r["lo_pp"]), "hi": float(r["hi_pp"])}

    for g in ["m", "w"]:
        for s in ["1", "2"]:
            fe1 = "server x side x matchup x break point"
            h[f"body_win_{g}{s}"] = w(g, s, "win body minus win wide", fe1)
            h[f"body_in_{g}{s}"] = w(g, s, "serve in body minus serve in wide", fe1)
            h[f"bp_wide_{g}{s}"] = w(g, s, "break_point: change in wide share", "match x server x side")
            h[f"bp_body_{g}{s}"] = w(g, s, "break_point: change in body share", "match x server x side")
            r = [x for x in serial if x["gender"] == g and x["serve_num"] == s][0]
            h[f"serial_{g}{s}"] = {"coef": float(r["diff_pp"]), "lo": float(r["lo_pp"]), "hi": float(r["hi_pp"])}

    mix = [r for r in rows("player_mix.csv") if r["gender"] == "m" and r["server"] == "Rafael Nadal"
           and r["serve_num"] == "1" and r["side"] == "deuce"]
    n = {(r["situation"], r["direction"]): int(r["n"]) for r in mix}
    share = {(r["situation"], r["direction"]): float(r["share"]) for r in mix}
    other_tot = sum(n[("all", d)] for d in DIRS) - sum(n[("bp", d)] for d in DIRS)
    h["nadal_deuce_wide_s1"] = {"other": (n[("all", "wide")] - n[("bp", "wide")]) / other_tot,
                                "bp": share[("bp", "wide")], "all": share[("all", "wide")]}
    fam = {"mix": sum(1 for r in tests if r["p_mix"] != ""), "win": sum(1 for r in tests if r["p_win"] != ""),
           "bp": sum(1 for r in tests for d in DIRS if r[f"bp_{d}_p"] != "")}
    return h, tests, fam


def expected_players():
    ps = rows("players.csv")
    ps.sort(key=lambda r: (r["gender"], -int(r["serves"])))  # stable, like the mergesort in the exporter
    out = []
    for r in ps:
        out.append([r["gender"], r["server"], r["hand"], int(r["matches"]), int(r["first"]), int(r["last"]),
                    int(r["serves"])])
    return out


def expected_tests(tests):
    groups = defaultdict(list)
    for r in tests:
        groups[(r["gender"], r["serve_num"], r["side"])].append(float(r["top_share"]))
    out = {}
    for r in tests:
        g = groups[(r["gender"], r["serve_num"], r["side"])]
        v = {}
        for f in TEST_FIELDS:
            if f == "fav_pct":
                # share of the other listed players (same gender, serve, side) with a lower top_share
                v[f] = sum(1 for x in g if x < float(r["top_share"])) / (len(g) - 1)
            elif f in TEST_STR:
                v[f] = r[f]
            elif f in TEST_INT:
                v[f] = int(r[f])
            else:
                v[f] = fnum(r[f])
        out[(f"{r['gender']}|{r['server']}|{r['serve_num']}", r["side"])] = v
    return out


def expected_mix(tests):
    """{key: {side: {sit: {dir: {field: value}}}}} with the export null rules applied."""
    G = {(r["gender"], r["server"], r["serve_num"], r["side"]): int(r["matches"]) for r in tests}
    out = {}
    for r in rows("player_mix.csv"):
        key = f"{r['gender']}|{r['server']}|{r['serve_num']}"
        few = G.get((r["gender"], r["server"], r["serve_num"], r["side"]), 0) < 2
        n = int(r["n"])
        v = {"n": n}
        for f in MIX_FIELDS[1:]:
            v[f] = fnum(r[f])
        if n == 0:
            for f in ["win", "win_lo", "win_hi", "share_lo", "share_hi"]:
                v[f] = None
        for est, lo, hi in [("share", "share_lo", "share_hi"), ("win", "win_lo", "win_hi")]:
            if v[est] is None or isnan(v[est]) or few:
                v[lo] = v[hi] = None
        out.setdefault(key, {}).setdefault(r["side"], {}).setdefault(r["situation"], {})[r["direction"]] = v
    for key, sides in out.items():
        for side, sits in sides.items():
            n_other = sum(sits["all"][d]["n"] - sits["bp"][d]["n"] for d in DIRS)
            sits["other"] = {}
            for d in DIRS:
                nd = sits["all"][d]["n"] - sits["bp"][d]["n"]
                o = {f: None for f in MIX_FIELDS}
                o["n"], o["share"] = nd, (nd / n_other if n_other else float("nan"))
                sits["other"][d] = o
    return out


class Diff:
    """Collects field mismatches for one block, prints the first few, then a PASS/FAIL line."""

    def __init__(self, check, block):
        self.check, self.block, self.n, self.bad, self.null_bounds = check, block, 0, 0, 0

    def cmp(self, where, field, want, got):
        self.n += 1
        if not same(want, got):
            self.bad += 1
            if self.bad <= 8:
                report("FAIL", self.check, f"{self.block} {where} {field}: CSV {want!r} vs JSON {got!r}")

    def fail(self, msg):
        self.bad += 1
        report("FAIL", self.check, f"{self.block} {msg}")

    def bounds(self, where, vals, triples):
        for est, lo, hi in triples:
            if vals.get(est) is None and (vals.get(lo) is not None or vals.get(hi) is not None):
                self.null_bounds += 1
                self.bad += 1
                if self.null_bounds <= 8:
                    report("FAIL", self.check, f"{self.block} {where}: CI bound present but {est} is null")

    def done(self):
        if self.bad > 8:
            report("FAIL", self.check, f"{self.block}: {self.bad} mismatches in total ({self.bad - 8} not shown)")
        if not self.bad:
            report("PASS", self.check, f"{self.block}: all {self.n} values match the CSV recomputation")


def check_fields(D, got, want):
    if got != want:
        D.fail(f"fields {got} differ from the schema {want}")
        return False
    return True


def check_tables(J, tests):
    C = "1 data"
    # players
    D = Diff(C, "players")
    P = J.get("players", {})
    if check_fields(D, P.get("fields"), PLAYER_FIELDS):
        want = expected_players()
        if len(P["rows"]) != len(want):
            D.fail(f"has {len(P['rows'])} rows, CSV {len(want)}")
        for i, (w, g) in enumerate(zip(want, P["rows"])):
            if len(g) != len(PLAYER_FIELDS):
                D.fail(f"row {i} has {len(g)} values")
                continue
            for f, a, b in zip(PLAYER_FIELDS, w, g):
                D.cmp(f"row {i} ({w[1]})", f, a, b)
    D.done()

    # tests
    D = Diff(C, "tests")
    T = J.get("tests", {})
    if check_fields(D, T.get("fields"), TEST_FIELDS):
        want = expected_tests(tests)
        got_keys = {(k, s) for k, sides in T["data"].items() for s in sides}
        if got_keys != set(want):
            D.fail(f"keys differ: {len(set(want) - got_keys)} missing, {len(got_keys - set(want))} extra")
        for (key, side), w in want.items():
            g = T["data"].get(key, {}).get(side)
            if g is None or len(g) != len(TEST_FIELDS):
                D.fail(f"{key} {side} missing or wrong length")
                continue
            for i, f in enumerate(TEST_FIELDS):
                D.cmp(f"{key} {side}", f, w[f], g[i])
        # (3) q < 0.05 decisions per family
        for fam, col in Q_FAMILIES:
            qi = TEST_FIELDS.index(col)
            n_csv = n_json = mism = 0
            for (key, side), w in want.items():
                g = T["data"].get(key, {}).get(side)
                if g is None:
                    continue
                dc = (not isnan(w[col])) and w[col] < Q
                gj = g[qi]
                dj = isinstance(gj, (int, float)) and not isinstance(gj, bool) and gj < Q
                n_csv += dc
                n_json += dj
                if dc != dj:
                    mism += 1
                    if mism <= 3:
                        report("FAIL", C, f"q decision ({fam}) {key} {side}: CSV q {w[col]!r} vs JSON q {gj!r}")
            if mism:
                D.bad += 1
                report("FAIL", C, f"q < {Q} decisions ({fam}): {mism} differ; CSV {n_csv} significant, JSON {n_json}")
            else:
                report("PASS", C, f"q < {Q} decisions ({fam}): JSON and CSV agree ({n_csv} significant)")
    D.done()

    # mix
    D = Diff(C, "mix")
    M = J.get("mix", {})
    if check_fields(D, M.get("fields"), MIX_FIELDS):
        want = expected_mix(tests)
        if set(M["data"]) != set(want):
            D.fail(f"keys differ: {len(set(want) - set(M['data']))} missing, {len(set(M['data']) - set(want))} extra")
        triples = [("share", "share_lo", "share_hi"), ("win", "win_lo", "win_hi")]
        for key, sides in want.items():
            gs = M["data"].get(key, {})
            if set(gs) != set(sides):
                D.fail(f"{key}: sides {sorted(gs)}")
            for side, sits in sides.items():
                for sit, dirs in sits.items():
                    gd = gs.get(side, {}).get(sit, {})
                    if set(gd) != set(DIRS):
                        D.fail(f"{key} {side} {sit}: directions {sorted(gd)}")
                        continue
                    for d, w in dirs.items():
                        g = gd[d]
                        if len(g) != len(MIX_FIELDS):
                            D.fail(f"{key} {side} {sit} {d}: {len(g)} values")
                            continue
                        gv = dict(zip(MIX_FIELDS, g))
                        for f in MIX_FIELDS:
                            D.cmp(f"{key} {side} {sit} {d}", f, w[f], gv[f])
                        D.bounds(f"{key} {side} {sit} {d}", gv, triples)
    D.done()

    # league tables, written as-is from the CSVs
    L = J.get("league", {})
    for blk, fn, fields in [("directions", "baseline_directions.csv", DIRECTION_FIELDS),
                            ("within", "baseline_within_server.csv", header("baseline_within_server.csv")),
                            ("serial", "baseline_serial.csv", header("baseline_serial.csv")),
                            ("stability", "stability.csv", header("stability.csv"))]:
        D = Diff(C, f"league.{blk}")
        t = L.get(blk, {})
        if check_fields(D, t.get("fields"), fields):
            src = rows(fn)
            if len(t["rows"]) != len(src):
                D.fail(f"has {len(t['rows'])} rows, CSV {len(src)}")
            triples = [(a, b, c) for a, b, c in [("share", "share_lo", "share_hi"), ("win", "win_lo", "win_hi"),
                                                 ("coef_pp", "lo_pp", "hi_pp"), ("diff_pp", "lo_pp", "hi_pp")]
                       if a in fields]
            for i, (r, g) in enumerate(zip(src, t["rows"])):
                if len(g) != len(fields):
                    D.fail(f"row {i}: {len(g)} values")
                    continue
                for f, gv in zip(fields, g):
                    D.cmp(f"row {i}", f, parse_cell(r[f]), gv)
                D.bounds(f"row {i}", dict(zip(fields, g)), triples)
        D.done()


def check_data(J):
    C = "1 data"
    mine, tests, fam = recompute()
    H = J.get("headline", {})
    bad = 0
    for k, v in mine.items():
        if k not in H:
            report("FAIL", C, f"headline key {k} missing from JSON")
            bad += 1
            continue
        if isinstance(v, dict):
            if not isinstance(H[k], dict) or set(H[k]) != set(v):
                report("FAIL", C, f"headline {k}: JSON {H[k]!r} does not have the keys {sorted(v)}")
                bad += 1
                continue
            for sk, sv in v.items():
                if not same(sv, H[k][sk]):
                    report("FAIL", C, f"headline {k}.{sk}: CSV {sv!r} vs JSON {H[k][sk]!r}")
                    bad += 1
        elif not same(v, H[k]):
            report("FAIL", C, f"headline {k}: CSV {v!r} vs JSON {H[k]!r}")
            bad += 1
    extra = set(H) - set(mine)
    if extra:
        report("FAIL", C, f"JSON headline keys not recomputed by the checker: {sorted(extra)}")
        bad += 1
    if not bad:
        report("PASS", C, f"{len(mine)} headline values recomputed from CSV match the JSON (sign-aware)")

    # verified numbers, formatted the way the copy prints them
    def pct0(x):
        return f"{100 * x:.0f}"

    def f(x, d):
        return f"{x:.{d}f}"

    expect = [
        ("players 447 / men 249 / women 198", (mine["players"], mine["men"], mine["women"]), (447, 249, 198)),
        ("serves analysed 2,473,821", f"{mine['serves_analysed']:,}", "2,473,821"),
        ("serves of listed players 2,156,772", f"{mine['serves_listed']:,}", "2,156,772"),
        ("1st-serve mix differs 604 of 894 (68%)",
         (mine["mix_differs_n_s1"], mine["mix_total_s1"], pct0(mine["mix_differs_share_s1"])), (604, 894, "68")),
        ("2nd-serve mix differs 61.7%", f(100 * mine["mix_differs_share_s2"], 1), "61.7"),
        ("24 players, 26 player-sides, 0 on 2nd serves",
         (mine["costly_players_s1"], mine["costly_sides_s1"], mine["costly_s2"]), (24, 26, 0)),
        ("84 significant break-point tests on 62 patterns",
         (mine["bp_sig_tests"], mine["bp_sig_patterns"]), (84, 62)),
        ("favorite-target share r 0.57 to 0.69", (f(mine["fav_r_min"], 2), f(mine["fav_r_max"], 2)), ("0.57", "0.69")),
        ("T-minus-wide edge r -0.02 to 0.17", (f(mine["edge_r_min"], 2), f(mine["edge_r_max"], 2)), ("-0.02", "0.17")),
        ("body minus wide won, men -4.39 (CI -4.87 to -3.90)",
         tuple(f(mine["body_win_m1"][k], 2) for k in ["coef", "lo", "hi"]), ("-4.39", "-4.87", "-3.90")),
        ("body minus wide won, women -4.09", f(mine["body_win_w1"]["coef"], 2), "-4.09"),
        ("body minus wide in, men +9.9 / women +8.5",
         (f"{mine['body_in_m1']['coef']:+.1f}", f"{mine['body_in_w1']['coef']:+.1f}"), ("+9.9", "+8.5")),
        ("2nd serve body minus wide won, men -0.60 / women -0.76",
         (f(mine["body_win_m2"]["coef"], 2), f(mine["body_win_w2"]["coef"], 2)), ("-0.60", "-0.76")),
        ("break-point wide share, men 1st +1.71 (CI 1.01 to 2.40)",
         (f"{mine['bp_wide_m1']['coef']:+.2f}", f(mine["bp_wide_m1"]["lo"], 2), f(mine["bp_wide_m1"]["hi"], 2)),
         ("+1.71", "1.01", "2.40")),
        ("break-point wide share, women 1st +1.19", f"{mine['bp_wide_w1']['coef']:+.2f}", "+1.19"),
        ("break-point wide share, 2nd serves within 0.7 to 0.85",
         0.7 <= min(mine["bp_wide_m2"]["coef"], mine["bp_wide_w2"]["coef"])
         and f(max(mine["bp_wide_m2"]["coef"], mine["bp_wide_w2"]["coef"]), 2) == "0.85", True),
        ("break-point body share, men 1st -1.14", f(mine["bp_body_m1"]["coef"], 2), "-1.14"),
        ("serial, men 1st -1.60 (CI -2.70 to -0.50)",
         tuple(f(mine["serial_m1"][k], 2) for k in ["coef", "lo", "hi"]), ("-1.60", "-2.70", "-0.50")),
        ("serial, women 1st -4.89", f(mine["serial_w1"]["coef"], 2), "-4.89"),
        ("Nadal deuce wide 1st: other 30.0%, bp 45.0%, all 30.5%",
         tuple(f(100 * mine["nadal_deuce_wide_s1"][k], 1) for k in ["other", "bp", "all"]), ("30.0", "45.0", "30.5")),
        ("test families 1,788 / 1,777 / 5,364 (CSV)", (fam["mix"], fam["win"], fam["bp"]), (1788, 1777, 5364)),
    ]
    meta = J.get("meta", {})
    mt = meta.get("tests", {})
    expect.append(("test families in meta", (mt.get("mix"), mt.get("win"), mt.get("bp")), (1788, 1777, 5364)))
    for label, got, want in expect:
        report("PASS" if got == want else "FAIL", C, f"verified number: {label}" + ("" if got == want else f" (got {got})"))

    # meta
    for name, digest in meta.get("csv_sha256", {}).items():
        p = DATA / name
        now = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        if now != digest:
            report("FAIL", C, f"meta.csv_sha256 for {name} does not match the file (re-run src/export_site.py)")
    csvs = sorted(p.name for p in DATA.glob("*.csv"))
    if sorted(meta.get("csv_sha256", {})) != csvs:
        report("FAIL", C, f"meta.csv_sha256 covers {sorted(meta.get('csv_sha256', {}))}, app/data has {csvs}")
    else:
        report("PASS", C, f"meta.csv_sha256 matches all {len(csvs)} CSV files")
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if head != meta.get("git_sha"):
            report("WARN", C, f"meta.git_sha {meta.get('git_sha')} is not HEAD {head} (exported before a commit)")
    except Exception:
        report("WARN", C, "could not read git HEAD")
    th = meta.get("thresholds")
    report("PASS" if th == THRESHOLDS else "FAIL", C, f"meta.thresholds {th}")
    counts = (meta.get("players"), meta.get("men"), meta.get("women"))
    want_c = (mine["players"], mine["men"], mine["women"])
    report("PASS" if counts == want_c else "FAIL", C, f"meta players / men / women {counts}")
    consts = {"serves_total": SERVES_TOTAL, "tiebreak_share": TIEBREAK_SHARE, "nodir_share": NODIR_SHARE}
    got_c = {k: meta.get(k) for k in consts}
    ok = all(same(v, got_c[k]) for k, v in consts.items())
    report("PASS" if ok else "FAIL", C, f"meta named constants {got_c}" + ("" if ok else f" (want {consts})"))
    if not mine["serves_analysed"] < SERVES_TOTAL:
        report("FAIL", C, f"serves_analysed {mine['serves_analysed']} is not below SERVES_TOTAL {SERVES_TOTAL}")

    check_tables(J, tests)


# ---------------------------------------------------------------- masking helpers

def blank(text, a, b):
    """Replace text[a:b] with spaces, keeping newlines so line numbers stay right."""
    return text[:a] + re.sub(r"[^\n]", " ", text[a:b]) + text[b:]


class Regions(HTMLParser):
    """Find [start, end) offsets of elements matching a predicate, plus text nodes with offsets."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, text, pred):
        super().__init__(convert_charrefs=True)
        self.text, self.pred = text, pred
        self.line_off = [0]
        for m in re.finditer("\n", text):
            self.line_off.append(m.end())
        self.stack = []      # (tag, start_offset, depth) of open matching elements
        self.regions = []
        self.texts = []      # (offset, data)

    def off(self):
        line, col = self.getpos()
        return self.line_off[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self.off()
        end = start + len(self.get_starttag_text() or "")
        for s in self.stack:
            if s[0] == tag:
                s[2] += 1
        if self.pred(tag, dict(attrs)):
            if tag in self.VOID:
                self.regions.append((start, end))
            else:
                self.stack.append([tag, start, 0])

    def handle_startendtag(self, tag, attrs):
        start = self.off()
        if self.pred(tag, dict(attrs)):
            self.regions.append((start, start + len(self.get_starttag_text() or "")))

    def handle_endtag(self, tag):
        start = self.off()
        end = self.text.find(">", start) + 1
        for s in list(self.stack):
            if s[0] == tag:
                if s[2] == 0:
                    self.regions.append((s[1], end))
                    self.stack.remove(s)
                else:
                    s[2] -= 1

    def handle_data(self, data):
        self.texts.append((self.off(), data))


def html_regions(text, pred):
    p = Regions(text, pred)
    p.feed(text)
    p.close()
    return p.regions, p.texts


def is_tex(tag, attrs):
    return ("tex" in (attrs.get("class") or "").split()
            or (tag == "script" and (attrs.get("type") or "").startswith("math/tex")))


def mask_math_delims(text):
    for pat in [r"\$\$.*?\$\$", r"\\\(.*?\\\)", r"\\\[.*?\\\]"]:
        for m in list(re.finditer(pat, text, re.S)):
            text = blank(text, m.start(), m.end())
    return text


def mask_html(text):
    regs, _ = html_regions(text, is_tex)
    for a, b in regs:
        text = blank(text, a, b)
    for m in list(re.finditer(r"""data-tex\s*=\s*("[^"]*"|'[^']*')""", text)):
        text = blank(text, m.start(1), m.end(1))
    return mask_math_delims(text)


def mask_js(text):
    for m in list(re.finditer(r"String\.raw\s*`", text)):
        i = m.end()
        while i < len(text) and text[i] != "`":
            i += 2 if text[i] == "\\" else 1
        text = blank(text, m.start(), i + 1)
    for m in list(re.finditer(r"\bFORMULAS\s*[=:]\s*\{", text)):
        i, depth, quote = m.end(), 1, None
        while i < len(text) and depth:
            c = text[i]
            if quote:
                if c == "\\":
                    i += 1
                elif c == quote:
                    quote = None
            elif c in "\"'`":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        text = blank(text, m.start(), i)
    return text


def mask_md(text):
    for pat in [r"```math.*?```", r"\$\$.*?\$\$", r"(?<![\\$])\$(?=\S)[^$\n]+?(?<=\S)\$"]:
        for m in list(re.finditer(pat, text, re.S)):
            text = blank(text, m.start(), m.end())
    return text


def docstring_lines(text):
    """(start_line, end_line) of every module / class / function docstring."""
    out = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                out.add(first.lineno)
    return out


def mask_py(text, comments=True, docstrings=True):
    """Keep only string literals (not raw strings), plus comments and docstrings if asked."""
    out = re.sub(r"[^\n]", " ", text)
    lines = text.splitlines(True)
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln))
    doc = set() if docstrings else docstring_lines(text)
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        keep = False
        if tok.type == tokenize.COMMENT:
            keep = comments
        elif tok.type == tokenize.STRING and "r" not in re.match(r"[A-Za-z]*", tok.string).group().lower():
            keep = tok.start[0] not in doc
        if keep:
            a = offs[tok.start[0] - 1] + tok.start[1]
            b = offs[tok.end[0] - 1] + tok.end[1]
            out = out[:a] + text[a:b] + out[b:]
    return out


def mask_braces(text):
    """Blank {...} fields (f-string / str.format / plotly %{...}), nested braces included."""
    i = 0
    while True:
        a = text.find("{", i)
        if a < 0:
            return text
        depth, b = 0, a
        while b < len(text):
            if text[b] == "{":
                depth += 1
            elif text[b] == "}":
                depth -= 1
                if depth == 0:
                    break
            b += 1
        start = a - 1 if a > 0 and text[a - 1] == "%" else a
        text = blank(text, start, b + 1)
        i = b + 1


def mask_py_numbers(text):
    """Displayed text of app/streamlit_app.py: string literals only, without code inside them."""
    text = mask_py(text, comments=False, docstrings=False)
    text = mask_braces(text)
    for pat in [r"#[0-9A-Fa-f]{3,8}\b", r"rgba?\([^)]*\)"]:
        for m in list(re.finditer(pat, text)):
            text = blank(text, m.start(), m.end())
    return text


# ---------------------------------------------------------------- check 2: copy bans

BANNED_CHARS = [
    ("em dash", r"—"), ("en dash", r"–"), ("minus sign", r"−"), ("multiplication sign", r"×"),
    ("middle dot", r"·"), ("arrow", r"[←-⇿⟰-⟿⤀-⥿]"),
    ("emoji/pictograph", r"[⌀-⏿☀-➿⬀-⯿️‍\U0001f000-\U0001faff]"),
    ("tilde used as 'about'", r"~\s*\d"),
    ("escaped banned character", r"(?i)\\(?:u\{?|x)?0*(?:2014|2013|2212|d7|b7|21[9a-f][0-9a-f])(?![0-9a-f])"),
    ("HTML entity for a banned character", r"(?i)&(?:mdash|ndash|minus|times|middot|rarr|larr|harr|uarr|darr|#8212|#8211|#8722|#215|#183|#x2014|#x2013|#x2212|#xd7|#xb7);"),
]
# case-insensitive substrings ("not only" also covers "Not only"). "shuffled order" of a server's own serves is a
# legitimate description of the serial test, so only label-shuffling claims are banned.
BANNED_PHRASES = [
    "guess rate", "honest headline", "Honest uncertainty", "No cherry-picking", "Not fooling ourselves",
    "The real leak", "plain English", "dive into", "unlock", "The takeaway",
    "shuffling labels", "shuffled labels", "shuffling directions", "calibrat", "checked by hand", "by hand", "re-parse", "reparse", "costs nothing", "not only",
]
# (pattern, label, flags)
BANNED_REGEX = [
    (r"[?]\s*(Yes|No)\b", "'X? Yes/No' heading", 0),
    (r"\bhonest(ly)?\b", "honest/honestly", re.I),
    (r"\bgame[- ]changer\b", "game-changer", re.I),
    (r"\bnot only\b", "Not only ... but", re.I),
    (r"\bmeasurable cost\b", "claim: use the tested wording", re.I),
    (r"\bcosts? nothing\b", "claim: say 'little evidence of a cost'", re.I),
    (r"\bguess[ _-]?rate\b", "rename to favorite-target share", re.I),
]
BRITISH = r"\b(favourite\w*|colour\w*|recognis\w*|analys(e|ed|es|ing)|behaviour\w*|centre\w*|organis\w*|optimis\w*|randomis\w*)\b"


def copy_files():
    return [(f"{SITE_REL}/index.html", SITE / "index.html", mask_html),
            (f"{SITE_REL}/app.js", SITE / "app.js", mask_js),
            (f"{SITE_REL}/styles.css", SITE / "styles.css", lambda t: t),
            ("README.md", ROOT / "README.md", mask_md),
            ("docs/SUBMISSION.md", ROOT / "docs" / "SUBMISSION.md", mask_md),
            ("app/streamlit_app.py", ROOT / "app" / "streamlit_app.py", mask_py)]


def check_copy():
    C = "2 copy"
    for rel, p, masker in copy_files():
        if not p.exists():
            missing(C, rel)
            continue
        text = masker(p.read_text(encoding="utf-8"))
        hits = []
        for i, line in enumerate(text.splitlines(), 1):
            for name, pat in BANNED_CHARS:
                for m in re.finditer(pat, line):
                    hits.append((i, f"{name} {m.group()!r}"))
            seen = set()  # start offsets already reported on this line (phrase and regex overlap)
            for ph in BANNED_PHRASES:
                pat = (r"\bunlock" if ph == "unlock" else re.escape(ph)) + r"\w*"
                for m in re.finditer(pat, line, re.I):
                    if m.start() not in seen:
                        seen.add(m.start())
                        hits.append((i, f"phrase '{ph}': {m.group()!r}"))
            for pat, why, flags in BANNED_REGEX:
                for m in re.finditer(pat, line, flags):
                    if m.start() not in seen:
                        seen.add(m.start())
                        hits.append((i, f"{why}: {m.group()!r}"))
            for m in re.finditer(BRITISH, line, re.I):
                report("WARN", C, f"{rel}:{i}: British spelling {m.group()!r}")
        for i, msg in hits:
            report("FAIL", C, f"{rel}:{i}: {msg}")
        if not hits:
            report("PASS", C, f"{rel}: no banned characters or phrases")


# ---------------------------------------------------------------- check 3: numbers in site prose

def check_html_numbers():
    C = "3 html-numbers"
    p = SITE / "index.html"
    rel = f"{SITE_REL}/index.html"
    if not p.exists():
        missing(C, rel)
        return
    text = p.read_text(encoding="utf-8")
    skip, texts = html_regions(text, lambda tag, a: is_tex(tag, a) or a.get("id") == "license"
                               or tag in ("script", "style"))
    line_of = lambda off: text.count("\n", 0, off) + 1
    bad = warn = 0
    for off, data in texts:
        if any(a <= off < b for a, b in skip):
            continue
        data_m = mask_math_delims(data)
        for m in re.finditer(r"\d+(?:\.\d+)?\s?%|\d+\.\d+", data_m):
            ln = line_of(off) + data_m.count("\n", 0, m.start())
            report("FAIL", C, f"{rel}:{ln}: number in prose {m.group()!r}")
            bad += 1
        for m in re.finditer(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?![\w.%])|(?<![\w.,])\d{2,}(?![\w.,%])", data_m):
            v = int(m.group().replace(",", ""))
            if v > 10 and not (len(m.group()) == 4 and 1990 <= v <= 2030):
                ln = line_of(off) + data_m.count("\n", 0, m.start())
                report("WARN", C, f"{rel}:{ln}: integer in prose {m.group()!r} (bind it to the JSON?)")
                warn += 1
    if not bad:
        report("PASS", C, f"{rel}: no percentages or decimals in prose ({warn} integer warnings)")


# ---------------------------------------------------------------- check 4: keys read by the site

def check_keys(J):
    C = "4 keys"
    H, meta = J.get("headline", {}), J.get("meta", {})
    refs = []  # (file, line, top, sub, obj)
    js = SITE / "app.js"
    js_rel, html_rel = f"{SITE_REL}/app.js", f"{SITE_REL}/index.html"
    if js.exists():
        for i, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"\b(?:headline|H)\.([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*)|\[\s*[\"'`]([^\"'`]+)[\"'`]\s*\])?", line):
                refs.append((js_rel, i, m.group(1), m.group(2) or m.group(3), "headline"))
            for m in re.finditer(r"\b(?:headline|H)\[\s*[\"'`]([^\"'`]+)[\"'`]\s*\](?:\.([A-Za-z_]\w*)|\[\s*[\"'`]([^\"'`]+)[\"'`]\s*\])?", line):
                refs.append((js_rel, i, m.group(1), m.group(2) or m.group(3), "headline"))
            for m in re.finditer(r"\bmeta\.([A-Za-z_]\w*)", line):
                refs.append((js_rel, i, m.group(1), None, "meta"))
    else:
        missing(C, js_rel)
    html = SITE / "index.html"
    if html.exists():
        for i, line in enumerate(html.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"""data-(?:h|headline)\s*=\s*["']([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?["']""", line):
                refs.append((html_rel, i, m.group(1), m.group(2), "headline"))
    bad = 0
    for f, i, top, sub, obj in refs:
        src = H if obj == "headline" else meta
        if top not in src:
            report("FAIL", C, f"{f}:{i}: {obj}.{top} is not in the JSON")
            bad += 1
        elif sub and isinstance(src[top], dict) and sub not in src[top]:
            report("FAIL", C, f"{f}:{i}: {obj}.{top}.{sub} is not in the JSON")
            bad += 1
    if refs and not bad:
        report("PASS", C, f"all {len(refs)} headline/meta references resolve")
    elif not refs and js.exists():
        report("WARN", C, f"no headline./H./meta. references found in {js_rel} or data-h in {html_rel}")


# ---------------------------------------------------------------- check 5: numbers in README / SUBMISSION / app

VERSION_STRINGS = ["3.9.6", "3.9", "3.12", "3.13", "2.0.2", "1.13.1", "1.50.0", "7.1.0", "4.0", "2.3.3", "21.0.0"]
PP_KEYS = ("body_win_", "body_in_", "bp_wide_", "bp_body_", "serial_")      # percentage points: .1f, .2f
SHARE_KEYS = {"mix_differs_share_s1": (0, 1), "mix_differs_share_s2": (0, 1),  # percent, these decimals
              "nadal_deuce_wide_s1": (1,)}
R_KEYS = ("fav_r_min", "fav_r_max", "edge_r_min", "edge_r_max")              # correlations: .2f


def allowed_numbers(mine, fam):
    """Explicitly named values only. Returns (plain, signed, contextual).
    plain:  {token: name} for unsigned tokens (absolute value of signed quantities)
    signed: {token: name} for tokens written with a sign (+9.9, -0.02)
    contextual: [(token, name, predicate(line, start, end))] allowed only in that context."""
    plain, signed = {}, {}

    def add(v, name, decimals, pct=False):
        x = 100 * v if pct else v
        for d in decimals:
            s = f"{x:+.{d}f}"
            signed.setdefault(s, name)
            plain.setdefault(s[1:], name)

    def add_int(v, name):
        plain.setdefault(str(v), name)
        plain.setdefault(f"{v:,}", name)

    for k, v in mine.items():
        if isinstance(v, dict):
            for sk, sv in v.items():
                if k.startswith(PP_KEYS):
                    add(sv, f"headline.{k}.{sk}", (1, 2))
                elif k in SHARE_KEYS:
                    add(sv, f"headline.{k}.{sk}", SHARE_KEYS[k], pct=True)
                else:
                    raise KeyError(f"headline key {k} has no named display precision")
        elif isinstance(v, int):
            add_int(v, f"headline.{k}")
        elif k in SHARE_KEYS:
            add(v, f"headline.{k}", SHARE_KEYS[k], pct=True)
        elif k in R_KEYS:
            add(v, f"headline.{k}", (2,))
        else:
            raise KeyError(f"headline key {k} has no named display precision")
    for k, v in THRESHOLDS.items():
        if k == "min_dir_n":
            continue  # contextual, below
        if isinstance(v, int):
            add_int(v, f"threshold {k}")
        else:
            plain.setdefault(f"{v}", f"threshold {k}")
    for k, v in fam.items():
        add_int(v, f"test family size ({k})")
    add_int(SERVES_TOTAL, "SERVES_TOTAL")
    for y in range(1974, 2031):
        plain.setdefault(str(y), "year")
    for v in VERSION_STRINGS:
        plain.setdefault(v, "version string")
    for v in ["99.0", "99.6"]:
        plain.setdefault(v, "reconciliation range")
    plain.setdefault("95", "95% CI")
    plain.setdefault("0.05", "q threshold")

    def after(pat):
        return lambda line, a, b: re.match(pat, line[b:]) is not None

    def near(word, before=40, after_=40, pct=True):
        def pred(line, a, b):
            if pct and not re.match(r"\s?%", line[b:]):
                return False
            return word in line[max(0, a - before):b + after_].lower()
        return pred

    def near_before(word, chars=40):
        def pred(line, a, b):
            return re.match(r"\s?%", line[b:]) is not None and word in line[max(0, a - chars):a].lower()
        return pred

    contextual = [
        (f"{SERVES_TOTAL / 1e6:.2f}", "SERVES_TOTAL in millions", after(r"\s+million")),
        (f"{100 * TIEBREAK_SHARE:.0f}", "TIEBREAK_SHARE percent", near_before("tiebreak")),
        (f"{100 * NODIR_SHARE:.1f}", "NODIR_SHARE percent", near("direction")),
        (str(THRESHOLDS["min_dir_n"]), "threshold min_dir_n", near("serves", pct=False)),
        (str(THRESHOLDS["min_dir_n"]), "threshold min_dir_n", near("times", pct=False)),
        ("33", "even three-way split (100/3), always a percent", after(r"\s?%")),
        ("1.5", "app/data size", after(r"\s?MB\b")),
        ("5", "FDR percent", after(r"\s?%")),
    ]
    # integers 1 to 4 (counts, list numbers), but never as a percentage: "3%" is only the tiebreak share
    contextual += [(str(i), "small integer", lambda line, a, b: not re.match(r"\s?%", line[b:])) for i in range(1, 5)]
    return plain, signed, contextual


def doc_texts():
    out = []
    for rel in ["README.md", "docs/SUBMISSION.md"]:
        p = ROOT / rel
        out.append((rel, p, lambda t: mask_md(t)))
    out.append(("app/streamlit_app.py", ROOT / "app" / "streamlit_app.py", mask_py_numbers))
    return out


def check_doc_numbers(mine, fam):
    C = "5 doc-numbers"
    plain, signed, contextual = allowed_numbers(mine, fam)
    for rel, p, masker in doc_texts():
        if not p.exists():
            missing(C, rel)
            continue
        text = masker(p.read_text(encoding="utf-8"))
        for pat in [r"(?i)equations?\s+\(\d+\)(?:\s*(?:,|and|to)\s*\(\d+\))*", r"<!--.*?-->", r"https?://[^\s)>\]\"']+",
                    r"\]\([^)]*\)", r"<[^>\n]+>"]:
            for m in list(re.finditer(pat, text, re.S)):
                text = blank(text, m.start(), m.end())
        bad = 0
        for i, line in enumerate(text.splitlines(), 1):
            for m in re.finditer(r"(?<![\w.])([-+−]?)(\d{1,3}(?:,\d{3})+|\d+)((?:\.\d+)*)", line):
                tail = line[m.end():m.end() + 2]
                if re.match(r"(st|nd|rd|th)\b", tail):
                    continue
                sign = m.group(1).replace("−", "-")
                tok = m.group(2) + m.group(3)
                a, b = m.start(2), m.end()
                if sign:
                    ok = (sign + tok) in signed or (sign + tok.replace(",", "")) in signed
                else:
                    ok = tok in plain
                if not ok:
                    ok = not sign and any(tok == t and pred(line, a, b) for t, _, pred in contextual)
                if not ok:
                    hint = ""
                    if sign and tok in plain:
                        hint = f" (the sign does not match {plain[tok]})"
                    report("FAIL", C, f"{rel}:{i}: number {m.group()!r} is not a named headline value, "
                                      f"threshold or allowed constant{hint}")
                    bad += 1
        if not bad:
            report("PASS", C, f"{rel}: every number is in the named allowed set")


# ---------------------------------------------------------------- main

def main():
    print(f"site: {SITE_REL}   data: {JSON_PATH}")
    check_files()
    J = None
    if not JSON_PATH.exists():
        report("FAIL", "1 data", f"{JSON_PATH} missing: run src/export_site.py")
    else:
        try:
            J = load_json(JSON_PATH)
        except ValueError as e:
            report("FAIL", "1 data", f"{JSON_PATH} is not strict JSON: {e}")
        if J is not None:
            try:
                check_data(J)
            except (KeyError, TypeError, IndexError, AttributeError, ValueError) as e:
                report("FAIL", "1 data", f"JSON does not have the expected structure: {type(e).__name__} {e}")
    check_copy()
    check_html_numbers()
    if J is not None:
        check_keys(J)
    mine, _, fam = recompute()
    check_doc_numbers(mine, fam)
    n = {s: sum(1 for r in results if r[0] == s) for s in ["PASS", "FAIL", "WARN"]}
    print(f"\nSUMMARY: {n['PASS']} PASS, {n['FAIL']} FAIL, {n['WARN']} WARN")
    sys.exit(1 if n["FAIL"] else 0)


if __name__ == "__main__":
    main()
