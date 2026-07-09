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
    # shipped ∧ CONFIRMED derives `accepted` (verdict is computed, not stored as
    # the literal "verified" since the v3.4 derived-verdict adoption).
    assert "014-v" in body and "Accepted" in body


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
    # same contract → accepted, not revalidation
    monkeypatch.setenv("CONTRACT_VERSION", "v1")
    body = sl.render(sl.load_records(), include_all=True)
    assert "201-cash-bridge" in body and "NEEDS-REVALIDATION" not in body
    # contract bumped → needs-revalidation, and NOT a regression
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


# ---------------------------------------------------------------------------
# Spec 168 — Derived-Verdict Guard: a spec-level CONFIRMED cannot override a
# REJECTED criterion, regardless of which judge wrote the spec-level stamp.
# ---------------------------------------------------------------------------


def _write_verdict_raw(sl, sid, **fields):
    """Write a verified/ record by hand — simulates the `blind-closeout` grader
    that stamped `verdict: CONFIRMED` directly on top of rev's REJECTED criteria
    (the live 150/133 corruption)."""
    vdir = sl.LEDGER_DIR / "verified"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{sid}.yml").write_text(yaml.safe_dump({"spec_id": sid, **fields}))


def test_rejected_criterion_blocks_accepted_despite_spec_level_confirmed(
    monkeypatch, tmp_path
):
    """R1 — the live 150 shape: criteria sc1+br1 REJECTED, stored spec-level
    verdict CONFIRMED from blind-closeout. Must NOT derive `accepted`."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "150-cash-bridge", title="Cash Bridge", status="shipped")
    _write_verdict_raw(
        sl,
        "150-cash-bridge",
        criteria={"sc1": "REJECTED", "br1": "REJECTED"},
        verdict="CONFIRMED",
        judge="blind-closeout",
        evidence_ref="live Vercel 3ae416a7",
    )
    verdicts = sl.load_verdicts()
    rec = sl.load_records()[0]
    eff = sl.effective_status(rec, verdicts.get("150-cash-bridge"))
    assert eff == "needs-rework", eff
    body = sl.render(sl.load_records(), include_all=True)
    assert "150-cash-bridge" in body
    assert "NEEDS-REWORK" in body
    # must NOT appear in the Accepted list
    accepted_section = body.split("## Accepted")[-1]
    assert "150-cash-bridge" not in accepted_section


def test_partial_red_133_shape_blocks_accepted(monkeypatch, tmp_path):
    """R1/R4 — the 133/QurLife shape: c0..c8 all CONFIRMED except c7 REJECTED,
    stored spec-level CONFIRMED. The single red gate must keep it not-accepted."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "133-qurlife", title="QurLife portal", status="shipped")
    crit = {f"c{i}": "CONFIRMED" for i in range(9)}
    crit["c7"] = "REJECTED"
    _write_verdict_raw(
        sl, "133-qurlife", criteria=crit, verdict="CONFIRMED", judge="blind-closeout"
    )
    rec = sl.load_records()[0]
    eff = sl.effective_status(rec, sl.load_verdicts().get("133-qurlife"))
    assert eff == "needs-rework", eff


