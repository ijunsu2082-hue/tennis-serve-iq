"""Build a clean one-row-per-serve dataset from Jeff Sackmann's Match Charting Project.

Usage:
    python src/build_serves.py            # men + women -> output/serves.parquet

Each charted point gives one row for the first serve, plus one row for the
second serve if the first serve was a fault. Every cleaning step prints how
many rows it touched.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

SCORE_MAP = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4}
DIRECTION = {"4": "wide", "5": "body", "6": "T"}
# Fault codes: n net, w wide, d deep, x wide+deep, g foot fault, e unknown, ! shank, V time violation.
FAULT_CHARS = "nwdxge!V"
# Rally shot letters (forehand, backhand, slices, volleys, lobs, drop shots, ...; q = unknown shot).
SHOT_CHARS = "fbrsvzopuylmhijktq"
SURFACES = {"Hard", "Clay", "Grass", "Carpet"}
# Whole-point codes that mean "no serve recorded": S/R = point to server/returner with no
# details charted, P/Q = point penalty against server/returner.
NO_SERVE_CODES = {"S", "R", "P", "Q"}
# The same player under two names.
NAME_ALIASES = {"Marcelo Tomas Barrios Vera": "Tomas Barrios Vera"}
# A match with at most this many repeated point numbers is one chart with a few slips: keep the more
# detailed copy of each repeated point. More than this means two disagreeing charts: drop the match.
MAX_REPEATED_POINTS = 3


def clean_hand(s):
    s = s.astype("string").str.strip().str.upper()
    return s.where(s.isin(["R", "L"]))


def load_matches(g, log):
    m = pd.read_csv(DATA / f"charting-{g}-matches.csv", dtype=str)
    # A few rows are column-shifted (player names missing, so the names hold hands and the hand
    # column holds the date). They come first among duplicated match_ids, so a plain keep-first
    # would keep the broken row. Drop them; the good copy's blank date/hands are filled below.
    shifted = m["Player 1"].isin(["R", "L"]) & m["Pl 1 hand"].fillna("").str.fullmatch(r"\d{8}")
    log["matches_shifted_rows_dropped"] = int(shifted.sum())
    m = m[~shifted].copy()
    assert not m["match_id"].duplicated().any(), "duplicate match_id left after dropping shifted rows"
    no_date = ~m["Date"].fillna("").str.fullmatch(r"\d{8}")
    log["matches_date_from_match_id"] = int(no_date.sum())
    m.loc[no_date, "Date"] = m.loc[no_date, "match_id"].str[:8]  # match_id starts with YYYYMMDD
    bad_surface = ~m["Surface"].isin(SURFACES)
    log["matches_bad_surface_set_na"] = int(bad_surface.sum())
    m.loc[bad_surface, "Surface"] = pd.NA
    m = m[["match_id", "Player 1", "Player 2", "Pl 1 hand", "Pl 2 hand", "Date", "Surface"]].copy()
    for k in ["Player 1", "Player 2"]:
        log["names_aliased"] = log.get("names_aliased", 0) + int(m[k].isin(NAME_ALIASES).sum())
        m[k] = m[k].replace(NAME_ALIASES)
    m["Pl 1 hand"] = clean_hand(m["Pl 1 hand"])
    m["Pl 2 hand"] = clean_hand(m["Pl 2 hand"])
    return fix_hands(m, log)


def fix_hands(m, log):
    """Use each player's usual hand: some matches label a player wrongly, swapped, or blank."""
    long = pd.concat([
        m[["Player 1", "Pl 1 hand"]].set_axis(["player", "hand"], axis=1),
        m[["Player 2", "Pl 2 hand"]].set_axis(["player", "hand"], axis=1),
    ]).dropna()
    trusted = trusted_hands(long)
    old = m[["Pl 1 hand", "Pl 2 hand"]].copy()
    for k in ["1", "2"]:
        canon = m[f"Player {k}"].map(trusted)
        log[f"hand_p{k}_filled"] = int((old[f"Pl {k} hand"].isna() & canon.notna()).sum())
        log[f"hand_p{k}_overridden"] = int((old[f"Pl {k} hand"].notna() & canon.notna()
                                            & (old[f"Pl {k} hand"] != canon)).sum())
        m[f"Pl {k} hand"] = canon.fillna(old[f"Pl {k} hand"]).astype("string")
    # Pass 2: an override usually means the two labels in that match were swapped, so the
    # other player's label there is suspect too. Re-vote without those matches and, for players
    # in them, use the hand if the remaining labels are unanimous.
    suspect = (old.ne(m[["Pl 1 hand", "Pl 2 hand"]]) & old.notna()).any(axis=1)
    clean = long[~long.index.isin(m.index[suspect])]
    unanimous = clean.groupby("player")["hand"].agg(lambda h: h.iloc[0] if h.nunique() == 1 else None).dropna()
    changed = 0
    for k in ["1", "2"]:
        fix = suspect & m[f"Player {k}"].isin(unanimous.index)
        new = m.loc[fix, f"Player {k}"].map(unanimous)
        changed += int((new != m.loc[fix, f"Pl {k} hand"]).sum())
        m.loc[fix, f"Pl {k} hand"] = new
    log["hand_fixed_in_swapped_matches"] = changed
    return m


