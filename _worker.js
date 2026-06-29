/* Algohns V11
 * Cloudflare Worker for an Alpaca Paper Quant Asset Manager OS.
 * Real-money execution is locked by design: only https://paper-api.alpaca.markets is accepted.
 */

const APP_VERSION = '11.0.0';
const APP_BUILD = 'algohns-v11-alpaca-core-2026-06-14';
const PAPER_BASE = 'https://paper-api.alpaca.markets';
const DATA_BASE = 'https://data.alpaca.markets';
const MAX_BATCH_SYMBOLS = 200;
const MAX_ORDER_BATCH = 10;
const OPERATIONAL_UNIVERSE_CAP = 600;
const SNAPSHOT_CAP = 220;

const STRATEGIES = {
  Defensive: { cashFloor: 0.30, maxPosition: 0.055, gross: 0.70, turnover: 0.18, hedgeBudget: 0.08, l1: 0.70, l2: 0.72, killDD: -0.08, slots: 14 },
  Balanced: { cashFloor: 0.18, maxPosition: 0.075, gross: 0.82, turnover: 0.25, hedgeBudget: 0.05, l1: 0.86, l2: 0.60, killDD: -0.12, slots: 16 },
  Advanced: { cashFloor: 0.10, maxPosition: 0.095, gross: 0.92, turnover: 0.34, hedgeBudget: 0.03, l1: 1.00, l2: 0.48, killDD: -0.16, slots: 18 },
  Aggressive: { cashFloor: 0.04, maxPosition: 0.125, gross: 0.98, turnover: 0.48, hedgeBudget: 0.00, l1: 1.18, l2: 0.35, killDD: -0.22, slots: 20 }
};

const ENGINE_STATE_KEY = 'engine:state';
const ENGINE_JOURNAL_KEY = 'engine:journal';

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') return cors(new Response(null, { status: 204 }));

    try {
      if (url.pathname.startsWith('/api/')) {
        return cors(await routeApi(request, env, url));
      }
      return env.ASSETS ? env.ASSETS.fetch(request) : new Response('Algohns assets binding missing.', { status: 500 });
    } catch (err) {
      return cors(json({ error: 'Worker failure', detail: String(err && err.message ? err.message : err), build: APP_BUILD }, 500));
    }
  },

  // Cloudflare Cron Trigger entry point. Runs the Strategy Engine on the edge
  // on the schedule declared in wrangler.toml, independent of the browser UI.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runEngineOnce(env, 'cron'));
  }
};

async function routeApi(request, env, url) {
  const pathname = url.pathname.replace(/\/$/, '') || '/';

  if (pathname === '/api/health') {
    return json({
      ok: true,
      app: 'Algohns',
      version: APP_VERSION,
      build: APP_BUILD,
      url: 'https://algohns.dreanquero.workers.dev',
      broker: 'Alpaca Paper',
      realMoney: 'locked',
      timestamp: new Date().toISOString()
    });
  }

  if (pathname === '/api/config') {
    const cfg = alpacaCfg(env);
    return json({
      realMoney: 'locked',
      alpaca: { configured: hasAlpaca(env), base: cfg.base, mode: cfg.isPaper ? 'paper' : 'blocked' },
      build: APP_BUILD
    });
  }

  if (pathname === '/api/broker/alpaca/status') return alpacaStatus(env);
  if (pathname === '/api/broker/alpaca/account') return alpacaProxy('/v2/account', env);
  if (pathname === '/api/broker/alpaca/positions' && request.method === 'GET') return alpacaProxy('/v2/positions', env);
  if (pathname === '/api/broker/alpaca/orders' && request.method === 'GET') return alpacaProxy('/v2/orders?status=all&limit=100&direction=desc', env);
  if (pathname === '/api/broker/alpaca/orders' && request.method === 'POST') return alpacaSubmitOrders(request, env);
  if (pathname === '/api/broker/alpaca/orders' && request.method === 'DELETE') return alpacaCancelAll(env);
  if (pathname === '/api/broker/alpaca/position' && request.method === 'DELETE') return alpacaClosePosition(url, env);
  if (pathname === '/api/broker/alpaca/assets') return alpacaAssets(url, env);
  if (pathname === '/api/broker/alpaca/clock') return alpacaProxy('/v2/clock', env);
  if (pathname === '/api/broker/alpaca/history') {
    const period = url.searchParams.get('period') || '3M';
    const timeframe = url.searchParams.get('timeframe') || '1D';
    return alpacaProxy(`/v2/account/portfolio/history?period=${enc(period)}&timeframe=${enc(timeframe)}`, env);
  }

  if (pathname === '/api/market/snapshots') return alpacaSnapshots(url, env);
  if (pathname === '/api/market/bars') return alpacaBars(url, env);
  if (pathname === '/api/market/news') return alpacaNews(url, env);

  if (pathname === '/api/engine/state' && request.method === 'GET') return json(await readEngineState(env));
  if (pathname === '/api/engine/state' && request.method === 'POST') return engineControl(request, env);
  if (pathname === '/api/engine/journal' && request.method === 'GET') return json({ journal: await readEngineJournal(env) });
  if (pathname === '/api/engine/run' && request.method === 'POST') return engineRunNow(env);

  return json({ error: 'Unknown API route', path: pathname, build: APP_BUILD }, 404);
}

