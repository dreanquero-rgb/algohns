"""Tests for the opt-in code-execution sandbox.

The sandbox runs user-edited strategy/backtest code, so its guard rails are the
part that most needs pinning: it must stay OFF unless explicitly enabled, and
even when enabled it must reject the ways sandboxed CPython is usually escaped
(imports, dunder access, eval/exec/open/getattr).
"""
from __future__ import annotations

import pandas as pd
import pytest

from algohns.modules.code_sandbox import (
    SandboxDisabled,
    SandboxError,
    run_user_code,
    static_check,
)


# ------------------------------------------------------------- opt-in gate
class TestExecutionGate:
    def test_disabled_by_default_raises(self):
        with pytest.raises(SandboxDisabled):
            run_user_code("result = 1", force=False)

    def test_force_true_runs(self):
        out = run_user_code("result = 2 + 2", force=True)
        assert out.result == 4

    def test_honours_settings_when_force_is_none(self, monkeypatch):
        from types import SimpleNamespace

        import algohns.modules.code_sandbox as cs
        monkeypatch.setattr(cs, "get_settings",
                            lambda: SimpleNamespace(allow_code_exec=False))
        with pytest.raises(SandboxDisabled):
            run_user_code("result = 1")
        monkeypatch.setattr(cs, "get_settings",
                            lambda: SimpleNamespace(allow_code_exec=True))
        assert run_user_code("result = 7").result == 7


# ----------------------------------------------------------- static guards
class TestStaticCheck:
    @pytest.mark.parametrize("code", [
        "import os",
        "from os import system",
        "x = ().__class__",
        "x = ().__class__.__bases__",
        "eval('1+1')",
        "exec('x=1')",
        "open('/etc/passwd')",
        "getattr(x, 'y')",
        "__import__('os')",
        "compile('1', '<s>', 'eval')",
    ])
    def test_rejects_dangerous_code(self, code):
        with pytest.raises(SandboxError):
            static_check(code)

    def test_rejects_at_run_time_too(self):
        with pytest.raises(SandboxError):
            run_user_code("import os", force=True)

    def test_syntax_error_is_a_sandbox_error(self):
        with pytest.raises(SandboxError):
            run_user_code("result = (1 + ", force=True)

    @pytest.mark.parametrize("code", [
        "result = sum([1, 2, 3])",
        "result = sorted([3, 1, 2])",
        "result = [x * 2 for x in range(3)]",
        "f = lambda a: a + 1\nresult = f(4)",
        "def g(n):\n    return n * n\nresult = g(5)",
    ])
    def test_allows_ordinary_code(self, code):
        static_check(code)  # should not raise
        assert run_user_code(code, force=True).result is not None


# --------------------------------------------------------- run behaviour
class TestRunBehaviour:
    def test_no_import_available(self):
        # __import__ is not in the sandbox builtins, so even without the static
        # check an import expression cannot resolve a module.
        with pytest.raises(SandboxError):
            run_user_code("import math\nresult = math.pi", force=True)

    def test_injected_context_is_available(self):
        df = pd.DataFrame({"ticker": ["A", "B"], "beta": [0.5, 1.5]})

        def pick(frame):
            return frame[frame["beta"] < 1.0]

        out = run_user_code(
            "result = pick(universe)",
            {"universe": df, "pick": pick},
            force=True,
        )
        assert list(out.result["ticker"]) == ["A"]

    def test_stdout_is_captured(self):
        out = run_user_code("print('hello')\nresult = 1", force=True)
        assert "hello" in out.stdout

    def test_missing_result_var_is_none(self):
        out = run_user_code("x = 5", force=True)
        assert out.result is None

    def test_custom_result_var(self):
        out = run_user_code("weights = {'SPY': 1.0}", force=True,
                            result_var="weights")
        assert out.result == {"SPY": 1.0}

    def test_runtime_error_wrapped(self):
        with pytest.raises(SandboxError, match="ZeroDivisionError"):
            run_user_code("result = 1 / 0", force=True)

    def test_builtins_are_curated(self):
        # A builtin that is neither on the allow-list nor forbidden by name is
        # simply absent from the namespace — proof the map is curated, not the
        # full builtins. `hash` is a good witness: harmless, but not injected.
        with pytest.raises(SandboxError, match="NameError"):
            run_user_code("result = hash(1)", force=True)


# ----------------------------------------- end-to-end with platform funcs
class TestStrategyIntegration:
    def test_screen_and_weight_via_sandbox(self):
        from algohns.modules import strategy_lab as sl

        universe = sl.demo_universe()
        if universe.empty:
            pytest.skip("world universe not bundled")
        code = (
            "criteria = ScreenCriteria(beta_max=1.2, max_positions=5)\n"
            "picks = screen_universe(universe, criteria)\n"
            "weights = build_weights(picks, 'equal')\n"
        )
        out = run_user_code(
            code,
            {
                "universe": universe,
                "screen_universe": sl.screen_universe,
                "build_weights": sl.build_weights,
                "ScreenCriteria": sl.ScreenCriteria,
            },
            result_var="weights",
            force=True,
        )
        assert out.result
        assert sum(out.result.values()) == pytest.approx(1.0)
        assert len(out.result) <= 5
