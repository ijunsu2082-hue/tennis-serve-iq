"""Shared data contracts for the tennis analytics pipeline.

Every module imports from here; nothing else is shared between modules.
All times are seconds from the start of the video. Court coordinates are in
metres in the ITF singles/doubles model:

    origin = centre of the net, x across the court (+x = right side as seen
    from the near/bottom end of the main camera), y along the court
    (+y = towards the FAR baseline, -y = towards the NEAR baseline).
    Baselines at y = +/-11.885, singles sidelines x = +/-4.115,
    doubles sidelines x = +/-5.485, service lines y = +/-6.40, centre line x = 0.

Player identity is always "A" or "B". By default A = the top row of the
broadcast score bug (or the name given in the config), B = the bottom row.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Literal
import json

PlayerId = Literal["A", "B"]
End = Literal["near", "far"]
Side = Literal["deuce", "ad"]

# ---- court model constants (metres) ---------------------------------------
HALF_LEN = 11.885
SINGLES_HALF_W = 4.115
DOUBLES_HALF_W = 5.485
SERVICE_LINE = 6.40


def other(p: str) -> str:
    return "B" if p == "A" else "A"


# ---- video / scenes --------------------------------------------------------
@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    duration: float


@dataclass
class Segment:
    """A contiguous camera shot. kind == 'main' means the high main camera
    that shows the whole court (the only view used for vision charting)."""
    start: float
    end: float
    kind: Literal["main", "other"]
    conf: float = 1.0


# ---- scoreboard -------------------------------------------------------------
@dataclass
class BugLayout:
    """Where the broadcast score bug lives, in full-frame pixel coords."""
    x0: int
    y0: int
    x1: int
    y1: int
    names: list[str]                 # [top_row_name, bottom_row_name]
    rows: list[tuple[int, int]]      # y-range per row (full-frame px)
    cols: list[tuple[int, int]]      # x-range per score column (full-frame px), left->right
    dot_rois: list[tuple[int, int, int, int]] = field(default_factory=list)  # (x0,y0,x1,y1) per row


@dataclass
class ScoreObs:
    """One debounced reading of the score bug.

    sets: completed-set games per row, e.g. [[7, 6], [4, 6]] means
          row0 won set1 7-6, lost set2 4-6. Current set games are in `games`.
    points: current game points as strings per row ("0","15","30","40","A")
            or integer strings in a tiebreak.
    server: 0 or 1 (row index with the serve dot), None if unknown.
    """
    t: float
    sets: list[list[int]]
    games: list[int]
    points: list[str]
    server: Optional[int]
    in_tb: bool = False
    conf: float = 1.0
    raw: str = ""


# ---- scoring engine ---------------------------------------------------------
@dataclass
class MatchFormat:
    best_of: int = 3
    no_ad: bool = False              # college (NCAA) dual matches use no-ad
    games_per_set: int = 6
    tb_at: int = 6                   # tiebreak when games reach tb_at-tb_at
    tb_points: int = 7
    final_set: Literal["tb", "match_tb10", "adv"] = "tb"
    no_ad_deciding_side: Literal["deuce", "ad"] = "ad"   # side the receiver picks at no-ad deuce


@dataclass
class ScoreState:
    sets: list[list[int]] = field(default_factory=list)   # completed sets, [gamesA, gamesB]
    games: list[int] = field(default_factory=lambda: [0, 0])  # [A, B]
    points: list[int] = field(default_factory=lambda: [0, 0])  # raw point counts in game/TB
    server: PlayerId = "A"
    in_tb: bool = False
    tb_first_server: Optional[PlayerId] = None
    ends: dict = field(default_factory=lambda: {"A": "near", "B": "far"})
    match_over: bool = False


@dataclass
class PointBoundary:
    """A point identified by the engine (from OCR or inferred winners)."""
    index: int
    t_end: float                      # time the point was decided (score change seen)
    t_start: Optional[float]          # start of the point's rally window (if known)
    winner: Optional[PlayerId]
    server: PlayerId
    score_before: dict                # {"sets":[[a,b],...],"games":[a,b],"points":"30-15","in_tb":bool}
    score_after: dict
    side: Side
    flags: dict                       # {"break_point":bool,"game_point":bool,"set_point":bool,"match_point":bool}
    gap_before: bool = False          # True if points were skipped (highlight cut / missed read)
    conf: float = 1.0
    source: str = "ocr"               # ocr | engine | vision | manual


# ---- court / players / ball -------------------------------------------------
@dataclass
class CourtFit:
    """Homography from image pixels (in the given frame size) to court metres."""
    H: list[list[float]]              # 3x3, image(px) -> court(m)
    frame_w: int
    frame_h: int
    err_px: float
    conf: float


@dataclass
class PlayerObs:
    t: float
    pid: PlayerId
    end: End
    bbox: tuple[float, float, float, float]   # x0,y0,x1,y1 in full-frame px
    court_xy: tuple[float, float]             # foot point in metres
    conf: float = 1.0


@dataclass
class BallObs:
    t: float
    xy_px: tuple[float, float]                # full-frame px
    conf: float = 1.0


@dataclass
class ShotEvent:
    t: float
    kind: Literal["serve", "hit", "bounce"]
    by: Optional[PlayerId] = None              # hitter for serve/hit
    court_xy: Optional[tuple[float, float]] = None   # bounce location (m) or hitter foot point
    wing: Optional[Literal["fh", "bh"]] = None
    stroke: Optional[Literal["groundstroke", "volley", "overhead", "serve"]] = None
    conf: float = 0.5


@dataclass
class ServeVision:
    n: int                                     # 1 or 2
    t: float
    result: Literal["in", "fault", "unknown"]
    zone: Optional[Literal["wide", "body", "T"]] = None     # where it landed / was aimed
    fault: Optional[Literal["net", "long", "wide", "long_wide"]] = None
    bounce_xy: Optional[tuple[float, float]] = None
    speed_kmh: Optional[float] = None
    conf: float = 0.5


@dataclass
class PointVision:
    """Everything vision could extract for one point window."""
    t_start: float
    t_end: float
    serves: list[ServeVision] = field(default_factory=list)
    shots: list[ShotEvent] = field(default_factory=list)
    rally_len: Optional[int] = None            # number of shots incl. serve (MCP convention)
    return_wing: Optional[str] = None          # fh | bh
    last_shot_by: Optional[PlayerId] = None
    last_shot_wing: Optional[str] = None
    last_shot_stroke: Optional[str] = None
    last_shot_result: Optional[Literal["in", "out_long", "out_wide", "net", "unknown"]] = None
    ending: Optional[Literal["ace", "service_winner", "double_fault", "winner",
                             "unforced_error", "forced_error", "unknown"]] = None
    inferred_winner: Optional[PlayerId] = None
    ends: Optional[dict] = None                # {"A":"near","B":"far"} observed
    net_approaches: dict = field(default_factory=dict)   # {"A": n, "B": n}
    player_dist_m: dict = field(default_factory=dict)    # metres run per player
    conf: dict = field(default_factory=dict)   # per-field confidence 0..1


# ---- final per-point record (what analytics / report / review consume) -----
@dataclass
class PointRecord:
    id: int
    t_start: Optional[float]
    t_end: float
    set_no: int
    score_before: dict
    server: PlayerId
    receiver: PlayerId
    side: Side
    flags: dict
    winner: Optional[PlayerId]
    winner_source: str
    serves: list[dict] = field(default_factory=list)   # asdict(ServeVision)
    serve_number_won_on: Optional[int] = None          # 1, 2, or None (double fault / unknown)
    return_wing: Optional[str] = None
    rally_len: Optional[int] = None
    ending: Optional[str] = None
    ending_by: Optional[PlayerId] = None               # who hit the ending shot
    last_shot_wing: Optional[str] = None
    last_shot_stroke: Optional[str] = None
    last_shot_result: Optional[str] = None
    ends: Optional[dict] = None
    net_approaches: dict = field(default_factory=dict)
    player_dist_m: dict = field(default_factory=dict)
    gap_before: bool = False
    conf: dict = field(default_factory=dict)
    mcp: str = ""
    review: dict = field(default_factory=lambda: {"status": "auto", "edited_fields": []})


def to_json(obj) -> str:
    def default(o):
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        raise TypeError(type(o))
    return json.dumps(obj, default=default, indent=2)
