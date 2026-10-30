/* Serve IQ league dashboard (#league). ES module.
   Four cards ported from the v2 league panels A to D (site/app.js renderLeague): points won by
   direction, break-point shift, split-half repeatability and serial repeats. Same rows, same
   numbers, same confidence intervals and the same rules for every sentence; only the layout changed.
   Every number comes from data/serveiq.json through data.js. */

import {
  DIRS, SIDES, SIDE_LABEL, TOUR_PLURAL, MATCHUP, SERVE_LABEL, CI_LEVEL,
  fPct, fInt, fNum, fSigned, ciText, esc, isNum,
} from "./data.js";
import * as ui from "./ui.js";

const FE_WIN = "server x side x matchup x break point";
const FE_BP = "match x server x side";
const MU_SHORT = { RvR: "R vs R", RvL: "R vs L", LvR: "L vs R", LvL: "L vs L" };

let D = null;
let root = null;
const S = { g: "m", s: 1, mu: "RvR" };
const prev = { g: null, s: null, mu: null };
const cards = {};
let lastWidth = 0;

// ------------------------------------------------------------ small helpers

const ci = () => fPct(CI_LEVEL, 0);
// a same-server estimate is "clear" when its interval excludes zero (the v2 caption rule)
const sig = (r) => !!r && isNum(r.lo_pp) && isNum(r.hi_pp) && (r.lo_pp > 0 || r.hi_pp < 0);
function wText(r, d) {
  if (!r) return "n/a";
  return fSigned(r.coef_pp, d) + " percentage points (" + ci() + " CI " + fNum(r.lo_pp, d) + " to " + fNum(r.hi_pp, d) + ")";
}
function slot(card, name) { return card.querySelector('[data-lg-slot="' + name + '"]'); }
function reduced() { return ui.reducedMotion() || document.documentElement.classList.contains("reveal-all"); }

function tableHTML(caption, cols, rows) {
  return "<table><caption>" + esc(caption) + "</caption><thead><tr>" +
    cols.map((c) => '<th scope="col"' + (c.left ? ' class="l"' : "") + ">" + esc(c.label) + "</th>").join("") +
    "</tr></thead><tbody>" +
    rows.map((r) => "<tr>" + r.map((v, i) => (i === 0 ? '<th scope="row">' : "<td" + (cols[i] && cols[i].left ? ' class="l"' : "") + ">") +
      esc(v) + (i === 0 ? "</th>" : "</td>")).join("") + "</tr>").join("") +
    "</tbody></table>";
}

/* tags under the title: only the fixed scopes that differ from the toolbar (the toolbar already shows tour, serve and hands) */
function setTags(card, tags) {
  const host = slot(card, "tags");
  host.hidden = !tags.length;
  const old = card.__tags || {};
  host.innerHTML = tags.map((t) => '<span class="lg-tag' + (t.fixed ? " lg-tag-fixed" : "") +
    (t.note ? " lg-tag-note" : "") + (old[t.key] != null && old[t.key] !== t.text ? " is-new" : "") + '">' + esc(t.text) + "</span>").join("");
  const now = {};
  tags.forEach((t) => { now[t.key] = t.text; });
  card.__tags = now;
}

/* mini stat tiles, built once per card and updated in place so the numbers tween */
function setStats(card, items, scope) {
  const host = slot(card, "stats");
  if (!items.length) { host.hidden = true; host.innerHTML = ""; card.__stats = null; return; }
  host.hidden = false;
  if (!card.__stats || card.__stats.length !== items.length) {
    host.innerHTML = '<p class="lg-stats-scope"></p>' + items.map(() =>
      '<div class="lg-stat"><span class="lg-stat-label" aria-hidden="true"></span>' +
      '<span class="lg-stat-value" aria-hidden="true"><span class="lg-stat-num"></span><span class="lg-stat-unit">pts</span></span>' +
      '<span class="visually-hidden lg-stat-sr"></span></div>').join("");
    card.__stats = Array.prototype.slice.call(host.querySelectorAll(".lg-stat"));
  }
  const sc = host.querySelector(".lg-stats-scope");
  sc.textContent = scope || "";
  sc.hidden = !scope;
  items.forEach((it, i) => {
    const box = card.__stats[i];
    const r = it.r;
    const clear = sig(r);
    box.classList.toggle("is-sig", clear);
    box.classList.toggle("is-ns", !clear);
    box.querySelector(".lg-stat-label").textContent = it.label;
    const num = box.querySelector(".lg-stat-num");
    if (r && isNum(r.coef)) ui.tween(num, r.coef, (v) => fSigned(v, 2), { duration: 700 });
    else num.textContent = "n/a";
    box.querySelector(".lg-stat-sr").textContent = (it.tip || it.label) + ": " +
      (r && isNum(r.coef) ? fSigned(r.coef, 2) + " percentage points, " + ci() + " CI " + fNum(r.lo, 2) + " to " + fNum(r.hi, 2) +
        (clear ? "." : ", the interval includes zero.") : "n/a.");
  });
}

