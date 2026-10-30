#!/usr/bin/env node
// Serve IQ static-site smoke test (headless browser), v4: one court screen plus one finding.
//
// Usage:  node scripts/smoke_site.mjs <siteDir> <outDir> [port]
//
// Playwright is NOT installed in this repo. The script loads it from, in order:
//   1. normal module resolution,
//   2. $PLAYWRIGHT_DIR (a folder with node_modules/playwright),
//   4. /private/tmp/serveiq-harness  (npm i playwright@1.49.1 there).
// Browser: system Google Chrome (channel "chrome") first, else Playwright's bundled Chromium, else
// $CHROME_PATH, else the Chrome app path on macOS. The script serves <siteDir> itself on 127.0.0.1
// (port 0 = any free port), so it never needs another server.
//
// What it checks (each line of the JSON summary is one check), against the DOM contract of the v4 spec:
//   index.html at 1440x900 and 390x844:
//     - the page boots (html.data-ready), the default player is Rafael Nadal on 1st serve
//     - zero console errors, page errors, "Serve IQ:" module warnings and failed requests
//     - no horizontal page scroll; no banned characters, placeholder text (NaN, undefined, n/a, null),
//       the word "league", he/she pronouns or banned voice words anywhere in the rendered text
//     - #court has 2 svg panels and 6 .arrow[data-side][data-dir] with tabindex 0 and an aria-label;
//       focusing each arrow shows #court-tip with text, inside the viewport; Tab moves to the next arrow;
//       each panel's arrows are in left-to-right DOM (Tab) order; every share label is within 10px (390) or
//       12px (1440) of its own arrowhead's center and no two labels overlap
//     - the player photo loads for Nadal
//     - page height: at most 1,900px at 1440, at most 3,600px at 390 (default player and every quick pick)
//     - 390 only: on the first screen (scrollY 0) the search box, the 1st/2nd switch, the bottom of the
//       court panels and the top of the first sentence are all within 844px
//   index.html interaction (1440):
//     - #players datalist has one option per player (447); typing a full name into #player-input loads it;
//       a name that matches nobody keeps the typed text, shows "No charted player matches ..." and keeps the
//       loaded player, and Esc restores the loaded name (names compare by nameKey(), as the page shows display
//       spellings such as "John McEnroe")
//     - #quick-picks has 5 links and each one loads the player it names
//     - #serve-switch: 2 buttons; clicking 2nd serve flips aria-pressed and changes the arrow share labels
//     - #sentences has 1 to 4 <p> for every quick pick and every waffle player, on both serves, each with
//       no "league", no banned characters, no he/she and no placeholder text
//     - details#sure is closed by default, opens, and has at least 2 tables with rows (Nadal)
//     - the waffle has 447 .waffle-dot and 24 .waffle-dot.is-hit (headline.costly_players_s1), every hit is
//       a <button> with an aria-label "<name>, tap to show"; clicking one loads that player on 1st serve and
//       scrolls #tool into view
//     - a player with no photo hides #player-photo
//   data/serveiq.json aborted, index at 1440 and 390: html.no-data, #status shows the error with a link to the
//     backup app, and #tool, #findings and the intro's number line are hidden.
//   method.html at 1440 and 390: boots, no errors or failed requests, no horizontal scroll, no banned
//     characters, KaTeX renders every .fx formula block (13 formulas plus the hypothesis line) with no
//     errors, the credits table has one row per images.json player.
//   Reduced motion (prefers-reduced-motion: reduce), index at 1440 and 390: boots with Nadal, the serve
//     switch works and the arrow labels show their final values at once (no tween), no errors.
//   Screenshots go to <outDir>/v4-<page>-<width>.png (full page) and v4-<page>-<width>-first.png (first screen).
//
// Prints a JSON summary to stdout and exits 1 if any check fails.

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

