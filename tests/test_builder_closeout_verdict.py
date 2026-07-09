"""Tests for builder_closeout_verdict.py — spec 296 R1-AC2 / spec 357."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from copy import deepcopy
from pathlib import Path

import pytest

# Make the scripts dir importable without modifying pyproject / pytest.ini.
_SCRIPTS_DIR_PATH = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR_PATH))
import builder_closeout_verdict as v  # noqa: E402


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _all_pass() -> dict:
    """Return a fully-passing verdict dict."""
    return {
        "pre_gates": {
            "build": "pass",
            "routes_200": "pass",
            "screenshot": "pass",
        },
        "checks": {
            "c1_prod_migration": "pass",
            "c2_sibling_tests": "pass",
            "c3_migration_lint": "pass",
        },
        "matches_intent": "yes",
        "matches_intent_reason": "all good",
        "card_ok": "yes",
        "card_ok_reason": "card is correct",
        "draft_card": "## Review card body",
    }


# ---------------------------------------------------------------------------
# R1-AC2 (i) — full pass → ready
# ---------------------------------------------------------------------------


def test_all_pass_ready():
    assert v.verdict_decision(_all_pass()) == "ready"


# ---------------------------------------------------------------------------
# 2026-07-03 corrective — the REAL grader emits BOOLEAN true/false (the prompt
# asks for `<true|false>`), not the string "yes". Prior logic compared against
# "yes" and false-failed every spec (surfaced by 334). Both forms must pass.
# ---------------------------------------------------------------------------


def test_boolean_pass_ready():
    verdict = deepcopy(_all_pass())
    verdict["matches_intent"] = True
    verdict["card_ok"] = True
    verdict["pre_gates"] = {"python_import": "pass"}
    verdict["checks"] = {"mechanical": "pass"}
    assert v.verdict_decision(verdict) == "ready"


def test_boolean_false_regrades():
    verdict = deepcopy(_all_pass())
    verdict["matches_intent"] = True
    verdict["card_ok"] = False
    assert v.verdict_decision(verdict) == "regrade"


def test_extract_json_from_prose_and_fence():
    """claude -p commonly prepends a prose sentence and wraps the JSON in a
    ```json fence despite the bare-JSON instruction — must still parse."""
    raw = (
        "The code faithfully implements the intent — verified.\n\n"
        "```json\n"
        '{"pre_gates":{"python_import":"pass"},"checks":{"mechanical":"pass"},'
        '"matches_intent":true,"matches_intent_reason":"ok",'
        '"card_ok":true,"card_ok_reason":"fine"}\n'
        "```\n"
    )
    verdict = v.extract_verdict_json(raw)
    assert v.verdict_decision(verdict) == "ready"


def test_extract_json_bare_and_brace_span():
    # bare JSON parses
    assert v.extract_verdict_json('{"card_ok": true}') == {"card_ok": True}
    # leading/trailing prose without a fence → outermost brace span
    assert v.extract_verdict_json('note: {"card_ok": true} done') == {"card_ok": True}


def test_extract_json_no_object_raises():
    with pytest.raises(ValueError):
        v.extract_verdict_json("no json here at all")


# ---------------------------------------------------------------------------
# R1-AC2 (ii) — any single field flipped → regrade
# ---------------------------------------------------------------------------

_MUTATIONS = [
    ("pre_gates", "build", "fail"),
    ("pre_gates", "routes_200", "fail"),
    ("pre_gates", "screenshot", "fail"),
    ("checks", "c1_prod_migration", "fail"),
    ("checks", "c2_sibling_tests", "fail"),
    ("checks", "c3_migration_lint", "fail"),
    (None, "matches_intent", "no"),
    (None, "card_ok", "no"),
]


@pytest.mark.parametrize("parent,key,val", _MUTATIONS)
def test_any_field_no_regrades(parent, key, val):
    verdict = deepcopy(_all_pass())
    if parent is None:
        verdict[key] = val
    else:
        verdict[parent][key] = val
    assert v.verdict_decision(verdict) == "regrade"


