"""Tests for the DO-IT spec ledger renderer (bus-based model)."""

import importlib.util
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    spec = importlib.util.spec_from_file_location("spec_ledger_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(sl, sid, **fields):
    sl.LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    (sl.LEDGER_DIR / f"{sid}.yml").write_text(
        yaml.safe_dump({"spec_id": sid, **fields})
    )


def test_ledger_dir_from_env(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    assert sl.LEDGER_DIR == tmp_path / "ledger"
    assert sl.OUTSTANDING_MD == tmp_path / "mirror" / "OUTSTANDING.md"


def test_new_statuses_valid(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    assert {"bounced", "retired"} <= sl.VALID_STATUS


def test_bounced_is_loud(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl, "003-foo", title="Foo", status="bounced", bounce_reason="target path gone"
    )
    body = sl.render(sl.load_records(), include_all=False)
    assert "003-foo" in body and "BOUNCED" in body and "target path gone" in body


def test_bounced_requires_reason(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "004-bar", title="Bar", status="bounced")
    assert any("bounce_reason" in e for e in sl.validate(sl.load_records()))


def test_registered_renders_outstanding(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "005-baz", title="Baz", status="registered")
    body = sl.render(sl.load_records(), include_all=False)
    assert "005-baz" in body and "registered" in body


# ---------------------------------------------------------------------------
# A1: Verifier-owned verified/ namespace + verify subcommand
# ---------------------------------------------------------------------------


def _verified_dir(sl):
    return sl.LEDGER_DIR / "verified"


def test_verify_writes_to_verified_namespace(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "010-foo", title="Foo", status="shipped")  # builder's file
    rc = sl.cmd_verify(
        [
            "010-foo",
            "CONFIRMED",
            "--judge",
            "codex",
            "--evidence",
            "runs/2026-06-07/ev-010.json",
        ]
    )
    assert rc == 0
    vf = _verified_dir(sl) / "010-foo.yml"
    assert vf.exists()
    data = yaml.safe_load(vf.read_text())
    assert data["verdict"] == "CONFIRMED"
    assert data["judge"] == "codex"
    assert data["evidence_ref"] == "runs/2026-06-07/ev-010.json"
    assert data["history"][-1]["verdict"] == "CONFIRMED"


def test_verify_invisible_to_builder_load(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "011-bar", title="Bar", status="shipped")
    sl.cmd_verify(["011-bar", "CONFIRMED", "--judge", "codex", "--evidence", "x"])
    # builder's load_records must NOT pick up the verified/ subdir as a record
    ids = {r["spec_id"] for r in sl.load_records()}
    assert ids == {"011-bar"}  # not "011-bar" twice, not a stray verified record


def test_set_cannot_clobber_verdict(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "012-baz",
        title="Baz",
        status="shipped",
        history=[{"at": "2026-06-07T00:00:00Z", "status": "shipped", "by": "orc"}],
    )
    sl.cmd_verify(["012-baz", "CONFIRMED", "--judge", "codex", "--evidence", "x"])
    # builder advances its own file afterwards
    sl.cmd_set(["012-baz", "shipped", "--by", "orc"])
    # verdict file is untouched
    data = yaml.safe_load((_verified_dir(sl) / "012-baz.yml").read_text())
    assert data["verdict"] == "CONFIRMED"


# ---------------------------------------------------------------------------
# A2: Advisory lock on every record write
# ---------------------------------------------------------------------------


def test_concurrent_set_serializes(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "013-q",
        title="Q",
        status="registered",
        history=[{"at": "2026-06-07T00:00:00Z", "status": "registered", "by": "h"}],
    )
    import threading

    def worker(status):
        sl.cmd_set(["013-q", status, "--by", "orc"])

    ts = [threading.Thread(target=worker, args=(s,)) for s in ("building", "shipped")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rec = yaml.safe_load((sl.LEDGER_DIR / "013-q.yml").read_text())
    # both history entries survived (no lost update); final status is one of the two
    statuses = [h["status"] for h in rec["history"]]
    assert "building" in statuses and "shipped" in statuses


# ---------------------------------------------------------------------------
# A3: render() derives done-ness from the verdict namespace
# ---------------------------------------------------------------------------


def test_render_marks_verified_from_verdict_file(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "014-v", title="Vee", status="shipped")
    sl.cmd_verify(["014-v", "CONFIRMED", "--judge", "codex", "--evidence", "x"])
    body = sl.render(sl.load_records(), include_all=True)
    # CONFIRMED verdict -> effective_status == accepted -> appears in Accepted section
    assert "014-v" in body and "Accepted (1)" in body


def test_contract_bump_is_revalidation_not_regression(monkeypatch, tmp_path):
    # F5: a $-asserting verdict held under contract v1; bumping the contract to v2
    # must flip it to needs-revalidation, NEVER a false regression/REJECTED.
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "201-cash-bridge", title="Cash Bridge", status="shipped")
    sl.cmd_verify(
        [
            "201-cash-bridge",
            "CONFIRMED",
            "--judge",
            "rev",
            "--evidence",
            "x",
            "--contract-version",
            "v1",
        ]
    )
    # same contract -> accepted, not revalidation
    monkeypatch.setenv("CONTRACT_VERSION", "v1")
    body = sl.render(sl.load_records(), include_all=True)
    assert "201-cash-bridge" in body and "NEEDS-REVALIDATION" not in body
    # contract bumped -> needs-revalidation, and NOT a regression
    monkeypatch.setenv("CONTRACT_VERSION", "v2")
    body = sl.render(sl.load_records(), include_all=True)
    assert "NEEDS-REVALIDATION" in body
    assert "REJECTED" not in body  # never a false regression


def test_render_rejected_is_outstanding(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "015-r", title="Are", status="shipped")
    sl.cmd_verify(["015-r", "REJECTED", "--judge", "codex", "--evidence", "x"])
    body = sl.render(sl.load_records(), include_all=False)
    # a rejected verdict must surface, not sit silently in "shipped"
    assert "015-r" in body and "REJECTED" in body.upper()


# ---------------------------------------------------------------------------
# A4: Observable-criterion --check heuristic
# ---------------------------------------------------------------------------


def test_presence_phrased_criterion_warns(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "016-p",
        title="P",
        status="registered",
        acceptance_criteria=["A metric-picker component exists on the card"],
    )
    warns = sl.observable_warnings(sl.load_records())
    assert any("016-p" in w and "metric-picker" in w for w in warns)


def test_action_observation_criterion_ok(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "017-a",
        title="A",
        status="registered",
        acceptance_criteria=[
            "Given Overview, when the user clicks the card metric menu, "
            "then the displayed metric label changes"
        ],
    )
    assert sl.observable_warnings(sl.load_records()) == []
