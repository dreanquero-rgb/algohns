/* Algohns V9.2 — fix & polish, broker-isolated portfolio views, stable charts, Alpaca Demo + eToro Demo only. */
'use strict';

const VERSION = '9.2 Fix & Polish';
const CORE_SYMBOLS = ['SPY','QQQ','IWM','RSP','XLK','XLF','XLV','XLE','XLI','XLY','XLP','GLD','SLV','USO','BIL','SHY','IEF','TLT','LQD','HYG','VNQ','SH','PSQ'];
const ETF_META = {
  SPY:['S&P 500','Equity','US Broad Market'], QQQ:['Nasdaq 100','Equity','US Growth'], IWM:['Russell 2000','Equity','Small Caps'], RSP:['Equal Weight S&P 500','Equity','Equal Weight'],
  XLK:['Technology ETF','Sector ETF','Technology'], XLF:['Financials ETF','Sector ETF','Financials'], XLV:['Health Care ETF','Sector ETF','Health Care'], XLE:['Energy ETF','Sector ETF','Energy'], XLI:['Industrials ETF','Sector ETF','Industrials'], XLY:['Consumer Discretionary ETF','Sector ETF','Consumer'], XLP:['Consumer Staples ETF','Sector ETF','Consumer'],
  GLD:['Gold ETF','Commodity','Gold'], SLV:['Silver ETF','Commodity','Silver'], USO:['Oil ETF','Commodity','Oil'], BIL:['T-Bills ETF','Cash/T-Bills','Cash'], SHY:['Short Treasury ETF','Bonds','Treasuries'], IEF:['Intermediate Treasury ETF','Bonds','Treasuries'], TLT:['Long Treasury ETF','Bonds','Treasuries'], LQD:['Investment Grade Credit','Credit','Credit'], HYG:['High Yield Credit','Credit','Credit'], VNQ:['REIT ETF','Real Assets','Real Estate'], SH:['Short S&P 500','Short Exposure','Hedge'], PSQ:['Short Nasdaq','Short Exposure','Hedge']
};

const DEFAULT_CONFIG = {
  mode: 'Balanced',
  realMoneyLocked: true,
  activeBrokers: { alpaca: true, etoro: true },
  universe: { alpaca: true, etoro: true, custom: '' },
  filters: { minPrice: 3, minDollarVolume: 1000000, minBars: 180 },
  risk: { maxPosition: 0.10, maxGross: 1.25, cashFloor: 0.08, maxDailyTurnover: 0.20, autoKillDrawdown: -0.12 },
  backtest: { period: '3Y', benchmark: 'SPY', universe: 'Visible filtered universe' },
  execution: { autonomousDemo: true, allowAlpacaPaper: true, allowEtoroDemo: true, killSwitch: false, minTradeValue: 25 }
};

const state = {
  view: 'control',
  running: false,
  timer: null,
  config: loadConfig(),
  log: [],
  prices: {},
  quotes: {},
  news: [],
  alpaca: { status:null, account:null, positions:[], orders:[], assets:[], history:null, lastSync:null },
  etoro: { status:null, me:null, portfolio:null, positions:[], orders:[], assets:[], lastSync:null },
  core: { regime:null, targets:{}, rank:[], lastDecision:'No decision yet. Sync brokers, load universe and run strategy engine.', report:null },
  backtest: { stale:true, loaded:false, symbols:[], result:null },
  orders: { alpaca:[], etoro:[] },
  executionResults: { alpaca:null, etoro:null },
  orderDiagnostics: { alpaca:[], etoro:[] },
  navOpen: { portfolio:true, orders:true },
  chartRange: { combined:'All', alpaca:'All', etoro:'All' }
};

