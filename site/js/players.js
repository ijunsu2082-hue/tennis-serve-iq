/* Serve IQ players dashboard (#players). ES module.
   Featured photo chips, a searchable combobox over every charted player, a player card, view controls
   (1st or 2nd serve, all points or break points, compare with the league or another player), the court
   heatmap (court.js), scouting badges (the v2 note rules, from D.sideInsights), a win-by-direction mini
   chart and a break-point shift mini chart. State lives in the URL hash (#p=tour|player|serve|sit|compare),
   written with history.replaceState after the first change only. Every number comes from serveiq.json. */

import {
  DIRS, SIDES, SIDE_LABEL, TOUR_LABEL, TOUR_PLURAL, SERVE_LABEL, CI_LEVEL, NO_CLEAR_FAVORITE,
  fPct, fInt, fNum, pEq, ciText, esc, isNum, initials, shortName, norm, surname,
} from "./data.js";
import {
  $, $$, debounce, icon, segmented, tween, avatarHTML, initPopovers, closePopover, infoButton, setInfo,
  onThemeChange, el, txt, newSvg, domainOf, diamond, keyDot, keyDiamond, setKey, rampSvg, reducedMotion, shareColor,
} from "./ui.js";
import { createCourt } from "./court.js";

let D = null;
let ANIM = null;
const S = { tour: "m", player: null, serve: 1, sit: "all", cmp: null, zone: null };
let M = null;      // model of the selected player
let CM = null;     // model of the comparison player (or null)
let court = null;
let ctl = {};      // segmented controls
let started = false;
let lastW = 0;

const DIR_WORD = { wide: "wide", body: "body", T: "T" };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// ------------------------------------------------------------ small inline icons (aria-hidden SVG)

const BADGE_ICON = {
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  focus: '<path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  mix: '<path d="M5 19v-7M12 19V5M19 19v-10"/>',
  win: '<path d="M8 4h8v4.5a4 4 0 0 1-8 0zM8 6H5a3 3 0 0 0 3.4 3.6M16 6h3a3 3 0 0 1-3.4 3.6M12 12.5V16M8.5 20h7M10 16h4v4h-4z"/>',
  bolt: '<path d="M13 3L6 13.5h5L10 21l7-10.5h-5z"/>',
  dash: '<path d="M7 12h10"/>',
};
function bicon(name) {
  return '<svg class="icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + BADGE_ICON[name] + "</svg>";
}

// ------------------------------------------------------------ helpers

function serveWord() { return SERVE_LABEL[S.serve]; }
function bpMode() { return S.sit === "bp"; }
function player() { return D.findPlayer(S.tour, S.player); }
function lastName(name) { return surname(name); }

/* remove the popover elements that belonged to "?" buttons inside host before re-rendering it */
function dropPops(host) {
  $$("button.info[aria-controls]", host).forEach((b) => {
    if (b.getAttribute("aria-expanded") === "true") closePopover(false);
    const p = document.getElementById(b.getAttribute("aria-controls"));
    if (p) p.remove();
  });
}

// ------------------------------------------------------------ URL hash (v2 format plus a 5th compare field)

function parseHash() {
  const raw = location.hash.replace(/^#/, "");
  const kv = {};
  const order = [];
  if (raw.indexOf("=") < 0) return { kv, order };
  raw.split("&").forEach((part) => {
    const i = part.indexOf("=");
    if (i > 0) { const k = part.slice(0, i); kv[k] = part.slice(i + 1); order.push(k); }
  });
  return { kv, order };
}
function readHash() {
  const { kv } = parseHash();
  if (!kv.p) return null;
  const dec = (s) => { try { return decodeURIComponent(s); } catch (e) { return s; } };
  const a = kv.p.split("|").map(dec);
  if (a[0] === "m" || a[0] === "w") S.tour = a[0];
  S.player = a[1] && D.findPlayer(S.tour, a[1]) ? a[1] : D.playerList(S.tour)[0].server;
  if (a[2] === "1" || a[2] === "2") S.serve = Number(a[2]);
  if (a[3] === "all" || a[3] === "bp") S.sit = a[3];
  S.cmp = a[4] && a[4] !== S.player && D.findPlayer(S.tour, a[4]) ? a[4] : null;
  return kv.at || "players";
}
function writeHash() {
  const { kv, order } = parseHash();
  kv.p = [S.tour, S.player, S.serve, S.sit, S.cmp || ""].map((x) => encodeURIComponent(String(x))).join("|");
  kv.at = "players";
  const keys = ["p"].concat(order.filter((k) => k !== "p" && k !== "at"), ["at"]);
  const h = "#" + keys.map((k) => k + "=" + kv[k]).join("&");
  try { history.replaceState(null, "", h); } catch (e) { /* file:// or sandboxed */ }
}

// ------------------------------------------------------------ combobox (ARIA 1.2 pattern, list autocomplete)

/* combobox({ input, list, live, items(query), pick(player), keepValue, label })
   input[role=combobox] + ul[role=listbox]. Arrow keys move the active option, Enter picks it,
   Escape closes (a second Escape clears the text), Tab closes. Options show a photo thumbnail. */
let cbxSeq = 0;
function combobox(cfg) {
  const { input, list, live } = cfg;
  const wrap = input.closest(".cbx");
  const prefix = "cbx" + (++cbxSeq) + "-opt-";
  let items = [];
  let active = -1;
  let isOpen = false;
  let shown = ""; // the text the input shows when it is not being edited (keepValue mode)

  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("autocomplete", "off");
  list.setAttribute("role", "listbox");

  const announce = debounce((n) => {
    if (live) live.textContent = n === 0 ? "No players match." : n === 1 ? "One player matches." : fInt(n) + " players match.";
  }, 350);

  function render(q) {
    items = cfg.items(q || "");
    if (!items.length) {
      list.innerHTML = '<li class="cbx-empty" role="presentation">No player matches that name.</li>';
    } else {
      list.innerHTML = items.map((p, i) =>
        '<li role="option" id="' + prefix + i + '" data-i="' + i + '" aria-selected="false">' +
        avatarHTML(p.server, D.photo(p.server), { size: 32 }) +
        '<span class="cbx-txt"><span class="cbx-name">' + esc(p.server) + "</span>" +
        '<span class="cbx-meta">' + esc((cfg.showTour && p.gender !== S.tour ? TOUR_LABEL[p.gender] + ", " : "") + fInt(p.matches) + " matches") + "</span></span></li>").join("");
    }
  }
  function setActive(i, scroll) {
    const opts = $$('[role="option"]', list);
    if (active >= 0 && opts[active]) { opts[active].classList.remove("is-active"); opts[active].setAttribute("aria-selected", "false"); }
    active = opts.length ? Math.max(-1, Math.min(opts.length - 1, i)) : -1;
    if (active >= 0) {
      const o = opts[active];
      o.classList.add("is-active");
      o.setAttribute("aria-selected", "true");
      input.setAttribute("aria-activedescendant", o.id);
      if (scroll) {
        const top = o.offsetTop, bottom = top + o.offsetHeight;
        if (top < list.scrollTop) list.scrollTop = top - 6;
        else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight + 6;
      }
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }
  function open(q, startAt) {
    render(q);
    list.hidden = false;
    isOpen = true;
    input.setAttribute("aria-expanded", "true");
    if (wrap) wrap.classList.add("is-open");
    list.scrollTop = 0;
    active = -1;
    setActive(startAt == null ? -1 : startAt, true);
  }
  function close() {
    if (!isOpen) return;
    isOpen = false;
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    if (wrap) wrap.classList.remove("is-open");
    active = -1;
  }
  function choose(i) {
    const p = items[i];
    if (!p) return;
    close();
    if (cfg.keepValue) { shown = p.server; input.value = shown; } else input.value = "";
    cfg.pick(p);
  }
  function query() { return cfg.keepValue && input.value === shown ? "" : input.value; }

  input.addEventListener("input", () => {
    const q = input.value;
    open(q, q.trim() ? 0 : -1);
    announce(items.length);
  });
  input.addEventListener("click", () => { if (!isOpen) open(query(), -1); });
  input.addEventListener("focus", () => { if (cfg.keepValue) input.select(); });
  input.addEventListener("keydown", (e) => {
    const n = items.length;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!isOpen) open(query(), e.altKey ? -1 : 0);
      else setActive(active < n - 1 ? active + 1 : 0, true);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!isOpen) open(query(), n ? Math.max(0, cfg.items(query()).length - 1) : -1);
      else setActive(active > 0 ? active - 1 : n - 1, true);
    } else if (e.key === "PageDown" && isOpen) {
      e.preventDefault(); setActive(Math.min(n - 1, active + 8), true);
    } else if (e.key === "PageUp" && isOpen) {
      e.preventDefault(); setActive(Math.max(0, active - 8), true);
    } else if (e.key === "Enter") {
      if (isOpen && active >= 0) { e.preventDefault(); choose(active); }
      else if (isOpen && n === 1) { e.preventDefault(); choose(0); }
    } else if (e.key === "Escape") {
      if (isOpen) { e.preventDefault(); e.stopPropagation(); close(); if (cfg.keepValue) input.value = shown; }
      else if (input.value && input.value !== shown) { e.preventDefault(); input.value = cfg.keepValue ? shown : ""; }
    } else if (e.key === "Tab") {
      close();
      if (cfg.keepValue) input.value = shown;
    }
  });
  input.addEventListener("blur", () => {
    setTimeout(() => {
      if (document.activeElement !== input) { close(); if (cfg.keepValue) input.value = shown; }
    }, 120);
  });
  // keep focus in the input while the pointer works the list
  list.addEventListener("mousedown", (e) => e.preventDefault());
  list.addEventListener("click", (e) => {
    const o = e.target.closest('[role="option"]');
    if (o) choose(Number(o.getAttribute("data-i")));
  });
  list.addEventListener("mousemove", (e) => {
    const o = e.target.closest('[role="option"]');
    if (o && Number(o.getAttribute("data-i")) !== active) setActive(Number(o.getAttribute("data-i")), false);
  });
  document.addEventListener("pointerdown", (e) => {
    if (isOpen && wrap && !wrap.contains(e.target)) { close(); if (cfg.keepValue) input.value = shown; }
  });

  return {
    close,
    setShown(text) { shown = text || ""; input.value = shown; },
  };
}

