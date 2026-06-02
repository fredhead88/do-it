#!/usr/bin/env bash
# Usage: call-external.sh <prompt_file> <out_file>
# Sends the prompt to the cross-vendor partner (Codex) and writes the review
# markdown to <out_file>. Exit codes:
#   0 = cross-host review succeeded (out_file populated)
#   2 = degraded: partner unreachable/failed (caller must fall back in-session)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="${1:?prompt_file required}"
OUT_FILE="${2:?out_file required}"
TIMEOUT="${ADVERSARIAL_AUDIT_TIMEOUT:-600}"
MODEL="${ADVERSARIAL_AUDIT_MODEL:-gpt-5.5}"

# Portable timeout: gtimeout -> timeout -> perl alarm (macOS-safe).
run_with_timeout() {
  local secs="$1"; shift
  if command -v gtimeout >/dev/null 2>&1; then gtimeout "$secs" "$@"; return $?; fi
  if command -v timeout  >/dev/null 2>&1; then timeout  "$secs" "$@"; return $?; fi
  perl -e 'my $s=shift; eval { local $SIG{ALRM}=sub{die"timeout\n"}; alarm $s; exec @ARGV };' "$secs" "$@"
}

HOST="$(bash "$HERE/detect-host.sh")"
# Cross-vendor: if inside Claude, partner is Codex. (Codex-host -> claude -p is
# a future rung; for this machine the partner is always Codex.)
if [ "$HOST" = "codex" ]; then
  echo "aaaudit: host is codex; cross-host partner not configured" >&2
  exit 2
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "aaaudit: codex CLI not found" >&2
  exit 2
fi

STDERR_FILE="$(mktemp)"
# stdin = prompt; -o = review markdown out; stderr captured & discarded (MCP noise).
if run_with_timeout "$TIMEOUT" codex exec --json -m "$MODEL" \
      -c model_reasoning_effort=high --sandbox read-only --skip-git-repo-check \
      -o "$OUT_FILE" - < "$PROMPT_FILE" >/dev/null 2>"$STDERR_FILE"; then
  if [ -s "$OUT_FILE" ]; then rm -f "$STDERR_FILE"; exit 0; fi
fi
# Distinguish an invalid model id from a genuine transport/auth failure, so a
# stale -m default does not masquerade as "Codex unreachable".
if grep -qiE 'model.*(not found|unknown|invalid|does not exist|unsupported)|unsupported model' "$STDERR_FILE"; then
  echo "aaaudit: model '$MODEL' was rejected by codex — fix ADVERSARIAL_AUDIT_MODEL (this is NOT a transport/auth failure)" >&2
fi
# Surface only the last real error line, not the MCP transport spam.
grep -v -E 'rmcp::|AuthRequired|oauth-protected-resource|Transport channel' "$STDERR_FILE" | tail -3 >&2 || true
rm -f "$STDERR_FILE"
exit 2
