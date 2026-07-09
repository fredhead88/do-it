"""Tests for scripts/orc_deploy_verdict.py.

All tests are hermetic — they never run the real ./deploy.sh.
Unit tests import core functions directly; one end-to-end test
invokes the script via subprocess with a harmless --command.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


# Import the module under test directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.orc_deploy_verdict import (
    build_note,
    derive_health_from_log,
    extract_sha,
    produce_verdict,
    write_log,
)

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "orc_deploy_verdict.py"

# ---------------------------------------------------------------------------
# Synthetic log fixtures
# ---------------------------------------------------------------------------

_SUCCESS_LOG = (
    "deploying...\nHEAD is now at abc1234\nhealth check passed\ndeploy complete\n"
)

_FAILURE_LOG = (
    "deploying...\n"
    "HEAD is now at def5678\n"
    "Traceback (most recent call last):\n"
    "  File 'deploy.sh', line 42\n"
    "RuntimeError: alembic upgrade failed\n"
)

_AMBIGUOUS_LOG = "deploying...\nfinished.\n"  # no success or failure markers


# ---------------------------------------------------------------------------
# extract_sha
# ---------------------------------------------------------------------------


def test_extract_sha_from_success_log():
    assert extract_sha(_SUCCESS_LOG) == "abc1234"


def test_extract_sha_from_failure_log():
    assert extract_sha(_FAILURE_LOG) == "def5678"


def test_extract_sha_unknown_when_no_marker():
    # A log with no SHA marker — will try git or return "unknown".
    result = extract_sha("no sha here at all\n")
    # Should be a hex string (from git) or "unknown" — never empty.
    assert result  # non-empty
    assert result == "unknown" or all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# derive_health_from_log
# ---------------------------------------------------------------------------


def test_health_pass_on_success_log():
    assert derive_health_from_log(_SUCCESS_LOG) == "pass"


def test_health_fail_on_traceback():
    assert derive_health_from_log(_FAILURE_LOG) == "fail"


def test_health_fail_on_nonzero_returncode():
    assert derive_health_from_log(_SUCCESS_LOG, returncode=1) == "fail"


def test_health_fail_when_ambiguous():
    # No success marker → fail (conservative).
    assert derive_health_from_log(_AMBIGUOUS_LOG) == "fail"


def test_health_fail_explicit_failure_keyword():
    log = "deploy complete\nerror: something went wrong\n"
    assert derive_health_from_log(log) == "fail"


# ---------------------------------------------------------------------------
# write_log
# ---------------------------------------------------------------------------


def test_write_log_creates_parents(tmp_path):
    target = tmp_path / "deep" / "nested" / "deploy.log"
    write_log("some log content\n", target)
    assert target.exists()
    assert target.read_text() == "some log content\n"


# ---------------------------------------------------------------------------
# produce_verdict — from_log mode
# ---------------------------------------------------------------------------


def test_produce_verdict_from_log_success(tmp_path):
    src = tmp_path / "src.log"
    src.write_text(_SUCCESS_LOG)
    dest = tmp_path / "out.log"

    verdict = produce_verdict(from_log=str(src), log_file=dest)

    assert set(verdict.keys()) == {"deployed_sha", "health_check", "note", "log_file"}
    assert verdict["health_check"] == "pass"
    assert verdict["deployed_sha"] == "abc1234"
    assert dest.exists()
    assert _SUCCESS_LOG in dest.read_text()


def test_produce_verdict_from_log_failure(tmp_path):
    src = tmp_path / "fail.log"
    src.write_text(_FAILURE_LOG)
    dest = tmp_path / "out.log"

    verdict = produce_verdict(from_log=str(src), log_file=dest)

    assert verdict["health_check"] == "fail"
    assert verdict["deployed_sha"] == "def5678"


def test_produce_verdict_same_src_and_dest(tmp_path):
    # --from-log and --log-file pointing at the same file — must not error.
    src = tmp_path / "same.log"
    src.write_text(_SUCCESS_LOG)

    verdict = produce_verdict(from_log=str(src), log_file=src)
    assert verdict["health_check"] == "pass"


# ---------------------------------------------------------------------------
# produce_verdict — command mode (harmless shell command)
# ---------------------------------------------------------------------------


def test_produce_verdict_command_mode(tmp_path):
    log_file = tmp_path / "cmd.log"
    cmd = "printf 'deploying...\\nHEAD is now at abc1234\\nhealth check passed\\ndeploy complete\\n'"

    verdict = produce_verdict(command=cmd, log_file=log_file)

    assert set(verdict.keys()) == {"deployed_sha", "health_check", "note", "log_file"}
    assert verdict["health_check"] == "pass"
    assert verdict["deployed_sha"] == "abc1234"
    assert log_file.exists()
    assert "abc1234" in log_file.read_text()


# ---------------------------------------------------------------------------
# End-to-end subprocess test
# ---------------------------------------------------------------------------


def test_e2e_subprocess_stdout_is_compact_json(tmp_path):
    """The script must emit <=5 lines of valid JSON with exactly 4 keys.
    The raw log body must NOT appear in stdout."""
    log_file = tmp_path / "e2e.log"
    cmd = "printf 'deploying...\\nHEAD is now at abc1234\\nhealth check passed\\ndeploy complete\\n'"

    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--command",
            cmd,
            "--log-file",
            str(log_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0, f"script failed: {result.stderr}"

    stdout = result.stdout
    lines = [l for l in stdout.splitlines() if l.strip()]

    # Must be <= 5 lines.
    assert len(lines) <= 5, f"stdout has {len(lines)} lines: {stdout!r}"

    # Must be valid JSON with exactly 4 keys.
    verdict = json.loads(stdout)
    assert set(verdict.keys()) == {"deployed_sha", "health_check", "note", "log_file"}

    # Health check must pass for the success-marker log.
    assert verdict["health_check"] == "pass"

    # SHA must be extracted correctly.
    assert verdict["deployed_sha"] == "abc1234"

    # The raw log file must exist and contain the full log body.
    assert log_file.exists()
    raw = log_file.read_text()
    assert "health check passed" in raw
    assert "deploy complete" in raw

    # The distinctive multi-line raw log body must NOT appear in stdout.
    assert "health check passed\ndeploy complete" not in stdout
    assert "deploying..." not in stdout


def test_e2e_subprocess_failure_log(tmp_path):
    """A log with Traceback must produce health_check == 'fail'."""
    log_file = tmp_path / "fail.log"
    cmd = "printf 'deploying...\\nTraceback (most recent call last):\\n  RuntimeError: boom\\n'"

    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--command",
            cmd,
            "--log-file",
            str(log_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0
    verdict = json.loads(result.stdout)
    assert verdict["health_check"] == "fail"


# ---------------------------------------------------------------------------
# build_note smoke tests
# ---------------------------------------------------------------------------


def test_build_note_pass():
    note = build_note("pass", "./deploy.sh", 0, None)
    assert "deploy.sh" in note
    assert "pass" in note.lower() or "completed" in note.lower()


def test_build_note_fail_nonzero():
    note = build_note("fail", "./deploy.sh", 1, None)
    assert "1" in note


def test_build_note_with_health_url():
    note = build_note("pass", "./deploy.sh", 0, "http://example.com/health")
    assert "200" in note
    assert "example.com" in note
