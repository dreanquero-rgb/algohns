// TennisForest Cloudflare Pages Worker v22
// Live Tracking & Model Diagnostic Upgrade: immutable prediction registry + realtime settlement + model diagnostics.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/api/health') return json({ ok: true, service: 'tennisforest-api', version: 'v22-live-tracking-diagnostic', time: new Date().toISOString(), provider: 'api-tennis' });
      if (url.pathname === '/api/future') return await handleFuture(env, url);
      if (url.pathname === '/api/odds') return await handleOdds(env, url);
      if (url.pathname === '/api/stats') return await handleStats(env);
      if (url.pathname === '/api/predictions') return await handlePredictions(env, url);
      if (url.pathname === '/api/tracking-check') return await handleTrackingCheck(env, url);
      if (url.pathname === '/api/settle') return await handleSettle(env, url);
      if (url.pathname === '/api/backtest') return await handleBacktest(env, url);
      if (url.pathname === '/api/model') return await handleModel(env, url);
      if (url.pathname === '/api/validate') return await handleValidate(env, url);
      if (url.pathname === '/api/cleanup-demo') return await handleCleanupDemo(env);
      return env.ASSETS.fetch(request);
    } catch (err) {
      return json({ ok: false, error: String(err && err.message ? err.message : err), path: url.pathname, time: new Date().toISOString() }, 500);
    }
  }
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'access-control-allow-origin': '*' }
  });
}

async function loadOfflineModel(env, requestUrl) {
  try {
    const modelUrl = new URL('/model.json', requestUrl.origin);
    const res = await env.ASSETS.fetch(new Request(modelUrl.toString(), { method: 'GET' }));
    if (!res.ok) return null;
    const model = await res.json();
    if (!model || !model.features || !model.coefficients) return null;
    // Ignore the bundled placeholder. The offline model is active only after running training/train_model.py.
    if (!model.players || Object.keys(model.players).length < 25 || Number(model.training_rows || 0) <= 0) return null;
    return model;
  } catch {
    return null;
  }
}

function modelPlayer(model, name) {
  const key = playerKey(name);
  return model?.players?.[key] || null;
}

function valueFromPlayer(player, path, fallback = 0) {
  if (!player) return fallback;
  const parts = String(path).split('.');
  let cur = player;
  for (const p of parts) {
    if (cur == null || !(p in cur)) return fallback;
    cur = cur[p];
  }
  const n = Number(cur);
  return Number.isFinite(n) ? n : fallback;
}

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
  const isGrandSlam = ['grand slam','australian open','roland garros','french open','wimbledon','us open'].some(x => levelText.includes(x));
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
    // v21 forward-compatible features. They are used only if the exported model.json contains coefficients for them.
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
  const advancedKeys = ['serve_strength','return_strength','hold_rate','break_rate','ace_rate','double_fault_rate','tiebreak_win_rate','vs_top50_win_rate'];
  const p1Advanced = p1m ? advancedKeys.filter(k => p1m[k] != null).length : 0;
  const p2Advanced = p2m ? advancedKeys.filter(k => p2m[k] != null).length : 0;
  const evidence = Math.min(1, (p1Matches + p2Matches) / 60);
  const dataDepthScore = Math.round(Math.min(100, (evidence * 55) + ((p1Advanced + p2Advanced) / (advancedKeys.length * 2) * 35) + ((p1m && p2m) ? 10 : 0)));
  const dataQuality = dataDepthScore >= 72 ? 'high' : dataDepthScore >= 48 ? 'medium' : 'low';
  return { features, dataQuality, dataDepthScore, evidence, p1Matches, p2Matches, advancedFields: { p1Advanced, p2Advanced, max: advancedKeys.length }, modelAvailable: !!model, p1InModel: !!p1m, p2InModel: !!p2m };
}

function scoreOfflineModel(model, fv) {
  if (!model || !model.features || !model.coefficients) return null;
  let z = Number(model.intercept || 0);
  for (const name of model.features) z += Number(model.coefficients[name] || 0) * Number(fv.features[name] || 0);

  // v21: conservative calibration. We reduce overconfidence when evidence is thin and support
  // an optional temperature exported by the Python trainer. This does not invent picks; it makes
  // BET selection stricter and more robust when the model is uncertain.
  const evidenceShrink = 0.50 + 0.50 * Number(fv.evidence || 0);
  const temperature = Math.max(0.75, Number(model.calibration?.temperature || 1.12));
  const raw = sigmoid((z * evidenceShrink) / temperature);
  const priorBlend = String(fv.dataQuality || '') === 'high' ? 0.05 : String(fv.dataQuality || '') === 'medium' ? 0.12 : 0.24;
  const calibrated = (raw * (1 - priorBlend)) + (0.5 * priorBlend);
  return clamp(calibrated, 0.08, 0.92);
}

function todayISO(offsetDays = 0) { const d = new Date(); d.setUTCDate(d.getUTCDate() + offsetDays); return d.toISOString().slice(0, 10); }
function addDaysISO(iso, offset) { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + offset); return d.toISOString().slice(0, 10); }
function safeId(input) { return String(input || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 140) || `id-${Date.now()}`; }
function normalizeName(name) { return String(name || '').trim().replace(/\s+/g, ' ') || 'Unknown Player'; }
function sigmoid(x) { return 1 / (1 + Math.exp(-x)); }
function clamp(n, min, max) { return Math.max(min, Math.min(max, Number(n) || 0)); }
function avg(nums) { const clean = nums.map(Number).filter(n => Number.isFinite(n) && n > 1); return clean.length ? clean.reduce((a,b)=>a+b,0) / clean.length : null; }
function money(n) { return Number((Number(n) || 0).toFixed(2)); }

function tournamentLevel(t = '', raw = {}) {
  const text = `${t} ${raw.event_type_type || ''} ${raw.event_type_name || ''} ${raw.league_name || ''} ${raw.tournament_type || ''}`.toLowerCase();
  if (['australian open','roland garros','french open','wimbledon','us open'].some(x=>text.includes(x))) return 'Grand Slam';
  if (text.includes('masters') || text.includes('1000')) return 'Masters 1000';
  if (text.includes('finals')) return 'ATP Finals';
  if (text.includes('500')) return 'ATP 500';
  if (text.includes('250')) return 'ATP 250';
  if (text.includes('atp')) return 'ATP Tour';
  return 'Unknown';
}

function isAtp250Plus(raw = {}, fixture = {}) {
  const text = `${fixture.tournament || ''} ${fixture.level || ''} ${fixture.round || ''} ${raw.event_type_type || ''} ${raw.event_type_name || ''} ${raw.tournament_name || ''} ${raw.event_round || ''} ${raw.tournament_round || ''}`.toLowerCase();

  const banned = ['wta','challenger','itf','qualification','qualifying','qualif','junior','boys','girls','utr','doubles','exhibition'];
  if (banned.some(x => text.includes(x))) return false;

  // GreenCode production filter: broad ATP main-tour singles.
  // We accept generic "ATP Singles" again because API-Tennis often labels Roland Garros / ATP main-draw matches this way.
  return (
    text.includes('atp singles') ||
    text.includes('atp 250') ||
    text.includes('atp 500') ||
    text.includes('masters 1000') ||
    text.includes('atp masters') ||
    text.includes('1000') ||
    text.includes('atp finals') ||
    text.includes('tour finals') ||
    text.includes('nitto') ||
    ['australian open','roland garros','french open','wimbledon','us open'].some(x => text.includes(x))
  );
}

function isAtpSinglesLoose(raw = {}, fixture = {}) {
  const text = `${fixture.tournament || ''} ${fixture.level || ''} ${raw.event_type_type || ''} ${raw.event_type_name || ''} ${raw.tournament_name || ''} ${raw.event_first_player || ''} ${raw.event_second_player || ''}`.toLowerCase();
  const banned = ['wta','challenger','itf','qualification','qualifying','junior','boys','girls','utr','doubles','exhibition'];
  if (banned.some(x => text.includes(x))) return false;
  return text.includes('atp') || text.includes('masters') || text.includes('grand slam') || ['australian open','roland garros','french open','wimbledon','us open'].some(x => text.includes(x));
}

function isFinishedRaw(raw = {}) {
  const status = String(raw.event_status || raw.status || raw.match_status || '').toLowerCase();
  const result = String(raw.event_final_result || raw.result || raw.final_result || '').trim();
  return status.includes('finished') || status.includes('final') || status.includes('ended') || /\d/.test(result);
}

function detectSurface(raw = {}) {
  const text = `${raw.tournament_name || ''} ${raw.event_surface || ''} ${raw.surface || ''} ${raw.event_surface_type || ''}`.toLowerCase();
  if (text.includes('clay')) return 'Clay';
  if (text.includes('grass')) return 'Grass';
  if (text.includes('carpet')) return 'Carpet';
  if (text.includes('hard')) return 'Hard';
  return 'Hard';
}