function setInfo(card, text, label) {
  const host = slot(card, "info");
  let btn = host.querySelector("button.info");
  if (!btn) {
    host.innerHTML = ui.infoButton(text, label);
    btn = host.querySelector("button.info");
    ui.initPopovers(host);
  } else {
    ui.setInfo(btn, text);
  }
}

function setTitle(card, text) {
  const h = slot(card, "title");
  if (h.textContent === text) return;
  const first = !h.textContent;
  h.textContent = text;
  if (!first && !reduced()) {
    h.classList.remove("lg-swap");
    void h.offsetWidth;
    h.classList.add("lg-swap");
  }
}

/* re-run the grow-in of the marks after the controls change (only once the card has been revealed) */
function replay(card) {
  if (reduced() || !card.classList.contains("is-in")) return;
  const chart = slot(card, "chart");
  chart.classList.add("lg-reset");
  void chart.offsetWidth;
  chart.classList.remove("lg-reset");
}

// ------------------------------------------------------------ chart: dots with whiskers, two side panels, shared axis

function drawDots(host, panels, opt) {
  const W = Math.max(280, Math.floor(host.clientWidth || 560));
  const H = opt.height || 230;
  const narrow = W < 420;
  const ml = 40, mr = 4, mt = 30, mb = 28, gap = narrow ? 14 : 24;
  const iw = (W - ml - mr - gap) / panels.length;
  const ih = H - mt - mb;
  const svg = ui.newSvg(host, W, H, opt.label);
  const vals = [];
  panels.forEach((p) => p.items.forEach((it) => vals.push(it.lo, it.hi, it.v)));
  const dom = ui.domainOf(vals, 0.1, 4, 0, 1);
  const y = (v) => mt + ih - ((v - dom.lo) / (dom.hi - dom.lo)) * ih;
  dom.ticks.forEach((tv) => ui.txt(svg, ml - 8, y(tv) + 4, fPct(tv, 0), null, "end"));
  let k = 0;
  panels.forEach((p, pi) => {
    const ox = ml + pi * (iw + gap);
    const g = ui.el("g", {}, svg);
    ui.txt(g, ox, 14, p.title, "panel-title");
    const grid = ui.el("g", { class: "grid" }, g);
    dom.ticks.forEach((tv) => ui.el("line", { x1: ox, x2: ox + iw, y1: y(tv), y2: y(tv) }, grid));
    ui.el("line", { x1: ox, x2: ox + iw, y1: mt + ih, y2: mt + ih, class: "baseline" }, g);
    const band = iw / p.items.length;
    p.items.forEach((it, i) => {
      const cx = ox + band * (i + 0.5);
      ui.txt(g, cx, H - 8, it.cat, "cat", "middle");
      if (!isNum(it.v)) {
        ui.txt(g, cx, mt + ih / 2, "n/a", "val-muted", "middle");
        return;
      }
      const d = k++;
      const wc = it.muted ? "w-muted" : "w-player";
      if (isNum(it.lo) && isNum(it.hi)) {
        const w = ui.el("g", { class: "fade-in", style: "--d:" + d }, g);
        ui.el("line", { x1: cx, x2: cx, y1: y(it.lo), y2: y(it.hi), class: wc }, w);
        ui.el("line", { x1: cx - 5, x2: cx + 5, y1: y(it.lo), y2: y(it.lo), class: wc }, w);
        ui.el("line", { x1: cx - 5, x2: cx + 5, y1: y(it.hi), y2: y(it.hi), class: wc }, w);
      }
      ui.el("circle", { cx: cx, cy: y(it.v), r: narrow ? 5 : 6, class: (it.muted ? "m-player-muted" : "m-player") + " pop-in", style: "--d:" + d }, g);
      const ly = (isNum(it.hi) ? y(it.hi) : y(it.v)) - 8;
      ui.txt(g, cx, ly, fPct(it.v, 1), (it.muted ? "val-muted" : "val") + " fade-in lg-val" + (band < 46 ? " lg-val-sm" : ""), "middle", { style: "--d:" + (d + 2) });
      ui.el("rect", { x: cx - band / 2, y: mt, width: band, height: ih, class: "hit", "data-tip": it.tip }, g);
    });
  });
}

