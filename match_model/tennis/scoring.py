"""Tennis rules engine: scoring, serve rotation, ends, pressure flags, and
reconciliation of score-bug readings into point boundaries.

Everything here is pure Python on the dataclasses in ``contracts.py``; no
video processing. States are never mutated: ``apply`` returns a new state.

Conventions and documented decisions
------------------------------------
* ``ScoreState.points`` holds raw point counts for the current game or
  tiebreak, in [A, B] order. In advantage games they are kept canonical:
  once both players reach 3 (40-40) the counts are reduced so the lower one
  is 3, i.e. deuce is always [3, 3] and advantage is [4, 3] / [3, 4].
  Parity (and therefore the serving side) is unchanged by the reduction.
* ``point_label`` is always in A-B order ("30-15" means A 30, B 15), not
  server-first. Advantage is "AD-40" / "40-AD"; deuce is "40-40".
* **No-ad deciding point.** At 40-40 in a no-ad game the receiver chooses
  the side. The video cannot tell us reliably, so ``side`` returns
  ``NO_AD_DECIDING_SIDE`` (default ``"ad"``) for that point. To override,
  pass ``no_ad_side=`` or give the format a ``no_ad_deciding_side``
  attribute (not part of the contract yet). Pure parity would say "deuce".
* **Tiebreak / match tiebreak.** Tiebreak at ``tb_at``-``tb_at``, first to
  ``tb_points`` by 2. A tiebreak set is recorded as 7-6 (or 6-7). With
  ``final_set == "match_tb10"`` the deciding set is replaced by a tiebreak
  to 10 (win by 2). It is recorded in ``sets`` as ``[1, 0]`` or ``[0, 1]``.
  In both kinds of tiebreak the player whose turn it is serves the first
  point, then serve alternates every two points. The next set starts with
  the player who received the first tiebreak point.
* **Ends (ITF rule 10).** Players change ends at the end of the 1st, 3rd and
  every following odd game of a set. That is a change after any game that
  leaves the set's game total odd. The same rule covers set ends: a set that
  finishes with an odd total gets a change, and a set with an even total
  does not, so the change comes after game 1 of the next set. That game also
  leaves an odd total. In a tiebreak, ends change after every 6 points.
  At the end of a tiebreak set the players always change ends once. (If the
  tiebreak ends on a multiple of 6 points, there is no second change.)
  Before a tiebreak (12 games, even total) there is no change.
* ``flags`` reports, for the point about to be played: ``break_point``
  (the receiver wins the game by winning this point, including the no-ad
  deciding point), ``game_point`` (the server does), ``set_point`` and
  ``match_point`` (either player). ``*_for`` lists say who holds them.
  Tiebreak points are never break or game points; they can be set/match
  points.

Reconcile gap policy
--------------------
``reconcile`` walks debounced ``ScoreObs`` readings.

* The next reading is exactly one legal point from the current state:
  emit a boundary, ``source="ocr"``, ``conf = 0.98 * obs.conf`` (capped
  at 0.98).
* An observation is ignored as a transient misread if (a) the following
  observation reverts to the current state, or (b) it is unreadable or more
  than one point away while the following observation is the current state
  or one point from it. Rule (a) has one exception: at deuce and advantage,
  going back is a legal next point (40-40, AD-40, 40-40), so a reading that
  stayed on the board for at least ``MIN_HOLD_S`` seconds is a real point.
* The reading is 2..``max_fill`` points away (default 3), and exactly one
  sequence of winners of the shortest length reaches it (e.g. 15-0 -> 40-0
  can only be A, A). The missed points are then emitted with
  ``source="engine"``, ``gap_before=True`` and lowered conf (0.75 for
  2 apart, 0.6 for 3 apart). Their ``t_end`` is the time of the observation
  that revealed them, and ``t_start`` is None.
* Anything else (ambiguous order, new set after a highlight cut, score went
  backwards) resyncs to the observed state. No phantom points are invented.
  The next emitted boundary gets ``gap_before=True``. Across a resync, ends
  are carried exactly when the jump stays inside one non-tiebreak set.
  Otherwise they are kept as they were (vision can correct them).
* Server: the serve dot of the first reading sets the server. After that
  the engine's rotation is trusted unless two consecutive readings show a
  different dot, in which case the dot wins (handles a wrong initial
  guess). On a resync, a visible dot is adopted immediately.
* The first boundary has ``gap_before=True`` when ``initial`` is None and
  the first reading is not the start of the match.

``score_before`` / ``score_after`` dicts follow the contract
(``sets``, ``games``, ``points`` label, ``in_tb``) plus an extra ``ends``
key ({"A": "near"|"far", "B": ...}) for identity anchoring downstream.
"""
from __future__ import annotations

