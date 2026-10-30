#!/usr/bin/env node
// Serve IQ static-site smoke test (headless browser), v5: one court with a stats panel, plus "By the numbers".
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
// What it checks (each line of the JSON summary is one check), against the DOM contract of the v5 site:
//   index.html at 1440x900 and 390x844:
//     - the page boots (html.data-ready); the default view is the typical pro of the men on 1st serve:
//       tab [data-key="m"] pressed, #who h2 "Typical pro, men", the first #seg button pressed
//     - zero console errors, page errors, "Serve IQ:" module warnings and failed requests. The Archivo font
//       comes from fonts.googleapis.com / fonts.gstatic.com: a failure on those two hosts (no network) is a
//       warning, not a failure
//     - no horizontal page scroll; no banned characters, placeholder text (NaN, undefined, n/a, null),
//       the word "league", he/she pronouns or banned voice words anywhere in the rendered text
//     - #court svg has 6 lane share labels and 6 serve paths (each with its shade path); once the draw
//       animation settles every path has stroke-dashoffset 0
//     - #sides has 2 .side blocks and 6 .row[data-k] with tabindex 0 and an aria-label; each row's share and
//       points won equal a pooled typical pro computed here from league.directions (sum n over matchups and
//       both break-point states per gender / serve / side / direction; share = n / side total; win =
//       n-weighted mean), in whole percents
//     - the typical pro (men, 1st serve) flags both body rows "Wins least"
//     - page height: at most 2,000px at 1440, at most 3,400px at 390
//   index.html interaction (1440):
//     - the Nadal tab loads Rafael Nadal: deuce T and ad wide shares equal mix.data["m|Rafael Nadal|1"]
//       (51%, 54%), the deuce wide row is .best with "Wins clearly more", the #who photo loads
//     - hovering or focusing a row adds .hl to it alone and dims the other court paths (opacity 0.18)
//     - #seg: 2nd serve flips aria-pressed and changes the court labels; 1st serve restores them
//     - #find: #players datalist has one option per player (headline.players); typing a full name and Enter
//       loads that player and adds a tab for them; a name that matches nobody shows "No charted player
//       matches" in #find-msg and keeps the player
//     - details#sure is closed by default, opens and has text; for a player it lists 6 li
//     - #numbers: #vs has 4 bars whose values equal |body_in_m1|, |body_win_m1|, |body_in_w1|, |body_win_w1|
//       (headline coefs, 1 decimal); #clear shows the ring with costly_players_s1 of players and exactly
//       costly_players_s1 .chip buttons; clicking a chip loads that player on 1st serve, presses the chip
//       and scrolls #tool into view
//     - a player with no photo (in players, not in images.json) shows the ball placeholder, no img
//   data/serveiq.json aborted, index at 1440 and 390: html.no-data, #status shows the error with a link to
//     serve-iq.streamlit.app, every .needs-data part is hidden.
//   method.html at 1440 and 390: boots, no errors or failed requests (same font rule), no horizontal scroll
//     (cards closed and all open), no banned characters, 13 details.fcard all closed by default, KaTeX renders
//     every .fx block with no errors after all cards are opened, opening #credits-fold shows one credits row
//     per images.json player with a photo, method.html#f7 opens f7 and method.html#credits opens the fold.
//   Reduced motion (prefers-reduced-motion: reduce), index at 1440 and 390: boots, every path has
//     stroke-dashoffset 0 at once, switching serve updates the labels in the same task, no errors.
//   Screenshots go to <outDir>/v5-index-<w>.png (full page), v5-index-<w>-first.png (first screen) and
//   v5-method-<w>.png (full page).
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
const IMG_PLAYERS = IMAGES.players || {};
const N_CREDITS = Object.keys(IMG_PLAYERS).filter((k) => IMG_PLAYERS[k] && IMG_PLAYERS[k].src).length;
const N_PLAYERS = DATA.headline.players;
const N_HITS = DATA.headline.costly_players_s1;
const SERVERS = (() => {
  const f = DATA.players.fields;
  const si = f.indexOf('server');
  return DATA.players.rows.map((r) => r[si]);
})();
const DEFAULT_H2 = 'Typical pro, men';
const NADAL = 'Rafael Nadal';
const SIDES = ['deuce', 'ad'];
const DIRS = ['wide', 'body', 'T'];
// names: the page shows a display spelling for some players ("John McEnroe" for the data's "John Mcenroe",
// "Jo-Wilfried Tsonga"), so names are compared by the same key js/data.js nameKey() uses
const nameKey = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()
  .replace(/['’]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
const sameName = (a, b) => nameKey(a) === nameKey(b);
const LIMITS = { 1440: { w: 1440, h: 900, maxHeight: 2000 }, 390: { w: 390, h: 844, maxHeight: 3400 } };
// the web font hosts: a failure there (no network) is a warning, not a failed check
const FONT_HOST = /^https?:\/\/fonts\.(googleapis|gstatic)\.com\//;
const pct = (x) => Math.round(100 * x);

/* The typical pro, computed here from the data file (not from the page): per side and direction, n = sum of
   n over every matchup and both is_break_point values; share = n / side total; win = n-weighted mean of win. */
function pooledTypical(g, serve) {
  const LD = DATA.league.directions;
  const F = {};
  LD.fields.forEach((f, i) => { F[f] = i; });
  const acc = {};
  SIDES.forEach((s) => { acc[s] = {}; DIRS.forEach((d) => { acc[s][d] = { n: 0, w: 0, wn: 0 }; }); });
  for (const r of LD.rows) {
    if (r[F.gender] !== g || r[F.serve_num] !== serve) continue;
    const a = acc[r[F.side]] && acc[r[F.side]][r[F.direction]];
    if (!a || typeof r[F.n] !== 'number') continue;
    a.n += r[F.n];
    if (typeof r[F.win] === 'number') { a.w += r[F.n] * r[F.win]; a.wn += r[F.n]; }
  }
  const out = {};
  SIDES.forEach((s) => {
    const tot = DIRS.reduce((t, d) => t + acc[s][d].n, 0);
    DIRS.forEach((d) => { out[s + d] = { share: pct(acc[s][d].n / tot), win: pct(acc[s][d].w / acc[s][d].wn) }; });
  });
  return out;
}
// a player's 1st-serve share per side and direction from mix.data (field "share")
function mixShare(key, side, dir) {
  const i = DATA.mix.fields.indexOf('share');
  const m = DATA.mix.data[key];
  return m && m[side] && m[side].all && m[side].all[dir] ? m[side].all[dir][i] : null;
}

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

function attachCollectors(page) {
  const log = { consoleErrors: [], moduleWarnings: [], pageErrors: [], failedRequests: [] };
  page.on('console', (m) => {
    const where = (m.location() && m.location().url) || '';
    const line = `${m.text().slice(0, 300)}${where ? ' @ ' + where : ''}`;
    if (m.type() === 'warning' && /Serve IQ/.test(m.text())) { log.moduleWarnings.push(line); return; }
    if (m.type() !== 'error') return;
    if (/\/favicon\.ico$/.test(where) || FONT_HOST.test(where)) { warnings.push(`${tagNow}: ${line}`); return; }
    log.consoleErrors.push(line);
  });
  page.on('pageerror', (e) => log.pageErrors.push(String(e.message || e).slice(0, 300)));
  const failed = (line, url) => { if (FONT_HOST.test(url)) warnings.push(`${tagNow}: font host: ${line}`); else log.failedRequests.push(line); };
  page.on('requestfailed', (r) => failed(`${r.failure()?.errorText || 'failed'} ${r.url()}`, r.url()));
  page.on('response', (r) => { if (r.status() >= 400) failed(`HTTP ${r.status()} ${r.url()}`, r.url()); });
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

// index: data bound, the stats panel and the court drawn, and "By the numbers" built (numbers.js runs after tool.js)
async function waitForIndex(page) {
  await page.waitForFunction(() => document.documentElement.classList.contains('data-ready') ||
    document.documentElement.classList.contains('no-data'), null, { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => {
    const h = document.querySelector('#who h2');
    return h && h.textContent.trim() && document.querySelectorAll('#sides .row[data-k]').length === 6 &&
      document.querySelectorAll('#court path').length > 0 && document.querySelectorAll('#clear .chip').length > 0;
  }, null, { timeout: 15000 }).catch(() => {});
}
// the draw animation is done when every court path has stroke-dashoffset 0
const pathsSettled = () => {
  const ps = [...document.querySelectorAll('#court path')];
  return ps.length > 0 && ps.every((p) => parseFloat(getComputedStyle(p).strokeDashoffset) === 0);
};
async function waitForPaths(page) {
  return page.waitForFunction(pathsSettled, null, { timeout: 8000 }).then(() => true).catch(() => false);
}

async function waitForMethod(page) {
  await page.waitForFunction(() => document.documentElement.classList.contains('data-ready') ||
    document.documentElement.classList.contains('no-data'), null, { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => document.querySelectorAll('.fx.tex-ok, .fx.tex-raw').length >= document.querySelectorAll('.fx').length &&
    document.querySelectorAll('#credits-list tbody tr, #credits-list .m-muted').length > 0 &&
    !/load with the page/.test(document.getElementById('credits-list').textContent), null, { timeout: 20000 }).catch(() => {});
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
      if (r.right < 0) return false; // wholly off to the left: the visually hidden .skip pattern, no scroll
      for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
        if (getComputedStyle(p).overflowX !== 'visible') return false; // clipped or scrolls inside its own box
      }
      return r.right > window.innerWidth + 1 || r.left < -1;
    }).slice(0, 5).map((e) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${typeof e.className === 'string' && e.className ? '.' + e.className.split(' ')[0] : ''} right=${Math.round(e.getBoundingClientRect().right)}`),
  }));
}

// the six lane share labels on the court (the 19px numbers above the far service line)
const courtLabels = (page) => page.evaluate(() => [...document.querySelectorAll('#court text[font-size="19"]')].map((t) => t.textContent.trim()));
const segPressed = (page) => page.evaluate(() => [...document.querySelectorAll('#seg button')].map((b) => b.getAttribute('aria-pressed')));
const whoNow = (page) => page.evaluate(() => (document.querySelector('#who h2')?.textContent || '').replace(/\s+/g, ' ').trim());
// rows as { k, share, win } in whole percents (null when not a number), plus class and flag
const rowsNow = (page) => page.evaluate(() => [...document.querySelectorAll('#sides .row[data-k]')].map((r) => {
  const num = (s) => { const m = /^(\d+)%$/.exec((s || '').replace(/\s+/g, '')); return m ? Number(m[1]) : null; };
  return { k: r.getAttribute('data-k'), share: num(r.querySelector('.v')?.textContent), win: num(r.querySelector('.won')?.textContent),
    cls: r.className, flag: (r.querySelector('.flag')?.textContent || '').trim(), tab: r.getAttribute('tabindex'), label: r.getAttribute('aria-label') || '' };
}));

// type into #find the way a person does (input events per key), then Enter
async function findPlayer(page, text) {
  const inp = page.locator('#find');
  await inp.scrollIntoViewIfNeeded();
  await inp.click();
  await inp.fill('');
  await inp.pressSequentially(text, { delay: 8 });
  await inp.press('Enter');
}
const waitWho = (page, name) => page.waitForFunction((n) => {
  const h = document.querySelector('#who h2');
  return h && h.textContent.trim() === n;
}, name, { timeout: 5000 }).then(() => true).catch(() => false);

// ---------- main ----------
const server = await startServer();
const base = `http://127.0.0.1:${server.address().port}/`;
const { browser, browserName } = await launchBrowser();
const runs = [];
const TYP_M1 = pooledTypical('m', 1);

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
    check(`${tag}: page boots and binds the data`, !gotoErr && ready, gotoErr || { ready });
    const def = await page.evaluate(() => ({
      tab: document.querySelector('#tabs [data-key="m"]')?.getAttribute('aria-pressed') || null,
      others: [...document.querySelectorAll('#tabs .tab:not([data-key="m"])')].filter((b) => b.getAttribute('aria-pressed') === 'true').length,
    }));
    const who = await whoNow(page);
    const pr = await segPressed(page);
    check(`${tag}: default view is the typical pro of the men on 1st serve`,
      def.tab === 'true' && def.others === 0 && who === DEFAULT_H2 && pr.join() === 'true,false', { ...def, who, seg: pr });

    // court
    const settled = await waitForPaths(page);
    const court = await page.evaluate(() => {
      const ps = [...document.querySelectorAll('#court path')];
      return { labels: [...document.querySelectorAll('#court text[font-size="19"]')].map((t) => t.textContent.trim()),
        paths: ps.length, serve: ps.filter((p) => p.getAttribute('opacity') === '0.95').length,
        offsets: ps.map((p) => getComputedStyle(p).strokeDashoffset) };
    });
    check(`${tag}: #court svg has 6 lane share labels and 6 serve paths (plus 6 shade paths)`,
      court.labels.length === 6 && court.labels.every((l) => /^\d+%$/.test(l)) && court.serve === 6 && court.paths === 12, { labels: court.labels, serve: court.serve, paths: court.paths });
    check(`${tag}: after the draw animation every court path has stroke-dashoffset 0`, settled, court.offsets);

    // stats panel against the pooled typical pro
    const rows = await rowsNow(page);
    const sides = await page.locator('#sides .side').count();
    check(`${tag}: #sides has 2 .side blocks and 6 .row[data-k] with tabindex 0 and an aria-label`,
      sides === 2 && rows.length === 6 && new Set(rows.map((r) => r.k)).size === 6 && rows.every((r) => r.tab === '0' && r.label.length > 10),
      { sides, rows: rows.map((r) => `${r.k} tab=${r.tab} label=${r.label.length}`) });
    const mism = rows.filter((r) => !TYP_M1[r.k] || r.share !== TYP_M1[r.k].share || r.win !== TYP_M1[r.k].win)
      .map((r) => ({ k: r.k, page: [r.share, r.win], data: TYP_M1[r.k] ? [TYP_M1[r.k].share, TYP_M1[r.k].win] : null }));
    check(`${tag}: typical-pro rows match the pooled league.directions computation (share, points won, whole %)`,
      rows.length === 6 && mism.length === 0, mism.length ? mism : rows.map((r) => `${r.k} ${r.share}/${r.win}`));
    const least = rows.filter((r) => /\bworst\b/.test(r.cls) || r.flag);
    check(`${tag}: the typical pro (men, 1st serve) flags both body rows "Wins least" and nothing else`,
      least.length === 2 && least.every((r) => /body$/.test(r.k) && /\bworst\b/.test(r.cls) && r.flag === 'Wins least'), least);

    // first screen
    await page.screenshot({ path: path.join(outDir, `v5-${tag}-first.png`), fullPage: false });

    // whole-page checks
    const ov = await overflow(page);
    check(`${tag}: no horizontal page scroll`, ov.scrollWidth <= Math.min(ov.innerWidth, w) && ov.wideEls.length === 0, ov);
    const scan = await scanText(page);
    check(`${tag}: no banned characters or terms`, hits(scan, BANNED).length === 0, hits(scan, BANNED));
    check(`${tag}: no "league", he/she or banned voice words in the rendered page`, hits(scan, INDEX_BANNED).length === 0, hits(scan, INDEX_BANNED));
    const visible = await page.evaluate(() => document.body.innerText + '\n' + [...document.querySelectorAll('svg text')].map((t) => t.textContent).join(' '));
    check(`${tag}: no placeholder text (NaN, undefined, n/a, null)`, hits(visible, PLACEHOLDER).length === 0, hits(visible, PLACEHOLDER));
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    check(`${tag}: page height at most ${L.maxHeight.toLocaleString('en-US')}px`, height <= L.maxHeight, { height });

    // full page: scroll through once so "By the numbers" fills, then back to the top
    await page.locator('#numbers').scrollIntoViewIfNeeded();
    await page.waitForFunction(() => [...document.querySelectorAll('#vs [data-w]')].every((b) => b.style.width), null, { timeout: 3000 }).catch(() => {});
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(outDir, `v5-${tag}.png`), fullPage: true });

    check(`${tag}: no console errors, page errors or module warnings`, errorsOf(log).length === 0, errorsOf(log));
    check(`${tag}: no failed requests (font hosts are warnings)`, log.failedRequests.length === 0, log.failedRequests);
    runs.push({ tag, height, labels: court.labels });
    await ctx.close();
  }

  // ---------- index interaction (1440) ----------
  tagNow = 'interaction';
  {
    const { ctx, page, log } = await newPage(browser, { w: 1440, h: 900 });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await waitForIndex(page);
    const out = {};

    // details#sure for the typical pro: closed, opens, has text
    const sure0 = await page.evaluate(() => { const d = document.querySelector('details#sure'); return d ? d.open : null; });
    await page.locator('details#sure > summary').click();
    await page.waitForFunction(() => document.querySelector('details#sure').open, null, { timeout: 2000 }).catch(() => {});
    const sureT = await page.evaluate(() => { const d = document.querySelector('details#sure'); return { open: d.open, text: d.querySelector('#sure-body').innerText.trim() }; });
    await page.locator('details#sure > summary').click();

    // tabs: Nadal
    await page.locator('#tabs [data-key="Rafael Nadal"]').click();
    const nadalOk = await waitWho(page, NADAL);
    await page.waitForFunction(() => { const i = document.querySelector('#who img'); return i && i.complete; }, null, { timeout: 5000 }).catch(() => {});
    const nadal = await rowsNow(page);
    const img = await page.evaluate(() => { const i = document.querySelector('#who img'); return i ? { ok: i.complete && i.naturalWidth > 0, src: i.getAttribute('src') } : null; });
    const tabPressed = await page.evaluate(() => document.querySelector('#tabs [data-key="Rafael Nadal"]').getAttribute('aria-pressed'));
    const k1 = 'm|Rafael Nadal|1';
    const want = { deuceT: pct(mixShare(k1, 'deuce', 'T')), adwide: pct(mixShare(k1, 'ad', 'wide')) };
    const got = Object.fromEntries(nadal.map((r) => [r.k, r]));
    check('interact: the Nadal tab loads Rafael Nadal (tab pressed, #who h2)', nadalOk && tabPressed === 'true', { who: await whoNow(page), tabPressed });
    check(`interact: Nadal's deuce T row says ${want.deuceT}% and ad wide ${want.adwide}% (mix.data share; spec: 51%, 54%)`,
      want.deuceT === 51 && want.adwide === 54 && got.deuceT?.share === want.deuceT && got.adwide?.share === want.adwide,
      { want, page: { deuceT: got.deuceT?.share, adwide: got.adwide?.share } });
    check('interact: Nadal\'s deuce wide row is .best with a "Wins clearly more" flag',
      /\bbest\b/.test(got.deucewide?.cls || '') && got.deucewide?.flag === 'Wins clearly more', got.deucewide);
    check('interact: Nadal\'s photo in #who loads', img && img.ok, img);

    // hover / focus a row: .hl on that row alone, the other court paths dimmed to 0.18
    const hlState = (k) => page.evaluate((key) => {
      const SIDES = ['deuce', 'ad'], DIRS = ['wide', 'body', 'T'];
      const i = SIDES.indexOf(key.startsWith('deuce') ? 'deuce' : 'ad') * 3 + DIRS.indexOf(key.replace(/^(deuce|ad)/, ''));
      const ps = [...document.querySelectorAll('#court path')];
      const op = ps.map((p) => getComputedStyle(p).opacity);
      // paths come in (shade, path) pairs in SIDES x DIRS order
      return { hl: [...document.querySelectorAll('#sides .row.hl')].map((r) => r.getAttribute('data-k')),
        lit: op.every((o, j) => (Math.floor(j / 2) === i ? o === '1' : o === '0.18')), op };
    }, k);
    await page.locator('#sides .row[data-k="deuceT"]').hover();
    const hov = await hlState('deuceT');
    await page.mouse.move(5, 5);
    await page.locator('#sides .row[data-k="adbody"]').focus();
    const foc = await hlState('adbody');
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    const off = await page.evaluate(() => ({ hl: document.querySelectorAll('#sides .row.hl').length,
      op: [...document.querySelectorAll('#court path')].map((p) => getComputedStyle(p).opacity) }));
    check('interact: hovering a row adds .hl to it and dims the other court paths to 0.18', hov.hl.join() === 'deuceT' && hov.lit, hov);
    check('interact: focusing a row adds .hl to it and dims the other court paths to 0.18', foc.hl.join() === 'adbody' && foc.lit, foc);
    check('interact: leaving the row clears .hl and the dimming', off.hl === 0 && off.op.every((o) => o === '1'), off);

    // #seg: 2nd serve and back
    const before = await courtLabels(page);
    const p0 = await segPressed(page);
    await page.locator('#seg button[data-serve="2"]').click();
    await page.waitForFunction((b) => [...document.querySelectorAll('#court text[font-size="19"]')].map((t) => t.textContent.trim()).join() !== b,
      before.join(), { timeout: 3000 }).catch(() => {});
    const after = await courtLabels(page);
    const p1 = await segPressed(page);
    check('interact: #seg 2nd serve flips aria-pressed', p0.join() === 'true,false' && p1.join() === 'false,true', { before: p0, after: p1 });
    check('interact: 2nd serve changes the court labels', before.length === 6 && after.length === 6 && before.join() !== after.join() &&
      after.every((s) => /^\d+%$/.test(s)), { before, after });
    await page.locator('#seg button[data-serve="1"]').click();
    await page.waitForFunction((b) => [...document.querySelectorAll('#court text[font-size="19"]')].map((t) => t.textContent.trim()).join() === b,
      before.join(), { timeout: 3000 }).catch(() => {});
    const back = await courtLabels(page);
    check('interact: back to 1st serve restores the court labels and aria-pressed', back.join() === before.join() && (await segPressed(page)).join() === 'true,false', { before, back });

    // details#sure for a player
    await page.locator('details#sure > summary').click();
    await page.waitForFunction(() => document.querySelector('details#sure').open, null, { timeout: 2000 }).catch(() => {});
    const sureP = await page.evaluate(() => { const d = document.querySelector('details#sure'); return { open: d.open, li: d.querySelectorAll('li').length, text: d.innerText }; });
    await page.locator('details#sure > summary').click();
    check('interact: details#sure is closed by default, opens and has text; for a player it lists 6 li',
      sure0 === false && sureT.open && sureT.text.length > 40 && sureP.open && sureP.li === 6,
      { closedByDefault: sure0 === false, typicalOpen: sureT.open, typicalText: sureT.text.length, playerOpen: sureP.open, li: sureP.li });
    check('interact: "How sure are we?" text has no "league", placeholder or banned text',
      hits(sureT.text + '\n' + sureP.text, [...BANNED, ...INDEX_BANNED, ...PLACEHOLDER]).length === 0, hits(sureT.text + '\n' + sureP.text, [...BANNED, ...INDEX_BANNED, ...PLACEHOLDER]));

    // #find
    const opts = await page.evaluate(() => {
      const i = document.getElementById('find');
      const l = document.getElementById('players');
      return { list: i && i.getAttribute('list'), tag: l && l.tagName, n: l ? l.querySelectorAll('option').length : 0,
        values: l ? [...l.querySelectorAll('option')].map((o) => o.value) : [] };
    });
    const optKeys = new Set(opts.values.map(nameKey));
    check(`interact: #players datalist has one option per player (${N_PLAYERS}) and #find uses it`,
      opts.list === 'players' && opts.tag === 'DATALIST' && opts.n === N_PLAYERS && SERVERS.length === N_PLAYERS && SERVERS.every((s) => optKeys.has(nameKey(s))),
      { list: opts.list, tag: opts.tag, n: opts.n, missing: SERVERS.filter((s) => !optKeys.has(nameKey(s))).slice(0, 5) });
    const TABS = ['Rafael Nadal', 'Novak Djokovic', 'Aryna Sabalenka', 'Iga Swiatek'];
    const typed = SERVERS.includes('Roger Federer') ? 'Roger Federer' : SERVERS.find((s) => !TABS.includes(s));
    await findPlayer(page, typed);
    const typedOk = await waitWho(page, typed);
    const typedTab = await page.evaluate((n) => { const b = document.querySelector('#tabs .tab[data-key="' + CSS.escape(n) + '"]'); return b ? b.getAttribute('aria-pressed') : null; }, typed);
    check(`interact: typing "${typed}" + Enter in #find loads that player and adds a pressed tab for them`, typedOk && typedTab === 'true', { who: await whoNow(page), tab: typedTab });
    await findPlayer(page, 'Zzqx');
    await page.waitForFunction(() => /No charted player matches/.test(document.getElementById('find-msg').textContent), null, { timeout: 2000 }).catch(() => {});
    const miss = await page.evaluate(() => ({ msg: document.getElementById('find-msg').textContent.trim(), who: document.querySelector('#who h2').textContent.trim() }));
    check('interact: a name that matches nobody shows "No charted player matches" in #find-msg and keeps the player',
      /^No charted player matches/.test(miss.msg) && miss.who === typed, miss);
    await page.locator('#find').press('Escape');

    // a player with no photo shows the ball placeholder
    const noPhoto = SERVERS.find((s) => !IMG_PLAYERS[s]);
    if (noPhoto) {
      await findPlayer(page, noPhoto);
      await page.waitForFunction((n) => { const h = document.querySelector('#who h2'); return h && h.textContent.trim() !== n; }, typed, { timeout: 5000 }).catch(() => {});
      const ph = await page.evaluate(() => ({ who: document.querySelector('#who h2').textContent.trim(), img: !!document.querySelector('#who img'), ball: !!document.querySelector('#who .ball svg') }));
      check(`interact: a player with no photo (${noPhoto}) shows the ball placeholder instead of an img`, sameName(ph.who, noPhoto) && !ph.img && ph.ball, ph);
    } else {
      check('interact: a player with no photo shows the ball placeholder', false, 'every player has a photo; nothing to test');
    }

    // #numbers: #vs bars
    const vs = await page.evaluate(() => [...document.querySelectorAll('#vs .vs-row')].map((r) => ({
      val: (r.querySelector('.val')?.textContent || '').trim(), bar: !!r.querySelector('b[data-w]') })));
    const H = DATA.headline;
    const wantVs = ['body_in_m1', 'body_win_m1', 'body_in_w1', 'body_win_w1'].map((k) => Math.abs(H[k].coef).toFixed(1));
    check(`interact: #vs shows 4 bars whose values equal |body_in_m1|, |body_win_m1|, |body_in_w1|, |body_win_w1| (${wantVs.join(', ')})`,
      vs.length === 4 && vs.every((r, i) => r.bar && r.val.split(' ')[0] === wantVs[i]), { page: vs.map((r) => r.val), want: wantVs });

    // #numbers: #clear ring and chips
    const ring = await page.evaluate(() => ({ big: document.querySelector('#clear .ring .big')?.textContent.trim(),
      of: document.querySelector('#clear .ring .of')?.textContent.trim(), circle: !!document.querySelector('#clear .ring .ring-fill'),
      chips: [...document.querySelectorAll('#clear .chip')].map((c) => ({ tag: c.tagName, name: c.getAttribute('data-name') })) }));
    check(`interact: #clear ring shows ${N_HITS} of ${N_PLAYERS} (headline.costly_players_s1, players)`,
      ring.circle && ring.big === String(N_HITS) && ring.of === `of ${N_PLAYERS}`, { big: ring.big, of: ring.of, circle: ring.circle });
    check(`interact: #clear has exactly ${N_HITS} .chip buttons, each a charted player`,
      ring.chips.length === N_HITS && ring.chips.every((c) => c.tag === 'BUTTON' && SERVERS.includes(c.name)), { chips: ring.chips.length });
    const chipName = (ring.chips.find((c) => c.name !== NADAL && c.name !== typed) || ring.chips[0] || {}).name;
    if (chipName) {
      // start from 2nd serve, so the click must also switch back to 1st
      await page.locator('#seg button[data-serve="2"]').click();
      await page.waitForFunction(() => document.querySelector('#seg button[data-serve="2"]').getAttribute('aria-pressed') === 'true', null, { timeout: 2000 }).catch(() => {});
      const chip = page.locator(`#clear .chip[data-name="${chipName}"]`);
      await chip.scrollIntoViewIfNeeded();
      await chip.click();
      // smooth scroll: wait until #tool's top has reached the top part of the screen
      await page.waitForFunction(() => { const r = document.getElementById('tool').getBoundingClientRect(); return r.top >= -2 && r.top < 120; }, null, { timeout: 5000 }).catch(() => {});
      const clicked = await page.evaluate((n) => {
        const r = document.getElementById('tool').getBoundingClientRect();
        return { who: document.querySelector('#who h2').textContent.trim(), seg: [...document.querySelectorAll('#seg button')].map((b) => b.getAttribute('aria-pressed')).join(),
          chip: document.querySelector('#clear .chip[data-name="' + CSS.escape(n) + '"]').getAttribute('aria-pressed'),
          otherChips: [...document.querySelectorAll('#clear .chip[aria-pressed="true"]')].length,
          toolTop: Math.round(r.top), toolBottom: Math.round(r.bottom), vh: window.innerHeight };
      }, chipName);
      check(`interact: clicking the ${chipName} chip loads that player on 1st serve, presses the chip and scrolls #tool into view`,
        sameName(clicked.who, chipName) && clicked.seg === 'true,false' && clicked.chip === 'true' && clicked.otherChips === 1 &&
        clicked.toolTop >= -2 && clicked.toolTop < 120 && clicked.toolBottom > 0, clicked);
      out.chip = { name: chipName, ...clicked };
    } else {
      check('interact: clicking a chip loads that player', false, 'no chips');
    }

    check('interact: no console errors, page errors or module warnings', errorsOf(log).length === 0, errorsOf(log));
    check('interact: no failed requests (font hosts are warnings)', log.failedRequests.length === 0, log.failedRequests);
    runs.push({ tag: 'interaction', typed, noPhoto, vs: vs.map((r) => r.val), ring: { big: ring.big, of: ring.of, chips: ring.chips.length }, ...out });
    await ctx.close();
  }

  // ---------- the data file fails: #status shows an error line ----------
  for (const w of [1440, 390]) {
    const L = LIMITS[w];
    const tag = `no-data-${w}`;
    tagNow = tag;
    const { ctx, page } = await newPage(browser, { w: L.w, h: L.h });
    await page.route('**/data/serveiq.json', (r) => r.abort());
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.documentElement.classList.contains('no-data'), null, { timeout: 10000 }).catch(() => {});
    const st = await page.evaluate(() => {
      const shown = (e) => !!e && !e.hidden && getComputedStyle(e).display !== 'none' && e.getClientRects().length > 0;
      const s = document.getElementById('status');
      const needs = [...document.querySelectorAll('.needs-data')];
      return { noData: document.documentElement.classList.contains('no-data'), status: shown(s), text: s ? s.textContent.trim() : '',
        link: !!(s && s.querySelector('a[href^="https://serve-iq.streamlit.app"]')),
        needsData: needs.length, needsShown: needs.filter(shown).map((e) => e.id || e.className), h1: shown(document.getElementById('intro-h')) };
    });
    check(`${tag}: when data/serveiq.json fails, #status explains it with a link to serve-iq.streamlit.app and every .needs-data part is hidden`,
      st.noData && st.status && /could not be loaded/.test(st.text) && st.link && st.needsData >= 4 && st.needsShown.length === 0 && st.h1, st);
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
    const ready = await page.evaluate(() => document.documentElement.classList.contains('data-ready'));
    check(`${tag}: page boots and binds the data`, !gotoErr && ready, gotoErr || { ready });
    const cards = await page.evaluate(() => [...document.querySelectorAll('details.fcard')].map((d) => ({ id: d.id, open: d.open })));
    check(`${tag}: 13 details.fcard, all closed by default`, cards.length === 13 && cards.every((c) => !c.open), cards.filter((c) => c.open).map((c) => c.id).concat([`count=${cards.length}`]));
    const ov0 = await overflow(page);
    const scan = await scanText(page);

    // open every card (via JS), then KaTeX must be visible in every .fx block
    await page.evaluate(() => document.querySelectorAll('details.fcard').forEach((d) => { d.open = true; }));
    const fx = await page.evaluate(() => {
      const all = [...document.querySelectorAll('.fx')];
      return { fx: all.length, katex: all.filter((b) => b.querySelector('.katex') && b.getClientRects().length > 0 && b.getBoundingClientRect().height > 0).length,
        errors: document.querySelectorAll('.katex-error, .tex-raw').length, open: document.querySelectorAll('details.fcard[open]').length };
    });
    check(`${tag}: after opening all 13 cards KaTeX renders every .fx block (${fx.fx}) with no errors`,
      fx.open === 13 && fx.fx >= 14 && fx.katex === fx.fx && fx.errors === 0, fx);
    const ov1 = await overflow(page);
    check(`${tag}: no horizontal page scroll (cards closed and all open)`,
      [ov0, ov1].every((ov) => ov.scrollWidth <= Math.min(ov.innerWidth, w) && ov.wideEls.length === 0), { closed: ov0, open: ov1 });
    check(`${tag}: no banned characters or terms`, hits(scan, BANNED).length === 0, hits(scan, BANNED));

    // credits fold
    const fold0 = await page.evaluate(() => document.getElementById('credits-fold')?.open);
    await page.locator('#credits-fold > summary').click();
    await page.waitForFunction(() => document.getElementById('credits-fold').open, null, { timeout: 2000 }).catch(() => {});
    const credits = await page.evaluate(() => ({ open: document.getElementById('credits-fold').open,
      rows: [...document.querySelectorAll('#credits-list tbody tr')].filter((r) => r.getClientRects().length > 0).length }));
    check(`${tag}: opening #credits-fold shows one credits row per images.json player (${N_CREDITS})`,
      fold0 === false && credits.open && credits.rows === N_CREDITS, { closedByDefault: fold0 === false, ...credits });

    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(outDir, `v5-${tag}.png`), fullPage: true });

    // deep links: #f7 opens formula 7, #credits opens the credits fold (fresh loads)
    const deep = {};
    for (const [hash, test] of [['f7', () => document.getElementById('f7')?.open === true], ['credits', () => document.getElementById('credits-fold')?.open === true]]) {
      const p2 = await ctx.newPage();
      const log2 = attachCollectors(p2);
      await p2.goto(base + 'method.html#' + hash, { waitUntil: 'domcontentloaded' });
      deep[hash] = await p2.waitForFunction(test, null, { timeout: 10000 }).then(() => true).catch(() => false);
      deep[hash + 'Errors'] = errorsOf(log2).concat(log2.failedRequests);
      await p2.close();
    }
    check(`${tag}: method.html#f7 opens card f7`, deep.f7 && deep.f7Errors.length === 0, deep);
    check(`${tag}: method.html#credits opens #credits-fold`, deep.credits && deep.creditsErrors.length === 0, deep);

    check(`${tag}: no console errors, page errors or module warnings`, errorsOf(log).length === 0, errorsOf(log));
    check(`${tag}: no failed requests (font hosts are warnings)`, log.failedRequests.length === 0, log.failedRequests);
    runs.push({ tag, fx: fx.fx, credits: credits.rows, height: await page.evaluate(() => document.documentElement.scrollHeight) });
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
    const who = await whoNow(page);
    // read in the same task as the boot check: no waiting for an animation
    const at0 = await page.evaluate(() => [...document.querySelectorAll('#court path')].map((p) => getComputedStyle(p).strokeDashoffset));
    check(`${tag}: page boots (typical pro, men) under prefers-reduced-motion`, who === DEFAULT_H2, { who });
    check(`${tag}: every court path has stroke-dashoffset 0 at once`, at0.length === 12 && at0.every((o) => parseFloat(o) === 0), at0);
    // click and read the labels in one evaluate: they must already be final
    const sw = await page.evaluate(() => {
      const lab = () => [...document.querySelectorAll('#court text[font-size="19"]')].map((t) => t.textContent.trim());
      const before = lab();
      document.querySelector('#seg button[data-serve="2"]').click();
      const soon = lab();
      const offs = [...document.querySelectorAll('#court path')].map((p) => getComputedStyle(p).strokeDashoffset);
      return { before, soon, offs, seg: [...document.querySelectorAll('#seg button')].map((b) => b.getAttribute('aria-pressed')).join() };
    });
    const settledLater = await page.waitForFunction((s) => [...document.querySelectorAll('#court text[font-size="19"]')].map((t) => t.textContent.trim()).join() === s,
      sw.soon.join(), { timeout: 1000 }).then(() => true).catch(() => false);
    check(`${tag}: switching to 2nd serve updates the labels at once and the paths stay at stroke-dashoffset 0`,
      sw.seg === 'false,true' && sw.before.length === 6 && sw.soon.join() !== sw.before.join() && settledLater && sw.offs.every((o) => parseFloat(o) === 0),
      { before: sw.before, soon: sw.soon, seg: sw.seg });
    check(`${tag}: no errors`, errorsOf(log).length === 0 && log.failedRequests.length === 0, errorsOf(log).concat(log.failedRequests));
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
