"""Algohns V12 — Quant Asset Manager OS (Python edition).

A modular, glue-code architecture that integrates best-in-class open-source
quant libraries into a single Streamlit-driven platform:

    Module 1  bond_engine        European Bond Yield & Multi-Tax Engine
    Module 2  alpaca_execution   Alpaca Asynchronous Auto-Trading Engine
    Module 3  backtest_suite     Backtesting & Portfolio Optimization Suite
    Module 4  supply_chain_graph S&P 500 Supply Chain Graph Analytics
    Module 5  sec_aggregator     Consolidated SEC Financial Statements

Each module degrades gracefully when an optional heavy dependency
(QuantLib, alpaca-py, spaCy, PyPortfolioOpt, vectorbt ...) is not installed,
so the platform always boots and tells the user what to `pip install`.
"""

__version__ = "12.0.0"
__all__ = ["__version__"]
