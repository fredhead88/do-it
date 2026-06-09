#!/bin/bash
# relay-watch.sh — outer half of the automated orc relay loop.
#
# Cron runs this every minute. When the orc-token-watch hook has flagged a
# handoff (sentinel in /tmp) AND the orc has actually written the baton
# (docs/sessions/orc-relay.md status: HANDED-OFF) AND the session has gone
# quiet, it sends /clear + /orc to the orc's tmux pane — the keystrokes you
# used to type by hand.
#
# Project-agnostic: the relay file path is derived from the CWD the hook
# recorded in the sentinel, so one cron line serves every repo running DO-IT.
#
# Env overrides (mainly for testing):
#   ORC_RELAY_FILE   — override the derived <cwd>/docs/sessions/orc-relay.md
#   ORC_WATCH_DRY=1  — log what would happen instead of sending keys
#   ORC_QUIET_SECS   — seconds of transcript silence required (default 45)
#   BATON_FRESH_SECS — max baton age (mtime) to honor; refuse stale (default 5400 = 90m)
#
# F11/F12 hardening (2026-06-09) — see CHANGELOG v3.7.0 / docs/2026-06-09-relay-hardening:
#   F11: scan baton head for `status:`, not just line 1 (rev's title is on L1).
#   F12: only relay a baton that is FRESH (mtime ≤ BATON_FRESH_SECS), atomically
#        COMPLETE (status + handed_off_at both present), from the NEWEST sentinel
#        for its pane (manual-reboot identity guard), and UNCONSUMED (stamped
#        after one relay so the next cron tick can't /clear the new session again).
set -u

ROLE="${ROLE:-orc}"
BOOT_CMD="${ROLE_BOOT_CMD:-/$ROLE}"
QUIET_SECS="${ORC_QUIET_SECS:-45}"
FRESH_SECS="${BATON_FRESH_SECS:-5400}"
DRY="${ORC_WATCH_DRY:-0}"
LOG="/tmp/${ROLE}-relay-watch.log"
LOCK="/tmp/${ROLE}-relay-watch.lock"

exec 9>"$LOCK"
flock -n 9 || exit 0

ts() { date -u +%FT%TZ; }

# --- Manual-reboot identity guard (F12) -------------------------------------
# If a pane was manually rebooted, a stale sentinel from the dead generation can
# still be sitting in /tmp pointing at the OLD (now-quiet) transcript — firing on
# it would /clear the NEW session running in that same pane. Defence: when more
# than one sentinel names the same PANE, keep only the NEWEST and drop the rest.
declare -A _seen_pane
for sentinel in $(ls -t /tmp/${ROLE}-handoff-due-* 2>/dev/null); do
  [ -e "$sentinel" ] || continue
  p=$(grep -m1 '^PANE=' "$sentinel" 2>/dev/null | cut -d= -f2-)
  [ -n "$p" ] || continue
  if [ -n "${_seen_pane[$p]:-}" ]; then
    echo "$(ts) superseded sentinel $sentinel (newer exists for pane $p); dropping" >>"$LOG"
    rm -f "$sentinel"
  else
    _seen_pane[$p]=$sentinel
  fi
done

for sentinel in /tmp/${ROLE}-handoff-due-*; do
  [ -e "$sentinel" ] || continue

  PANE="" SESSION_ID="" TRANSCRIPT="" CWD="" CONTEXT=""
  # shellcheck disable=SC1090
  . "$sentinel"

  RELAY="${ORC_RELAY_FILE:-$CWD/docs/sessions/${ROLE}-relay.md}"

  # 1. Baton actually written, HANDED-OFF? (the skill stamps RESUMED on pickup,
  #    so HANDED-OFF here can only mean this handoff is still pending). F11: scan
  #    the head, not just line 1 — rev's baton has its H1 title on line 1.
  grep -qE '^status:[[:space:]]*HANDED-OFF' "$RELAY" 2>/dev/null || continue

  # 1b. Atomic completeness (F12): a tmp-then-rename baton has BOTH a status and
  #     a handed_off_at line. A half-written file (status but no handed_off_at,
  #     or vice versa) is refused — do not act on a partial baton.
  grep -qE '^handed_off_at:' "$RELAY" 2>/dev/null || {
    echo "$(ts) baton $RELAY incomplete (no handed_off_at); skipping" >>"$LOG"
    continue
  }

  # 1c. Freshness gate (F12): refuse a stale baton. Use the baton FILE MTIME, not
  #     the prose handed_off_at — orc writes ISO, rev writes "2026-06-08 ~19:56
  #     UTC" (tilde, prose); mtime is the one format-agnostic, tmp-then-rename-
  #     accurate signal. A baton older than BATON_FRESH_SECS is a leftover from a
  #     dead cycle, not a live handoff.
  baton_age=$(( $(date +%s) - $(stat -c %Y "$RELAY" 2>/dev/null || echo 0) ))
  if [ "$baton_age" -ge "$FRESH_SECS" ]; then
    echo "$(ts) baton $RELAY stale (${baton_age}s ≥ ${FRESH_SECS}s); refusing relay" >>"$LOG"
    continue
  fi

  # 1d. Consume-once (F12): a marker keyed to baton mtime. Once we relay a given
  #     baton (identified by its mtime), the next cron tick must NOT /clear again
  #     — the new session may not have stamped RESUMED yet. mtime changes when the
  #     baton is rewritten, so a genuinely new handoff gets a new marker.
  baton_mtime=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
  CONSUMED="/tmp/${ROLE}-relay-consumed-${PANE//[^a-zA-Z0-9]/_}"
  if [ -f "$CONSUMED" ] && [ "$(cat "$CONSUMED" 2>/dev/null)" = "$baton_mtime" ]; then
    echo "$(ts) baton $RELAY already consumed (mtime $baton_mtime); skipping" >>"$LOG"
    rm -f "$sentinel"
    continue
  fi

  # 2. Session quiet (orc finished its final message)?
  if [ ! -f "$TRANSCRIPT" ]; then
    echo "$(ts) transcript gone for $SESSION_ID; dropping sentinel" >>"$LOG"
    rm -f "$sentinel"
    continue
  fi
  age=$(( $(date +%s) - $(stat -c %Y "$TRANSCRIPT") ))
  [ "$age" -ge "$QUIET_SECS" ] || continue

  # 3. Pane still alive?
  if ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$PANE"; then
    echo "$(ts) pane $PANE gone; dropping sentinel for $SESSION_ID" >>"$LOG"
    rm -f "$sentinel"
    continue
  fi

  if [ "$DRY" = "1" ]; then
    echo "$(ts) DRY RUN: would /clear + $BOOT_CMD pane $PANE (context ${CONTEXT:-?}, baton_age ${baton_age}s)" >>"$LOG"
    continue
  fi

  echo "$(ts) restarting $ROLE in pane $PANE (session $SESSION_ID, context ${CONTEXT:-?}, baton_age ${baton_age}s)" >>"$LOG"
  tmux send-keys -t "$PANE" "/clear" Enter
  sleep 6
  tmux send-keys -t "$PANE" "$BOOT_CMD" Enter
  echo "$baton_mtime" > "$CONSUMED"   # consume-once: pin this baton's mtime
  rm -f "$sentinel"
done
