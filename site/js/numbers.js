/* Serve IQ "By the numbers" (#numbers). ES module.
   Two panels:
     #vs     body serves compared with wide serves by the same player on the same side (league.within via
             headline body_in_* and body_win_*): one bar for "go in", one for "points won", per tour.
     #clear  a ring: players with a 1st-serve side whose win test passes (q_win below the threshold), out of
             all listed players (headline costly_players_s1 and players), plus one chip per such player; a
             chip loads that player into the tool and scrolls to it.
   Bars and the ring fill once when the section scrolls into view (at once under reduced motion). */

import { SIDES, esc, fInt, fNum, isNum, displayName, surname } from "./data.js";
import { $, $$, reducedMotion } from "./ui.js";

let D = null;
const VS_MAX = 12;   // the bars are full at 12 per 100

function vsHtml() {
  const row = (what, v, color) => {
    if (!isNum(v)) return "";
    const w = (100 * Math.min(Math.abs(v) / VS_MAX, 1)).toFixed(1);
    return "<div class='vs-row'><div class='vs-label'><span class='what'>" + what + "</span><span class='val tnum' style='color:" + color + "'>" +
      fNum(Math.abs(v), 1) + " " + (v > 0 ? "more" : "fewer") + "</span></div>" +
      "<div class='vs-bars one'><span><b data-w='" + w + "' style='background:" + color + "'></b></span></div></div>";
  };
  let h = "<h3>Body serves are safer but win less</h3>";
  [["m", "Men"], ["w", "Women"]].forEach(([g, label]) => {
    h += "<p class='vs-tour'>" + label + "</p>" +
      row("Serves that go in, per 100", D.h("body_in_" + g + "1.coef"), "var(--green)") +
      row("Points won, per 100", D.h("body_win_" + g + "1.coef"), "var(--red)");
  });
  return h + "<p class='note'>Body 1st serves compared with wide 1st serves by the same player from the same side. Wide and T win about the same.</p>";
}

/* clear(p): a 1st-serve side with q_win below the threshold, the rule behind headline.costly_players_s1 */
function clear(p) {
  const tr = D.raw.tests.data[p.gender + "|" + p.server + "|1"];
  if (!tr) return false;
  return SIDES.some((s) => tr[s] && isNum(tr[s][D.TESTF.q_win]) && tr[s][D.TESTF.q_win] < D.thresholds.q);
}

function clearHtml(list) {
  const n = D.h("costly_players_s1"), all = D.h("players"), s2 = D.h("costly_s2");
  const R = 54, C = 2 * Math.PI * R;
  const frac = isNum(n) && isNum(all) && all > 0 ? n / all : 0;
  const initials = (name) => displayName(name).split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  const chip = (p) => {
    const ph = D.photo(p.server);
    return "<button type='button' class='chip' data-name='" + esc(p.server) + "' aria-pressed='false'>" +
      (ph && ph.src ? "<img alt='' src='" + esc(ph.src) + "' width='24' height='24' loading='lazy'>" : "<span class='ini' aria-hidden='true'>" + esc(initials(p.server)) + "</span>") +
      esc(surname(p.server)) + "</button>";
  };
  return "<h3>Does a serve habit cost pros points?</h3>" +
    "<div class='ring-row'><div class='ring' role='img' aria-label='" + fInt(n) + " of " + fInt(all) + " players'>" +
    "<svg viewBox='0 0 132 132' aria-hidden='true'><circle cx='66' cy='66' r='" + R + "' fill='none' stroke='var(--track)' stroke-width='14'/>" +
    "<circle class='ring-fill' cx='66' cy='66' r='" + R + "' fill='none' stroke='var(--blue)' stroke-width='14' stroke-linecap='round'" +
    " stroke-dasharray='" + C.toFixed(1) + "' stroke-dashoffset='" + C.toFixed(1) + "' data-to='" + (C * (1 - frac)).toFixed(1) + "'" +
    " style='transition: stroke-dashoffset 1.2s cubic-bezier(.2,.8,.2,1)'/></svg>" +
    "<div class='in'><span class='big tnum'>" + fInt(n) + "</span><span class='of tnum'>of " + fInt(all) + "</span></div></div>" +
    "<div class='ring-text'><p>Only <b class='tnum'>" + fInt(n) + "</b> pros win clearly more 1st-serve points by aiming one way.</p>" +
    "<p>On 2nd serves: <b class='tnum'>" + fInt(s2) + "</b>. Most pros have a favorite spot, but it rarely costs them.</p></div></div>" +
    "<p class='chips-h'>Tap a player to see them on the court</p>" +
    "<div class='chips'>" + list.map(chip).join("") + "</div>";
}

function fill() {
  $$("#numbers [data-w]").forEach((b) => { b.style.width = b.getAttribute("data-w") + "%"; });
  $$("#numbers .ring-fill").forEach((c) => { c.style.strokeDashoffset = c.getAttribute("data-to"); });
}

export async function init(ctx) {
  D = ctx.data;
  $("#vs").innerHTML = vsHtml();
  const list = D.players.filter(clear);
  $("#clear").innerHTML = clearHtml(list);

  $("#clear").addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    const name = b.getAttribute("data-name");
    if (ctx.tool) ctx.tool.select(name, 1);
    else document.dispatchEvent(new CustomEvent("serveiq:select", { detail: { name, serve: 1 } }));
    const t = $("#tool");
    t.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
    t.focus({ preventScroll: true });
  });
  document.addEventListener("serveiq:shown", (e) => {
    const name = e.detail && e.detail.name;
    $$("#clear .chip").forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute("data-name") === name)));
  });

  if (reducedMotion() || !("IntersectionObserver" in window)) { fill(); return; }
  const io = new IntersectionObserver((es) => {
    if (es.some((x) => x.isIntersecting)) { fill(); io.disconnect(); }
  }, { threshold: 0.25 });
  io.observe($("#numbers"));
}
