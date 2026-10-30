#!/usr/bin/env node
// Serve IQ static-site smoke test (headless browser).
//
// Usage:  node scripts/smoke_site.mjs <siteDir> <outDir> [port]
//
// Playwright is NOT installed in this repo. The script loads it from, in order:
//   1. normal module resolution,
//   2. $PLAYWRIGHT_DIR (a folder with node_modules/playwright),
//   3. /private/tmp/serveiq-harness  (npm i playwright@1.49.1 there).
// Browser: Playwright's bundled Chromium if installed, else system Google Chrome
// (channel "chrome"), else $CHROME_PATH.
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
if (!fs.existsSync(path.join(siteDir, 'index.html'))) {
  console.error(`no index.html in ${siteDir}`);
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });

// ---------- load playwright ----------
async function loadPlaywright() {
  try { return await import('playwright'); } catch { /* fall through */ }
  const dirs = [process.env.PLAYWRIGHT_DIR, '/private/tmp/serveiq-harness'].filter(Boolean);
  for (const d of dirs) {
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
      let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
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
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)));
}

// ---------- helpers ----------
const checks = [];   // {name, pass, detail}
const warnings = [];
let tagNow = '';
function check(name, pass, detail) { checks.push({ name, pass: !!pass, ...(detail !== undefined ? { detail } : {}) }); }

function attachCollectors(page, base) {
  const log = { consoleErrors: [], pageErrors: [], failedRequests: [] };
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const where = (m.location() && m.location().url) || '';
    const line = `${m.text().slice(0, 300)}${where ? ' @ ' + where : ''}`;
    // A missing /favicon.ico is requested by the browser itself; report it but do not fail on it.
    if (/\/favicon\.ico$/.test(where)) { warnings.push(`${tagNow}: ${line}`); return; }
    log.consoleErrors.push(line);
  });
  page.on('pageerror', (e) => log.pageErrors.push(String(e.message || e).slice(0, 300)));
  page.on('requestfailed', (r) => log.failedRequests.push(`${r.failure()?.errorText || 'failed'} ${r.url()}`));
  page.on('response', (r) => { if (r.status() >= 400) log.failedRequests.push(`HTTP ${r.status()} ${r.url()}`); });
  return log;
}

