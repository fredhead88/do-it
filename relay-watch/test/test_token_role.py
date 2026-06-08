import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "orc-token-watch.py"


def _run(role, pane, sid, transcript):
    env = {**os.environ, "ROLE": role, "TMUX_PANE": pane, "ORC_WATCH_THRESHOLD": "1"}
    hook_in = json.dumps(
        {"session_id": sid, "cwd": "/tmp", "transcript_path": transcript}
    )
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=hook_in,
        env=env,
        capture_output=True,
        text=True,
    )


def test_rev_role_writes_rev_sentinel_and_boot(tmp_path):
    active = Path("/tmp/rev-active")
    sentinel = Path("/tmp/rev-handoff-due-sid1")
    active.write_text("PANE=%7\n")
    # a transcript with a usage block over threshold
    t = tmp_path / "sid1.jsonl"
    t.write_text(json.dumps({"message": {"usage": {"input_tokens": 999999}}}) + "\n")
    try:
        r = _run("rev", "%7", "sid1", str(t))
        assert r.returncode == 0
        assert sentinel.exists()
        # the injected message must reference the rev baton + /rev, not orc
        out = r.stdout
        assert "rev-relay.md" in out and "/rev" in out
    finally:
        sentinel.unlink(missing_ok=True)
        active.unlink(missing_ok=True)


def test_wrong_pane_is_noop(tmp_path):
    active = Path("/tmp/rev-active")
    sentinel = Path("/tmp/rev-handoff-due-sid2")
    active.write_text("PANE=%7\n")
    t = tmp_path / "sid2.jsonl"
    t.write_text(json.dumps({"message": {"usage": {"input_tokens": 999999}}}) + "\n")
    try:
        r = _run("rev", "%DIFFERENT", "sid2", str(t))
        assert r.returncode == 0 and r.stdout.strip() == ""
        assert not sentinel.exists()
    finally:
        sentinel.unlink(missing_ok=True)
        active.unlink(missing_ok=True)
