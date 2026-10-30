/* Serve IQ finding (#findings). ES module.
   One sentence bound from serveiq.json and a waffle of every listed player, one dot each, in the order of
   D.players: one group per tour (a small "Men" / "Women" line starts each), then charted serves. A player
   "passes" when either side of the 1st serve has q_win < thresholds.q, the same rule as
   headline.costly_players_s1. Passing players are blue buttons: hover or focus shows the name, a click loads
   the player into the tool and scrolls to #tool. The dot of the player in the tool has a ring.
   No animation. */

import { SIDES, esc, fInt, isNum, displayName } from "./data.js";
import { $ } from "./ui.js";

let D = null;
let tool = null;

/* passes(p): any side of serve 1 with q_win below the false discovery threshold */
function passes(p) {
  const tr = D.raw.tests.data[p.gender + "|" + p.server + "|1"];
  if (!tr) return false;
  const qth = D.thresholds.q;
  return SIDES.some((side) => {
    const row = tr[side];
    const q = row ? row[D.TESTF.q_win] : null;
    return isNum(q) && q < qth;
  });
}

/* the second-serve clause: the static "nobody does" stays when headline.costly_s2 is 0 */
function secondServeClause() {
  const host = $("#findings-s2");
  const n = D.h("costly_s2");
  if (!host || !isNum(n) || n === 0) return;
  host.innerHTML = "On second serves, only <b>" + esc(fInt(n)) + "</b> " + (n === 1 ? "does" : "do") + ".";
}

function load(name) {
  if (tool && typeof tool.select === "function") tool.select(name, 1);
  else document.dispatchEvent(new CustomEvent("serveiq:select", { detail: { name, serve: 1 } }));
  const sec = document.getElementById("tool");
  if (!sec) return;
  sec.scrollIntoView({ block: "start", behavior: "auto" });
  // keyboard and screen reader users land in the tool too
  if (!sec.hasAttribute("tabindex")) sec.setAttribute("tabindex", "-1");
  sec.focus({ preventScroll: true });
}

const TOUR = { m: "Men", w: "Women" };

function drawWaffle(host) {
  let hits = 0;
  const dot = (p) => {
    if (!passes(p)) return '<span class="waffle-dot" data-name="' + esc(p.server) + '"></span>';
    hits++;
    return '<button type="button" class="waffle-dot is-hit" data-name="' + esc(p.server) + '" aria-label="' + esc(displayName(p.server)) + ', tap to show"></button>';
  };
  const tours = [];
  D.players.forEach((p) => { if (tours.indexOf(p.gender) < 0) tours.push(p.gender); });
  host.innerHTML = tours.map((g) => '<div class="waffle-group"><p class="waffle-label">' + esc(TOUR[g] || g) + "</p>" +
    '<div class="waffle-dots">' + D.players.filter((p) => p.gender === g).map(dot).join("") + "</div></div>").join("");
  host.setAttribute("role", "group");
  host.setAttribute("aria-label", "One dot per player. The " + fInt(hits) + " blue dots are buttons.");
  return hits;
}

/* the name tip over a blue dot, kept inside the waffle's width */
function wireTip(host) {
  const tip = document.createElement("div");
  tip.className = "waffle-tip";
  tip.setAttribute("aria-hidden", "true");
  tip.hidden = true;
  host.appendChild(tip);
  const show = (b) => {
    tip.textContent = displayName(b.getAttribute("data-name"));
    tip.hidden = false;
    const w = host.clientWidth;
    const tw = tip.offsetWidth;
    const cx = b.offsetLeft + b.offsetWidth / 2;
    tip.style.left = Math.max(0, Math.min(w - tw, cx - tw / 2)) + "px";
    tip.style.top = (b.offsetTop - tip.offsetHeight - 6) + "px";
  };
  const hide = () => { tip.hidden = true; };
  host.addEventListener("pointerover", (e) => {
    const b = e.target.closest("button.waffle-dot");
    if (b) show(b);
  });
  host.addEventListener("pointerout", (e) => {
    const b = e.target.closest("button.waffle-dot");
    if (b && !b.contains(e.relatedTarget)) hide();
  });
  host.addEventListener("focusin", (e) => {
    const b = e.target.closest("button.waffle-dot");
    if (b) show(b);
  });
  host.addEventListener("focusout", hide);
  // Esc closes the name tip, also when the mouse opened it
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  host.addEventListener("click", (e) => {
    const b = e.target.closest("button.waffle-dot");
    if (!b) return;
    hide();
    load(b.getAttribute("data-name"));
  });
}

export async function init(ctx) {
  D = ctx.data;
  tool = ctx.tool || null;
  const host = $("#waffle");
  if (!host) return;
  const hits = drawWaffle(host);
  const want = D.h("costly_players_s1");
  if (hits !== want) console.warn("Serve IQ: the waffle has " + hits + " blue dots but headline.costly_players_s1 is " + want);
  secondServeClause();
  wireTip(host);
  // ring the dot of the player shown in the tool, now and after every change
  const ring = (name) => {
    host.querySelectorAll(".waffle-dot.is-current").forEach((d) => { d.classList.remove("is-current"); d.removeAttribute("aria-current"); });
    const d = name ? host.querySelector('.waffle-dot[data-name="' + (window.CSS && CSS.escape ? CSS.escape(name) : name) + '"]') : null;
    if (d) { d.classList.add("is-current"); if (d.tagName === "BUTTON") d.setAttribute("aria-current", "true"); }
  };
  document.addEventListener("serveiq:shown", (e) => ring(e.detail && e.detail.name));
  if (tool && tool.state) ring(tool.state.name);
}
