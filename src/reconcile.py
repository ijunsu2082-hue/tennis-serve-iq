"""Reconcile output/serves.parquet against Sackmann's own charting-{g}-stats-ServeDirection.csv.

Reference layout (per match_id x player):
  row "1": first serves IN, by side x direction (deuce/ad x wide/middle/t)
  row "2": ALL second serves (in or out), by side x direction
  err_*  : all faults (first-serve faults + double faults), by error type

Usage:
    python src/reconcile.py
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CELLS = [f"{s}_{d}" for s in ["deuce", "ad"] for d in ["wide", "middle", "t"]]
ERR = ["err_net", "err_wide", "err_deep", "err_wide_deep", "err_foot", "err_unknown"]
DIR_NAME = {"wide": "wide", "body": "middle", "T": "t"}


def my_counts(serves, mask):
    s = serves[mask & serves["direction"].notna()]
    key = s["side"].astype(str) + "_" + s["direction"].astype(str).map(DIR_NAME)
    c = s.groupby([s["match_id"], s["server"], key]).size().unstack(fill_value=0)
    return c.reindex(columns=CELLS, fill_value=0)


def compare(mine, ref, label):
    """mine/ref: DataFrames indexed by (match_id, player) with the 6 side x direction cells."""
    idx = ref.index
    mine = mine.reindex(idx, fill_value=0)
    exact = (mine[CELLS].values == ref[CELLS].values).all(axis=1)
    ratio = mine[CELLS].sum() / ref[CELLS].sum()
    print(f"  {label}: exact match in {exact.mean():.1%} of {len(idx):,} match-player pairs")
    print("    my/ref total by cell: " + "  ".join(f"{c}={r:.3f}" for c, r in ratio.items()))
    return exact


def main():
    serves = pd.read_parquet(ROOT / "output" / "serves.parquet")
    for g in ["m", "w"]:
        sv = serves[serves["gender"] == g]
        ref = pd.read_csv(ROOT / "data" / f"charting-{g}-stats-ServeDirection.csv", dtype={"row": str})
        ref = ref[ref["match_id"].isin(sv["match_id"].unique())].rename(columns={"player": "server"})
        r1 = ref[ref["row"] == "1"].set_index(["match_id", "server"])
        r2 = ref[ref["row"] == "2"].set_index(["match_id", "server"])
        print(f"\n=== {g}: {sv['match_id'].nunique():,} matches in my data, "
              f"{ref['match_id'].nunique():,} also in the reference ===")
        for tb_label, tb_mask in [("incl. tiebreaks", True), ("excl. tiebreaks", ~sv["is_tiebreak"])]:
            print(f" [{tb_label}]")
            first, second = sv["serve_num"] == 1, sv["serve_num"] == 2
            compare(my_counts(sv, tb_mask & second), r2, 'row "2" = all 2nd serves')
            compare(my_counts(sv, tb_mask & first & sv["serve_in"]), r1, 'row "1" = 1st serves in')
            # Fault identity: (1st-serve faults) + (double faults) == sum of err_* columns.
            faults = (sv["serve_num"] == 1) & ~sv["serve_in"] | (sv["serve_num"] == 2) & ~sv["serve_in"]
            mf = sv[tb_mask & faults].groupby(["match_id", "server"]).size()
            rf = r2[ERR].sum(axis=1)
            mf = mf.reindex(rf.index, fill_value=0)
            print(f"  faults (1st faults + double faults) == sum(err_*): exact in {(mf == rf).mean():.1%}; "
                  f"my/ref total = {mf.sum() / rf.sum():.3f}")
            # Swap check: any direction systematically landing in a different reference column?
        # Players in the reference that we cannot find by name in our data (name mismatch check).
        names = set(zip(sv["match_id"], sv["server"]))
        missing = [k for k in r2.index if k not in names]
        print(f"  reference match-player pairs with no serves in my data: {len(missing):,}")


if __name__ == "__main__":
    main()
