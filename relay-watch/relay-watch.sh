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
#   ORC_RELAY_FILE  — override the derived <cwd>/docs/sessions/orc-relay.md
#   ORC_WATCH_DRY=1 — log what would happen instead of sending keys
#   ORC_QUIET_SECS  — seconds of transcript silence required (default 45)
set -u

ROLE="${ROLE:-orc}"
BOOT_CMD="${ROLE_BOOT_CMD:-/$ROLE}"
QUIET_SECS="${ORC_QUIET_SECS:-45}"
DRY="${ORC_WATCH_DRY:-0}"
LOG="/tmp/${ROLE}-relay-watch.log"
LOCK="/tmp/${ROLE}-relay-watch.lock"

exec 9>"$LOCK"
flock -n 9 || exit 0

ts() { date -u +%FT%TZ; }

for sentinel in /tmp/${ROLE}-handoff-due-*; do
  [ -e "$sentinel" ] || continue

  PANE="" SESSION_ID="" TRANSCRIPT="" CWD="" CONTEXT=""
  # shellcheck disable=SC1090
  . "$sentinel"

  RELAY="${ORC_RELAY_FILE:-$CWD/docs/sessions/${ROLE}-relay.md}"

  # 1. Baton actually written? (the skill stamps RESUMED on pickup, so
  #    HANDED-OFF here can only mean this handoff is pending)
  head -1 "$RELAY" 2>/dev/null | grep -q "status: HANDED-OFF" || continue

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
    echo "$(ts) DRY RUN: would /clear + $BOOT_CMD pane $PANE (context was ${CONTEXT:-?})" >>"$LOG"
    continue
  fi

  echo "$(ts) restarting $ROLE in pane $PANE (session $SESSION_ID, context ${CONTEXT:-?})" >>"$LOG"
  tmux send-keys -t "$PANE" "/clear" Enter
  sleep 6
  tmux send-keys -t "$PANE" "$BOOT_CMD" Enter
  rm -f "$sentinel"
done
