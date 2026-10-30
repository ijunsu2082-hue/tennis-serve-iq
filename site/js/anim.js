/* Serve IQ motion. ES module, vanilla JS. Reveal on scroll, count-up, chart grow-in replays,
   active-section nav, hero parallax. Everything is off under prefers-reduced-motion (final state at once).

   Safety: the inline script in <head> adds class "js" to <html> (CSS hides [data-reveal] only under .js)
   and starts a fallback timer that adds "reveal-all" after 3 s. init() cancels that timer and takes over;
   if IntersectionObserver is missing or motion is reduced, it adds "reveal-all" at once. A second
   safety pass reveals anything already on screen that the observer has not marked. */

import { fmt, isNum } from "./data.js";

const html = document.documentElement;
export function reducedMotion() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}
const hasIO = typeof window.IntersectionObserver === "function";
let revealIO = null, countIO = null;

function revealAll() { html.classList.add("reveal-all"); }

function onScreen(el) {
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.top < window.innerHeight && (r.width > 0 || r.height > 0);
}
// a count-up waits until its nearest [data-reveal] ancestor is revealed
function countReady(el) {
  const a = el.parentElement && el.parentElement.closest("[data-reveal]");
  return !a || a.classList.contains("is-in") || html.classList.contains("reveal-all");
}
function markIn(el) {
  if (el.classList.contains("is-in")) return;
  el.classList.add("is-in");
  el.querySelectorAll("[data-countup]").forEach((c) => { if (onScreen(c) && countReady(c)) startCount(c); });
  if (el.hasAttribute("data-countup")) startCount(el);
}

/* stagger: children of [data-stagger] that carry data-reveal get --reveal-i = their index (CSS turns it into a delay) */
function applyStagger(root) {
  (root || document).querySelectorAll("[data-stagger]").forEach((box) => {
    let i = 0;
    Array.prototype.forEach.call(box.children, (c) => {
      if (c.hasAttribute("data-reveal")) c.style.setProperty("--reveal-i", String(i++));
    });
  });
}

/* observe(root): start watching every [data-reveal] and [data-countup] under root that is not watched yet.
   Call it again after a module injects new markup or after D.bind() fills count-up values
   (main.js calls it after binding and again after all modules init). */
export function observe(root) {
  const r = root || document;
  applyStagger(r);
  const reveals = Array.prototype.slice.call(r.querySelectorAll("[data-reveal]"));
  if (r !== document && r.hasAttribute && r.hasAttribute("data-reveal")) reveals.push(r);
  const counts = Array.prototype.slice.call(r.querySelectorAll("[data-countup]"));
  if (!hasIO || reducedMotion()) {
    revealAll();
    reveals.forEach((el) => el.classList.add("is-in"));
    counts.forEach(startCount);
    return;
  }
  reveals.forEach((el) => {
    if (el.__revealWatched) return;
    el.__revealWatched = true;
    revealIO.observe(el);
  });
  counts.forEach((el) => {
    if (el.__counted) return;
    if (onScreen(el) && countReady(el)) startCount(el);
    if (!el.__counted) countIO.observe(el);
  });
}

// ------------------------------------------------------------ count-up

/* A [data-countup] element counts from 0 to its value when it scrolls into view.
   The value is data-value (set by D.bind for [data-h]) and the format is data-f (data.js fmt codes),
   so <span data-h="players" data-f="int" data-countup></span> just works. The final text is always
   what D.bind wrote, so a missed animation never shows a wrong number. */
export function countUp(el, to, format, duration) {
  const f = format || ((v) => fmt(v, el.getAttribute("data-f")));
  if (!isNum(to)) return;
  const finalText = f(to);
  if (reducedMotion()) { el.textContent = finalText; return; }
  if (el.__countRaf) cancelAnimationFrame(el.__countRaf);
  const dur = duration || 1200;
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    el.textContent = f(to * e);
    if (k < 1) el.__countRaf = requestAnimationFrame(step);
    else { el.__countRaf = null; el.textContent = finalText; }
  };
  el.__countRaf = requestAnimationFrame(step);
}
function startCount(el) {
  if (el.__counted) return;
  const v = parseFloat(el.getAttribute("data-value"));
  if (!isNum(v)) return;
  el.__counted = true;
  countUp(el, v);
}

// ------------------------------------------------------------ chart grow-in

/* replay(container): re-runs the CSS grow-in of marks (.grow-y, .grow-x, .pop-in, .fade-in) inside a
   [data-reveal] container after a redraw. No-op under reduced motion or before the container is revealed. */
