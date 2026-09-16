"""Disk cache for external API payloads.

SEC and market-data endpoints are rate-limited and slow; every module here
reads through this cache so a Streamlit rerun does not re-hit the network.
Keys are hashed so arbitrary URLs/params are safe as filenames.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from core.config import settings

__all__ = ["cached_json", "cache_key", "purge"]

T = TypeVar("T")


def cache_key(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _path(key: str) -> Path:
    return settings.cache_dir / f"{key}.json"


def cached_json(
    key: str,
    producer: Callable[[], Any],
    *,
    ttl: int | None = None,
    force: bool = False,
) -> Any:
    """Return cached JSON for `key`, else call `producer` and store it.

    A corrupt or unreadable cache entry is treated as a miss rather than an
    error, so a partial write (killed process) self-heals.
    """
    ttl = settings.cache_ttl_seconds if ttl is None else ttl
    p = _path(key)

    if not force and p.exists():
        try:
            age = time.time() - p.stat().st_mtime
            if age < ttl:
                return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # treat as miss

    value = producer()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so readers never see a partial file.
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(p)
    except (OSError, TypeError):
        pass  # cache write failures must never break the caller
    return value


def purge(prefix: str = "") -> int:
    """Delete cache entries, optionally filtered by key prefix. Returns count."""
    n = 0
    for f in settings.cache_dir.glob("*.json"):
        if f.name.startswith(prefix):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n
