"""Durable, section-scoped store for saved strategies.

Two sections live side by side and **never mix**:

* ``PM``  — Portfolio Management (macro: allocation / rebalance strategies);
* ``HFT`` — Auto-Trading (statistical: intraday / high-frequency strategies).

A strategy saved in one section is invisible to the other unless it is
explicitly copied. Each strategy carries its ``section`` so the two libraries
stay separate by construction.

Storage is best-effort JSON on the cache dir (one file per section). On a
laptop / Docker / a persistent-volume host it survives restarts; on an
ephemeral free host it lasts until the container recycles. The API is a thin
seam, so the backend can later move to a real database (e.g. Postgres) without
touching any caller.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from ..config import get_settings

__all__ = [
    "Strategy",
    "VALID_SECTIONS",
    "list_strategies",
    "get_strategy",
    "save_strategy",
    "delete_strategy",
    "set_enabled",
]

VALID_SECTIONS = ("PM", "HFT")
VALID_KINDS = ("builtin", "rules", "python")


@dataclass
class Strategy:
    """One saved strategy, bound to exactly one section."""

    name: str
    section: str                       # "PM" | "HFT"
    kind: str = "builtin"              # "builtin" | "rules" | "python"
    params: dict[str, Any] = field(default_factory=dict)
    code: str = ""                     # for kind == "python"
    notes: str = ""
    enabled: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.section not in VALID_SECTIONS:
            raise ValueError(f"section must be one of {VALID_SECTIONS}")
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Strategy":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


def _path(section: str):
    if section not in VALID_SECTIONS:
        raise ValueError(f"unknown section {section!r}")
    return get_settings().cache_dir / f"strategies_{section}.json"


def _load_all(section: str) -> list[Strategy]:
    try:
        p = _path(section)
        if not p.exists():
            return []
        raw = json.loads(p.read_text(encoding="utf-8"))
        out: list[Strategy] = []
        for d in raw if isinstance(raw, list) else []:
            try:
                out.append(Strategy.from_dict(d))
            except Exception:  # noqa: BLE001 - skip a corrupt row, keep the rest
                continue
        return out
    except Exception:  # noqa: BLE001 - unreadable store degrades to empty
        return []


def _save_all(section: str, items: list[Strategy]) -> bool:
    try:
        _path(section).write_text(
            json.dumps([s.to_dict() for s in items], indent=2, default=str),
            encoding="utf-8",
        )
        return True
    except Exception:  # noqa: BLE001 - persistence is best-effort
        return False


def list_strategies(section: str) -> list[Strategy]:
    """All strategies saved in ``section`` (newest first)."""
    return sorted(_load_all(section), key=lambda s: s.updated, reverse=True)


def get_strategy(section: str, sid: str) -> Strategy | None:
    for s in _load_all(section):
        if s.id == sid:
            return s
    return None


def save_strategy(strat: Strategy) -> Strategy:
    """Insert or update ``strat`` (matched by id) within its own section."""
    items = _load_all(strat.section)
    strat.updated = time.time()
    for i, s in enumerate(items):
        if s.id == strat.id:
            items[i] = strat
            break
    else:
        items.append(strat)
    _save_all(strat.section, items)
    return strat


def delete_strategy(section: str, sid: str) -> bool:
    items = _load_all(section)
    kept = [s for s in items if s.id != sid]
    if len(kept) == len(items):
        return False
    return _save_all(section, kept)


def set_enabled(section: str, sid: str, enabled: bool) -> bool:
    items = _load_all(section)
    changed = False
    for s in items:
        if s.id == sid:
            s.enabled = bool(enabled)
            s.updated = time.time()
            changed = True
    if changed:
        _save_all(section, items)
    return changed