function mainItems(q) {
  const f = norm(q);
  if (!f) return D.playerList(S.tour);
  const hits = D.search(null, q);
  return hits.filter((p) => p.gender === S.tour).concat(hits.filter((p) => p.gender !== S.tour));
}
function cmpItems(q) {
  return D.search(S.tour, q).filter((p) => p.server !== S.player);
}

// ------------------------------------------------------------ featured chips

function renderChips() {
  const row = $("#pl-chips");
  const list = D.featured(S.tour);
  row.innerHTML = list.map((p) =>
    '<li><button type="button" class="chip pl-chip" data-name="' + esc(p.server) + '" aria-pressed="false">' +
    avatarHTML(p.server, D.photo(p.server), { size: 36 }) + "<span>" + esc(shortName(p.server)) + "</span></button></li>").join("");
  $$("button.pl-chip", row).forEach((b) => b.addEventListener("click", () => selectPlayer(S.tour, b.getAttribute("data-name"))));
  markChips(false);
  updateChipArrows();
}
function markChips(scroll) {
  const row = $("#pl-chips");
  let on = null;
  const chips = $$("button.pl-chip", row);
  chips.forEach((b) => {
    const is = b.getAttribute("data-name") === S.player;
    b.setAttribute("aria-pressed", is ? "true" : "false");
    if (is) on = b;
  });
  // one tab stop for the whole row (roving tabindex): the pressed chip, or the first one
  chips.forEach((b) => b.setAttribute("tabindex", b === (on || chips[0]) ? "0" : "-1"));
  if (scroll && on) {
    const li = on.parentElement;
    const left = li.offsetLeft - row.clientWidth / 2 + li.offsetWidth / 2;
    row.scrollTo({ left: Math.max(0, left), behavior: reducedMotion() ? "auto" : "smooth" });
  }
}
/* Left/Right/Home/End move focus along the chip row; Enter or Space picks the focused chip */
function chipKeys(e) {
  const chips = $$("button.pl-chip", $("#pl-chips"));
  const i = chips.indexOf(document.activeElement);
  if (i < 0) return;
  let j = -1;
  if (e.key === "ArrowRight" || e.key === "ArrowDown") j = Math.min(chips.length - 1, i + 1);
  else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = Math.max(0, i - 1);
  else if (e.key === "Home") j = 0;
  else if (e.key === "End") j = chips.length - 1;
  if (j < 0) return;
  e.preventDefault();
  chips.forEach((b, k) => b.setAttribute("tabindex", k === j ? "0" : "-1"));
  chips[j].focus();
}
function updateChipArrows() {
  const row = $("#pl-chips");
  const l = $("#pl-chips-l"), r = $("#pl-chips-r");
  if (!row || !l || !r) return;
  const max = row.scrollWidth - row.clientWidth;
  l.disabled = row.scrollLeft <= 8;
  r.disabled = row.scrollLeft >= max - 8;
  row.parentElement.classList.toggle("at-start", row.scrollLeft <= 8);
  row.parentElement.classList.toggle("at-end", row.scrollLeft >= max - 8);
}

// ------------------------------------------------------------ selection

function selectPlayer(g, name) {
  if (!D.findPlayer(g, name)) return;
  const tourChanged = g !== S.tour;
  if (!tourChanged && name === S.player) return;
  S.tour = g;
  S.player = name;
  S.zone = null;
  if (tourChanged) {
    ctl.tour.set(g, true);
    S.cmp = null;
    renderChips();
  }
  if (S.cmp === S.player) S.cmp = null;
  syncCompare();
  render({ animate: true });
  markChips(true);
  writeHash();
  const live = $("#pl-live");
  if (live) live.textContent = name + " selected.";
}

