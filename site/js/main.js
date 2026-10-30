/* Serve IQ boot. ES module.
   index.html (<body data-page="index">) boots itself: data, [data-h] / [data-b] binding, then tool.js and
   numbers.js. Each section module exports init(ctx), ctx = { data, ui }, and is imported with try/catch so
   a missing or broken module never breaks the rest of the page.
   method.html loads js/method.js, which calls boot([]) from here and then renders itself. */

import * as data from "./data.js";
import * as ui from "./ui.js";

/* The data file failed: say so in #status (with a link to the backup app) and add html.no-data, which the
   CSS uses to hide the parts that need the data (.needs-data). */
function showError(err) {
  const status = document.getElementById("status");
  if (status) {
    status.hidden = false;
    status.classList.add("is-error");
    status.textContent = "The data file (data/serveiq.json) could not be loaded: " + (err && err.message ? err.message : "unknown error") +
      ". Reload the page. If it still fails, the same analysis is available at ";
    const a = document.createElement("a");
    a.href = "https://serve-iq.streamlit.app";
    a.textContent = "serve-iq.streamlit.app";
    status.appendChild(a);
    status.appendChild(document.createTextNode("."));
  }
  document.documentElement.classList.add("no-data");
}

let booted = null;
/* boot(modules): runs once per page and resolves to ctx ({ data: D or null, ui }).
   modules: names of ./<name>.js files to import and init(ctx), in order. */
export function boot(modules) {
  if (booted) return booted;
  booted = (async () => {
    let D = null;
    try {
      D = await data.load();
    } catch (err) {
      showError(err);
    }
    const ctx = { data: D, ui };
    if (!D) return ctx;
    D.bind(document);
    document.documentElement.classList.add("data-ready");
    for (const name of modules || []) {
      try {
        const mod = await import("./" + name + ".js");
        if (mod && typeof mod.init === "function") await mod.init(ctx);
      } catch (err) {
        console.warn("Serve IQ: section module " + name + ".js did not start", err);
      }
    }
    // modules may have written new [data-h] / [data-b] elements
    D.bind(document);
    return ctx;
  })();
  return booted;
}

if (document.body && document.body.getAttribute("data-page") === "index") {
  boot(["tool", "match"]);
}
