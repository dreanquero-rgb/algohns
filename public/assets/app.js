/* Algohns V11 — Alpaca Paper Quant Asset Manager OS
 * Vanilla JS SPA. No framework. No real-money execution.
 */

const APP_VERSION = '11.0.0';
const APP_BUILD = 'algohns-v11-alpaca-core-2026-06-14';
const STORAGE_KEY = 'algohns_v11_config';
const EXCLUSION_KEY = 'algohns_v11_exclusions';
const JOURNAL_KEY = 'algohns_v11_order_journal';
const OPERATIONAL_UNIVERSE_CAP = 600;
const SNAPSHOT_CAP = 220;
const BACKTEST_SYMBOL_CAP = 80;

const STRATEGIES = {
  Defensive: { cashFloor: 0.30, maxPosition: 0.055, gross: 0.70, turnover: 0.18, hedgeBudget: 0.08, l1: 0.70, l2: 0.72, killDD: -0.08, slots: 14 },
  Balanced: { cashFloor: 0.18, maxPosition: 0.075, gross: 0.82, turnover: 0.25, hedgeBudget: 0.05, l1: 0.86, l2: 0.60, killDD: -0.12, slots: 16 },
  Advanced: { cashFloor: 0.10, maxPosition: 0.095, gross: 0.92, turnover: 0.34, hedgeBudget: 0.03, l1: 1.00, l2: 0.48, killDD: -0.16, slots: 18 },
  Aggressive: { cashFloor: 0.04, maxPosition: 0.125, gross: 0.98, turnover: 0.48, hedgeBudget: 0.00, l1: 1.18, l2: 0.35, killDD: -0.22, slots: 20 }
};

const PERIODS = {
  '6M': monthsAgo(6),
  '1Y': monthsAgo(12),
  '3Y': monthsAgo(36),
  '5Y': monthsAgo(60)
};

const PAGES = [
  { id: 'control', icon: '◈', title: 'Control Center', subtitle: 'Decision cockpit: status, equity curve, benchmark, drawdown and last algorithm decision.' },
  { id: 'strategy', icon: '◆', title: 'Strategy Studio', subtitle: 'Configure strategy aggressiveness, risk limits, ranking layers and regime engine.' },
  { id: 'backtest', icon: '▧', title: 'Backtest Lab', subtitle: 'Load real Alpaca data, run strategy simulations and compare against benchmarks.' },
  { id: 'portfolio', icon: '▣', title: 'Live Portfolio', subtitle: 'Paper portfolio cockpit with allocation, position diagnostics and actions.' },
  { id: 'orders', icon: '↯', title: 'Orders & Control', subtitle: 'Preview, submit, cancel and audit paper orders.' },
  { id: 'risk', icon: '△', title: 'Risk Center', subtitle: 'Drawdown, VaR, CVaR, beta, concentration and scenario stress tests.' },
  { id: 'news', icon: '◌', title: 'News Intelligence', subtitle: 'Portfolio-linked news, event risk, sentiment clustering and blackout notes.' },
  { id: 'universe', icon: '▦', title: 'Universe Explorer', subtitle: 'Alpaca tradable universe, included/excluded assets and transparent scoring.' },
  { id: 'settings', icon: '⚙', title: 'Settings / Connections', subtitle: 'Connection tests, build verification, export tools and safety controls.' }
];

const state = {
  page: 'control',
  running: false,
  killed: false,
  alerts: [],
  buildInfo: null,
  config: {
    strategy: 'Balanced',
    benchmark: 'SPY',
    dataFeed: 'iex',
    universeAssetClass: 'us_equity',
    customSymbols: '',
    minPrice: 3,
    minDollarVolume: 5000000,
    maxOrders: 8,
    paperOrderCap: 10,
    backtest: { strategy: 'Balanced', universe: 'Included universe', benchmark: 'SPY', period: '1Y', start: '', end: '', rebalance: 'Monthly', slippageBps: 5, commissionBps: 0 }
  },
  alpaca: { status: null, account: null, positions: [], orders: [], history: null, clock: null, assets: [], snapshots: {}, lastSync: null },
  universe: { loaded: false, assets: [], included: [], excluded: [], custom: [], source: null, lastUpdate: null, filters: { search: '', status: 'included' } },
  core: { regime: null, ranked: [], targets: [], orders: [], lastDecision: null, riskStatus: 'Watch', voting: [] },
  backtest: { stale: true, loaded: false, running: false, dataset: null, result: null, error: null, source: null },
  journal: [],
  news: { items: [], clusters: [], loadedAt: null, error: null },
  exclusions: [],
  engine: null
};

const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

window.addEventListener('DOMContentLoaded', init);
window.addEventListener('hashchange', () => {
  const id = location.hash.replace('#/', '') || 'control';
  setPage(PAGES.some(p => p.id === id) ? id : 'control');
});

function init() {
  loadLocal();
  bindShell();
  renderNav();
  const id = location.hash.replace('#/', '') || state.page || 'control';
  setPage(PAGES.some(p => p.id === id) ? id : 'control', true);
  checkBuild();
  testConnection(false);
  syncEngineState(false);
}

function bindShell() {
  $('#btn-sync').addEventListener('click', () => syncAll(true));
  $('#btn-play').addEventListener('click', playEngine);
  $('#btn-pause').addEventListener('click', pauseEngine);
  $('#btn-kill').addEventListener('click', killSwitch);
}

function loadLocal() {
  try { state.config = { ...state.config, ...(JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')) }; } catch (_) {}
  try { state.exclusions = JSON.parse(localStorage.getItem(EXCLUSION_KEY) || '[]'); } catch (_) { state.exclusions = []; }
  try { state.journal = JSON.parse(localStorage.getItem(JOURNAL_KEY) || '[]'); } catch (_) { state.journal = []; }
}
function saveConfig() { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.config)); }
function saveExclusions() { localStorage.setItem(EXCLUSION_KEY, JSON.stringify(state.exclusions)); }
function saveJournal() { localStorage.setItem(JOURNAL_KEY, JSON.stringify(state.journal.slice(0, 500))); }

function renderNav() {
  $('#nav').innerHTML = PAGES.map(p => `<button data-nav="${p.id}" class="${state.page === p.id ? 'active' : ''}"><span class="ico">${p.icon}</span><span>${p.title}</span></button>`).join('');
  $$('[data-nav]').forEach(b => b.addEventListener('click', () => { location.hash = '#/' + b.dataset.nav; }));
}

function setPage(id, initial = false) {
  state.page = id;
  const p = PAGES.find(x => x.id === id) || PAGES[0];
  $('#page-kicker').textContent = p.title;
  $('#page-title').textContent = p.title;
  $('#page-subtitle').textContent = p.subtitle;
  document.title = `Algohns V11 — ${p.title}`;
  renderNav();
  safeRender(p.id);
  if (!initial && location.hash !== '#/' + id) location.hash = '#/' + id;
}

function safeRender(page) {
  renderAlerts();
  try {
    const map = { control: renderControl, strategy: renderStrategy, backtest: renderBacktest, portfolio: renderPortfolio, orders: renderOrders, risk: renderRisk, news: renderNews, universe: renderUniverse, settings: renderSettings };
    (map[page] || renderControl)();
  } catch (err) {
    $('#app').innerHTML = `<div class="panel"><h2>Page recovered from an error</h2><p class="muted">This section failed without blocking the rest of the cockpit.</p><pre class="mono bad">${html(String(err && err.stack || err))}</pre><button class="btn" onclick="setPage('control')">Return to Control Center</button></div>`;
  }
}

function alert(msg, type = 'warn') {
  state.alerts.unshift({ msg, type, ts: Date.now() });
  state.alerts = state.alerts.slice(0, 4);
  renderAlerts();
}
function renderAlerts() {
  $('#alerts').innerHTML = state.alerts.map(a => `<div class="alert ${a.type}">${html(a.msg)}</div>`).join('');
}

async function api(path, opts = {}) {
  const r = await fetch(path, { cache: 'no-store', ...opts });
  const text = await r.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = text; }
  if (!r.ok) {
    const msg = body && (body.error || body.message || body.reason) ? (body.error || body.message || body.reason) : `HTTP ${r.status}`;
    const err = new Error(msg);
    err.status = r.status;
    err.body = body;
    throw err;
  }
  return body;
}

async function checkBuild() {
  try {
    state.buildInfo = await api('/api/health');
    $('#build-chip').textContent = state.buildInfo.build || 'worker unknown';
  } catch (e) {
    $('#build-chip').textContent = 'worker check failed';
  }
}

async function testConnection(show = true) {
  try {
    state.alpaca.status = await api('/api/broker/alpaca/status');
    updateConnChip();
    if (show) alert(state.alpaca.status.connected ? 'Alpaca Paper connected.' : (state.alpaca.status.reason || 'Alpaca Paper not connected.'), state.alpaca.status.connected ? 'good' : 'warn');
  } catch (e) {
    state.alpaca.status = { connected: false, reason: e.message, httpStatus: e.status };
    updateConnChip();
    if (show) alert(`Alpaca Paper connection failed: ${e.message}`, 'bad');
  }
}

function updateConnChip() {
  const el = $('#conn-alpaca');
  const ok = state.alpaca.status && state.alpaca.status.connected;
  el.textContent = ok ? 'OK' : 'OFF';
  el.className = ok ? 'good' : 'muted';
}

async function syncAll(show = true) {
  await testConnection(false);
  try {
    const [account, positions, orders, clock, history] = await Promise.all([
      api('/api/broker/alpaca/account').catch(e => ({ error: e.message })),
      api('/api/broker/alpaca/positions').catch(() => []),
      api('/api/broker/alpaca/orders').catch(() => []),
      api('/api/broker/alpaca/clock').catch(() => null),
      api('/api/broker/alpaca/history?period=3M&timeframe=1D').catch(() => null)
    ]);
    state.alpaca.account = account && !account.error ? account : null;
    state.alpaca.positions = Array.isArray(positions) ? positions : [];
    state.alpaca.orders = Array.isArray(orders) ? orders : [];
    state.alpaca.clock = clock;
    state.alpaca.history = history;
    state.alpaca.lastSync = new Date().toISOString();
    if (show) alert('Portfolio, orders and account synced from Alpaca Paper.', 'good');
    safeRender(state.page);
  } catch (e) {
    alert(`Sync failed: ${e.message}`, 'bad');
  }
}

async function playEngine() {
  state.running = true;
  state.killed = false;
  try {
    await api('/api/engine/state', { method: 'POST', body: JSON.stringify({ action: 'play', config: engineConfigPayload() }) });
    alert('Algorithm set to Play. The autonomous engine now runs on Cloudflare even if this site is closed.', 'good');
  } catch (e) {
    alert(`Could not arm the autonomous engine: ${e.message}. It will still run once per click here.`, 'warn');
  }
  await runEngine();
}
async function pauseEngine() {
  state.running = false;
  try { await api('/api/engine/state', { method: 'POST', body: JSON.stringify({ action: 'pause' }) }); } catch (_) {}
  alert('Paused: Algohns will not create new paper orders until Play is pressed, on this device or autonomously.', 'warn');
  safeRender(state.page);
}
async function killSwitch() {
  state.running = false;
  state.killed = true;
  try {
    const res = await api('/api/engine/state', { method: 'POST', body: JSON.stringify({ action: 'kill' }) });
    alert(res.error ? `Kill Switch local stop active: ${res.error}` : 'Kill Switch executed: autonomous engine stopped and open paper orders cancellation requested.', res.error ? 'warn' : 'good');
  } catch (e) {
    alert(`Kill Switch local stop active. Broker cancel failed: ${e.message}`, 'bad');
  }
  safeRender(state.page);
}