function syncCompare() {
  const on = !!S.cmp;
  if (ctl.cmp) ctl.cmp.set(on ? "player" : "league", true);
  const box = $("#pl-cmp-box");
  if (box) box.hidden = !on;
  if (cmpBox) cmpBox.setShown(on ? S.cmp : "");
}

// ------------------------------------------------------------ render

function render(opts) {
  const o = opts || {};
  const p = player();
  M = D.model(S.tour, S.player, S.serve);
  CM = S.cmp ? D.model(S.tour, S.cmp, S.serve) : null;
  if (!p || !M) return;
  if (!S.zone) S.zone = D.favoriteZone(M);
  renderCard(p);
  renderCourt(o.animate);
  renderDetail(o.animate);
  renderBadges();
  renderWin(o.animate);
  renderBp(o.animate);
}

function renderCard(p) {
  const photo = $("#pl-photo");
  if (photo.getAttribute("data-name") !== p.server) {
    photo.setAttribute("data-name", p.server);
    photo.innerHTML = avatarHTML(p.server, D.photo(p.server), { size: 132, cls: "avatar-ring", eager: true });
    const card = $("#pl-card");
    if (card && !reducedMotion() && card.animate && started) {
      photo.animate([{ opacity: 0, transform: "scale(0.92)" }, { opacity: 1, transform: "none" }], { duration: 420, easing: "cubic-bezier(0.22, 1, 0.36, 1)" });
    }
  }
  $("#pl-name").textContent = p.server;
  $("#pl-tags").innerHTML =
    '<span class="badge pl-tag">' + esc(TOUR_LABEL[p.gender]) + "</span>" +
    '<span class="badge pl-tag">' + (p.hand === "L" ? "Left-handed" : "Right-handed") + "</span>" +
    '<span class="badge pl-tag">' + esc(p.first + " to " + p.last) + "</span>";
  tween($("#pl-matches"), p.matches, fInt);
  tween($("#pl-serves"), p.serves, fInt);
  const known = SIDES.reduce((a, s) => a + (M[s].test.n || 0), 0);
  $("#pl-known-l").textContent = serveWord() + " serves";
  tween($("#pl-known"), known, fInt);
  const vs = $("#pl-vs");
  if (S.cmp) {
    const c = D.findPlayer(S.tour, S.cmp);
    vs.hidden = false;
    vs.innerHTML = '<span class="pl-vs-word">vs</span>' + avatarHTML(c.server, D.photo(c.server), { size: 44 }) +
      '<span class="pl-vs-name">' + esc(c.server) + '<small>' + esc(fInt(c.matches) + " matches") + "</small></span>";
  } else {
    vs.hidden = true;
    vs.innerHTML = "";
  }
}

// ---------------------------------------------------- court

function cmpLabel() { return [lastName(S.cmp), initials(S.cmp)]; }
function courtView() {
  const bp = bpMode();
  const cells = {};
  SIDES.forEach((side) => {
    cells[side] = {};
    DIRS.forEach((dir) => {
      const c = bp ? M[side].bp[dir] : M[side].all[dir];
      const oth = M[side].other[dir];
      const cc = CM ? (bp ? CM[side].bp[dir] : CM[side].all[dir]) : null;
      const small = [];
      if (bp) small.push({ label: "other", value: oth.share });
      if (cc) small.push({ label: cmpLabel(), value: cc.share });
      else small.push({ label: "league", value: c.league_share });
      const aria = SIDE_LABEL[side] + ", " + dir + ": " + fPct(c.share, 1) + " of " + (bp ? "break-point " : "") + "serves" +
        (c.share == null ? "" : ", " + fPct(CI_LEVEL, 0) + " CI " + ciText(c.share_lo, c.share_hi, 1)) +
        (bp ? ", other points " + fPct(oth.share, 1) : "") +
        (cc ? ", " + S.cmp + " " + fPct(cc.share, 1) : (bp ? ", league, all points " : ", league ") + fPct(c.league_share, 1)) +
        ", " + fInt(c.n) + " serves" + (D.fewServes(c) ? " (few serves, rough interval)" : "");
      cells[side][dir] = { share: c.share, small, aria };
    });
  });
  return {
    label: "Schematic service boxes seen from behind the server, showing the share of " + serveWord() +
      " serves to wide, body and T on the deuce and ad sides" + (bp ? " on break points" : "") + ". Each zone is a button that shows its details.",
    selected: S.zone,
    cells,
  };
}

function courtTitle() {
  const ins = SIDES.map((s) => D.sideInsights(M, s));
  if (bpMode()) {
    const shifts = [];
    ins.forEach((I) => I.bp.shifts.forEach((sh) => shifts.push(Object.assign({ side: I.side }, sh))));
    if (!shifts.length) return "No significant shift on break points";
    shifts.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
    const s = shifts[0];
    return (s.change > 0 ? "More " : "Less ") + s.dir + " on " + s.side + "-side break points";
  }
  const part = (I) => I.favorite ? I.favorite.dir + " on " + I.side : "no clear favorite on " + I.side;
  const a = part(ins[0]), b = part(ins[1]);
  const t = (ins[0].favorite ? "Goes " : "") + a + ", " + (ins[1].favorite && !ins[0].favorite ? "goes " : "") + b;
  return cap(t);
}

function renderCourt(animate) {
  const bp = bpMode();
  $("#pl-court-title").textContent = courtTitle();
  const thin = SIDES.filter((s) => (M[s].test.n_bp || 0) < D.thresholds.bp_warn_n);
  const warn = $("#pl-bpwarn");
  if (bp && thin.length) {
    const parts = thin.map((s) => fInt(M[s].test.n_bp || 0) + " break-point serves on the " + s + " side");
    warn.innerHTML = '<p class="warn" role="note"><strong>Small sample.</strong> Only ' + esc(parts.join(" and ")) +
      " (fewer than " + esc(fInt(D.thresholds.bp_warn_n)) + "). Read these shares as rough.</p>";
  } else {
    warn.innerHTML = "";
  }
  const who = S.cmp ? shortName(S.cmp) : null;
  setKeyHtml($("#pl-court-key"),
    '<span class="ramp">' + rampSvg() + "<span>Share of serves</span></span>" +
    '<span class="pl-key-txt">' + esc(bp
      ? "Big: break points. Small: other points, then " + (who ? who + " on break points" : "league, all points") + "."
      : "Big: this player. Small: " + (who ? who + ", same serve" : "league, same opponents") + ".") + "</span>");
  court.draw(courtView(), { animate });
}
function setKeyHtml(host, html) { host.innerHTML = html; }

// ---------------------------------------------------- zone detail

