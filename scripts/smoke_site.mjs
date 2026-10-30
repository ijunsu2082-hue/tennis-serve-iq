#!/usr/bin/env node
// Serve IQ static-site smoke test (headless browser), v6: "Call the serve" on the court, a scouting card,
// and the match model section.
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
// Expected numbers are computed here from data/serveiq.json and data/match.json with the same rules as
// js/tool.js (typical-pro lane by league_share, a split when the top two are within 0.03, the scouting-edge
// gate on 95% ranges, serves needed = z^2 (p1 + p2 - d^2) / d^2), never read from the page.
//
// What it checks (each line of the JSON summary is one check):
//   index.html at 1440x900 and 390x844:
//     - boots (html.data-ready) on Kyrgios, ad side, 1st serve, all points, in the call phase: the first quick
//       pick pressed, the prompt names Kyrgios and the ad side, #result hidden, the card asks for a call
//     - the court has 6 serve paths (plus 6 shades); the ad side's 3 share labels show "?", the deuce side's
//       are empty, and the ad paths are rewound (stroke-dashoffset > 0); 3 lane buttons in court order
//     - zero console errors, page errors, module warnings and failed requests (font hosts are warnings);
//       no horizontal scroll; no banned characters, placeholders, "league", pronouns, "predictable",
//       "repeats", "pattern", "insight" or "you were right" in the rendered text; height under the cap
//   index.html interaction (1440):
//     - Kyrgios ad, call "Down the middle": the big number, "Your call", "Typical pro's lane" and "Random
//       guess" equal the data (whole numbers in 100); the scouting-edge line shows the computed edge; the ad
//       paths draw in (stroke-dashoffset 0); the card opens with the favorite, the range and serves needed
//     - "Now make it a break point": #seg-sit flips, the big number is the break-point share, and the
//       break-point line lists exactly the lanes whose change passes its test
//     - "Call again" goes back to the call phase
//     - Nadal by search, deuce side: each lane call shows that lane's share; "Best call" appears only off the
//       favorite; "Same lane as the typical pro" appears (Nadal's deuce favorite is the typical-pro lane)
//     - Henin quick pick (ad): the typical-pro split note and the averaged bar
//     - every quick pick passes the scouting-edge gate (computed here)
//     - clicking a lane on the court itself (not a button) makes the call
//     - #explore: one dot per 1st-serve player-side, the filled count equals the gated count computed here, only
//       live dots take focus, the Women filter shows the women's sides, and Enter on a live dot loads it
//     - #match: the strip has one item per point in data/match.json, and the claim prints named of served
//   data/serveiq.json aborted, index at 1440 and 390: html.no-data, #status shows the error with a link to
//     serve-iq.streamlit.app, every .needs-data part is hidden.
//   method.html at 1440 and 390: boots, no errors, no horizontal scroll (cards closed and open), no banned
//     characters, FORMULA_COUNT details.fcard all closed by default, KaTeX renders every .fx block, the
//     credits fold lists every photo, method.html#f7 and #credits deep links open.
//   Reduced motion, index at 1440 and 390: boots; a call reveals the paths at once (stroke-dashoffset 0 in
//     the same task); no errors.
//   Screenshots: <outDir>/v6-index-<w>.png (full page), v6-index-<w>-first.png, v6-index-1440-called.png and
//   v6-method-<w>.png.
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
for (const f of ['index.html', 'method.html', 'data/serveiq.json', 'data/images.json', 'data/match.json']) {
  if (!fs.existsSync(path.join(siteDir, f))) {
    console.error(`no ${f} in ${siteDir}`);
    process.exit(2);
  }
}
fs.mkdirSync(outDir, { recursive: true });
const DATA = JSON.parse(fs.readFileSync(path.join(siteDir, 'data/serveiq.json'), 'utf8'));
const MATCH = JSON.parse(fs.readFileSync(path.join(siteDir, 'data/match.json'), 'utf8'));
const IMAGES = JSON.parse(fs.readFileSync(path.join(siteDir, 'data/images.json'), 'utf8'));
const IMG_PLAYERS = IMAGES.players || {};
const N_CREDITS = Object.keys(IMG_PLAYERS).filter((k) => IMG_PLAYERS[k] && IMG_PLAYERS[k].src).length;
const FORMULA_COUNT = 14;
const SIDES = ['deuce', 'ad'];
const DIRS = ['wide', 'body', 'T'];
const LANE = { wide: 'Out wide', body: 'At the body', T: 'Down the middle' };
const ORDER = { deuce: ['wide', 'body', 'T'], ad: ['T', 'body', 'wide'] };
const PICKS = [['Nick Kyrgios', 'ad'], ['Justine Henin', 'ad'], ['Pete Sampras', 'ad'], ['Naomi Osaka', 'ad']];
const NO_CLEAR = 0.03, Z = 1.96, Q = DATA.meta.thresholds.q;
const LIMITS = { 1440: { w: 1440, h: 900, maxHeight: 2800 }, 390: { w: 390, h: 844, maxHeight: 5000 } };
// the web font hosts: a failure there (no network) is a warning, not a failed check
const FONT_HOST = /^https?:\/\/fonts\.(googleapis|gstatic)\.com\//;
const r100 = (x) => Math.round(100 * x);
const fInt = (x) => new Intl.NumberFormat('en-US').format(Math.round(x));

