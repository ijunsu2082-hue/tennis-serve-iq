/* Serve IQ method page (method.html). ES module, the page's only script besides KaTeX.
   boot([]) from main.js wires theme, nav, motion, data and [data-h] / [data-b] binding, then init(ctx) here:
   KaTeX rendering (raw LaTeX stays in <code> if KaTeX is missing or fails), collapsible formula cards,
   the sticky table of contents with scroll spy, the header photo and the photo credits from images.json. */
import { boot } from "./main.js";
import { $, $$, debounce, icon, avatarHTML, initPopovers, reducedMotion } from "./ui.js";
import { observe } from "./anim.js";
import { esc } from "./data.js";

const NARROW = "(max-width: 639px)";
const mq = (q) => !!(window.matchMedia && window.matchMedia(q).matches);

// ------------------------------------------------------------ KaTeX (ported from v2 renderTex)

function renderTex(root) {
  const ok = typeof window.katex !== "undefined" && window.katex && typeof window.katex.render === "function";
  $$(".tex", root).forEach((node) => {
    const code = node.querySelector("code");
    const src = code ? code.textContent : node.getAttribute("data-tex") || "";
    if (!ok) {
      if (!code) { const c = document.createElement("code"); c.textContent = src; node.appendChild(c); }
      node.classList.add("tex-raw");
      return;
    }
    try {
      const out = document.createElement("div");
      window.katex.render(src, out, { displayMode: true, throwOnError: true, strict: "ignore" });
      node.innerHTML = "";
      node.appendChild(out);
      node.classList.add("tex-ok");
    } catch (e) {
      if (!code) { const c = document.createElement("code"); c.textContent = src; node.appendChild(c); }
      node.classList.add("tex-raw");
    }
  });
}

// a formula wider than its box scrolls sideways; make that region reachable by keyboard (v2 rule)
function markScrollableFormulas(root) {
  $$(".fx", root).forEach((box) => {
    if (!box.clientWidth) return; // inside a closed card: measured again when it opens
    const card = box.closest(".fcard");
    const name = card ? "Formula " + card.id.replace(/^f/, "") : "Formula";
    if (box.scrollWidth > box.clientWidth + 1) {
      box.setAttribute("tabindex", "0");
      box.setAttribute("role", "region");
      box.setAttribute("aria-label", name + ", scrolls sideways");
      box.classList.add("is-scrollable");
    } else {
      box.removeAttribute("tabindex");
      box.removeAttribute("role");
      box.removeAttribute("aria-label");
      box.classList.remove("is-scrollable");
    }
  });
}

// ------------------------------------------------------------ collapsible formula cards

function initCards(root) {
  const cards = $$("details.fcard", root);
  const btn = $("[data-m-toggle-all]", root);
  const sync = () => {
    if (!btn) return;
    const allOpen = cards.every((c) => c.open);
    btn.innerHTML = "<span>" + (allOpen ? "Collapse all" : "Expand all") + '</span><span class="hide-sm">formulas</span>';
    btn.setAttribute("aria-expanded", allOpen ? "true" : "false");
  };
  // phones start with the cards closed (title and one line each); a card the URL points to stays open
  if (mq(NARROW)) {
    const target = hashTarget();
    cards.forEach((c) => { c.open = !!(target && (c === target || c.contains(target))); });
  }
  cards.forEach((c) => c.addEventListener("toggle", () => {
    if (c.open) markScrollableFormulas(c);
    sync();
  }));
  if (btn) {
    btn.addEventListener("click", () => {
      const open = !cards.every((c) => c.open);
      cards.forEach((c) => { c.open = open; });
      sync();
    });
  }
  sync();
}

function hashTarget() {
  const h = decodeURIComponent((location.hash || "").slice(1));
  return h ? document.getElementById(h) : null;
}
// a link to a formula (method.html#f7) opens its card
function openHashTarget() {
  const t = hashTarget();
  if (!t) return;
  const card = t.matches("details") ? t : t.closest("details");
  if (card && !card.open) {
    card.open = true;
    requestAnimationFrame(() => t.scrollIntoView({ block: "start", behavior: "instant" }));
  }
}
/* The browser jumps to the URL's anchor before this module runs; closing cards on phones, KaTeX, web fonts
   and the credits list then move it. Land on the anchor again, unless the reader has started scrolling. */
