/* Serve IQ data layer. ES module, no build step.
   Loads data/serveiq.json once (and data/images.json, optional), reads table columns by field name,
   formats numbers, binds [data-h] / [data-b] elements, and builds the per-player model.
   Every statistic on the site comes from serveiq.json through this module. Ported from the v2 app.js. */

// ------------------------------------------------------------ constants (method constants, not statistics)

export const DIRS = ["wide", "body", "T"];
export const SIDES = ["deuce", "ad"];
// confidence level, even three-way split, "no clear favorite" margin
export const CI_LEVEL = 0.95;
export const EVEN_SPLIT = 1 / 3;
export const NO_CLEAR_FAVORITE = 0.03;
// court geometry in meters (schematic court)
export const COURT = { BOX_W: 4.115, BOX_D: 6.40, SINGLES_W: 8.23, HALF_LEN: 11.885, BACK_STRIP: 0.6 };
export const SHARE_DOMAIN_MAX = 0.7;

// ------------------------------------------------------------ columns by name

export function fieldIndex(table) {
  const o = {};
  table.fields.forEach((f, i) => { o[f] = i; });
  return o;
}
export function col(table, name) {
  const i = table.fields.indexOf(name);
  if (i < 0) throw new Error("Column " + name + " is missing from the data file");
  return i;
}
export function objects(table) {
  const idx = fieldIndex(table);
  return table.rows.map((r) => {
    const o = {};
    Object.keys(idx).forEach((k) => { o[k] = r[idx[k]]; });
    return o;
  });
}
export function rowObj(arr, idx) {
  const o = {};
  Object.keys(idx).forEach((k) => { o[k] = arr == null ? null : arr[idx[k]]; });
  return o;
}

// ------------------------------------------------------------ formatting (same output as v2)

const NF = new Intl.NumberFormat("en-US");
export const isNum = (x) => typeof x === "number" && isFinite(x);
export function cleanZero(s) { return /^-0(\.0+)?$/.test(s) ? s.slice(1) : s; }
export function fInt(x) { return isNum(x) ? NF.format(Math.round(x)) : "n/a"; }
export function fPct(x, d) { return isNum(x) ? cleanZero((100 * x).toFixed(d == null ? 1 : d)) + "%" : "n/a"; }
export function fNum(x, d) { return isNum(x) ? cleanZero(x.toFixed(d)) : "n/a"; }
export function fSigned(x, d) {
  if (!isNum(x)) return "n/a";
  const s = fNum(x, d);
  return x > 0 && parseFloat(s) !== 0 ? "+" + s : s;
}
export function fP(p) {
  if (!isNum(p)) return "n/a";
  if (p < 0.0001) return "< 0.0001";
  return p.toPrecision(2);
}

/* fmt(value, code): the data-f codes used by [data-h] bindings.
   int | pct0 | pct1 | r2 | abs1 | s1 | s2 | signed1 | signed2 ; anything else: String(value). */
export function fmt(v, f) {
  switch (f) {
    case "int": return fInt(v);
    case "pct0": return fPct(v, 0);
    case "pct1": return fPct(v, 1);
    case "r2": return fNum(v, 2);
    case "abs1": return isNum(v) ? fNum(Math.abs(v), 1) : "n/a";
    case "s1": return fNum(v, 1);
    case "s2": return fNum(v, 2);
    case "signed1": return fSigned(v, 1);
    case "signed2": return fSigned(v, 2);
    default: return v == null ? "n/a" : String(v);
  }
}
export function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

// ------------------------------------------------------------ names

export function norm(s) { return String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim(); }
/* nameKey(s): the search key. Accents and case are dropped, an apostrophe is removed and any other
   non-letter becomes a space, so "O'Connell" matches "Oconnell" and "Jo-Wilfried" matches "Jo Wilfried". */