function defaultEngineState() {
  return {
    running: false,
    killed: false,
    strategy: 'Balanced',
    dataFeed: 'iex',
    universeAssetClass: 'us_equity',
    customSymbols: '',
    minPrice: 3,
    minDollarVolume: 5000000,
    maxOrders: 8,
    paperOrderCap: 10,
    exclusions: [],
    regime: null,
    voting: [],
    targets: [],
    orders: [],
    lastDecision: null,
    lastRunAt: null,
    lastRunSource: null,
    lastRunNote: null
  };
}

async function readEngineState(env) {
  if (!env.ALGOHNS_KV) return { ...defaultEngineState(), error: 'ALGOHNS_KV binding not configured' };
  const raw = await env.ALGOHNS_KV.get(ENGINE_STATE_KEY);
  if (!raw) return defaultEngineState();
  try { return { ...defaultEngineState(), ...JSON.parse(raw) }; } catch (_) { return defaultEngineState(); }
}

async function writeEngineState(env, patch) {
  if (!env.ALGOHNS_KV) return;
  const current = await readEngineState(env);
  const next = { ...current, ...patch };
  await env.ALGOHNS_KV.put(ENGINE_STATE_KEY, JSON.stringify(next));
  return next;
}

async function readEngineJournal(env) {
  if (!env.ALGOHNS_KV) return [];
  const raw = await env.ALGOHNS_KV.get(ENGINE_JOURNAL_KEY);
  if (!raw) return [];
  try { return JSON.parse(raw); } catch (_) { return []; }
}

async function pushEngineJournal(env, entries) {
  if (!env.ALGOHNS_KV || !entries.length) return;
  const current = await readEngineJournal(env);
  const next = entries.concat(current).slice(0, 500);
  await env.ALGOHNS_KV.put(ENGINE_JOURNAL_KEY, JSON.stringify(next));
}

async function engineControl(request, env) {
  if (!env.ALGOHNS_KV) return json({ error: 'ALGOHNS_KV binding not configured. See wrangler.toml.', code: 'NO_KV' }, 412);
  const payload = await safeJson(request);
  const action = String(payload.action || '').toLowerCase();
  const patch = {};
  if (payload.config && typeof payload.config === 'object') {
    const allowed = ['strategy', 'dataFeed', 'universeAssetClass', 'customSymbols', 'minPrice', 'minDollarVolume', 'maxOrders', 'paperOrderCap', 'exclusions'];
    for (const k of allowed) if (k in payload.config) patch[k] = payload.config[k];
  }
  if (action === 'play') { patch.running = true; patch.killed = false; }
  else if (action === 'pause') { patch.running = false; }
  else if (action === 'kill') {
    patch.running = false;
    patch.killed = true;
    const guard = guardAlpaca(env);
    if (!guard) await alpacaFetch('/v2/orders', env, { method: 'DELETE' });
  }
  const next = await writeEngineState(env, patch);
  return json(next);
}

async function engineRunNow(env) {
  const result = await runEngineOnce(env, 'manual');
  return json(result);
}