def trusted_hands(long):
    """player -> hand when the labels are unanimous, or strong (>= 3 matches, >= 80% agree)."""
    counts = long.groupby(["player", "hand"]).size().unstack(fill_value=0)
    n = counts.sum(axis=1)
    share = counts.max(axis=1) / n
    return counts.idxmax(axis=1)[(share == 1) | ((n >= 3) & (share >= 0.8))]


def load_points(g, log):
    files = sorted(DATA.glob(f"charting-{g}-points-*.csv"))
    pts = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)
    log["points_raw"] = len(pts)
    # Exact duplicate rows (a chart pasted twice) are safe to collapse.
    pts = pts.drop_duplicates()
    # Repeated point numbers left after that: a few in a match are slips (one copy "S", the other the
    # full code) -> keep the copy with the longer code. Many mean the match was charted twice and the
    # charts disagree -> drop the match.
    rep = pts.duplicated(["match_id", "Pt"], keep=False)
    n_rep = pts[rep].drop_duplicates(["match_id", "Pt"]).groupby("match_id").size()
    drop_ids = n_rep.index[n_rep > MAX_REPEATED_POINTS]
    log["matches_dropped_double_charted"] = len(drop_ids)
    pts = pts[~pts["match_id"].isin(drop_ids)]
    detail = (pts["1st"].fillna("").str.strip().str.len() + pts["2nd"].fillna("").str.strip().str.len()
              - 1000 * pts["1st"].fillna("").str.strip().isin(NO_SERVE_CODES))
    before = len(pts)
    pts = (pts.assign(_detail=detail).sort_values("_detail", ascending=False, kind="stable")
              .drop_duplicates(["match_id", "Pt"]).drop(columns="_detail").sort_index())
    log["repeated_points_resolved_keep_detailed"] = before - len(pts)
    cols = ["match_id", "Pt", "Set1", "Set2", "Gm1", "Gm2", "Pts", "Svr", "1st", "2nd", "PtWinner"]
    return pts[cols]


def is_fault_char(ch):
    # Fault letters are sometimes typed in uppercase ("6W"); 'V' (time violation) is uppercase by design.
    return ch.isin(list(FAULT_CHARS)) | ch.isin(list("NWDXGE"))


