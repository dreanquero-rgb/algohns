function json(data, status=200){ return new Response(JSON.stringify(data,null,2),{status,headers:{'content-type':'application/json;charset=utf-8','cache-control':'no-store'}}); }
function text(data, status=200){ return new Response(data,{status,headers:{'content-type':'text/plain;charset=utf-8'}}); }
const CORS={ 'access-control-allow-origin':'*', 'access-control-allow-methods':'GET,POST,OPTIONS', 'access-control-allow-headers':'content-type,authorization' };
function withCors(r){ const h=new Headers(r.headers); Object.entries(CORS).forEach(([k,v])=>h.set(k,v)); return new Response(r.body,{status:r.status,headers:h}); }

export default {
  async fetch(request, env, ctx){
    if(request.method==='OPTIONS') return new Response(null,{headers:CORS});
    const url = new URL(request.url);
    try{
      if(url.pathname.startsWith('/api/')) return withCors(await routeApi(request, env));
      return env.ASSETS ? env.ASSETS.fetch(request) : text('Static assets binding missing. Deploy as Cloudflare Pages/Workers static assets.',500);
    } catch(e){ return withCors(json({error:e.message, stack:e.stack},500)); }
  },
  async scheduled(event, env, ctx){
    // Cloudflare cron hook for Alpaca Paper event-monitor cycles.
    // Safe by default: monitor/preview only unless ALG_AUTO_PAPER_ENABLED is explicitly set.
    ctx.waitUntil(scheduledCycle(event, env));
  }
};

async function routeApi(request, env){
  const url = new URL(request.url);
  if(url.pathname==='/api/health') return json({ok:true, service:'algohns', ts:new Date().toISOString()});
  if(url.pathname==='/api/market/prices') return marketPrices(url, env);
  if(url.pathname==='/api/cycle') return algohnsCycle(url, env);
  if(url.pathname==='/api/fundamentals') return fundamentals(url, env);
  if(url.pathname==='/api/broker/status') return brokerStatus(env);
  if(url.pathname==='/api/broker/diagnostics') return brokerDiagnostics(env);
  if(url.pathname==='/api/broker/alpaca/assets') return alpacaAssets(url, env);
  if(url.pathname==='/api/broker/alpaca/bars') return alpacaBars(url, env);
  if(url.pathname==='/api/broker/alpaca/account') return alpacaProxy('/v2/account', env);
  if(url.pathname==='/api/broker/alpaca/positions') return alpacaProxy('/v2/positions', env);
  if(url.pathname==='/api/broker/alpaca/orders') return alpacaOrders(request, env);
  if(url.pathname==='/api/broker/alpaca/cancel-open-orders') return alpacaCancelOpenOrders(env);
  if(url.pathname==='/api/broker/alpaca/portfolio-history') return alpacaProxy('/v2/account/portfolio/history?period=1M&timeframe=1D', env);
  if(url.pathname==='/api/news') return alpacaNews(url, env);
  if(url.pathname==='/api/broker/alpaca/batch-orders') return alpacaBatchOrders(request, env);

  // eToro Demo broker adapter. Demo-only by design; real-money endpoints are not exposed.
  if(url.pathname==='/api/broker/etoro/status') return etoroStatus(env);
  if(url.pathname==='/api/broker/etoro/me') return etoroProxy('/me', env);
  if(url.pathname==='/api/broker/etoro/portfolio') return etoroPortfolio(env);
  if(url.pathname==='/api/broker/etoro/orders') return etoroOrders(request, env);
  if(url.pathname==='/api/broker/etoro/search') return etoroSearch(url, env);
  if(url.pathname==='/api/broker/etoro/assets') return etoroAssets(url, env);
  if(url.pathname==='/api/broker/etoro/batch-orders') return etoroBatchOrders(request, env);
  if(url.pathname==='/api/broker/etoro/close-position') return etoroClosePosition(request, env);

  return json({error:'Unknown API route'},404);
}


async function scheduledCycle(event, env){
  try{
    const enabled = env.ALG_AUTO_PAPER_ENABLED === 'I_CONFIRM_ALPACA_PAPER_ONLY';
    console.log(JSON.stringify({service:'algohns', cron:event.cron, enabled, mode: env.ALG_DAILY_MODE || 'Aggressive', broker:'alpaca-paper', ts:new Date().toISOString()}));
    // Future production step: monitor data -> detect meaningful change -> persist preview -> risk gate -> optionally send Alpaca Paper orders.
  }catch(e){ console.log('Algohns scheduled cycle failed', e.message); }
}
async function algohnsCycle(url, env){
  const enabled = env.ALG_AUTO_PAPER_ENABLED === 'I_CONFIRM_ALPACA_PAPER_ONLY';
  return json({
    ok:true,
    mode: env.ALG_DAILY_MODE || url.searchParams.get('mode') || 'Aggressive',
    broker:'alpaca-paper',
    autoPaperEnabled: enabled,
    safety: enabled ? 'Alpaca Paper scheduler enabled by explicit server-side flag.' : 'Dry-run only. Set ALG_AUTO_PAPER_ENABLED=I_CONFIRM_ALPACA_PAPER_ONLY to allow scheduled Alpaca Paper order submission.',
    steps:['monitor market data','detect regime/risk/ranking change','generate target allocation','execution gate','paper execution only when explicitly enabled'],
    ts:new Date().toISOString()
  });
}

