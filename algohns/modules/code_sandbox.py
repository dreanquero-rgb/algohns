"""Safe, opt-in execution sandbox for user-edited strategy / backtest code.

The platform deploys to a public URL, so running visitor-supplied Python there
would be a remote-code-execution hole. Code execution is therefore:

* **OFF by default** (``ALGO_ALLOW_CODE_EXEC`` unset/false). The in-app editor
  still lets you read, edit and download the code, but "Run" is disabled with a
  clear message. This is the safe posture for a shared deployment.
* **ON** when you set ``ALGO_ALLOW_CODE_EXEC=true`` — intended for running the
  app locally / in Docker on your own machine, where executing your own edited
  code is exactly the point.

Even when enabled the runner is defensive. Before anything runs, a static check
(:func:`static_check`) rejects imports, every double-underscore name — which
closes the classic CPython sandbox escapes such as ``().__class__.__bases__`` —
and the obvious I/O and introspection hatches. Execution then happens in a
namespace whose ``__builtins__`` is a curated allow-list (no ``__import__``,
``open``, ``eval``, ``exec``, ``compile`` …) plus only the objects the caller
injects. This is a usable strategy DSL, not a general Python shell.

It is deliberately **not** presented as a hard security boundary: sandboxing
CPython in-process is leaky by nature, which is precisely why execution stays
off on public deployments and the guard rails above exist as defence in depth
for the local case.
"""
from __future__ import annotations

import ast
import builtins as _builtins
import contextlib
import io
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import get_settings

__all__ = [
    "SandboxError",
    "SandboxDisabled",
    "SandboxResult",
    "static_check",
    "run_user_code",
    "SAFE_BUILTIN_NAMES",
]


class SandboxError(RuntimeError):
    """Raised when user code is rejected by the static check or fails to run."""


class SandboxDisabled(SandboxError):
    """Raised when execution is attempted while code execution is turned off."""


# Names that must never be referenced or called by user source. Combined with the
# blanket ban on double-underscore names below, this removes the usual escapes.
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "open", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "memoryview", "help", "exit", "quit", "copyright", "credits", "license",
})

# AST node types that have no place in a strategy snippet.
_FORBIDDEN_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)

# The only builtins the sandboxed namespace exposes. Notably absent:
# __import__, open, eval, exec, compile, getattr/setattr, input.
SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "int", "isinstance", "len", "list", "map",
    "max", "min", "print", "range", "repr", "reversed", "round", "set",
    "slice", "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
)


def _safe_builtins() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in SAFE_BUILTIN_NAMES:
        if hasattr(_builtins, name):
            out[name] = getattr(_builtins, name)
    return out


@dataclass
class SandboxResult:
    """Outcome of a sandboxed run."""

    result: Any = None
    stdout: str = ""
    variables: dict[str, Any] = field(default_factory=dict)


def static_check(code: str) -> None:
    """Reject code that could break out of the sandbox, before it runs.

    Raises :class:`SandboxError` on the first problem found.
    """
    if "__" in code:
        raise SandboxError(
            "double-underscore names are not allowed — they enable sandbox "
            "escapes (e.g. __class__, __globals__, __import__)."
        )
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SandboxError(f"syntax error: {exc.msg} (line {exc.lineno})") from exc

    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODES):
            raise SandboxError(
                f"`{type(node).__name__.lower()}` is not allowed here — the "
                "libraries you need (pandas, numpy, the platform functions) are "
                "already provided, so you never need to import."
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SandboxError("dunder attribute access is not allowed.")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise SandboxError(f"use of `{node.id}` is not allowed.")


def run_user_code(
    code: str,
    context: dict[str, Any] | None = None,
    *,
    result_var: str = "result",
    force: bool | None = None,
) -> SandboxResult:
    """Run ``code`` in a restricted namespace and return its result.

    ``context`` objects (a DataFrame, ``pd``/``np``, platform functions) are
    injected so the code needs no imports. The value of ``result_var`` after the
    run is returned as :attr:`SandboxResult.result`; anything printed is captured
    in :attr:`SandboxResult.stdout`.

    Execution requires opt-in: it runs only when ``force`` is True, or when
    ``force`` is None and ``ALGO_ALLOW_CODE_EXEC`` is set. Otherwise it raises
    :class:`SandboxDisabled`.
    """
    enabled = get_settings().allow_code_exec if force is None else force
    if not enabled:
        raise SandboxDisabled(
            "Code execution is disabled on this deployment. Set "
            "ALGO_ALLOW_CODE_EXEC=true (local / Docker) to run edited code."
        )

    static_check(code)

    ns: dict[str, Any] = {"__builtins__": _safe_builtins()}
    injected = dict(context or {})
    ns.update(injected)

    buf = io.StringIO()
    try:
        compiled = compile(code, "<strategy>", "exec")
        with contextlib.redirect_stdout(buf):
            exec(compiled, ns)  # noqa: S102 - restricted namespace, opt-in only
    except SandboxError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the user
        raise SandboxError(f"{type(exc).__name__}: {exc}") from exc

    user_vars = {
        k: v for k, v in ns.items()
        if not k.startswith("_") and k not in injected
    }
    return SandboxResult(
        result=ns.get(result_var),
        stdout=buf.getvalue(),
        variables=user_vars,
    )
