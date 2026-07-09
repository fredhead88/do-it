"""Hermetic tests for spec 300: gating lifecycle state in the spec ledger.

AC2: gating is a valid non-terminal status, renders in a DISTINCT bucket
ordered AFTER building and BEFORE ready/Ready-to-merge in the OUTSTANDING mirror.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    spec = importlib.util.spec_from_file_location("spec_ledger_gating_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(sl, sid, **fields):
    sl.LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    (sl.LEDGER_DIR / f"{sid}.yml").write_text(
        yaml.safe_dump({"spec_id": sid, **fields})
    )


# ---------------------------------------------------------------------------
# 1. gating in VALID_STATUS and OUTSTANDING_STATUSES
# ---------------------------------------------------------------------------


def test_gating_in_valid_status(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    assert "gating" in sl.VALID_STATUS


def test_gating_in_outstanding_statuses(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    assert "gating" in sl.OUTSTANDING_STATUSES


# ---------------------------------------------------------------------------
# 2. --check exits 0 with a gating record present (round-trip validation)
# ---------------------------------------------------------------------------


def test_check_passes_with_gating_record(monkeypatch, tmp_path):
    """A valid gating record must pass --check (exit 0)."""
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / "300-gating-test.yml").write_text(
        yaml.safe_dump({
            "spec_id": "300-gating-test",
            "title": "Gating test spec",
            "status": "gating",
            "intent": "test gating state",
            "spec_file": "docs/do-it/specs/300-test.md",
        })
    )
    env = {
        "DOIT_LEDGER_DIR": str(ledger_dir),
        "DOIT_MIRROR_DIR": str(tmp_path / "mirror"),
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        env={**__import__("os").environ, **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"--check failed with gating record.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_validate_accepts_gating_record(monkeypatch, tmp_path):
    """validate() returns no errors for a gating record (in-process round-trip)."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "300-validate", title="Validate gating", status="gating")
    errs = sl.validate(sl.load_records())
    assert errs == [], f"Expected no errors, got: {errs}"


# ---------------------------------------------------------------------------
# 3. Renderer: gating record appears in gating-specific section, ordered
#    AFTER building section and BEFORE ready/"Ready to merge" section.
# ---------------------------------------------------------------------------


def test_gating_renders_in_distinct_section(monkeypatch, tmp_path):
    """A gating record must appear in the Gating section, not in Outstanding."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "300-gating", title="Gating spec", status="gating")
    body = sl.render(sl.load_records(), include_all=False)

    assert "300-gating" in body, "gating record id must appear in output"
    assert "Gating" in body, "output must contain a Gating section header"
    assert "GATING" in body, "output must contain GATING marker"

    # Must NOT appear in the generic Outstanding bucket
    outstanding_section = body.split("## Outstanding")[1].split("## ")[0]
    assert "300-gating" not in outstanding_section, (
        "gating record must NOT appear in the generic Outstanding bucket"
    )


def test_gating_section_after_building_before_ready(monkeypatch, tmp_path):
    """Section order: building content → Gating section → Ready to merge section."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "301-building", title="Building spec", status="building")
    _write(sl, "300-gating", title="Gating spec", status="gating")
    _write(
        sl, "302-ready", title="Ready spec", status="ready",
        branch="feat/302-ready", ready_sha="deadbeefdeadbeef",
        claimed_by="builder-pane-0", claimed_at="2026-07-01T10:00:00Z",
        worktree="/tmp/wt-302", retry_count=0,
        writes=["api/app/lib/"],
    )
    body = sl.render(sl.load_records(), include_all=False)

    # All three records must appear somewhere
    assert "301-building" in body
    assert "300-gating" in body
    assert "302-ready" in body

    # Locate the canonical section headers
    assert "## Outstanding" in body, "Outstanding section must exist"
    assert "Gating" in body, "Gating section must exist"
    assert "Ready to merge" in body, "Ready to merge section must exist"

    idx_building_record = body.index("301-building")
    idx_gating_section = body.index("Gating")
    idx_ready_section = body.index("Ready to merge")
    idx_gating_record = body.index("300-gating")
    idx_ready_record = body.index("302-ready")

    # building record appears before the Gating section header
    assert idx_building_record < idx_gating_section, (
        "building record must come before the Gating section header"
    )
    # Gating section header appears before Ready to merge section header
    assert idx_gating_section < idx_ready_section, (
        "Gating section must come before Ready to merge section"
    )
    # gating record appears before ready record
    assert idx_gating_record < idx_ready_record, (
        "gating record must appear before the ready record in the output"
    )


# ---------------------------------------------------------------------------
# 4. cmd_set accepts building→gating and gating→ready transitions
# ---------------------------------------------------------------------------


def test_cmd_set_building_to_gating(monkeypatch, tmp_path):
    """building → gating transition is accepted by cmd_set."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl, "300-transition", title="Transition test", status="building",
        history=[{"at": "2026-07-01T08:00:00Z", "status": "building", "by": "builder"}],
    )
    rc = sl.cmd_set(["300-transition", "gating", "--by", "builder"])
    assert rc == 0, "cmd_set should accept building→gating"
    rec = yaml.safe_load((sl.LEDGER_DIR / "300-transition.yml").read_text())
    assert rec["status"] == "gating"
    statuses = [h["status"] for h in rec["history"]]
    assert "building" in statuses
    assert "gating" in statuses


def test_cmd_set_gating_to_ready(monkeypatch, tmp_path):
    """gating → ready transition is accepted by cmd_set."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl, "300-grady", title="Gating to ready", status="gating",
        history=[
            {"at": "2026-07-01T08:00:00Z", "status": "building", "by": "builder"},
            {"at": "2026-07-01T09:00:00Z", "status": "gating", "by": "builder"},
        ],
    )
    rc = sl.cmd_set([
        "300-grady", "ready", "--by", "grader",
        "--field", "ready_sha=abc123abc123abc1",
        "--field", "branch=feat/300-grady",
    ])
    assert rc == 0, "cmd_set should accept gating→ready"
    rec = yaml.safe_load((sl.LEDGER_DIR / "300-grady.yml").read_text())
    assert rec["status"] == "ready"
    statuses = [h["status"] for h in rec["history"]]
    assert "gating" in statuses
    assert "ready" in statuses


def test_full_building_gating_ready_sequence(monkeypatch, tmp_path):
    """Full sequence: building → gating → ready records history correctly."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl, "300-full", title="Full gating sequence", status="building",
        history=[{"at": "2026-07-01T07:00:00Z", "status": "building", "by": "builder"}],
    )
    rc = sl.cmd_set(["300-full", "gating", "--by", "builder"])
    assert rc == 0
    rc = sl.cmd_set([
        "300-full", "ready", "--by", "grader",
        "--field", "ready_sha=cafebabecafebabe",
        "--field", "branch=feat/300-full",
    ])
    assert rc == 0
    rec = yaml.safe_load((sl.LEDGER_DIR / "300-full.yml").read_text())
    assert rec["status"] == "ready"
    statuses = [h["status"] for h in rec["history"]]
    assert statuses == ["building", "gating", "ready"]
