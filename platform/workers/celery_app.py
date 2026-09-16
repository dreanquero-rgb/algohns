"""Celery application for background execution.

Celery is optional. When it is not installed (or Redis is unreachable),
`workers.scheduler` falls back to APScheduler in-process, so the platform
still rebalances on a timer for a single-node deployment. Celery is the
right choice once you want retries, a task history and more than one
worker.
"""
from __future__ import annotations

import logging

from core.config import settings

log = logging.getLogger(__name__)

try:
    from celery import Celery  # type: ignore
    from celery.schedules import crontab  # type: ignore

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by absence
    Celery = None  # type: ignore
    crontab = None  # type: ignore
    CELERY_AVAILABLE = False


def make_app() -> "Celery | None":
    """Build the Celery app, or None when Celery is absent."""
    if not CELERY_AVAILABLE:
        log.info("Celery non installato: uso APScheduler in-process.")
        return None

    app = Celery(
        "algohns",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=["workers.tasks"],
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Europe/Rome",
        enable_utc=True,
        # A rebalance is not idempotent: never re-deliver it silently after a
        # worker dies mid-task. Failures surface instead of double-trading.
        task_acks_late=False,
        task_reject_on_worker_lost=False,
        worker_prefetch_multiplier=1,
        task_time_limit=600,
        task_soft_time_limit=540,
        result_expires=7 * 24 * 3600,
    )
    app.conf.beat_schedule = {
        "sync-portfolio-every-15min": {
            "task": "workers.tasks.sync_portfolio",
            # Market hours only, weekdays, in the configured timezone.
            "schedule": crontab(minute="*/15", hour="15-22", day_of_week="1-5"),
        },
        "drift-check-daily-close": {
            "task": "workers.tasks.check_drift",
            "schedule": crontab(minute="45", hour="21", day_of_week="1-5"),
        },
    }
    return app


celery_app = make_app()
