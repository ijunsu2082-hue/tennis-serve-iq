/* Serve IQ data layer. ES module, no build step.
   Loads data/serveiq.json once (and data/images.json, optional), reads table columns by field name,
   formats numbers, binds [data-h] / [data-b] elements, and builds the per-player model.
   Every statistic on the site comes from serveiq.json through this module. Ported from the v2 app.js. */

// ------------------------------------------------------------ constants (method constants, not statistics)

export const DIRS = ["wide", "body", "T"];
export const SIDES = ["deuce", "ad"];
export const SIDE_LABEL = { deuce: "Deuce side", ad: "Ad side" };
export const TOUR_LABEL = { m: "Men", w: "Women" };
export const TOUR_PLURAL = { m: "men", w: "women" };
export const MATCHUP = { RvR: "Righty vs righty", RvL: "Righty vs lefty", LvR: "Lefty vs righty", LvL: "Lefty vs lefty" };
export const SERVE_LABEL = { 1: "1st", 2: "2nd" };
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
export function pEq(name, p) {
  const s = fP(p);
  return s.charAt(0) === "<" ? name + " " + s : name + " = " + s;
}
// short aliases named in the spec
export const pct = (x, d) => fPct(x, d);          // share (0..1) to "68%" / "68.4%"
export const int = (x) => fInt(x);                // 12345 to "12,345"
export const num = (x, d) => fNum(x, d);          // fixed decimals, "-0.0" cleaned to "0.0"
export const signed = (x, d) => fSigned(x, d);    // "+1.2" / "-1.2" / "0.0"
export const pp = (x, d) => fSigned(x, d == null ? 1 : d);                 // value already in percentage points
export const ppShare = (x, d) => (isNum(x) ? fSigned(100 * x, d == null ? 1 : d) : "n/a"); // share difference to points
export const q = (p) => fP(p);                    // p or q value: "0.0031" / "< 0.0001"

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
export function ciText(lo, hi, d) { return fPct(lo, d) + " to " + fPct(hi, d); }

// ------------------------------------------------------------ names

export function norm(s) { return String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim(); }
/* slug(name): lowercase, NFKD strip accents, non-alphanumerics to "-", trim "-".  "Gaël Monfils" to "gael-monfils" */
export function slug(name) {
  return String(name).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}
/* Players whose given name is two words. Every other name is read as one given name plus a surname
   that may have several words ("Roberto Bautista Agut", "Alejandro Davidovich Fokina"). */
