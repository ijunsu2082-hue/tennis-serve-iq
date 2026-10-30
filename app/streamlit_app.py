"""Serve IQ: where pro tennis players serve, and whether any direction wins more points.

Run:  streamlit run app/streamlit_app.py
Data: app/data/, built by src/app_data.py from the Match Charting Project.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

DATA = Path(__file__).resolve().parent / "data"
DIRS = ["wide", "body", "T"]
SIDES = ["deuce", "ad"]
GENDER = {"m": "Men", "w": "Women"}
Q = 0.05  # false discovery rate used for every "significant" label

# Reference palette (dataviz skill): sequential blue for magnitude, one accent for the player,
# neutral gray for the league reference.
BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
        "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
PLAYER = "#2a78d6"
LEAGUE = "#8a8984"

st.set_page_config(page_title="Serve IQ", layout="wide")


# ---------------------------------------------------------------- data

@st.cache_data
def load():
    mix = pd.read_csv(DATA / "player_mix.csv")
    tests = pd.read_csv(DATA / "player_tests.csv")
    players = pd.read_csv(DATA / "players.csv")
    within = pd.read_csv(DATA / "baseline_within_server.csv")
    dirs = pd.read_csv(DATA / "baseline_directions.csv")
    serial = pd.read_csv(DATA / "baseline_serial.csv")
    stab = pd.read_csv(DATA / "stability.csv")
    # favorite-target share (top_share): share of serves to the server's most-used direction.
    # fav_pct: share of the other listed players (same gender, serve, side) with a lower one
    g = tests.groupby(["gender", "serve_num", "side"]).top_share
    tests["fav_pct"] = (g.rank(method="min") - 1) / (g.transform("count") - 1)
    return mix, tests, players, within, dirs, serial, stab


mix, tests, players, within, league_dirs, serial, stab = load()


def pct(x, d=0):
    return "n/a" if pd.isna(x) else f"{100 * x:.{d}f}%"


P_FLOOR = 1e-4  # p and q values below this print as "< floor", as on the static site


def pq(x):
    """'= 0.031' style p or q value text; values under P_FLOOR print as a bound, not in e-notation."""
    if pd.isna(x):
        return "= n/a"
    return f"< {P_FLOOR:g}" if x < P_FLOOR else f"= {x:.2g}"


def r_range(a, b):
    return f"{a:.2f} to {b:.2f}"


def pp(x, d=1):
    return "n/a" if pd.isna(x) else f"{100 * x:+.{d}f} pts"


def ws(gender, serve, question, fe=None):
    r = within[(within.gender == gender) & (within.serve_num == serve) & (within.question == question)]
    if fe:
        r = r[r.fixed_effect == fe]
    return r.iloc[0]


def other_share(rows, side, d):
    """Share of direction d on points that are NOT break points (what the break-point test compares to)."""
    a = rows[(rows.side == side) & (rows.situation == "all")]
    b = rows[(rows.side == side) & (rows.situation == "bp")]
    n_other = a.n.sum() - b.n.sum()
    return (a[a.direction == d].n.sum() - b[b.direction == d].n.sum()) / n_other if n_other else np.nan


# ---------------------------------------------------------------- charts

def blue_for(share):
    """Sequential blue step for a share (0 -> lightest, >=60% -> darkest)."""
    i = int(round(min(max(share, 0), 0.6) / 0.6 * (len(BLUE) - 1)))
    return BLUE[i], ("#ffffff" if i >= 7 else "#0b0b0b")


def court(rows, bp=False):
    """Schematic service boxes seen from behind the server. rows: player_mix rows for one
    serve/situation. The data code wide / body / T, not landing spots, so the zones are drawn as
    equal thirds. bp: break-point rows, whose league values are all-points league values."""
    league_label = "League, all points, same opponents" if bp else "League, same opponents"
    W, L = 4.115, 6.40  # singles service box: 4.115 m wide, 6.40 m deep
    G = 0.35            # visual gap at the center service line, so the two boxes read as two
    A = W + G           # left edge of the ad box
    zones = {  # x ranges, server's view: the deuce box is on the left, the ad box on the right
        "deuce": {"wide": (0, W / 3), "body": (W / 3, 2 * W / 3), "T": (2 * W / 3, W)},
        "ad": {"T": (A, A + W / 3), "body": (A + W / 3, A + 2 * W / 3), "wide": (A + 2 * W / 3, A + W)},
    }
    fig = go.Figure()
    for side in SIDES:
        for d in DIRS:
            r = rows[(rows.side == side) & (rows.direction == d)].iloc[0]
            x0, x1 = zones[side][d]
            fill, ink = blue_for(r.share)
            if r.n == 0:
                ci_txt = "no serves"
            elif r.n < 30:
                ci_txt = f"few serves ({int(r.n)}), so the interval is not reliable"
            else:
                ci_txt = f"95% CI {pct(r.share_lo)} to {pct(r.share_hi)}"
            # zones are drawn as filled scatter polygons so they carry a hover tooltip
            fig.add_trace(go.Scatter(
                x=[x0, x1, x1, x0, x0], y=[0, 0, L, L, 0], fill="toself", mode="lines",
                fillcolor=fill, line=dict(color="rgba(255,255,255,0.9)", width=2),
                hoveron="fills", showlegend=False,
                text=(f"<b>{side.title()} side, {d}</b><br>"
                      f"Player: {pct(r.share)} ({ci_txt})<br>"
                      f"{league_label}: {pct(r.league_share)}<br>"
                      f"{int(r.n):,} serves"),
                hoverinfo="text"))
            fig.add_annotation(x=(x0 + x1) / 2, y=L * 0.62, text=f"<b>{pct(r.share)}</b>",
                               showarrow=False, font=dict(size=22, color=ink))
            fig.add_annotation(x=(x0 + x1) / 2, y=L * 0.40, text=d, showarrow=False,
                               font=dict(size=13, color=ink))
            if bp:
                fig.add_annotation(x=(x0 + x1) / 2, y=L * 0.17,
                                   text=f"league,<br>all points<br>{pct(r.league_share)}",
                                   showarrow=False, font=dict(size=9, color=ink))
            else:
                fig.add_annotation(x=(x0 + x1) / 2, y=L * 0.20, text=f"avg {pct(r.league_share)}",
                                   showarrow=False, font=dict(size=10, color=ink))
    for x0 in (0, A):  # box outlines in the muted color, so light zones still read as a box
        fig.add_shape(type="rect", x0=x0, x1=x0 + W, y0=0, y1=L, line=dict(color=LEAGUE, width=1.5))
    for side, x in [("DEUCE side", W / 2), ("AD side", A + W / 2)]:
        fig.add_annotation(x=x, y=L + 0.55, text=f"<b>{side}</b>", showarrow=False, font=dict(size=13))
    fig.add_shape(type="line", x0=W + G / 2, x1=W + G / 2, y0=0, y1=L, line=dict(color=LEAGUE, width=2))
    fig.add_shape(type="line", x0=-0.2, x1=A + W + 0.2, y0=0, y1=0, line=dict(color=LEAGUE, width=3))
    fig.add_annotation(x=W + G / 2, y=-0.45, text="net (view from behind the server, schematic)", showarrow=False,
                       font=dict(size=11, color=LEAGUE))
    fig.update_xaxes(visible=False, range=[-0.3, A + W + 0.3], fixedrange=True)
    fig.update_yaxes(visible=False, range=[-0.8, L + 0.9], fixedrange=True, scaleanchor="x")
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=0, b=0),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    return fig


def win_chart(rows):
    """Points won by direction with 95% CIs, player vs league reference, one panel per side."""
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.06,
                        subplot_titles=["Deuce side", "Ad side"])
    for c, side in enumerate(SIDES, start=1):
        r = rows[rows.side == side].set_index("direction").reindex(DIRS)
        fig.add_trace(go.Scatter(
            x=DIRS, y=r.win * 100, mode="markers", name="This player",
            marker=dict(size=11, color=PLAYER),
            error_y=dict(type="data", symmetric=False, array=(r.win_hi - r.win) * 100,
                         arrayminus=(r.win - r.win_lo) * 100, color=PLAYER, thickness=2, width=6),
            customdata=np.stack([r.n, r.win_lo * 100, r.win_hi * 100], axis=1),
            hovertemplate="%{x}: %{y:.1f}% won (95% CI %{customdata[1]:.1f}% to %{customdata[2]:.1f}%)"
                          "<br>%{customdata[0]:,} serves<extra>This player</extra>",
            showlegend=(c == 1), legendgroup="p"), row=1, col=c)
        fig.add_trace(go.Scatter(
            x=DIRS, y=r.league_win * 100, mode="markers", name="League, same opponents",
            marker=dict(size=10, color=LEAGUE, symbol="diamond-open", line=dict(width=2)),
            hovertemplate="%{x}: %{y:.1f}% won<extra>League</extra>",
            showlegend=(c == 1), legendgroup="l"), row=1, col=c)
    fig.update_yaxes(title_text="Points won by the server (%)", row=1, col=1, ticksuffix="%")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=-0.15, x=0))
    return fig


# ---------------------------------------------------------------- header + main findings

st.title("Serve IQ")
st.markdown("#### Where pro tennis players serve, and whether any direction wins more points")
st.caption("2.56 million charted serves from the Match Charting Project and 447 players with enough "
           "data. Shares and win rates have 95% confidence intervals.")

t1 = tests[tests.serve_num == 1]
t2 = tests[tests.serve_num == 2]
costly_players = t1[t1.q_win < Q][["gender", "server"]].drop_duplicates()
n_players = len(players)
stab1 = stab.groupby("metric")["corr"].agg(["min", "max"])
body_m = ws("m", 1, "win body minus win wide")
body_in_m = ws("m", 1, "serve in body minus serve in wide")

c1, c2, c3 = st.columns(3)
with c1, st.container(border=True):
    st.markdown("**Serve mixes differ from the league**")
    st.metric("1st-serve mixes unlike the league average", pct((t1.q_mix < Q).mean()),
              help="Share of player by court-side combinations (1st serves) where the direction mix "
                   "differs from the league average for the same opponents (false discovery rate 5%).")
    st.caption(f"The habit is stable: a player's favorite-target share in one random half of their "
               f"matches correlates with the other half "
               f"(r = {r_range(stab1.loc['guess', 'min'], stab1.loc['guess', 'max'])}).")
with c2, st.container(border=True):
    st.markdown("**Few players show a win-rate gap by direction**")
    st.metric("Players with a side where 1st-serve win rates differ by direction",
              f"{len(costly_players)} of {n_players}",
              help="Players with at least one court side where 1st-serve win rates differ by "
                   "direction (false discovery rate 5%). Player-sides on 2nd serves: "
                   f"{int((t2.q_win < Q).sum())}.")
    st.caption(f"Player-sides on 2nd serves: {int((t2.q_win < Q).sum())}. The T-minus-wide win gap does "
               f"not replicate across random halves of a player's matches (r = "
               f"{r_range(stab1.loc['edge', 'min'], stab1.loc['edge', 'max'])}). "
               "For most players, win rates do not differ significantly by direction.")
with c3, st.container(border=True):
    st.markdown("**Body serves win fewer points**")
    st.metric("Body minus wide, 1st serve, same server (men)", f"{body_m.coef_pp:+.1f} pts won",
              help="Within-server comparison (server by side by matchup by break point fixed effects), "
                   f"men. 95% CI {body_m.lo_pp:+.1f} to {body_m.hi_pp:+.1f}.")
    st.caption(f"Body serves go in {body_in_m.coef_pp:.1f} percentage points more often than wide ones (men), "
               f"yet the server wins {-body_m.coef_pp:.1f} fewer points per 100. Women: "
               f"{-ws('w', 1, 'win body minus win wide').coef_pp:.1f} fewer.")

tab_player, tab_league, tab_how = st.tabs(["Scout a server", "League findings", "How it works"])

# ---------------------------------------------------------------- player scout

with tab_player:
    f1, f2, f3, f4 = st.columns([1, 2.4, 1, 1.2])
    gender = f1.selectbox("Tour", ["m", "w"], format_func=GENDER.get)
    plist = players[players.gender == gender].sort_values("serves", ascending=False)
    player = f2.selectbox("Server (most charted serves first, type to search)", plist.server.tolist())
    serve = f3.selectbox("Serve", [1, 2], format_func=lambda s: "1st serve" if s == 1 else "2nd serve")
    situation = f4.selectbox("Points", ["all", "bp"],
                             format_func=lambda s: "All points" if s == "all" else "Break points only")

    info = plist[plist.server == player].iloc[0]
    rows = mix[(mix.gender == gender) & (mix.server == player) & (mix.serve_num == serve)]
    rows_sit = rows[rows.situation == situation]
    tst = tests[(tests.gender == gender) & (tests.server == player) & (tests.serve_num == serve)].set_index("side")
    hand = "Left-handed" if info.hand == "L" else "Right-handed"
    st.markdown(f"### {player}")
    st.caption(f"{hand}, {info.matches} charted matches ({info['first']} to {info['last']}), "
               f"{int(tst.n.sum()):,} {'1st' if serve == 1 else '2nd'} serves with a known direction, "
               f"tiebreaks excluded")

    left, right = st.columns([1.25, 1])
    with left:
        st.markdown(f"**Where the {'1st' if serve == 1 else '2nd'} serve goes"
                    f"{' on break points' if situation == 'bp' else ''}**")
        n_sit = rows_sit.groupby("side").n.sum()
        if situation == "bp" and n_sit.min() < 100:
            st.warning(f"Only {int(n_sit.min())} break-point serves on one side: read the shares as rough "
                       "(hover a zone for its confidence interval).")
        st.plotly_chart(court(rows_sit, bp=(situation == "bp")), config={"displayModeBar": False})
        st.caption(("Darker blue means used more. 'league, all points' is the average pro facing the same "
                    "mix of right- and left-handed returners, over all points: the league expectation is "
                    "not split by break point. " if situation == "bp" else
                    "Darker blue means used more. 'avg' is the average pro facing the same mix of right- "
                    "and left-handed returners. ")
                   + "Hover a zone for the 95% confidence interval. The court is a schematic: the charts "
                   "code each serve as wide, body or T, not where it landed.")

    with right:
        st.markdown("**Scouting report for the returner**")
        st.caption("Favorite-target share is the share of serves to the server's most-used target. If a "
                   "returner always leaned toward that target, they would be right this often; a server "
                   "who split evenly across three targets would give one in three. The charts only record where "
                   "the serve went, not what the returner did.")
        allp = " (all points)" if situation == "bp" else ""
        for side in SIDES:
            t = tst.loc[side]
            r = rows[(rows.situation == "all") & (rows.side == side)].set_index("direction")
            top = t.top_dir
            with st.container(border=True):
                ranked = r.share.sort_values(ascending=False)
                if ranked.iloc[0] - ranked.iloc[1] < 0.03:
                    st.markdown(f"**{side.title()} side.** No clear favorite target{allp}: "
                                f"{ranked.index[0]} {pct(ranked.iloc[0])}, {ranked.index[1]} {pct(ranked.iloc[1])}, "
                                f"{ranked.index[2]} {pct(ranked.iloc[2])}.")
                else:
                    st.markdown(f"**{side.title()} side.** Favorite target{allp} **{top}**: "
                                f"{pct(t.top_share)} of serves "
                                f"(CI {pct(r.loc[top, 'share_lo'])} to {pct(r.loc[top, 'share_hi'])}), "
                                f"league {pct(r.loc[top, 'league_share'])}.")
                st.caption(f"Favorite-target share{allp} higher than {pct(t.fav_pct)} of listed "
                           f"{GENDER[gender].lower()} on this side.")
                if pd.isna(t.q_mix):
                    st.markdown("Not enough serves to test the direction mix against the league.")
                elif t.q_mix < Q:
                    st.markdown(f"Direction mix{allp} differs from the league for the same opponents "
                                f"(q {pq(t.q_mix)}).")
                else:
                    st.markdown(f"Direction mix{allp} does not differ significantly from the league for the "
                                f"same opponents (q {pq(t.q_mix)}).")
                shifts = []
                for d in DIRS:
                    if t[f"bp_{d}_q"] < Q:
                        normal = other_share(rows, side, d)
                        bp_share = rows[(rows.situation == "bp") & (rows.side == side) &
                                        (rows.direction == d)].share.iloc[0]
                        shifts.append(f"{d} {pct(normal)} to {pct(bp_share)}")
                if shifts:
                    st.markdown("Break-point shift (other points to break points): "
                                + ", ".join(shifts) + ".")
                if t.q_win < Q:
                    used = [d for d in DIRS if r.loc[d, "n"] >= 30]
                    rates = ", ".join(f"{d} {pct(r.loc[d, 'win'])}" for d in used)
                    st.markdown(f"Win rates differ by direction on this side: points won after {rates}. "
                                f"Sending every serve to {t.best_dir} could be worth at most "
                                f"{t.gain_pp:.1f} points per 100 serves. This is an upper bound: returners "
                                "would adjust, and it picks the best of several noisy rates.")
                elif pd.isna(t.q_win):
                    st.markdown("Not enough serves to test whether directions pay differently.")
                else:
                    st.markdown("Win rates do not differ significantly by direction after correction.")

    st.markdown("**Points won after serving each direction**")
    st.plotly_chart(win_chart(rows[rows.situation == "all"]), config={"displayModeBar": False})
    verdicts = []
    for side in SIDES:
        t = tst.loc[side]
        if pd.isna(t.p_win):
            verdicts.append(f"{side.title()}: not enough serves to test")
            continue
        verdicts.append(f"{side.title()}: " + (f"**win rates differ by direction** (p {pq(t.p_win)}; "
                                               f"after correcting for {int(tests.q_win.notna().sum()):,} "
                                               f"tests, q {pq(t.q_win)})"
                                               if t.q_win < Q else
                                               f"no significant difference after correcting for "
                                               f"{int(tests.q_win.notna().sum()):,} tests "
                                               f"(p {pq(t.p_win)}, q {pq(t.q_win)})"))
    st.caption("; ".join(verdicts) + ". Game theory says a server mixing optimally wins the same share "
               "with every direction; if one direction wins more, theory says the server should use it more. "
               "Tests compare serves within the same match."
               + (" 1st-serve win rates include points where the 1st serve missed." if serve == 1 else ""))

    with st.expander("Break points vs. other points (all directions)"):
        bp_tab = []
        for side in SIDES:
            t = tst.loc[side]
            for d in DIRS:
                a = rows[(rows.situation == "all") & (rows.side == side) & (rows.direction == d)].iloc[0]
                b = rows[(rows.situation == "bp") & (rows.side == side) & (rows.direction == d)].iloc[0]
                bp_tab.append({"Side": side, "Direction": d, "Other points": pct(other_share(rows, side, d)),
                               "Break points": pct(b.share), "Break-point serves": int(b.n),
                               "Change vs. other points": pp(t[f"bp_{d}"]),
                               "Significant (q < 0.05)": "yes" if t[f"bp_{d}_q"] < Q else "no"})
        st.dataframe(pd.DataFrame(bp_tab), hide_index=True)
        st.caption("Significance compares break points with other points in the same match and is corrected "
                   "for every player, side and direction tested.")

    with st.expander("Data table"):
        show = rows[["side", "situation", "direction", "n", "share", "share_lo", "share_hi", "league_share",
                     "win", "win_lo", "win_hi", "league_win"]].copy()
        show.loc[show.n == 0, ["win_lo", "win_hi"]] = np.nan  # no serves: no win interval to show
        st.dataframe(show, hide_index=True, column_config={
            c: st.column_config.NumberColumn(format="percent") for c in show.columns
            if c.startswith(("share", "win", "league"))})
        st.caption("On break-point rows (situation bp), league_share and league_win are league, all points "
                   "values: the league expectation is not split by break point.")

# ---------------------------------------------------------------- league findings

with tab_league:
    g1, g2, g3 = st.columns(3)
    lg = g1.selectbox("Tour ", ["m", "w"], format_func=GENDER.get)
    mu = g2.selectbox("Server vs. returner", ["RvR", "RvL", "LvR", "LvL"],
                      format_func=lambda m: {"RvR": "Righty vs. righty", "RvL": "Righty vs. lefty",
                                             "LvR": "Lefty vs. righty", "LvL": "Lefty vs. lefty"}[m])
    ls = g3.selectbox("Serve ", [1, 2], format_func=lambda s: "1st serve" if s == 1 else "2nd serve")
    d = league_dirs[(league_dirs.gender == lg) & (league_dirs.matchup == mu) & (league_dirs.serve_num == ls)]

    w1 = ws(lg, ls, "win body minus win wide")
    w2 = ws(lg, ls, "win T minus win wide")
    i1 = ws(lg, ls, "serve in body minus serve in wide")
    if ls == 1:
        st.markdown("##### 1. Wide and T win about the same; body wins less")
    else:
        gap = max(abs(w1.coef_pp), abs(w2.coef_pp))
        st.markdown(f"##### 1. On 2nd serves every direction wins within {gap:.1f} points of wide")
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, subplot_titles=["Deuce side", "Ad side"],
                        horizontal_spacing=0.06)
    for c, side in enumerate(SIDES, start=1):
        r = d[(d.side == side) & (~d.is_break_point)].set_index("direction").reindex(DIRS)
        fig.add_trace(go.Scatter(
            x=DIRS, y=r.win * 100, mode="markers+text", marker=dict(size=11, color=PLAYER),
            error_y=dict(type="data", symmetric=False, array=(r.win_hi - r.win) * 100,
                         arrayminus=(r.win - r.win_lo) * 100, color=PLAYER, thickness=2, width=6),
            text=[f"{v:.1f}%" for v in r.win * 100], textposition="middle right",
            customdata=np.stack([r.share * 100, r.n], axis=1),
            hovertemplate="%{x}: %{y:.1f}% won<br>used on %{customdata[0]:.1f}% of serves"
                          "<br>%{customdata[1]:,} serves<extra></extra>", showlegend=False), row=1, col=c)
    fig.update_yaxes(ticksuffix="%", gridcolor="rgba(128,128,128,0.15)")
    fig.update_xaxes(range=[-0.5, 2.6])
    fig.update_yaxes(title_text="Points won by the server", row=1, col=1)
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, config={"displayModeBar": False})
    st.caption(f"Non-break points, with 95% confidence intervals. Comparing serves by the same server on the "
               f"same side (all matchups), body minus wide: {w1.coef_pp:+.1f} pts won (95% CI {w1.lo_pp:+.1f} to "
               f"{w1.hi_pp:+.1f}), even though body serves go in {i1.coef_pp:+.1f} pts more often. "
               f"T minus wide: {w2.coef_pp:+.1f} pts won (CI {w2.lo_pp:+.1f} to {w2.hi_pp:+.1f}).")

    st.markdown("##### 2. On break points, servers go wide a little more")
    rows_bp = []
    for side in SIDES:
        for d_ in DIRS:
            for bp in [False, True]:
                r = d[(d.side == side) & (d.direction == d_) & (d.is_break_point == bp)].iloc[0]
                rows_bp.append(dict(side=side, direction=d_, bp=bp, share=r.share, lo=r.share_lo, hi=r.share_hi))
    rb = pd.DataFrame(rows_bp)
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, subplot_titles=["Deuce side", "Ad side"],
                        horizontal_spacing=0.06)
    for c, side in enumerate(SIDES, start=1):
        for bp, color, name in [(False, LEAGUE, "Other points"), (True, PLAYER, "Break points")]:
            r = rb[(rb.side == side) & (rb.bp == bp)]
            fig.add_trace(go.Bar(
                x=r.direction, y=r.share * 100, name=name, marker=dict(color=color, cornerradius=4),
                error_y=dict(type="data", symmetric=False, array=(r.hi - r.share) * 100,
                             arrayminus=(r.share - r.lo) * 100, color=LEAGUE),
                hovertemplate="%{x}: %{y:.1f}% of serves<extra>" + name + "</extra>",
                showlegend=(c == 1), legendgroup=name), row=1, col=c)
    fig.update_yaxes(ticksuffix="%", gridcolor="rgba(128,128,128,0.15)")
    fig.update_yaxes(title_text="Share of serves", row=1, col=1)
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10), barmode="group", bargap=0.3,
                      bargroupgap=0.08, legend=dict(orientation="h", y=-0.15, x=0))
    st.plotly_chart(fig, config={"displayModeBar": False})
    bw = ws(lg, ls, "break_point: change in wide share", "match x server x side")
    bb = ws(lg, ls, "break_point: change in body share", "match x server x side")
    st.caption(f"Same server, same match, same side (all matchups): on break points the wide share moves "
               f"{bw.coef_pp:+.1f} pts (CI {bw.lo_pp:+.1f} to {bw.hi_pp:+.1f}) and body {bb.coef_pp:+.1f} pts "
               f"(CI {bb.lo_pp:+.1f} to {bb.hi_pp:+.1f}). Most player-sides show no significant break-point "
               f"shift; the flagged ones are marked in the scout tab.")

    st.markdown("##### 3. Favorite-target share repeats across halves; the T-minus-wide win gap does not")
    sb = stab[stab.gender == lg].copy()
    fig = go.Figure()
    for metric, color, name in [("guess", PLAYER, "Favorite-target share (how lopsided the mix is)"),
                                ("edge", LEAGUE, "Win-rate gap, T minus wide")]:
        r = sb[sb.metric == metric].set_index("side").reindex(SIDES)
        fig.add_trace(go.Bar(x=[s.title() + " side" for s in SIDES], y=r["corr"], name=name,
                             marker=dict(color=color, cornerradius=4),
                             text=[f"{v:.2f}" for v in r["corr"]], textposition="outside",
                             hovertemplate="%{x}: r = %{y:.2f}<extra>" + name + "</extra>"))
    fig.update_yaxes(range=[-0.1, 1], title_text="Correlation between halves",
                     gridcolor="rgba(128,128,128,0.15)", zeroline=True, zerolinecolor=LEAGUE)
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), barmode="group", bargap=0.35,
                      legend=dict(orientation="h", y=-0.15, x=0))
    st.plotly_chart(fig, config={"displayModeBar": False})
    st.caption(f"Each player's matches split at random into two halves; 1st serves; "
               f"{int(sb.players.iloc[0])} players. A stable trait shows up in both halves (r near 1); noise "
               "does not (r near 0). How lopsided a server's mix is replicates. The T-minus-wide win gap "
               "mostly does not, so for most players there is little evidence of a stable win-rate edge.")

    st.markdown("##### 4. Servers switch direction a little more often than a random order would")
    sr = serial[serial.gender == lg].set_index("serve_num")
    a, b = st.columns(2)
    for col, sn in [(a, 1), (b, 2)]:
        r = sr.loc[sn]
        col.metric(f"{'1st' if sn == 1 else '2nd'} serve: repeats the last direction (same side)",
                   pct(r.repeat_rate, 1), f"{r.diff_pp:+.1f} pts vs. random order", delta_color="off",
                   help=f"95% CI for the difference: {r.lo_pp:+.1f} to {r.hi_pp:+.1f} pts.")
    st.caption("A server who fully randomizes repeats as often as a shuffled version of their own serves. "
               "Switching too often is a known human bias (Walker and Wooders 2001). Here the difference is small.")

# ---------------------------------------------------------------- how it works

with tab_how:
    n_men, n_women = int((players.gender == "m").sum()), int((players.gender == "w").sum())
    st.markdown(f"""
