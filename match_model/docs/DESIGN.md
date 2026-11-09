# Tennis Match Analytics: Design (reviewed plan)

## Goal
Drop a match video into `inbox/`, run one command, and get (a 3-hour match took about 45 minutes on an M1 with 8 GB; see the speed budget):
- a point-by-point chart: score, server, side, break-point flags, serves (in/fault, fault type, zone), return wing, rally length, how the point ended, and who won
- player A/B stats and plain-English notes, each with a chance test of how sure it is
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
2. **score bug** (`scoreboard.py`, `vision_ocr.py`): auto-locate the bug with Apple Vision OCR on sampled frames. Then read when the bug crop changes and once more on the next sample. A bug with no points box reads 0-0 only if it shows the serve dot. With no dot it has been cleared between games and gives no reading.
   - Vision reads names reliably but often drops isolated digits, so each score cell is read separately (crop, binarise, pad, upscale). We also bootstrap per-digit templates from confident Vision reads and fall back to template matching.
   - Superscript = tiebreak score of a finished set. The serve dot gives the server.
   - Debounce, then output `ScoreObs[]`.
3. **engine** (`scoring.py`): the full tennis rules (ad/no-ad, tiebreaks, match tiebreak, serve rotation, ends, flags).
   - `reconcile(ScoreObs[])` produces `PointBoundary[]` with winners. It resyncs on jumps: highlight videos skip points, and those boundaries get `gap_before`.
   - With no bug, `replay(winners)` uses vision-inferred winners.
4. **court** (`court.py`): classical white-line detection, then a homography to the ITF model, fitted once per main segment, refined per frame only if the camera moves.
5. **players** (`players.py`): YOLO11n persons at about 5 fps inside rally windows only.
   - Near/far assignment comes from the court projection.
   - Identity (A/B), that is which end each player is at: the engine's ends (it changes ends by the rules) are turned the right way round per stretch of continuous score (`pipeline.score_stretches`, `orient_by_votes`). Each window votes with the serve set-up seen before its first serve-like hit (`window_vote`, `setup_before`; votes need confidence 0.8). A stretch with at least 5 votes follows its own votes when 75% go one way, else stays undecided; a stretch with fewer votes takes the whole match's decision (at least 3 votes, 75% one way). Only windows left undecided fall back to the older path: a probe at the window start and appearance clusters (torso and shorts colour).
6. **ball and events** (`ball.py`, `events.py`): classical full-fps ball detection inside rally windows (frame difference plus colour/shape plus trajectory linking), then hits, bounces and serves. Serve bounce in court metres gives the zone (wide/body/T) or the fault (long/wide/net).
   - Serve (`mark_serves`): the first hit by the server's end from behind the baseline that passes the set-up check (`chart.serve_setup_ok`: in the 2.5 s ending 0.5 s before the hit both players stand in set-up places on the halves the score requires and nearly still). When a window starts at a score change inside one camera shot, the check may read the last 3 s of the window before (`pipeline.setup_prev_of`).
   - Serve hit not seen (`_bounce_led_serves`): a bounce in the receiver's half, no deeper than the service line plus 3 m and inside the singles side lines plus 0.5 m, with no hit 0.15 to 1.2 s before it and outside the server's reach (his box stretched up by half a body height), stands for the serve, placed 0.45 s before the bounce, only when the set-up check says yes.
   - Second serve: another server hit from behind the baseline more than 3 s later. One receiver hit within 3 s of the serve is allowed (a fault knocked back); the next serve must then come more than 3 s after it and pass the set-up check. A later or second receiver hit ends the search.
   - Known limits: a second serve found without its first serve is called serve 1; the 0.45 s flight is a professional value and slow serves were not tested; a run resumed with part of the vision cache filled can differ from a fresh run by a serve or two, because the window after a cached one has no footage from the window before.