function normalizeFixture(raw) {
  const tournament = raw.tournament_name || raw.league_name || raw.event_name || raw.tournament || 'ATP Tournament';
  const player1 = normalizeName(raw.event_first_player || raw.first_player || raw.player1 || raw.home_team || raw.event_home_team || raw.home_name);
  const player2 = normalizeName(raw.event_second_player || raw.second_player || raw.player2 || raw.away_team || raw.event_away_team || raw.away_name);
  const date = raw.event_date || raw.date || raw.match_date || todayISO(0);
  const time = raw.event_time || raw.time || raw.match_time || '';
  const id = safeId(raw.event_key || raw.match_key || raw.id || `${tournament}-${date}-${time}-${player1}-${player2}`);
  const level = tournamentLevel(tournament, raw);
  return {
    id,
    sourceMatchId: String(raw.event_key || raw.match_key || raw.id || id),
    source: 'api-tennis',
    tournament,
    level,
    round: raw.event_round || raw.tournament_round || raw.round || raw.event_stage || raw.stage || 'TBD',
    date,
    time,
    surface: detectSurface(raw),
    player1,
    player2,
    firstPlayerKey: raw.first_player_key || raw.player1_key || '',
    secondPlayerKey: raw.second_player_key || raw.player2_key || '',
    rawStatus: raw.event_status || raw.status || '',
    finalResult: raw.event_final_result || raw.result || '',
    winnerRaw: raw.event_winner || raw.winner || '',
    eventLive: raw.event_live || '0',
    rawStatus: raw.event_status || raw.status || raw.match_status || '',
    finalResult: raw.event_final_result || raw.result || raw.final_result || '',
    winnerRaw: raw.event_winner || raw.winner || raw.match_winner || '',
    _raw: raw
  };
}

async function callApiTennis(env, method, params = {}) {
  const key = env.TENNIS_API_KEY;
  if (!key) throw new Error('Missing TENNIS_API_KEY');
  const url = new URL('https://api.api-tennis.com/tennis/');
  url.searchParams.set('method', method);
  url.searchParams.set('APIkey', key);
  Object.entries(params).forEach(([k,v]) => { if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v)); });
  const res = await fetch(url.toString(), { headers: { accept: 'application/json' } });
  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text); } catch { data = { parseError: text.slice(0, 700) }; }
  if (!res.ok) throw new Error(`API-Tennis ${method} HTTP ${res.status}: ${JSON.stringify(data).slice(0, 300)}`);
  if (data.error) throw new Error(`API-Tennis ${method} error: ${JSON.stringify(data.error).slice(0, 300)}`);
  return data;
}

async function fetchFixturesRange(env, start, stop, { useEventType = true, mode = 'future', allowLooseFallback = false } = {}) {
  const key = env.TENNIS_API_KEY;
  if (!key || String(key).includes('la_tua') || String(key).includes('your_')) {
    return { source: 'no-api-key', fixtures: [], rawFixtures: [], rawCount: 0, normalizedCount: 0, warning: 'TENNIS_API_KEY assente.', debug: { reason: 'missing-key', start, stop } };
  }

  let raw = [];
  const attempts = [];
  const params1 = { date_start: start, date_stop: stop };
  if (useEventType) params1.event_type_key = env.ATP_EVENT_TYPE_KEY || '265';

  try {
    const data = await callApiTennis(env, 'get_fixtures', params1);
    raw = Array.isArray(data.result) ? data.result : [];
    attempts.push({ params: params1, rawCount: raw.length });
  } catch (e) {
    attempts.push({ params: params1, error: String(e.message || e) });
  }

  // API-Tennis sometimes returns past results only without event_type_key, so always retry for backtest if filtered result is empty.
  if ((!raw.length || mode === 'backtest') && useEventType) {
    try {
      const data2 = await callApiTennis(env, 'get_fixtures', { date_start: start, date_stop: stop });
      const raw2 = Array.isArray(data2.result) ? data2.result : [];
      attempts.push({ params: { date_start: start, date_stop: stop }, rawCount: raw2.length, note: 'retry-no-event-type' });
      if (raw2.length > raw.length) raw = raw2;
    } catch (e) {
      attempts.push({ params: { date_start: start, date_stop: stop }, error: String(e.message || e), note: 'retry-no-event-type' });
    }
  }

  const normalized = raw.map(normalizeFixture).filter(f => f.player1 && f.player2 && f.player1 !== 'Unknown Player' && f.player2 !== 'Unknown Player');
  const strict = normalized.filter((f, idx) => isAtp250Plus(raw[idx], f));

  // IMPORTANT: by default the backtest is strict ATP 250+ only.
  // The old v10 fallback could include too many ATP-looking matches and made the score misleading.
  const loose = normalized.filter((f, idx) => isAtpSinglesLoose(raw[idx], f));
  let selected = strict;
  let filterMode = 'atp250plus-strict';
  if (mode === 'model') {
    selected = loose.length > strict.length ? loose : strict;
    filterMode = loose.length > strict.length ? 'model-atp-singles-history' : 'model-atp250plus-history';
  } else if (mode === 'backtest' && allowLooseFallback && selected.length === 0 && loose.length > 0) {
    selected = loose;
    filterMode = 'atp-singles-loose-fallback';
  }

  if (mode === 'backtest' || mode === 'model') {
    selected = selected.filter(f => isFinishedRaw(f._raw || f));
  }

  let warning = '';
  if (!selected.length && normalized.length) warning = mode === 'backtest'
    ? 'API reale collegata, ma non ha trovato partite ATP 250+ concluse nel range strict. Non faccio fallback automatico perché renderebbe il backtest fuorviante.'
    : 'API reale collegata, ma il filtro ATP 250+ ha scartato tutte le fixtures. Non mostro demo.';
  if (!normalized.length) warning = 'API reale collegata, ma non ha restituito fixtures nel range selezionato.';

  return {
    source: 'api-tennis',
    fixtures: selected,
    rawFixtures: raw,
    rawCount: raw.length,
    normalizedCount: normalized.length,
    warning,
    debug: { attempts, start, stop, filteredAtp250Plus: strict.length, looseAtpSingles: loose.length, selected: selected.length, filterMode, mode }
  };
}

function extractOddsForMatch(oddsPayload, matchKey) {
  if (!oddsPayload || !matchKey) return null;
  const result = oddsPayload.result || oddsPayload;
  const matchOdds = result[String(matchKey)] || result[Number(matchKey)] || null;
  if (!matchOdds) return null;
  const market = matchOdds['Home/Away'] || matchOdds['Match Winner'] || matchOdds['Winner'] || null;
  if (!market) return null;
  const homeObj = market.Home || market.home || market['1'] || market.First || null;
  const awayObj = market.Away || market.away || market['2'] || market.Second || null;
  if (!homeObj || !awayObj) return null;
  const homeAvg = avg(Object.values(homeObj));
  const awayAvg = avg(Object.values(awayObj));
  if (!homeAvg || !awayAvg) return null;
  return {
    homeAvg: money(homeAvg),
    awayAvg: money(awayAvg),
    homeBest: money(Math.max(...Object.values(homeObj).map(Number).filter(Number.isFinite))),
    awayBest: money(Math.max(...Object.values(awayObj).map(Number).filter(Number.isFinite))),
    bookmakers: Array.from(new Set([...Object.keys(homeObj), ...Object.keys(awayObj)])).slice(0, 12)
  };
}

async function fetchOddsMap(env, start, stop) {
  if (String(env.ENABLE_ODDS || 'true').toLowerCase() === 'false') return { ok:false, reason:'odds-disabled', oddsByMatch:{}, diagnostics:{ method:'disabled' } };
  try {
    const params = { date_start: start, date_stop: stop };
    if (env.ATP_EVENT_TYPE_KEY) params.event_type_key = env.ATP_EVENT_TYPE_KEY;
    const data = await callApiTennis(env, 'get_odds', params);
    return { ok:true, oddsByMatch: data.result || {}, diagnostics:{ method:'date-range', keys:Object.keys(data.result || {}).length } };
  } catch (e) {
    return { ok:false, reason:String(e.message || e), oddsByMatch:{}, diagnostics:{ method:'date-range' } };
  }
}

async function fetchOddsForMatch(env, matchKey) {
  if (String(env.ENABLE_ODDS || 'true').toLowerCase() === 'false') return { ok:false, reason:'odds-disabled', matchKey, odds:null };
  if (!matchKey) return { ok:false, reason:'missing-match-key', matchKey, odds:null };
  try {
    const data = await callApiTennis(env, 'get_odds', { match_key: matchKey });
    const odds = extractOddsForMatch(data, matchKey);
    return { ok: !!odds, reason: odds ? '' : 'no-home-away-market-for-match', matchKey, odds };
  } catch (e) {
    return { ok:false, reason:String(e.message || e), matchKey, odds:null };
  }
}