// ------------------------------------------------------------ chart: grouped bars, shared axis

function drawBars(host, panels, opt) {
  const W = Math.max(280, Math.floor(host.clientWidth || 560));
  const H = opt.height || 230;
  const narrow = W < 420;
  const ml = 40, mr = 4, mb = 28, gap = narrow ? 14 : 24;
  const mt = panels.some((p) => p.title) || opt.axisTitle ? 30 : 16;
  const iw = (W - ml - mr - gap * (panels.length - 1)) / panels.length;
  const ih = H - mt - mb;
  const svg = ui.newSvg(host, W, H, opt.label);
  const dom = opt.domain;
  const y = (v) => mt + ih - ((v - dom.lo) / (dom.hi - dom.lo)) * ih;
  dom.ticks.forEach((tv) => ui.txt(svg, ml - 8, y(tv) + 4, opt.tick(tv), null, "end"));
  if (opt.axisTitle) {
    const at = ui.txt(svg, 0, 14, opt.axisTitle, "lg-axis-title", "start", { style: "font-size:12px" });
    ui.fitText(at, W - 4, 9.5);
  }
  let k = 0;
  panels.forEach((p, pi) => {
    const ox = ml + pi * (iw + gap);
    const g = ui.el("g", {}, svg);
    if (p.title) ui.txt(g, ox, 14, p.title, "panel-title");
    const grid = ui.el("g", { class: "grid" }, g);
    dom.ticks.forEach((tv) => ui.el("line", { x1: ox, x2: ox + iw, y1: y(tv), y2: y(tv) }, grid));
    const y0 = y(Math.max(dom.lo, Math.min(dom.hi, 0)));
    const band = iw / p.groups.length;
    p.groups.forEach((grp, i) => {
      const cx = ox + band * (i + 0.5);
      ui.txt(g, cx, H - 8, grp.cat, "cat", "middle");
      const nb = grp.bars.length;
      const bw = Math.max(8, Math.min(opt.maxBar || 30, (band * 0.74 - 4 * (nb - 1)) / nb));
      const total = nb * bw + 4 * (nb - 1);
      grp.bars.forEach((b, j) => {
        const bx = cx - total / 2 + j * (bw + 4);
        if (!isNum(b.v)) {
          ui.txt(g, bx + bw / 2, y0 - 6, "n/a", "val-muted", "middle");
          return;
        }
        const d = k++;
        ui.el("path", { d: ui.barPath(bx, y0, y(b.v), bw, 4), class: b.cls + " grow-y" + (b.v < 0 ? " from-top" : ""), style: "--d:" + d }, g);
        if (isNum(b.lo) && isNum(b.hi)) {
          const m = bx + bw / 2;
          const w = ui.el("g", { class: "fade-in", style: "--d:" + (d + 3) }, g);
          ui.el("line", { x1: m, x2: m, y1: y(b.lo), y2: y(b.hi), class: "ci" }, w);
          ui.el("line", { x1: m - 3, x2: m + 3, y1: y(b.lo), y2: y(b.lo), class: "ci" }, w);
          ui.el("line", { x1: m - 3, x2: m + 3, y1: y(b.hi), y2: y(b.hi), class: "ci" }, w);
        }
        if (b.label) {
          const top = b.v >= 0 ? Math.min(y(b.v), isNum(b.hi) ? y(b.hi) : y(b.v)) - 7 : Math.max(y(b.v), isNum(b.lo) ? y(b.lo) : y(b.v)) + 15;
          ui.txt(g, bx + bw / 2, top, b.label, "val fade-in lg-val", "middle", { style: "--d:" + (d + 3) });
        }
        ui.el("rect", { x: bx - 2, y: mt, width: bw + 4, height: ih, class: "hit", "data-tip": b.tip }, g);
      });
    });
    ui.el("line", { x1: ox, x2: ox + iw, y1: y0, y2: y0, class: dom.lo < 0 ? "zero" : "baseline" }, g);
  });
}

// ------------------------------------------------------------ chart: dumbbell (actual vs random order)

