"""Tests for Review Loop v2 — Plan 1 (derived ledger verdict)."""

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    spec = importlib.util.spec_from_file_location("spec_ledger_rlv2", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_spec_verdict(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    assert sl.resolve_spec_verdict({}) is None
    assert sl.resolve_spec_verdict({"c1": "CONFIRMED"}) == "CONFIRMED"
    assert (
        sl.resolve_spec_verdict({"c1": "CONFIRMED", "c2": "CONFIRMED"}) == "CONFIRMED"
    )
    assert sl.resolve_spec_verdict({"c1": "CONFIRMED", "c2": "REJECTED"}) == "REJECTED"
    # not-applicable is excluded from the all-pass test
    assert (
        sl.resolve_spec_verdict({"c1": "CONFIRMED", "c2": "not-applicable"})
        == "CONFIRMED"
    )
    # a spec of only not-applicable has nothing observable -> incomplete
    assert sl.resolve_spec_verdict({"c1": "not-applicable"}) is None
    # not-run is incomplete, not a pass
    assert sl.resolve_spec_verdict({"c1": "CONFIRMED", "c2": "not-run"}) is None
    # a single REJECTED dominates everything
    assert sl.resolve_spec_verdict({"c1": "not-run", "c2": "REJECTED"}) == "REJECTED"
