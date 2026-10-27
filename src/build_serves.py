"""Build a clean one-row-per-serve dataset from Jeff Sackmann's Match Charting Project.

Usage:
    python src/build_serves.py            # men + women -> output/serves.parquet

Each charted point gives one row for the first serve, plus one row for the
second serve if the first was a fault ("2nd" column is non-empty).
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

SCORE_MAP = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4}
DIRECTION = {"4": "wide", "5": "body", "6": "T"}
# A serve is a fault if the direction digit is followed (optionally after "+",
# the serve-and-volley marker) by a fault code: n net, w wide, d deep,
# x wide+deep, g foot fault, e unknown, ! shank, V time violation.
FAULT_RE = r"^[^0-9]*[0-9]\+?[nwdxge!V]"


def clean_hand(s):
    s = s.astype("string").str.strip().str.upper()
    return s.where(s.isin(["R", "L"]))


def load_matches(g):
    m = pd.read_csv(DATA / f"charting-{g}-matches.csv", dtype=str)
    n_dup = m.duplicated("match_id").sum()
    m = m.drop_duplicates("match_id", keep="first")
    m = m[["match_id", "Player 1", "Player 2", "Pl 1 hand", "Pl 2 hand", "Date", "Surface"]].copy()
    m["Pl 1 hand"] = clean_hand(m["Pl 1 hand"])
    m["Pl 2 hand"] = clean_hand(m["Pl 2 hand"])
    return m, n_dup


def load_points(g):
    files = sorted(DATA.glob(f"charting-{g}-points-*.csv"))
    cols = ["match_id", "Pt", "Set1", "Set2", "Gm1", "Gm2", "Pts", "Svr", "1st", "2nd", "PtWinner"]
    return pd.concat([pd.read_csv(f, dtype=str, usecols=cols) for f in files], ignore_index=True)


def parse_direction(s):
    """First digit after any leading non-digit codes (lets 'c', etc.). 4/5/6 -> wide/body/T, else NA."""
    digit = s.str.extract(r"^[^0-9]*([0-9])", expand=False)
    return digit.map(DIRECTION).astype("string")


def build(g):
    log = {}
    matches, log["dup_match_rows_in_matches_file"] = load_matches(g)
    pts = load_points(g)
    log["points_raw"] = len(pts)

    # Some matches were charted twice under one match_id; the two charts disagree, so drop them.
    dup_ids = pts.loc[pts.duplicated(["match_id", "Pt"]), "match_id"].unique()
    log["matches_dropped_double_charted"] = len(dup_ids)
    pts = pts[~pts["match_id"].isin(dup_ids)]

    # Drop points with an unusable server / winner code.
    ok = pts["Svr"].isin(["1", "2"]) & pts["PtWinner"].isin(["1", "2"])
    log["points_dropped_bad_svr_or_winner"] = int((~ok).sum())
    pts = pts[ok]

    pts = pts.merge(matches, on="match_id", how="inner", validate="many_to_one")
    log["points_after_cleaning"] = len(pts)
    log["matches_used"] = pts["match_id"].nunique()

    # --- Score, tiebreak, side, break point ---
    sp = pts["Pts"].fillna("").str.split("-", n=1, expand=True)
    a, b = sp[0].str.strip(), sp[1].fillna("").str.strip()
    regular = a.isin(SCORE_MAP.keys()) & b.isin(SCORE_MAP.keys())
    numeric = a.str.fullmatch(r"\d+") & b.str.fullmatch(r"\d+")
    # A game is a tiebreak if any of its points has a score outside 0/15/30/40/AD (e.g. "1-0", "7-6").
    # This catches 6-6 tiebreaks, match tiebreaks (at 0-0 games) and short-set tiebreaks,
    # and does NOT mislabel plain 6-6 games in advantage final sets.
    game_key = [pts["match_id"], pts["Set1"], pts["Set2"], pts["Gm1"], pts["Gm2"]]
    tb_point = (numeric & ~regular).astype(bool)
    is_tb = tb_point.groupby(game_key, dropna=False).transform("any").astype(bool)

    srv_pts = np.where(is_tb, pd.to_numeric(a, errors="coerce"), a.map(SCORE_MAP))
    ret_pts = np.where(is_tb, pd.to_numeric(b, errors="coerce"), b.map(SCORE_MAP))
    srv_pts = pd.Series(srv_pts, index=pts.index, dtype="float")
    ret_pts = pd.Series(ret_pts, index=pts.index, dtype="float")
    bad_score = srv_pts.isna() | ret_pts.isna()
    log["points_dropped_unparseable_score"] = int(bad_score.sum())

    total = srv_pts + ret_pts
    side = pd.Series(np.where(total % 2 == 0, "deuce", "ad"), index=pts.index)
    bp = (~is_tb) & (ret_pts >= 3) & (ret_pts > srv_pts)

    svr1 = pts["Svr"] == "1"
    base = pd.DataFrame({
        "gender": g,
        "match_id": pts["match_id"],
        "pt": pd.to_numeric(pts["Pt"], errors="coerce").astype("Int32"),
        "date": pd.to_datetime(pts["Date"], format="%Y%m%d", errors="coerce"),
        "surface": pts["Surface"],
        "server": np.where(svr1, pts["Player 1"], pts["Player 2"]),
        "returner": np.where(svr1, pts["Player 2"], pts["Player 1"]),
        "server_hand": np.where(svr1, pts["Pl 1 hand"], pts["Pl 2 hand"]),
        "returner_hand": np.where(svr1, pts["Pl 2 hand"], pts["Pl 1 hand"]),
        "score": pts["Pts"],
        "srv_pts": srv_pts.astype("Int16"),
        "ret_pts": ret_pts.astype("Int16"),
        "side": side,
        "is_tiebreak": is_tb.astype(bool),
        "is_break_point": bp.astype(bool),
        "srv_won": pts["PtWinner"] == pts["Svr"],
    })
    base = base[~bad_score.values]
    first_s = pts.loc[~bad_score.values, "1st"].fillna("")
    second_s = pts.loc[~bad_score.values, "2nd"].fillna("")
    has_2nd = second_s.str.strip() != ""

    s1 = base.copy()
    s1["serve_num"] = 1
    s1["direction"] = parse_direction(first_s).values
    s1["serve_in"] = (~has_2nd).values  # 1st serve is in unless a 2nd serve was hit
    s1["code"] = first_s.values

    s2 = base[has_2nd.values].copy()
    s2["serve_num"] = 2
    s2["direction"] = parse_direction(second_s[has_2nd]).values
    s2["serve_in"] = (~second_s[has_2nd].str.contains(FAULT_RE, regex=True)).values
    s2["code"] = second_s[has_2nd].values

    out = pd.concat([s1, s2], ignore_index=True)
    out["serve_num"] = out["serve_num"].astype("int8")
    for c in ["server_hand", "returner_hand"]:
        out[c] = out[c].astype("string")
    out["matchup"] = (out["server_hand"] + "v" + out["returner_hand"]).astype("string")  # e.g. "RvL"
    log["serves_total"] = len(out)
    log["serves_missing_direction"] = int(out["direction"].isna().sum())
    log["serves_missing_hand"] = int(out["matchup"].isna().sum())
    log["tiebreak_serves"] = int(out["is_tiebreak"].sum())
    return out.sort_values(["match_id", "pt", "serve_num"]).reset_index(drop=True), log


def main():
    OUT.mkdir(exist_ok=True)
    frames = []
    for g in ["m", "w"]:
        df, log = build(g)
        frames.append(df)
        print(f"[{g}]", *(f"\n  {k}: {v:,}" for k, v in log.items()))
    serves = pd.concat(frames, ignore_index=True)
    for c in ["gender", "surface", "side", "direction", "matchup", "server_hand", "returner_hand"]:
        serves[c] = serves[c].astype("category")
    serves.to_parquet(OUT / "serves.parquet", index=False)
    print(f"\nWrote {len(serves):,} serves to {OUT / 'serves.parquet'}")


if __name__ == "__main__":
    main()
