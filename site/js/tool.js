/* Serve IQ "Call the serve" (#tool and the picker bar). ES module.
   One player, one side, one serve number at a time. The court (js/court.js) hides that side's serve paths
   and asks the visitor to tap the lane they expect; the tap reveals the paths and a result block, and the
   scouting card fills in. A situation switch (all points / break points) re-weights the paths.

   Every number is a share already in serveiq.json (mix cells) or a rule over them. Nothing is sampled,
   so a call always scores the same: the share of serves that went to the lane picked.

   Rules (shared with js/explore.js through ctx.tool):
     typical-pro lane  the lane with the largest league_share (where a typical pro facing the same
                       returners serves). When the top two are within NO_CLEAR_FAVORITE they are both
                       the typical-pro lane ("splits"), and the baseline is the player's average share
                       over the two.
     scouting edge     favorite share minus that baseline, shown only when the favorite's 95% range sits
                       above the range of every other typical-pro lane; "same lane" when the favorite is
                       the only typical-pro lane.
     serves needed     z^2 (p1 + p2 - d^2) / d^2 with d = p1 - p2, the two largest shares on all points
                       (the favorite's lead clears its 95% range); matches = that times matches / serves.
     break points      a change is shown only when its test passes (bp_<dir>_q below the threshold),
                       with the thin-sample note under bp_warn_n break points.

   Exports select(name, serve, side); fires "serveiq:shown" after every change. */

import { SIDES, DIRS, EVEN_SPLIT, NO_CLEAR_FAVORITE, esc, fInt, fPct, isNum, displayName, surname } from "./data.js";
import { $, $$ } from "./ui.js";
import { createCourt } from "./court.js";

let D = null;
let court = null;
const S = { who: "Nick Kyrgios", serve: 1, side: "ad", sit: "all", phase: "call", pick: null };

const PICKS = [["Nick Kyrgios", "ad"], ["Justine Henin", "ad"], ["Pete Sampras", "ad"], ["Naomi Osaka", "ad"]];
const LANE = { wide: "Out wide", body: "At the body", T: "Down the middle" };
const lane = (d) => LANE[d].toLowerCase();
const ORDER = { deuce: ["wide", "body", "T"], ad: ["T", "body", "wide"] };   // left to right as on court
const Z = 1.96;
const BAR_MAX = 0.8;                                                         // result bars are full at 80 in 100
const per100 = (x) => (isNum(x) ? String(Math.round(100 * x)) : "n/a");

const photo = (name) => {
  const ph = name && D.photo(name);
  return ph && ph.src ? ph.src : null;
};

// ------------------------------------------------------------ rules

/* typicalLanes(cells): the lane(s) a typical pro serves to most, by league_share */
export function typicalLanes(cells) {
  const r = DIRS.filter((d) => isNum(cells[d].league_share)).sort((a, b) => cells[b].league_share - cells[a].league_share);
  if (!r.length) return [];
  return r.length > 1 && cells[r[0]].league_share - cells[r[1]].league_share < NO_CLEAR_FAVORITE ? [r[0], r[1]] : [r[0]];
}
/* edge(cells, fav): { typ, base, same, clear, value } for one side on all points */
export function edge(cells, fav) {
  const typ = typicalLanes(cells);
  const base = typ.length ? typ.reduce((t, d) => t + cells[d].share, 0) / typ.length : null;
  const others = typ.filter((d) => d !== fav);
  const same = !!fav && typ.length === 1 && typ[0] === fav;
  const f = fav && cells[fav];
  const clear = !!f && others.length > 0 && isNum(f.share_lo) &&
    others.every((d) => isNum(cells[d].share_hi) && f.share_lo > cells[d].share_hi);
  return { typ, base, same, clear, value: f && isNum(base) ? f.share - base : null };
}
/* need(cells, test): { serves, matches } to spot the lean on all points, or null without a leader */
export function need(cells, test) {
  const sh = DIRS.map((d) => cells[d].share).filter(isNum).sort((a, b) => b - a);
  if (sh.length < 2) return null;
  const d = sh[0] - sh[1];
  if (!(d > 0)) return null;
  const n = (Z * Z * (sh[0] + sh[1] - d * d)) / (d * d);
  return { serves: n, matches: isNum(test.n) && test.n > 0 ? (n * test.matches) / test.n : null };
}
const bestOf = (cells) => DIRS.filter((d) => isNum(cells[d].share)).sort((a, b) => cells[b].share - cells[a].share)[0] || null;
const favOf = (ins, all) => (ins.favorite ? ins.favorite.dir : bestOf(all));

