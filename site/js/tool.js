/* Serve IQ tool (#tool and the picker bar). ES module.
   Tabs pick the typical pro of either tour ("All men", "All women": every charted serve of the tour pooled)
   or one of a few well-known players; the search box finds any of
   the listed players (a searched player gets their own tab at the end). The 1st/2nd serve switch applies
   to whoever is shown. The court (js/court.js) draws the serve paths; the stats panel lists, per side and
   direction, the share of serves as a bar and the share of points the server won.

   Tags, only when the data back them:
     "Wins clearly more" (blue): a player's side whose win test passes (q_win below the threshold); it goes
       on that side's best direction.
     "Wins least" (red): the typical pro's body lane, when the tour-wide within-server comparison says body
       wins fewer points than wide (whole range below zero) and body is the lowest of the three.

   Exports select(name, serve) and showTypical(g, serve); fires "serveiq:shown" after every change. */

import { SIDES, DIRS, CI_LEVEL, esc, fInt, fPct, isNum, displayName, surname, nameKey } from "./data.js";
import { $, $$ } from "./ui.js";
import { createCourt } from "./court.js";

let D = null;
let court = null;
const S = { who: "m", serve: 1 };          // who: "m" | "w" (typical pro) or a data-file player name
let started = false;

const TABS = ["Rafael Nadal", "Novak Djokovic", "Aryna Sabalenka", "Iga Swiatek"];
const NAMES = { wide: "Wide", body: "Body", T: "T" };
const ORDER = { deuce: ["wide", "body", "T"], ad: ["T", "body", "wide"] };   // left to right as on court
const BAR_MAX = 0.7;                                                         // share bars are full at 70%
const typicalLabel = (g) => "Typical pro, " + (g === "w" ? "women" : "men");

const photo = (name) => {
  const ph = name && D.photo(name);
  return ph && ph.src ? ph.src : null;
};
const isTypical = () => S.who === "m" || S.who === "w";

// ------------------------------------------------------------ view model

function view() {
  const out = { sides: {} };
  let M = null, p = null, g = S.who;
  if (!isTypical()) {
    p = D.findPlayerAny(S.who);
    g = p.gender;
    M = D.model(p.gender, p.server, S.serve);
  }
  const typ = D.typical(g, S.serve);
  SIDES.forEach((s) => {
    const ins = M ? D.sideInsights(M, s) : null;
    const side = { clear: !!(ins && ins.win.differs), best: ins ? ins.win.best_dir : null, cells: {} };
    DIRS.forEach((d) => {
      const c = M ? M[s].all[d] : typ[s][d];
      side.cells[d] = {
        share: c ? c.share : null,
        lo: c ? c.share_lo : null,
        hi: c ? c.share_hi : null,
        win: c && !(M && D.fewServes(c)) ? c.win : null,
        win_lo: c ? c.win_lo : null,
        win_hi: c ? c.win_hi : null,
        typical: M && c ? c.league_share : null,
        n: c ? c.n : null,
      };
    });
    out.sides[s] = side;
  });
  const wb = D.within(g, S.serve, "win body minus win wide");
  out.bodyLeast = !M && !!wb && isNum(wb.hi_pp) && wb.hi_pp < 0;
  out.player = p;
  out.g = g;
  out.hasData = !isTypical() ? !!M : true;
  return out;
}

// ------------------------------------------------------------ stats panel

const BALL = "<span class='ball'><svg width='30' height='30' viewBox='0 0 24 24' aria-hidden='true'><circle cx='12' cy='12' r='10' fill='#e8f84a'/>" +
  "<path d='M4.5 5.5c3.2 3 3.2 10 0 13M19.5 5.5c-3.2 3-3.2 10 0 13' fill='none' stroke='#fff' stroke-width='1.6'/></svg></span>";

function whoHtml(v) {
  const serveTxt = S.serve === 1 ? "1st serves" : "2nd serves";
  if (!v.player) {
    return BALL + "<div><h2>" + esc(typicalLabel(v.g)) + "</h2><p>All charted " + (v.g === "w" ? "women" : "men") + ", " + serveTxt + "</p></div>";
  }
  const src = photo(v.player.server);
  return (src ? "<img alt='' src='" + esc(src) + "' width='56' height='56'>" : BALL) +
    "<div><h2>" + esc(displayName(v.player.server)) + "</h2><p class='tnum'>" + fInt(v.player.matches) + " charted matches, " + serveTxt + "</p></div>";
}

