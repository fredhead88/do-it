#!/usr/bin/env bash
# liveness.sh — dead-man's switch for the review loop. Run by cron.
#   liveness.sh verifier   — VERIFIER_DOWN if PROGRESS.jsonl is stale
#   liveness.sh pane <role> — {ROLE}_DOWN if /tmp/<role>-active points at a dead pane
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
    RUNS="${VL_RUNS_DIR:-$HOME/do-it/verification-loop/runs}"
    STALE_MIN="${VERIFIER_STALE_MIN:-90}"
    latest="$(ls -1dt "$RUNS"/*/PROGRESS.jsonl 2>/dev/null | head -1)"
    if [ -z "$latest" ]; then raise VERIFIER_DOWN "no PROGRESS.jsonl found under $RUNS"; exit 0; fi
    age_min=$(( ( $(date +%s) - $(stat -c %Y "$latest") ) / 60 ))
    if [ "$age_min" -gt "$STALE_MIN" ]; then raise VERIFIER_DOWN "PROGRESS.jsonl stale ${age_min}m (> ${STALE_MIN}m)"; else drop VERIFIER_DOWN; fi
    ;;
  pane)
    role="${2:?role}"; active="/tmp/${role}-active"
    [ -f "$active" ] || { drop "${role^^}_DOWN"; exit 0; }  # not armed -> not "down"
    pane="$(grep -oP '(?<=PANE=).*' "$active" 2>/dev/null)"
    if [ -n "$pane" ] && ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$pane"; then
      raise "${role^^}_DOWN" "$active points at dead pane $pane"
    else drop "${role^^}_DOWN"; fi
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