// ------------------------------------------------------------ view model

function view() {
  const p = D.findPlayerAny(S.who);
  const M = p ? D.model(p.gender, p.server, S.serve) : null;
  if (!M) return { player: p, ok: false };
  const sit = S.sit === "bp" ? "bp" : "all";
  const out = { player: p, ok: true, M, sides: {}, ins: {} };
  SIDES.forEach((s) => {
    out.ins[s] = D.sideInsights(M, s);
    out.sides[s] = { cells: {} };
    DIRS.forEach((d) => { out.sides[s].cells[d] = { share: M[s][sit][d].share, n: M[s][sit][d].n }; });
  });
  return out;
}

// ------------------------------------------------------------ copy

function promptText(v) {
  const serve = S.serve === 1 ? "first" : "second";
  return (S.sit === "bp" ? "Break point. " : "") + surname(v.player.server) + " serves from the " + S.side + " side. Tap where the " + serve +
    " serve goes: out wide, at the body, or down the middle.";
}

function bar(label, x, cls) {
  const w = isNum(x) ? (100 * Math.min(x / BAR_MAX, 1)).toFixed(1) : "0";
  return "<div class='rb " + cls + "'><span class='rl'>" + label + "</span><span class='rt'><b data-w='" + w + "'></b></span>" +
    "<span class='rv tnum'>" + per100(x) + "</span></div>";
}

function bpLine(v, s) {
  const b = v.ins[s].bp;
  const thin = b.thin ? " Few break points, so treat this as rough." : "";
  if (!b.shifts.length) return "No clear change on break points (" + fInt(b.n_bp) + " break points)." + thin;
  const parts = b.shifts.map((x) => lane(x.dir) + " " + (x.change > 0 ? per100(x.change) + " more" : per100(-x.change) + " fewer"));
  return "On break points: " + parts.join(", ") + " times in 100 (" + fInt(b.n_bp) + " break points)." + thin;
}

function resultHtml(v) {
  const s = S.side, who = surname(v.player.server);
  const all = v.M[s].all, cur = v.M[s][S.sit === "bp" ? "bp" : "all"];
  const pick = cur[S.pick];
  const best = bestOf(cur);
  const e = edge(all, favOf(v.ins[s], all));
  const base = e.typ.length ? e.typ.reduce((t, d) => t + cur[d].share, 0) / e.typ.length : null;
  const nTot = DIRS.reduce((t, d) => t + (cur[d].n || 0), 0);
  let h = "<p class='big'><b class='tnum'>" + per100(pick.share) + "</b> in 100</p>" +
    "<p class='lead'>" + (S.sit === "bp" ? "On break points, " : "") + esc(who) + " served " + lane(S.pick) + " " + per100(pick.share) +
    " times in 100 from the " + s + " side" + (S.sit === "bp" ? " (" + fInt(nTot) + " break points)" : "") + ".</p>";
  h += "<div class='rbars'>" + bar("Your call", pick.share, "you") +
    bar(e.typ.length > 1 ? "Typical pro's lanes" : "Typical pro's lane", base, "typ") +
    bar("Random guess", EVEN_SPLIT, "rnd") + "</div>";
  if (e.typ.length > 1) h += "<p class='note'>The typical pro splits between " + lane(e.typ[0]) + " and " + lane(e.typ[1]) + " here, so that bar averages the two.</p>";
  if (S.sit === "all" && v.ins[s].noClearFavorite) h += "<p class='line'>No clear favorite on this side: the top two lanes are close.</p>";
  else if (best && best !== S.pick) h += "<p class='line'>Best call: " + lane(best) + ", " + per100(cur[best].share) + " in 100.</p>";
  if (S.sit === "all") {
    if (e.same) h += "<p class='line'>Same lane as the typical pro: scouting adds nothing on this side.</p>";
    else if (e.clear && e.value > 0) h += "<p class='line good'>Scouting edge: " + per100(e.value) + " more in 100 than guessing like the typical pro.</p>";
    else h += "<p class='line'>Too close to call against the typical pro.</p>";
  } else {
    h += "<p class='line" + (v.ins[s].bp.shifts.length ? " chip" : "") + "'>" + bpLine(v, s) + "</p>";
  }
  h += "<div class='ractions'><button type='button' class='btn primary' data-act='sit'>" +
    (S.sit === "all" ? "Now make it a break point" : "Back to all points") + "</button>" +
    "<button type='button' class='btn' data-act='again'>Call again</button></div>";
  if (S.sit === "all") h += "<p class='gloss'>Break point: the server is one point from losing the game.</p>";
  return h;
}