async function waitForData(page) {
  await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
  // Wait until the page shows a real player name or a sizeable amount of text (data rendered).
  await page.waitForFunction(() => {
    const t = document.body ? document.body.innerText : '';
    return /Nadal|Federer|Djokovic|Swiatek|Williams/.test(t) || t.length > 1500;
  }, null, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(600);
}

// Visible text including SVG <text>, minus KaTeX, script and style.
// Visible text plus SVG <text> (used for value checks).
async function pageText(page) {
  return page.evaluate(() => (document.body.innerText || '') + '\n' +
    [...document.querySelectorAll('svg text, svg title')].map((t) => t.textContent).join(' '));
}
// All text in the DOM, including hidden panels and aria-labels, minus KaTeX / script / style (used for the banned-character scan).
async function scanText(page) {
  return page.evaluate(() => {
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,.katex,.katex-mathml,annotation').forEach((n) => n.remove());
    const attrs = [...document.querySelectorAll('[aria-label],[title],[alt],[placeholder]')].filter((e) => !e.closest('.katex'))
      .map((e) => [e.getAttribute('aria-label'), e.getAttribute('title'), e.getAttribute('alt'), e.getAttribute('placeholder')].filter(Boolean).join(' '));
    return (document.title || '') + '\n' + (clone.textContent || '') + '\n' + attrs.join('\n');
  });
}

const BANNED = [
  [/—/g, 'em dash'], [/–/g, 'en dash'], [/−/g, 'minus sign'], [/×/g, 'times sign'],
  [/[←-⇿]/g, 'arrow'], [/·/g, 'middle dot'], [/~\s?\d/g, 'tilde as about'],
  [/[☀-➿]/g, 'pictograph'], [/[\u{1F000}-\u{1FAFF}]/gu, 'emoji'], [/️/g, 'variation selector'],
  [/guess rate/gi, 'old term "guess rate"'],
];
function bannedHits(text) {
  const hits = [];
  for (const [re, label] of BANNED) {
    const m = text.match(re);
    if (m) {
      const i = text.search(re);
      hits.push({ label, count: m.length, context: text.slice(Math.max(0, i - 40), i + 40).replace(/\s+/g, ' ') });
    }
  }
  return hits;
}

// ---- robust control finder ----
// Choose an option in a control whose label matches labelRe, option text matching optRe.
// Tries <select>, radios / tabs / toggle buttons, then a generic clickable with that text.
async function choose(page, labelRe, optRe) {
  // 1. <select> elements
  const selects = page.locator('select');
  const nSel = await selects.count();
  const selInfo = [];
  for (let i = 0; i < nSel; i++) {
    const s = selects.nth(i);
    const info = await s.evaluate((el) => {
      const lab = (el.labels && el.labels[0] ? el.labels[0].innerText : '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' +
        (el.id || '') + ' ' + (el.name || '') + ' ' +
        (el.getAttribute('aria-labelledby') ? (document.getElementById(el.getAttribute('aria-labelledby'))?.innerText || '') : '');
      return { label: lab, options: [...el.options].map((o) => ({ text: o.text.trim(), value: o.value })), visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) };
    });
    selInfo.push({ i, ...info });
  }
  const tryOrder = [...selInfo.filter((s) => labelRe.test(s.label)), ...selInfo.filter((s) => !labelRe.test(s.label))];
  for (const s of tryOrder) {
    const opt = s.options.find((o) => optRe.test(o.text));
    if (opt) {
      await selects.nth(s.i).selectOption(opt.value);
      await page.waitForTimeout(300);
      return `select[${s.i}] "${s.label.trim()}" -> "${opt.text}"`;
    }
  }
  // 2. radios / tabs / pressed buttons by accessible name
  for (const role of ['radio', 'tab', 'button', 'option', 'menuitemradio', 'switch', 'checkbox']) {
    const loc = page.getByRole(role, { name: optRe });
    const n = await loc.count();
    for (let i = 0; i < n; i++) {
      const el = loc.nth(i);
      if (!(await el.isVisible().catch(() => false))) {
        // hidden radio input styled by a label: click its label instead
        if (role === 'radio') {
          const ok = await el.evaluate((r) => { const l = r.labels && r.labels[0]; if (l) { l.click(); return true; } r.click(); return true; }).catch(() => false);
          if (ok) { await page.waitForTimeout(300); return `${role}(hidden, via label) "${optRe}"`; }
        }
        continue;
      }
      if (role === 'checkbox' || role === 'switch') {
        if (!(await el.isChecked().catch(() => false))) await el.click();
      } else {
        await el.click();
      }
      await page.waitForTimeout(300);
      return `${role} "${(await el.innerText().catch(() => '')).trim() || optRe}"`;
    }
  }
  // 3. a label element (e.g. for visually hidden inputs)
  const lab = page.locator('label').filter({ hasText: optRe });
  if (await lab.count()) { await lab.first().click(); await page.waitForTimeout(300); return `label "${optRe}"`; }
  return null;
}

// Choose a player by name (or the first one if name is null).
async function choosePlayer(page, name) {
  const playerLabel = /player|server/i;
  // 1. a <select> with player names
  const selects = page.locator('select');
  const nSel = await selects.count();
  for (let i = 0; i < nSel; i++) {
    const info = await selects.nth(i).evaluate((el) => ({
      label: ((el.labels && el.labels[0] ? el.labels[0].innerText : '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + el.id + ' ' + el.name),
      options: [...el.options].map((o) => ({ text: o.text.trim(), value: o.value, disabled: o.disabled })),
    }));
    const looksPlayer = playerLabel.test(info.label) || info.options.some((o) => /Nadal|Federer|Djokovic|Swiatek|Serena/.test(o.text));
    if (!looksPlayer) continue;
    const opt = name ? info.options.find((o) => o.text.includes(name))
                     : info.options.find((o) => o.value && !o.disabled && !/choose|select|pick|search/i.test(o.text));
    if (opt) { await selects.nth(i).selectOption(opt.value); await page.waitForTimeout(400); return `select[${i}] -> "${opt.text}"`; }
  }
  // 2. text / search / combobox input
  const inputs = page.locator('input[type="search"], input[type="text"], input:not([type]), [role="combobox"]');
  const nIn = await inputs.count();
  for (let i = 0; i < nIn; i++) {
    const inp = inputs.nth(i);
    if (!(await inp.isVisible().catch(() => false))) continue;
    const meta = await inp.evaluate((el) => ((el.labels && el.labels[0] ? el.labels[0].innerText : '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.placeholder || '') + ' ' + el.id + ' ' + (el.getAttribute('list') ? 'datalist' : '')));
    if (!playerLabel.test(meta) && !/search|name/i.test(meta)) continue;
    let target = name;
    if (!target) {
      // first suggestion from datalist or listbox
      target = await inp.evaluate((el) => { const dl = el.list; return dl && dl.options.length ? dl.options[0].value : null; });
    }
    await inp.click();
    await inp.fill('');
    if (target) await inp.fill(target);
    await page.waitForTimeout(300);
    const optLoc = target ? page.getByRole('option', { name: new RegExp(target.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i') })
                          : page.getByRole('option');
    if (await optLoc.count()) { await optLoc.first().click().catch(() => {}); }
    else { await inp.press('Enter'); }
    await inp.dispatchEvent('change').catch(() => {});
    await page.waitForTimeout(400);
    return `input[${i}] "${meta.trim()}" -> "${target || '(first option)'}"`;
  }
  // 3. a clickable list item / button with the name
  if (name) {
    const loc = page.getByRole('button', { name: new RegExp(name, 'i') }).or(page.getByRole('option', { name: new RegExp(name, 'i') })).or(page.getByRole('link', { name: new RegExp(name, 'i') }));
    if (await loc.count()) { await loc.first().click(); await page.waitForTimeout(400); return `clickable "${name}"`; }
  }
  return null;
}

// ---------- main ----------
const server = await startServer();
const base = `http://127.0.0.1:${server.address().port}/`;
const { browser, browserName } = await launchBrowser();
const runs = [];
const bgByWidth = {};

try {
  for (const [w, h] of [[1280, 900], [375, 812]]) {
    for (const scheme of ['light', 'dark']) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, isMobile: w < 768, hasTouch: w < 768 });
      const page = await ctx.newPage();
      await page.emulateMedia({ colorScheme: scheme });
      const log = attachCollectors(page, base);
      const tag = `${w}-${scheme}`;
      tagNow = tag;
      let gotoErr = null;
      await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch((e) => { gotoErr = e.message; });
      await waitForData(page);
      const m = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        katex: document.querySelectorAll('.katex').length,
        katexErrors: document.querySelectorAll('.katex-error').length,
        bg: getComputedStyle(document.body).backgroundColor,
        rootBg: getComputedStyle(document.documentElement).backgroundColor,
        title: document.title,
        wideEls: [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > Math.min(window.innerWidth, screen.width || window.innerWidth) + 1 && getComputedStyle(e).position !== 'fixed')
          .slice(0, 5).map((e) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : ''} right=${Math.round(e.getBoundingClientRect().right)}`),
      }));
      const shot = path.join(outDir, `${tag}.png`);
      await page.screenshot({ path: shot, fullPage: true });
      const banned = bannedHits(await scanText(page));
      bgByWidth[w] = bgByWidth[w] || {};
      bgByWidth[w][scheme] = m.bg === 'rgba(0, 0, 0, 0)' ? m.rootBg : m.bg;

      check(`${tag}: page loaded`, !gotoErr, gotoErr || undefined);
      check(`${tag}: no console/page errors`, log.consoleErrors.length + log.pageErrors.length === 0, [...log.consoleErrors, ...log.pageErrors]);
      check(`${tag}: no failed requests`, log.failedRequests.length === 0, log.failedRequests);
      // Mobile emulation lets innerWidth grow to the layout width, so compare against the device width too.
      check(`${tag}: no horizontal scroll`, m.scrollWidth <= Math.min(m.innerWidth, w), { scrollWidth: m.scrollWidth, innerWidth: m.innerWidth, deviceWidth: w, overflowing: m.wideEls });
      check(`${tag}: KaTeX rendered`, m.katex > 0 && m.katexErrors === 0, { katex: m.katex, katexErrors: m.katexErrors, note: m.katex === 0 ? 'no .katex elements (KaTeX not loaded, or fallback text shown)' : undefined });
      check(`${tag}: no banned characters/terms in rendered text`, banned.length === 0, banned);
      runs.push({ tag, screenshot: shot, title: m.title, bg: bgByWidth[w][scheme], ...m, wideEls: undefined });
      await ctx.close();
    }
    check(`${w}: dark scheme changes body background`, bgByWidth[w].light !== bgByWidth[w].dark, bgByWidth[w]);
  }

  // ---------- interaction ----------
  tagNow = 'interaction';
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'light' });
  const page = await ctx.newPage();
  const log = attachCollectors(page, base);
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await waitForData(page);
  const steps = {};
  steps.tourMen = await choose(page, /tour|gender/i, /^\s*(men|atp|men'?s)\b/i);
  steps.player = await choosePlayer(page, 'Rafael Nadal');
  steps.serve1 = await choose(page, /serve/i, /^\s*(1st|first)\b/i);
  steps.breakPoints = await choose(page, /point|situation/i, /break/i);
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(outDir, 'interact-nadal-bp.png'), fullPage: true });
  const nadalText = await pageText(page);
  check('interact: all controls found (Men, Nadal, 1st, break points)', steps.tourMen && steps.player && steps.serve1 && steps.breakPoints, steps);
  check('interact: Nadal break-point view shows 45.0%', nadalText.includes('45.0%'));
  check('interact: Nadal break-point view shows 30.0%', nadalText.includes('30.0%'));
  const errsBefore = log.consoleErrors.length + log.pageErrors.length;
  check('interact: no console errors during Nadal steps', errsBefore === 0, [...log.consoleErrors, ...log.pageErrors]);

  steps.tourWomen = await choose(page, /tour|gender/i, /^\s*(women|wta|women'?s)\b/i);
  steps.womenFirstPlayer = await choosePlayer(page, null);
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(outDir, 'interact-women-first.png'), fullPage: true });
  const womenText = await pageText(page);
  check('interact: Women tour control found', !!steps.tourWomen, steps.tourWomen);
  check('interact: first women player selected', !!steps.womenFirstPlayer, steps.womenFirstPlayer);
  check('interact: page no longer shows Nadal as selected after switching to Women', !/Rafael Nadal/.test(await page.evaluate(() => {
    const sel = [...document.querySelectorAll('select')].map((s) => s.selectedOptions[0]?.text || '').join(' ');
    const inp = [...document.querySelectorAll('input')].map((i) => i.value).join(' ');
    return sel + ' ' + inp;
  })));
  check('interact: no console errors after switching to Women', log.consoleErrors.length + log.pageErrors.length === 0, [...log.consoleErrors, ...log.pageErrors]);
  check('interact: no failed requests', log.failedRequests.length === 0, log.failedRequests);

  // ---------- keyboard: court zones ----------
  await page.evaluate(() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); });
  await page.locator('body').click({ position: { x: 1, y: 1 } }).catch(() => {});
  const seen = new Map();
  let total = 0;
  for (let i = 0; i < 250; i++) {
    await page.keyboard.press('Tab');
    const d = await page.evaluate(() => {
      const e = document.activeElement;
      if (!e || e === document.body) return null;
      const label = (e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('data-zone') || '') + ' ' + (e.getAttribute('data-dir') || '') + ' ' + (e.textContent || '').trim().slice(0, 40);
      const inSvg = !!e.closest('svg');
      const zoneLike = inSvg || e.hasAttribute('data-zone') || /\bzone\b/i.test(e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className || '');
      const key = e.tagName + '|' + (e.id || '') + '|' + label + '|' + [...e.parentNode.children].indexOf(e);
      return { key, tag: e.tagName.toLowerCase(), label: label.trim(), inSvg, zoneLike };
    });
    if (!d) continue;
    total++;
    if (seen.has(d.key)) { if (total > 5 && [...seen.keys()][0] === d.key) break; continue; }
    seen.set(d.key, d);
  }
  const zones = [...seen.values()].filter((d) => d.zoneLike);
  check('keyboard: at least 6 focusable court zones', zones.length >= 6, { zones: zones.length, focusables: seen.size, sample: zones.slice(0, 8).map((z) => `${z.tag} "${z.label}"`) });
  runs.push({ tag: 'interaction', steps });
  await ctx.close();
} finally {
  await browser.close();
  server.close();
}

const failed = checks.filter((c) => !c.pass);
const summary = { siteDir, outDir, url: base, browser: browserName, passed: checks.length - failed.length, failed: failed.length, ok: failed.length === 0, warnings, checks, runs };
console.log(JSON.stringify(summary, null, 2));
process.exit(failed.length ? 1 : 0);