# ---------------------------------------------------------------------------
# R1-AC2 (iii) — no log-bearing keys in clean verdict
# ---------------------------------------------------------------------------


def test_no_log_field_clean():
    assert v.verdict_has_no_logs(_all_pass()) is True


# ---------------------------------------------------------------------------
# Log-bearing field detection
# ---------------------------------------------------------------------------


def test_log_bearing_field_detected_top_level():
    bad = deepcopy(_all_pass())
    bad["pytest_output"] = "collected 42 items ..."
    assert v.verdict_has_no_logs(bad) is False
    _, problems = v.validate_and_decide(bad)
    assert any("log-bearing" in p for p in problems)


def test_log_bearing_field_detected_nested():
    bad = deepcopy(_all_pass())
    bad["checks"]["stdout"] = "some output"
    assert v.verdict_has_no_logs(bad) is False
    _, problems = v.validate_and_decide(bad)
    assert any("log-bearing" in p for p in problems)


# ---------------------------------------------------------------------------
# Missing keys → fail-closed
# ---------------------------------------------------------------------------


def test_missing_keys_fail_closed_empty():
    assert v.verdict_decision({}) == "regrade"


def test_missing_keys_fail_closed_no_checks():
    verdict = deepcopy(_all_pass())
    del verdict["checks"]
    assert v.verdict_decision(verdict) == "regrade"


def test_missing_keys_fail_closed_no_pre_gates():
    verdict = deepcopy(_all_pass())
    del verdict["pre_gates"]
    assert v.verdict_decision(verdict) == "regrade"


# ---------------------------------------------------------------------------
# Unexpected top-level key flagged
# ---------------------------------------------------------------------------


def test_unexpected_top_key_flagged():
    verdict = deepcopy(_all_pass())
    verdict["transcript"] = "raw build output ..."

    decision, problems = v.validate_and_decide(verdict)

    # Base decision still computed (independent of problems)
    assert decision == "ready"

    # Both the unexpected-key AND the log-bearing problem must appear
    assert any("unexpected top-level key: transcript" in p for p in problems)
    assert any("log-bearing" in p for p in problems)


# ---------------------------------------------------------------------------
# CLI — exit codes
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_MODULE = str(_SCRIPTS_DIR / "builder_closeout_verdict.py")


def test_cli_ready_exit0(tmp_path):
    verdict_file = tmp_path / "verdict.json"
    verdict_file.write_text(json.dumps(_all_pass()))

    result = subprocess.run(
        [sys.executable, _MODULE, "--verdict", str(verdict_file)],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "ready"
    assert result.returncode == 0


def test_cli_regrade_exit1(tmp_path):
    bad = deepcopy(_all_pass())
    bad["pre_gates"]["build"] = "fail"
    verdict_file = tmp_path / "verdict.json"
    verdict_file.write_text(json.dumps(bad))

    result = subprocess.run(
        [sys.executable, _MODULE, "--verdict", str(verdict_file)],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "regrade"
    assert result.returncode == 1


# ===========================================================================
# Spec 357 — owed-data three-way verdict
# ===========================================================================

# ---------------------------------------------------------------------------
# Gating-watch harness helpers (adapted from test_gating_watch.py; self-contained)
# ---------------------------------------------------------------------------

_GATING_WATCH_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "gating-watch.sh"
)
_SPEC_LEDGER_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"
)


def _make_exec(path: Path, content: str) -> Path:
    """Write *content* to *path* and mark it executable."""
    path.write_text(textwrap.dedent(content))
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_gating_file(lane_dir: Path, spec_id: str) -> Path:
    f = lane_dir / f"{spec_id}.gating.md"
    f.write_text(
        f"---\n"
        f"spec_id: {spec_id}\n"
        f"base_sha: aabbccdd1111\n"
        f"ready_sha: 99887766ffff\n"
        f"branch: feat/{spec_id}\n"
        f"card_path: \n"
        f"---\n"
        f"Build complete.\n"
    )
    return f