async function marketPrices(url, env){
  const symbols = (url.searchParams.get('symbols')||'').split(',').map(s=>s.trim().toUpperCase()).filter(Boolean).slice(0,150);
  const range = url.searchParams.get('range') || '2y';
  const rangeBars = range==='10y'?2520:range==='5y'?1260:range==='3y'?756:range==='2y'?520:range==='1y'?252:520;
  // Priority 1: FMP if configured. Priority 2: demo deterministic fallback.
  if(env.FMP_API_KEY){
    try{
      const prices={}; const quotes={};
      await Promise.all(symbols.map(async sym=>{
        const histUrl = `https://financialmodelingprep.com/stable/historical-price-eod/full?symbol=${sym}&apikey=${env.FMP_API_KEY}`;
        const qUrl = `https://financialmodelingprep.com/stable/quote-short?symbol=${sym}&apikey=${env.FMP_API_KEY}`;
        const [hr,qr] = await Promise.all([fetch(histUrl), fetch(qUrl)]);
        const hj = await hr.json(); const qj = await qr.json();
        const arr = Array.isArray(hj) ? hj : (hj.historical || hj.data || []);
        const cut = arr.slice(0,rangeBars).reverse().map(d=>({date:d.date, close:Number(d.close||d.adjClose||d.price), open:Number(d.open||d.close||d.price), high:Number(d.high||d.close||d.price), low:Number(d.low||d.close||d.price), volume:Number(d.volume||0)})).filter(d=>Number.isFinite(d.close)&&d.close>0);
        if(cut.length>250) prices[sym]=cut;
        const q = Array.isArray(qj) ? qj[0] : qj;
        const last = cut[cut.length-1];
        quotes[sym]={ price:Number(q?.price||last?.close||0), avgVolumeDollar:Number(q?.price||last?.close||0)*Number(q?.volume||last?.volume||1000000), source:'fmp' };
      }));
      if(Object.keys(prices).length) return json({meta:{mode:'real',provider:'FMP',symbols:Object.keys(prices).length,range},prices,quotes});
    }catch(e){ console.log('FMP price failed', e.message); }
  }
  const demo = demoPrices(symbols);
  return json({meta:{mode:'demo',provider:'deterministic fallback',notice:'Configure FMP_API_KEY or another provider secret for real prices.'},...demo});
}

async function fundamentals(url, env){
  const symbols = (url.searchParams.get('symbols')||'').split(',').map(s=>s.trim().toUpperCase()).filter(Boolean).slice(0,150);
  if(env.FMP_API_KEY){
    try{
      const out={};
      await Promise.all(symbols.map(async sym=>{
        const [profileR, ratiosR, metricsR, growthR] = await Promise.all([
          fetch(`https://financialmodelingprep.com/stable/profile?symbol=${sym}&apikey=${env.FMP_API_KEY}`),
          fetch(`https://financialmodelingprep.com/stable/ratios-ttm?symbol=${sym}&apikey=${env.FMP_API_KEY}`),
          fetch(`https://financialmodelingprep.com/stable/key-metrics-ttm?symbol=${sym}&apikey=${env.FMP_API_KEY}`),
          fetch(`https://financialmodelingprep.com/stable/income-statement-growth?symbol=${sym}&limit=1&apikey=${env.FMP_API_KEY}`)
        ]);
        const profile = first(await profileR.json());
        const ratios = first(await ratiosR.json());
        const metrics = first(await metricsR.json());
        const growth = first(await growthR.json());
        out[sym]={
          sector: profile?.sector || profile?.industry || 'Unknown',
          marketCap: num(profile?.marketCap),
          pe: num(ratios?.peRatioTTM ?? metrics?.peRatioTTM ?? metrics?.peRatio),
          evToEbitda: num(metrics?.enterpriseValueOverEBITDATTM ?? metrics?.evToEBITDATTM ?? metrics?.enterpriseValueOverEBITDA),
          roe: num(ratios?.returnOnEquityTTM ?? ratios?.returnOnEquity),
          roic: num(ratios?.returnOnInvestedCapitalTTM ?? ratios?.returnOnCapitalEmployedTTM ?? ratios?.returnOnCapitalEmployed),
          debtToEquity: num(ratios?.debtEquityRatioTTM ?? ratios?.debtEquityRatio),
          grossMargin: num(ratios?.grossProfitMarginTTM ?? ratios?.grossProfitMargin),
          epsGrowth: num(growth?.growthEPSDiluted ?? growth?.growthEPS),
          source:'fmp'
        };
      }));
      return json({meta:{mode:'fmp',provider:'Financial Modeling Prep'},fundamentals:out});
    }catch(e){ console.log('FMP fundamentals failed', e.message); }
  }
  const fundamentals={}; symbols.forEach(s=>fundamentals[s]=demoFundamental(s));
  return json({meta:{mode:'demo',provider:'deterministic fallback',notice:'Configure FMP_API_KEY for real quality/value/earnings factors.'},fundamentals});
}
function first(x){ return Array.isArray(x)?x[0]:x; }
function num(x){ const n=Number(x); return Number.isFinite(n)?n:null; }