// ---------- expected values from the data files ----------
const MF = {}; DATA.mix.fields.forEach((f, i) => { MF[f] = i; });
const TF = {}; DATA.tests.fields.forEach((f, i) => { TF[f] = i; });
const genderOf = (name) => {
  const f = DATA.players.fields;
  const r = DATA.players.rows.find((x) => x[f.indexOf('server')] === name);
  return r ? r[f.indexOf('gender')] : null;
};
function cells(name, side, sit, serve) {
  const m = DATA.mix.data[genderOf(name) + '|' + name + '|' + (serve || 1)];
  const out = {};
  DIRS.forEach((d) => { const a = m[side][sit][d]; out[d] = {}; Object.keys(MF).forEach((k) => { out[d][k] = a[MF[k]]; }); });
  return out;
}
function test(name, side, serve) {
  const t = DATA.tests.data[genderOf(name) + '|' + name + '|' + (serve || 1)][side];
  const o = {}; Object.keys(TF).forEach((k) => { o[k] = t[TF[k]]; });
  return o;
}
const ranked = (c) => DIRS.filter((d) => typeof c[d].share === 'number').sort((a, b) => c[b].share - c[a].share);
function favorite(name, side) {
  const c = cells(name, side, 'all'), r = ranked(c);
  if (r.length >= 2 && c[r[0]].share - c[r[1]].share < NO_CLEAR) return null;
  return test(name, side).top_dir;
}
function typicalLanes(c) {
  const r = DIRS.filter((d) => typeof c[d].league_share === 'number').sort((a, b) => c[b].league_share - c[a].league_share);
  return r.length > 1 && c[r[0]].league_share - c[r[1]].league_share < NO_CLEAR ? [r[0], r[1]] : [r[0]];
}
function edge(name, side) {
  const c = cells(name, side, 'all');
  const fav = favorite(name, side) || ranked(c)[0];
  const typ = typicalLanes(c);
  const base = typ.reduce((t, d) => t + c[d].share, 0) / typ.length;
  const others = typ.filter((d) => d !== fav);
  return { fav, typ, base, same: typ.length === 1 && typ[0] === fav,
    clear: others.length > 0 && others.every((d) => c[fav].share_lo > c[d].share_hi), value: c[fav].share - base };
}
function need(name, side) {
  const c = cells(name, side, 'all'), r = ranked(c), t = test(name, side);
  const p1 = c[r[0]].share, p2 = c[r[1]].share, d = p1 - p2;
  const n = (Z * Z * (p1 + p2 - d * d)) / (d * d);
  return { serves: n, matches: (n * t.matches) / t.n };
}
const bpShifts = (name, side) => { const t = test(name, side); return DIRS.filter((d) => typeof t['bp_' + d + '_q'] === 'number' && t['bp_' + d + '_q'] < Q); };

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

