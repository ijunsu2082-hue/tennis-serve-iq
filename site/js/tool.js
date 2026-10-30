/* Serve IQ tool (#tool). ES module.
   Player search (native input + datalist), five quick picks, the 1st / 2nd serve switch, the player line,
   the court (court.js), up to three plain sentences and the "How sure are we?" tables. Every number comes
   from serveiq.json through D.model and D.sideInsights.

   Other modules load a player with ctx.tool.select(name, serve) or by dispatching
   document event "serveiq:select" with detail { name, serve }. */

import { DIRS, SIDES, CI_LEVEL, NO_CLEAR_FAVORITE, fPct, fInt, fP, fSigned, esc, isNum, surname, displayName } from "./data.js";
import { $, $$, debounce } from "./ui.js";
import { createCourt } from "./court.js";

const DEFAULT_PLAYER = "Rafael Nadal";
const QUICK = ["Rafael Nadal", "Novak Djokovic", "Roger Federer", "Aryna Sabalenka", "Iga Swiatek"];

const ORD = { 1: "first", 2: "second" };
const BETWEEN = { wide: "wide", body: "the body", T: "the T" };
const AFTER = { wide: "serving wide", body: "serving at the body", T: "serving down the T" };
const GOES = { wide: "goes wide", body: "aims at the body", T: "goes down the T" };
const SERVES_WAY = { wide: "serves wide", body: "serves at the body", T: "serves down the T" };
const TIP_HEAD = { wide: "Out wide", body: "At the body", T: "Down the T" };
const SIDE_WORD = { deuce: "deuce", ad: "ad" };
const TITLE = { deuce: "Deuce side", ad: "Ad side" };

let D = null;
let court = null;
const S = { g: null, name: null, serve: 1 };
let started = false;

const P = (x) => fPct(x, 0);
const B = (s) => "<b>" + esc(s) + "</b>";

// ------------------------------------------------------------ sentences

/* Habit sentences. Each direction gets a plain-words gloss the first time it appears, so a reader who does not
   play knows what "the T" means. The typical-pro figure is the cell's league_share. The comparison uses the
   whole percents the reader sees: the same number is "the same", 1 to CLOSE_PTS points apart is "about the
   same", anything further gets its own number. When both sides match a typical pro, one sentence covers both.
   Otherwise the side where the player differs most from a typical pro comes first. */
const CLOSE_PTS = 3;
const HIT_GLOSS = { wide: "out wide (toward the sideline)", body: "at the body (between wide and the T)", T: "down the T (toward the middle line)" };
const HIT_PLAIN = { wide: "out wide", body: "at the body", T: "down the T" };
const GLOSS = { wide: "toward the sideline", body: "between wide and the T", T: "toward the middle line" };

function hitWords(dir, used) {
  if (used.has(dir)) return HIT_PLAIN[dir];
  used.add(dir);
  return HIT_GLOSS[dir];
}
/* "wide (47%, toward the sideline)" the first time, "wide (47%)" after that */
function betweenWords(dir, share, used) {
  const pct = B(P(share));
  if (used.has(dir)) return BETWEEN[dir] + " (" + pct + ")";
  used.add(dir);
  return BETWEEN[dir] + " (" + pct + ", " + GLOSS[dir] + ")";
}
/* how the player's share compares with a typical pro, in the rounded numbers on screen: "same", "close" or "far" */
function closeness(share, league) {
  if (!isNum(share) || !isNum(league)) return "none";
  const d = Math.abs(Math.round(100 * share) - Math.round(100 * league));
  if (d === 0) return "same";
  return d <= CLOSE_PTS ? "close" : "far";
}
const near = (c) => c === "same" || c === "close";
function deviation(I) {
  if (!I.favorite || !isNum(I.favorite.league)) return -1;
  return Math.abs(I.favorite.share - I.favorite.league);
}

function favoriteSentence(I, sur, sw, used, first, prevSame) {
  const f = I.favorite;
  const side = SIDE_WORD[I.side];
  const lead = first
    ? sur + " hits " + B(P(f.share)) + " of " + side + "-side " + sw + " serves " + hitWords(f.dir, used)
    : "On the " + side + " side, " + sur + " hits " + B(P(f.share)) + " " + hitWords(f.dir, used);
  const c = closeness(f.share, f.league);
  if (c === "same") return lead + ", " + (prevSame ? "also " : "") + "the same as a typical pro.";
  if (c === "close") return lead + ", about the same as a typical pro (" + B(P(f.league)) + ").";
  if (c === "far") return lead + ". A typical pro " + SERVES_WAY[f.dir] + " " + B(P(f.league)) + " of the time.";
  return lead + ".";
}

