/* Serve IQ boot. ES module.
   index.html (<body data-page="index">) boots itself: theme, nav, motion, data, [data-h] binding, then
   hero.js, players.js and league.js. Each section module exports init(ctx), ctx = { data, ui, anim },
   and is imported with try/catch so a missing or broken module never breaks the rest of the page.
   method.html loads js/method.js, which calls boot([]) from here and then renders itself. */

import * as data from "./data.js";
import * as ui from "./ui.js";
import * as anim from "./anim.js";

function showError(err) {
  const status = document.getElementById("status");
  const msg = "The data file (data/serveiq.json) could not be loaded: " + (err && err.message ? err.message : "unknown error") +
    ". Reload the page. If it still fails, the same analysis is available at serve-iq.streamlit.app.";
  if (status) {
    status.hidden = false;
    status.classList.add("is-error");
    status.textContent = msg;
  }
  document.documentElement.classList.add("no-data");
}

/* A plain anchor (#league, #how) is reached by the browser while the sections above it are still empty;
   once they are filled in, the target has moved down. settleHash() scrolls back to it, unless the
   visitor has already scrolled or pressed a key. State hashes (#p=...) are left to players.js. */
let userMoved = false;
["wheel", "touchstart", "keydown", "mousedown"].forEach((t) =>
  window.addEventListener(t, () => { userMoved = true; }, { passive: true, once: true }));
function settleHash() {
  const id = decodeURIComponent((location.hash || "").slice(1));
  if (!id || id.indexOf("=") >= 0) return;
  const target = document.getElementById(id);
  if (!target) return;
  const go = () => { if (!userMoved) target.scrollIntoView({ block: "start", behavior: "instant" }); };
  requestAnimationFrame(go);
  // late photos can still shift the layout a little
  if (document.readyState !== "complete") window.addEventListener("load", () => requestAnimationFrame(go), { once: true });
}

let booted = null;
/* boot(modules): runs once per page and resolves to ctx ({ data: D or null, ui, anim }).
   modules: names of ./<name>.js files to import and init(ctx), in order. */
export function boot(modules) {
  if (booted) return booted;
  booted = (async () => {
    ui.initTheme();
    ui.initNav();
    anim.init();
    ui.initPopovers(document);
    let D = null;
    try {
      D = await data.load();
    } catch (err) {
      showError(err);
    }
    const ctx = { data: D, ui, anim };
    if (!D) return ctx;
    D.bind(document);
    document.documentElement.classList.add("data-ready");
    anim.observe(document);
    for (const name of modules || []) {
      try {
        const mod = await import("./" + name + ".js");
        if (mod && typeof mod.init === "function") await mod.init(ctx);
      } catch (err) {
        console.warn("Serve IQ: section module " + name + ".js did not start", err);
      }
    }
    // modules may have injected new [data-h], "?" buttons or reveal targets
    D.bind(document);
    ui.initPopovers(document);
    anim.observe(document);
    if (modules && modules.length) settleHash();
    return ctx;
  })();
  return booted;
}

if (document.body && document.body.getAttribute("data-page") === "index") {
  boot(["hero", "players", "league"]);
}
