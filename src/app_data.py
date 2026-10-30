"""Precompute the small tables the Streamlit app reads (app/data/), so the app never needs the
2.5M-row serve file.

Per player x serve number x side:
  - direction mix (wide / body / T) with 95% CIs, overall and on break points
  - points won per direction with 95% CIs
  - the league mix and win rates a player "should" be compared to: league numbers re-weighted to
    the player's own mix of opponents' handedness (a lefty's wide serve is not a righty's)
  - tests, Benjamini-Hochberg corrected across every player-side-serve:
      * mix differs from the league               (is this player's pattern unusual?)
      * win rates differ across directions        (minimax: if equal, no direction wins more)
      * break-point shift per direction           (does the pattern change under pressure?)

CIs are clustered by match (serves within a match are not independent), with t(G-1) critical
values, G = number of matches. Same exclusions as src/baseline.py: tiebreaks, unknown direction,
unknown handedness, unknown side (no-ad deciding points). A 1st serve's "win" counts the whole point, including points where the 1st
serve was a fault: that is the payoff of choosing a direction (Walker & Wooders 2001).

Usage:
    python src/app_data.py
"""
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "data"
DIRS = ["wide", "body", "T"]
MIN_SERVES = 300   # 1st serves per side (known direction) for a player to be listed
MIN_MATCHES = 10   # clusters for the CIs
MIN_DIR_N = 30     # directions used fewer times are left out of the equal-win test
KEY = ["gender", "server", "serve_num", "side"]


def load():
    s = pd.read_parquet(ROOT / "output" / "serves.parquet")
    s = s[s.direction.notna() & s.server_hand.notna() & s.returner_hand.notna() & s.side.notna()
          & ~s.is_tiebreak].copy()
    s["direction"] = s.direction.astype(str)
    for c in ["side", "matchup", "gender"]:
        s[c] = s[c].astype(str)
    s["srv_won"] = s.srv_won.astype(int)
    return s


