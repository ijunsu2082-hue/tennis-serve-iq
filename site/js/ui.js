/* Serve IQ UI helpers. ES module.
   DOM shortcuts, debounce, the reduced-motion check and the SVG element helper (js/court.js). */

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

// ------------------------------------------------------------ SVG helpers

export const SVGNS = "http://www.w3.org/2000/svg";
export function el(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  if (attrs) Object.keys(attrs).forEach((k) => { if (attrs[k] != null) e.setAttribute(k, attrs[k]); });
  if (parent) parent.appendChild(e);
  return e;
}