let readerMoved = false;
["wheel", "touchstart", "keydown", "mousedown"].forEach((ev) =>
  window.addEventListener(ev, () => { readerMoved = true; }, { once: true, passive: true }));
function settleHash() {
  const t = hashTarget();
  if (!t || readerMoved) return;
  // a reveal target still sits 16px low; show it in its final place first so the jump lands exactly
  const box = t.closest("[data-reveal]");
  if (box && !box.classList.contains("is-in")) {
    box.style.transition = "none";
    box.classList.add("is-in");
    void box.offsetWidth;
    box.style.transition = "";
  }
  t.scrollIntoView({ block: "start", behavior: "instant" });
}

// ------------------------------------------------------------ table of contents: scroll spy and progress

function initToc(root) {
  const toc = $(".m-toc", root);
  if (!toc) return;
  const links = $$('.m-toc-list a[href^="#"]', toc);
  const list = $(".m-toc-list", toc);
  const items = links
    .map((a) => ({ a, sec: document.getElementById(a.getAttribute("href").slice(1)) }))
    .filter((x) => x.sec);
  const main = $(".m-main", root);
  let current = null;

  const update = () => {
    const line = window.innerHeight * 0.3;
    let best = items[0] || null;
    items.forEach((x) => { if (x.sec.getBoundingClientRect().top <= line) best = x; });
    // at the very bottom the last block wins even if its top never reaches the line
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) best = items[items.length - 1];
    if (best && best !== current) {
      if (current) { current.a.classList.remove("is-active"); current.a.removeAttribute("aria-current"); }
      best.a.classList.add("is-active");
      best.a.setAttribute("aria-current", "location");
      current = best;
      // on phones the list is a sideways strip: keep the active entry in view without moving the page
      if (list && list.scrollWidth > list.clientWidth + 1) {
        const l = best.a.parentElement.offsetLeft - list.clientWidth / 2 + best.a.parentElement.offsetWidth / 2;
        list.scrollTo({ left: Math.max(0, l), behavior: reducedMotion() ? "auto" : "smooth" });
      }
    }
    if (main) {
      const r = main.getBoundingClientRect();
      const span = Math.max(1, r.height - window.innerHeight * 0.6);
      const p = Math.min(1, Math.max(0, (window.innerHeight * 0.4 - r.top) / span));
      toc.style.setProperty("--m-progress", p.toFixed(3));
    }
  };
  let raf = 0;
  const onScroll = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; update(); });
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  $$("details.fcard", root).forEach((c) => c.addEventListener("toggle", onScroll));
  update();
}

// ------------------------------------------------------------ photos

async function imagesOf(D) {
  if (D && D.images) return D.images;
  try {
    const r = await fetch("data/images.json", { credentials: "same-origin" });
    if (!r.ok) return { players: {}, scenes: {} };
    const j = await r.json();
    return { players: (j && j.players) || {}, scenes: (j && j.scenes) || {} };
  } catch (e) {
    return { players: {}, scenes: {} };
  }
}

function heroPhoto(root, images) {
  const box = $(".m-hero-media", root);
  if (!box) return;
  const key = box.getAttribute("data-scene") || "stadium";
  const e = images.scenes[key] || images.scenes.hero;
  if (!e || !e.src) return;
  const img = new Image();
  img.alt = "";
  img.decoding = "async";
  img.addEventListener("load", () => box.classList.add("is-loaded"));
  img.addEventListener("error", () => img.remove());
  img.src = e.src;
  box.appendChild(img);
}

// what each scene shows; used only when the file still matches, otherwise a plain label
const SCENE_LABEL = {
  hero: ["Rafael Nadal serving", "Nadal"],
  stadium: ["Indoor arena at the ATP Finals", "Arena"],
  court: ["Tennis courts from the air", "tennis_courts"],
  ball: ["Tennis ball on a court", "ball"],
};
function sceneSubject(key, e) {
  const m = SCENE_LABEL[key];
  if (m && String(e.source || "").indexOf(m[1]) !== -1) return m[0];
  return "Site photo (" + key + ")";
}