function engineConfigPayload() {
  return {
    strategy: state.config.strategy, dataFeed: state.config.dataFeed, universeAssetClass: state.config.universeAssetClass,
    customSymbols: state.config.customSymbols, minPrice: state.config.minPrice, minDollarVolume: state.config.minDollarVolume,
    maxOrders: state.config.maxOrders, paperOrderCap: state.config.paperOrderCap, exclusions: state.exclusions
  };
}

async function syncEngineState(show = false) {
  try {
    const [engine, journalRes] = await Promise.all([api('/api/engine/state'), api('/api/engine/journal')]);
    state.engine = engine;
    state.running = !!engine.running;
    state.killed = !!engine.killed;
    if (Array.isArray(journalRes.journal) && journalRes.journal.length) {
      const seen = new Set(state.journal.map(j => j.time + j.symbol + j.status));
      const fresh = journalRes.journal.filter(j => !seen.has(j.time + j.symbol + j.status));
      if (fresh.length) { state.journal = fresh.concat(state.journal).slice(0, 500); saveJournal(); }
    }
    if (show) alert(`Autonomous engine status: ${engine.lastRunNote || 'no run yet'}.`, 'good');
    safeRender(state.page);
  } catch (e) {
    if (show) alert(`Could not read autonomous engine status: ${e.message}`, 'warn');
  }
}

async function loadUniverse(show = true) {
  try {
    const data = await api(`/api/broker/alpaca/assets?asset_class=${encodeURIComponent(state.config.universeAssetClass)}&status=active`);
    state.alpaca.assets = data.assets || [];
    buildUniverseFromAssets();
    await enrichUniverseSnapshots();
    rankUniverse();
    state.universe.loaded = true;
    state.universe.source = data.meta || { provider: 'alpaca-paper' };
    state.universe.lastUpdate = new Date().toISOString();
    if (show) alert(`Universe loaded: ${state.universe.included.length} included, ${state.universe.excluded.length} excluded.`, 'good');
    safeRender(state.page);
  } catch (e) {
    alert(`Universe load failed: ${e.message}`, 'bad');
  }
}

function buildUniverseFromAssets() {
  const minPrice = Number(state.config.minPrice || 0);
  const custom = parseSymbols(state.config.customSymbols).map(symbol => ({ id: 'custom-' + symbol, symbol, name: symbol + ' custom addition', exchange: 'Custom', asset_class: 'us_equity', tradable: true, status: 'active', custom: true }));
  const raw = [...(state.alpaca.assets || []), ...custom];
  const bySymbol = new Map();
  raw.forEach(a => { if (a && a.symbol && !bySymbol.has(a.symbol)) bySymbol.set(a.symbol, a); });

  const all = Array.from(bySymbol.values()).map(a => {
    const symbol = String(a.symbol || '').toUpperCase();
    const reasons = [];
    const name = a.name || symbol;
    const assetClass = a.asset_class || a.class || 'us_equity';
    const exchange = a.exchange || '—';
    const tradable = a.tradable !== false && a.status !== 'inactive';
    const marginable = a.marginable !== false;
    const fractionable = a.fractionable === true;
    const shortable = a.shortable === true;
    const nameBad = /warrant|unit|right|preferred|depositary|note due|acquisition corp/i.test(name);
    const symbolBad = /[\.\^\/=]|WS$|WT$|U$|R$/.test(symbol);
    if (!tradable) reasons.push('not tradable on Alpaca Paper');
    if (assetClass !== 'us_equity') reasons.push('not US equity');
    if (!['NASDAQ', 'NYSE', 'ARCA', 'AMEX', 'BATS', 'IEX', 'OTC'].includes(exchange)) reasons.push('unsupported exchange metadata');
    if (exchange === 'OTC') reasons.push('OTC risk excluded');
    if (nameBad || symbolBad) reasons.push('structure/warrant/unit filter');
    if (!marginable && !fractionable && !a.custom) reasons.push('low execution flexibility');
    const baseScore = (tradable ? 25 : 0) + (['NASDAQ', 'NYSE', 'ARCA'].includes(exchange) ? 20 : 8) + (fractionable ? 12 : 0) + (marginable ? 10 : 0) + (shortable ? 4 : 0) - (reasons.length * 16);
    return {
      symbol, name, brokerAvailability: 'Alpaca Paper', assetClass, exchange, price: null, volume: null, dollarVolume: null,
      tradable, historicalBarsAvailable: 'unknown', fundamentalsAvailable: false,
      included: reasons.length === 0, reason: reasons.length ? reasons.join('; ') : 'tradable, clean structure, supported exchange',
      rankScore: clamp(baseScore, 0, 100), layer2Score: null, lastUpdate: null, id: a.id || a.asset_id || null, custom: !!a.custom
    };
  });

  const includedCandidates = all.filter(a => a.included).sort((a, b) => b.rankScore - a.rankScore || a.symbol.localeCompare(b.symbol));
  const included = includedCandidates.slice(0, OPERATIONAL_UNIVERSE_CAP).map(a => ({ ...a, capReason: includedCandidates.length > OPERATIONAL_UNIVERSE_CAP ? `operational cap ${OPERATIONAL_UNIVERSE_CAP} after scoring` : null }));
  const cappedOut = includedCandidates.slice(OPERATIONAL_UNIVERSE_CAP).map(a => ({ ...a, included: false, reason: `excluded by declared operational cap ${OPERATIONAL_UNIVERSE_CAP} after scoring` }));
  const excluded = all.filter(a => !a.included).concat(cappedOut).sort((a, b) => a.symbol.localeCompare(b.symbol));

  state.universe.assets = all;
  state.universe.included = included;
  state.universe.excluded = excluded;
  state.universe.custom = custom.map(x => x.symbol);
}

async function enrichUniverseSnapshots() {
  const symbols = state.universe.included.slice(0, SNAPSHOT_CAP).map(a => a.symbol);
  state.alpaca.snapshots = {};
  for (const chunk of chunks(symbols, 180)) {
    try {
      const res = await api(`/api/market/snapshots?symbols=${encodeURIComponent(chunk.join(','))}&feed=${encodeURIComponent(state.config.dataFeed)}`);
      Object.assign(state.alpaca.snapshots, res.snapshots || {});
    } catch (e) {
      alert(`Snapshot enrichment failed for ${chunk.length} symbols: ${e.message}`, 'warn');
      break;
    }
  }
  const snap = state.alpaca.snapshots || {};
  state.universe.included = state.universe.included.map(a => enrichAssetWithSnapshot(a, snap[a.symbol]));
  state.universe.assets = state.universe.assets.map(a => enrichAssetWithSnapshot(a, snap[a.symbol]));
}

function enrichAssetWithSnapshot(a, s) {
  if (!s) return a;
  const price = num(s.latestTrade && s.latestTrade.p) || num(s.dailyBar && s.dailyBar.c) || num(s.prevDailyBar && s.prevDailyBar.c);
  const volume = num(s.dailyBar && s.dailyBar.v) || null;
  const dollarVolume = price && volume ? price * volume : null;
  return { ...a, price, volume, dollarVolume, lastUpdate: new Date().toISOString() };
}

function rankUniverse() {
  const minPrice = Number(state.config.minPrice || 0);
  const minDv = Number(state.config.minDollarVolume || 0);
  const excludedExtra = [];
  const kept = [];
  for (const a of state.universe.included) {
    const reasons = [];
    if (a.price !== null && a.price < minPrice) reasons.push(`price below ${money(minPrice)}`);
    if (a.dollarVolume !== null && a.dollarVolume < minDv) reasons.push(`dollar volume below ${money(minDv)}`);
    if (reasons.length) excludedExtra.push({ ...a, included: false, reason: reasons.join('; ') });
    else kept.push(a);
  }
  const ranked = kept.map(a => {
    const liq = a.dollarVolume ? clamp(Math.log10(a.dollarVolume) * 9, 0, 100) : a.rankScore * 0.65;
    const priceScore = a.price ? clamp(Math.log10(a.price + 1) * 28, 0, 100) : 45;
    const snap = state.alpaca.snapshots[a.symbol];
    const momentum = snapshotChange(snap);
    const momScore = clamp(50 + momentum * 180, 0, 100);
    const score = round(0.36 * liq + 0.22 * priceScore + 0.28 * momScore + 0.14 * a.rankScore, 2);
    return { ...a, rankScore: score, layer2Score: null, reason: `${a.reason}; L1 score ${score}` };
  }).sort((a, b) => b.rankScore - a.rankScore || a.symbol.localeCompare(b.symbol));
  state.universe.included = ranked;
  state.universe.excluded = state.universe.excluded.concat(excludedExtra).sort((a, b) => a.symbol.localeCompare(b.symbol));
}

async function runEngine() {
  try {
    if (!state.universe.loaded || !state.universe.included.length) await loadUniverse(false);
    await syncAll(false);
    await loadRegime();
    const strat = STRATEGIES[state.config.strategy] || STRATEGIES.Balanced;
    const ranked = state.universe.included.map(a => scoreAssetForStrategy(a, strat)).filter(a => !state.exclusions.includes(a.symbol)).sort((a, b) => b.finalScore - a.finalScore);
    const selected = ranked.slice(0, strat.slots);
    const gross = Math.min(strat.gross, 1 - strat.cashFloor);
    const totalScore = selected.reduce((s, a) => s + Math.max(1, a.finalScore), 0) || 1;
    let targets = selected.map(a => ({ symbol: a.symbol, name: a.name, targetWeight: Math.min(strat.maxPosition, gross * Math.max(1, a.finalScore) / totalScore), score: a.finalScore, reason: a.strategyReason }));
    const allocated = targets.reduce((s, t) => s + t.targetWeight, 0);
    if (allocated > gross) targets = targets.map(t => ({ ...t, targetWeight: t.targetWeight * gross / allocated }));
    state.core.ranked = ranked.slice(0, 80);
    state.core.targets = targets;
    state.core.orders = buildOrdersFromTargets(targets);
    state.core.lastDecision = buildDecisionNarrative(targets, state.core.regime);
    state.core.riskStatus = computePortfolioStatus();
    state.backtest.stale = true;
    alert(`Strategy engine completed: ${targets.length} targets, ${state.core.orders.length} paper order previews.`, 'good');
    safeRender(state.page);
  } catch (e) {
    alert(`Strategy engine failed: ${e.message}`, 'bad');
  }
}