def _run_gating_watch(
    lane_dir: Path,
    liveness_dir: Path,
    tmp_path: Path,
    seam_overrides: dict[str, str],
) -> subprocess.CompletedProcess:
    lock_file = tmp_path / "gating-watch.lock"
    env = {
        **os.environ,
        "LANE_DIR": str(lane_dir),
        "LIVENESS_DIR": str(liveness_dir),
        "REPO_ROOT": str(tmp_path / "fake-repo"),
        "GATING_STALE_SECS": "1800",
        "GATING_MAX_REDISPATCH": "3",
        "LOCK_FILE": str(lock_file),
        **seam_overrides,
    }
    return subprocess.run(
        ["bash", str(_GATING_WATCH_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# R1-AC1 — grader prompt teaches observed-data class + requires owed_data_acs
# ---------------------------------------------------------------------------


def test_r1_ac1_grader_prompt_contains_owed_data_instructions(tmp_path):
    """Run gating-watch with a GRADER_CMD stub that captures its first arg (the prompt)
    to a file. Assert the prompt contains the owed-data deferral instructions and
    the owed_data_acs requirement."""
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    prompt_capture = tmp_path / "captured-prompt.txt"
    ledger_log = tmp_path / "ledger.log"
    checkout_dir = tmp_path / "co"

    all_pass_json = json.dumps(
        {
            "pre_gates": {"python_import": "pass"},
            "checks": {"mechanical": "pass"},
            "matches_intent": True,
            "matches_intent_reason": "all code ACs satisfied",
            "card_ok": True,
            "card_ok_reason": "card is complete",
            "owed_data_acs": [],
        }
    )

    # GRADER_CMD stub: capture prompt to file, then emit valid all-pass JSON
    grader_stub = _make_exec(
        bin_dir / "stub-grader.sh",
        f"""\
        #!/usr/bin/env bash
        printf '%s' "$1" > '{prompt_capture}'
        echo '{all_pass_json}'
        """,
    )

    verdict_stub = _make_exec(
        bin_dir / "stub-verdict.sh",
        """\
        #!/usr/bin/env bash
        cat > /dev/null
        echo 'ready'
        exit 0
        """,
    )

    ledger_stub = _make_exec(
        bin_dir / "stub-ledger.sh",
        f"""\
        #!/usr/bin/env bash
        echo "$@" >> '{ledger_log}'
        exit 0
        """,
    )

    checkout_stub = _make_exec(
        bin_dir / "stub-checkout.sh",
        f"""\
        #!/usr/bin/env bash
        mkdir -p '{checkout_dir}'
        echo '{checkout_dir}'
        """,
    )

    closeout_stub = _make_exec(
        bin_dir / "stub-closeout.sh", "#!/usr/bin/env bash\nexit 0\n"
    )
    alarm_stub = _make_exec(bin_dir / "stub-alarm.sh", "#!/usr/bin/env bash\nexit 0\n")

    spec_id = "357-r1ac1-prompt"
    _write_gating_file(lane_dir, spec_id)

    result = _run_gating_watch(
        lane_dir,
        liveness_dir,
        tmp_path,
        {
            "GRADER_CMD": str(grader_stub),
            "VERDICT_CMD": str(verdict_stub),
            "LEDGER_SET_CMD": str(ledger_stub),
            "CHECKOUT_CMD": str(checkout_stub),
            "CLOSEOUT_CHECK_CMD": str(closeout_stub),
            "ALARM_CMD": str(alarm_stub),
        },
    )
    assert result.returncode == 0, result.stderr
    assert prompt_capture.exists(), "GRADER_CMD stub did not capture the prompt"

    prompt_text = prompt_capture.read_text()

    # (a) Teaches that [observed-data] (and related tags) ACs are deferred
    assert "[observed-data]" in prompt_text, (
        "[observed-data] tag not found in grader prompt"
    )
    # At least one of the deferral/owed keywords must appear
    assert any(
        kw in prompt_text for kw in ("owed to", "deferred", "not yours to verify")
    ), "No deferral instruction found in grader prompt"

    # (b) Requires an owed_data_acs array
    assert "owed_data_acs" in prompt_text, (
        "owed_data_acs field requirement not found in grader prompt"
    )


# ---------------------------------------------------------------------------
# R1-AC2 — owed_data_acs is an allowed top-level key (no 'unexpected key' problem)
# ---------------------------------------------------------------------------


def test_r1_ac2_owed_data_acs_is_allowed_key():
    """validate_and_decide with owed_data_acs populated must return problems == []."""
    # R1-AC2: owed_data_acs in ALLOWED_TOP_KEYS
    verdict = {**_all_pass(), "owed_data_acs": ["R1 [observed-data] data job result"]}
    _, problems = v.validate_and_decide(verdict)
    assert problems == [], f"Expected no problems, got: {problems}"


# ---------------------------------------------------------------------------
# R2-AC1 — verdict_decision three-way classification
# ---------------------------------------------------------------------------


def test_r2_ac1_all_pass_plus_owed_data_acs_gives_owed_data():
    """All gates pass + non-empty owed_data_acs → owed-data."""
    verdict = {
        **_all_pass(),
        "owed_data_acs": ["R2 AC1 [observed-data] revenue reconciliation"],
    }
    assert v.verdict_decision(verdict) == "owed-data"


def test_r2_ac1_all_pass_empty_owed_data_acs_gives_ready():
    """All gates pass + empty owed_data_acs → ready."""
    verdict = {**_all_pass(), "owed_data_acs": []}
    assert v.verdict_decision(verdict) == "ready"


def test_r2_ac1_all_pass_absent_owed_data_acs_gives_ready():
    """All gates pass + absent owed_data_acs → ready."""
    verdict = _all_pass()  # no owed_data_acs key at all
    assert v.verdict_decision(verdict) == "ready"


def test_r2_ac1_failing_gate_card_ok_with_owed_data_gives_regrade():
    """Failing gate (card_ok: False) + non-empty owed_data_acs → regrade.
    Owed data must never mask a failing gate."""
    verdict = {
        **_all_pass(),
        "card_ok": False,
        "owed_data_acs": ["R2 AC1 [observed-data] cron job check"],
    }
    assert v.verdict_decision(verdict) == "regrade"


def test_r2_ac1_failing_gate_matches_intent_with_owed_data_gives_regrade():
    """Failing gate (matches_intent: False) + non-empty owed_data_acs → regrade."""
    verdict = {
        **_all_pass(),
        "matches_intent": False,
        "owed_data_acs": ["R2 AC1 [financial] margin check"],
    }
    assert v.verdict_decision(verdict) == "regrade"


def test_r2_ac1_failing_check_with_owed_data_gives_regrade():
    """Failing checks entry + non-empty owed_data_acs → regrade."""
    verdict = deepcopy(_all_pass())
    verdict["checks"]["c1_prod_migration"] = "fail"
    verdict["owed_data_acs"] = ["R2 AC1 [cron] nightly pipeline result"]
    assert v.verdict_decision(verdict) == "regrade"


# ---------------------------------------------------------------------------
# R2-AC2 — CLI exit contract distinguishes the three verdicts
# ---------------------------------------------------------------------------


def test_r2_ac2_cli_ready_exit0(tmp_path):
    """ready verdict → exit 0, stdout 'ready'."""
    verdict_file = tmp_path / "v.json"
    verdict_file.write_text(json.dumps(_all_pass()))
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPTS_DIR_PATH / "builder_closeout_verdict.py"),
            "--verdict",
            str(verdict_file),
        ],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "ready"
    assert result.returncode == 0


def test_r2_ac2_cli_owed_data_exit0(tmp_path):
    """owed-data verdict → exit 0, stdout 'owed-data'."""
    verdict = {
        **_all_pass(),
        "owed_data_acs": ["R2 AC2 [observed-data] settlement check"],
    }
    verdict_file = tmp_path / "v.json"
    verdict_file.write_text(json.dumps(verdict))
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPTS_DIR_PATH / "builder_closeout_verdict.py"),
            "--verdict",
            str(verdict_file),
        ],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "owed-data"
    assert result.returncode == 0


