# Tennis Match Analytics: Design (reviewed plan)

## Goal
Drop a match video into `inbox/`, run one command, and within minutes get:
- a point-by-point chart: score, server, side, break-point flags, serves (in/fault, fault type, zone), return wing, rally length, how the point ended, and who won
- player A/B scouting stats and plain-English insights
- an HTML report plus JSON/CSV
- a fast review UI to correct any field, which re-runs the analytics in seconds

The CLI is `python -m tennis analyze <video>`. Results are cached in `out/<video_stem>/`, so re-running only re-renders, which takes seconds.

## Honest accuracy model
No open-source project does full automatic point-by-point charting (we checked TennisProject, TrackNet, TennisCourtDetector, abdullahtarek/tennis_analysis, ArtLabss, TrackNetV3, philippdubach/tennis-vision and the MCP). Each field therefore carries `conf` (0..1) and `source`.

| Field | Source | Expected reliability |
|---|---|---|
| score, server, point winner, break/set/match point | broadcast score-bug OCR + scoring engine | very high when a bug exists |
| deuce/ad side, end assignment | scoring engine | very high |
| rally window, duration | scene segmentation | high |
| player positions, distance run, net approaches | YOLO + court homography | high |
| serve in/fault, serve zone, fault type | ball track + bounce in court coordinates | medium |
| rally length, return wing, last-shot wing | ball/player geometry | medium-low |
| winner vs forced vs unforced | heuristics + known point winner | medium; review UI recommended |

Measured results (single run and a 50-run pixel-jitter stress test) are in the README. Fields at conf >= 0.6 were right 89% of the time in the stress test, so the review UI shades everything below that.

## Pipeline (each stage caches its output in `out/<stem>/cache/`)
1. **probe and scan** (`video.py`, `scenes.py`): decode at 5 fps, 160x90, using the fast flags. The scan is about 70x realtime, so a 3 h video takes about 2.5 min. It computes per-frame features, shot cuts and a main-camera classifier (learned per video: dominant court-view cluster plus a court-line check), and outputs `Segment[]`.
2. **score bug** (`scoreboard.py`, `vision_ocr.py`): auto-locate the bug with Apple Vision OCR on sampled frames. Then read only when the bug crop changes.
   - Vision reads names reliably but often drops isolated digits, so each score cell is read separately (crop, binarise, pad, upscale). We also bootstrap per-digit templates from confident Vision reads and fall back to template matching.
   - Superscript = tiebreak score of a finished set. The serve dot gives the server.
   - Debounce, then output `ScoreObs[]`.
3. **engine** (`scoring.py`): the full tennis rules (ad/no-ad, tiebreaks, match tiebreak, serve rotation, ends, flags).
   - `reconcile(ScoreObs[])` produces `PointBoundary[]` with winners. It resyncs on jumps: highlight videos skip points, and those boundaries get `gap_before`.
   - With no bug, `replay(winners)` uses vision-inferred winners.
4. **court** (`court.py`): classical white-line detection, then a homography to the ITF model, fitted once per main segment, refined per frame only if the camera moves.
5. **players** (`players.py`): YOLO11n persons at about 5 fps inside rally windows only.
   - Near/far assignment comes from the court projection.
   - Identity (A/B) comes from appearance clusters (torso/shorts colour), anchored by the scoring engine's server and ends.
6. **ball and events** (`ball.py`, `events.py`): classical full-fps ball detection inside rally windows (frame difference plus colour/shape plus trajectory linking), then hits, bounces and serves. Serve bounce in court metres gives the zone (wide/body/T) or the fault (long/wide/net).
7. **chart** (`chart.py`): per-point `PointVision`. The point winner from the score bug (`known_winner`) constrains the ending to a winner/ace by that player or an error/double fault by the other. When the loser hit the last detected ball but it came back into play (it landed in, or bounced deep on the loser's own half at least 0.6 s later after clearing the net), the winner's reply was missed: it is counted as a winner and its contact is estimated from the ball's closest approach to that player. Pose (yolo11n-pose) is only a fallback for the wing when the ball side is undecided.
8. **serve attempts** (`pipeline.py`): the rally window is the last main-camera stretch of at least 2 s before a score change. Earlier main-camera shots of at least 1.5 s in the same interval are serve attempts, charted separately and merged as the 1st-serve fault, with the rally serve renumbered as the 2nd serve.
9. **merge** (`pipeline.py`): join `PointBoundary` and `PointVision` into `PointRecord`. Inconsistent endings (an ace won by the receiver, an error by the point winner) are set to unknown. The engine's guessed starting ends are flipped when confident vision ends disagree.
10. **analytics, report, review** (`analytics.py`, `report.py`, `review_server.py`): stats, insights, HTML/CSV/JSON, and the review UI.

## Speed budget (3 h 720p on M1 8GB)
| Stage | Budget |
|---|---|
| scan | ~3 min |
| OCR | ~1 min |
| vision inside rally windows (~20% of the match) | ~8-10 min |
| everything else | seconds |

`--quick` runs score and stats only, in about 4 min.

## Conventions
See `tennis/contracts.py`. Court metres: origin at the net centre, +y toward the far baseline. A = top row of the score bug unless the config says otherwise.
