"""Tests for spec 402 verdict-correctness gates (R1/R2/R3/R4).

Sandboxes the ledger via DOIT_LEDGER_DIR/DOIT_MIRROR_DIR env overrides and a
per-test module reimport — hermetic, no ~/.claude ledger touched.

R1 — full-declared-set gate: CONFIRMED only when every declared criterion is
     present; omission is not a pass; fail-safe blocks undeclared CONFIRMED.
R2 — typed-evidence gate: observed-data/cron/financial/ui criteria must cite
     evidence whose shape matches the declared type; persisted across calls.
R3 — manifest recency guard (pure fns): manifest_trusted_for_ship /
     manifest_trusted compare written_at against shipped_at.
R4 — external-io/integration-owed: external-io needs a real observed call;
     integration-owed is a valid criterion verdict that blocks spec CONFIRMED.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    spec = importlib.util.spec_from_file_location("spec_ledger_402_verify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(sl, sid, **fields):
    sl.LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    (sl.LEDGER_DIR / f"{sid}.yml").write_text(
        yaml.safe_dump({"spec_id": sid, **fields})
    )


def _vpath(sl, sid):
    return sl.LEDGER_DIR / "verified" / f"{sid}.yml"


def _load_verdict(sl, sid):
    p = _vpath(sl, sid)
    return sl._load_yaml(p) if p.exists() else None


# ── R1 — full-declared-set gate ───────────────────────────────────────────────


def test_r1_partial_map_not_confirmed_names_missing(monkeypatch, tmp_path, capsys):
    """R1-AC1: Declared N=3, supply only c1=CONFIRMED → NOT CONFIRMED; message names c2, c3."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r1-t1", "--judge", "rev",
        "--evidence",
        "alembic_version=genesis_squash_001; SELECT count(*) FROM t → 5 rows",
        "--criteria-count", "3",
        "--criterion", "c1=CONFIRMED",
    ])
    # partial map writes the record but leaves it incomplete (rc=0)
    assert rc == 0
    rec = _load_verdict(sl, "402-r1-t1")
    assert rec is not None, "record should be written even when verdict is incomplete"
    assert rec.get("verdict") != "CONFIRMED", "partial map must not derive CONFIRMED"
    # output message must name the missing declared ids
    out = capsys.readouterr().out
    assert "c2" in out and "c3" in out, (
        f"output should name missing declared criteria c2 and c3; got: {out!r}"
    )
    assert sorted(rec.get("declared_criteria", [])) == ["c1", "c2", "c3"]


def test_r1_not_run_criterion_blocks_confirmed(monkeypatch, tmp_path):
    """R1-AC2: Declared 3, c1=CONFIRMED, c2=CONFIRMED, c3=not-run → NOT CONFIRMED."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r1-t2", "--judge", "rev",
        "--evidence",
        "alembic_version=genesis_squash_001; SELECT count(*) FROM t → 5 rows",
        "--criteria-count", "3",
        "--criterion", "c1=CONFIRMED",
        "--criterion", "c2=CONFIRMED",
        "--criterion", "c3=not-run",
    ])
    assert rc == 0
    rec = _load_verdict(sl, "402-r1-t2")
    assert rec is not None
    assert rec.get("verdict") != "CONFIRMED", "not-run criterion must block CONFIRMED"


def test_r1_all_confirmed_derives_confirmed(monkeypatch, tmp_path):
    """R1-AC3: Declared 3, all CONFIRMED → spec-level CONFIRMED."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r1-t3", "--judge", "rev",
        "--evidence",
        "alembic_version=genesis_squash_001; SELECT count(*) FROM t → 5 rows",
        "--criteria-count", "3",
        "--criterion", "c1=CONFIRMED",
        "--criterion", "c2=CONFIRMED",
        "--criterion", "c3=CONFIRMED",
    ])
    assert rc == 0
    rec = _load_verdict(sl, "402-r1-t3")
    assert rec is not None
    assert rec.get("verdict") == "CONFIRMED"