function $(id){ return document.getElementById(id); }
function html(s=''){ return String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[c])); }
function money(v){ const n=Number(v); return Number.isFinite(n) ? n.toLocaleString(undefined,{style:'currency',currency:'USD',maximumFractionDigits:0}) : '—'; }
function pct(v){ const n=Number(v); return Number.isFinite(n) ? (n*100).toFixed(1)+'%' : '—'; }
function num(v,d=2){ const n=Number(v); return Number.isFinite(n) ? n.toFixed(d) : '—'; }
function avg(a){ const b=a.filter(Number.isFinite); return b.length ? b.reduce((x,y)=>x+y,0)/b.length : 0; }
function std(a){ const m=avg(a); return Math.sqrt(avg(a.map(x => (x-m)**2))); }
function last(a){ return a && a.length ? a[a.length-1] : null; }
function returns(arr){ const out=[]; for(let i=1;i<arr.length;i++){ if(arr[i]?.close && arr[i-1]?.close) out.push(arr[i].close/arr[i-1].close-1); } return out; }
function clamp(x,a,b){ return Math.max(a, Math.min(b, x)); }
function norm(x,a,b){ return clamp((x-a)/(b-a),0,1); }
function loadConfig(){ try{ return merge(DEFAULT_CONFIG, JSON.parse(localStorage.getItem('algohns_v9_config') || '{}')); }catch{ return structuredClone(DEFAULT_CONFIG); } }
function saveConfig(){ localStorage.setItem('algohns_v9_config', JSON.stringify(state.config)); }
function merge(a,b){ const out = Array.isArray(a) ? [...a] : {...a}; for(const [k,v] of Object.entries(b||{})){ out[k] = v && typeof v==='object' && !Array.isArray(v) ? merge(out[k]||{}, v) : v; } return out; }
function setStatus(msg, level='info'){ const el=$('status'); if(el){ el.className='status '+level; el.textContent=msg; } }
function setLoading(btn,isLoading){ if(!btn) return; if(isLoading){ btn.disabled=true; btn.dataset.originalText=btn.innerHTML; btn.innerHTML='<i class="ti ti-loader-2" style="animation:spin 1s linear infinite"></i> Loading...'; } else { btn.disabled=false; if(btn.dataset.originalText) btn.innerHTML=btn.dataset.originalText; } }
function log(step, detail, level='info'){ state.log.unshift({ts:new Date().toLocaleTimeString(), step, detail, level}); state.log = state.log.slice(0,160); }
async function api(path, opts={}){ const r=await fetch(path,{...opts, headers:{'content-type':'application/json', ...(opts.headers||{})}}); const text=await r.text(); let j={}; try{ j=text?JSON.parse(text):{}; }catch{ j={error:text||'Invalid response'}; } if(!r.ok) throw new Error(j.error || j.detail || `HTTP ${r.status}`); return j; }
function cssVar(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
if(!window._chartRegistry) window._chartRegistry = {};
function destroyChart(id){
  if(window._chartRegistry?.[id]){
    try{ window._chartRegistry[id].destroy(); }catch{}
    delete window._chartRegistry[id];
  }
}
function registerChart(id, instance){
  destroyChart(id);
  window._chartRegistry[id] = instance;
  return instance;
}
function tooltipTheme(){
  const dark = document.body.classList.contains('dark-mode');
  return {
    backgroundColor: dark ? '#1C1917' : '#FFFFFF',
    borderColor: dark ? '#3D3835' : '#E7E5E4',
    titleColor: dark ? '#A8A29E' : '#78716C',
    bodyColor: dark ? '#FAFAF9' : '#1C1917'
  };
}
function formatDateLabel(x, opts={month:'short', year:'2-digit'}){
  if(!x) return '';
  try{
    const d = typeof x === 'number' ? new Date(x) : new Date(String(x));
    if(Number.isNaN(d.getTime())) return String(x);
    return d.toLocaleDateString('en-US', opts);
  }catch{ return String(x); }
}
function timeAgo(ts){ if(!ts) return 'never'; const d=new Date(ts); const s=Math.max(0,Math.floor((Date.now()-d.getTime())/1000)); if(s<60) return s+'s ago'; const m=Math.floor(s/60); if(m<60) return m+'m ago'; const h=Math.floor(m/60); if(h<24) return h+'h ago'; return Math.floor(h/24)+'d ago'; }

function nameOf(sym){ const e = ETF_META[sym]; return e ? e[0] : (state.alpaca.assets.find(a=>a.symbol===sym)?.name || state.etoro.assets.find(a=>a.symbol===sym)?.name || sym); }
function classOf(sym){ const e = ETF_META[sym]; return e ? e[1] : (state.alpaca.assets.find(a=>a.symbol===sym)?.assetClass || state.etoro.assets.find(a=>a.symbol===sym)?.assetClass || 'Stock'); }
function sectorOf(sym){ const e = ETF_META[sym]; return e ? e[2] : 'Unknown'; }

function navigate(view){ setView(view); }
function setView(view){
  if(view === 'portfolio') view = 'portfolio-combined';
  if(view === 'orders') view = 'orders-all';
  if(!PAGES[view]) view='control';
  const content=$('content');
  state.view=view;
  history.replaceState(null,'','#'+view);
  document.title = 'Algohns V9.2 — ' + (PAGES[view]?.title || 'Control Center');
  if(content){
    content.style.opacity='0';
    setTimeout(()=>{ render(); content.style.opacity='1'; }, 100);
  } else render();
  $('main')?.scrollTo?.({top:0,behavior:'smooth'});
}

function bindOnce(){
  document.addEventListener('click', async ev => {
    const groupBtn = ev.target.closest('[data-nav-toggle]');
    if(groupBtn){
      ev.preventDefault();
      const key = groupBtn.dataset.navToggle;
      state.navOpen[key] = !state.navOpen[key];
      renderNav();
      updateNavBadges();
      return;
    }
    const viewBtn = ev.target.closest('[data-view]');
    if(viewBtn){ ev.preventDefault(); navigate(viewBtn.dataset.view); return; }
    const actBtn = ev.target.closest('[data-act]');
    if(!actBtn) return;
    ev.preventDefault();
    const act = actBtn.dataset.act;
    const asyncActions = new Set(['refresh','sync-alpaca','sync-etoro','load-universe','load-backtest-data','etoro-search','exit-position','reduce-position','send-orders-all','send-orders-alpaca','send-orders-etoro','kill','play-pause','run-core','run-backtest']);
    const processingRow = ['exit-position','reduce-position'].includes(act) ? actBtn.closest('tr') : null;
    try{
      if(processingRow){ processingRow.style.opacity = '0.4'; processingRow.setAttribute('aria-busy','true'); }
      if(asyncActions.has(act)) setLoading(actBtn,true);
      if(act==='refresh') await syncBoth();
      if(act==='sync-alpaca') await syncAlpaca();
      if(act==='sync-etoro') await syncEtoro();
      if(act==='play-pause') await toggleMonitor();
      if(act==='kill') await killSwitch();
      if(act==='toggle-theme') toggleTheme();
      if(act==='load-universe') await loadUniverse();
      if(act==='load-backtest-data') await loadBacktestData();
      if(act==='run-backtest') runBacktest();
      if(act==='run-core') runCoreAlgorithm();
      if(act==='apply-json') applyStrategyJson();
      if(act==='export-snapshot') exportSnapshot();
      if(act==='reset-config') resetLocalConfig();
      if(act==='etoro-search') await searchEtoro();
      if(act==='send-orders-all') await executeOrders('combined');
      if(act==='send-orders-alpaca') await executeOrders('alpaca');
      if(act==='send-orders-etoro') await executeOrders('etoro');
      if(act==='exit-position') await exitPosition(actionPayload(actBtn));
      if(act==='reduce-position') await reducePosition(actionPayload(actBtn));
      if(act==='exclude-symbol'){ state.config.exclusions = [...new Set([...(state.config.exclusions||[]), actBtn.dataset.symbol])]; saveConfig(); setStatus(`${actBtn.dataset.symbol} excluded from future target selection.`, 'warn'); }
      render();
    }catch(e){
      log('Action error', e.message || String(e), 'bad');
      setStatus(e.message || String(e), 'bad');
      render();
    }finally{
      if(processingRow){ processingRow.style.opacity = '1'; processingRow.removeAttribute('aria-busy'); }
      if(asyncActions.has(act)) setLoading(actBtn,false);
    }
  }, true);

  document.addEventListener('change', ev => {
    const el = ev.target;
    if(el.id==='strategy-mode'){ state.config.mode=el.value; saveConfig(); log('Strategy',`Mode changed to ${el.value}`,'good'); render(); }
    if(el.id==='bt-period'){ state.config.backtest.period=el.value; state.backtest.stale=true; saveConfig(); render(); }
    if(el.id==='bt-benchmark'){ state.config.backtest.benchmark=el.value; state.backtest.stale=true; saveConfig(); render(); }
    if(el.id==='bt-universe'){ state.config.backtest.universe=el.value; state.backtest.stale=true; saveConfig(); render(); }
  });

  document.addEventListener('input', ev => {
    if(ev.target.id==='strategy-json') state._strategyDraft = ev.target.value;
    if(ev.target.id==='custom-universe') state.config.universe.custom = ev.target.value;
  });

  window.addEventListener('hashchange', () => setView(location.hash.replace('#','') || 'control'));
}

function render(){
  safeState();
  const title=$('page-title'), subtitle=$('page-subtitle'), content=$('content');
  const meta = PAGES[state.view] || PAGES.control;
  renderNav();
  if(title) title.textContent = meta.title;
  if(subtitle) subtitle.textContent = meta.subtitle;
  updateSticky();
  updateNavBadges();
  try{ content.innerHTML = meta.render(); }
  catch(e){ content.innerHTML = `<div class="card span-12">${cardHeader('Page error')}<p class="bad">${html(e.message||e)}</p><button data-act="reset-config" class="danger"><i class="ti ti-restore"></i> Reset local config</button></div>`; }
  requestAnimationFrame(()=>{ initCharts(); if(String(state.view).startsWith('portfolio-')) afterRenderPortfolio(state.view); });
}
function safeState(){
  state.config.activeBrokers = { alpaca: state.config.activeBrokers?.alpaca !== false, etoro: state.config.activeBrokers?.etoro !== false };
  state.config.execution = merge(DEFAULT_CONFIG.execution, state.config.execution||{});
  state.config.realMoneyLocked = true;
}
function updateSticky(){
  const algo=$('algo-status'), broker=$('broker-status'), regime=$('regime-pill'), dot=$('algo-dot'), play=$('play-copy'), theme=$('theme-icon');
  const killed = !!state.config.execution.killSwitch;
  if(algo) algo.textContent = state.running ? `Running · ${state.config.mode}` : (killed ? 'Killed' : 'Stopped');
  if(dot){ dot.className = 'status-dot ' + (state.running?'running':killed?'killed':''); }
  if(play) play.textContent = state.running ? 'Pause' : 'Play';
  const playIcon = document.querySelector('[data-act="play-pause"] i');
  if(playIcon) playIcon.className = 'ti ' + (state.running ? 'ti-player-pause' : 'ti-player-play');
  if(theme) theme.className = 'ti ' + (document.body.classList.contains('dark-mode') ? 'ti-sun' : 'ti-moon');
  const connected = [state.alpaca.status?.configured || state.alpaca.account, state.etoro.status?.connected].filter(Boolean).length;
  if(broker) broker.textContent = connected ? `${connected}/2 synced` : 'Not synced';
  const label = state.core.regime?.label || 'Neutral';
  if(regime){
    regime.textContent = label;
    const cls = label==='Risk-on' || label==='Soft-landing' ? 'good' : label==='Risk-off' || label==='Crash' ? 'bad' : 'neutral';
    regime.className = 'regime-pill ' + (cls==='neutral'?'':cls);
  }
}
function updateNavBadges(){
  setBadge('backtest', state.backtest.loaded && !state.backtest.stale ? 'ready' : 'no data', state.backtest.loaded && !state.backtest.stale ? 'green' : '');
  const alpacaPos = state.alpaca.positions?.length || 0;
  const etoroPos = state.etoro.positions?.length || 0;
  const pos = alpacaPos + etoroPos;
  setBadge('portfolio', pos>0 ? String(pos) : '', 'blue');
  setBadge('portfolio-combined', pos>0 ? String(pos) : '', 'blue');
  setBadge('portfolio-alpaca', alpacaPos>0 ? String(alpacaPos) : '', 'blue');
  setBadge('portfolio-etoro', etoroPos>0 ? String(etoroPos) : '', 'blue');
  const alpacaOrders = (state.orders.alpaca?.length || 0) + (state.alpaca.orders?.length || 0);
  const etoroOrders = (state.orders.etoro?.length || 0) + (state.etoro.orders?.length || 0);
  const pending = alpacaOrders + etoroOrders;
  setBadge('orders', pending>0 ? String(pending) : '', 'amber');
  setBadge('orders-all', pending>0 ? String(pending) : '', 'amber');
  setBadge('orders-alpaca', alpacaOrders>0 ? String(alpacaOrders) : '', 'amber');
  setBadge('orders-etoro', etoroOrders>0 ? String(etoroOrders) : '', 'amber');
  const u = universeRows().length;
  setBadge('universe', u>0 ? String(u) : '', 'green');
}
function setBadge(id,text,cls=''){
  const el=$('nav-badge-'+id); if(!el) return;
  el.textContent=text; el.className = text ? 'show '+cls : '';
}

async function syncBoth(){ setStatus('Syncing Alpaca + eToro...', 'warn'); await Promise.allSettled([syncAlpaca(), syncEtoro()]); setStatus('Broker refresh completed.', 'good'); }
async function syncAlpaca(){
  const calls = await Promise.allSettled([
    api('/api/broker/status'), api('/api/broker/alpaca/account'), api('/api/broker/alpaca/positions'), api('/api/broker/alpaca/orders'), api('/api/broker/alpaca/portfolio-history')
  ]);
  if(calls[0].status==='fulfilled') state.alpaca.status=calls[0].value;
  if(calls[1].status==='fulfilled') state.alpaca.account=calls[1].value;
  if(calls[2].status==='fulfilled') state.alpaca.positions=Array.isArray(calls[2].value)?calls[2].value:(calls[2].value.positions||[]);
  if(calls[3].status==='fulfilled') state.alpaca.orders=Array.isArray(calls[3].value)?calls[3].value:(calls[3].value.orders||[]);
  if(calls[4].status==='fulfilled') state.alpaca.history=calls[4].value;
  state.alpaca.lastSync = new Date().toISOString();
  log('Alpaca sync', `${state.alpaca.positions.length} positions · ${state.alpaca.orders.length} orders`, 'good');
}
async function syncEtoro(){
  const calls = await Promise.allSettled([
    api('/api/broker/etoro/status'), api('/api/broker/etoro/me'), api('/api/broker/etoro/portfolio'), api('/api/broker/etoro/orders')
  ]);
  state.etoro.errors = [];
  if(calls[0].status==='fulfilled') state.etoro.status=calls[0].value; else state.etoro.errors.push({area:'status', error:calls[0].reason?.message || String(calls[0].reason)});
  if(calls[1].status==='fulfilled') state.etoro.me=calls[1].value; else state.etoro.errors.push({area:'me', error:calls[1].reason?.message || String(calls[1].reason)});
  if(calls[2].status==='fulfilled') ingestEtoroPortfolio(calls[2].value); else state.etoro.errors.push({area:'portfolio', error:calls[2].reason?.message || String(calls[2].reason)});
  if(calls[3].status==='fulfilled') state.etoro.orders = normalizeArray(calls[3].value.orders || calls[3].value.items || calls[3].value.openOrders || []); else state.etoro.errors.push({area:'orders', error:calls[3].reason?.message || String(calls[3].reason)});
  state.etoro.lastSync = new Date().toISOString();
  if(state.etoro.status?.configured === false) log('eToro sync', 'eToro keys missing. Configure ETORO_API_KEY and ETORO_USER_KEY.', 'warn');
  else if(state.etoro.errors.length) log('eToro sync', `${state.etoro.positions.length} positions · ${state.etoro.errors.length} API warning(s). Open Settings/Orders for details.`, 'warn');
  else log('eToro sync', `${state.etoro.positions.length} positions · ${state.etoro.orders.length} orders · connected=${!!state.etoro.status?.connected}`, state.etoro.status?.connected?'good':'warn');
}
function normalizeArray(x){ return Array.isArray(x) ? x : []; }
function deepFindArrays(obj, names){
  const out=[]; const seen=new WeakSet(); const keys = names.map(x=>String(x).toLowerCase());
  function walk(v){
    if(!v || typeof v !== 'object') return;
    if(seen.has(v)) return; seen.add(v);
    if(Array.isArray(v)){ out.push(v); v.slice(0,20).forEach(walk); return; }
    for(const [k,val] of Object.entries(v)){
      if(Array.isArray(val) && keys.includes(k.toLowerCase())) out.push(val);
      else if(val && typeof val === 'object') walk(val);
    }
  }
  walk(obj);
  return out;
}
function looksLikeEtoroPosition(p){
  if(!p || typeof p !== 'object') return false;
  const keys = Object.keys(p).map(k=>k.toLowerCase());
  return keys.some(k=>['positionid','position_id','instrumentid','instrument_id','units','currentvalue','netprofit','amount'].includes(k));
}
function normalizeEtoroPosition(p){
  const r=p?.raw||p||{};
  const symbol = r.symbol || r.Symbol || r.ticker || r.Ticker || r.instrumentSymbol || r.InstrumentSymbol || r.InstrumentDisplayName || r.displayName || r.DisplayName || r.name || r.Name || r.instrumentId || r.InstrumentID || 'ETORO';
  const qty = Number(r.qty ?? r.quantity ?? r.units ?? r.Units ?? r.amount ?? r.Amount ?? 0);
  const value = Number(r.market_value ?? r.value ?? r.CurrentValue ?? r.currentValue ?? r.Invested ?? r.invested ?? r.Amount ?? r.amount ?? 0);
  return {
    symbol: String(symbol).toUpperCase(),
    qty,
    units: Number(r.units ?? r.Units ?? qty ?? 0),
    market_value: value,
    unrealized_pl: Number(r.unrealized_pl ?? r.unrealizedProfit ?? r.NetProfit ?? r.netProfit ?? r.pnl ?? r.PnL ?? r.profitLoss ?? 0),
    positionId: r.positionId ?? r.PositionID ?? r.PositionId ?? r.positionID ?? r.id ?? r.ID ?? '',
    instrumentId: r.instrumentId ?? r.InstrumentID ?? r.InstrumentId ?? r.instrumentID ?? '',
    raw:r
  };
}
function ingestEtoroPortfolio(j){
  state.etoro.portfolio = j;
  const b = j?.portfolio || j?.clientPortfolio || j || {};
  state.etoro.account = j?.account || b?.account || b?.balance || b?.summary || b?.clientAccount || b;
  const direct = [j?.positions, j?.openPositions, j?.items, j?.PositionInfo, b?.positions, b?.openPositions, b?.PositionInfo, b?.clientPositions].filter(Array.isArray);
  const deep = deepFindArrays(j, ['positions','openPositions','PositionInfo','clientPositions','mirrorPositions']);
  const raw = [...direct.flat(), ...deep.flat()].filter(looksLikeEtoroPosition);
  const uniq = new Map();
  raw.map(normalizeEtoroPosition).forEach(p=>{
    const key = `${p.positionId || p.instrumentId || p.symbol}:${p.units}:${p.market_value}`;
    if(p.symbol || p.positionId || p.instrumentId) uniq.set(key, p);
  });
  state.etoro.positions = [...uniq.values()];
  const orderArrays = deepFindArrays(j, ['orders','openOrders','pendingOrders']);
  if(orderArrays.length && !state.etoro.orders?.length) state.etoro.orders = orderArrays.flat().filter(x=>x && typeof x === 'object');
  // Feed the eToro asset cache from actual positions so close/reduce and future previews do not lose instrument IDs.
  const fromPositions = state.etoro.positions.filter(p=>p.instrumentId).map(p=>({symbol:positionSymbol(p), name:positionSymbol(p), instrumentId:p.instrumentId, assetClass:'eToro position', source:'portfolio'}));
  if(fromPositions.length) state.etoro.assets = dedupeBySymbol([...(state.etoro.assets||[]), ...fromPositions]);
}

async function loadUniverse(){
  setStatus('Loading real visible universe...', 'warn');
  const alp = await api('/api/broker/alpaca/assets?asset_class=all&limit=10000').catch(e => ({assets:[], error:e.message}));
  state.alpaca.assets = alp.assets || [];
  const et = await api('/api/broker/etoro/assets').catch(e => ({assets:[], error:e.message}));
  state.etoro.assets = et.assets || [];
  log('Universe', `Alpaca ${state.alpaca.assets.length} assets · eToro verified ${state.etoro.assets.length}`, 'good');
  setStatus('Universe loaded. Use Load Backtest Data before running a dated backtest.', 'good');
}
async function searchEtoro(){
  const q = $('etoro-search')?.value?.trim(); if(!q) return;
  const r = await api('/api/broker/etoro/search?q=' + encodeURIComponent(q));
  const items = r.instruments || r.items || r.assets || [];
  const mapped = items.map(x => ({symbol:(x.symbol||x.ticker||q).toUpperCase(), name:x.name||x.displayName||x.symbol||q, assetClass:x.assetClass||x.instrumentType||'eToro instrument', instrumentId:x.instrumentId||x.InstrumentID||x.id, tradable:true, source:'etoro-search'}));
  state.etoro.assets = dedupeBySymbol([...(state.etoro.assets||[]), ...mapped]);
  log('eToro search', `${mapped.length} instruments found for ${q}`, mapped.length?'good':'warn');
}
function dedupeBySymbol(arr){ const m=new Map(); for(const x of arr||[]) if(x.symbol) m.set(String(x.symbol).toUpperCase(), {...x, symbol:String(x.symbol).toUpperCase()}); return [...m.values()]; }
function desiredUniverse(){
  const custom = String(state.config.universe.custom||'').split(/[,\s]+/).map(x=>x.trim().toUpperCase()).filter(Boolean);
  const alpaca = (state.alpaca.assets||[]).filter(a => a.tradable !== false).map(a => a.symbol);
  const etoro = (state.etoro.assets||[]).map(a => a.symbol);
  return [...new Set([...CORE_SYMBOLS, ...alpaca, ...etoro, ...custom])].filter(Boolean);
}
function diversifiedSample(symbols, max=240){
  const uniq=[...new Set(symbols)].filter(Boolean);
  const core=uniq.filter(s=>CORE_SYMBOLS.includes(s));
  const rest=uniq.filter(s=>!CORE_SYMBOLS.includes(s));
  rest.sort((a,b)=>hash(a)-hash(b));
  return [...new Set([...core, ...rest])].slice(0,max);
}
function hash(s){ return [...String(s)].reduce((a,c)=>((a*31+c.charCodeAt(0))>>>0),7); }
async function loadBacktestData(){
  const range = periodToRange(state.config.backtest.period);
  const syms = diversifiedSample(backtestUniverse(), 240);
  if(!syms.length) throw new Error('No universe loaded. Go to Universe Explorer → Load universe first.');
  state.prices={}; state.quotes={};
  setStatus(`Loading backtest data: ${syms.length} symbols, ${range}...`, 'warn');
  for(let i=0;i<syms.length;i+=120){
    const batch=syms.slice(i,i+120);
    const r=await api('/api/market/prices?range='+encodeURIComponent(range)+'&symbols='+encodeURIComponent(batch.join(',')));
    Object.assign(state.prices, r.prices||{}); Object.assign(state.quotes, r.quotes||{});
    log('Backtest data', `Batch ${Math.floor(i/120)+1}: ${Object.keys(r.prices||{}).length} symbols loaded`, 'good');
  }
  state.backtest.symbols = Object.keys(state.prices);
  state.backtest.loaded=true; state.backtest.stale=false; state.backtest.result=null;
  setStatus(`Backtest data ready: ${state.backtest.symbols.length} symbols.`, 'good');
}
function periodToRange(p){ return p==='10Y'?'10y':p==='5Y'?'5y':p==='1Y'?'1y':'3y'; }
function backtestUniverse(){
  const u=state.config.backtest.universe;
  if(u==='Alpaca universe') return (state.alpaca.assets||[]).map(a=>a.symbol);
  if(u==='eToro universe') return (state.etoro.assets||[]).map(a=>a.symbol);
  if(u==='Core ETFs') return CORE_SYMBOLS;
  return desiredUniverse();
}

function scoreSymbol(sym){
  const arr=state.prices[sym]||[]; if(arr.length<80) return null;
  const px=last(arr)?.close; const q=state.quotes[sym]||{};
  const r21=arr[arr.length-22]?.close ? px/arr[arr.length-22].close-1 : 0;
  const r63=arr[arr.length-64]?.close ? px/arr[arr.length-64].close-1 : 0;
  const r126=arr[arr.length-127]?.close ? px/arr[arr.length-127].close-1 : 0;
  const rets=returns(arr.slice(-64)); const vol=std(rets)*Math.sqrt(252);
  const ma200=avg(arr.slice(-200).map(x=>x.close)); const trend=px>ma200;
  const liquidity = norm(q.avgVolumeDollar||px*1000000, 1_000_000, 50_000_000);
  const momentum = 0.25*r21 + 0.35*r63 + 0.40*r126;
  const score = 100*(0.48*norm(momentum,-0.15,0.45) + 0.20*(trend?1:0) + 0.18*norm(0.45-vol,0,0.45) + 0.14*liquidity);
  return {symbol:sym, name:nameOf(sym), assetClass:classOf(sym), sector:sectorOf(sym), price:px, momentum, vol, trend, liquidity, finalScore:score};
}
function detectRegime(){
  const spy=scoreSymbol('SPY'), qqq=scoreSymbol('QQQ'), hyg=scoreSymbol('HYG'), tlt=scoreSymbol('TLT');
  const eqMom=avg([spy?.momentum, qqq?.momentum]);
  const vol=spy?.vol||0;
  const breadthSyms=CORE_SYMBOLS.slice(0,15); const breadth=avg(breadthSyms.map(s=>scoreSymbol(s)?.trend?1:0));
  let riskOn=0, riskOff=0;
  if(eqMom>0.05) riskOn++; if(eqMom<-0.04) riskOff++;
  if(vol<0.18) riskOn++; if(vol>0.32) riskOff++;
  if(breadth>0.65) riskOn++; if(breadth<0.40) riskOff++;
  if((hyg?.momentum||0)>0) riskOn++; if((hyg?.momentum||0)<-0.04) riskOff++;
  if((tlt?.momentum||0)<0.04) riskOn++; if((tlt?.momentum||0)>0.08) riskOff++;
  let label='Neutral'; if(riskOff>=4 || vol>0.45) label='Crash'; else if(riskOff>=3) label='Risk-off'; else if(riskOn>=4) label='Risk-on'; else if(riskOn>=3) label='Soft-landing';
  return {label, riskOnVotes:riskOn, riskOffVotes:riskOff, equityMomentum:eqMom, equityVol:vol, breadthPct:breadth};
}
function runCoreAlgorithm(){
  if(!Object.keys(state.prices).length){ log('Strategy engine','Load data first. Using demo core only is not enough for real screening.','warn'); }
  state.core.regime = detectRegime();
  const scored = Object.keys(state.prices).map(scoreSymbol).filter(Boolean).sort((a,b)=>b.finalScore-a.finalScore);
  const filtered = scored.filter(x => x.price >= state.config.filters.minPrice && x.trend).slice(0, Math.max(8, modeTopN()));
  const inv = filtered.map(x => x.finalScore/Math.max(x.vol,0.08)); const total=inv.reduce((a,b)=>a+b,0)||1;
  const targets={};
  filtered.forEach((x,i)=>{ targets[x.symbol] = Math.min(state.config.risk.maxPosition, (1-state.config.risk.cashFloor)*inv[i]/total); });
  targets.BIL = Math.max(state.config.risk.cashFloor, 1 - Object.values(targets).reduce((a,b)=>a+b,0));
  state.core.targets=targets; state.core.rank=scored.slice(0,50);
  state.core.report={initial:desiredUniverse().length, priced:scored.length, selected:Object.keys(targets).length, regime:state.core.regime.label};
  state.core.lastDecision = `Selected ${Object.keys(targets).length} target weights from ${scored.length} priced assets. Regime: ${state.core.regime.label}. No ticker list was hardcoded.`;
  buildOrderPreview();
  const eToroSkips = state.orderDiagnostics?.etoro?.length || 0;
  if(eToroSkips && !state.orders.etoro.length) log('eToro preview', `${eToroSkips} target(s) skipped. Most common cause: missing eToro instrumentId. Use Universe Explorer → Load universe/Search eToro.`, 'warn');
  log('Strategy engine', state.core.lastDecision, 'good');
  setStatus('Strategy engine updated target allocation.', 'good');
}
function modeTopN(){ return state.config.mode==='Defensive'?12:state.config.mode==='Advanced'?24:18; }
function buildOrderPreview(){
  state.orderDiagnostics = { alpaca:[], etoro:[] };
  state.orders.alpaca = state.config.execution.allowAlpacaPaper ? buildBrokerOrderPreview('alpaca') : [];
  state.orders.etoro = state.config.execution.allowEtoroDemo ? buildBrokerOrderPreview('etoro') : [];
}
function buildBrokerOrderPreview(broker){
  const equity = brokerValue(broker) || brokerValue('combined') || Number(state.alpaca.account?.equity || state.alpaca.account?.portfolio_value || 100000);
  const current = weightsFromPositions(broker);
  const diagnostics = state.orderDiagnostics?.[broker] || (state.orderDiagnostics[broker] = []);
  const orders=[];
  for(const [sym,w] of Object.entries(state.core.targets||{})){
    if(sym==='BIL') continue;
    const diff = w - (current[sym] || 0);
    const notional = Math.abs(diff * equity);
    if(notional < state.config.execution.minTradeValue){ diagnostics.push({symbol:sym, issue:'below minimum trade value', detail:`${money(notional)} < ${money(state.config.execution.minTradeValue)}`}); continue; }
    if(broker === 'etoro'){
      const asset = findEtoroAsset(sym);
      const existing = findBrokerPosition('etoro', sym);
      const instrumentId = asset?.instrumentId || asset?.InstrumentID || asset?.id || positionInstrumentId(existing);
      if(!instrumentId){ diagnostics.push({symbol:sym, issue:'missing eToro instrumentId', detail:'Load Universe or use Search eToro in Universe Explorer before sending eToro orders.'}); continue; }
      const side = diff>0?'buy':'sell';
      const order = { _broker:broker, symbol:sym, side, notional, amount:notional, instrumentId, type:'market', reason:'Target allocation drift · eToro verified instrument' };
      if(side === 'sell'){
        const pid = positionId(existing);
        const units = positionUnits(existing);
        const val = Math.abs(positionValue(existing || {}));
        if(pid && units > 0 && val > 0){
          order.positionId = pid;
          order.unitsToDeduct = Math.min(units, Math.max(0, notional / val * units));
          order.reason = 'Target allocation drift · eToro close/reduce existing demo position';
        } else {
          diagnostics.push({symbol:sym, issue:'missing eToro positionId for sell', detail:'Sync eToro portfolio. Without positionId, Algohns will not fake a close-position order.'});
          continue;
        }
      }
      orders.push(order);
    } else {
      const order = { _broker:broker, symbol:sym, side:diff>0?'buy':'sell', notional, type:'market', time_in_force:'day', reason:'Target allocation drift' };
      if(order.side === 'sell'){
        const pos = findBrokerPosition('alpaca', sym);
        const val = Math.abs(positionValue(pos || {}));
        const qty = positionQty(pos || {});
        if(qty > 0 && val > 0) order.qty = clamp(notional / val, 0, 1) * qty;
      }
      orders.push(order);
    }
  }
  return orders;
}
function weightsFromPositions(broker){
  const rows = broker==='etoro' ? state.etoro.positions : state.alpaca.positions;
  const total = rows.reduce((a,p)=>a+Math.abs(positionValue(p)),0)||1;
  const out={}; rows.forEach(p=>{ const s=positionSymbol(p); if(s) out[s]=(out[s]||0)+positionValue(p)/total; }); return out;
}
function positionSymbol(p){ return String(p.symbol || p.asset_symbol || p.ticker || '').toUpperCase(); }
function positionQty(p){ return Number(p.qty ?? p.quantity ?? p.units ?? p.qty_available ?? 0); }
function positionValue(p){ return Number(p.market_value ?? p.value ?? p.CurrentValue ?? (Number(p.current_price||p.avg_entry_price||0)*positionQty(p)) ?? 0); }
function positionPnl(p){ return Number(p.unrealized_pl ?? p.unrealizedProfit ?? p.NetProfit ?? p.pnl ?? 0); }

function actionPayload(btn){
  return {
    broker: btn.dataset.broker,
    symbol: btn.dataset.symbol,
    qty: Number(btn.dataset.qty || 0),
    positionId: btn.dataset.positionId || '',
    instrumentId: btn.dataset.instrumentId || '',
    units: Number(btn.dataset.units || btn.dataset.qty || 0)
  };
}
function canonicalSymbol(s){ return String(s||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/US$/,''); }
function findBrokerPosition(broker, symbol){ return (state[broker]?.positions || []).find(p => canonicalSymbol(positionSymbol(p)) === canonicalSymbol(symbol)); }
function findEtoroAsset(symbol){
  const target = canonicalSymbol(symbol);
  const fromPositions = (state.etoro.positions || []).map(p => ({symbol:positionSymbol(p), name:positionSymbol(p), instrumentId:positionInstrumentId(p), assetClass:'eToro position', source:'portfolio'}));
  const rows = [...(state.etoro.assets || []), ...fromPositions];
  return rows.find(a => {
    const vals = [a.symbol, a.ticker, a.name, a.displayName, a.DisplayName, a.raw?.symbol, a.raw?.Symbol, a.raw?.ticker, a.raw?.Ticker];
    return vals.some(v => canonicalSymbol(v) === target) || String(a.instrumentId || a.InstrumentID || a.id || '') === String(symbol||'');
  });
}
function positionId(p){ const r=p?.raw||{}; return p?.positionId ?? p?.PositionID ?? p?.id ?? r.positionId ?? r.PositionID ?? r.PositionId ?? r.id ?? ''; }
function positionInstrumentId(p){ const r=p?.raw||{}; return p?.instrumentId ?? p?.InstrumentID ?? p?.instrumentID ?? r.instrumentId ?? r.InstrumentID ?? r.InstrumentId ?? r.instrumentID ?? ''; }
function positionUnits(p){ const r=p?.raw||{}; return Number(p?.units ?? p?.Units ?? r.units ?? r.Units ?? positionQty(p)); }
function cleanQty(n){ const x=Number(n); if(!Number.isFinite(x) || x<=0) return ''; return String(Number(x.toFixed(6))).replace(/\.0+$/,''); }
function orderSymbol(o){ return String(o.symbol || o.asset_symbol || o.ticker || o.instrumentSymbol || o.InstrumentDisplayName || '').toUpperCase(); }
function orderSide(o){ return String(o.side || o.Side || (o.IsBuy===true?'buy':o.IsBuy===false?'sell':'')).toLowerCase(); }
function orderStatus(o){ return String(o.status || o.Status || o.order_status || o.state || o.State || 'preview'); }
function orderNotional(o){ return Number(o.notional ?? o.amount ?? o.Amount ?? o.filled_notional ?? o.market_value ?? 0) || 0; }
function brokerActualOrders(broker){
  if(broker==='combined') return [...(state.alpaca.orders||[]).map(o=>({...o,_broker:'alpaca'})), ...(state.etoro.orders||[]).map(o=>({...o,_broker:'etoro'}))];
  return (state[broker]?.orders || []).map(o=>({...o,_broker:broker}));
}
function brokerPreviewOrders(broker){
  if(broker==='combined') return [...(state.orders.alpaca||[]).map(o=>({...o,_broker:'alpaca'})), ...(state.orders.etoro||[]).map(o=>({...o,_broker:'etoro'}))];
  return (state.orders[broker] || []).map(o=>({...o,_broker:broker}));
}
function sparklineFor(symbol, pnl=0){
  const positive = Number(pnl) >= 0;
  const seed = [...String(symbol||'')].reduce((a,c)=>a+c.charCodeAt(0),0) || 1;
  const pts = Array.from({length:12}, (_,i) => 12 + Math.sin((i+seed%7)/2.3)*5 + (positive ? i*.55 : -i*.55) + (((seed*(i+3))%9)-4)*.25);
  const mn=Math.min(...pts), mx=Math.max(...pts), span=mx-mn || 1;
  const path = pts.map((v,i)=>`${(i*(80/11)).toFixed(1)},${(24-((v-mn)/span)*20).toFixed(1)}`).join(' ');
  return `<svg class="mini-spark ${positive?'good':'bad'}" viewBox="0 0 80 24" role="img" aria-label="Simulated 5 day trend for ${html(symbol)}"><polyline points="${path}"></polyline></svg><span class="sim-label">simulated</span>`;
}
function sentimentClass(symbol, pnl=0){
  const n=Number(pnl)||0;
  if(n>0) return 'good';
  if(n<0) return 'bad';
  const q=state.quotes?.[symbol];
  return q?.price ? 'warn' : '';
}
function brokerFromView(view){ if(String(view).includes('alpaca')) return 'alpaca'; if(String(view).includes('etoro')) return 'etoro'; return 'combined'; }
function positionsForBroker(broker){
  if(broker === 'combined') return combinedPositions();
  return (state[broker]?.positions || []).map(p => ({...p, _broker: broker}));
}
function brokerLabel(broker){ return broker === 'alpaca' ? 'Alpaca Demo' : broker === 'etoro' ? 'eToro Demo' : 'Combined View'; }
function brokerAccount(broker){ return broker === 'alpaca' ? (state.alpaca.account || {}) : (state.etoro.account || state.etoro.me || state.etoro.portfolio || {}); }
function brokerCash(broker){
  if(broker === 'combined') return brokerCash('alpaca') + brokerCash('etoro');
  const acc = brokerAccount(broker);
  if(broker === 'etoro') return Number(acc.cash ?? acc.balance ?? acc.availableCash ?? acc.available ?? 0) || 0;
  return Number(acc.cash ?? acc.buying_power ?? acc.buyingPower ?? 0) || 0;
}
function brokerValue(broker){
  if(broker === 'combined') return brokerValue('alpaca') + brokerValue('etoro');
  const acc = brokerAccount(broker);
  const fromAccount = Number(acc.portfolio_value ?? acc.equity ?? acc.totalValue ?? acc.value ?? acc.netLiquidation ?? 0) || 0;
  if(fromAccount) return fromAccount;
  return positionsForBroker(broker).reduce((s,p)=>s+Math.abs(positionValue(p)),0) + brokerCash(broker);
}
function brokerDailyPnl(broker){
  if(broker === 'combined') return brokerDailyPnl('alpaca') + brokerDailyPnl('etoro');
  const acc = brokerAccount(broker);
  return Number(acc.unrealized_intraday_pl ?? acc.unrealizedIntradayPl ?? acc.dailyPnl ?? acc.todayPnl ?? 0) || 0;
}
function totalCash(){ return brokerCash('combined'); }
function totalValue(){ return brokerValue('combined'); }

function runBacktest(){
  if(!state.backtest.loaded || state.backtest.stale) throw new Error('Click Load Backtest Data first.');
  const scored = state.backtest.symbols.map(scoreSymbol).filter(Boolean).sort((a,b)=>b.finalScore-a.finalScore).slice(0,16);
  const dates = commonDates(scored.map(x=>state.prices[x.symbol]));
  const bench = state.prices[state.config.backtest.benchmark] || state.prices.SPY || [];
  const nav=[], bnav=[]; let base=null, bbase=null;
  for(const d of dates){
    const vals=scored.map(x=>priceOnDate(state.prices[x.symbol],d)).filter(Number.isFinite);
    if(vals.length<3) continue;
    const equal=avg(vals);
    if(base===null) base=equal;
    const bp=priceOnDate(bench,d); if(bbase===null && Number.isFinite(bp)) bbase=bp;
    nav.push({date:d, value:100*equal/base});
    if(Number.isFinite(bp) && bbase) bnav.push({date:d, value:100*bp/bbase});
  }
  const rets=[]; for(let i=1;i<nav.length;i++) rets.push(nav[i].value/nav[i-1].value-1);
  const dd = drawdownSeries(nav);
  state.backtest.result = {nav, benchmark:bnav, drawdown:dd, symbols:scored.map(x=>x.symbol), metrics:{return:(last(nav)?.value||100)/100-1, vol:std(rets)*Math.sqrt(252), maxDD:Math.min(...dd.map(x=>x.value),0), sharpe:avg(rets)/(std(rets)||1)*Math.sqrt(252)}};
  log('Backtest', `Ran on ${scored.length} dynamically selected assets vs ${state.config.backtest.benchmark}.`, 'good');
}
function commonDates(arrs){ const m=new Map(); arrs.forEach(a=>a.forEach(x=>m.set(x.date,(m.get(x.date)||0)+1))); return [...m.entries()].filter(x=>x[1]>=Math.max(2,Math.floor(arrs.length*0.5))).map(x=>x[0]).sort(); }
function priceOnDate(arr,d){ return arr.find(x=>x.date===d)?.close; }
function drawdownSeries(nav){ let peak=0; return nav.map(x=>{ peak=Math.max(peak,x.value); return {date:x.date,value:peak?x.value/peak-1:0}; }); }

async function toggleMonitor(){
  state.config.execution.killSwitch=false;
  state.running=!state.running;
  if(state.timer) clearInterval(state.timer);
  state.timer=null;
  if(state.running){
    log('Algorithm','Started demo monitor. First cycle runs immediately; next cycles run every 5 minutes while this page is open.','good');
    await runExecutionCycle('Play');
    state.timer=setInterval(()=>{ runExecutionCycle('Timer').then(()=>render()).catch(e=>{ log('Monitor error', e.message, 'bad'); render(); }); }, 5*60*1000);
  } else {
    log('Algorithm','Paused demo monitor. No new paper/demo orders will be submitted from the UI loop.','warn');
  }
  render();
}
async function runExecutionCycle(source='Manual'){
  if(state.config.execution.killSwitch){ setStatus('Kill switch is active. Reset it before running the algorithm.', 'bad'); return; }
  if(!state.backtest.loaded || state.backtest.stale){ log('Execution cycle', 'Data not ready: load backtest data before automatic order generation.', 'warn'); setStatus('Load data before running/sending demo orders.', 'warn'); return; }
  runCoreAlgorithm();
  if(state.config.execution.autonomousDemo) await executeOrders('combined', {syncAfter:true, renderAfter:false, source});
}
async function killSwitch(){ state.running=false; if(state.timer) clearInterval(state.timer); state.timer=null; state.config.execution.killSwitch=true; state.orders.alpaca=[]; state.orders.etoro=[]; saveConfig(); await api('/api/broker/alpaca/cancel-open-orders').catch(()=>null); log('Kill Switch','Activated. Monitor stopped, previews cleared, Alpaca open-order cancel requested.','bad'); setStatus('KILL SWITCH ACTIVE. No new demo orders will be prepared.', 'bad'); }
function toggleTheme(){ document.body.classList.toggle('dark-mode'); localStorage.setItem('algohns_v9_theme', document.body.classList.contains('dark-mode')?'dark':'light'); }
function normalizeAlpacaOrder(o){
  const symbol = orderSymbol(o);
  const side = orderSide(o) || 'buy';
  const payload = {symbol, side, type:o.type || 'market', time_in_force:o.time_in_force || 'day'};
  if(side === 'sell'){
    const qty = Number(o.qty || 0) || (()=>{ const pos=findBrokerPosition('alpaca',symbol); const val=Math.abs(positionValue(pos||{})); const q=positionQty(pos||{}); return val>0 ? clamp(orderNotional(o)/val,0,1)*q : 0; })();
    if(qty <= 0) return {error:`${symbol}: no Alpaca quantity available for sell order`};
    payload.qty = cleanQty(qty);
  } else {
    const n = orderNotional(o);
    if(n <= 0) return {error:`${symbol}: missing buy notional`};
    payload.notional = String(Number(n.toFixed(2)));
  }
  return payload;
}
function normalizeEtoroOrder(o){
  const symbol = orderSymbol(o);
  const side = orderSide(o) || 'buy';
  const asset = findEtoroAsset(symbol) || {};
  const instrumentId = Number(o.instrumentId || o.InstrumentID || asset.instrumentId || asset.InstrumentID || 0);
  if(!instrumentId) return {error:`${symbol}: missing eToro instrumentId. Search/load it in Universe Explorer first.`};
  const amount = Number(o.amount || o.notional || 0);
  if(side === 'sell' && (o.positionId || o.PositionID)) return {symbol, side, positionId:o.positionId || o.PositionID, instrumentId, unitsToDeduct:o.unitsToDeduct || o.units || null};
  if(amount <= 0) return {error:`${symbol}: missing eToro amount`};
  return {symbol, side, instrumentId, amount, type:'market', leverage:o.leverage || 1};
}
async function executeOrders(broker='combined', opts={}){
  if(state.config.execution.killSwitch) throw new Error('Kill switch is active. Orders blocked.');
  const brokers = broker === 'combined' ? ['alpaca','etoro'] : [broker];
  let totalSubmitted=0, totalPrepared=0;
  for(const b of brokers){
    const previews = state.orders[b] || [];
    totalPrepared += previews.length;
    if(!previews.length){ log(`${brokerLabel(b)} execution`, 'No prepared orders to submit. Run strategy engine first.', 'warn'); continue; }
    const normalized = previews.map(o => b==='alpaca' ? normalizeAlpacaOrder(o) : normalizeEtoroOrder(o));
    const invalid = normalized.filter(x=>x.error);
    invalid.forEach(x=>log(`${brokerLabel(b)} order skipped`, x.error, 'warn'));
    const valid = normalized.filter(x=>!x.error);
    if(!valid.length){ state.executionResults[b] = {submitted:0, results:invalid}; continue; }
    const path = b==='alpaca' ? '/api/broker/alpaca/batch-orders' : '/api/broker/etoro/batch-orders';
    const result = await api(path, {method:'POST', body:JSON.stringify({orders:valid, source:opts.source || 'manual-ui'})});
    state.executionResults[b] = result;
    totalSubmitted += Number(result.submitted || 0);
    log(`${brokerLabel(b)} execution`, `${result.submitted || 0}/${valid.length} demo orders submitted.`, result.submitted ? 'good' : 'warn');
    state.orders[b] = [];
    if(opts.syncAfter !== false){ if(b==='alpaca') await syncAlpaca(); else await syncEtoro(); }
  }
  setStatus(totalPrepared ? `Demo execution complete: ${totalSubmitted}/${totalPrepared} orders submitted. See Orders for broker responses.` : 'No prepared orders to send. Run Strategy Engine first.', totalSubmitted?'good':'warn');
  if(opts.renderAfter !== false) render();
}
async function exitPosition(arg){
  const {broker,symbol,qty,positionId,instrumentId,units} = arg;
  if(!broker || !symbol) throw new Error('Missing broker or symbol for exit order.');
  state.config.exclusions = [...new Set([...(state.config.exclusions||[]), symbol])]; saveConfig();
  if(broker === 'alpaca'){
    const q = qty || positionQty(findBrokerPosition('alpaca',symbol) || {});
    if(!q) throw new Error(`No Alpaca quantity available for ${symbol}. Sync positions first.`);
    const r = await api('/api/broker/alpaca/orders', {method:'POST', body:JSON.stringify({symbol, side:'sell', qty:cleanQty(q), type:'market', time_in_force:'day'})});
    state.executionResults.alpaca = {submitted:1, results:[r]};
    log('Alpaca exit', `Market sell submitted for ${symbol} · qty ${cleanQty(q)}.`, 'good');
    await syncAlpaca();
  } else if(broker === 'etoro'){
    if(!positionId || !instrumentId) throw new Error(`Missing eToro positionId/instrumentId for ${symbol}. Sync portfolio first; if still missing, the eToro API did not expose a closable position id.`);
    const r = await api('/api/broker/etoro/close-position', {method:'POST', body:JSON.stringify({positionId, instrumentId, unitsToDeduct:null})});
    state.executionResults.etoro = {submitted:1, results:[r]};
    log('eToro exit', `Demo close-position submitted for ${symbol}.`, 'good');
    await syncEtoro();
  }
  setStatus(`Exit order submitted for ${symbol}.`, 'good');
}
async function reducePosition(arg){
  const {broker,symbol,qty,positionId,instrumentId,units} = arg;
  if(!broker || !symbol) throw new Error('Missing broker or symbol for reduce order.');
  if(broker === 'alpaca'){
    const q = qty || (positionQty(findBrokerPosition('alpaca',symbol) || {}) / 2);
    if(!q) throw new Error(`No Alpaca quantity available for ${symbol}. Sync positions first.`);
    const r = await api('/api/broker/alpaca/orders', {method:'POST', body:JSON.stringify({symbol, side:'sell', qty:cleanQty(q), type:'market', time_in_force:'day'})});
    state.executionResults.alpaca = {submitted:1, results:[r]};
    log('Alpaca reduce', `Market sell submitted for ${symbol} · qty ${cleanQty(q)}.`, 'good');
    await syncAlpaca();
  } else if(broker === 'etoro'){
    if(!positionId || !instrumentId) throw new Error(`Missing eToro positionId/instrumentId for ${symbol}. Sync portfolio first; if still missing, the eToro API did not expose a closable position id.`);
    const deduct = units || null;
    const r = await api('/api/broker/etoro/close-position', {method:'POST', body:JSON.stringify({positionId, instrumentId, unitsToDeduct:deduct})});
    state.executionResults.etoro = {submitted:1, results:[r]};
    log('eToro reduce', `Demo reduce-position submitted for ${symbol}.`, 'good');
    await syncEtoro();
  }
  setStatus(`Reduce 50% order submitted for ${symbol}.`, 'good');
}
function applyStrategyJson(){ const txt = state._strategyDraft || $('strategy-json')?.value || '{}'; const obj=JSON.parse(txt); state.config=merge(DEFAULT_CONFIG,obj); saveConfig(); state.backtest.stale=true; log('Strategy','Applied JSON strategy configuration.','good'); }
function resetLocalConfig(){ localStorage.removeItem('algohns_v9_config'); state.config=structuredClone(DEFAULT_CONFIG); log('Settings','Local config reset.','warn'); render(); }
function exportSnapshot(){ const blob=new Blob([`<html><body><h1>Algohns Snapshot</h1><pre>${html(JSON.stringify({ts:new Date().toISOString(),config:state.config,core:state.core,alpaca:state.alpaca,etoro:state.etoro},null,2))}</pre></body></html>`],{type:'text/html'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='algohns_snapshot.html'; a.click(); URL.revokeObjectURL(a.href); }

const NAV_GROUPS = [
  { label: 'Control Center', view: 'control', icon: 'ti-layout-dashboard' },
  { label: 'Strategy Studio', view: 'strategy', icon: 'ti-adjustments-horizontal' },
  { label: 'Backtest Lab', view: 'backtest', icon: 'ti-chart-line' },
  {
    key: 'portfolio', label: 'Portfolio', icon: 'ti-briefcase', group: true,
    children: [
      { label: 'Combined View', view: 'portfolio-combined', icon: 'ti-layers-union' },
      { label: 'Alpaca Demo', view: 'portfolio-alpaca', icon: 'ti-building-bank' },
      { label: 'eToro Demo', view: 'portfolio-etoro', icon: 'ti-trending-up' },
    ]
  },
  {
    key: 'orders', label: 'Orders', icon: 'ti-list-details', group: true,
    children: [
      { label: 'All Orders', view: 'orders-all', icon: 'ti-stack-2' },
      { label: 'Alpaca Orders', view: 'orders-alpaca', icon: 'ti-building-bank' },
      { label: 'eToro Orders', view: 'orders-etoro', icon: 'ti-trending-up' },
    ]
  },
  { label: 'Risk Center', view: 'risk', icon: 'ti-shield-check' },
  { label: 'News Intelligence', view: 'news', icon: 'ti-news' },
  { label: 'Universe Explorer', view: 'universe', icon: 'ti-world' },
  { label: 'Settings', view: 'settings', icon: 'ti-settings' },
];

const PAGES = {
  control:{ title:'Control Center', subtitle:'Portfolio-first quant cockpit: equity curve, allocation, risk and latest decision above the fold.', render:renderControl },
  strategy:{ title:'Strategy Studio', subtitle:'Configure strategy profile, limits and advanced JSON without hidden ticker lists.', render:renderStrategy },
  backtest:{ title:'Backtest Lab', subtitle:'Load real market data explicitly, then run dated backtests on the selected universe.', render:renderBacktest },
  'portfolio-combined':{ title:'Combined Portfolio', subtitle:'Aggregated Alpaca Demo + eToro Demo portfolio, allocation, P&L and risk.', render:()=>renderPortfolio('portfolio-combined') },
  'portfolio-alpaca':{ title:'Alpaca Demo Portfolio', subtitle:'Isolated Alpaca paper portfolio with broker-specific cash, value, positions and controls.', render:()=>renderPortfolio('portfolio-alpaca') },
  'portfolio-etoro':{ title:'eToro Demo Portfolio', subtitle:'Isolated eToro demo portfolio with broker-specific cash, value, positions and controls.', render:()=>renderPortfolio('portfolio-etoro') },
  'orders-all':{ title:'All Orders', subtitle:'Aggregated order previews and execution journal for Alpaca Demo + eToro Demo.', render:()=>renderOrders('orders-all') },
  'orders-alpaca':{ title:'Alpaca Orders', subtitle:'Alpaca paper order preview, safety controls and audit log.', render:()=>renderOrders('orders-alpaca') },
  'orders-etoro':{ title:'eToro Orders', subtitle:'eToro demo order preview, safety controls and audit log.', render:()=>renderOrders('orders-etoro') },
  portfolio:{ title:'Combined Portfolio', subtitle:'Aggregated Alpaca Demo + eToro Demo portfolio, allocation, P&L and risk.', render:()=>renderPortfolio('portfolio-combined') },
  orders:{ title:'All Orders', subtitle:'Aggregated order previews and execution journal for Alpaca Demo + eToro Demo.', render:()=>renderOrders('orders-all') },
  risk:{ title:'Risk Center', subtitle:'Risk gauges, concentration, drawdown and dynamic stress notes from the actual portfolio.', render:renderRisk },
  news:{ title:'News Intelligence', subtitle:'Sentiment overlay, earnings blackout and macro events for current positions.', render:renderNews },
  universe:{ title:'Universe Explorer', subtitle:'Visible Alpaca + eToro universe with inclusion and exclusion reasons.', render:renderUniverse },
  settings:{ title:'Settings', subtitle:'Connection status, local setup and safety lock.', render:renderSettings }
};

function navId(item){ return item.key || item.view || item.label.toLowerCase().replace(/[^a-z0-9]+/g,'-'); }
function renderNav(){
  const nav = $('nav'); if(!nav) return;
  nav.innerHTML = NAV_GROUPS.map(item => {
    const id = navId(item);
    if(item.group){
      const active = item.children.some(ch => ch.view === state.view);
      const open = active || state.navOpen[id];
      return `<div class="nav-group ${open?'open':''} ${active?'active-group':''}">
        <button class="nav-parent ${active?'active':''}" data-nav-toggle="${html(id)}">
          <i class="ti ${html(item.icon)}"></i><span>${html(item.label)}</span><em id="nav-badge-${html(id)}"></em><i class="ti ti-chevron-down nav-chevron"></i>
        </button>
        <div class="nav-children">${item.children.map(ch => `
          <button data-view="${html(ch.view)}" class="nav-child ${state.view===ch.view?'active':''}">
            <i class="ti ${html(ch.icon)}"></i><span>${html(ch.label)}</span><em id="nav-badge-${html(ch.view)}"></em>
          </button>`).join('')}
        </div>
      </div>`;
    }
    return `<button data-view="${html(item.view)}" class="${state.view===item.view?'active':''}"><i class="ti ${html(item.icon)}"></i><span>${html(item.label)}</span><em id="nav-badge-${html(id)}"></em></button>`;
  }).join('');
}


function icon(name){ return `<i class="ti ${name}"></i>`; }
function cardHeader(label, action=''){ return `<div class="card-header"><h2>${html(label)}</h2>${action||''}</div>`; }
function emptyState(message, iconName='ti-inbox', action=null){
  return `<div class="empty-state"><i class="ti ${iconName}"></i><span>${html(message)}</span>${action ? `<button data-act="${html(action.act)}" class="${action.cls||'primary'}"><i class="ti ${html(action.icon)}"></i> ${html(action.label)}</button>` : ''}</div>`;
}
function metricCard(label,value,delta='',cls=''){
  return `<div class="metric"><div class="metric-label">${html(label)}</div><div class="metric-value ${cls}">${value}</div>${delta?`<div class="metric-delta ${cls}">${delta}</div>`:''}</div>`;
}

function renderControl(){
  const mood = portfolioMood();
  const weights = combinedWeights();
  return `<div class="grid">
    <div class="card span-12">
      ${cardHeader('Portfolio state')}
      <div class="metric-grid">
        ${metricCard('Mood', mood.title, mood.text, mood.cls)}
        ${metricCard('Open P&L', money(totalOpenPnl()), totalOpenPnl()>=0?'positive':'under pressure', totalOpenPnl()>=0?'good':'bad')}
        ${metricCard('Gross exposure', pct(grossExposure()), `${combinedPositions().length} active positions`)}
        ${metricCard('Engine', state.running?'Running':'Stopped', `Mode: ${state.config.mode}`, state.running?'good':'')}
      </div>
    </div>
    <div class="card span-8">
      ${cardHeader('Equity curve', '<button data-act="load-backtest-data" class="card-action"><i class="ti ti-download"></i> Load data</button>')}
      ${renderEquityBlock('main')}
    </div>
    <div class="card span-4">
      ${cardHeader('Risk bar')}
      ${riskGauges()}
    </div>
    <div class="card span-5">
      ${cardHeader('Allocation treemap')}
      ${renderTreemap(weights)}
    </div>
    <div class="card span-4">
      ${cardHeader('Last decision')}
      ${lastDecisionCard()}
    </div>
    <div class="card span-3">
      ${cardHeader('Engine snapshot')}
      ${engineCard()}
    </div>
    <div class="card span-12">
      ${cardHeader('Latest audit log')}
      ${logTable(8)}
    </div>
  </div>`;
}
function portfolioMood(){
  const p=combinedPositions(); const pnl=totalOpenPnl();
  if(!p.length) return {cls:'warn',title:'Not synced',text:'Refresh brokers to read portfolio state.'};
  if(pnl>0) return {cls:'good',title:'Positive',text:'Portfolio is making money; check risk quality.'};
  if(pnl<0) return {cls:'bad',title:'Under pressure',text:'Losses detected; inspect concentration and signals.'};
  return {cls:'warn',title:'Flat',text:'No clear P&L direction yet.'};
}
function totalOpenPnl(){ return combinedPositions().reduce((a,x)=>a+positionPnl(x),0); }
function grossExposure(){ const w=combinedWeights(); return Object.values(w).reduce((a,b)=>a+Math.abs(Number(b)||0),0); }
function engineCard(){
  const r=state.core.regime;
  return `<table>
    <tr><td>Mode</td><td><span class="regime-badge">${html(state.config.mode)}</span></td></tr>
    <tr><td>Regime</td><td>${r?html(r.label):'—'}</td></tr>
    <tr><td>Ranked assets</td><td>${state.core.rank.length}</td></tr>
    <tr><td>Targets</td><td>${Object.keys(state.core.targets||{}).length}</td></tr>
    <tr><td>Backtest data</td><td>${state.backtest.loaded && !state.backtest.stale?'Ready':'Needs load'}</td></tr>
  </table>`;
}
function lastDecisionCard(){
  const decision = state.core.lastDecision || 'No decision yet. Sync brokers, load universe and run the strategy engine.';
  return `<div class="decision-card"><strong>Decision rationale</strong><br>${html(decision)}<br><br><span class="tertiary">Every engine run records the reason, universe size and detected regime.</span></div>`;
}

function renderEquityBlock(id){
  const series=getEquitySeries();
  if(!series) return emptyState('No data loaded', 'ti-chart-line', {act:'load-backtest-data', icon:'ti-download', label:'Load backtest data'});
  return `<div class="legend"><span class="legend-item"><i class="legend-line"></i> Portfolio</span><span class="legend-item"><i class="legend-line benchmark"></i> ${html(series.benchmarkLabel || state.config.backtest.benchmark || 'Benchmark')}</span></div><div class="chart-wrap"><canvas id="equity-${id}-chart" data-chart="equity"></canvas></div><div class="drawdown-wrap"><canvas id="drawdown-${id}-chart" data-chart="drawdown"></canvas></div>`;
}
function getEquitySeries(){
  if(state.backtest.result?.nav?.length){
    return {
      labels:state.backtest.result.nav.map(x=>formatDateLabel(x.date)),
      portfolio:state.backtest.result.nav.map(x=>x.value),
      benchmark:state.backtest.result.benchmark?.map(x=>x.value) || [],
      drawdown:state.backtest.result.drawdown?.map(x=>x.value*100) || [],
      benchmarkLabel:state.config.backtest.benchmark
    };
  }
  const h=state.alpaca.history;
  if(h?.equity && Array.isArray(h.equity) && h.equity.length){
    const labels = Array.isArray(h.timestamp) ? h.timestamp.map(x=>formatDateLabel(Number(x)*1000,{month:'short', day:'numeric'})) : h.equity.map((_,i)=>String(i+1));
    const portfolio = h.equity.map(Number).filter(Number.isFinite);
    if(portfolio.length) return {labels:labels.slice(-portfolio.length), portfolio, benchmark:[], drawdown:drawdownFromValues(portfolio), benchmarkLabel:'Benchmark'};
  }
  return null;
}

function drawdownFromValues(vals){ let peak=0; return vals.map(v=>{ peak=Math.max(peak,v); return peak ? (v/peak-1)*100 : 0; }); }
function initCharts(){
  if(!window.Chart) return;
  document.querySelectorAll('canvas[data-chart="equity"]').forEach(canvas=>{
    const s=getEquitySeries(); if(!s) return;
    const datasets=[{label:'Portfolio',data:s.portfolio,borderColor:'#2563EB',backgroundColor:'rgba(37,99,235,0.06)',borderWidth:2,pointRadius:0,fill:true,tension:.25}];
    if(s.benchmark?.length) datasets.push({label:s.benchmarkLabel||'Benchmark',data:s.benchmark,borderColor:'#A8A29E',borderWidth:1.5,pointRadius:0,borderDash:[4,4],fill:false,tension:.25});
    destroyChart(canvas.id);
    registerChart(canvas.id, new Chart(canvas.getContext('2d'),{type:'line',data:{labels:s.labels,datasets},options:chartOptions(false)}));
  });
  document.querySelectorAll('canvas[data-chart="drawdown"]').forEach(canvas=>{
    const s=getEquitySeries(); if(!s?.drawdown?.length) return;
    destroyChart(canvas.id);
    registerChart(canvas.id, new Chart(canvas.getContext('2d'),{type:'line',data:{labels:s.labels,datasets:[{label:'Drawdown',data:s.drawdown,borderColor:'#DC2626',backgroundColor:'rgba(220,38,38,0.05)',borderWidth:1.5,pointRadius:0,fill:true,tension:.2}]},options:chartOptions(true)}));
  });
}
function chartOptions(drawdown=false){
  const dark=document.body.classList.contains('dark-mode');
  const tip=tooltipTheme();
  return {responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{...tip,borderWidth:1,padding:10,mode:'index',intersect:false}},interaction:{mode:'index',intersect:false},scales:{x:{grid:{display:false},border:{display:false},ticks:{font:{size:11},color:'#A8A29E',maxTicksLimit:8,autoSkip:true,maxRotation:0}},y:{grid:{color:dark?'rgba(255,255,255,0.06)':'rgba(0,0,0,0.06)'},border:{display:false},ticks:{font:{size:11},color:'#A8A29E',callback:v=>drawdown?`${v}%`:v}}}};
}

function riskGauges(){
  const w=combinedWeights(); const abs=Object.values(w).map(x=>Math.abs(Number(x)||0));
  const maxW=Math.max(0,...abs); const gross=abs.reduce((a,b)=>a+b,0); const dd=state.backtest.result?.metrics?.maxDD || 0;
  return gauge('Max position', maxW, state.config.risk.maxPosition)
    + gauge('Gross exposure', gross, state.config.risk.maxGross)
    + gauge('Drawdown to kill', Math.abs(dd), Math.abs(state.config.risk.autoKillDrawdown));
}
function gauge(name,val,lim){
  const ratio=lim?clamp(val/lim,0,1):0; const cls=ratio>.9?'bad':ratio>.65?'warn':'good';
  return `<div class="risk-gauge ${cls}"><div class="risk-row"><b>${html(name)}</b><span>${pct(val)} / ${pct(lim)}</span></div><div class="risk-track"><i style="width:${ratio*100}%"></i></div><small>${ratio>.9?'Close to limit':ratio>.65?'Monitor closely':'Inside limit'}</small></div>`;
}
function renderTreemap(weights){
  const entries=Object.entries(weights||{}).filter(([,v])=>Math.abs(v)>0.005).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,16);
  if(!entries.length) return emptyState('No positions', 'ti-layout');
  const cols = entries.length <= 4 ? entries.length : entries.length <= 9 ? 3 : 4;
  return `<div class="treemap-grid" style="grid-template-columns:repeat(${cols},1fr)">${entries.map(([s,w])=>{
    const isPos=w>=0;
    return `<div class="tile ${isPos?'pos':'neg'}" title="${html(nameOf(s))}"><span class="tile-name">${html(s)}</span><span class="tile-meta">${html(classOf(s))}</span><span class="tile-value ${isPos?'good':'bad'}">${pct(Math.abs(w))}</span></div>`;
  }).join('')}</div>`;
}
function combinedWeights(){
  const pos=combinedPositions();
  if(pos.length){ const total=pos.reduce((a,p)=>a+Math.abs(positionValue(p)),0)||1; const out={}; pos.forEach(p=>{ const s=positionSymbol(p); if(s) out[s]=(out[s]||0)+positionValue(p)/total; }); return out; }
  return state.core.targets || {};
}
function combinedPositions(){ return [...(state.alpaca.positions||[]).map(p=>({...p,_broker:'alpaca'})), ...(state.etoro.positions||[]).map(p=>({...p,_broker:'etoro'}))]; }

function renderStrategy(){
  return `<div class="grid">
    <div class="card span-12">${cardHeader('Official profile')}<div class="mode-grid">${['Defensive','Balanced','Advanced','Aggressive'].map(mode=>`<label class="mode-card ${state.config.mode===mode?'active':''}"><input type="radio" name="strategy-mode-radio" value="${mode}" ${state.config.mode===mode?'checked':''} onchange="document.getElementById('strategy-mode').value='${mode}';document.getElementById('strategy-mode').dispatchEvent(new Event('change',{bubbles:true}))" style="display:none"><h3>${mode}</h3><p>${mode==='Defensive'?'Lower concentration and more cash.':mode==='Balanced'?'Default profile for diversified demo execution.':mode==='Advanced'?'Broader ranking and higher selection intensity.':'More aggressive risk usage, still demo-only.'}</p></label>`).join('')}</div><select id="strategy-mode" style="display:none"><option ${state.config.mode==='Defensive'?'selected':''}>Defensive</option><option ${state.config.mode==='Balanced'?'selected':''}>Balanced</option><option ${state.config.mode==='Advanced'?'selected':''}>Advanced</option><option ${state.config.mode==='Aggressive'?'selected':''}>Aggressive</option></select></div>
    <div class="card span-5">${cardHeader('Risk limits')}${riskConfigTable()}</div>
    <div class="card span-7">${cardHeader('Engine readout')}${engineCard()}<div class="actions-row" style="margin-top:12px"><button data-act="run-core" class="primary"><i class="ti ti-rocket"></i> Run strategy engine</button><button data-act="send-orders-all"><i class="ti ti-send"></i> Send prepared demo orders</button><button data-act="load-backtest-data"><i class="ti ti-download"></i> Load data</button></div></div>
    <div class="card span-12">${cardHeader('Advanced Strategy JSON')}<textarea id="strategy-json" class="code">${html(JSON.stringify(state.config,null,2))}</textarea><div class="actions-row" style="margin-top:12px"><button data-act="apply-json" class="primary"><i class="ti ti-check"></i> Apply JSON</button><button data-act="reset-config" class="danger"><i class="ti ti-restore"></i> Reset local config</button></div></div>
  </div>`;
}
function riskConfigTable(){ return `<table><tr><th>Field</th><th>Value</th></tr><tr><td>Max position</td><td>${pct(state.config.risk.maxPosition)}</td></tr><tr><td>Max gross</td><td>${pct(state.config.risk.maxGross)}</td></tr><tr><td>Cash floor</td><td>${pct(state.config.risk.cashFloor)}</td></tr><tr><td>Auto kill drawdown</td><td>${pct(state.config.risk.autoKillDrawdown)}</td></tr><tr><td>Min trade value</td><td>${money(state.config.execution.minTradeValue)}</td></tr></table>`; }

function renderBacktest(){
  const r=state.backtest.result;
  return `<div class="grid">
    <div class="card span-12">${cardHeader('Backtest controls')}<div class="actions-row"><select id="bt-period"><option ${state.config.backtest.period==='1Y'?'selected':''}>1Y</option><option ${state.config.backtest.period==='3Y'?'selected':''}>3Y</option><option ${state.config.backtest.period==='5Y'?'selected':''}>5Y</option><option ${state.config.backtest.period==='10Y'?'selected':''}>10Y</option></select><select id="bt-benchmark"><option ${state.config.backtest.benchmark==='SPY'?'selected':''}>SPY</option><option ${state.config.backtest.benchmark==='QQQ'?'selected':''}>QQQ</option><option ${state.config.backtest.benchmark==='TLT'?'selected':''}>TLT</option></select><select id="bt-universe"><option ${state.config.backtest.universe==='Visible filtered universe'?'selected':''}>Visible filtered universe</option><option ${state.config.backtest.universe==='Alpaca universe'?'selected':''}>Alpaca universe</option><option ${state.config.backtest.universe==='eToro universe'?'selected':''}>eToro universe</option><option ${state.config.backtest.universe==='Core ETFs'?'selected':''}>Core ETFs</option></select><button data-act="load-backtest-data"><i class="ti ti-download"></i> Load data</button><button data-act="run-backtest" class="primary"><i class="ti ti-rocket"></i> Run backtest</button></div><p class="muted">Status: ${state.backtest.stale?'Data stale or missing — load again after changing settings.':state.backtest.loaded?'Data ready.':'No data loaded.'}</p></div>
    <div class="card span-8">${cardHeader('NAV vs benchmark')}${r?renderEquityBlock('backtest'):emptyState('No backtest result', 'ti-chart-line', {act:'load-backtest-data', icon:'ti-download', label:'Load backtest data'})}</div>
    <div class="card span-4">${cardHeader('Metrics')}${r?metricsTable(r.metrics):emptyState('No metrics yet', 'ti-gauge')}</div>
    <div class="card span-12">${cardHeader('Selected universe')}<p class="muted">${r?html(r.symbols.join(', ')):'Run a backtest to see exactly which assets were selected.'}</p></div>
  </div>`;
}
function svgBacktest(r){ return renderEquityBlock('legacy'); }
function metricsTable(m){ return `<div class="metric-grid" style="grid-template-columns:1fr"><div>${metricCard('Total return', pct(m.return), '', m.return>=0?'good':'bad')}</div><div>${metricCard('Volatility', pct(m.vol))}</div><div>${metricCard('Max drawdown', pct(m.maxDD), '', 'bad')}</div><div>${metricCard('Sharpe', num(m.sharpe,2), '', m.sharpe>=1?'good':m.sharpe<0?'bad':'')}</div></div>`; }

function combinedSummaryBar(){
  const alpacaConnected = !!(state.alpaca.lastSync || state.alpaca.account || state.alpaca.status?.configured);
  const etoroConnected = !!(state.etoro.lastSync || state.etoro.status?.connected || state.etoro.positions?.length);
  return `<div class="broker-bar combined">
    <span class="broker-title"><span class="broker-dot ${alpacaConnected || etoroConnected ? 'on' : ''}"></span><b>Combined View</b></span>
    <span>Cash <b>${money(totalCash())}</b></span>
    <span>Portfolio <b>${money(totalValue())}</b></span>
    <span>Positions <b>${combinedPositions().length}</b></span>
    <span class="tertiary">Alpaca ${alpacaConnected?'synced':'idle'} · eToro ${etoroConnected?'synced':'idle'}</span>
    <button data-act="refresh"><i class="ti ti-refresh"></i> Sync all</button>
  </div>`;
}
function brokerBar(broker){
  const cash = brokerCash(broker);
  const value = brokerValue(broker);
  const isConnected = !!state[broker]?.lastSync;
  return `<div class="broker-bar">
    <span class="broker-title"><span class="broker-dot ${isConnected ? 'on' : ''}"></span><b>${brokerLabel(broker)}</b></span>
    <span>Cash <b>${money(cash)}</b></span>
    <span>Portfolio <b>${money(value)}</b></span>
    <span>P&L today <b class="${brokerDailyPnl(broker)>=0?'good':'bad'}">${brokerDailyPnl(broker)>=0?'+':''}${money(brokerDailyPnl(broker))}</b></span>
    <span>Positions <b>${positionsForBroker(broker).length}</b></span>
    ${state[broker]?.lastSync ? `<span class="tertiary">Last sync ${timeAgo(state[broker].lastSync)}</span>` : '<span class="tertiary">Not synced yet</span>'}
    <button data-act="run-core"><i class="ti ti-rocket"></i> Run engine</button>
    <button data-act="send-orders-${broker}"><i class="ti ti-send"></i> Send ${brokerLabel(broker)}</button>
    <button data-act="sync-${broker}"><i class="ti ti-refresh"></i> Sync</button>
  </div>`;
}
function kpiStrip(broker){
  const positions = positionsForBroker(broker);
  const value = brokerValue(broker);
  const cash = brokerCash(broker);
  const invested = Math.max(0, value - cash);
  const totalPnl = positions.reduce((s,p) => s + positionPnl(p), 0);
  const dailyPnl = brokerDailyPnl(broker);
  const cards = [
    { label:'Portfolio value', value:money(value) },
    { label:'Cash available', value:money(cash), delta:`${pct(cash/(value||1))} of total` },
    { label:'Invested', value:money(invested) },
    { label:'Total P&L', value:`${totalPnl>=0?'+':''}${money(totalPnl)}`, delta:pct(totalPnl/(invested||1)), cls:totalPnl>=0?'good':'bad' },
    { label:"Today's P&L", value:`${dailyPnl>=0?'+':''}${money(dailyPnl)}`, cls:dailyPnl>=0?'good':'bad' },
  ];
  return `<div class="kpi-strip">${cards.map(c => `
    <div class="metric">
      <div class="metric-label">${html(c.label)}</div>
      <div class="metric-value ${c.cls||''}">${c.value}</div>
      ${c.delta ? `<div class="metric-delta ${c.cls||''}">${c.delta}</div>` : ''}
    </div>`).join('')}</div>`;
}
function equityChartBlock(broker){
  const id = `equity-${broker}`;
  return `<div class="card portfolio-chart-card">
    <div class="card-header chart-header">
      <h2>Equity curve</h2>
      <div class="chart-toolbar">
        <div class="legend"><span class="legend-item"><i class="legend-line"></i> Portfolio</span><span class="legend-item"><i class="legend-line benchmark"></i> Benchmark</span></div>
        <div class="range-group">${['1W','1M','3M','6M','1Y','All'].map(r => `<button class="range-btn" data-broker="${broker}" data-range="${r}" onclick="setChartRange('${r}','${broker}')">${r}</button>`).join('')}</div>
      </div>
    </div>
    <div id="${id}" class="portfolio-equity-wrap"><canvas id="${id}-canvas" role="img" aria-label="Equity curve for ${html(brokerLabel(broker))}"></canvas></div>
  </div>`;
}
function pieChartBlock(broker){
  const id = `pie-${broker}`;
  return `<div class="card allocation-card">
    ${cardHeader('Allocation')}
    <div id="${id}" class="pie-layout">
      <div class="pie-canvas-wrap"><canvas id="${id}-canvas" role="img" aria-label="Portfolio allocation doughnut chart"></canvas></div>
      <div id="${id}-legend" class="pie-legend"></div>
    </div>
  </div>`;
}
function compositionBlock(positions){
  if(!positions.length) return `<div class="card">${cardHeader('Composition')}${emptyState('No positions', 'ti-list')}</div>`;
  const total = positions.reduce((s,p) => s + Math.abs(positionValue(p)), 0) || 1;
  const byClass = {};
  positions.forEach(p => { const cls = classOf(positionSymbol(p)); byClass[cls] = (byClass[cls] || 0) + Math.abs(positionValue(p)); });
  const rows = Object.entries(byClass).sort((a,b)=>b[1]-a[1]).map(([cls,val]) => {
    const w = val/total;
    return `<div class="composition-row"><div><b>${html(cls)}</b><span>${pct(w)}</span></div><div class="composition-track"><i style="width:${(w*100).toFixed(1)}%"></i></div></div>`;
  }).join('');
  return `<div class="card">${cardHeader('Composition')}<div>${rows}</div><div class="composition-foot">${positions.length} positions · ${pct(total/(brokerValue('combined')||total))} of synced value represented by positions</div></div>`;
}
function positionsTableFull(positions, broker){
  if(!positions.length) return `<div class="card">${emptyState('No positions synced yet', 'ti-briefcase', {act:broker==='combined'?'refresh':`sync-${broker}`, icon:'ti-refresh', label: broker==='combined'?'Sync brokers':`Sync ${broker}`})}</div>`;
  const total = positions.reduce((s,p) => s + Math.abs(positionValue(p)), 0) || 1;
  const rows = positions.map(p => {
    const rowBroker = p._broker || broker;
    const sym = positionSymbol(p);
    const val = Math.abs(positionValue(p));
    const qty = positionQty(p);
    const pnl = positionPnl(p);
    const pnlPct = pnl / Math.abs(val - pnl || 1);
    const weight = val / total;
    const isPos = pnl >= 0;
    return `<tr class="position-row">
      <td class="asset-cell"><div class="ticker">${html(sym)}</div><div class="asset-name" title="${html(nameOf(sym))}">${html(nameOf(sym))}${broker==='combined' ? ` · ${html(rowBroker)}` : ''}</div></td>
      <td class="spark-cell">${sparklineFor(sym,pnl)}</td>
      <td>${num(qty,4)}</td>
      <td><b>${money(val)}</b></td>
      <td><div class="${isPos?'good':'bad'}"><b>${isPos?'+':''}${money(pnl)}</b></div><div class="small ${isPos?'good':'bad'}">${isPos?'+':''}${pct(pnlPct)}</div></td>
      <td><div class="weight-cell"><div class="weight-track"><i style="width:${(weight*100).toFixed(1)}%"></i></div><span>${pct(weight)}</span></div></td>
      <td><span class="sentiment-dot ${sentimentClass(sym,pnl)}" title="${sentimentClass(sym,pnl)} sentiment"></span></td>
      <td><div class="actions-row"><button data-act="exit-position" data-broker="${html(rowBroker)}" data-symbol="${html(sym)}" data-qty="${qty}" data-position-id="${html(positionId(p))}" data-instrument-id="${html(positionInstrumentId(p))}" data-units="${positionUnits(p)}" class="compact danger"><i class="ti ti-x"></i> Exit</button><button data-act="reduce-position" data-broker="${html(rowBroker)}" data-symbol="${html(sym)}" data-qty="${qty/2}" data-position-id="${html(positionId(p))}" data-instrument-id="${html(positionInstrumentId(p))}" data-units="${positionUnits(p)/2}" class="compact"><i class="ti ti-arrows-minimize"></i> −50%</button><button data-act="exclude-symbol" data-symbol="${html(sym)}" class="compact"><i class="ti ti-eye-off"></i></button></div></td>
    </tr>`;
  }).join('');
  return `<div class="card positions-full" style="padding:0;overflow:hidden">
    <div class="table-title"><span>Positions</span><em>${positions.length} open</em></div>
    <div class="position-table-wrap"><table><tr><th>Asset</th><th>Trend</th><th>Qty</th><th>Value</th><th>P&L</th><th>Weight</th><th>Sentiment</th><th>Actions</th></tr>${rows}</table></div>
  </div>`;
}
function pnlBreakdown(broker){
  const positions = positionsForBroker(broker);
  const dailyPnl = brokerDailyPnl(broker);
  const sorted = [...positions].sort((a,b)=>positionPnl(b)-positionPnl(a));
  const winners = sorted.filter(p=>positionPnl(p)>=0).slice(0,3);
  const losers = sorted.filter(p=>positionPnl(p)<0).slice(-3).reverse();
  const miniRows = (rows, kind) => rows.length ? rows.map(p => {
    const pnl=positionPnl(p); return `<div class="pnl-mini-row"><span>${html(positionSymbol(p))}</span><b class="${pnl>=0?'good':'bad'}">${pnl>=0?'+':''}${money(pnl)}</b></div>`;
  }).join('') : `<div class="muted">No ${kind} yet.</div>`;
  return `<div class="pnl-grid">
    <div class="card"><div class="mini-label">Today's P&L</div><div class="pnl-number ${dailyPnl>=0?'good':'bad'}">${dailyPnl>=0?'+':''}${money(dailyPnl)}</div><div class="muted">From broker account fields when available.</div></div>
    <div class="card"><div class="mini-label">Top winners</div>${miniRows(winners,'winners')}</div>
    <div class="card"><div class="mini-label">Worst positions</div>${miniRows(losers,'losers')}</div>
  </div>`;
}
function renderPortfolio(view){
  const broker = brokerFromView(view);
  const positions = positionsForBroker(broker);
  const header = broker !== 'combined' ? brokerBar(broker) : combinedSummaryBar();
  return `${header}${kpiStrip(broker)}${equityChartBlock(broker)}<div class="portfolio-two-col">${pieChartBlock(broker)}${compositionBlock(positions)}</div>${positionsTableFull(positions, broker)}${pnlBreakdown(broker)}`;
}
function portfolioHistorySeries(broker){
  let labels=[], portfolio=[], benchmark=[], benchmarkLabel='Benchmark';
  if(broker === 'combined' && state.backtest.result?.nav?.length){
    labels = state.backtest.result.nav.map(x=>formatDateLabel(x.date,{month:'short', day:'numeric'}));
    portfolio = state.backtest.result.nav.map(x=>Number(x.value)).filter(Number.isFinite);
    benchmark = (state.backtest.result.benchmark || []).map(x=>Number(x.value)).filter(Number.isFinite);
    benchmarkLabel = state.config.backtest.benchmark || 'Benchmark';
  } else if(broker === 'alpaca'){
    const h = state.alpaca.history;
    if(h?.equity && Array.isArray(h.equity)){
      portfolio = h.equity.map(Number).filter(Number.isFinite);
      labels = Array.isArray(h.timestamp) ? h.timestamp.slice(-portfolio.length).map(x=>formatDateLabel(Number(x)*1000,{month:'short', day:'numeric'})) : portfolio.map((_,i)=>String(i+1));
      benchmark = [];
      benchmarkLabel = 'Benchmark';
    }
  } else if(broker === 'etoro'){
    const h = state.etoro.portfolio?.history || state.etoro.portfolio?.equity || state.etoro.portfolio?.portfolio?.history || [];
    if(Array.isArray(h) && h.length){
      portfolio = h.map(x=>Number(x.value ?? x.equity ?? x.nav ?? x)).filter(Number.isFinite);
      labels = h.map((x,i)=> (x && typeof x === 'object' && (x.date || x.timestamp)) ? formatDateLabel(x.date || x.timestamp,{month:'short', day:'numeric'}) : String(i+1));
      benchmark = [];
    }
  }
  if(portfolio.length <= 1) return null;
  return {labels:labels.slice(-portfolio.length), portfolio, benchmark, benchmarkLabel};
}

function renderEquityChart(containerId, broker){
  const wrap = $(containerId);
  const canvas = $(containerId+'-canvas');
  const series = portfolioHistorySeries(broker);
  if(!series){
    destroyChart(containerId+'-canvas');
    const action = broker === 'alpaca' ? {act:'sync-alpaca', icon:'ti-refresh', label:'Sync Alpaca'} : broker === 'etoro' ? {act:'sync-etoro', icon:'ti-refresh', label:'Sync eToro'} : {act:'load-backtest-data', icon:'ti-download', label:'Load backtest data'};
    if(wrap) wrap.innerHTML = emptyState('No historical data loaded', 'ti-chart-line', action);
    return;
  }
  if(!canvas || !window.Chart) return;
  state._portfolioSeries = state._portfolioSeries || {};
  state._portfolioSeries[broker] = series;
  const dark=document.body.classList.contains('dark-mode');
  const tip=tooltipTheme();
  const datasets=[{label:brokerLabel(broker),data:series.portfolio,borderColor:'#2563EB',backgroundColor:'rgba(37,99,235,0.07)',fill:true,tension:.3,pointRadius:0,pointHoverRadius:4,borderWidth:2}];
  if(series.benchmark?.length){ datasets.push({label:series.benchmarkLabel,data:series.benchmark,borderColor:'#A8A29E',borderDash:[5,4],backgroundColor:'transparent',tension:.3,pointRadius:0,pointHoverRadius:3,borderWidth:1.5}); }
  destroyChart(containerId+'-canvas');
  registerChart(containerId+'-canvas', new Chart(canvas.getContext('2d'), {type:'line',data:{labels:series.labels,datasets},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:{...tip,borderWidth:1,padding:10,callbacks:{label:ctx=>` ${ctx.dataset.label}: ${money(ctx.parsed.y)}`}}},scales:{x:{grid:{display:false},border:{display:false},ticks:{font:{size:11},color:'#A8A29E',maxTicksLimit:8,autoSkip:true,maxRotation:0}},y:{grid:{color:dark?'rgba(255,255,255,0.06)':'rgba(0,0,0,0.05)',drawBorder:false},border:{display:false},ticks:{font:{size:11},color:'#A8A29E',callback:v=>money(v)}}}}}));
}
function setChartRange(range, broker){
  const rangeMap = { '1W':7, '1M':30, '3M':90, '6M':180, '1Y':365, 'All':99999 };
  const series = state._portfolioSeries?.[broker];
  state.chartRange[broker] = range;
  document.querySelectorAll(`.range-btn[data-broker="${broker}"]`).forEach(b=>{ b.classList.toggle('active', b.dataset.range===range); });
  const chartId = `equity-${broker}-canvas`;
  const chart = window._chartRegistry?.[chartId];
  if(!series || !chart) return;
  const days = rangeMap[range] || 99999;
  chart.data.labels = series.labels.slice(-days);
  chart.data.datasets[0].data = series.portfolio.slice(-days);
  if(chart.data.datasets[1]) chart.data.datasets[1].data = (series.benchmark || []).slice(-days);
  chart.update('active');
}
function renderPieChart(containerId, positions){
  const wrap=$(containerId), canvas=$(containerId+'-canvas'), legendEl=$(containerId+'-legend');
  if(!positions.length){ destroyChart(containerId+'-canvas'); if(wrap) wrap.innerHTML = emptyState('No positions', 'ti-chart-pie'); return; }
  if(!canvas || !window.Chart) return;
  const total = positions.reduce((s,p)=>s+Math.abs(positionValue(p)),0) || 1;
  const sorted = [...positions].sort((a,b)=>Math.abs(positionValue(b))-Math.abs(positionValue(a))).slice(0,10);
  const colors=['#2563EB','#16A34A','#D97706','#7C3AED','#0891B2','#DC2626','#059669','#9333EA','#EA580C','#0284C7'];
  const tip=tooltipTheme();
  destroyChart(containerId+'-canvas');
  registerChart(containerId+'-canvas', new Chart(canvas.getContext('2d'), {type:'doughnut',data:{labels:sorted.map(p=>positionSymbol(p)),datasets:[{data:sorted.map(p=>((Math.abs(positionValue(p))/total)*100).toFixed(2)),backgroundColor:colors,borderWidth:2,borderColor:tip.backgroundColor,hoverOffset:6}]},options:{responsive:true,maintainAspectRatio:false,cutout:'62%',plugins:{legend:{display:false},tooltip:{...tip,borderWidth:1,padding:10,callbacks:{label:ctx=>` ${ctx.label}: ${Number(ctx.parsed).toFixed(1)}%`}}}}}));
  if(legendEl) legendEl.innerHTML = sorted.map((p,i)=>{ const w=Math.abs(positionValue(p))/total; const pnl=positionPnl(p); return `<div class="pie-legend-row"><span style="background:${colors[i]}"></span><b>${html(positionSymbol(p))}</b><em>${pct(w)}</em><strong class="${pnl>=0?'good':'bad'}">${pnl>=0?'+':''}${pct(pnl/Math.abs(positionValue(p)||1))}</strong></div>`; }).join('');
}
function afterRenderPortfolio(view){
  const broker = brokerFromView(view);
  const positions = positionsForBroker(broker);
  function tryInit(attempt=0){
    if(!window.Chart){ if(attempt < 10) setTimeout(()=>tryInit(attempt+1),150); return; }
    renderEquityChart(`equity-${broker}`, broker);
    renderPieChart(`pie-${broker}`, positions);
    setChartRange(state.chartRange[broker] || 'All', broker);
  }
  setTimeout(()=>tryInit(), 50);
}