// Ports the Strategy Studio engine (universe, regime, scoring, sizing, paper
// order submission) from the frontend so it can run unattended on a Cron
// Trigger, on the edge, without any browser tab open.
async function runEngineOnce(env, source) {
  const now = new Date().toISOString();
  const state = await readEngineState(env);
  if (!hasAlpaca(env) || !alpacaCfg(env).isPaper) {
    return writeEngineState(env, { lastRunAt: now, lastRunSource: source, lastRunNote: 'Skipped: Alpaca Paper not configured.' });
  }
  if (state.killed) {
    return writeEngineState(env, { lastRunAt: now, lastRunSource: source, lastRunNote: 'Skipped: Kill Switch is active.' });
  }

  const strat = STRATEGIES[state.strategy] || STRATEGIES.Balanced;
  let note = '';
  try {
    const clockRes = await alpacaFetch('/v2/clock', env);
    const clock = await readBody(clockRes);
    const marketOpen = !!(clock && clock.is_open);

    const universe = await buildEngineUniverse(env, state);
    const regime = await computeRegime(env, state);
    const ranked = universe
      .map(a => scoreAssetForStrategy(a, strat))
      .filter(a => !(state.exclusions || []).includes(a.symbol))
      .sort((a, b) => b.finalScore - a.finalScore);
    const selected = ranked.slice(0, strat.slots);
    const gross = Math.min(strat.gross, 1 - strat.cashFloor);
    const totalScore = selected.reduce((s, a) => s + Math.max(1, a.finalScore), 0) || 1;
    let targets = selected.map(a => ({ symbol: a.symbol, name: a.name, targetWeight: Math.min(strat.maxPosition, gross * Math.max(1, a.finalScore) / totalScore), score: a.finalScore, reason: a.strategyReason }));
    const allocated = targets.reduce((s, t) => s + t.targetWeight, 0);
    if (allocated > gross) targets = targets.map(t => ({ ...t, targetWeight: t.targetWeight * gross / allocated }));

    const accountRes = await alpacaFetch('/v2/account', env);
    const account = await readBody(accountRes);
    const positionsRes = await alpacaFetch('/v2/positions', env);
    const positions = await readBody(positionsRes);
    const orders = buildOrdersFromTargets(targets, account, Array.isArray(positions) ? positions : [], state);
    const lastDecision = buildDecisionNarrative(targets, regime, state.strategy);

    const journalEntries = [];
    if (state.running && marketOpen && orders.length) {
      const cap = Number(state.paperOrderCap || 10);
      for (const order of orders.slice(0, cap)) {
        const body = { symbol: order.symbol, side: order.side, type: 'market', time_in_force: 'day', notional: String(round(Math.max(1, order.notional), 2)) };
        const r = await alpacaFetch('/v2/orders', env, { method: 'POST', body: JSON.stringify(body) });
        const resBody = await readBody(r);
        journalEntries.push({
          time: now, broker: 'Alpaca Paper', symbol: order.symbol, side: order.side, notional: order.notional, qty: null,
          orderType: 'market', strategy: state.strategy, reason: order.reason, status: r.ok ? 'submitted' : 'rejected',
          detail: r.ok ? 'submitted to Alpaca Paper by autonomous engine' : extractError(resBody), httpStatus: r.status, source: 'autonomous-cron'
        });
        await sleep(250);
      }
      note = `Autonomous run: submitted ${journalEntries.length} order(s).`;
    } else if (!state.running) {
      note = 'Engine paused: targets computed, no orders submitted.';
    } else if (!marketOpen) {
      note = 'Market closed: targets computed, no orders submitted.';
    } else {
      note = 'No order deltas above minimum trade threshold.';
    }
    if (journalEntries.length) await pushEngineJournal(env, journalEntries);

    return writeEngineState(env, {
      regime, voting: regime ? regime.voting : [], targets, orders, lastDecision,
      lastRunAt: now, lastRunSource: source, lastRunNote: note
    });
  } catch (err) {
    return writeEngineState(env, { lastRunAt: now, lastRunSource: source, lastRunNote: `Run failed: ${String(err && err.message ? err.message : err)}` });
  }
}