def test_all_green_criteria_still_derives_confirmed(monkeypatch, tmp_path):
    """R1 no-regression — every observable criterion CONFIRMED derives accepted."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "200-ok", title="OK", status="shipped")
    sl.cmd_verify(
        [
            "200-ok",
            "--judge",
            "rev",
            "--evidence",
            "x",
            "--declared-criteria",
            "a1,a2",
            "--criterion",
            "a1=CONFIRMED",
            "--criterion",
            "a2=CONFIRMED",
        ]
    )
    rec = sl.load_records()[0]
    eff = sl.effective_status(rec, sl.load_verdicts().get("200-ok"))
    assert eff == "accepted", eff


def test_writetime_block_refuses_spec_confirmed_over_rejected_criterion(
    monkeypatch, tmp_path
):
    """R2 — `verify <sid> CONFIRMED` (legacy spec-level path) is REFUSED while a
    criterion stands REJECTED on the record. This is the exact blind-closeout move."""
    sl = _load(monkeypatch, tmp_path)
    # rev first lays down a REJECTED criterion
    sl.cmd_verify(
        ["151-x", "--judge", "rev", "--evidence", "e", "--criterion", "c1=REJECTED"]
    )
    # builder-side grader tries to stamp spec-level CONFIRMED over it
    rc = sl.cmd_verify(
        ["151-x", "CONFIRMED", "--judge", "blind-closeout", "--evidence", "e2"]
    )
    assert rc != 0
    # the stored verdict must NOT have flipped to CONFIRMED
    data = yaml.safe_load((sl.LEDGER_DIR / "verified" / "151-x.yml").read_text())
    assert data["criteria"]["c1"] == "REJECTED"
    assert data.get("verdict") != "CONFIRMED"


def test_writetime_block_allows_per_criterion_confirm_with_evidence(
    monkeypatch, tmp_path
):
    """R2 — the grader keeps its legitimate function: it may CONFIRM individual
    criteria (with per-criterion evidence). A non-contradicting CONFIRMED writes."""
    sl = _load(monkeypatch, tmp_path)
    sl.cmd_verify(
        [
            "152-y",
            "--judge",
            "rev",
            "--evidence",
            "e",
            "--criteria-count",
            "2",
            "--criterion",
            "c1=CONFIRMED",
        ]
    )
    rc = sl.cmd_verify(
        [
            "152-y",
            "--judge",
            "blind-closeout",
            "--evidence",
            "e2",
            "--criteria-count",
            "2",
            "--criterion",
            "c2=CONFIRMED",
        ]
    )
    assert rc == 0
    data = yaml.safe_load((sl.LEDGER_DIR / "verified" / "152-y.yml").read_text())
    assert data["verdict"] == "CONFIRMED"  # both green → derives CONFIRMED


def test_override_path_a_criterion_flip_with_evidence_clears_it(monkeypatch, tmp_path):
    """R3a — flipping the REJECTED criterion to CONFIRMED with criterion-specific
    evidence is a legitimate override and re-derives the spec to accepted."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "153-flip", title="Flip", status="shipped")
    sl.cmd_verify(
        ["153-flip", "--judge", "rev", "--evidence", "e", "--criterion", "c1=REJECTED"]
    )
    assert (
        sl.effective_status(sl.load_records()[0], sl.load_verdicts().get("153-flip"))
        == "needs-rework"
    )
    # the criterion is fixed and re-verified green with its own evidence
    sl.cmd_verify(
        [
            "153-flip",
            "--judge",
            "rev",
            "--evidence",
            "runs/c1-fixed.json",
            "--criteria-count",
            "1",
            "--criterion",
            "c1=CONFIRMED",
        ]
    )
    assert (
        sl.effective_status(sl.load_records()[0], sl.load_verdicts().get("153-flip"))
        == "accepted"
    )


def test_override_path_b_owner_waiver_naming_criterion_and_human_clears_it(
    monkeypatch, tmp_path
):
    """R3b — a recorded owner-waiver that NAMES the criterion AND the human clears
    the red criterion; the spec re-derives to accepted."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "154-waive", title="Waive", status="shipped")
    sl.cmd_verify(
        ["154-waive", "--judge", "rev", "--evidence", "e", "--criterion", "c1=REJECTED"]
    )
    assert (
        sl.effective_status(sl.load_records()[0], sl.load_verdicts().get("154-waive"))
        == "needs-rework"
    )
    rc = sl.cmd_waive(
        [
            "154-waive",
            "--criterion",
            "c1",
            "--human",
            "ephraim",
            "--reason",
            "known data gap, accepted for go-live",
        ]
    )
    assert rc == 0
    assert (
        sl.effective_status(sl.load_records()[0], sl.load_verdicts().get("154-waive"))
        == "accepted"
    )


def test_owner_waiver_missing_human_is_refused(monkeypatch, tmp_path):
    """R3 — a waiver lacking the human name is refused (argparse-required)."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "155-bad", title="Bad", status="shipped")
    sl.cmd_verify(
        ["155-bad", "--judge", "rev", "--evidence", "e", "--criterion", "c1=REJECTED"]
    )
    import pytest

    with pytest.raises(SystemExit):
        sl.cmd_waive(["155-bad", "--criterion", "c1", "--reason", "no human named"])