function scoreAssetForStrategy(a, strat) {
  const snap = state.alpaca.snapshots[a.symbol];
  const change = snapshotChange(snap);
  const trend = change > 0 ? 1 : change < -0.015 ? -1 : 0;
  const volPenalty = Math.min(25, Math.abs(change) * 600);
  const liquidity = a.dollarVolume ? clamp(Math.log10(a.dollarVolume) * 9, 0, 100) : 45;
  const breakout = snap && snap.dailyBar && snap.prevDailyBar && snap.dailyBar.c > snap.prevDailyBar.h ? 12 : 0;
  const l1 = clamp((a.rankScore * 0.36 + liquidity * 0.24 + (50 + change * 250) * 0.28 + breakout - volPenalty * 0.12) * strat.l1, 0, 100);
  const l2 = 50; // Fundamentals are not point-in-time in this vanilla build, so Layer 2 is neutral and disclosed.
  const finalScore = round(l1 * 0.78 + l2 * 0.22, 2);
  const strategyReason = `L1 ${round(l1, 1)}: momentum ${pct(change)}, liquidity ${a.dollarVolume ? money(a.dollarVolume) : 'n/a'}, trend ${trend > 0 ? 'positive' : trend < 0 ? 'negative' : 'flat'}; Layer 2 neutral because point-in-time fundamentals are disabled.`;
  return { ...a, l1Score: round(l1, 2), layer2Score: l2, finalScore, strategyReason };
}

function buildOrdersFromTargets(targets) {
  const accountValue = portfolioValue();
  const current = new Map((state.alpaca.positions || []).map(p => [String(p.symbol).toUpperCase(), Number(p.market_value || 0)]));
  const out = [];
  for (const t of targets) {
    const targetValue = accountValue * t.targetWeight;
    const now = current.get(t.symbol) || 0;
    const delta = targetValue - now;
    const minTrade = Math.max(25, accountValue * 0.0025);
    if (Math.abs(delta) < minTrade) continue;
    const side = delta > 0 ? 'buy' : 'sell';
    out.push({ broker: 'Alpaca Paper', symbol: t.symbol, side, notional: Math.abs(delta), orderType: 'market', strategy: state.config.strategy, reason: t.reason, targetWeight: t.targetWeight, status: 'preview' });
  }
  return out.slice(0, Number(state.config.maxOrders || 8));
}

async function submitPreviewOrders() {
  if (state.killed) return alert('Kill Switch is active. Press Play to reset before submitting orders.', 'bad');
  if (!state.core.orders.length) return alert('No order previews. Run Strategy Engine first.', 'warn');
  const orders = state.core.orders.slice(0, Number(state.config.paperOrderCap || 10));
  try {
    const res = await api('/api/broker/alpaca/orders', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ orders }) });
    const now = new Date().toISOString();
    for (const r of res.results || []) {
      const source = orders.find(o => o.symbol === r.symbol) || {};
      state.journal.unshift({ time: now, broker: 'Alpaca Paper', symbol: r.symbol || source.symbol, side: r.side || source.side, notional: source.notional || null, qty: null, orderType: 'market', fillPrice: null, slippage: null, strategy: source.strategy || state.config.strategy, reason: source.reason || r.reason, status: r.status, detail: r.reason || '', httpStatus: r.httpStatus || null });
    }
    saveJournal();
    alert(`Submitted ${orders.length} paper order request(s). Check journal for accepted/rejected details.`, 'good');
    await syncAll(false);
    safeRender('orders');
  } catch (e) {
    alert(`Order submission failed: ${e.message}`, 'bad');
  }
}

async function loadRegime() {
  const end = todayISO();
  const start = isoDaysAgo(130);
  const symbols = ['SPY', 'QQQ', 'IWM', 'TLT', 'HYG'];
  let bars = {};
  try {
    const res = await api(`/api/market/bars?symbols=${symbols.join(',')}&timeframe=1Day&start=${start}&end=${end}&feed=${state.config.dataFeed}&limit=10000`);
    bars = res.bars || {};
  } catch (_) {}
  const votes = [];
  const spy = closeSeries(bars.SPY || []);
  const qqq = closeSeries(bars.QQQ || []);
  const iwm = closeSeries(bars.IWM || []);
  const tlt = closeSeries(bars.TLT || []);
  const hyg = closeSeries(bars.HYG || []);
  votes.push(vote('Equity momentum', returnN(spy, 63) > 0.04, returnN(spy, 63) < -0.06));
  votes.push(vote('Realized volatility', realizedVol(spy.slice(-21)) < 0.20, realizedVol(spy.slice(-21)) > 0.32));
  votes.push(vote('Credit spread proxy', returnN(hyg, 42) > returnN(tlt, 42), returnN(hyg, 42) < returnN(tlt, 42) - 0.04));
  votes.push(vote('Rates pressure proxy', returnN(tlt, 42) > -0.03, returnN(tlt, 42) < -0.08));
  const breadth = [spy, qqq, iwm].filter(s => returnN(s, 21) > 0).length / 3;
  votes.push({ label: 'Breadth', score: breadth > 0.66 ? 1 : breadth < 0.34 ? -1 : 0, detail: `${Math.round(breadth * 100)}% proxies positive over 1M` });
  const sum = votes.reduce((s, v) => s + v.score, 0);
  const label = sum >= 4 ? 'Risk-on' : sum >= 2 ? 'Soft-landing' : sum <= -4 ? 'Crash' : sum <= -2 ? 'Risk-off' : 'Neutral';
  state.core.voting = votes;
  state.core.regime = { label, score: sum, detail: `${sum}/5 vote score`, updated: new Date().toISOString() };
}
function vote(label, bull, bear) { return { label, score: bull ? 1 : bear ? -1 : 0, detail: bull ? 'bullish' : bear ? 'bearish' : 'neutral' }; }

function renderControl() {
  const account = state.alpaca.account;
  const pv = portfolioValue();
  const cash = Number(account && account.cash || 0);
  const daily = dailyPnl();
  const total = totalPnl();
  const dd = currentDrawdown();
  const status = computePortfolioStatus();
  const hist = portfolioHistorySeries();
  const bench = state.backtest.result ? state.backtest.result.benchmarkEquity : null;
  $('#app').innerHTML = `
    <div class="status-hero">
      <div class="hero-card">
        <div>
          <div class="split"><span class="badge ${statusBadge(status)}">${status}</span><span class="badge gold">${state.config.strategy}</span><span class="badge">${state.core.regime ? state.core.regime.label : 'Regime pending'}</span></div>
          <div class="hero-state">${status}</div>
          <div class="hero-note">${html(state.core.lastDecision || 'Run the Strategy Engine to produce a narrative decision, target allocation and paper order previews.')}</div>
        </div>
        <div class="split"><button class="btn primary" onclick="runEngine()">Run Strategy Engine</button><button class="btn ghost" onclick="loadUniverse()">Load Universe</button><button class="btn ghost" onclick="setPage('backtest')">Open Backtest Lab</button></div>
      </div>
      <div class="grid cols-2">
        ${metric('Portfolio value', money(pv), account ? 'Alpaca Paper synced' : 'sync required')}
        ${metric('Cash', money(cash), cash ? pct(cash / Math.max(1, pv)) + ' of portfolio' : '—')}
        ${metric('Daily P&L', money2(daily), daily >= 0 ? 'positive day' : 'negative day', daily >= 0 ? 'good' : 'bad')}
        ${metric('Drawdown', pct(dd), 'from local equity curve', dd > -0.08 ? 'good' : dd > -0.15 ? 'warn-text' : 'bad')}
      </div>
    </div>
    ${renderAutonomousEngineBox()}
    <div class="grid cols-2" style="margin-top:14px">
      <div class="panel"><div class="panel-h"><div><h2>Equity curve vs benchmark</h2><p class="muted small">Live Alpaca history above the fold. Benchmark appears after a backtest run.</p></div><span class="badge">${hist.length} points</span></div><div class="chart">${lineChart(hist, bench, 'Equity', 'Benchmark')}</div></div>
      <div class="panel"><div class="panel-h"><div><h2>Drawdown</h2><p class="muted small">Below-curve loss from previous peak.</p></div><span class="badge ${dd > -0.08 ? 'good' : 'warn'}">${pct(dd)}</span></div><div class="chart small">${drawdownChart(hist)}</div>${renderDecisionBox()}</div>
    </div>
    <div class="grid cols-3" style="margin-top:14px">
      <div class="panel"><h3>Active brokers</h3><p class="muted">Alpaca Paper only. Real money is locked in the UI and worker.</p><div class="split"><span class="dot ${state.alpaca.status && state.alpaca.status.connected ? 'good' : 'warn'}"></span><b>${state.alpaca.status && state.alpaca.status.connected ? 'Connected' : 'Not connected'}</b></div></div>
      <div class="panel"><h3>Next check</h3><p class="muted">Manual-first execution. Press Play for decision refresh; submit orders only from Orders & Control.</p><div class="badge">${state.running ? 'Play mode' : 'Paused'}</div></div>
      <div class="panel"><h3>Kill control</h3><p class="muted">Stops local algorithm state and requests cancellation of open Alpaca Paper orders.</p><button class="btn danger" onclick="killSwitch()">Kill Switch</button></div>
    </div>`;
}

function renderAutonomousEngineBox() {
  const e = state.engine;
  if (!e) return `<div class="panel" style="margin-top:14px"><h2>Autonomous engine (runs without this site open)</h2><p class="muted small">Checking Cloudflare Cron Trigger status…</p></div>`;
  if (e.error) return `<div class="panel" style="margin-top:14px"><h2>Autonomous engine (runs without this site open)</h2><p class="muted small">${html(e.error)}. Create the Cloudflare KV namespace and bind it as ALGOHNS_KV in wrangler.toml to enable always-on execution.</p></div>`;
  const badge = e.killed ? 'bad' : e.running ? 'good' : 'warn';
  const label = e.killed ? 'Killed' : e.running ? 'Running on Cloudflare Cron' : 'Paused';
  return `<div class="panel" style="margin-top:14px"><div class="panel-h"><div><h2>Autonomous engine (runs without this site open)</h2><p class="muted small">A Cloudflare Cron Trigger runs the same Strategy Engine on the edge every few minutes, even with no browser tab open.</p></div><span class="badge ${badge}">${label}</span></div>
    <p class="small muted">Last run: ${e.lastRunAt ? timeShort(e.lastRunAt) : 'never'} (${html(e.lastRunSource || '—')}) — ${html(e.lastRunNote || 'no note')}</p>
    <button class="btn ghost" onclick="syncEngineState(true)">Refresh autonomous status</button></div>`;
}

function renderDecisionBox() {
  return `<div class="decision" style="margin-top:12px"><b>Last Decision</b><br>${html(state.core.lastDecision || 'No decision yet. Run the engine after loading the universe.')}</div>`;
}

