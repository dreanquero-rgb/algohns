"""Module 3 UI — Walk-forward backtesting and portfolio optimisation."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.backtest_suite import (  # noqa: E402
    REBALANCE_RULES,
    BacktestConfig,
    compare_objectives,
    run_backtest,
)
from modules.market_data import SP500_SAMPLE, load_prices  # noqa: E402
from modules.optimizers import Objective, covariance, expected_returns, optimize  # noqa: E402

st.set_page_config(page_title="Backtest Suite", page_icon="◆", layout="wide")
st.title("Module 3 — Advanced Backtesting & Portfolio Optimization")
st.caption(
    "Motore walk-forward strettamente causale: a ogni ribilanciamento "
    "l'ottimizzatore vede solo i prezzi fino a quella data."
)


@st.cache_data(ttl=3600, show_spinner="Carico i prezzi…")
def _prices(tickers: tuple[str, ...], start: date, end: date, synthetic: bool):
    res = load_prices(list(tickers), start, end, synthetic=synthetic)
    return res.prices, res.source, res.warnings


ctx = st.session_state.get("ctx", {})
with st.sidebar:
    st.subheader("Parametri backtest")
    tickers = st.multiselect(
        "Universo", list(SP500_SAMPLE),
        default=list(ctx.get("tickers") or list(SP500_SAMPLE)[:12]),
    )
    years = st.slider("Anni di storico", 2, 12, 6)
    synthetic = st.toggle("Dati sintetici", value=bool(ctx.get("synthetic", False)))
    objective = st.selectbox("Obiettivo", list(Objective),
                             format_func=lambda o: o.value.replace("_", " ").title())
    rebalance = st.selectbox("Frequenza ribilanciamento",
                             list(REBALANCE_RULES), index=2)
    lookback = st.slider("Finestra di stima (barre)", 60, 1260, 504, 21)
    cost_bps = st.slider("Costi di transazione (bps)", 0.0, 50.0, 5.0, 0.5)
    rf = st.slider("Tasso risk-free annuo %", 0.0, 8.0, 2.0, 0.25) / 100.0
    cap = st.slider("Peso massimo per titolo", 0.05, 1.0, 0.35, 0.05)
    shrink = st.toggle("Shrinkage Ledoit-Wolf", value=True,
                       help="Senza shrinkage la covarianza campionaria su molti "
                            "titoli è quasi singolare e i pesi sono rumore.")

if len(tickers) < 2:
    st.warning("Seleziona almeno 2 titoli.")
    st.stop()

end = date.today()
prices, source, warnings = _prices(
    tuple(tickers), end - timedelta(days=365 * years), end, synthetic
)
if source == "synthetic":
    st.info(
        "🟡 **Dati sintetici.** Riproducibili e correlati, utili per validare "
        "la meccanica del motore. Non sono dati di mercato."
    )
for w in warnings:
    st.caption(f"⚠️ {w}")

try:
    cfg = BacktestConfig(
        objective=objective, rebalance=rebalance, lookback=lookback,
        transaction_cost_bps=cost_bps, risk_free_rate=rf,
        max_weight_per_asset=cap, shrinkage=shrink,
    )
except ValueError as exc:
    st.error(f"Configurazione non valida: {exc}")
    st.stop()

benchmark = prices.mean(axis=1).pct_change().dropna()
benchmark.name = "Equal-weight universe"

try:
    bt = run_backtest(prices, cfg, benchmark=benchmark)
except ValueError as exc:
    st.error(f"Backtest non eseguibile: {exc}")
    st.stop()

m = bt.report.to_dict()
a, b, c, d, e = st.columns(5)
a.metric("CAGR", f"{m['CAGR %']:.2f}%")
b.metric("Sharpe", f"{m['Sharpe']:.3f}")
c.metric("Max Drawdown", f"{m['Max Drawdown %']:.2f}%")
d.metric("Sortino", f"{m['Sortino']:.3f}")
e.metric("Calmar", f"{m['Calmar']:.3f}")

f, g, h, i = st.columns(4)
f.metric("Volatilità", f"{m['Volatility %']:.2f}%")
g.metric("Turnover medio", f"{bt.avg_turnover:.2%}")
h.metric("Costo (drag)", f"{bt.cost_drag_bps:.1f} bps")
i.metric("Ribilanciamenti", len(bt.rebalance_dates))

for note in bt.warnings + bt.report.notes:
    st.caption(f"⚠️ {note}")

t1, t2, t3, t4, t5 = st.tabs(
    ["Equity curve", "Pesi", "Metriche", "Confronto obiettivi", "Ottimizzazione oggi"]
)

with t1:
    curve = pd.DataFrame({
        "Strategia": bt.equity_curve,
        "Benchmark": (1 + benchmark.reindex(bt.returns.index).fillna(0)).cumprod(),
    })
    st.line_chart(curve, height=380)
    from modules.metrics import drawdown_series

    st.area_chart(drawdown_series(bt.returns) * 100, height=200)
    st.caption("Drawdown in percentuale dal picco precedente.")
    if bt.report.max_dd_start:
        rec = bt.report.max_dd_recovery
        st.caption(
            f"Peggior drawdown: da {bt.report.max_dd_start.date()} "
            f"a {bt.report.max_dd_end.date()}, "
            + (f"recuperato il {rec.date()}." if rec is not None
               else "**non ancora recuperato** nel periodo.")
        )

with t2:
    st.area_chart(bt.weights_history * 100, height=380)
    st.caption("Composizione nel tempo. Salti frequenti = turnover alto.")
    st.dataframe(
        bt.final_weights().mul(100).to_frame("Peso %").style.format("{:.2f}"),
        width='stretch',
    )

with t3:
    st.dataframe(
        pd.Series(m).to_frame("Valore").style.format("{:.4f}"),
        width='stretch', height=460,
    )
    if bt.benchmark_report:
        st.caption("Benchmark: universo equipesato, stessi costi non applicati.")

with t4:
    st.caption(
        "Stessi prezzi, stesse date di ribilanciamento, stessi costi: il "
        "confronto isola l'obiettivo, non il setup."
    )
    with st.spinner("Eseguo i backtest…"):
        table = compare_objectives(prices, config=cfg, benchmark=benchmark)
    st.dataframe(table.style.format("{:.3f}"), width='stretch')
    if "Sharpe" in table.columns:
        st.bar_chart(table["Sharpe"], height=280)

with t5:
    st.caption("Ottimizzazione sull'intera finestra disponibile, per ispezione.")
    try:
        mu = expected_returns(prices)
        cov = covariance(prices, shrinkage=shrink)
        opt = optimize(mu, cov, objective=objective, risk_free_rate=rf,
                       max_weight_per_asset=cap)
    except ValueError as exc:
        st.error(f"Ottimizzazione fallita: {exc}")
    else:
        k1, k2, k3 = st.columns(3)
        k1.metric("Sharpe atteso", f"{opt.sharpe:.3f}")
        k2.metric("Volatilità", f"{opt.volatility * 100:.2f}%")
        k3.metric(
            "N effettivo", f"{opt.effective_n:.2f}",
            help="Inverso dell'indice di Herfindahl: quanti titoli il "
                 "portafoglio detiene *effettivamente*.",
        )
        st.dataframe(
            pd.DataFrame({
                "Peso %": {k: v * 100 for k, v in opt.weights.items()},
                "Contributo al rischio %": {
                    k: v * 100 for k, v in opt.risk_contributions.items()
                },
            }).style.format("{:.2f}"),
            width='stretch',
        )
        if not opt.converged:
            st.warning(f"Ottimizzatore non convergente: {opt.message}")
        for n in opt.notes:
            st.caption(f"⚠️ {n}")
        st.info(
            "I pesi qui usano **tutta** la storia, quindi non sono un "
            "risultato di backtest: servono come allocazione corrente."
        )
