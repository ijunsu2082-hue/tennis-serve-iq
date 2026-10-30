# Serve IQ

**Where pro tennis players serve, and whether any direction wins more points.**

Serve IQ uses 2.56 million charted professional serves from the Match Charting Project. After the exclusions
listed under Limits, 2,473,821 serves are analyzed. Pick any of 447 players
(249 men, 198 women) to see where they serve on each side of the court, how that compares with the league,
how it changes on break points, and whether any direction wins more points than the others. Shares and win
rates come with 95% confidence intervals.

Live site: https://serve-iq.netlify.app (backup: https://serve-iq.streamlit.app)

![Serve IQ site](docs/screenshot.png)

## Main findings

| Finding | Evidence |
|---|---|
| Most serve mixes differ from the league. | On 1st serves, 604 of 894 player-sides (68%) have a direction mix that differs from the league average for the same opponents; on 2nd serves, 61.7%. A player's favorite-target share in one random half of their matches correlates with the other half (r = 0.57 to 0.69). |
| Few players show a win-rate gap by direction. | 24 of 447 players have a court side where 1st-serve win rates differ by direction (false discovery rate 5%), and none on 2nd serves. The T-minus-wide win gap does not replicate across halves of a player's matches (r = -0.02 to 0.17). |
| Body serves win fewer points. | Comparing serves by the same server on the same side, 1st-serve body serves go in 9.9 percentage points more often than wide ones (men; women 8.5), yet the server wins 4.39 fewer points per 100 (men, 95% CI 3.90 to 4.87 fewer; women 4.09 fewer). On 2nd serves the gap is small: 0.60 fewer for men, 0.76 fewer for women. |
| Break points shift the mix a little. | On break points servers go wide 1.2 to 1.7 percentage points more often on 1st serves (same server, match and side). 62 player by side by serve patterns show a significant shift. Example: Rafael Nadal's deuce-side 1st serve goes wide on 30.0% of other points and 45.0% of break points. |
| Servers switch more than a random order would. | Men's 1st serves repeat their previous direction 1.60 percentage points less often than a random order would; women's 4.89 less. |

Most pros have a recognizable serve habit. Only a few of them have a court side where win rates differ by
direction. The dashboard shows who those players are and has a scouting report for every listed player.

The charts only record where each serve went (wide, body or T). They do not record the returner. The
favorite-target share is the share of serves to a server's most-used target: if a returner always leaned
toward that target, they would be right this often, and a server who split evenly across three targets would
give one in three. We call a server predictable on a side when the mix is lopsided and differs from the league average
for the same opponents (mix test, q < 0.05).

## What's in the dashboard

- Scout a server: a schematic court showing where the serve goes on the deuce and ad side, compared with the
  league average for the same mix of right- and left-handed returners; a scouting report with the favorite
  target, any break-point habit and whether any direction wins significantly more; points won by direction
  with confidence intervals. The court drawing is a schematic: the data code each serve as wide, body or T,
  not where it landed.
- League findings: direction mix and win rates by matchup, the body-serve gap, break-point shifts, the
  split-half check and serial patterns.
- How it works: methods and limits.

In break-point mode the league comparison values are all-points league values, labeled "league, all
points", because the league expectation is not split by break point.

## Run it

Streamlit app (the backup site):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

The app reads only the small precomputed tables in `app/data/` (about 1.5 MB), so it runs without the raw
data. Tested locally on Python 3.9.6; deployed on Streamlit Community Cloud with Python 3.12. The free
Streamlit tier puts the backup app to sleep when nobody has used it for a while; if you see a "wake up"
button, click it and wait about a minute.

Static site: serve the `site/` folder with any static server, for example
`python3 -m http.server --directory site`. Its data files are rebuilt with `python src/export_site.py`.

