/* Serve IQ "Scout it from video" (#match). ES module.
   Shows the match model (match_model/ in the repository) as the charting step: a pipeline line, its chart of
   the 10-point development clip next to the chart made by eye, and the sample-size numbers that say how much
   video a habit needs. Data: data/match.json, written by src/export_match.py and checked by check 9 of
   scripts/check_site.py. Nothing here is a scouting result: the clip is too short and has no break points. */

import { esc, fInt, isNum } from "./data.js";
import { $ } from "./ui.js";

const LANE = { wide: "Out wide", body: "At the body", T: "Down the middle" };
const STEPS = [
  ["Match video", "a broadcast"],
  ["Score bug", "server, side, score"],
  ["Court lines", "court in meters"],
  ["Ball bounce", "where it came down"],
  ["Lane", "wide, body or middle"],
];
const REPO = "https://github.com/ijunsu2082-hue/tennis-serve-iq/tree/main/match_model";

function pipeline() {
  const W = 1000, H = 120, n = STEPS.length, gap = W / n;
  let g = "<svg class='pipe' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='" +
    esc("Pipeline: " + STEPS.map((s) => s[0]).join(", then ")) + "'>";
  g += "<line x1='" + gap / 2 + "' y1='34' x2='" + (W - gap / 2) + "' y2='34' stroke='#c9d3e3' stroke-width='3'/>";
  STEPS.forEach(([t, sub], i) => {
    const x = gap / 2 + i * gap;
    g += "<circle cx='" + x + "' cy='34' r='" + (i === n - 1 ? 15 : 11) + "' fill='" + (i === n - 1 ? "#e8f84a" : "#0b1d42") + "' stroke='#0b1d42' stroke-width='3'/>" +
      "<text x='" + x + "' y='76' text-anchor='middle' font-weight='800' font-size='19' fill='#0e1726'>" + esc(t) + "</text>" +
      "<text x='" + x + "' y='100' text-anchor='middle' font-weight='600' font-size='14' fill='#5b6577'>" + esc(sub) + "</text>";
  });
  g += "</svg>";
  // phones: the same steps as a list (the diagram is too small to read there)
  return g + "<ol class='pipe-list'>" + STEPS.map(([t, sub]) => "<li><b>" + esc(t) + "</b> " + esc(sub) + "</li>").join("") + "</ol>";
}

function strip(points) {
  return "<ol class='pts'>" + points.map((p) => {
    const cls = p.chart == null ? "na" : p.model == null ? "blank" : p.same ? "same" : "diff";
    const tag = cls === "same" ? "Same lane" : cls === "blank" ? "Left blank" : cls === "diff" ? "Different" : "No serve in";
    return "<li class='pt " + cls + "'><span class='pn tnum'>Point " + fInt(p.n) + "</span>" +
      "<span class='ps'>" + esc(p.server) + ", " + esc(p.side) + ", " + (p.serve === 2 ? "2nd" : "1st") + " serve</span>" +
      "<span class='pl'><small>Model</small>" + (p.model ? LANE[p.model] : "blank") + "</span>" +
      "<span class='pl'><small>Chart by eye</small>" + (p.chart ? LANE[p.chart] : "none") + "</span>" +
      "<span class='pt-tag'>" + tag + "</span></li>";
  }).join("") + "</ol>";
}

function html(M) {
  const t = M.totals, n = M.need;
  const all = t.matched === t.named ? "all " + fInt(t.named) : fInt(t.matched);
  const q = n.serves_q, mq = n.matches_q;
  return "<p class='lede'>For a player nobody charts, the first step is the chart itself. The match model reads a broadcast video and writes down, " +
    "point by point, who served, from which side, to which lane, and who won, in the same shorthand the pro charts use.</p>" +
    pipeline() +
    "<div class='claim'><p>On its " + fInt(t.points) + "-point development clip (" + esc(M.players.join(" and ")) + "), the match model named the lane for <b>" +
    fInt(t.named) + " of " + fInt(t.served) + "</b> serves, and " + all + " matched a chart made by eye. It read the point winner right on " +
    fInt(t.winners) + " of " + fInt(t.points) + ". That is the clip its rules were tuned on, not a blind test, and it has " +
    (t.break_points ? fInt(t.break_points) : "no") + " break points, so break-point changes are untested on video.</p>" +
    "<p>One match gives a side about <b>" + fInt(n.serves_per_match) + "</b> first serves. The median pro side needs about <b>" + fInt(q[1]) +
    "</b> to show its lean clearly (middle half " + fInt(q[0]) + " to " + fInt(q[2]) + "), about " + fInt(mq[1]) + " matches of video (middle half " +
    fInt(mq[0]) + " to " + fInt(mq[2]) + ").</p></div>" +
    strip(M.points) +
    "<p class='limits'>Needs a Mac and a broadcast score bug. Lanes come from a strict split of the box into thirds, while pro charts are made by eye; " +
    "the two have not been compared on the same serves. <a href='" + REPO + "'>Code and tests</a></p>";
}

export async function init() {
  const host = $("#match");
  if (!host) return;
  let M = null;
  try {
    const r = await fetch("data/match.json", { cache: "no-cache" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    M = await r.json();
  } catch (err) {
    host.innerHTML = "<p class='limits'>The match model data did not load.</p>";
    console.warn("Serve IQ: data/match.json did not load", err);
    return;
  }
  if (!M || !M.totals || !M.need || !isNum(M.need.serves_per_match)) return;
  host.innerHTML = html(M);
}
