/* Serve IQ court. ES module.
   Two top-down panels of a singles hard court seen from behind the server (server at the bottom), one for
   the deuce side and one for the ad side. The far half is drawn to scale from the COURT constants in
   data.js; the near half is squeezed (NEAR_SCALE) so a panel fits on a phone. A dot marks the server about
   a meter from the center mark, and three white arrows run crosscourt from it into the target box:
   wide (toward the singles sideline), body (between the two) and T (toward the center service line).
   Arrow width follows the share of serves; the share and the direction word sit right above the arrowhead.
   When two labels would overlap (narrow panels), the middle one moves up one row and a thin line ties it
   to its arrowhead, so every label stays above its own arrow. Arrows show direction codes, not landing spots.

   Each panel is an svg with role="group", not role="img" as the spec first said: role="img" would hide the
   focusable arrows from screen readers. The svg label names the side only; the arrows and the hidden
   table carry the numbers.

   createCourt(host) returns { draw(view, { animate }) }.
   view = {
     caption: "Nadal, first serves",                          (hidden table caption)
     sides: { deuce: { label, cells: { wide: cell, body: cell, T: cell } }, ad: {...} },
   }
   cell = { share, n, shareText, leagueText, winText, nText, tip }    (tip is the tooltip sentence)  */

import { COURT, SIDES, SHARE_DOMAIN_MAX, isNum, fPct, esc } from "./data.js";
import { el, txt, debounce } from "./ui.js";
import { tween } from "./anim.js";

const { BOX_W, BOX_D, SINGLES_W, HALF_LEN } = COURT;
const NEAR_SCALE = 0.36;    // the near half is drawn at 36% of its length
const M_SIDE = 0.6;         // green surround left and right of the singles sidelines, meters
const M_TOP = 0.45;         // behind the far baseline
const M_BOT = 0.85;         // behind the near baseline (the server stands here)
const SERVER_OFF = 1.0;     // server distance from the center mark
const AIM_IN = 1.0;         // arrow tips sit this far inside the far service line
const AIM_SIDE = 0.55;      // wide and T tips sit this far inside the box edge
const ORIGIN_IN = 0.5;      // arrows start this far in front of the server dot (toward the net)
const FAN_GAP = 2;          // px between neighboring arrow tails, so the tails sit side by side, not on top of each other
const W_MIN = 2, W_MAX = 22, K_REF = 32;   // arrow width in px at K_REF px per meter
const MAX_NUDGE = 5;         // px a label may slide sideways off its arrowhead before the middle one moves up a row
const NARROW = 240;         // px; panels narrower than this get the short subcaption and smaller labels
const W_SCALE_MIN = 0.72, W_SCALE_MAX = 1.1;
const PANEL_MAX = 300;      // px; wider panels push the caption below the first desktop screen
const TWEEN_MS = 450;

const DIR_WORD = { wide: "wide", body: "body", T: "T" };
const SUB = { deuce: "server stands right of the center mark", ad: "server stands left of the center mark" };
const SUB_SHORT = { deuce: "server right of center", ad: "server left of center" };   // narrow panels only
const NOTE = "Arrows show the direction of the serve, not where the ball landed.";
const TITLE = { deuce: "Deuce side", ad: "Ad side" };

const W_M = SINGLES_W + 2 * M_SIDE;
const Y_NET = M_TOP + HALF_LEN;
const H_M = Y_NET + HALF_LEN * NEAR_SCALE + M_BOT;

