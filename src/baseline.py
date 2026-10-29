"""League-wide baseline: serve direction mix and points won, by situation, with 95% CIs.

Situation cell = gender x serve number x side x break point x handedness matchup.
Within each cell, for each direction (wide / body / T):
  share = how often the server picks it
  win   = share of those points the server wins (for 1st serves this includes points
          where the 1st serve was a fault: the payoff used by Walker & Wooders 2001)

Inference: serves are not independent. The same server repeats habits, and the same
returner faces many serves (e.g. Nadal returns ~27% of men's righty-vs-lefty points).
All CIs and tests are two-way cluster-robust by server AND returner (Cameron, Gelbach &
Miller 2011), with t(G-1) critical values, G = the smaller number of clusters. If the
two-way variance is not positive, the larger one-way variance is used instead.

Outputs (output/):
  baseline_cells.csv          one row per situation cell
  baseline_directions.csv     one row per cell x direction, with CIs
  baseline_within_server.csv  within-server comparisons (fixed effects)
  baseline_serial.csv         do servers repeat or switch direction more than chance?

Excluded: tiebreak points, serves with unknown direction or handedness.

Usage:
    python src/baseline.py
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
CELL = ["gender", "serve_num", "side", "is_break_point", "matchup"]
DIRS = ["wide", "body", "T"]
LOW_N = 100  # direction rows with fewer serves are flagged as too thin to read


# ---------- two-way cluster-robust variance ----------

def _cluster_meat(scores, codes):
    """G/(G-1) * sum over clusters of (summed score)(summed score)'."""
    s = pd.DataFrame(scores).groupby(codes).sum().to_numpy()
    g = len(s)
    return g / (g - 1) * s.T @ s, g


def twoway_meat(scores, srv, ret):
    """Two-way (server, returner) clustered 'meat' matrix and the cluster count for t(G-1).

    scores: (N, k) array of per-serve score contributions; srv, ret: labels per serve.
    """
    scores = np.asarray(scores, float).reshape(len(srv), -1)
    s_codes, _ = pd.factorize(np.asarray(srv))
    r_codes, _ = pd.factorize(np.asarray(ret))
    m_s, g_s = _cluster_meat(scores, s_codes)
    m_r, g_r = _cluster_meat(scores, r_codes)
    m_sr, _ = _cluster_meat(scores, s_codes.astype(np.int64) * (r_codes.max() + 1) + r_codes)
    meat = m_s + m_r - m_sr
    if np.linalg.eigvalsh(meat).min() < 0:  # rare in big cells; fall back conservatively
        meat = m_s if np.trace(m_s) >= np.trace(m_r) else m_r
    return meat, min(g_s, g_r)


def ratio_ci(y, n, srv, ret, clip=True):
    """Estimate sum(y)/sum(n) with a two-way clustered 95% CI."""
    y, n = np.asarray(y, float), np.asarray(n, float)
    total = n.sum()
    p = y.sum() / total
    meat, g = twoway_meat((y - p * n) / total, srv, ret)
    if g < 2:
        return p, np.nan, np.nan
    half = stats.t.ppf(0.975, g - 1) * np.sqrt(meat[0, 0])
    lo, hi = p - half, p + half
    return (p, max(0.0, lo), min(1.0, hi)) if clip else (p, lo, hi)


def win_tests(d):
    """Minimax says win rates are equal across directions. Returns the clustered Wald p for
    'all used directions equal', plus wide minus T (pp) and its p (the Walker & Wooders contrast)."""
    used = [x for x in DIRS if (d["direction"] == x).any()]
    y = d["srv_won"].to_numpy(float)
    p, cols = [], []
    for x in used:
        m = (d["direction"] == x).to_numpy()
        px = y[m].mean()
        p.append(px)
        cols.append(np.where(m, (y - px) / m.sum(), 0.0))
    p = np.array(p)
    meat, g = twoway_meat(np.column_stack(cols), d["server"], d["returner"])
    out = {"wald_p": np.nan, "wide_minus_T_pp": np.nan, "wide_minus_T_p": np.nan}
    if len(used) >= 2 and g >= 2:
        R = np.array([[1.0 if j == 0 else (-1.0 if j == i else 0.0) for j in range(len(used))]
                      for i in range(1, len(used))])
        diff = R @ p
        q = len(diff)
        w = diff @ np.linalg.pinv(R @ meat @ R.T) @ diff
        out["wald_p"] = stats.f.sf(w / q, q, g - 1)
    if "wide" in used and "T" in used:
        i, j = used.index("wide"), used.index("T")
        se = np.sqrt(meat[i, i] + meat[j, j] - 2 * meat[i, j])
        out["wide_minus_T_pp"] = 100 * (p[i] - p[j])
        out["wide_minus_T_p"] = 2 * stats.t.sf(abs(p[i] - p[j]) / se, g - 1) if se > 0 else np.nan
    return out


