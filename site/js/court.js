/* Serve IQ court. ES module.
   A hard court seen from behind the server, the way a TV camera behind the baseline shows it. Two servers
   stand at the near baseline: right of the center mark for deuce-side serves, left of it for ad-side serves.
   Each serves crosscourt into the far service box, which is split into three equal lanes:
     deuce box (on the left, as seen from behind the server): wide (by the sideline), body, T (by the center line)
     ad box (on the right): T, body, wide
   The lanes are serve directions, not landing spots. A curved path runs from each server to each of its
   three lanes; its width and the lane's tint grow with the share of serves. Above the far service line
   each lane carries its name and share.

   Camera: telephoto-like. The near half of the court is drawn shorter than to scale so the two receiving
   boxes (where the serves go) get most of the height, then a mild perspective narrows the far end.

   createCourt(svg, { onHover, onPick }) returns { draw(view, { animate }), highlight(key), focus(side),
   hidePaths(), pickable(side, on), mark(key) }.
   view.sides[side].cells[dir].share (0 to 1 or null); key = side + dir, e.g. "deuceT".
     focus(side)        dims everything that belongs to the other side (null shows both)
     hidePaths()        rewinds the paths and shows "?" in the focused side's share labels (the call phase)
     pickable(side, on) lets the visitor click that side's three lanes; onPick(key) fires on a click
     mark(key)          outlines the lane the visitor picked (null clears it)
   draw() reveals: the paths draw in and the labels show the shares. */

import { SIDES, DIRS, isNum, fPct } from "./data.js";
import { el, debounce, reducedMotion } from "./ui.js";

const SW = 4.115, DW = 5.485, NET = 11.885, SVC = NET + 6.40;   // meters
const VB = { w: 640, h: 470 };
const Y0 = -2.6, Y1 = SVC + 3.4;
const warp = (y) => (y < NET ? y * 0.36 : NET * 0.36 + (y - NET));
const D0 = 40;
const sc = (y) => 1 / (warp(y) + D0);
const S_NEAR = sc(Y0), S_FAR = sc(Y1);
const KX = 600 / (2 * (DW + 0.4) * S_NEAR);                    // near doubles width plus run-off fills 600px
const P = (x, y) => [VB.w / 2 + x * sc(y) * KX, 14 + (sc(y) - S_FAR) / (S_NEAR - S_FAR) * (VB.h - 34)];
const fx = (x, y) => P(x, y)[0];
const fy = (x, y) => P(x, y)[1];
const poly = (pts) => pts.map((p) => P(p[0], p[1]).map((v) => v.toFixed(1)).join(",")).join(" ");
const quad = (x0, x1, y0, y1) => poly([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]);

const COLORS = { court: "#2d63b5", surround: "#3f7a4f", line: "#ffffff", deuce: "#ffd23f", ad: "#ffffff", navy: "#0b1d42", ball: "#e8f84a" };
const LANE_X = {
  deuce: { wide: [-SW, -SW * 2 / 3], body: [-SW * 2 / 3, -SW / 3], T: [-SW / 3, 0] },
  ad: { T: [0, SW / 3], body: [SW / 3, SW * 2 / 3], wide: [SW * 2 / 3, SW] },
};
const SERVER = { deuce: [1.1, -0.35], ad: [-1.1, -0.35] };
const NAMES = { wide: "Wide", body: "Body", T: "T" };
const DRAW_MS = 750, STAGGER = 110;

