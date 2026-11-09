# Tennis Match Analytics

Drop in a match video and get back a point-by-point chart, player stats and plain-English insights for both players.

## Quick start

This folder is part of the Serve IQ repository. The test video, the virtual environment and the YOLO weights are not included: the weights download on first run, and you supply your own match video.

```bash
cd match_model
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tennis analyze your_match.mp4
open out/your_match/report.html
```

You can also drop videos into `inbox/` and run `.venv/bin/python -m tennis inbox`.

Results go to `out/<video name>/`:

| File | What it is |
|---|---|
| `report.html` | Self-contained report. It works offline and prints to PDF. |
| `points.csv` | One row per point, for Excel or Sheets. |
| `match.json` | Everything, machine-readable. |
| `cache/` | Saved stage results. Re-running the same video takes seconds. |

### Options

```bash
.venv/bin/python -m tennis analyze match.mp4 \
    --a-name "Smith" --b-name "Jones"    # names (default: read from the broadcast score bug)
    --a-hand R --b-hand L                # playing hand, used for forehand/backhand (if left out:
                                         # right is assumed, and the report says it was not entered)
    --no-ad                              # college no-ad scoring
    --final-set match_tb10               # 10-point match tiebreak instead of a 3rd set
    --quick                              # score and serve/return stats only, no ball tracking
    --force                              # ignore the cache
```

### Fixing anything the computer got wrong (review UI)

```bash
.venv/bin/python -m tennis review out/videoplayback
```

This opens http://127.0.0.1:8765. Each point plays automatically, and you fix a field with one key press:

| Key | Action |
|---|---|
| `j` / `k` | Next / previous point |
| `space` | Replay the point |
| `a` / `b` | Point winner |
| `1` / `2` | Serve the point was played on |
| `w` / `o` / `t` | Serve zone: wide / body / T |
| `f` / `h` | Return wing: forehand / backhand |
| `F` / `H` | Last-shot wing |
| `e` | Cycle the ending (winner / unforced / forced / ace / double fault) |
| `Enter` | Mark reviewed and move to the next point |

Saving updates the report straight away. Corrections live in `corrections.json` and survive re-analysis. The review server only listens on this computer (a loopback address such as 127.0.0.1), only answers requests addressed to it (localhost or a loopback address), and refuses saves sent from other web sites. To re-render after hand-editing corrections:

```bash
.venv/bin/python -m tennis rebuild out/<name>
```

### Asking questions about a match (chat)

```bash
.venv/bin/python -m tennis chat out/<name>          # ask in the terminal
.venv/bin/python -m tennis chat out/<name> --web    # a small page on http://127.0.0.1:8766
```

The bot (Claude, model claude-opus-5-5) answers only from `out/<name>/match.json`, through four tools that read the file: serve directions by player, side, serve number and score situation; a serve summary per player; a point finder; and one point's details. It keeps eye-checked values (set or confirmed in a review) and automatic values apart, gives the count with every share ("7 of 11 (64%)"), will not call a group with fewer than 10 serves of known direction a habit, and explains when a question cannot happen (40-0 is always played from the ad side, so "40-0 on the deuce side" gets an explanation instead of numbers). It says so when the data cannot answer.

It needs the `anthropic` package (in `requirements.txt`) and an API key: a line `ANTHROPIC_API_KEY=...` in the `.env` file in the folder above `match_model` (or `--env-file PATH`, or the `ANTHROPIC_API_KEY` environment variable). The key is read when the chat starts, is sent only to the Anthropic API and is never printed or logged. Each question costs a few API calls (usually 1 to 3). `--usage-log FILE` writes one line per call with token counts, and `--max-calls N` stops after N calls. The page only listens on 127.0.0.1 and refuses requests from other sites.

## How it works (and why it's fast)

The pipeline never feeds every frame of a 3-hour video to a neural network. Each stage only does what it needs to:

1. **Scene scan** (`scenes.py`): decodes at 5 fps, 160x90, about 70x realtime. It learns this video's main broadcast camera and separates it from close-ups, crowd shots and replays.
2. **Scoreboard OCR** (`scoreboard.py`): Apple Vision finds the score bug (the score board drawn on the broadcast). Player names may have accented letters (BÁEZ, ŠVÁBOVÁ); names written in mixed case (Sinner) are not taken as board names yet. Each digit cell is then read with self-learned templates, and only when the bug changes, plus once more right after, so a reading caught in the middle of a board animation is not kept. On the board styles it was tested on, this gives the exact score, the server and who won every point; other styles can be read wrongly (see Tests), and a board that is found but read badly gives a warning in the log and the report. It runs in parallel with step 1.
3. **Score engine** (`scoring.py`): the full tennis rules (ad/no-ad, tiebreaks, match tiebreak, serve rotation, change of ends, break/set/match points). It reconciles the OCR readings, flags skipped points (highlights or missed reads) and never invents them.
4. **Court homography** (`court.py`): classical line detection. The reprojection error is under 1 px on the test video.
5. **Players** (`players.py`): YOLO11 person detection inside rally windows only. Ultralytics' NMS time limit is lifted, because on a busy machine it silently drops player boxes and makes results depend on system load.
   - Which player is at which end (A/B identity): the score engine already knows when the players change ends (after odd games, in tiebreaks, between sets), so within a stretch of continuous score only one thing is unknown, which end each player started at. Each rally window votes on that from the serve set-up seen just before its first serve-like hit (both players near their baselines, the server closer to the centre mark and the receiver further out wide). A stretch with at least 5 confident votes is turned the way 75% or more of them say; a stretch with fewer votes takes the whole match's vote (at least 3 votes, 75% one way). On the ABN AMRO 2023 match one stretch covered all 279 points and 191 of 196 votes said to flip the engine's starting ends.
   - Only when no vote decides does the older path run: a probe at the window start plus a shirt and shorts colour model.
6. **Ball and events** (`ball.py`, `events.py`): fast classical ball tracking at full frame rate inside rally windows. It finds hits, bounces and serves. Bounces are converted to court metres, which gives the serve zone (wide/body/T) and the fault type (long/wide/net).
7. **Charting** (`chart.py`): per point, it produces serves, return wing, rally length, last shot and ending.
   - Broadcasts often cut away after a 1st-serve fault and show the 2nd serve in a new shot. Every earlier main-camera shot between two score changes is treated as a serve attempt, so those points are charted as "1st serve fault, played on the 2nd serve".
   - The point winner from the score bug constrains the ending: it must be a winner/ace by the point winner or an error/double fault by the other player.
   - If the loser hit the last detected ball but it clearly came back into play, the winner's reply was missed by the ball tracker. It is added as a winner, so a missed final shot no longer turns a winner into an error.
8. **Analytics** (`analytics.py`) and **report** (`report.py`):
   - stats by serve number, side and pressure situation
   - serve habits (`patterns.py`): for each player, where serves landed (T near the middle line, body in the middle third of the box, wide near the side line, or unknown) by deuce and ad side, first and second serve, and score situation (break point, game point, 40-0, 0-40, 30-30, deuce, advantage either way, tiebreak, first point of a game), plus first serve in and double faults. Every share comes with its count, a group under 10 serves is marked too small to call a habit, and values checked in a review are counted apart from automatic ones. Saved in `match.json` as `serve_patterns` and shown in the report's "Serve habits" section.
   - insights with sample-size rules
   - tactical recommendations

## Speed (Apple M1, 8 GB)

| Video | Time |
|---|---|
| 11-min broadcast clip (`videoplayback.mp4`) | 93 s end to end (measured with the machine heavily loaded); the vision stage alone 37 s. Older than the Nov 7 changes and not measured again. On the Nov 7 code it took 184.16 s from scratch, but other heavy programs ran (1-minute load 9.48 and 10.01 at the two samples taken), so that is not a speed result |
| Re-run with cache | under 1 s |
| ABN AMRO 2023 qualifying, Halys against Barrere (168.8 min, 30 fps, 279 points), Nov 7 2026 | 2697.68 s (45 min) from scratch, peak memory 5.39 GB. Not a clean number: other heavy programs ran at the same time (load up to 215 for 5 minutes) and the Mac went on battery near the end |
| 10-minute part of the ABN AMRO match, code before and after the Nov 7 fixes | 185.6 s before, 174.6 s after (the middle of two runs each), on battery with Low Power Mode on, so rough |
| 3-hour file (176 min, 160 points: the test clip joined end to end 16 times, so a speed and memory test, not a new match) | 16 min from scratch, peak memory 600 MB (measured with background load, before the Nov 7 changes, and not measured again). Scene scan, OCR and court took about 5.5 min; vision about 10.5 min (about 4 s per point). `--quick` skips vision. |