async function buildEngineUniverse(env, state) {
  const r = await alpacaFetch(`/v2/assets?status=active&asset_class=${enc(state.universeAssetClass || 'us_equity')}`, env);
  const assets = await readBody(r);
  const custom = parseSymbols(state.customSymbols).map(symbol => ({ symbol, name: symbol + ' custom addition', exchange: 'Custom', asset_class: 'us_equity', tradable: true, status: 'active', custom: true }));
  const raw = [...(Array.isArray(assets) ? assets : []), ...custom];
  const bySymbol = new Map();
  raw.forEach(a => { if (a && a.symbol && !bySymbol.has(a.symbol)) bySymbol.set(a.symbol, a); });

  const included = Array.from(bySymbol.values()).map(a => {
    const symbol = String(a.symbol || '').toUpperCase();
    const name = a.name || symbol;
    const assetClass = a.asset_class || a.class || 'us_equity';
    const exchange = a.exchange || '—';
    const tradable = a.tradable !== false && a.status !== 'inactive';
    const nameBad = /warrant|unit|right|preferred|depositary|note due|acquisition corp/i.test(name);
    const symbolBad = /[.\^/=]|WS$|WT$|U$|R$/.test(symbol);
    const reasons = [];
    if (!tradable) reasons.push('not tradable');
    if (assetClass !== 'us_equity') reasons.push('not US equity');
    if (!['NASDAQ', 'NYSE', 'ARCA', 'AMEX', 'BATS', 'IEX', 'OTC'].includes(exchange)) reasons.push('unsupported exchange');
    if (exchange === 'OTC') reasons.push('OTC risk excluded');
    if (nameBad || symbolBad) reasons.push('structure/warrant/unit filter');
    return { symbol, name, exchange, assetClass, tradable, included: reasons.length === 0, rankScore: 50, dollarVolume: null, price: null };
  }).filter(a => a.included).slice(0, OPERATIONAL_UNIVERSE_CAP);

  const symbols = included.slice(0, SNAPSHOT_CAP).map(a => a.symbol);
  const snapshots = {};
  for (const chunk of chunkArray(symbols, 180)) {
    const sr = await alpacaDataFetch(`/v2/stocks/snapshots?symbols=${enc(chunk.join(','))}&feed=${enc(state.dataFeed || 'iex')}`, env);
    const sBody = await readBody(sr);
    if (sr.ok && sBody) Object.assign(snapshots, sBody);
  }

  const minPrice = Number(state.minPrice || 0);
  const minDv = Number(state.minDollarVolume || 0);
  return included.map(a => {
    const s = snapshots[a.symbol];
    if (!s) return a;
    const price = numOrNull(s.latestTrade && s.latestTrade.p) || numOrNull(s.dailyBar && s.dailyBar.c) || numOrNull(s.prevDailyBar && s.prevDailyBar.c);
    const volume = numOrNull(s.dailyBar && s.dailyBar.v);
    const dollarVolume = price && volume ? price * volume : null;
    return { ...a, price, volume, dollarVolume, snapshot: s };
  }).filter(a => (a.price === null || a.price >= minPrice) && (a.dollarVolume === null || a.dollarVolume >= minDv));
}

function scoreAssetForStrategy(a, strat) {
  const change = snapshotChange(a.snapshot);
  const volPenalty = Math.min(25, Math.abs(change) * 600);
  const liquidity = a.dollarVolume ? clamp(Math.log10(a.dollarVolume) * 9, 0, 100) : 45;
  const breakout = a.snapshot && a.snapshot.dailyBar && a.snapshot.prevDailyBar && a.snapshot.dailyBar.c > a.snapshot.prevDailyBar.h ? 12 : 0;
  const l1 = clamp((a.rankScore * 0.36 + liquidity * 0.24 + (50 + change * 250) * 0.28 + breakout - volPenalty * 0.12) * strat.l1, 0, 100);
  const l2 = 50;
  const finalScore = round(l1 * 0.78 + l2 * 0.22, 2);
  const trend = change > 0 ? 'positive' : change < -0.015 ? 'negative' : 'flat';
  const strategyReason = `L1 ${round(l1, 1)}: momentum ${(change * 100).toFixed(2)}%, liquidity ${a.dollarVolume ? Math.round(a.dollarVolume) : 'n/a'}, trend ${trend}; Layer 2 neutral.`;
  return { symbol: a.symbol, name: a.name, l1Score: round(l1, 2), layer2Score: l2, finalScore, strategyReason };
}