let detailPrev = {};
function renderDetail(animate) {
  const z = S.zone;
  if (!M || !z) return;
  const side = M[z.side];
  const all = side.all[z.dir], bpc = side.bp[z.dir], oth = side.other[z.dir];
  const bp = bpMode();
  const cc = CM ? (bp ? CM[z.side].bp[z.dir] : CM[z.side].all[z.dir]) : null;
  const P = (v) => fPct(v, 1);
  let rows;
  if (bp) {
    rows = [
      { k: "share", label: "Share on break points", v: bpc.share, f: P, sub: bpc.share == null ? "no break-point serves" : "", hero: true },
      { k: "other", label: "Share on other points", v: oth.share, f: P },
      { k: "league", label: "League, all points", v: bpc.league_share, f: P },
      { k: "n", label: "Break-point serves", v: bpc.n, f: fInt },
    ];
  } else {
    rows = [
      { k: "share", label: "Share of serves", v: all.share, f: P, hero: true },
      { k: "league", label: "League, same opponents", v: all.league_share, f: P },
      { k: "won", label: "Points won after it", v: all.win, f: P, sub: all.win == null ? "no serves" : "league " + P(all.league_win) },
      { k: "n", label: "Serves", v: all.n, f: fInt },
    ];
  }
  if (cc) rows.splice(bp ? 3 : 2, 0, { k: "cmp", label: S.cmp + (bp ? ", break points" : ""), v: cc.share, f: P, sub: "not adjusted for opponents" });
  const c = bp ? bpc : all;
  const few = D.fewServes(c) && c.share != null;
  const host = $("#pl-detail");
  const sw = '<span class="pl-swatch" style="background:' + esc(swatchFill(c.share)) + '"></span>';
  host.innerHTML =
    '<div class="card-head"><div><p class="card-tag">Selected zone</p><h3 class="card-title">' + sw +
    esc(SIDE_LABEL[z.side] + ", " + z.dir) + "</h3></div></div>" +
    '<dl class="pl-dl">' + rows.map((r) =>
      '<div class="' + (r.hero ? "is-hero" : "") + '"><dt>' + esc(r.label) + '</dt><dd><span class="pl-dv" data-k="' + r.k + '">' + esc(r.f(r.v)) + "</span>" +
      (r.sub ? "<small>" + esc(r.sub) + "</small>" : "") + "</dd></div>").join("") + "</dl>" +
    mixBars(z.side) +
    (few ? '<p class="warn pl-few" role="note">Fewer than ' + esc(fInt(D.thresholds.min_dir_n)) + " serves here, so these figures are rough.</p>" : "");
  // compact one-line readout under the court (shown on narrow screens, where the panel sits below the court)
  const ro = $("#pl-readout");
  const byK = {};
  rows.forEach((r) => { byK[r.k] = r; });
  const span = (k) => '<span class="pl-rv" data-k="' + k + '">' + esc(byK[k].f(byK[k].v)) + "</span>";
  if (ro) {
    const head = "<strong>" + esc(cap(z.side) + " " + z.dir) + ":</strong> ";
    ro.innerHTML = head + (bp
      ? span("share") + " of break-point serves, other points " + span("other") + (cc ? ", " + esc(lastName(S.cmp)) + " " + span("cmp") : "")
      : span("share") + " of serves, " + (cc ? esc(lastName(S.cmp)) + " " + span("cmp") : "league " + span("league")) + ", won " + span("won"));
  }
  const next = {};
  const moving = animate && !reducedMotion();
  rows.forEach((r) => {
    next[r.k] = r.v;
    const nodes = [host.querySelector('.pl-dv[data-k="' + r.k + '"]'), ro && ro.querySelector('.pl-rv[data-k="' + r.k + '"]')];
    nodes.forEach((node) => {
      if (node && animate && isNum(detailPrev[r.k]) && isNum(r.v)) tween(node, r.v, r.f, { from: detailPrev[r.k], duration: 500 });
    });
  });
  detailPrev = next;
  // the small sub-lines fade in while the big numbers tween, so old and new figures never sit side by side
  if (moving) $$(".pl-dl small", host).forEach((n) => { if (n.animate) n.animate([{ opacity: 0 }, { opacity: 0, offset: 0.6 }, { opacity: 1 }], { duration: 560, easing: "ease-out" }); });
  growMix(host, animate);
  const live = $("#pl-zone-live");
  if (live && liveZone) live.textContent = SIDE_LABEL[z.side] + ", " + z.dir + ": " + P(c.share) + (bp ? " of break-point serves." : " of serves.");
  liveZone = false;
}
let liveZone = false;

/* the whole mix on the selected side as two 100% bars: this player and the league (or the comparison player) */
let mixPrev = {};
function mixRow(key, label, cells) {
  const tot = DIRS.reduce((a, d) => a + (isNum(cells[d]) ? cells[d] : 0), 0) || 1;
  return '<div class="pl-mix-row"><span class="pl-mix-lab">' + esc(label) + '</span><div class="pl-mix-bar" role="img" aria-label="' +
    esc(label + ": " + DIRS.map((d) => d + " " + fPct(cells[d], 0)).join(", ")) + '">' +
    DIRS.map((d) => {
      const w = 100 * (isNum(cells[d]) ? cells[d] : 0) / tot;
      const on = S.zone && S.zone.dir === d;
      const full = d + " " + fPct(cells[d], 0);
      return '<span class="pl-seg pl-seg-' + d + (on ? " is-on" : "") + '" data-k="' + key + "|" + d + '" data-w="' + w.toFixed(3) +
        '" data-full="' + esc(full) + '" data-short="' + esc(fPct(cells[d], 0)) + '" title="' + esc(full) + '"><span>' +
        (w >= 22 ? esc(full) : w >= 12 ? esc(fPct(cells[d], 0)) : "") + "</span></span>";
    }).join("") + "</div></div>";
}
function mixBars(sideName) {
  const bp = bpMode();
  const side = M[sideName];
  const mine = {}, ref = {};
  DIRS.forEach((d) => {
    mine[d] = (bp ? side.bp[d] : side.all[d]).share;
    ref[d] = CM ? (bp ? CM[sideName].bp[d] : CM[sideName].all[d]).share : side.all[d].league_share;
  });
  const refLabel = CM ? shortName(S.cmp) + (bp ? ", break points" : "") : (bp ? "League, all points" : "League, same opponents");
  return '<div class="pl-mix"><p class="pl-mix-title">' + esc(SIDE_LABEL[sideName] + " mix") + "</p>" +
    mixRow("me", bp ? "Player, break points" : "Player", mine) + mixRow("ref", refLabel, ref) + "</div>";
}
/* a segment label is printed only when it fits the segment's final width: "body 18%", then "18%", then nothing
   (the value stays in the bar's aria-label and in the segment's title) */
