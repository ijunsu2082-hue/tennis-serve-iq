# Serve IQ

**Scouting reports for 447 pro servers: where they serve, and whether any direction wins more points.**

## Inspiration
<!-- USER: rewrite in your own words -->
Returning serve is mostly guessing. I wanted to know whether pros have readable serving habits, and whether those habits cost them points.

## What it does
Most pros have a recognizable serve habit. Only a few of them have a court side where win rates differ by direction.

Scout a server: pick any of 447 players (249 men, 198 women). A schematic court shows where their 1st or 2nd serve goes on each side, next to the league average for the same mix of right- and left-handed opponents. The scouting report gives the favorite target, any break-point habit, and whether any direction wins significantly more.

The favorite-target share is the share of serves to a server's most-used target; an even split across three targets gives one in three. The charts only record where the serve went, not what the returner did.

League findings:
- On 1st serves, 604 of 894 player-sides (68%) have a direction mix that differs from the league for the same opponents.
- 24 of 447 players have a court side where 1st-serve win rates differ by direction (false discovery rate 5%); none on 2nd serves.
- Same server, same side: body 1st serves go in 9.9 percentage points more often than wide ones (men; women 8.5), yet the server wins 4.4 fewer points per 100 (men) and 4.1 fewer (women).
- On break points the wide share of 1st serves rises 1.2 to 1.7 percentage points (same server, match and side). Rafael Nadal's deuce-side 1st serve goes wide on 30.0% of other points and 45.0% of break points.
- Men's 1st serves repeat their previous direction 1.60 percentage points less often than a random order would; women's 4.89 less.

## How we built it
Data: Jeff Sackmann's Match Charting Project, rebuilt as one row per serve (2.56 million charted serves). We left out tiebreak points (3%), serves with no recorded direction (0.2%) and no-ad deciding points, which leaves 2,473,821 serves. Our totals match the project's own per-match serve-direction totals exactly for 99.0 to 99.6% of player-matches. Players need at least 300 charted 1st serves on each side and 10 charted matches.

Cochran-Mantel-Haenszel tests compare serves within the same match, with Benjamini-Hochberg false discovery rate control at 5%. Confidence intervals are clustered by match. The site's Method section and the README show every formula.

Python precomputes small tables. The site is hand-written HTML, CSS and JavaScript with KaTeX, hosted on Netlify, with a Streamlit backup. Built with help from Claude Code, an AI coding assistant.

## Challenges
With thousands of tests, some players look unusual by luck. We corrected every test family for false discoveries and split each player's matches in half to see what repeats. The favorite-target share repeats (r = 0.57 to 0.69). The T-minus-wide win gap does not (r = -0.02 to 0.17), so even the 24 flagged win-rate differences may not hold up in new matches.

## Accomplishments
We kept the main result even though it is partly null: most players do not show a win-rate gap by direction. Every one of the 447 players has a scouting report with confidence intervals.

## What we learned
<!-- USER: rewrite in your own words -->
Being predictable is not the same as being beatable, and a pattern only counts if it survives a fair test.

## What's next
Return-side scouting and printable player reports. Limits: volunteers choose which matches to chart, and win rates by direction are observational.

## Built with
Python, pandas, NumPy, SciPy, pyarrow, HTML, CSS, JavaScript, KaTeX, Netlify, Streamlit, Plotly

## Links
- Live site: https://serve-iq.netlify.app
- Backup: https://serve-iq.streamlit.app
- Code: https://github.com/ijunsu2082-hue/tennis-serve-iq

## Data and license
Match Charting Project data by Jeff Sackmann and contributors, CC BY-NC-SA 4.0; derived tables share that license. Code: MIT.