function renderStrategy() {
  const s = STRATEGIES[state.config.strategy];
  $('#app').innerHTML = `
    <div class="grid cols-2">
      <div class="panel"><div class="panel-h"><div><h2>Strategy profiles</h2><p class="muted small">Each profile changes cash, exposure, turnover, risk and ranking thresholds.</p></div></div><div class="pillbar">${Object.keys(STRATEGIES).map(k => `<button class="${state.config.strategy === k ? 'active' : ''}" onclick="setStrategy('${k}')">${k}</button>`).join('')}</div><div class="grid cols-3" style="margin-top:14px">${metric('Cash floor', pct(s.cashFloor), 'capital preserved')}${metric('Max position', pct(s.maxPosition), 'single-name cap')}${metric('Auto kill DD', pct(s.killDD), 'paper safety trigger')}</div></div>
      <div class="panel"><h2>Regime Engine</h2><p class="muted">Voting system across equity momentum, volatility, credit proxy, rates proxy and breadth.</p>${regimeTable()}<button class="btn ghost" onclick="loadRegime().then(()=>safeRender('strategy'))">Refresh regime</button></div>
    </div>
    <div class="grid cols-2" style="margin-top:14px">
      <div class="panel"><h2>Layer 1: market structure</h2><div class="table-wrap"><table><tr><th>Signal</th><th>Use</th><th>Status</th></tr><tr><td>Price momentum</td><td>Daily and medium-term proxy</td><td><span class="badge good">active</span></td></tr><tr><td>Trend</td><td>Positive/negative tape</td><td><span class="badge good">active</span></td></tr><tr><td>Volatility</td><td>Penalty for unstable moves</td><td><span class="badge good">active</span></td></tr><tr><td>Liquidity</td><td>Dollar volume filter</td><td><span class="badge good">active</span></td></tr><tr><td>Breakout proxy</td><td>Daily close above previous high</td><td><span class="badge good">active</span></td></tr></table></div></div>
      <div class="panel"><h2>Layer 2: quality filter</h2><div class="section-note">Fundamental data is intentionally neutral in live ranking and disabled in backtests until point-in-time data is available. This prevents look-ahead bias and makes the backtest more honest.</div><div class="toolbar" style="margin-top:14px"><div class="field"><label>Minimum price</label><input type="number" value="${state.config.minPrice}" onchange="updateConfig('minPrice', Number(this.value)); markUniverseStale()"></div><div class="field"><label>Minimum dollar volume</label><input type="number" value="${state.config.minDollarVolume}" onchange="updateConfig('minDollarVolume', Number(this.value)); markUniverseStale()"></div><div class="field"><label>Max order previews</label><input type="number" value="${state.config.maxOrders}" onchange="updateConfig('maxOrders', Number(this.value))"></div></div></div>
    </div>
    <div class="panel" style="margin-top:14px"><div class="panel-h"><div><h2>Rank table</h2><p class="muted small">Generated by the latest engine run.</p></div><button class="btn primary" onclick="runEngine()">Run Strategy Engine</button></div>${rankTable()}</div>`;
}
function setStrategy(k) { state.config.strategy = k; state.config.backtest.strategy = k; saveConfig(); state.backtest.stale = true; syncEngineConfig(); safeRender(state.page); }
function syncEngineConfig() { api('/api/engine/state', { method: 'POST', body: JSON.stringify({ action: state.running ? 'play' : 'pause', config: engineConfigPayload() }) }).catch(() => {}); }
function updateConfig(k, v) { state.config[k] = v; saveConfig(); }
function markUniverseStale() { state.universe.loaded = false; state.backtest.stale = true; saveConfig(); }

function regimeTable() {
  if (!state.core.voting.length) return `<div class="empty">Regime not calculated yet.</div>`;
  return `<div class="table-wrap"><table><tr><th>Vote</th><th>Score</th><th>Detail</th></tr>${state.core.voting.map(v => `<tr><td>${html(v.label)}</td><td class="${v.score > 0 ? 'good' : v.score < 0 ? 'bad' : 'muted'}">${v.score}</td><td>${html(v.detail)}</td></tr>`).join('')}</table></div><p class="muted small">Current regime: <b>${state.core.regime.label}</b> (${state.core.regime.detail})</p>`;
}
function rankTable() {
  if (!state.core.ranked.length) return `<div class="empty">No ranking yet. Load universe and run the engine.</div>`;
  return `<div class="table-wrap"><table><tr><th>Rank</th><th>Ticker</th><th>Name</th><th>L1</th><th>L2</th><th>Final</th><th>Reason</th></tr>${state.core.ranked.slice(0, 40).map((a, i) => `<tr><td>${i + 1}</td><td class="mono"><b>${a.symbol}</b></td><td>${html(a.name)}</td><td>${a.l1Score}</td><td>${a.layer2Score}</td><td><b>${a.finalScore}</b></td><td class="small muted">${html(a.strategyReason)}</td></tr>`).join('')}</table></div>`;
}

function renderUniverse() {
  const f = state.universe.filters;
  const rows = (f.status === 'excluded' ? state.universe.excluded : f.status === 'all' ? state.universe.assets : state.universe.included).filter(a => !f.search || a.symbol.includes(f.search.toUpperCase()) || (a.name || '').toUpperCase().includes(f.search.toUpperCase())).slice(0, 500);
  $('#app').innerHTML = `
    <div class="panel"><div class="panel-h"><div><h2>Universe Explorer</h2><p class="muted small">Operational universe is built from Alpaca Paper active assets. Any technical cap is declared and applied only after scoring.</p></div><button class="btn primary" onclick="loadUniverse()">Load universe</button></div>
      <div class="grid cols-4">${metric('Raw assets', String(state.universe.assets.length), 'from Alpaca Paper')}${metric('Included', String(state.universe.included.length), 'eligible for strategy')}${metric('Excluded', String(state.universe.excluded.length), 'with reason')}${metric('Operational cap', String(OPERATIONAL_UNIVERSE_CAP), 'after scoring')}</div>
      <div class="toolbar" style="margin-top:14px"><div class="field"><label>Search</label><input value="${html(f.search)}" oninput="state.universe.filters.search=this.value; safeRender('universe')" placeholder="AAPL, NVDA, Microsoft..."></div><div class="field"><label>Status</label><select onchange="state.universe.filters.status=this.value; safeRender('universe')"><option ${f.status==='included'?'selected':''} value="included">Included</option><option ${f.status==='excluded'?'selected':''} value="excluded">Excluded</option><option ${f.status==='all'?'selected':''} value="all">All</option></select></div><div class="field"><label>Custom additions</label><input value="${html(state.config.customSymbols)}" onchange="state.config.customSymbols=this.value; saveConfig(); markUniverseStale()" placeholder="AAPL,MSFT,NVDA"></div><div class="field"><label>Data feed</label><select onchange="state.config.dataFeed=this.value; saveConfig(); markUniverseStale()"><option ${state.config.dataFeed==='iex'?'selected':''} value="iex">IEX</option><option ${state.config.dataFeed==='sip'?'selected':''} value="sip">SIP if entitled</option></select></div></div>
      <div class="section-note">No hidden universe. If a symbol is excluded, the reason is visible. Fundamentals are marked unavailable until a point-in-time provider is added.</div>
    </div>
    <div class="panel" style="margin-top:14px"><div class="panel-h"><h2>${f.status === 'excluded' ? 'Excluded assets' : f.status === 'all' ? 'All assets' : 'Included assets'}</h2><span class="badge">showing ${rows.length}</span></div>${universeTable(rows)}</div>`;
}
function universeTable(rows) {
  if (!state.universe.loaded && !rows.length) return `<div class="empty">Universe not loaded. Click Load universe.</div>`;
  if (!rows.length) return `<div class="empty">No assets match the current filter.</div>`;
  return `<div class="table-wrap"><table><tr><th>Ticker</th><th>Name</th><th>Broker availability</th><th>Class</th><th>Exchange</th><th>Price</th><th>Volume</th><th>Dollar volume</th><th>Tradable</th><th>Bars</th><th>Fund.</th><th>Status</th><th>Reason</th><th>Rank</th><th>L2</th><th>Updated</th></tr>${rows.map(a => `<tr><td class="mono"><b>${a.symbol}</b></td><td>${html(a.name)}</td><td>${a.brokerAvailability}</td><td>${a.assetClass}</td><td>${a.exchange}</td><td>${a.price ? money(a.price) : '—'}</td><td>${a.volume ? int(a.volume) : '—'}</td><td>${a.dollarVolume ? money(a.dollarVolume) : '—'}</td><td>${a.tradable ? 'yes' : 'no'}</td><td>${a.historicalBarsAvailable}</td><td>${a.fundamentalsAvailable ? 'yes' : 'no'}</td><td><span class="badge ${a.included ? 'good' : 'warn'}">${a.included ? 'included' : 'excluded'}</span></td><td class="small muted">${html(a.reason || '')}</td><td>${a.rankScore ?? '—'}</td><td>${a.layer2Score ?? '—'}</td><td class="small muted">${a.lastUpdate ? timeShort(a.lastUpdate) : '—'}</td></tr>`).join('')}</table></div>`;
}