def test_r1_failsafe_refuses_confirmed_without_declared_set(monkeypatch, tmp_path, capsys):
    """R1-AC4: c1=CONFIRMED with no declared flag and none persisted → rc!=0, nothing written."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r1-t4", "--judge", "rev",
        "--evidence",
        "alembic_version=genesis_squash_001; SELECT count(*) FROM t → 5 rows",
        "--criterion", "c1=CONFIRMED",
    ])
    assert rc != 0, "CONFIRMED without declared set must be refused"
    assert not _vpath(sl, "402-r1-t4").exists(), "no verdict file should be written"
    err = capsys.readouterr().err
    # stderr must instruct the caller to declare the set
    assert "--criteria-count" in err or "--declared-criteria" in err, (
        f"stderr should reference --criteria-count or --declared-criteria; got: {err!r}"
    )


def test_r1_declared_criteria_non_c_ids(monkeypatch, tmp_path):
    """R1-AC5: --declared-criteria with non-c ids (a1,a2) works; CONFIRMED when all present."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r1-t5", "--judge", "rev",
        "--evidence",
        "alembic_version=genesis_squash_001; SELECT count(*) FROM t → 5 rows",
        "--declared-criteria", "a1,a2",
        "--criterion", "a1=CONFIRMED",
        "--criterion", "a2=CONFIRMED",
    ])
    assert rc == 0
    rec = _load_verdict(sl, "402-r1-t5")
    assert rec is not None
    assert rec.get("verdict") == "CONFIRMED"
    assert sorted(rec.get("declared_criteria", [])) == ["a1", "a2"]


# ── R2 — typed-evidence gate ──────────────────────────────────────────────────


def test_r2_observed_data_refused_on_fixture_evidence(monkeypatch, tmp_path):
    """R2-AC1: observed-data CONFIRMED with fixture-only evidence → refused."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r2-t6a", "--judge", "rev",
        "--evidence",
        "pytest: in-memory DB has 42 records; fixture confirms data loaded correctly",
        "--criteria-count", "1",
        "--criterion", "c1=CONFIRMED",
        "--observed-data-criterion", "c1",
    ])
    assert rc != 0, "observed-data CONFIRMED on fixture evidence must be refused"
    assert not _vpath(sl, "402-r2-t6a").exists()


def test_r2_observed_data_accepted_on_live_pg_evidence(monkeypatch, tmp_path):
    """R2-AC1: observed-data CONFIRMED with live-PG reference (alembic_version + SELECT FROM) → counts."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r2-t6b", "--judge", "rev",
        "--evidence", (
            "alembic_version=genesis_squash_001 (prod); "
            "SELECT count(*) FROM amazon_listing_price_snapshot → 4210 rows "
            "against $SUPABASE_DB_URL"
        ),
        "--criteria-count", "1",
        "--criterion", "c1=CONFIRMED",
        "--observed-data-criterion", "c1",
    ])
    assert rc == 0
    rec = _load_verdict(sl, "402-r2-t6b")
    assert rec is not None
    assert rec.get("verdict") == "CONFIRMED"
    assert "c1" in (rec.get("observed_data_criteria") or [])


def test_r2_persisted_type_gates_subsequent_call(monkeypatch, tmp_path):
    """R2-AC1: type persisted on call 1 still gates CONFIRMED in call 2 (no --criterion-type re-pass)."""
    sl = _load(monkeypatch, tmp_path)
    # Call 1: type c1 as observed-data but record it as not-run (no evidence gate fires)
    rc1 = sl.cmd_verify([
        "402-r2-t7", "--judge", "rev",
        "--evidence", "no live-db observation here",
        "--criteria-count", "1",
        "--criterion", "c1=not-run",
        "--criterion-type", "c1=observed-data",
    ])
    assert rc1 == 0, "not-run does not trigger the evidence gate"
    rec = _load_verdict(sl, "402-r2-t7")
    assert rec is not None
    assert (rec.get("criterion_types") or {}).get("c1") == "observed-data"
    # Call 2: confirm c1 WITHOUT re-passing the type flag — persisted type must gate
    rc2 = sl.cmd_verify([
        "402-r2-t7", "--judge", "rev",
        "--evidence", "UI screenshot: button clicked, table shows 5 rows",
        "--criterion", "c1=CONFIRMED",
    ])
    assert rc2 != 0, "persisted observed-data type must refuse non-live-db evidence"