def parse_serve(codes):
    """Parse serve codes -> (direction, outcome) where outcome is 'fault', 'in' or 'unknown'.

    Leading lets ('c') and markers ('+', '^', ';') are skipped. If the next char is a digit,
    it is the direction (4 wide, 5 body, 6 T; 0 or others = unknown) and the char after it
    (skipping markers and a net-cord 'c') says fault vs in play.
    A code starting with a letter has no direction digit:
      fault letter alone or + digit ("n", "g", "n4") -> fault ("n4": direction 4)
      fault letter then rally shots ("wf#", "eqf2...")  -> in (direction unknown)
      rally shot letter first ("fb2...", "rb1w#")       -> in (direction unknown)
    """
    body = codes.fillna("").str.strip().str.replace(r"^[c+^;]+", "", regex=True)
    first = body.str[:1]
    starts_digit = first.str.isdigit().fillna(False).astype(bool)
    after = body.str[1:].str.lstrip("+^;c").str[:1]
    fault_first = ~starts_digit & is_fault_char(first)
    letter_digit = body.str.extract(r"^[nwdxge]([0-9])", expand=False)
    direction = first.where(starts_digit).fillna(letter_digit).map(DIRECTION).astype("string")
    rally_after_fault = fault_first & body.str[1:].str.contains(f"[{SHOT_CHARS}]")
    outcome = pd.Series("unknown", index=codes.index)
    outcome[starts_digit & is_fault_char(after)] = "fault"
    outcome[starts_digit & (after != "") & ~is_fault_char(after)] = "in"
    outcome[fault_first & ~rally_after_fault] = "fault"
    outcome[rally_after_fault] = "in"
    outcome[~starts_digit & first.isin(list(SHOT_CHARS))] = "in"
    return direction, outcome