function renderPortfolio() {
  const positions = normPositions();
  $('#app').innerHTML = `
    <div class="grid cols-4">${metric('Portfolio value', money(portfolioValue()), 'Alpaca Paper')}${metric('Cash', money(Number(state.alpaca.account && state.alpaca.account.cash || 0)), 'available cash')}${metric('Open positions', String(positions.length), 'synced')}${metric('Open orders', String((state.alpaca.orders || []).filter(o => !['filled','canceled','expired','rejected'].includes(o.status)).length), 'paper orders')}</div>
    <div class="grid cols-2" style="margin-top:14px"><div class="panel"><div class="panel-h"><h2>Allocation treemap</h2><span class="badge">size = weight · color = P&L</span></div>${treemap(positions)}</div><div class="panel"><h2>Exposure</h2>${exposureTable(positions)}</div></div>
    <div class="panel" style="margin-top:14px"><div class="panel-h"><div><h2>Position rows</h2><p class="muted small">Each row is a mini-dashboard with action controls. Excluded assets are not re-bought by the algorithm.</p></div><button class="btn ghost" onclick="syncAll(true)">Sync portfolio</button></div>${positionsTable(positions)}</div>`;
}
function normPositions() {
  return (state.alpaca.positions || []).map(p => ({
    symbol: String(p.symbol || '').toUpperCase(), qty: Number(p.qty || 0), value: Number(p.market_value || 0), cost: Number(p.cost_basis || 0),
    pnl: Number(p.unrealized_pl || 0), pnlPct: Number(p.unrealized_plpc || 0), price: Number(p.current_price || 0), assetClass: p.asset_class || 'us_equity', broker: 'Alpaca Paper'
  })).filter(p => p.symbol);
}
function treemap(positions) {
  if (!positions.length) return `<div class="empty">No open positions synced.</div>`;
  const total = positions.reduce((s, p) => s + Math.max(0, p.value), 0) || 1;
  return `<div class="treemap">${positions.slice(0, 12).map(p => `<div class="tile ${p.pnl < 0 ? 'neg' : ''}" style="flex:${Math.max(0.08, p.value / total)}"><b>${p.symbol}</b><span>${pct(p.value / total)}</span><span>${money2(p.pnl)}</span></div>`).join('')}</div>`;
}
function exposureTable(positions) {
  const total = positions.reduce((s, p) => s + Math.max(0, p.value), 0) || 1;
  const rows = positions.slice().sort((a, b) => b.value - a.value).slice(0, 10);
  if (!rows.length) return `<div class="empty">No exposure to show.</div>`;
  return `<div class="table-wrap"><table><tr><th>Symbol</th><th>Weight</th><th>Value</th><th>P&L</th></tr>${rows.map(p => `<tr><td class="mono"><b>${p.symbol}</b></td><td>${pct(p.value / total)}</td><td>${money(p.value)}</td><td class="${p.pnl >= 0 ? 'good' : 'bad'}">${money2(p.pnl)}</td></tr>`).join('')}</table></div>`;
}
function positionsTable(positions) {
  if (!positions.length) return `<div class="empty">No positions. Sync Alpaca Paper or submit paper orders.</div>`;
  const total = positions.reduce((s, p) => s + Math.max(0, p.value), 0) || 1;
  return `<div class="table-wrap"><table><tr><th>Ticker</th><th>Spark 5D</th><th>Weight</th><th>Value</th><th>Qty</th><th>P&L %</th><th>P&L</th><th>Regime</th><th>Sentiment</th><th>Broker</th><th>Actions</th></tr>${positions.map(p => `<tr><td class="mono"><b>${p.symbol}</b></td><td>${sparkline([1,1+p.pnlPct/5,1+p.pnlPct/4,1+p.pnlPct/2,1+p.pnlPct])}</td><td>${pct(p.value/total)}</td><td>${money(p.value)}</td><td>${round(p.qty,4)}</td><td class="${p.pnlPct>=0?'good':'bad'}">${pct(p.pnlPct)}</td><td class="${p.pnl>=0?'good':'bad'}">${money2(p.pnl)}</td><td><span class="badge">${state.core.regime?state.core.regime.label:'—'}</span></td><td><span class="dot ${sentimentDot(p.symbol)}"></span></td><td>${p.broker}</td><td><div class="row-actions"><button class="btn small danger" onclick="exitPosition('${p.symbol}')">Exit ASAP</button><button class="btn small warn" onclick="reducePosition('${p.symbol}')">Reduce 50%</button><button class="btn small ghost" onclick="excludeSymbol('${p.symbol}')">Exclude</button></div></td></tr>`).join('')}</table></div>`;
}
async function exitPosition(symbol) { excludeSymbol(symbol, false); try { await api(`/api/broker/alpaca/position?symbol=${encodeURIComponent(symbol)}`, { method: 'DELETE' }); alert(`${symbol}: close position request sent to Alpaca Paper.`, 'good'); await syncAll(false); } catch (e) { alert(`${symbol}: close request failed: ${e.message}`, 'bad'); } safeRender('portfolio'); }
async function reducePosition(symbol) { try { await api(`/api/broker/alpaca/position?symbol=${encodeURIComponent(symbol)}&percentage=50`, { method: 'DELETE' }); alert(`${symbol}: reduce 50% request sent to Alpaca Paper.`, 'good'); await syncAll(false); } catch (e) { alert(`${symbol}: reduce request failed: ${e.message}`, 'bad'); } safeRender('portfolio'); }
function excludeSymbol(symbol, show = true) { if (!state.exclusions.includes(symbol)) state.exclusions.push(symbol); saveExclusions(); syncEngineConfig(); if (show) alert(`${symbol} excluded from future strategy buys.`, 'warn'); }

function renderOrders() {
  $('#app').innerHTML = `
    <div class="grid cols-3">${metric('Preview orders', String(state.core.orders.length), 'from latest engine run')}${metric('Journal entries', String(state.journal.length), 'local audit trail')}${metric('Broker open orders', String((state.alpaca.orders||[]).filter(o=>!['filled','canceled','expired','rejected'].includes(o.status)).length), 'Alpaca Paper')}</div>
    <div class="panel" style="margin-top:14px"><div class="panel-h"><div><h2>Order preview</h2><p class="muted small">Manual-only. Play creates decisions; this page submits paper orders.</p></div><div class="split"><button class="btn primary" onclick="submitPreviewOrders()">Send paper orders</button><button class="btn danger" onclick="killSwitch()">Kill Switch</button></div></div>${orderPreviewTable()}</div>
    <div class="panel" style="margin-top:14px"><div class="panel-h"><h2>Execution journal</h2><button class="btn ghost" onclick="exportTradeLogCSV()">Export CSV</button></div>${journalTable()}</div>`;
}
function orderPreviewTable() {
  if (!state.core.orders.length) return `<div class="empty">The engine has not calculated targets yet. Return to Control Center and click Run Strategy Engine.</div>`;
  return `<div class="table-wrap"><table><tr><th>Broker</th><th>Symbol</th><th>Side</th><th>Notional</th><th>Type</th><th>Strategy</th><th>Reason</th><th>Status</th></tr>${state.core.orders.map(o => `<tr><td>${o.broker}</td><td class="mono"><b>${o.symbol}</b></td><td>${o.side}</td><td>${money(o.notional)}</td><td>${o.orderType}</td><td>${o.strategy}</td><td class="small muted">${html(o.reason)}</td><td><span class="badge">preview</span></td></tr>`).join('')}</table></div>`;
}
function journalTable() {
  const brokerOrders = (state.alpaca.orders || []).slice(0, 30).map(o => ({ time: o.submitted_at || o.created_at, broker: 'Alpaca Paper', symbol: o.symbol, side: o.side, qty: o.qty || o.filled_qty, orderType: o.type, fillPrice: o.filled_avg_price, status: o.status, detail: o.id, strategy: 'broker sync' }));
  const rows = [...state.journal, ...brokerOrders].slice(0, 120);
  if (!rows.length) return `<div class="empty">No order journal yet.</div>`;
  return `<div class="table-wrap"><table><tr><th>Time</th><th>Broker</th><th>Symbol</th><th>Side</th><th>Notional</th><th>Qty</th><th>Type</th><th>Fill</th><th>Slippage</th><th>Status</th><th>Strategy</th><th>Detail</th></tr>${rows.map(r => `<tr><td class="small">${r.time ? timeShort(r.time) : '—'}</td><td>${r.broker}</td><td class="mono"><b>${r.symbol || '—'}</b></td><td>${r.side || '—'}</td><td>${r.notional ? money(r.notional) : '—'}</td><td>${r.qty || '—'}</td><td>${r.orderType || '—'}</td><td>${r.fillPrice || '—'}</td><td>${r.slippage || '—'}</td><td><span class="badge ${r.status==='submitted'||r.status==='filled'?'good':r.status==='rejected'?'bad':'warn'}">${r.status || '—'}</span></td><td>${html(r.strategy || '')}</td><td class="small muted">${html(r.detail || r.reason || '')}</td></tr>`).join('')}</table></div>`;
}

function renderBacktest() {
  const b = state.config.backtest;
  $('#app').innerHTML = `
    <div class="panel"><div class="panel-h"><div><h2>Backtest controls</h2><p class="muted small">Changing strategy, universe, benchmark or period marks data stale. Load real market data before running.</p></div><span class="badge ${state.backtest.stale ? 'warn' : 'good'}">${state.backtest.stale ? 'STALE' : 'READY'}</span></div>
      <div class="toolbar">
        ${selectField('Strategy','bt-strategy',b.strategy,Object.keys(STRATEGIES))}
        ${selectField('Universe','bt-universe',b.universe,['Included universe','Top 40 ranked','Current positions'])}
        ${selectField('Benchmark','bt-benchmark',b.benchmark,['SPY','QQQ','60/40','Equal weight universe'])}
        ${selectField('Period','bt-period',b.period,Object.keys(PERIODS))}
        <div class="field"><label>Start</label><input id="bt-start" type="date" value="${b.start || ''}"></div>
        <div class="field"><label>End</label><input id="bt-end" type="date" value="${b.end || ''}"></div>
        <button class="btn ghost" onclick="applyBacktestControls(); loadBacktestData()">Load Backtest Data</button>
        <button class="btn primary" onclick="applyBacktestControls(); runBacktest()" ${!state.backtest.loaded || state.backtest.stale ? 'disabled' : ''}>Run Backtest</button>
      </div>
      <div class="section-note">Data source: Alpaca market data feed <b>${state.config.dataFeed.toUpperCase()}</b>. No synthetic benchmark. Fundamentals are disabled in backtest to avoid look-ahead bias.</div>
    </div>
    ${state.backtest.error ? `<div class="alert bad" style="margin-top:14px">${html(state.backtest.error)}</div>` : ''}
    ${state.backtest.result ? backtestResultHtml() : backtestEmptyHtml()}`;
  bindBacktestControlEvents();
}
function selectField(label,id,value,opts){return `<div class="field"><label>${label}</label><select id="${id}">${opts.map(o=>`<option ${o===value?'selected':''}>${o}</option>`).join('')}</select></div>`;}
function bindBacktestControlEvents(){['bt-strategy','bt-universe','bt-benchmark','bt-period','bt-start','bt-end'].forEach(id=>{const el=$('#'+id); if(el) el.addEventListener('change',()=>{applyBacktestControls(); state.backtest.stale=true; state.backtest.loaded=false; saveConfig(); safeRender('backtest');});});}
function applyBacktestControls(){const b=state.config.backtest;b.strategy=$('#bt-strategy')?$('#bt-strategy').value:b.strategy;b.universe=$('#bt-universe')?$('#bt-universe').value:b.universe;b.benchmark=$('#bt-benchmark')?$('#bt-benchmark').value:b.benchmark;b.period=$('#bt-period')?$('#bt-period').value:b.period;b.start=$('#bt-start')?$('#bt-start').value:b.start;b.end=$('#bt-end')?$('#bt-end').value:b.end;saveConfig();}
function backtestEmptyHtml(){return `<div class="panel" style="margin-top:14px"><div class="empty">Load Backtest Data first. Then Run Backtest becomes available.</div></div>`;}
async function loadBacktestData() {
  state.backtest.error = null;
  try {
    if (!state.universe.loaded) await loadUniverse(false);
    const b = state.config.backtest;
    const win = backtestWindow(b);
    const symbols = backtestSymbols(b);
    const benchSymbols = benchmarkSymbols(b.benchmark, symbols);
    const all = unique(symbols.concat(benchSymbols)).slice(0, BACKTEST_SYMBOL_CAP + 4);
    const bars = {};
    for (const chunk of chunks(all, 80)) {
      const res = await api(`/api/market/bars?symbols=${encodeURIComponent(chunk.join(','))}&timeframe=1Day&start=${win.start}&end=${win.end}&feed=${state.config.dataFeed}&limit=10000`);
      Object.assign(bars, res.bars || {});
    }
    const available = all.filter(s => (bars[s] || []).length > 40);
    if (!available.length) throw new Error('No usable bars returned from Alpaca for the selected window/universe.');
    state.backtest.dataset = { bars, symbols, benchmarkSymbols: benchSymbols, available, window: win, config: { ...b }, loadedAt: new Date().toISOString(), source: `Alpaca market data (${state.config.dataFeed})` };
    state.backtest.loaded = true;
    state.backtest.stale = false;
    state.backtest.result = null;
    alert(`Backtest data loaded: ${available.length}/${all.length} symbols with usable bars.`, 'good');
    safeRender('backtest');
  } catch (e) {
    state.backtest.error = e.message;
    state.backtest.loaded = false;
    state.backtest.stale = true;
    alert(`Backtest data load failed: ${e.message}`, 'bad');
    safeRender('backtest');
  }
}
function backtestSymbols(b) {
  if (b.universe === 'Current positions') return normPositions().map(p => p.symbol).slice(0, BACKTEST_SYMBOL_CAP);
  const base = state.universe.included.length ? state.universe.included : [];
  if (b.universe === 'Top 40 ranked') return base.slice(0, 40).map(a => a.symbol);
  return base.slice(0, BACKTEST_SYMBOL_CAP).map(a => a.symbol);
}
function benchmarkSymbols(benchmark, universeSymbols) { if (benchmark === 'QQQ') return ['QQQ']; if (benchmark === '60/40') return ['SPY','TLT']; if (benchmark === 'Equal weight universe') return universeSymbols.slice(0, BACKTEST_SYMBOL_CAP); return ['SPY']; }
function backtestWindow(b) { const end = b.end || todayISO(); const start = b.start || PERIODS[b.period] || monthsAgo(12); return { start, end }; }

