"""Background tasks for asynchronous auto-trading (Module 2).

Each task is registered with Celery *when Celery is available*; otherwise the
plain Python functions can still be called synchronously, and an APScheduler
fallback (:class:`InlineScheduler`) provides periodic execution without a
broker — enough to keep a paper strategy running with the browser closed.
"""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..core.utils import is_available, lazy_import
from ..modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket
from .celery_app import app
from .strategy import StrategyError, resolve_target_weights

_aps = lazy_import(
    "apscheduler.schedulers.background",
    pip_name="APScheduler",
    reason="schedule tasks without a Celery broker",
)


# ---------------------------------------------------------------------------
# Task bodies (plain functions — callable sync or via Celery)
# ---------------------------------------------------------------------------
def _sync_portfolio() -> dict[str, Any]:
    engine = AlpacaExecutionEngine()
    if not engine.configured:
        return {"status": "skipped", "reason": "alpaca keys not configured"}
    return {"status": "ok", "snapshot": engine.portfolio_snapshot()}


def _execute_order(ticket_dict: dict[str, Any]) -> dict[str, Any]:
    engine = AlpacaExecutionEngine()
    ticket = OrderTicket(**ticket_dict)
    return engine.submit_order(ticket)


def _rebalance(target_weights: dict[str, float], dry_run: bool = False) -> list[dict[str, Any]]:
    engine = AlpacaExecutionEngine()
    return engine.rebalance_to_weights(target_weights, dry_run=dry_run)


def _scheduled_rebalance(engine: AlpacaExecutionEngine | None = None) -> dict[str, Any]:
    """The all-day trading step: rebalance the paper account to the configured
    target, but only when it is safe and asked for.

    Four gates, checked in order, each returning a ``skipped`` status rather
    than trading:

    1. **keys present** — no credentials, nothing to do;
    2. **opt-in** — ``ALGO_AUTO_REBALANCE`` must be true, so merely running the
       stack never places an order;
    3. **market open** — the live Alpaca clock must say the market is open, so a
       cron firing on a holiday or at the wrong hour is a no-op;
    4. **strategy resolves** — a bad allocation is reported, not traded.

    Only past all four does it submit orders. Real-money execution stays locked
    upstream: the engine refuses to construct a non-paper client.
    """
    settings = get_settings()
    engine = engine or AlpacaExecutionEngine()

    if not engine.configured:
        return {"status": "skipped", "reason": "alpaca keys not configured"}
    if not settings.auto_rebalance:
        return {"status": "skipped",
                "reason": "auto-rebalance disabled (set ALGO_AUTO_REBALANCE=true)"}

    clock = engine.clock()
    if not clock.get("is_open", False):
        return {"status": "skipped", "reason": "market closed",
                "next_open": clock.get("next_open")}

    try:
        weights = resolve_target_weights(settings.strategy_preset,
                                         settings.target_weights_json)
    except StrategyError as exc:
        return {"status": "error", "reason": str(exc)}

    plan = engine.rebalance_to_weights(weights, dry_run=False)
    return {
        "status": "ok",
        "strategy": settings.strategy_preset if not settings.target_weights_json else "custom",
        "target": weights,
        "orders": plan,
        "n_orders": len(plan),
    }


# ---------------------------------------------------------------------------
# Celery registration (only if Celery is present)
# ---------------------------------------------------------------------------
if app is not None:  # pragma: no cover - requires Celery installed

    @app.task(name="algohns.workers.tasks.sync_portfolio", bind=True, max_retries=3)
    def sync_portfolio(self):  # noqa: ANN001
        try:
            return _sync_portfolio()
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc, countdown=30)

    @app.task(name="algohns.workers.tasks.execute_order")
    def execute_order(ticket_dict: dict[str, Any]):
        return _execute_order(ticket_dict)

    @app.task(name="algohns.workers.tasks.rebalance")
    def rebalance(target_weights: dict[str, float], dry_run: bool = False):
        return _rebalance(target_weights, dry_run=dry_run)

    @app.task(name="algohns.workers.tasks.scheduled_rebalance", bind=True, max_retries=2)
    def scheduled_rebalance(self):  # noqa: ANN001
        try:
            return _scheduled_rebalance()
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc, countdown=60)

else:  # Celery not installed — expose the plain functions under the same names.
    sync_portfolio = _sync_portfolio  # type: ignore[assignment]
    execute_order = _execute_order  # type: ignore[assignment]
    rebalance = _rebalance  # type: ignore[assignment]
    scheduled_rebalance = _scheduled_rebalance  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# APScheduler fallback (no broker required)
# ---------------------------------------------------------------------------
class InlineScheduler:
    """Keep a paper strategy running in-process with APScheduler.

    Useful for a laptop deployment where standing up Redis + Celery is overkill.
    """

    def __init__(self) -> None:
        if not is_available(_aps):
            raise RuntimeError("APScheduler not installed: pip install APScheduler")
        self.scheduler = _aps.BackgroundScheduler(timezone="UTC")

    def start(self, sync_interval_seconds: int | None = None,
              auto_rebalance: bool | None = None) -> None:
        """Start the in-process loops: a periodic account sync, and — when
        auto-rebalance is enabled — the scheduled trading step on its cron.

        Arguments default to the configured settings, so ``InlineScheduler().start()``
        with a filled ``.env`` is enough to run the strategy on a laptop.
        """
        settings = get_settings()
        interval = sync_interval_seconds or settings.sync_interval_seconds
        enabled = settings.auto_rebalance if auto_rebalance is None else auto_rebalance

        self.scheduler.add_job(_sync_portfolio, "interval", seconds=interval,
                               id="sync_portfolio", replace_existing=True)
        if enabled:
            from apscheduler.triggers.cron import CronTrigger

            self.scheduler.add_job(
                _scheduled_rebalance,
                CronTrigger.from_crontab(settings.rebalance_cron),
                id="scheduled_rebalance", replace_existing=True,
            )
        self.scheduler.start()

    def schedule_rebalance(self, target_weights: dict[str, float], cron: str = "0 15 * * 1-5") -> None:
        from apscheduler.triggers.cron import CronTrigger

        self.scheduler.add_job(
            lambda: _rebalance(target_weights, dry_run=False),
            CronTrigger.from_crontab(cron),
            id="rebalance",
            replace_existing=True,
        )

    def stop(self) -> None:
        self.scheduler.shutdown(wait=False)