import copy
from itertools import product
from typing import Optional, Iterable

from .contracts import MatchFormat, ScoreState, ScoreObs, PointBoundary, other

NO_AD_DECIDING_SIDE = "ad"
PT_LABELS = ["0", "15", "30", "40"]
_IDX = {"A": 0, "B": 1}
_PT_PARSE = {"0": 0, "00": 0, "O": 0, "LOVE": 0, "15": 1, "30": 2, "40": 3}
_ADV_STR = {"A", "AD", "ADV", "AV"}
MIN_HOLD_S = 5.0        # shorter than any real point, longer than a board animation


# ---------------------------------------------------------------------------
# basics
# ---------------------------------------------------------------------------
def initial_state(fmt: Optional[MatchFormat] = None, server: str = "A",
                  ends: Optional[dict] = None) -> ScoreState:
    """Start-of-match state."""
    fmt = fmt or MatchFormat()
    s = ScoreState(server=server, ends=dict(ends or {"A": "near", "B": "far"}))
    if fmt.best_of == 1 and fmt.final_set == "match_tb10":
        s.in_tb = True
        s.tb_first_server = server
    return s


def _clone(s: ScoreState) -> ScoreState:
    return copy.deepcopy(s)


def sets_won(state: ScoreState, p: str) -> int:
    i = _IDX[p]
    return sum(1 for g in state.sets if g[i] > g[1 - i])


def _is_final_set(state: ScoreState, fmt: MatchFormat) -> bool:
    return len(state.sets) == fmt.best_of - 1


def is_match_tb(state: ScoreState, fmt: MatchFormat) -> bool:
    return bool(state.in_tb and fmt.final_set == "match_tb10" and _is_final_set(state, fmt))


def tb_target(state: ScoreState, fmt: MatchFormat) -> int:
    return 10 if is_match_tb(state, fmt) else fmt.tb_points