# ---------- pooled cells ----------

def load():
    s = pd.read_parquet(OUT / "serves.parquet")
    s = s[~s["is_tiebreak"] & s["direction"].notna() & s["matchup"].notna()].copy()
    for c in ["direction", "matchup", "side", "gender"]:
        s[c] = s[c].astype(str)
    s["srv_won"] = s["srv_won"].astype(bool)
    return s


def summarize(s):
    dir_rows, cell_rows = [], []
    for key, d in s.groupby(CELL):
        row = dict(zip(CELL, key))
        row.update(n=len(d), n_servers=d["server"].nunique(), n_returners=d["returner"].nunique())
        shares = []
        for x in DIRS:
            is_x = (d["direction"] == x).to_numpy(float)
            share, s_lo, s_hi = ratio_ci(is_x, np.ones(len(d)), d["server"], d["returner"])
            dx = d[d["direction"] == x]
            if len(dx):
                win, w_lo, w_hi = ratio_ci(dx["srv_won"], np.ones(len(dx)), dx["server"], dx["returner"])
            else:
                win = w_lo = w_hi = np.nan
            dir_rows.append({**dict(zip(CELL, key)), "direction": x, "n": len(dx), "low_n": len(dx) < LOW_N,
                             "share": share, "share_lo": s_lo, "share_hi": s_hi,
                             "win": win, "win_lo": w_lo, "win_hi": w_hi})
            row[f"share_{x}"], row[f"win_{x}"] = share, win
            shares.append(share)
        p = np.array(shares)
        nz = p[p > 0]
        row["top_share"] = p.max()  # 1/3 = perfectly even, 1 = always the same direction
        row["entropy"] = float(-(nz * np.log(nz)).sum() / np.log(3))  # 1 = perfectly even mix
        wins = [row[f"win_{x}"] for x in DIRS]
        row["win_spread_pp"] = 100 * (np.nanmax(wins) - np.nanmin(wins))
        row.update(win_tests(d))
        cell_rows.append(row)
    return pd.DataFrame(cell_rows), pd.DataFrame(dir_rows)


# ---------- within-server comparisons ----------

def within_server(df, y, xcols, group):
    """OLS of y on xcols with a fixed effect per `group`, two-way clustered by server and returner.

    Each coefficient compares a server only with themselves in the same situation, so
    differences in who serves where (e.g. big servers hitting more T) cannot drive it.
    Small-sample factor (N-1)/(N-K) counts only xcols, since the fixed effects are nested
    in the server clusters (the fixest / reghdfe convention). Returns pp with a 95% CI.
    """
    cols = [y] + xcols
    d = df[list(dict.fromkeys(cols + group + ["server", "returner"]))]
    vals = d[cols].astype(float)
    dm = vals - vals.groupby([d[c] for c in group], observed=True).transform("mean")
    Y, X = dm[y].to_numpy(), dm[xcols].to_numpy()
    bread = np.linalg.inv(X.T @ X)
    beta = bread @ X.T @ Y
    meat, g = twoway_meat(X * (Y - X @ beta)[:, None], d["server"], d["returner"])
    n, k = len(Y), len(xcols)
    se = np.sqrt(np.diag(bread @ meat @ bread) * (n - 1) / (n - k))
    half = stats.t.ppf(0.975, g - 1) * se
    return [{"term": t, "coef_pp": 100 * b, "lo_pp": 100 * (b - h), "hi_pp": 100 * (b + h), "n": n}
            for t, b, h in zip(xcols, beta, half)]