function normalizedAlpacaBase(env){
  let raw = String(env.ALPACA_BASE_URL || 'https://paper-api.alpaca.markets').trim().replace(/\/+$/,'');
  // Users sometimes paste https://paper-api.alpaca.markets/v2.
  // Internally we append /v2/account, /v2/assets, etc., so normalize it here.
  raw = raw.replace(/\/v2$/,'');
  return raw;
}
function alpacaConfig(env){
  const base = normalizedAlpacaBase(env);
  const hasKeys = Boolean(env.ALPACA_API_KEY && env.ALPACA_SECRET_KEY);
  const isPaper = base === 'https://paper-api.alpaca.markets' || base.includes('paper-api.alpaca.markets');
  const isBrokerSandbox = base.includes('broker-api.sandbox.alpaca.markets');
  const liveUnlocked = env.ALLOW_LIVE_TRADING === 'I_UNDERSTAND_RISK';
  return {base, hasKeys, isPaper, isBrokerSandbox, liveUnlocked};
}
function brokerStatus(env){
  const cfg = alpacaConfig(env);
  let mode = 'internal-paper-only';
  let configWarning = null;
  if(cfg.hasKeys && cfg.isPaper) mode = 'alpaca-paper-configured';
  if(cfg.hasKeys && cfg.isBrokerSandbox) configWarning = 'ALPACA_BASE_URL is set to Broker API sandbox. Use Trading API Paper instead: https://paper-api.alpaca.markets';
  if(cfg.hasKeys && !cfg.isPaper && !cfg.liveUnlocked && !cfg.isBrokerSandbox) configWarning = 'ALPACA_BASE_URL is not the Alpaca paper endpoint. Paper execution will be blocked.';
  return json({ mode, alpacaPaper:cfg.hasKeys && cfg.isPaper, liveTradingUnlocked:cfg.liveUnlocked, baseKind: cfg.isPaper?'paper-trading-api':(cfg.isBrokerSandbox?'broker-api-sandbox':'unknown/non-paper'), baseExpected:'https://paper-api.alpaca.markets', configWarning, tradableUniverse: cfg.hasKeys&&cfg.isPaper?'broker-derived via Alpaca /v2/assets':'not available until Alpaca paper secrets are set correctly', dataUniverse: cfg.hasKeys?'broker bars via Alpaca Data API':'FMP/demo prices only', safety:'Live trading is locked unless explicit server-side unlock is set.' });
}
async function brokerDiagnostics(env){
  const cfg = alpacaConfig(env);
  const report = { ts:new Date().toISOString(), hasApiKey:Boolean(env.ALPACA_API_KEY), hasSecretKey:Boolean(env.ALPACA_SECRET_KEY), baseKind: cfg.isPaper?'paper-trading-api':(cfg.isBrokerSandbox?'broker-api-sandbox':'unknown/non-paper'), expectedBase:'https://paper-api.alpaca.markets', tests:[] };
  if(!cfg.hasKeys){ report.tests.push({name:'secrets', ok:false, detail:'Missing ALPACA_API_KEY or ALPACA_SECRET_KEY'}); return json(report,412); }
  if(cfg.isBrokerSandbox){ report.tests.push({name:'base-url', ok:false, detail:'Wrong Alpaca product: Broker API sandbox. This app needs Trading API Paper.'}); return json(report,400); }
  if(!cfg.isPaper && !cfg.liveUnlocked){ report.tests.push({name:'base-url', ok:false, detail:'Non-paper base URL blocked. Set ALPACA_BASE_URL to https://paper-api.alpaca.markets'}); return json(report,400); }
  async function test(name, url, base='trading'){
    try{
      const r = await fetch(url,{headers:{'APCA-API-KEY-ID':env.ALPACA_API_KEY,'APCA-API-SECRET-KEY':env.ALPACA_SECRET_KEY,'accept':'application/json'}});
      let body=''; try{ body=(await r.text()).slice(0,260); }catch{}
      report.tests.push({name, endpoint: base, ok:r.ok, status:r.status, requestId:r.headers.get('x-request-id'), detail: r.ok?'OK':body});
    }catch(e){ report.tests.push({name, endpoint:base, ok:false, detail:e.message}); }
  }
  await test('account', cfg.base + '/v2/account');
  await test('assets', cfg.base + '/v2/assets?status=active&asset_class=us_equity');
  await test('bars', 'https://data.alpaca.markets/v2/stocks/bars?symbols=SPY&timeframe=1Day&limit=5&feed=iex', 'data');
  await test('news', 'https://data.alpaca.markets/v1beta1/news?symbols=SPY&limit=1', 'data');
  return json(report, report.tests.every(t=>t.ok)?200:207);
}

