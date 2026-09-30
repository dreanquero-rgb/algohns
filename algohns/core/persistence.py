"""Durable, local persistence for user choices.

Streamlit ``session_state`` is per-session and lost on restart. These helpers
write small JSON blobs to the cache directory so a filled risk profile and a
chosen strategy survive an app restart — on a laptop or in Docker they persist
indefinitely; on an ephemeral host they last until the container is recycled.

Best-effort by design: any IO/parse error degrades to "no saved state" rather
than breaking the page, so a read-only or missing cache dir is never fatal.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import get_settings


def _path(name: str):
    safe = "".join(c for c in name if c.isalnum() or c in ("_", "-"))
    return get_settings().cache_dir / f"state_{safe}.json"


def save_state(name: str, data: dict[str, Any]) -> bool:
    """Persist ``data`` under ``name``. Returns True on success."""
    try:
        _path(name).write_text(json.dumps(data, indent=2, default=str),
                               encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 - persistence is best-effort
        return False


def load_state(name: str) -> dict[str, Any] | None:
    """Load the blob saved under ``name``, or None if absent/unreadable."""
    try:
        p = _path(name)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        pass
    return None


def clear_state(name: str) -> None:
    """Remove the saved blob for ``name`` if present."""
    try:
        _path(name).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
