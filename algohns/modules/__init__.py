"""The five platform modules.

Import them lazily from the pages / orchestrator so a missing optional
dependency in one module never blocks the others.
"""

__all__ = [
    "bond_engine",
    "alpaca_execution",
    "backtest_suite",
    "supply_chain_graph",
    "sec_aggregator",
]