def test_r2_cron_refused_on_non_cron_evidence(monkeypatch, tmp_path):
    """R2-AC1: cron-typed criterion CONFIRMED with non-cron evidence → refused."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r2-t8a", "--judge", "rev",
        "--evidence", "UI screenshot: button clicked; label changed as expected",
        "--criteria-count", "1",
        "--criterion", "c1=CONFIRMED",
        "--criterion-type", "c1=cron",
    ])
    assert rc != 0, "cron CONFIRMED on non-cron evidence must be refused"
    assert not _vpath(sl, "402-r2-t8a").exists()


def test_r2_cron_accepted_on_cron_evidence(monkeypatch, tmp_path):
    """R2-AC1: cron-typed criterion CONFIRMED with cron_runs row evidence → counts."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r2-t8b", "--judge", "rev",
        "--evidence", "cron_runs row: fired at 2026-07-07T03:30:00Z, exit_code 0",
        "--criteria-count", "1",
        "--criterion", "c1=CONFIRMED",
        "--criterion-type", "c1=cron",
    ])
    assert rc == 0
    rec = _load_verdict(sl, "402-r2-t8b")
    assert rec is not None
    assert rec.get("verdict") == "CONFIRMED"


# ── R4 — external-io / integration-owed ──────────────────────────────────────


def test_r4_external_io_refused_on_stub_evidence(monkeypatch, tmp_path):
    """R4-AC1: external-io CONFIRMED with stub/mock/pytest-only evidence → refused."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r4-t9a", "--judge", "rev",
        "--evidence", "pytest stubbed suite, mock returned 42 candidates",
        "--criteria-count", "1",
        "--criterion", "c1=CONFIRMED",
        "--external-io-criterion", "c1",
    ])
    assert rc != 0, "external-io CONFIRMED on stub evidence must be refused"
    assert not _vpath(sl, "402-r4-t9a").exists()


def test_r4_external_io_accepted_on_real_call_evidence(monkeypatch, tmp_path):
    """R4-AC1: external-io CONFIRMED with real observed call → counts."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r4-t9b", "--judge", "rev",
        "--evidence",
        "real call to Keepa /query, status 200, api returned 12 candidates",
        "--criteria-count", "1",
        "--criterion", "c1=CONFIRMED",
        "--external-io-criterion", "c1",
    ])
    assert rc == 0
    rec = _load_verdict(sl, "402-r4-t9b")
    assert rec is not None
    assert rec.get("verdict") == "CONFIRMED"


def test_r4_integration_owed_in_valid_criterion_verdict(monkeypatch, tmp_path):
    """R4-AC1: 'integration-owed' is a member of VALID_CRITERION_VERDICT."""
    sl = _load(monkeypatch, tmp_path)
    assert "integration-owed" in sl.VALID_CRITERION_VERDICT


def test_r4_integration_owed_blocks_spec_confirmed(monkeypatch, tmp_path):
    """R4-AC1: declared {c1,c2}, c1=CONFIRMED, c2=integration-owed → spec NOT CONFIRMED."""
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify([
        "402-r4-t10", "--judge", "rev",
        "--evidence",
        "alembic_version=genesis_squash_001; SELECT count(*) FROM t → 5 rows",
        "--declared-criteria", "c1,c2",
        "--criterion", "c1=CONFIRMED",
        "--criterion", "c2=integration-owed",
    ])
    assert rc == 0
    rec = _load_verdict(sl, "402-r4-t10")
    assert rec is not None
    assert rec.get("verdict") != "CONFIRMED", (
        "integration-owed must block spec-level CONFIRMED"
    )


