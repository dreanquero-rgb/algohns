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
        # Periodic schedule: sync the portfolio every 5 minutes during the day.
        beat_schedule={
            "sync-portfolio": {
                "task": "algohns.workers.tasks.sync_portfolio",
                "schedule": 300.0,
            },
        },
    )
    return application


app = _build_app()
