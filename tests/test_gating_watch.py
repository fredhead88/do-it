"""tests/test_gating_watch.py — Hermetic tests for scripts/gating-watch.sh (spec 300).

Every external coupling is replaced by an ENV-overridable stub written to a
tmp_path bin dir.  No real LLM / git / prod / cron is touched.

Post-fire assertion discipline (spec-300 R1): each test drives the script to
completion, then inspects the produced lane/liveness artifacts.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo root (worktree root, not /opt/albert-scott)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "gating-watch.sh"


# ---------------------------------------------------------------------------
# Stub factories
# ---------------------------------------------------------------------------

def _make_executable(path: Path, content: str) -> Path:
    """Write *content* to *path* and mark it executable."""
    path.write_text(textwrap.dedent(content))
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_stubs(
    bin_dir: Path,
    *,
    grader_json: str = '{"pre_gates":{"python_import":"pass"},"checks":{"mechanical":"pass"},"matches_intent":true,"matches_intent_reason":"looks good","card_ok":true,"card_ok_reason":"card fine"}',
    verdict_exit: int = 0,
    verdict_output: str = "ready",
    ledger_log: Path,
    checkout_dir: Path,
    alarm_log: Path,
    closeout_exit: int = 0,
) -> dict[str, str]:
    """Create stub executables in *bin_dir*; return a dict of seam env vars."""

    # GRADER_CMD stub — echoes scripted JSON verdict
    grader = _make_executable(
        bin_dir / "stub-grader.sh",
        f"""\
        #!/usr/bin/env bash
        echo '{grader_json}'
        """,
    )

    # VERDICT_CMD stub — reads stdin (ignored), prints decision, exits scripted code
    verdict = _make_executable(
        bin_dir / "stub-verdict.sh",
        f"""\
        #!/usr/bin/env bash
        cat > /dev/null
        echo '{verdict_output}'
        exit {verdict_exit}
        """,
    )

    # LEDGER_SET_CMD stub — logs args
    ledger = _make_executable(
        bin_dir / "stub-ledger.sh",
        f"""\
        #!/usr/bin/env bash
        echo "$@" >> '{ledger_log}'
        exit 0
        """,
    )

    # CHECKOUT_CMD stub — creates a plain temp dir and prints its path
    checkout = _make_executable(
        bin_dir / "stub-checkout.sh",
        f"""\
        #!/usr/bin/env bash
        mkdir -p '{checkout_dir}'
        echo '{checkout_dir}'
        """,
    )

    # CLOSEOUT_CHECK_CMD stub
    closeout = _make_executable(
        bin_dir / "stub-closeout.sh",
        f"""\
        #!/usr/bin/env bash
        exit {closeout_exit}
        """,
    )

    # ALARM_CMD stub — logs invocation
    alarm = _make_executable(
        bin_dir / "stub-alarm.sh",
        f"""\
        #!/usr/bin/env bash
        echo "alarm fired $@" >> '{alarm_log}'
        exit 0
        """,
    )

    return {
        "GRADER_CMD": str(grader),
        "VERDICT_CMD": str(verdict),
        "LEDGER_SET_CMD": str(ledger),
        "CHECKOUT_CMD": str(checkout),
        "CLOSEOUT_CHECK_CMD": str(closeout),
        "ALARM_CMD": str(alarm),
    }


def _write_gating_file(lane_dir: Path, spec_id: str, card_path: str = "") -> Path:
    """Write a minimal NNN.gating.md to lane_dir and return its path."""
    f = lane_dir / f"{spec_id}.gating.md"
    f.write_text(
        f"---\n"
        f"spec_id: {spec_id}\n"
        f"base_sha: aabbccdd1111\n"
        f"ready_sha: 99887766ffff\n"
        f"branch: feat/{spec_id}\n"
        f"card_path: {card_path}\n"
        f"---\n"
        f"Build complete.\n"
    )
    return f


def _run_script(
    lane_dir: Path,
    liveness_dir: Path,
    tmp_path: Path,
    seam_overrides: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run gating-watch.sh with all seams pointed at tmp_path fixtures."""
    lock_file = tmp_path / "gating-watch.lock"
    env = {
        **os.environ,
        "LANE_DIR": str(lane_dir),
        "LIVENESS_DIR": str(liveness_dir),
        "REPO_ROOT": str(tmp_path / "fake-repo"),
        "GATING_STALE_SECS": "1800",
        "GATING_MAX_REDISPATCH": "3",
        "LOCK_FILE": str(lock_file),
        **(seam_overrides or {}),
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# AC3: PASS path → .gating becomes .ready
# ---------------------------------------------------------------------------

def test_ac3_pass_to_ready(tmp_path):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ledger_log = tmp_path / "ledger-calls.log"
    alarm_log = tmp_path / "alarm-calls.log"
    checkout_dir = tmp_path / "co"

    seams = _write_stubs(
        bin_dir,
        verdict_exit=0,
        verdict_output="ready",
        ledger_log=ledger_log,
        checkout_dir=checkout_dir,
        alarm_log=alarm_log,
    )

    spec_id = "042-test-pass"
    _write_gating_file(lane_dir, spec_id)

    result = _run_script(lane_dir, liveness_dir, tmp_path, seam_overrides=seams)
    assert result.returncode == 0, result.stderr

    # .gating.md must be gone
    assert not (lane_dir / f"{spec_id}.gating.md").exists(), \
        ".gating.md should have been renamed to .ready.md"

    # .ready.md must exist with required fields
    ready_file = lane_dir / f"{spec_id}.ready.md"
    assert ready_file.exists(), "Expected .ready.md to be created"
    content = ready_file.read_text()
    assert "graded_by: gating-watch" in content, "graded_by missing from .ready.md"
    assert "graded_at:" in content, "graded_at missing from .ready.md"

    # No .assigned file must have been created (integrator owns that transition)
    assert not list(lane_dir.glob("*.assigned.md")), \
        "Script must not create .assigned files"

    # Ledger set stub must have been called with 'ready'
    assert ledger_log.exists(), "LEDGER_SET_CMD was never called"
    ledger_calls = ledger_log.read_text()
    assert spec_id in ledger_calls
    assert "ready" in ledger_calls

    # Completions log must record a 'pass' entry
    completions = (liveness_dir / "gating-watch-completions").read_text()
    assert f"graded {spec_id} result=pass" in completions


# ---------------------------------------------------------------------------
# AC4: FAIL path → .gating becomes .rework (never .assigned)
# ---------------------------------------------------------------------------

def test_ac4_fail_to_rework(tmp_path):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ledger_log = tmp_path / "ledger-calls.log"
    alarm_log = tmp_path / "alarm-calls.log"
    checkout_dir = tmp_path / "co"

    seams = _write_stubs(
        bin_dir,
        verdict_exit=1,
        verdict_output="regrade",
        ledger_log=ledger_log,
        checkout_dir=checkout_dir,
        alarm_log=alarm_log,
    )

    spec_id = "043-test-fail"
    _write_gating_file(lane_dir, spec_id)

    result = _run_script(lane_dir, liveness_dir, tmp_path, seam_overrides=seams)
    assert result.returncode == 0, result.stderr

    # .gating.md must be gone
    assert not (lane_dir / f"{spec_id}.gating.md").exists(), \
        ".gating.md should have been renamed"

    # .rework.md must exist with rework_reason
    rework_file = lane_dir / f"{spec_id}.rework.md"
    assert rework_file.exists(), "Expected .rework.md to be created"
    content = rework_file.read_text()
    assert "rework_reason:" in content, "rework_reason missing from .rework.md"

    # Must NOT create any .assigned file (integrator's job)
    assert not list(lane_dir.glob("*.assigned.md")), \
        "Script must not create .assigned files — integrator owns that"

    # Ledger stub must have been called with 'rework'
    assert ledger_log.exists(), "LEDGER_SET_CMD was never called"
    ledger_calls = ledger_log.read_text()
    assert spec_id in ledger_calls
    assert "rework" in ledger_calls

    # Completions log must record a 'fail' entry
    completions = (liveness_dir / "gating-watch-completions").read_text()
    assert f"graded {spec_id} result=fail" in completions


# ---------------------------------------------------------------------------
# AC5a: Reap/redispatch → dead stale marker bounces to .rework with
#        rework_reason: gating-runner-inert when counter >= GATING_MAX_REDISPATCH
# ---------------------------------------------------------------------------

def test_ac5a_reap_inert_runner(tmp_path):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ledger_log = tmp_path / "ledger-calls.log"
    alarm_log = tmp_path / "alarm-calls.log"
    checkout_dir = tmp_path / "co"

    seams = _write_stubs(
        bin_dir,
        verdict_exit=0,
        verdict_output="ready",
        ledger_log=ledger_log,
        checkout_dir=checkout_dir,
        alarm_log=alarm_log,
    )
    # GATING_MAX_REDISPATCH=0 → first stale dead marker immediately bounces
    seams["GATING_MAX_REDISPATCH"] = "0"
    # GATING_STALE_SECS=30 — marker is aged further below
    seams["GATING_STALE_SECS"] = "30"

    spec_id = "044-test-reap"
    _write_gating_file(lane_dir, spec_id)

    # Write an in-progress marker with a dead PID
    # Use a PID that definitely doesn't exist on this system
    dead_pid = 99999999
    marker = liveness_dir / f".grading-{spec_id}"
    marker.write_text(f"{dead_pid} 2020-01-01T00:00:00Z\n")

    # Age the marker past GATING_STALE_SECS=30 (set mtime to 60s ago)
    old_mtime = time.time() - 60
    os.utime(marker, (old_mtime, old_mtime))

    result = _run_script(
        lane_dir,
        liveness_dir,
        tmp_path,
        seam_overrides=seams,
    )
    assert result.returncode == 0, result.stderr

    # .gating.md must be gone
    assert not (lane_dir / f"{spec_id}.gating.md").exists(), \
        ".gating.md should be gone after reap"

    # .rework.md must exist with gating-runner-inert reason
    rework_file = lane_dir / f"{spec_id}.rework.md"
    assert rework_file.exists(), "Expected .rework.md after reap"
    content = rework_file.read_text()
    assert "rework_reason: gating-runner-inert" in content, \
        f"Expected 'gating-runner-inert' in rework reason, got: {content}"

    # Must NOT create any .assigned file
    assert not list(lane_dir.glob("*.assigned.md")), \
        "Script must not create .assigned files"

    # Completions log must record 'reaped'
    completions = (liveness_dir / "gating-watch-completions").read_text()
    assert f"graded {spec_id} result=reaped" in completions


# ---------------------------------------------------------------------------
# AC5b: Inert alarm — gating files present, in-progress marker with live PID
#        (so nothing completes), old completions log → stall flag + ALARM_CMD fired
# ---------------------------------------------------------------------------

def test_ac5b_inert_alarm(tmp_path):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ledger_log = tmp_path / "ledger-calls.log"
    alarm_log = tmp_path / "alarm-calls.log"
    checkout_dir = tmp_path / "co"

    seams = _write_stubs(
        bin_dir,
        verdict_exit=0,
        verdict_output="ready",
        ledger_log=ledger_log,
        checkout_dir=checkout_dir,
        alarm_log=alarm_log,
    )
    # GATING_STALE_SECS=1000: live marker is NOT stale (it's freshly written),
    # but the completions log entry from year 2020 IS older than 1000s → alarm fires.
    seams["GATING_STALE_SECS"] = "1000"
    seams["GATING_MAX_REDISPATCH"] = "5"

    spec_id = "045-test-inert"
    _write_gating_file(lane_dir, spec_id)

    # Spawn a long-lived subprocess so we have a real live PID
    sleeper = subprocess.Popen(["sleep", "60"])
    live_pid = sleeper.pid
    try:
        # Write in-progress marker with live PID — script will skip grading
        marker = liveness_dir / f".grading-{spec_id}"
        marker.write_text(f"{live_pid} 2026-07-01T00:00:00Z\n")
        # Marker is fresh (just written), so marker_age < GATING_STALE_SECS=1000 → not stale
        # But process is alive → script takes the "SKIP" path immediately

        # Write an old completions log entry (epoch well in the past)
        completions_log = liveness_dir / "gating-watch-completions"
        completions_log.write_text(
            "2020-01-01T00:00:00Z gating-watch graded 000-old result=pass\n"
        )

        result = _run_script(
            lane_dir,
            liveness_dir,
            tmp_path,
            seam_overrides=seams,
        )
    finally:
        sleeper.kill()
        sleeper.wait()

    assert result.returncode == 0, result.stderr

    # Stall flag must have been raised
    stall_flag = liveness_dir / "GATING_WATCH_STALL"
    assert stall_flag.exists(), \
        "Expected GATING_WATCH_STALL flag to be raised when inert"
    assert "gating-watch inert" in stall_flag.read_text()

    # ALARM_CMD stub must have been invoked
    assert alarm_log.exists(), \
        f"ALARM_CMD was never invoked; stderr: {result.stderr}"
    assert "alarm fired" in alarm_log.read_text()

    # .gating.md must still exist (nothing completed this run)
    assert (lane_dir / f"{spec_id}.gating.md").exists(), \
        ".gating.md should still exist (grading was skipped)"


# ---------------------------------------------------------------------------
# AC6: Single-instance lock — second invocation exits 0 without any grading
# ---------------------------------------------------------------------------

def test_ac6_single_instance_lock(tmp_path):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ledger_log = tmp_path / "ledger-calls.log"
    alarm_log = tmp_path / "alarm-calls.log"
    checkout_dir = tmp_path / "co"

    seams = _write_stubs(
        bin_dir,
        verdict_exit=0,
        verdict_output="ready",
        ledger_log=ledger_log,
        checkout_dir=checkout_dir,
        alarm_log=alarm_log,
    )

    spec_id = "046-test-lock"
    _write_gating_file(lane_dir, spec_id)

    # Hold the lock in this process via fcntl — simulate a running instance
    lock_file = tmp_path / "gating-watch.lock"
    lock_file.touch()
    lock_fd = open(lock_file, "w")  # noqa: SIM115
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Now run the script — it should see the lock held and exit 0 immediately
        result = _run_script(
            lane_dir,
            liveness_dir,
            tmp_path,
            seam_overrides=seams,
            extra_env={"LOCK_FILE": str(lock_file)},
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

    assert result.returncode == 0, f"Script should exit 0 on lock contention: {result.stderr}"

    # Grader stub must NOT have been invoked (lock was held)
    assert not ledger_log.exists() or ledger_log.read_text().strip() == "", \
        "LEDGER_SET_CMD should not have been called when lock is held"

    # .gating.md must still exist (nothing was processed)
    assert (lane_dir / f"{spec_id}.gating.md").exists(), \
        ".gating.md should be untouched when script exits due to lock"


# ---------------------------------------------------------------------------
# AC7: No .gating files → clean exit 0, no stubs invoked
# ---------------------------------------------------------------------------

def test_ac7_empty_lane_clean_exit(tmp_path):
    lane_dir = tmp_path / "lane"
    lane_dir.mkdir()
    liveness_dir = tmp_path / "liveness"
    liveness_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ledger_log = tmp_path / "ledger-calls.log"
    alarm_log = tmp_path / "alarm-calls.log"
    checkout_dir = tmp_path / "co"

    seams = _write_stubs(
        bin_dir,
        ledger_log=ledger_log,
        checkout_dir=checkout_dir,
        alarm_log=alarm_log,
    )

    # Lane dir is empty (no .gating.md files)
    result = _run_script(lane_dir, liveness_dir, tmp_path, seam_overrides=seams)

    assert result.returncode == 0, f"Expected clean exit 0 on empty lane: {result.stderr}"

    # No stubs should have been invoked
    assert not ledger_log.exists() or ledger_log.read_text().strip() == "", \
        "LEDGER_SET_CMD should not be called with empty lane"
    assert not alarm_log.exists() or alarm_log.read_text().strip() == "", \
        "ALARM_CMD should not be called with empty lane"

    # Completions log should not exist (nothing happened)
    completions_log = liveness_dir / "gating-watch-completions"
    assert not completions_log.exists() or completions_log.read_text().strip() == "", \
        "Completions log should be empty when no .gating files present"