# ── R3 — manifest recency guard (pure fns) ────────────────────────────────────


def test_r3_manifest_trusted_for_ship_stale(monkeypatch, tmp_path):
    """R3-AC1: written_at predates shipped_at → False (stale manifest)."""
    sl = _load(monkeypatch, tmp_path)
    result = sl.manifest_trusted_for_ship(
        "2026-07-06T10:00:00Z", "2026-07-07T04:56:00Z"
    )
    assert result is False


def test_r3_manifest_trusted_for_ship_fresh(monkeypatch, tmp_path):
    """R3-AC1: written_at after shipped_at → True; written at same time → True."""
    sl = _load(monkeypatch, tmp_path)
    assert sl.manifest_trusted_for_ship(
        "2026-07-08T10:00:00Z", "2026-07-07T04:56:00Z"
    ) is True
    assert sl.manifest_trusted_for_ship(
        "2026-07-07T04:56:00Z", "2026-07-07T04:56:00Z"
    ) is True, "written at exact ship time should be trusted"


def test_r3_manifest_trusted_for_ship_none_written_at(monkeypatch, tmp_path):
    """R3-AC1: written_at None → False."""
    sl = _load(monkeypatch, tmp_path)
    assert sl.manifest_trusted_for_ship(None, "2026-07-07T04:56:00Z") is False


def test_r3_manifest_trusted_for_ship_unparseable_written_at(monkeypatch, tmp_path):
    """R3-AC1: unparseable written_at → False."""
    sl = _load(monkeypatch, tmp_path)
    assert sl.manifest_trusted_for_ship("not-a-date", "2026-07-07T04:56:00Z") is False


def test_r3_manifest_trusted_for_ship_no_shipped_at(monkeypatch, tmp_path):
    """R3-AC1: shipped_at None → True (nothing to be stale against)."""
    sl = _load(monkeypatch, tmp_path)
    assert sl.manifest_trusted_for_ship("2026-07-06T10:00:00Z", None) is True


def test_r3_manifest_trusted_delegates(monkeypatch, tmp_path):
    """R3-AC1: manifest_trusted reads written_at from manifest dict and delegates correctly."""
    sl = _load(monkeypatch, tmp_path)
    assert sl.manifest_trusted(
        {"written_at": "2026-07-08T10:00:00Z"}, "2026-07-07T04:56:00Z"
    ) is True
    assert sl.manifest_trusted(
        {"written_at": "2026-07-06T10:00:00Z"}, "2026-07-07T04:56:00Z"
    ) is False
    # missing written_at → False
    assert sl.manifest_trusted({}, "2026-07-07T04:56:00Z") is False
    # None manifest dict → False
    assert sl.manifest_trusted(None, "2026-07-07T04:56:00Z") is False


@pytest.mark.skip(
    reason="requires live SSH to prod server (DEPLOY_SERVER unreachable in CI); "
    "run manually with a reachable host"
)
def test_r3_deploy_manifest_emits_master_committed_at(tmp_path):
    """R3 best-effort: write_deploy_manifest.sh emits a non-empty master_committed_at.

    This fires the script with DEPLOY_SERVER=root@127.0.0.1 (will fail to connect /
    not match — that's expected). If the JSON is written anyway, we assert the field.
    """
    import json
    import os
    import subprocess
    from pathlib import Path as _Path

    manifest_path = str(tmp_path / "manifest.json")
    subprocess.run(
        ["bash", "scripts/write_deploy_manifest.sh"],
        env={
            **os.environ,
            "DEPLOY_MANIFEST_PATH": manifest_path,
            "DEPLOY_SERVER": "root@127.0.0.1",
        },
        capture_output=True,
        timeout=30,
    )
    p = _Path(manifest_path)
    if p.exists():
        data = json.loads(p.read_text())
        val = data.get("master_committed_at", "")
        assert val, "master_committed_at must be a non-empty string"
        assert "T" in val or "-" in val, f"expected ISO date-time, got: {val!r}"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