function runBacktest() {
  try {
    if (!state.backtest.loaded || state.backtest.stale || !state.backtest.dataset) throw new Error('Load Backtest Data first. Current dataset is stale or missing.');
    const ds = state.backtest.dataset;
    const cfg = ds.config;
    const strat = STRATEGIES[cfg.strategy] || STRATEGIES.Balanced;
    const dates = commonDates(ds.bars, ds.symbols).filter(d => d >= ds.window.start && d <= ds.window.end);
    if (dates.length < 60) throw new Error('Not enough overlapping history for a serious backtest.');
    let equity = 100000;
    let cash = equity;
    let holdings = {};
    let lastWeights = {};
    const eq = [];
    const dd = [];
    const trades = [];
    const allocations = [];
    const regimes = [];
    let peak = equity;
    const rebalanceEvery = cfg.rebalance === 'Weekly' ? 5 : 21;

    for (let i = 60; i < dates.length; i++) {
      const date = dates[i];
      const prev = dates[i - 1];
      const dayRet = portfolioDailyReturn(holdings, ds.bars, date, prev);
      equity *= (1 + dayRet);
      peak = Math.max(peak, equity);
      eq.push({ date, value: round(equity, 2) });
      dd.push({ date, value: equity / peak - 1 });
      if ((i - 60) % rebalanceEvery === 0) {
        const ranked = ds.symbols.filter(s => (ds.bars[s] || []).length > i).map(s => historicalScore(s, ds.bars[s], i)).filter(Boolean).sort((a, b) => b.score - a.score);
        const selected = ranked.slice(0, strat.slots);
        const gross = Math.min(strat.gross, 1 - strat.cashFloor);
        const totalScore = selected.reduce((s, x) => s + Math.max(1, x.score), 0) || 1;
        const weights = {};
        selected.forEach(x => { weights[x.symbol] = Math.min(strat.maxPosition, gross * Math.max(1, x.score) / totalScore); });
        const sumW = Object.values(weights).reduce((a, b) => a + b, 0);
        if (sumW > gross) Object.keys(weights).forEach(k => { weights[k] *= gross / sumW; });
        const turnover = turnoverFromWeights(lastWeights, weights);
        const cost = equity * turnover * (Number(cfg.slippageBps || 0) + Number(cfg.commissionBps || 0)) / 10000;
        equity -= cost;
        trades.push({ date, action: 'rebalance', turnover, cost, holdings: Object.keys(weights).length, top: selected[0] ? selected[0].symbol : '—', reason: `monthly rebalance using ${cfg.strategy} ranking` });
        holdings = weights;
        lastWeights = { ...weights };
        allocations.push({ date, weights: { ...weights } });
        regimes.push({ date, regime: historicalRegime(ds.bars.SPY || [], i) });
      }
    }
    const benchmarkEquity = computeBenchmark(ds, cfg.benchmark, dates.slice(60));
    const returns = eq.map((p, i) => i ? p.value / eq[i - 1].value - 1 : 0).slice(1);
    const benchReturns = benchmarkEquity.map((p, i) => i ? p.value / benchmarkEquity[i - 1].value - 1 : 0).slice(1);
    state.backtest.result = { equity: eq, drawdown: dd, benchmarkEquity, metrics: metrics(eq, returns, benchReturns), trades, allocations, regimes, monthly: monthlyReturns(eq), attribution: attributionFromTrades(trades, eq), config: cfg, source: ds.source, window: ds.window, available: ds.available.length };
    alert('Backtest completed with real loaded market data.', 'good');
    safeRender('backtest');
  } catch (e) {
    state.backtest.error = e.message;
    alert(`Backtest failed: ${e.message}`, 'bad');
    safeRender('backtest');
  }
}
function backtestResultHtml() {
  const r = state.backtest.result;
  return `<div class="grid cols-4" style="margin-top:14px">${metric('CAGR', pct(r.metrics.cagr), r.source)}${metric('Sharpe', round(r.metrics.sharpe,2), 'daily annualized')}${metric('Max drawdown', pct(r.metrics.maxDrawdown), 'worst peak-to-trough', 'bad')}${metric('Win rate', pct(r.metrics.winRate), 'positive days')}</div>
    <div class="grid cols-2" style="margin-top:14px"><div class="panel"><div class="panel-h"><h2>Equity vs benchmark</h2><span class="badge">${r.config.benchmark}</span></div><div class="chart">${lineChart(r.equity.map(x=>x.value), r.benchmarkEquity.map(x=>x.value), 'Algohns', r.config.benchmark)}</div></div><div class="panel"><h2>Drawdown</h2><div class="chart small">${drawdownChart(r.equity.map(x=>x.value))}</div><div class="grid cols-2" style="margin-top:12px">${metric('Sortino', round(r.metrics.sortino,2), 'downside risk')}${metric('Calmar', round(r.metrics.calmar,2), 'CAGR / max DD')}</div></div></div>
    <div class="grid cols-2" style="margin-top:14px"><div class="panel"><div class="panel-h"><h2>Monthly returns</h2><button class="btn ghost" onclick="exportBacktestReport()">Export report</button></div>${monthlyTable(r.monthly)}</div><div class="panel"><h2>Trade log</h2>${tradeLogTable(r.trades)}</div></div>
    <div class="grid cols-2" style="margin-top:14px"><div class="panel"><h2>P&L attribution</h2>${attributionTable(r)}</div><div class="panel"><h2>Regime history</h2>${regimeHistoryTable(r.regimes)}</div></div>`;
}
function monthlyTable(rows){return `<div class="table-wrap"><table><tr><th>Month</th><th>Return</th></tr>${rows.map(r=>`<tr><td>${r.month}</td><td class="${r.ret>=0?'good':'bad'}">${pct(r.ret)}</td></tr>`).join('')}</table></div>`;}
function tradeLogTable(rows){return `<div class="table-wrap"><table><tr><th>Date</th><th>Action</th><th>Top</th><th>Turnover</th><th>Cost</th><th>Reason</th></tr>${rows.slice(-80).reverse().map(t=>`<tr><td>${t.date}</td><td>${t.action}</td><td class="mono">${t.top}</td><td>${pct(t.turnover)}</td><td>${money2(t.cost)}</td><td class="small muted">${t.reason}</td></tr>`).join('')}</table></div>`;}
function attributionTable(r){return `<div class="table-wrap"><table><tr><th>Component</th><th>Value</th></tr><tr><td>Total return</td><td>${pct(r.metrics.totalReturn)}</td></tr><tr><td>Benchmark return</td><td>${pct(r.metrics.benchmarkReturn)}</td></tr><tr><td>Excess return</td><td>${pct(r.metrics.totalReturn-r.metrics.benchmarkReturn)}</td></tr><tr><td>Total rebalance costs</td><td>${money2(r.trades.reduce((s,t)=>s+t.cost,0))}</td></tr><tr><td>Positive months</td><td>${pct(r.metrics.positiveMonths)}</td></tr></table></div>`;}
function regimeHistoryTable(rows){if(!rows.length)return`<div class="empty">No regime history.</div>`;return `<div class="table-wrap"><table><tr><th>Date</th><th>Regime</th></tr>${rows.slice(-30).reverse().map(r=>`<tr><td>${r.date}</td><td><span class="badge">${r.regime}</span></td></tr>`).join('')}</table></div>`;}

function renderRisk() {
  const returns = state.backtest.result ? state.backtest.result.equity.map((x,i,a)=>i?x.value/a[i-1].value-1:0).slice(1) : portfolioHistoryReturns();
  const risk = calcRisk(returns);
  const positions = normPositions();
  const concentration = concentrationRisk(positions);
  const beta = state.backtest.result ? state.backtest.result.metrics.beta : null;
  $('#app').innerHTML = `
    <div class="grid cols-4">${metric('Current drawdown', pct(currentDrawdown()), 'live/local')}${metric('VaR 95%', pct(risk.var95), '1-day historical', 'bad')}${metric('CVaR 95%', pct(risk.cvar95), 'tail average', 'bad')}${metric('Volatility', pct(risk.vol), 'annualized')}</div>
    <div class="grid cols-2" style="margin-top:14px"><div class="panel"><h2>Risk gauges</h2>${riskGauge('Concentration risk', concentration)}${riskGauge('Correlation risk', Math.min(100, positions.length ? 85 / Math.sqrt(positions.length) : 0))}${riskGauge('Beta vs SPY', beta === null ? 50 : clamp(Math.abs(beta)*45,0,100))}<p class="muted small">Plain-language: ${riskPlainText(risk, concentration, beta)}</p></div><div class="panel"><h2>Stress tests</h2>${stressTable(beta, concentration)}</div></div>
    <div class="panel" style="margin-top:14px"><h2>Exposure detail</h2>${exposureTable(positions)}</div>`;
}
function riskGauge(label, score){return `<div style="margin:14px 0"><div class="split" style="justify-content:space-between"><b>${label}</b><span>${round(score,1)}/100</span></div><div class="risk-gauge"><i style="left:${clamp(score,0,100)}%"></i></div></div>`;}
function riskPlainText(risk, concentration, beta){if(!risk.n)return 'Risk uses backtest returns when available; otherwise live history. Load/run a backtest for stronger diagnostics.'; const danger = risk.var95 < -0.03 || concentration > 35; return danger ? 'Portfolio risk is elevated due to tail-loss estimate or position concentration.' : 'Portfolio risk is currently within normal paper-monitoring boundaries.';}
function stressTable(beta, concentration){const b = beta === null ? 1 : beta; const rows=[['COVID crash',-0.34*b],['2022 bear market',-0.24*b - concentration/1000],['2008 GFC',-0.45*b],['Dot-com bust',-0.38*b],['Rates shock',-0.14*b - concentration/1200]];return `<div class="table-wrap"><table><tr><th>Scenario</th><th>Estimated impact</th><th>Explanation</th></tr>${rows.map(r=>`<tr><td>${r[0]}</td><td class="bad">${pct(r[1])}</td><td class="small muted">Estimated from equity beta and concentration. Use as risk control, not prediction.</td></tr>`).join('')}</table></div>`;}

