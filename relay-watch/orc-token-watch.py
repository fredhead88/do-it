#!/usr/bin/env python3
"""PostToolUse hook: token-threshold trigger for the automated orc relay loop.

Watches the live session's context size (read from the transcript's usage
blocks — Anthropic's own accounting, nothing estimated) and, past the
threshold, injects a one-time instruction telling the orchestrator to write
the relay baton and stop. A sentinel file in /tmp signals relay-watch.sh
(cron, every minute) to /clear + /orc the pane once the baton lands.

Armed only when /tmp/orc-active records this pane's $TMUX_PANE — the orc
skill writes that file at boot (First moves, step 0). Every other session
(thinkers, unrelated projects) is an instant no-op.

Threshold: ORC_WATCH_THRESHOLD env var, default 400000. That default assumes
a 1M context window; on a 200k window use ~160000 so the handoff fires
before any lossy auto-compaction.

Stdlib only — no dependencies. See SETUP.md for registration.
"""

import json
import os
import sys
import time

THRESHOLD = int(os.environ.get("ORC_WATCH_THRESHOLD", "400000"))
# Deadlock fix: arm the relay sentinel at the SOFT line, not the hard threshold.
# A handoff in the soft..hard band used to leave no sentinel -> the cron never read
# the HANDED-OFF baton -> the session wedged forever (2026-06-09: orc @371k + rev
# @384k both stood down between 360k and 400k and neither relayed). The hard line
# only escalates the nudge text now; the sentinel (what lets the cron act) is dropped
# at SOFT, so any handoff at or above the soft line relays.
SOFT = int(os.environ.get("ORC_WATCH_SOFT", str(int(THRESHOLD * 0.9))))
ROLE = os.environ.get("ROLE", "orc")
BOOT_CMD = os.environ.get("ROLE_BOOT_CMD", f"/{ROLE}")
ACTIVE = f"/tmp/{ROLE}-active"
REMIND_SECS = 600  # re-inject at most every 10 min if orc hasn't handed off yet
TAIL_BYTES = 2_000_000


def read_kv(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
    except OSError:
        pass
    return out


def current_context(transcript_path):
    """Exact live context size: usage of the latest non-sidechain assistant msg."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return 0
    for line in reversed(tail.splitlines()):
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("isSidechain"):
            continue
        u = (d.get("message") or {}).get("usage") or {}
        if u.get("input_tokens") is None:
            continue
        return (
            (u.get("input_tokens") or 0)
            + (u.get("cache_read_input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0)
        )
    return 0


def main():
    try:
        hook = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    pane = os.environ.get("TMUX_PANE", "")
    active = read_kv(ACTIVE)
    if not pane or active.get("PANE") != pane:
        return  # not the armed orc pane

    sid = hook.get("session_id") or ""
    cwd = hook.get("cwd") or os.getcwd()
    transcript = hook.get("transcript_path") or ""
    # Always measure the MAIN session transcript, even if a subagent fired the hook.
    if sid:
        main_t = os.path.join(os.path.dirname(transcript) or ".", f"{sid}.jsonl")
        if os.path.exists(main_t):
            transcript = main_t
    if not transcript or not os.path.exists(transcript):
        return

    ctx = current_context(transcript)
    if ctx < SOFT:
        return
    hard = ctx >= THRESHOLD

    sentinel = f"/tmp/{ROLE}-handoff-due-{sid or 'unknown'}"
    if (
        os.path.exists(sentinel)
        and time.time() - os.path.getmtime(sentinel) < REMIND_SECS
    ):
        return  # already told it; don't nag every tool call

    with open(sentinel, "w") as f:
        f.write(
            f"PANE={pane}\nSESSION_ID={sid}\nTRANSCRIPT={transcript}\n"
            f"CWD={cwd}\nCONTEXT={ctx}\n"
        )

    band = "HARD" if hard else "SOFT"
    urgency = (
        "Finish ONLY the current atomic step, then"
        if hard
        else "Reach the next natural break (finish the in-flight task), then"
    )
    msg = (
        f"{ROLE.upper()} CONTEXT WATCH ({band}): live context is {ctx:,} tokens "
        f"(soft {SOFT:,} / hard {THRESHOLD:,}). This is an observable relay signal per "
        f"the {ROLE} skill. {urgency} write the relay baton "
        f"(docs/sessions/{ROLE}-relay.md, status: HANDED-OFF, tmp-then-rename) and STOP "
        f"— do not start new work. The relay watcher will /clear this pane and boot a "
        f"fresh {BOOT_CMD} automatically once the baton lands; you do not need to tell "
        f"the user to do it. (The sentinel is already armed, so your HANDED-OFF baton "
        f"will relay even though you are below the hard ceiling.)"
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": msg,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
