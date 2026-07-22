/*
 * src/names.js — TennisForest v23 player-name resolver.
 *
 * Fixes the v22 showstopper (DIAGNOSI 0.2: 0% hit-rate): API-Tennis sends
 * "Cognome I." ("Djokovic N.", "Auger-Aliassime F.", "Davidovich Fokina A.")
 * but the model is keyed by "nome cognome". This resolver bridges the two with,
 * in order: unicode normalisation -> exact key -> alias table -> bidirectional
 * "Cognome I." <-> "Nome Cognome" parsing -> normalised-Levenshtein fuzzy match
 * (>= 0.90) as a last resort. Everything is precomputed once per model load.
 *
 * No runtime dependencies. Works in the Cloudflare Worker and under Node.
 */

/** Lowercase, strip accents (NFD), drop punctuation, collapse whitespace. */
export function normalizeUnicode(name) {
  return String(name || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** Legacy key used by model.players / model.h2h (kept identical for compat). */
export function playerKey(name) {
  return normalizeUnicode(name);
}

/** Is `tok` an initial like "n" or "n j" (from "N.J.")? */
function isInitialToken(tok) {
  return tok.length <= 2 && /^[a-z]+$/.test(tok);
}

/**
 * Parse an input into { surname, initial, full } candidates.
 * Handles "Cognome I.", "I. Cognome", "Nome Cognome", double surnames,
 * hyphenated surnames (already flattened by normalizeUnicode).
 */
function parseInput(norm) {
  const toks = norm.split(' ').filter(Boolean);
  const out = { full: norm, forms: [] };
  if (toks.length === 0) return out;

  // "Cognome I." -> last token is a 1-letter initial
  if (toks.length >= 2 && toks[toks.length - 1].length === 1) {
    out.forms.push({ surname: toks.slice(0, -1).join(' '), initial: toks[toks.length - 1] });
  }
  // "I. Cognome" -> first token is a 1-letter initial
  if (toks.length >= 2 && toks[0].length === 1) {
    out.forms.push({ surname: toks.slice(1).join(' '), initial: toks[0] });
  }
  // "Nome Cognome[...]" -> first token is given name, rest surname
  if (toks.length >= 2) {
    out.forms.push({ surname: toks.slice(1).join(' '), initial: toks[0][0], full: true });
    // and the mirror: some feeds send "Cognome Nome"
    out.forms.push({ surname: toks.slice(0, -1).join(' '), initial: toks[toks.length - 1][0], full: true });
  }
  // single token -> surname only, no initial
  if (toks.length === 1) out.forms.push({ surname: toks[0], initial: '' });
  return out;
}

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  if (!m) return n; if (!n) return m;
  let prev = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    let cur = [i];
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    prev = cur;
  }
  return prev[n];
}

function simRatio(a, b) {
  const d = levenshtein(a, b);
  const L = Math.max(a.length, b.length) || 1;
  return 1 - d / L;
}

/**
 * Build a resolver over model.players. Returns { resolve, size }.
 * resolve(name) -> { key, method, score, matched } | { key:null, method:'none' }
 */
export function buildResolver(players, opts = {}) {
  const fuzzyThreshold = opts.fuzzyThreshold ?? 0.90;
  const keys = Object.keys(players || {});

  const exact = new Set(keys);
  const aliasToKey = new Map();       // "surname|initial" -> best key
  const surnameToKeys = new Map();    // "surname" -> [keys] (for initial-less / fuzzy)
  const matchesOf = (k) => Number(players[k]?.matches || 0);

  function addAlias(alias, key) {
    const prev = aliasToKey.get(alias);
    if (prev === undefined || matchesOf(key) > matchesOf(prev)) aliasToKey.set(alias, key);
  }
  function addSurname(sn, key) {
    if (!surnameToKeys.has(sn)) surnameToKeys.set(sn, []);
    surnameToKeys.get(sn).push(key);
  }

  for (const k of keys) {
    const toks = k.split(' ').filter(Boolean);
    if (!toks.length) continue;
    const firstInitial = toks[0][0];
    // Candidate surname groupings so both single and double-barrel API forms hit.
    const surnames = new Set();
    surnames.add(toks.slice(1).join(' '));        // all-after-first
    surnames.add(toks[toks.length - 1]);          // last token
    if (toks.length >= 3) surnames.add(toks.slice(-2).join(' ')); // last two
    for (const sn of surnames) {
      if (!sn) continue;
      addAlias(`${sn}|${firstInitial}`, k);
      addSurname(sn, k);
    }
  }

  function resolve(rawName) {
    const norm = normalizeUnicode(rawName);
    if (!norm) return { key: null, method: 'none', score: 0 };
    if (exact.has(norm)) return { key: norm, method: 'exact', score: 1, matched: norm };

    const parsed = parseInput(norm);
    for (const form of parsed.forms) {
      if (form.initial) {
        const hit = aliasToKey.get(`${form.surname}|${form.initial}`);
        if (hit) return { key: hit, method: form.full ? 'fullname' : 'surname-initial', score: 1, matched: hit };
      }
    }
    // surname-only unique hit
    for (const form of parsed.forms) {
      const list = surnameToKeys.get(form.surname);
      if (list && list.length === 1) return { key: list[0], method: 'surname-unique', score: 0.95, matched: list[0] };
    }
    // fuzzy last resort against full keys
    let best = null, bestScore = 0;
    for (const k of keys) {
      const s = simRatio(norm, k);
      if (s > bestScore) { bestScore = s; best = k; }
    }
    if (best && bestScore >= fuzzyThreshold) return { key: best, method: 'fuzzy', score: Number(bestScore.toFixed(4)), matched: best };
    return { key: null, method: 'none', score: Number(bestScore.toFixed(4)) };
  }

  return { resolve, size: keys.length };
}