function renderOrders(view='orders-all'){
  const broker = brokerFromView(view);
  const previewRows = brokerPreviewOrders(broker);
  const brokerRows = brokerActualOrders(broker);
  const scopeTitle = broker === 'combined' ? 'All orders' : `${brokerLabel(broker)} orders`;
  const syncAction = broker === 'combined' ? 'refresh' : `sync-${broker}`;
  const sendAction = broker === 'combined' ? 'send-orders-all' : `send-orders-${broker}`;
  return `<div class="grid">
    <div class="card span-12">${cardHeader(scopeTitle)}<div class="actions-row orders-actions"><button data-act="run-core" class="primary"><i class="ti ti-rocket"></i> Prepare orders</button><button data-act="${sendAction}"><i class="ti ti-send"></i> Send to ${broker === 'combined' ? 'both brokers' : brokerLabel(broker)}</button><button data-act="play-pause"><i class="ti ${state.running?'ti-player-pause':'ti-player-play'}"></i> ${state.running?'Pause':'Play'} algorithm</button><button data-act="${syncAction}"><i class="ti ti-refresh"></i> Sync ${broker==='combined'?'brokers':brokerLabel(broker)}</button><button data-act="kill" class="danger push-right"><i class="ti ti-power"></i> Kill switch</button></div><p class="muted">Prepared orders are generated by the core algorithm. Broker orders are the real responses returned by Alpaca Paper/eToro Demo after sync.</p>${broker==='etoro'?'<p class="muted">eToro needs verified numeric instrument IDs before demo orders can be submitted. Use Universe Explorer → Load universe or Search eToro if the preview is empty.</p>':''}</div>
    <div class="card span-12">${cardHeader('Prepared orders to send')} ${ordersTableScoped(previewRows, broker, 'preview')}</div>
    ${orderDiagnosticsTable(broker)}
    <div class="card span-12">${cardHeader('Broker orders from API')} ${ordersTableScoped(brokerRows, broker, 'broker')}</div>
    <div class="card span-12">${cardHeader('Last execution response')} ${executionResultsTable(broker)}</div>
    <div class="card span-12">${cardHeader('Event log')}${logTable()}</div>
  </div>`;
}
function orderDiagnosticsTable(broker){
  const rows = broker === 'combined' ? [...(state.orderDiagnostics.alpaca||[]).map(x=>({...x,_broker:'alpaca'})), ...(state.orderDiagnostics.etoro||[]).map(x=>({...x,_broker:'etoro'}))] : (state.orderDiagnostics?.[broker] || []).map(x=>({...x,_broker:broker}));
  if(!rows.length) return '';
  return `<div class="card span-12">${cardHeader('Order preparation diagnostics')}<div class="position-table-wrap"><table><tr><th>Broker</th><th>Symbol</th><th>Issue</th><th>Detail</th></tr>${rows.slice(0,60).map(r=>`<tr><td>${html(r._broker)}</td><td><span class="ticker">${html(r.symbol||'—')}</span></td><td class="warn">${html(r.issue||'skipped')}</td><td>${html(r.detail||'')}</td></tr>`).join('')}</table></div></div>`;
}