function sideHtml(v, s) {
  const sd = v.sides[s];
  const wins = DIRS.map((d) => sd.cells[d].win).filter(isNum);
  const minW = wins.length ? Math.min.apply(null, wins) : null;
  const maxW = wins.length ? Math.max.apply(null, wins) : null;
  let h = "<div class='side'><div class='side-h'><h3><i class='sw' style='background:" + (s === "deuce" ? "var(--deuce)" : "var(--ad)") + "'></i>" +
    (s === "deuce" ? "Deuce side" : "Ad side") + "</h3><small>" +
    (s === "deuce" ? "server starts right of center" : "server starts left of center") + "</small></div>" +
    "<div class='cols' aria-hidden='true'><span></span><span>Share of serves</span><span>Points won</span></div>";
  ORDER[s].forEach((d) => {
    const c = sd.cells[d];
    let cls = "row", flag = "";
    if (v.player) {
      if (sd.clear && sd.best === d) { cls += " best"; flag = "<span class='flag b'>Wins clearly more</span>"; }
    } else if (v.bodyLeast && d === "body" && isNum(c.win) && c.win === minW && minW < maxW) {
      cls += " worst"; flag = "<span class='flag r'>Wins least</span>";
    }
    const share = isNum(c.share) ? c.share : 0;
    const pos = (100 * Math.min(share / BAR_MAX, 1)).toFixed(1);
    const outside = share < 0.12;
    const winTxt = isNum(c.win) ? Math.round(100 * c.win) + "<small>%</small>" : "<small>few serves</small>";
    const aria = (s === "deuce" ? "Deuce" : "Ad") + " side, " + NAMES[d] + ": " + (isNum(c.share) ? fPct(c.share, 0) : "no data") +
      " of serves" + (isNum(c.win) ? ", server won " + fPct(c.win, 0) + " of points" : "") +
      (isNum(c.typical) ? ". Typical pro: " + fPct(c.typical, 0) + " of serves" : "") + (flag ? ". " + flag.replace(/<[^>]+>/g, "") : "");
    h += "<div class='" + cls + "' tabindex='0' data-k='" + s + d + "' aria-label='" + esc(aria) + "'>" +
      "<span class='d'>" + NAMES[d] + "</span>" +
      "<span class='bar'><span class='fill' data-w='" + pos + "'></span>" +
      (isNum(c.typical) ? "<span class='typ' style='left:" + (100 * Math.min(c.typical / BAR_MAX, 1)).toFixed(1) + "%'></span>" : "") +
      "<span class='v tnum" + (outside ? " out" : "") + "' style='left:" + (outside ? "calc(" + pos + "% + 8px)" : "8px") + "'>" +
      (isNum(c.share) ? fPct(c.share, 0) : "n/a") + "</span></span>" +
      "<span class='won tnum'>" + winTxt + "</span>" + flag + "</div>";
  });
  return h + "</div>";
}

function sureHtml(v) {
  const range = (a, lo, hi) => (isNum(a) ? fPct(a, 0) + (isNum(lo) && isNum(hi) ? " (" + fPct(lo, 0) + " to " + fPct(hi, 0) + ")" : "") : "n/a");
  let h = "<p>" + esc(fPct(CI_LEVEL, 0)) + " ranges show where the true number likely sits, given how many serves were charted. " +
    "\"Clearly\" means the gap passes a false-discovery check at " + esc(D.b("fdr")) + " across all " + fInt(D.h("players")) + " players.</p>";
  if (!v.player) {
    return h + "<p>The typical pro pools every charted serve of the tour, so its ranges are very narrow. Each player is compared with a typical pro who serves with the same hand.</p>";
  }
  h += "<ul>";
  SIDES.forEach((s) => ORDER[s].forEach((d) => {
    const c = v.sides[s].cells[d];
    h += "<li class='tnum'>" + (s === "deuce" ? "Deuce" : "Ad") + ", " + NAMES[d].toLowerCase() + ": " + range(c.share, c.lo, c.hi) +
      " of serves, won " + range(c.win, c.win_lo, c.win_hi) + "</li>";
  }));
  return h + "</ul>";
}