## Accuracy: what to trust

Each point in `match.json` has a confidence (`conf`) for each of its main fields, and each serve has its own `conf`. The point winner also has a source (`winner_source`: ocr, engine, vision or manual). The report marks low-confidence cells that were not checked in a review, and its "About the data" section gives how much of each field is known.

Measured against a hand-made shot-by-shot chart of all 10 points of the test video (59 shots). The rules were tuned on these same points, so treat the numbers as a best case. Results on a full match checked by AI labelers follow the table ("Full-match check"). "Stress test" re-runs every point 5 times with the player-detection crop shifted by a few pixels, the kind of variation any new video brings, so it estimates accuracy on footage the rules were not tuned on better than the single run does.

| Field | How it's obtained | Single run | Stress test (50 runs) |
|---|---|---|---|
| Score, server, point winner, break/set/match points | scoreboard OCR + rules engine | 10/10 (79/79 score-bug frames) | not affected |
| Deuce/ad side, change of ends, A/B identity | engine + serve geometry + colour model | 10/10 (also 160/160 on the 16x looped 3-hour file) | not affected |
| 1st serve in vs fault (incl. faults shown in their own camera shot) | serve-attempt shots + ball bounce | 9/10 points | 49/50 |
| Fault type (net / long / wide) | bounce in court metres | 3/5 exact, 4/5 roughly right (long vs long-wide) | n/a |
| Serve zone (wide / body / T) | bounce, else receiver position | 8/10 | 42/50 |
| Return wing (forehand / backhand) | ball side of the body | 9/9 | 39/45 |
| Who hit the last shot | hit detection + the known point winner | 10/10 | 50/50 |
| Last-shot wing | ball side of the body | 9/10 | 40/50 |
| Where the last shot landed (in / net / out) | bounce in court metres | 9/9 | 41/45 |
| How the point ended (winner / forced / unforced / ace / double fault) | hits + bounce + the known point winner | 10/10 | 49/50 |
| Rally length | hit count | 7/10 exact | 35/50 exact, usually ±1 to 2 |

**Trust rule:** fields with confidence ≥ 0.6 were right 96% of the time (172/180) in the stress test. Lower-confidence cells are shaded in the report; check those in the review UI. Forced vs unforced is a judgement call even for human charters.

Known hard cases: serve, return and volley merging into one ball path in the image (rally counted short), lobs that leave the frame, and stray ball detections on a player's arm.

### Full-match check (Nov 2026)

One full match, ABN AMRO 2023, was checked point by point by AI agents looking at video frames (three blind labelers, and two blind settlers where they disagreed), not by a person. It was split by whole games into tune games (the serve rules were tuned on these) and held-out games (scored after the rules were frozen). A second match, whose video may not be shared, was also checked on a sample of points; its results stay on this computer and are not in this repo.

| | ABN AMRO, tune games | ABN AMRO, held-out games |
|---|---|---|
| Score, server, end, winner | 169 of 169 | 108 of 108, but the score reader was built on the whole match, so this is not a test on unseen games |
| Real serves found | 128 of 236 | 74 of 144 |
| Serves reported that did not happen | 42 of 170 | 43 of 120 |
| In or fault, right when answered | 99 of 117 | 50 of 65 |
| Direction (T, body, wide), right when answered | 47 of 50 | 24 of 30 |
| Every direction shown, right | 47 of 65 | 24 of 49 |

Most serve numbers are lower on the held-out games, but only two of the gaps are bigger than chance often gives (two-sided Fisher exact test): every direction shown (chance gives a gap that big about 1 time in 50) and serves reported that did not happen (about 1 time in 20). Serves found (about 6 times in 10), in or fault (about 1 time in 4) and direction when answered (about 1 time in 14) could be chance. So the serve rules may fit the tune games too closely, but one match cannot show it for sure. Serve directions from the computer alone are not reliable yet; check them in the review UI.