def build(g):
    log = {}
    matches = load_matches(g, log)
    pts = load_points(g, log)

    ok = pts["Svr"].isin(["1", "2"]) & pts["PtWinner"].isin(["1", "2"])
    log["points_dropped_bad_svr_or_winner"] = int((~ok).sum())
    pts = pts[ok]
    pts = pts.merge(matches, on="match_id", how="inner", validate="many_to_one")
    # The same match uploaded twice under different match_ids (e.g. a tournament renamed, a stray space):
    # same date, same two players, identical point codes. Keep the first.
    sig = pts.groupby("match_id", sort=True).agg(
        date=("Date", "first"), p1=("Player 1", "first"), p2=("Player 2", "first"),
        codes=("1st", lambda c: "|".join(c.fillna(""))))
    sig["pair"] = [" & ".join(sorted(x)) for x in zip(sig.p1, sig.p2)]
    dup_match = sig.duplicated(["date", "pair", "codes"])
    log["matches_dropped_uploaded_twice"] = int(dup_match.sum())
    pts = pts[~pts["match_id"].isin(sig.index[dup_match])]

    # "1st" == "1st" is a stray copy of the header row.
    no_serve = pts["1st"].fillna("").str.strip().isin(NO_SERVE_CODES | {"1st"})
    log["points_dropped_no_serve_recorded (1st = S/R/P/Q)"] = int(no_serve.sum())
    pts = pts[~no_serve].reset_index(drop=True)
    log["points_used"] = len(pts)
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

    srv_pts = pd.Series(np.where(is_tb, pd.to_numeric(a, errors="coerce"), a.map(SCORE_MAP)), dtype="float")
    ret_pts = pd.Series(np.where(is_tb, pd.to_numeric(b, errors="coerce"), b.map(SCORE_MAP)), dtype="float")
    bad_score = (srv_pts.isna() | ret_pts.isna()).values
    log["points_dropped_unparseable_score"] = int(bad_score.sum())

    total = srv_pts + ret_pts
    side = pd.Series(np.where(total % 2 == 0, "deuce", "ad"), dtype="object")
    bp = (~is_tb) & (ret_pts >= 3) & (ret_pts > srv_pts)
    # No-ad scoring (e.g. Next Gen Finals): 40-40 is a deciding point. It is a break point, and the
    # returner picks the court, so the side is unknown. A match is no-ad if it never shows "AD" and
    # some regular game ends at 40-40.
    deuce_pt = (~is_tb) & (srv_pts == 3) & (ret_pts == 3)
    last_in_game = ~pd.Series(list(zip(*game_key))).duplicated(keep="last").values
    no_ad_match = pd.Series(deuce_pt.values & last_in_game).groupby(pts["match_id"].values).transform("any").values
    has_ad = pts["Pts"].fillna("").str.contains("AD").groupby(pts["match_id"]).transform("any").values
    deciding = deuce_pt.values & no_ad_match & ~has_ad
    log["no_ad_deciding_points"] = int(deciding.sum())
    bp = bp | deciding
    side[deciding] = None

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
        "is_tiebreak": is_tb.values,
        "is_break_point": bp.values,
        "srv_won": (pts["PtWinner"] == pts["Svr"]).values,
    })
    keep = ~bad_score
    base, pts = base[keep], pts[keep]

    code1 = pts["1st"].fillna("").str.strip()
    code2 = pts["2nd"].fillna("").str.strip()
    # Free-text notes sometimes land in "2nd" (e.g. "Play suspended- resumed ..."): not a serve.
    note_in_2nd = code2.str.contains(r"\s")
    log["2nd_free_text_ignored"] = int(note_in_2nd.sum())
    has_2nd = (code2 != "") & ~note_in_2nd

    dir1, out1 = parse_serve(code1)
    dir2, out2 = parse_serve(code2)
    # If "1st" shows a finished point (ace / winner / error: * # @) the 1st serve was in and a
    # filled "2nd" is a charting slip. If "1st" is an unfinished fragment ("6b") but "2nd" is a
    # real serve, the 2nd column wins and the 1st serve was a fault.
    complete1 = code1.str.contains(r"[*#@]")
    real_serve2 = code2.str.replace(r"^[c+^;]+", "", regex=True).str[:1].str.contains(r"[0-9nwdxgeV]")
    fragment_then_serve = has_2nd & (out1 == "in") & ~complete1 & real_serve2
    out1 = out1.mask(fragment_then_serve, "fault")
    log["1st_fragment_treated_as_fault"] = int(fragment_then_serve.sum())
    # First serve in/out comes from its own code; "2nd" being filled is only the fallback.
    first_in = np.where(out1 == "fault", False, np.where(out1 == "in", True, ~has_2nd))
    phantom_2nd = has_2nd & (out1 == "in")
    log["2nd_dropped_because_1st_was_in_play"] = int(phantom_2nd.sum())
    log["1st_fault_with_no_2nd_recorded"] = int(((out1 == "fault") & ~has_2nd).sum())
    make_2nd = has_2nd & ~phantom_2nd

    s1 = base.copy()
    s1["serve_num"] = 1
    s1["direction"] = dir1.values
    s1["serve_in"] = pd.array(first_in, dtype="boolean")
    s1["code"] = code1.values

    s2 = base[make_2nd.values].copy()
    s2["serve_num"] = 2
    s2["direction"] = dir2[make_2nd].values
    s2["serve_in"] = pd.array(out2[make_2nd].map({"in": True, "fault": False, "unknown": pd.NA}), dtype="boolean")
    s2["code"] = code2[make_2nd].values
    log["2nd_serve_in_unknown"] = int(s2["serve_in"].isna().sum())

    out = pd.concat([s1, s2], ignore_index=True)
    out["serve_num"] = out["serve_num"].astype("int8")
    for c in ["server_hand", "returner_hand"]:
        out[c] = out[c].astype("string")
    out["matchup"] = (out["server_hand"] + "v" + out["returner_hand"]).astype("string")  # e.g. "RvL"
    assert (out["server"] != out["returner"]).all(), "server == returner somewhere"
    log["serves_total"] = len(out)
    log["serves_missing_direction"] = int(out["direction"].isna().sum())
    log["serves_missing_hand"] = int(out["matchup"].isna().sum())
    log["serves_missing_surface"] = int(out["surface"].isna().sum())
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
