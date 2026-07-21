const $ = (id) => document.getElementById(id);
const state = { timer: null, lastSettle: 0, valueRule: { edgeMin: 0.05, confMin: 0.60, minOdds: 1.25, maxOdds: 3.25, maxOverround: 1.10, minDataDepth: 50 } };
function log(message, obj) { console.info('[TennisForest]', message, obj || ''); }
function pct(n) { return n == null ? '—' : `${Math.round(Number(n) * 100)}%`; }
function eur(n) { if (n == null || Number.isNaN(Number(n))) return '—'; const x = Number(n); return `${x >= 0 ? '+' : ''}${x.toFixed(2)}€`; }
function odds(n) { return Number(n) > 1 ? Number(n).toFixed(2) : '—'; }
function safe(text) { return String(text ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function setText(id, value) { const el = $(id); if (el) el.textContent = value; }
function shortErr(x) { return String(x || '').replace(/Firestore set predictions\/[^ ]+/g, 'Firestore set predictions/...').slice(0, 120) || '—'; }
function renderModel(data) {
  if (!data.available) {
    $('modelStatus').textContent = 'model.json assente · BET disattivato';
    $('modelType').textContent = 'Fallback Elo · no betting';
    $('modelTrainRows').textContent = '—';
    $('modelAccuracy').textContent = '—';
    $('modelLogLoss').textContent = '—';
    $('modelPlayers').textContent = '—';
    $('modelDate').textContent = '—';
    return;
  }
  $('modelStatus').textContent = 'modello attivo · BET solo con value';
  $('modelType').textContent = data.version || 'GreenCode v22';
  $('modelTrainRows').textContent = data.trainingRows ?? '—';
  $('modelAccuracy').textContent = data.metrics?.test_accuracy == null ? '—' : pct(data.metrics.test_accuracy);
  $('modelLogLoss').textContent = data.metrics?.test_log_loss == null ? '—' : Number(data.metrics.test_log_loss).toFixed(3);
  $('modelPlayers').textContent = data.players ?? '—';
  $('modelDate').textContent = data.trainedThrough || '—';
  renderDiagnosis(data);
}

function renderDiagnosis(data) {
  const warnings = data?.diagnostics?.warnings || [];
  const el = $('diagnosisList');
  if (!el) return;
  if (!warnings.length) {
    setText('diagnosisStatus', 'nessun warning critico');
    el.innerHTML = `<div class="diag okdiag"><strong>Modello senza warning principali</strong><span>Continua comunque a giudicarlo su previsioni live salvate e settled.</span></div>`;
    return;
  }
  setText('diagnosisStatus', `${warnings.length} warning`);
  el.innerHTML = warnings.map(w => `<div class="diag ${safe(w.level)}"><strong>${safe(w.code)}</strong><span>${safe(w.message)}</span></div>`).join('');
}

async function refreshModel() {
  const res = await fetch('/api/model', { cache: 'no-store' });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'Model API error');
  renderModel(data);
}



function computeBetSignal(p) {
  const r = state.valueRule || { edgeMin:0.05, confMin:0.60, minOdds:1.25, maxOdds:3.25, maxOverround:1.10 };
  const hasOdds = Number(p.oddsPick) >= Number(r.minOdds || 1.25) && Number(p.oddsPick) <= Number(r.maxOdds || 3.5);
  const oddsClean = p.overround == null || Number(p.overround) <= Number(r.maxOverround || 1.10);
  const enoughEdge = Number(p.edge) >= Number(r.edgeMin || 0.05);
  const enoughConfidence = Number(p.confidence) >= Number(r.confMin || 0.60);
  const qualityOk = ['high','medium'].includes(String(p.dataQuality || '')) && Number(p.dataDepthScore || p.features?.dataDepthScore || 0) >= Number(r.minDataDepth || 50) && p.features?.p1InModel === true && p.features?.p2InModel === true;
  const trainedModel = String(p.modelType || '').includes('greencode-offline-model-json');
  const positiveEV = Number(p.expectedProfit100) > 0;
  const bet = trainedModel && hasOdds && oddsClean && enoughEdge && enoughConfidence && qualityOk && positiveEV;
  let reason = 'BET · stake 100€';
  if (!trainedModel) reason = 'NO BET · modello non allenato';
  else if (!qualityOk) reason = 'NO BET · data depth insufficiente';
  else if (!hasOdds) reason = 'NO BET · quota fuori range';
  else if (!oddsClean) reason = 'NO BET · margine book alto';
  else if (!positiveEV) reason = 'NO BET · EV non positivo';
  else if (!enoughEdge) reason = 'NO BET · edge basso';
  else if (!enoughConfidence) reason = 'NO BET · confidence bassa';
  return { bet, reason, stake: bet ? 100 : 0 };
}

function renderRecommendations(predictions) {
  const recs = (predictions || []).map(p => ({...p, signal: computeBetSignal(p)})).filter(p => p.signal.bet).sort((a,b) => Number(b.edge || 0) - Number(a.edge || 0));
  $('betRule').textContent = `edge ≥ ${Math.round((state.valueRule.edgeMin || 0.05)*100)}%, conf ≥ ${Math.round((state.valueRule.confMin || 0.60)*100)}%, data depth ≥ ${state.valueRule.minDataDepth || 50}`;
  const el = $('recommendations');
  if (!recs.length) {
    el.innerHTML = `<div class="rec empty"><strong>Nessuna puntata consigliata ora</strong><span>Meglio non forzare: quote assenti, margine basso o confidence non abbastanza pulita.</span></div>`;
    return;
  }
  el.innerHTML = recs.slice(0, 8).map(p => `<div class="rec bet">
    <strong>${safe(p.predictedWinner)}</strong>
    <span>${safe(p.player1)} vs ${safe(p.player2)}</span>
    <small>${safe(p.tournament)} · quota ${odds(p.oddsPick)} · edge ${p.edge == null ? '—' : Math.round(Number(p.edge)*100) + '%'} · depth ${p.dataDepthScore ?? '—'}/100 · EV ${p.expectedProfit100 == null ? '—' : eur(p.expectedProfit100)} · stake 100€</small>
  </div>`).join('');
}

function renderOddsPanel(data) {
  const o = data.odds || {};
  const total = Number(o.totalMatches || (data.predictions || []).length || 0);
  const withOdds = Number(o.withOdds || 0);
  const status = o.status || (o.ok ? 'ok' : 'not-available');
  const source = withOdds > 0 ? 'API-Tennis get_odds' : (o.ok ? 'API chiamata, quote assenti' : 'Quote non disponibili');
  $('oddsWith').textContent = String(withOdds);
  $('oddsTotal').textContent = String(total);
  $('oddsSource').textContent = source;
  $('oddsState').textContent = status;
  $('oddsStatus').textContent = `quote: ${withOdds}/${total}`;
  $('oddsExplanation').textContent = withOdds > 0
    ? 'Quote attive: il P/L viene calcolato simulando 100€ su ogni pick con quota disponibile.'
    : 'Quote non disponibili per queste fixtures. Il sito resta real-only: non inserisce quote finte.';
}

function renderMatches(predictions) {
  const el = $('matches');
  if (!predictions.length) {
    el.innerHTML = `<div class="match empty"><span class="tag">REAL ONLY</span><h3>Nessuna partita reale trovata</h3><p>Demo rimossa. Allarga FUTURE_DAYS o controlla che API-Tennis stia restituendo fixtures ATP main-tour.</p></div>`;
    return;
  }
  el.innerHTML = predictions.map(p => {
    const p1width = Math.round(p.p1Prob * 100);
    const hasOdds = !!p.oddsPick;
    const valueClass = p.edge > 0 ? 'value-good' : 'value-bad';
    const signal = computeBetSignal(p);
    return `<article class="match ${signal.bet ? 'bet-match' : ''}">
      <span class="tag">${safe(p.level)} · ${safe(p.surface)} · ${safe(p.date)} ${safe(p.time)}</span>
      <div class="players"><div>${safe(p.player1)} <small>${Math.round(p.p1Prob*100)}%</small></div><div class="vs">VS</div><div>${safe(p.player2)} <small>${Math.round(p.p2Prob*100)}%</small></div></div>
      <div class="pick">Pick: <strong>${safe(p.predictedWinner)}</strong><br/><span class="muted">Confidence ${pct(p.confidence)} · Data depth ${p.dataDepthScore ?? '—'}/100 · ${safe(p.tournament)}</span><div class="${signal.bet ? 'bet-badge' : 'nobet-badge'}">${signal.reason}</div></div>
      <div class="oddsbox"><span>Quota 1: <b>${odds(p.oddsHome)}</b></span><span>Quota 2: <b>${odds(p.oddsAway)}</b></span><span>Pick odds: <b>${odds(p.oddsPick)}</b></span><span class="${hasOdds ? valueClass : ''}">EV 100€: <b>${p.expectedProfit100 == null ? '—' : eur(p.expectedProfit100)}</b></span></div>
      <div class="probbar"><div style="width:${p1width}%"></div></div>
    </article>`;
  }).join('');
}

function renderResultChartInto(id, stats) {
  const correct = Number(stats.correct || 0);
  const wrong = Number(stats.wrong || 0);
  const total = correct + wrong;
  const c = total ? Math.round(correct / total * 100) : 0;
  const w = total ? 100 - c : 0;
  $(id).innerHTML = `<div class="stacked"><div class="good" style="width:${c}%"></div><div class="bad" style="width:${w}%"></div></div><div class="legend"><span><i class="good-dot"></i> Prese: ${correct}</span><span><i class="bad-dot"></i> Sbagliate: ${wrong}</span></div>`;
}
function renderResultChart(stats) { renderResultChartInto('resultChart', stats); }

function drawBankroll(canvasId, rows) {
  const canvas = $(canvasId);
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(600, Math.floor(rect.width * dpr));
  canvas.height = Math.floor((canvasId === 'btBankrollChart' ? 190 : 210) * dpr);
  ctx.scale(dpr, dpr);
  const w = canvas.width / dpr, h = canvas.height / dpr;
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle = 'rgba(245, 214, 71, .18)'; ctx.lineWidth = 1;
  for (let i=0;i<5;i++){ const y = 18 + i*(h-40)/4; ctx.beginPath(); ctx.moveTo(8,y); ctx.lineTo(w-8,y); ctx.stroke(); }
  if (!rows || !rows.length) { ctx.fillStyle = '#9f9b85'; ctx.font = '14px system-ui'; ctx.fillText('Nessun risultato con quote.', 14, h/2); return; }
  const vals = rows.map(r => Number(r.cumulative || 0));
  const min = Math.min(0, ...vals), max = Math.max(0, ...vals);
  const span = max - min || 1;
  const x = i => 12 + i * (w-24) / Math.max(1, rows.length - 1);
  const y = v => 18 + (max - v) * (h-40) / span;
  ctx.beginPath();
  rows.forEach((r,i)=>{ const px=x(i), py=y(Number(r.cumulative||0)); if(i===0) ctx.moveTo(px,py); else ctx.lineTo(px,py); });
  ctx.strokeStyle = '#f5d647'; ctx.lineWidth = 3; ctx.stroke();
  ctx.fillStyle = '#f5d647'; ctx.font = '13px system-ui'; ctx.fillText(`P/L finale: ${eur(vals[vals.length-1])}`, 14, 20);
}

function renderStats(stats) {
  $('totalPredictions').textContent = stats.totalPredictions ?? '—';
  $('pendingPredictions').textContent = stats.pending ?? '—';
  $('record').textContent = `${stats.correct ?? 0}/${stats.wrong ?? 0}`;
  $('accuracy').textContent = stats.accuracy == null ? '—' : pct(stats.accuracy);
  $('profit').textContent = eur(stats.profit);
  $('withOdds').textContent = stats.withOdds ?? '—';
  $('totalStake').textContent = stats.totalStake == null ? '—' : `${Number(stats.totalStake).toFixed(0)}€`;
  $('roi').textContent = stats.roi == null ? '—' : pct(stats.roi);
  renderResultChart(stats);
  drawBankroll('bankrollChart', stats.bankroll || []);
  $('calibration').innerHTML = (stats.calibration || []).map(b => { const acc = b.accuracy == null ? 0 : Math.round(b.accuracy * 100); return `<div class="bucket"><span>${b.label}</span><div class="bucketbar"><div style="width:${acc}%"></div></div><span>${b.total ? acc + '%' : 'n/a'}</span></div>`; }).join('');
  $('historyBody').innerHTML = (stats.latest || []).map(p => {
    const status = p.status === 'settled' ? 'settled' : 'pending';
    const conf = p.confidence || p.currentConfidence;
    const pick = p.predictedWinner || p.currentPredictedWinner;
    const result = p.status === 'settled' ? `<span class="${p.correct ? 'ok' : 'wrong'}">${p.correct ? 'presa' : 'sbagliata'} · ${safe(p.actualWinner)}</span>` : 'in attesa';
    const pl = p.status === 'settled' ? eur(p.profit100) : '—';
    return `<tr><td>${safe(p.date || '')}</td><td>${safe(p.tournament || '')}</td><td>${safe(p.player1 || '')} vs ${safe(p.player2 || '')}</td><td><strong>${safe(pick || '')}</strong></td><td>${pct(conf)}</td><td>${odds(p.oddsPick)}</td><td>${p.expectedProfit100 == null ? '—' : eur(p.expectedProfit100)}</td><td><span class="status-${status}">${status}</span></td><td>${result}</td><td>${pl}</td></tr>`;
  }).join('');
}


function renderValidationFromBacktest(data) {
  const s = data.stats || {};
  const d = s.diagnostics || {};
  $('validationVerdict').textContent = !data.dataQuality?.modelActive ? 'NON VALIDATO · model.json non attivo'
    : d.oddsInversionSuspected ? 'ATTENZIONE · possibili quote invertite'
    : (s.optimizedStrategy?.roi > 0 ? 'VALIDO · value strategy positiva' : 'NON PROFITTEVOLE · serve calibrazione');
  $('valModelRoi').textContent = s.roi == null ? 'N/D' : pct(s.roi);
  $('valBookRoi').textContent = s.bookmakerBaseline?.roi == null ? 'N/D' : pct(s.bookmakerBaseline.roi);
  $('valValueRoi').textContent = s.valueStrategy?.roi == null ? 'N/D' : pct(s.valueStrategy.roi);
  $('valAutoRoi').textContent = s.optimizedStrategy?.roi == null ? 'N/D' : pct(s.optimizedStrategy.roi);
  $('valInvRoi').textContent = s.invertedOddsCheck?.roi == null ? 'N/D' : pct(s.invertedOddsCheck.roi);
  $('valOddsMap').textContent = d.oddsInversionSuspected ? 'CHECK' : 'OK';
}

function renderBacktest(data) {
  const s = data.stats || {};
  renderValidationFromBacktest(data);
  $('backtestRange').textContent = data.range ? `${data.range.start} → ${data.range.stop} · ${data.filterMode || 'real'}` : 'ultimi 20 giorni';
  $('btTotal').textContent = s.total ?? '—';
  $('btRecord').textContent = `${s.correct ?? 0}/${s.wrong ?? 0}`;
  $('btAccuracy').textContent = s.accuracy == null ? '—' : pct(s.accuracy);
  $('btRoi').textContent = !s.totalStake ? 'N/D' : pct(s.roi);
  $('btFilter').textContent = data.filterMode || 'strict';
  $('btOdds').textContent = `${s.withOdds || 0}/${s.total || 0}`;
  $('btQuality').textContent = data.dataQuality?.note || 'Backtest strict: niente fallback automatico e niente partite demo.';
  const vs = s.valueStrategy || {};
  $('btValueBets').textContent = vs.bets ?? '—';
  $('btValueRecord').textContent = `${vs.correct ?? 0}/${vs.wrong ?? 0}`;
  $('btValueRoi').textContent = vs.roi == null ? 'N/D' : pct(vs.roi);
  $('btValueProfit').textContent = vs.profit == null ? 'N/D' : eur(vs.profit);
  const os = s.optimizedStrategy || {};
  if (os.edgeMin != null && os.confMin != null && Number(os.bets || 0) > 0) {
    state.valueRule = { edgeMin: Number(os.edgeMin), confMin: Number(os.confMin), minOdds: Number(os.minOdds || 1.25), maxOdds: Number(os.maxOdds || 3.25), maxOverround: Number(os.maxOverround || 1.10) };
  }
  $('btAutoBets').textContent = os.bets ?? '—';
  $('btAutoRoi').textContent = os.roi == null ? 'N/D' : pct(os.roi);
  $('btAutoProfit').textContent = os.profit == null ? 'N/D' : eur(os.profit);
  $('btAutoRule').textContent = os.edgeMin == null ? '—' : `E${Math.round(os.edgeMin*100)} C${Math.round(os.confMin*100)}`;
  renderResultChartInto('btResultChart', s);
  drawBankroll('btBankrollChart', s.bankroll || []);
  const rows = data.rows || [];
  if (!rows.length) {
    $('backtestBody').innerHTML = `<tr><td colspan="10">Nessuna partita conclusa trovata nel range. API raw: ${safe(data.api?.countRaw ?? 0)}, ATP main-tour strict: ${safe(data.api?.filteredAtp250Plus ?? 0)}, ATP singles disponibili: ${safe(data.api?.looseAtpSingles ?? 0)}. Nessun risultato pulito nel periodo selezionato. Il dato resta real-only: niente demo e niente fallback automatico.</td></tr>`;
    return;
  }
  $('backtestBody').innerHTML = rows.map(p => {
    const pl = p.profit100 == null ? '—' : eur(p.profit100);
    return `<tr><td>${safe(p.date)}</td><td>${safe(p.tournament)}</td><td>${safe(p.player1)} vs ${safe(p.player2)}</td><td><strong>${safe(p.predictedWinner)}</strong></td><td>${safe(p.bookmakerFavorite || '—')}</td><td><span class="${p.correct ? 'ok' : 'wrong'}">${safe(p.actualWinner)}</span></td><td>${pct(p.confidence)}</td><td>${p.edge == null ? '—' : Math.round(Number(p.edge)*100) + '%'}</td><td>${odds(p.oddsPick)}</td><td>${pl}</td></tr>`;
  }).join('');
}


function renderTrackingSummary(tracking, stats) {
  tracking = tracking || {};
  setText('trackingSaved', tracking.saved ?? '—');
  setText('trackingUpdated', tracking.updated ?? '—');
  setText('trackingErrors', tracking.errors ?? '—');
  setText('trackingTotal', stats?.totalPredictions ?? '—');
  setText('trackingBets', stats?.betCount ?? stats?.bets ?? '—');
  setText('trackingLastError', shortErr(tracking.lastError));
  const errors = Number(tracking.errors || 0);
  setText('trackingStatus', errors ? 'tracking con errori Firebase' : 'tracking attivo');
}

async function trackingCheck() {
  $('statusPill').textContent = 'Test salvataggio Firebase...';
  const res = await fetch('/api/tracking-check?debug=1', { cache: 'no-store' });
  const data = await res.json();
  if (!data.ok) throw new Error(data.detail || data.error || 'Tracking check failed');
  $('statusPill').textContent = 'Salvataggio Firebase operativo';
  setText('trackingStatus', 'Firebase scrivibile');
}

async function refreshFuture() {
  $('statusPill').textContent = 'Aggiorno fixtures reali...';
  const res = await fetch('/api/future', { cache: 'no-store' });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'Future API error');
  renderOddsPanel(data);
  renderMatches(data.predictions || []);
  renderRecommendations(data.predictions || []);
  renderTrackingSummary(data.tracking || {}, null);
  $('lastUpdate').textContent = `Ultimo refresh ${new Date(data.generatedAt).toLocaleString()} · ${data.mode} · ${data.model?.type || 'model'} · quote ${data.odds?.withOdds || 0}`;
  const tr = data.tracking || {};
  const saveMsg = Number(tr.errors || 0) ? ` · SAVE ERR ${tr.errors}` : ` · salvate ${tr.saved || 0}/aggiornate ${tr.updated || 0}`;
  $('statusPill').textContent = data.mode === 'api-tennis' ? `Dati reali: ${data.predictions?.length || 0} match · ${data.model?.type || 'model'} · quote ${data.odds?.withOdds || 0}${saveMsg}` : 'Nessun dato reale';
  log(`Future predictions`, { mode: data.mode, count: data.predictions?.length || 0, tracking: data.tracking });
}
async function refreshStats() { const res = await fetch('/api/stats', { cache: 'no-store' }); const data = await res.json(); if (!data.ok) throw new Error(data.error || 'Stats API error'); renderStats(data); renderTrackingSummary(null, data); return data; }
async function settle({ silent = false } = {}) {
  if (!silent) $('statusPill').textContent = 'Aggiorno risultati reali...';
  let data = null;
  try {
    const res = await fetch('/api/settle', { cache: 'no-store' });
    const text = await res.text();
    try { data = JSON.parse(text); } catch { data = { ok:false, error:text.slice(0,220) }; }
    if (!res.ok || !data.ok) throw new Error(data.error || data.warning || `Settle HTTP ${res.status}`);
    await refreshStats();
    if (!silent) $('statusPill').textContent = `Risultati reali aggiornati: ${data.updated || 0}`;
    if (data.warning) log('Settlement warning', data);
    return data;
  } catch (err) {
    const msg = err?.message || String(err);
    log('Errore aggiornamento risultati reali', { error: msg, response: data });
    if (!silent) $('statusPill').textContent = `Risultati non aggiornati: ${msg}`;
    return { ok:false, error:msg };
  }
}
async function backtest() {
  $('statusPill').textContent = 'Eseguo backtest ultimi 20 giorni...';
  const res = await fetch('/api/backtest?days=20', { cache: 'no-store' });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'Backtest API error');
  renderBacktest(data);
  $('statusPill').textContent = `Backtest completato: ${data.stats?.total || 0} match`;
}
async function cleanupDemo() {
  $('statusPill').textContent = 'Pulisco demo vecchie...';
  const res = await fetch('/api/cleanup-demo', { cache: 'no-store' });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'Cleanup API error');
  $('statusPill').textContent = `Pulizia completata: ${data.deleted || 0} righe rimosse`;
  await refreshStats();
}

async function tick() {
  try {
    await refreshFuture();
    if (Date.now() - state.lastSettle > 5 * 60 * 1000) {
      state.lastSettle = Date.now();
      await settle({ silent:true });
    }
    await refreshStats();
  } catch (err) {
    $('statusPill').textContent = 'Errore connessione partite/quote';
    log(err.message || String(err));
  }
}
$('refreshBtn').addEventListener('click', tick);
$('settleBtn').addEventListener('click', () => settle({ silent:false }));
const backtestBtn = $('backtestBtn'); if (backtestBtn) backtestBtn.addEventListener('click', backtest);
$('trackingCheckBtn').addEventListener('click', () => trackingCheck().catch(err => { $('statusPill').textContent = 'Tracking non scrivibile'; setText('trackingStatus', 'errore salvataggio'); setText('trackingLastError', shortErr(err.message)); log(err.message || String(err)); }));
$('cleanupBtn').addEventListener('click', cleanupDemo);
window.addEventListener('resize', () => { refreshStats().catch(()=>{}); });
refreshModel().catch(() => {});
tick();
// Backtest non parte più in automatico: il prodotto ora usa tracking reale salvato.
state.timer = setInterval(tick, 30000);