function sideHabit(I, sur, sw, used, first, prevSame) {
  const side = SIDE_WORD[I.side];
  if (I.favorite) return favoriteSentence(I, sur, sw, used, first, prevSame);
  if (I.noClearFavorite) {
    const r = I.noClearFavorite;
    if (r.length === 3 && r[0].share - r[2].share < NO_CLEAR_FAVORITE) {
      const all = DIRS.map((d) => r.find((x) => x.dir === d));
      const even = all.every((x) => P(x.share) === P(all[0].share));
      return sur + " splits " + side + "-side " + sw + " serves " + (even ? "evenly" : "almost evenly") + " across all three directions (" +
        all.map((x) => BETWEEN[x.dir].replace(/^the /, "") + " " + B(P(x.share))).join(", ") + ").";
    }
    const a = r[0], b = r[1];
    return sur + " splits " + side + "-side " + sw + " serves almost evenly between " + betweenWords(a.dir, a.share, used) +
      " and " + betweenWords(b.dir, b.share, used) + ".";
  }
  return "There are too few " + side + "-side " + sw + " serves from " + sur + " to say which way they go.";
}

function habitSentence(ins, sur, sw) {
  const used = new Set();
  const [d, a] = ins;
  const cd = d.favorite ? closeness(d.favorite.share, d.favorite.league) : "none";
  const ca = a.favorite ? closeness(a.favorite.share, a.favorite.league) : "none";
  const ld = d.favorite ? P(d.favorite.league) : "", la = a.favorite ? P(a.favorite.league) : "";
  const bothSides = ld === la ? B(ld) + " on both sides" : B(ld) + " on the deuce side and " + B(la) + " on the ad side";
  const timeSides = ld === la ? B(ld) + " of the time on both sides" : B(ld) + " of the time on the deuce side and " + B(la) + " on the ad side";
  if (d.favorite && a.favorite && d.favorite.dir === a.favorite.dir) {
    const lead = sur + " hits " + B(P(d.favorite.share)) + " of deuce-side and " + B(P(a.favorite.share)) + " of ad-side " + sw +
      " serves " + hitWords(d.favorite.dir, used);
    if (cd === "same" && ca === "same") return lead + ". Both numbers match a typical pro.";
    if (near(cd) && near(ca)) return lead + ", about the same as a typical pro (" + bothSides + ").";
    return lead + ". A typical pro " + SERVES_WAY[d.favorite.dir] + " " + timeSides + ".";
  }
  // a different favorite on each side, both matching a typical pro: one sentence, not two that repeat each other
  if (d.favorite && a.favorite && near(cd) && near(ca)) {
    const lead = sur + " hits " + B(P(d.favorite.share)) + " of deuce-side " + sw + " serves " + hitWords(d.favorite.dir, used) +
      " and " + B(P(a.favorite.share)) + " of ad-side ones " + hitWords(a.favorite.dir, used);
    if (cd === "same" && ca === "same") return lead + ". Both numbers match a typical pro.";
    return lead + ". A typical pro is about the same (" + B(ld) + " and " + B(la) + ").";
  }
  // the side that differs most from a typical pro leads; ties keep deuce first
  const order = ins.slice().sort((x, y) => deviation(y) - deviation(x));
  let prevSame = false;
  return order.map((I, i) => {
    const out = sideHabit(I, sur, sw, used, i === 0, prevSame);
    prevSame = !!(I.favorite && closeness(I.favorite.share, I.favorite.league) === "same");
    return out;
  }).join(" ");
}

/* the best direction (best_dir) against the lowest-win direction with enough serves, raw win values */
function winPair(I) {
  const rates = I.win.rates.filter((r) => isNum(r.win));
  if (rates.length < 2) return null;
  let best = rates.find((r) => r.dir === I.win.best_dir);
  let low = best ? rates.filter((r) => r !== best).sort((x, y) => x.win - y.win)[0] : null;
  if (!best || !low || best.win <= low.win) {
    const s = rates.slice().sort((x, y) => y.win - x.win);
    best = s[0];
    low = s[s.length - 1];
  }
  return { best, low };
}
/* One sentence per side whose win test passes. On 1st serves the player is then one of the blue dots in
   #findings (headline.costly_players_s1), so the sentence says so. */