function drawSerial(host, rows, label) {
  const W = Math.max(280, Math.floor(host.clientWidth || 500));
  const rowH = 62, mt = 8, mb = 28, ml = 86, mr = 18;
  const H = mt + rows.length * rowH + mb;
  const svg = ui.newSvg(host, W, H, label);
  const vals = [];
  rows.forEach((r) => vals.push(r.actual, r.random));
  const dom = ui.domainOf(vals, 0.25, 4, 0, 1);
  const x = (v) => ml + ((v - dom.lo) / (dom.hi - dom.lo)) * (W - ml - mr);
  const grid = ui.el("g", { class: "grid" }, svg);
  dom.ticks.forEach((tv) => {
    ui.el("line", { x1: x(tv), x2: x(tv), y1: mt, y2: mt + rows.length * rowH }, grid);
    ui.txt(svg, x(tv), H - 8, fPct(tv, 0), null, "middle");
  });
  rows.forEach((r, i) => {
    const cy = mt + i * rowH + rowH / 2 + 6;
    ui.txt(svg, 0, cy + 4, r.label, "cat");
    const lo = Math.min(x(r.random), x(r.actual));
    ui.el("rect", { x: lo, y: cy - 3, width: Math.abs(x(r.actual) - x(r.random)), height: 6, rx: 3, class: "lg-gap grow-x", style: "--d:" + (i * 3) }, svg);
    ui.el("circle", { cx: x(r.random), cy: cy, r: 6, class: "m-league pop-in", style: "--d:" + (i * 3) }, svg);
    ui.el("circle", { cx: x(r.actual), cy: cy, r: 6.5, class: "m-player pop-in", style: "--d:" + (i * 3 + 1) }, svg);
    const leftIsActual = r.actual <= r.random;
    ui.txt(svg, x(r.actual) + (leftIsActual ? -12 : 12), cy - 12, fPct(r.actual, 1), "val fade-in", leftIsActual ? "end" : "start", { style: "--d:" + (i * 3 + 2) });
    ui.txt(svg, x(r.random) + (leftIsActual ? 12 : -12), cy - 12, fPct(r.random, 1), "val-muted fade-in", leftIsActual ? "start" : "end", { style: "--d:" + (i * 3 + 2) });
    ui.el("rect", { x: ml, y: cy - rowH / 2, width: W - ml - mr, height: rowH, class: "hit", "data-tip": r.tip }, svg);
  });
}

// ------------------------------------------------------------ card A: points won by direction

/* The title follows the pooled same-server body vs wide gap (all matchups), so it says "Same server".
   The T vs wide gap is small and does not repeat across halves (card C); it stays in the tile and popover. */
function titleA(wb) {
  if (!wb) return "Points won after each serve direction";
  if (!sig(wb)) return "Same server: body and wide win similar shares";
  return wb.coef_pp < 0 ? "Same server: body serves win fewer points than wide" : "Same server: body serves win more points than wide";
}

