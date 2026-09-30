"""Celery application factory for background auto-trading.

Run a worker with:

    celery -A algohns.workers.celery_app.app worker --loglevel=info

And the beat scheduler (periodic sync / rebalance) with:

    celery -A algohns.workers.celery_app.app beat --loglevel=info

If Celery is not installed, ``app`` is ``None`` and callers should fall back to
:class:`algohns.workers.tasks.InlineScheduler` (APScheduler) or synchronous
execution.
"""
from __future__ import annotations

from ..config import get_settings
from ..core.utils import is_available, lazy_import

_celery = lazy_import("celery", pip_name="celery[redis]", reason="run background trading tasks")


def _build_app():
    if not is_available(_celery):
        return None
    settings = get_settings()
    application = _celery.Celery(
        "algohns",
        broker=settings.celery_broker,
        backend=settings.celery_backend,
        include=["algohns.workers.tasks"],
    )
    application.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        # Periodic schedule:
        #   * read the account for the monitoring log every N seconds;
        #   * run the strategy rebalance on its cron. That task self-gates on
        #     the opt-in flag and the live market clock, so it is safe to keep
        #     scheduled even when auto-rebalance is off — it simply no-ops.
        beat_schedule={
            "sync-portfolio": {
                "task": "algohns.workers.tasks.sync_portfolio",
                "schedule": float(settings.sync_interval_seconds),
            },
            "scheduled-rebalance": {
                "task": "algohns.workers.tasks.scheduled_rebalance",
                "schedule": _rebalance_schedule(settings.rebalance_cron),
            },
        },
    )
    return application


def _rebalance_schedule(cron_expr: str):
    """Turn a 5-field cron string into a Celery schedule.

    Falls back to a daily interval if the expression is malformed, so a typo in
    ALGO_REBALANCE_CRON degrades to "once a day" rather than crashing beat.
    """
    try:
        from celery.schedules import crontab

        minute, hour, dom, month, dow = cron_expr.split()
        return crontab(minute=minute, hour=hour, day_of_month=dom,
                       month_of_year=month, day_of_week=dow)
    except Exception:  # noqa: BLE001
        return 86_400.0


app = _build_app()