def test_invalid_waiver_object_does_not_clear_criterion(monkeypatch, tmp_path):
    """R1/R3 — a hand-planted waiver missing the human (or naming the wrong
    criterion) must NOT validate; the red criterion still blocks accepted."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "156-fake", title="Fake", status="shipped")
    _write_verdict_raw(
        sl,
        "156-fake",
        criteria={"c1": "REJECTED"},
        waivers={"c1": {"criterion": "c1"}},  # no human
        verdict="CONFIRMED",
        judge="blind-closeout",
    )
    eff = sl.effective_status(sl.load_records()[0], sl.load_verdicts().get("156-fake"))
    assert eff == "needs-rework", eff


def test_check_passes_with_corrupt_then_derived_records(monkeypatch, tmp_path):
    """R4 — `--check` stays green: validate() is build-record only; a verified/
    record with a contradicting stamp does not fail --check (it is correctly
    DERIVED at render, not flagged as a malformed build record)."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "150-cash-bridge", title="Cash Bridge", status="shipped")
    _write_verdict_raw(
        sl,
        "150-cash-bridge",
        criteria={"sc1": "REJECTED"},
        verdict="CONFIRMED",
        judge="blind-closeout",
    )
    assert sl.validate(sl.load_records()) == []


# ---------------------------------------------------------------------------
# Spec 168 R5 — Criteria-free non-rev guard (named-case assertions).
# A criteria-free verified/ record authored by a non-rev judge (blind-closeout,
# blind-closeout-opus, codex, …) must NOT derive `accepted`.
# A criteria-free record authored by `rev` STILL derives accepted (rev-legacy
# records are trusted; this is the existing behaviour and must not regress).
# These are synthetic fixtures that replicate the exact shape of the two live
# false-accepts that triggered the spec: 141 (blind-closeout) and 162
# (blind-closeout-opus).
# ---------------------------------------------------------------------------


def test_r5_criteria_free_blind_closeout_does_not_derive_accepted(
    monkeypatch, tmp_path
):
    """Named case: 141-shape — criteria: {} (empty), judge=blind-closeout, verdict=CONFIRMED.
    Must NOT derive accepted."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "141-synthetic", title="141 shape", status="shipped")
    _write_verdict_raw(
        sl,
        "141-synthetic",
        criteria={},  # empty — the exact live 141 shape
        verdict="CONFIRMED",
        judge="blind-closeout",
        evidence_ref="blind-closeout run 2026-06-11",
    )
    rec = sl.load_records()[0]
    eff = sl.effective_status(rec, sl.load_verdicts().get("141-synthetic"))
    assert eff != "accepted", f"141-shape: expected not-accepted but got {eff!r}"
    assert eff == "awaiting-verify", (
        f"141-shape: expected awaiting-verify but got {eff!r}"
    )


def test_r5_criteria_free_blind_closeout_opus_does_not_derive_accepted(
    monkeypatch, tmp_path
):
    """Named case: 162-shape — criteria: {} (empty), judge=blind-closeout-opus, verdict=CONFIRMED.
    Must NOT derive accepted."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "162-synthetic", title="162 shape", status="shipped")
    _write_verdict_raw(
        sl,
        "162-synthetic",
        criteria={},  # empty — the exact live 162 shape
        verdict="CONFIRMED",
        judge="blind-closeout-opus",
        evidence_ref="blind-closeout-opus run 2026-06-10",
    )
    rec = sl.load_records()[0]
    eff = sl.effective_status(rec, sl.load_verdicts().get("162-synthetic"))
    assert eff != "accepted", f"162-shape: expected not-accepted but got {eff!r}"
    assert eff == "awaiting-verify", (
        f"162-shape: expected awaiting-verify but got {eff!r}"
    )


