"""Expose selected engine modules as editable source — VSCode-style — safely.

Only a curated, safe set of *engine* modules is editable from the browser: the
compute / logic cores (backtest, strategy lab, world simulation, bond engine).
The Alpaca execution engine, the secrets bridge, config and UI plumbing are
deliberately **not** editable from a web page — editing code that touches the
API keys from the browser is a bad idea even behind a login.

Editing model (safe by construction):

* the shipped ``.py`` file on disk is the DEFAULT and is never overwritten;
* an edit is saved as an OVERRIDE — durable text on the cache dir;
* :func:`load_module` compiles the current source (override if present, else the
  shipped file) and execs it in a FRESH module object. A syntax / exec error
  falls back to reporting the error, so a bad edit can only ever break that one
  engine, never the whole app;
* :func:`revert` deletes the override, returning to the shipped source;
* :func:`activate_overrides` (called once at startup, after login) swaps each
  *working* override into ``sys.modules`` so the live pages that import that
  engine pick up the edit on their next run. A broken override is skipped, never
  activated, so it cannot take a page down.

Execution requires ``ALGO_ALLOW_CODE_EXEC`` (the login gate turns it on), which
is why this is only reachable on the private, authenticated instance.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

from ..config import get_settings

__all__ = [
    "EDITABLE",
    "labels",
    "shipped_source",
    "current_source",
    "has_override",
    "validate",
    "save_override",
    "revert",
    "load_module",
    "activate_overrides",
]

# algohns/ package root (…/algohns).
_PKG_ROOT = Path(__file__).resolve().parent.parent
_MODULES_DIR = _PKG_ROOT / "modules"

# Curated, editable engine modules. Key == file stem under algohns/modules/.
# NOTE: alpaca_execution (API keys), config and ui are intentionally absent.
EDITABLE: dict[str, dict[str, str]] = {
    "backtest_suite": {
        "label": "Backtest & Optimization engine",
        "module": "algohns.modules.backtest_suite",
    },
    "strategy_lab": {
        "label": "Strategy Lab — screening rules & code generation",
        "module": "algohns.modules.strategy_lab",
    },
    "hft_strategies": {
        "label": "HFT strategies (momentum / mean-reversion / breakout)",
        "module": "algohns.modules.hft_strategies",
    },
    "world_forward": {
        "label": "World Simulation — forward engine",
        "module": "algohns.modules.world_forward",
    },
    "stochastic": {
        "label": "World Simulation — stochastic price paths",
        "module": "algohns.modules.stochastic",
    },
    "bond_engine": {
        "label": "Bond calculator — YTM / duration / tax",
        "module": "algohns.modules.bond_engine",
    },
}


def labels() -> dict[str, str]:
    """{key: human label} for the editable modules."""
    return {k: v["label"] for k, v in EDITABLE.items()}


def _check_key(name: str) -> None:
    if name not in EDITABLE:
        raise KeyError(f"{name!r} is not an editable engine module")


def _shipped_file(name: str) -> Path:
    _check_key(name)
    return _MODULES_DIR / f"{name}.py"


def _override_file(name: str) -> Path:
    _check_key(name)
    return get_settings().cache_dir / f"codeoverride_{name}.py"


def shipped_source(name: str) -> str:
    """The original, repo-shipped source for ``name``."""
    try:
        return _shipped_file(name).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def has_override(name: str) -> bool:
    try:
        return _override_file(name).exists()
    except Exception:  # noqa: BLE001
        return False


def current_source(name: str) -> str:
    """The active source: the override if one is saved, else the shipped file."""
    if has_override(name):
        try:
            return _override_file(name).read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return shipped_source(name)


def validate(code: str) -> tuple[bool, str]:
    """Static compile check. Returns (ok, error-message)."""
    try:
        compile(code, "<edited>", "exec")
        return True, ""
    except SyntaxError as exc:
        return False, f"Riga {exc.lineno}: {exc.msg}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def save_override(name: str, code: str) -> tuple[bool, str]:
    """Validate then persist an override. Returns (ok, error-message)."""
    ok, err = validate(code)
    if not ok:
        return False, err
    try:
        _override_file(name).write_text(code, encoding="utf-8")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"Salvataggio fallito: {exc}"


def revert(name: str) -> bool:
    """Drop the override, returning to the shipped source."""
    try:
        _override_file(name).unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def load_module(name: str, source: str | None = None):
    """Compile+exec ``name``'s current (or given) source as a fresh module.

    Returns the live module object. Relative imports inside the engine resolve
    against ``algohns.modules`` just like the shipped file. Raises on any
    compile/exec error so callers can show it — the shipped module on disk and
    everything in ``sys.modules`` are left untouched.
    """
    _check_key(name)
    src = current_source(name) if source is None else source
    full = EDITABLE[name]["module"]
    mod = types.ModuleType(full)
    mod.__file__ = str(_shipped_file(name))
    # __package__ lets relative imports (``from ..config import ...``) resolve
    # against algohns.modules exactly like the shipped file.
    mod.__package__ = "algohns.modules"
    mod.__loader__ = None
    code_obj = compile(src, mod.__file__, "exec")
    # The module must be in sys.modules *during* exec so machinery that looks it
    # up by name (dataclasses, typing, pickling) finds it. Restore the previous
    # entry afterwards so a plain load stays isolated — activate_overrides is the
    # one that deliberately keeps it registered.
    _sentinel = object()
    prev = sys.modules.get(full, _sentinel)
    sys.modules[full] = mod
    try:
        exec(code_obj, mod.__dict__)  # noqa: S102 - trusted, private, gated instance
    finally:
        if prev is _sentinel:
            sys.modules.pop(full, None)
        else:
            sys.modules[full] = prev
    return mod


def activate_overrides() -> dict[str, str]:
    """Swap every *working* override into ``sys.modules`` so live pages use it.

    Called once at startup on the private instance. Returns {name: error} for
    any override that failed to load (and was therefore skipped, never
    activated) — the shipped version keeps serving those.
    """
    errors: dict[str, str] = {}
    if not get_settings().allow_code_exec:
        return errors
    for name in EDITABLE:
        if not has_override(name):
            continue
        full = EDITABLE[name]["module"]
        try:
            mod = load_module(name)
            sys.modules[full] = mod  # live pages import this on their next run
        except Exception as exc:  # noqa: BLE001 - broken override is skipped
            errors[name] = str(exc)
    return errors