async function alpacaAssets(url, env){
  const limit = Math.min(Number(url.searchParams.get('limit')||5000), 10000);
  const assetClass = url.searchParams.get('asset_class') || 'us_equity';
  const status = url.searchParams.get('status') || 'active';
  const classes = assetClass === 'all' ? ['us_equity','crypto'] : [assetClass];
  const all = [];
  const errors = [];
  for (const cls of classes) {
    const raw = await alpacaProxy(`/v2/assets?status=${encodeURIComponent(status)}&asset_class=${encodeURIComponent(cls)}`, env);
    if(raw.status >= 400){
      errors.push({assetClass:cls,status:raw.status});
      continue;
    }
    const arr = await raw.json();
    for(const a of (Array.isArray(arr)?arr:[])) all.push({...a, _requestedAssetClass:cls});
  }
  const bySymbol = new Map();
  for(const a of all){
    if(a.tradable && a.symbol && a.status === 'active') bySymbol.set(a.symbol, a);
  }
  const clean = [...bySymbol.values()]
    .map(a => ({symbol:a.symbol, name:a.name, exchange:a.exchange, assetClass:a.class || a.asset_class || a._requestedAssetClass, tradable:a.tradable, marginable:a.marginable, shortable:a.shortable, fractionable:a.fractionable, easyToBorrow:a.easy_to_borrow}))
    .sort((a,b)=> (a.symbol>b.symbol?1:-1))
    .slice(0, limit);
  return json({meta:{mode:'broker',provider:'alpaca',assetClass,status,count:clean.length,limit,errors},assets:clean});
}
async function alpacaBars(url, env){
  const symbols = (url.searchParams.get('symbols')||'').split(',').map(s=>s.trim().toUpperCase()).filter(Boolean).slice(0,200);
  const timeframe = url.searchParams.get('timeframe') || '1Day';
  const start = url.searchParams.get('start') || new Date(Date.now()-1000*60*60*24*365*3).toISOString();
  const feed = url.searchParams.get('feed') || 'iex';
  if(!symbols.length) return json({error:'symbols required'},400);
  if(!env.ALPACA_API_KEY || !env.ALPACA_SECRET_KEY) return json({error:'Alpaca secrets not configured.'},412);
  const endpoint = `https://data.alpaca.markets/v2/stocks/bars?symbols=${encodeURIComponent(symbols.join(','))}&timeframe=${encodeURIComponent(timeframe)}&start=${encodeURIComponent(start)}&adjustment=split&feed=${encodeURIComponent(feed)}&limit=10000`;
  const r = await fetch(endpoint,{headers:{'APCA-API-KEY-ID':env.ALPACA_API_KEY,'APCA-API-SECRET-KEY':env.ALPACA_SECRET_KEY}});
  if(!r.ok) return new Response(await r.text(),{status:r.status,headers:{'content-type':'application/json'}});
  const j = await r.json();
  const prices={}, quotes={};
  for(const [sym,bars] of Object.entries(j.bars||{})){
    const arr=(bars||[]).map(b=>({date:(b.t||'').slice(0,10), open:b.o, high:b.h, low:b.l, close:b.c, volume:b.v})).filter(x=>Number.isFinite(x.close));
    if(arr.length){ prices[sym]=arr; const last=arr[arr.length-1]; quotes[sym]={price:last.close, avgVolumeDollar:last.close*(last.volume||0), source:'alpaca-bars'}; }
  }
  return json({meta:{mode:'broker',provider:'alpaca-data',symbols:Object.keys(prices).length,feed,timeframe},prices,quotes});
}

