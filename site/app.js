/* Serve IQ static site. Vanilla ES2019, no build step.
   Every statistic comes from data/serveiq.json. Headline values are read only through the H object (data.headline). */
"use strict";

(function () {
  const DIRS = ["wide", "body", "T"];
  const SIDES = ["deuce", "ad"];
  const SIDE_LABEL = { deuce: "Deuce side", ad: "Ad side" };
  const TOUR_PLURAL = { m: "men", w: "women" };
  const MATCHUP = { RvR: "Righty vs righty", RvL: "Righty vs lefty", LvR: "Lefty vs righty", LvL: "Lefty vs lefty" };
  const SERVE_LABEL = { 1: "1st", 2: "2nd" };
  // Method constants (not statistics): confidence level, even three-way split,
  // "no clear favorite" margin, and the court geometry in meters.
  const CI_LEVEL = 0.95;
  const EVEN_SPLIT = 1 / 3;
  const NO_CLEAR_FAVORITE = 0.03;
  const BOX_W = 4.115;
  const BOX_D = 6.40;
  const SINGLES_W = 8.23;
  const HALF_LEN = 11.885;
  const BACK_STRIP = 0.6;    // depth of backcourt drawn behind the service line (the rest is cut)
  const SHARE_DOMAIN_MAX = 0.7;
  const RAMP_LIGHT = ["#e8f1fc", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
  const RAMP_DARK = ["#1c2838", "#1d3350", "#20426d", "#26538c", "#2e67ad", "#4381d0", "#68a0e8", "#9cc4f5"];
  const SVGNS = "http://www.w3.org/2000/svg";

  let DATA = null;
  let H = null;
  let meta = null;
  let PLAYERS = [];
  let MIXF = null;
  let TESTF = null;
  let LDIR = [];
  let WITHIN = [];
  let SERIAL = [];
  let STAB = [];

  const S = { tour: "m", player: null, serve: 1, sit: "all", zone: null, ltour: "m", matchup: "RvR", lserve: 1, at: null };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  // ------------------------------------------------------------ columns by name

  function fieldIndex(table) {
    const o = {};
    table.fields.forEach((f, i) => { o[f] = i; });
    return o;
  }
  function col(table, name) {
    const i = table.fields.indexOf(name);
    if (i < 0) throw new Error("Column " + name + " is missing from the data file");
    return i;
  }
  function objects(table) {
    const idx = fieldIndex(table);
    return table.rows.map((r) => {
      const o = {};
      Object.keys(idx).forEach((k) => { o[k] = r[idx[k]]; });
      return o;
    });
  }
  function rowObj(arr, idx) {
    const o = {};
    Object.keys(idx).forEach((k) => { o[k] = arr == null ? null : arr[idx[k]]; });
    return o;
  }

  // ------------------------------------------------------------ formatting

  const NF = new Intl.NumberFormat("en-US");
  const isNum = (x) => typeof x === "number" && isFinite(x);
  function cleanZero(s) { return /^-0(\.0+)?$/.test(s) ? s.slice(1) : s; }
  function fInt(x) { return isNum(x) ? NF.format(Math.round(x)) : "n/a"; }
  function fPct(x, d) { return isNum(x) ? cleanZero((100 * x).toFixed(d == null ? 1 : d)) + "%" : "n/a"; }
  function fNum(x, d) { return isNum(x) ? cleanZero(x.toFixed(d)) : "n/a"; }
  function fSigned(x, d) {
    if (!isNum(x)) return "n/a";
    const s = fNum(x, d);
    return x > 0 && parseFloat(s) !== 0 ? "+" + s : s;
  }
  function fP(p) {
    if (!isNum(p)) return "n/a";
    if (p < 0.0001) return "< 0.0001";
    return p.toPrecision(2);
  }
  function pEq(name, p) {
    const s = fP(p);
    return s.charAt(0) === "<" ? name + " " + s : name + " = " + s;
  }
  function fmt(v, f) {
    switch (f) {
      case "int": return fInt(v);
      case "pct0": return fPct(v, 0);
      case "pct1": return fPct(v, 1);
      case "r2": return fNum(v, 2);
      case "abs1": return isNum(v) ? fNum(Math.abs(v), 1) : "n/a";
      case "s1": return fNum(v, 1);
      case "s2": return fNum(v, 2);
      default: return v == null ? "n/a" : String(v);
    }
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }
  function ciText(lo, hi, d) { return fPct(lo, d) + " to " + fPct(hi, d); }
  // A cell with fewer serves than the per-direction minimum gets a rough (possibly zero-width or 0 to 100%) interval.
  function fewServes(c) { return !!c && isNum(c.n) && c.n < meta.thresholds.min_dir_n; }
  function ciCell(c) {
    if (!c || c.share == null) return "";
    return ciText(c.share_lo, c.share_hi, 1) + (fewServes(c) ? " (few serves)" : "");
  }
  function winCiCell(c) {
    if (!c || c.win == null) return "";
    return ciText(c.win_lo, c.win_hi, 1) + (fewServes(c) ? " (few serves)" : "");
  }

  // ------------------------------------------------------------ bindings (headline values through the H object)

  const HB = {
    serves_analysed: () => H.serves_analysed,
    serves_listed: () => H.serves_listed,
    players: () => H.players,
    men: () => H.men,
    women: () => H.women,
    mix_differs_share_s1: () => H.mix_differs_share_s1,
    mix_differs_n_s1: () => H.mix_differs_n_s1,
    mix_total_s1: () => H.mix_total_s1,
    mix_differs_share_s2: () => H.mix_differs_share_s2,
    fav_r_min: () => H.fav_r_min,
    fav_r_max: () => H.fav_r_max,
    costly_players_s1: () => H.costly_players_s1,
    costly_sides_s1: () => H.costly_sides_s1,
    costly_s2: () => H.costly_s2,
    edge_r_min: () => H.edge_r_min,
    edge_r_max: () => H.edge_r_max,
    bp_sig_tests: () => H.bp_sig_tests,
    bp_sig_patterns: () => H.bp_sig_patterns,
    "body_win_m1.coef": () => H.body_win_m1.coef,
    "body_win_m1.lo": () => H.body_win_m1.lo,
    "body_win_m1.hi": () => H.body_win_m1.hi,
    "body_in_m1.coef": () => H.body_in_m1.coef,
    "body_win_w1.coef": () => H.body_win_w1.coef,
    "body_in_w1.coef": () => H.body_in_w1.coef,
    "body_win_m2.coef": () => H.body_win_m2.coef,
    "body_win_w2.coef": () => H.body_win_w2.coef,
    "nadal_deuce_wide_s1.other": () => H.nadal_deuce_wide_s1.other,
    "nadal_deuce_wide_s1.bp": () => H.nadal_deuce_wide_s1.bp,
  };

  function yearRange() {
    const first = Math.min.apply(null, PLAYERS.map((p) => p.first));
    const last = Math.max.apply(null, PLAYERS.map((p) => p.last));
    return [first, last];
  }

  const BB = {
    ci: () => fPct(CI_LEVEL, 0),
    even: () => fPct(EVEN_SPLIT, 0),
    fdr: () => fPct(meta.thresholds.q, 0),
    q: () => String(meta.thresholds.q),
    min_serves_side: () => fInt(meta.thresholds.min_serves_side),
    min_matches: () => fInt(meta.thresholds.min_matches),
    min_dir_n: () => fInt(meta.thresholds.min_dir_n),
    bp_warn_n: () => fInt(meta.thresholds.bp_warn_n),
    tests_mix: () => fInt(meta.tests.mix),
    tests_win: () => fInt(meta.tests.win),
    tests_bp: () => fInt(meta.tests.bp),
    year_first: () => String(yearRange()[0]),
    year_last: () => String(yearRange()[1]),
    per100: () => "100",
    serves_total: () => fInt(meta.serves_total),
    tiebreak_share: () => fPct(meta.tiebreak_share, 0),
    nodir_share: () => fPct(meta.nodir_share, 1),
    box_dims: () => BOX_W.toFixed(3) + " m wide by " + BOX_D.toFixed(2) + " m deep",
  };

  function bindAll() {
    $$("[data-h]").forEach((el) => {
      const key = el.getAttribute("data-h");
      const get = HB[key];
      el.textContent = get ? fmt(get(), el.getAttribute("data-f")) : "n/a";
    });
    $$("[data-b]").forEach((el) => {
      const get = BB[el.getAttribute("data-b")];
      el.textContent = get ? get() : "n/a";
    });
  }
  // constants that do not need the data file
  function bindStatic() {
    $$("[data-b]").forEach((el) => {
      const k = el.getAttribute("data-b");
      if (k === "ci" || k === "even" || k === "box_dims" || k === "per100") el.textContent = BB[k]();
    });
  }

  // ------------------------------------------------------------ theme

  function isDark() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t === "dark" || t === "light") return t === "dark";
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }
  function syncThemeButton() {
    const btn = $("#theme-btn");
    const dark = isDark();
    btn.textContent = dark ? "Light" : "Dark";
    btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    const meta2 = document.querySelectorAll('meta[name="theme-color"]');
    const bg = getComputedStyle(document.body).backgroundColor;
    meta2.forEach((m) => { if (document.documentElement.hasAttribute("data-theme")) m.setAttribute("content", bg); });
  }
  function initTheme() {
    const btn = $("#theme-btn");
    btn.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("serveiq-theme", next); } catch (e) { /* storage blocked */ }
      syncThemeButton();
      redraw();
    });
    if (window.matchMedia) {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const onChange = () => { syncThemeButton(); redraw(); };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
    syncThemeButton();
  }

  // ------------------------------------------------------------ color

  function hexRgb(h) {
    const n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgbHex(c) {
    return "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  }
  function lum(c) {
    const a = c.map((v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }
  function contrast(a, b) {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }
  function shareColor(share) {
    const ramp = (isDark() ? RAMP_DARK : RAMP_LIGHT).map(hexRgb);
    const t = Math.max(0, Math.min(1, (isNum(share) ? share : 0) / SHARE_DOMAIN_MAX)) * (ramp.length - 1);
    const i = Math.min(ramp.length - 2, Math.floor(t));
    const f = t - i;
    const c = ramp[i].map((v, k) => v + (ramp[i + 1][k] - v) * f);
    const dark = [11, 11, 11], light = [255, 255, 255];
    const ink = contrast(c, dark) >= contrast(c, light) ? "#0b0b0b" : "#ffffff";
    return { fill: rgbHex(c), ink: ink };
  }
  // color key: gradient bar with three ticks (0, middle, top of the domain)
  function rampSvg() {
    const ramp = isDark() ? RAMP_DARK : RAMP_LIGHT;
    const w = 150, h = 26;
    let s = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" aria-hidden="true"><defs><linearGradient id="court-ramp" x1="0" x2="1" y1="0" y2="0">' +
      ramp.map((c, i) => '<stop offset="' + (i / (ramp.length - 1)) + '" stop-color="' + c + '"/>').join("") + "</linearGradient></defs>" +
      '<rect x="0.5" y="0.5" width="' + (w - 1) + '" height="10" rx="2" fill="url(#court-ramp)" stroke="var(--rule)"/>';
    const ticks = [0, SHARE_DOMAIN_MAX / 2, SHARE_DOMAIN_MAX];
    ticks.forEach((v, i) => {
      const x = (v / SHARE_DOMAIN_MAX) * w;
      const anchor = i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle";
      s += '<text x="' + x + '" y="24" text-anchor="' + anchor + '">' + esc(fPct(v, 0) + (i === ticks.length - 1 ? "+" : "")) + "</text>";
    });
    return s + "</svg>";
  }

  // ------------------------------------------------------------ svg helpers

  function el(tag, attrs, parent) {
    const e = document.createElementNS(SVGNS, tag);
    if (attrs) Object.keys(attrs).forEach((k) => { if (attrs[k] != null) e.setAttribute(k, attrs[k]); });
    if (parent) parent.appendChild(e);
    return e;
  }
  function txt(parent, x, y, s, cls, anchor, extra) {
    const t = el("text", Object.assign({ x: x, y: y, class: cls || null, "text-anchor": anchor || "start" }, extra || {}), parent);
    t.textContent = s;
    return t;
  }
  function newSvg(host, w, h, label, role) {
    host.innerHTML = "";
    const svg = el("svg", { viewBox: "0 0 " + w + " " + h, width: w, height: h, role: role || "img", "aria-label": label, preserveAspectRatio: "xMinYMin meet" });
    host.appendChild(svg);
    bindTips(svg);
    return svg;
  }
  function fitText(t, maxW, minSize) {
    let size = parseFloat(t.style.fontSize);
    let guard = 0;
    while (t.getComputedTextLength() > maxW && size > minSize && guard < 24) {
      size -= 0.5;
      t.style.fontSize = size + "px";
      guard++;
    }
  }
  function niceStep(range, target) {
    const raw = range / target;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const m = raw / pow;
    const step = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10;
    return step * pow;
  }
  function domainOf(values, padFrac, target, clampLo, clampHi) {
    const v = values.filter(isNum);
    let lo = Math.min.apply(null, v), hi = Math.max.apply(null, v);
    if (!v.length) { lo = 0; hi = 1; }
    const pad = Math.max((hi - lo) * padFrac, 0.01);
    lo -= pad; hi += pad;
    if (clampLo != null) lo = Math.max(clampLo, lo);
    if (clampHi != null) hi = Math.min(clampHi, hi);
    const step = niceStep(hi - lo, target || 4);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    const ticks = [];
    for (let x = lo; x <= hi + step / 2; x += step) ticks.push(Math.round(x / step) * step);
    return { lo: lo, hi: hi, ticks: ticks, step: step };
  }
  function barPath(x, yBase, yVal, w, r) {
    const h = Math.abs(yBase - yVal);
    r = Math.min(r, h, w / 2);
    if (h < 0.5) return "M" + x + "," + yBase + "h" + w + "v0h" + (-w) + "z";
    if (yVal < yBase) {
      return "M" + x + "," + yBase + "V" + (yVal + r) + "Q" + x + "," + yVal + " " + (x + r) + "," + yVal +
        "H" + (x + w - r) + "Q" + (x + w) + "," + yVal + " " + (x + w) + "," + (yVal + r) + "V" + yBase + "Z";
    }
    return "M" + x + "," + yBase + "V" + (yVal - r) + "Q" + x + "," + yVal + " " + (x + r) + "," + yVal +
      "H" + (x + w - r) + "Q" + (x + w) + "," + yVal + " " + (x + w) + "," + (yVal - r) + "V" + yBase + "Z";
  }
  function diamond(cx, cy, s) {
    return "M" + cx + "," + (cy - s) + "L" + (cx + s) + "," + cy + "L" + cx + "," + (cy + s) + "L" + (cx - s) + "," + cy + "Z";
  }

  // ------------------------------------------------------------ tooltip

  let tipTimer = null;
  function showTip(text, e) {
    const tip = $("#tip");
    tip.textContent = text;
    tip.classList.add("on");
    placeTip(e);
  }
  function placeTip(e) {
    const tip = $("#tip");
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + w > window.innerWidth - 8) x = Math.max(8, e.clientX - w - 14);
    if (y + h > window.innerHeight - 8) y = Math.max(8, e.clientY - h - 14);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { $("#tip").classList.remove("on"); }
  function bindTips(svg) {
    svg.addEventListener("pointerover", (e) => {
      const t = e.target.closest ? e.target.closest("[data-tip]") : null;
      if (!t) return;
      showTip(t.getAttribute("data-tip"), e);
      if (e.pointerType === "touch") {
        clearTimeout(tipTimer);
        tipTimer = setTimeout(hideTip, 3000);
      }
    });
    svg.addEventListener("pointermove", (e) => {
      if ($("#tip").classList.contains("on")) placeTip(e);
    });
    svg.addEventListener("pointerout", (e) => {
      if (e.pointerType !== "touch") hideTip();
    });
  }

  // ------------------------------------------------------------ tables

  function table(host, caption, cols, rows) {
    let h = "<table>";
    if (caption) h += "<caption>" + esc(caption) + "</caption>";
    h += "<thead><tr>" + cols.map((c, i) => '<th scope="col"' + (c.left || i === 0 ? ' class="l"' : "") + ">" + esc(c.label) + "</th>").join("") + "</tr></thead><tbody>";
    rows.forEach((r) => {
      h += "<tr>" + r.map((v, i) => {
        const c = cols[i];
        const cls = [];
        if (c.left || i === 0) cls.push("l");
        if (c.flag && v) cls.push("yes");
        const tag = i === 0 ? 'th scope="row"' : "td";
        return "<" + tag + (cls.length ? ' class="' + cls.join(" ") + '"' : "") + ">" + esc(v == null ? "" : v) + "</" + (i === 0 ? "th" : "td") + ">";
      }).join("") + "</tr>";
    });
    host.innerHTML = h + "</tbody></table>";
    if (host.classList.contains("table-wrap")) markScrollableTable(host);
  }

  // ------------------------------------------------------------ keys (legends)

  function keyDot(cls) {
    return '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="5" class="' + cls + '"/></svg>';
  }
  function keyDiamond() {
    return '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="' + diamond(7, 7, 5) + '" class="m-league"/></svg>';
  }
  function keySquare(cls) {
    return '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="1" y="1" width="12" height="12" rx="2" class="' + cls + '"/></svg>';
  }
  function keyRing() {
    return '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="4.5" class="m-league"/></svg>';
  }
  function setKey(host, items) {
    host.innerHTML = items.map((it) => '<span class="sw">' + it[0] + "<span>" + esc(it[1]) + "</span></span>").join("");

  }

  // ------------------------------------------------------------ player model

  function playerList(g) { return PLAYERS.filter((p) => p.gender === g); }
  function findPlayer(g, name) {
    for (let i = 0; i < PLAYERS.length; i++) if (PLAYERS[i].gender === g && PLAYERS[i].server === name) return PLAYERS[i];
    return null;
  }
  function model(g, name, serve) {
    const key = g + "|" + name + "|" + serve;
    const raw = DATA.mix.data[key];
    const tr = DATA.tests.data[key];
    if (!raw || !tr) return null;
    const out = {};
    SIDES.forEach((side) => {
      const s = { all: {}, bp: {}, other: {}, test: rowObj(tr[side], TESTF) };
      ["all", "bp", "other"].forEach((sit) => {
        DIRS.forEach((d) => { s[sit][d] = rowObj(raw[side] && raw[side][sit] ? raw[side][sit][d] : null, MIXF); });
      });
      out[side] = s;
    });
    return out;
  }

  // ------------------------------------------------------------ scout: controls

  let FILTER = "";
  function norm(s) { return String(s).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim(); }
  function filteredPlayers() {
    const list = playerList(S.tour);
    const f = norm(FILTER);
    if (!f) return list;
    return list.filter((p) => norm(p.server).indexOf(f) >= 0);
  }
  function fillPlayerSelect() {
    const sel = $("#s-player");
    let list = filteredPlayers();
    const status = $("#s-find-status");
    if (FILTER && !list.length) {
      list = playerList(S.tour);
      status.textContent = "No server among the " + TOUR_PLURAL[S.tour] + " matches that name, so the full list is shown.";
    } else if (FILTER) {
      status.textContent = list.length + (list.length === 1 ? " server matches." : " servers match.");
    } else {
      status.textContent = "";
    }
    sel.innerHTML = list.map((p) =>
      '<option value="' + esc(p.server) + '">' + esc(p.server) + " (" + fInt(p.matches) + " matches)</option>").join("");
    sel.value = S.player;
    return list;
  }
  function onFilter() {
    const list = fillPlayerSelect();
    if (list.length && !list.some((p) => p.server === S.player)) {
      S.player = list[0].server;
      S.zone = null;
      $("#s-player").value = S.player;
      S.at = "scout"; renderScout(); writeHash();
    }
  }
  function setRadio(name, value) {
    $$('input[name="' + name + '"]').forEach((i) => { i.checked = i.value === String(value); });
  }
  function initScoutControls() {
    const find = $("#s-find");
    const onFind = debounce(() => { FILTER = find.value; onFilter(); }, 180);
    find.addEventListener("input", onFind);
    find.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "Enter") { e.preventDefault(); FILTER = find.value; onFilter(); $("#s-player").focus(); }
      if (e.key === "Escape" && find.value) { e.preventDefault(); find.value = ""; FILTER = ""; fillPlayerSelect(); }
    });
    $$('input[name="s-tour"]').forEach((i) => i.addEventListener("change", () => {
      S.tour = i.value;
      S.player = playerList(S.tour)[0].server;
      S.zone = null;
      FILTER = ""; find.value = "";
      fillPlayerSelect();
      S.at = "scout"; renderScout(); writeHash();
    }));
    $("#s-player").addEventListener("change", (e) => {
      S.player = e.target.value; S.zone = null; S.at = "scout"; renderScout(); writeHash();
    });
    $$('input[name="s-serve"]').forEach((i) => i.addEventListener("change", () => {
      S.serve = Number(i.value); S.at = "scout"; renderScout(); writeHash();
    }));
    $$('input[name="s-sit"]').forEach((i) => i.addEventListener("change", () => {
      S.sit = i.value; S.at = "scout"; renderScout(); writeHash();
    }));
  }

  // ------------------------------------------------------------ scout: render

  let M = null; // current player model

  function favoriteZone() {
    const t = M.deuce.test;
    return { side: "deuce", dir: t.top_dir && DIRS.indexOf(t.top_dir) >= 0 ? t.top_dir : "wide" };
  }

  function renderScout() {
    const p = findPlayer(S.tour, S.player);
    M = model(S.tour, S.player, S.serve);
    if (!p || !M) return;
    if (!S.zone) S.zone = favoriteZone();
    const serveWord = SERVE_LABEL[S.serve];

    $("#p-name").textContent = p.server;
    const known = SIDES.reduce((a, s) => a + (M[s].test.n || 0), 0);
    const facts = [
      p.hand === "L" ? "Left-handed" : "Right-handed",
      fInt(p.matches) + " charted matches",
      p.first + " to " + p.last,
      fInt(known) + " " + serveWord + " serves with a known direction, tiebreaks excluded",
    ];
    $("#p-facts").innerHTML = facts.map((f) => "<li>" + esc(f) + "</li>").join("");

    $("#court-sub").textContent = serveWord + " serves, " + (S.sit === "bp" ? "break points only" : "all points") +
      ". Share of serves to each direction on each side.";

    // break-point warning
    const warnN = meta.thresholds.bp_warn_n;
    const thin = SIDES.filter((s) => (M[s].test.n_bp || 0) < warnN);
    const warn = $("#bp-warn");
    if (S.sit === "bp" && thin.length) {
      const parts = thin.map((s) => fInt(M[s].test.n_bp || 0) + " break-point serves on the " + s + " side");
      warn.innerHTML = '<p class="warn" role="note"><strong>Small sample.</strong> Only ' + esc(parts.join(" and ")) +
        " (fewer than " + esc(fInt(warnN)) + "). Read these shares as rough; each zone's " +
        esc(fPct(CI_LEVEL, 0)) + " confidence interval is in the detail panel.</p>";
    } else {
      warn.innerHTML = "";
    }

    // key
    const k = $("#court-key");
    k.innerHTML = '<span class="ramp">' + rampSvg() + "<span>Share of serves to the zone</span></span>" +
      "<span>" + (S.sit === "bp"
        ? "Big figure: share on break points. Small figures: share on other points, and league, all points (same opponents)."
        : "Big figure: this player. Small figure: league, same opponents.") + "</span>";

    drawCourt();
    renderDetail();
    renderNotes();
    renderWinChart();
    renderBpTable();
    renderFullTable();
    renderCourtTable();
  }

  function cellFor(side, dir) {
    return S.sit === "bp" ? M[side].bp[dir] : M[side].all[dir];
  }

  function drawCourt() {
    const host = $("#court");
    if (!M) return;
    const W = Math.max(260, Math.floor(host.clientWidth || 400));
    const mx = 0.34;
    const k = W / (SINGLES_W + 2 * mx);
    const top = 4, below = 34;
    const strip = Math.max(BACK_STRIP * k, 22);
    const Hh = Math.round(top + strip + BOX_D * k + below);
    const bp = S.sit === "bp";
    const minN = meta.thresholds.min_dir_n;
    const svg = newSvg(host, W, Hh, "Schematic service boxes seen from behind the server, showing the share of " + SERVE_LABEL[S.serve] +
      " serves to wide, body and T on the deuce and ad sides" + (bp ? " on break points" : "") + ". Each zone is a button that shows its details.", "group");
    const x0 = mx * k, xr = x0 + SINGLES_W * k;
    const yBase = top, yServ = top + strip, yNet = yServ + BOX_D * k;
    const zw = (BOX_W * k) / 3, zh = BOX_D * k;

    // a thin strip of backcourt behind the service line; the rest of the backcourt is cut
    el("rect", { x: x0, y: yBase, width: SINGLES_W * k, height: yServ - yBase, class: "backcourt" }, svg);
    el("line", { x1: x0, y1: yBase, x2: x0, y2: yServ, class: "faint" }, svg);
    el("line", { x1: xr, y1: yBase, x2: xr, y2: yServ, class: "faint" }, svg);

    const order = { deuce: ["wide", "body", "T"], ad: ["T", "body", "wide"] };
    const sel = S.zone;
    SIDES.forEach((side) => {
      const bx = side === "deuce" ? x0 : x0 + BOX_W * k;
      txt(svg, bx + (BOX_W * k) / 2, yServ - Math.max(6, (strip - 11) / 2), side === "deuce" ? "Deuce box" : "Ad box", "court-lab", "middle");
      order[side].forEach((dir, i) => {
        const c = cellFor(side, dir);
        const zx = bx + i * zw;
        const col2 = shareColor(c.share);
        const pressed = sel && sel.side === side && sel.dir === dir;
        const oth = M[side].other[dir];
        const g = el("g", {
          class: "zone", tabindex: 0, role: "button", "aria-pressed": pressed ? "true" : "false",
          "aria-label": SIDE_LABEL[side] + ", " + dir + ": " + fPct(c.share, 1) + " of " + (bp ? "break-point " : "") + "serves" +
            (c.share == null ? "" : ", " + fPct(CI_LEVEL, 0) + " CI " + ciText(c.share_lo, c.share_hi, 1)) +
            (bp ? ", other points " + fPct(oth.share, 1) + ", league, all points " : ", league ") + fPct(c.league_share, 1) +
            ", " + fInt(c.n) + " serves" + (fewServes(c) ? " (few serves, rough interval)" : ""),
          "data-side": side, "data-dir": dir,
        }, svg);
        el("rect", { x: zx, y: yServ, width: zw, height: zh, class: "fill", style: "fill:" + col2.fill }, g);
        el("rect", { x: zx + 2, y: yServ + 2, width: zw - 4, height: zh - 4, class: "ring" }, g);
        el("rect", { x: zx + 4.75, y: yServ + 4.75, width: zw - 9.5, height: zh - 9.5, class: "ring-in" }, g);
        const fs = Math.max(15, Math.min(28, zw * 0.34));
        const sx = zx + zw / 2;
        const tS = txt(g, sx, yServ + zh * 0.36, fPct(c.share, 0), "zt-share", "middle", { style: "font-size:" + fs + "px;fill:" + col2.ink });
        fitText(tS, zw - 6, 11);
        const ds = Math.max(11, Math.min(12.5, zw / 6));
        const tD = txt(g, sx, yServ + zh * 0.36 + ds + 8, dir, "zt-dir", "middle", { style: "font-size:" + ds + "px;fill:" + col2.ink });
        fitText(tD, zw - 6, 11);
        // small figures never go below 11px; a word that does not fit the zone at that size is dropped (the key explains the order)
        const ss = Math.max(11, Math.min(12, zw / 5.5));
        const lines = bp
          ? [["other", true], [fPct(oth.share, 0), false], ["league", true], [fPct(c.league_share, 0), false]]
          : [["league", true], [fPct(c.league_share, 0), false]];
        const y0 = yServ + zh * (bp ? 0.62 : 0.72);
        lines.forEach((ln, j) => {
          const t = txt(g, sx, y0 + j * (ss + 3) + (bp && j >= 2 ? 5 : 0), ln[0], "zt-small", "middle", { style: "font-size:" + ss + "px;fill:" + col2.ink });
          if (ln[1] && t.getComputedTextLength() > zw - 6) t.remove();
        });
        const select = () => { S.zone = { side: side, dir: dir }; markZones(); renderDetail(); };
        g.addEventListener("click", select);
        g.addEventListener("focus", select);
        g.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") select(); });
        g.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); }
        });
      });
    });

    // strong lines on top: service boxes, center service line, sidelines, net
    el("rect", { x: x0, y: yServ, width: SINGLES_W * k, height: zh, class: "line" }, svg);
    el("line", { x1: x0 + BOX_W * k, y1: yServ, x2: x0 + BOX_W * k, y2: yNet, class: "line" }, svg);
    el("line", { x1: x0 - 0.25 * k, y1: yNet, x2: xr + 0.25 * k, y2: yNet, class: "net" }, svg);
    el("circle", { cx: x0 - 0.25 * k, cy: yNet, r: 3.5, class: "post" }, svg);
    el("circle", { cx: xr + 0.25 * k, cy: yNet, r: 3.5, class: "post" }, svg);
    txt(svg, (x0 + xr) / 2, yNet + 22, "Net. The server stands on the near side.", "court-lab", "middle");
  }

  function markZones() {
    $$("#court .zone").forEach((g) => {
      const on = S.zone && g.getAttribute("data-side") === S.zone.side && g.getAttribute("data-dir") === S.zone.dir;
      g.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function renderDetail() {
    const z = S.zone;
    if (!M || !z) return;
    const side = M[z.side];
    const all = side.all[z.dir], bpc = side.bp[z.dir], oth = side.other[z.dir];
    const ci = fPct(CI_LEVEL, 0) + " CI ";
    const sw = SERVE_LABEL[S.serve];
    let items;
    if (S.sit === "bp") {
      const nbp = DIRS.reduce((a, d) => a + (side.bp[d].n || 0), 0);
      const few = fewServes(bpc);
      items = [
        ["Share on break points", fPct(bpc.share, 1), bpc.share == null ? "no break-point serves" : ci + ciText(bpc.share_lo, bpc.share_hi, 1)],
        ["Share on other points", fPct(oth.share, 1), fInt(oth.n) + " serves"],
        ["League, all points", fPct(bpc.league_share, 1), "same opponents; not split by break point"],
        ["Break-point serves", fInt(bpc.n), "of " + fInt(nbp) + " on this side"],
      ];
      if (few && bpc.share != null) items.push(["Small sample", "Few serves", "Fewer than " + fInt(meta.thresholds.min_dir_n) + " break-point serves here, so the share and its interval are rough.", "few"]);
    } else {
      const few = fewServes(all);
      items = [
        ["Share of serves", fPct(all.share, 1), all.share == null ? "" : ci + ciText(all.share_lo, all.share_hi, 1)],
        ["League, same opponents", fPct(all.league_share, 1), "weighted by this player's returners"],
        ["Serves", fInt(all.n), "of " + fInt(side.test.n) + " on this side"],
        ["Points won after it", fPct(all.win, 1), all.win == null ? "no serves" : ci + ciText(all.win_lo, all.win_hi, 1) + "; league " + fPct(all.league_win, 1)],
      ];
      if (few && all.share != null) items.push(["Small sample", "Few serves", "Fewer than " + fInt(meta.thresholds.min_dir_n) + " serves here, so these rates and intervals are rough.", "few"]);
    }
    $("#detail").innerHTML =
      '<p class="label">Selected zone</p><p class="d-title">' + esc(SIDE_LABEL[z.side] + ", " + z.dir + ", " + sw + " serve" + (S.sit === "bp" ? ", break points" : "")) + "</p>" +
      "<dl>" + items.map((it) => "<div><dt>" + esc(it[0]) + "</dt><dd>" + esc(it[1]) + (it[2] ? "<small" + (it[3] ? ' class="' + it[3] + '"' : "") + ">" + esc(it[2]) + "</small>" : "") + "</dd></div>").join("") + "</dl>" +
      '<p class="hint">Select another zone with the mouse, a tap or the Tab key.</p>';
  }

  function renderNotes() {
    const q = meta.thresholds.q;
    const minN = meta.thresholds.min_dir_n;
    const tour = TOUR_PLURAL[S.tour];
    const bp = S.sit === "bp";
    const allTag = bp ? " (all points)" : "";
    const allIn = bp ? "all points, " : "";
    $("#notes-sub").textContent = bp
      ? "This serve. The break-point line compares break points with other points; the lines marked (all points) use every point. Tests compare serves within the same match."
      : "All points, this serve. Tests compare serves within the same match.";
    const html = SIDES.map((sideName) => {
      const side = M[sideName];
      const t = side.test;
      const parts = [];
      // favorite target
      const ranked = DIRS.map((d) => [d, side.all[d].share]).filter((x) => isNum(x[1])).sort((a, b) => b[1] - a[1]);
      if (ranked.length >= 2 && ranked[0][1] - ranked[1][1] < NO_CLEAR_FAVORITE) {
        parts.push("No clear favorite target" + allTag + ": " + esc(ranked.map((x) => x[0] + " " + fPct(x[1], 1)).join(", ")) + ".");
      } else if (t.top_dir) {
        const c = side.all[t.top_dir];
        parts.push("Favorite target" + allTag + ": <strong>" + esc(t.top_dir) + "</strong>, " + esc(fPct(t.top_share, 1)) + " of serves (" +
          esc(fPct(CI_LEVEL, 0)) + " CI " + esc(ciText(c.share_lo, c.share_hi, 1)) + "), against " + esc(fPct(c.league_share, 1)) +
          " for the league facing the same opponents.");
      }
      parts.push("Favorite-target share" + allTag + " higher than " + esc(fPct(t.fav_pct, 0)) + " of listed " + tour + " on this side.");
      // mix test
      if (isNum(t.q_mix)) {
        parts.push(t.q_mix < q
          ? '<span class="flag">Mix differs</span>Direction mix differs from the league for the same opponents (' + allIn + esc(pEq("q", t.q_mix)) + ")."
          : "Direction mix does not differ significantly from the league for the same opponents (" + allIn + esc(pEq("q", t.q_mix)) + ").");
      }
      // break-point shifts
      const shifts = DIRS.filter((d) => isNum(t["bp_" + d + "_q"]) && t["bp_" + d + "_q"] < q).map((d) =>
        d + " " + fPct(side.other[d].share, 1) + " on other points, " + fPct(side.bp[d].share, 1) + " on break points (" + pEq("q", t["bp_" + d + "_q"]) + ")");
      if (shifts.length) {
        parts.push('<span class="flag">Break points</span>' + esc(shifts.join("; ")) + ".");
      } else {
        parts.push("No direction shifts significantly on break points (" + esc(fInt(t.n_bp)) + " break-point serves)." +
          ((t.n_bp || 0) < meta.thresholds.bp_warn_n ? " With so few break-point serves, only a large shift could be detected." : ""));
      }
      // win test
      if (!isNum(t.q_win)) {
        parts.push("Not enough serves to test whether win rates differ by direction" + allTag + ".");
      } else if (t.q_win < q) {
        const used = DIRS.filter((d) => (side.all[d].n || 0) >= minN);
        parts.push('<span class="flag">Win rates</span>Win rates differ by direction (' + allIn + esc(pEq("p", t.p_win)) + ", " + esc(pEq("q", t.q_win)) +
          "): points won after " + esc(used.map((d) => d + " " + fPct(side.all[d].win, 1)).join(", ")) + ".");
        parts.push("Sending every serve to " + esc(t.best_dir) + " could be worth at most " + esc(fNum(t.gain_pp, 1)) +
          " points per 100 serves" + allTag + ". This is an upper bound: returners would adjust, and it picks the best of several noisy rates.");
      } else {
        parts.push("Win rates do not differ significantly by direction (" + allIn + esc(pEq("p", t.p_win)) + ", " + esc(pEq("q", t.q_win)) + ").");
      }
      return '<div class="note-card"><h4>' + esc(SIDE_LABEL[sideName]) + '</h4><p class="meta">' + esc(fInt(t.n) + " serves in " + fInt(t.matches) + " matches") + "</p>" +
        parts.map((x) => "<p>" + x + "</p>").join("") + "</div>";
    }).join("");
    $("#notes").innerHTML = html;
  }

  // dot plot with whiskers, one panel per side
  function drawDotPanels(host, panels, opt) {
    const W = Math.max(260, Math.floor(host.clientWidth || 600));
    const stacked = W < 600;
    const gap = 32;
    const pw = stacked ? W : (W - gap) / 2;
    const ph = opt.height || 250;
    const Hh = stacked ? ph * panels.length + gap * (panels.length - 1) : ph;
    const svg = newSvg(host, W, Hh, opt.label);
    const vals = [];
    panels.forEach((p) => p.items.forEach((it) => { vals.push(it.lo, it.hi, it.v, it.ref); }));
    const dom = domainOf(vals, 0.08, 4, 0, 1);
    panels.forEach((p, pi) => {
      const ox = stacked ? 0 : pi * (pw + gap);
      const oy = stacked ? pi * (ph + gap) : 0;
      const ml = 42, mr = 8, mt = 30, mb = 28;
      const iw = pw - ml - mr, ih = ph - mt - mb;
      const y = (v) => oy + mt + ih - ((v - dom.lo) / (dom.hi - dom.lo)) * ih;
      const g = el("g", {}, svg);
      txt(g, ox, oy + 14, p.title, "panel-title");
      const grid = el("g", { class: "grid" }, g);
      dom.ticks.forEach((tv) => {
        el("line", { x1: ox + ml, x2: ox + pw - mr, y1: y(tv), y2: y(tv) }, grid);
        txt(g, ox + ml - 8, y(tv) + 4, fPct(tv, 0), null, "end");
      });
      el("line", { x1: ox + ml, x2: ox + pw - mr, y1: oy + mt + ih, y2: oy + mt + ih, class: "baseline" }, g);
      const band = iw / p.items.length;
      p.items.forEach((it, i) => {
        const cx = ox + ml + band * (i + 0.5);
        txt(g, cx, oy + ph - 8, it.cat, "cat", "middle");
        const off = opt.ref ? Math.min(14, band * 0.16) : 0;
        const px = cx - off;
        if (!isNum(it.v)) {
          txt(g, cx, oy + mt + ih / 2, "no serves", "val-muted", "middle");
        } else {
          const wc = it.muted ? "w-muted" : "w-player";
          if (isNum(it.lo) && isNum(it.hi)) {
            el("line", { x1: px, x2: px, y1: y(it.lo), y2: y(it.hi), class: wc }, g);
            el("line", { x1: px - 4, x2: px + 4, y1: y(it.lo), y2: y(it.lo), class: wc }, g);
            el("line", { x1: px - 4, x2: px + 4, y1: y(it.hi), y2: y(it.hi), class: wc }, g);
          }
          el("circle", { cx: px, cy: y(it.v), r: 5, class: it.muted ? "m-player-muted" : "m-player" }, g);
          // value label left of the dot when it clears the axis labels, else centered above the interval
          const label = fPct(it.v, 1);
          const fitsLeft = px - 10 - label.length * 7 > ox + ml + 2;
          const ly = fitsLeft ? y(it.v) + 4 : (isNum(it.hi) ? y(it.hi) : y(it.v) - 4) - 8;
          txt(g, fitsLeft ? px - 10 : px, ly, label, it.muted ? "val-muted" : "val", fitsLeft ? "end" : "middle");
        }
        if (opt.ref && isNum(it.ref)) {
          el("path", { d: diamond(cx + off, y(it.ref), 5.5), class: "m-league" }, g);
        }
        el("rect", { x: cx - band / 2, y: oy + mt, width: band, height: ih, class: "hit", "data-tip": it.tip }, g);
      });
    });
  }

  function renderWinChart() {
    const minN = meta.thresholds.min_dir_n;
    const q = meta.thresholds.q;
    const panels = SIDES.map((side) => ({
      title: SIDE_LABEL[side],
      items: DIRS.map((d) => {
        const c = M[side].all[d];
        const muted = (c.n || 0) < minN;
        return {
          cat: d, v: c.win, lo: c.win_lo, hi: c.win_hi, ref: c.league_win, muted: muted,
          tip: SIDE_LABEL[side] + ", " + d + "\nThis player: " + fPct(c.win, 1) + " won" +
            (isNum(c.win) ? " (" + fPct(CI_LEVEL, 0) + " CI " + ciText(c.win_lo, c.win_hi, 1) + ")" : "") +
            "\nLeague, same opponents: " + fPct(c.league_win, 1) + "\n" + fInt(c.n) + " serves" +
            (muted ? "\nFewer than " + fInt(minN) + " serves: left out of the win test" : ""),
        };
      }),
    }));
    setKey($("#win-key"), [[keyDot("m-player"), "This player"], [keyDiamond(), "League, same opponents"], [keyDot("m-player-muted"), "Fewer than " + fInt(minN) + " serves (not tested)"]]);
    drawDotPanels($("#win-chart"), panels, {
      ref: true, height: 260,
      label: "Points won by the server after each serve direction, " + SERVE_LABEL[S.serve] + " serves, deuce and ad side, with confidence intervals and league reference",
    });
    const verdicts = SIDES.map((side) => {
      const t = M[side].test;
      if (!isNum(t.p_win)) return SIDE_LABEL[side] + ": not enough serves to test.";
      return SIDE_LABEL[side] + ": " + (t.q_win < q
        ? "win rates differ by direction (" + pEq("p", t.p_win) + ", " + pEq("q", t.q_win) + ")."
        : "win rates do not differ significantly by direction (" + pEq("p", t.p_win) + ", " + pEq("q", t.q_win) + ").");
    });
    $("#win-caption").textContent = verdicts.join(" ") +
      " If the server mixed optimally, every direction would win the same share of points. Adjusted for the " + fInt(meta.tests.win) + " win tests across all players, sides and serves (q)." +
      (S.serve === 1 ? " A 1st serve's win counts the whole point, including points where the 1st serve missed." : "");
  }

  function renderBpTable() {
    const q = meta.thresholds.q;
    const rows = [];
    SIDES.forEach((side) => {
      const t = M[side].test;
      DIRS.forEach((d) => {
        const qv = t["bp_" + d + "_q"];
        rows.push([
          SIDE_LABEL[side], d, fPct(M[side].other[d].share, 1), fPct(M[side].bp[d].share, 1), fInt(M[side].bp[d].n),
          isNum(t["bp_" + d]) ? fSigned(100 * t["bp_" + d], 1) : "n/a", fP(qv), isNum(qv) && qv < q ? "yes" : "",
        ]);
      });
    });
    table($("#bp-table"), SERVE_LABEL[S.serve] + " serves. Change is in percentage points.", [
      { label: "Side" }, { label: "Direction", left: true }, { label: "Other points" }, { label: "Break points" },
      { label: "Break-point serves" }, { label: "Change" }, { label: "q" }, { label: "Flag (q < " + q + ")", flag: true },
    ], rows);
  }

  function renderFullTable() {
    const rows = [];
    SIDES.forEach((side) => {
      ["all", "bp"].forEach((sit) => {
        DIRS.forEach((d) => {
          const c = M[side][sit][d];
          rows.push([
            SIDE_LABEL[side], sit === "all" ? "All points" : "Break points", d, fInt(c.n), fPct(c.share, 1),
            ciCell(c),
            fPct(c.league_share, 1) + (sit === "bp" ? " (league, all points)" : ""),
            fPct(c.win, 1), winCiCell(c),
            fPct(c.league_win, 1) + (sit === "bp" ? " (league, all points)" : ""),
          ]);
        });
      });
    });
    const ci = fPct(CI_LEVEL, 0) + " CI";
    table($("#full-table"), S.player + ", " + SERVE_LABEL[S.serve] + " serves.", [
      { label: "Side" }, { label: "Points", left: true }, { label: "Direction", left: true }, { label: "Serves" }, { label: "Share" },
      { label: "Share " + ci }, { label: "League share" }, { label: "Won" }, { label: "Won " + ci }, { label: "League won" },
    ], rows);
  }

  function renderCourtTable() {
    const bp = S.sit === "bp";
    const rows = [];
    SIDES.forEach((side) => DIRS.forEach((d) => {
      const c = cellFor(side, d);
      const r = [SIDE_LABEL[side], d, fPct(c.share, 1), ciCell(c)];
      if (bp) r.push(fPct(M[side].other[d].share, 1));
      r.push(fPct(c.league_share, 1), fInt(c.n));
      rows.push(r);
    }));
    const cols = [{ label: "Side" }, { label: "Direction", left: true }, { label: bp ? "Break points" : "Share" }, { label: fPct(CI_LEVEL, 0) + " CI" }];
    if (bp) cols.push({ label: "Other points" });
    cols.push({ label: bp ? "League, all points" : "League, same opponents" }, { label: "Serves" });
    table($("#court-table"), S.player + ", " + SERVE_LABEL[S.serve] + " serves, " + (bp ? "break points" : "all points") + ".", cols, rows);
  }

  // ------------------------------------------------------------ league

  function initLeagueControls() {
    $$('input[name="l-tour"]').forEach((i) => i.addEventListener("change", () => { S.ltour = i.value; S.at = "league"; renderLeague(); writeHash(); }));
    $("#l-matchup").addEventListener("change", (e) => { S.matchup = e.target.value; S.at = "league"; renderLeague(); writeHash(); });
    $$('input[name="l-serve"]').forEach((i) => i.addEventListener("change", () => { S.lserve = Number(i.value); S.at = "league"; renderLeague(); writeHash(); }));
  }

  function within(g, s, question, fe) {
    for (let i = 0; i < WITHIN.length; i++) {
      const r = WITHIN[i];
      if (r.gender === g && r.serve_num === s && r.question === question && r.fixed_effect === fe) return r;
    }
    return null;
  }
  function wText(r, d) {
    if (!r) return "n/a";
    return fSigned(r.coef_pp, d) + " points (" + fPct(CI_LEVEL, 0) + " CI " + fNum(r.lo_pp, d) + " to " + fNum(r.hi_pp, d) + ")";
  }

  function drawBarPanels(host, panels, opt) {
    const W = Math.max(260, Math.floor(host.clientWidth || 600));
    const stacked = opt.stack != null ? opt.stack : W < 600;
    const gap = 28;
    const pw = stacked ? W : (W - gap) / panels.length;
    const ph = opt.height || 240;
    const Hh = stacked ? ph * panels.length + gap * (panels.length - 1) : ph;
    const svg = newSvg(host, W, Hh, opt.label);
    const dom = opt.domain;
    panels.forEach((p, pi) => {
      const ox = stacked ? 0 : pi * (pw + gap);
      const oy = stacked ? pi * (ph + gap) : 0;
      const ml = 42, mr = 6, mt = p.title ? 30 : 12, mb = 28;
      const iw = pw - ml - mr, ih = ph - mt - mb;
      const y = (v) => oy + mt + ih - ((v - dom.lo) / (dom.hi - dom.lo)) * ih;
      const g = el("g", {}, svg);
      if (p.title) txt(g, ox, oy + 14, p.title, "panel-title");
      const grid = el("g", { class: "grid" }, g);
      dom.ticks.forEach((tv) => {
        el("line", { x1: ox + ml, x2: ox + pw - mr, y1: y(tv), y2: y(tv) }, grid);
        txt(g, ox + ml - 8, y(tv) + 4, opt.tick(tv), null, "end");
      });
      const y0 = y(Math.max(dom.lo, Math.min(dom.hi, 0)));
      el("line", { x1: ox + ml, x2: ox + pw - mr, y1: y0, y2: y0, class: dom.lo < 0 ? "zero" : "baseline" }, g);
      const band = iw / p.groups.length;
      p.groups.forEach((grp, i) => {
        const cx = ox + ml + band * (i + 0.5);
        txt(g, cx, oy + ph - 8, grp.cat, "cat", "middle");
        const nb = grp.bars.length;
        const bw = Math.min(24, (band * 0.7 - 2 * (nb - 1)) / nb);
        const total = nb * bw + 2 * (nb - 1);
        grp.bars.forEach((b, j) => {
          const bx = cx - total / 2 + j * (bw + 2);
          if (!isNum(b.v)) {
            txt(g, bx + bw / 2, y0 - 6, "n/a", "val-muted", "middle");
            return;
          }
          el("path", { d: barPath(bx, y0, y(b.v), bw, 4), class: b.cls }, g);
          if (isNum(b.lo) && isNum(b.hi)) {
            const mxp = bx + bw / 2;
            el("line", { x1: mxp, x2: mxp, y1: y(b.lo), y2: y(b.hi), class: "ci" }, g);
            el("line", { x1: mxp - 3, x2: mxp + 3, y1: y(b.lo), y2: y(b.lo), class: "ci" }, g);
            el("line", { x1: mxp - 3, x2: mxp + 3, y1: y(b.hi), y2: y(b.hi), class: "ci" }, g);
          }
          if (b.label) {
            const top = b.v >= 0 ? Math.min(y(b.v), isNum(b.hi) ? y(b.hi) : y(b.v)) - 6 : Math.max(y(b.v), isNum(b.lo) ? y(b.lo) : y(b.v)) + 14;
            txt(g, bx + bw / 2, top, b.label, "val", "middle");
          }
          el("rect", { x: bx - 1, y: oy + mt, width: bw + 2, height: ih, class: "hit", "data-tip": b.tip }, g);
        });
      });
    });
  }

  function drawSerial(host, rows, label) {
    const W = Math.max(260, Math.floor(host.clientWidth || 500));
    const rowH = 64, mt = 14, mb = 30, ml = 76, mr = 16;
    const Hh = mt + rows.length * rowH + mb;
    const svg = newSvg(host, W, Hh, label);
    const vals = [];
    rows.forEach((r) => vals.push(r.actual, r.random));
    const dom = domainOf(vals, 0.25, 4, 0, 1);
    const x = (v) => ml + ((v - dom.lo) / (dom.hi - dom.lo)) * (W - ml - mr);
    const grid = el("g", { class: "grid" }, svg);
    dom.ticks.forEach((tv) => {
      el("line", { x1: x(tv), x2: x(tv), y1: mt, y2: mt + rows.length * rowH }, grid);
      txt(svg, x(tv), Hh - 10, fPct(tv, 0), null, "middle");
    });
    rows.forEach((r, i) => {
      const cy = mt + i * rowH + rowH / 2;
      txt(svg, 0, cy + 4, r.label, "cat");
      el("line", { x1: x(r.random), x2: x(r.actual), y1: cy, y2: cy, class: "dumb" }, svg);
      el("circle", { cx: x(r.random), cy: cy, r: 5, class: "m-league" }, svg);
      el("circle", { cx: x(r.actual), cy: cy, r: 5.5, class: "m-player" }, svg);
      const leftIsActual = r.actual <= r.random;
      txt(svg, x(r.actual) + (leftIsActual ? -10 : 10), cy - 10, fPct(r.actual, 1), "val", leftIsActual ? "end" : "start");
      txt(svg, x(r.random) + (leftIsActual ? 10 : -10), cy - 10, fPct(r.random, 1), "val-muted", leftIsActual ? "start" : "end");
      el("rect", { x: ml, y: cy - rowH / 2, width: W - ml - mr, height: rowH, class: "hit", "data-tip": r.tip }, svg);
    });
  }

  function renderLeague() {
    const g = S.ltour, mu = S.matchup, s = S.lserve;
    const ci = fPct(CI_LEVEL, 0);
    const sw = SERVE_LABEL[s];
    const rows = LDIR.filter((r) => r.gender === g && r.matchup === mu && r.serve_num === s);
    const get = (side, bp, d) => {
      for (let i = 0; i < rows.length; i++) if (rows[i].side === side && rows[i].is_break_point === bp && rows[i].direction === d) return rows[i];
      return null;
    };
    const anyLow = rows.some((r) => r.low_n);

    // (a) win by direction, non-break points
    drawDotPanels($("#la-chart"), SIDES.map((side) => ({
      title: SIDE_LABEL[side],
      items: DIRS.map((d) => {
        const r = get(side, false, d) || {};
        return {
          cat: d, v: r.win, lo: r.win_lo, hi: r.win_hi, muted: !!r.low_n,
          tip: SIDE_LABEL[side] + ", " + d + "\n" + fPct(r.win, 1) + " won (" + ci + " CI " + ciText(r.win_lo, r.win_hi, 1) + ")\nUsed on " +
            fPct(r.share, 1) + " of serves\n" + fInt(r.n) + " serves",
        };
      }),
    })), { height: 240, label: "League points won by direction, non-break points, " + MATCHUP[mu] + ", " + sw + " serves" });
    const wb = within(g, s, "win body minus win wide", "server x side x matchup x break point");
    const wt = within(g, s, "win T minus win wide", "server x side x matchup x break point");
    const ib = within(g, s, "serve in body minus serve in wide", "server x side x matchup x break point");
    // the lead rests on the same-server estimates (with CIs), not on which point estimate is highest
    const sig = (r) => !!r && isNum(r.lo_pp) && isNum(r.hi_pp) && (r.lo_pp > 0 || r.hi_pp < 0);
    const laLead = (wb && wt)
      ? "For the same server (all matchups), " +
        (sig(wb) ? (wb.coef_pp < 0 ? "body serves won fewer points than wide serves" : "body serves won more points than wide serves")
          : "body and wide serves won similar shares of points") +
        (sig(wt) ? (wt.coef_pp < 0 ? ", and T serves won fewer than wide serves. " : ", and T serves won more than wide serves. ")
          : ", and T and wide serves won about the same share. ")
      : "";
    $("#la-caption").textContent = laLead + "Filter: " + MATCHUP[mu] + ", " + sw + " serves, non-break points. Points won by the server with " + ci +
      " confidence intervals" + (anyLow ? "; gray marks are thin samples" : "") + ". Same server and side (all matchups, " + TOUR_PLURAL[g] +
      "): points won, body minus wide, " + wText(wb, 2) + "; T minus wide, " + wText(wt, 2) + ". Serve-in rate, body minus wide, " + wText(ib, 2) + ".";

    // (b) share by direction, other points vs break points
    setKey($("#lb-key"), [[keySquare("b-ref"), "Other points"], [keySquare("b-1"), "Break points"]]);
    const shareVals = [];
    rows.forEach((r) => shareVals.push(r.share_hi, r.share));
    const sd = domainOf(shareVals.concat([0]), 0.04, 4, 0, 1);
    drawBarPanels($("#lb-chart"), SIDES.map((side) => ({
      title: SIDE_LABEL[side],
      groups: DIRS.map((d) => ({
        cat: d,
        bars: [false, true].map((bp) => {
          const r = get(side, bp, d) || {};
          return {
            v: r.share, lo: r.share_lo, hi: r.share_hi, cls: bp ? "b-1" : "b-ref",
            tip: SIDE_LABEL[side] + ", " + d + ", " + (bp ? "break points" : "other points") + "\n" + fPct(r.share, 1) + " of serves (" + ci + " CI " +
              ciText(r.share_lo, r.share_hi, 1) + ")\n" + fInt(r.n) + " serves" + (r.low_n ? "\nThin sample" : ""),
          };
        }),
      })),
    })), { domain: { lo: 0, hi: sd.hi, ticks: sd.ticks.filter((t) => t >= 0) }, tick: (v) => fPct(v, 0), height: 240,
      label: "League share of serves by direction on break points and other points, " + MATCHUP[mu] + ", " + sw + " serves" });
    const bw2 = within(g, s, "break_point: change in wide share", "match x server x side");
    const bb2 = within(g, s, "break_point: change in body share", "match x server x side");
    const dirWord = (r, more, fewer) => !r ? null : r.lo_pp > 0 ? more : r.hi_pp < 0 ? fewer : null;
    const wWord = dirWord(bw2, "more serves wide", "fewer serves wide");
    const bWord = dirWord(bb2, "more serves to the body", "fewer serves to the body");
    const shifts = [wWord, bWord].filter(Boolean);
    const lbLead = shifts.length
      ? "On break points the same servers send " + shifts.join(" and ") + " than on other points. "
      : "On break points the same servers keep about the same direction mix as on other points. ";
    $("#lb-caption").textContent = lbLead + "Filter: " + MATCHUP[mu] + ", " + sw + " serves. Share of serves with " + ci + " confidence intervals" +
      (anyLow ? "; some cells are thin samples" : "") + ". Same server, same match, same side (all matchups, " + TOUR_PLURAL[g] +
      "): on break points the wide share changes by " + wText(bw2, 2) + " and the body share by " + wText(bb2, 2) + ".";

    // (c) split-half stability, selected tour, 1st serves
    setKey($("#lc-key"), [[keySquare("b-1"), "Favorite-target share"], [keySquare("b-2"), "T-minus-wide win edge"]]);
    const st = STAB.filter((r) => r.gender === g);
    const stGet = (side, m) => { for (let i = 0; i < st.length; i++) if (st[i].side === side && st[i].metric === m) return st[i]; return null; };
    const rv = st.map((r) => r.corr);
    const lo = Math.min(0, Math.min.apply(null, rv));
    const cdom = { lo: lo < 0 ? -0.25 : 0, hi: 1, ticks: lo < 0 ? [-0.25, 0, 0.25, 0.5, 0.75, 1] : [0, 0.25, 0.5, 0.75, 1] };
    const nPlayers = st.length ? st[0].players : null;
    drawBarPanels($("#lc-chart"), [{
      title: null,
      groups: SIDES.map((side) => ({
        cat: SIDE_LABEL[side],
        bars: [["guess", "b-1", "Favorite-target share"], ["edge", "b-2", "T-minus-wide win edge"]].map((m) => {
          const r = stGet(side, m[0]) || {};
          return { v: r.corr, cls: m[1], label: fNum(r.corr, 2), tip: SIDE_LABEL[side] + "\n" + m[2] + "\nr = " + fNum(r.corr, 2) + " across " + fInt(r.players) + " players" };
        }),
      })),
    }], { domain: cdom, tick: (v) => fNum(v, 2), height: 240, stack: false,
      label: "Split-half correlation of favorite-target share and T-minus-wide win edge, " + TOUR_PLURAL[g] + ", 1st serves" });
    const fr = (side, m) => { const r = stGet(side, m); return r ? fNum(r.corr, 2) : "n/a"; };
    const favs = st.filter((r) => r.metric === "guess").map((r) => r.corr).filter(isNum);
    const edges = st.filter((r) => r.metric === "edge").map((r) => r.corr).filter(isNum);
    const sep = favs.length && edges.length && Math.min.apply(null, favs) > Math.max.apply(null, edges);
    const lcLead = sep
      ? "The favorite-target share repeats from one half to the other; the T-minus-wide win gap mostly does not. "
      : "The split-half correlations show which of the two numbers behaves like a stable trait. ";
    $("#lc-caption").textContent = lcLead + "Filter: " + TOUR_PLURAL[g] + ", 1st serves, all matchups, " + fInt(nPlayers) + " listed players. " +
      "Pearson r between two random halves of each player's matches; one split, no confidence interval. Favorite-target share: r = " + fr("deuce", "guess") + " (deuce) and " + fr("ad", "guess") +
      " (ad). T-minus-wide win edge: r = " + fr("deuce", "edge") + " (deuce) and " + fr("ad", "edge") + " (ad).";

    // (d) serial repeats
    setKey($("#ld-key"), [[keyDot("m-player"), "Actual repeat rate"], [keyRing(), "If the same serves came in random order"]]);
    const ser = [1, 2].map((sn) => {
      let r = null;
      SERIAL.forEach((x) => { if (x.gender === g && x.serve_num === sn) r = x; });
      return r;
    }).filter(Boolean);
    drawSerial($("#ld-chart"), ser.map((r) => ({
      label: SERVE_LABEL[r.serve_num] + " serve", actual: r.repeat_rate, random: r.repeat_rate_if_random_order,
      tip: SERVE_LABEL[r.serve_num] + " serves, " + TOUR_PLURAL[g] + "\nRepeats the last direction: " + fPct(r.repeat_rate, 1) +
        "\nRandom order: " + fPct(r.repeat_rate_if_random_order, 1) + "\nDifference: " + fSigned(r.diff_pp, 2) + " points\n" + fInt(r.transitions) + " consecutive pairs",
    })), "Repeat rate of serve direction against random order, " + TOUR_PLURAL[g]);
    const allBelow = ser.length && ser.every((r) => isNum(r.hi_pp) && r.hi_pp < 0);
    const ldLead = allBelow
      ? "Servers repeat the previous direction less often than a random order of the same serves would. "
      : "Servers repeat the previous direction about as often as a random order of the same serves would. ";
    const maxGap = Math.max.apply(null, ser.map((r) => Math.abs(r.diff_pp)).filter(isNum));
    $("#ld-caption").textContent = ldLead + "Filter: " + TOUR_PLURAL[g] + ", all matchups. " +
      "Share of serves that go to the same direction as the previous serve (same match, server, side and serve number). " + ser.map((r) => SERVE_LABEL[r.serve_num] + " serves: " + fPct(r.repeat_rate, 1) + " against " +
      fPct(r.repeat_rate_if_random_order, 1) + " in random order, a difference of " + fSigned(r.diff_pp, 2) + " points (" + ci + " CI " +
      fNum(r.lo_pp, 2) + " to " + fNum(r.hi_pp, 2) + ").").join(" ") +
      " Below zero means servers switch direction more often than a random order would. Switching more often than that has been reported before in professional tennis (Walker and Wooders 2001)." +
      (isFinite(maxGap) ? " The largest difference here is " + fNum(maxGap, 2) + " points." : "");

    // league table
    const tr = [];
    SIDES.forEach((side) => [false, true].forEach((bp) => DIRS.forEach((d) => {
      const r = get(side, bp, d);
      if (!r) return;
      tr.push([SIDE_LABEL[side], bp ? "Break points" : "Other points", d, fInt(r.n), fPct(r.share, 1), ciText(r.share_lo, r.share_hi, 1),
        fPct(r.win, 1), ciText(r.win_lo, r.win_hi, 1), r.low_n ? "thin" : ""]);
    })));
    table($("#league-table"), MATCHUP[mu] + ", " + TOUR_PLURAL[g] + ", " + sw + " serves.", [
      { label: "Side" }, { label: "Points", left: true }, { label: "Direction", left: true }, { label: "Serves" }, { label: "Share" },
      { label: "Share " + ci + " CI" }, { label: "Won" }, { label: "Won " + ci + " CI" }, { label: "Sample" },
    ], tr);
  }

  // ------------------------------------------------------------ URL hash

  function writeHash() {
    const p = [S.tour, S.player, S.serve, S.sit].map((x) => encodeURIComponent(String(x))).join("|");
    const l = [S.ltour, S.matchup, S.lserve].join("|");
    const h = "#p=" + p + "&l=" + l + (S.at ? "&at=" + S.at : "");
    try { history.replaceState(null, "", h); } catch (e) { /* file:// or sandboxed */ }
  }
  function readHash() {
    const raw = location.hash.replace(/^#/, "");
    if (raw.indexOf("p=") < 0 && raw.indexOf("l=") < 0) return false;
    const kv = {};
    raw.split("&").forEach((part) => {
      const i = part.indexOf("=");
      if (i > 0) kv[part.slice(0, i)] = part.slice(i + 1);
    });
    const dec = (s) => { try { return decodeURIComponent(s); } catch (e) { return s; } };
    if (kv.p) {
      const a = kv.p.split("|").map(dec);
      if (a[0] === "m" || a[0] === "w") S.tour = a[0];
      S.player = a[1] && findPlayer(S.tour, a[1]) ? a[1] : playerList(S.tour)[0].server;
      if (a[2] === "1" || a[2] === "2") S.serve = Number(a[2]);
      if (a[3] === "all" || a[3] === "bp") S.sit = a[3];
    }
    if (kv.l) {
      const b = kv.l.split("|").map(dec);
      if (b[0] === "m" || b[0] === "w") S.ltour = b[0];
      if (MATCHUP[b[1]]) S.matchup = b[1];
      if (b[2] === "1" || b[2] === "2") S.lserve = Number(b[2]);
    }
    S.at = kv.at === "league" ? "league" : kv.at === "scout" ? "scout" : (kv.p ? "scout" : "league");
    return true;
  }
  function syncControls() {
    setRadio("s-tour", S.tour);
    fillPlayerSelect();
    setRadio("s-serve", S.serve);
    setRadio("s-sit", S.sit);
    setRadio("l-tour", S.ltour);
    $("#l-matchup").value = S.matchup;
    setRadio("l-serve", S.lserve);
  }

  // ------------------------------------------------------------ KaTeX

  function renderTex() {
    const ok = typeof window.katex !== "undefined" && window.katex && typeof window.katex.render === "function";
    $$(".tex").forEach((node) => {
      const code = node.querySelector("code");
      const src = code ? code.textContent : node.getAttribute("data-tex") || "";
      if (!ok) {
        if (!code) { const c = document.createElement("code"); c.textContent = src; node.appendChild(c); }
        return;
      }
      try {
        const out = document.createElement("div");
        window.katex.render(src, out, { displayMode: true, throwOnError: true, strict: "ignore" });
        node.innerHTML = "";
        node.appendChild(out);
      } catch (e) {
        if (!code) { const c = document.createElement("code"); c.textContent = src; node.appendChild(c); }
      }
    });
  }

  // a table wider than its box scrolls sideways; make that region reachable by keyboard
  function markScrollableTable(box) {
    const cap = box.querySelector("caption");
    if (box.scrollWidth > box.clientWidth + 1) {
      box.setAttribute("tabindex", "0");
      box.setAttribute("role", "region");
      box.setAttribute("aria-label", (cap && cap.textContent ? cap.textContent : "Table") + " Scrolls sideways.");
    } else {
      box.removeAttribute("tabindex");
      box.removeAttribute("role");
      box.removeAttribute("aria-label");
    }
  }
  function markScrollableTables() {
    $$(".table-wrap").forEach(markScrollableTable);
  }

  // a formula wider than its column scrolls sideways; make that region reachable by keyboard
  function markScrollableFormulas() {
    $$(".formula .fx").forEach((box) => {
      const h = box.parentNode.querySelector("h4[data-n]");
      if (box.scrollWidth > box.clientWidth + 1) {
        box.setAttribute("tabindex", "0");
        box.setAttribute("role", "region");
        box.setAttribute("aria-label", "Formula " + (h ? h.getAttribute("data-n") : "") + ", scrolls sideways");
      } else {
        box.removeAttribute("tabindex");
        box.removeAttribute("role");
        box.removeAttribute("aria-label");
      }
    });
  }

  // on narrow screens the selected-zone panel sits directly under the court; on wide screens it heads the right column
  const NARROW = "(max-width: 860px)";
  function placeDetail() {
    const det = $("#detail");
    const narrow = !!(window.matchMedia && window.matchMedia(NARROW).matches);
    if (narrow) {
      const court = $("#court");
      if (court.nextElementSibling !== det) court.insertAdjacentElement("afterend", det);
    } else {
      const home = $("#detail-home");
      if (home.firstElementChild !== det) home.insertBefore(det, home.firstChild);
    }
  }

  // ------------------------------------------------------------ redraw on resize / theme

  let lastW = 0;
  function redraw() {
    if (!DATA) return;
    if (M) {
      // renderScout redraws the court
      renderScout();
    }
    renderLeague();
  }
  function onResize() {
    const w = document.documentElement.clientWidth;
    if (w === lastW) return;
    lastW = w;
    placeDetail();
    markScrollableFormulas();
    redraw();
    markScrollableTables();
  }
  function debounce(fn, ms) {
    let t = null;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  // ------------------------------------------------------------ init

  function init(data) {
    DATA = data;
    H = data.headline;
    meta = data.meta;
    PLAYERS = objects(data.players);
    MIXF = fieldIndex(data.mix);
    TESTF = fieldIndex(data.tests);
    col(data.mix, "share");
    col(data.tests, "top_share");
    LDIR = objects(data.league.directions);
    WITHIN = objects(data.league.within);
    SERIAL = objects(data.league.serial);
    STAB = objects(data.league.stability);

    S.player = playerList("m")[0].server;
    const fromHash = readHash();
    bindAll();
    syncControls();
    initScoutControls();
    initLeagueControls();
    placeDetail();
    renderScout();
    renderLeague();
    document.documentElement.classList.add("data-ready");
    const status = $("#status");
    status.textContent = "";
    status.hidden = true;
    if (fromHash && S.at) {
      const target = document.getElementById(S.at);
      if (target) target.scrollIntoView();
    }
  }

  function showError(err) {
    const status = $("#status");
    status.hidden = false;
    status.classList.add("error");
    $$(".dataline").forEach((d) => d.classList.add("no-data"));
    status.textContent = "The data file (data/serveiq.json) could not be loaded: " + (err && err.message ? err.message : "unknown error") +
      ". Reload the page. If it still fails, the same analysis is available at serve-iq.streamlit.app.";
    $$("#scout .controls, #league .controls").forEach((f) => { f.setAttribute("aria-disabled", "true"); });
  }

  function start() {
    bindStatic();
    initTheme();
    renderTex();
    markScrollableFormulas();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(markScrollableFormulas).catch(() => {});
    // a table inside a closed <details> has no width until it opens
    $$("details").forEach((d) => d.addEventListener("toggle", markScrollableTables));
    lastW = document.documentElement.clientWidth;
    window.addEventListener("resize", debounce(onResize, 150));
    fetch("data/serveiq.json", { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error("the server answered " + r.status);
        return r.json();
      })
      .then(init)
      .catch(showError);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
