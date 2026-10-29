"""Reconcile output/serves.parquet against Sackmann's own charting-{g}-stats-ServeDirection.csv.

Reference layout (per match_id x player), confirmed against the data:
  row "1": first serves IN, by side x direction (deuce/ad x wide/middle/t)
  row "2": ALL second serves (in or out), by side x direction
  err_*  : faults by type. Almost all sit on row "2" (a handful on row "1"), and the sum
           over both rows is all faults. There is no column for shanks ('!') or time
           violations ('V'), so those are left out of the fault check.
The reference includes tiebreak points, so we compare with tiebreaks included.

Usage:
    python src/reconcile.py
"""
from pathlib import Path

import pandas as pd

from build_serves import NAME_ALIASES

ROOT = Path(__file__).resolve().parent.parent
CELLS = [f"{s}_{d}" for s in ["deuce", "ad"] for d in ["wide", "middle", "t"]]
ERR = ["err_net", "err_wide", "err_deep", "err_wide_deep", "err_foot", "err_unknown"]
DIR_NAME = {"wide": "wide", "body": "middle", "T": "t"}
SHANK_OR_TIME = r"^c*(?:[0-9][+^;]*)?[!V]"


def my_counts(serves, mask):
    s = serves[mask & serves["direction"].notna()]
    key = s["side"].astype(str) + "_" + s["direction"].astype(str).map(DIR_NAME)
    c = s.groupby([s["match_id"], s["server"], key]).size().unstack(fill_value=0)
    return c.reindex(columns=CELLS, fill_value=0)


def compare(mine, ref, label, tb_pairs):
    """mine/ref: indexed by (match_id, player), columns = the 6 side x direction cells."""
    idx = ref.index.union(mine.index)
    mine = mine[CELLS].reindex(idx, fill_value=0)
    ref = ref[CELLS].reindex(idx, fill_value=0)
    exact = (mine.values == ref.values).all(axis=1)
    # A "side swap" pair has the right count per direction but 1+ serves on the other court.
    # This comes from the reference's own side rule in long tiebreaks (e.g. "8-6" counted as ad),
    # so it only counts as a swap if that server served in a tiebreak in that match.
    per_dir = lambda df: df[[f"deuce_{d}" for d in DIR_NAME.values()]].values + \
        df[[f"ad_{d}" for d in DIR_NAME.values()]].values
    swap_only = ~exact & (per_dir(mine) == per_dir(ref)).all(axis=1) & idx.isin(tb_pairs)
    ratio = mine.sum() / ref.sum()
    print(f"  {label}: exact match in {exact.mean():.1%} of {len(idx):,} match-player pairs; "
          f"another {swap_only.mean():.1%} differ only by a deuce/ad swap; "
          f"{(~exact & ~swap_only).mean():.1%} other mismatches")
    print("    my/ref total by cell: " + "  ".join(f"{c}={r:.4f}" for c, r in ratio.items()))


def main():
    serves = pd.read_parquet(ROOT / "output" / "serves.parquet")
    for g in ["m", "w"]:
        sv = serves[serves["gender"] == g]
        ref = pd.read_csv(ROOT / "data" / f"charting-{g}-stats-ServeDirection.csv", dtype={"row": str})
        ref = ref.drop_duplicates(["match_id", "player", "row"]).rename(columns={"player": "server"})
        ref["server"] = ref["server"].replace(NAME_ALIASES)
        my_ids, ref_ids = set(sv["match_id"]), set(ref["match_id"])
        print(f"\n=== {g}: {len(my_ids):,} matches in my data; "
              f"{len(my_ids - ref_ids):,} of them have no reference rows (skipped) ===")
        # Reference matches we do not have, and why.
        pts_ids = pd.concat([pd.read_csv(f, dtype=str, usecols=["match_id", "Pt"])
                             for f in sorted((ROOT / "data").glob(f"charting-{g}-points-*.csv"))])
        double = set(pts_ids.loc[pts_ids.duplicated(["match_id", "Pt"]), "match_id"])
        lost = ref_ids - my_ids
        print(f"  reference matches not in my data: {len(lost)} "
              f"(double-charted, dropped: {len(lost & double)}; no points charted: "
              f"{len(lost - set(pts_ids['match_id']))}; other: {len(lost & set(pts_ids['match_id']) - double)})")
        ref = ref[ref["match_id"].isin(my_ids)]
        sv = sv[sv["match_id"].isin(ref_ids)]
        r1 = ref[ref["row"] == "1"].set_index(["match_id", "server"])
        r2 = ref[ref["row"] == "2"].set_index(["match_id", "server"])
        tb_pairs = pd.MultiIndex.from_frame(sv.loc[sv["is_tiebreak"], ["match_id", "server"]].drop_duplicates())

        first, second = sv["serve_num"] == 1, sv["serve_num"] == 2
        compare(my_counts(sv, second), r2, 'row "2" = all 2nd serves', tb_pairs)
        compare(my_counts(sv, first & sv["serve_in"].fillna(False)), r1, 'row "1" = 1st serves in', tb_pairs)

        # Fault identity: our faults (minus shanks / time violations) == sum of err_* over rows 1+2.
        faults = sv["serve_in"].eq(False) & ~sv["code"].str.contains(SHANK_OR_TIME)
        mf = sv[faults].groupby(["match_id", "server"]).size()
        rf = r1[ERR].sum(axis=1).add(r2[ERR].sum(axis=1), fill_value=0)
        idx = rf.index.union(mf.index)
        mf, rf = mf.reindex(idx, fill_value=0), rf.reindex(idx, fill_value=0)
        print(f"  faults == sum(err_*): exact in {(mf == rf).mean():.1%} of {len(idx):,} pairs; "
              f"my/ref total = {mf.sum() / rf.sum():.4f}")

        mine_pairs = set(zip(sv["match_id"], sv["server"]))
        ref_pairs = set(r2.index) | set(r1.index)
        print(f"  pairs only in reference: {len(ref_pairs - mine_pairs):,}; "
              f"pairs only in my data: {len(mine_pairs - ref_pairs):,}")


if __name__ == "__main__":
    main()
