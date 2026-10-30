#!/usr/bin/env node
// Serve IQ static-site smoke test (headless browser), v3 dashboard layout.
//
// Usage:  node scripts/smoke_site.mjs <siteDir> <outDir> [port]
//
// Playwright is NOT installed in this repo. The script loads it from, in order:
//   1. normal module resolution,
//   2. $PLAYWRIGHT_DIR (a folder with node_modules/playwright),
//   4. /private/tmp/serveiq-harness  (npm i playwright@1.49.1 there).
// Browser: Playwright's bundled Chromium if installed, else system Google Chrome
// (channel "chrome"), else $CHROME_PATH, else the Chrome app path on macOS.
//
// What it checks (each line of the JSON summary is one check):
//   index.html and method.html at 1440 and 375 px, light and dark: page loads, zero console errors,
//   zero "Serve IQ:" module warnings, zero failed requests, no horizontal overflow, no banned characters or
//   terms, no leftover placeholder text (n/a, NaN, undefined), no broken or unloaded images after scrolling
//   the whole page, dark scheme changes the body background. method.html: KaTeX renders every formula with
//   no errors, the credits list has one entry per images.json entry and every credit photo loads.
//   index.html interaction (1440, light): combobox keyboard selection of Rafael Nadal, 1st serve, break
//   points, deuce-side wide zone shows 45.0% (break points) and 30.0% (other points), compare mode with a
//   second player, hash state restores the player, Women switch and a woman picked by search, court zones are
//   keyboard focusable, league controls change the cards, a league table opens. Mobile menu opens at 375.
//   Reduced motion: every reveal target is visible at once and count-ups show their final value, on both
//   pages. JS fallback: with js/main.js blocked, hidden reveal targets become visible after the head timer.
//   Full-page screenshots go to <outDir>/<page>-<width>-<scheme>.png.
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
const N_CREDITS = Object.keys(IMAGES.players || {}).length + Object.keys(IMAGES.scenes || {}).length;

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
    ['bundled chromium', {}],
    ['system chrome (channel)', { channel: 'chrome' }],
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
  '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.csv': 'text/csv; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.webp': 'image/webp', '.ico': 'image/x-icon',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf',
  '.xml': 'application/xml', '.webmanifest': 'application/manifest+json', '.map': 'application/json',
};
function startServer() {
  const server = http.createServer((req, res) => {
    try {
      const p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      let file = path.normalize(path.join(siteDir, p));
      if (!file.startsWith(siteDir)) { res.writeHead(403); return res.end('forbidden'); }
      if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
      if (!fs.existsSync(file)) { res.writeHead(404, { 'content-type': 'text/plain' }); return res.end('not found'); }
      res.writeHead(200, {
        'content-type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream',
        'cache-control': 'no-store',
      });
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
const fmtInt = (v) => Number(v).toLocaleString('en-US');

function attachCollectors(page) {
  const log = { consoleErrors: [], moduleWarnings: [], pageErrors: [], failedRequests: [] };
  page.on('console', (m) => {
    const where = (m.location() && m.location().url) || '';
    const line = `${m.text().slice(0, 300)}${where ? ' @ ' + where : ''}`;
    if (m.type() === 'warning' && /Serve IQ/.test(m.text())) { log.moduleWarnings.push(line); return; }
    if (m.type() !== 'error') return;
    // A missing /favicon.ico is requested by the browser itself; report it but do not fail on it.
    if (/\/favicon\.ico$/.test(where)) { warnings.push(`${tagNow}: ${line}`); return; }
    log.consoleErrors.push(line);
  });
  page.on('pageerror', (e) => log.pageErrors.push(String(e.message || e).slice(0, 300)));
  page.on('requestfailed', (r) => {
    const why = r.failure()?.errorText || 'failed';
    if (/ERR_ABORTED/.test(why) && /fonts\.(googleapis|gstatic)/.test(r.url())) return; // font request cancelled by navigation
    log.failedRequests.push(`${why} ${r.url()}`);
  });
  page.on('response', (r) => { if (r.status() >= 400) log.failedRequests.push(`HTTP ${r.status()} ${r.url()}`); });
  return log;
}
const errorsOf = (log) => [...log.consoleErrors, ...log.pageErrors, ...log.moduleWarnings];

// record image load errors before the page's own handler removes the broken <img>
const INIT_SCRIPT = `
  window.__imgErrors = [];
  addEventListener('error', function (e) {
    var t = e.target;
    if (t && t.tagName === 'IMG') window.__imgErrors.push(t.currentSrc || t.src || '(no src)');
  }, true);
`;

async function waitForBoot(page, which) {
  await page.waitForFunction(() => document.documentElement.classList.contains('data-ready') ||
    document.documentElement.classList.contains('no-data'), null, { timeout: 20000 }).catch(() => {});
  if (which === 'index') {
    await page.waitForFunction(() => {
      const n = document.getElementById('pl-name');
      const t = document.querySelector('#lg-a [data-lg-slot="title"]');
      return n && n.textContent.trim() && t && t.textContent.trim() && document.querySelectorAll('#pl-court .zone').length >= 6;
    }, null, { timeout: 15000 }).catch(() => {});
  } else {
    await page.waitForFunction(() => document.querySelectorAll('#credits-list .credit').length > 0 &&
      document.querySelectorAll('.fx .katex, .fx .tex-raw, .fx code').length > 0, null, { timeout: 15000 }).catch(() => {});
  }
  await page.waitForTimeout(300);
}

// scroll the whole page so reveal targets, count-ups and lazy images run, then return to the top
async function scrollThrough(page) {
  await page.evaluate(async () => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const step = Math.max(200, Math.floor(window.innerHeight * 0.6));
    for (let y = 0; y <= document.documentElement.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await sleep(110);
    }
    window.scrollTo(0, document.documentElement.scrollHeight);
    await sleep(400);
  });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
  await page.waitForTimeout(1400); // count-ups (1.2 s) and grow-ins finish
  await page.waitForFunction(() => [...document.querySelectorAll('[data-reveal]')].every((e) => !e.getClientRects().length || Number(getComputedStyle(e).opacity) >= 0.99),
    null, { timeout: 6000, polling: 100 }).catch(() => {});
}

// Static runs only: stop CSS transitions so a reveal that has started (is-in set) is measured at its end
// state, not mid-fade on a busy machine. A target the observer never reached still reads opacity 0.
// Motion itself is tested by the reduced-motion and JS-fallback runs.
async function freezeTransitions(page) {
  await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}' }).catch(() => {});
  await page.waitForTimeout(100);
}

// method.html: scroll the credits list into view and wait until every thumbnail is decoded
async function creditImagesLoaded(page) {
  await page.evaluate(async () => {
    const list = document.getElementById('credits-list');
    if (!list) return;
    const imgs = [...list.querySelectorAll('img')];
    for (const i of imgs) { if (i.loading === 'lazy') i.loading = 'eager'; }
    list.scrollIntoView({ block: 'start' });
    await Promise.race([
      Promise.all(imgs.map((i) => (i.decode ? i.decode() : Promise.resolve()).catch(() => {}))),
      new Promise((r) => setTimeout(r, 8000)),
    ]);
  }).catch(() => {});
  await page.waitForFunction(() => [...document.querySelectorAll('#credits-list img')].every((i) => i.complete && i.naturalWidth > 0),
    null, { timeout: 8000, polling: 100 }).catch(() => {});
  return page.evaluate(() => [...document.querySelectorAll('#credits-list img')].filter((i) => i.complete && i.naturalWidth > 0).length);
}

async function imageState(page) {
  // lazy images off to the side (the chip row scrolls inside itself) are forced to load, then every <img> is checked
  await page.evaluate(() => [...document.images].forEach((i) => { if (i.loading === 'lazy') i.loading = 'eager'; }));
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForFunction(() => [...document.images].every((i) => i.complete), null, { timeout: 10000, polling: 100 }).catch(() => {});
  return page.evaluate(() => {
    const imgs = [...document.images];
    const broken = imgs.filter((i) => i.complete && i.naturalWidth === 0 && i.getAttribute('src')).map((i) => i.getAttribute('src'));
    const unloaded = imgs.filter((i) => !i.complete).map((i) => i.getAttribute('src'));
    return { total: imgs.length, loaded: imgs.filter((i) => i.complete && i.naturalWidth > 0).length, broken, unloaded, errors: window.__imgErrors || [] };
  });
}

// All text in the DOM, including hidden panels and aria-labels / popover sentences, minus KaTeX, script and style.
async function scanText(page) {
  return page.evaluate(() => {
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,.katex,.katex-mathml,annotation,.tex,.fx').forEach((n) => n.remove());
    // one line per text node, so text from neighboring elements never runs together ("?" + "No clear favorite")
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
  [/[←-⇿⟰-⟿⤀-⥿]/g, 'arrow'], [/·/g, 'middle dot'], [/~\s?\d/g, 'tilde as about'],
  [/[⌀-⏿☀-➿⬀-⯿]/g, 'pictograph'], [/[\u{1F000}-\u{1FAFF}]/gu, 'emoji'], [/️/g, 'variation selector'],
  [/guess[ _-]?rate/gi, 'old term "guess rate"'], [/\bhonest(ly)?\b/gi, 'honest'], [/\bnot only\b/gi, 'not only'],
  [/\bmeasurabl[ey]\b/gi, 'measurably'], [/\bcosts? nothing\b/gi, 'costs nothing'], [/\bdive into\b/gi, 'dive into'],
  [/\bunlock/gi, 'unlock'], [/[?][ \t]*(Yes|No)\b/g, '"X? Yes/No"'], [/\b(favourite|colour|analys(e|ed|es|ing)|behaviour|centre)\b/gi, 'British spelling'],
];
const PLACEHOLDER = [[/\bNaN\b/g, 'NaN'], [/\bundefined\b/g, 'undefined'], [/\bn\/a\b/g, 'n/a'], [/\[object /g, '[object'], [/\bnull\b/g, 'null']];
function hits(text, list) {
  const out = [];
  for (const [re, label] of list) {
    const m = text.match(re);
    if (m) {
      const i = text.search(re);
      out.push({ label, count: m.length, context: text.slice(Math.max(0, i - 50), i + 50).replace(/\s+/g, ' ') });
    }
  }
  return out;
}

async function newPage(browser, opts) {
  const ctx = await browser.newContext({
    viewport: { width: opts.w, height: opts.h || 900 }, colorScheme: opts.scheme || 'light',
    isMobile: opts.w < 768, hasTouch: opts.w < 768, reducedMotion: opts.reduce ? 'reduce' : 'no-preference',
  });
  await ctx.addInitScript(INIT_SCRIPT);
  if (opts.block) await ctx.route(opts.block, (r) => r.abort());
  const page = await ctx.newPage();
  const log = attachCollectors(page);
  return { ctx, page, log };
}

const radio = (page, host, value) => page.locator(`${host} button[role="radio"][data-value="${value}"]`);
async function pickRadio(page, host, value) {
  const b = radio(page, host, value);
  await b.scrollIntoViewIfNeeded();
  await b.click();
  await page.waitForTimeout(650);
  return (await b.getAttribute('aria-checked')) === 'true';
}

// keyboard selection in a combobox: type, ArrowDown to the matching option, Enter
async function comboPick(page, inputSel, name) {
  const inp = page.locator(inputSel);
  await inp.scrollIntoViewIfNeeded();
  await inp.click();
  await inp.fill('');
  await inp.pressSequentially(name, { delay: 15 });
  await page.waitForTimeout(250);
  const info = await page.evaluate(([sel, nm]) => {
    const i = document.querySelector(sel);
    const list = document.getElementById(i.getAttribute('aria-controls'));
    const opts = [...list.querySelectorAll('[role="option"]')];
    return { role: i.getAttribute('role'), expanded: i.getAttribute('aria-expanded'), n: opts.length,
      idx: opts.findIndex((o) => o.textContent.includes(nm)), thumbs: list.querySelectorAll('img, .avatar').length };
  }, [inputSel, name]);
  if (info.idx < 0) return { ok: false, ...info };
  for (let k = 0; k <= info.idx; k++) await inp.press('ArrowDown');
  const active = await page.evaluate((sel) => {
    const i = document.querySelector(sel);
    const id = i.getAttribute('aria-activedescendant');
    return id && document.getElementById(id) ? document.getElementById(id).textContent : null;
  }, inputSel);
  await inp.press('Enter');
  await page.waitForTimeout(700);
  const after = await page.evaluate((sel) => document.querySelector(sel).getAttribute('aria-expanded'), inputSel);
  return { ok: !!active && active.includes(name) && after === 'false', ...info, active, expandedAfter: after };
}
const text = (page, sel) => page.locator(sel).first().textContent().then((t) => (t || '').replace(/\s+/g, ' ')).catch(() => '');

// ---------- main ----------
const server = await startServer();
const base = `http://127.0.0.1:${server.address().port}/`;
const { browser, browserName } = await launchBrowser();
const runs = [];
const bg = {};

try {
  // ---------- warm-up: one throwaway load of each page so the first measured run is not a cold start ----------
  for (const which of ['index', 'method']) {
    const { ctx, page } = await newPage(browser, { w: 1440, h: 900, scheme: 'light' });
    await page.goto(base + (which === 'index' ? '' : 'method.html'), { waitUntil: 'load', timeout: 30000 }).catch(() => {});
    await waitForBoot(page, which);
    await ctx.close();
  }

  // ---------- static runs: both pages, two widths, two schemes ----------
  for (const which of ['index', 'method']) {
    for (const w of [1440, 375]) {
      for (const scheme of ['light', 'dark']) {
        const tag = `${which}-${w}-${scheme}`;
        tagNow = tag;
        const { ctx, page, log } = await newPage(browser, { w, h: w < 768 ? 812 : 900, scheme });
        let gotoErr = null;
        await page.goto(base + (which === 'index' ? '' : 'method.html'), { waitUntil: 'domcontentloaded', timeout: 30000 }).catch((e) => { gotoErr = e.message; });
        await waitForBoot(page, which);
        await scrollThrough(page);
        await freezeTransitions(page);
        const m = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth: window.innerWidth,
          ready: document.documentElement.classList.contains('data-ready'),
          bg: getComputedStyle(document.body).backgroundColor,
          title: document.title,
          hidden: [...document.querySelectorAll('[data-reveal]')].filter((e) => e.getClientRects().length && Number(getComputedStyle(e).opacity) < 0.99)
            .map((e) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}.${String(e.className).split(' ')[0]} opacity=${getComputedStyle(e).opacity} in=${e.classList.contains('is-in')}`),
          katex: document.querySelectorAll('.fx .katex').length,
          fx: document.querySelectorAll('.fx').length,
          katexErrors: document.querySelectorAll('.katex-error, .tex-raw').length,
          credits: document.querySelectorAll('#credits-list .credit').length,
          wideEls: [...document.querySelectorAll('body *')].filter((e) => {
            const r = e.getBoundingClientRect();
            if (r.width === 0 || getComputedStyle(e).position === 'fixed') return false;
            for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
              const ox = getComputedStyle(p).overflowX;
              if (ox !== 'visible') return false; // clipped or scrolls inside its own box
            }
            return r.right > window.innerWidth + 1 || r.left < -1;
          }).slice(0, 5).map((e) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${typeof e.className === 'string' && e.className ? '.' + e.className.split(' ')[0] : ''} right=${Math.round(e.getBoundingClientRect().right)}`),
        }));
        const img = await imageState(page);
        if (which === 'method') m.creditImgs = await creditImagesLoaded(page);
        const scan = await scanText(page);
        const visible = await page.evaluate(() => document.body.innerText + '\n' + [...document.querySelectorAll('svg text')].map((t) => t.textContent).join(' '));
        const shot = path.join(outDir, `${tag}.png`);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(250);
        await page.screenshot({ path: shot, fullPage: true });
        bg[`${which}-${w}`] = bg[`${which}-${w}`] || {};
        bg[`${which}-${w}`][scheme] = m.bg;

        check(`${tag}: page loaded and data bound`, !gotoErr && m.ready, gotoErr || { ready: m.ready });
        check(`${tag}: no console errors, page errors or module warnings`, errorsOf(log).length === 0, errorsOf(log));
        check(`${tag}: no failed requests`, log.failedRequests.length === 0, log.failedRequests);
        check(`${tag}: no horizontal page scroll`, m.scrollWidth <= Math.min(m.innerWidth, w) && m.wideEls.length === 0,
          { scrollWidth: m.scrollWidth, innerWidth: m.innerWidth, deviceWidth: w, overflowing: m.wideEls });
        check(`${tag}: no banned characters or terms`, hits(scan, BANNED).length === 0, hits(scan, BANNED));
        check(`${tag}: no placeholder text (n/a, NaN, undefined, null)`, hits(visible, PLACEHOLDER).length === 0, hits(visible, PLACEHOLDER));
        check(`${tag}: every reveal target visible after scrolling`, m.hidden.length === 0, { hidden: m.hidden });
        check(`${tag}: no broken or unloaded images`, img.broken.length + img.unloaded.length + img.errors.length === 0 && img.loaded > 0,
          { loaded: img.loaded, total: img.total, broken: img.broken, unloaded: img.unloaded, errors: img.errors });
        if (which === 'method') {
          check(`${tag}: KaTeX renders every formula block`, m.fx >= 14 && m.katex === m.fx && m.katexErrors === 0, { fx: m.fx, katex: m.katex, errors: m.katexErrors });
          check(`${tag}: credits list count equals images.json entries (${N_CREDITS})`, m.credits === N_CREDITS, { credits: m.credits });
          check(`${tag}: every credit photo loaded`, m.creditImgs === N_CREDITS, { loaded: m.creditImgs, want: N_CREDITS });
        } else {
          const chips = await page.evaluate(() => [...document.querySelectorAll('#pl-chips img')].map((i) => i.complete && i.naturalWidth > 0));
          check(`${tag}: featured player chips show loaded photos`, chips.length >= 6 && chips.every(Boolean), { chips: chips.length, loaded: chips.filter(Boolean).length });
          if (w < 768) {
            const tg = page.locator('[data-nav-toggle]');
            await tg.click();
            await page.waitForTimeout(350);
            const open = await page.evaluate(() => {
              const a = document.querySelector('#nav-links a[href="#players"]');
              return { expanded: document.querySelector('[data-nav-toggle]').getAttribute('aria-expanded'), linkVisible: !!(a && a.getClientRects().length && a.getBoundingClientRect().height > 0) };
            });
            await page.keyboard.press('Escape');
            await page.waitForTimeout(250);
            const closed = await page.evaluate(() => document.querySelector('[data-nav-toggle]').getAttribute('aria-expanded'));
            check(`${tag}: mobile menu opens and closes`, open.expanded === 'true' && open.linkVisible && closed === 'false', { open, closed });
          }
        }
        runs.push({ tag, screenshot: shot, title: m.title, bg: m.bg, scrollWidth: m.scrollWidth, images: img.loaded });
        await ctx.close();
      }
      const b = bg[`${which}-${w}`];
      check(`${which}-${w}: dark scheme changes body background`, b.light !== b.dark, b);
    }
  }

  // ---------- index interaction ----------
  tagNow = 'interaction';
  {
    const { ctx, page, log } = await newPage(browser, { w: 1440, h: 900 });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await waitForBoot(page, 'index');
    const steps = {};
    steps.men = await pickRadio(page, '#pl-tour', 'm');
    steps.combo = await comboPick(page, '#pl-q', 'Rafael Nadal');
    check('interact: combobox keyboard selection (type, ArrowDown, Enter) picks Rafael Nadal', steps.combo.ok && steps.combo.role === 'combobox', steps.combo);
    check('interact: combobox options show photo thumbnails', steps.combo.thumbs > 0, { thumbs: steps.combo.thumbs });
    const name = await text(page, '#pl-name');
    check('interact: player card shows Rafael Nadal', /Rafael Nadal/.test(name), name);
    steps.serve1 = await pickRadio(page, '#pl-serve', '1');
    steps.bp = await pickRadio(page, '#pl-sit', 'bp');
    const zone = page.locator('#pl-court .zone[data-side="deuce"][data-dir="wide"]');
    await zone.scrollIntoViewIfNeeded();
    await zone.click();
    await page.waitForTimeout(900);
    const detail = await text(page, '#pl-detail');
    check('interact: controls found and set (Men, 1st serve, Break points)', steps.men && steps.serve1 && steps.bp, steps);
    check('interact: Nadal deuce wide on break points shows 45.0%', /45\.0%/.test(detail), detail.slice(0, 300));
    check('interact: Nadal deuce wide on other points shows 30.0%', /30\.0%/.test(detail), detail.slice(0, 300));
    check('interact: break-point mode labels the league reference "League, all points"', /League, all points/.test(detail));
    const want = DATA.headline.nadal_deuce_wide_s1;
    check('interact: Nadal figures match serveiq.json headline', detail.includes((100 * want.bp).toFixed(1) + '%') && detail.includes((100 * want.other).toFixed(1) + '%'));
    await page.screenshot({ path: path.join(outDir, 'interact-nadal-bp.png'), fullPage: false });
    const hash = await page.evaluate(() => location.hash);
    check('interact: URL hash records the selection', /p=m\|Rafael%20Nadal\|1\|bp/.test(hash), hash);

    // compare mode
    steps.cmp = await pickRadio(page, '#pl-cmp', 'player');
    const boxVisible = await page.locator('#pl-cmp-box').isVisible();
    steps.cmpCombo = await comboPick(page, '#pl-cq', 'Jannik Sinner');
    await page.waitForTimeout(500);
    const vs = await page.evaluate(() => { const v = document.getElementById('pl-vs'); return v && !v.hidden ? v.innerText : ''; });
    const detail2 = await text(page, '#pl-detail');
    check('interact: compare mode picks a second player (Jannik Sinner)', steps.cmp && boxVisible && steps.cmpCombo.ok && /Sinner/.test(vs) && /Jannik Sinner/.test(detail2),
      { box: boxVisible, combo: steps.cmpCombo, vs: vs.slice(0, 120) });
    steps.league = await pickRadio(page, '#pl-cmp', 'league');
    const vsGone = await page.evaluate(() => document.getElementById('pl-vs').hidden);
    check('interact: back to League hides the comparison', steps.league && vsGone);
    check('interact: no errors during player steps', errorsOf(log).length === 0, errorsOf(log));

    // hash restores state in a new page
    {
      const p2 = await ctx.newPage();
      const l2 = attachCollectors(p2);
      await p2.goto(base + '#p=m|Rafael%20Nadal|1|bp|&at=players', { waitUntil: 'domcontentloaded' });
      await waitForBoot(p2, 'index');
      const n2 = await text(p2, '#pl-name');
      const bpOn = await p2.locator('#pl-sit button[data-value="bp"]').getAttribute('aria-checked');
      check('interact: a shared #p= link restores the player and break-point mode', /Rafael Nadal/.test(n2) && bpOn === 'true', { name: n2, bp: bpOn });
      check('interact: no errors on the shared-link page', errorsOf(l2).length === 0, errorsOf(l2));
      await p2.close();
    }

    // keyboard: court zones
    const zones = await page.evaluate(() => [...document.querySelectorAll('#pl-court .zone')].map((z) => ({ tab: z.getAttribute('tabindex'), label: z.getAttribute('aria-label') || '' })));
    check('keyboard: 6 focusable court zones with labels', zones.length === 6 && zones.every((z) => z.tab === '0' && z.label.length > 10), zones.length);
    await page.locator('#pl-court .zone').first().focus();
    await page.keyboard.press('Tab');
    await page.waitForTimeout(400);
    const focused = await page.evaluate(() => { const e = document.activeElement; return e && e.classList.contains('zone') ? e.getAttribute('data-side') + ' ' + e.getAttribute('data-dir') : null; });
    const detail3 = await text(page, '#pl-detail');
    check('keyboard: Tab moves to the next zone and the detail follows', !!focused && detail3.toLowerCase().includes(focused.split(' ')[1].toLowerCase()), { focused });

    // women
    steps.women = await pickRadio(page, '#pl-tour', 'w');
    await page.waitForTimeout(500);
    const wName = (await text(page, '#pl-name')).trim();
    const women = new Set(DATA.players.rows.filter((r) => r[0] === 'w').map((r) => r[1]));
    check('interact: Women switch selects a woman', steps.women && women.has(wName), wName);
    steps.gauff = await comboPick(page, '#pl-q', 'Coco Gauff');
    const gName = (await text(page, '#pl-name')).trim();
    const wZones = await page.locator('#pl-court .zone').count();
    const badges = await page.locator('#pl-badges .badge').count();
    check('interact: a woman picked by search (Coco Gauff) draws the court and badges', gName === 'Coco Gauff' && wZones === 6 && badges > 0, { name: gName, zones: wZones, badges });
    const winMarks = await page.locator('#pl-win svg circle, #pl-win svg path, #pl-win svg rect').count();
    const bpMarks = await page.locator('#pl-bp svg circle, #pl-bp svg path, #pl-bp svg rect, #pl-bp svg line').count();
    check('interact: win-by-direction and break-point mini charts drawn', winMarks > 0 && bpMarks > 0, { winMarks, bpMarks });
    await page.screenshot({ path: path.join(outDir, 'interact-women.png'), fullPage: false });

    // league controls
    const cards = ['a', 'b', 'c', 'd'];
    const snap = () => page.evaluate((ids) => ids.map((id) => {
      const c = document.getElementById('lg-' + id);
      return { title: c.querySelector('[data-lg-slot="title"]').textContent.trim(), stats: c.querySelector('[data-lg-slot="stats"]').textContent.trim(),
        caption: c.querySelector('[data-lg-slot="caption"]').textContent.trim(),
        chart: c.querySelector('[data-lg-slot="chart"]').innerHTML.length + ':' + c.querySelector('[data-lg-slot="chart"]').textContent + c.querySelector('[data-lg-slot="table"]').textContent, marks: c.querySelectorAll('svg circle, svg rect, svg path').length };
    }), cards);
    const before = await snap();
    check('league: 4 cards with title, caption and chart marks', before.every((c) => c.title && c.caption && c.marks > 0), before.map((c) => c.title));
    check('league: titles at most 9 words, captions at most 20 words', before.every((c) => c.title.split(/\s+/).length <= 9 && c.caption.split(/\s+/).length <= 20),
      before.map((c) => [c.title.split(/\s+/).length, c.caption.split(/\s+/).length]));
    steps.lgWomen = await pickRadio(page, '[data-lg-tour]', 'w');
    const afterW = await snap();
    steps.lg2 = await pickRadio(page, '[data-lg-serve]', '2');
    const after2 = await snap();
    steps.lgRvL = await pickRadio(page, '[data-lg-matchup]', 'RvL');
    const afterM = await snap();
    const changed = (a, b) => a.some((c, i) => c.stats !== b[i].stats || c.title !== b[i].title || c.caption !== b[i].caption || c.chart !== b[i].chart);
    check('league: Tour, Serve and Hands controls each change the cards', steps.lgWomen && steps.lg2 && steps.lgRvL && changed(before, afterW) && changed(afterW, after2) && changed(after2, afterM),
      { women: changed(before, afterW), serve2: changed(afterW, after2), matchup: changed(after2, afterM) });
    check('league: cards still have chart marks after control changes', afterM.every((c) => c.marks > 0));
    const tb = page.locator('#lg-a [data-lg-table-btn]');
    await tb.scrollIntoViewIfNeeded();
    await tb.click();
    await page.waitForTimeout(300);
    const tbl = await page.evaluate(() => { const t = document.getElementById('lg-a-table'); return { hidden: t.hidden, rows: t.querySelectorAll('tbody tr').length }; });
    check('league: Show table opens a data table', !tbl.hidden && tbl.rows > 0, tbl);
    // popover
    const info = page.locator('#lg-b button.info').first();
    await info.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await info.click();
    await page.waitForTimeout(250);
    const popOpen = await info.getAttribute('aria-expanded');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    const popClosed = await info.getAttribute('aria-expanded');
    check('league: "?" popover opens on click and closes on Escape', popOpen === 'true' && popClosed !== 'true', { popOpen, popClosed });
    await page.screenshot({ path: path.join(outDir, 'interact-league.png'), fullPage: false });
    check('interact: no console errors, page errors or module warnings', errorsOf(log).length === 0, errorsOf(log));
    check('interact: no failed requests', log.failedRequests.length === 0, log.failedRequests);
    runs.push({ tag: 'interaction', steps });
    await ctx.close();
  }

  // ---------- reduced motion ----------
  for (const which of ['index', 'method']) {
    for (const w of [1440, 375]) {
      tagNow = `reduced-${which}-${w}`;
      const { ctx, page, log } = await newPage(browser, { w, h: w < 768 ? 812 : 900, reduce: true });
      await page.goto(base + (which === 'index' ? '' : 'method.html'), { waitUntil: 'domcontentloaded' });
      await waitForBoot(page, which);
      await page.waitForTimeout(200);
      // no scrolling: everything must already be visible and final
      const r = await page.evaluate(() => {
        const rv = [...document.querySelectorAll('[data-reveal]')].filter((e) => e.getClientRects().length);
        const cu = [...document.querySelectorAll('[data-countup]')].filter((e) => e.getClientRects().length);
        return {
          reveal: rv.length, hidden: rv.filter((e) => getComputedStyle(e).opacity !== '1').length,
          countups: cu.length, notFinal: cu.filter((e) => !e.textContent.trim()).map((e) => e.outerHTML.slice(0, 80)),
          texts: cu.map((e) => e.textContent.trim()),
          marksHidden: [...document.querySelectorAll('.grow-y, .grow-x, .pop-in, .fade-in')].filter((e) => getComputedStyle(e).opacity === '0').length,
        };
      });
      const wantTexts = which === 'index' ? [fmtInt(DATA.headline.players), fmtInt(DATA.headline.serves_analysed), Math.round(100 * DATA.headline.mix_differs_share_s1) + '%', String(DATA.headline.costly_players_s1)] : [fmtInt(DATA.headline.serves_analysed)];
      check(`reduced-motion ${which}-${w}: every reveal target visible without scrolling`, r.reveal > 0 && r.hidden === 0, { reveal: r.reveal, hidden: r.hidden });
      check(`reduced-motion ${which}-${w}: count-ups show final values at once`, r.notFinal.length === 0 && wantTexts.every((t) => r.texts.includes(t)), { texts: r.texts, want: wantTexts });
      check(`reduced-motion ${which}-${w}: chart marks not left at opacity 0`, r.marksHidden === 0, { marksHidden: r.marksHidden });
      check(`reduced-motion ${which}-${w}: no errors`, errorsOf(log).length === 0, errorsOf(log));
      await ctx.close();
    }
  }

  // ---------- reveal fallback: js/main.js never runs ----------
  tagNow = 'fallback';
  {
    const { ctx, page } = await newPage(browser, { w: 1440, h: 900, block: '**/js/main.js' });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3600);
    const r = await page.evaluate(() => {
      const rv = [...document.querySelectorAll('[data-reveal]')].filter((e) => e.getClientRects().length);
      return { reveal: rv.length, hidden: rv.filter((e) => getComputedStyle(e).opacity !== '1').length, cls: document.documentElement.className };
    });
    check('fallback: with js/main.js blocked, reveal targets become visible after the head timer', r.reveal > 0 && r.hidden === 0, r);
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