7. **chart** (`chart.py`): per-point `PointVision`. The point winner from the score bug (`known_winner`) constrains the ending to a winner/ace by that player or an error/double fault by the other. When the loser hit the last detected ball but it came back into play (it landed in, or bounced deep on the loser's own half at least 0.6 s later after clearing the net), the winner's reply was missed: it is counted as a winner and its contact is estimated from the ball's closest approach to that player. Pose (yolo11n-pose) is only a fallback for the wing when the ball side is undecided.
8. **serve attempts** (`pipeline.py`): the rally window is the last main-camera stretch of at least 2 s before a score change. Earlier main-camera shots of at least 1.5 s in the same interval are serve attempts, charted separately and merged as the 1st-serve fault, with the rally serve renumbered as the 2nd serve.
9. **merge** (`pipeline.py`): join `PointBoundary` and `PointVision` into `PointRecord`. Inconsistent endings (an ace won by the receiver, an error by the point winner) are set to unknown. The engine's guessed starting ends are flipped per stretch by the ends vote of stage 5.
10. **analytics, report, review** (`analytics.py`, `report.py`, `review_server.py`): stats, insights, HTML/CSV/JSON, and the review UI. A serve tendency is the most common landing spot against the next most common one (not against an even three-way split); where a serve landed is compared first serve against first serve and second against second. Each insight has a two-sided chance test of its gap (`analytics.fisher_p` against its comparison group, `binom_p` for a split between two spots): high confidence under 0.01, medium under 0.05, low otherwise or under 10 points or with low tracking confidence; insights are listed high, medium, low, small samples last, and recommendations are made only from high and medium ones. The report explains these words. Every insight, recommendation and statistics row carries whether its values were checked in a review (`analytics.field_checked`, `checked_status`). A point the score board skipped (`gap_before` on the point itself and no window of its own, `analytics.score_filled`) is shown as skipped, not as missing; the report's video times use `analytics.point_time` (first serve, else window start, else score change), as the chat does. The review server listens only on a loopback address, answers only requests whose Host header is localhost or a loopback address, and takes saves only as JSON from its own origin. `match.json` names the video by its file name (or its path inside the project); the full path is kept in `<out>/cache/video_path.txt`, where the report link and the review server find it. A playing hand that was not given (`--a-hand`, `--b-hand`) is taken as right and marked `hand_given: false`, and the report and the chat say so.

Frame size: the pixel rules after detection (ball linking in `ball.py`, `events.py`, `chart.py`) were set on 1280x720 video. Any other video is measured in pixels of a 720-row frame (`contracts.px_unit`): `events.to_ref_units` scales the ball track, the player boxes and the homography, and `events.events_to_px` turns the pixel values kept on events back. The far half of a taller video is read at its 720-row size. Two faults on a point the score board gives to the server cannot be a double fault, so that point's second serve and double fault are left unknown.
11. **serve habits** (`patterns.py`, run by `analytics.refresh`, so every render and rebuild recomputes it): per player, direction counts (T, body, wide, unknown) for serves that went in, by side (deuce, ad, both), serve number (1, 2, or number unknown for the eye check's `serves_number_unclear`) and situation (all, break point against the server, game point for the server, 40-0, 0-40, 30-30, deuce, advantage server, advantage receiver, tiebreak, first point; situations other than tiebreak are regular games only, server's score first). Lets (the eye check's `lets`) are counted apart. First serve in and double faults come from the same serve records; a second serve fault on a point the server won is counted as `score_disagrees`, not as a double fault. A serve is "checked" only when the point was reviewed, or the review touched that serve and every known value used from it (result, and direction when known) is in `review.edited_fields` or `review.checked_fields`; every other serve is "auto", and the two are counted apart. Groups with no serve are left out of `match["serve_patterns"]` (`patterns.group` fills them in). On the eye-checked output of the ABN AMRO 2023 match it gives the same numbers as a separate count made straight from the eye check's labels on 4412 of 4418 compared numbers; the 6 others come from two points the labelers did not record, which have no serves.

12. **chat** (`chat.py`, `python -m tennis chat <out_dir>`, not part of the pipeline): questions about one finished match, answered by Claude (claude-opus-5-5, adaptive thinking, effort high, streaming) through four strict tools that only read `match.json`: `serve_directions` (one player's serves in a situation, score, serve number and side group, counts and shares per source, points won after each direction, and `side_rule`, which says from the score whether the asked side is possible), `serve_summary`, `list_points` and `point_detail` (each value with its source: eye_checked, automatic or not known). Serve records are split into eye-checked and automatic with the same rule as `patterns.serve_source`, and the counts equal `match["serve_patterns"]` (checked in `tests/test_chat.py`). The first question carries an overview of the match (players, result worked out from the last recorded point, where each kind of value comes from, what the data does not have). History is append-only; a failed question is removed from the end. Every HTTP request is one call (SDK retries off; network, overload and rate limit errors are retried twice here, each try counted). The prompt and tool list are cached. The local page binds 127.0.0.1 and accepts questions only with its own header, a JSON body and a local Origin and Host.

## Speed budget (3 h 720p on M1 8GB)
The first plan was about 3 min for the scene scan, 1 min for the score board and 8 to 10 min for vision, on the idea that rally windows are about 20% of a match. Measured on the 168.8 min ABN AMRO 2023 match (Nov 7 2026): a point's window runs from the end of the point before to the score change, so the windows covered 9978.5 s of the 10130 s video, and the vision stage took 2539.0 s (about 42 min, on a busy machine) of a 2697.68 s run. Making vision skip the time between points is the main open speed task.

| Stage | Measured on the 3-hour match |
|---|---|
| scene scan and score board (run side by side) | 128.8 s and 153.8 s |
| court | 1.0 s |
| vision inside point windows (99% of this video) | 2539.0 s (busy machine) |
| everything else | seconds |

`--quick` runs score and stats only, in about 4 min.

## Conventions
See `tennis/contracts.py`. Court metres: origin at the net centre, +y toward the far baseline. A = top row of the score bug unless the config says otherwise.