def test_r2_ac2_cli_regrade_exit1(tmp_path):
    """regrade verdict → exit 1, stdout 'regrade'."""
    verdict = deepcopy(_all_pass())
    verdict["matches_intent"] = False
    verdict_file = tmp_path / "v.json"
    verdict_file.write_text(json.dumps(verdict))
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPTS_DIR_PATH / "builder_closeout_verdict.py"),
            "--verdict",
            str(verdict_file),
        ],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "regrade"
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# R3-AC1 — gating-watch owed-data → .ready.md with owed fields + reasons
# ---------------------------------------------------------------------------


def test_r3_ac1_gating_watch_owed_data_to_ready(tmp_path):
    """GRADER_CMD emits owed-data verdict; VERDICT_CMD echoes 'owed-data' exit 0.
    Assert .ready.md created (not .rework.md), owed fields present, ledger called
    with owed_data_acs, completions records result=owed-data."""
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    ledger_log = tmp_path / "ledger.log"
    checkout_dir = tmp_path / "co"

    ac1 = "R3-AC1a [observed-data] nightly revenue reconciliation"
    ac2 = "R3-AC1b [cron] settlement file ingested"
    mi_reason = "All backend routes verified; data job deferred"
    co_reason = "Card structure complete and accurate"

    grader_json = json.dumps(
        {
            "pre_gates": {"python_import": "pass"},
            "checks": {"mechanical": "pass"},
            "matches_intent": True,
            "matches_intent_reason": mi_reason,
            "card_ok": True,
            "card_ok_reason": co_reason,
            "owed_data_acs": [ac1, ac2],
        }
    )

    grader_stub = _make_exec(
        bin_dir / "stub-grader.sh",
        f"""\
        #!/usr/bin/env bash
        echo '{grader_json}'
        """,
    )

    verdict_stub = _make_exec(
        bin_dir / "stub-verdict.sh",
        """\
        #!/usr/bin/env bash
        cat > /dev/null
        echo 'owed-data'
        exit 0
        """,
    )

    ledger_stub = _make_exec(
        bin_dir / "stub-ledger.sh",
        f"""\
        #!/usr/bin/env bash
        echo "$@" >> '{ledger_log}'
        exit 0
        """,
    )

    checkout_stub = _make_exec(
        bin_dir / "stub-checkout.sh",
        f"""\
        #!/usr/bin/env bash
        mkdir -p '{checkout_dir}'
        echo '{checkout_dir}'
        """,
    )

    closeout_stub = _make_exec(
        bin_dir / "stub-closeout.sh", "#!/usr/bin/env bash\nexit 0\n"
    )
    alarm_stub = _make_exec(bin_dir / "stub-alarm.sh", "#!/usr/bin/env bash\nexit 0\n")

    spec_id = "357-r3ac1-owed"
    _write_gating_file(lane_dir, spec_id)

    result = _run_gating_watch(
        lane_dir,
        liveness_dir,
        tmp_path,
        {
            "GRADER_CMD": str(grader_stub),
            "VERDICT_CMD": str(verdict_stub),
            "LEDGER_SET_CMD": str(ledger_stub),
            "CHECKOUT_CMD": str(checkout_stub),
            "CLOSEOUT_CHECK_CMD": str(closeout_stub),
            "ALARM_CMD": str(alarm_stub),
        },
    )
    assert result.returncode == 0, result.stderr

    ready_file = lane_dir / f"{spec_id}.ready.md"
    rework_file = lane_dir / f"{spec_id}.rework.md"
    assert ready_file.exists(), ".ready.md must be created for owed-data verdict"
    assert not rework_file.exists(), ".rework.md must NOT exist for owed-data verdict"

    content = ready_file.read_text()
    assert "owed_data: true" in content, "owed_data: true missing from .ready.md"
    assert ac1 in content, f"First owed AC string missing from .ready.md: {ac1}"
    assert ac2 in content, f"Second owed AC string missing from .ready.md: {ac2}"
    assert mi_reason in content, "matches_intent_reason missing from .ready.md"
    assert co_reason in content, "card_ok_reason missing from .ready.md"

    assert ledger_log.exists(), "LEDGER_SET_CMD was never called"
    ledger_calls = ledger_log.read_text()
    assert spec_id in ledger_calls
    assert "ready" in ledger_calls
    assert "owed_data_acs" in ledger_calls

    completions = (liveness_dir / "gating-watch-completions").read_text()
    assert f"graded {spec_id} result=owed-data" in completions


