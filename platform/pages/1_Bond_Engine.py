"""Module 1 UI — European bond yield and multi-tax engine."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.taxation import PROFILES, SOURCES, FiscalResidence  # noqa: E402
from modules.bond_engine import Bond, analyse, quantlib_bridge  # noqa: E402

st.set_page_config(page_title="Bond Engine", page_icon="◆", layout="wide")
st.title("Module 1 — European Bond Yield & Multi-Tax Engine")
st.caption(
    "YTM netto risolto come TIR dei flussi post-imposta, convenzione ICMA "
    "con capitalizzazione nominale alla frequenza cedolare."
)

# Representative European government bonds, as worked examples.
PRESETS = {
    "BTP 3.85% 01/07/2034": dict(
        isin="IT0005611741", maturity=date(2034, 7, 1), coupon_rate=0.0385,
        clean_price=101.25, frequency=2, issuer_country="IT", is_government=True),
    "BTP 4.00% 30/10/2031": dict(
        isin="IT0005560948", maturity=date(2031, 10, 30), coupon_rate=0.0400,
        clean_price=103.10, frequency=2, issuer_country="IT", is_government=True),
    "Bund 2.60% 15/08/2034": dict(
        isin="DE000BU2Z015", maturity=date(2034, 8, 15), coupon_rate=0.0260,
        clean_price=98.40, frequency=1, issuer_country="DE", is_government=True),
    "OAT 3.00% 25/05/2033": dict(
        isin="FR001400H7M1", maturity=date(2033, 5, 25), coupon_rate=0.0300,
        clean_price=97.85, frequency=1, issuer_country="FR", is_government=True),
    "Bonos 3.55% 31/10/2033": dict(
        isin="ES0000012L695", maturity=date(2033, 10, 31), coupon_rate=0.0355,
        clean_price=100.60, frequency=1, issuer_country="ES", is_government=True),
    "Corporate IG 4.25% 2030": dict(
        isin="XS2000000000", maturity=date(2030, 6, 15), coupon_rate=0.0425,
        clean_price=99.10, frequency=1, issuer_country="IT", is_government=False),
}

left, right = st.columns([1, 2])

with left:
    st.subheader("Titolo")
    preset_name = st.selectbox("Preset", list(PRESETS))
    preset = PRESETS[preset_name]

    isin = st.text_input("ISIN", preset["isin"])
    maturity = st.date_input("Scadenza", preset["maturity"],
                             min_value=date(2026, 1, 1), max_value=date(2075, 1, 1))
    coupon = st.number_input("Cedola annua %", 0.0, 20.0,
                             preset["coupon_rate"] * 100, step=0.05) / 100.0
    price = st.number_input("Prezzo tel quel (per 100)", 1.0, 250.0,
                            preset["clean_price"], step=0.05)
    freq = st.selectbox("Frequenza cedolare", [1, 2, 4],
                        index=[1, 2, 4].index(preset["frequency"]),
                        format_func=lambda f: {1: "Annuale", 2: "Semestrale",
                                               4: "Trimestrale"}[f])
    country = st.text_input("Paese emittente (ISO2)", preset["issuer_country"]).upper()
    is_gov = st.checkbox("Titolo di Stato", preset["is_government"])
    is_supra = st.checkbox("Sovranazionale (BEI, MES…)", False)

    st.subheader("Fiscalità")
    residence = st.selectbox(
        "Residenza fiscale", list(FiscalResidence),
        format_func=lambda r: PROFILES[r].label,
    )
    zainetto = st.number_input(
        "Minusvalenze riportabili (€)", 0.0, 1e7, 0.0, step=100.0,
        help=(
            "Zainetto fiscale. Compensa solo i redditi diversi (plusvalenza "
            "a rimborso), mai le cedole: sono redditi di capitale."
        ),
    )
    bollo = st.checkbox("Includi imposta di bollo 0,2%/anno", False)
    settlement = st.date_input("Data di regolamento", date.today())

with right:
    try:
        bond = Bond(
            isin=isin, name=preset_name, maturity=maturity, coupon_rate=coupon,
            clean_price=price, frequency=freq, issuer_country=country,
            is_government=is_gov, is_supranational=is_supra,
        )
        res = analyse(bond, settlement, residence=residence,
                      loss_carryforward=zainetto, include_stamp_duty=bollo)
    except ValueError as exc:
        st.error(f"Input non valido: {exc}")
        st.stop()

    st.subheader("Risultati")
    a, b, c, d = st.columns(4)
    a.metric("YTM lordo", f"{res.ytm_gross * 100:.3f}%")
    b.metric("YTM netto", f"{res.ytm_net * 100:.3f}%",
             delta=f"-{res.tax_drag_bps:.0f} bps", delta_color="inverse")
    c.metric("Duration mod.", f"{res.modified_duration:.3f}")
    d.metric("Convexity", f"{res.convexity:.2f}")

    e, f, g, h = st.columns(4)
    e.metric("Rateo lordo", f"{res.accrued_gross:.4f}")
    f.metric("Prezzo tel quel", f"{res.dirty_price:.4f}")
    g.metric("YTM netto eff.", f"{res.ytm_net_effective * 100:.3f}%",
             help="Tasso annuo effettivo: confrontabile tra frequenze diverse.")
    h.metric("Classe fiscale", res.tax_class.value.replace("_", " "))

    if zainetto and res.loss_carryforward_used:
        st.success(
            f"Compensate minusvalenze per €{res.loss_carryforward_used:,.2f} "
            "sulla plusvalenza a rimborso."
        )
    for w in res.warnings:
        st.warning(w)

    st.divider()
    tab1, tab2, tab3, tab4 = st.tabs(
        ["Sensitività", "Flussi di cassa", "Confronto fiscale", "Validazione"]
    )

    with tab1:
        shifts = [-200, -150, -100, -50, -25, 0, 25, 50, 100, 150, 200]
        rows = [
            {
                "Shift (bps)": s,
                "Δ prezzo %": res.price_change(s / 10_000) * 100,
                "Prezzo stimato": res.dirty_price * (1 + res.price_change(s / 10_000)),
            }
            for s in shifts
        ]
        frame = pd.DataFrame(rows).set_index("Shift (bps)")
        st.line_chart(frame[["Δ prezzo %"]], height=300)
        st.dataframe(frame.style.format("{:.4f}"), width='stretch')
        st.caption(
            "Espansione duration + convexity. L'asimmetria fra +100 e -100 bps "
            "è l'effetto della convexity."
        )

    with tab2:
        cf = pd.DataFrame([
            {
                "Data": c.pay_date, "Tipo": c.kind, "Lordo": c.gross,
                "Imposta": c.tax, "Netto": c.net, "Anni": round(c.years, 4),
            }
            for c in res.cash_flows
        ])
        st.dataframe(cf.style.format({
            "Lordo": "{:.4f}", "Imposta": "{:.4f}", "Netto": "{:.4f}",
        }), width='stretch', height=340)
        st.caption(f"Imposta totale sul piano: {res.total_tax:.4f} per 100 di nominale.")

    with tab3:
        comparison = []
        for r in FiscalResidence:
            try:
                alt = analyse(bond, settlement, residence=r,
                              loss_carryforward=zainetto, include_stamp_duty=bollo)
            except ValueError:
                continue
            comparison.append({
                "Residenza": PROFILES[r].label,
                "YTM netto %": alt.ytm_net * 100,
                "Tax drag bps": alt.tax_drag_bps,
                "Classe": alt.tax_class.value,
            })
        st.dataframe(
            pd.DataFrame(comparison).set_index("Residenza").style.format("{:.3f}",
                subset=["YTM netto %", "Tax drag bps"]),
            width='stretch',
        )
        st.caption("Stesso titolo, stesso prezzo, residenze fiscali diverse.")
        with st.expander("Riferimenti normativi"):
            for k, v in SOURCES.items():
                st.caption(f"**{k}** — {v}")

    with tab4:
        st.caption(
            "QuantLib è opzionale e serve solo a controllare la matematica "
            "nativa, che è già coperta da test su casi in forma chiusa."
        )
        ql = quantlib_bridge(bond, settlement)
        if ql is None:
            st.info("QuantLib non installato: `pip install QuantLib`.")
        else:
            check = pd.DataFrame({
                "Nativo": {
                    "YTM": res.ytm_gross,
                    "Duration mod.": res.modified_duration,
                    "Convexity": res.convexity,
                },
                "QuantLib": {
                    "YTM": ql["ytm"],
                    "Duration mod.": ql["modified_duration"],
                    "Convexity": ql["convexity"],
                },
            })
            check["Δ"] = check["Nativo"] - check["QuantLib"]
            st.dataframe(check.style.format("{:.8f}"), width='stretch')