function aimPoints(side) {
  // x measured from the left singles sideline as the viewer sees it; the deuce target box is on the left
  const y = Y_NET - (BOX_D - AIM_IN);
  if (side === "deuce") {
    return { wide: [AIM_SIDE, y], body: [BOX_W / 2, y], T: [BOX_W - AIM_SIDE, y] };
  }
  return { wide: [SINGLES_W - AIM_SIDE, y], body: [BOX_W + BOX_W / 2, y], T: [BOX_W + AIM_SIDE, y] };
}
function serverPoint(side) {
  const x = BOX_W + (side === "deuce" ? SERVER_OFF : -SERVER_OFF);
  return [x, Y_NET + HALF_LEN * NEAR_SCALE + 0.25];
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const r1 = (v) => Math.round(v * 10) / 10;

export function createCourt(host) {
  let panels = null;     // { side: { svg, k, arrows: { dir: arrowState } } }
  let lastView = null;
  let lastW = 0;
  let cancel = null;
  let shown = {};        // "side|dir" -> share currently drawn
  let tipFor = null;     // arrow <g> whose tooltip is open

  host.classList.add("court");
  host.innerHTML = "";
  const row = document.createElement("div");
  row.className = "court-panels";
  host.appendChild(row);
  const note = document.createElement("p");
  note.className = "court-note";
  note.textContent = NOTE;
  host.appendChild(note);
  const tip = document.createElement("div");
  tip.id = "court-tip";
  tip.className = "court-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  host.appendChild(tip);
  const sr = document.createElement("div");      // a table ignores a 1px width, so its wrapper is the hidden box
  sr.className = "visually-hidden";
  const table = document.createElement("table");
  sr.appendChild(table);
  host.appendChild(sr);

  // ------------------------------------------------------------ geometry and drawing

  function build(view) {
    hideTip();
    row.innerHTML = "";
    const hostW = Math.floor(host.clientWidth || 358);
    const gap = hostW < 520 ? 10 : 22;
    const pw = Math.floor(Math.min((hostW - gap) / 2, PANEL_MAX));
    const k = pw / W_M;
    const ph = Math.round(H_M * k);
    row.style.gap = gap + "px";
    note.style.maxWidth = (2 * pw + gap) + "px";
    const sub = pw >= NARROW ? SUB : SUB_SHORT;
    panels = {};
    SIDES.forEach((side) => {
      const fig = document.createElement("figure");
      fig.className = "court-panel";
      fig.setAttribute("data-side", side);
      fig.style.width = pw + "px";
      fig.innerHTML = '<figcaption><span class="cp-title">' + TITLE[side] + '</span><span class="cp-sub">' + sub[side] + "</span></figcaption>";
      const svg = el("svg", {
        viewBox: "0 0 " + pw + " " + ph, width: pw, height: ph, role: "group",
        "aria-label": view.sides[side].label,
      });
      fig.appendChild(svg);
      row.appendChild(fig);
      drawCourt(svg, k, pw, ph);
      const arrows = {};
      const layer = el("g", { class: "arrows" }, svg);
      const aims = aimPoints(side);
      const sp = serverPoint(side);
      const dot = [(M_SIDE + sp[0]) * k, sp[1] * k];
      const base = [dot[0], (sp[1] - ORIGIN_IN) * k];
      // left to right as the viewer sees the target box. The arrows go into the DOM in this order, so Tab
      // walks them left to right (deuce: wide, body, T; ad: T, body, wide) whatever the shares are.
      // The tails sit side by side (placeTails), so no arrow is drawn over another and paint order is free.
      const byX = Object.keys(aims).sort((a, b) => aims[a][0] - aims[b][0]);
      byX.forEach((dir) => {
        const tipPt = [(M_SIDE + aims[dir][0]) * k, aims[dir][1] * k];
        arrows[dir] = makeArrow(layer, side, dir, base.slice(), tipPt, k, pw);
      });
      el("circle", { cx: r1(dot[0]), cy: r1(dot[1]), r: r1(Math.max(4, 0.26 * k)), class: "server-dot" }, svg);
      panels[side] = { svg, k, pw, ph, arrows, base, byX };
    });
  }

  function drawCourt(svg, k, pw, ph) {
    const X = (m) => (M_SIDE + m) * k;
    const Y = (m) => m * k;
    const lw = clamp(0.055 * k, 1.25, 2);
    const snap = (v, w) => (Math.round(w) % 2 ? Math.round(v - 0.5) + 0.5 : Math.round(v));
    el("rect", { x: 0, y: 0, width: pw, height: ph, class: "c-surround" }, svg);
    const yFar = Y(M_TOP), yNear = Y(Y_NET + HALF_LEN * NEAR_SCALE), yNet = Y(Y_NET);
    const xl = X(0), xr = X(SINGLES_W), xc = X(BOX_W);
    el("rect", { x: xl, y: yFar, width: xr - xl, height: yNear - yFar, class: "c-play" }, svg);
    const g = el("g", { class: "c-lines", "stroke-width": lw }, svg);
    const L = (x1, y1, x2, y2, w) => {
      const ww = w || lw;
      const a = { x1, y1, x2, y2 };
      if (x1 === x2) { a.x1 = a.x2 = snap(x1, ww); }
      if (y1 === y2) { a.y1 = a.y2 = snap(y1, ww); }
      el("line", Object.assign(a, ww !== lw ? { "stroke-width": ww } : {}), g);
    };
    const yFarSvc = Y(Y_NET - BOX_D), yNearSvc = Y(Y_NET + BOX_D * NEAR_SCALE);
    const bw = lw * 1.5;
    L(xl, yFar, xr, yFar, bw);                 // far baseline
    L(xl, yNear, xr, yNear, bw);               // near baseline
    L(xl, yFar - bw / 2, xl, yNear + bw / 2);  // singles sidelines
    L(xr, yFar - bw / 2, xr, yNear + bw / 2);
    L(xl, yFarSvc, xr, yFarSvc);               // service lines
    L(xl, yNearSvc, xr, yNearSvc);
    L(xc, yFarSvc, xc, yNearSvc);              // center service line
    const cm = Math.max(4, 0.3 * k);           // center marks
    L(xc, yFar, xc, yFar + cm);
    L(xc, yNear - cm, xc, yNear);
    // the net runs past the sidelines to the posts
    el("line", { x1: 0, y1: yNet, x2: pw, y2: yNet, class: "c-net-shadow", "stroke-width": lw * 2.4 + 2 }, svg);
    el("line", { x1: 0, y1: yNet, x2: pw, y2: yNet, class: "c-net", "stroke-width": lw * 2.4 }, svg);
  }

  function makeArrow(layer, side, dir, origin, tipPt, k, pw) {
    const g = el("g", { class: "arrow", tabindex: 0, role: "button", "data-side": side, "data-dir": dir }, layer);
    const hit = el("path", { class: "a-hit" }, g);
    const leader = el("path", { class: "a-leader" }, g);
    const casing = el("path", { class: "a-casing" }, g);
    const shaft = el("path", { class: "a-shaft" }, g);
    const head = el("path", { class: "a-head" }, g);
    const share = txt(g, tipPt[0], 0, "", "a-share", "middle");
    const word = txt(g, tipPt[0], 0, DIR_WORD[dir], "a-word", "middle");
    const wide = pw >= NARROW;
    const fsS = wide ? 16 : 13;
    const fsW = wide ? 13 : 11;
    share.style.fontSize = fsS + "px";
    word.style.fontSize = fsW + "px";
    const st = { g, hit, leader, casing, shaft, head, share, word, origin, tipPt, k, fsS, fsW, side, dir, labelX: tipPt[0], row: 0 };
    aimFrom(st, origin);
    bindArrow(g);
    return st;
  }

  function aimFrom(a, origin) {
    a.origin = origin;
    const dx = a.tipPt[0] - origin[0], dy = a.tipPt[1] - origin[1];
    const len = Math.hypot(dx, dy);
    a.u = [dx / len, dy / len];
    a.nrm = [-a.u[1], a.u[0]];
  }

  /* the middle arrow's tail sits in front of the server dot and its neighbors' tails sit just beside it,
     spaced by their current widths, so every arrow keeps its full width down to its tail */
  function placeTails(p, shares) {
    const w = p.byX.map((dir) => widthFor(shares[dir], p.k));
    const xs = [0, 0, 0];
    xs[0] = -((w[0] + w[1]) / 2 + FAN_GAP);
    xs[2] = (w[1] + w[2]) / 2 + FAN_GAP;
    p.byX.forEach((dir, i) => aimFrom(p.arrows[dir], [p.base[0] + xs[i], p.base[1]]));
  }

  function widthFor(share, k) {
    const s = clamp(isNum(share) ? share : 0, 0, SHARE_DOMAIN_MAX) / SHARE_DOMAIN_MAX;
    return (W_MIN + (W_MAX - W_MIN) * s) * clamp(k / K_REF, W_SCALE_MIN, W_SCALE_MAX);
  }

  function shapeArrow(a, share, empty) {
    const w = widthFor(share, a.k);
    const hw = Math.max(1.9 * w, w + 11) / 2;   // arrowhead half width: clearly wider than the shaft
    const L = 1.05 * 2 * hw;                    // arrowhead length
    const start = 0;
    const P = (d, n) => [a.origin[0] + a.u[0] * d + a.nrm[0] * (n || 0), a.origin[1] + a.u[1] * d + a.nrm[1] * (n || 0)];
    const len = Math.hypot(a.tipPt[0] - a.origin[0], a.tipPt[1] - a.origin[1]);
    const s0 = P(start), s1 = P(len - L * 0.7);
    const b0 = P(len - L, hw), b1 = P(len - L, -hw), t = a.tipPt;
    const d = "M" + r1(s0[0]) + "," + r1(s0[1]) + "L" + r1(s1[0]) + "," + r1(s1[1]);
    a.shaft.setAttribute("d", d);
    a.shaft.setAttribute("stroke-width", r1(w));
    a.casing.setAttribute("d", d);
    a.casing.setAttribute("stroke-width", r1(w + 2));
    a.hit.setAttribute("d", "M" + r1(s0[0]) + "," + r1(s0[1]) + "L" + r1(t[0]) + "," + r1(t[1]));
    a.hit.setAttribute("stroke-width", r1(Math.max(w + 10, 20)));
    a.head.setAttribute("d", "M" + r1(t[0]) + "," + r1(t[1]) + "L" + r1(b0[0]) + "," + r1(b0[1]) + "L" + r1(b1[0]) + "," + r1(b1[1]) + "Z");
    a.g.classList.toggle("is-empty", !!empty);
    a.w = w;
  }

  // Labels sit in the open backcourt just beyond the far service line, straight above their arrowheads, so
  // no court line runs through them. If neighbors would overlap, the middle label moves up one row (with a
  // thin leader line down to its arrowhead) instead of sideways, so a label never drifts over another arrow.
  function layoutLabels(p, view, side) {
    const list = p.byX.map((dir) => p.arrows[dir]);
    const measure = (node, text, fs) => {
      const old = node.textContent;
      node.textContent = text;
      let w = 0;
      try { w = node.getComputedTextLength(); } catch (e) { w = 0; }
      node.textContent = old;
      return w > 0 ? w : text.length * fs * 0.62;
    };
    const pad = 2, gap = Math.max(4, p.k * 0.25);
    const lo = M_SIDE * p.k + pad, hi = (M_SIDE + SINGLES_W) * p.k - pad;
    list.forEach((a) => {
      const text = view.sides[side].cells[a.dir].shareText;
      const ws = Math.max(measure(a.share, text, a.fsS), measure(a.share, "00%", a.fsS));
      const ww = measure(a.word, DIR_WORD[a.dir], a.fsW);
      a.half = Math.max(ws, ww) / 2 + 1;
      // keep labels inside the singles sidelines so no line runs through them
      a.home = clamp(a.tipPt[0], lo + a.half, hi - a.half);
    });
    // labels on the same row that overlap are pushed apart, then kept inside the sidelines
    const spread = () => {
      for (let it = 0; it < 30; it++) {
        let moved = false;
        for (let i = 0; i < list.length; i++) {
          for (let j = i + 1; j < list.length; j++) {
            const A = list[i], B = list[j];
            if (A.row !== B.row) continue;
            const need = A.half + B.half + gap - (B.labelX - A.labelX);
            if (need > 0.01) { A.labelX -= need / 2; B.labelX += need / 2; moved = true; }
          }
        }
        list.forEach((a) => {
          const x = clamp(a.labelX, lo + a.half, hi - a.half);
          if (x !== a.labelX) { a.labelX = x; moved = true; }
        });
        if (!moved) break;
      }
      return Math.max(...list.map((a) => Math.abs(a.labelX - a.home)));
    };
    // one row if that moves no label more than MAX_NUDGE px off its arrowhead (wide panels);
    // otherwise the middle label goes up a row (narrow panels)
    list.forEach((a) => { a.labelX = a.home; a.row = 0; });
    if (spread() > MAX_NUDGE) {
      list.forEach((a) => { a.labelX = a.home; a.row = 0; });
      list[1].row = 1;
      spread();
    }
    const ySvc = (Y_NET - BOX_D) * p.k;
    const rowH = list[0].fsS + list[0].fsW + 6;
    list.forEach((a) => {
      const yWord = ySvc - Math.max(7, 0.35 * p.k) - a.row * rowH;   // word baseline, clear of the service line
      const ySh = yWord - a.fsW - 2;
      a.share.setAttribute("x", r1(a.labelX));
      a.share.setAttribute("y", r1(ySh));
      a.word.setAttribute("x", r1(a.labelX));
      a.word.setAttribute("y", r1(yWord));
      a.leader.setAttribute("d", a.row
        ? "M" + r1(a.labelX) + "," + r1(yWord + 4) + "L" + r1(a.tipPt[0]) + "," + r1(a.tipPt[1] - 1)
        : "");
    });
  }

  function apply(view, from, k01) {
    SIDES.forEach((side) => {
      const p = panels[side];
      const vals = {};
      Object.keys(p.arrows).forEach((dir) => {
        const c = view.sides[side].cells[dir];
        const key = side + "|" + dir;
        const to = isNum(c.share) ? c.share : 0;
        const f = from && isNum(from[key]) ? from[key] : to;
        vals[dir] = f + (to - f) * k01;
      });
      placeTails(p, vals);
      Object.keys(p.arrows).forEach((dir) => {
        const a = p.arrows[dir];
        const c = view.sides[side].cells[dir];
        const key = side + "|" + dir;
        const v = vals[dir];
        shapeArrow(a, v, !(c.n > 0));
        a.share.textContent = k01 >= 1 ? c.shareText : fPct(v, 0);
        shown[key] = v;
      });
    });
  }

  function texts(view) {
    SIDES.forEach((side) => {
      const p = panels[side];
      p.svg.setAttribute("aria-label", view.sides[side].label);
      Object.keys(p.arrows).forEach((dir) => {
        const a = p.arrows[dir];
        const c = view.sides[side].cells[dir];
        a.g.setAttribute("aria-label", c.tip);
        a.tip = c.tip;
      });
    });
    table.innerHTML = "<caption>" + esc(view.caption) + "</caption><thead><tr><th scope=\"col\">Side</th><th scope=\"col\">Direction</th>" +
      "<th scope=\"col\">Share of serves</th><th scope=\"col\">Typical pro</th><th scope=\"col\">Points won</th><th scope=\"col\">Serves</th></tr></thead><tbody>" +
      SIDES.map((side) => ["wide", "body", "T"].map((dir) => {
        const c = view.sides[side].cells[dir];
        return "<tr><th scope=\"row\">" + esc(TITLE[side]) + "</th><td>" + esc(DIR_WORD[dir]) + "</td><td>" + esc(c.shareText) + "</td><td>" +
          esc(c.leagueText) + "</td><td>" + esc(c.winText) + "</td><td>" + esc(c.nText) + "</td></tr>";
      }).join("")).join("") + "</tbody>";
  }

  function draw(view, opts) {
    const animate = !!(opts && opts.animate);
    const w = Math.floor(host.clientWidth || 0);
    const fresh = !panels || w !== lastW;
    const from = fresh ? null : Object.assign({}, shown);
    // a rebuild (new width) replaces the arrows; remember a focused one so focus and its tooltip come back
    const act = document.activeElement;
    const refocus = fresh && panels && act && act.classList && act.classList.contains("arrow") && host.contains(act)
      ? [act.getAttribute("data-side"), act.getAttribute("data-dir")] : null;
    lastView = view;
    lastW = w;
    if (cancel) { cancel(); cancel = null; }
    if (fresh) build(view);
    texts(view);
    SIDES.forEach((side) => layoutLabels(panels[side], view, side));
    if (animate && from) {
      cancel = tween(TWEEN_MS, (k01) => apply(view, from, k01), () => { cancel = null; });
    } else {
      apply(view, null, 1);
    }
    if (refocus && panels[refocus[0]] && panels[refocus[0]].arrows[refocus[1]]) {
      panels[refocus[0]].arrows[refocus[1]].g.focus({ preventScroll: true });
    } else if (tipFor) {
      showTip(tipFor);
    }
  }

  // ------------------------------------------------------------ tooltip

  function showTip(g) {
    const side = g.getAttribute("data-side"), dir = g.getAttribute("data-dir");
    const a = panels && panels[side] && panels[side].arrows[dir];
    if (!a) return;
    tipFor = g;
    host.classList.add("has-active");
    Object.keys(panels).forEach((s) => Object.keys(panels[s].arrows).forEach((d) => panels[s].arrows[d].g.classList.toggle("is-active", panels[s].arrows[d] === a)));
    tip.textContent = a.tip || "";
    tip.hidden = false;
    const vw = document.documentElement.clientWidth;
    tip.style.maxWidth = Math.min(280, vw - 16) + "px";
    tip.style.left = "0px";
    tip.style.top = "0px";
    const hr = host.getBoundingClientRect();
    const lr = a.share.getBoundingClientRect();
    const hd = a.head.getBoundingClientRect();
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const cx = (lr.left + lr.right) / 2;
    let x = clamp(cx - tw / 2, 8, vw - tw - 8);
    let y = lr.top - th - 8;
    if (y < 8) y = hd.bottom + 10;                       // no room above the label: go under the arrowhead
    y = Math.min(y, window.innerHeight - th - 8);
    tip.style.left = Math.round(x - hr.left) + "px";
    tip.style.top = Math.round(y - hr.top) + "px";
  }
  function hideTip() {
    tipFor = null;
    tip.hidden = true;
    host.classList.remove("has-active");
    if (panels) Object.keys(panels).forEach((s) => Object.keys(panels[s].arrows).forEach((d) => panels[s].arrows[d].g.classList.remove("is-active")));
  }
  function bindArrow(g) {
    g.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") showTip(g); });
    g.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse" && document.activeElement !== g) hideTip(); });
    g.addEventListener("focus", () => showTip(g));
    g.addEventListener("blur", () => { if (tipFor === g) hideTip(); });
    g.addEventListener("click", () => showTip(g));
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (tipFor === g) hideTip(); else showTip(g); }
      else if (e.key === "Escape") { hideTip(); }
    });
  }
  // Esc closes the tooltip wherever focus is, also when it was opened by mouse hover
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && tipFor) hideTip(); });
  document.addEventListener("pointerdown", (e) => {
    if (tipFor && !(e.target.closest && e.target.closest(".arrow"))) hideTip();
  });
  window.addEventListener("scroll", () => { if (tipFor && document.activeElement !== tipFor) hideTip(); else if (tipFor) showTip(tipFor); }, { passive: true });
  window.addEventListener("resize", debounce(() => {
    if (lastView && Math.floor(host.clientWidth || 0) !== lastW) draw(lastView, { animate: false });
  }, 120));

  return { draw };
}
