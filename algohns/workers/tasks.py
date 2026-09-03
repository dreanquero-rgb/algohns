"""Background tasks for asynchronous auto-trading (Module 2).

Each task is registered with Celery *when Celery is available*; otherwise the
plain Python functions can still be called synchronously, and an APScheduler
fallback (:class:`InlineScheduler`) provides periodic execution without a
broker — enough to keep a paper strategy running with the browser closed.
"""
from __future__ import annotations

from typing import Any

from ..core.utils import is_available, lazy_import
from ..modules.alpaca_execution import AlpacaExecutionEngine, OrderTicket
from .celery_app import app

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

else:  # Celery not installed — expose the plain functions under the same names.
    sync_portfolio = _sync_portfolio  # type: ignore[assignment]
    execute_order = _execute_order  # type: ignore[assignment]
    rebalance = _rebalance  # type: ignore[assignment]


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

    def start(self, sync_interval_seconds: int = 300) -> None:
        self.scheduler.add_job(_sync_portfolio, "interval", seconds=sync_interval_seconds, id="sync_portfolio", replace_existing=True)
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
