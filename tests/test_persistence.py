"""Tests for durable local state (risk profile / active strategy).

The point of this layer is that a filled profile and a chosen strategy survive
a restart, so the round-trip and the degrade-to-None failure modes are what
matter. A temp cache dir is injected so tests never touch the repo's cache.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import algohns.core.persistence as ps


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "get_settings",
                        lambda: SimpleNamespace(cache_dir=tmp_path))


def test_roundtrip(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert ps.load_state("risk_profile") is None
    assert ps.save_state("risk_profile", {"answers": {"horizon": 2}}) is True
    assert ps.load_state("risk_profile") == {"answers": {"horizon": 2}}


def test_clear(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    ps.save_state("active_strategy", {"weights": {"SPY": 1.0}})
    ps.clear_state("active_strategy")
    assert ps.load_state("active_strategy") is None


def test_clear_missing_is_noop(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    ps.clear_state("never_written")  # must not raise


def test_non_dict_payload_loads_as_none(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    ps._path("weird").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert ps.load_state("weird") is None


def test_corrupt_file_loads_as_none(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    ps._path("broken").write_text("{not valid json", encoding="utf-8")
    assert ps.load_state("broken") is None


def test_name_is_sanitised_consistently(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    ps.save_state("a/b c!", {"ok": True})
    # Same (sanitised) name resolves to the same file.
    assert ps.load_state("a/b c!") == {"ok": True}
    # No path separator leaked into the filename.
    assert "/" not in ps._path("a/b c!").name


def test_survives_reload(monkeypatch, tmp_path):
    """A fresh 'process' (new call) reads what a previous one wrote."""
    _use_tmp(monkeypatch, tmp_path)
    ps.save_state("risk_profile", {"answers": {"horizon": 3}, "preferences": ["Equity"]})
    # Simulate a restart: nothing in memory, only the file on disk.
    again = ps.load_state("risk_profile")
    assert again == {"answers": {"horizon": 3}, "preferences": ["Equity"]}