def bh(p):
    """Benjamini-Hochberg q-values (NaN-safe)."""
    p = np.asarray(p, float)
    q = np.full_like(p, np.nan)
    ok = ~np.isnan(p)
    pv = p[ok]
    order = np.argsort(pv)
    ranked = pv[order] * len(pv) / np.arange(1, len(pv) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty_like(pv)
    out[order] = np.minimum(ranked, 1)
    q[ok] = out
    return q


def tcrit(g):
    return stats.t.ppf(0.975, g - 1)


def ratio(y, n):
    """sum(y)/sum(n) over match clusters, with an SE. y, n: per-match arrays.

    Uses the larger of the match-clustered SE and the plain binomial SE: with only a few serves
    per match (break points) the clustered SE is biased low."""
    N = n.sum()
    p = y.sum() / N if N else np.nan
    g = len(n)
    if N == 0 or g < 2:
        return p, np.nan
    sc = (y - p * n) / N
    return p, max(np.sqrt(g / (g - 1) * (sc ** 2).sum()), np.sqrt(p * (1 - p) / N))


def cmh(n, y):
    """Generalized Cochran-Mantel-Haenszel test that a success rate is equal across k groups,
    stratified by match. n, y: (matches, k) arrays of trials and successes. Returns the p-value
    (chi-square, k-1 df). The variance is the exact conditional (hypergeometric) variance given each
    match's margins; the p-value is the asymptotic chi-square one, not an exact test."""
    k = n.shape[1]
    T = n.sum(1)
    S = y.sum(1)
    ok = T > 1
    n, y, T, S = n[ok], y[ok], T[ok], S[ok]
    if len(T) == 0:
        return np.nan
    r = n[:, : k - 1] / T[:, None]                     # group margins (first k-1)
    dev = (y[:, : k - 1] - n[:, : k - 1] * (S / T)[:, None]).sum(0)
    c = S * (T - S) / (T - 1)                           # T^2/(T-1) * p(1-p)
    V = (np.einsum("h,hi,ij->ij", c, r, np.eye(k - 1))
         - np.einsum("h,hi,hj->ij", c, r, r))
    try:
        Q = dev @ np.linalg.solve(V, dev)
    except np.linalg.LinAlgError:
        return np.nan
    return stats.chi2.sf(Q, k - 1)


def summarise(g, league):
    """One player x serve_num x side. g: that player's serves. Returns (direction rows, test row)."""
    side = g.side.iat[0]
    # per-match counts: n and wins per direction, overall and by break point
    m = g.groupby(["match_id", "is_break_point", "direction"]).srv_won.agg(["size", "sum"]).unstack(["is_break_point", "direction"], fill_value=0)
    m = m.reindex(columns=pd.MultiIndex.from_product([["size", "sum"], [False, True], DIRS]), fill_value=0)
    n = m["size"].to_numpy(float).reshape(len(m), 2, 3)   # match x bp x dir
    w = m["sum"].to_numpy(float).reshape(len(m), 2, 3)
    G = len(m)
    t = tcrit(G)

    # league expectation, re-weighted to this player's opponents' handedness
    mix = g.matchup.value_counts(normalize=True)
    lg = league.loc[(g.gender.iat[0], g.serve_num.iat[0], side)]
    exp_share = {d: sum(mix[k] * lg.loc[(k, d), "share"] for k in mix.index) for d in DIRS}
    exp_win = {d: sum(mix[k] * lg.loc[(k, d), "win"] for k in mix.index) for d in DIRS}

    rows = []
    tot_all = n.sum(axis=(1, 2))
    for sit, sl in [("all", slice(None)), ("bp", slice(1, 2))]:
        tot = n[:, sl, :].sum(axis=(1, 2))
        for j, d in enumerate(DIRS):
            nd = n[:, sl, j].sum(axis=1)
            wd = w[:, sl, j].sum(axis=1)
            sh, sh_se = ratio(nd, tot)
            wi, wi_se = ratio(wd, nd)
            rows.append(dict(situation=sit, direction=d, n=int(nd.sum()), won=int(wd.sum()),
                             share=sh, share_lo=max(0, sh - t * sh_se), share_hi=min(1, sh + t * sh_se),
                             win=wi, win_lo=max(0, wi - t * wi_se), win_hi=min(1, wi + t * wi_se),
                             league_share=exp_share[d], league_win=exp_win[d]))

    # ---- tests (situation = all) ----
    nd_all = n.sum(axis=1)             # match x dir
    wd_all = w.sum(axis=1)
    N = tot_all.sum()
    share = nd_all.sum(0) / N
    # (a) mix vs league: Wald on wide & T shares (body is the remainder), 2 dof
    sc = (nd_all[:, [0, 2]] - np.outer(tot_all, share[[0, 2]])) / N
    V = G / (G - 1) * sc.T @ sc
    diff = share[[0, 2]] - np.array([exp_share["wide"], exp_share["T"]])
    try:
        W = diff @ np.linalg.solve(V, diff)
        p_mix = stats.f.sf(W / 2, 2, G - 1)
    except np.linalg.LinAlgError:
        p_mix = np.nan

    # (b) equal win rates across used directions, compared within the same match
    used = [j for j in range(3) if nd_all[:, j].sum() >= MIN_DIR_N]
    p_win = cmh(nd_all[:, used], wd_all[:, used]) if len(used) >= 2 else np.nan
    win_by_dir = wd_all.sum(0) / np.where(nd_all.sum(0) > 0, nd_all.sum(0), np.nan)
    best = DIRS[int(np.nanargmax(np.where(nd_all.sum(0) >= MIN_DIR_N, win_by_dir, np.nan)))]
    # points per 100 serves gained by moving every serve to the direction that won most for this
    # player. An upper bound: it ignores that the returner would adapt, and it picks the best of
    # three noisy rates. Only shown when the equal-win test is significant.
    cur = (wd_all.sum() / N)
    gain = 100 * (win_by_dir[DIRS.index(best)] - cur)

    test = dict(n=int(N), matches=G, p_mix=p_mix, p_win=p_win, best_dir=best, gain_pp=gain,
                top_dir=DIRS[int(share.argmax())], top_share=share.max(),
                league_top_share=exp_share[DIRS[int(share.argmax())]], win=cur,
                league_win=sum(exp_share[d] * exp_win[d] for d in DIRS))

    # (c) break-point shift per direction: share on BP minus share otherwise. The test compares
    # BP and non-BP serves within the same match (with only a few BP serves per match, a clustered
    # SE would be unreliable, so the test is stratified by match instead).
    tb, tn = n[:, 1, :].sum(1), n[:, 0, :].sum(1)
    Nb, Nn = tb.sum(), tn.sum()
    for j, d in enumerate(DIRS):
        if Nb < 1 or Nn < 1:
            test.update({f"bp_{d}": np.nan, f"bp_{d}_p": np.nan})
            continue
        test[f"bp_{d}"] = n[:, 1, j].sum() / Nb - n[:, 0, j].sum() / Nn
        # rows: BP / not BP; "success" = served direction d
        test[f"bp_{d}_p"] = cmh(np.stack([tb, tn], 1), np.stack([n[:, 1, j], n[:, 0, j]], 1))
    test["n_bp"] = int(Nb)
    return rows, test


def stability(s, seed=0):
    """Split each player's matches at random into two halves. A trait that is real shows up in
    both halves; noise does not. Correlation across players between halves, 1st serves, per
    gender x side, for: favorite-target share (share of the most-used direction; key "guess")
    and the win-rate edge of T over wide (a contrast with no best-of-three selection in it)."""
    rng = np.random.default_rng(seed)
    f = s[s.serve_num == 1]
    rows = []
    for (gd, side), g in f.groupby(["gender", "side"]):
        half = {}
        for p, gp in g.groupby("server"):
            ids = gp.match_id.unique()
            a = set(rng.permutation(ids)[: len(ids) // 2])
            half[p] = gp.match_id.isin(a)
        res = []
        for p, gp in g.groupby("server"):
            r = {}
            for h, part in [("a", gp[half[p]]), ("b", gp[~half[p]])]:
                sh = part.direction.value_counts(normalize=True)
                w = part.groupby("direction").srv_won.mean()
                r[f"guess_{h}"] = sh.max()
                r[f"edge_{h}"] = w.get("T", np.nan) - w.get("wide", np.nan)
            res.append(r)
        res = pd.DataFrame(res)
        for metric in ["guess", "edge"]:
            ok = res[[f"{metric}_a", f"{metric}_b"]].dropna()
            rows.append(dict(gender=gd, side=side, metric=metric, players=len(ok),
                             corr=ok[f"{metric}_a"].corr(ok[f"{metric}_b"])))
    return pd.DataFrame(rows)


def main():
    s = load()
    OUT.mkdir(parents=True, exist_ok=True)

    league = (s.groupby(["gender", "serve_num", "side", "matchup", "direction"])
               .srv_won.agg(n="size", win="mean").reset_index())
    league["share"] = league.n / league.groupby(["gender", "serve_num", "side", "matchup"]).n.transform("sum")
    league = league.set_index(["gender", "serve_num", "side", "matchup", "direction"]).sort_index()

    first = s[s.serve_num == 1]
    per_side = first.groupby(["gender", "server", "side"]).size().unstack("side").min(axis=1)
    matches = s.groupby(["gender", "server"]).match_id.nunique()
    keep = per_side[(per_side >= MIN_SERVES) & (matches.reindex(per_side.index) >= MIN_MATCHES)].index
    s = s.set_index(["gender", "server"]).loc[keep].reset_index()
    print(f"players: {len(keep)} ({pd.Series([k[0] for k in keep]).value_counts().to_dict()})")

    mix_rows, tests = [], []
    for key, g in s.groupby(KEY):
        r, t = summarise(g, league)
        for row in r:
            mix_rows.append(dict(zip(KEY, key), **row))
        tests.append(dict(zip(KEY, key), **t))
    mix = pd.DataFrame(mix_rows)
    tests = pd.DataFrame(tests)

    # Multiple-testing correction across every player x serve x side tested, per family
    tests["q_mix"] = bh(tests.p_mix)
    tests["q_win"] = bh(tests.p_win)
    bp_p = tests[[f"bp_{d}_p" for d in DIRS]].to_numpy().ravel()
    bp_q = bh(bp_p).reshape(len(tests), 3)
    for j, d in enumerate(DIRS):
        tests[f"bp_{d}_q"] = bp_q[:, j]

    info = (s.groupby(["gender", "server"])
             .agg(hand=("server_hand", lambda x: x.astype(str).mode().iat[0]),
                  matches=("match_id", "nunique"), first=("date", "min"), last=("date", "max"),
                  serves=("pt", "size"))
             .reset_index())
    info["first"] = info["first"].dt.year
    info["last"] = info["last"].dt.year

    mix.to_csv(OUT / "player_mix.csv", index=False, float_format="%.5g")
    tests.to_csv(OUT / "player_tests.csv", index=False, float_format="%.5g")
    info.to_csv(OUT / "players.csv", index=False)
    league.reset_index().to_csv(OUT / "league_cells.csv", index=False, float_format="%.5g")
    stab = stability(s)
    stab.to_csv(OUT / "stability.csv", index=False, float_format="%.4g")
    print("\nsplit-half stability (1st serves):")
    print(stab.round(3).to_string(index=False))
    for f in ["baseline_cells.csv", "baseline_directions.csv", "baseline_within_server.csv", "baseline_serial.csv"]:
        shutil.copy(ROOT / "output" / f, OUT / f)

    # headline numbers, printed so they can be checked against the app
    for (gd, sn), t in tests.groupby(["gender", "serve_num"]):
        print(f"\n{gd} serve {sn}: {len(t)} player-sides")
        print(f"  mix differs from league (q<.05): {(t.q_mix < .05).mean():.0%}")
        print(f"  win rates differ by direction (q<.05): {(t.q_win < .05).sum()} of {t.q_win.notna().sum()}")
        sig_bp = (t[[f'bp_{d}_q' for d in DIRS]] < .05).any(axis=1)
        print(f"  any significant break-point shift: {sig_bp.sum()}")
    print("\nwin-rate outliers (q<.05):")
    print(tests[tests.q_win < .05][KEY + ["n", "best_dir", "gain_pp", "q_win"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