function winSentence(ins, sur, serve) {
  const parts = ins.filter((I) => I.win.differs).map((I) => {
    const w = winPair(I);
    if (!w) return null;
    return "On the " + SIDE_WORD[I.side] + " side, " + sur + " wins " + B(P(w.best.win)) + " of points after " + AFTER[w.best.dir] +
      " but only " + B(P(w.low.win)) + " after " + AFTER[w.low.dir] + ".";
  }).filter(Boolean);
  if (!parts.length) return "No direction wins clearly more points for " + sur + ".";
  const n = D.h("costly_players_s1");
  const tail = serve === 1 && isNum(n)
    ? sur + " is one of only " + B(fInt(n)) + " players with a gap this clear."
    : "That is a clear difference.";
  return parts.join(" ") + " " + tail;
}

function bpSentence(ins, sur) {
  const all = [];
  ins.forEach((I) => {
    if (I.bp.thin) return;
    I.bp.shifts.forEach((s) => { if (isNum(s.bp) && isNum(s.other)) all.push(Object.assign({ side: I.side }, s)); });
  });
  if (!all.length) return null;
  all.sort((x, y) => Math.abs(y.bp - y.other) - Math.abs(x.bp - x.other));
  const s = all[0];
  return "On " + SIDE_WORD[s.side] + "-side break points, " + sur + " " + GOES[s.dir] + " " + B(P(s.bp)) + " of the time, " +
    (s.bp > s.other ? "up" : "down") + " from " + B(P(s.other)) + " on other points.";
}

export function sentences(M, name, serve) {
  const sur = surname(name);
  const sw = ORD[serve];
  const ins = SIDES.map((s) => D.sideInsights(M, s));
  const out = [habitSentence(ins, sur, sw), winSentence(ins, sur, serve)];
  const bp = bpSentence(ins, sur);
  if (bp) out.push(bp);
  return out;
}

// ------------------------------------------------------------ court view

function tipText(c, side, dir, sur, sw) {
  const head = TIP_HEAD[dir];
  const typical = " A typical pro with the same serving hand goes this way " + P(c.league_share) + " of the time.";
  if (!(c.n > 0)) return head + ", no " + side + "-side " + sw + " serves from " + sur + " went this way." + typical;
  const count = D.fewServes(c) ? "(only " + fInt(c.n) + " serves)" : "(" + fInt(c.n) + " serves)";
  return head + ", " + P(c.share) + " of " + side + "-side " + sw + " serves " + count + "." + typical +
    (isNum(c.win) ? " " + sur + " won " + P(c.win) + " of these points." : "");
}

function courtView(M, name, serve) {
  const sur = surname(name);
  const sw = ORD[serve];
  const sides = {};
  SIDES.forEach((side) => {
    const cells = {};
    DIRS.forEach((dir) => {
      const c = M[side].all[dir];
      cells[dir] = {
        share: c.share, n: c.n,
        shareText: P(c.share), leagueText: P(c.league_share), winText: isNum(c.win) ? P(c.win) : "no serves", nText: fInt(c.n),
        tip: tipText(c, SIDE_WORD[side], dir, sur, sw),
      };
    });
    // the arrows and the hidden table carry the numbers, so the panel label only names it
    sides[side] = { label: TITLE[side] + ", " + sw + " serves from " + sur + ".", cells };
  });
  return { caption: displayName(name) + ", " + sw + " serves", sides };
}

// ------------------------------------------------------------ "How sure are we?" tables