function renderA(rows, get, anyLow) {
  const card = cards.a;
  const { g, s, mu } = S;
  const sw = SERVE_LABEL[s];
  const wb = D.within(g, s, "win body minus win wide", FE_WIN);
  const wt = D.within(g, s, "win T minus win wide", FE_WIN);
  const ib = D.within(g, s, "serve in body minus serve in wide", FE_WIN);
  setTitle(card, titleA(wb));
  setTags(card, []);
  const st = (r) => r && { coef: r.coef_pp, lo: r.lo_pp, hi: r.hi_pp, lo_pp: r.lo_pp, hi_pp: r.hi_pp };
  setStats(card, [
    { label: "Body vs wide", r: st(wb), tip: "Points won, body minus wide, same server and side, all matchups" },
    { label: "T vs wide", r: st(wt), tip: "Points won, T minus wide, same server and side, all matchups" },
  ], "Same server, all matchups");
  ui.setKey(slot(card, "key"), [[ui.keyDot("m-player"), "Points won, " + ci() + " CI"]].concat(anyLow ? [[ui.keyDot("m-player-muted"), "Thin sample"]] : []));
  drawDots(slot(card, "chart"), SIDES.map((side) => ({
    title: SIDE_LABEL[side],
    items: DIRS.map((d) => {
      const r = get(side, false, d) || {};
      return {
        cat: d, v: r.win, lo: r.win_lo, hi: r.win_hi, muted: !!r.low_n,
        tip: SIDE_LABEL[side] + ", " + d + "\n" + fPct(r.win, 1) + " won (" + ci() + " CI " + ciText(r.win_lo, r.win_hi, 1) + ")\nUsed on " +
          fPct(r.share, 1) + " of serves\n" + fInt(r.n) + " serves" + (r.low_n ? "\nThin sample" : ""),
      };
    }),
  })), { height: 236, label: "League points won by direction, non-break points, " + MATCHUP[mu] + ", " + sw + " serves, " + TOUR_PLURAL[g] });
  slot(card, "caption").textContent = "Dots show the selected matchup on non-break points.";
  setInfo(card,
    "Dots: share of points the server won after serving each direction, " + MATCHUP[mu] + ", " + sw + " serves, non-break points, with " + ci() +
    " confidence intervals" + (anyLow ? "; gray marks are thin samples" : "") + ". Tiles: each server compared with their own wide serves on the same side, pooled over all matchups (" +
    TOUR_PLURAL[g] + "), so they can differ from the dots of one matchup. Points won, body minus wide, " + wText(wb, 2) + "; T minus wide, " + wText(wt, 2) +
    ", a small gap that does not repeat across halves (card C). Serve-in rate, body minus wide, " + wText(ib, 2) + ".",
    "About card A, points won by direction");
  const tr = [];
  SIDES.forEach((side) => DIRS.forEach((d) => {
    const r = get(side, false, d);
    if (!r) return;
    tr.push([SIDE_LABEL[side], d, fPct(r.win, 1), ciText(r.win_lo, r.win_hi, 1), fPct(r.share, 1), fInt(r.n), r.low_n ? "thin" : ""]);
  }));
  slot(card, "table").innerHTML = tableHTML(MATCHUP[mu] + ", " + TOUR_PLURAL[g] + ", " + sw + " serves, non-break points.", [
    { label: "Side" }, { label: "Direction", left: true }, { label: "Won" }, { label: "Won " + ci() + " CI" }, { label: "Share" },
    { label: "Serves" }, { label: "Sample" },
  ], tr);
}

// ------------------------------------------------------------ card B: break points against other points

function titleB(bw2, bb2) {
  const word = (r, more, fewer) => (!r ? null : r.lo_pp > 0 ? more : r.hi_pp < 0 ? fewer : null);
  const shifts = [word(bw2, "more wide serves", "fewer wide serves"), word(bb2, "more body serves", "fewer body serves")].filter(Boolean);
  return shifts.length ? "Break points: " + shifts.join(", ") : "Servers keep the same mix on break points";
}

function renderB(rows, get, anyLow) {
  const card = cards.b;
  const { g, s, mu } = S;
  const sw = SERVE_LABEL[s];
  const bw2 = D.within(g, s, "break_point: change in wide share", FE_BP);
  const bb2 = D.within(g, s, "break_point: change in body share", FE_BP);
  setTitle(card, titleB(bw2, bb2));
  setTags(card, []);
  const st = (r) => r && { coef: r.coef_pp, lo: r.lo_pp, hi: r.hi_pp, lo_pp: r.lo_pp, hi_pp: r.hi_pp };
  setStats(card, [
    { label: "Wide share", r: st(bw2), tip: "Change in the wide share on break points, same server, match and side, all matchups" },
    { label: "Body share", r: st(bb2), tip: "Change in the body share on break points, same server, match and side, all matchups" },
  ], "Same server and match, all matchups");
  ui.setKey(slot(card, "key"), [[ui.keySquare("b-ref"), "Other points"], [ui.keySquare("b-1"), "Break points"]]);
  const shareVals = [];
  rows.forEach((r) => shareVals.push(r.share_hi, r.share));
  const sd = ui.domainOf(shareVals.concat([0]), 0.04, 4, 0, 1);
  drawBars(slot(card, "chart"), SIDES.map((side) => ({
    title: SIDE_LABEL[side],
    groups: DIRS.map((d) => ({
      cat: d,
      bars: [false, true].map((bp) => {
        const r = get(side, bp, d) || {};
        return {
          v: r.share, lo: r.share_lo, hi: r.share_hi, cls: bp ? "b-1" : "b-ref",
          tip: SIDE_LABEL[side] + ", " + d + ", " + (bp ? "break points" : "other points") + "\n" + fPct(r.share, 1) + " of serves (" + ci() + " CI " +
            ciText(r.share_lo, r.share_hi, 1) + ")\n" + fInt(r.n) + " serves" + (r.low_n ? "\nThin sample" : ""),
        };
      }),
    })),
  })), {
    domain: { lo: 0, hi: sd.hi, ticks: sd.ticks.filter((t) => t >= 0) }, tick: (v) => fPct(v, 0), height: 236, maxBar: 22,
    label: "League share of serves by direction on break points and other points, " + MATCHUP[mu] + ", " + sw + " serves, " + TOUR_PLURAL[g],
  });
  slot(card, "caption").textContent = "Bars show the selected matchup.";
  setInfo(card,
    "Bars: share of serves to each direction on break points and on other points, " + MATCHUP[mu] + ", " + sw + " serves, with " + ci() +
    " confidence intervals" + (anyLow ? "; some cells are thin samples" : "") + ". Tiles: the same server in the same match and side, pooled over all matchups (" + TOUR_PLURAL[g] +
    "). On break points the wide share changes by " + wText(bw2, 2) + " and the body share by " + wText(bb2, 2) + ".",
    "About card B, break points");
  const tr = [];
  SIDES.forEach((side) => [false, true].forEach((bp) => DIRS.forEach((d) => {
    const r = get(side, bp, d);
    if (!r) return;
    tr.push([SIDE_LABEL[side], bp ? "Break points" : "Other points", d, fPct(r.share, 1), ciText(r.share_lo, r.share_hi, 1), fInt(r.n), r.low_n ? "thin" : ""]);
  })));
  slot(card, "table").innerHTML = tableHTML(MATCHUP[mu] + ", " + TOUR_PLURAL[g] + ", " + sw + " serves.", [
    { label: "Side" }, { label: "Points", left: true }, { label: "Direction", left: true }, { label: "Share" }, { label: "Share " + ci() + " CI" },
    { label: "Serves" }, { label: "Sample" },
  ], tr);
}

