# Serve IQ

**Scouting reports for 447 pro servers: where they serve, and whether it costs them.**

## Inspiration
<!-- USER: rewrite in your own words -->
Returning serve is mostly guessing. I wanted to know whether pros have readable serving habits, and whether those habits cost them points.

## What it does
The takeaway for a returner or coach: pros are predictable, but that predictability almost never costs them anything we can measure. Three things are worth acting on: each player's scouting report, the body-serve gap, and break-point shifts for flagged players.

**Scout a server.** Pick any of 447 players (249 men, 198 women). A court diagram shows where their 1st or 2nd serve goes on each side, next to the league average for the same mix of right- and left-handed opponents. A plain-English report gives the favourite direction per side, any break-point habit, and whether any direction wins measurably more (95% confidence intervals).

**League findings.**
- Predictable: 68% of player × court-side 1st-serve patterns differ from the league average for the same opponents. The habit replicates across random halves of a player's matches (r = 0.57 to 0.69).
- Rarely costly: only 24 of 447 players have a court side where some 1st-serve direction wins measurably more; none on 2nd serves.
- Body serve: same server, same side, body serves land in 9.9 points more often than wide ones, yet the server wins 4.4 fewer points per 100 (men) and 4.1 fewer (women).
- Break points: the wide share rises 1.7 points and the body share falls 1.1 points (same server, match and side). 62 player × side × serve patterns shift significantly; Rafael Nadal's deuce-side wide serve goes from 30% to 45%.
- Mixing: servers repeat their previous direction 1.6 points less often than a shuffled order.

**How it works.** Methods and limits, in plain English.

## How we built it
Data: Jeff Sackmann's Match Charting Project, volunteer point-by-point charts of pro matches, rebuilt as one row per serve (2.56 million serves) and checked against the project's own totals. Players need at least 300 charted 1st serves on each side and 10 charted matches.

Deuce and ad are always separate, and each player is compared with the league re-weighted to their mix of right- and left-handed opponents. Per-player Cochran–Mantel–Haenszel tests compare serves within the same match, with Benjamini–Hochberg false discovery rate control at 5% across families of 1,777 to 5,364 tests. 95% confidence intervals are clustered by match; a split-half check tests replication. Tiebreak points (3%) and serves with no recorded direction (0.2%) are excluded.

The Streamlit and Plotly app reads small precomputed tables, so it runs without the raw data. This project was built with help from Claude Code, an AI coding assistant.

## Challenges
Not fooling ourselves: with thousands of tests, some players look exploitable by luck. The win-rate edge did not replicate across halves (r = −0.02 to 0.17), so we say "rarely costly" instead of promising weaknesses.

## Accomplishments
Every number has a confidence interval, the headline stays honest though partly null, and a coach with no statistics background can read a scouting report in a minute.

## What we learned
<!-- USER: rewrite in your own words -->
Being predictable is not the same as being beatable, and a pattern only counts if it survives a fair test.

## What's next
Return-side scouting, printable player reports, and updates as new matches are charted.

Limits: volunteers choose which matches to chart (mostly big matches of well-known players), win rates by direction are observational, and "could be worth" figures are upper bounds because returners would adapt.

## Built with
Python, pandas, NumPy, SciPy, pyarrow, Streamlit, Plotly

## Links
- Live app: https://serve-iq.streamlit.app
- Code: https://github.com/ijunsu2082-hue/tennis-serve-iq

## Data and license
Data: the Match Charting Project by Jeff Sackmann and contributors, licensed CC BY-NC-SA 4.0. Derived tables share that license; non-commercial use only. Code: MIT.

*Note: the free Streamlit tier sleeps when idle; click "wake up" and wait a minute.*
