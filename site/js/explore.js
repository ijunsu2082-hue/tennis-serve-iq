/* Serve IQ "Find the sides worth scouting" (#explore). ES module.
   One dot per 1st-serve player-side (every listed player, both sides):
     x  the scouting edge: favorite share minus the share in the typical pro's lane (js/tool.js edge()),
        in points per 100; 0 when the favorite is the typical pro's lane
     y  the largest break-point change of any lane (|bp_<dir>|), in points per 100
   A dot is filled only when the edge passes the card's gate (js/tool.js edgeShown(): a clear favorite, q_mix
   below the threshold and the favorite's 95% range above every other typical-pro lane); a red ring marks a
   break-point change whose test passes with at least bp_warn_n break points. Everything else is a small
   hollow dot. Filled and ringed dots are buttons with one tab stop for the chart (roving tabindex: the arrow
   keys move left and right through them); click or Enter loads that player and side into the court and
   scrolls up. Counts in the caption are computed here. */

import { SIDES, DIRS, esc, fInt, isNum, surname } from "./data.js";
import { $, $$, el, reducedMotion } from "./ui.js";

const W = 1000, H = 350, PAD = { l: 56, r: 16, t: 14, b: 46 };
const LANE = { wide: "out wide", body: "at the body", T: "down the middle" };
let D = null, tool = null, dots = [], tour = "all";

function build() {
  const qth = D.thresholds.q, bpMin = D.thresholds.bp_warn_n;
  const out = [];
  D.players.forEach((p) => {
    const M = D.model(p.gender, p.server, 1);
    if (!M) return;
    SIDES.forEach((s) => {
      const ins = D.sideInsights(M, s), all = M[s].all;
      const fav = tool.favOf(ins, all);
      const e = tool.edge(all, fav);
      const t = M[s].test;
      let y = 0, ydir = null;
      DIRS.forEach((d) => { const v = t["bp_" + d]; if (isNum(v) && Math.abs(v) > Math.abs(y)) { y = v; ydir = d; } });
      const bpClear = (t.n_bp || 0) >= bpMin && ins.bp.shifts.length > 0;
      out.push({
        name: p.server, g: p.gender, side: s, fav, x: e.same ? 0 : Math.max(0, e.value || 0), y: Math.abs(y), ydir, ysign: y,
        same: e.same, filled: tool.edgeShown(ins, e), ring: bpClear, n_bp: t.n_bp, q: qth,
      });
    });
  });
  return out;
}

function scales(list) {
  const xmax = Math.max(0.3, ...list.map((d) => d.x));
  const ymax = Math.max(0.15, ...list.map((d) => d.y));
  const X = (v) => PAD.l + (v / xmax) * (W - PAD.l - PAD.r);
  const Y = (v) => H - PAD.b - (v / ymax) * (H - PAD.t - PAD.b);
  return { X, Y, xmax, ymax };
}

function describe(d) {
  const who = surname(d.name) + ", " + d.side + " side";
  const edgeTxt = d.same ? "leans where the typical pro does" : d.filled ? Math.round(100 * d.x) + " more in 100 than guessing like the typical pro"
    : "too close to call against the typical pro";
  const bpTxt = d.ring ? "on break points " + LANE[d.ydir] + " " + Math.round(100 * Math.abs(d.ysign)) + (d.ysign > 0 ? " more" : " fewer") + " in 100 than on other points"
    : "no clear change on break points";
  return who + ": " + edgeTxt + "; " + bpTxt + ".";
}

