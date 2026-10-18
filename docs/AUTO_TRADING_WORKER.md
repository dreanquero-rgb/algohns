# HFT Auto-Trading — running the engine 24/7 (free, Oracle)

The Auto-Trading · HFT section runs its strategies **while the app is open**.
A Streamlit page is a UI, not a long-lived loop, and free hosts sleep when idle
— so for continuous intraday trading the engine belongs on an always-on worker.

## The model
- The **app** (Streamlit) stays the control panel: build strategies, enable
  them, watch signals and P/L. Saved strategies live in the section-scoped
  strategy store (`algohns/core/strategy_store.py`, section `HFT`).
- A small **worker** runs on a machine that never sleeps, loops on 1-minute
  bars, reads the *enabled* `HFT` strategies, computes signals
  (`algohns/modules/hft_strategies.py`) and places paper orders via
  `AlpacaExecutionEngine`. It shares the same Alpaca account and strategy store
  as the app.

## Why Oracle Cloud Always-Free
A single Always-Free VM (ARM Ampere, up to 24 GB RAM, free forever — a card is
needed only to verify the account) is more than enough to run the worker and,
if you like, the Streamlit app too. Any always-on box works (a VPS, a home
server, a Raspberry Pi); Oracle is the generous free option.

## Sketch of the worker loop
```
while market_is_open():              # or 24/7 once we move to crypto
    for strat in enabled_HFT_strategies():
        bars   = recent_1m_bars(strat.symbols)
        signal = compute_signals(strat.builtin, bars, strat.params)
        place_or_adjust_orders(signal)   # paper, idempotent
    sleep(60)
```

This is the next build step — say the word and we wire the worker + a shared
(Postgres) strategy store so the app and the worker see the same strategies.