function cardHtml(v) {
  const p = v.player, who = surname(p.server);
  const src = photo(p.server);
  let h = "<div class='who'>" + (src ? "<img alt='' src='" + esc(src) + "' width='56' height='56'>" : "<span class='ball' aria-hidden='true'></span>") +
    "<div><h2>" + esc(displayName(p.server)) + "</h2><p class='tnum'>" + fInt(p.matches) + " charted matches, " +
    (S.serve === 1 ? "1st" : "2nd") + " serves</p></div></div>";
  if (S.phase === "call") {
    return h + "<p class='wait'>Make your call on the court to open the scouting card for the " + S.side + " side.</p>";
  }
  const s = S.side, ins = v.ins[s], all = v.M[s].all;
  const nd = need(all, ins);
  h += "<div class='scout'><h3>Scouting card, " + s + " side</h3><dl>";
  if (ins.favorite) {
    h += "<dt>Leans</dt><dd><b>" + LANE[ins.favorite.dir] + "</b>, " + per100(ins.favorite.share) + " in 100</dd>" +
      (isNum(ins.favorite.lo) ? "<dt>Likely range</dt><dd class='tnum'>" + per100(ins.favorite.lo) + " to " + per100(ins.favorite.hi) + " in 100</dd>" : "");
  } else {
    h += "<dt>Leans</dt><dd>No clear favorite</dd>";
  }
  if (isNum(ins.favPct)) h += "<dt>How lopsided</dt><dd>More lopsided than " + fPct(ins.favPct, 0) + " of pros on this side</dd>";
  h += "<dt>Against the typical pro</dt><dd>" + (ins.mix.differs ? "A different mix of lanes, beyond chance" : "No clear difference in the mix") + "</dd>";
  h += "<dt>Break points</dt><dd>" + bpLine(v, s) + "</dd>";
  if (nd) {
    const m = nd.matches;
    h += "<dt>To spot this lean</dt><dd>About " + fInt(nd.serves) + " charted serves from this side, " +
      (!isNum(m) ? "" : m < 1 ? "less than one match" : "about " + fInt(m) + (Math.round(m) === 1 ? " match" : " matches")) + "</dd>";
  }
  h += "<dt>Charted</dt><dd class='tnum'>" + fInt(ins.n) + " serves in " + fInt(ins.matches) + " matches</dd></dl>";
  const w = ins.win;
  h += "<p class='win'>" + (w.differs && w.best_dir
    ? "On this side, " + esc(who) + " wins clearly more points serving " + lane(w.best_dir) + ". This may not hold up in new matches."
    : "Direction does not clearly change how often " + esc(who) + " wins the point.") + "</p></div>";
  return h;
}

// ------------------------------------------------------------ render

function setPressed(sel, attr, val) {
  $$(sel).forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute(attr) === String(val))));
}

function render(opts) {
  const animate = !!(opts && opts.animate);
  const v = view();
  setPressed("#picks .pick", "data-key", S.who + "|" + S.side);
  setPressed("#seg-side button", "data-side", S.side);
  setPressed("#seg-serve button", "data-serve", S.serve);
  setPressed("#seg-sit button", "data-sit", S.sit);
  const r = $("#result");
  if (!v.ok) {
    $("#prompt").textContent = "No " + (S.serve === 1 ? "1st" : "2nd") + "-serve data for this player.";
    $("#lanebtns").innerHTML = "";
    r.innerHTML = ""; r.hidden = true;
    $("#card").innerHTML = "";
    return;
  }
  $("#prompt").textContent = promptText(v);
  $("#lanebtns").innerHTML = ORDER[S.side].map((d) => "<button type='button' class='lanebtn' data-dir='" + d + "' aria-pressed='" +
    String(S.pick === d) + "'" + (S.phase === "call" ? "" : " disabled") + ">" + LANE[d] + "</button>").join("");
  court.focus(S.side);
  if (S.phase === "call") {
    court.draw(v, { animate: false });
    court.hidePaths();
    court.mark(null);
    court.pickable(S.side, true);
    r.innerHTML = ""; r.hidden = true;
  } else {
    court.pickable(S.side, false);
    court.draw(v, { animate });
    court.mark(S.side + S.pick);
    r.hidden = false;
    r.innerHTML = resultHtml(v);
    requestAnimationFrame(() => $$("#result [data-w]").forEach((b) => { b.style.width = b.getAttribute("data-w") + "%"; }));
  }
  $("#card").innerHTML = cardHtml(v);
  const img = $("#card .who img");
  if (img) img.addEventListener("error", () => { img.outerHTML = "<span class='ball' aria-hidden='true'></span>"; });
  document.dispatchEvent(new CustomEvent("serveiq:shown", { detail: { name: S.who, serve: S.serve, side: S.side, sit: S.sit, phase: S.phase } }));
}

