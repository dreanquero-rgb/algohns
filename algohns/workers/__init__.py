"""Background worker layer (Celery + Redis) for asynchronous auto-trading.

Import ``celery_app.app`` to register tasks. If Celery/Redis are not installed
the module degrades to a no-op so the rest of the platform still imports.
"""

__all__ = ["celery_app", "tasks"]
