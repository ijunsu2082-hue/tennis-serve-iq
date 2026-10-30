/* Serve IQ UI helpers. ES module. Theme, mobile nav, "?" popovers, segmented controls, number tweens,
   toast, avatars, chart tooltip, SVG helpers and the court color ramp (ported from v2 app.js). */

import { esc, initials, isNum, fPct, SHARE_DOMAIN_MAX } from "./data.js";

export const $ = (sel, root) => (root || document).querySelector(sel);
export const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

export function debounce(fn, ms) {
  let t = null;
  return function () {
    const args = arguments, self = this;
    clearTimeout(t);
    t = setTimeout(() => fn.apply(self, args), ms);
  };
}
export function reducedMotion() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}
export function cssVar(name, el) {
  return getComputedStyle(el || document.documentElement).getPropertyValue(name).trim();
}

// ------------------------------------------------------------ icons (inline SVG strings, aria-hidden)

const ICON_PATHS = {
  sun: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.2"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  external: '<path d="M14 5h5v5M19 5l-8 8M18 14v5H5V6h5"/>',
  github: '<path d="M9 19c-4.5 1.4-4.5-2.2-6.3-2.7M15 21.5v-3.4a3 3 0 0 0-.8-2.3c2.8-.3 5.6-1.4 5.6-6.1a4.7 4.7 0 0 0-1.3-3.3 4.4 4.4 0 0 0-.1-3.3s-1-.3-3.4 1.3a11.6 11.6 0 0 0-6 0C6.6 2.8 5.6 3.1 5.6 3.1a4.4 4.4 0 0 0-.1 3.3 4.7 4.7 0 0 0-1.3 3.3c0 4.6 2.8 5.8 5.5 6.1a3 3 0 0 0-.8 2.3v3.4"/>',
  ball: '<circle cx="12" cy="12" r="9.5"/><path d="M4.6 6a12.5 12.5 0 0 1 0 12M19.4 6a12.5 12.5 0 0 0 0 12"/>',
};
/* icon(name, cls): an inline SVG string. Names: sun moon menu close search info chevron external github ball */
export function icon(name, cls) {
  return '<svg class="icon' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
    (ICON_PATHS[name] || "") + "</svg>";
}

// ------------------------------------------------------------ theme

const THEME_KEY = "serveiq-theme";
const themeListeners = [];
export function isDark() {
  const t = document.documentElement.getAttribute("data-theme");
  if (t === "dark" || t === "light") return t === "dark";
  return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
/* onThemeChange(fn): fn(isDark) runs after the toggle or a system theme change. Redraw SVG charts here. */
export function onThemeChange(fn) { themeListeners.push(fn); }
function emitTheme() {
  const d = isDark();
  themeListeners.forEach((fn) => { try { fn(d); } catch (e) { console.warn("theme listener failed", e); } });
}
function syncThemeButtons() {
  const dark = isDark();
  $$("[data-theme-toggle]").forEach((btn) => {
    btn.innerHTML = icon(dark ? "sun" : "moon") + '<span class="visually-hidden">' + (dark ? "Switch to light theme" : "Switch to dark theme") + "</span>";
    btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  });
  const bg = getComputedStyle(document.body).backgroundColor;
  if (document.documentElement.hasAttribute("data-theme")) {
    $$('meta[name="theme-color"]').forEach((m) => m.setAttribute("content", bg));
  }
}
export function setTheme(next) {
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* storage blocked */ }
  syncThemeButtons();
  emitTheme();
}
/* initTheme(): wires every [data-theme-toggle] button and follows system changes. Idempotent. */
let themeReady = false;
export function initTheme() {
  if (themeReady) return;
  themeReady = true;
  $$("[data-theme-toggle]").forEach((btn) => btn.addEventListener("click", () => setTheme(isDark() ? "light" : "dark")));
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => { syncThemeButtons(); emitTheme(); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
  syncThemeButtons();
}

// ------------------------------------------------------------ mobile nav

let navReady = false;
export function initNav() {
  if (navReady) return;
  navReady = true;
  const bar = $(".topbar");
  const btn = $("[data-nav-toggle]");
  if (!bar || !btn) return;
  const close = () => {
    bar.classList.remove("nav-open");
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = icon("menu") + '<span class="visually-hidden">Open menu</span>';
  };
  const open = () => {
    bar.classList.add("nav-open");
    btn.setAttribute("aria-expanded", "true");
    btn.innerHTML = icon("close") + '<span class="visually-hidden">Close menu</span>';
    // the links come before the toggle in the DOM, so move focus into the open menu
    const first = $(".nav-links a", bar);
    if (first) first.focus();
  };
  close();
  btn.addEventListener("click", () => (bar.classList.contains("nav-open") ? close() : open()));
  $$(".nav-links a", bar).forEach((a) => a.addEventListener("click", close));
  // tabbing out of the bar closes the menu
  bar.addEventListener("focusout", (e) => {
    if (bar.classList.contains("nav-open") && e.relatedTarget && !bar.contains(e.relatedTarget)) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && bar.classList.contains("nav-open")) { close(); btn.focus(); }
  });
  document.addEventListener("click", (e) => {
    // composedPath: the toggle swaps its own icon, so e.target may already be detached
    if (bar.classList.contains("nav-open") && e.composedPath().indexOf(bar) < 0) close();
  });
  window.addEventListener("resize", debounce(() => { if (window.innerWidth > 860) close(); }, 120));
}