function buildOrdersFromTargets(targets, account, positions, state) {
  const accountValue = Number(account && (account.portfolio_value || account.equity) || 0) || 100000;
  const current = new Map(positions.map(p => [String(p.symbol).toUpperCase(), Number(p.market_value || 0)]));
  const out = [];
  for (const t of targets) {
    const targetValue = accountValue * t.targetWeight;
    const now = current.get(t.symbol) || 0;
    const delta = targetValue - now;
    const minTrade = Math.max(25, accountValue * 0.0025);
    if (Math.abs(delta) < minTrade) continue;
    out.push({ broker: 'Alpaca Paper', symbol: t.symbol, side: delta > 0 ? 'buy' : 'sell', notional: Math.abs(delta), orderType: 'market', strategy: state.strategy, reason: t.reason, targetWeight: t.targetWeight, status: 'preview' });
  }
  return out.slice(0, Number(state.maxOrders || 8));
}

async function computeRegime(env, state) {
  const symbols = ['SPY', 'QQQ', 'IWM', 'TLT', 'HYG'];
  const end = new Date().toISOString().slice(0, 10);
  const start = new Date(Date.now() - 130 * 86400000).toISOString().slice(0, 10);
  const r = await alpacaDataFetch(`/v2/stocks/bars?symbols=${symbols.join(',')}&timeframe=1Day&start=${start}&end=${end}&feed=${enc(state.dataFeed || 'iex')}&adjustment=all&limit=10000`, env);
  const body = await readBody(r);
  const bars = (r.ok && body && body.bars) || {};
  const closeSeries = arr => (arr || []).map(b => Number(b.c)).filter(Number.isFinite);
  const spy = closeSeries(bars.SPY), qqq = closeSeries(bars.QQQ), iwm = closeSeries(bars.IWM), tlt = closeSeries(bars.TLT), hyg = closeSeries(bars.HYG);
  const votes = [];
  votes.push(makeVote('Equity momentum', returnN(spy, 63) > 0.04, returnN(spy, 63) < -0.06));
  votes.push(makeVote('Realized volatility', realizedVol(spy.slice(-21)) < 0.20, realizedVol(spy.slice(-21)) > 0.32));
  votes.push(makeVote('Credit spread proxy', returnN(hyg, 42) > returnN(tlt, 42), returnN(hyg, 42) < returnN(tlt, 42) - 0.04));
  votes.push(makeVote('Rates pressure proxy', returnN(tlt, 42) > -0.03, returnN(tlt, 42) < -0.08));
  const breadth = [spy, qqq, iwm].filter(s => returnN(s, 21) > 0).length / 3;
  votes.push({ label: 'Breadth', score: breadth > 0.66 ? 1 : breadth < 0.34 ? -1 : 0, detail: `${Math.round(breadth * 100)}% proxies positive over 1M` });
  const sum = votes.reduce((s, v) => s + v.score, 0);
  const label = sum >= 4 ? 'Risk-on' : sum >= 2 ? 'Soft-landing' : sum <= -4 ? 'Crash' : sum <= -2 ? 'Risk-off' : 'Neutral';
  return { label, score: sum, detail: `${sum}/5 vote score`, updated: new Date().toISOString(), voting: votes };
}
function makeVote(label, bull, bear) { return { label, score: bull ? 1 : bear ? -1 : 0, detail: bull ? 'bullish' : bear ? 'bearish' : 'neutral' }; }
function returnN(s, n) { if (!s || s.length < n + 1) return 0; return s[s.length - 1] / s[s.length - 1 - n] - 1; }
function realizedVol(s) { const r = s.map((v, i) => i ? v / s[i - 1] - 1 : 0).slice(1); return stdev(r) * Math.sqrt(252); }
function mean(a) { return a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0; }
function stdev(a) { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(mean(a.map(x => (x - m) ** 2))); }
function snapshotChange(s) { if (!s || !s.dailyBar || !s.prevDailyBar || !s.prevDailyBar.c) return 0; return Number(s.dailyBar.c) / Number(s.prevDailyBar.c) - 1; }
function buildDecisionNarrative(targets, regime, strategyName) {
  const t = targets[0];
  const stamp = new Date().toISOString();
  if (!t) return `${stamp} — Autonomous engine moved to cash because no asset passed current filters.`;
  const gross = targets.reduce((s, x) => s + x.targetWeight, 0);
  return `${stamp} — Autonomous engine increased exposure to ${t.symbol} and ${Math.max(0, targets.length - 1)} other names because market regime is ${regime ? regime.label : 'Neutral'}, breadth/liquidity filters passed, and ${strategyName} risk limits allow ${(gross * 100).toFixed(1)}% gross exposure.`;
}
function chunkArray(a, n) { const out = []; for (let i = 0; i < a.length; i += n) out.push(a.slice(i, i + n)); return out; }
function numOrNull(v) { const n = Number(v); return Number.isFinite(n) ? n : null; }
function clamp(v, a, b) { return Math.max(a, Math.min(b, Number(v) || 0)); }