Deploy the Streamlit app: on [share.streamlit.io](https://share.streamlit.io), create an app from this
repository with main file `app/streamlit_app.py` and Python 3.12 under Advanced settings (the pinned numpy
and scipy have no Python 3.13+ wheels).

## Rebuild everything from the raw data

```bash
git clone https://github.com/JeffSackmann/tennis_MatchChartingProject data   # raw charting files
python src/build_serves.py   # writes output/serves.parquet, one row per serve
python src/reconcile.py      # checks against the project's own serve-direction totals
python src/baseline.py       # league-wide baseline with two-way clustered CIs
python src/app_data.py       # writes app/data/, per-player tables and tests for the app
python src/export_site.py    # writes the data files for the static site
```

## Method

- One row per serve. Each charted point gives a 1st serve and, if it missed, a 2nd serve: direction (wide,
  body or T), in or fault, score, court side, break point, who won the point, and both players' handedness.
  Cleaning steps (duplicate charts, slips in the codes, swapped handedness labels, no-ad deciding points) are
  documented in `src/build_serves.py` and print their counts when run.
- Our totals match the project's own per-match serve-direction stats exactly for 99.0 to 99.6% of
  player-matches. The remaining player-matches include points we treat differently on purpose, such as no-ad
  deciding points, where the court side is unknown.
- Deuce and ad side are always separate. A player is compared with the league average re-weighted to their
  own mix of right- and left-handed opponents.
- A player is listed with at least 300 charted 1st serves on each side and at least 10 charted matches.
  Directions with fewer than 30 serves are left out of the win test, and break-point samples under 100 serves
  get a warning.
- The payoff of a 1st serve is the whole point, including points where the 1st serve missed (Walker and
  Wooders 2001). Game theory says an optimal server wins the same share with every direction.
- Shares and win rates have 95% CIs clustered by match (the wider of clustered and binomial). League CIs are
  two-way clustered by server and returner.
- Per-player tests (does the mix differ from the league, do win rates differ by direction, did the mix shift
  on break points) control the false discovery rate at 5% with Benjamini-Hochberg within each family: 1,788
  mix tests, 1,777 win tests and 5,364 break-point tests (pooled over directions). The win and break-point
  tests are Cochran-Mantel-Haenszel tests that compare serves within the same match. 84 break-point tests are
  significant, on 62 player by side by serve patterns.
- Each player's matches are split at random into halves. A stable trait shows up in both halves.

### Formulas

The numbering follows the method section of the site. The hypothesis being tested is that every direction
wins the same share of points, $w_{\mathrm{wide}} = w_{\mathrm{body}} = w_T$, which is what minimax play
predicts (Walker and Wooders 2001).

#### Estimates

Equation (1), player share and win rate (code: `ratio` and `summarise` in `src/app_data.py`). A player's
matches $g = 1, \dots, G$ are pooled. For a share, $y_g$ counts serves to direction $d$ and $m_g$ counts serves
in the situation; for a win rate, $y_g$ counts points won after serving $d$ and $m_g$ counts serves to $d$. The
standard error treats each match as a cluster but is never smaller than the binomial one:

$$\hat p = \frac{\sum_g y_g}{N}, \qquad N = \sum_g m_g, \qquad
\widehat{\mathrm{SE}} = \max\left( \sqrt{\frac{G}{G-1} \sum_g \left( \frac{y_g - \hat p\, m_g}{N} \right)^2},\ \sqrt{\frac{\hat p (1 - \hat p)}{N}} \right)$$

$$\mathrm{CI} = \left[ \max\left(0,\ \hat p - t_{0.975,\, G-1}\, \widehat{\mathrm{SE}}\right),\ \min\left(1,\ \hat p + t_{0.975,\, G-1}\, \widehat{\mathrm{SE}}\right) \right]$$

Equation (2), favorite-target share and its percentile (code: `summarise` in `src/app_data.py`; percentile in
`load` in `src/export_site.py`). The favorite-target share $f$ is the share of serves to the most-used
direction on all points. The percentile is the share of the other listed players in the same tour by serve by
side group whose $f$ is lower:

$$f = \max_{d} \hat s_d, \qquad
\mathrm{pct}_i = \frac{\text{number of players } j \ne i \text{ with } f_j < f_i}{n_{\mathrm{group}} - 1}$$

Equation (3), league expectation for the same opponents (code: `summarise` in `src/app_data.py`). League
shares and win rates for each handedness matchup $k$ are weighted by the player's own share $\pi_k$ of serves
in that matchup. The league values pool break points and other points and include the player:

$$\bar s_d = \sum_k \pi_k\, s^{L}_{k,d}, \qquad \bar w_d = \sum_k \pi_k\, w^{L}_{k,d}$$

Equation (4), break-point shift (code: `summarise` in `src/app_data.py`). The share of serves to direction $d$
on break points minus the share on all other points:

$$\Delta_d = \frac{n^{BP}_d}{N^{BP}} - \frac{n^{\lnot BP}_d}{N^{\lnot BP}}$$

Equation (5), upper-bound gain (code: `summarise` in `src/app_data.py`). Points per 100 serves the server
would gain by sending every serve to the direction that won most for them, among directions with at
least 30 serves. It is shown only when the win test is significant, and it is an upper bound: it ignores that returners
adapt and it picks the best of several noisy rates:

$$\mathrm{gain} = 100\, \left( \hat w_{d^*} - \hat w_{\mathrm{all}} \right), \qquad d^* = \arg\max_{d:\ n_d \ge 30} \hat w_d$$

#### Tests and corrections

Equation (6), mix compared with the league (code: `summarise` in `src/app_data.py`). A Wald test of whether the
wide and T shares (body is the remainder) differ from the league expectation, with a match-clustered
covariance. The league values are treated as fixed and there is no binomial floor:

$$u_g = \frac{1}{N} \begin{pmatrix} n_{g,\mathrm{wide}} - m_g \hat s_{\mathrm{wide}} \\ n_{g,T} - m_g \hat s_T \end{pmatrix}, \qquad
\hat V = \frac{G}{G-1} \sum_g u_g u_g^{\top}, \qquad
\delta = \begin{pmatrix} \hat s_{\mathrm{wide}} - \bar s_{\mathrm{wide}} \\ \hat s_T - \bar s_T \end{pmatrix}$$

$$W = \delta^{\top} \hat V^{-1} \delta, \qquad p = \Pr\left( F_{2,\, G-1} > W / 2 \right)$$

Equation (7), generalized Cochran-Mantel-Haenszel test (code: `cmh` in `src/app_data.py`). It tests whether a
success rate is equal across $k$ groups while comparing serves only within the same match $h$ (matches with
more than one serve). In the win test the groups are directions with at least 30 serves and success is winning
the point; in the break-point test the groups are break points and other points and success is serving to
$d$. $n_{hi}$ is the number of serves in group $i$, $y_{hi}$ the successes, and $T_h$ and $S_h$ the match
totals. The variance is the exact conditional variance given each match's margins; the p-value is the
asymptotic chi-square one:

$$D_i = \sum_h \left( y_{hi} - n_{hi} \frac{S_h}{T_h} \right), \qquad
V_{ij} = \sum_h \frac{S_h (T_h - S_h)}{T_h - 1} \left( \frac{n_{hi}}{T_h} \delta_{ij} - \frac{n_{hi}\, n_{hj}}{T_h^2} \right),\ \ i, j \le k-1, \qquad
Q = D^{\top} V^{-1} D \sim \chi^2_{k-1}$$

Equation (8), Benjamini-Hochberg false discovery rate (code: `bh` in `src/app_data.py`). With the $m$ p-values
of a family sorted so that $p_{(1)} \le \dots \le p_{(m)}$, a test is flagged when its q-value is below 0.05,
which keeps the expected share of false flags at 5%. Each family is pooled across tours, serves and sides:

$$q_{(i)} = \min_{j \ge i} \min\left( 1,\ \frac{m\, p_{(j)}}{j} \right)$$

#### League-wide estimates

Equation (9), two-way clustered variance (code: `twoway_meat` in `src/baseline.py`). League standard errors
allow for correlation within the same server, the same returner and the same server-returner pair. If the
combined matrix is not positive semidefinite, the one-way matrix with the larger trace is used; degrees of
freedom are the smaller of the server and returner cluster counts minus one:

$$M_c = \frac{G_c}{G_c - 1} \sum_{g \in c} \Big( \sum_{i \in g} u_i \Big) \Big( \sum_{i \in g} u_i \Big)^{\top}, \qquad
M = M_{\mathrm{srv}} + M_{\mathrm{ret}} - M_{\mathrm{srv} \times \mathrm{ret}}$$

Equation (10), league direction intervals (code: `ratio_ci` in `src/baseline.py`). League shares and win rates
use the two-way clustered variance above, with no binomial floor:

$$u_i = \frac{y_i - \hat p\, n_i}{N}, \qquad
\hat p \pm t_{0.975,\, \min(G_s, G_r) - 1} \sqrt{M_{11}}, \ \text{clipped to } [0, 1]$$

Equation (11), within-server comparisons (code: `within_server` in `src/baseline.py`). A linear probability
model with a separate intercept for each fixed-effect group: server by side by matchup by break point for
points won and serve in, so the body coefficient compares body and wide serves by the same server in the same
situation; and match by server by side for the break-point shift, where the outcome is "served to $d$" and the
regressors are break-point and game-point indicators. $K$ counts only the regressors:

$$\tilde y_i = y_i - \bar y_{f(i)}, \quad \tilde x_i = x_i - \bar x_{f(i)}, \quad
\hat \beta = (\tilde X^{\top} \tilde X)^{-1} \tilde X^{\top} \tilde y$$

$$u_i = \tilde x_i \left( \tilde y_i - \tilde x_i^{\top} \hat \beta \right), \qquad
\hat V = \frac{N-1}{N-K} (\tilde X^{\top} \tilde X)^{-1} M (\tilde X^{\top} \tilde X)^{-1}$$

Equation (12), serial repeats (code: `serial_test` in `src/baseline.py`). Within each match by server by side
group (one tour and serve number, in point order, at least two serves), the number of serves that repeat the
previous direction is compared with the number expected if the same serves came in random order:

$$R_h = \text{number of } t \text{ with } d_{h,t} = d_{h,t-1}, \qquad
E_h = \frac{\sum_k n_{hk} (n_{hk} - 1)}{n_h}, \qquad
\mathrm{diff} = \frac{\sum_h (R_h - E_h)}{\sum_h (n_h - 1)}$$

#### Replication

Equation (13), split-half stability (code: `stability` in `src/app_data.py`). Each player's matches are split
at random once, with a fixed seed, into halves A and B. The favorite-target share $f$ and the T-minus-wide win
edge $e$ are computed in each half from plain proportions on 1st serves and correlated across players within
tour and side. This is one split, with no confidence interval:

$$f^{A}_i = \max_d \hat s^{A}_{i,d}, \qquad e^{A}_i = \hat w^{A}_{i,T} - \hat w^{A}_{i,\mathrm{wide}}, \qquad
r = \mathrm{corr}_i\left( f^{A}_i, f^{B}_i \right)$$

## Limits

- Charted matches are chosen by volunteers: mostly big matches of well-known players, not a random sample.
- Win rate by direction is observational. A server may choose a direction *because* of the returner's
  position, and returners would adapt to any change, so "could be worth" figures are upper bounds.
- The data do not record the returner's position or lean, only where the serve went.
- A test that finds no significant difference does not show that every direction wins equally: a small edge
  may be too small to detect with this much data.
- Tiebreak points (3%), serves with no recorded direction (0.2%) and no-ad deciding points are left out.

## Repository

```
app/streamlit_app.py   the Streamlit dashboard (backup site)
app/data/              precomputed tables the app reads (CC BY-NC-SA 4.0, see below)
site/                  the static site
src/build_serves.py    raw charting files to one row per serve
src/reconcile.py       checks the rebuild against the project's own totals
src/baseline.py        league-wide baseline, clustered CIs, within-server effects
src/app_data.py        per-player tables, tests and stability checks for the app
src/export_site.py     data files for the static site
```

## Data and license

Data: [The Tennis Abstract Match Charting Project](https://github.com/JeffSackmann/tennis_MatchChartingProject)
by Jeff Sackmann and contributors, licensed
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). The derived tables in `app/data/`
are shared under the same license. Non-commercial use only.

Code in this repository (`app/`, `src/`) is released under the MIT License; see `LICENSE`.