// ------------------------------------------------------------ "?" info popovers

/* Markup:  <button type="button" class="info" data-info="One sentence." aria-label="About serves analyzed"></button>
   or from JS: infoButton("One sentence.", "About serves analyzed") returns that HTML string.
   initPopovers(root) upgrades every button.info[data-info] under root (safe to call again after re-rendering).
   Click, Enter or Space toggles; Esc, outside click or scroll closes. The text is also the button's
   aria-describedby, so screen readers read it on focus. setInfo(btn, text) changes the sentence. */
let popSeq = 0;
let openPop = null;
export function infoButton(text, label, cls) {
  return '<button type="button" class="info' + (cls ? " " + cls : "") + '" data-info="' + esc(text) + '" aria-label="' + esc(label || "More information") + '"></button>';
}
function placePop(btn, pop) {
  const r = btn.getBoundingClientRect();
  pop.style.left = "0px";
  pop.style.top = "0px";
  const w = pop.offsetWidth, h = pop.offsetHeight;
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  let x = r.left + r.width / 2 - w / 2;
  x = Math.max(8, Math.min(vw - w - 8, x));
  let y = r.bottom + 8;
  if (y + h > vh - 8 && r.top - h - 8 > 8) { y = r.top - h - 8; pop.setAttribute("data-place", "top"); }
  else pop.setAttribute("data-place", "bottom");
  pop.style.left = Math.round(x) + "px";
  pop.style.top = Math.round(y) + "px";
}
export function closePopover(refocus) {
  if (!openPop) return;
  const { btn, pop } = openPop;
  pop.classList.remove("is-open");
  pop.hidden = true;
  btn.setAttribute("aria-expanded", "false");
  openPop = null;
  if (refocus) btn.focus();
}
function openPopover(btn) {
  const pop = document.getElementById(btn.getAttribute("aria-controls"));
  if (!pop) return;
  closePopover(false);
  pop.hidden = false;
  pop.classList.add("is-open");
  btn.setAttribute("aria-expanded", "true");
  placePop(btn, pop);
  openPop = { btn, pop };
}
let popGlobal = false;
export function initPopovers(root) {
  $$("button.info[data-info]", root).forEach((btn) => {
    if (btn.__pop) return;
    btn.__pop = true;
    if (!btn.innerHTML.trim()) btn.innerHTML = '<span aria-hidden="true">?</span>';
    const pop = document.createElement("span");
    pop.className = "pop";
    pop.id = "pop-" + (++popSeq);
    pop.setAttribute("role", "tooltip");
    pop.hidden = true;
    pop.textContent = btn.getAttribute("data-info");
    document.body.appendChild(pop);
    btn.setAttribute("aria-controls", pop.id);
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-describedby", pop.id);
    // keyboard focus moving elsewhere closes it (a click on plain text gives no relatedTarget and is left to the click handler)
    btn.addEventListener("focusout", (e) => {
      const to = e.relatedTarget;
      if (openPop && openPop.btn === btn && to && to !== btn && !openPop.pop.contains(to)) closePopover(false);
    });
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (openPop && openPop.btn === btn) closePopover(false);
      else openPopover(btn);
    });
  });
  if (!popGlobal) {
    popGlobal = true;
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openPop) { e.preventDefault(); closePopover(true); } });
    document.addEventListener("click", (e) => { if (openPop && !openPop.pop.contains(e.target)) closePopover(false); });
    window.addEventListener("scroll", () => { if (openPop) closePopover(false); }, { passive: true });
    window.addEventListener("resize", () => { if (openPop) placePop(openPop.btn, openPop.pop); });
  }
}
export function setInfo(btn, text) {
  btn.setAttribute("data-info", text);
  const id = btn.getAttribute("aria-controls");
  const pop = id && document.getElementById(id);
  if (pop) pop.textContent = text;
}

