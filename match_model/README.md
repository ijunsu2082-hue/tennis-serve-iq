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
    --a-hand R --b-hand L                # handedness, used for forehand/backhand
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

Saving updates the report straight away. Corrections live in `corrections.json` and survive re-analysis. To re-render after hand-editing corrections:

```bash
.venv/bin/python -m tennis rebuild out/<name>
```

## How it works (and why it's fast)

The pipeline never feeds every frame of a 3-hour video to a neural network. Each stage only does what it needs to:

1. **Scene scan** (`scenes.py`): decodes at 5 fps, 160x90, about 70x realtime. It learns this video's main broadcast camera and separates it from close-ups, crowd shots and replays.
2. **Scoreboard OCR** (`scoreboard.py`): Apple Vision finds the score bug. Each digit cell is then read with self-learned templates, and only when the bug changes. This gives the exact score, the server and who won every point. It runs in parallel with step 1.
3. **Score engine** (`scoring.py`): the full tennis rules (ad/no-ad, tiebreaks, match tiebreak, serve rotation, change of ends, break/set/match points). It reconciles the OCR readings, flags skipped points (highlights or missed reads) and never invents them.
4. **Court homography** (`court.py`): classical line detection. The reprojection error is under 1 px on the test video.
5. **Players** (`players.py`): YOLO11 person detection inside rally windows only. Ultralytics' NMS time limit is lifted, because on a busy machine it silently drops player boxes and makes results depend on system load.
   - A/B identity is anchored by the scoreboard's server dot plus serve set-up geometry (the server stands at the centre mark, the receiver out wide).
   - That anchor trains a shirt/shorts colour model, which keeps identity through blur and changes of ends.
6. **Ball and events** (`ball.py`, `events.py`): fast classical ball tracking at full frame rate inside rally windows. It finds hits, bounces and serves. Bounces are converted to court metres, which gives the serve zone (wide/body/T) and the fault type (long/wide/net).
7. **Charting** (`chart.py`): per point, it produces serves, return wing, rally length, last shot and ending.
   - Broadcasts often cut away after a 1st-serve fault and show the 2nd serve in a new shot. Every earlier main-camera shot between two score changes is treated as a serve attempt, so those points are charted as "1st serve fault, played on the 2nd serve".
   - The point winner from the score bug constrains the ending: it must be a winner/ace by the point winner or an error/double fault by the other player.
   - If the loser hit the last detected ball but it clearly came back into play, the winner's reply was missed by the ball tracker. It is added as a winner, so a missed final shot no longer turns a winner into an error.
8. **Analytics** (`analytics.py`) and **report** (`report.py`):
   - stats by serve number, side and pressure situation
   - insights with sample-size rules
   - tactical recommendations

## Speed (Apple M1, 8 GB)

| Video | Time |
|---|---|
| 11-min broadcast clip (`videoplayback.mp4`) | 93 s end to end (measured with the machine heavily loaded); the vision stage alone 37 s |
| Re-run with cache | under 1 s |
| 3-hour file (176 min, 160 points: the test clip joined end to end 16 times, so a speed and memory test, not a new match) | 16 min from scratch, peak memory 600 MB (measured with background load). Scene scan, OCR and court took about 5.5 min; vision about 10.5 min (about 4 s per point). `--quick` skips vision. |

## Accuracy: what to trust

Every field in `match.json` carries a confidence (`conf`) and a `source` (ocr / engine / vision / manual). The report highlights low-confidence cells and has a Data Quality section.

Measured against a hand-made shot-by-shot chart of all 10 points of the test video (59 shots). The rules were tuned on these same points and no second match has been charted yet, so treat the numbers as a best case. "Stress test" re-runs every point 5 times with the player-detection crop shifted by a few pixels, the kind of variation any new video brings, so it estimates accuracy on footage the rules were not tuned on better than the single run does.

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
| Rally length | hit count | 7/10 exact | 35/50 exact, usually ±1–2 |

**Trust rule:** fields with confidence ≥ 0.6 were right 96% of the time (172/180) in the stress test. Lower-confidence cells are shaded in the report; check those in the review UI. Forced vs unforced is a judgement call even for human charters.

Known hard cases: serve, return and volley merging into one ball path in the image (rally counted short), lobs that leave the frame, and stray ball detections on a player's arm.

Insights only fire with at least 5 points of evidence and a gap of at least 15 percentage points, and they are marked "small sample" below 10. A full match gives many; an 11-minute highlight gives almost none, by design.

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
- **philippdubach/tennis-vision**: the closest design (broadcast OCR plus event detection). The author reports F1 0.54 on events and 46–91% OCR parsing at 720p.
- **JeffSackmann/tennis_MatchChartingProject**: the charting notation. Our `mcp` codes follow it: 4/5/6 for serve direction; f/b, v and o for shots; n/w/d/x for errors; * @ # for outcomes.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

The tests check against ground truth hand-labelled from the test video:
- 400 main-camera labels
- 79 score-bug readings
- 283 ball positions
- full shot-by-shot charts of all 10 points

## Project layout

```
tennis/            pipeline modules (contracts.py = shared data types)
review/index.html  review / correction UI
docs/DESIGN.md     architecture
tests/             unit + ground-truth tests, fixtures/
inbox/  out/       drop videos here / results
```