let measureCtx = null;
function fitMix(host) {
  const bars = $$(".pl-mix-bar", host);
  bars.forEach((bar) => {
    const segs = $$(".pl-seg", bar);
    const inner = bar.clientWidth - 2 * Math.max(0, segs.length - 1);
    if (!(inner > 0)) return;
    if (!measureCtx) { try { measureCtx = document.createElement("canvas").getContext("2d"); } catch (e) { measureCtx = null; } }
    segs.forEach((g) => {
      const lab = g.firstElementChild;
      if (!lab) return;
      const px = (Number(g.getAttribute("data-w")) / 100) * inner - 10;
      const cs = getComputedStyle(g);
      const width = (t) => {
        if (!measureCtx) return t.length * 7.2;
        measureCtx.font = cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
        return measureCtx.measureText(t).width;
      };
      const full = g.getAttribute("data-full"), short = g.getAttribute("data-short");
      lab.textContent = width(full) <= px ? full : width(short) <= px ? short : "";
    });
  });
}
function growMix(host, animate) {
  const segs = $$(".pl-seg", host);
  const set = () => segs.forEach((g) => { g.style.flexBasis = g.getAttribute("data-w") + "%"; });
  if (animate && !reducedMotion()) {
    segs.forEach((g) => { const k = g.getAttribute("data-k"); g.style.flexBasis = (isNum(mixPrev[k]) ? mixPrev[k] : 0) + "%"; });
    void host.offsetWidth;
    requestAnimationFrame(set);
  } else {
    set();
  }
  fitMix(host);
  mixPrev = {};
  segs.forEach((g) => { mixPrev[g.getAttribute("data-k")] = Number(g.getAttribute("data-w")); });
}
// the swatch next to the zone name uses the zone's own color
function swatchFill(share) { return shareColor(share).fill; }

function onZone(side, dir, how) {
  const same = S.zone && S.zone.side === side && S.zone.dir === dir;
  if (same) return;
  S.zone = { side, dir };
  court.mark(S.zone);
  liveZone = how !== "hover";
  renderDetail(true);
}

// ---------------------------------------------------- badges

function badge(cls, ico, text, qv, info, label) {
  return '<span class="badge ' + cls + '">' + bicon(ico) + "<span>" + esc(text) + "</span>" +
    (qv != null ? '<span class="qtag">' + esc(pEq("q", qv)) + "</span>" : "") +
    infoButton(info, "About: " + label) + "</span>";
}
function sideBadges(I) {
  const bp = bpMode();
  const sideWord = I.side + " side";
  const allNote = bp ? " All points." : "";
  const tour = TOUR_PLURAL[S.tour];
  const strong = [];   // badges that state a finding, strongest kind first
  const quiet = [];    // sentences for results that are not clear; they share one muted badge
  // win test
  if (!I.win.tested) {
    quiet.push("Too few serves to test whether win rates differ by direction.");
  } else if (I.win.differs) {
    strong.push(badge("badge-good", "win", "Wins more going " + I.win.best_dir, I.win.q,
      "Win rates differ by direction (" + pEq("q", I.win.q) + "): points won after " +
      I.win.rates.map((r) => r.dir + " " + fPct(r.win, 1)).join(", ") + ". Sending every serve " + I.win.best_dir +
      " could be worth at most " + fNum(I.win.gain_pp, 1) + " points per 100 serves, an upper bound: returners would adjust." + allNote,
      "win test, " + sideWord));
  } else {
    quiet.push("Win rates do not differ clearly by direction (" + pEq("q", I.win.q) + ").");
  }
  // break-point shifts (break points against other points)
  if (I.bp.shifts.length) {
    // one badge per side: the shifted directions in the text, each q value in the popover
    const sh = I.bp.shifts.slice().sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
    const words = sh.map((x) => (x.change > 0 ? "more " : "less ") + x.dir).join(", ");
    strong.push(badge("badge-warn", "bolt", "Break points: " + words, sh.length === 1 ? sh[0].q : null,
      sh.map((x) => cap(x.dir) + ": " + fPct(x.other, 1) + " on other points, " + fPct(x.bp, 1) + " on break points (" + pEq("q", x.q) + ").").join(" ") +
      (I.bp.thin ? " Only " + fInt(I.bp.n_bp) + " break-point serves on this side." : ""),
      "break-point shift, " + sideWord));
  } else {
    quiet.push("No direction shifts clearly on break points (" + fInt(I.bp.n_bp) + " break-point serves)." +
      (I.bp.thin ? " With so few, only a large shift could be detected." : ""));
  }
  // favorite target, with the favorite-target share against other players in its popover
  let pctBadge = null;
  if (I.favorite) {
    const f = I.favorite;
    let rank = "";
    if (isNum(I.favPct)) {
      rank = I.favPct >= 0.5
        ? " That share is higher than for " + fPct(I.favPct, 0) + " of listed " + tour + " on this side."
        : " That share is lower than for " + fPct(1 - I.favPct, 0) + " of listed " + tour + " on this side, so the mix is more even.";
      if (I.favPct >= 0.8) pctBadge = badge("badge-blue", "focus", "More one-sided than " + fPct(I.favPct, 0) + " of " + tour, null,
        "The share sent to the favorite target is higher than for " + fPct(I.favPct, 0) + " of listed " + tour + " on the " + sideWord + "." + allNote,
        "favorite-target share, " + sideWord);
      else if (I.favPct <= 0.2) pctBadge = badge("badge-blue", "focus", "More mixed than " + fPct(1 - I.favPct, 0) + " of " + tour, null,
        "The share sent to the favorite target is lower than for " + fPct(1 - I.favPct, 0) + " of listed " + tour + " on the " + sideWord + "." + allNote,
        "favorite-target share, " + sideWord);
    }
    strong.push(badge("badge-blue", "target", "Favorite: " + f.dir, null,
      cap(f.dir) + " gets " + fPct(f.share, 1) + " of " + serveWord() + " serves on the " + sideWord +
      ". League facing the same opponents: " + fPct(f.league, 1) + "." + rank + allNote,
      "favorite target, " + sideWord));
  } else if (I.noClearFavorite) {
    quiet.push("No clear favorite: the top two directions are within " + fNum(100 * NO_CLEAR_FAVORITE, 0) + " percentage points (" +
      I.noClearFavorite.slice(0, 2).map((x) => x.dir + " " + fPct(x.share, 1)).join(", ") + ").");
  }
  // mix test
  if (I.mix.tested) {
    if (I.mix.differs) {
      strong.push(badge("badge-blue", "mix", "Mix differs from league", I.mix.q,
        "The direction mix differs from the league facing the same opponents (" + pEq("q", I.mix.q) + ", adjusted for " + fInt(D.meta.tests.mix) +
        " mix tests). With this many serves, even a small gap in the bars can be clear." + allNote,
        "mix test, " + sideWord));
    } else {
      quiet.push("The mix does not differ clearly from the league facing the same opponents (" + pEq("q", I.mix.q) + ").");
    }
  }
  // the share rank gets its own badge only when it is extreme and there is room (it is also in the favorite popover)
  if (pctBadge && strong.length < 3) strong.push(pctBadge);
  let out = strong.join("");
  if (quiet.length) {
    out += badge("badge-muted pl-quiet", "dash", strong.length ? "Nothing else stands out" : "Nothing stands out", null,
      quiet.join(" ") + (bp ? " Favorite, mix and win results use all points." : ""), "results that are not clear, " + sideWord);
  }
  return out;
}
function renderBadges() {
  const host = $("#pl-badges");
  dropPops(host);
  host.innerHTML = SIDES.map((s) => {
    const I = D.sideInsights(M, s);
    return '<div class="pl-bgroup"><p class="pl-bside"><span>' + esc(SIDE_LABEL[s]) + '</span></p><div class="badge-row">' + sideBadges(I) + "</div></div>";
  }).join("");
  $("#pl-badges-note").hidden = !bpMode();
  initPopovers(host);
}