async function alpacaNews(url, env){
  const symbols = (url.searchParams.get('symbols')||'SPY,QQQ,AAPL,MSFT,NVDA').split(',').map(s=>s.trim().toUpperCase()).filter(Boolean).slice(0,50);
  const limit = Math.min(Number(url.searchParams.get('limit')||40),80);
  const items=[];

  // Source 1: Alpaca/Benzinga metadata when Alpaca data keys exist.
  if(env.ALPACA_API_KEY && env.ALPACA_SECRET_KEY){
    try{
      const endpoint = `https://data.alpaca.markets/v1beta1/news?symbols=${encodeURIComponent(symbols.join(','))}&limit=${limit}&sort=desc`;
      const r = await fetch(endpoint,{headers:{'APCA-API-KEY-ID':env.ALPACA_API_KEY,'APCA-API-SECRET-KEY':env.ALPACA_SECRET_KEY}});
      if(r.ok){
        const j = await r.json();
        for(const n of (j.news||[])) items.push({
          id:'alpaca-'+n.id, headline:n.headline, source:n.source || 'Benzinga via Alpaca', created_at:n.created_at, updated_at:n.updated_at,
          symbols:n.symbols||[], summary:n.summary || '', url:n.url || '', provider:'alpaca-news'
        });
      }
    }catch(e){ console.log('Alpaca news failed', e.message); }
  }

  // Source 2+: public RSS metadata feeds. FT/WSJ content may be paywalled; Algohns stores links/metadata only.
  const feeds = [
    {name:'Financial Times', url:'https://www.ft.com/markets?format=rss'},
    {name:'Wall Street Journal', url:'https://feeds.a.dj.com/rss/RSSMarketsMain.xml'},
    {name:'Reuters', url:'https://www.reutersagency.com/feed/?best-topics=markets&post_type=best'},
    {name:'Yahoo Finance', url:'https://finance.yahoo.com/news/rssindex'}
  ];
  await Promise.all(feeds.map(async f=>{
    try{
      const r=await fetch(f.url,{headers:{'user-agent':'AlgohnsNewsRiskBot/1.0'}});
      if(!r.ok) return;
      const xml=await r.text();
      for(const n of extractRssItems(xml, f.name, symbols).slice(0, Math.ceil(limit/2))) items.push(n);
    }catch(e){ console.log('RSS failed', f.name, e.message); }
  }));

  const news = dedupeNews(items).slice(0, limit);
  return json({
    meta:{mode:'multi-source-news',provider:'Alpaca/Benzinga + public RSS metadata',sources:[...new Set(news.map(n=>n.source))],symbols:symbols.length,count:news.length,notice:'FT/WSJ may provide public metadata/links only unless licensed access is configured.'},
    news
  });
}
function extractRssItems(xml, source, symbols){
  const out=[]; const itemRe=/<item[\s\S]*?<\/item>/gi; const matches=xml.match(itemRe)||[];
  for(const item of matches){
    const get=(tag)=>{ const m=item.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`,'i')); return m?decodeXml(m[1].replace(/<!\[CDATA\[|\]\]>/g,'').trim()):''; };
    const title=get('title'), link=get('link'), desc=get('description'), date=get('pubDate')||get('dc:date')||new Date().toISOString();
    if(!title) continue;
    const text=(title+' '+desc).toUpperCase();
    const tagged=symbols.filter(sym=>new RegExp(`\\b${sym.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}\\b`).test(text));
    out.push({id:`rss-${source}-${simpleHash(title+link)}`, headline:title, source, created_at:new Date(date).toISOString(), symbols:tagged, summary:stripHtml(desc).slice(0,320), url:link, provider:'public-rss-metadata'});
  }
  return out;
}
function dedupeNews(items){
  const seen=new Set(), out=[];
  for(const n of items.sort((a,b)=>new Date(b.created_at||0)-new Date(a.created_at||0))){
    const key=(n.headline||'').toLowerCase().replace(/[^a-z0-9 ]/g,'').split(' ').filter(w=>w.length>3).slice(0,10).join(' ');
    if(!key || seen.has(key)) continue;
    seen.add(key); out.push(n);
  }
  return out;
}
function stripHtml(x=''){ return String(x).replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim(); }
function decodeXml(x=''){ return stripHtml(String(x).replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'")); }
function simpleHash(s){ let h=0; for(let i=0;i<s.length;i++) h=((h<<5)-h+s.charCodeAt(i))|0; return Math.abs(h).toString(36); }


/* =========================
   eToro Demo API adapter
   Base URL: https://public-api.etoro.com/api/v1
   Auth headers: x-api-key, x-user-key, x-request-id.
   Only DEMO execution routes are exposed from Algohns.
========================= */
function etoroConfig(env){
  const base = String(env.ETORO_BASE_URL || 'https://public-api.etoro.com/api/v1').replace(/\/+$/,'');
  return {
    base,
    hasKeys: Boolean(env.ETORO_API_KEY && env.ETORO_USER_KEY),
    apiKey: env.ETORO_API_KEY,
    userKey: env.ETORO_USER_KEY,
    demoOnly: true
  };
}
function uuid(){
  try{ return crypto.randomUUID(); }catch{ return `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
}
function etoroHeaders(env, extra={}){
  const cfg = etoroConfig(env);
  return {
    'content-type':'application/json',
    'x-api-key': cfg.apiKey || '',
    'x-user-key': cfg.userKey || '',
    'x-request-id': uuid(),
    ...extra
  };
}
async function etoroProxy(path, env, init={}){
  const cfg = etoroConfig(env);
  if(!cfg.hasKeys) return json({error:'eToro API secrets not configured. Set ETORO_API_KEY and ETORO_USER_KEY as Cloudflare secrets. Demo only.'},412);
  const endpoint = cfg.base + path;
  const r = await fetch(endpoint, {...init, headers:etoroHeaders(env, init.headers||{})});
  const txt = await r.text();
  return new Response(txt || '{}', {status:r.status, headers:{'content-type':'application/json'}});
}
async function etoroStatus(env){
  const cfg = etoroConfig(env);
  if(!cfg.hasKeys) return json({configured:false, connected:false, demoOnly:true, realMoney:'locked', reason:'Missing ETORO_API_KEY / ETORO_USER_KEY Cloudflare secrets.'});
  const r = await etoroProxy('/me', env);
  let me={}; try{ me=await r.json(); }catch{}
  return json({configured:true, connected:r.status<400, demoOnly:true, realMoney:'locked', status:r.status, identity:me, demoAccountId:me.demoAccountCustomerId || me.demoCustomerId || me.demoCID || me.DemoAccountCustomerID || null});
}
async function tryEtoro(paths, env){
  const errors=[];
  for(const path of paths){
    const r = await etoroProxy(path, env);
    let body={}; try{ body=await r.json(); }catch{}
    if(r.status<400) return {ok:true,path,status:r.status,body};
    errors.push({path,status:r.status,error:body?.error || body?.message || body});
  }
  return {ok:false, errors};
}

function extractArraysByKeys(obj, names){
  const out=[]; const seen=new WeakSet(); const keys=names.map(x=>String(x).toLowerCase());
  function walk(v){
    if(!v || typeof v !== 'object') return;
    if(seen.has(v)) return; seen.add(v);
    if(Array.isArray(v)){ out.push(v); v.slice(0,20).forEach(walk); return; }
    for(const [k,val] of Object.entries(v)){
      if(Array.isArray(val) && keys.includes(k.toLowerCase())) out.push(val);
      else if(val && typeof val === 'object') walk(val);
    }
  }
  walk(obj); return out;
}
function looksLikePosition(x){
  if(!x || typeof x !== 'object') return false;
  const keys=Object.keys(x).map(k=>k.toLowerCase());
  return keys.some(k=>['positionid','position_id','instrumentid','instrument_id','units','currentvalue','netprofit','amount','invested'].includes(k));
}
function extractEtoroPositions(body){
  const roots=[body, body?.portfolio, body?.clientPortfolio, body?.account].filter(Boolean);
  const direct=[];
  for(const r of roots){
    for(const k of ['positions','openPositions','PositionInfo','clientPositions','mirrorPositions']) if(Array.isArray(r?.[k])) direct.push(...r[k]);
  }
  const deep=extractArraysByKeys(body, ['positions','openPositions','PositionInfo','clientPositions','mirrorPositions']).flat();
  const uniq=new Map();
  [...direct, ...deep].filter(looksLikePosition).forEach(x=>{
    const id=x.positionId ?? x.PositionID ?? x.PositionId ?? x.id ?? `${x.instrumentId||x.InstrumentID||x.symbol||x.Symbol}:${x.units||x.Units||x.amount||x.Amount}`;
    uniq.set(String(id), x);
  });
  return [...uniq.values()];
}
function extractEtoroOrders(body){
  const roots=[body, body?.portfolio, body?.clientPortfolio, body?.account].filter(Boolean);
  const direct=[];
  for(const r of roots){ for(const k of ['orders','openOrders','pendingOrders','OrderInfo']) if(Array.isArray(r?.[k])) direct.push(...r[k]); }
  return [...direct, ...extractArraysByKeys(body, ['orders','openOrders','pendingOrders','OrderInfo']).flat()].filter(x=>x && typeof x === 'object');
}
async function etoroPortfolio(env){
  // Resolve across the documented demo P&L endpoint and portfolio variants. Never falls back to fake positions.
  const res = await tryEtoro([
    '/trading/info/demo/pnl',
    '/trading/info/demo/portfolio',
    '/trading/info/demo/portfolio-details',
    '/trading/info/demo/account'
  ], env);
  if(!res.ok) return json({configured:etoroConfig(env).hasKeys, connected:false, positions:[], orders:[], portfolio:null, errors:res.errors, note:'eToro Demo portfolio endpoint not resolved for this account/API version. Authentication can still be checked with /api/broker/etoro/status.'},503);
  const b=res.body;
  const positions = extractEtoroPositions(b);
  const orders = extractEtoroOrders(b);
  return json({configured:true, connected:true, sourcePath:res.path, portfolio:b, positions, orders, counts:{positions:positions.length, orders:orders.length}});
}

async function etoroOrders(request, env){
  if(request.method==='GET'){
    const res = await tryEtoro(['/trading/info/demo/orders','/trading/info/demo/pnl','/trading/info/demo/portfolio'], env);
    if(!res.ok) return json({orders:[], errors:res.errors},503);
    const b=res.body; return json({orders:extractEtoroOrders(b), raw:b, sourcePath:res.path});
  }
  return json({error:'Use /api/broker/etoro/batch-orders for demo order submission.'},405);
}
async function etoroSearch(url, env){
  const q = (url.searchParams.get('q') || url.searchParams.get('query') || '').trim();
  if(!q) return json({query:q, instruments:[]});
  const candidates = [
    `/instruments/search?query=${encodeURIComponent(q)}`,
    `/instruments/search?q=${encodeURIComponent(q)}`,
    `/instruments?search=${encodeURIComponent(q)}`,
    `/instruments?query=${encodeURIComponent(q)}`
  ];
  const res = await tryEtoro(candidates, env);
  if(!res.ok) return json({query:q, instruments:[], errors:res.errors, note:'eToro instrument search path was not accepted by this API version/account.'},503);
  const b=res.body;
  const rows = Array.isArray(b) ? b : Array.isArray(b.instruments) ? b.instruments : Array.isArray(b.items) ? b.items : Array.isArray(b.results) ? b.results : [];
  return json({query:q, sourcePath:res.path, instruments:rows.map(normalizeEtoroInstrument)});
}
async function etoroAssets(url, env){
  // Conservative starter: search liquid symbols already used by Algohns and return verified rows where the API responds.
  const symbols=(url.searchParams.get('symbols') || 'SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL,AMD,PLTR,GLD,TLT').split(',').map(x=>x.trim()).filter(Boolean).slice(0,80);
  const out=[]; const errors=[];
  for(const s of symbols){
    try{
      const u = new URL('https://x/'); u.searchParams.set('q',s);
      const res = await etoroSearch(u, env); const j=await res.json();
      if(j.instruments?.length) out.push({...j.instruments[0], symbol:s, broker:'eToro', source:'etoro_search'});
      else errors.push({symbol:s,error:j.note||'not found'});
    }catch(e){ errors.push({symbol:s,error:e.message}); }
  }
  return json({assets:out, errors, note:'eToro universe is built from verified instrument search/cache. Algohns does not fake a full eToro universe.'});
}
function normalizeEtoroInstrument(x){
  return {
    instrumentId: x.instrumentID || x.InstrumentID || x.instrumentId || x.id,
    symbol: String(x.symbol || x.Symbol || x.ticker || x.Ticker || x.name || '').toUpperCase(),
    name: x.displayName || x.DisplayName || x.name || x.Name || x.symbol || x.Symbol || '',
    assetClass: x.assetClass || x.AssetClass || x.instrumentType || x.InstrumentType || 'eToro instrument',
    exchange: x.exchange || x.Exchange || '',
    raw:x
  };
}
async function etoroBatchOrders(request, env){
  if(request.method!=='POST') return json({error:'Method not allowed'},405);
  const body = await request.json();
  if(!Array.isArray(body.orders) || body.orders.length>25) return json({error:'orders must be an array with max 25 items'},400);
  const results=[];
  for(const o of body.orders){
    if(String(o.side||'').toLowerCase()==='sell' && o.positionId){
      const payload = {InstrumentID:Number(o.instrumentId), UnitsToDeduct:o.unitsToDeduct ?? o.units ?? null};
      const r=await etoroProxy(`/trading/execution/demo/market-close-orders/positions/${encodeURIComponent(o.positionId)}`, env, {method:'POST',body:JSON.stringify(payload)});
      let j={}; try{j=await r.json();}catch{}
      results.push({symbol:o.symbol,side:o.side,status:r.status,payload:j});
      continue;
    }
    const instrumentId = Number(o.instrumentId || o.InstrumentID);
    if(!instrumentId){ results.push({symbol:o.symbol,side:o.side,status:400,error:'Missing eToro instrumentId. Resolve the ticker in Universe Explorer first.'}); continue; }
    const payload = {InstrumentID:instrumentId, IsBuy:String(o.side||'buy').toLowerCase()==='buy', Leverage:Number(o.leverage||1), Amount:Number(o.amount || o.notional || 0), IsNoStopLoss:true, IsNoTakeProfit:true};
    if(!payload.Amount || payload.Amount<1){ results.push({symbol:o.symbol,side:o.side,status:400,error:'Missing/too-small amount'}); continue; }
    const r=await etoroProxy('/trading/execution/demo/market-open-orders/by-amount', env, {method:'POST',body:JSON.stringify(payload)});
    let j={}; try{j=await r.json();}catch{}
    results.push({symbol:o.symbol,side:o.side,status:r.status,payload:j});
  }
  return json({mode:'etoro-demo-batch',submitted:results.filter(x=>x.status>=200 && x.status<400).length,results,realMoney:'locked'});
}
async function etoroClosePosition(request, env){
  if(request.method!=='POST') return json({error:'Method not allowed'},405);
  const b=await request.json();
  if(!b.positionId || !b.instrumentId) return json({error:'positionId and instrumentId required'},400);
  return etoroProxy(`/trading/execution/demo/market-close-orders/positions/${encodeURIComponent(b.positionId)}`, env, {method:'POST',body:JSON.stringify({InstrumentID:Number(b.instrumentId),UnitsToDeduct:b.unitsToDeduct ?? null})});
}


async function alpacaBatchOrders(request, env){
  if(request.method !== 'POST') return json({error:'Method not allowed'},405);
  const body = await request.json();
  if(!Array.isArray(body.orders) || body.orders.length > 25) return json({error:'orders must be an array with max 25 items.'},400);
  const results=[];
  const cfg = alpacaConfig(env);
  if(cfg.isBrokerSandbox) return json({error:'Wrong ALPACA_BASE_URL: you used Broker API sandbox. Set it to https://paper-api.alpaca.markets'},400);
  if(!cfg.isPaper) return json({error:'Batch execution is locked to Alpaca Paper only. Set ALPACA_BASE_URL=https://paper-api.alpaca.markets'},403);
  for(const order of body.orders){
    const clean={...order};
    const type = clean.type || 'market';
    const orderPayload = {symbol:String(clean.symbol||'').toUpperCase(), side:String(clean.side||'').toLowerCase(), type, time_in_force:clean.time_in_force||'day'};
    if(!orderPayload.symbol || !['buy','sell'].includes(orderPayload.side)){ results.push({symbol:clean.symbol,status:400,error:'Missing symbol or invalid side'}); continue; }
    if(clean.qty) orderPayload.qty = String(clean.qty);
    if(clean.notional && !clean.qty) orderPayload.notional = String(clean.notional);
    if(orderPayload.type === 'limit'){
      if(!clean.limit_price){ results.push({symbol:clean.symbol,side:clean.side,status:400,error:'Limit order requires limit_price'}); continue; }
      orderPayload.limit_price = String(clean.limit_price);
    }
    if(!orderPayload.qty && !orderPayload.notional){ results.push({symbol:clean.symbol,side:clean.side,status:400,error:'Order requires qty or notional'}); continue; }
    if(clean.extended_hours) orderPayload.extended_hours = true;
    const res = await alpacaProxy('/v2/orders', env, {method:'POST', body:JSON.stringify(orderPayload)});
    let payload; try{ payload=await res.json(); } catch{ payload={status:res.status}; }
    results.push({symbol:clean.symbol,side:clean.side,status:res.status,requestId:res.headers.get('x-request-id'),payload});
  }
  return json({mode:'alpaca-paper-batch',submitted:results.filter(x=>x.status>=200 && x.status<300).length,results,realMoney:'locked'});
}

async function alpacaProxy(path, env, init={}){
  const cfg = alpacaConfig(env);
  const base = cfg.base;
  if(!env.ALPACA_API_KEY || !env.ALPACA_SECRET_KEY) return json({error:'Alpaca secrets not configured. Use internal paper trading or set Cloudflare secrets.'},412);
  if(cfg.isBrokerSandbox) return json({error:'Wrong ALPACA_BASE_URL: Broker API sandbox is not the Trading API. Set ALPACA_BASE_URL to https://paper-api.alpaca.markets'},400);
  if(!cfg.isPaper && !cfg.liveUnlocked) return json({error:'Live Alpaca base URL blocked. Use https://paper-api.alpaca.markets or explicit server-side unlock.'},403);
  const r = await fetch(base + path, { ...init, headers:{'APCA-API-KEY-ID':env.ALPACA_API_KEY,'APCA-API-SECRET-KEY':env.ALPACA_SECRET_KEY,'content-type':'application/json',...(init.headers||{})} });
  return new Response(await r.text(), {status:r.status, headers:{'content-type':'application/json'}});
}

async function alpacaCancelOpenOrders(env){
  const r = await alpacaProxy('/v2/orders', env, {method:'DELETE'});
  if(r.status >= 400) return r;
  let body=[]; try{ body=await r.json(); }catch{}
  return json({ok:true, cancelled:body, note:'Alpaca paper open-order cancellation requested.'});
}

async function alpacaOrders(request, env){
  if(request.method==='GET') return alpacaProxy('/v2/orders?status=all&limit=50', env);
  if(request.method==='POST'){
    const cfg = alpacaConfig(env);
    if(!cfg.isPaper) return json({error:'Direct execution is locked to Alpaca Paper only. Set ALPACA_BASE_URL=https://paper-api.alpaca.markets'},403);
    const body = await request.json();
    const allowed=['symbol','qty','side','type','time_in_force','limit_price','notional'];
    const clean={}; for(const k of allowed) if(body[k]!==undefined) clean[k]=body[k];
    clean.symbol=String(clean.symbol||'').toUpperCase();
    clean.side=String(clean.side||'').toLowerCase();
    clean.type = clean.type || 'market'; clean.time_in_force = clean.time_in_force || 'day';
    if(!clean.symbol || !['buy','sell'].includes(clean.side)) return json({error:'symbol and side are required'},400);
    if(clean.type==='limit' && !clean.limit_price) return json({error:'limit_price required for limit orders'},400);
    if(!clean.qty && !clean.notional) return json({error:'qty or notional required'},400);
    return alpacaProxy('/v2/orders', env, {method:'POST', body:JSON.stringify(clean)});
  }
  return json({error:'Method not allowed'},405);
}


function demoPrices(symbols){
  const prices={}, quotes={};
  for(const sym of symbols){
    const seed = [...sym].reduce((a,c)=>a+c.charCodeAt(0),0); let px=40 + (seed%250);
    const arr=[]; const today=new Date();
    for(let i=520;i>=0;i--){
      const d=new Date(today); d.setDate(today.getDate()-i); if(d.getDay()===0||d.getDay()===6) continue;
      const trend = Math.sin((520-i+seed)/80)*0.002 + ((seed%7)-3)*0.00008;
      const shock = Math.sin((520-i+seed)/9)*0.006;
      px = Math.max(5, px*(1+trend+shock));
      arr.push({date:d.toISOString().slice(0,10), open:px*.995, high:px*1.01, low:px*.99, close:px, volume:1000000+(seed%100)*25000});
    }
    prices[sym]=arr; quotes[sym]={price:arr[arr.length-1].close, avgVolumeDollar:arr[arr.length-1].close*1500000, source:'demo'};
  }
  return {prices, quotes};
}
function demoFundamental(sym){
  const seed = [...sym].reduce((a,c)=>a+c.charCodeAt(0),0); const sectors=['Technology','Financial Services','Healthcare','Consumer Defensive','Energy','Industrials','Communication Services'];
  return { sector: sectors[seed%sectors.length], pe: 10+(seed%40), evToEbitda: 6+(seed%25), roe:.08+(seed%25)/100, roic:.05+(seed%18)/100, debtToEquity:.2+(seed%15)/10, grossMargin:.25+(seed%45)/100, epsGrowth:-.05+(seed%30)/100, source:'demo' };
}
