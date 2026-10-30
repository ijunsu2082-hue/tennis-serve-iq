/* Serve IQ court heatmap. ES module.
   A schematic of the two service boxes seen from behind the server, drawn to scale (v2 geometry), with
   the backcourt cut short. Each box is split into three equal zones that stand for the direction codes
   wide, body and T (not landing spots). Zones are colored by share with the v2 ramp; the ink on each zone
   is picked for contrast. Every zone is a focusable button: click, tap, focus or mouse hover selects it.

   createCourt(host, { onSelect(side, dir) }) returns { draw(view, { animate }), mark(sel) }.
   view = {
     label: "aria label of the whole court",
     selected: { side, dir },
     cells: { deuce: { wide: { share, aria, small: [{ label, value }] }, body: ..., T: ... }, ad: {...} },
   }
   small: the figures under the big share (value is a share 0..1 or null); label is a string or a list of
   candidates (longest first); a label that does not fit is replaced by the next candidate or dropped. */

import { COURT, SIDES, isNum, fPct } from "./data.js";
import { el, txt, newSvg, fitText, shareColor, reducedMotion } from "./ui.js";

const ORDER = { deuce: ["wide", "body", "T"], ad: ["T", "body", "wide"] };

export function createCourt(host, opts) {
  const o = opts || {};
  let prev = null;     // last drawn values per zone, for tweening
  let zones = [];

  function mark(sel) {
    zones.forEach((g) => {
      const on = !!sel && g.getAttribute("data-side") === sel.side && g.getAttribute("data-dir") === sel.dir;
      g.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function draw(view, dopts) {
    const animate = !!(dopts && dopts.animate) && !!prev && !reducedMotion();
    const { BOX_W, BOX_D, SINGLES_W, BACK_STRIP } = COURT;
    const W = Math.max(260, Math.floor(host.clientWidth || 400));
    const mx = 0.34;
    const k = W / (SINGLES_W + 2 * mx);
    const top = 4, below = 34;
    const strip = Math.max(BACK_STRIP * k, 22);
    const Hh = Math.round(top + strip + BOX_D * k + below);
    const svg = newSvg(host, W, Hh, view.label, "group");
    const x0 = mx * k, xr = x0 + SINGLES_W * k;
    const yBase = top, yServ = top + strip, yNet = yServ + BOX_D * k;
    const zw = (BOX_W * k) / 3, zh = BOX_D * k;

    // a thin strip of backcourt behind the service line; the rest of the backcourt is cut
    el("rect", { x: x0, y: yBase, width: SINGLES_W * k, height: yServ - yBase, class: "backcourt" }, svg);
    el("line", { x1: x0, y1: yBase, x2: x0, y2: yServ, class: "faint" }, svg);
    el("line", { x1: xr, y1: yBase, x2: xr, y2: yServ, class: "faint" }, svg);

    const next = {};
    zones = [];
    let seqI = 0;
    SIDES.forEach((side) => {
      const bx = side === "deuce" ? x0 : x0 + BOX_W * k;
      txt(svg, bx + (BOX_W * k) / 2, yServ - Math.max(6, (strip - 11) / 2), side === "deuce" ? "Deuce box" : "Ad box", "court-lab", "middle");
      ORDER[side].forEach((dir, i) => {
        const c = view.cells[side][dir];
        const key = side + "|" + dir;
        const zx = bx + i * zw;
        const col = shareColor(c.share);
        const g = el("g", {
          class: "zone fade-in", tabindex: 0, role: "button", "aria-pressed": "false",
          "aria-label": c.aria, "data-side": side, "data-dir": dir,
        }, svg);
        g.style.setProperty("--d", String(seqI++ * 2));
        const fill = el("rect", { x: zx, y: yServ, width: zw, height: zh, class: "fill", style: "fill:" + col.fill }, g);
        el("rect", { x: zx + 2, y: yServ + 2, width: zw - 4, height: zh - 4, class: "ring" }, g);
        el("rect", { x: zx + 4.75, y: yServ + 4.75, width: zw - 9.5, height: zh - 9.5, class: "ring-in" }, g);
        const fs = Math.max(15, Math.min(30, zw * 0.34));
        const sx = zx + zw / 2;
        const shareText = fPct(c.share, 0);
        const tS = txt(g, sx, yServ + zh * 0.36, shareText, "zt-share", "middle", { style: "font-size:" + fs + "px;fill:" + col.ink });
        fitText(tS, zw - 6, 11);
        const ds = Math.max(11, Math.min(12.5, zw / 6));
        const tD = txt(g, sx, yServ + zh * 0.36 + ds + 8, dir, "zt-dir", "middle", { style: "font-size:" + ds + "px;fill:" + col.ink });
        fitText(tD, zw - 6, 11);
        // small figures never go below 11px; a label that does not fit the zone at that size is dropped
        const ss = Math.max(11, Math.min(12, zw / 5.5));
        const small = c.small || [];
        const y0 = yServ + zh * (small.length > 1 ? 0.62 : 0.72);
        const smallNodes = [];
        small.forEach((sm, j) => {
          const yy = y0 + j * (2 * (ss + 3) + 5);
          // label may be a list of candidates, longest first: the first one that fits is kept
          const labels = Array.isArray(sm.label) ? sm.label : [sm.label];
          for (let li = 0; li < labels.length; li++) {
            const tl = txt(g, sx, yy, labels[li], "zt-small zt-lab", "middle", { style: "font-size:" + ss + "px;fill:" + col.ink });
            if (tl.getComputedTextLength() <= zw - 6) break;
            tl.remove();
          }
          const tv = txt(g, sx, yy + ss + 3, fPct(sm.value, 0), "zt-small", "middle", { style: "font-size:" + ss + "px;fill:" + col.ink });
          smallNodes.push(tv);
        });
        next[key] = { share: c.share, fill: col.fill, small: small.map((s) => s.value) };

        if (animate && prev[key]) {
          const p = prev[key];
          if (p.fill !== col.fill && fill.animate) {
            fill.animate([{ fill: p.fill }, { fill: col.fill }], { duration: 520, easing: "cubic-bezier(0.22, 1, 0.36, 1)", delay: i * 40 });
          }
          tweenText(tS, p.share, c.share, (v) => fPct(v, 0));
          smallNodes.forEach((n, j) => {
            if (p.small.length === small.length) tweenText(n, p.small[j], small[j].value, (v) => fPct(v, 0));
          });
        }

        const select = (how) => { if (o.onSelect) o.onSelect(side, dir, how); };
        g.addEventListener("click", () => select("click"));
        g.addEventListener("focus", () => select("focus"));
        g.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") select("hover"); });
        g.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select("key"); }
        });
        zones.push(g);
      });
    });

    // strong lines on top: service boxes, center service line, sidelines, net
    el("rect", { x: x0, y: yServ, width: SINGLES_W * k, height: zh, class: "line" }, svg);
    el("line", { x1: x0 + BOX_W * k, y1: yServ, x2: x0 + BOX_W * k, y2: yNet, class: "line" }, svg);
    el("line", { x1: x0 - 0.25 * k, y1: yNet, x2: xr + 0.25 * k, y2: yNet, class: "net" }, svg);
    el("circle", { cx: x0 - 0.25 * k, cy: yNet, r: 3.5, class: "post" }, svg);
    el("circle", { cx: xr + 0.25 * k, cy: yNet, r: 3.5, class: "post" }, svg);
    txt(svg, (x0 + xr) / 2, yNet + 22, "Net. The server stands on the near side.", "court-lab", "middle");

    prev = next;
    mark(view.selected);
  }

  return { draw, mark };
}

/* text tween for SVG <text>, from a known start value (the node is new, so ui.tween has no memory of it) */
function tweenText(node, from, to, f) {
  if (!isNum(from) || !isNum(to) || from === to) return;
  const dur = 520;
  const t0 = performance.now();
  const step = (now) => {
    if (!node.isConnected) return;
    const k = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    node.textContent = f(from + (to - from) * e);
    if (k < 1) requestAnimationFrame(step);
    else node.textContent = f(to);
  };
  node.textContent = f(from);
  requestAnimationFrame(step);
}