export function nameKey(s) {
  return norm(s).replace(/['\u2019]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

/* The data files spell some names without their capitals, hyphens or apostrophes. displayName() fixes
   them for display only; every lookup (data keys, photos, data-name) keeps the data file's spelling. */
const DISPLAY = {
  "John Mcenroe": "John McEnroe",
  "Mackenzie Mcdonald": "Mackenzie McDonald",
  "Caty Mcnally": "Caty McNally",
  "Mccartney Kessler": "McCartney Kessler",
  "Christopher Oconnell": "Christopher O'Connell",
  "Jo Wilfried Tsonga": "Jo-Wilfried Tsonga",
  "Pierre Hugues Herbert": "Pierre-Hugues Herbert",
  "Jan Lennard Struff": "Jan-Lennard Struff",
  "Anna Lena Friedsam": "Anna-Lena Friedsam",
  "Felix Auger Aliassime": "Felix Auger-Aliassime",
  "Alison Riske Amritraj": "Alison Riske-Amritraj",
  "Botic Van De Zandschulp": "Botic van de Zandschulp",
  "Tim Van Rijthoven": "Tim van Rijthoven",
  "Alex De Minaur": "Alex de Minaur",
  "Jesper De Jong": "Jesper de Jong",
  "Juan Martin Del Potro": "Juan Martin del Potro",
};
export function displayName(name) { return DISPLAY[name] || String(name); }
/* Players whose given name is two words (keyed by display name). Every other name is read as one given
   name plus a surname that may have several words ("Roberto Bautista Agut", "Alejandro Davidovich Fokina"). */
const TWO_WORD_GIVEN = new Set([
  "Anna Karolina Schmiedlova", "Daniel Elahi Galan", "Elena Gabriela Ruse", "Irina Camelia Begu",
  "Juan Carlos Ferrero", "Juan Martin del Potro", "Karim Mohamed Maamoun", "Su Wei Hsieh",
  "Tomas Martin Etcheverry", "Xin Yu Wang",
]);
/* surname("Rafael Nadal") -> "Nadal", surname("Botic Van De Zandschulp") -> "Van de Zandschulp".
   Takes a data-file name or a display name, returns the display surname with a capital first letter,
   because in the sentences it often starts a sentence. One-word names are unchanged. */
export function surname(name) {
  const s = displayName(String(name).trim());
  const parts = s.split(/\s+/).filter(Boolean);
  if (parts.length < 2) return s;
  const g = TWO_WORD_GIVEN.has(s) && parts.length > 2 ? 2 : 1;
  const sur = parts.slice(g).join(" ");
  return sur.charAt(0).toUpperCase() + sur.slice(1);
}

// ------------------------------------------------------------ loading

let PROMISE = null;

async function fetchJson(url, optional) {
  const r = await fetch(url, { credentials: "same-origin" });
  if (!r.ok) {
    if (optional) return null;
    throw new Error("the server answered " + r.status);
  }
  return r.json();
}

/* load(): resolves to the data object D (cached; every call returns the same promise). Rejects if serveiq.json fails. */
export function load(base) {
  if (!PROMISE) {
    const b = base || "data/";
    PROMISE = Promise.all([
      fetchJson(b + "serveiq.json", false),
      fetchJson(b + "images.json", true).catch(() => null),
    ]).then(([raw, images]) => build(raw, images));
  }
  return PROMISE;
}

function build(raw, imagesRaw) {
  const H = raw.headline;
  const meta = raw.meta;
  const players = objects(raw.players);       // order of the data file: most charted first within each tour
  const MIXF = fieldIndex(raw.mix);
  const TESTF = fieldIndex(raw.tests);
  col(raw.mix, "share");
  col(raw.tests, "top_share");
  const images = { players: (imagesRaw && imagesRaw.players) || {} };

  const D = {
    raw, H, meta, players, images,
    MIXF, TESTF,
    thresholds: meta.thresholds,
  };

  D.playerList = (g) => players.filter((p) => p.gender === g);
  D.findPlayer = (g, name) => {
    for (let i = 0; i < players.length; i++) if (players[i].gender === g && players[i].server === name) return players[i];
    return null;
  };
  D.findPlayerAny = (name) => {
    for (let i = 0; i < players.length; i++) if (players[i].server === name) return players[i];
    return null;
  };
  /* exact(text): the player whose name (data-file or display spelling) matches text, or null */
  D.exact = (text) => {
    const f = nameKey(text || "");
    if (!f) return null;
    for (let i = 0; i < players.length; i++) {
      const p = players[i];
      if (nameKey(p.server) === f || nameKey(displayName(p.server)) === f) return p;
    }
    return null;
  };
  /* search(g, text): players whose name contains text, best first: the surname starts with it, then any
     word of the name starts with it, then anywhere in the name; ties go to the player with more charted
     serves, so "ser" finds Serena Williams before Sergi Bruguera. */
  D.search = (g, text) => {
    const f = nameKey(text || "");
    const list = g ? D.playerList(g) : players;
    if (!f) return list;
    const rank = (p) => {
      const full = nameKey(displayName(p.server)) + " " + nameKey(p.server);
      if (full.indexOf(f) < 0) return -1;
      if (nameKey(surname(p.server)).indexOf(f) === 0) return 0;
      return (" " + full).indexOf(" " + f) >= 0 ? 1 : 2;
    };
    return list.map((p) => [p, rank(p)]).filter((x) => x[1] >= 0)
      .sort((a, b) => a[1] - b[1] || (b[0].serves || 0) - (a[0].serves || 0)).map((x) => x[0]);
  };
  D.photo = (name) => images.players[name] || null;
  // a cell with fewer serves than the per-direction minimum gets a rough interval
  D.fewServes = (c) => !!c && isNum(c.n) && c.n < meta.thresholds.min_dir_n;

  /* model(g, name, serve): { deuce: {all:{wide,body,T}, bp:{...}, other:{...}, test:{...}}, ad: {...} } or null.
     Cells have the mix fields (n, share, share_lo, share_hi, league_share, win, win_lo, win_hi, league_win). */
  D.model = (g, name, serve) => {
    const key = g + "|" + name + "|" + serve;
    const mx = raw.mix.data[key];
    const tr = raw.tests.data[key];
    if (!mx || !tr) return null;
    const out = {};
    SIDES.forEach((side) => {
      const s = { all: {}, bp: {}, other: {}, test: rowObj(tr[side], TESTF) };
      ["all", "bp", "other"].forEach((sit) => {
        DIRS.forEach((d) => { s[sit][d] = rowObj(mx[side] && mx[side][sit] ? mx[side][sit][d] : null, MIXF); });
      });
      out[side] = s;
    });
    return out;
  };
  /* sideInsights(M, side): the rules behind the tool's sentences, as data (js/tool.js).
     favorite / noClearFavorite always use all points. */
  D.sideInsights = (M, sideName) => {
    const side = M[sideName];
    const t = side.test;
    const qth = meta.thresholds.q;
    const minN = meta.thresholds.min_dir_n;
    const ranked = DIRS.map((d) => [d, side.all[d].share]).filter((x) => isNum(x[1])).sort((a, b) => b[1] - a[1]);
    let favorite = null, noClearFavorite = null;
    if (ranked.length >= 2 && ranked[0][1] - ranked[1][1] < NO_CLEAR_FAVORITE) {
      noClearFavorite = ranked.map((x) => ({ dir: x[0], share: x[1] }));
    } else if (t.top_dir) {
      const c = side.all[t.top_dir];
      favorite = { dir: t.top_dir, share: t.top_share, lo: c.share_lo, hi: c.share_hi, league: c.league_share };
    }
    const mix = { tested: isNum(t.q_mix), differs: isNum(t.q_mix) && t.q_mix < qth, q: t.q_mix, p: t.p_mix };
    const shifts = DIRS.filter((d) => isNum(t["bp_" + d + "_q"]) && t["bp_" + d + "_q"] < qth).map((d) => ({
      dir: d, other: side.other[d].share, bp: side.bp[d].share, change: t["bp_" + d], q: t["bp_" + d + "_q"],
    }));
    const win = {
      tested: isNum(t.q_win), differs: isNum(t.q_win) && t.q_win < qth, p: t.p_win, q: t.q_win,
      rates: DIRS.filter((d) => (side.all[d].n || 0) >= minN).map((d) => ({ dir: d, win: side.all[d].win })),
      best_dir: t.best_dir, gain_pp: t.gain_pp,
    };
    return {
      side: sideName, n: t.n, matches: t.matches, favPct: t.fav_pct,
      favorite, noClearFavorite, mix,
      bp: { shifts, n_bp: t.n_bp, thin: (t.n_bp || 0) < meta.thresholds.bp_warn_n },
      win,
    };
  };

  /* headline value by dotted key, e.g. "players", "body_win_m1.coef" */
  D.h = (key) => {
    let v = H;
    String(key).split(".").forEach((k) => { v = v == null ? undefined : v[k]; });
    return v;
  };
  // derived bindings for [data-b] (same keys as v2)
  const BB = {
    ci: () => fPct(CI_LEVEL, 0),
    even: () => fPct(EVEN_SPLIT, 0),
    fdr: () => fPct(meta.thresholds.q, 0),
    q: () => String(meta.thresholds.q),
    min_serves_side: () => fInt(meta.thresholds.min_serves_side),
    min_matches: () => fInt(meta.thresholds.min_matches),
    min_dir_n: () => fInt(meta.thresholds.min_dir_n),
    bp_warn_n: () => fInt(meta.thresholds.bp_warn_n),
    tests_mix: () => fInt(meta.tests.mix),
    tests_win: () => fInt(meta.tests.win),
    tests_bp: () => fInt(meta.tests.bp),
    per100: () => "100",
    serves_total: () => fInt(meta.serves_total),
    tiebreak_share: () => fPct(meta.tiebreak_share, 0),
    nodir_share: () => fPct(meta.nodir_share, 1),
  };
  D.b = (key) => (BB[key] ? BB[key]() : "n/a");

  /* bind(root): fills every [data-h] (headline key, format in data-f) and [data-b] (derived key) under root.
     Each [data-h] element also gets data-value (the raw number). */
  D.bind = (root) => {
    const r = root || document;
    r.querySelectorAll("[data-h]").forEach((el) => {
      const v = D.h(el.getAttribute("data-h"));
      el.textContent = v === undefined ? "n/a" : fmt(v, el.getAttribute("data-f"));
      if (isNum(v)) el.setAttribute("data-value", String(v));
    });
    r.querySelectorAll("[data-b]").forEach((el) => { el.textContent = D.b(el.getAttribute("data-b")); });
  };
  return D;
}