// ---------- args ----------
const [siteDirArg, outDirArg, portArg] = process.argv.slice(2);
if (!siteDirArg || !outDirArg) {
  console.error('usage: node scripts/smoke_site.mjs <siteDir> <outDir> [port]');
  process.exit(2);
}
const siteDir = path.resolve(siteDirArg);
const outDir = path.resolve(outDirArg);
const port = portArg ? Number(portArg) : 0;
for (const f of ['index.html', 'method.html', 'data/serveiq.json', 'data/images.json']) {
  if (!fs.existsSync(path.join(siteDir, f))) {
    console.error(`no ${f} in ${siteDir}`);
    process.exit(2);
  }
}
fs.mkdirSync(outDir, { recursive: true });
const DATA = JSON.parse(fs.readFileSync(path.join(siteDir, 'data/serveiq.json'), 'utf8'));
const IMAGES = JSON.parse(fs.readFileSync(path.join(siteDir, 'data/images.json'), 'utf8'));
const N_CREDITS = Object.keys(IMAGES.players || {}).length;
const N_PLAYERS = DATA.headline.players;
const N_HITS = DATA.headline.costly_players_s1;
const SERVERS = (() => {
  const f = DATA.players.fields;
  const si = f.indexOf('server');
  return DATA.players.rows.map((r) => r[si]);
})();
const DEFAULT = 'Rafael Nadal';
// names: the page shows a display spelling for some players ("John McEnroe" for the data's "John Mcenroe",
// "Jo-Wilfried Tsonga"), so names are compared by the same key js/data.js nameKey() uses
const nameKey = (s) => String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim()
  .replace(/['\u2019]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
const sameName = (a, b) => nameKey(a) === nameKey(b);
const LIMITS = { 1440: { w: 1440, h: 900, maxHeight: 1900 }, 390: { w: 390, h: 844, maxHeight: 3600 } };

// ---------- load playwright ----------
const HARNESS_DIRS = [
  process.env.PLAYWRIGHT_DIR,
  '/private/tmp/serveiq-harness',
].filter(Boolean);
async function loadPlaywright() {
  try { return await import('playwright'); } catch { /* fall through */ }
  for (const d of HARNESS_DIRS) {
    try { return createRequire(path.join(path.resolve(d), 'noop.js'))('playwright'); } catch { /* next */ }
  }
  console.error('playwright not found. Run: mkdir -p /private/tmp/serveiq-harness && cd /private/tmp/serveiq-harness && npm init -y && npm i playwright@1.49.1');
  process.exit(2);
}
const { chromium } = await loadPlaywright();

async function launchBrowser() {
  const attempts = [
    ['system chrome (channel)', { channel: 'chrome' }],
    ['bundled chromium', {}],
  ];
  if (process.env.CHROME_PATH) attempts.push(['CHROME_PATH', { executablePath: process.env.CHROME_PATH }]);
  attempts.push(['system chrome (path)', { executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' }]);
  const errs = [];
  for (const [name, opts] of attempts) {
    try { const b = await chromium.launch({ headless: true, ...opts }); return { browser: b, browserName: `${name} ${b.version()}` }; }
    catch (e) { errs.push(`${name}: ${String(e.message).split('\n')[0]}`); }
  }
  console.error('no usable browser:\n' + errs.join('\n'));
  process.exit(2);
}

// ---------- static server ----------
const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2', '.map': 'application/json',
};
function startServer() {
  const server = http.createServer((req, res) => {
    try {
      const p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      let file = path.normalize(path.join(siteDir, p));
      if (!file.startsWith(siteDir)) { res.writeHead(403); return res.end('forbidden'); }
      if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
      if (!fs.existsSync(file)) { res.writeHead(404, { 'content-type': 'text/plain' }); return res.end('not found'); }
      res.writeHead(200, { 'content-type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-store' });
      fs.createReadStream(file).pipe(res);
    } catch (e) { res.writeHead(500); res.end(String(e)); }
  });
  return new Promise((resolve) => server.listen({ port, host: '127.0.0.1', backlog: 511 }, () => resolve(server)));
}

// ---------- helpers ----------
const checks = [];   // {name, pass, detail}
const warnings = [];
let tagNow = '';
function check(name, pass, detail) { checks.push({ name, pass: !!pass, ...(detail !== undefined ? { detail } : {}) }); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function attachCollectors(page) {
  const log = { consoleErrors: [], moduleWarnings: [], pageErrors: [], failedRequests: [] };
  page.on('console', (m) => {
    const where = (m.location() && m.location().url) || '';
    const line = `${m.text().slice(0, 300)}${where ? ' @ ' + where : ''}`;
    if (m.type() === 'warning' && /Serve IQ/.test(m.text())) { log.moduleWarnings.push(line); return; }
    if (m.type() !== 'error') return;
    if (/\/favicon\.ico$/.test(where)) { warnings.push(`${tagNow}: ${line}`); return; }
    log.consoleErrors.push(line);
  });
  page.on('pageerror', (e) => log.pageErrors.push(String(e.message || e).slice(0, 300)));
  page.on('requestfailed', (r) => log.failedRequests.push(`${r.failure()?.errorText || 'failed'} ${r.url()}`));
  page.on('response', (r) => { if (r.status() >= 400) log.failedRequests.push(`HTTP ${r.status()} ${r.url()}`); });
  return log;
}
const errorsOf = (log) => [...log.consoleErrors, ...log.pageErrors, ...log.moduleWarnings];

async function newPage(browser, opts) {
  const ctx = await browser.newContext({
    viewport: { width: opts.w, height: opts.h }, colorScheme: 'light',
    isMobile: opts.w < 768, hasTouch: opts.w < 768, reducedMotion: opts.reduce ? 'reduce' : 'no-preference',
  });
  const page = await ctx.newPage();
  const log = attachCollectors(page);
  return { ctx, page, log };
}

async function waitForIndex(page) {
  await page.waitForFunction(() => document.documentElement.classList.contains('data-ready') ||
    document.documentElement.classList.contains('no-data'), null, { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => {
    const n = document.getElementById('player-name');
    return n && n.textContent.trim() && document.querySelectorAll('#court .arrow').length >= 6 &&
      document.querySelectorAll('#sentences p').length > 0;
  }, null, { timeout: 15000 }).catch(() => {});
  // findings.js runs after tool.js; give it a moment, then settle the arrow tween
  await page.waitForFunction(() => document.querySelectorAll('.waffle .waffle-dot').length > 0, null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(700);
}

async function waitForMethod(page) {
  await page.waitForFunction(() => document.documentElement.classList.contains('data-ready') ||
    document.documentElement.classList.contains('no-data'), null, { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => document.querySelectorAll('.fx .katex, .fx.tex-raw, .tex-raw').length >= document.querySelectorAll('.fx').length &&
    document.querySelectorAll('#credits-list tbody tr, #credits-list li').length > 0, null, { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(300);
}

// All text in the DOM, including hidden parts and aria-labels / titles / tooltips, minus KaTeX, script and style.
async function scanText(page) {
  return page.evaluate(() => {
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,.katex,.katex-mathml,annotation,.tex,.fx').forEach((n) => n.remove());
    const nodes = [];
    const tw = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) if (n.nodeValue.trim()) nodes.push(n.nodeValue.trim());
    const attrs = [...document.querySelectorAll('[aria-label],[title],[alt],[placeholder],[data-info]')]
      .filter((e) => !e.closest('.katex') && !e.closest('.fx'))
      .map((e) => ['aria-label', 'title', 'alt', 'placeholder', 'data-info'].map((a) => e.getAttribute(a)).filter(Boolean).join(' '));
    const meta = [...document.querySelectorAll('meta[name="description"]')].map((m) => m.content);
    return (document.title || '') + '\n' + meta.join('\n') + '\n' + nodes.join('\n') + '\n' + attrs.join('\n');
  });
}

const BANNED = [
  [/—/g, 'em dash'], [/–/g, 'en dash'], [/−/g, 'minus sign'], [/×/g, 'times sign'],
  [/[←-⇿⟰-⟿⤀-⥿]/g, 'arrow'], [/·/g, 'middle dot'], [/~/g, 'tilde'],
  [/[⌀-⏿☀-➿⬀-⯿]/g, 'pictograph'], [/[\u{1F000}-\u{1FAFF}]/gu, 'emoji'], [/️/g, 'variation selector'],
  [/guess[ _-]?rate/gi, 'old term "guess rate"'], [/\bhonest(ly)?\b/gi, 'honest'], [/\bnot only\b/gi, 'not only'],
  [/\bcosts? nothing\b/gi, 'costs nothing'], [/\bdive into\b/gi, 'dive into'],
  [/\bunlock/gi, 'unlock'], [/[?][ \t]*(Yes|No)\b/g, '"X? Yes/No"'], [/\b(favourite|colour|analys(e|ed|es|ing)|behaviour|centre)\b/gi, 'British spelling'],
];
// index.html only (spec voice rules): "league" is "typical pro" there, no he/she, no buzzwords
const INDEX_BANNED = [
  [/\bleague\b/gi, '"league" (say "typical pro")'],
  [/\b(he|she|him|her|his|hers|himself|herself)\b/gi, 'he/she pronoun'],
  [/\b(insights?|patterns?|under the hood|dive)\b/gi, 'banned voice word'],
];
const PLACEHOLDER = [[/\bNaN\b/g, 'NaN'], [/\bundefined\b/g, 'undefined'], [/\bn\/a\b/g, 'n/a'], [/\[object /g, '[object'], [/\bnull\b/g, 'null'], [/\bInfinity\b/g, 'Infinity']];
function hits(txt, list) {
  const out = [];
  for (const [re, label] of list) {
    const m = txt.match(re);
    if (m) {
      const i = txt.search(re);
      out.push({ label, count: m.length, context: txt.slice(Math.max(0, i - 50), i + 50).replace(/\s+/g, ' ') });
    }
  }
  return out;
}

async function overflow(page) {
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    wideEls: [...document.querySelectorAll('body *')].filter((e) => {
      const r = e.getBoundingClientRect();
      if (r.width === 0 || getComputedStyle(e).position === 'fixed') return false;
      for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
        if (getComputedStyle(p).overflowX !== 'visible') return false; // clipped or scrolls inside its own box
      }
      return r.right > window.innerWidth + 1 || r.left < -1;
    }).slice(0, 5).map((e) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${typeof e.className === 'string' && e.className ? '.' + e.className.split(' ')[0] : ''} right=${Math.round(e.getBoundingClientRect().right)}`),
  }));
}

const shareLabels = (page) => page.evaluate(() => [...document.querySelectorAll('#court .arrow')].map((a) => {
  const s = a.querySelector('.a-share');
  return `${a.getAttribute('data-side')}/${a.getAttribute('data-dir')}=${s ? s.textContent.trim() : ''}`;
}));
const pressed = (page) => page.evaluate(() => [...document.querySelectorAll('#serve-switch button[aria-pressed]')].map((b) => b.getAttribute('aria-pressed')));
const nameNow = (page) => page.evaluate(() => (document.getElementById('player-name')?.textContent || '').replace(/\s+/g, ' ').trim());

// load a player the way a person does it: select the search text, type the name (input events fire per key)
async function typePlayer(page, name) {
  const inp = page.locator('#player-input');
  await inp.scrollIntoViewIfNeeded();
  await inp.click();
  await inp.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  await inp.press('Backspace');
  await inp.pressSequentially(name, { delay: 8 });
  await page.waitForTimeout(700);
  return nameNow(page);
}

// the six arrows: focusable, labelled, tooltip on focus inside the viewport
async function arrowChecks(page, tag) {
  const arrows = await page.evaluate(() => [...document.querySelectorAll('#court .arrow[data-side][data-dir]')].map((a) => ({
    side: a.getAttribute('data-side'), dir: a.getAttribute('data-dir'), tab: a.getAttribute('tabindex'),
    label: a.getAttribute('aria-label') || '',
  })));
  const panels = await page.locator('#court svg').count();
  check(`${tag}: #court has 2 svg panels and 6 arrows with data-side / data-dir`, panels === 2 && arrows.length === 6 &&
    new Set(arrows.map((a) => a.side + a.dir)).size === 6, { panels, arrows: arrows.length });
  check(`${tag}: every arrow is focusable (tabindex 0) with an aria-label`, arrows.length === 6 && arrows.every((a) => a.tab === '0' && a.label.length > 10),
    arrows.map((a) => `${a.side}/${a.dir} tab=${a.tab} label=${a.label.length}`));
  // Tab order: in each panel the arrows come in the DOM left to right as drawn, whatever the shares are
  const order = await page.evaluate(() => [...document.querySelectorAll('#court svg')].map((svg) =>
    [...svg.querySelectorAll('.arrow')].map((a) => { const r = a.querySelector('.a-head').getBoundingClientRect(); return { dir: a.getAttribute('data-dir'), x: Math.round((r.left + r.right) / 2) }; })));
  check(`${tag}: the arrows of each panel are in left-to-right order (Tab order)`, order.length === 2 && order.every((p) => p.every((a, i) => i === 0 || a.x > p[i - 1].x)),
    order.map((p) => p.map((a) => a.dir + '@' + a.x).join(' ')));
  // every share label sits over its own arrowhead (no label slid over a neighbor) and no two labels overlap
  const labels = await page.evaluate(() => [...document.querySelectorAll('#court svg')].map((svg) => [...svg.querySelectorAll('.arrow')].map((a) => {
    const s = a.querySelector('.a-share').getBoundingClientRect(), w = a.querySelector('.a-word').getBoundingClientRect(), h = a.querySelector('.a-head').getBoundingClientRect();
    return { id: a.getAttribute('data-side') + '/' + a.getAttribute('data-dir'), off: Math.round(Math.abs((s.left + s.right) / 2 - (h.left + h.right) / 2)),
      box: [Math.min(s.left, w.left), Math.min(s.top, w.top), Math.max(s.right, w.right), Math.max(s.bottom, w.bottom)] };
  })));
  const flat = labels.flat();
  const overlap = (a, b) => a.box[0] < b.box[2] && b.box[0] < a.box[2] && a.box[1] < b.box[3] && b.box[1] < a.box[3];
  const clashes = labels.flatMap((p) => p.flatMap((a, i) => p.slice(i + 1).filter((b) => overlap(a, b)).map((b) => a.id + ' x ' + b.id)));
  // 10px on the phone (review finding); 12px on desktop, where a steep wide arrow's head box sits a little
  // off its tip and the label is straight above the tip
  const maxOff = /390/.test(tag) ? 10 : 12;
  check(`${tag}: every share label is within ${maxOff}px of its own arrowhead and no labels overlap`, flat.length === 6 && flat.every((a) => a.off <= maxOff) && clashes.length === 0,
    { offsets: flat.map((a) => a.id + '=' + a.off), clashes });
  const tips = [];
  for (let i = 0; i < arrows.length; i++) {
    await page.locator('#court .arrow').nth(i).focus();
    await page.waitForTimeout(120);
    tips.push(await page.evaluate(() => {
      const t = document.getElementById('court-tip');
      if (!t) return { exists: false };
      const r = t.getBoundingClientRect();
      return { exists: true, hidden: t.hidden || getComputedStyle(t).display === 'none', text: t.textContent.trim(),
        left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom),
        vw: window.innerWidth, vh: window.innerHeight };
    }));
  }
  const tipOk = (t) => t.exists && !t.hidden && t.text.length > 10 && t.left >= 0 && t.right <= t.vw && t.top >= 0 && t.bottom <= t.vh;
  check(`${tag}: focusing each arrow shows #court-tip with text, inside the viewport`, tips.length === 6 && tips.every(tipOk),
    tips.filter((t) => !tipOk(t)).slice(0, 3));
  check(`${tag}: tooltips name the direction ("Out wide", "At the body", "Down the T")`,
    tips.length === 6 && tips.every((t) => /^(Out wide|At the body|Down the T)\b/.test(t.text || '')), tips.map((t) => (t.text || '').slice(0, 40)));
  await page.locator('#court .arrow').first().focus();
  await page.keyboard.press('Tab');
  await page.waitForTimeout(150);
  const next = await page.evaluate(() => { const e = document.activeElement; return e && e.classList.contains('arrow') ? e.getAttribute('data-side') + '/' + e.getAttribute('data-dir') : (e ? e.tagName : null); });
  check(`${tag}: Tab moves from one arrow to the next`, /\//.test(next || ''), next);
  await page.keyboard.press('Escape');
  await page.evaluate(() => document.activeElement && document.activeElement.blur && document.activeElement.blur());
  await page.waitForTimeout(100);
  return tips;
}

// ---------- main ----------
const server = await startServer();
const base = `http://127.0.0.1:${server.address().port}/`;
const { browser, browserName } = await launchBrowser();
const runs = [];

try {
  // warm-up: one throwaway load so the first measured run is not a cold start
  {
    const { ctx, page } = await newPage(browser, { w: 1440, h: 900 });
    await page.goto(base, { waitUntil: 'load', timeout: 30000 }).catch(() => {});
    await waitForIndex(page);
    await ctx.close();
  }

  // ---------- index.html at both widths ----------
  for (const w of [1440, 390]) {
    const L = LIMITS[w];
    const tag = `index-${w}`;
    tagNow = tag;
    const { ctx, page, log } = await newPage(browser, { w: L.w, h: L.h });
    let gotoErr = null;
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch((e) => { gotoErr = e.message; });
    await waitForIndex(page);
    const ready = await page.evaluate(() => document.documentElement.classList.contains('data-ready'));
    const name = await nameNow(page);
    const pr = await pressed(page);
    check(`${tag}: page boots and binds the data`, !gotoErr && ready, gotoErr || { ready });
    check(`${tag}: default player is ${DEFAULT} on 1st serve`, name === DEFAULT && pr.length === 2 && pr[0] === 'true' && pr[1] === 'false', { name, pressed: pr });
    const photo = await page.evaluate(() => { const i = document.getElementById('player-photo'); return i ? { hidden: i.hidden, ok: i.complete && i.naturalWidth > 0, w: Math.round(i.getBoundingClientRect().width) } : null; });
    check(`${tag}: ${DEFAULT}'s photo loads, about 72 to 88px`, photo && !photo.hidden && photo.ok && photo.w >= 64 && photo.w <= 96, photo);

    // first screen
    await page.screenshot({ path: path.join(outDir, `v4-${tag}-first.png`), fullPage: false });
    const first = await page.evaluate(() => {
      const b = (sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom) }; };
      const svgs = [...document.querySelectorAll('#court svg')].map((s) => s.getBoundingClientRect().bottom);
      return { input: b('#player-input'), sw: b('#serve-switch'), courtBottom: svgs.length ? Math.round(Math.max(...svgs)) : null, sentence: b('#sentences p'), vh: window.innerHeight };
    });
    if (w === 390) {
      const ok = first.input && first.sw && first.sentence && first.courtBottom !== null &&
        first.input.bottom <= 844 && first.sw.bottom <= 844 && first.courtBottom <= 844 && first.sentence.top < 844;
      check(`${tag}: first screen shows the search, the switch, the whole court and the start of the first sentence (within 844px)`, ok, first);
    }

    // arrows and tooltips
    await arrowChecks(page, tag);

    // whole-page checks
    const ov = await overflow(page);
    check(`${tag}: no horizontal page scroll`, ov.scrollWidth <= Math.min(ov.innerWidth, w) && ov.wideEls.length === 0, ov);
    const scan = await scanText(page);
    check(`${tag}: no banned characters or terms`, hits(scan, BANNED).length === 0, hits(scan, BANNED));
    check(`${tag}: no "league", he/she or banned voice words in the rendered page`, hits(scan, INDEX_BANNED).length === 0, hits(scan, INDEX_BANNED));
    const visible = await page.evaluate(() => document.body.innerText + '\n' + [...document.querySelectorAll('svg text')].map((t) => t.textContent).join(' '));
    check(`${tag}: no placeholder text (NaN, undefined, n/a, null)`, hits(visible, PLACEHOLDER).length === 0, hits(visible, PLACEHOLDER));

    // heights: the default and every quick pick (details closed)
    const heights = { [DEFAULT]: await page.evaluate(() => document.documentElement.scrollHeight) };
    const picks = await page.evaluate(() => [...document.querySelectorAll('#quick-picks a')].map((a) => a.getAttribute('data-name') || a.textContent.trim()));
    for (let i = 0; i < picks.length; i++) {
      await page.locator('#quick-picks a').nth(i).click();
      await page.waitForTimeout(600);
      heights[await nameNow(page)] = await page.evaluate(() => document.documentElement.scrollHeight);
    }
    const maxH = Math.max(...Object.values(heights));
    check(`${tag}: page height at most ${L.maxHeight.toLocaleString('en-US')}px (default and quick picks)`, maxH <= L.maxHeight, heights);
    // back to the default for the full screenshot
    await typePlayer(page, DEFAULT);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(outDir, `v4-${tag}.png`), fullPage: true });

    check(`${tag}: no console errors, page errors or module warnings`, errorsOf(log).length === 0, errorsOf(log));
    check(`${tag}: no failed requests`, log.failedRequests.length === 0, log.failedRequests);
    runs.push({ tag, heights, firstScreen: first });
    await ctx.close();
  }

  // ---------- index interaction (1440) ----------
  tagNow = 'interaction';
  {
    const { ctx, page, log } = await newPage(browser, { w: 1440, h: 900 });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await waitForIndex(page);

    // search
    const opts = await page.evaluate(() => {
      const i = document.getElementById('player-input');
      const l = document.getElementById('players');
      return { list: i && i.getAttribute('list'), tag: l && l.tagName, n: l ? l.querySelectorAll('option').length : 0,
        values: l ? [...l.querySelectorAll('option')].map((o) => o.value) : [] };
    });
    check(`interact: #players datalist has ${N_PLAYERS} options and #player-input uses it`, opts.list === 'players' && opts.tag === 'DATALIST' && opts.n === N_PLAYERS &&
      SERVERS.every((s) => opts.values.some((v) => sameName(v, s))), { list: opts.list, tag: opts.tag, n: opts.n });
    const typed = SERVERS.includes('Iga Swiatek') ? 'Iga Swiatek' : SERVERS[1];
    const gotTyped = await typePlayer(page, typed);
    check(`interact: typing "${typed}" into the search loads that player`, sameName(gotTyped, typed), gotTyped);

    // a name that matches nobody keeps the typed text, says so, and leaves the loaded player alone
    {
      const inp = page.locator('#player-input');
      await inp.click();
      await inp.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
      await inp.press('Backspace');
      await inp.pressSequentially('Zzqx', { delay: 8 });
      await inp.press('Enter');
      await page.waitForTimeout(200);
      const miss = await page.evaluate(() => ({ value: document.getElementById('player-input').value,
        msg: (document.getElementById('search-msg') || {}).textContent || '', name: document.getElementById('player-name').textContent.trim() }));
      await inp.press('Escape');
      await page.waitForTimeout(100);
      const back = await page.evaluate(() => ({ value: document.getElementById('player-input').value, msg: (document.getElementById('search-msg') || {}).textContent || '' }));
      check('interact: a name that matches nobody keeps the text, shows a "No charted player matches" line and keeps the player; Esc restores',
        miss.value === 'Zzqx' && /^No charted player matches "Zzqx"\.$/.test(miss.msg) && sameName(miss.name, typed) && sameName(back.value, typed) && back.msg === '',
        { miss, back });
    }

    // quick picks
    const picks = await page.evaluate(() => [...document.querySelectorAll('#quick-picks a')].map((a) => ({ text: a.textContent.trim(), name: a.getAttribute('data-name') })));
    const pickResults = [];
    for (let i = 0; i < picks.length; i++) {
      await page.locator('#quick-picks a').nth(i).click();
      await page.waitForTimeout(500);
      const n = await nameNow(page);
      pickResults.push({ link: picks[i].text, loaded: n, ok: n.endsWith(picks[i].text) || sameName(n, picks[i].name) });
    }
    check('interact: #quick-picks has 5 links and each loads the player it names', picks.length === 5 && pickResults.every((r) => r.ok), pickResults);

    // serve switch
    await typePlayer(page, DEFAULT);
    const before = await shareLabels(page);
    const p0 = await pressed(page);
    await page.locator('#serve-switch button').nth(1).click();
    await page.waitForTimeout(800);
    const after = await shareLabels(page);
    const p1 = await pressed(page);
    check('interact: #serve-switch has 2 buttons and 2nd serve flips aria-pressed', p0.join() === 'true,false' && p1.join() === 'false,true', { before: p0, after: p1 });
    check('interact: switching to 2nd serve changes the arrow share labels', before.length === 6 && after.length === 6 && before.join() !== after.join() &&
      after.every((s) => /=\d+%$/.test(s) || /=$/.test(s)), { before, after });
    await page.locator('#serve-switch button').nth(0).click();
    await page.waitForTimeout(800);
    const back = await shareLabels(page);
    check('interact: switching back to 1st serve restores the labels', back.join() === before.join(), { before, back });

    // details#sure
    const sure0 = await page.evaluate(() => { const d = document.querySelector('details#sure'); return d ? d.open : null; });
    await page.locator('details#sure > summary').scrollIntoViewIfNeeded();
    await page.locator('details#sure > summary').click();
    await page.waitForTimeout(300);
    const sure = await page.evaluate(() => {
      const d = document.querySelector('details#sure');
      const t = [...d.querySelectorAll('table')];
      return { open: d.open, tables: t.length, rows: t.map((x) => x.querySelectorAll('tbody tr').length), text: d.innerText };
    });
    check('interact: details#sure is closed by default, opens, and has at least 2 tables with rows', sure0 === false && sure.open && sure.tables >= 2 && sure.rows.every((r) => r > 0),
      { closedByDefault: sure0 === false, open: sure.open, tables: sure.tables, rows: sure.rows });
    check('interact: "How sure are we?" text has no "league", placeholder or banned text',
      hits(sure.text, [...BANNED, ...INDEX_BANNED, ...PLACEHOLDER]).length === 0, hits(sure.text, [...BANNED, ...INDEX_BANNED, ...PLACEHOLDER]));
    await page.screenshot({ path: path.join(outDir, 'v4-interact-sure.png'), fullPage: false });
    await page.locator('details#sure > summary').click();

    // waffle
    const waffle = await page.evaluate(() => {
      const dots = [...document.querySelectorAll('.waffle .waffle-dot')];
      const hitEls = [...document.querySelectorAll('.waffle .waffle-dot.is-hit')];
      return { dots: dots.length, hits: hitEls.length, buttons: hitEls.filter((b) => b.tagName === 'BUTTON').length,
        labels: hitEls.map((b) => b.getAttribute('aria-label') || ''), names: hitEls.map((b) => b.getAttribute('data-name') || (b.getAttribute('aria-label') || '').replace(/, tap to show$/, '')),
        exists: !!document.getElementById('findings') };
    });
    check(`findings: #findings exists with ${N_PLAYERS} waffle dots`, waffle.exists && waffle.dots === N_PLAYERS, { exists: waffle.exists, dots: waffle.dots });
    check(`findings: ${N_HITS} blue dots (headline.costly_players_s1), all buttons labelled "<name>, tap to show"`,
      waffle.hits === N_HITS && waffle.buttons === N_HITS && waffle.labels.every((l) => /^.+, tap to show$/.test(l)) && waffle.names.every((n) => SERVERS.includes(n)),
      { hits: waffle.hits, buttons: waffle.buttons, badLabels: waffle.labels.filter((l) => !/^.+, tap to show$/.test(l)).slice(0, 3) });
    const target = waffle.names.find((n) => n !== DEFAULT) || waffle.names[0];
    let clicked = null;
    if (target) {
      // start from 2nd serve, so the click must also switch back to 1st
      await page.locator('#serve-switch button').nth(1).click();
      await page.waitForTimeout(400);
      const btn = page.locator('.waffle .waffle-dot.is-hit').nth(waffle.names.indexOf(target));
      await btn.scrollIntoViewIfNeeded();
      await btn.hover();
      await page.waitForTimeout(150);
      const tipText = await page.evaluate(() => [...document.querySelectorAll('#findings *')].filter((e) => e.children.length === 0 && !e.hidden &&
        e.getClientRects().length && !e.closest('.waffle-dot') && /\S/.test(e.textContent) && !e.closest('p')).map((e) => e.textContent.trim()));
      await btn.click();
      await page.waitForTimeout(800);
      clicked = await page.evaluate(() => {
        const r = document.getElementById('tool').getBoundingClientRect();
        return { name: (document.getElementById('player-name').textContent || '').trim(), toolTop: Math.round(r.top), toolBottom: Math.round(r.bottom), vh: window.innerHeight,
          pressed: [...document.querySelectorAll('#serve-switch button')].map((b) => b.getAttribute('aria-pressed')).join() };
      });
      clicked.hoverTip = tipText;
      check(`findings: hovering a blue dot shows the player's name`, tipText.some((t) => t.includes(target)), { target, shown: tipText.slice(0, 5) });
      check(`findings: clicking a blue dot (${target}) loads that player on 1st serve and scrolls to #tool`,
        sameName(clicked.name, target) && clicked.pressed === 'true,false' && clicked.toolTop < clicked.vh && clicked.toolBottom > 0, clicked);
    } else {
      check('findings: clicking a blue dot loads that player and scrolls to #tool', false, 'no blue dots');
    }

    // sentences for every quick pick and every waffle player, both serves (set through the search input)
    const names = [...new Set([DEFAULT, ...picks.map((p) => p.name).filter(Boolean), ...waffle.names])];
    const sent = await page.evaluate(async ([list]) => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const input = document.getElementById('player-input');
      const out = [];
      for (const n of list) {
        for (const sv of [1, 2]) {
          input.value = n;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          await sleep(30);
          document.querySelectorAll('#serve-switch button')[sv - 1].click();
          await sleep(30);
          const ps = [...document.querySelectorAll('#sentences p')].map((p) => p.textContent.replace(/\s+/g, ' ').trim());
          const tips = [...document.querySelectorAll('#court .arrow')].map((a) => a.getAttribute('aria-label') || '');
          out.push({ name: n, serve: sv, loaded: document.getElementById('player-name').textContent.trim(), ps, tips, sure: document.getElementById('sure').innerText });
        }
      }
      input.blur();
      return out;
    }, [names]);
    const BAD_S = [...BANNED, ...INDEX_BANNED, ...PLACEHOLDER];
    const badCount = sent.filter((s) => s.ps.length < 1 || s.ps.length > 4 || !sameName(s.loaded, s.name));
    check(`sentences: 1 to 4 <p> in #sentences for ${names.length} players on both serves`, sent.length === 2 * names.length && badCount.length === 0,
      badCount.slice(0, 5).map((s) => ({ name: s.name, serve: s.serve, loaded: s.loaded, n: s.ps.length })));
    const badText = sent.map((s) => ({ name: s.name, serve: s.serve, hits: hits(s.ps.join('\n'), BAD_S) })).filter((s) => s.hits.length);
    check('sentences: no "league", banned characters, he/she or placeholder text in any sentence', badText.length === 0, badText.slice(0, 5));
    const badTips = sent.map((s) => ({ name: s.name, serve: s.serve, hits: hits(s.tips.join('\n') + '\n' + s.sure, BAD_S) })).filter((s) => s.hits.length);
    check('sentences: no "league", banned characters, he/she or placeholder text in any arrow label or "How sure are we?"', badTips.length === 0, badTips.slice(0, 5));
    const surnameUse = sent.filter((s) => s.ps.length && !s.ps.some((p) => nameKey(p).includes(nameKey(s.name.split(' ').slice(-1)[0]))));
    check('sentences: the sentences name the player by surname', surnameUse.length === 0, surnameUse.slice(0, 3).map((s) => [s.name, s.ps[0]]));
    const nadal2 = sent.find((s) => s.name === DEFAULT && s.serve === 2);
    check('sentences: on 2nd serves Nadal has no clear winning direction', nadal2 && nadal2.ps.some((p) => /No direction wins clearly more points/.test(p)), nadal2 && nadal2.ps);

    // a player with no photo hides the image
    const noPhoto = SERVERS.find((s) => !(IMAGES.players || {})[s]);
    if (noPhoto) {
      await typePlayer(page, noPhoto);
      const ph = await page.evaluate(() => { const i = document.getElementById('player-photo'); return { hidden: !i || i.hidden || getComputedStyle(i).display === 'none', name: document.getElementById('player-name').textContent.trim() }; });
      check(`interact: a player with no photo (${noPhoto}) hides #player-photo`, sameName(ph.name, noPhoto) && ph.hidden, ph);
    }

    check('interact: no console errors, page errors or module warnings', errorsOf(log).length === 0, errorsOf(log));
    check('interact: no failed requests', log.failedRequests.length === 0, log.failedRequests);
    runs.push({ tag: 'interaction', typed, picks: pickResults, sure: { tables: sure.tables, rows: sure.rows }, waffle: { dots: waffle.dots, hits: waffle.hits }, clicked, sentenceRuns: sent.length });
    await ctx.close();
  }

  // ---------- the data file fails (spec: #status shows an error line) ----------
  for (const w of [1440, 390]) {
    const L = LIMITS[w];
    const tag = `no-data-${w}`;
    tagNow = tag;
    const { ctx, page } = await newPage(browser, { w: L.w, h: L.h });
    await page.route('**/data/serveiq.json', (r) => r.abort());
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.documentElement.classList.contains('no-data'), null, { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(200);
    const st = await page.evaluate(() => {
      const shown = (sel) => { const e = document.querySelector(sel); return !!e && !e.hidden && getComputedStyle(e).display !== 'none' && e.getClientRects().length > 0; };
      const s = document.getElementById('status');
      return { noData: document.documentElement.classList.contains('no-data'), status: shown('#status'), text: s ? s.textContent.trim() : '',
        link: !!(s && s.querySelector('a[href^="https://serve-iq.streamlit.app"]')),
        tool: shown('#tool'), findings: shown('#findings'), src: shown('.intro-src'), h1: shown('#intro-h') };
    });
    await page.screenshot({ path: path.join(outDir, `v4-${tag}.png`), fullPage: true });
    check(`${tag}: when data/serveiq.json fails, #status explains it with a link to the backup app and the parts that need the data are hidden`,
      st.noData && st.status && st.text.length > 20 && st.link && !st.tool && !st.findings && !st.src && st.h1, st);
    await ctx.close();
  }

  // ---------- method.html at both widths ----------
  for (const w of [1440, 390]) {
    const L = LIMITS[w];
    const tag = `method-${w}`;
    tagNow = tag;
    const { ctx, page, log } = await newPage(browser, { w: L.w, h: L.h });
    let gotoErr = null;
    await page.goto(base + 'method.html', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch((e) => { gotoErr = e.message; });
    await waitForMethod(page);
    const m = await page.evaluate(() => ({
      ready: document.documentElement.classList.contains('data-ready'),
      fx: document.querySelectorAll('.fx').length,
      katex: document.querySelectorAll('.fx .katex').length,
      katexErrors: document.querySelectorAll('.katex-error, .tex-raw').length,
      cards: document.querySelectorAll('[id^="f"].fcard, details.fcard').length,
      credits: document.querySelectorAll('#credits-list tbody tr, #credits-list li').length,
      height: document.documentElement.scrollHeight,
    }));
    const ov = await overflow(page);
    const scan = await scanText(page);
    await page.screenshot({ path: path.join(outDir, `v4-${tag}.png`), fullPage: true });
    check(`${tag}: page boots and binds the data`, !gotoErr && m.ready, gotoErr || { ready: m.ready });
    check(`${tag}: KaTeX renders all 13 formulas and the hypothesis line with no errors`, m.cards === 13 && m.fx >= 14 && m.katex === m.fx && m.katexErrors === 0,
      { cards: m.cards, fx: m.fx, katex: m.katex, errors: m.katexErrors });
    check(`${tag}: photo credits list one row per images.json player (${N_CREDITS})`, m.credits === N_CREDITS, { credits: m.credits });
    check(`${tag}: no horizontal page scroll`, ov.scrollWidth <= Math.min(ov.innerWidth, w) && ov.wideEls.length === 0, ov);
    check(`${tag}: no banned characters or terms`, hits(scan, BANNED).length === 0, hits(scan, BANNED));
    check(`${tag}: no console errors, page errors or module warnings`, errorsOf(log).length === 0, errorsOf(log));
    check(`${tag}: no failed requests`, log.failedRequests.length === 0, log.failedRequests);
    runs.push({ tag, height: m.height });
    await ctx.close();
  }

  // ---------- reduced motion ----------
  for (const w of [1440, 390]) {
    const L = LIMITS[w];
    const tag = `reduced-${w}`;
    tagNow = tag;
    const { ctx, page, log } = await newPage(browser, { w: L.w, h: L.h, reduce: true });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await waitForIndex(page);
    const name = await nameNow(page);
    const before = await shareLabels(page);
    await page.locator('#serve-switch button').nth(1).click();
    await sleep(60);
    const soon = await shareLabels(page);
    await page.waitForTimeout(900);
    const settled = await shareLabels(page);
    const pr = await pressed(page);
    check(`${tag}: page boots with ${DEFAULT} under prefers-reduced-motion`, name === DEFAULT && before.length === 6, { name, arrows: before.length });
    check(`${tag}: the serve switch works and the arrow labels are final at once (no tween)`, pr.join() === 'false,true' && soon.join() !== before.join() && soon.join() === settled.join(),
      { before, soon, settled });
    check(`${tag}: no errors`, errorsOf(log).length === 0, errorsOf(log));
    await ctx.close();
  }
} finally {
  await browser.close();
  server.close();
}

const failed = checks.filter((c) => !c.pass);
const summary = { siteDir, outDir, url: base, browser: browserName, passed: checks.length - failed.length, failed: failed.length, ok: failed.length === 0, warnings, failedChecks: failed, checks, runs };
console.log(JSON.stringify(summary, null, 2));
process.exit(failed.length ? 1 : 0);