// ------------------------------------------------------------ card C: split-half repeatability (1st serves)

function renderC() {
  const card = cards.c;
  const { g } = S;
  const st = D.stability(g);
  const stGet = (side, m) => { for (let i = 0; i < st.length; i++) if (st[i].side === side && st[i].metric === m) return st[i]; return null; };
  const favs = st.filter((r) => r.metric === "guess").map((r) => r.corr).filter(isNum);
  const edges = st.filter((r) => r.metric === "edge").map((r) => r.corr).filter(isNum);
  const sep = favs.length && edges.length && Math.min.apply(null, favs) > Math.max.apply(null, edges);
  setTitle(card, sep ? "Favorite-target share repeats; the win gap mostly does not" : "Which numbers repeat across two halves");
  const nPlayers = st.length ? st[0].players : null;
  setTags(card, [
    { key: "s", text: "1st serves only", fixed: true, note: S.s !== 1 }, { key: "mu", text: "All matchups", fixed: true },
  ]);
  setStats(card, []);
  ui.setKey(slot(card, "key"), [[ui.keySquare("b-1"), "Favorite-target share"], [ui.keySquare("b-2"), "Win edge of T over wide"]]);
  const rv = st.map((r) => r.corr);
  const lo = Math.min(0, Math.min.apply(null, rv));
  const cdom = { lo: lo < 0 ? -0.25 : 0, hi: 1, ticks: lo < 0 ? [-0.25, 0, 0.25, 0.5, 0.75, 1] : [0, 0.25, 0.5, 0.75, 1] };
  drawBars(slot(card, "chart"), [{
    title: null,
    groups: SIDES.map((side) => ({
      cat: SIDE_LABEL[side],
      bars: [["guess", "b-1", "Favorite-target share"], ["edge", "b-2", "Win edge of T over wide"]].map((m) => {
        const r = stGet(side, m[0]) || {};
        return { v: r.corr, cls: m[1], label: fNum(r.corr, 2), tip: SIDE_LABEL[side] + "\n" + m[2] + "\nr = " + fNum(r.corr, 2) + " across " + fInt(r.players) + " players" };
      }),
    })),
  }], {
    domain: cdom, tick: (v) => fNum(v, 2), maxBar: 44,
    height: (slot(card, "chart").clientWidth || 500) < 420 ? 270 : 318,
    axisTitle: "How well the two halves agree (0 none, 1 perfect)",
    label: "Split-half correlation of favorite-target share and the win edge of T over wide, " + TOUR_PLURAL[g] + ", 1st serves",
  });
  slot(card, "caption").textContent = "Each player's matches split into two random halves; a stable trait scores high in both.";
  const fr = (side, m) => { const r = stGet(side, m); return r ? fNum(r.corr, 2) : "n/a"; };
  setInfo(card,
    "Pearson r between two random halves of each player's matches, " + TOUR_PLURAL[g] + ", 1st serves, all matchups, " + fInt(nPlayers) +
    " listed players; one split, no confidence interval. A stable trait shows up in both halves (r near 1); noise does not (r near 0). Favorite-target share: r = " +
    fr("deuce", "guess") + " (deuce) and " + fr("ad", "guess") + " (ad). Win edge of T over wide: r = " + fr("deuce", "edge") + " (deuce) and " + fr("ad", "edge") + " (ad).",
    "About card C, what repeats across halves");
  const tr = [];
  SIDES.forEach((side) => [["guess", "Favorite-target share"], ["edge", "Win edge of T over wide"]].forEach((m) => {
    const r = stGet(side, m[0]);
    if (r) tr.push([SIDE_LABEL[side], m[1], fNum(r.corr, 2), fInt(r.players)]);
  }));
  slot(card, "table").innerHTML = tableHTML("Split-half r, " + TOUR_PLURAL[g] + ", 1st serves, all matchups.", [
    { label: "Side" }, { label: "Measure", left: true }, { label: "r" }, { label: "Players" },
  ], tr);
}