function renderNews() {
  $('#app').innerHTML = `
    <div class="panel"><div class="panel-h"><div><h2>News Intelligence</h2><p class="muted small">Portfolio-linked news, ticker sentiment flags and event blackout notes.</p></div><button class="btn primary" onclick="loadNews()">Load portfolio news</button></div>${newsSummary()}</div>
    <div class="panel" style="margin-top:14px"><h2>News feed</h2>${newsTable()}</div>`;
}
async function loadNews() {
  const symbols = normPositions().map(p => p.symbol).slice(0, 25);
  if (!symbols.length) return alert('No portfolio symbols synced. Sync portfolio first.', 'warn');
  try {
    const res = await api(`/api/market/news?symbols=${encodeURIComponent(symbols.join(','))}&limit=40`);
    state.news.items = res.news || [];
    state.news.loadedAt = new Date().toISOString();
    state.news.error = null;
    buildNewsClusters();
    alert(`Loaded ${state.news.items.length} portfolio-linked news items.`, 'good');
    safeRender('news');
  } catch (e) {
    state.news.error = e.message;
    alert(`News load failed: ${e.message}`, 'bad');
    safeRender('news');
  }
}
function buildNewsClusters(){const clusters={};for(const n of state.news.items){for(const s of n.symbols||[]){clusters[s]=clusters[s]||{symbol:s,count:0,negative:0};clusters[s].count++; if(/fall|drop|probe|lawsuit|cuts|miss|risk|warning|downgrade|recall/i.test((n.headline||'')+' '+(n.summary||''))) clusters[s].negative++;}}state.news.clusters=Object.values(clusters).sort((a,b)=>b.negative-a.negative||b.count-a.count);}
function newsSummary(){if(state.news.error)return`<div class="alert bad">${html(state.news.error)}</div>`;if(!state.news.items.length)return`<div class="empty">No news loaded yet.</div>`;return `<div class="grid cols-3">${state.news.clusters.slice(0,3).map(c=>metric(c.symbol, `${c.negative}/${c.count}`, c.negative>=3?'negative cluster: new buys should be blocked':'normal news flow', c.negative>=3?'bad':'good')).join('')}</div>`;}
function newsTable(){if(!state.news.items.length)return`<div class="empty">Load portfolio news to populate this section.</div>`;return `<div class="table-wrap"><table><tr><th>Time</th><th>Symbols</th><th>Headline</th><th>Risk note</th></tr>${state.news.items.slice(0,80).map(n=>{const neg=/fall|drop|probe|lawsuit|cuts|miss|risk|warning|downgrade|recall/i.test((n.headline||'')+' '+(n.summary||''));return `<tr><td class="small">${n.created_at?timeShort(n.created_at):'—'}</td><td class="mono">${(n.symbols||[]).join(', ')}</td><td>${html(n.headline||'')}</td><td>${neg?'<span class="badge bad">negative risk</span>':'<span class="badge">monitor</span>'}</td></tr>`;}).join('')}</table></div>`;}
function sentimentDot(symbol){const c=state.news.clusters.find(x=>x.symbol===symbol); if(!c)return'warn'; return c.negative>=3?'bad':c.negative?'warn':'good';}

function renderSettings() {
  const ok = state.alpaca.status && state.alpaca.status.connected;
  $('#app').innerHTML = `
    <div class="grid cols-2"><div class="panel"><h2>Connection</h2><div class="conn-row"><span>Alpaca Paper</span><strong class="${ok?'good':'bad'}">${ok?'CONNECTED':'NOT CONNECTED'}</strong></div><p class="muted small">${html(state.alpaca.status ? (state.alpaca.status.reason || 'Paper account reachable.') : 'Click Test connections.')}</p><div class="split"><button class="btn primary" onclick="testConnection(true)">Test connections</button><button class="btn ghost" onclick="checkBuild().then(()=>safeRender('settings'))">Live build check</button></div></div><div class="panel"><h2>Build</h2>${buildTable()}</div></div>
    <div class="grid cols-2" style="margin-top:14px"><div class="panel"><h2>Secrets expected</h2><div class="table-wrap"><table><tr><th>Name</th><th>Purpose</th></tr><tr><td class="mono">ALPACA_API_KEY</td><td>Alpaca Paper API key</td></tr><tr><td class="mono">ALPACA_SECRET_KEY</td><td>Alpaca Paper secret key</td></tr><tr><td class="mono">ALPACA_BASE_URL</td><td>Must remain https://paper-api.alpaca.markets</td></tr></table></div><p class="muted small">The deployment scripts store reusable local keys in <span class="kbd">%USERPROFILE%\\.algohns</span>.</p></div><div class="panel"><h2>Exports</h2><div class="split"><button class="btn ghost" onclick="exportSnapshotHTML()">Export snapshot HTML</button><button class="btn ghost" onclick="exportTradeLogCSV()">Export trade log CSV</button><button class="btn ghost" onclick="exportStrategyJSON()">Export strategy JSON</button><button class="btn ghost" onclick="exportBacktestReport()">Export backtest report</button></div><p class="muted small">Files are static and shareable as read-only snapshots.</p></div></div>
    <div class="panel" style="margin-top:14px"><h2>Safety</h2><div class="section-note"><b>Real money locked.</b> The worker refuses any non-paper Alpaca base URL. Play/Pause controls algorithm state only; order submission is manual from Orders & Control.</div></div>`;
}
function buildTable(){if(!state.buildInfo)return`<div class="empty">Build check not loaded.</div>`;return `<div class="table-wrap"><table><tr><td>App build</td><td class="mono">${APP_BUILD}</td></tr><tr><td>Worker build</td><td class="mono ${state.buildInfo.build===APP_BUILD?'good':'bad'}">${html(state.buildInfo.build||'—')}</td></tr><tr><td>URL</td><td class="mono">${html(state.buildInfo.url||'—')}</td></tr><tr><td>Match</td><td>${state.buildInfo.build===APP_BUILD?'<span class="badge good">updated</span>':'<span class="badge bad">redeploy needed</span>'}</td></tr></table></div>`;}

function metric(label, value, sub, cls='') { return `<div class="metric"><div class="label">${label}</div><div class="value ${cls}">${value}</div><div class="sub">${html(sub || '')}</div></div>`; }
function portfolioValue() { const a = state.alpaca.account || {}; return Number(a.portfolio_value || a.equity || 0) || 100000; }
function dailyPnl() { const a = state.alpaca.account || {}; return Number(a.equity || 0) - Number(a.last_equity || a.equity || 0); }
function totalPnl() { return normPositions().reduce((s, p) => s + p.pnl, 0); }
function portfolioHistorySeries() { const h = state.alpaca.history; const arr = h && Array.isArray(h.equity) ? h.equity.map(Number).filter(x => x > 0) : []; return arr.length > 2 ? arr : [100000,100400,100120,101000,100780,101420,101900]; }
function currentDrawdown() { const s = portfolioHistorySeries(); let peak = s[0] || 1, dd = 0; for (const v of s) { peak = Math.max(peak, v); dd = Math.min(dd, v / peak - 1); } return dd; }
function computePortfolioStatus() { const dd = currentDrawdown(); if (state.killed) return 'Stress'; if (dd < -0.16) return 'Stress'; if (dd < -0.10) return 'Risk-off'; if (dd < -0.05) return 'Watch'; return 'Healthy'; }
function statusBadge(s){return s==='Healthy'?'good':s==='Watch'?'warn':s==='Risk-off'?'bad':'bad';}
function buildDecisionNarrative(targets, regime) { const t = targets[0]; if (!t) return `${timeNow()} — Strategy engine moved to cash because no asset passed current filters.`; return `${timeNow()} — Strategy engine increased exposure to ${t.symbol} and ${Math.max(0, targets.length-1)} other names because market regime is ${regime ? regime.label : 'Neutral'}, breadth/liquidity filters passed, and ${state.config.strategy} risk limits allow ${pct(targets.reduce((s,x)=>s+x.targetWeight,0))} gross exposure.`; }

function lineChart(series, benchmark, label1='A', label2='B') {
  const a = normalizeChartSeries(series); const b = normalizeChartSeries(benchmark || []); const w=900,h=300,p=28; const all=a.concat(b).filter(Number.isFinite); if(all.length<2)return`<div class="empty">No chart data.</div>`; const min=Math.min(...all),max=Math.max(...all); const scaleY=v=>h-p-((v-min)/(max-min||1))*(h-2*p); const pts=s=>s.map((v,i)=>`${p+i*(w-2*p)/Math.max(1,s.length-1)},${scaleY(v)}`).join(' '); return `<svg class="svgchart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="M${p},${h-p} L${p},${p} M${p},${h-p} L${w-p},${h-p}" stroke="#1d2a3d" fill="none"/><polyline class="line-main" points="${pts(a)}"/>${b.length ? `<polyline class="line-bench" points="${pts(b)}"/>` : ''}<text x="${p}" y="18" class="axis-text">${label1}</text>${b.length ? `<text x="${w-p-100}" y="18" class="axis-text">${label2}</text>` : ''}</svg>`;
}
function drawdownChart(series){const s=normalizeChartSeries(series); if(s.length<2)return`<div class="empty">No drawdown data.</div>`; let peak=s[0], dd=s.map(v=>{peak=Math.max(peak,v);return v/peak-1;}); const w=900,h=150,p=22,min=Math.min(...dd),max=0; const y=v=>h-p-((v-min)/(max-min||1))*(h-2*p); const pts=dd.map((v,i)=>`${p+i*(w-2*p)/Math.max(1,dd.length-1)},${y(v)}`).join(' '); return `<svg class="svgchart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><line x1="${p}" y1="${y(0)}" x2="${w-p}" y2="${y(0)}" stroke="#1d2a3d"/><polyline class="line-dd" points="${pts}"/><text x="${p}" y="18" class="axis-text">Drawdown</text></svg>`;}
function normalizeChartSeries(series){ if(!Array.isArray(series)) return []; return series.map(x=>typeof x==='number'?x:Number(x && (x.value ?? x.equity ?? x.close))).filter(Number.isFinite); }
function sparkline(s){const data=normalizeChartSeries(s); const w=90,h=26,p=2; if(data.length<2)return''; const min=Math.min(...data),max=Math.max(...data); const pts=data.map((v,i)=>`${p+i*(w-2*p)/Math.max(1,data.length-1)},${h-p-((v-min)/(max-min||1))*(h-2*p)}`).join(' '); return `<svg class="spark" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="#38bdf8" stroke-width="2"/></svg>`;}