All these numbers are for 1280x720 video. The ball and shot rules measure any other video in pixels of a 720-row frame, so a 1080p or 4K copy of the same scene gives the same result (checked on match 1's saved detections made 1.5 and 3 times bigger, and on a drawn clip); no real 1080p or 4K match has been tested.

The report opens with a card for each player: strengths, weaknesses and how to play against them, as short lines with the numbers in small type. The card only uses notes whose gap chance would give less than 1 time in 5 (`ACTION_MAX_P` in `report.py`) and shows how sure each one is; every note is still listed under "More detail".

Notes need at least 5 points of evidence and a gap of at least 15 percentage points (for where serves landed: between the most common spot and the next one). Where a serve landed is compared first serve against first serve and second against second. Each note gets high, medium or low confidence from a chance test of its gap (high: chance would give a gap that big less than 1 time in 100; medium: less than 1 time in 20; low: more often, under 10 points, or low tracking confidence), and the report explains this. The plan is made only from high and medium notes. Each note, plan line and statistics row says whether its values were checked in a review or come from the computer alone. A full match gives many notes; an 11-minute highlight gives almost none, by design.

## Videos without a score bug (college streams, fixed cameras)

This mode is beta.
- The whole video is treated as the main camera.
- Points are split by player activity, and the server and side come from serve set-up geometry. A short serve-only clip followed by another serve from the same player and side is merged as a 1st-serve fault.
- Tested by covering the score bug on the test video: all 10 points and all five 1st-serve faults are found, but the server can drift on highlight clips because there are no game starts to re-anchor on.
- Point winners are inferred from the last shot, and the score is replayed from 0-0, so the review UI is strongly recommended.
- Player A is the player at the near end at the first serve.
- It assumes the video starts at the beginning of the match.

## Existing open-source work (researched October 2026)

No public repo does full automatic point-by-point charting with score, serve direction and winner/error. We checked each one and reused ideas where they helped:

- **yastrebksv/TennisProject, TrackNet, TennisCourtDetector**: ball heatmaps and court keypoints. There's no license, they pin torch 1.5 and the weights are on Google Drive. Our classical court fit reaches sub-pixel error without weights.
- **abdullahtarek/tennis_analysis**: YOLO player/ball detection plus speed stats. It has no scoring logic.
- **ArtLabss/tennis-tracking**: TrackNet plus bounce classifier on a TensorFlow 2.6 stack, which is slow and impractical on Apple Silicon.
- **qaz812345/TrackNetV3**: trained on badminton shuttlecocks. Its gap-filling idea is noted for future use.
- **philippdubach/tennis-vision**: the closest design (broadcast OCR plus event detection). The author reports F1 0.54 on events and 46 to 91% OCR parsing at 720p.
- **JeffSackmann/tennis_MatchChartingProject**: the charting notation. Our `mcp` codes follow it: 4/5/6 for serve direction; f/b, v and o for shots; n/w/d/x for errors; * @ # for outcomes.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

The tests check against ground truth hand-labelled from the older test video (`videoplayback.mp4`):
- 400 main-camera labels
- 79 score-bug readings
- 283 ball positions
- full shot-by-shot charts of all 10 points

and against values read by AI agents looking at the frames (not by a person) from the full ABN AMRO 2023 match (Halys against Barrere), which has another score board style:
- 42 real score-bug crops (`tests/test_scoreboard_crops.py`, needs no video). The crop pictures are cut from match 1's video, so `match_model/.gitignore` keeps them out of git; only `labels.json` can be shared, and without the pictures these tests skip themselves.
- the whole score of that match: 279 scores and 280 points (set `TENNIS_MATCH2_VIDEO` to that match's video file to also run the reader on all of it; the variable is named for the second match the reader was tested on)

Drawn score boards (`tests/test_scoreboard_layouts.py`, `tests/test_scoreboard_styles.py`) cover a points-first college style with made-up names and accented names, a board with no highlighted cell that shows 0 0 at the start of each game, and a serve dot drawn close to the name. A board whose points sit last in a highlighted cell with dark digits is found with the right column roles, but its two-digit points are not yet read reliably. When a board is found but read badly (no score read, the serve marker never read, or the score often jumping), the run log and the report give a warning.

`tests/test_review_fixes2.py` also checks that a 1080p or 4K copy of a real match 1 window gives the same ball track and the same serves as the 720p original, and that `match.json` names the video without this computer's folders (the full path is kept in `out/<match>/cache/`, which is not shared).

## Project layout

```
tennis/            pipeline modules (contracts.py = shared data types)
review/index.html  review / correction UI
docs/DESIGN.md     architecture
tests/             unit + ground-truth tests, fixtures/
inbox/  out/       drop videos here / results
```