function ordersTableScoped(rows, broker, kind='preview'){
  if(!rows?.length){
    const action = kind==='preview' ? {act:'run-core', icon:'ti-rocket', label:'Prepare orders'} : {act: broker==='combined'?'refresh':`sync-${broker}`, icon:'ti-refresh', label:'Sync broker orders'};
    return emptyState(kind==='preview' ? 'No prepared orders yet' : 'No broker orders returned by API yet', 'ti-list-details', action);
  }
  return `<div class="position-table-wrap"><table><tr><th>Broker</th><th>Symbol</th><th>Side</th><th>Qty / Notional</th><th>Status</th><th>Reason / Time</th></tr>${rows.map(o=>{
    const b=o._broker || broker;
    const sym=orderSymbol(o) || '—';
    const side=orderSide(o) || '—';
    const qty=Number(o.qty ?? o.quantity ?? o.filled_qty ?? o.units ?? o.Units ?? 0);
    const notional=orderNotional(o);
    const detail = qty ? `qty ${num(qty,4)}` : notional ? money(notional) : '—';
    const status = orderStatus(o);
    const reason = o.reason || o.submitted_at || o.created_at || o.CreateDate || o.sourcePath || o.id || '';
    return `<tr><td>${html(b)}</td><td><span class="ticker">${html(sym)}</span></td><td>${html(side)}</td><td>${html(detail)}</td><td class="${status.toLowerCase().includes('reject')||status.toLowerCase().includes('error')?'bad':status.toLowerCase().includes('fill')||status.toLowerCase().includes('accepted')||status.toLowerCase().includes('new')?'good':'warn'}">${html(status)}</td><td>${html(reason)}</td></tr>`;
  }).join('')}</table></div>`;
}
function executionResultsTable(broker='combined'){
  const brokers = broker === 'combined' ? ['alpaca','etoro'] : [broker];
  const rows=[];
  brokers.forEach(b=>{
    const r=state.executionResults?.[b];
    const items = Array.isArray(r?.results) ? r.results : [];
    if(!r) return;
    if(!items.length) rows.push({broker:b, symbol:'—', status:r.submitted ? 'submitted' : 'no-response', detail:JSON.stringify(r).slice(0,220)});
    items.forEach(x=>rows.push({broker:b, symbol:x.symbol || x.payload?.symbol || x.payload?.Symbol || '—', status:String(x.status || x.payload?.status || x.error || 'ok'), detail:x.error || x.payload?.message || x.payload?.error || x.payload?.id || JSON.stringify(x.payload || x).slice(0,220)}));
  });
  if(!rows.length) return emptyState('No execution response yet', 'ti-send');
  return `<div class="position-table-wrap"><table><tr><th>Broker</th><th>Symbol</th><th>Status</th><th>Detail</th></tr>${rows.map(r=>`<tr><td>${html(r.broker)}</td><td><span class="ticker">${html(r.symbol)}</span></td><td class="${String(r.status).match(/^2|ok|submitted/i)?'good':String(r.status).match(/^4|^5|error|reject/i)?'bad':'warn'}">${html(r.status)}</td><td>${html(r.detail)}</td></tr>`).join('')}</table></div>`;
}
function ordersTable(rows){ return ordersTableScoped(rows || [], 'combined', 'preview'); }