function hasAlpaca(env) {
  return !!(env.ALPACA_API_KEY && env.ALPACA_SECRET_KEY);
}

function alpacaCfg(env) {
  const base = String(env.ALPACA_BASE_URL || PAPER_BASE).replace(/\/$/, '');
  const dataBase = String(env.ALPACA_DATA_BASE_URL || DATA_BASE).replace(/\/$/, '');
  return { base, dataBase, isPaper: base === PAPER_BASE };
}

async function alpacaStatus(env) {
  if (!hasAlpaca(env)) {
    return json({ connected: false, mode: 'paper', reason: 'Alpaca paper secrets are not configured.', realMoney: 'locked', build: APP_BUILD });
  }
  const cfg = alpacaCfg(env);
  if (!cfg.isPaper) {
    return json({ connected: false, mode: 'blocked', reason: 'Only Alpaca Paper is allowed. Set ALPACA_BASE_URL to https://paper-api.alpaca.markets', realMoney: 'locked', build: APP_BUILD }, 403);
  }
  const r = await alpacaFetch('/v2/account', env);
  const body = await readBody(r);
  return json({
    connected: r.ok,
    mode: 'paper',
    httpStatus: r.status,
    account: sanitizeAccount(body),
    reason: r.ok ? 'Paper account reachable.' : extractError(body),
    realMoney: 'locked',
    build: APP_BUILD
  }, r.ok ? 200 : r.status);
}

function alpacaHeaders(env, extra = {}) {
  return {
    'APCA-API-KEY-ID': env.ALPACA_API_KEY || '',
    'APCA-API-SECRET-KEY': env.ALPACA_SECRET_KEY || '',
    'content-type': 'application/json',
    ...extra
  };
}

async function alpacaFetch(path, env, init = {}) {
  const cfg = alpacaCfg(env);
  return fetch(cfg.base + path, { ...init, headers: alpacaHeaders(env, init.headers || {}) });
}

async function alpacaDataFetch(path, env, init = {}) {
  const cfg = alpacaCfg(env);
  return fetch(cfg.dataBase + path, { ...init, headers: alpacaHeaders(env, init.headers || {}) });
}

async function alpacaProxy(path, env, init = {}) {
  const guard = guardAlpaca(env);
  if (guard) return guard;
  const r = await alpacaFetch(path, env, init);
  const body = await readBody(r);
  return json(body, r.status);
}

function guardAlpaca(env) {
  if (!hasAlpaca(env)) return json({ error: 'Alpaca paper secrets not configured', code: 'NO_ALPACA_KEYS' }, 412);
  if (!alpacaCfg(env).isPaper) return json({ error: 'Live endpoint blocked. Algohns only uses Alpaca Paper.', code: 'LIVE_BLOCKED' }, 403);
  return null;
}

async function alpacaAssets(url, env) {
  const guard = guardAlpaca(env);
  if (guard) return guard;
  const status = url.searchParams.get('status') || 'active';
  const cls = url.searchParams.get('asset_class') || 'us_equity';
  const requestedLimit = Math.max(0, Math.min(Number(url.searchParams.get('limit') || 0), 20000));
  const r = await alpacaFetch(`/v2/assets?status=${enc(status)}&asset_class=${enc(cls)}`, env);
  const body = await readBody(r);
  if (!r.ok) return json({ error: extractError(body), httpStatus: r.status, assets: [] }, r.status);
  const arr = Array.isArray(body) ? body : [];
  const assets = requestedLimit ? arr.slice(0, requestedLimit) : arr;
  return json({
    meta: { provider: 'alpaca-paper', status, assetClass: cls, returned: assets.length, totalReceived: arr.length, limitApplied: requestedLimit || null, timestamp: new Date().toISOString() },
    assets
  });
}

