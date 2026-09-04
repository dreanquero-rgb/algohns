"""Streamlit page — Module 1: European Bond Screener & Multi-Tax Yield Engine.

A RendimentiBTP / simpletoolsforinvestors-style screener: the full list of
European bonds (BTP/BOT/CCT/EuroMOT) with gross & net yield-to-maturity,
duration and current yield computed per instrument for the chosen tax profile —
filterable and sortable — plus a single-bond calculator for deep analysis.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from algohns.modules.bond_data import MOT_LISTS, BondScreener, tax_profile_options
from algohns.modules.bond_engine import TAX_PROFILES, Bond, BondEngine
from algohns.ui import dependency_notice, header

header(
    "European Bond Screener & Multi-Tax Engine",
    "Rendimento netto, duration e convexity su BTP · Bund · OAT · Bonos · Eurobond.",
    badge="Module 1",
)


@st.cache_data(ttl=1800, show_spinner=False)
def _load_universe(markets: tuple[str, ...]):
    sc = BondScreener()
    bonds, source = sc.load_universe(list(markets))
    return bonds, source


tab_screener, tab_calc = st.tabs(["📋 Screener", "🧮 Single-bond calculator"])

# =============================================================================
# TAB 1 — SCREENER
# =============================================================================
with tab_screener:
    top = st.columns([2, 2, 1])
    markets = top[0].multiselect("Markets", list(MOT_LISTS.keys()),
                                 default=list(MOT_LISTS.keys()))
    tax_opts = tax_profile_options()
    tax_key = top[1].selectbox("Tax profile (applied to whole table)",
                               list(tax_opts.keys()), format_func=lambda k: tax_opts[k])
    if top[2].button("🔄 Refresh", help="Re-fetch the live universe"):
        _load_universe.clear()

    try:
        bonds, source = _load_universe(tuple(markets) or tuple(MOT_LISTS.keys()))
    except Exception as exc:  # noqa: BLE001
        dependency_notice(exc)
        st.stop()

    if source == "live":
        st.success(f"🟢 Live data from Borsa Italiana — {len(bonds)} instruments.")
    else:
        st.warning("🟡 Showing **sample** data (exchange unreachable from here). "
                   "On deploy — where outbound network is open — this loads the live universe.")

    sc = BondScreener()
    df = sc.build_table(bonds, tax_key=tax_key)
    if df.empty:
        st.info("No instruments loaded.")
        st.stop()

    # ---- Filters -----------------------------------------------------------
    with st.expander("🔎 Filters", expanded=True):
        f = st.columns(4)
        countries = sorted(df["Country"].dropna().unique().tolist())
        sel_countries = f[0].multiselect("Country", countries, default=countries)
        types = sorted(df["Type"].dropna().unique().tolist())
        sel_types = f[1].multiselect("Type", types, default=types)
        ymax = float(df["Years"].dropna().max() or 30)
        yr = f[2].slider("Years to maturity", 0.0, round(ymax, 1), (0.0, round(ymax, 1)))
        min_net = f[3].number_input("Min Net YTM %", value=0.0, step=0.25)

    mask = df["Country"].isin(sel_countries) & df["Type"].isin(sel_types)
    if "Years" in df:
        mask &= df["Years"].fillna(0).between(yr[0], yr[1])
    if "NetYTM%" in df and min_net > 0:
        mask &= df["NetYTM%"].fillna(-99) >= min_net
    view = df[mask].copy()

    # ---- Metrics row -------------------------------------------------------
    m = st.columns(4)
    m[0].metric("Instruments", len(view))
    if "NetYTM%" in view and view["NetYTM%"].notna().any():
        m[1].metric("Avg Net YTM", f"{view['NetYTM%'].mean():.2f}%")
        best = view.loc[view["NetYTM%"].idxmax()]
        m[2].metric("Top Net YTM", f"{best['NetYTM%']:.2f}%", help=str(best["Name"]))
        m[3].metric("Avg Mod.Duration", f"{view['ModDur'].mean():.2f}")

    # ---- Table (sortable) --------------------------------------------------
    col_cfg = {
        "Coupon%": st.column_config.NumberColumn(format="%.2f%%"),
        "YTM%": st.column_config.NumberColumn("Gross YTM", format="%.3f%%"),
        "NetYTM%": st.column_config.NumberColumn("Net YTM", format="%.3f%%"),
        "Curr.Yield%": st.column_config.NumberColumn("Curr.Yield", format="%.2f%%"),
        "Price": st.column_config.NumberColumn(format="%.2f"),
        "ModDur": st.column_config.NumberColumn("Mod.Dur", format="%.2f"),
        "Years": st.column_config.NumberColumn(format="%.1f"),
    }
    st.dataframe(
        view.sort_values("NetYTM%", ascending=False, na_position="last"),
        use_container_width=True, hide_index=True, height=460, column_config=col_cfg,
    )
    st.download_button("⬇️ Download CSV", view.to_csv(index=False).encode(),
                       file_name="algohns_bond_screener.csv", mime="text/csv")
    st.caption(f"Tax profile: {TAX_PROFILES[tax_key].name} — {TAX_PROFILES[tax_key].note}")

# =============================================================================
# TAB 2 — SINGLE-BOND CALCULATOR
# =============================================================================
with tab_calc:
    with st.form("bond"):
        c1, c2, c3 = st.columns(3)
        with c1:
            name = st.text_input("Name / ISIN", value="IT0005000000")
            face = st.number_input("Face value", value=100.0, step=10.0)
            coupon = st.number_input("Coupon rate (%)", value=3.5, step=0.05) / 100
        with c2:
            freq = st.selectbox("Coupon frequency", [1, 2, 4], index=1)
            clean_price = st.number_input("Clean price", value=98.4, step=0.1)
            issue_price = st.number_input("Issue price (disaggio)", value=100.0, step=0.1)
        with c3:
            settlement = st.date_input("Settlement date", value=date.today())
            maturity = st.date_input("Maturity date", value=date(2030, 3, 1))
            issue_date = st.date_input("Issue date", value=date(2020, 1, 1))
        tax_key2 = st.selectbox("Tax profile", list(TAX_PROFILES.keys()),
                                format_func=lambda k: TAX_PROFILES[k].name)
        st.caption(TAX_PROFILES[tax_key2].note)
        submitted = st.form_submit_button("Analyse bond", type="primary")

    if submitted:
        bond = Bond(face_value=face, coupon_rate=coupon, frequency=int(freq),
                    issue_date=issue_date, maturity_date=maturity, settlement_date=settlement,
                    clean_price=clean_price, issue_price=issue_price, name=name)
        try:
            engine = BondEngine()
            res = engine.analyse(bond, tax_key=tax_key2)
        except Exception as exc:  # noqa: BLE001
            dependency_notice(exc)
            st.stop()

        m = st.columns(4)
        m[0].metric("Gross YTM", f"{res.ytm_gross*100:.3f}%")
        m[1].metric("Net YTM", f"{res.ytm_net*100:.3f}%", delta=f"{(res.ytm_net-res.ytm_gross)*100:.3f}%")
        m[2].metric("Modified Duration", f"{res.modified_duration:.3f}")
        m[3].metric("Convexity", f"{res.convexity:.2f}")
        m2 = st.columns(4)
        m2[0].metric("Dirty price", f"{res.dirty_price:.3f}")
        m2[1].metric("Accrued (rateo)", f"{res.accrued_interest:.3f}")
        m2[2].metric("Total tax", f"{res.total_tax_paid:.2f}")
        m2[3].metric("Capital gain", f"{res.capital_gain:.2f}")
        if res.minusvalenza_credit > 0:
            st.info(f"Capital loss → minusvalenza tax credit {res.minusvalenza_credit:.2f}.")

        cf = pd.DataFrame(res.cashflow_table)
        st.markdown("**Cash-flow schedule (gross vs net)**")
        st.dataframe(cf, use_container_width=True, hide_index=True)
        if not cf.empty:
            st.line_chart(cf.set_index("date")[["gross_cf", "net_cf"]])

        ql = engine.quantlib_crosscheck(bond)
        with st.expander("QuantLib cross-check (independent verification)"):
            st.json(ql if ql else {"note": "QuantLib not installed; pure-python engine used."})
