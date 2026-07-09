#!/usr/bin/env bash
# liveness.sh — dead-man's switch for the review loop. Run by cron.
#   liveness.sh verifier                — VERIFIER_DOWN if PROGRESS.jsonl is stale
#   liveness.sh verifier-retire [reason] — mark automated verifier intentionally off (VERIFIER_RETIRED)
#   liveness.sh verifier-unretire       — clear VERIFIER_RETIRED marker
#   liveness.sh handoff-due <role>      — {ROLE^^}_HANDOFF_STALE if /tmp/<role>-handoff-due-* sentinel undelivered
#   liveness.sh pane <role> [--standing] — {ROLE}_DOWN if /tmp/<role>-active points at a
#       dead pane, or (--standing) if the role is not armed at all (always-on roles).
#   liveness.sh hook <role> <settings.json> — {ROLE}_HOOK_MISSING if not registered
# Flags are written under LIVENESS_FLAG (default ~/.claude/ledger/liveness) so the
# ledger render surfaces them. A cleared condition removes its flag.
set -u
FLAG_DIR="${LIVENESS_FLAG:-$HOME/.claude/ledger/liveness}"
mkdir -p "$FLAG_DIR"
ts() { date -u +%FT%TZ; }
raise() { echo "$(ts) $1: $2" > "$FLAG_DIR/$1"; }
drop() { rm -f "$FLAG_DIR/$1"; }

case "${1:-}" in
  verifier)
      # R1: read the CANONICAL runs dir where the verifier writer (~/.claude/
      # verification-loop/tick.mjs) actually lands PROGRESS.jsonl — NOT the divergent
      # $HOME/do-it/... default that never receives runs. VL_RUNS_DIR stays the knob.
      RUNS="${VL_RUNS_DIR:-$HOME/.claude/verification-loop/runs}"
      STALE_MIN="${VERIFIER_STALE_MIN:-90}"
      # R2: retirement is an explicit positive state. When the operator has retired the
      # automated verifier, do NOT raise VERIFIER_DOWN — the VERIFIER_RETIRED marker
      # itself renders on the board (distinct from DOWN, distinct from a green/no-flag).
      if [ -f "$FLAG_DIR/VERIFIER_RETIRED" ]; then drop VERIFIER_DOWN; exit 0; fi
      latest="$(ls -1dt "$RUNS"/*/PROGRESS.jsonl 2>/dev/null | head -1)"
      if [ -z "$latest" ]; then raise VERIFIER_DOWN "no PROGRESS.jsonl found under $RUNS"; exit 0; fi
      age_min=$(( ( $(date +%s) - $(stat -c %Y "$latest") ) / 60 ))
      if [ "$age_min" -gt "$STALE_MIN" ]; then raise VERIFIER_DOWN "PROGRESS.jsonl stale ${age_min}m (> ${STALE_MIN}m) under $RUNS"; else drop VERIFIER_DOWN; fi
      ;;
    verifier-retire)
      # R2: operator explicitly marks the automated verifier intentionally off.
      reason="${2:-intentionally retired; automated verifier off — primary review is hand-walked live-PG verdicts}"
      echo "$(ts) VERIFIER RETIRED — $reason" > "$FLAG_DIR/VERIFIER_RETIRED"
      drop VERIFIER_DOWN
      ;;
    verifier-unretire)
      drop VERIFIER_RETIRED
      ;;
    handoff-due)
      # R3: a HANDED-OFF sentinel that never delivered must go loud within minutes.
      # /tmp/<role>-handoff-due-* files are written by the token-watch hook and (post
      # DO-IT v3.7) consumed by nobody; a legacy SESSION_ID-guarded one sat undelivered
      # 27 days. Surface any older than HANDOFF_DUE_MAX_AGE so the never-armed-guard
      # class can never be silent again.
      role="${2:?handoff-due needs a role}"
      glob="${HANDOFF_DUE_GLOB:-/tmp/${role}-handoff-due-*}"
      max_age="${HANDOFF_DUE_MAX_AGE:-300}"
      flag="${role^^}_HANDOFF_STALE"
      now="$(date +%s)"; oldest=""; oldest_age=0
      for f in $glob; do
        [ -f "$f" ] || continue
        a=$(( now - $(stat -c %Y "$f" 2>/dev/null || echo "$now") ))
        if [ "$a" -gt "$max_age" ] && [ "$a" -ge "$oldest_age" ]; then oldest="$f"; oldest_age="$a"; fi
      done
      if [ -n "$oldest" ]; then
        raise "$flag" "handoff-due sentinel $oldest undelivered ${oldest_age}s (> ${max_age}s) — check baton_token guard (legacy SESSION_ID sentinels never relay)"
      else
        drop "$flag"
      fi
      ;;
  pane)
    role="${2:?role}"; active="/tmp/${role}-active"
    # --standing flag: always-on roles (e.g. watcher) raise DOWN even when not armed.
    standing=0; [ "${3:-}" = "--standing" ] && standing=1
    if [ ! -f "$active" ]; then
      if [ "$standing" = "1" ]; then
        raise "${role^^}_DOWN" "$active missing — $role is a standing role and must always be armed"
      else
        drop "${role^^}_DOWN"; exit 0  # not armed -> not "down" for non-standing roles
      fi
      exit 0
    fi
    pane="$(grep -oP '(?<=PANE=).*' "$active" 2>/dev/null)"
    if [ -z "$pane" ]; then
      raise "${role^^}_DOWN" "$active exists but has no PANE= line (corrupt?)"
    elif ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$pane"; then
      raise "${role^^}_DOWN" "$active points at dead pane $pane"
    else
      drop "${role^^}_DOWN"
    fi
    ;;
  hook)
    role="${2:?role}"; settings="${3:?settings.json}"
    if grep -q "${role}-token-watch\|ROLE=${role}.*token-watch\|token-watch.*ROLE=${role}" "$settings" 2>/dev/null \
       || { [ "$role" = orc ] && grep -q "orc-token-watch" "$settings" 2>/dev/null; }; then
      drop "${role^^}_HOOK_MISSING"
    else raise "${role^^}_HOOK_MISSING" "no $role token-watch hook in $settings (relay silently dead)"; fi
    ;;
  *) echo "usage: liveness.sh verifier | pane <role> | hook <role> <settings.json>" >&2; exit 2 ;;
esac