async function alpacaSnapshots(url, env) {
  const guard = guardAlpaca(env);
  if (guard) return guard;
  const symbols = parseSymbols(url.searchParams.get('symbols'));
  if (!symbols.length) return json({ snapshots: {}, meta: { count: 0 } });
  if (symbols.length > MAX_BATCH_SYMBOLS) return json({ error: `Too many symbols. Max ${MAX_BATCH_SYMBOLS} per request.`, code: 'TOO_MANY_SYMBOLS' }, 400);
  const feed = url.searchParams.get('feed') || 'iex';
  const r = await alpacaDataFetch(`/v2/stocks/snapshots?symbols=${enc(symbols.join(','))}&feed=${enc(feed)}`, env);
  const body = await readBody(r);
  if (!r.ok) return json({ error: extractError(body), httpStatus: r.status, snapshots: {} }, r.status);
  return json({ snapshots: body, meta: { provider: 'alpaca-data', feed, symbols: symbols.length, timestamp: new Date().toISOString() } });
}

async function alpacaBars(url, env) {
  const guard = guardAlpaca(env);
  if (guard) return guard;
  const symbols = parseSymbols(url.searchParams.get('symbols'));
  if (!symbols.length) return json({ bars: {}, meta: { count: 0 } });
  if (symbols.length > MAX_BATCH_SYMBOLS) return json({ error: `Too many symbols. Max ${MAX_BATCH_SYMBOLS} per request.`, code: 'TOO_MANY_SYMBOLS' }, 400);
  const timeframe = url.searchParams.get('timeframe') || '1Day';
  const start = url.searchParams.get('start');
  const end = url.searchParams.get('end');
  const feed = url.searchParams.get('feed') || 'iex';
  const adjustment = url.searchParams.get('adjustment') || 'all';
  const limit = Math.max(1000, Math.min(Number(url.searchParams.get('limit') || 10000), 10000));
  const qs = new URLSearchParams({ symbols: symbols.join(','), timeframe, feed, adjustment, limit: String(limit) });
  if (start) qs.set('start', start);
  if (end) qs.set('end', end);
  const r = await alpacaDataFetch('/v2/stocks/bars?' + qs.toString(), env);
  const body = await readBody(r);
  if (!r.ok) return json({ error: extractError(body), httpStatus: r.status, bars: {}, meta: { symbols: symbols.length, feed } }, r.status);
  return json({ bars: body.bars || {}, next_page_token: body.next_page_token || null, meta: { provider: 'alpaca-data', feed, adjustment, timeframe, symbols: symbols.length, timestamp: new Date().toISOString() } });
}

async function alpacaNews(url, env) {
  const guard = guardAlpaca(env);
  if (guard) return guard;
  const symbols = parseSymbols(url.searchParams.get('symbols')).slice(0, 50);
  const qs = new URLSearchParams({ limit: String(Math.max(1, Math.min(Number(url.searchParams.get('limit') || 30), 50))) });
  if (symbols.length) qs.set('symbols', symbols.join(','));
  const r = await alpacaDataFetch('/v1beta1/news?' + qs.toString(), env);
  const body = await readBody(r);
  if (!r.ok) return json({ news: [], error: extractError(body), httpStatus: r.status }, r.status);
  return json({ news: body.news || [], meta: { provider: 'alpaca-news', symbols, timestamp: new Date().toISOString() } });
}

