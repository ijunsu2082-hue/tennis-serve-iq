"""Export the match model's demo clip and the "serves needed" numbers to site/data/match.json.

Reads ONLY:
  - match_model/out/videoplayback/match.json          the model's chart of its 10-point development clip
  - match_model/tests/fixtures/gt_points_vision.json  the chart of the same 10 points made by eye
  - site/data/serveiq.json                            the pro data the site already uses (for "need")

Standard library only (Python 3.9). Never imports the match model's code (tennis/), never reads the video,
and writes no paths, video fields or bounce coordinates.

Points are paired in order: model point i is chart point i (both cover the same rally windows; server and
side must agree, or the export stops). The served ball is the last serve whose result is "in" in the chart.
Its lane is the model's zone for the same serve number, or null when the model left it blank.

need: for every 1st-serve player-side with a favorite lane that leads the runner-up (d = p1 - p2 > 0),
  serves needed  n = z^2 (p1 + p2 - d^2) / d^2    (the favorite's lead clears its 95% range)
  matches needed = n * matches / serves            (that side's own serves per match)
stored as quartiles (statistics.quantiles, n=4, default "exclusive" method), with the median number of
1st serves a side gets per match. The same formula runs in site/js/tool.js for one side.

Run (Python 3.9):  .venv/bin/python src/export_match.py
"""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "match_model" / "out" / "videoplayback" / "match.json"
CHART = ROOT / "match_model" / "tests" / "fixtures" / "gt_points_vision.json"
PROS = ROOT / "site" / "data" / "serveiq.json"
OUT = ROOT / "site" / "data" / "match.json"

Z = 1.96
DIRS = ["wide", "body", "T"]
SIDES = ["deuce", "ad"]


def sig(x, k=5):
    return float(f"{x:.{k}g}")


def played(serves):
    """the chart's serve that went in (the last one), or None"""
    ins = [s for s in serves if s.get("result") == "in"]
    return ins[-1] if ins else None


def clip():
    model = json.loads(MODEL.read_text(encoding="utf-8"))
    chart = json.loads(CHART.read_text(encoding="utf-8"))
    mp, cp = model["points"], chart["points"]
    if len(mp) != len(cp):
        raise SystemExit(f"model has {len(mp)} points, chart has {len(cp)}")
    names = {k: v["name"] for k, v in model["meta"]["players"].items()}
    points = []
    for i, (m, c) in enumerate(zip(mp, cp)):
        if m["server"] != c["server"] or m["side"] != c["side"]:
            raise SystemExit(f"point {i}: model and chart disagree on server or side")
        cs = played(c["serves"])
        ms = None
        if cs is not None:
            for s in m["serves"]:
                if s.get("n") == cs["n"]:
                    ms = s
        model_lane = ms.get("zone") if ms else None
        chart_lane = cs["zone"] if cs else None
        points.append({
            "n": i + 1,
            "server": names[m["server"]],
            "side": m["side"],
            "serve": cs["n"] if cs else None,
            "model": model_lane,
            "chart": chart_lane,
            "same": None if model_lane is None or chart_lane is None else model_lane == chart_lane,
            "winner_same": m["winner"] == c["winner"],
        })
    served = [p for p in points if p["chart"] is not None]
    totals = {
        "points": len(points),
        "served": len(served),
        "named": sum(1 for p in served if p["model"] is not None),
        "matched": sum(1 for p in served if p["same"] is True),
        "winners": sum(1 for p in points if p["winner_same"]),
        "break_points": sum(1 for m in mp if m["flags"].get("break_point")),
    }
    return {"players": [names["A"], names["B"]], "points": points, "totals": totals}


def need():
    raw = json.loads(PROS.read_text(encoding="utf-8"))
    mf = {f: i for i, f in enumerate(raw["mix"]["fields"])}
    tf = {f: i for i, f in enumerate(raw["tests"]["fields"])}
    serves, matches, per_match = [], [], []
    for key, mix in raw["mix"]["data"].items():
        if not key.endswith("|1"):
            continue
        test = raw["tests"]["data"][key]
        for side in SIDES:
            t = test[side]
            n_side, m_side = t[tf["n"]], t[tf["matches"]]
            per_match.append(n_side / m_side)
            sh = sorted((mix[side]["all"][d][mf["share"]] for d in DIRS), reverse=True)
            p1, p2 = sh[0], sh[1]
            d = p1 - p2
            if d <= 0:
                continue
            n = Z * Z * (p1 + p2 - d * d) / (d * d)
            serves.append(n)
            matches.append(n * m_side / n_side)
    q = lambda a: [sig(x) for x in statistics.quantiles(a, n=4)]
    return {
        "z": Z,
        "sides": len(serves),
        "serves_q": q(serves),
        "matches_q": q(matches),
        "serves_per_match": sig(statistics.median(per_match)),
    }


def build():
    out = clip()
    out["need"] = need()
    return out


def main():
    out = build()
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    t, n = out["totals"], out["need"]
    print(f"wrote {OUT.relative_to(ROOT)}: {t['named']} of {t['served']} served lanes named, "
          f"{t['matched']} matched, winners {t['winners']} of {t['points']}, break points {t['break_points']}")
    print(f"need: {n['sides']} sides, serves quartiles {n['serves_q']}, matches {n['matches_q']}, "
          f"{n['serves_per_match']} 1st serves per side per match")


if __name__ == "__main__":
    main()
