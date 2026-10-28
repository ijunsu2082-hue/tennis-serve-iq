"""League-wide baseline: serve direction mix and points won, by situation, with 95% CIs.

Situation cell = gender x serve number x side x break point x handedness matchup.
Within each cell, for each direction (wide / body / T):
  share = how often the server picks it
  win   = share of those points the server wins (for 1st serves this includes
          points where the 1st serve was a fault, the payoff used by minimax tests)

Confidence intervals are cluster-robust by SERVER: one player's serves are not
independent, so plain binomial intervals would be too narrow.

Excluded: tiebreak points (they have no break points and a different rhythm),
serves with unknown direction, serves with unknown handedness.

Usage:
    python src/baseline.py        -> output/baseline_cells.csv, output/baseline_directions.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
CELL = ["gender", "serve_num", "side", "is_break_point", "matchup"]
DIRS = ["wide", "body", "T"]
Z = 1.96


def cluster_ci(y, n):
    """Ratio estimate sum(y)/sum(n) with a cluster-robust 95% CI; y, n are per-cluster totals."""
    y, n = np.asarray(y, float), np.asarray(n, float)
    g, total = len(n), n.sum()
    p = y.sum() / total
    if g < 2:
        return p, np.nan, np.nan
    var = g / (g - 1) * ((y - p * n) ** 2).sum() / total**2
    se = np.sqrt(var)
    return p, max(0.0, p - Z * se), min(1.0, p + Z * se)


def load():
    s = pd.read_parquet(OUT / "serves.parquet")
    s = s[~s["is_tiebreak"] & s["direction"].notna() & s["matchup"].notna()].copy()
    for c in ["direction", "matchup", "side", "gender"]:
        s[c] = s[c].astype(str)
    return s


def summarize(s):
    dir_rows, cell_rows = [], []
    # Per-server totals inside each cell: n serves, serves per direction, wins per direction.
    per = s.groupby(CELL + ["server", "direction"]).agg(n=("srv_won", "size"), won=("srv_won", "sum")).reset_index()
    for key, cell in per.groupby(CELL):
        by_srv = cell.pivot_table(index="server", columns="direction", values=["n", "won"], fill_value=0)
        n_srv = by_srv["n"].reindex(columns=DIRS, fill_value=0)
        w_srv = by_srv["won"].reindex(columns=DIRS, fill_value=0)
        tot_srv = n_srv.sum(axis=1)
        row = dict(zip(CELL, key))
        row.update(n=int(tot_srv.sum()), n_servers=len(tot_srv))
        shares = []
        for d in DIRS:
            share, s_lo, s_hi = cluster_ci(n_srv[d], tot_srv)
            used = n_srv[d] > 0
            win, w_lo, w_hi = cluster_ci(w_srv[d][used], n_srv[d][used]) if used.any() else (np.nan,) * 3
            dir_rows.append({**dict(zip(CELL, key)), "direction": d, "n": int(n_srv[d].sum()),
                             "share": share, "share_lo": s_lo, "share_hi": s_hi,
                             "win": win, "win_lo": w_lo, "win_hi": w_hi})
            row[f"share_{d}"], row[f"win_{d}"] = share, win
            shares.append(share)
        p = np.array(shares)
        nz = p[p > 0]
        row["top_share"] = p.max()  # 1/3 = perfectly even, 1 = always the same direction
        row["entropy"] = float(-(nz * np.log(nz)).sum() / np.log(3))  # 1 = perfectly mixed
        wins = [row[f"win_{d}"] for d in DIRS]
        row["win_spread_pp"] = 100 * (np.nanmax(wins) - np.nanmin(wins))
        # Minimax says win rates should be equal across directions. Chi-square of direction x won
        # (not cluster-adjusted, so p-values are optimistic; use the CIs for judgement).
        table = np.array([[w_srv[d].sum(), n_srv[d].sum() - w_srv[d].sum()] for d in DIRS if n_srv[d].sum() > 0])
        row["chi2_p"] = chi2_contingency(table)[1] if len(table) > 1 else np.nan
        cell_rows.append(row)
    return pd.DataFrame(cell_rows), pd.DataFrame(dir_rows)


def within_server(df, y, xcols, group, cluster="server"):
    """OLS of y on xcols with a fixed effect per `group`, cluster-robust SEs by `cluster`.

    Each coefficient compares a server only with themselves in the same situation, so
    differences in who serves where (e.g. big servers hitting more T) cannot drive it.
    Returns a DataFrame with coef and 95% CI, in percentage points.
    """
    d = df[list(dict.fromkeys([y, cluster] + xcols + group))].copy()
    d[y] = d[y].astype(float)
    g = d.groupby(group, observed=True)
    cols = [y] + xcols
    dm = d[cols].astype(float) - g[cols].transform("mean")
    Y, X = dm[y].to_numpy(), dm[xcols].to_numpy()
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ Y
    resid = Y - X @ beta
    scores = pd.DataFrame(X * resid[:, None]).groupby(d[cluster].to_numpy()).sum().to_numpy()
    n_cl, n, k = len(scores), len(Y), len(xcols) + g.ngroups
    adj = n_cl / (n_cl - 1) * (n - 1) / (n - k)
    se = np.sqrt(np.diag(XtX_inv @ (scores.T @ scores) @ XtX_inv) * adj)
    return pd.DataFrame({"term": xcols, "coef_pp": 100 * beta,
                         "lo_pp": 100 * (beta - Z * se), "hi_pp": 100 * (beta + Z * se)})


def within_server_effects(s):
    """Two questions answered within each server: (1) does the payoff differ by direction?
    (2) does the direction mix shift on break points?"""
    rows = []
    for (g, sn), d in s.groupby(["gender", "serve_num"]):
        d = d.assign(body=(d["direction"] == "body").astype(float), T=(d["direction"] == "T").astype(float))
        # (1) Win rate of body and T relative to wide, same server / side / matchup / break point.
        r = within_server(d, "srv_won", ["body", "T"], ["server", "side", "matchup", "is_break_point"])
        rows += [{"gender": g, "serve_num": sn, "question": f"win {t} minus win wide", **v}
                 for t, v in zip(["body", "T"], r.drop(columns="term").to_dict("records"))]
        # (2) Change in each direction's share on break points, same server / side / matchup.
        d = d.assign(bp=d["is_break_point"].astype(float))
        for dname in DIRS:
            r = within_server(d.assign(pick=(d["direction"] == dname).astype(float)),
                              "pick", ["bp"], ["server", "side", "matchup"])
            rows.append({"gender": g, "serve_num": sn, "question": f"break point change in {dname} share",
                         **r.drop(columns="term").iloc[0].to_dict()})
    return pd.DataFrame(rows)


def main():
    s = load()
    print(f"Serves used: {len(s):,} (no tiebreaks, known direction and handedness)")
    cells, dirs = summarize(s)
    cells.to_csv(OUT / "baseline_cells.csv", index=False)
    dirs.to_csv(OUT / "baseline_directions.csv", index=False)
    effects = within_server_effects(s)
    effects.to_csv(OUT / "baseline_within_server.csv", index=False)
    print("\nWithin-server effects (percentage points, 95% CI clustered by server):")
    print(effects.round(2).to_string(index=False))
    pd.set_option("display.width", 200)
    show = cells[cells["gender"] == "m"].copy()
    pct = [c for c in show.columns if c.startswith(("share_", "win_", "top_")) and c != "win_spread_pp"]
    show[pct] = (show[pct] * 100).round(1)
    show = show.round({"entropy": 3, "win_spread_pp": 1})
    print(show.drop(columns="gender").to_string(index=False))
    print(f"\nWrote {len(cells)} cells to output/baseline_cells.csv and output/baseline_directions.csv")


if __name__ == "__main__":
    main()