# ---------------------------------------------------------------------------
# R3-AC2 — gating-watch regrade → .rework.md contains real reason (not literal "regrade")
# ---------------------------------------------------------------------------


def test_r3_ac2_gating_watch_regrade_has_real_reason(tmp_path):
    """GRADER_CMD emits a failing verdict with a specific reason sentence.
    VERDICT_CMD echoes 'regrade' exit 1.
    Assert .rework.md rework_reason: contains the real sentence, not bare 'regrade'."""
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    ledger_log = tmp_path / "ledger.log"
    checkout_dir = tmp_path / "co"

    real_reason = "AC2 backend route returns 500 under concurrent load"

    grader_json = json.dumps(
        {
            "pre_gates": {"python_import": "pass"},
            "checks": {"mechanical": "pass"},
            "matches_intent": False,
            "matches_intent_reason": real_reason,
            "card_ok": True,
            "card_ok_reason": "card structure is fine",
            "owed_data_acs": [],
        }
    )

    grader_stub = _make_exec(
        bin_dir / "stub-grader.sh",
        f"""\
        #!/usr/bin/env bash
        echo '{grader_json}'
        """,
    )

    verdict_stub = _make_exec(
        bin_dir / "stub-verdict.sh",
        """\
        #!/usr/bin/env bash
        cat > /dev/null
        echo 'regrade'
        exit 1
        """,
    )

    ledger_stub = _make_exec(
        bin_dir / "stub-ledger.sh",
        f"""\
        #!/usr/bin/env bash
        echo "$@" >> '{ledger_log}'
        exit 0
        """,
    )

    checkout_stub = _make_exec(
        bin_dir / "stub-checkout.sh",
        f"""\
        #!/usr/bin/env bash
        mkdir -p '{checkout_dir}'
        echo '{checkout_dir}'
        """,
    )

    closeout_stub = _make_exec(
        bin_dir / "stub-closeout.sh", "#!/usr/bin/env bash\nexit 0\n"
    )
    alarm_stub = _make_exec(bin_dir / "stub-alarm.sh", "#!/usr/bin/env bash\nexit 0\n")

    spec_id = "357-r3ac2-rework"
    _write_gating_file(lane_dir, spec_id)

    result = _run_gating_watch(
        lane_dir,
        liveness_dir,
        tmp_path,
        {
            "GRADER_CMD": str(grader_stub),
            "VERDICT_CMD": str(verdict_stub),
            "LEDGER_SET_CMD": str(ledger_stub),
            "CHECKOUT_CMD": str(checkout_stub),
            "CLOSEOUT_CHECK_CMD": str(closeout_stub),
            "ALARM_CMD": str(alarm_stub),
        },
    )
    assert result.returncode == 0, result.stderr

    rework_file = lane_dir / f"{spec_id}.rework.md"
    assert rework_file.exists(), ".rework.md must exist for regrade verdict"
    assert not (lane_dir / f"{spec_id}.ready.md").exists()

    content = rework_file.read_text()
    # rework_reason must contain the actual reason sentence
    assert real_reason in content, (
        f"Real reason sentence not found in .rework.md content:\n{content}"
    )
    # rework_reason must NOT be the bare word "regrade"
    import re as _re

    match = _re.search(r"rework_reason:\s*(.+)", content)
    assert match is not None, "rework_reason: line not found in .rework.md"
    reason_value = match.group(1).strip()
    assert reason_value != "regrade", (
        f"rework_reason must not be the bare word 'regrade', got: {reason_value!r}"
    )