// ---------------------------------------------------- mini charts (horizontal dot rows, animated between states)

function lerp(a, b, t) { return isNum(a) && isNum(b) ? a + (b - a) * t : b; }
function lerpData(A, B, t) {
  return {
    lo: lerp(A.lo, B.lo, t), hi: lerp(A.hi, B.hi, t), ticks: B.ticks,
    rows: B.rows.map((r, i) => {
      const a = A.rows[i] || {};
      const o = Object.assign({}, r);
      ["v", "lo", "hi", "ref", "a", "b"].forEach((k) => { o[k] = lerp(a[k], r[k], t); });
      return o;
    }),
  };
}
const charts = {};
/* animateChart(name, host, data, drawFn, animate): draws data; if animate, glides from the last drawn data */
function animateChart(name, host, data, drawFn, animate) {
  const st = charts[name] || (charts[name] = { raf: 0, last: null });
  if (st.raf) cancelAnimationFrame(st.raf);
  const from = st.last;
  st.last = data;
  if (!animate || !from || reducedMotion() || from.rows.length !== data.rows.length) { drawFn(host, data); return; }
  const t0 = performance.now(), dur = 560;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    drawFn(host, k < 1 ? lerpData(from, data, e) : data, k < 1);
    st.raf = k < 1 ? requestAnimationFrame(step) : 0;
  };
  st.raf = requestAnimationFrame(step);
}

const ROW_H = 30, HEAD_H = 26, AXIS_H = 22;
function rowLayout(host, rows) {
  const W = Math.max(280, Math.floor(host.clientWidth || 480));
  let y = AXIS_H;
  const pos = [];
  let lastSide = null;
  rows.forEach((r) => {
    if (r.side !== lastSide) { y += HEAD_H; lastSide = r.side; }
    pos.push(y + ROW_H / 2);
    y += ROW_H;
  });
  return { W, H: y + 6, pos };
}
function drawAxis(svg, W, ml, mr, x, ticks, H, lo, hi) {
  const grid = el("g", { class: "grid" }, svg);
  ticks.forEach((tv) => {
    if (tv < lo - 1e-9 || tv > hi + 1e-9) return;
    el("line", { x1: x(tv), x2: x(tv), y1: AXIS_H - 4, y2: H - 4 }, grid);
    txt(svg, x(tv), AXIS_H - 9, fPct(tv, 0), null, "middle");
  });
}
function sideHeads(svg, rows, pos) {
  let lastSide = null;
  rows.forEach((r, i) => {
    if (r.side === lastSide) return;
    lastSide = r.side;
    txt(svg, 0, pos[i] - ROW_H / 2 - 8, SIDE_LABEL[r.side], "panel-title");
  });
}

function winData() {
  const minN = D.thresholds.min_dir_n;
  const rows = [];
  SIDES.forEach((side) => DIRS.forEach((d) => {
    const c = M[side].all[d];
    const cc = CM ? CM[side].all[d] : null;
    const muted = (c.n || 0) < minN;
    rows.push({
      side, dir: d, v: c.win, lo: c.win_lo, hi: c.win_hi, ref: cc ? cc.win : c.league_win, muted,
      tip: SIDE_LABEL[side] + ", " + d + "\nThis player: " + fPct(c.win, 1) + " won" +
        (isNum(c.win) ? " (" + fPct(CI_LEVEL, 0) + " CI " + ciText(c.win_lo, c.win_hi, 1) + ")" : "") +
        (cc ? "\n" + S.cmp + ": " + fPct(cc.win, 1) : "\nLeague, same opponents: " + fPct(c.league_win, 1)) +
        "\n" + fInt(c.n) + " serves" + (muted ? "\nFewer than " + fInt(minN) + " serves: left out of the win test" : ""),
    });
  }));
  const vals = [];
  rows.forEach((r) => vals.push(r.v, r.lo, r.hi, r.ref));
  const dom = domainOf(vals, 0.06, 4, 0, 1);
  return { lo: dom.lo, hi: dom.hi, ticks: dom.ticks, rows };
}
function drawWin(host, data, moving) {
  const { W, H, pos } = rowLayout(host, data.rows);
  const svg = newSvg(host, W, H, "Points won by the server after each serve direction, " + serveWord() +
    " serves, deuce and ad side, with confidence intervals and " + (S.cmp ? S.cmp : "the league") + " for reference", "img");
  const ml = 54, mr = 58;
  const x = (v) => ml + ((v - data.lo) / (data.hi - data.lo)) * (W - ml - mr);
  drawAxis(svg, W, ml, mr, x, data.ticks, H, data.lo, data.hi);
  sideHeads(svg, data.rows, pos);
  data.rows.forEach((r, i) => {
    const y = pos[i];
    const g = el("g", {}, svg);
    txt(g, 0, y + 4, r.dir, "cat");
    el("line", { x1: ml, x2: W - mr, y1: y, y2: y, class: "pl-track" }, g);
    if (!isNum(r.v)) {
      txt(g, W, y + 4, "no serves", "val-muted", "end");
    } else {
      const wc = r.muted ? "w-muted" : "w-player";
      if (isNum(r.lo) && isNum(r.hi)) {
        el("line", { x1: x(r.lo), x2: x(r.hi), y1: y, y2: y, class: wc + " grow-x", style: "--d:" + i }, g);
        el("line", { x1: x(r.lo), x2: x(r.lo), y1: y - 5, y2: y + 5, class: wc + " fade-in", style: "--d:" + i }, g);
        el("line", { x1: x(r.hi), x2: x(r.hi), y1: y - 5, y2: y + 5, class: wc + " fade-in", style: "--d:" + i }, g);
      }
      if (isNum(r.ref)) {
        if (S.cmp) el("circle", { cx: x(r.ref), cy: y, r: 5.5, class: "m-cmp pop-in", style: "--d:" + (i + 2) }, g);
        else el("path", { d: diamond(x(r.ref), y, 6), class: "m-league pop-in", style: "--d:" + (i + 2) }, g);
      }
      el("circle", { cx: x(r.v), cy: y, r: 6.5, class: (r.muted ? "m-player-muted" : "m-player") + " pop-in", style: "--d:" + (i + 1) }, g);
      txt(g, W, y + 4, fPct(r.v, 1), r.muted ? "val-muted" : "val", "end");
    }
    if (!moving) el("rect", { x: 0, y: y - ROW_H / 2, width: W, height: ROW_H, class: "hit", "data-tip": r.tip }, g);
  });
}
function renderWin(animate) {
  const ins = SIDES.map((s) => D.sideInsights(M, s));
  const diff = ins.filter((I) => I.win.differs);
  let title;
  if (diff.length === 2) title = "Wins more going " + diff[0].win.best_dir + " (deuce) and " + diff[1].win.best_dir + " (ad)";
  else if (diff.length === 1) title = "Wins more going " + diff[0].win.best_dir + " on the " + diff[0].side + " side";
  else if (!ins.some((I) => I.win.tested)) title = "Too few serves to test win rates";
  else title = "No direction wins significantly more";
  $("#pl-win-title").textContent = title;
  const minN = D.thresholds.min_dir_n;
  setKey($("#pl-win-key"), [
    [keyDot("m-player"), "This player"],
    S.cmp ? [keyDot("m-cmp"), shortName(S.cmp)] : [keyDiamond(), "League, same opponents"],
    [keyDot("m-player-muted"), "Under " + fInt(minN) + " serves"],
  ]);
  const wd = winData();
  fillTable($("#pl-win-table"), "Points won after each " + serveWord() + " serve direction, all points",
    ["Side", "Direction", "Points won", fPct(CI_LEVEL, 0) + " CI", S.cmp ? S.cmp : "League, same opponents", "Serves"],
    wd.rows.map((r) => {
      const c = M[r.side].all[r.dir];
      return [SIDE_LABEL[r.side], r.dir, fPct(c.win, 1), isNum(c.win) ? ciText(c.win_lo, c.win_hi, 1) : "n/a", fPct(r.ref, 1), fInt(c.n)];
    }));
  animateChart("win", $("#pl-win"), wd, drawWin, animate);
}