function sureTables(M, name, serve) {
  const sw = ORD[serve];
  const qth = D.thresholds.q;
  const ci = fPct(CI_LEVEL, 0);
  const range = (lo, hi) => (isNum(lo) && isNum(hi) ? P(lo) + " to " + P(hi) : "");
  const t1 = '<table class="sure-table"><caption>' + esc(displayName(name) + ", " + sw + " serves") + "</caption>" +
    '<thead><tr><th scope="col">Direction</th><th scope="col">Share <small>' + esc(ci) + ' range</small></th><th scope="col">Typical pro</th>' +
    '<th scope="col">Points won <small>' + esc(ci) + ' range</small></th><th scope="col">Serves</th></tr></thead>' +
    SIDES.map((side) => {
      return '<tbody><tr class="sure-side"><th scope="rowgroup" colspan="5">' + esc(TITLE[side]) + "</th></tr>" +
        DIRS.map((d) => {
          const c = M[side].all[d];
          return '<tr><th scope="row">' + esc(d) + "</th><td>" + esc(P(c.share)) + "<small>" + esc(range(c.share_lo, c.share_hi)) + "</small></td><td>" +
            esc(P(c.league_share)) + "</td><td>" + esc(isNum(c.win) ? P(c.win) : "none") + "<small>" + esc(range(c.win_lo, c.win_hi)) + "</small></td><td>" +
            esc(fInt(c.n)) + (D.fewServes(c) ? "<small>few</small>" : "") + "</td></tr>";
        }).join("") + "</tbody>";
    }).join("") + "</table>";
  const qs = SIDES.filter((side) => isNum(M[side].test.q_win)).map((side) => fP(M[side].test.q_win) + " on the " + SIDE_WORD[side] + " side");
  const qLine = qs.length ? '<p class="sure-q">q (lower is stronger) for points won by direction is ' + esc(qs.join(" and ")) + ".</p>" : "";
  const t2 = '<table class="sure-table"><caption>Break points against other points</caption>' +
    '<thead><tr><th scope="col">Direction</th><th scope="col">Other points</th><th scope="col">Break points</th>' +
    '<th scope="col">Change <small>serves per 100</small></th><th scope="col">Clear</th><th scope="col">q <small>lower is stronger</small></th></tr></thead>' +
    SIDES.map((side) => {
      const t = M[side].test;
      const thin = (t.n_bp || 0) < D.thresholds.bp_warn_n;
      return '<tbody><tr class="sure-side"><th scope="rowgroup" colspan="6">' + esc(TITLE[side]) + " <span>" +
        esc(fInt(t.n_bp || 0) + " break-point serves" + (thin ? ", too few to trust small changes" : "")) + "</span></th></tr>" +
        DIRS.map((d) => {
          const q = t["bp_" + d + "_q"];
          const ob = M[side].other[d].share, bb = M[side].bp[d].share;
          // change in the rounded shares the row shows, so the columns add up for a reader who checks
          const ch = isNum(ob) && isNum(bb) ? Math.round(100 * bb) - Math.round(100 * ob) : null;
          return '<tr><th scope="row">' + esc(d) + "</th><td>" + esc(P(M[side].other[d].share)) + "</td><td>" + esc(P(M[side].bp[d].share)) + "</td><td>" +
            esc(isNum(ch) ? fSigned(ch, 0) : "n/a") + "</td><td>" + (isNum(q) && q < qth ? "yes" : "no") + "</td><td>" + esc(isNum(q) ? fP(q) : "n/a") + "</td></tr>";
        }).join("") + "</tbody>";
    }).join("") + "</table>";
  return '<div class="sure-scroll" data-label="Share and points won by direction">' + t1 + "</div>" + qLine +
    '<div class="sure-scroll" data-label="Break points against other points">' + t2 + "</div>";
}

/* a table wider than its box scrolls sideways; only then is the box a keyboard stop (a named region) */
function markScroll() {
  $$("#sure .sure-scroll").forEach((box) => {
    if (box.clientWidth && box.scrollWidth > box.clientWidth + 1) {
      box.setAttribute("tabindex", "0");
      box.setAttribute("role", "region");
      box.setAttribute("aria-label", box.getAttribute("data-label") + ", scrolls sideways");
    } else {
      box.removeAttribute("tabindex");
      box.removeAttribute("role");
      box.removeAttribute("aria-label");
    }
  });
}

// ------------------------------------------------------------ render

function render() {
  const p = D.findPlayer(S.g, S.name);
  const M = D.model(S.g, S.name, S.serve);
  if (!p || !M) return;
  const shown = displayName(p.server);
  // player line
  $("#player-name").textContent = shown;
  $("#player-meta").textContent = fInt(p.matches) + " matches charted, " + fInt(p.serves) + " serves";
  const img = $("#player-photo");
  const ph = D.photo(p.server);
  if (ph && ph.src) {
    if (img.getAttribute("src") !== ph.src) img.setAttribute("src", ph.src);
    img.hidden = false;
  } else {
    img.removeAttribute("src");
    img.hidden = true;
  }
  // input, picks, switch
  const input = $("#player-input");
  if (document.activeElement !== input) input.value = shown;
  say("");
  $$("#quick-picks a").forEach((a) => {
    if (a.getAttribute("data-name") === p.server) a.setAttribute("aria-current", "true");
    else a.removeAttribute("aria-current");
  });
  $$("#serve-switch button").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.getAttribute("data-serve")) === S.serve)));
  // court, sentences, tables
  court.draw(courtView(M, p.server, S.serve), { animate: started });
  $("#sentences").innerHTML = sentences(M, p.server, S.serve).map((s) => "<p>" + s + "</p>").join("");
  $("#sure-tables").innerHTML = sureTables(M, p.server, S.serve);
  markScroll();
  started = true;
  // js/findings.js rings this player's dot in the waffle
  document.dispatchEvent(new CustomEvent("serveiq:shown", { detail: { name: p.server, serve: S.serve } }));
}

