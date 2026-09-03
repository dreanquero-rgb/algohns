"""Streamlit page — Module 1: European Bond Yield & Multi-Tax Engine."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from algohns.modules.bond_engine import TAX_PROFILES, Bond, BondEngine
from algohns.ui import dependency_notice, header

header(
    "European Bond Yield & Multi-Tax Engine",
    "Net YTM (TIR/XIRR), accrued interest, duration & convexity with dynamic taxation.",
    badge="Module 1",
)

preset = st.selectbox(
    "Preset",
    ["Custom", "BTP 3.5% 2030 (IT gov)", "Bund 2.3% 2033 (DE gov)", "Corporate 5% 2029"],
)

defaults = {
    "BTP 3.5% 2030 (IT gov)": dict(coupon=3.5, freq=2, maturity=date(2030, 3, 1), price=98.4, tax="IT_GOV_WHITELIST", issue=95.0),
    "Bund 2.3% 2033 (DE gov)": dict(coupon=2.3, freq=1, maturity=date(2033, 2, 15), price=96.2, tax="IT_GOV_WHITELIST", issue=100.0),
    "Corporate 5% 2029": dict(coupon=5.0, freq=2, maturity=date(2029, 6, 30), price=101.5, tax="IT_CORPORATE", issue=100.0),
}
d = defaults.get(preset, dict(coupon=3.5, freq=2, maturity=date(2030, 3, 1), price=98.4, tax="IT_GOV_WHITELIST", issue=100.0))

with st.form("bond"):
    c1, c2, c3 = st.columns(3)
    with c1:
        name = st.text_input("Name / ISIN", value=preset if preset != "Custom" else "IT0005000000")
        face = st.number_input("Face value", value=100.0, step=10.0)
        coupon = st.number_input("Coupon rate (%)", value=float(d["coupon"]), step=0.05) / 100
    with c2:
        freq = st.selectbox("Coupon frequency", [1, 2, 4], index=[1, 2, 4].index(d["freq"]))
        clean_price = st.number_input("Clean price", value=float(d["price"]), step=0.1)
        issue_price = st.number_input("Issue price (disaggio)", value=float(d["issue"]), step=0.1)
    with c3:
        settlement = st.date_input("Settlement date", value=date.today())
        maturity = st.date_input("Maturity date", value=d["maturity"])
        issue_date = st.date_input("Issue date", value=date(2020, 1, 1))

    tax_key = st.selectbox(
        "Tax profile",
        options=list(TAX_PROFILES.keys()),
        index=list(TAX_PROFILES.keys()).index(d["tax"]),
        format_func=lambda k: TAX_PROFILES[k].name,
    )
    st.caption(TAX_PROFILES[tax_key].note)
    submitted = st.form_submit_button("Analyse bond", type="primary")

if submitted:
    bond = Bond(
        face_value=face, coupon_rate=coupon, frequency=int(freq),
        issue_date=issue_date, maturity_date=maturity, settlement_date=settlement,
        clean_price=clean_price, issue_price=issue_price, name=name,
    )
    try:
        engine = BondEngine()
        res = engine.analyse(bond, tax_key=tax_key)
    except Exception as exc:  # noqa: BLE001
        dependency_notice(exc)
        st.stop()

    st.subheader("Results")
    m = st.columns(4)
    m[0].metric("Gross YTM", f"{res.ytm_gross*100:.3f}%")
    m[1].metric("Net YTM (after tax)", f"{res.ytm_net*100:.3f}%",
                delta=f"{(res.ytm_net-res.ytm_gross)*100:.3f}%")
    m[2].metric("Modified Duration", f"{res.modified_duration:.3f}")
    m[3].metric("Convexity", f"{res.convexity:.2f}")

    m2 = st.columns(4)
    m2[0].metric("Dirty price", f"{res.dirty_price:.3f}")
    m2[1].metric("Accrued (rateo)", f"{res.accrued_interest:.3f}")
    m2[2].metric("Total tax", f"{res.total_tax_paid:.2f}")
    m2[3].metric("Capital gain", f"{res.capital_gain:.2f}")

    if res.minusvalenza_credit > 0:
        st.info(f"Capital loss detected → minusvalenza tax credit of {res.minusvalenza_credit:.2f} "
                "(usable against future capital gains).")

    st.markdown("**Cash-flow schedule (gross vs net)**")
    cf = pd.DataFrame(res.cashflow_table)
    st.dataframe(cf, use_container_width=True, hide_index=True)
    if not cf.empty:
        st.line_chart(cf.set_index("date")[["gross_cf", "net_cf"]])

    # Independent QuantLib cross-check when available.
    ql = engine.quantlib_crosscheck(bond)
    with st.expander("QuantLib cross-check (independent verification)"):
        if ql is None:
            st.caption("QuantLib not installed — pure-python engine used. `pip install QuantLib` to enable.")
        else:
            st.json(ql)