function bpData() {
  const q = D.thresholds.q;
  const rows = [];
  SIDES.forEach((side) => {
    const t = M[side].test;
    DIRS.forEach((d) => {
      const qv = t["bp_" + d + "_q"];
      const a = M[side].other[d].share, b = M[side].bp[d].share;
      rows.push({
        side, dir: d, a, b, sig: isNum(qv) && qv < q, change: t["bp_" + d], q: qv,
        tip: SIDE_LABEL[side] + ", " + d + "\nOther points: " + fPct(a, 1) + "\nBreak points: " + fPct(b, 1) + " (" + fInt(M[side].bp[d].n) + " serves)" +
          (isNum(qv) ? "\n" + pEq("q", qv) : ""),
      });
    });
  });
  const vals = [];
  rows.forEach((r) => vals.push(r.a, r.b));
  const dom = domainOf(vals, 0.08, 4, 0, 1);
  return { lo: dom.lo, hi: dom.hi, ticks: dom.ticks, rows };
}
function drawBp(host, data, moving) {
  const { W, H, pos } = rowLayout(host, data.rows);
  const svg = newSvg(host, W, H, "Share of " + serveWord() + " serves to each direction on other points and on break points, deuce and ad side", "img");
  const ml = 54, mr = 58;
  const x = (v) => ml + ((v - data.lo) / (data.hi - data.lo)) * (W - ml - mr);
  drawAxis(svg, W, ml, mr, x, data.ticks, H, data.lo, data.hi);
  sideHeads(svg, data.rows, pos);
  data.rows.forEach((r, i) => {
    const y = pos[i];
    const g = el("g", { class: r.sig ? "pl-sig" : "" }, svg);
    txt(g, 0, y + 4, r.dir, "cat");
    el("line", { x1: ml, x2: W - mr, y1: y, y2: y, class: "pl-track" }, g);
    if (isNum(r.a) && isNum(r.b)) {
      const x1 = Math.min(x(r.a), x(r.b)), x2 = Math.max(x(r.a), x(r.b));
      el("line", { x1, x2, y1: y, y2: y, class: (r.sig ? "pl-shift" : "pl-shift-muted") + " grow-x", style: "--d:" + i }, g);
      el("circle", { cx: x(r.a), cy: y, r: 5.5, class: "m-other pop-in", style: "--d:" + i }, g);
      el("circle", { cx: x(r.b), cy: y, r: 6.5, class: (r.sig ? "m-player" : "m-player-muted") + " pop-in", style: "--d:" + (i + 1) }, g);
      const lab = isNum(r.change) ? fNum(100 * r.change, 1) : "n/a";
      txt(g, W, y + 4, (isNum(r.change) && r.change > 0 && lab !== "0.0" ? "+" : "") + lab + " pts", r.sig ? "val" : "val-muted", "end");
    } else {
      txt(g, W, y + 4, "no serves", "val-muted", "end");
    }
    if (!moving) el("rect", { x: 0, y: y - ROW_H / 2, width: W, height: ROW_H, class: "hit", "data-tip": r.tip }, g);
  });
}
function renderBp(animate) {
  const ins = SIDES.map((s) => D.sideInsights(M, s));
  const shifts = [];
  ins.forEach((I) => I.bp.shifts.forEach((sh) => shifts.push(Object.assign({ side: I.side }, sh))));
  shifts.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
  $("#pl-bp-title").textContent = shifts.length
    ? (shifts[0].change > 0 ? "More " : "Less ") + shifts[0].dir + " on " + shifts[0].side + "-side break points"
    : "No significant shift on break points";
  setKey($("#pl-bp-key"), [
    [keyDot("m-other"), "Other points"],
    [keyDot("m-player"), "Break points, significant"],
    [keyDot("m-player-muted"), "Break points, not significant"],
  ]);
  const thin = SIDES.filter((s) => (M[s].test.n_bp || 0) < D.thresholds.bp_warn_n);
  const capEl = $("#pl-bp-cap");
  capEl.textContent = thin.length
    ? "Small sample: only " + thin.map((s) => fInt(M[s].test.n_bp || 0) + " " + s + "-side").join(" and ") + " break-point serves."
    : "";
  capEl.hidden = !thin.length;
  capEl.classList.toggle("pl-cap-warn", thin.length > 0);
  const bd = bpData();
  fillTable($("#pl-bp-table"), "Share of " + serveWord() + " serves to each direction on other points and on break points",
    ["Side", "Direction", "Other points", "Break points", "Change, percentage points", "Test"],
    bd.rows.map((r) => [SIDE_LABEL[r.side], r.dir, fPct(r.a, 1), fPct(r.b, 1),
      isNum(r.change) ? fNum(100 * r.change, 1) : "n/a", isNum(r.q) ? pEq("q", r.q) : "n/a"]));
  animateChart("bp", $("#pl-bp"), bd, drawBp, animate);
}
/* visually hidden data table under a mini chart, so screen readers get every value */
function fillTable(t, caption, head, rows) {
  if (!t) return;
  t.innerHTML = "<caption>" + esc(caption) + "</caption><thead><tr>" + head.map((h) => '<th scope="col">' + esc(h) + "</th>").join("") +
    "</tr></thead><tbody>" + rows.map((r) => "<tr>" + r.map((v, i) => (i < 2 ? '<th scope="row">' : "<td>") + esc(v) + (i < 2 ? "</th>" : "</td>")).join("") + "</tr>").join("") +
    "</tbody>";
}

