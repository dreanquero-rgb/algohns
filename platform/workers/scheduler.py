"""APScheduler fallback for single-node deployments.

Runs the same job bodies as `workers.tasks` on a background thread inside
the Streamlit process, so a laptop deployment keeps syncing with the browser
closed without needing Redis or a separate worker.

Its limits are real and worth knowing: jobs die with the process, there is no
retry history, and two Streamlit processes would each run their own copy.
Use Celery once any of that matters.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from core.config import settings

log = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
    from apscheduler.triggers.cron import CronTrigger  # type: ignore

    APSCHEDULER_AVAILABLE = True
except ImportError:  # pragma: no cover
    BackgroundScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    APSCHEDULER_AVAILABLE = False

_scheduler: Any = None


def get_scheduler() -> Any:
    """Return the shared scheduler, starting it on first use."""
    global _scheduler
    if not APSCHEDULER_AVAILABLE:
        raise RuntimeError(
            "APScheduler non installato. `pip install apscheduler` "
            "oppure imposta SCHEDULER_BACKEND=celery."
        )
    if _scheduler is None:
        _scheduler = BackgroundScheduler(
            timezone="Europe/Rome",
            job_defaults={
                # A missed sync should not pile up replays after a laptop sleeps.
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
        )
        _scheduler.start()
        log.info("APScheduler avviato (backend in-process).")
    return _scheduler


def schedule_cron(
    job_id: str,
    fn: Callable[..., Any],
    *,
    minute: str = "*/15",
    hour: str = "15-22",
    day_of_week: str = "mon-fri",
    replace: bool = True,
    **kwargs: Any,
) -> str:
    """Register `fn` on a cron trigger. Returns the job id."""
    sched = get_scheduler()
    sched.add_job(
        fn,
        trigger=CronTrigger(minute=minute, hour=hour, day_of_week=day_of_week),
        id=job_id,
        replace_existing=replace,
        kwargs=kwargs,
    )
    log.info("job %s schedulato: %s %s %s", job_id, minute, hour, day_of_week)
    return job_id


def list_jobs() -> list[dict[str, Any]]:
    if _scheduler is None:
        return []
    return [
        {
            "id": j.id,
            "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            "trigger": str(j.trigger),
        }
        for j in _scheduler.get_jobs()
    ]


def remove_job(job_id: str) -> bool:
    if _scheduler is None:
        return False
    try:
        _scheduler.remove_job(job_id)
        return True
    except Exception:
        return False


def shutdown(wait: bool = False) -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=wait)
        _scheduler = None


def active_backend() -> str:
    """Which async backend is actually usable right now."""
    from workers.celery_app import CELERY_AVAILABLE

    if settings.scheduler_backend == "celery" and CELERY_AVAILABLE:
        return "celery"
    if APSCHEDULER_AVAILABLE:
        return "apscheduler"
    return "none"
