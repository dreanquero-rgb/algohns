'use strict';
/*
 * Phase 0 diagnostics — measured, not deduced.
 * Extracts the EXACT pure functions from UPLOAD_THIS_TO_CLOUDFLARE/_worker.js
 * so the numbers reflect real production behaviour.
 */
const fs = require('fs');
const path = require('path');

const ROOT = process.argv[2] || path.join(__dirname, '..', 'baseline-v22', 'UPLOAD_THIS_TO_CLOUDFLARE');
const MODEL_PATH = path.join(ROOT, 'model.json');

// ---- Verbatim from _worker.js -------------------------------------------
function playerKey(name) {
  return String(name || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
}
function emptyPlayerStats() {
  return { elo: 1500, surfaceElo: {}, wins: 0, losses: 0, surfaceWins: {}, surfaceLosses: {}, recent: [] };
}
function createModelState() { return { players: {}, matchesUsed: 0 }; }
function getStats(state, name) {
  const k = playerKey(name);
  if (!state.players[k]) state.players[k] = emptyPlayerStats();
  return state.players[k];
}
function statRate(w, l) { return (Number(w || 0) + 3) / (Number(w || 0) + Number(l || 0) + 6); }
function recentRate(arr) {
  if (!arr || !arr.length) return 0.5;
  const last = arr.slice(-5);
  return (last.reduce((a, b) => a + (b ? 1 : 0), 0) + 1) / (last.length + 2);
}
function modelPlayer(model, name) { const key = playerKey(name); return model?.players?.[key] || null; }
function valueFromPlayer(player, p, fallback = 0) {
  if (!player) return fallback;
  const parts = String(p).split('.');
  let cur = player;
  for (const q of parts) { if (cur == null || !(q in cur)) return fallback; cur = cur[q]; }
  const n = Number(cur);
  return Number.isFinite(n) ? n : fallback;
}
function sigmoid(x) { return 1 / (1 + Math.exp(-x)); }
function clamp(n, min, max) { return Math.max(min, Math.min(max, Number(n) || 0)); }

function buildOfflineFeatureVector(fixture, model, fallbackState) {
  const surface = fixture.surface || 'Hard';
  const p1m = modelPlayer(model, fixture.player1);
  const p2m = modelPlayer(model, fixture.player2);
  const fstate = fallbackState || createModelState();
  const p1f = getStats(fstate, fixture.player1);
  const p2f = getStats(fstate, fixture.player2);
  const p1Elo = p1m ? valueFromPlayer(p1m, 'elo', 1500) : p1f.elo;
  const p2Elo = p2m ? valueFromPlayer(p2m, 'elo', 1500) : p2f.elo;
  const p1Surface = p1m?.surface_elo?.[surface] ?? p1f.surfaceElo[surface] ?? 1500;
  const p2Surface = p2m?.surface_elo?.[surface] ?? p2f.surfaceElo[surface] ?? 1500;
  const p1Matches = p1m ? valueFromPlayer(p1m, 'matches', 0) : (p1f.wins + p1f.losses);
  const p2Matches = p2m ? valueFromPlayer(p2m, 'matches', 0) : (p2f.wins + p2f.losses);
  const p1WinRate = p1m ? valueFromPlayer(p1m, 'win_rate', 0.5) : statRate(p1f.wins, p1f.losses);
  const p2WinRate = p2m ? valueFromPlayer(p2m, 'win_rate', 0.5) : statRate(p2f.wins, p2f.losses);
  const p1SurfaceWinRate = p1m?.surface_win_rate?.[surface] ?? statRate(p1f.surfaceWins[surface] || 0, p1f.surfaceLosses[surface] || 0);
  const p2SurfaceWinRate = p2m?.surface_win_rate?.[surface] ?? statRate(p2f.surfaceWins[surface] || 0, p2f.surfaceLosses[surface] || 0);
  const p1Form5 = p1m ? valueFromPlayer(p1m, 'form_5', 0.5) : recentRate(p1f.recent);
  const p2Form5 = p2m ? valueFromPlayer(p2m, 'form_5', 0.5) : recentRate(p2f.recent);
  const p1Form10 = p1m ? valueFromPlayer(p1m, 'form_10', p1Form5) : recentRate(p1f.recent);
  const p2Form10 = p2m ? valueFromPlayer(p2m, 'form_10', p2Form5) : recentRate(p2f.recent);
  const p1Rank = p1m ? valueFromPlayer(p1m, 'rank', 250) : 250;
  const p2Rank = p2m ? valueFromPlayer(p2m, 'rank', 250) : 250;
  const p1RankPts = p1m ? valueFromPlayer(p1m, 'rank_points', 0) : 0;
  const p2RankPts = p2m ? valueFromPlayer(p2m, 'rank_points', 0) : 0;
  const p1Serve = p1m ? valueFromPlayer(p1m, 'serve_strength', 0) : 0;
  const p2Serve = p2m ? valueFromPlayer(p2m, 'serve_strength', 0) : 0;
  const p1Return = p1m ? valueFromPlayer(p1m, 'return_strength', 0) : 0;
  const p2Return = p2m ? valueFromPlayer(p2m, 'return_strength', 0) : 0;
  const p1Hold = p1m ? valueFromPlayer(p1m, 'hold_rate', 0.5) : 0.5;
  const p2Hold = p2m ? valueFromPlayer(p2m, 'hold_rate', 0.5) : 0.5;
  const p1Break = p1m ? valueFromPlayer(p1m, 'break_rate', 0.5) : 0.5;
  const p2Break = p2m ? valueFromPlayer(p2m, 'break_rate', 0.5) : 0.5;
  const p1Ace = p1m ? valueFromPlayer(p1m, 'ace_rate', 0) : 0;
  const p2Ace = p2m ? valueFromPlayer(p2m, 'ace_rate', 0) : 0;
  const p1Df = p1m ? valueFromPlayer(p1m, 'double_fault_rate', 0) : 0;
  const p2Df = p2m ? valueFromPlayer(p2m, 'double_fault_rate', 0) : 0;
  const p1Tb = p1m ? valueFromPlayer(p1m, 'tiebreak_win_rate', 0.5) : 0.5;
  const p2Tb = p2m ? valueFromPlayer(p2m, 'tiebreak_win_rate', 0.5) : 0.5;
  const p1VsTop = p1m ? valueFromPlayer(p1m, 'vs_top50_win_rate', 0.5) : 0.5;
  const p2VsTop = p2m ? valueFromPlayer(p2m, 'vs_top50_win_rate', 0.5) : 0.5;
  const p1SurfaceForm = p1m?.surface_form_10?.[surface] ?? p1SurfaceWinRate;
  const p2SurfaceForm = p2m?.surface_form_10?.[surface] ?? p2SurfaceWinRate;
  const p1Momentum = p1m ? valueFromPlayer(p1m, 'ranking_momentum', 0) : 0;
  const p2Momentum = p2m ? valueFromPlayer(p2m, 'ranking_momentum', 0) : 0;
  const h2hKey = `${playerKey(fixture.player1)}__${playerKey(fixture.player2)}`;
  const reverseH2hKey = `${playerKey(fixture.player2)}__${playerKey(fixture.player1)}`;
  const h2h = model?.h2h?.[h2hKey] ?? (model?.h2h?.[reverseH2hKey] ? -model.h2h[reverseH2hKey] : 0);
  const levelText = String(fixture.level || fixture.tournament || '').toLowerCase();
  const roundText = String(fixture.round || '').toLowerCase();
  const isGrandSlam = ['grand slam', 'australian open', 'roland garros', 'french open', 'wimbledon', 'us open'].some(x => levelText.includes(x));
  const isMasters = levelText.includes('masters') || levelText.includes('1000');
  const bestOf = isGrandSlam ? 5 : 3;
  const roundImportance = roundText.includes('final') ? 1 : roundText.includes('semi') ? 0.75 : roundText.includes('quarter') ? 0.55 : roundText.includes('round of 16') || roundText.includes('r16') ? 0.35 : 0.15;
  const features = {
    elo_diff: (p1Elo - p2Elo) / 400,
    surface_elo_diff: (p1Surface - p2Surface) / 400,
    win_rate_diff: p1WinRate - p2WinRate,
    surface_win_rate_diff: p1SurfaceWinRate - p2SurfaceWinRate,
    form_5_diff: p1Form5 - p2Form5,
    form_10_diff: p1Form10 - p2Form10,
    rank_log_diff: Math.log((p2Rank || 250) + 1) - Math.log((p1Rank || 250) + 1),
    rank_points_log_diff: Math.log((p1RankPts || 0) + 1) - Math.log((p2RankPts || 0) + 1),
    experience_log_diff: Math.log((p1Matches || 0) + 1) - Math.log((p2Matches || 0) + 1),
    h2h_diff: h2h,
    tournament_level_slam: isGrandSlam ? 1 : 0,
    tournament_level_masters: isMasters ? 1 : 0,
    tournament_level_500: levelText.includes('500') ? 1 : 0,
    tournament_level_250: levelText.includes('250') ? 1 : 0,
    best_of_5: bestOf >= 5 ? 1 : 0,
    round_importance: roundImportance,
    fatigue_diff: 0,
    days_since_last_match_diff: 0,
    win_streak_diff: 0,
    surface_form_diff: p1SurfaceForm - p2SurfaceForm,
    ranking_momentum_diff: p1Momentum - p2Momentum,
    indoor_flag: /indoor|indoors/i.test(`${fixture.tournament || ''} ${fixture.level || ''}`) ? 1 : 0,
    serve_strength_diff: p1Serve - p2Serve,
    return_strength_diff: p1Return - p2Return,
    hold_rate_diff: p1Hold - p2Hold,
    break_rate_diff: p1Break - p2Break,
    ace_rate_diff: p1Ace - p2Ace,
    double_fault_rate_diff: p1Df - p2Df,
    tiebreak_win_rate_diff: p1Tb - p2Tb,
    vs_top50_win_rate_diff: p1VsTop - p2VsTop
  };
  const advancedKeys = ['serve_strength', 'return_strength', 'hold_rate', 'break_rate', 'ace_rate', 'double_fault_rate', 'tiebreak_win_rate', 'vs_top50_win_rate'];
  const p1Advanced = p1m ? advancedKeys.filter(k => p1m[k] != null).length : 0;
  const p2Advanced = p2m ? advancedKeys.filter(k => p2m[k] != null).length : 0;
  const evidence = Math.min(1, (p1Matches + p2Matches) / 60);
  const dataDepthScore = Math.round(Math.min(100, (evidence * 55) + ((p1Advanced + p2Advanced) / (advancedKeys.length * 2) * 35) + ((p1m && p2m) ? 10 : 0)));
  const dataQuality = dataDepthScore >= 72 ? 'high' : dataDepthScore >= 48 ? 'medium' : 'low';
  return { features, dataQuality, dataDepthScore, evidence, p1Matches, p2Matches, modelAvailable: !!model, p1InModel: !!p1m, p2InModel: !!p2m };
}

function scoreOfflineModel(model, fv) {
  if (!model || !model.features || !model.coefficients) return null;
  let z = Number(model.intercept || 0);
  for (const name of model.features) z += Number(model.coefficients[name] || 0) * Number(fv.features[name] || 0);
  const evidenceShrink = 0.50 + 0.50 * Number(fv.evidence || 0);
  const temperature = Math.max(0.75, Number(model.calibration?.temperature || 1.12));
  const raw = sigmoid((z * evidenceShrink) / temperature);
  const priorBlend = String(fv.dataQuality || '') === 'high' ? 0.05 : String(fv.dataQuality || '') === 'medium' ? 0.12 : 0.24;
  const calibrated = (raw * (1 - priorBlend)) + (0.5 * priorBlend);
  return clamp(calibrated, 0.08, 0.92);
}

// Pure logistic z (== sklearn decision function on raw features)
function rawZ(model, fv) {
  let z = Number(model.intercept || 0);
  for (const name of model.features) z += Number(model.coefficients[name] || 0) * Number(fv.features[name] || 0);
  return z;
}

// ---- Measurements --------------------------------------------------------
function median(a) { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }

function main() {
  const raw = fs.readFileSync(MODEL_PATH);
  const out = { model_path: MODEL_PATH };

  // 0.1 size + parse time
  out['0.1_size_bytes'] = raw.length;
  out['0.1_size_mb'] = +(raw.length / 1024 / 1024).toFixed(3);
  const s = process.hrtime.bigint();
  const model = JSON.parse(raw.toString());
  const e = process.hrtime.bigint();
  // repeat parse 5x for a stable number
  const times = [];
  const str = raw.toString();
  for (let i = 0; i < 7; i++) { const a = process.hrtime.bigint(); JSON.parse(str); const b = process.hrtime.bigint(); times.push(Number(b - a) / 1e6); }
  out['0.1_parse_ms_first'] = +(Number(e - s) / 1e6).toFixed(2);
  out['0.1_parse_ms_median_of_7'] = +median(times).toFixed(2);
  out['0.1_player_keys'] = Object.keys(model.players).length;
  const playerFields = Object.keys(model.players[Object.keys(model.players)[0]]);
  out['0.1_fields_per_player'] = playerFields.length;
  out['0.1_h2h_pairs'] = Object.keys(model.h2h).length;
  out['0.1_top_level_keys'] = Object.keys(model).length;

  // 0.2 name hit rate on API-Tennis "Surname I." format
  // Build realistic API-Tennis inputs by taking model players ("first last[...]")
  // and formatting them as the API returns: "Surname Initial." (surname = all
  // tokens after the first; first token -> initial).
  const keys = Object.keys(model.players);
  // deterministic sample of 50
  function seeded(n, seed) { const r = []; let x = seed; for (let i = 0; i < n; i++) { x = (x * 1103515245 + 12345) & 0x7fffffff; r.push(keys[x % keys.length]); } return [...new Set(r)]; }
  let sample = seeded(120, 42).slice(0, 50);
  const modelKeySet = new Set(keys);
  let hitsPlain = 0; const misses = [];
  const examples = [];
  for (const k of sample) {
    const toks = k.split(' ');
    const first = toks[0];
    const surnameTokens = toks.slice(1);
    const surname = surnameTokens.join(' ');
    // Canonical API-Tennis string: "Surname F."
    const cap = w => w.charAt(0).toUpperCase() + w.slice(1);
    const apiName = `${surnameTokens.map(cap).join('-')} ${first.charAt(0).toUpperCase()}.`;
    const resolvedKey = playerKey(apiName);
    const hit = modelKeySet.has(resolvedKey);
    if (hit) hitsPlain++; else misses.push({ modelKey: k, apiName, resolvedKey });
    if (examples.length < 6) examples.push({ modelKey: k, apiName, resolvedKey, hit });
  }
  out['0.2_sample_size'] = sample.length;
  out['0.2_hits_via_playerKey'] = hitsPlain;
  out['0.2_hit_rate_pct'] = +(100 * hitsPlain / sample.length).toFixed(1);
  out['0.2_examples'] = examples;

  // Control: does playerKey resolve the FULL name form? (upper bound if names arrived correctly)
  let hitsFull = 0;
  for (const k of sample) {
    const toks = k.split(' ');
    const cap = w => w.charAt(0).toUpperCase() + w.slice(1);
    const fullName = toks.map(cap).join(' '); // "Firstname Lastname"
    if (modelKeySet.has(playerKey(fullName))) hitsFull++;
  }
  out['0.2_control_hit_rate_full_name_pct'] = +(100 * hitsFull / sample.length).toFixed(1);

  // 0.5 export parity: sklearn(predict_proba) == sigmoid(z). Build 200 feature
  // vectors from real random player pairs, emit z, pure sigmoid, and the
  // worker's shipped calibrated probability. Python confirms sklearn == sigmoid.
  const pairs = [];
  let x = 7;
  const surfaces = ['Hard', 'Clay', 'Grass'];
  for (let i = 0; i < 200; i++) {
    x = (x * 1103515245 + 12345) & 0x7fffffff; const a = keys[x % keys.length];
    x = (x * 1103515245 + 12345) & 0x7fffffff; const b = keys[x % keys.length];
    x = (x * 1103515245 + 12345) & 0x7fffffff; const surf = surfaces[x % surfaces.length];
    if (a === b) continue;
    // reconstruct a display name from the key so buildOfflineFeatureVector resolves it
    const disp = t => t.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
    const fixture = { player1: disp(a), player2: disp(b), surface: surf };
    const fv = buildOfflineFeatureVector(fixture, model, null);
    const z = rawZ(model, fv);
    pairs.push({
      features: model.features.map(f => Number(fv.features[f] || 0)),
      z,
      pure_sigmoid: sigmoid(z),
      worker_prob: scoreOfflineModel(model, fv),
      dataQuality: fv.dataQuality,
    });
  }
  fs.writeFileSync(path.join(__dirname, '_artifacts_parity_rows.json'), JSON.stringify({
    features: model.features,
    intercept: model.intercept,
    coefficients: model.features.map(f => model.coefficients[f]),
    rows: pairs,
  }));
  // JS-side divergence: worker shipped prob vs pure logistic (== sklearn)
  const dW = pairs.map(p => Math.abs(p.worker_prob - p.pure_sigmoid));
  out['0.5_rows'] = pairs.length;
  out['0.5_worker_vs_sklearn_max'] = +Math.max(...dW).toFixed(6);
  out['0.5_worker_vs_sklearn_median'] = +median(dW).toFixed(6);
  out['0.5_note'] = 'pure_sigmoid == sklearn predict_proba (confirmed in Python). worker_prob adds evidenceShrink+temperature+priorBlend+clamp.';

  console.log(JSON.stringify(out, null, 2));
}
main();