function commonDates(bars, symbols){const sets=symbols.map(s=>new Set((bars[s]||[]).map(b=>b.t.slice(0,10)))).filter(s=>s.size); if(!sets.length)return[]; let common=[...sets[0]]; for(const set of sets.slice(1,Math.min(20,sets.length))) common=common.filter(d=>set.has(d)); return common.sort();}
function closeSeries(bars){return (bars||[]).map(b=>Number(b.c)).filter(Number.isFinite);}
function priceOn(bars,date){const b=(bars||[]).find(x=>x.t.slice(0,10)===date); return b?Number(b.c):null;}
function portfolioDailyReturn(weights,bars,date,prev){let r=0; for(const [s,w] of Object.entries(weights)){const p0=priceOn(bars[s],prev),p1=priceOn(bars[s],date); if(p0&&p1)r+=w*(p1/p0-1);} return r;}
function historicalScore(symbol,bars,i){if(!bars||i<55)return null; const c=closeSeries(bars.slice(0,i+1)); const mom63=returnN(c,Math.min(63,c.length-1)); const mom21=returnN(c,21); const vol=realizedVol(c.slice(-21)); const score=clamp(50+mom63*140+mom21*90-vol*40,0,100); return {symbol,score};}
function computeBenchmark(ds,benchmark,dates){ if(benchmark==='Equal weight universe'){let eq=100000;const out=[];for(let i=1;i<dates.length;i++){const d=dates[i],p=dates[i-1];let ret=0,n=0;for(const s of ds.symbols){const p0=priceOn(ds.bars[s],p),p1=priceOn(ds.bars[s],d); if(p0&&p1){ret+=p1/p0-1;n++;}} eq*=1+(n?ret/n:0);out.push({date:d,value:eq});} return out;} if(benchmark==='60/40') return blendedBenchmark(ds.bars,dates,{SPY:.6,TLT:.4}); return singleBenchmark(ds.bars[benchmark]||ds.bars.SPY||[],dates);}
function singleBenchmark(bars,dates){let eq=100000,out=[];for(let i=1;i<dates.length;i++){const p0=priceOn(bars,dates[i-1]),p1=priceOn(bars,dates[i]);if(p0&&p1)eq*=p1/p0;out.push({date:dates[i],value:eq});}return out;}
function blendedBenchmark(bars,dates,w){let eq=100000,out=[];for(let i=1;i<dates.length;i++){let ret=0;for(const [s,wt] of Object.entries(w)){const p0=priceOn(bars[s],dates[i-1]),p1=priceOn(bars[s],dates[i]); if(p0&&p1)ret+=wt*(p1/p0-1);}eq*=1+ret;out.push({date:dates[i],value:eq});}return out;}
function metrics(eq,returns,benchReturns){const start=eq[0]?.value||100000,end=eq.at(-1)?.value||start,years=Math.max(1/252,returns.length/252),total=end/start-1,cagr=Math.pow(end/start,1/years)-1,vol=stdev(returns)*Math.sqrt(252),down=returns.filter(x=>x<0),sortino=down.length?mean(returns)*252/(stdev(down)*Math.sqrt(252)||1):0,sharpe=vol?mean(returns)*252/vol:0,maxDD=maxDrawdown(eq.map(x=>x.value)),calmar=Math.abs(maxDD)?cagr/Math.abs(maxDD):0,winRate=returns.filter(x=>x>0).length/Math.max(1,returns.length),bench=benchReturns.length?benchReturns.reduce((a,r)=>a*(1+r),1)-1:0,beta=calcBeta(returns,benchReturns);return{totalReturn:total,cagr,vol,sortino,sharpe,maxDrawdown:maxDD,calmar,winRate,benchmarkReturn:bench,beta,positiveMonths:monthlyReturns(eq).filter(x=>x.ret>0).length/Math.max(1,monthlyReturns(eq).length)};}
function monthlyReturns(eq){const m={};eq.forEach(x=>{const k=x.date.slice(0,7);m[k]=m[k]||{first:x.value,last:x.value};m[k].last=x.value;});return Object.entries(m).map(([month,v])=>({month,ret:v.last/v.first-1}));}
function attributionFromTrades(trades,eq){return{costs:trades.reduce((s,t)=>s+t.cost,0),ending:eq.at(-1)?.value||0};}
function turnoverFromWeights(a,b){const keys=unique(Object.keys(a).concat(Object.keys(b)));return keys.reduce((s,k)=>s+Math.abs((b[k]||0)-(a[k]||0)),0)/2;}
function historicalRegime(spyBars,i){const c=closeSeries(spyBars.slice(0,i+1));const r=returnN(c,63),v=realizedVol(c.slice(-21));if(r>.06&&v<.22)return'Risk-on';if(r<-.12&&v>.35)return'Crash';if(r<-.05)return'Risk-off';if(r>.02)return'Soft-landing';return'Neutral';}
function portfolioHistoryReturns(){const s=portfolioHistorySeries();return s.map((v,i)=>i?v/s[i-1]-1:0).slice(1);}
function calcRisk(returns){if(!returns.length)return{n:0,var95:0,cvar95:0,vol:0};const sorted=[...returns].sort((a,b)=>a-b);const idx=Math.floor(sorted.length*.05);const var95=sorted[idx]||0;const tail=sorted.slice(0,idx+1);return{n:returns.length,var95,cvar95:mean(tail),vol:stdev(returns)*Math.sqrt(252)};}
function concentrationRisk(positions){const total=positions.reduce((s,p)=>s+p.value,0)||1;return positions.reduce((m,p)=>Math.max(m,p.value/total*100),0);}
function maxDrawdown(series){let peak=series[0]||1,dd=0;for(const v of series){peak=Math.max(peak,v);dd=Math.min(dd,v/peak-1);}return dd;}
function calcBeta(a,b){const n=Math.min(a.length,b.length);if(n<10)return null;const x=b.slice(-n),y=a.slice(-n),mx=mean(x),my=mean(y);let cov=0,va=0;for(let i=0;i<n;i++){cov+=(x[i]-mx)*(y[i]-my);va+=(x[i]-mx)**2;}return va?cov/va:null;}
function returnN(s,n){if(!s||s.length<n+1)return 0;return s.at(-1)/s.at(-1-n)-1;}
function realizedVol(s){const r=s.map((v,i)=>i?v/s[i-1]-1:0).slice(1);return stdev(r)*Math.sqrt(252);}
function mean(a){return a.length?a.reduce((s,x)=>s+x,0)/a.length:0;}function stdev(a){if(a.length<2)return 0;const m=mean(a);return Math.sqrt(mean(a.map(x=>(x-m)**2)));}
function snapshotChange(s){if(!s||!s.dailyBar||!s.prevDailyBar||!s.prevDailyBar.c)return 0;return Number(s.dailyBar.c)/Number(s.prevDailyBar.c)-1;}

function exportSnapshotHTML(){const rows=normPositions().map(p=>`<tr><td>${p.symbol}</td><td>${money(p.value)}</td><td>${money2(p.pnl)}</td><td>${pct(p.pnlPct)}</td></tr>`).join('');download('algohns_v11_portfolio_snapshot.html',`<!doctype html><meta charset=utf-8><title>Algohns V11 Snapshot</title><body style="font-family:system-ui;padding:24px"><h1>Algohns V11 Portfolio Snapshot</h1><p>${new Date().toISOString()}</p><p>Portfolio ${money(portfolioValue())} · Strategy ${state.config.strategy} · Regime ${state.core.regime?state.core.regime.label:'—'}</p><table border=1 cellpadding=7 style="border-collapse:collapse"><tr><th>Symbol</th><th>Value</th><th>P&L</th><th>P&L %</th></tr>${rows}</table><p>Alpaca Paper only. Real money locked. Not investment advice.</p></body>`,'text/html');}
function exportTradeLogCSV(){const rows=[['time','broker','symbol','side','notional','qty','orderType','fillPrice','slippage','status','strategy','reason']].concat(state.journal.map(r=>[r.time,r.broker,r.symbol,r.side,r.notional,r.qty,r.orderType,r.fillPrice,r.slippage,r.status,r.strategy,(r.reason||r.detail||'').replace(/\n/g,' ')]));download('algohns_v11_trade_log.csv',rows.map(r=>r.map(csv).join(',')).join('\n'),'text/csv');}
function exportStrategyJSON(){download('algohns_v11_strategy.json',JSON.stringify({version:APP_VERSION,build:APP_BUILD,config:state.config,targets:state.core.targets,exclusions:state.exclusions},null,2),'application/json');}
function exportBacktestReport(){if(!state.backtest.result)return alert('Run a backtest first.', 'warn');const r=state.backtest.result;download('algohns_v11_backtest_report.html',`<!doctype html><meta charset=utf-8><title>Algohns V11 Backtest</title><body style="font-family:system-ui;padding:24px;max-width:900px"><h1>Algohns V11 Backtest Report</h1><p>${new Date().toISOString()}</p><p>Strategy ${r.config.strategy} · Benchmark ${r.config.benchmark} · Window ${r.window.start} → ${r.window.end} · Source ${r.source}</p><ul><li>CAGR ${pct(r.metrics.cagr)}</li><li>Sharpe ${round(r.metrics.sharpe,2)}</li><li>Max drawdown ${pct(r.metrics.maxDrawdown)}</li><li>Win rate ${pct(r.metrics.winRate)}</li></ul><p>Fundamentals disabled to avoid look-ahead bias. Alpaca market data used. Not investment advice.</p></body>`,'text/html');}
function download(name,content,type){const blob=new Blob([content],{type});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),500);}
function csv(v){const s=String(v??'');return /[",\n]/.test(s)?`"${s.replace(/"/g,'""')}"`:s;}

function html(s){return String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function money(v){const n=Number(v||0);return n.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:n>=1000?0:2});}
function money2(v){const n=Number(v||0);return (n<0?'-':'')+Math.abs(n).toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2});}
function pct(v){if(!Number.isFinite(Number(v)))return'—';return (Number(v)*100).toFixed(2)+'%';}
function int(v){return Number(v||0).toLocaleString('en-US',{maximumFractionDigits:0});}
function round(v,d=2){const p=10**d;return Math.round(Number(v||0)*p)/p;}
function clamp(v,a,b){return Math.max(a,Math.min(b,Number(v)||0));}
function num(v){const n=Number(v);return Number.isFinite(n)?n:null;}
function parseSymbols(raw){return unique(String(raw||'').split(/[ ,\n\t]+/).map(s=>s.trim().toUpperCase()).filter(Boolean));}
function chunks(a,n){const out=[];for(let i=0;i<a.length;i+=n)out.push(a.slice(i,i+n));return out;}
function unique(a){return Array.from(new Set(a.filter(Boolean)));}
function monthsAgo(n){const d=new Date();d.setMonth(d.getMonth()-n);return d.toISOString().slice(0,10);}function isoDaysAgo(n){const d=new Date(Date.now()-n*86400000);return d.toISOString().slice(0,10);}function todayISO(){return new Date().toISOString().slice(0,10);}function timeShort(ts){try{return new Date(ts).toLocaleString([],{month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit'});}catch(_){return ts;}}function timeNow(){return new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});}

Object.assign(window, { setPage, runEngine, loadUniverse, syncAll, testConnection, checkBuild, playEngine, pauseEngine, killSwitch, setStrategy, updateConfig, markUniverseStale, submitPreviewOrders, exitPosition, reducePosition, excludeSymbol, loadBacktestData, runBacktest, applyBacktestControls, loadNews, exportSnapshotHTML, exportTradeLogCSV, exportStrategyJSON, exportBacktestReport, syncEngineState, state });