function licenseHTML(e) {
  const name = esc(e.license || "License not stated");
  return e.license_url ? '<a href="' + esc(e.license_url) + '" rel="license noopener">' + name + "</a>" : "<span>" + name + "</span>";
}
function fileLink(e, subject) {
  if (!e.source) return "";
  return '<a class="credit-file" href="' + esc(e.source) + '" rel="noopener" aria-label="File page for the photo of ' + esc(subject) + ' on Wikimedia Commons">File page' +
    icon("external", "credit-ext") + "</a>";
}
function creditHTML(subject, e, thumb, i) {
  return '<li class="credit" data-reveal style="--reveal-i:' + (i % 3) + '" data-name="' + esc(subject.toLowerCase()) + '">' +
    thumb +
    '<div class="credit-body">' +
    '<p class="credit-subject">' + esc(subject) + "</p>" +
    '<p class="credit-author">Photo: ' + esc(e.author || "Author not stated") + "</p>" +
    '<p class="credit-links">' + licenseHTML(e) + fileLink(e, subject) + "</p>" +
    "</div></li>";
}

function renderCredits(root, images) {
  const host = $("#credits-list", root);
  if (!host) return;
  const scenes = Object.keys(images.scenes).filter((k) => images.scenes[k] && images.scenes[k].src);
  const players = Object.keys(images.players).filter((k) => images.players[k] && images.players[k].src)
    .sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" }));
  const total = scenes.length + players.length;
  const count = $("#credits-count", root);
  if (!total) {
    host.innerHTML = '<p class="m-note muted">No photo credits to show.</p>';
    return;
  }
  if (count) { count.textContent = total + (total === 1 ? " photo" : " photos"); count.hidden = false; }

  let html = "";
  if (scenes.length) {
    html += '<h3 class="credit-group">Scenes <span class="m-chip tnum">' + scenes.length + "</span></h3>";
    html += '<ul class="credit-grid">' + scenes.map((k, i) => {
      const e = images.scenes[k];
      const subject = sceneSubject(k, e);
      const thumb = '<span class="credit-scene" aria-hidden="true"><img src="' + esc(e.src) + '" alt="" width="96" height="64" loading="lazy" decoding="async"></span>';
      return creditHTML(subject, e, thumb, i);
    }).join("") + "</ul>";
  }
  if (players.length) {
    html += '<div class="credit-group-row"><h3 class="credit-group">Players <span class="m-chip tnum">' + players.length + "</span></h3>" +
      '<label class="credit-filter"><span class="visually-hidden">Filter player photo credits by name</span>' + icon("search") +
      '<input class="input" type="search" id="credit-q" placeholder="Filter by name" autocomplete="off" spellcheck="false"></label></div>';
    html += '<ul class="credit-grid" id="credit-players">' + players.map((name, i) => {
      const e = images.players[name];
      return creditHTML(name, e, avatarHTML(name, e, { size: 56, cls: "credit-avatar" }), i);
    }).join("") + "</ul>";
    html += '<p class="m-note muted" id="credit-none" hidden>No player matches that name.</p>';
  }
  host.innerHTML = html;

  // a scene thumbnail that fails to load leaves a plain tile
  $$(".credit-scene img", host).forEach((img) => img.addEventListener("error", () => img.remove()));

  const q = $("#credit-q", host);
  if (q) {
    const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
    const rows = $$("#credit-players > li", host).map((li) => ({ li, key: norm(li.getAttribute("data-name") || "") }));
    const none = $("#credit-none", host);
    q.addEventListener("input", debounce(() => {
      const t = norm(q.value.trim());
      let shown = 0;
      rows.forEach((r) => {
        const hit = !t || r.key.indexOf(t) !== -1;
        r.li.hidden = !hit;
        if (hit) { shown++; r.li.classList.add("is-in"); }
      });
      if (none) none.hidden = shown > 0;
    }, 80));
  }
}

// ------------------------------------------------------------ init

export async function init(ctx) {
  const root = document.getElementById("method");
  if (!root) return;
  const D = ctx && ctx.data;

  renderTex(root);
  initCards(root);
  markScrollableFormulas(root);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => markScrollableFormulas(root)).catch(() => {});
  window.addEventListener("resize", debounce(() => markScrollableFormulas(root), 150));
  initToc(root);
  window.addEventListener("hashchange", openHashTarget);
  openHashTarget();
  settleHash();

  const images = await imagesOf(D);
  heroPhoto(root, images);
  renderCredits(root, images);
  initPopovers(root);
  observe(root);
  settleHash();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(settleHash).catch(() => {});
}

boot([]).then(init).catch((err) => console.warn("Serve IQ: method page did not start", err));