function call(dir) {
  if (S.phase !== "call" || DIRS.indexOf(dir) < 0) return;
  S.pick = dir;
  S.phase = "shown";
  render({ animate: true });
  const first = $("#result .btn");
  if (first) first.focus({ preventScroll: true });
  if (window.matchMedia("(max-width: 960px)").matches) $("#result").scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function reset(changes) {
  Object.assign(S, changes || {}, { phase: "call", pick: null });
  render();
}

/* select(name, serve, side): a player by data-file or display name; false when there is none */
export function select(name, serve, side) {
  const p = D.findPlayerAny(name) || D.exact(name);
  if (!p) return false;
  const ch = { who: p.server };
  if (serve === 1 || serve === 2) ch.serve = serve;
  if (side === "deuce" || side === "ad") ch.side = side;
  reset(ch);
  return true;
}

// ------------------------------------------------------------ controls

function buildPicks() {
  $("#picks").innerHTML = PICKS.filter((x) => D.findPlayerAny(x[0])).map(([n, side]) => {
    const src = photo(n);
    return "<button type='button' class='pick' data-key='" + esc(n + "|" + side) + "' data-name='" + esc(n) + "' data-side='" + side + "' aria-pressed='false'>" +
      (src ? "<img alt='' src='" + esc(src) + "' width='28' height='28' loading='lazy'>" : "") +
      "<span>" + esc(surname(n)) + "</span><small>" + side + " side</small></button>";
  }).join("");
}

function wire() {
  $("#picks").addEventListener("click", (e) => {
    const b = e.target.closest(".pick");
    if (b) select(b.getAttribute("data-name"), null, b.getAttribute("data-side"));
  });
  $("#seg-side").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b && b.getAttribute("data-side") !== S.side) reset({ side: b.getAttribute("data-side") });
  });
  $("#seg-serve").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b && Number(b.getAttribute("data-serve")) !== S.serve) reset({ serve: Number(b.getAttribute("data-serve")) });
  });
  const setSit = (sit) => {
    if (sit === S.sit) return;
    S.sit = sit;
    render({ animate: false });
  };
  $("#seg-sit").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) setSit(b.getAttribute("data-sit"));
  });
  $("#lanebtns").addEventListener("click", (e) => {
    const b = e.target.closest(".lanebtn");
    if (b) call(b.getAttribute("data-dir"));
  });
  $("#result").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    if (b.getAttribute("data-act") === "sit") {
      setSit(S.sit === "all" ? "bp" : "all");
      const again = $("#result .btn.primary");
      if (again) again.focus({ preventScroll: true });
    } else {
      reset();
      const first = $("#lanebtns .lanebtn");
      if (first) first.focus({ preventScroll: true });
    }
  });

  const input = $("#find"), msg = $("#find-msg");
  $("#players").innerHTML = D.players.map((p) => "<option value='" + esc(displayName(p.server)) + "'></option>").join("");
  const commit = () => {
    const text = input.value.trim();
    msg.textContent = "";
    if (!text) return;
    const p = D.exact(text) || D.search(null, text)[0];
    if (p) { select(p.server); input.value = ""; input.blur(); return; }
    msg.textContent = "No charted player matches that name.";
  };
  input.addEventListener("input", () => { msg.textContent = ""; if (D.exact(input.value)) commit(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    else if (e.key === "Escape") { input.value = ""; msg.textContent = ""; input.blur(); }
  });
  input.addEventListener("change", commit);
}

export async function init(ctx) {
  D = ctx.data;
  court = createCourt($("#court"), {
    onHover: (k) => court.highlight(S.phase === "shown" ? k : null),
    onPick: (k) => call(k.replace(/^(deuce|ad)/, "")),
  });
  buildPicks();
  wire();
  render();
  document.addEventListener("serveiq:select", (e) => {
    const d = e.detail || {};
    if (d.name) select(d.name, d.serve, d.side);
  });
  ctx.tool = { select, edge, need, typicalLanes, favOf, get state() { return Object.assign({}, S); } };
}