const TWO_WORD_GIVEN = new Set([
  "Anna Karolina Schmiedlova", "Anna Lena Friedsam", "Daniel Elahi Galan", "Elena Gabriela Ruse",
  "Irina Camelia Begu", "Jan Lennard Struff", "Jo Wilfried Tsonga", "Juan Carlos Ferrero",
  "Juan Martin Del Potro", "Karim Mohamed Maamoun", "Pierre Hugues Herbert", "Su Wei Hsieh",
  "Tomas Martin Etcheverry", "Xin Yu Wang",
]);
/* nameParts("Juan Martin Del Potro") -> { given: ["Juan", "Martin"], surname: "Del Potro" } */
export function nameParts(name) {
  const s = String(name).trim();
  const parts = s.split(/\s+/).filter(Boolean);
  if (parts.length < 2) return { given: parts, surname: "" };
  const g = TWO_WORD_GIVEN.has(s) && parts.length > 2 ? 2 : 1;
  return { given: parts.slice(0, g), surname: parts.slice(g).join(" ") };
}
/* surname("Roberto Bautista Agut") -> "Bautista Agut"; one-word names unchanged */
export function surname(name) {
  const p = nameParts(name);
  return p.surname || String(name);
}
/* "Roger Federer" to "RF", "Roberto Bautista Agut" to "RB", "Juan Martin Del Potro" to "JD" */
export function initials(name) {
  const p = nameParts(name);
  if (!p.given.length) return "?";
  return (p.given[0].charAt(0) + (p.surname ? p.surname.charAt(0) : "")).toUpperCase();
}
/* "Roger Federer" to "R. Federer", "Juan Martin Del Potro" to "J.M. Del Potro"; one-word names unchanged */
export function shortName(name) {
  const p = nameParts(name);
  if (!p.surname) return String(name);
  return p.given.map((w) => w.charAt(0) + ".").join("") + " " + p.surname;
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
  const LDIR = objects(raw.league.directions);
  const WITHIN = objects(raw.league.within);
  const SERIAL = objects(raw.league.serial);
  const STAB = objects(raw.league.stability);
  const images = {
    players: (imagesRaw && imagesRaw.players) || {},
    scenes: (imagesRaw && imagesRaw.scenes) || {},
  };

  const D = {
    raw, H, meta, players, LDIR, WITHIN, SERIAL, STAB, images,
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
  D.search = (g, text) => {
    const f = norm(text || "");
    const list = g ? D.playerList(g) : players;
    return f ? list.filter((p) => norm(p.server).indexOf(f) >= 0) : list;
  };
  D.photo = (name) => images.players[name] || null;
  D.scene = (key) => images.scenes[key] || null;
  D.featured = (g, limit) => {
    const list = (g ? D.playerList(g) : players).filter((p) => !!images.players[p.server]);
    return limit ? list.slice(0, limit) : list;
  };
  D.yearRange = () => [
    Math.min.apply(null, players.map((p) => p.first)),
    Math.max.apply(null, players.map((p) => p.last)),
  ];
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
  // the deuce side's favorite target, the zone a fresh court selects first
  D.favoriteZone = (M) => {
    const t = M.deuce.test;
    return { side: "deuce", dir: t.top_dir && DIRS.indexOf(t.top_dir) >= 0 ? t.top_dir : "wide" };
  };

  /* sideInsights(M, side): the v2 "scouting notes" rules as data, for badges.
     favorite / noClearFavorite always use all points (v2 adds "(all points)" when the view is break points). */
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

  // league tables
  D.leagueRows = (g, matchup, s) => LDIR.filter((r) => r.gender === g && r.matchup === matchup && r.serve_num === s);
  D.leagueCell = (rows, side, bp, d) => {
    for (let i = 0; i < rows.length; i++) if (rows[i].side === side && rows[i].is_break_point === bp && rows[i].direction === d) return rows[i];
    return null;
  };
  D.within = (g, s, question, fe) => {
    for (let i = 0; i < WITHIN.length; i++) {
      const r = WITHIN[i];
      if (r.gender === g && r.serve_num === s && r.question === question && r.fixed_effect === fe) return r;
    }
    return null;
  };
  D.serial = (g, sn) => {
    let r = null;
    SERIAL.forEach((x) => { if (x.gender === g && x.serve_num === sn) r = x; });
    return r;
  };
  D.stability = (g) => STAB.filter((r) => r.gender === g);

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
    year_first: () => String(D.yearRange()[0]),
    year_last: () => String(D.yearRange()[1]),
    per100: () => "100",
    serves_total: () => fInt(meta.serves_total),
    tiebreak_share: () => fPct(meta.tiebreak_share, 0),
    nodir_share: () => fPct(meta.nodir_share, 1),
    box_dims: () => COURT.BOX_W.toFixed(3) + " m wide by " + COURT.BOX_D.toFixed(2) + " m deep",
  };
  D.b = (key) => (BB[key] ? BB[key]() : "n/a");

  /* bind(root): fills every [data-h] (headline key, format in data-f) and [data-b] (derived key) under root.
     Each [data-h] element also gets data-value (the raw number) so anim.js can count up to it. */
  D.bind = (root) => {
    const r = root || document;
    r.querySelectorAll("[data-h]").forEach((el) => {
      const v = D.h(el.getAttribute("data-h"));
      el.textContent = v === undefined ? "n/a" : fmt(v, el.getAttribute("data-f"));
      if (isNum(v)) el.setAttribute("data-value", String(v));
    });
    r.querySelectorAll("[data-b]").forEach((el) => { el.textContent = D.b(el.getAttribute("data-b")); });
  };

  // formatters on the object too, for modules that only hold ctx
  D.fmt = { fmt, fInt, fPct, fNum, fSigned, fP, pEq, pct, int, num, signed, pp, ppShare, q, esc, ciText, slug, initials, shortName, surname, isNum };
  return D;
}
