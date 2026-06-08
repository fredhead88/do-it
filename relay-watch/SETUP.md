# Relay-Watch Setup — the automated orc baton loop

Closes the last manual step in DO-IT: when an orchestrator session fills its
context, you no longer type "hand over the baton", `/clear`, `/orc`. The loop
runs itself:

```
orc works ──▶ hook sees context ≥ threshold ──▶ injects "write baton + STOP"
                                                        │
fresh /orc boots ◀── cron watcher sends /clear + /orc ◀─┘ (baton written, quiet)
```

Verified in production 2026-06-07: an orc handed off at 400,005 tokens and its
successor was reconciling the tree 4 minutes later, zero keystrokes.

## How it works

Two watchers, split across a hard boundary — code *inside* a session (a hook)
can talk to the model but can't restart the session; code *outside* (tmux)
can restart it but can't see token counts:

1. **`orc-token-watch.py`** — a Claude Code PostToolUse hook. After every tool
   call it reads the session transcript's last `usage` block;
   `input + cache_read + cache_creation` is the exact live context size. Past
   the threshold it injects an "ORC CONTEXT WATCH" message (the orc skill
   treats this as an official relay signal: write the baton, then STOP) and
   writes a sentinel to `/tmp/orc-handoff-due-<session>`.
2. **`relay-watch.sh`** — a cron job, every minute. When the sentinel exists
   AND the baton says `status: HANDED-OFF` AND the transcript has been quiet
   45s AND the pane is alive, it sends `/clear`, waits, sends `/orc`, and
   deletes the sentinel.

**Scoping:** the hook only acts in the pane recorded in `/tmp/orc-active`,
which the orc skill writes at boot (First moves, step 0). Thinker sessions,
other projects, anything else — instant no-op.

## Requirements

- The orc session runs inside **tmux** (the watcher restarts it via
  `tmux send-keys`). No tmux → the hook still fires and the orc still writes
  the baton, but the restart stays manual.
- `python3` (stdlib only), `flock`, cron.
- The DO-IT orc skill v3.2.0+ (has the arming step and the STOP-after-baton
  behavior).

## Install

1. **Register the hook** in your project's `.claude/settings.json`
   (`hooks.PostToolUse`, empty matcher = all tools):

   ```json
   {
     "matcher": "",
     "hooks": [
       {
         "type": "command",
         "command": "python3 /path/to/do-it/relay-watch/orc-token-watch.py",
         "timeout": 10
       }
     ]
   }
   ```

2. **Install the cron** (one line serves all your DO-IT repos — the sentinel
   carries the project path):

   ```
   * * * * * /path/to/do-it/relay-watch/relay-watch.sh
   ```

3. **Pick a threshold.** Default 400000 assumes a 1M context window. On a
   200k window set ~160000 — the point is to hand off *before* lossy
   auto-compaction, with a deliberate baton instead of a machine summary.
   Override via env in the hook entry:

   ```json
   "command": "ORC_WATCH_THRESHOLD=160000 python3 /path/to/.../orc-token-watch.py"
   ```

That's it. The next `/orc` boot arms itself.

## Operations

- **Audit trail:** `/tmp/orc-relay-watch.log` — one line per generation
  turnover.
- **Pause the automation** (keep a session past the threshold):
  `rm /tmp/orc-active`. The next `/orc` boot re-arms.
- **Covering an already-running orc** (booted before install): tell it to run
  `printf "PANE=%s\n" "$TMUX_PANE" > /tmp/orc-active`.
- **Testing without sending keys:** `ORC_WATCH_DRY=1 relay-watch.sh` logs
  what it would do; `ORC_RELAY_FILE` and `ORC_QUIET_SECS` override the other
  gates. To exercise the hook: arm `/tmp/orc-active` with your pane and run
  it with `ORC_WATCH_THRESHOLD=1000` and a fake stdin payload
  (`{"session_id":"...","transcript_path":"..."}`).

## Failure modes (all fail safe = fall back to manual)

- Orc not in tmux / pane closed → sentinel dropped, logged.
- Baton never written (orc ignored the signal) → watcher waits forever; the
  hook re-reminds every 10 minutes.
- Two repos handing off simultaneously → fine; sentinels are per-session and
  carry their own project path.
- The `/clear`//`/orc` keystrokes go through Claude Code's slash-command
  menu; exact match ranks first. If a future skill name shadows `/orc`,
  add a trailing space to the send-keys strings.

## Standing `rev` (the reviewer twin)

`rev` self-relays with the SAME scripts, role-scoped via `ROLE=rev`:

1. Register a second PostToolUse hook entry:
   ```json
   { "matcher": "", "hooks": [ { "type": "command",
     "command": "ROLE=rev python3 /path/to/.../orc-token-watch.py", "timeout": 10 } ] }
   ```
2. Add a second cron line:
   ```
   * * * * * ROLE=rev /path/to/.../relay-watch.sh
   ```
3. Liveness (the dead-man's switch — run every 30 min):
   ```
   */30 * * * * /path/to/.../liveness.sh verifier; /path/to/.../liveness.sh pane orc; /path/to/.../liveness.sh pane rev; /path/to/.../liveness.sh hook orc /path/to/repo/.claude/settings.json; /path/to/.../liveness.sh hook rev /path/to/repo/.claude/settings.json
   ```
   A missing hook (the 2026-06-08 silent break) now raises `*_HOOK_MISSING` on the
   board instead of failing silently.
