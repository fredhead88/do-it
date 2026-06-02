#!/usr/bin/env bash
# Prints "claude" or "codex" — the host we are running INSIDE.
# Priority: explicit override -> Codex env -> Claude env -> PPID walk.
# Codex is checked before Claude because Claude env vars leak into a nested
# Codex process but not vice-versa.
set -euo pipefail

detect_host() {
  if [ -n "${ADVERSARIAL_AUDIT_HOST:-}" ]; then
    echo "$ADVERSARIAL_AUDIT_HOST"; return
  fi
  if [ -n "${CODEX_THREAD_ID:-}${CODEX_CI:-}" ]; then echo "codex"; return; fi
  if [ -n "${CLAUDE_CODE_ENTRYPOINT:-}${CLAUDE_AGENT_SDK_VERSION:-}" ]; then
    echo "claude"; return
  fi
  # PPID walk, up to 8 levels; innermost match wins.
  local pid=$PPID lvl=0 comm
  while [ "$lvl" -lt 8 ]; do
    # Guard the arithmetic test: empty/non-numeric pid (failed ps) => stop.
    case "$pid" in ''|*[!0-9]*) pid=1 ;; esac
    [ "$pid" -gt 1 ] || break
    comm=$(ps -o comm= -p "$pid" 2>/dev/null || true)
    case "$comm" in
      *codex*) echo "codex"; return ;;
      *claude*) echo "claude"; return ;;
    esac
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    lvl=$((lvl+1))
  done
  echo "claude"  # default assumption
}

detect_host
