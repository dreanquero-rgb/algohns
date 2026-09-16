"""Smoke tests: every Streamlit page must execute without raising.

Uses Streamlit's own AppTest harness, which runs each script top to bottom
and records exceptions. HTTP 200 from the dev server proves only that the
HTML shell was served — the Python body runs over the websocket, so a page
can 200 and still be broken. This catches that.
"""
from __future__ import annotations

from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

PLATFORM = Path(__file__).resolve().parent.parent
PAGES = sorted((PLATFORM / "pages").glob("*.py"))


def _run(path: Path, timeout: int = 120):
    app = AppTest.from_file(str(path), default_timeout=timeout)
    app.run()
    return app


def _assert_clean(app, label: str) -> None:
    if app.exception:
        messages = [e.value for e in app.exception]
        pytest.fail(f"{label} ha sollevato: {messages}")


def test_main_app_runs():
    app = _run(PLATFORM / "app.py")
    _assert_clean(app, "app.py")
    assert any("Algohns" in t.value for t in app.title)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_page_runs(page: Path):
    """Each page must render with default widget values."""
    app = _run(page)
    _assert_clean(app, page.name)
    assert app.title, f"{page.name} non ha prodotto un titolo"


def test_all_five_modules_have_a_page():
    assert len(PAGES) == 5, f"attese 5 pagine, trovate {[p.name for p in PAGES]}"


def test_bond_page_computes_a_yield():
    """The bond page must produce real numbers, not just render."""
    app = _run(PLATFORM / "pages" / "1_Bond_Engine.py")
    _assert_clean(app, "bond page")
    labels = [m.label for m in app.metric]
    assert "YTM lordo" in labels
    ytm = next(m for m in app.metric if m.label == "YTM lordo")
    assert ytm.value.endswith("%")
    assert float(ytm.value.rstrip("%")) != 0.0


def test_supply_chain_page_propagates():
    app = _run(PLATFORM / "pages" / "4_Supply_Chain.py")
    _assert_clean(app, "supply chain page")
    labels = [m.label for m in app.metric]
    assert "Moltiplicatore di contagio" in labels
    mult = next(m for m in app.metric if m.label == "Moltiplicatore di contagio")
    assert float(mult.value.rstrip("x")) > 1.0