# ---------------------------------------------------------------------------
# R3-AC3 — ledger record carries non-empty owed_data_acs as a real list
# ---------------------------------------------------------------------------


def test_r3_ac3_ledger_owed_data_acs_stored_as_list(tmp_path):
    """Register a spec then set owed_data_acs via spec_ledger.py CLI.
    Load the written YAML and assert owed_data_acs is a list of length 2."""
    import yaml  # bundled with PyYAML (already a dep)

    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Spec 357 test\n")

    spec_id = "999-r3ac3-owed"
    env = {**os.environ, "DOIT_LEDGER_DIR": str(ledger_dir)}

    # Register
    r = subprocess.run(
        [
            sys.executable,
            str(_SPEC_LEDGER_SCRIPT),
            "register",
            spec_id,
            "--title",
            "R3-AC3 test",
            "--intent",
            "Verify owed_data_acs stored as list",
            "--spec-file",
            str(spec_file),
            "--by",
            "test",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, f"register failed: {r.stderr}"

    # Set shipped (required before setting owed_data_acs in a realistic scenario;
    # but the set command allows any status transition, so use a status transition
    # that the ledger accepts — shipped is fine)
    r = subprocess.run(
        [
            sys.executable,
            str(_SPEC_LEDGER_SCRIPT),
            "set",
            spec_id,
            "shipped",
            "--by",
            "test",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, f"set shipped failed: {r.stderr}"

    # Set owed_data_acs
    owed_acs = [
        "R3 AC3 [observed-data] revenue reconciliation x",
        "R4 [cron] nightly run y",
    ]
    r = subprocess.run(
        [
            sys.executable,
            str(_SPEC_LEDGER_SCRIPT),
            "set",
            spec_id,
            "shipped",
            "--by",
            "test",
            "--field",
            f"owed_data_acs={json.dumps(owed_acs)}",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, f"set owed_data_acs failed: {r.stderr}"

    rec_file = ledger_dir / f"{spec_id}.yml"
    assert rec_file.exists(), f"Ledger record not found at {rec_file}"
    rec = yaml.safe_load(rec_file.read_text())
    assert isinstance(rec.get("owed_data_acs"), list), (
        f"owed_data_acs must be a list, got: {type(rec.get('owed_data_acs'))}"
    )
    assert len(rec["owed_data_acs"]) == 2, (
        f"Expected 2 owed ACs, got {len(rec['owed_data_acs'])}: {rec['owed_data_acs']}"
    )


# ---------------------------------------------------------------------------
# R4-AC1 — owed-data record renders as awaiting-verify, NOT accepted
# ---------------------------------------------------------------------------


def test_r4_ac1_owed_data_record_renders_as_awaiting_verify():
    """effective_status on a shipped record with owed_data_acs and no verifier verdict
    must return 'awaiting-verify', NOT 'accepted'. Proves owed_data_acs doesn't
    create a new path to accepted."""
    import spec_ledger as _sl

    rec = {
        "status": "shipped",
        "owed_data_acs": ["R4 AC1 [observed-data] some deferred check"],
        "spec_id": "999-r4ac1-test",
    }
    # verdict=None simulates no verifier verdict file present
    result = _sl.effective_status(rec, None)
    assert result == "awaiting-verify", (
        f"Expected 'awaiting-verify' but got {result!r}. "
        "owed_data_acs must not grant 'accepted'."
    )