def test_r5_criteria_absent_non_rev_does_not_derive_accepted(monkeypatch, tmp_path):
    """R5 coverage — criteria field entirely absent (not empty {}), non-rev judge.
    Same guard must fire: codex writing a bare CONFIRMED with no criteria map."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "199-synthetic", title="199 codex shape", status="shipped")
    _write_verdict_raw(
        sl,
        "199-synthetic",
        # no `criteria` key at all
        verdict="CONFIRMED",
        judge="codex",
        evidence_ref="codex run 2026-06-16",
    )
    rec = sl.load_records()[0]
    eff = sl.effective_status(rec, sl.load_verdicts().get("199-synthetic"))
    assert eff != "accepted", (
        f"codex-no-criteria: expected not-accepted but got {eff!r}"
    )
    assert eff == "awaiting-verify", (
        f"codex-no-criteria: expected awaiting-verify but got {eff!r}"
    )


def test_r5_criteria_free_rev_still_derives_accepted(monkeypatch, tmp_path):
    """R5 no-regression — a criteria-free CONFIRMED from judge=rev STILL derives
    accepted. Rev-authored legacy records are trusted; this path must not break."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "202-rev-legacy", title="Rev legacy", status="shipped")
    _write_verdict_raw(
        sl,
        "202-rev-legacy",
        criteria={},  # empty — legacy rev record
        verdict="CONFIRMED",
        judge="rev",
        evidence_ref="rev manual check 2026-06-01",
    )
    rec = sl.load_records()[0]
    eff = sl.effective_status(rec, sl.load_verdicts().get("202-rev-legacy"))
    assert eff == "accepted", f"rev-legacy: expected accepted but got {eff!r}"


def test_r5_criteria_absent_rev_still_derives_accepted(monkeypatch, tmp_path):
    """R5 no-regression — criteria field absent (not just empty), judge=rev.
    Rev-authored records with no criteria field must still derive accepted."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "203-rev-legacy-no-crit", title="Rev legacy no crit", status="shipped")
    _write_verdict_raw(
        sl,
        "203-rev-legacy-no-crit",
        # no criteria key
        verdict="CONFIRMED",
        judge="rev",
        evidence_ref="rev manual 2026-05-20",
    )
    rec = sl.load_records()[0]
    eff = sl.effective_status(rec, sl.load_verdicts().get("203-rev-legacy-no-crit"))
    assert eff == "accepted", f"rev-legacy-no-crit: expected accepted but got {eff!r}"


# ---------------------------------------------------------------------------
# Spec 252 Phase 0 — `ready` status: ledger + renderer
# ---------------------------------------------------------------------------


def test_252_ready_is_valid_status(monkeypatch, tmp_path):
    """ready must appear in VALID_STATUS."""
    sl = _load(monkeypatch, tmp_path)
    assert "ready" in sl.VALID_STATUS


def test_252_ready_is_outstanding_status(monkeypatch, tmp_path):
    """ready must appear in OUTSTANDING_STATUSES (non-terminal)."""
    sl = _load(monkeypatch, tmp_path)
    assert "ready" in sl.OUTSTANDING_STATUSES


def test_252_ready_record_with_seven_fields_passes_check(monkeypatch, tmp_path):
    """(a) A hand-constructed ready record with all 7 new fields passes --check."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "252-parallel-builders",
        title="Parallel builders",
        status="ready",
        writes=["api/app/lib/profit_v2/", "dashboard/src/components/profit-v2/"],
        claimed_by="builder-pane-0",
        claimed_at="2026-06-29T08:00:00Z",
        worktree="/tmp/wt-252",
        branch="feat/252-parallel-builders",
        ready_sha="abc1234def567890abc1234def567890abc12345",
        retry_count=0,
    )
    errs = sl.validate(sl.load_records())
    assert errs == [], f"Expected no validation errors, got: {errs}"