def tb_server(first: str, k: int) -> str:
    """Server of tiebreak point number k (0-based) when `first` served point 0."""
    return first if ((k + 1) // 2) % 2 == 0 else other(first)


def _tb_first_from(server: str, k: int) -> str:
    """Inverse of tb_server: who served first if `server` serves point k."""
    return server if ((k + 1) // 2) % 2 == 0 else other(server)


def _swap_ends(s: ScoreState) -> None:
    s.ends = {"A": s.ends.get("B", "far"), "B": s.ends.get("A", "near")}


def _finish_set(s: ScoreState, set_score: list, next_server: str, fmt: MatchFormat) -> None:
    s.sets.append(list(set_score))
    s.games = [0, 0]
    s.points = [0, 0]
    s.in_tb = False
    s.tb_first_server = None
    s.server = next_server
    need = fmt.best_of // 2 + 1
    if sets_won(s, "A") >= need or sets_won(s, "B") >= need:
        s.match_over = True
        return
    if _is_final_set(s, fmt) and fmt.final_set == "match_tb10":
        s.in_tb = True
        s.tb_first_server = next_server


# ---------------------------------------------------------------------------
# apply one point
# ---------------------------------------------------------------------------
def apply(state: ScoreState, winner: str, fmt: Optional[MatchFormat] = None) -> ScoreState:
    """Return the state after `winner` ("A"/"B") wins the next point."""
    fmt = fmt or MatchFormat()
    if state.match_over:
        raise ValueError("match is over")
    if winner not in _IDX:
        raise ValueError(f"bad winner {winner!r}")
    s = _clone(state)
    i = _IDX[winner]
    s.points[i] += 1
    a, b = s.points

    if s.in_tb:
        target = tb_target(state, fmt)
        k = a + b
        if s.tb_first_server is None:
            s.tb_first_server = _tb_first_from(state.server, k - 1)
        if max(a, b) >= target and abs(a - b) >= 2:
            if is_match_tb(state, fmt):
                set_score = [1, 0] if winner == "A" else [0, 1]
            else:
                set_score = list(s.games)
                set_score[i] += 1
            _swap_ends(s)                     # always change ends after a tiebreak
            _finish_set(s, set_score, other(s.tb_first_server), fmt)
        else:
            s.server = tb_server(s.tb_first_server, k)
            if k % 6 == 0:
                _swap_ends(s)
        return s

    if fmt.no_ad:
        won = s.points[i] >= 4
    else:
        won = s.points[i] >= 4 and s.points[i] - s.points[1 - i] >= 2
    if not won:
        if not fmt.no_ad and a >= 3 and b >= 3:
            m = min(a, b) - 3
            s.points = [a - m, b - m]
        return s

    s.games[i] += 1
    s.points = [0, 0]
    ga, gb = s.games
    if (ga + gb) % 2 == 1:
        _swap_ends(s)
    next_server = other(state.server)
    final_adv = _is_final_set(state, fmt) and fmt.final_set == "adv"
    if max(ga, gb) >= fmt.games_per_set and abs(ga - gb) >= 2:
        _finish_set(s, [ga, gb], next_server, fmt)
    else:
        s.server = next_server
        if ga == gb == fmt.tb_at and not final_adv:
            s.in_tb = True
            s.tb_first_server = next_server
    return s


# ---------------------------------------------------------------------------
# derived views
# ---------------------------------------------------------------------------
def point_label(state: ScoreState) -> str:
    """Current game/tiebreak score in A-B order: '30-15', '40-AD', '3-2'."""
    a, b = state.points
    if state.in_tb:
        return f"{a}-{b}"
    if a >= 3 and b >= 3:
        if a == b:
            return "40-40"
        return "AD-40" if a > b else "40-AD"
    return f"{PT_LABELS[min(a, 3)]}-{PT_LABELS[min(b, 3)]}"


def side(state: ScoreState, fmt: Optional[MatchFormat] = None,
         no_ad_side: Optional[str] = None) -> str:
    """'deuce' when the total points in the game/tiebreak is even, else 'ad'.

    The no-ad deciding point (40-40 with fmt.no_ad) is the receiver's choice;
    see NO_AD_DECIDING_SIDE.
    """
    a, b = state.points
    if fmt is not None and fmt.no_ad and not state.in_tb and a == 3 and b == 3:
        return no_ad_side or getattr(fmt, "no_ad_deciding_side", None) or NO_AD_DECIDING_SIDE
    return "deuce" if (a + b) % 2 == 0 else "ad"


def receiver(state: ScoreState) -> str:
    return other(state.server)


def flags(state: ScoreState, fmt: Optional[MatchFormat] = None) -> dict:
    """Pressure flags for the point about to be played from `state`."""
    fmt = fmt or MatchFormat()
    out = {"break_point": False, "game_point": False, "set_point": False,
           "match_point": False, "break_point_for": [], "game_point_for": [],
           "set_point_for": [], "match_point_for": []}
    if state.match_over:
        return out
    for p in ("A", "B"):
        n = apply(state, p, fmt)
        set_won = len(n.sets) > len(state.sets)
        game_won = set_won or n.games != state.games
        if game_won:
            out["game_point_for"].append(p)
        if set_won:
            out["set_point_for"].append(p)
        if n.match_over:
            out["match_point_for"].append(p)
    if not state.in_tb:
        if receiver(state) in out["game_point_for"]:
            out["break_point"] = True
            out["break_point_for"] = [receiver(state)]
        if state.server in out["game_point_for"]:
            out["game_point"] = True
    out["set_point"] = bool(out["set_point_for"])
    out["match_point"] = bool(out["match_point_for"])
    return out


def score_dict(state: ScoreState) -> dict:
    return {"sets": [list(x) for x in state.sets], "games": list(state.games),
            "points": point_label(state), "in_tb": bool(state.in_tb),
            "ends": dict(state.ends)}


def parse_point_label(label: str, in_tb: bool) -> list:
    """Inverse of point_label (canonical counts)."""
    a, b = str(label).split("-")
    if in_tb:
        return [int(a), int(b)]
    a, b = a.strip().upper(), b.strip().upper()
    if a in _ADV_STR:
        return [4, 3]
    if b in _ADV_STR:
        return [3, 4]
    return [_PT_PARSE[a], _PT_PARSE[b]]


def state_from_score_dict(d: dict, server: str, fmt: Optional[MatchFormat] = None,
                          ends: Optional[dict] = None) -> ScoreState:
    """Rebuild a ScoreState from a score_before/score_after dict + server."""
    fmt = fmt or MatchFormat()
    in_tb = bool(d.get("in_tb"))
    s = ScoreState(sets=[list(x) for x in d.get("sets", [])], games=list(d.get("games", [0, 0])),
                   points=parse_point_label(d.get("points", "0-0"), in_tb), server=server,
                   in_tb=in_tb, ends=dict(ends or d.get("ends") or {"A": "near", "B": "far"}))
    if in_tb:
        s.tb_first_server = _tb_first_from(server, sum(s.points))
    need = fmt.best_of // 2 + 1
    s.match_over = sets_won(s, "A") >= need or sets_won(s, "B") >= need
    return s


def state_key(state: ScoreState) -> tuple:
    """Score identity (ignores server/ends)."""
    return (tuple(tuple(x) for x in state.sets), tuple(state.games),
            tuple(state.points), bool(state.in_tb))


# ---------------------------------------------------------------------------
# score-bug observations
# ---------------------------------------------------------------------------
def _row_map(names_map) -> dict:
    """names_map: {row_index: PlayerId}, e.g. {0: 'B', 1: 'A'}. Default identity."""
    m = {0: "A", 1: "B"}
    if names_map:
        for k, v in names_map.items():
            try:
                k = int(k)
            except (TypeError, ValueError):
                continue
            if v in _IDX and k in (0, 1):
                m[k] = v
    if set(m.values()) != {"A", "B"}:
        raise ValueError(f"names_map must map rows 0/1 to A/B: {names_map!r}")
    return m


def obs_server(obs: ScoreObs, names_map=None) -> Optional[str]:
    if obs.server is None:
        return None
    return _row_map(names_map).get(int(obs.server))


def _is_int(x: str) -> bool:
    try:
        int(x)
        return True
    except (TypeError, ValueError):
        return False


def state_from_obs(obs: ScoreObs, names_map=None, fmt: Optional[MatchFormat] = None,
                   default_server: str = "A", ends: Optional[dict] = None) -> ScoreState:
    """Convert one score-bug reading into a ScoreState.

    Rows are mapped to players with names_map (default row0=A, row1=B).
    Raises ValueError for readings that cannot be a tennis score.
    in_tb is taken from obs.in_tb, or inferred: games at tb_at-tb_at (unless
    the deciding set is played with advantage), the deciding-set match
    tiebreak of the format, or integer points outside {0,15,30,40}.
    """
    fmt = fmt or MatchFormat()
    rm = _row_map(names_map)
    ra = 0 if rm[0] == "A" else 1
    rb = 1 - ra
    if len(obs.games) != 2 or len(obs.points) != 2:
        raise ValueError("need two rows")
    sets = []
    for st in obs.sets:
        if len(st) != 2:
            raise ValueError("bad set entry")
        sets.append([int(st[ra]), int(st[rb])])
    games = [int(obs.games[ra]), int(obs.games[rb])]
    pa = str(obs.points[ra]).strip().upper()
    pb = str(obs.points[rb]).strip().upper()
    # Broadcast bugs keep a just-finished set (often the final set of the match)
    # in the "current games" column with no points, e.g. 6-7 / 6-4 / 7-5. A set
    # in progress can never show such a score, so treat it as completed.
    gps = fmt.games_per_set
    hi, lo = max(games), min(games)
    if pa in ("0", "") and pb in ("0", "") and not obs.in_tb and (
            (hi == gps + 1 and lo in (gps - 1, gps)) or (hi == gps and lo <= gps - 2)):
        if len(sets) < fmt.best_of:
            sets.append(games)
            games = [0, 0]
    s = ScoreState(sets=sets, games=games, ends=dict(ends or {"A": "near", "B": "far"}))
    need = fmt.best_of // 2 + 1
    if len(sets) > fmt.best_of:
        raise ValueError("too many sets")
    final = len(sets) == fmt.best_of - 1
    in_tb = bool(obs.in_tb)
    if not in_tb:
        if games == [fmt.tb_at, fmt.tb_at] and not (final and fmt.final_set == "adv"):
            in_tb = True
        elif final and fmt.final_set == "match_tb10" and games == [0, 0]:
            in_tb = True
        elif _is_int(pa) and _is_int(pb) and (pa not in _PT_PARSE or pb not in _PT_PARSE):
            in_tb = True
    if in_tb:
        if not (_is_int(pa) and _is_int(pb)):
            raise ValueError(f"non-integer tiebreak points {pa}-{pb}")
        s.points = [int(pa), int(pb)]
        if min(s.points) < 0:
            raise ValueError("negative points")
        mtb = final and fmt.final_set == "match_tb10" and games == [0, 0]
        if games != [fmt.tb_at, fmt.tb_at] and not mtb:
            raise ValueError(f"tiebreak points at games {games}")
    else:
        if pa in _ADV_STR or pb in _ADV_STR:
            if pa in _ADV_STR and pb == "40":
                s.points = [4, 3]
            elif pb in _ADV_STR and pa == "40":
                s.points = [3, 4]
            else:
                raise ValueError(f"bad advantage reading {pa}-{pb}")
            if fmt.no_ad:
                raise ValueError("advantage shown in a no-ad match")
        else:
            if pa not in _PT_PARSE or pb not in _PT_PARSE:
                raise ValueError(f"bad points {pa}-{pb}")
            s.points = [_PT_PARSE[pa], _PT_PARSE[pb]]
        # plausibility of games in a non-tb set
        cap = fmt.games_per_set + 1
        if not (final and fmt.final_set == "adv") and max(games) > cap:
            raise ValueError("too many games")
    s.in_tb = in_tb
    srv = obs_server(obs, names_map) or default_server
    s.server = srv
    if in_tb:
        s.tb_first_server = _tb_first_from(srv, sum(s.points))
    s.match_over = sets_won(s, "A") >= need or sets_won(s, "B") >= need
    return s


# ---------------------------------------------------------------------------
# point boundaries
# ---------------------------------------------------------------------------
def _boundary(index: int, cur: ScoreState, winner: Optional[str], t_end: float,
              fmt: MatchFormat, gap: bool, conf: float, source: str,
              t_start: Optional[float] = None) -> PointBoundary:
    after = apply(cur, winner, fmt) if winner else cur
    return PointBoundary(index=index, t_end=float(t_end), t_start=t_start, winner=winner,
                         server=cur.server, score_before=score_dict(cur),
                         score_after=score_dict(after), side=side(cur, fmt),
                         flags=flags(cur, fmt), gap_before=gap, conf=round(conf, 3),
                         source=source)


def winner_paths(cur: ScoreState, target_key: tuple, fmt: MatchFormat,
                 max_len: int) -> list:
    """All shortest winner sequences (length 1..max_len) from cur to target."""
    for L in range(1, max_len + 1):
        found = []
        for seq in product("AB", repeat=L):
            s = cur
            ok = True
            for p in seq:
                if s.match_over:
                    ok = False
                    break
                s = apply(s, p, fmt)
            if ok and state_key(s) == target_key:
                found.append(seq)
        if found:
            return found
    return []


def _set_server(s: ScoreState, p: str) -> None:
    s.server = p
    if s.in_tb:
        s.tb_first_server = _tb_first_from(p, sum(s.points))


def _carry_after_jump(cur: ScoreState, new: ScoreState) -> None:
    """Best-effort ends/server for `new` after a resync from `cur`."""
    same_set = (len(cur.sets) == len(new.sets) and not cur.in_tb and not new.in_tb)
    if same_set:
        t0, t1 = sum(cur.games), sum(new.games)
        if t1 >= t0:
            swaps = sum(1 for t in range(t0 + 1, t1 + 1) if t % 2 == 1)
            ends = dict(cur.ends)
            if swaps % 2 == 1:
                ends = {"A": ends["B"], "B": ends["A"]}
            new.ends = ends
            if (t1 - t0) % 2 == 1:
                new.server = other(cur.server)
            else:
                new.server = cur.server
            return
    new.ends = dict(cur.ends)


def _retro_fix_server(out: list, cur: ScoreState, fmt: MatchFormat) -> None:
    """After the dot overrides the server, fix already-emitted boundaries of
    the same (non-tiebreak) game, which must share that server."""
    if cur.in_tb:
        return
    for b in reversed(out):
        sb = b.score_before
        if sb.get("in_tb") or sb["sets"] != cur.sets or sb["games"] != cur.games:
            break
        if b.server == cur.server:
            continue
        b.server = cur.server
        st = state_from_score_dict(sb, cur.server, fmt)
        b.flags = flags(st, fmt)


def _can_go_back(cur: ScoreState, winner: str, fmt: MatchFormat) -> bool:
    """After `winner` wins a point from cur, can the next point restore cur's score?
    True only around deuce (40-40 to advantage and back, advantage to 40-40 and back)."""
    after = apply(cur, winner, fmt)
    return not after.match_over and any(state_key(apply(after, p, fmt)) == state_key(cur) for p in "AB")


def _is_start(s: ScoreState) -> bool:
    return not s.sets and s.games == [0, 0] and s.points == [0, 0]


def reconcile(obs_list: list, fmt: Optional[MatchFormat] = None,
              initial: Optional[ScoreState] = None, names_map=None,
              max_fill: int = 3) -> list:
    """Turn debounced score-bug readings into PointBoundary[] (see module doc)."""
    fmt = fmt or MatchFormat()
    parsed = []
    for o in obs_list:
        try:
            st = state_from_obs(o, names_map, fmt)
        except (ValueError, TypeError, IndexError):
            st = None
        parsed.append((o, st, obs_server(o, names_map) if st is not None else None))

    out: list = []
    cur = _clone(initial) if initial is not None else None
    gap = False
    i = 0
    n = len(parsed)
    if cur is None:
        while i < n and parsed[i][1] is None:
            i += 1
        if i >= n:
            return out
        cur = _clone(parsed[i][1])
        gap = not _is_start(cur)
        i += 1
    mismatch = 0

    def near_cur(st) -> bool:
        if st is None:
            return False
        k = state_key(st)
        if k == state_key(cur):
            return True
        if cur.match_over:
            return False
        return any(state_key(apply(cur, p, fmt)) == k for p in "AB")

    while i < n:
        o, st, srv = parsed[i]
        nxt = parsed[i + 1][1] if i + 1 < n else None
        if st is None:
            i += 1
            continue
        k = state_key(st)
        if k != state_key(cur):
            one = [] if cur.match_over else [p for p in "AB" if state_key(apply(cur, p, fmt)) == k]
            # transient: reverts on the next reading
            if nxt is not None and state_key(nxt) == state_key(cur):
                held = float(parsed[i + 1][0].t) - float(o.t)
                if not (held >= MIN_HOLD_S and len(one) == 1 and _can_go_back(cur, one[0], fmt)):
                    i += 1
                    continue
            if len(one) == 1:
                conf = min(0.98, 0.98 * float(o.conf if o.conf is not None else 1.0))
                out.append(_boundary(len(out), cur, one[0], o.t, fmt, gap, conf, "ocr"))
                cur = apply(cur, one[0], fmt)
                gap = False
            else:
                # misread in between: next reading is consistent with cur
                if nxt is not None and near_cur(nxt):
                    i += 1
                    continue
                paths = [] if cur.match_over else winner_paths(cur, k, fmt, max_fill)
                if paths and len(paths) == 1 and len(paths[0]) >= 2:
                    seq = paths[0]
                    conf = 0.75 if len(seq) == 2 else 0.6
                    for p in seq:
                        out.append(_boundary(len(out), cur, p, o.t, fmt, True, conf, "engine"))
                        cur = apply(cur, p, fmt)
                    gap = False
                else:
                    new = _clone(st)
                    _carry_after_jump(cur, new)
                    if srv is not None:
                        _set_server(new, srv)
                    elif new.in_tb:
                        _set_server(new, new.server)
                    cur = new
                    gap = True
                    mismatch = 0
        # verify the server with the serve dot
        if srv is not None and not cur.match_over:
            if srv != cur.server:
                mismatch += 1
                if mismatch >= 2 or not out:
                    _set_server(cur, srv)
                    _retro_fix_server(out, cur, fmt)
                    mismatch = 0
            else:
                mismatch = 0
        i += 1
    return out


def replay(winners: Iterable, fmt: Optional[MatchFormat] = None,
           initial: Optional[ScoreState] = None, t_ends: Optional[list] = None,
           confs: Optional[list] = None, source: str = "engine",
           t_starts: Optional[list] = None) -> list:
    """Score a match from a winner sequence (no score bug).

    A None winner produces a boundary with winner None and an unchanged
    score; the following boundary is flagged gap_before because the score
    can no longer be trusted. Winners after match end are ignored.
    """
    fmt = fmt or MatchFormat()
    cur = _clone(initial) if initial is not None else initial_state(fmt)
    out = []
    gap = False
    for j, w in enumerate(winners):
        if cur.match_over:
            break
        t = t_ends[j] if t_ends is not None else float(j)
        ts = t_starts[j] if t_starts is not None else None
        c = confs[j] if confs is not None else 1.0
        if w not in ("A", "B"):
            out.append(_boundary(len(out), cur, None, t, fmt, gap, 0.0, source, ts))
            gap = True
            continue
        out.append(_boundary(len(out), cur, w, t, fmt, gap, c, source, ts))
        cur = apply(cur, w, fmt)
        gap = False
    return out