function renderStats(v) {
  $("#who").innerHTML = whoHtml(v);
  const host = $("#sides");
  if (!v.hasData) {
    host.innerHTML = "<p class='foot'>No " + (S.serve === 1 ? "1st" : "2nd") + "-serve data for this player.</p>";
    $("#foot").textContent = "";
    $("#sure-body").innerHTML = "";
    return;
  }
  host.innerHTML = sideHtml(v, "deuce") + sideHtml(v, "ad");
  const img = $("#who img");
  if (img) img.addEventListener("error", () => { img.outerHTML = BALL; });
  // bars grow from their old width: set it on the next frame so the CSS transition runs
  requestAnimationFrame(() => $$(".fill", host).forEach((f) => { f.style.width = f.getAttribute("data-w") + "%"; }));
  $("#foot").innerHTML = v.player
    ? "<i></i>Typical pro with the same serving hand. Points won counts the whole point."
    : "Points won counts the whole point, including rallies. Bars are full at " + fPct(BAR_MAX, 0) + " of serves.";
  $("#sure-body").innerHTML = sureHtml(v);
  $$(".row", host).forEach((r) => {
    const k = r.getAttribute("data-k");
    const on = () => hover(k), off = () => hover(null);
    r.addEventListener("mouseenter", on); r.addEventListener("mouseleave", off);
    r.addEventListener("focus", on); r.addEventListener("blur", off);
  });
}

function hover(k) {
  court.highlight(k);
  $$("#sides .row").forEach((r) => r.classList.toggle("hl", r.getAttribute("data-k") === k));
}

// ------------------------------------------------------------ tabs and controls

function tabHtml(key, label, src) {
  return "<button type='button' class='tab' data-key='" + esc(key) + "' aria-pressed='false'>" +
    (src ? "<img alt='' src='" + esc(src) + "' width='26' height='26' loading='lazy'>" : "") + "<span>" + esc(label) + "</span></button>";
}
function buildTabs() {
  const fixed = TABS.filter((n) => D.findPlayerAny(n));
  let h = tabHtml("m", "All men") + tabHtml("w", "All women") + "<span class='divider' aria-hidden='true'></span>" +
    fixed.map((n) => tabHtml(n, surname(n), photo(n))).join("");
  if (!isTypical() && fixed.indexOf(S.who) < 0) h += tabHtml(S.who, surname(S.who), photo(S.who));
  $("#tabs").innerHTML = h;
}

function render() {
  if (!isTypical() && !$("#tabs .tab[data-key='" + CSS.escape(S.who) + "']")) buildTabs();
  $$("#tabs .tab").forEach((b) => {
    const on = b.getAttribute("data-key") === S.who;
    b.setAttribute("aria-pressed", String(on));
    if (on && started) b.scrollIntoView({ block: "nearest", inline: "nearest" });
  });
  $$("#seg button").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.getAttribute("data-serve")) === S.serve)));
  const v = view();
  renderStats(v);
  court.draw(v, { animate: true });
  started = true;
  document.dispatchEvent(new CustomEvent("serveiq:shown", { detail: { name: isTypical() ? null : S.who, serve: S.serve } }));
}

/* select(name, serve): a player by data-file or display name; false when there is none */
export function select(name, serve) {
  const p = D.findPlayerAny(name) || D.exact(name);
  if (!p) return false;
  S.who = p.server;
  if (serve === 1 || serve === 2) S.serve = serve;
  render();
  return true;
}
export function showTypical(g, serve) {
  S.who = g === "w" ? "w" : "m";
  if (serve === 1 || serve === 2) S.serve = serve;
  render();
}

function wire() {
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tab");
    if (!b) return;
    const k = b.getAttribute("data-key");
    if (k === S.who) return;
    if (k === "m" || k === "w") showTypical(k); else select(k);
  });
  $$("#seg button").forEach((b) => b.addEventListener("click", () => {
    const sv = Number(b.getAttribute("data-serve"));
    if (sv !== S.serve) { S.serve = sv; render(); }
  }));

  const input = $("#find"), msg = $("#find-msg");
  $("#players").innerHTML = D.players.map((p) => "<option value='" + esc(displayName(p.server)) + "'></option>").join("");
  const commit = () => {
    const text = input.value.trim();
    msg.textContent = "";
    if (!text) return;
    const k = nameKey(text);
    if (k.indexOf("typical") === 0) { showTypical(/wom[ae]n/.test(k) ? "w" : "m"); input.value = ""; return; }
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
  court = createCourt($("#court"), { onHover: hover });
  buildTabs();
  wire();
  render();
  document.addEventListener("serveiq:select", (e) => {
    const d = e.detail || {};
    if (d.name) select(d.name, d.serve);
  });
  ctx.tool = { select, showTypical, get state() { return Object.assign({}, S); } };
}
