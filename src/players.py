"""Step 3: per-player serve predictability and cost, with shrinkage, for the most-charted men.

Predictability = how lopsided a player's first-serve direction mix is on a side (1 - normalized entropy).
Cost = points per 100 first serves a player would gain by moving serves toward the direction that wins
most for them, measured against their own results (shrunk toward the league win rate per direction).
Everything is computed per side because break-point / court effects differ by side.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIRS = ["wide", "body", "T"]
TOP_N = 50
K = 150  # prior strength (pseudo-serves) for shrinking a player's win rate toward the league


def main():
    s = pd.read_parquet(ROOT / "output" / "serves.parquet")
    s = s[(s.gender == "m") & (s.serve_num == 1) & s.direction.notna() & ~s.is_tiebreak & (s.date >= "2010-01-01")]
    s["direction"] = s.direction.astype(str)
    top = s.server.value_counts().head(TOP_N).index
    league = s.groupby(["side", "direction"], observed=True).srv_won.mean()

    rows = []
    for (p, side), g in s[s.server.isin(top)].groupby(["server", "side"], observed=True):
        n = len(g)
        cnt = g.direction.value_counts().reindex(DIRS, fill_value=0)
        share = cnt / n
        h = -(share[share > 0] * np.log(share[share > 0])).sum() / np.log(3)
        # shrunk win rate per direction
        win = {}
        for d in DIRS:
            gd = g[g.direction == d]
            lw = league[(side, d)]
            win[d] = (gd.srv_won.sum() + K * lw) / (len(gd) + K)
        cur = sum(share[d] * win[d] for d in DIRS)
        best = max(DIRS, key=lambda d: win[d])
        rows.append(dict(server=p, side=side, n=n, **{f"share_{d}": share[d] for d in DIRS},
                         **{f"win_{d}": win[d] for d in DIRS}, predictability=1 - h,
                         fav=share.idxmax(), best=best, cost_pp=100 * (win[best] - cur)))
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output" / "player_side.csv", index=False)

    # noise floor: same stats from random players-sized samples of the league pool, per side
    rng = np.random.default_rng(0)
    null = []
    for side, g in s.groupby("side", observed=True):
        p = g.direction.value_counts(normalize=True).reindex(DIRS).values
        for n in out[out.side == side].n:
            for _ in range(200):
                c = rng.multinomial(n, p) / n
                c = c[c > 0]
                null.append((side, 1 + (c * np.log(c)).sum() / np.log(3)))
    null = pd.DataFrame(null, columns=["side", "pred"])
    print("Predictability by side: players (top 50) vs. same-size samples drawn from the league mix")
    print(out.groupby("side").predictability.describe()[["mean", "min", "50%", "max"]].round(4))
    print(null.groupby("side").pred.describe(percentiles=[.5, .95, .99])[["mean", "50%", "95%", "99%", "max"]].round(4))
    print("\nMost predictable (deuce/ad):")
    print(out.sort_values("predictability", ascending=False).head(10)[["server", "side", "n", "share_wide", "share_body", "share_T", "predictability"]].round(3).to_string(index=False))
    print("\nCost pp/100 serves, shrunk:")
    print(out.groupby("side").cost_pp.describe().round(2))
    print(out.sort_values("cost_pp", ascending=False).head(8)[["server", "side", "n", "fav", "best", "share_wide", "share_body", "share_T", "win_wide", "win_body", "win_T", "cost_pp"]].round(3).to_string(index=False))
    print("\ncorr(predictability, cost):", round(out.predictability.corr(out.cost_pp), 3))


if __name__ == "__main__":
    main()
