"""Tests for Review Loop v2 — Plan 1 (derived ledger verdict)."""

import importlib.util
from pathlib import Path

import yaml


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


def test_effective_status(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    # pre-shipped lifecycle is unchanged
    assert sl.effective_status({"status": "building"}, None) == "building"
    # shipped + no verdict -> awaiting-prod
    assert sl.effective_status({"status": "shipped"}, None) == "awaiting-prod"
    # shipped + CONFIRMED -> accepted (derived, never stored)
    assert (
        sl.effective_status({"status": "shipped"}, {"verdict": "CONFIRMED"})
        == "accepted"
    )
    # shipped + REJECTED -> needs-rework
    assert (
        sl.effective_status({"status": "shipped"}, {"verdict": "REJECTED"})
        == "needs-rework"
    )
    # shipped + open needs_human (no verdict) -> needs-human
    assert (
        sl.effective_status({"status": "shipped"}, {"needs_human": "taste"})
        == "needs-human"
    )
    # legacy records already stored as accepted still read as accepted
    assert sl.effective_status({"status": "accepted"}, None) == "accepted"


def test_cmd_verify_derives_from_criteria(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify(
        [
            "100-x",
            "--judge",
            "codex",
            "--evidence",
            "e.json",
            "--criterion",
            "c1=CONFIRMED",
            "--criterion",
            "c2=REJECTED",
        ]
    )
    capsys.readouterr()
    assert rc == 0
    rec = yaml.safe_load((sl._verified_path("100-x")).read_text())
    assert rec["criteria"] == {"c1": "CONFIRMED", "c2": "REJECTED"}
    assert rec["verdict"] == "REJECTED"  # derived, not supplied


def test_cmd_verify_all_confirmed_is_confirmed(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    sl.cmd_verify(
        ["100-x", "--judge", "codex", "--evidence", "e", "--criterion", "c1=CONFIRMED"]
    )
    sl.cmd_verify(
        ["100-x", "--judge", "codex", "--evidence", "e", "--criterion", "c2=CONFIRMED"]
    )
    capsys.readouterr()
    rec = yaml.safe_load((sl._verified_path("100-x")).read_text())
    assert rec["criteria"] == {"c1": "CONFIRMED", "c2": "CONFIRMED"}
    assert rec["verdict"] == "CONFIRMED"


def test_cmd_verify_rejects_bad_criterion_verdict(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify(
        ["100-x", "--judge", "c", "--evidence", "e", "--criterion", "c1=MAYBE"]
    )
    err = capsys.readouterr().err
    assert rc == 1 and "MAYBE" in err


def test_cmd_verify_refuses_disagreeing_positional(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify(
        [
            "100-x",
            "CONFIRMED",
            "--judge",
            "c",
            "--evidence",
            "e",
            "--criterion",
            "c1=REJECTED",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 1 and "refus" in err.lower()


def test_cmd_verify_legacy_positional_still_works(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify(["100-x", "CONFIRMED", "--judge", "c", "--evidence", "e"])
    capsys.readouterr()
    assert rc == 0
    rec = yaml.safe_load((sl._verified_path("100-x")).read_text())
    assert rec["verdict"] == "CONFIRMED"


def test_cmd_set_refuses_accepted(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    sl.cmd_register(["100-x", "--title", "T", "--intent", "I", "--spec-file", "f.md"])
    capsys.readouterr()
    rc = sl.cmd_set(["100-x", "accepted", "--by", "orc"])
    err = capsys.readouterr().err
    assert rc == 1 and "computed-only" in err


def test_cmd_set_other_status_still_works(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    sl.cmd_register(["100-x", "--title", "T", "--intent", "I", "--spec-file", "f.md"])
    capsys.readouterr()
    rc = sl.cmd_set(["100-x", "planned", "--by", "orc"])
    capsys.readouterr()
    assert rc == 0


def _ship(sl, sid, title="X"):
    sl._write_record(
        sl._record_path(sid),
        {
            "spec_id": sid,
            "title": title,
            "status": "shipped",
            "history": [
                {"at": "2026-06-08T00:00:00Z", "status": "shipped", "by": "orc"}
            ],
        },
    )


def test_render_rejected_goes_to_top_needs_rework(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _ship(sl, "100-x", "Broken thing")
    sl._write_verdict(
        sl._verified_path("100-x"),
        {"spec_id": "100-x", "verdict": "REJECTED", "judge": "codex"},
    )
    body = sl.render(sl.load_records(), include_all=False)
    assert "NEEDS-REWORK" in body
    top = body.split("NEEDS-REWORK")[1].split("##")[0]
    assert "100-x" in top  # listed under the NEEDS-REWORK section


def test_render_confirmed_is_accepted_not_awaiting(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _ship(sl, "100-x")
    sl._write_verdict(
        sl._verified_path("100-x"),
        {"spec_id": "100-x", "verdict": "CONFIRMED", "judge": "codex"},
    )
    body = sl.render(sl.load_records(), include_all=True)
    assert "Accepted (1)" in body
    # not sitting in the awaiting-prod bucket
    awaiting = body.split("Awaiting prod-verification")[1].split("##")[0]
    assert "100-x" not in awaiting


def test_render_no_verdict_is_awaiting_prod(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _ship(sl, "100-x")
    body = sl.render(sl.load_records(), include_all=False)
    assert "Awaiting prod-verification (1)" in body
    assert "NEEDS-REWORK" not in body  # nothing rejected
