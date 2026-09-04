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

  return json({ error: 'Unknown API route', path: pathname, build: APP_BUILD }, 404);
}

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