// ------------------------------------------------------------ init

let cmpBox = null;
function redrawAll() {
  if (!M) return;
  renderCourt(false);
  renderDetail(false);
  drawWin($("#pl-win"), charts.win ? charts.win.last : winData());
  drawBp($("#pl-bp"), charts.bp ? charts.bp.last : bpData());
}

export function init(ctx) {
  D = ctx.data;
  ANIM = ctx.anim;
  const root = $("#players");
  if (!D || !root) return;

  S.player = D.playerList("m")[0].server;
  const at = readHash();

  // tour switch
  ctl.tour = segmented($("#pl-tour"), {
    label: "Tour", value: S.tour,
    options: [{ value: "m", label: "Men" }, { value: "w", label: "Women" }],
    onChange: (v) => selectPlayer(v, D.playerList(v)[0].server),
  });
  // search
  const q = $("#pl-q");
  q.setAttribute("placeholder", "Search " + fInt(D.players.length) + " players");
  combobox({
    input: q, list: $("#pl-list"), live: $("#pl-live"), showTour: true,
    items: mainItems,
    pick: (p) => selectPlayer(p.gender, p.server),
  });
  // view controls
  ctl.serve = segmented($("#pl-serve"), {
    label: "Serve", value: String(S.serve),
    options: [{ value: "1", label: "1st serve" }, { value: "2", label: "2nd serve" }],
    onChange: (v) => { S.serve = Number(v); render({ animate: true }); writeHash(); },
  });
  ctl.sit = segmented($("#pl-sit"), {
    label: "Points", value: S.sit,
    options: [{ value: "all", label: "All points" }, { value: "bp", label: "Break points" }],
    onChange: (v) => { S.sit = v; render({ animate: true }); writeHash(); },
  });
  let cmpPointer = -1e9;
  $("#pl-cmp").addEventListener("pointerdown", () => { cmpPointer = performance.now(); });
  ctl.cmp = segmented($("#pl-cmp"), {
    label: "Compare with", value: S.cmp ? "player" : "league",
    options: [{ value: "league", label: "League" }, { value: "player", label: "Player" }],
    onChange: (v) => {
      if (v === "player") {
        const pick = D.featured(S.tour).concat(D.playerList(S.tour)).filter((p) => p.server !== S.player)[0];
        S.cmp = pick ? pick.server : null;
      } else {
        S.cmp = null;
      }
      syncCompare();
      render({ animate: true });
      writeHash();
      if (v === "player" && performance.now() - cmpPointer < 1000) { const ci = $("#pl-cq"); if (ci) ci.focus(); }
    },
  });
  cmpBox = combobox({
    input: $("#pl-cq"), list: $("#pl-clist"), live: $("#pl-live"), keepValue: true,
    items: cmpItems,
    pick: (p) => { S.cmp = p.server; syncCompare(); render({ animate: true }); writeHash(); },
  });

  court = createCourt($("#pl-court"), { onSelect: onZone });

  const qCut = fNum(D.thresholds.q, 2);
  const bInfo = $("#pl-badges-info");
  if (bInfo) setInfo(bInfo, "Badges with a q value are test results that allow for testing " + fInt(D.players.length) +
    " players at once. Under " + qCut + " counts as clear, and a smaller q is stronger evidence. The favorite badges describe the serve mix.");
  const wInfo = $("#pl-win-info");
  if (wInfo) setInfo(wInfo, wInfo.getAttribute("data-info") + " Dots show points won after each direction on all points; lines show " +
    fPct(CI_LEVEL, 0) + " confidence intervals.");
  const pInfo = $("#pl-bp-info");
  if (pInfo) setInfo(pInfo, pInfo.getAttribute("data-info") + " The number on the right is break points minus other points, in percentage points.");

  // banner backdrop: the ball-on-clay photo (the stadium photo is the league banner), plain navy otherwise
  const ball = D.scene("ball");
  const bg = ball || D.scene("stadium");
  const bgEl = $("#pl-card .pl-card-bg");
  if (bg && bg.src && bgEl) {
    // loaded when the card comes near the viewport, so phones do not fetch it with the hero
    const setBg = () => {
      bgEl.style.backgroundImage = 'url("' + bg.src.replace(/"/g, "") + '")';
      if (ball) bgEl.style.backgroundPosition = "50% 72%";
    };
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((ents) => {
        if (ents.some((e) => e.isIntersecting)) { io.disconnect(); setBg(); }
      }, { rootMargin: "200px 0px" });
      io.observe($("#pl-card"));
    } else {
      setBg();
    }
  }

  // chip row scroll buttons
  const row = $("#pl-chips");
  row.addEventListener("scroll", debounce(updateChipArrows, 60), { passive: true });
  row.addEventListener("keydown", chipKeys);
  $("#pl-chips-l").addEventListener("click", () => row.scrollBy({ left: -row.clientWidth * 0.8, behavior: reducedMotion() ? "auto" : "smooth" }));
  $("#pl-chips-r").addEventListener("click", () => row.scrollBy({ left: row.clientWidth * 0.8, behavior: reducedMotion() ? "auto" : "smooth" }));

  renderChips();
  syncCompare();
  render({ animate: false });
  started = true;

  lastW = document.documentElement.clientWidth;
  window.addEventListener("resize", debounce(() => {
    const w = document.documentElement.clientWidth;
    if (w === lastW) return;
    lastW = w;
    redrawAll();
    updateChipArrows();
  }, 150));
  onThemeChange(() => { redrawAll(); });

  window.addEventListener("hashchange", () => {
    if (!/(^#|&)p=/.test(location.hash)) return;
    const key = () => [S.tour, S.player, S.serve, S.sit, S.cmp].join("|");
    const before = key(), tour0 = S.tour;
    const at2 = readHash();
    if (key() === before) return;
    S.zone = null;
    ctl.tour.set(S.tour, true);
    ctl.serve.set(String(S.serve), true);
    ctl.sit.set(S.sit, true);
    if (S.tour !== tour0) renderChips();
    syncCompare();
    render({ animate: true });
    markChips(true);
    if (at2 === "players" || at2 === "scout") root.scrollIntoView({ block: "start" });
  });

  // a shared link (#p=...&at=players) or a plain #players anchor lands on the dashboard once it has its full height
  if ((at === "players" || at === "scout") || location.hash === "#players") {
    requestAnimationFrame(() => { root.scrollIntoView({ block: "start" }); });
  }
}
