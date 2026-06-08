#!/usr/bin/env bash
# Dry-run test: relay-watch.sh must boot the pane with the ROLE's command.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../relay-watch.sh"
TMP="$(mktemp -d)"
fail=0

run_role() {
  local role="$1" expect="$2"
  local sid="testsid-$role"
  # Fake sentinel + baton + transcript so the watcher reaches the DRY decision.
  printf 'PANE=%%99\nSESSION_ID=%s\nTRANSCRIPT=%s/t.jsonl\nCWD=%s\nCONTEXT=1\n' "$sid" "$TMP" "$TMP" > "/tmp/${role}-handoff-due-${sid}"
  mkdir -p "$TMP/docs/sessions"
  printf 'status: HANDED-OFF\n' > "$TMP/docs/sessions/${role}-relay.md"
  : > "$TMP/t.jsonl"; touch -d '120 seconds ago' "$TMP/t.jsonl"
  # Pretend the pane is alive by stubbing tmux on PATH.
  local out
  out="$(ROLE="$role" ORC_WATCH_DRY=1 ORC_QUIET_SECS=1 PATH="$HERE/stub:$PATH" \
        ORC_RELAY_FILE="$TMP/docs/sessions/${role}-relay.md" bash "$SCRIPT" 2>&1; cat /tmp/${role}-relay-watch.log 2>/dev/null)"
  if echo "$out" | grep -q "$expect"; then echo "ok: ROLE=$role -> $expect"; else echo "FAIL: ROLE=$role expected '$expect', got: $out"; fail=1; fi
  rm -f "/tmp/${role}-handoff-due-${sid}" "/tmp/${role}-relay-watch.log"
}

# tmux stub that always reports our fake pane alive
mkdir -p "$HERE/stub"
cat > "$HERE/stub/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  list-panes) echo "%99" ;;
  send-keys) echo "SEND $*" ;;
esac
STUB
chmod +x "$HERE/stub/tmux"

run_role orc "/orc"
run_role rev "/rev"
rm -rf "$TMP" "$HERE/stub"
exit $fail
