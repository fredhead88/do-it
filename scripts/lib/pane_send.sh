#!/bin/bash
# scripts/lib/pane_send.sh — shared tmux pane-send helper for DO-IT cadence scripts.
#
# Exposes:
#   pane_send_message PANE TEXT LOGFILE LABEL
#
# Fixes the "dead-Enter" bug: when text+Enter are sent in a single tmux call
# the TUI composer absorbs the Enter as a literal newline (bracket/paste mode),
# so the message stacks un-submitted while the caller logs "success" anyway.
#
# Strategy:
#   1. Send TEXT literally with -l (avoids tmux interpreting leading / or emoji
#      as a key name).  sleep 0.3.  Send Enter as a SEPARATE keystroke.
#   2. Belt-and-suspenders: sleep 0.2 more, then send a second Enter (handles
#      rare cases where the first Enter lands before the compositor is ready).
#   3. Verify: capture the bottom 3 lines of the pane (the composer area).
#      If a distinctive trailing slice of TEXT is still sitting there the
#      message did NOT submit — log a FAILURE line and return 1.
#      Only log success if the slice is absent from the bottom.
#
# Args:
#   PANE    — tmux pane target (e.g. "orc-session:0.0" or "%3")
#   TEXT    — the message to send
#   LOGFILE — absolute path of the calling script's log file
#   LABEL   — short label for log lines (e.g. "nudge" or "heartbeat")
#
# Requires: ts() function defined in the calling script (source this AFTER ts()).
#
# Usage (caller):
#   source "$(dirname "$0")/lib/pane_send.sh"
#   pane_send_message "$PANE" "$MSG" "$LOG" "nudge" || handle_failure

pane_send_message() {
  local PANE="$1"
  local TEXT="$2"
  local LOGFILE="$3"
  local LABEL="${4:-send}"

  # Self-contained timestamp: prefer the caller's ts() (preserves its log format)
  # but fall back to ISO-UTC so this helper never depends on the caller having
  # defined ts() (a dead-Enter fix must not itself emit "ts: command not found").
  local _now
  if command -v ts >/dev/null 2>&1; then _now="$(ts)"; else _now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; fi

  # ── Step 1: Send text literally, then Enter after a short delay ──────────────
  # -l sends text literally so leading "/" or emoji is not interpreted as a tmux
  # key name. Without -l, "/orc" would be treated as a key sequence, not typed.
  tmux send-keys -t "$PANE" -l "$TEXT" 2>/dev/null
  sleep 0.3
  tmux send-keys -t "$PANE" Enter 2>/dev/null

  # ── Step 2: Belt-and-suspenders second Enter ─────────────────────────────────
  # Handles rare cases where the first Enter lands before the TUI compositor has
  # finished registering the pasted text and is still in bracket/paste mode.
  sleep 0.2
  tmux send-keys -t "$PANE" Enter 2>/dev/null

  # ── Step 3: Verify the message submitted ─────────────────────────────────────
  # Give the TUI a moment to process the keystrokes before we capture.
  sleep 0.2

  # Extract a distinctive trailing slice of TEXT (last 30 chars).
  # We check whether this slice still appears in the BOTTOM 3 lines of the pane
  # (the composer / input box area). A submitted message moves up into the
  # transcript scroll — the composer area clears. Presence in the bottom lines
  # means Enter was NOT registered as a submit.
  local text_len="${#TEXT}"
  local slice_len=30
  if [ "$text_len" -le "$slice_len" ]; then
    slice_len="$text_len"
  fi
  local trail_slice="${TEXT:$(( text_len - slice_len ))}"

  # Capture only the bottom 3 lines of the pane (composer area).
  local bottom_lines
  bottom_lines="$(tmux capture-pane -p -t "$PANE" 2>/dev/null | tail -3)"

  if echo "$bottom_lines" | grep -qF "$trail_slice" 2>/dev/null; then
    # Trailing text still in composer — message did NOT submit.
    echo "$_now FAILURE: ${LABEL} to pane ${PANE} did NOT submit — trailing slice still in composer; bottom: $(echo "$bottom_lines" | tr '\n' '|')" >>"$LOGFILE"
    return 1
  fi

  echo "$_now ${LABEL} submitted to pane ${PANE} (verified — trailing slice absent from composer)" >>"$LOGFILE"
  return 0
}