// index: data bound, the quick picks built and the call prompt written
async function waitForIndex(page) {
  await page.waitForFunction(() => document.documentElement.classList.contains('data-ready') ||
    document.documentElement.classList.contains('no-data'), null, { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => document.querySelectorAll('#picks .pick').length > 0 &&
    (document.getElementById('prompt')?.textContent || '').trim() && document.querySelectorAll('#court path').length > 0 &&
    document.querySelectorAll('#match .pt').length > 0, null, { timeout: 15000 }).catch(() => {});
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
  [/\b(predictable|predictability|repeats?)\b/gi, '"predictable"/"repeats" (servers over-alternate: say "lopsided")'],
  [/\byou were (right|wrong)\b/gi, '"you were right" (returners were never observed)'],
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

// ---------- page readers ----------
const state = (page) => page.evaluate(() => {
  const txt = (s) => (document.querySelector(s)?.textContent || '').replace(/\s+/g, ' ').trim();
  const r = document.getElementById('result');
  const bars = {};
  document.querySelectorAll('#result .rb').forEach((b) => { bars[b.querySelector('.rl').textContent.trim()] = Number(b.querySelector('.rv').textContent.trim()); });
  return {
    prompt: txt('#prompt'),
    resultShown: !!r && !r.hidden && r.getClientRects().length > 0,
    big: Number(txt('#result .big b')),
    lead: txt('#result .lead'),
    lines: [...document.querySelectorAll('#result .line, #result .note')].map((p) => p.textContent.replace(/\s+/g, ' ').trim()),
    bars,
    card: (document.getElementById('card')?.innerText || '').replace(/\s+/g, ' ').trim(),
    picks: [...document.querySelectorAll('#picks .pick')].map((b) => [b.getAttribute('data-key'), b.getAttribute('aria-pressed')]),
    side: [...document.querySelectorAll('#seg-side button')].map((b) => b.getAttribute('aria-pressed')).join(),
    sit: [...document.querySelectorAll('#seg-sit button')].map((b) => b.getAttribute('aria-pressed')).join(),
    lanebtns: [...document.querySelectorAll('#lanebtns .lanebtn')].map((b) => b.textContent.trim()),
    labels: [...document.querySelectorAll('#court text[font-size="19"]')].map((t) => t.textContent.trim()),
  };
});
// path dash offsets per lane key, in court.js creation order (deuce wide, body, T, ad wide, body, T; path then shade)
const offsets = (page) => page.evaluate(() => [...document.querySelectorAll('#court g[fill="none"] path')].map((p) => parseFloat(getComputedStyle(p).strokeDashoffset)));
const settled = (page, side) => page.waitForFunction((s) => {
  const ps = [...document.querySelectorAll('#court g[fill="none"] path')];
  const mine = s === 'deuce' ? ps.slice(0, 6) : ps.slice(6, 12);
  return mine.length === 6 && mine.every((p) => parseFloat(getComputedStyle(p).strokeDashoffset) === 0);
}, side, { timeout: 8000 }).then(() => true).catch(() => false);
const callLane = async (page, dir) => {
  await page.locator(`#lanebtns .lanebtn[data-dir="${dir}"]`).click();
  await page.waitForFunction(() => { const r = document.getElementById('result'); return r && !r.hidden; }, null, { timeout: 3000 }).catch(() => {});
};
const act = async (page, a) => { await page.locator(`#result button[data-act="${a}"]`).click(); await page.waitForTimeout(80); };

async function findPlayer(page, text) {
  const inp = page.locator('#find');
  await inp.scrollIntoViewIfNeeded();
  await inp.click();
  await inp.fill('');
  await inp.pressSequentially(text, { delay: 8 });
  await inp.press('Enter');
  await page.waitForFunction((t) => (document.getElementById('prompt')?.textContent || '').includes(t.split(' ').pop()), text, { timeout: 5000 }).catch(() => {});
}

// ---------- main ----------
const server = await startServer();
const base = `http://127.0.0.1:${server.address().port}/`;
const { browser, browserName } = await launchBrowser();
const runs = [];

try {
  // ---------- index at both widths: the first screen ----------
  for (const w of [1440, 390]) {
    const L = LIMITS[w];
    const tag = `index-${w}`;
    tagNow = tag;
    const { ctx, page, log } = await newPage(browser, { w: L.w, h: L.h });
    let gotoErr = null;
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch((e) => { gotoErr = e.message; });
    await waitForIndex(page);
    await page.waitForTimeout(400);
    const ready = await page.evaluate(() => document.documentElement.classList.contains('data-ready'));
    check(`${tag}: page boots and binds the data`, !gotoErr && ready, gotoErr || { ready });
    await page.screenshot({ path: path.join(outDir, `v6-${tag}-first.png`), fullPage: false });
    const st = await state(page);
    check(`${tag}: boots on Kyrgios, ad side, 1st serve, all points, call phase`,
      st.picks.length === PICKS.length && st.picks[0][0] === 'Nick Kyrgios|ad' && st.picks[0][1] === 'true' && st.side === 'false,true' &&
      st.sit === 'true,false' && /^Kyrgios serves from the ad side\. Tap where the first serve goes/.test(st.prompt) && !st.resultShown &&
      /Make your call/.test(st.card), st);
    const offs = await offsets(page);
    check(`${tag}: 6 serve paths plus shades; ad labels "?", deuce labels empty; ad paths rewound`,
      offs.length === 12 && st.labels.join('|') === '|||?|?|?' && offs.slice(6).every((o) => o > 0), { labels: st.labels, offs });
    check(`${tag}: three lane buttons in court order for the ad side`, st.lanebtns.join('|') === ORDER.ad.map((d) => LANE[d]).join('|'), st.lanebtns);
    const ov = await overflow(page);
    check(`${tag}: no horizontal page scroll`, ov.scrollWidth <= Math.min(ov.innerWidth, w) && ov.wideEls.length === 0, ov);
    // reveal once so the scan covers the result and the card too
    await callLane(page, 'T');
    await settled(page, 'ad');
    const scan = await scanText(page);
    check(`${tag}: no banned characters or terms`, hits(scan, BANNED).length === 0, hits(scan, BANNED));
    check(`${tag}: no "league", pronouns, "predictable", "repeats" or other banned voice words`, hits(scan, INDEX_BANNED).length === 0, hits(scan, INDEX_BANNED));
    const visible = await page.evaluate(() => document.body.innerText);
    check(`${tag}: no placeholder text (NaN, undefined, n/a, null)`, hits(visible, PLACEHOLDER).length === 0, hits(visible, PLACEHOLDER));
    const ov2 = await overflow(page);
    check(`${tag}: no horizontal page scroll after a call`, ov2.scrollWidth <= Math.min(ov2.innerWidth, w) && ov2.wideEls.length === 0, ov2);
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    check(`${tag}: page height at most ${L.maxHeight.toLocaleString('en-US')}px`, height <= L.maxHeight, { height });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(outDir, `v6-${tag}.png`), fullPage: true });
    check(`${tag}: no console errors, page errors or module warnings`, errorsOf(log).length === 0, errorsOf(log));
    check(`${tag}: no failed requests (font hosts are warnings)`, log.failedRequests.length === 0, log.failedRequests);
    runs.push({ tag, height });
    await ctx.close();
  }

  // ---------- interaction (1440) ----------
  {
    const tag = 'interact';
    tagNow = tag;
    const { ctx, page, log } = await newPage(browser, { w: 1440, h: 900 });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await waitForIndex(page);

    // Kyrgios ad, call down the middle
    const K = 'Nick Kyrgios';
    const kc = cells(K, 'ad', 'all'), ke = edge(K, 'ad'), kn = need(K, 'ad');
    await callLane(page, 'T');
    const drawn = await settled(page, 'ad');
    let st = await state(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(150);
    await page.screenshot({ path: path.join(outDir, 'v6-index-1440-called.png'), fullPage: false });
    const want = { big: r100(kc.T.share), you: r100(kc.T.share), typ: r100(ke.base), rnd: 33 };
    check(`${tag}: Kyrgios ad, down the middle: big number and bars equal the data (${JSON.stringify(want)})`,
      st.resultShown && st.big === want.big && st.bars['Your call'] === want.you && st.bars["Typical pro's lane"] === want.typ && st.bars['Random guess'] === 33,
      { got: { big: st.big, bars: st.bars }, want });
    check(`${tag}: Kyrgios ad: the scouting-edge line shows ${r100(ke.value)} (gate passes: ${ke.clear})`,
      ke.clear && st.lines.some((l) => l === `Scouting edge: ${r100(ke.value)} more in 100 than guessing like the typical pro.`), st.lines);
    check(`${tag}: the ad paths draw in (stroke-dashoffset 0) and the labels show shares`, drawn && st.labels.slice(3).every((x) => /^\d+%$/.test(x)), st.labels);
    check(`${tag}: the card opens with the favorite, the range and serves needed (about ${fInt(kn.serves)})`,
      /Scouting card, ad side/.test(st.card) && /Leans Down the middle, \d+ in 100/.test(st.card) &&
      st.card.includes(`Likely range ${r100(kc.T.share_lo)} to ${r100(kc.T.share_hi)} in 100`) && st.card.includes(`About ${fInt(kn.serves)} charted serves from this side, about ${fInt(kn.matches)} matches`), st.card);

    // break points
    await act(page, 'sit');
    st = await state(page);
    const kbp = cells(K, 'ad', 'bp'), ksh = bpShifts(K, 'ad');
    check(`${tag}: "Now make it a break point" flips #seg-sit and shows the break-point share (${r100(kbp.T.share)})`,
      st.sit === 'false,true' && st.big === r100(kbp.T.share) && /^Break point\./.test(st.prompt), { sit: st.sit, big: st.big, prompt: st.prompt });
    const bpLine = st.lines.find((l) => /^On break points:/.test(l)) || '';
    check(`${tag}: the break-point line lists exactly the lanes whose change passes its test (${ksh.join(', ')})`,
      ksh.length > 0 && ksh.every((d) => bpLine.includes(LANE[d].toLowerCase())) && DIRS.filter((d) => !ksh.includes(d)).every((d) => !bpLine.includes(LANE[d].toLowerCase())), bpLine);
    await act(page, 'sit');
    await act(page, 'again');
    st = await state(page);
    check(`${tag}: "Call again" returns to the call phase`, !st.resultShown && st.labels.slice(3).every((x) => x === '?') && /Make your call/.test(st.card), st);

    // Nadal by search, deuce side
    const N = 'Rafael Nadal';
    await findPlayer(page, N);
    await page.locator('#seg-side button[data-side="deuce"]').click();
    const nc = cells(N, 'deuce', 'all'), nfav = favorite(N, 'deuce'), ne = edge(N, 'deuce');
    const per = {};
    for (const d of DIRS) {
      await callLane(page, d);
      st = await state(page);
      per[d] = { big: st.big, want: r100(nc[d].share), best: st.lines.some((l) => /^Best call:/.test(l)), same: st.lines.some((l) => /^Same lane as the typical pro/.test(l)) };
      await act(page, 'again');
    }
    check(`${tag}: Nadal deuce: each lane call shows that lane's share`, DIRS.every((d) => per[d].big === per[d].want), per);
    check(`${tag}: Nadal deuce: "Best call" only off the favorite (${nfav})`, DIRS.every((d) => per[d].best === (d !== nfav)), per);
    check(`${tag}: Nadal deuce: "Same lane as the typical pro" (computed: ${ne.same})`, ne.same && DIRS.every((d) => per[d].same), per);

    // Henin ad: typical pro splits
    const H = 'Justine Henin', he = edge(H, 'ad');
    await page.locator('#picks .pick[data-name="Justine Henin"]').click();
    await callLane(page, 'T');
    st = await state(page);
    check(`${tag}: Henin ad: typical-pro split note and the averaged bar (${r100(he.base)}, split ${he.typ.join('/')})`,
      he.typ.length === 2 && st.lines.some((l) => /^The typical pro splits between/.test(l)) && st.bars["Typical pro's lanes"] === r100(he.base), { lines: st.lines, bars: st.bars });

    // every quick pick passes the gate
    const gates = PICKS.map(([n, s]) => ({ n, s, ...edge(n, s) }));
    check(`${tag}: every quick pick passes the scouting-edge gate`, gates.every((g) => g.clear && g.value > 0), gates.map((g) => [g.n, g.s, g.clear, r100(g.value)]));

    // a click on the court lane itself makes the call
    await act(page, 'again');
    const hit = await page.evaluate(() => {
      const on = [...document.querySelectorAll('#court .lane-hit.on')];
      const r = on[0] && on[0].getBoundingClientRect();
      return { n: on.length, x: r ? r.left + r.width / 2 : 0, y: r ? r.top + r.height * 0.7 : 0 };
    });
    await page.mouse.click(hit.x, hit.y);
    await page.waitForTimeout(150);
    st = await state(page);
    check(`${tag}: clicking a lane on the court makes the call`, hit.n === 3 && st.resultShown, { hit, shown: st.resultShown });

    // explorer: one dot per 1st-serve player-side; filled = gated edge; Enter on a live dot loads it
    const allSides = [];
    for (const n of DATA.players.rows.map((r) => r[DATA.players.fields.indexOf('server')])) {
      if (!DATA.mix.data[genderOf(n) + '|' + n + '|1']) continue;
      for (const s of SIDES) { const e = edge(n, s); allSides.push({ n, s, filled: test(n, s).q_mix < Q && e.clear && e.value > 0 }); }
    }
    const wantFilled = allSides.filter((x) => x.filled).length;
    const ex = await page.evaluate(() => ({ dots: document.querySelectorAll('#scatter circle.dot').length,
      filled: [...document.querySelectorAll('#scatter circle.dot')].filter((c) => c.getAttribute('fill') === '#0a62d6').length,
      live: document.querySelectorAll('#scatter circle.dot.live[tabindex="0"]').length, cap: document.getElementById('scatter-cap')?.textContent || '' }));
    check(`${tag}: explorer has ${allSides.length} dots and ${wantFilled} filled (computed here); only live dots take focus`,
      ex.dots === allSides.length && ex.filled === wantFilled && ex.live > 0 && ex.live < ex.dots && ex.cap.includes(fInt(wantFilled)), ex);
    await page.locator('#seg-tour button[data-tour="w"]').click();
    const nW = await page.evaluate(() => document.querySelectorAll('#scatter circle.dot').length);
    const wantW = allSides.filter((x) => genderOf(x.n) === 'w').length;
    check(`${tag}: the Women filter shows ${wantW} dots`, nW === wantW, { nW, wantW });
    await page.locator('#seg-tour button[data-tour="all"]').click();
    const target = await page.evaluate(() => { const c = document.querySelector('#scatter circle.dot.live'); return c ? { name: c.dataset.name, side: c.dataset.side } : null; });
    await page.locator('#scatter circle.dot.live').first().focus();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(300);
    st = await state(page);
    check(`${tag}: Enter on a live dot loads that player and side into the court (${target && target.name}, ${target && target.side})`,
      !!target && st.prompt.includes(target.name.split(' ').pop()) && st.prompt.includes(`from the ${target.side} side`) && !st.resultShown, { target, prompt: st.prompt });

    // match model section
    const mt = MATCH.totals;
    const mm = await page.evaluate(() => ({ pts: document.querySelectorAll('#match .pt').length, claim: (document.querySelector('#match .claim')?.textContent || '').replace(/\s+/g, ' ') }));
    check(`${tag}: #match strip has ${MATCH.points.length} points and the claim prints ${mt.named} of ${mt.served}`,
      mm.pts === MATCH.points.length && mm.claim.includes(`${mt.named} of ${mt.served}`) && mm.claim.includes(`${mt.winners} of ${mt.points}`) &&
      mm.claim.includes(`about ${fInt(MATCH.need.serves_per_match)} first serves`) && mm.claim.includes(`about ${fInt(MATCH.need.serves_q[1])}`), mm);

    check(`${tag}: no console errors, page errors or module warnings`, errorsOf(log).length === 0, errorsOf(log));
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
    check(`${tag}: ${FORMULA_COUNT} details.fcard, all closed by default`, cards.length === FORMULA_COUNT && cards.every((c) => !c.open), cards.filter((c) => c.open).map((c) => c.id).concat([`count=${cards.length}`]));
    const ov0 = await overflow(page);
    const scan = await scanText(page);

    // open every card (via JS), then KaTeX must be visible in every .fx block
    await page.evaluate(() => document.querySelectorAll('details.fcard').forEach((d) => { d.open = true; }));
    const fx = await page.evaluate(() => {
      const all = [...document.querySelectorAll('.fx')];
      return { fx: all.length, katex: all.filter((b) => b.querySelector('.katex') && b.getClientRects().length > 0 && b.getBoundingClientRect().height > 0).length,
        errors: document.querySelectorAll('.katex-error, .tex-raw').length, open: document.querySelectorAll('details.fcard[open]').length };
    });
    check(`${tag}: after opening all ${FORMULA_COUNT} cards KaTeX renders every .fx block (${fx.fx}) with no errors`,
      fx.open === FORMULA_COUNT && fx.fx >= FORMULA_COUNT + 1 && fx.katex === fx.fx && fx.errors === 0, fx);
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
    await page.screenshot({ path: path.join(outDir, `v6-${tag}.png`), fullPage: true });

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
    const st = await state(page);
    check(`${tag}: page boots (Kyrgios, ad side) under prefers-reduced-motion`, /^Kyrgios serves from the ad side/.test(st.prompt), st.prompt);
    // click and read the paths in one evaluate: they must already be drawn
    const at = await page.evaluate(() => {
      document.querySelector('#lanebtns .lanebtn[data-dir="wide"]').click();
      const ps = [...document.querySelectorAll('#court g[fill="none"] path')].slice(6);
      return ps.map((p) => parseFloat(getComputedStyle(p).strokeDashoffset));
    });
    check(`${tag}: a call reveals the ad paths at once (stroke-dashoffset 0 in the same task)`, at.length === 6 && at.every((o) => o === 0), at);
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