def test_252_ready_record_renders_in_distinct_group(monkeypatch, tmp_path):
    """(b) The renderer output contains the distinct ready group with a ready record."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "252-parallel-builders",
        title="Parallel builders",
        status="ready",
        branch="feat/252-parallel-builders",
        ready_sha="abc1234def567890",
        claimed_by="builder-pane-0",
        claimed_at="2026-06-29T08:00:00Z",
        worktree="/tmp/wt-252",
        retry_count=0,
        writes=["api/app/lib/profit_v2/"],
    )
    body = sl.render(sl.load_records(), include_all=False)
    # Must appear in the distinct ready group
    assert "Ready to merge" in body, "Expected 'Ready to merge' section header"
    assert "awaiting integrator" in body, "Expected 'awaiting integrator' in header"
    assert "252-parallel-builders" in body
    assert "READY" in body
    # The 🟢 glyph must be present
    assert "🟢" in body
    # Branch and sha must be rendered
    assert "feat/252-parallel-builders" in body
    assert "abc1234" in body  # first 8 chars of sha


def test_252_ready_not_in_generic_outstanding_section(monkeypatch, tmp_path):
    """ready records must NOT appear in the generic Outstanding bucket."""
    sl = _load(monkeypatch, tmp_path)
    _write(sl, "252-test", title="Test ready", status="ready")
    body = sl.render(sl.load_records(), include_all=False)
    # Find the Outstanding section content up to the Ready section
    outstanding_section = body.split("## Outstanding")[1].split("## 🟢")[0]
    assert "252-test" not in outstanding_section, (
        "ready record must not appear in the generic Outstanding bucket"
    )


def test_252_cmd_set_allows_transition_to_ready(monkeypatch, tmp_path):
    """(c) building→ready transition is accepted by cmd_set."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "252-transition",
        title="Transition test",
        status="building",
        history=[{"at": "2026-06-29T07:00:00Z", "status": "building", "by": "builder"}],
    )
    rc = sl.cmd_set(
        [
            "252-transition",
            "ready",
            "--by",
            "builder",
            "--field",
            "ready_sha=deadbeefdeadbeef",
            "--field",
            "branch=feat/252-transition",
        ]
    )
    assert rc == 0
    import yaml as _yaml

    rec = _yaml.safe_load((sl.LEDGER_DIR / "252-transition.yml").read_text())
    assert rec["status"] == "ready"
    assert rec["ready_sha"] == "deadbeefdeadbeef"
    statuses = [h["status"] for h in rec["history"]]
    assert "ready" in statuses


def test_252_cmd_set_allows_transition_ready_to_merged(monkeypatch, tmp_path):
    """(c cont.) ready→merged transition is accepted by cmd_set."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "252-merge",
        title="Merge test",
        status="ready",
        history=[
            {"at": "2026-06-29T07:00:00Z", "status": "building", "by": "builder"},
            {"at": "2026-06-29T08:00:00Z", "status": "ready", "by": "builder"},
        ],
    )
    rc = sl.cmd_set(["252-merge", "merged", "--by", "integrator"])
    assert rc == 0
    import yaml as _yaml

    rec = _yaml.safe_load((sl.LEDGER_DIR / "252-merge.yml").read_text())
    assert rec["status"] == "merged"
    statuses = [h["status"] for h in rec["history"]]
    assert "ready" in statuses and "merged" in statuses


def test_252_full_building_ready_merged_sequence(monkeypatch, tmp_path):
    """(c) building→ready→merged full sequence via cmd_set."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "252-sequence",
        title="Full sequence",
        status="registered",
        history=[
            {"at": "2026-06-29T06:00:00Z", "status": "registered", "by": "handover"}
        ],
    )
    # registered → building
    rc = sl.cmd_set(["252-sequence", "building", "--by", "builder"])
    assert rc == 0
    # building → ready
    rc = sl.cmd_set(
        [
            "252-sequence",
            "ready",
            "--by",
            "builder",
            "--field",
            "ready_sha=cafebabe",
            "--field",
            "branch=feat/252-sequence",
        ]
    )
    assert rc == 0
    # ready → merged
    rc = sl.cmd_set(["252-sequence", "merged", "--by", "integrator"])
    assert rc == 0
    import yaml as _yaml

    rec = _yaml.safe_load((sl.LEDGER_DIR / "252-sequence.yml").read_text())
    assert rec["status"] == "merged"
    statuses = [h["status"] for h in rec["history"]]
    assert statuses == ["registered", "building", "ready", "merged"]


def test_252_cmd_set_still_blocks_accepted(monkeypatch, tmp_path):
    """accepted must remain blocked even though ready is now allowed."""
    sl = _load(monkeypatch, tmp_path)
    _write(
        sl,
        "252-block",
        title="Block test",
        status="shipped",
        history=[{"at": "2026-06-29T00:00:00Z", "status": "shipped", "by": "orc"}],
    )
    rc = sl.cmd_set(["252-block", "accepted", "--by", "orc"])
    assert rc != 0, "cmd_set must reject accepted (it is computed-only)"