def within_server_effects(s):
    rows = []

    def add(g, sn, question, fe, res):
        for r in res:
            rows.append({"gender": g, "serve_num": sn, "question": question.format(term=r["term"]), "fixed_effect": fe,
                         **{k: r[k] for k in ["coef_pp", "lo_pp", "hi_pp", "n"]}})

    situ = ["server", "side", "matchup"]
    point_game = ["match_id", "server", "side"]  # Walker & Wooders' "point game"
    for (g, sn), d in s.groupby(["gender", "serve_num"]):
        d = d.assign(body=(d["direction"] == "body").astype(float), T=(d["direction"] == "T").astype(float),
                     serve_in=d["serve_in"].astype(float))
        # Payoff: win rate of body and T relative to wide, same server and situation.
        add(g, sn, "win {term} minus win wide", "server x side x matchup x break point",
            within_server(d, "srv_won", ["body", "T"], situ + ["is_break_point"]))
        # Mechanism: how often each direction lands in, relative to wide.
        add(g, sn, "serve in {term} minus serve in wide", "server x side x matchup x break point",
            within_server(d, "serve_in", ["body", "T"], situ + ["is_break_point"]))
        # Pressure: change in each direction's share on break points and on the server's own
        # game points (40-0 / 40-15 / 40-30 / AD-40), relative to all other points.
        gp = (d["srv_pts"] >= 3) & (d["srv_pts"] > d["ret_pts"])
        d = d.assign(break_point=d["is_break_point"].astype(float), game_point=gp.astype(float))
        for x in DIRS:
            dx = d.assign(pick=(d["direction"] == x).astype(float))
            for fe_name, fe in [("server x side x matchup", situ), ("match x server x side", point_game)]:
                add(g, sn, f"{{term}}: change in {x} share", fe_name,
                    within_server(dx, "pick", ["break_point", "game_point"], fe))
    return pd.DataFrame(rows)


# ---------- serial dependence ----------

def serial_test(s):
    """Minimax also says the next serve should not depend on the last one. Within each
    match x server x side x serve number (in point order), compare how often a serve repeats
    the previous direction with the rate expected if that server's serves there were in a
    random order: sum_k n_k(n_k - 1) / n repeats per group (n_k = serves to direction k)."""
    rows = []
    s = s.sort_values(["match_id", "pt"])
    for (g, sn), d in s.groupby(["gender", "serve_num"]):
        keys = ["match_id", "server", "side"]
        prev = d.groupby(keys)["direction"].shift()
        d = d.assign(repeat=(d["direction"] == prev) & prev.notna())
        grp = d.groupby(keys)
        counts = grp["direction"].value_counts().rename("nk").reset_index()
        counts["pairs"] = counts["nk"] * (counts["nk"] - 1)
        per = grp.agg(n=("direction", "size"), observed=("repeat", "sum"), server=("server", "first"),
                      returner=("returner", "first"))
        per["expected"] = counts.groupby(keys)["pairs"].sum() / per["n"]
        per = per[per["n"] >= 2]
        trans = per["n"] - 1
        diff, lo, hi = ratio_ci(per["observed"] - per["expected"], trans, per["server"], per["returner"], clip=False)
        rows.append({"gender": g, "serve_num": sn, "transitions": int(trans.sum()),
                     "repeat_rate": per["observed"].sum() / trans.sum(),
                     "repeat_rate_if_random_order": per["expected"].sum() / trans.sum(),
                     "diff_pp": 100 * diff, "lo_pp": 100 * lo, "hi_pp": 100 * hi})
    return pd.DataFrame(rows)


def main():
    s = load()
    print(f"Serves used: {len(s):,} (no tiebreaks, known direction and handedness)")
    cells, dirs = summarize(s)
    cells.to_csv(OUT / "baseline_cells.csv", index=False)
    dirs.to_csv(OUT / "baseline_directions.csv", index=False)
    effects = within_server_effects(s)
    effects.to_csv(OUT / "baseline_within_server.csv", index=False)
    serial = serial_test(s)
    serial.to_csv(OUT / "baseline_serial.csv", index=False)

    pd.set_option("display.width", 220)
    show = cells[cells["gender"] == "m"].drop(columns="gender").copy()
    pct = [c for c in show.columns if c.startswith(("share_", "win_", "top_")) and c != "win_spread_pp"]
    show[pct] = (show[pct] * 100).round(1)
    print("\nMen, pooled cells (% of serves / % of points won):")
    print(show.round({"entropy": 3, "win_spread_pp": 1, "wide_minus_T_pp": 1}).to_string(index=False))
    print("\nWithin-server effects (pp, 95% CI two-way clustered by server and returner):")
    print(effects.round(2).to_string(index=False))
    print("\nSerial dependence (repeat the previous direction from the same side):")
    print(serial.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