export function createCourt(svg, opts) {
  const onHover = (opts && opts.onHover) || (() => {});
  const onPick = (opts && opts.onPick) || (() => {});
  const bySide = { deuce: [], ad: [] };     // labels and servers dimmed by focus() (lane tints are set in draw)
  let focused = null, hidden = false;
  const RM = reducedMotion();
  const font = { "font-family": "Archivo, system-ui, sans-serif", "font-weight": 800, "text-anchor": "middle" };

  // surround, court, lines
  el("polygon", { points: quad(-DW - 3.2, DW + 3.2, Y0, Y1), fill: COLORS.surround }, svg);
  el("polygon", { points: quad(-DW, DW, Y0, Y1), fill: COLORS.court }, svg);
  const line = (x0, y0, x1, y1, w) => el("line", {
    x1: fx(x0, y0), y1: fy(x0, y0), x2: fx(x1, y1), y2: fy(x1, y1),
    stroke: COLORS.line, "stroke-width": w || 2.2, "stroke-linecap": "square",
  }, svg);
  line(-DW, 0, DW, 0, 3);
  line(-DW, 0, -DW, Y1); line(DW, 0, DW, Y1);
  line(-SW, 0, -SW, Y1); line(SW, 0, SW, Y1);
  line(-SW, NET - 6.40, SW, NET - 6.40);
  line(0, NET - 6.40, 0, NET);
  line(-SW, SVC, SW, SVC);
  line(0, NET, 0, SVC);
  line(0, 0, 0, 0.3);

  // lane tints (white, opacity by share)
  const lanes = {};
  const lanesG = el("g", {}, svg);
  SIDES.forEach((s) => DIRS.forEach((d) => {
    const [a, b] = LANE_X[s][d];
    lanes[s + d] = el("polygon", { points: quad(a, b, NET, SVC), fill: "#ffffff", opacity: 0 }, lanesG);
  }));

  // net: tape plus a shadow strip
  const netY = fy(0, NET);
  el("line", { x1: fx(-DW - 0.9, NET), y1: netY + 6, x2: fx(DW + 0.9, NET), y2: netY + 6, stroke: "rgba(10,20,40,.35)", "stroke-width": 9 }, svg);
  el("line", { x1: fx(-DW - 0.9, NET), y1: netY - 1, x2: fx(DW + 0.9, NET), y2: netY - 1, stroke: "#f6f8fb", "stroke-width": 4 }, svg);

  // lane names and shares above the far service line
  const labels = {};
  const labG = el("g", font, svg);
  SIDES.forEach((s) => DIRS.forEach((d) => {
    const [a, b] = LANE_X[s][d];
    const x = fx((a + b) / 2, SVC + 0.6), y = fy(0, SVC + 0.6) - 6;
    const nm = el("text", { x, y: y - 24, fill: "#cfe0ff", "font-size": 12, "font-weight": 700 }, labG);
    nm.textContent = NAMES[d];
    labels[s + d] = el("text", { x, y, fill: "#ffffff", "font-size": 19, style: "font-variant-numeric: tabular-nums" }, labG);
    bySide[s].push(nm, labels[s + d]);
  }));

  // paths, balls, servers
  const pathsG = el("g", { fill: "none", "stroke-linecap": "round" }, svg);
  const ballsG = el("g", {}, svg);
  const srvG = el("g", font, svg);
  SIDES.forEach((s) => {
    const [x, y] = SERVER[s];
    const g = el("g", {}, srvG);
    el("ellipse", { cx: fx(x, y), cy: fy(x, y) + 3, rx: 11, ry: 4, fill: "rgba(0,0,0,.25)" }, g);
    el("circle", { cx: fx(x, y), cy: fy(x, y) - 4, r: 7.5, fill: COLORS[s], stroke: COLORS.navy, "stroke-width": 2 }, g);
    el("text", { x: fx(x, y), y: fy(x, y) + 22, fill: "#ffffff", "font-size": 12, "font-weight": 700 }, g).textContent =
      s === "deuce" ? "Deuce side server" : "Ad side server";
    bySide[s].push(g);
  });
  const paths = {};
  SIDES.forEach((s) => DIRS.forEach((d) => {
    const [a, b] = LANE_X[s][d];
    const [sx, sy] = SERVER[s];
    const tx = (a + b) / 2, ty = SVC - 1.2;
    const p0 = P(sx, sy), p2 = P(tx, ty);
    // a slight arc: the control point is lifted above the straight line, like a ball flight seen from behind
    const mid = P((sx + tx) / 2, (sy + ty) / 2);
    const dstr = "M" + p0[0].toFixed(1) + "," + (p0[1] - 4).toFixed(1) +
      " Q" + mid[0].toFixed(1) + "," + (mid[1] - 26).toFixed(1) + " " + p2[0].toFixed(1) + "," + p2[1].toFixed(1);
    const shade = el("path", { d: dstr, stroke: "rgba(11,29,66,.35)", "stroke-width": 0 }, pathsG);
    const path = el("path", { d: dstr, stroke: COLORS[s], "stroke-width": 0, opacity: 0.95 }, pathsG);
    const len = path.getTotalLength();
    [shade, path].forEach((e) => { e.style.strokeDasharray = len; e.style.strokeDashoffset = RM ? 0 : len; });
    const ball = el("circle", { r: 5, fill: COLORS.ball, stroke: COLORS.navy, "stroke-width": 1.2, opacity: 0 }, ballsG);
    paths[s + d] = { path, shade, len, ball, side: s };
  }));

  // the picked lane: an outline drawn over the lane
  const picks = {};
  const pickG = el("g", { fill: "none", stroke: COLORS.ball, "stroke-width": 3.5, "stroke-linejoin": "round" }, svg);
  SIDES.forEach((s) => DIRS.forEach((d) => {
    const [a, b] = LANE_X[s][d];
    picks[s + d] = el("polygon", { points: quad(a, b, NET, SVC), opacity: 0 }, pickG);
  }));

  // hover and click targets: the lane plus the space above it, so pointing at the label also works.
  // Keyboard and screen-reader users pick with the buttons under the court (js/tool.js), so these stay
  // out of the tab order.
  const hitG = el("g", {}, svg);
  const hits = {};
  SIDES.forEach((s) => DIRS.forEach((d) => {
    const [a, b] = LANE_X[s][d];
    const hit = el("polygon", { points: quad(a, b, NET, Y1), fill: "transparent", class: "lane-hit" }, hitG);
    hit.addEventListener("mouseenter", () => onHover(s + d));
    hit.addEventListener("mouseleave", () => onHover(null));
    hit.addEventListener("click", () => { if (hit.classList.contains("on")) onPick(s + d); });
    hits[s + d] = hit;
  }));

  function fly(o, delay) {
    const t0 = performance.now() + delay;
    const step = (t) => {
      const u = (t - t0) / DRAW_MS;
      if (u < 0) { requestAnimationFrame(step); return; }
      if (u >= 1) { o.ball.setAttribute("opacity", 0); return; }
      const e = 1 - Math.pow(1 - u, 3);
      const pt = o.path.getPointAtLength(e * o.len);
      o.ball.setAttribute("cx", pt.x.toFixed(1));
      o.ball.setAttribute("cy", pt.y.toFixed(1));
      o.ball.setAttribute("opacity", u > 0.9 ? ((1 - u) / 0.1).toFixed(2) : 1);
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function draw(view, o) {
    const animate = !!(o && o.animate) && !RM;
    let i = 0;
    SIDES.forEach((s) => DIRS.forEach((d) => {
      const k = s + d;
      const c = view.sides[s].cells[d];
      const share = isNum(c.share) ? c.share : 0;
      const w = 1.5 + share * 15;
      lanes[k].style.transition = RM ? "none" : "opacity .6s";
      const off = focused && s !== focused;
      lanes[k].setAttribute("opacity", off ? "0.04" : (0.04 + share * 0.62).toFixed(2));
      labels[k].textContent = off ? "" : isNum(c.share) ? fPct(c.share, 0) : "n/a";
      labels[k].classList.remove("q");
      const p = paths[k];
      p.path.setAttribute("stroke-width", w.toFixed(1));
      p.shade.setAttribute("stroke-width", (w + 3).toFixed(1));
      if (off) {
        [p.path, p.shade].forEach((e) => { e.style.transition = "none"; e.style.strokeDashoffset = p.len; });
        return;
      }
      [p.path, p.shade].forEach((e) => { e.style.transition = RM ? "none" : "stroke-width .5s"; });
      if (!animate && hidden) {
        [p.path, p.shade].forEach((e) => { e.style.strokeDashoffset = 0; });
      }
      if (!animate) return;
      const delay = (i++) * STAGGER;
      [p.path, p.shade].forEach((e) => {
        e.style.transition = "none";
        e.style.strokeDashoffset = p.len;
        void e.getBoundingClientRect();
        e.style.transition = "stroke-dashoffset " + DRAW_MS + "ms cubic-bezier(.3,.7,.2,1) " + delay + "ms";
        e.style.strokeDashoffset = 0;
      });
      fly(p, delay);
    }));
    hidden = false;
  }

  function focus(side) {
    focused = side || null;
    SIDES.forEach((s) => bySide[s].forEach((e) => { e.style.opacity = !focused || s === focused ? "" : "0.28"; }));
  }
  function hidePaths() {
    hidden = true;
    Object.keys(paths).forEach((k) => {
      const p = paths[k];
      [p.path, p.shade].forEach((e) => { e.style.transition = "none"; e.style.strokeDashoffset = p.len; });
      p.ball.setAttribute("opacity", 0);
      labels[k].textContent = !focused || p.side === focused ? "?" : "";
      labels[k].classList.toggle("q", !focused || p.side === focused);
      lanes[k].style.transition = RM ? "none" : "opacity .6s";
      lanes[k].setAttribute("opacity", !focused || p.side === focused ? "0.06" : "0.04");
    });
  }
  function pickable(side, on) {
    Object.keys(hits).forEach((k) => hits[k].classList.toggle("on", !!on && k.indexOf(side) === 0));
  }
  function mark(key) {
    Object.keys(picks).forEach((k) => picks[k].setAttribute("opacity", k === key ? 1 : 0));
  }

  function highlight(key) {
    Object.keys(paths).forEach((k) => {
      const on = !key || k === key;
      paths[k].path.style.opacity = on ? 1 : 0.18;
      paths[k].shade.style.opacity = on ? 1 : 0.18;
    });
  }

  /* phones: crop to the court so the lane labels stay readable (the near corners fall outside) */
  const fit = () => svg.setAttribute("viewBox", svg.getBoundingClientRect().width < 520 ? "96 0 448 470" : "0 0 " + VB.w + " " + VB.h);
  fit();
  window.addEventListener("resize", debounce(fit, 120));

  return { draw, highlight, focus, hidePaths, pickable, mark };
}
