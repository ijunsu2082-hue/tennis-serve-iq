/* Serve IQ method page (method.html). ES module, the page's only script besides KaTeX.
   boot([]) from main.js loads the data and fills every [data-h] / [data-b], then init(ctx) here renders the
   formulas with KaTeX (the raw LaTeX stays in <code> if KaTeX is missing or fails), makes a formula that is
   wider than the column scroll sideways by keyboard, opens a folded formula a link points to, and writes the
   photo credits (players only) from images.json. */
import { boot } from "./main.js";
import { $, $$, debounce } from "./ui.js";
import { esc, displayName } from "./data.js";

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
    if (!box.clientWidth) return; // inside a folded formula: measured again when it opens
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

// a link to a formula (method.html#f7) opens it if the reader folded it
function openHashTarget() {
  const h = decodeURIComponent((location.hash || "").slice(1));
  const t = h ? document.getElementById(h) : null;
  if (!t) return;
  const card = t.matches("details") ? t : t.closest("details");
  if (card && !card.open) card.open = true;
  t.scrollIntoView({ block: "start", behavior: "auto" });
}

// stop landing on the anchor again once the reader has started to move around
let readerMoved = false;
["wheel", "touchstart", "keydown", "mousedown"].forEach((ev) =>
  window.addEventListener(ev, () => { readerMoved = true; }, { once: true, passive: true }));

// ------------------------------------------------------------ photo credits, players only

async function playerImages(D) {
  if (D && D.images) return D.images.players || {};
  try {
    const r = await fetch("data/images.json", { credentials: "same-origin" });
    if (!r.ok) return {};
    const j = await r.json();
    return (j && j.players) || {};
  } catch (e) {
    return {};
  }
}

function licenseHTML(e) {
  const name = esc(e.license || "License not stated");
  return e.license_url ? '<a href="' + esc(e.license_url) + '" rel="license noopener">' + name + "</a>" : name;
}

function renderCredits(root, players) {
  const host = $("#credits-list", root);
  if (!host) return;
  const names = Object.keys(players).filter((k) => players[k] && players[k].src)
    .sort((a, b) => displayName(a).localeCompare(displayName(b), "en", { sensitivity: "base" }));
  if (!names.length) {
    host.innerHTML = '<p class="m-muted">No photo credits to show.</p>';
    return;
  }
  host.innerHTML = '<div class="credit-scroll" tabindex="0" role="region" aria-label="Photo credits">' +
    '<table class="credit-table"><caption class="visually-hidden">Player photo credits</caption>' +
    '<thead><tr><th scope="col">Player</th><th scope="col">Photo by</th><th scope="col">License</th><th scope="col">File</th></tr></thead><tbody>' +
    names.map((n) => {
      const e = players[n];
      const shown = displayName(n);   // images.json keys use the data file's spelling ("John Mcenroe")
      const file = e.source
        ? '<a href="' + esc(e.source) + '" rel="noopener" aria-label="File page for the photo of ' + esc(shown) + ' on Wikimedia Commons">File page</a>'
        : "";
      return '<tr><th scope="row">' + esc(shown) + "</th><td>" + esc(e.author || "Author not stated") + "</td><td>" + licenseHTML(e) + "</td><td>" + file + "</td></tr>";
    }).join("") +
    "</tbody></table></div>";
}

// ------------------------------------------------------------ init

export async function init(ctx) {
  const root = document.getElementById("method");
  if (!root) return;
  const D = ctx && ctx.data;

  renderTex(root);
  markScrollableFormulas(root);
  $$("details.fcard", root).forEach((c) => c.addEventListener("toggle", () => { if (c.open) markScrollableFormulas(c); }));
  window.addEventListener("resize", debounce(() => markScrollableFormulas(root), 150));
  window.addEventListener("hashchange", openHashTarget);

  renderCredits(root, await playerImages(D));
  // KaTeX and the credits table moved the page under the URL's anchor; land on it again
  if (location.hash && !readerMoved) openHashTarget();
}

boot([]).then(init).catch((err) => console.warn("Serve IQ: method page did not start", err));