async function alpacaSubmitOrders(request, env) {
  const guard = guardAlpaca(env);
  if (guard) return guard;
  const payload = await safeJson(request);
  const orders = Array.isArray(payload.orders) ? payload.orders.slice(0, MAX_ORDER_BATCH) : [];
  const skipped = Array.isArray(payload.orders) && payload.orders.length > MAX_ORDER_BATCH ? payload.orders.slice(MAX_ORDER_BATCH).map(o => ({ symbol: o.symbol, status: 'skipped', reason: `Batch cap ${MAX_ORDER_BATCH}` })) : [];
  const results = [];

  for (const order of orders) {
    const symbol = String(order.symbol || '').toUpperCase().trim();
    const side = String(order.side || '').toLowerCase() === 'sell' ? 'sell' : 'buy';
    const notional = Number(order.notional || order.amount || 0);
    const qty = Number(order.qty || order.quantity || 0);
    if (!symbol) { results.push({ status: 'rejected', reason: 'Missing symbol', sent: null }); continue; }
    if (!notional && !qty) { results.push({ symbol, side, status: 'rejected', reason: 'Missing notional or quantity', sent: null }); continue; }
    const body = { symbol, side, type: 'market', time_in_force: 'day' };
    if (qty > 0) body.qty = String(round(qty, 6));
    else body.notional = String(round(Math.max(1, notional), 2));
    const r = await alpacaFetch('/v2/orders', env, { method: 'POST', body: JSON.stringify(body) });
    const resBody = await readBody(r);
    results.push({ symbol, side, status: r.ok ? 'submitted' : 'rejected', httpStatus: r.status, order: r.ok ? resBody : null, reason: r.ok ? 'submitted to Alpaca Paper' : extractError(resBody), sent: body });
    await sleep(250);
  }

  return json({ results: results.concat(skipped), meta: { submitted: results.length, skipped: skipped.length, maxBatch: MAX_ORDER_BATCH, realMoney: 'locked' } });
}

async function alpacaCancelAll(env) {
  const guard = guardAlpaca(env);
  if (guard) return guard;
  const r = await alpacaFetch('/v2/orders', env, { method: 'DELETE' });
  const body = await readBody(r);
  return json({ cancelled: r.ok, httpStatus: r.status, detail: body, realMoney: 'locked' }, r.status);
}

async function alpacaClosePosition(url, env) {
  const guard = guardAlpaca(env);
  if (guard) return guard;
  const symbol = String(url.searchParams.get('symbol') || '').toUpperCase().trim();
  const qty = url.searchParams.get('qty');
  const percentage = url.searchParams.get('percentage');
  if (!symbol) return json({ error: 'Missing symbol' }, 400);
  const qs = new URLSearchParams();
  if (qty) qs.set('qty', qty);
  if (percentage) qs.set('percentage', percentage);
  const r = await alpacaFetch(`/v2/positions/${enc(symbol)}${qs.toString() ? '?' + qs.toString() : ''}`, env, { method: 'DELETE' });
  const body = await readBody(r);
  return json({ symbol, closed: r.ok, httpStatus: r.status, detail: body, realMoney: 'locked' }, r.status);
}

function sanitizeAccount(a) {
  if (!a || typeof a !== 'object') return null;
  return {
    id: a.id ? String(a.id).slice(0, 8) + '…' : null,
    status: a.status,
    currency: a.currency,
    portfolio_value: a.portfolio_value,
    cash: a.cash,
    buying_power: a.buying_power,
    equity: a.equity,
    last_equity: a.last_equity,
    trading_blocked: a.trading_blocked,
    account_blocked: a.account_blocked,
    pattern_day_trader: a.pattern_day_trader
  };
}

function parseSymbols(raw) {
  return String(raw || '').split(',').map(s => s.trim().toUpperCase()).filter(Boolean).filter((s, i, a) => a.indexOf(s) === i);
}
function enc(v) { return encodeURIComponent(v); }
function round(v, d = 2) { const p = Math.pow(10, d); return Math.round(v * p) / p; }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
async function safeJson(request) { try { return await request.json(); } catch (_) { return {}; } }
async function readBody(r) {
  const text = await r.text();
  if (!text) return null;
  try { return JSON.parse(text); } catch (_) { return text; }
}
function extractError(body) {
  if (!body) return 'Empty response';
  if (typeof body === 'string') return body.slice(0, 500);
  return body.message || body.error || body.detail || JSON.stringify(body).slice(0, 500);
}
function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } });
}
function cors(response) {
  const h = new Headers(response.headers);
  h.set('access-control-allow-origin', '*');
  h.set('access-control-allow-methods', 'GET,POST,DELETE,OPTIONS');
  h.set('access-control-allow-headers', 'content-type,authorization');
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers: h });
}