function draw() {
  const svg = $("#scatter");
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const list = dots.filter((d) => tour === "all" || d.g === tour);
  const { X, Y, xmax, ymax } = scales(dots);
  // grid and axes
  const ax = el("g", { "font-family": "Archivo, system-ui, sans-serif", "font-size": 12, fill: "#5b6577" }, svg);
  const step = (m) => (m > 0.4 ? 0.1 : 0.05);
  for (let v = 0; v <= xmax + 1e-9; v += step(xmax)) {
    el("line", { x1: X(v), x2: X(v), y1: PAD.t, y2: H - PAD.b, stroke: "#e3e7ee" }, ax);
    el("text", { x: X(v), y: H - PAD.b + 16, "text-anchor": "middle" }, ax).textContent = String(Math.round(100 * v));
  }
  for (let v = 0; v <= ymax + 1e-9; v += step(ymax)) {
    el("line", { x1: PAD.l, x2: W - PAD.r, y1: Y(v), y2: Y(v), stroke: "#e3e7ee" }, ax);
    el("text", { x: PAD.l - 8, y: Y(v) + 4, "text-anchor": "end" }, ax).textContent = String(Math.round(100 * v));
  }
  el("text", { x: (PAD.l + W - PAD.r) / 2, y: H - 6, "text-anchor": "middle", "font-weight": 700, fill: "#0e1726" }, ax).textContent =
    "Scouting edge: more in 100 than guessing like the typical pro";
  el("text", { x: 14, y: (PAD.t + H - PAD.b) / 2, "text-anchor": "middle", "font-weight": 700, fill: "#0e1726", transform: "rotate(-90 14 " + (PAD.t + H - PAD.b) / 2 + ")" }, ax).textContent =
    "Biggest break-point change, in 100";
  // dots: hollow first, then filled and ringed on top
  const g = el("g", {}, svg);
  const order = list.slice().sort((a, b) => (a.filled || a.ring ? 1 : 0) - (b.filled || b.ring ? 1 : 0));
  const live = [];
  order.forEach((d) => {
    const isLive = d.filled || d.ring;
    const c = el("circle", {
      cx: X(d.x).toFixed(1), cy: Y(d.y).toFixed(1), r: isLive ? 6 : 3.2,
      fill: d.filled ? "#0a62d6" : isLive ? "#fff" : "none",
      stroke: d.ring ? "#d7263d" : d.filled ? "#0a62d6" : "#aab4c4", "stroke-width": d.ring ? 2.6 : 1.2,
      class: isLive ? "dot live" : "dot",
    }, g);
    if (!isLive) return;
    live.push({ c, x: d.x, y: d.y });
    c.setAttribute("tabindex", "-1");
    c.setAttribute("role", "button");
    c.setAttribute("aria-label", describe(d) + " Load on the court.");
    c.dataset.name = d.name; c.dataset.side = d.side;
    const show = () => { $("#scatter-read").textContent = describe(d); $$("#scatter .dot.on").forEach((x) => x.classList.remove("on")); c.classList.add("on"); };
    c.addEventListener("mouseenter", show);
    c.addEventListener("focus", show);
    const go = () => {
      tool.select(d.name, 1, d.side);
      const t = $("#tool");
      t.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
      t.focus({ preventScroll: true });
    };
    c.addEventListener("click", go);
    c.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
  // one tab stop for the whole chart: the arrow keys walk the live dots left to right (then bottom to top)
  live.sort((a, b) => a.x - b.x || a.y - b.y);
  live.forEach((o, i) => {
    if (i === 0) o.c.setAttribute("tabindex", "0");
    o.c.addEventListener("focus", () => live.forEach((x) => x.c.setAttribute("tabindex", x === o ? "0" : "-1")));
    o.c.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, Home: -live.length, End: live.length }[e.key];
      if (!step) return;
      e.preventDefault();
      live[Math.max(0, Math.min(live.length - 1, i + step))].c.focus();
    });
  });
  // caption, all counts from the dots shown
  const same = list.filter((d) => d.same).length, filled = list.filter((d) => d.filled).length, ring = list.filter((d) => d.ring).length;
  $("#scatter-cap").innerHTML = "<span class='k k-fill'></span>" + fInt(filled) + " sides with a clear scouting edge, " +
    "<span class='k k-ring'></span>" + fInt(ring) + " with a clear break-point change, " +
    "<span class='k k-hollow'></span>the rest neither. " + fInt(same) + " of " + fInt(list.length) + " sides lean where the typical pro does. " +
    "Win rates clearly differ by direction for " + fInt(D.h("costly_players_s1")) + " of " + fInt(D.h("players")) + " players.";
  $("#scatter-read").textContent = "Point at a blue or red dot (or Tab in and use the arrow keys), then click or press Enter to load it on the court.";
}

export async function init(ctx) {
  D = ctx.data;
  tool = ctx.tool;
  if (!tool || !$("#scatter")) return;
  dots = build();
  draw();
  $("#seg-tour").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.getAttribute("data-tour") === tour) return;
    tour = b.getAttribute("data-tour");
    $$("#seg-tour button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    draw();
  });
  ctx.explore = { dots: () => dots.slice() };
}