function renderRisk(){
  const p=combinedPositions();
  return `<div class="grid"><div class="card span-6">${cardHeader('Current risk')}${riskGauges()}</div><div class="card span-6">${cardHeader('Stress scenarios')}${p.length?stressTable():emptyState('No portfolio synced', 'ti-shield-check', {act:'refresh', icon:'ti-refresh', label:'Refresh brokers'})}</div></div>`;
}
function stressTable(){
  const weights=combinedWeights(); const equityWeight=Object.entries(weights).filter(([s])=>['SPY','QQQ','IWM','RSP','XLK','XLF','XLV','XLE','XLI','XLY','XLP'].includes(s)).reduce((a,[,w])=>a+Math.abs(w),0);
  const durationWeight=Object.entries(weights).filter(([s])=>['IEF','TLT','LQD','SHY'].includes(s)).reduce((a,[,w])=>a+Math.abs(w),0);
  const hedgeWeight=Object.entries(weights).filter(([s])=>['GLD','SLV','BIL','SH','PSQ'].includes(s)).reduce((a,[,w])=>a+Math.abs(w),0);
  return `<table><tr><th>Scenario</th><th>Dynamic read</th></tr><tr><td>Equity selloff</td><td class="${equityWeight>.55?'bad':'warn'}">${pct(equityWeight)} exposed to equity beta</td></tr><tr><td>Rates shock</td><td class="${durationWeight>.35?'warn':'good'}">${pct(durationWeight)} duration/credit sleeve</td></tr><tr><td>Defensive hedge rally</td><td class="${hedgeWeight>.20?'good':'warn'}">${pct(hedgeWeight)} defensive/hedge sleeve</td></tr></table>`;
}
function renderNews(){
  return `<div class="grid"><div class="card span-12"><div class="coming-soon"><i class="ti ti-news"></i><p>News Intelligence</p><div class="muted">Sentiment overlay per position · Earnings blackout · Macro events</div><span>Coming in V9.3</span></div></div></div>`;
}
function renderUniverse(){
  const rows=universeRows();
  return `<div class="grid"><div class="card span-12">${cardHeader('Universe controls')}<div class="actions-row"><button data-act="load-universe" class="primary"><i class="ti ti-world-download"></i> Load universe</button><button data-act="load-backtest-data"><i class="ti ti-download"></i> Load backtest data</button><input id="etoro-search" placeholder="Search eToro instrument e.g. AAPL, TSLA, SPY"><button data-act="etoro-search"><i class="ti ti-search"></i> Search eToro</button></div><p class="muted">No hidden fixed ticker list. Rows explain why each asset passed or needs data.</p></div><div class="card span-12">${cardHeader('Universe rows')}${universeTable(rows)}</div></div>`;
}
function universeRows(){ const alp=(state.alpaca.assets||[]).map(a=>({symbol:a.symbol,name:a.name,broker:'Alpaca',class:a.assetClass||a.asset_class||'Stock',status:a.tradable!==false?'passed':'excluded',reason:a.tradable!==false?'Tradable Alpaca asset':'Not tradable'})); const et=(state.etoro.assets||[]).map(a=>({symbol:a.symbol,name:a.name,broker:'eToro',class:a.assetClass||'eToro instrument',status:a.instrumentId?'passed':'needs_data',reason:a.instrumentId?'Verified eToro instrumentId':'Needs instrumentId verification'})); return dedupeRows([...alp,...et]).slice(0,500); }
function dedupeRows(rows){ const m=new Map(); rows.forEach(r=>m.set(`${r.broker}:${r.symbol}`,r)); return [...m.values()].sort((a,b)=>a.symbol.localeCompare(b.symbol)); }
function universeTable(rows){ if(!rows.length) return emptyState('Universe not loaded yet', 'ti-world', {act:'load-universe', icon:'ti-world-download', label:'Load universe'}); return `<div class="position-table-wrap"><table><tr><th>Symbol</th><th>Name</th><th>Broker</th><th>Class</th><th>Status</th><th>Reason</th></tr>${rows.map(r=>`<tr><td><span class="ticker">${html(r.symbol)}</span></td><td>${html(r.name||r.symbol)}</td><td>${html(r.broker)}</td><td>${html(r.class||'')}</td><td class="${r.status==='passed'?'good':r.status==='excluded'?'bad':'warn'}">${html(r.status)}</td><td>${html(r.reason)}</td></tr>`).join('')}</table></div>`; }