// ------------------------------------------------------------ card D: repeating the last direction

function renderD() {
  const card = cards.d;
  const { g } = S;
  const ser = [1, 2].map((sn) => D.serial(g, sn)).filter(Boolean);
  const allBelow = ser.length && ser.every((r) => isNum(r.hi_pp) && r.hi_pp < 0);
  setTitle(card, allBelow ? "Servers switch direction more often than random order" : "Servers repeat directions about as often as random order");
  setTags(card, [{ key: "mu", text: "All matchups", fixed: true }]);
  setStats(card, ser.map((r) => ({
    label: SERVE_LABEL[r.serve_num] + " serves",
    r: { coef: r.diff_pp, lo: r.lo_pp, hi: r.hi_pp, lo_pp: r.lo_pp, hi_pp: r.hi_pp },
    tip: "Repeat rate minus the rate in random order, " + SERVE_LABEL[r.serve_num] + " serves, " + TOUR_PLURAL[g],
  })));
  ui.setKey(slot(card, "key"), [[ui.keyDot("m-player"), "Actual repeat rate"], [ui.keyRing(), "Same serves, random order"]]);
  drawSerial(slot(card, "chart"), ser.map((r) => ({
    label: SERVE_LABEL[r.serve_num] + " serve", actual: r.repeat_rate, random: r.repeat_rate_if_random_order,
    tip: SERVE_LABEL[r.serve_num] + " serves, " + TOUR_PLURAL[g] + "\nRepeats the last direction: " + fPct(r.repeat_rate, 1) +
      "\nRandom order: " + fPct(r.repeat_rate_if_random_order, 1) + "\nDifference: " + fSigned(r.diff_pp, 2) + " percentage points\n" + fInt(r.transitions) + " consecutive pairs",
  })), "Repeat rate of serve direction against random order, " + TOUR_PLURAL[g]);
  const maxGap = Math.max.apply(null, ser.map((r) => Math.abs(r.diff_pp)).filter(isNum));
  slot(card, "caption").textContent = "Below zero means more switching than random order" + (isFinite(maxGap) ? "; the largest gap is " + fNum(maxGap, 2) + " percentage points." : ".");
  setInfo(card,
    "Share of serves that go to the same direction as the previous serve (same match, server, side and serve number), " + TOUR_PLURAL[g] +
    ", all matchups, compared with a random order of the same serves. " +
    ser.map((r) => SERVE_LABEL[r.serve_num] + " serves: " + fPct(r.repeat_rate, 1) + " against " + fPct(r.repeat_rate_if_random_order, 1) +
      " in random order, a difference of " + fSigned(r.diff_pp, 2) + " percentage points (" + ci() + " CI " + fNum(r.lo_pp, 2) + " to " + fNum(r.hi_pp, 2) + ").").join(" ") +
    " Switching more often than a random order would has been reported before in professional tennis (Walker and Wooders 2001).",
    "About card D, repeating the last direction");
  slot(card, "table").innerHTML = tableHTML("Repeat rate of serve direction, " + TOUR_PLURAL[g] + ", all matchups.", [
    { label: "Serve" }, { label: "Repeats" }, { label: "Random order" }, { label: "Difference (points)" }, { label: ci() + " CI" }, { label: "Pairs" },
  ], ser.map((r) => [SERVE_LABEL[r.serve_num] + " serves", fPct(r.repeat_rate, 1), fPct(r.repeat_rate_if_random_order, 1), fSigned(r.diff_pp, 2),
    fNum(r.lo_pp, 2) + " to " + fNum(r.hi_pp, 2), fInt(r.transitions)]));
}

