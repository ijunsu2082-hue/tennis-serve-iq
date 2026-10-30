/* Serve IQ motion helper. ES module.
   The only motion on index.html is the court arrows: width and label tween when the player or the serve
   changes. tween() drives that. */

import { reducedMotion } from "./ui.js";

/* tween(duration, onFrame(k), onDone): eases k from 0 to 1 (ease-out cubic) and calls onFrame each frame.
   Reduced motion or duration 0 calls onFrame(1) at once. Returns a cancel function. */
export function tween(duration, onFrame, onDone) {
  if (!(duration > 0) || reducedMotion()) {
    onFrame(1);
    if (onDone) onDone();
    return () => {};
  }
  let raf = 0;
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / duration);
    onFrame(1 - Math.pow(1 - k, 3));
    if (k < 1) raf = requestAnimationFrame(step);
    else { raf = 0; if (onDone) onDone(); }
  };
  raf = requestAnimationFrame(step);
  return () => { if (raf) cancelAnimationFrame(raf); raf = 0; };
}