export function replay(container) {
  if (!container || reducedMotion()) return;
  const host = container.closest ? (container.closest("[data-reveal]") || container) : container;
  if (!host.classList.contains("is-in")) return;
  host.classList.remove("is-in");
  void host.offsetWidth; // restart transitions
  host.classList.add("is-in");
}
/* seq(nodes, step): sets --d on each node (0, 1, 2 ...) so CSS delays them by --d times 40 ms */
export function seq(nodes, start) {
  Array.prototype.forEach.call(nodes, (n, i) => n.style.setProperty("--d", String((start || 0) + i)));
}

// ------------------------------------------------------------ active section in the nav

function initScrollSpy() {
  const links = Array.prototype.slice.call(document.querySelectorAll('.nav-links a[href^="#"]'));
  if (!links.length || !hasIO) return;
  const map = new Map();
  links.forEach((a) => {
    const sec = document.getElementById(a.getAttribute("href").slice(1));
    if (sec) map.set(sec, a);
  });
  const visible = new Map();
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => visible.set(e.target, e.isIntersecting ? e.intersectionRatio : 0));
    let best = null, bestR = 0;
    visible.forEach((r, sec) => { if (r > bestR) { bestR = r; best = sec; } });
    links.forEach((a) => { a.classList.remove("is-active"); a.removeAttribute("aria-current"); });
    if (best && map.get(best)) {
      map.get(best).classList.add("is-active");
      map.get(best).setAttribute("aria-current", "true");
    }
  }, { rootMargin: "-30% 0px -55% 0px", threshold: [0, 0.01, 0.25, 0.5, 1] });
  map.forEach((a, sec) => io.observe(sec));
}

// ------------------------------------------------------------ parallax

/* [data-parallax="0.25"]: translateY(scrolled distance times factor) while the element is on screen; transform only */
function initParallax() {
  const els = Array.prototype.slice.call(document.querySelectorAll("[data-parallax]"));
  if (!els.length || reducedMotion()) return;
  let ticking = false;
  const update = () => {
    ticking = false;
    const vh = window.innerHeight;
    els.forEach((el) => {
      const box = el.parentElement || el;
      const r = box.getBoundingClientRect();
      if (r.bottom < 0 || r.top > vh) return;
      const f = parseFloat(el.getAttribute("data-parallax")) || 0.2;
      el.style.transform = "translate3d(0," + Math.round(-r.top * f) + "px,0)";
    });
  };
  window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  window.addEventListener("resize", update);
  update();
}

// ------------------------------------------------------------ topbar shadow once scrolled

function initTopbar() {
  const bar = document.querySelector(".topbar");
  if (!bar) return;
  const on = () => bar.classList.toggle("is-scrolled", window.scrollY > 8);
  window.addEventListener("scroll", on, { passive: true });
  on();
}

// ------------------------------------------------------------ init

let ready = false;
export function init() {
  if (ready) return;
  ready = true;
  if (window.__siqRevealTimer) { clearTimeout(window.__siqRevealTimer); window.__siqRevealTimer = null; }
  html.classList.add("js");
  if (hasIO && !reducedMotion()) {
    revealIO = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) { markIn(e.target); revealIO.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
    countIO = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting && countReady(e.target)) startCount(e.target);
        if (e.target.__counted) countIO.unobserve(e.target);
      });
    }, { threshold: 0.4 });
  }
  observe(document);
  // safety pass: anything on screen that is still hidden after 1.5 s is shown
  setTimeout(() => {
    const vh = window.innerHeight;
    document.querySelectorAll("[data-reveal]:not(.is-in)").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.top < vh && r.bottom > 0) markIn(el);
    });
  }, 1500);
  // sweep: the observer can miss targets that a fast scroll, an anchor jump or a busy main thread carries
  // past the viewport between two frames. After each scroll, anything at or above the reveal line is shown.
  if (revealIO) {
    let sweepT = null;
    const sweep = () => {
      sweepT = null;
      const line = window.innerHeight * 0.92;
      document.querySelectorAll("[data-reveal]:not(.is-in)").forEach((el) => {
        const r = el.getBoundingClientRect();
        if ((r.width > 0 || r.height > 0) && r.top < line) { markIn(el); revealIO.unobserve(el); }
      });
    };
    window.addEventListener("scroll", () => { if (!sweepT) sweepT = setTimeout(sweep, 150); }, { passive: true });
  }
  window.addEventListener("beforeprint", revealAll);
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onRM = () => { if (mq.matches) revealAll(); };
    if (mq.addEventListener) mq.addEventListener("change", onRM);
  }
  initScrollSpy();
  initParallax();
  initTopbar();
}