function renderSettings(){ return `<div class="grid"><div class="card span-4">${cardHeader('Alpaca Demo')} ${connectionTable(state.alpaca.status || {}, ['configured','alpacaPaper','baseKind','safety'])}<button data-act="sync-alpaca"><i class="ti ti-plug"></i> Test Alpaca</button></div><div class="card span-4">${cardHeader('eToro Demo')} ${connectionTable(state.etoro.status || {}, ['configured','connected','base','note'])}<button data-act="sync-etoro"><i class="ti ti-plug"></i> Test eToro API</button></div><div class="card span-4">${cardHeader('Safety')}<table><tr><td>Visible brokers</td><td>Alpaca + eToro</td></tr><tr><td>Real money</td><td class="bad">Locked</td></tr><tr><td>Kill switch</td><td>${state.config.execution.killSwitch?'Active':'Ready'}</td></tr></table></div><div class="card span-12">${cardHeader('Local setup')}<p class="muted">Use <b>0_START_HERE_INSTALL_KEYS_DEPLOY.bat</b>. It installs dependencies, uses local <b>npx wrangler</b>, stores eToro keys in <b>%USERPROFILE%\.algohns</b>, uploads Cloudflare secrets and deploys to <b>algohns</b>.</p></div></div>`; }
function connectionTable(obj,keys){ return `<table>${keys.map(k=>`<tr><td>${html(k)}</td><td>${html(obj?.[k] ?? '—')}</td></tr>`).join('')}</table>`; }
function logTable(limit=160){ if(!state.log.length) return emptyState('No log events yet', 'ti-clipboard-list'); const rows=state.log.slice(0,limit); return `<div class="log"><table><tr><th>Time</th><th>Area</th><th>Detail</th></tr>${rows.map(x=>`<tr><td>${html(x.ts)}</td><td class="${x.level}">${html(x.step)}</td><td>${html(x.detail)}</td></tr>`).join('')}</table></div>`; }

if(localStorage.getItem('algohns_v9_theme')==='dark') document.body.classList.add('dark-mode');
if(location.hash) state.view = location.hash.replace('#','');
bindOnce();
render();