// ------------------------------------------------------------ render all

function render(animate) {
  const rows = D.leagueRows(S.g, S.mu, S.s);
  const get = (side, bp, d) => D.leagueCell(rows, side, bp, d);
  const anyLow = rows.some((r) => r.low_n);
  const changed = { a: true, b: true, c: S.g !== prev.g, d: S.g !== prev.g };
  renderA(rows, get, anyLow);
  renderB(rows, get, anyLow);
  renderC();
  renderD();
  if (animate) Object.keys(cards).forEach((k) => { if (changed[k]) replay(cards[k]); });
  prev.g = S.g; prev.s = S.s; prev.mu = S.mu;
  lastWidth = root.querySelector(".lg-grid").clientWidth;
}

function initTables() {
  root.querySelectorAll("[data-lg-table-btn]").forEach((btn) => {
    const box = document.getElementById(btn.getAttribute("aria-controls"));
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") !== "true";
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.textContent = open ? "Hide table" : "Show table";
      box.hidden = !open;
    });
  });
}

/* sticky filter bar (900px and up): once it touches the top bar it turns compact. The margin makes up
   the lost height so the cards below do not jump. */
function initSticky() {
  const bar = root.querySelector(".lg-toolbar");
  if (!bar) return;
  const mq = window.matchMedia("(min-width: 900px)");
  let stuck = false;
  let queued = false;
  const navH = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--nav-h")) || 60;
  const check = () => {
    queued = false;
    const want = mq.matches && bar.getBoundingClientRect().top <= navH() + 0.5;
    if (want === stuck) return;
    stuck = want;
    if (want) {
      const full = bar.offsetHeight;
      bar.classList.add("is-stuck");
      bar.style.marginBottom = Math.max(0, full - bar.offsetHeight) + "px";
    } else {
      bar.classList.remove("is-stuck");
      bar.style.marginBottom = "";
    }
  };
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(check); } };
  window.addEventListener("scroll", queue, { passive: true });
  window.addEventListener("resize", queue);
  check();
}

function initBanner() {
  const img = root.querySelector("[data-lg-img]");
  const credit = root.querySelector("[data-lg-credit]");
  const sc = D.scene("stadium");
  if (!sc || !sc.src) {
    if (img) img.remove();
    if (credit) credit.remove();
    return;
  }
  if (img && img.getAttribute("src") !== sc.src) img.setAttribute("src", sc.src);
  if (img) img.addEventListener("error", () => { img.remove(); if (credit) credit.remove(); });
  if (credit) {
    credit.textContent = "Photo: " + (sc.author || "Wikimedia Commons") + (sc.license ? ", " + sc.license : "");
    credit.setAttribute("aria-label", "Photo credit: " + (sc.author || "Wikimedia Commons") + ", see all photo credits");
  }
}

export function init(ctx) {
  D = ctx.data;
  root = document.getElementById("league");
  if (!D || !root) return;
  ["a", "b", "c", "d"].forEach((k) => { cards[k] = document.getElementById("lg-" + k); });
  initBanner();
  const onChange = (key) => (v) => {
    S[key] = key === "s" ? Number(v) : v;
    ui.closePopover(false);
    render(true);
  };
  ui.segmented(root.querySelector("[data-lg-tour]"), { value: S.g, onChange: onChange("g") });
  ui.segmented(root.querySelector("[data-lg-serve]"), { value: String(S.s), onChange: onChange("s") });
  ui.segmented(root.querySelector("[data-lg-matchup]"), { value: S.mu, onChange: onChange("mu") });
  root.querySelectorAll("[data-lg-matchup] button").forEach((b) => { b.title = MATCHUP[b.getAttribute("data-value")]; });
  const ti = root.querySelector('[data-lg-slot="toolbar-info"]');
  ti.innerHTML = ui.infoButton("R is right-handed, L is left-handed. Hands apply to cards A and B. Cards C and D pool all matchups, and card C uses 1st serves only.",
    "About the league filters");
  ui.initPopovers(ti);
  initTables();
  initSticky();
  render(false);
  window.addEventListener("resize", ui.debounce(() => {
    const w = root.querySelector(".lg-grid").clientWidth;
    if (w !== lastWidth) render(false);
  }, 150));
}