**The question.** A serve has three targets: *wide*, *body* or *T* (down the middle). If a server always went
to the same target, returners would wait there. Game theory says the best server mixes so that every target
wins the same share of points, which leaves nothing to exploit. For every player we ask two things: how
lopsided the direction mix is, and whether any direction wins significantly more than the others (a sign the
server could gain by using it more).

**The data.** {n_players} players ({n_men} men, {n_women} women) with at least 300 charted 1st serves on each
side and at least 10 charted matches, from Jeff Sackmann's crowd-sourced
[Match Charting Project](https://github.com/JeffSackmann/tennis_MatchChartingProject): 2.56 million
charted serves coded point by point by volunteers. We rebuilt one row per serve and checked it against the
project's own serve-direction totals: they match exactly for 99.0 to 99.6% of player-matches. The charts
record where each serve went (wide, body or T). They do not record where the returner stood or which way
they leaned.

**How we read it.**
- Deuce and ad side are always separate, and a player is compared with the league average *for the same mix
  of right- and left-handed returners*.
- Favorite-target share is the share of serves to the server's most-used target. If a returner always leaned
  toward that target, they would be right this often; a server who split evenly across three targets would
  give one in three. We call a server predictable on a side when the direction mix is lopsided and differs from the
  league average for the same opponents (mix test, q < 0.05).
- Every share and win rate has a 95% confidence interval that allows for serves in the same match being alike.
- With {int(tests.q_win.notna().sum()):,} player by side by serve win tests, some would look significant by
  luck. Every flag in this app survives a 5% false discovery rate correction (Benjamini-Hochberg), and the
  tests (Cochran-Mantel-Haenszel) compare serves *within the same match*. Directions used fewer than 30 times
  are left out of the win test.
- Splitting each player's matches into two random halves shows which numbers are stable traits.
- The 1st-serve win rate counts the whole point, including points where the 1st serve missed, because that is
  the payoff of choosing a direction.

The formulas are in the method section of the
[project README](https://github.com/ijunsu2082-hue/tennis-serve-iq#method).

**Limits.**
- Charted matches are mostly big matches of well-known players, chosen by volunteers, not a random sample.
- Tiebreak points (3%), serves with no recorded direction (0.2%) and no-ad deciding points (where the court
  side is unknown) are left out.
- "Win rate by direction" is not a controlled experiment: a server may pick a direction *because* of the
  returner's position. The "could be worth" figure is an upper bound, because returners would adapt.
- A test that finds no significant difference does not show that every direction wins equally: a small edge
  may be too small to detect with this much data.

**Data license.** Match Charting Project data by Jeff Sackmann and contributors (Tennis Abstract), licensed
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). The derived tables in this app carry
the same license. Code: MIT, see the repository.
""")