// ------------------------------------------------------------ segmented control

/* segmented(host, { label, options: [{value, label}], value, onChange(value), name })
   Builds a radiogroup of buttons inside host (class "seg" is added). Arrow keys move and select,
   Home/End jump. If options is omitted, existing <button data-value> children are used.
   Returns { el, get value(), set(value, silent) }. */
export function segmented(host, opts) {
  const o = opts || {};
  host.classList.add("seg");
  host.setAttribute("role", "radiogroup");
  if (o.label) host.setAttribute("aria-label", o.label);
  if (o.options) {
    host.innerHTML = o.options.map((op) =>
      '<button type="button" class="seg-btn" role="radio" data-value="' + esc(op.value) + '">' + esc(op.label) + "</button>").join("");
  }
  const btns = $$("button[data-value]", host);
  btns.forEach((b) => { b.classList.add("seg-btn"); b.setAttribute("role", "radio"); });
  let value = o.value != null ? String(o.value) : (btns[0] && btns[0].getAttribute("data-value"));
  const paint = () => btns.forEach((b) => {
    const on = b.getAttribute("data-value") === value;
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
  const set = (v, silent) => {
    v = String(v);
    if (!btns.some((b) => b.getAttribute("data-value") === v)) return;
    const changed = v !== value;
    value = v;
    paint();
    if (changed && !silent && o.onChange) o.onChange(value);
  };
  btns.forEach((b, i) => {
    b.addEventListener("click", () => set(b.getAttribute("data-value")));
    b.addEventListener("keydown", (e) => {
      let j = null;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") j = (i + 1) % btns.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = (i - 1 + btns.length) % btns.length;
      else if (e.key === "Home") j = 0;
      else if (e.key === "End") j = btns.length - 1;
      if (j == null) return;
      e.preventDefault();
      btns[j].focus();
      set(btns[j].getAttribute("data-value"));
    });
  });
  paint();
  return { el: host, get value() { return value; }, set };
}

// ------------------------------------------------------------ number tween

/* tween(el, to, format, opts): animates el's text from the value now on screen (the last target, or the
   mid-value if a tween was interrupted; opts.from overrides; the first call jumps straight to `to`).
   opts: { duration = 600, from }. Non-numbers and reduced motion set the final text at once. */
export function tween(el, to, format, opts) {
  const o = opts || {};
  const f = format || ((v) => String(Math.round(v)));
  if (!el) return;
  const running = !!el.__tweenRaf;
  if (running) cancelAnimationFrame(el.__tweenRaf);
  el.__tweenRaf = null;
  const from = o.from != null ? o.from : (running && isNum(el.__tweenShown) ? el.__tweenShown : el.__tweenValue);
  el.__tweenValue = isNum(to) ? to : undefined;
  el.__tweenShown = el.__tweenValue;
  if (!isNum(to) || !isNum(from) || from === to || reducedMotion()) {
    el.textContent = f(to);
    return;
  }
  const dur = o.duration == null ? 600 : o.duration;
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    el.__tweenShown = from + (to - from) * e;
    el.textContent = f(el.__tweenShown);
    if (k < 1) el.__tweenRaf = requestAnimationFrame(step);
    else { el.__tweenRaf = null; el.__tweenShown = to; el.textContent = f(to); }
  };
  el.__tweenRaf = requestAnimationFrame(step);
}

// ------------------------------------------------------------ toast

let toastEl = null, toastTimer = null;
/* toast(message, { timeout = 3000 }): a short polite status message at the bottom of the screen */
export function toast(msg, opts) {
  if (!toastEl) {
    toastEl = document.createElement("div");
    toastEl.className = "toast";
    toastEl.setAttribute("role", "status");
    toastEl.setAttribute("aria-live", "polite");
    document.body.appendChild(toastEl);
  }
  toastEl.textContent = msg;
  toastEl.classList.add("is-on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("is-on"), (opts && opts.timeout) || 3000);
}

// ------------------------------------------------------------ avatars

/* avatarHTML(name, photoEntry, { size = 48, cls }): a round avatar. The initials always render underneath;
   the photo (if any) sits on top and removes itself if it fails to load. photoEntry is D.photo(name). */
export function avatarHTML(name, entry, opts) {
  const o = opts || {};
  const size = o.size || 48;
  const img = entry && entry.src
    ? '<img class="avatar-img" src="' + esc(entry.src) + '" alt="" width="' + size + '" height="' + size + '" loading="' + (o.eager ? "eager" : "lazy") + '" decoding="async">'
    : "";
  return '<span class="avatar' + (o.cls ? " " + o.cls : "") + '" style="--size:' + size + 'px" aria-hidden="true"><span class="avatar-initials">' +
    esc(initials(name)) + "</span>" + img + "</span>";
}
export function avatar(name, entry, opts) {
  const t = document.createElement("template");
  t.innerHTML = avatarHTML(name, entry, opts);
  return t.content.firstChild;
}
// one capturing listener: a broken avatar photo falls back to initials
document.addEventListener("error", (e) => {
  const t = e.target;
  if (t && t.classList && t.classList.contains("avatar-img")) t.remove();
}, true);

// ------------------------------------------------------------ chart tooltip (shared #tip)

let tipTimer = null;
function tipEl() {
  let t = document.getElementById("tip");
  if (!t) {
    t = document.createElement("div");
    t.id = "tip";
    t.className = "tip";
    t.setAttribute("role", "tooltip");
    t.setAttribute("aria-hidden", "true");
    document.body.appendChild(t);
  }
  return t;
}
export function showTip(text, e) {
  const tip = tipEl();
  tip.textContent = text;
  tip.classList.add("on");
  placeTip(e);
}
export function placeTip(e) {
  const tip = tipEl();
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + w > window.innerWidth - 8) x = Math.max(8, e.clientX - w - 14);
  if (y + h > window.innerHeight - 8) y = Math.max(8, e.clientY - h - 14);
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}
export function hideTip() { tipEl().classList.remove("on"); }
/* bindTips(svg): any descendant with data-tip="multi\nline text" shows the tooltip on hover or touch */
export function bindTips(svg) {
  svg.addEventListener("pointerover", (e) => {
    const t = e.target.closest ? e.target.closest("[data-tip]") : null;
    if (!t) return;
    showTip(t.getAttribute("data-tip"), e);
    if (e.pointerType === "touch") {
      clearTimeout(tipTimer);
      tipTimer = setTimeout(hideTip, 3000);
    }
  });
  svg.addEventListener("pointermove", (e) => { if (tipEl().classList.contains("on")) placeTip(e); });
  svg.addEventListener("pointerout", (e) => { if (e.pointerType !== "touch") hideTip(); });
}

// ------------------------------------------------------------ SVG helpers (same as v2)

export const SVGNS = "http://www.w3.org/2000/svg";
export function el(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  if (attrs) Object.keys(attrs).forEach((k) => { if (attrs[k] != null) e.setAttribute(k, attrs[k]); });
  if (parent) parent.appendChild(e);
  return e;
}
export function txt(parent, x, y, s, cls, anchor, extra) {
  const t = el("text", Object.assign({ x: x, y: y, class: cls || null, "text-anchor": anchor || "start" }, extra || {}), parent);
  t.textContent = s;
  return t;
}
/* newSvg(host, w, h, label, role): clears host, appends a responsive <svg> with tooltips bound */
export function newSvg(host, w, h, label, role) {
  host.innerHTML = "";
  const svg = el("svg", { viewBox: "0 0 " + w + " " + h, width: w, height: h, role: role || "img", "aria-label": label, preserveAspectRatio: "xMinYMin meet" });
  host.appendChild(svg);
  bindTips(svg);
  return svg;
}
export function fitText(t, maxW, minSize) {
  let size = parseFloat(t.style.fontSize);
  let guard = 0;
  while (t.getComputedTextLength() > maxW && size > minSize && guard < 24) {
    size -= 0.5;
    t.style.fontSize = size + "px";
    guard++;
  }
}
export function niceStep(range, target) {
  const raw = range / target;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / pow;
  const step = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10;
  return step * pow;
}
export function domainOf(values, padFrac, target, clampLo, clampHi) {
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
export function barPath(x, yBase, yVal, w, r) {
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
export function diamond(cx, cy, s) {
  return "M" + cx + "," + (cy - s) + "L" + (cx + s) + "," + cy + "L" + cx + "," + (cy + s) + "L" + (cx - s) + "," + cy + "Z";
}
/* legend item HTML strings, as in v2 keys */
export function keyDot(cls) { return '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="5" class="' + cls + '"/></svg>'; }
export function keyDiamond() { return '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="' + diamond(7, 7, 5) + '" class="m-league"/></svg>'; }
export function keySquare(cls) { return '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="1" y="1" width="12" height="12" rx="2" class="' + cls + '"/></svg>'; }
export function keyRing() { return '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="4.5" class="m-league"/></svg>'; }
/* setKey(host, [[swatchHtml, label], ...]) */
export function setKey(host, items) {
  host.innerHTML = items.map((it) => '<span class="sw">' + it[0] + "<span>" + esc(it[1]) + "</span></span>").join("");
}

// ------------------------------------------------------------ court color ramp (same values as v2)

export const RAMP_LIGHT = ["#e8f1fc", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
export const RAMP_DARK = ["#1c2838", "#1d3350", "#20426d", "#26538c", "#2e67ad", "#4381d0", "#68a0e8", "#9cc4f5"];
export const SHARE_MAX = SHARE_DOMAIN_MAX;
function hexRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function rgbHex(c) { return "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join(""); }
function lum(c) {
  const a = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
  return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
}
function contrast(a, b) { const la = lum(a), lb = lum(b); return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05); }
/* shareColor(share): { fill, ink } for a court zone, ink chosen for contrast */
export function shareColor(share) {
  const ramp = (isDark() ? RAMP_DARK : RAMP_LIGHT).map(hexRgb);
  const t = Math.max(0, Math.min(1, (isNum(share) ? share : 0) / SHARE_MAX)) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(t));
  const f = t - i;
  const c = ramp[i].map((v, k) => v + (ramp[i + 1][k] - v) * f);
  const ink = contrast(c, [11, 11, 11]) >= contrast(c, [255, 255, 255]) ? "#0b0b0b" : "#ffffff";
  return { fill: rgbHex(c), ink: ink };
}
/* rampSvg(): the gradient color key (0, middle, top of the domain) */
export function rampSvg() {
  const ramp = isDark() ? RAMP_DARK : RAMP_LIGHT;
  const w = 150, h = 26;
  let s = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" aria-hidden="true"><defs><linearGradient id="court-ramp" x1="0" x2="1" y1="0" y2="0">' +
    ramp.map((c, i) => '<stop offset="' + (i / (ramp.length - 1)) + '" stop-color="' + c + '"/>').join("") + "</linearGradient></defs>" +
    '<rect x="0.5" y="0.5" width="' + (w - 1) + '" height="10" rx="2" fill="url(#court-ramp)" stroke="var(--rule)"/>';
  const ticks = [0, SHARE_MAX / 2, SHARE_MAX];
  ticks.forEach((v, i) => {
    const x = (v / SHARE_MAX) * w;
    const anchor = i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle";
    s += '<text x="' + x + '" y="24" text-anchor="' + anchor + '">' + esc(fPct(v, 0) + (i === ticks.length - 1 ? "+" : "")) + "</text>";
  });
  return s + "</svg>";
}
