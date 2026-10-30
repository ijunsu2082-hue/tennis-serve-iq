/* Serve IQ hero and "How it works". ES module.
   Static markup (partials hero and how) carries the bound numbers ([data-h], [data-b]) and the count-ups;
   this module adds what needs data in JS: the photo checks and credit line (from images.json), the "?"
   sentences with numbers, and the total test count in step 3. Nothing here runs at import time. */

import { $, $$, setInfo } from "./ui.js";
import { fInt, isNum } from "./data.js";

function photoCheck(img, entry) {
  if (!img) return;
  if (!entry || !entry.src) {
    // no licensed entry for this photo: drop it and keep the plain navy band
    const box = img.closest(".hero-media, .how-media");
    if (box) box.remove(); else img.remove();
    return;
  }
  if (img.getAttribute("src") !== entry.src) img.setAttribute("src", entry.src);
  const drop = () => { const box = img.closest(".hero-media, .how-media"); if (box) box.remove(); };
  if (img.complete && !img.naturalWidth) drop();
  else img.addEventListener("error", drop, { once: true });
}

function heroCredit(D) {
  const link = $("[data-hero-credit]");
  if (!link) return;
  const e = D.scene("hero");
  if (!e) { link.remove(); return; }
  const who = String(e.author || "").trim();
  const lic = e.license || "see credits";
  const full = "Photo: " + (who ? who + ", " : "") + lic;
  // phones get the author without the Flickr location ("X from City, Country"); method.html#credits has it all
  const short = "Photo: " + (who ? who.replace(/\s+from\s.*$/i, "") + ", " : "") + lic;
  const fullEl = $(".hero-credit-full", link), shortEl = $(".hero-credit-short", link);
  if (fullEl && shortEl) { fullEl.textContent = full; shortEl.textContent = short; }
  else link.textContent = full;
  link.setAttribute("title", full);
  link.setAttribute("aria-label", full + ". All photo credits");
}

function infoText(D) {
  const f = D.fmt;
  const H = (k) => D.h(k);
  const th = D.thresholds || {};
  const text = {
    players: f.int(H("men")) + " men and " + f.int(H("women")) + " women, each with at least " +
      f.int(th.min_serves_side) + " charted 1st serves on each side and " + f.int(th.min_matches) + " charted matches.",
    serves: "Charted serves with a recorded direction (wide, body or T), from all charted players, after tiebreak and no-ad deciding points are left out.",
    mix: "Each player counts twice, once per court side. On " + f.int(H("mix_differs_n_s1")) + " of " +
      f.int(H("mix_total_s1")) + " sides, where 1st serves go differs from the league against the same kinds of opponents, after allowing for chance across many tests.",
    win: (function () {
      const s2 = H("costly_s2");
      const tail = f.isNum(s2) && s2 === 0 ? ", and none do on 2nd serves." : " (" + f.int(s2) + " on 2nd serves).";
      return f.int(H("costly_players_s1")) + " of " + f.int(H("players")) +
        " players have a court side where 1st-serve win rate differs by direction at a " + D.b("fdr") +
        " false discovery rate" + tail;
    })(),
  };
  const t = (D.meta || {}).tests || {};
  if (["mix", "win", "bp"].every((k) => isNum(t[k]))) {
    text.tests = f.int(t.mix) + " mix tests compare a player-side with the league. " + f.int(t.win) + " win tests and " +
      f.int(t.bp) + " break-point tests compare directions within the player's own points. Each family keeps a " +
      D.b("fdr") + " false discovery rate.";
    const how = $("[data-how-info]");
    if (how) setInfo(how, text.tests);
  }
  $$("[data-hero-info]").forEach((btn) => {
    const t = text[btn.getAttribute("data-hero-info")];
    if (t) setInfo(btn, t);
  });
}

function setCount(node, v) {
  if (!node || !isNum(v)) return;
  node.textContent = fInt(v);
  node.setAttribute("data-value", String(v));
  node.setAttribute("data-f", "int");
}

/* step stats: charted serves (meta.serves_total) and the number of per-player tests (mix + win + break point) */
function howStats(D) {
  const meta = D.meta || {};
  setCount($("[data-how-total]"), meta.serves_total);
  const t = meta.tests || {};
  const parts = ["mix", "win", "bp"].map((k) => t[k]);
  if (parts.every(isNum)) setCount($("[data-how-tests]"), parts[0] + parts[1] + parts[2]);
}

export function init(ctx) {
  const D = ctx && ctx.data;
  if (!D) return;
  photoCheck($("[data-hero-img]"), D.scene("hero"));
  photoCheck($("[data-how-img]"), D.scene("court"));
  heroCredit(D);
  infoText(D);
  howStats(D);
}
