/*
 * Measures the v23 resolver hit-rate against the REAL model.json (1730 players).
 * Compare with DIAGNOSI 0.2 (v22 = 0%).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildResolver, normalizeUnicode } from '../src/names.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const model = JSON.parse(fs.readFileSync(path.join(HERE, '..', 'baseline-v22', 'UPLOAD_THIS_TO_CLOUDFLARE', 'model.json'), 'utf8'));
const players = model.players;
const keys = Object.keys(players);
const { resolve } = buildResolver(players);

const cap = w => w.charAt(0).toUpperCase() + w.slice(1);

// ---- Test A: full population, API-Tennis "Cognome I." format ----------------
// Surname = all tokens after the first (matches how API renders multi-token names).
let hitA = 0, correctA = 0;
for (const k of keys) {
  const t = k.split(' ');
  if (t.length < 2) continue;
  const surname = t.slice(1).map(cap).join('-');   // hyphenate double surnames like the API
  const api = `${surname} ${t[0][0].toUpperCase()}.`;
  const r = resolve(api);
  if (r.key) { hitA++; if (r.key === k) correctA++; }
}
const nA = keys.filter(k => k.split(' ').length >= 2).length;

// ---- Test B: last-token-only surname (single-barrel rendering) --------------
let hitB = 0, correctB = 0;
for (const k of keys) {
  const t = k.split(' ');
  if (t.length < 2) continue;
  const api = `${cap(t[t.length - 1])} ${t[0][0].toUpperCase()}.`;
  const r = resolve(api);
  if (r.key) { hitB++; if (r.key === k) correctB++; }
}

// ---- Test C: accented / noisy inputs resolve too ----------------------------
const noisy = [
  ['Djokovic N.', 'novak djokovic'],
  ['Nadal R.', 'rafael nadal'],
  ['Federer R.', 'roger federer'],
  ['Zverev A.', 'alexander zverev'],
  ['Tsitsipas S.', 'stefanos tsitsipas'],
  ['Auger-Aliassime F.', 'felix auger aliassime'],
  ['Davidovich Fokina A.', 'alejandro davidovich fokina'],
  ['Carreño Busta P.', 'pablo carreno busta'],
  ['Ramos-Viñolas A.', 'albert ramos vinolas'],
  ['de Minaur A.', 'alex de minaur'],
];
const present = noisy.filter(([, k]) => keys.includes(k));
let hitC = 0; const cDetail = [];
for (const [api, want] of present) {
  const r = resolve(api);
  const ok = r.key === want;
  if (ok) hitC++;
  cDetail.push({ api, want, got: r.key, method: r.method, ok });
}

const pct = (a, b) => +(100 * a / b).toFixed(2);
console.log(JSON.stringify({
  players_in_model: keys.length,
  A_double_barrel_format: { candidates: nA, resolved_pct: pct(hitA, nA), correct_pct: pct(correctA, nA) },
  B_single_surname_format: { candidates: nA, resolved_pct: pct(hitB, nA), correct_pct: pct(correctB, nA) },
  C_realworld_named: { tested: present.length, correct: hitC, correct_pct: present.length ? pct(hitC, present.length) : null, detail: cDetail },
}, null, 2));