export function select(name, serve) {
  const p = D.findPlayerAny(name) || D.exact(name);   // data-file name, or a display spelling
  if (!p) return false;
  const sv = serve === 1 || serve === 2 ? serve : S.serve;
  if (p.server === S.name && sv === S.serve) return true;
  S.g = p.gender;
  S.name = p.server;
  S.serve = sv;
  render();
  return true;
}

// ------------------------------------------------------------ controls

/* the muted line under the search box: "No charted player matches ..." (empty hides it) */
function say(text) {
  const m = document.getElementById("search-msg");
  if (m) m.textContent = text;
}

function wireSearch() {
  const input = $("#player-input");
  const list = $("#players");
  list.innerHTML = D.players.map((p) => '<option value="' + esc(displayName(p.server)) + '"></option>').join("");
  input.placeholder = "Type a player";
  const msg = document.createElement("p");
  msg.id = "search-msg";
  msg.className = "t-msg";
  msg.setAttribute("aria-live", "polite");
  input.closest(".t-search").insertAdjacentElement("afterend", msg);   // its own grid row (site.css "msg")
  input.setAttribute("aria-describedby", "search-msg");
  const restore = () => { input.value = S.name ? displayName(S.name) : ""; say(""); };
  // a full name (data or display spelling) loads at once; otherwise the best partial match, or a message
  // that keeps the typed text so the reader can fix it
  const commit = () => {
    const text = input.value.trim();
    if (!text) { restore(); return null; }
    const p = D.exact(text) || D.search(null, text)[0] || null;
    if (p) { select(p.server); input.value = displayName(p.server); say(""); }
    else say('No charted player matches "' + text + '".');
    return p;
  };
  // select the whole name on focus so typing replaces it (the timeout beats the mouseup that clears it on iOS)
  input.addEventListener("focus", () => { input.select(); setTimeout(() => { if (document.activeElement === input) input.select(); }, 0); });
  input.addEventListener("input", () => {
    const p = D.exact(input.value);
    if (p) { select(p.server); say(""); }
  });
  input.addEventListener("change", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      if (commit()) input.blur();
    } else if (e.key === "Escape") {
      restore();
      input.blur();
    }
  });
  // leaving the box: a loaded name is shown in full; text that matched nothing stays, with its message
  input.addEventListener("blur", () => {
    const m = document.getElementById("search-msg");
    if (D.exact(input.value)) input.value = displayName(D.exact(input.value).server);
    else if (!(m && m.textContent)) restore();
  });
}

function wirePicks() {
  const host = $("#quick-picks");
  const names = QUICK.filter((n) => D.findPlayerAny(n));
  if (!names.length) { host.hidden = true; return; }
  const link = (n) => '<a href="#tool" data-name="' + esc(n) + '">' + esc(surname(n)) + "</a>";   // surname() shows the display spelling
  const parts = names.map(link);
  host.innerHTML = "Try " + (parts.length > 1 ? parts.slice(0, -1).join(", ") + " or " + parts[parts.length - 1] : parts[0]) + ".";
  host.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-name]");
    if (!a) return;
    e.preventDefault();
    select(a.getAttribute("data-name"));
  });
}

function wireSwitch() {
  $$("#serve-switch button").forEach((b) => b.addEventListener("click", () => {
    const sv = Number(b.getAttribute("data-serve"));
    if (sv !== S.serve) select(S.name, sv);
  }));
}

export async function init(ctx) {
  D = ctx.data;
  court = createCourt($("#court"));
  wireSearch();
  wirePicks();
  wireSwitch();
  $("#sure-defs").innerHTML =
    "<p>A typical pro is the average of all charted pros who serve with the same hand, against the same mix of righty and lefty returners.</p>" +
    "<p>A result is clear when it still holds after allowing for lucky results across all players (false discovery rate " + esc(D.b("fdr")) + ").</p>" +
    "<p>For first serves, points won counts the whole point, even when the first serve missed and a second serve was needed.</p>" +
    "<p>Shares are rounded, so the three shares on one side can add up to one point more or less than the whole.</p>";
  $("#sure").addEventListener("toggle", markScroll);
  window.addEventListener("resize", debounce(markScroll, 150));
  const $photo = $("#player-photo");
  $photo.addEventListener("error", () => { $photo.hidden = true; });
  const first = D.findPlayerAny(DEFAULT_PLAYER) || D.players[0];
  select(first.server, 1);
  document.addEventListener("serveiq:select", (e) => {
    const d = e.detail || {};
    if (d.name) select(d.name, d.serve);
  });
  ctx.tool = { select, get state() { return Object.assign({}, S); } };
}