function playerKey(name) {
  return String(name || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
}

function emptyPlayerStats() {
  return {
    elo: 1500,
    surfaceElo: {},
    wins: 0,
    losses: 0,
    surfaceWins: {},
    surfaceLosses: {},
    recent: []
  };
}

function getStats(state, name) {
  const k = playerKey(name);
  if (!state.players[k]) state.players[k] = emptyPlayerStats();
  return state.players[k];
}

function statRate(w, l) {
  // Bayesian smoothing: avoids extreme 100% from 1-0 samples.
  return (Number(w || 0) + 3) / (Number(w || 0) + Number(l || 0) + 6);
}

function recentRate(arr) {
  if (!arr || !arr.length) return 0.5;
  const last = arr.slice(-5);
  return (last.reduce((a,b)=>a+(b?1:0),0) + 1) / (last.length + 2);
}

function expectedScore(rA, rB) {
  return 1 / (1 + Math.pow(10, (rB - rA) / 400));
}

function updateElo(rA, rB, scoreA, k = 28) {
  return rA + k * (scoreA - expectedScore(rA, rB));
}

function sortFixtures(fixtures) {
  return [...fixtures].sort((a,b) => `${a.date || ''} ${a.time || ''} ${a.sourceMatchId || ''}`.localeCompare(`${b.date || ''} ${b.time || ''} ${b.sourceMatchId || ''}`));
}

function updateModelState(state, fixture) {
  const actual = resolveWinner(fixture, fixture);
  if (!actual) return false;

  const p1 = getStats(state, fixture.player1);
  const p2 = getStats(state, fixture.player2);
  const surface = fixture.surface || 'Hard';
  const p1Won = actual === fixture.player1;

  const r1 = p1.elo;
  const r2 = p2.elo;
  p1.elo = updateElo(r1, r2, p1Won ? 1 : 0);
  p2.elo = updateElo(r2, r1, p1Won ? 0 : 1);

  const s1 = p1.surfaceElo[surface] || 1500;
  const s2 = p2.surfaceElo[surface] || 1500;
  p1.surfaceElo[surface] = updateElo(s1, s2, p1Won ? 1 : 0, 22);
  p2.surfaceElo[surface] = updateElo(s2, s1, p1Won ? 0 : 1, 22);

  if (p1Won) { p1.wins++; p2.losses++; p1.surfaceWins[surface] = (p1.surfaceWins[surface] || 0) + 1; p2.surfaceLosses[surface] = (p2.surfaceLosses[surface] || 0) + 1; }
  else { p2.wins++; p1.losses++; p2.surfaceWins[surface] = (p2.surfaceWins[surface] || 0) + 1; p1.surfaceLosses[surface] = (p1.surfaceLosses[surface] || 0) + 1; }

  p1.recent.push(p1Won); p2.recent.push(!p1Won);
  if (p1.recent.length > 8) p1.recent.shift();
  if (p2.recent.length > 8) p2.recent.shift();

  state.matchesUsed++;
  return true;
}

function createModelState() {
  return { players: {}, matchesUsed: 0 };
}

function buildModelState(fixtures) {
  const state = createModelState();
  for (const f of sortFixtures(fixtures)) updateModelState(state, f);
  return state;
}

function predictFixture(fixture, env, odds = null, modelState = null, offlineModel = null) {
  const fv = buildOfflineFeatureVector(fixture, offlineModel, modelState);
  let p1Prob = scoreOfflineModel(offlineModel, fv);

  if (p1Prob == null) {
    // Fallback if model.json has not been generated yet.
    const f = fv.features;
    const zRaw = 1.15*f.elo_diff + 0.75*f.surface_elo_diff + 1.10*f.win_rate_diff + 0.55*f.surface_win_rate_diff + 0.80*f.form_5_diff + 0.45*f.h2h_diff;
    p1Prob = clamp(sigmoid(zRaw * (0.45 + 0.55 * fv.evidence)), 0.08, 0.92);
  }

  const p2Prob = 1 - p1Prob;
  const predictedWinner = p1Prob >= p2Prob ? fixture.player1 : fixture.player2;
  const confidence = Math.max(p1Prob, p2Prob);
  const pickSide = predictedWinner === fixture.player1 ? 'home' : 'away';
  const pickOdds = odds ? (pickSide === 'home' ? odds.homeAvg : odds.awayAvg) : null;
  const otherOdds = odds ? (pickSide === 'home' ? odds.awayAvg : odds.homeAvg) : null;
  const homeImpRaw = odds ? 1 / odds.homeAvg : null;
  const awayImpRaw = odds ? 1 / odds.awayAvg : null;
  const overround = homeImpRaw && awayImpRaw ? homeImpRaw + awayImpRaw : null;
  const homeImpFair = overround ? homeImpRaw / overround : null;
  const awayImpFair = overround ? awayImpRaw / overround : null;
  const impliedProb = pickOdds ? 1 / pickOdds : null;
  const fairImpliedProb = odds ? (pickSide === 'home' ? homeImpFair : awayImpFair) : null;
  const edge = fairImpliedProb != null ? confidence - fairImpliedProb : (pickOdds ? confidence - impliedProb : null);
  const expectedProfit100 = pickOdds ? (confidence * ((pickOdds - 1) * 100)) - ((1 - confidence) * 100) : null;
  const bookmakerFavoriteSide = odds ? (odds.homeAvg <= odds.awayAvg ? 'home' : 'away') : null;
  const bookmakerFavorite = odds ? (bookmakerFavoriteSide === 'home' ? fixture.player1 : fixture.player2) : null;
  const bookmakerFavoriteOdds = odds ? Math.min(odds.homeAvg, odds.awayAvg) : null;
  const modelAgainstBook = odds ? predictedWinner !== bookmakerFavorite : false;

  return {
    id: safeId(`${fixture.id}-${fixture.date}`),
    matchId: fixture.id,
    sourceMatchId: fixture.sourceMatchId,
    tournament: fixture.tournament,
    level: fixture.level,
    round: fixture.round,
    date: fixture.date,
    time: fixture.time,
    surface: fixture.surface,
    player1: fixture.player1,
    player2: fixture.player2,
    p1Prob: Number(p1Prob.toFixed(4)),
    p2Prob: Number(p2Prob.toFixed(4)),
    predictedWinner,
    pickSide,
    confidence: Number(confidence.toFixed(4)),
    oddsHome: odds ? odds.homeAvg : null,
    oddsAway: odds ? odds.awayAvg : null,
    oddsPick: pickOdds,
    oddsOther: otherOdds,
    bookmakerFavorite,
    bookmakerFavoriteSide,
    bookmakerFavoriteOdds,
    modelAgainstBook,
    overround: overround == null ? null : Number(overround.toFixed(4)),
    impliedProb: impliedProb ? Number(impliedProb.toFixed(4)) : null,
    fairImpliedProb: fairImpliedProb == null ? null : Number(fairImpliedProb.toFixed(4)),
    edge: edge == null ? null : Number(edge.toFixed(4)),
    expectedProfit100: expectedProfit100 == null ? null : money(expectedProfit100),
    oddsBookmakers: odds ? odds.bookmakers : [],
    status: 'pending',
    modelVersion: env.MODEL_VERSION || 'greencode-realtime-worker-v22',
    modelType: offlineModel ? 'greencode-offline-model-json' : 'online-elo-fallback',
    dataQuality: fv.dataQuality,
    dataDepthScore: fv.dataDepthScore,
    advancedFields: fv.advancedFields,
    features: {
      ...Object.fromEntries(Object.entries(fv.features).map(([k,v]) => [k, Number(Number(v).toFixed(3))])),
      p1Matches: fv.p1Matches,
      p2Matches: fv.p2Matches,
      evidence: Number(fv.evidence.toFixed(3)),
      p1InModel: fv.p1InModel,
      p2InModel: fv.p2InModel,
      dataDepthScore: fv.dataDepthScore
    }
  };
}

function firestoreBase(env) { const projectId = env.FIREBASE_PROJECT_ID || 'tennisforest-8456e'; return `https://firestore.googleapis.com/v1/projects/${projectId}/databases/(default)/documents`; }
function docUrl(env, collection, id) { const apiKey = env.FIREBASE_API_KEY || ''; return `${firestoreBase(env)}/${collection}/${id}?key=${apiKey}`; }
function listUrl(env, collection, pageSize = 100) { const apiKey = env.FIREBASE_API_KEY || ''; return `${firestoreBase(env)}/${collection}?pageSize=${pageSize}&key=${apiKey}`; }
function toFields(obj) { const fields = {}; for (const [k,v] of Object.entries(obj||{})) { if (v === undefined) continue; if (v === null) fields[k] = { nullValue: null }; else if (typeof v === 'string') fields[k] = { stringValue: v }; else if (typeof v === 'boolean') fields[k] = { booleanValue: v }; else if (typeof v === 'number') fields[k] = Number.isInteger(v) ? { integerValue: String(v) } : { doubleValue: v }; else if (Array.isArray(v)) fields[k] = { arrayValue: { values: v.map(x => toFields({x}).x) } }; else if (typeof v === 'object') fields[k] = { mapValue: { fields: toFields(v) } }; } return fields; }
function fromFields(fields = {}) { const obj = {}; for (const [k,v] of Object.entries(fields)) { if ('stringValue' in v) obj[k] = v.stringValue; else if ('integerValue' in v) obj[k] = Number(v.integerValue); else if ('doubleValue' in v) obj[k] = Number(v.doubleValue); else if ('booleanValue' in v) obj[k] = Boolean(v.booleanValue); else if ('nullValue' in v) obj[k] = null; else if ('mapValue' in v) obj[k] = fromFields(v.mapValue.fields || {}); else if ('arrayValue' in v) obj[k] = (v.arrayValue.values || []).map(x => fromFields({x}).x); } return obj; }
async function getDoc(env, collection, id) { const res = await fetch(docUrl(env, collection, id)); if (res.status === 404) return null; if (!res.ok) throw new Error(`Firestore get ${collection}/${id} ${res.status}: ${(await res.text()).slice(0,180)}`); const data = await res.json(); return { id, ...fromFields(data.fields || {}) }; }
async function setDoc(env, collection, id, data) { const res = await fetch(docUrl(env, collection, id), { method:'PATCH', headers:{'content-type':'application/json'}, body: JSON.stringify({ fields: toFields(data) }) }); if (!res.ok) throw new Error(`Firestore set ${collection}/${id} ${res.status}: ${(await res.text()).slice(0,180)}`); return res.json(); }
async function listDocs(env, collection, limit = 100) { const res = await fetch(listUrl(env, collection, limit)); if (!res.ok) throw new Error(`Firestore list ${collection} ${res.status}: ${(await res.text()).slice(0,180)}`); const data = await res.json(); return (data.documents || []).map(doc => ({ id: doc.name.split('/').pop(), ...fromFields(doc.fields || {}) })); }
async function deleteDoc(env, collection, id) { const res = await fetch(docUrl(env, collection, id), { method:'DELETE' }); if (!res.ok && res.status !== 404) throw new Error(`Firestore delete ${collection}/${id} ${res.status}: ${(await res.text()).slice(0,180)}`); return true; }

function compactPredictionForFirestore(p, now) {
  const decision = p.betSignal?.label || p.decision || 'NO BET';
  const stake = Number(p.betSignal?.stake || p.stake || 0);
  return {
    id: safeId(p.id),
    matchId: p.matchId || '',
    sourceMatchId: p.sourceMatchId || '',
    tournament: p.tournament || '',
    level: p.level || '',
    round: p.round || '',
    date: p.date || '',
    time: p.time || '',
    surface: p.surface || '',
    player1: p.player1 || '',
    player2: p.player2 || '',
    predictedWinner: p.predictedWinner || '',
    pickSide: p.pickSide || '',
    p1Prob: p.p1Prob,
    p2Prob: p.p2Prob,
    confidence: p.confidence,
    oddsHome: p.oddsHome ?? null,
    oddsAway: p.oddsAway ?? null,
    oddsPick: p.oddsPick ?? null,
    oddsOther: p.oddsOther ?? null,
    bookmakerFavorite: p.bookmakerFavorite || '',
    bookmakerFavoriteSide: p.bookmakerFavoriteSide || '',
    bookmakerFavoriteOdds: p.bookmakerFavoriteOdds ?? null,
    modelAgainstBook: p.modelAgainstBook === true,
    overround: p.overround ?? null,
    impliedProb: p.impliedProb ?? null,
    fairImpliedProb: p.fairImpliedProb ?? null,
    edge: p.edge ?? null,
    expectedProfit100: p.expectedProfit100 ?? null,
    status: p.status || 'pending',
    decision,
    reason: p.betSignal?.reason || p.reason || '',
    stake,
    modelVersion: p.modelVersion || '',
    modelType: p.modelType || '',
    dataQuality: p.dataQuality || '',
    dataDepthScore: p.dataDepthScore ?? null,
    features: p.features || {},
    createdAt: now,
    lastSeenAt: now
  };
}

function compactRunForFirestore(p, id, now) {
  return {
    predictionId:id,
    matchId:p.matchId || '',
    sourceMatchId:p.sourceMatchId || '',
    tournament:p.tournament || '',
    player1:p.player1 || '',
    player2:p.player2 || '',
    predictedWinner:p.predictedWinner || '',
    p1Prob:p.p1Prob,
    p2Prob:p.p2Prob,
    confidence:p.confidence,
    oddsPick:p.oddsPick ?? null,
    edge:p.edge ?? null,
    expectedProfit100:p.expectedProfit100 ?? null,
    decision:p.betSignal?.label || p.decision || 'NO BET',
    reason:p.betSignal?.reason || p.reason || '',
    stake:Number(p.betSignal?.stake || p.stake || 0),
    modelVersion:p.modelVersion || '',
    modelType:p.modelType || '',
    dataQuality:p.dataQuality || '',
    dataDepthScore:p.dataDepthScore ?? null,
    createdAt:now
  };
}

async function savePrediction(env, p) {
  if (String(env.SAVE_PREDICTIONS || 'true').toLowerCase() === 'false') return { id:safeId(p.id), skipped:true, reason:'SAVE_PREDICTIONS=false' };
  const id = safeId(p.id);
  const existing = await getDoc(env,'predictions',id).catch(()=>null);
  const now = new Date().toISOString();
  const fresh = compactPredictionForFirestore(p, now);
  let data;
  if (existing) {
    // Immutable original prediction: do not rewrite the first pick/probability/odds that was recorded.
    // We only append the latest market/model snapshot for monitoring.
    data = {
      ...existing,
      lastSeenAt: now,
      seenCount: Number(existing.seenCount || 1) + 1,
      latestSnapshot: {
        predictedWinner: p.predictedWinner || '',
        p1Prob: p.p1Prob,
        p2Prob: p.p2Prob,
        confidence: p.confidence,
        oddsHome: p.oddsHome ?? null,
        oddsAway: p.oddsAway ?? null,
        oddsPick: p.oddsPick ?? null,
        edge: p.edge ?? null,
        expectedProfit100: p.expectedProfit100 ?? null,
        decision: p.betSignal?.label || p.decision || 'NO BET',
        reason: p.betSignal?.reason || p.reason || '',
        dataDepthScore: p.dataDepthScore ?? null,
        modelVersion: p.modelVersion || '',
        seenAt: now
      }
    };
  } else {
    data = { ...fresh, originalLocked: true, seenCount: 1 };
  }
  await setDoc(env,'predictions',id,data);
  await setDoc(env,'predictionRuns', safeId(`${id}-${now}`), compactRunForFirestore(p, id, now));
  return { id, saved: !existing, updated: !!existing, status:'ok' };
}

function parseSetScores(result = '') {
  const setScores = String(result || '').match(/(\d+)\s*[-:]\s*(\d+)/g) || [];
  let p1Sets = 0, p2Sets = 0;
  for (const s of setScores) {
    const [a,b] = s.split(/[-:]/).map(x => Number(x.trim()));
    if (Number.isFinite(a) && Number.isFinite(b) && a !== b) {
      if (a > b) p1Sets++; else p2Sets++;
    }
  }
  return { p1Sets, p2Sets, parsedSets:setScores.length };
}

function requiredSetsToWin(raw = {}) {
  const bestOf = Number(raw.best_of || raw.event_best_of || raw.match_best_of || raw.bestof || 3);
  return bestOf >= 5 ? 3 : 2;
}

function isClearlyFinished(rawInput = {}) {
  const raw = rawInput && rawInput._raw ? rawInput._raw : rawInput || {};
  const status = String(raw.event_status || raw.status || raw.match_status || rawInput.rawStatus || '').toLowerCase();

  if (['not started','scheduled','postponed','cancelled','canceled','walkover'].some(x => status.includes(x))) return false;
  if (['live','in progress','1st set','2nd set','3rd set','4th set','5th set','set'].some(x => status.includes(x)) && !status.includes('finished')) return false;
  if (status.includes('finished') || status.includes('ended') || status === 'final' || status.includes('complete')) return true;

  // If the API status is missing, only use the score if enough sets were won.
  const result = String(raw.event_final_result || raw.result || raw.final_result || rawInput.finalResult || '').trim();
  const sets = parseSetScores(result);
  return Math.max(sets.p1Sets, sets.p2Sets) >= requiredSetsToWin(raw);
}

function resolveWinner(rawInput, p) {
  const raw = rawInput && rawInput._raw ? rawInput._raw : rawInput || {};
  if (!isClearlyFinished(rawInput)) return '';

  const winner = String(raw.event_winner || raw.winner || raw.match_winner || rawInput.winnerRaw || '').toLowerCase();
  if (winner.includes('first') || winner === '1' || winner.includes('home')) return p.player1;
  if (winner.includes('second') || winner === '2' || winner.includes('away')) return p.player2;

  const p1 = String(p.player1 || '').toLowerCase();
  const p2 = String(p.player2 || '').toLowerCase();
  if (winner && p1 && winner.includes(p1.slice(0, Math.min(8, p1.length)))) return p.player1;
  if (winner && p2 && winner.includes(p2.slice(0, Math.min(8, p2.length)))) return p.player2;

  const result = String(raw.event_final_result || raw.result || raw.final_result || rawInput.finalResult || '').trim();
  const sets = parseSetScores(result);
  if (sets.p1Sets !== sets.p2Sets && Math.max(sets.p1Sets, sets.p2Sets) >= requiredSetsToWin(raw)) {
    return sets.p1Sets > sets.p2Sets ? p.player1 : p.player2;
  }

  return '';
}

function computeStats(predictions, runs) {
  const settled = predictions.filter(p => p.status === 'settled');
  const pending = predictions.filter(p => p.status !== 'settled');
  const correct = settled.filter(p => p.correct === true).length;
  const wrong = settled.length - correct;
  const accuracy = settled.length ? Number((correct / settled.length).toFixed(4)) : null;
  const withOdds = settled.filter(p => Number(p.oddsPick) > 1);
  const totalStake = withOdds.length * 100;
  const profit = money(withOdds.reduce((sum,p) => sum + Number(p.profit100 || 0), 0));
  const roi = totalStake ? Number((profit / totalStake).toFixed(4)) : null;
  const pendingValue = money(pending.reduce((sum,p) => sum + Number(p.expectedProfit100 || 0), 0));
  const betCount = predictions.filter(p => String(p.decision || p.betSignal?.label || '').toUpperCase() === 'BET' || Number(p.stake || 0) > 0).length;
  const noBetCount = predictions.length - betCount;

  const calibration = ['50-60%','60-70%','70-80%','80-90%','90-100%'].map(label=>({label,total:0,correct:0,accuracy:null}));
  for (const p of settled) {
    const c = Number(p.confidence || p.currentConfidence || .5);
    const idx = Math.min(4, Math.max(0, Math.floor((c-.5)/.1)));
    calibration[idx].total++; if (p.correct) calibration[idx].correct++;
  }
  calibration.forEach(b=>{ b.accuracy = b.total ? Number((b.correct/b.total).toFixed(3)) : null; });

  const byDayMap = new Map();
  for (const p of settled) {
    const d = p.date || 'unknown';
    if (!byDayMap.has(d)) byDayMap.set(d, { date:d, total:0, correct:0, wrong:0, profit:0 });
    const row = byDayMap.get(d);
    row.total++; if (p.correct) row.correct++; else row.wrong++;
    row.profit = money(row.profit + Number(p.profit100 || 0));
  }
  const byDay = Array.from(byDayMap.values()).sort((a,b)=>String(a.date).localeCompare(String(b.date)));
  let cumulative = 0;
  const bankroll = byDay.map(d => { cumulative = money(cumulative + d.profit); return { date:d.date, profit:d.profit, cumulative }; });

  const latest = [...predictions].sort((a,b)=>String(b.createdAt||b.date||'').localeCompare(String(a.createdAt||a.date||''))).slice(0,120);
  const valueBets = settled.filter(p => Number(p.oddsPick) > 1 && Number(p.edge) >= 0.05 && Number(p.confidence || p.currentConfidence) >= 0.60 && String(p.dataQuality || '') !== 'low');
  const valueStake = valueBets.length * 100;
  const valueProfit = valueStake ? money(valueBets.reduce((sum,p)=>sum+Number(p.profit100||0),0)) : null;
  const valueCorrect = valueBets.filter(p=>p.correct===true).length;
  const valueWrong = valueBets.length - valueCorrect;
  const valueAccuracy = valueBets.length ? Number((valueCorrect/valueBets.length).toFixed(4)) : null;
  const valueRoi = valueStake ? Number((valueProfit/valueStake).toFixed(4)) : null;
  const optimizedStrategy = valueBets.length ? { bets:valueBets.length, correct:valueCorrect, wrong:valueWrong, accuracy:valueAccuracy, stake:valueStake, profit:valueProfit, roi:valueRoi, edgeMin:0.05, confMin:0.60, rule:'live optimized uses backtest rule once enough data exists' } : { bets:0, correct:0, wrong:0, accuracy:null, stake:0, profit:null, roi:null, edgeMin:0.05, confMin:0.60, rule:'waiting for settled value bets' };
  return { totalPredictions:predictions.length, totalRuns:runs.length, betCount, noBetCount, pending:pending.length, settled:settled.length, correct, wrong, accuracy, withOdds:withOdds.length, totalStake, profit, roi, valueStrategy:{ bets:valueBets.length, correct:valueCorrect, wrong:valueWrong, accuracy:valueAccuracy, stake:valueStake, profit:valueProfit, roi:valueRoi, rule:'edge >= 5%, confidence >= 60%, dataQuality != low' }, optimizedStrategy, pendingValue, calibration, byDay, bankroll, latest };
}


function diagnoseModel(model) {
  const warnings = [];
  const coeff = model?.coefficients || {};
  const metrics = model?.metrics || {};
  const players = model?.players || {};
  const trainedThrough = String(model?.trained_through || '');
  const lowSamplePlayers = Object.values(players).filter(p => Number(p.matches || 0) > 0 && Number(p.matches || 0) < 10).length;
  const totalPlayers = Object.keys(players).length;

  if (coeff.win_rate_diff < -0.25) warnings.push({ level:'high', code:'NEGATIVE_WIN_RATE_WEIGHT', message:'win_rate_diff ha peso negativo: il modello può penalizzare chi ha win-rate migliore. Da controllare feature engineering / orientamento winner-loser.' });
  if (coeff.return_strength_diff < -0.02) warnings.push({ level:'medium', code:'NEGATIVE_RETURN_WEIGHT', message:'return_strength_diff ha peso negativo: possibile proxy rumorosa o collinearità con altre feature.' });
  if (coeff.tiebreak_win_rate_diff < -0.10) warnings.push({ level:'medium', code:'NEGATIVE_TIEBREAK_WEIGHT', message:'tiebreak_win_rate_diff ha peso negativo: la feature può essere instabile o mal calibrata.' });
  if (Number(metrics.test_log_loss || 0) > 0.62) warnings.push({ level:'medium', code:'LOG_LOSS_NOT_PREMIUM', message:'Log loss discreto ma non ancora premium: serve migliorare calibrazione e value strategy.' });
  if (trainedThrough && trainedThrough < todayISO(-90)) warnings.push({ level:'medium', code:'MODEL_STALE', message:`Il modello è allenato fino a ${trainedThrough}: per partite live 2026 va aggiornato con dati recenti.` });
  if (totalPlayers && lowSamplePlayers / totalPlayers > 0.20) warnings.push({ level:'medium', code:'LOW_SAMPLE_PLAYERS', message:`Molti player hanno poche partite nello storico (${lowSamplePlayers}/${totalPlayers}). Serve data-depth gate più severo.` });

  const buckets = Array.isArray(model?.confidence_buckets) ? model.confidence_buckets : [];
  const bucketNotes = buckets.map(b => ({ bucket:b.bucket, n:b.n, accuracy:b.accuracy, avgConfidence:b.avg_confidence, calibrationGap: b.accuracy == null || b.avg_confidence == null ? null : Number((Number(b.accuracy)-Number(b.avg_confidence)).toFixed(4)) }));
  return { warnings, bucketNotes, lowSamplePlayers, totalPlayers };
}

async function handleModel(env, url) {
  const model = await loadOfflineModel(env, url);
  if (!model) return json({ ok:true, available:false, modelActive:false, message:'model.json allenato non trovato: le raccomandazioni BET restano disattivate. Esegui training/train_model.py per attivare GreenCode offline.' });
  return json({
    ok:true,
    available:true,
    modelActive:true,
    version:model.version || '',
    dataSource:model.data_source || '',
    dataSources:model.data_sources || [],
    trainedThrough:model.trained_through || '',
    trainingRows:model.training_rows || 0,
    testRows:model.test_rows || 0,
    features:model.features || [],
    coefficients:model.coefficients || {},
    metrics:model.metrics || {},
    topFeatures:model.top_features || [],
    players:Object.keys(model.players || {}).length,
    intelligence:model.intelligence || {},
    confidenceBuckets:model.confidence_buckets || [],
    diagnostics: diagnoseModel(model)
  });
}


function makeBetSignal(p, rule = {}) {
  const edgeMin = Number(rule.edgeMin ?? 0.05);
  const confMin = Number(rule.confMin ?? 0.60);
  const minOdds = Number(rule.minOdds ?? 1.25);
  const maxOdds = Number(rule.maxOdds ?? 3.25);
  const maxOverround = Number(rule.maxOverround ?? 1.10);
  const hasOdds = Number(p.oddsPick) >= minOdds && Number(p.oddsPick) <= maxOdds;
  const oddsClean = p.overround == null ? true : Number(p.overround) <= maxOverround;
  const enoughEdge = Number(p.edge) >= edgeMin;
  const enoughConfidence = Number(p.confidence) >= confMin;
  const qualityOk = ['high','medium'].includes(String(p.dataQuality || '')) && Number(p.dataDepthScore || p.features?.dataDepthScore || 0) >= Number(rule.minDataDepth ?? 50) && p.features?.p1InModel === true && p.features?.p2InModel === true;
  const trainedModel = String(p.modelType || '').includes('greencode-offline-model-json');
  const positiveEV = Number(p.expectedProfit100) > 0;

  const marketDisagreementNeedsExtraEdge = p.modelAgainstBook === true && Number(p.edge) < edgeMin + 0.04;
  const bet = trainedModel && hasOdds && oddsClean && enoughEdge && enoughConfidence && qualityOk && positiveEV && !marketDisagreementNeedsExtraEdge;
  let reason = 'BET 100€';
  if (!trainedModel) reason = 'NO BET: modello GreenCode non allenato';
  else if (!qualityOk) reason = 'NO BET: profondità dati insufficiente';
  else if (!hasOdds) reason = 'NO BET: quota fuori range o assente';
  else if (!oddsClean) reason = 'NO BET: margine bookmaker troppo alto';
  else if (!positiveEV) reason = 'NO BET: EV non positivo';
  else if (!enoughEdge) reason = `NO BET: edge sotto ${Math.round(edgeMin*100)}%`;
  else if (!enoughConfidence) reason = `NO BET: confidence sotto ${Math.round(confMin*100)}%`;
  else if (marketDisagreementNeedsExtraEdge) reason = 'NO BET: contro il favorito bookmaker serve edge extra';

  return {
    bet,
    label: bet ? 'BET' : 'NO BET',
    reason,
    stake: bet ? 100 : 0,
    edgeMin,
    confMin,
    minOdds,
    maxOdds,
    maxOverround,
    minDataDepth: Number(rule.minDataDepth ?? 50),
    trainedModel,
    positiveEV
  };
}

async function handleFuture(env, url) {
  const start = todayISO(0), stop = todayISO(Number(env.FUTURE_DAYS || 60));
  const offlineModel = await loadOfflineModel(env, url);
  const trainingDays = Number(env.TRAINING_DAYS || 180);
  const hist = offlineModel ? { fixtures: [] } : await fetchFixturesRange(env, todayISO(-trainingDays), todayISO(-1), { useEventType: true, mode: 'model', allowLooseFallback: true });
  const modelState = offlineModel ? createModelState() : buildModelState(hist.fixtures || []);
  const api = await fetchFixturesRange(env, start, stop, { useEventType: true });
  const fixtures = api.fixtures.slice(0, 60);
  const oddsInfo = await fetchOddsMap(env, start, stop);
  const predictions = [];
  const saveResults = [];
  const maxPerMatchOdds = Number(env.MAX_ODDS_MATCH_CALLS || 20);
  for (let i = 0; i < fixtures.length; i++) {
    const f = fixtures[i];
    let odds = oddsInfo.ok ? extractOddsForMatch({ result: oddsInfo.oddsByMatch }, f.sourceMatchId) : null;
    if (!odds && i < maxPerMatchOdds) {
      const check = await fetchOddsForMatch(env, f.sourceMatchId);
      if (check.ok) odds = check.odds;
    }
    const p = predictFixture(f, env, odds, modelState, offlineModel);
    p.betSignal = makeBetSignal(p, { edgeMin:Number(env.EDGE_MIN || 0.05), confMin:Number(env.CONF_MIN || 0.60), minOdds:Number(env.MIN_ODDS || 1.25), maxOdds:Number(env.MAX_ODDS || 3.25), maxOverround:Number(env.MAX_OVERROUND || 1.10) });
    predictions.push(p);
    try { saveResults.push(await savePrediction(env, p)); } catch (e) { saveResults.push({ id:p.id, error:String(e.message || e) }); }
  }
  const withOdds = predictions.filter(p => Number(p.oddsPick) > 1).length;
  const oddsStatus = withOdds > 0 ? 'ok' : (oddsInfo.ok ? 'no-odds-for-fixtures' : 'api-error');
  return json({
    ok:true,
    mode:api.source,
    realOnly:true,
    generatedAt:new Date().toISOString(),
    refreshSeconds:30,
    warning: api.warning || '',
    api: { source: api.source, countRaw: api.rawCount || 0, normalizedCount: api.normalizedCount || 0, filteredAtp250Plus: fixtures.length },
    model: {
      type: offlineModel ? 'greencode-offline-model-json' : 'online-elo-fallback',
      modelActive: !!offlineModel,
      trainedThrough: offlineModel?.trained_through || null,
      trainingRows: offlineModel?.training_rows || null,
      testAccuracy: offlineModel?.metrics?.test_accuracy ?? null,
      testLogLoss: offlineModel?.metrics?.test_log_loss ?? null,
      matchesUsed:modelState.matchesUsed,
      players: offlineModel ? Object.keys(offlineModel.players || {}).length : Object.keys(modelState.players).length
    },
    odds: { enabled: String(env.ENABLE_ODDS || 'true').toLowerCase() !== 'false', status: oddsStatus, ok: oddsInfo.ok, reason: oddsInfo.reason || '', withOdds, totalMatches: predictions.length },
    predictions,
    saveResults,
    tracking: { attempted: saveResults.length, saved: saveResults.filter(x=>x.saved).length, updated: saveResults.filter(x=>x.updated).length, errors: saveResults.filter(x=>x.error).length, skipped: saveResults.filter(x=>x.skipped).length, lastError: (saveResults.find(x=>x.error)||{}).error || '' }
  });
}

async function handleOdds(env, url) {
  const matchKey = url.searchParams.get('match_key') || url.searchParams.get('matchKey');
  if (matchKey) {
    const check = await fetchOddsForMatch(env, matchKey);
    return json({ ok: check.ok, mode:'match-key', matchKey, reason:check.reason || '', odds:check.odds });
  }
  const days = Number(url.searchParams.get('days') || env.FUTURE_DAYS || 60);
  const start = todayISO(0), stop = todayISO(days);
  const oddsInfo = await fetchOddsMap(env, start, stop);
  const sampleKeys = Object.keys(oddsInfo.oddsByMatch || {}).slice(0, 8);
  return json({ ok: oddsInfo.ok, mode:'date-range', start, stop, reason: oddsInfo.reason || '', sampleKeys, sample: sampleKeys.map(k => ({ matchKey:k, odds: oddsInfo.oddsByMatch[k] })) });
}


async function handlePredictions(env, url) {
  const limit = Math.min(1000, Number(url.searchParams.get('limit') || 300));
  const status = String(url.searchParams.get('status') || '').toLowerCase();
  const decision = String(url.searchParams.get('decision') || '').toUpperCase();
  let rows = [];
  try {
    rows = await listDocs(env, 'predictions', limit);
  } catch (e) {
    return json({ ok:false, error:'Impossibile leggere prediction registry da Firebase', detail:String(e.message || e) }, 500);
  }
  rows = rows.filter(p => !isDemoOrBrokenPrediction(p));
  if (status) rows = rows.filter(p => String(p.status || 'pending').toLowerCase() === status);
  if (decision) rows = rows.filter(p => String(p.decision || p.betSignal?.label || '').toUpperCase() === decision);
  rows.sort((a,b)=>String(b.createdAt || b.date || '').localeCompare(String(a.createdAt || a.date || '')));
  return json({ ok:true, generatedAt:new Date().toISOString(), count:rows.length, predictions:rows });
}

async function handleTrackingCheck(env, url) {
  const debug = String(url.searchParams.get('debug') || '').toLowerCase() === '1';
  const now = new Date().toISOString();
  const id = safeId(`tracking-check-${now.slice(0,19)}`);
  try {
    await setDoc(env, 'systemChecks', id, { type:'tracking-check', ok:true, createdAt:now, workerVersion:'v22-live-tracking-diagnostic' });
    const readBack = await getDoc(env, 'systemChecks', id);
    return json({ ok:true, writable:true, readable:!!readBack, id, message:'Firebase tracking operativo: le previsioni possono essere salvate.' });
  } catch (e) {
    return json({ ok:false, writable:false, error:'Firebase tracking non scrivibile dal Worker', detail: debug ? String(e.message || e) : 'Apri /api/tracking-check?debug=1 per vedere il dettaglio. Controlla FIREBASE_PROJECT_ID, FIREBASE_API_KEY e Firestore rules.' }, 500);
  }
}

async function handleSettle(env, url = new URL('https://local/')) {
  const debug = String(url.searchParams?.get('debug') || '').toLowerCase() === '1';
  let predictions = [];
  const updates = [];

  try {
    predictions = await listDocs(env, 'predictions', 900);
  } catch (e) {
    return json({
      ok: true,
      generatedAt: new Date().toISOString(),
      checked: 0,
      updated: 0,
      warning: 'Impossibile leggere il registro previsioni da Firebase. Il sito resta online, ma i risultati non sono stati aggiornati.',
      firebaseError: debug ? String(e.message || e) : 'firebase-read-error'
    });
  }

  const pending = predictions.filter(p => p.status !== 'settled' && !isDemoOrBrokenPrediction(p));
  const today = todayISO(0);
  const due = pending.filter(p => String(p.date || '') <= today).slice(0, 160);
  const byDate = new Map();

  for (const p of due) {
    if (!p.date) {
      updates.push({ id: p.id || '', status: 'missing-date' });
      continue;
    }
    if (!byDate.has(p.date)) byDate.set(p.date, []);
    byDate.get(p.date).push(p);
  }

  function playerPairKey(a, b) {
    return `${playerKey(a)}__${playerKey(b)}`;
  }

  for (const [date, items] of byDate.entries()) {
    let range = null;
    try {
      // Settlement must look at finished historical fixtures, not future strict-only fixtures.
      // Loose fallback is allowed here because we still match by stored match id / player names and never settle without a finished score.
      range = await fetchFixturesRange(env, addDaysISO(date, -1), addDaysISO(date, 1), {
        useEventType: true,
        mode: 'backtest',
        allowLooseFallback: true
      });
    } catch (e) {
      updates.push({ date, status: 'api-error', error: debug ? String(e.message || e) : 'api-results-error' });
      continue;
    }

    const fixtures = Array.isArray(range?.fixtures) ? range.fixtures : [];
    const rawByKey = new Map(fixtures.map(r => [String(r.sourceMatchId || r.matchId || r.id || ''), r]));
    const rawByPlayers = new Map();
    for (const r of fixtures) {
      rawByPlayers.set(playerPairKey(r.player1, r.player2), r);
      rawByPlayers.set(playerPairKey(r.player2, r.player1), r);
    }

    if (!fixtures.length) {
      updates.push({ date, status: 'no-finished-results', checked: items.length, apiRaw: range?.rawCount || 0, apiFiltered: 0 });
      continue;
    }

    for (const p of items) {
      try {
        const key = String(p.sourceMatchId || p.matchId || '');
        const r = rawByKey.get(key) || rawByPlayers.get(playerPairKey(p.player1, p.player2));
        if (!r) {
          updates.push({ id: p.id, status: 'not-found', date, player1: p.player1 || '', player2: p.player2 || '' });
          continue;
        }

        const actualWinner = resolveWinner(r, p);
        if (!actualWinner) {
          updates.push({ id: p.id, status: 'not-finished', finalResult: r.finalResult || '', rawStatus: r.rawStatus || '' });
          continue;
        }

        const pick = p.predictedWinner || p.currentPredictedWinner || '';
        const correct = actualWinner === pick;
        const odds = Number(p.oddsPick || 0);
        const stake = Number(p.stake || p.betSignal?.stake || 100);
        const isBet = String(p.betSignal?.label || p.decision || '').toUpperCase() === 'BET' || Number(p.stake || 0) > 0;
        const profit100 = odds > 1 ? (correct ? money((odds - 1) * stake) : -stake) : null;

        const settled = {
          ...p,
          status: 'settled',
          settledAt: new Date().toISOString(),
          actualWinner,
          correct,
          finalResult: r.finalResult || '',
          rawStatus: r.rawStatus || '',
          profit100,
          settledStake: stake,
          settledBet: isBet
        };

        await setDoc(env, 'predictions', safeId(p.id), settled);
        updates.push({ id: p.id, status: 'settled', actualWinner, correct, profit100 });
      } catch (e) {
        updates.push({ id: p.id || '', status: 'settle-write-error', error: debug ? String(e.message || e) : 'firebase-write-error' });
      }
    }
  }

  return json({
    ok: true,
    generatedAt: new Date().toISOString(),
    checked: due.length,
    updated: updates.filter(u => u.status === 'settled').length,
    pending: pending.length,
    updates,
    note: 'Settlement robusto: nessuna partita viene chiusa senza winner reale o score conclusivo.'
  });
}

async function handleStats(env) {
  const predictionsRaw = await listDocs(env,'predictions',900).catch(() => []);
  const runs = await listDocs(env,'predictionRuns',900).catch(() => []);
  const excludedDemoOrBroken = predictionsRaw.filter(isDemoOrBrokenPrediction).length;
  const predictions = predictionsRaw.filter(p => !isDemoOrBrokenPrediction(p));
  return json({ ok:true, generatedAt:new Date().toISOString(), excludedDemoOrBroken, ...computeStats(predictions, runs) });
}


async function handleCleanupDemo(env) {
  const predictions = await listDocs(env,'predictions',1000).catch(() => []);
  const bad = predictions.filter(isDemoOrBrokenPrediction);
  const results = [];
  for (const p of bad) {
    try {
      await deleteDoc(env, 'predictions', p.id);
      results.push({ id:p.id, deleted:true, tournament:p.tournament || '', player1:p.player1 || '', player2:p.player2 || '' });
    } catch (e) {
      results.push({ id:p.id, deleted:false, error:String(e.message || e) });
    }
  }
  return json({ ok:true, deleted:results.filter(r=>r.deleted).length, checked:predictions.length, results });
}

function buildBacktestStats(rows) {
  const settled = rows.filter(r => r.actualWinner);
  const correct = settled.filter(r => r.correct).length;
  const wrong = settled.length - correct;
  const accuracy = settled.length ? Number((correct / settled.length).toFixed(4)) : null;

  function plFor(row, odds, winnerName) {
    if (!(Number(odds) > 1)) return null;
    return row.actualWinner === winnerName ? money((Number(odds) - 1) * 100) : -100;
  }

  const withOddsRows = settled.filter(r => Number(r.oddsPick) > 1);
  const totalStake = withOddsRows.length * 100;
  const profit = totalStake ? money(withOddsRows.reduce((sum, r) => sum + Number(r.profit100 || 0), 0)) : null;
  const roi = totalStake ? Number((profit / totalStake).toFixed(4)) : null;

  const bookmakerRows = settled.filter(r => Number(r.bookmakerFavoriteOdds) > 1 && r.bookmakerFavorite);
  const bookmakerStake = bookmakerRows.length * 100;
  const bookmakerCorrect = bookmakerRows.filter(r => r.actualWinner === r.bookmakerFavorite).length;
  const bookmakerProfit = bookmakerStake ? money(bookmakerRows.reduce((sum,r)=>sum + Number(plFor(r, r.bookmakerFavoriteOdds, r.bookmakerFavorite) || 0),0)) : null;
  const bookmakerRoi = bookmakerStake ? Number((bookmakerProfit / bookmakerStake).toFixed(4)) : null;
  const bookmakerAccuracy = bookmakerRows.length ? Number((bookmakerCorrect / bookmakerRows.length).toFixed(4)) : null;

  const invertedRows = withOddsRows.filter(r => Number(r.oddsOther) > 1);
  const invertedStake = invertedRows.length * 100;
  const invertedProfit = invertedStake ? money(invertedRows.reduce((sum,r)=>sum + Number(plFor(r, r.oddsOther, r.predictedWinner) || 0),0)) : null;
  const invertedRoi = invertedStake ? Number((invertedProfit / invertedStake).toFixed(4)) : null;

  const buckets = ['50-60%','60-70%','70-80%','80-90%','90-100%'].map(label=>({label,total:0,correct:0,accuracy:null}));
  for (const r of settled) {
    const c = Number(r.confidence || .5);
    const idx = Math.min(4, Math.max(0, Math.floor((c-.5)/.1)));
    buckets[idx].total++; if (r.correct) buckets[idx].correct++;
  }
  buckets.forEach(b => { b.accuracy = b.total ? Number((b.correct / b.total).toFixed(3)) : null; });

  function strategyFor(edgeMin, confMin, minOdds = 1.25, maxOdds = 3.25, maxOverround = 1.10, requireAgainstBook = false) {
    const bets = settled.filter(r =>
      Number(r.oddsPick) >= minOdds &&
      Number(r.oddsPick) <= maxOdds &&
      Number(r.edge) >= edgeMin &&
      Number(r.confidence) >= confMin &&
      Number(r.expectedProfit100) > 0 &&
      (r.overround == null || Number(r.overround) <= maxOverround) &&
      String(r.modelType || '').includes('greencode-offline-model-json') &&
      String(r.dataQuality || '') !== 'low' &&
      r.features?.p1InModel === true &&
      r.features?.p2InModel === true &&
      (!requireAgainstBook || r.modelAgainstBook === true)
    );
    const stake = bets.length * 100;
    const profit = stake ? money(bets.reduce((sum, r) => sum + Number(r.profit100 || 0), 0)) : null;
    const correct = bets.filter(r => r.correct).length;
    const wrong = bets.length - correct;
    const accuracy = bets.length ? Number((correct / bets.length).toFixed(4)) : null;
    const roi = stake ? Number((profit / stake).toFixed(4)) : null;
    const avgOdds = bets.length ? Number((bets.reduce((s,r)=>s+Number(r.oddsPick||0),0)/bets.length).toFixed(3)) : null;
    return { bets:bets.length, correct, wrong, accuracy, stake, profit, roi, edgeMin, confMin, minOdds, maxOdds, maxOverround, requireAgainstBook, avgOdds };
  }

  const valueStrategy = strategyFor(0.05, 0.60, 1.25, 3.25, 1.10, false);
  const againstBookStrategy = strategyFor(0.08, 0.62, 1.40, 3.50, 1.10, true);

  // GreenCode validation scan: maximize ROI but keep minimum sample size and no overfit one-match rules.
  const candidates = [];
  const minBets = Math.max(10, Math.ceil(settled.length * 0.05));
  for (const edgeMin of [0.05,0.07,0.10,0.12]) {
    for (const confMin of [0.60,0.63,0.66,0.70]) {
      for (const minOdds of [1.25,1.35,1.50]) {
        for (const maxOdds of [2.2, 2.8, 3.25]) {
          for (const requireAgainstBook of [false, true]) {
            const s = strategyFor(edgeMin, confMin, minOdds, maxOdds, 1.10, requireAgainstBook);
            if (s.bets >= minBets) candidates.push(s);
          }
        }
      }
    }
  }
  const optimizedStrategy = candidates.length
    ? candidates.sort((a,b) => (Number(b.roi ?? -999) - Number(a.roi ?? -999)) || (Number(b.profit ?? -999) - Number(a.profit ?? -999)))[0]
    : strategyFor(0.05, 0.60, 1.25, 3.25, 1.10, false);

  const diagnostics = {
    modelRows: settled.filter(r => String(r.modelType || '').includes('greencode-offline-model-json')).length,
    rowsWithOdds: withOddsRows.length,
    bookmakerFavoriteAccuracy: bookmakerAccuracy,
    bookmakerFavoriteProfit: bookmakerProfit,
    bookmakerFavoriteRoi: bookmakerRoi,
    invertedPickProfit: invertedProfit,
    invertedPickRoi: invertedRoi,
    oddsInversionSuspected: invertedStake && totalStake ? (Number(invertedProfit) - Number(profit) > Math.max(500, Math.abs(Number(profit))*0.5)) : false,
    modelBeatsBookmakerAccuracy: accuracy != null && bookmakerAccuracy != null ? accuracy > bookmakerAccuracy : null,
    modelBeatsBookmakerROI: roi != null && bookmakerRoi != null ? roi > bookmakerRoi : null,
    note: 'If invertedPickROI is much better than model ROI, odds side mapping may be wrong.'
  };

  const byDayMap = new Map();
  for (const r of settled) {
    if (!byDayMap.has(r.date)) byDayMap.set(r.date, { date: r.date, correct:0, wrong:0, profit:0, total:0 });
    const d = byDayMap.get(r.date);
    d.total++; if (r.correct) d.correct++; else d.wrong++;
    d.profit = money(d.profit + Number(r.profit100 || 0));
  }
  const byDay = Array.from(byDayMap.values()).sort((a,b)=>String(a.date).localeCompare(String(b.date)));
  let cumulative = 0;
  const bankroll = byDay.map(d => { cumulative = money(cumulative + d.profit); return { date:d.date, profit:d.profit, cumulative }; });
  return {
    total: settled.length,
    correct,
    wrong,
    accuracy,
    withOdds: withOddsRows.length,
    totalStake,
    profit,
    roi,
    bookmakerBaseline: { bets:bookmakerRows.length, correct:bookmakerCorrect, accuracy:bookmakerAccuracy, stake:bookmakerStake, profit:bookmakerProfit, roi:bookmakerRoi },
    invertedOddsCheck: { bets:invertedRows.length, stake:invertedStake, profit:invertedProfit, roi:invertedRoi },
    valueStrategy:{ ...valueStrategy, rule:'GreenCode value: EV>0, edge>=5%, odds 1.25-3.25, overround<=10%, trained players only' },
    againstBookStrategy:{ ...againstBookStrategy, rule:'Model disagreement strategy: model picks against bookmaker favorite with value edge' },
    optimizedStrategy:{ ...optimizedStrategy, rule:'auto-selected on backtest: max ROI subject to minimum bet count and GreenCode filters' },
    diagnostics,
    calibration: buckets,
    byDay,
    bankroll
  };
}

async function handleBacktest(env, url) {
  const days = Math.max(1, Math.min(180, Number(url.searchParams.get('days') || env.BACKTEST_DAYS || 20)));
  const allowLooseFallback = String(url.searchParams.get('fallback') || env.ALLOW_BACKTEST_FALLBACK || 'false').toLowerCase() === 'true';
  const start = todayISO(-days);
  const stop = todayISO(-1);

  const offlineModel = await loadOfflineModel(env, url);
  const api = await fetchFixturesRange(env, start, stop, { useEventType: true, mode: 'backtest', allowLooseFallback });
  const fixtures = sortFixtures(api.fixtures || []);
  const oddsInfo = await fetchOddsMap(env, start, stop);
  const rows = [];
  const fallbackState = createModelState();
  const maxPerMatchOdds = Number(env.MAX_ODDS_MATCH_CALLS || 60);

  let strictTestCandidates = 0;
  let looseTestCandidates = 0;

  for (let i = 0; i < fixtures.length; i++) {
    const f = fixtures[i];
    const isStrict = isAtp250Plus(f._raw || f, f);
    const isLoose = isAtpSinglesLoose(f._raw || f, f);
    if (isStrict) strictTestCandidates++;
    if (isLoose) looseTestCandidates++;

    let odds = oddsInfo.ok ? extractOddsForMatch({ result: oddsInfo.oddsByMatch }, f.sourceMatchId) : null;
    if (!odds && i < maxPerMatchOdds) {
      const check = await fetchOddsForMatch(env, f.sourceMatchId);
      if (check.ok) odds = check.odds;
    }

    const p = predictFixture(f, env, odds, fallbackState, offlineModel);
    p.betSignal = makeBetSignal(p, { edgeMin:Number(env.EDGE_MIN || 0.05), confMin:Number(env.CONF_MIN || 0.60), minOdds:Number(env.MIN_ODDS || 1.25), maxOdds:Number(env.MAX_ODDS || 3.25), maxOverround:Number(env.MAX_OVERROUND || 1.10) });
    const actualWinner = resolveWinner(f, p);
    if (actualWinner) {
      const correct = actualWinner === p.predictedWinner;
      const profit100 = Number(p.oddsPick) > 1 ? (correct ? money((Number(p.oddsPick) - 1) * 100) : -100) : null;
      rows.push({ ...p, status:'settled', actualWinner, correct, finalResult: f.finalResult || '', profit100 });
    }
  }

  const stats = buildBacktestStats(rows);
  return json({
    ok:true,
    generatedAt:new Date().toISOString(),
    mode:'backtest',
    days,
    range:{ start, stop },
    warning: rows.length ? '' : 'Nessuna partita testabile trovata nel range.',
    filterMode: allowLooseFallback ? 'atp-singles-loose-fallback' : 'atp-main-tour',
    dataQuality: {
      strictTourOnly: !allowLooseFallback,
      looseFallbackAllowed: allowLooseFallback,
      noLookAhead: true,
      model: offlineModel ? 'trained GreenCode model.json' : 'fallback model only - backtest not validated',
      modelActive: !!offlineModel,
      trainedThrough: offlineModel?.trained_through || null,
      trainingRows: offlineModel?.training_rows || null,
      testAccuracy: offlineModel?.metrics?.test_accuracy ?? null,
      note: offlineModel ? 'Backtest uses uploaded GreenCode model.json. Betting diagnostics compare model ROI, bookmaker favorite ROI and inverted-odds check.' : 'model.json trained not active: results are not GreenCode validated.'
    },
    fixturesScanned: fixtures.length,
    api: {
      countRaw: api.rawCount || 0,
      normalizedCount: api.normalizedCount || 0,
      strictTestCandidates,
      looseAtpSingles: looseTestCandidates,
      selected: fixtures.length
    },
    oddsCoverage: { withOdds: stats.withOdds, total: stats.total },
    stats,
    rows: rows.sort((a,b)=>String(b.date).localeCompare(String(a.date))).slice(0,400)
  });
}

async function handleValidate(env, url) {
  const bt = await (await handleBacktest(env, url)).json().catch(() => null);
  if (!bt) return json({ ok:false, error:'validation failed' }, 500);
  const s = bt.stats || {};
  return json({
    ok:true,
    generatedAt:new Date().toISOString(),
    modelActive: bt.dataQuality?.modelActive || false,
    model: bt.dataQuality || {},
    diagnostics: s.diagnostics || {},
    summary: {
      modelAccuracy: s.accuracy,
      modelROI: s.roi,
      bookmakerAccuracy: s.bookmakerBaseline?.accuracy,
      bookmakerROI: s.bookmakerBaseline?.roi,
      valueROI: s.valueStrategy?.roi,
      optimizedROI: s.optimizedStrategy?.roi,
      invertedOddsROI: s.invertedOddsCheck?.roi
    },
    verdict: !bt.dataQuality?.modelActive ? 'NOT_VALIDATED_MODEL_INACTIVE'
      : s.diagnostics?.oddsInversionSuspected ? 'CHECK_ODDS_MAPPING'
      : (s.optimizedStrategy?.roi != null && s.optimizedStrategy.roi > 0 ? 'VALUE_RULE_POSITIVE' : 'MODEL_OR_ODDS_NOT_PROFITABLE_YET')
  });
}

