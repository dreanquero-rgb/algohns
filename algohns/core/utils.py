"""Shared utilities: graceful optional-dependency handling and small helpers.

The platform is designed as *glue code*: every module wires together heavy
third-party libraries. Rather than crash on `import quantlib` when the user
has not installed the extra, we import lazily and raise a friendly, actionable
error only if and when the feature is actually used.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


class OptionalDependency(RuntimeError):
    """Raised when a feature needs a library the user has not installed."""


@dataclass(frozen=True)
class _Missing:
    name: str
    pip_name: str
    reason: str

    def raise_error(self) -> None:
        raise OptionalDependency(
            f"'{self.name}' is required to {self.reason}. "
            f"Install it with:  pip install {self.pip_name}"
        )


def lazy_import(module: str, *, pip_name: str | None = None, reason: str = "use this feature"):
    """Import ``module`` or return a placeholder that explains how to install it.

    Returns the imported module on success. On failure returns a ``_Missing``
    sentinel whose :meth:`raise_error` raises a clear message. Callers use
    :func:`require` to convert the sentinel into a hard error at use time.
    """
    try:
        return importlib.import_module(module)
    except Exception:  # noqa: BLE001 - any import failure is "missing"
        return _Missing(name=module, pip_name=pip_name or module, reason=reason)


def require(obj: Any) -> Any:
    """Return ``obj`` unless it is a missing-dependency sentinel, else raise."""
    if isinstance(obj, _Missing):
        obj.raise_error()
    return obj


def is_available(obj: Any) -> bool:
    """True if a :func:`lazy_import` result is a usable module."""
    return not isinstance(obj, _Missing)


def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Divide protecting against zero / nan denominators."""
    try:
        if denominator == 0 or np.isnan(denominator):
            return default
        value = numerator / denominator
        return default if np.isnan(value) or np.isinf(value) else float(value)
    except (TypeError, ZeroDivisionError):
        return default


def to_frame(data: Any, name: str = "value") -> pd.DataFrame:
    """Coerce a Series / dict / array into a tidy DataFrame for display."""
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, pd.Series):
        return data.rename(name).to_frame()
    if isinstance(data, dict):
        return pd.DataFrame(list(data.items()), columns=["key", name])
    return pd.DataFrame({name: np.atleast_1d(data)})
