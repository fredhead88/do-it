#!/bin/bash
# relay-watch.sh — DO-IT v3.10 baton-direct relay + author-token guard.
#
# Cron runs this every minute (one cron line per role via ROLE= env var).
# When a role's baton is HANDED-OFF (at ANY token level), fresh, atomically
# complete, and the pane is quiet, it sends /clear + /<role>.
#
# DO-IT v3.7: The sentinel path is RETIRED (deviation #1, ratified 2026-06-10).
# Previously, the hook dropped /tmp/{role}-handoff-due-* only at/above 360k,
# so a deliberate HANDED-OFF below that threshold was never picked up (F12,
# live wedge 77 min on 2026-06-09). Now we scan the baton directly, making
# the relay token-level-agnostic.
#
# DO-IT v3.8 (spec 165, 2026-06-11):
#   R2: unquote baton_pane so `baton_pane: "%0"` == PANE=%0 (live dark cause).
#   R3: CWD written at arming (orc + rev skills); watcher already did this.
#   R4: notify_once/clear_alert rate-limited markers + 2×FRESH_SECS stall alert.
#   R5: atomic consume-once write (tmp-then-rename) + baton_id keyed consume-once.
#
# DO-IT v3.10 (author-token guard, 2026-06-21 — see
# docs/2026-06-21-relay-baton-author-guard-design.md):
#   2b: the cron force-clears a pane ONLY for a baton whose `baton_token:` matches
#       the per-session `TOKEN=` the role wrote into /tmp/<role>-active at arming.
#       A force-/clear is the least-reversible action in the loop, so a baton with
#       a missing/mismatched token is a FOREIGN write (e.g. a sub-worker that hit
#       its own context limit and stamped a stray HANDED-OFF over the baton,
#       2026-06-21) — refused loudly, never relayed. Back-compat: a role armed
#       before this guard has no TOKEN=; it falls through + logs, never wedges.
#
# Roles supported (set $ROLE): orc, rev, watcher (and any future role).
# Each reads its own /tmp/{role}-active file written by the role at boot.
#
# Env overrides (mainly for testing):
#   ORC_RELAY_FILE   — override the derived <cwd>/docs/sessions/<role>-relay.md
#   ORC_WATCH_DRY=1  — log what would happen instead of sending keys
#   ORC_QUIET_SECS   — seconds of transcript silence required (default 45)
#   BATON_FRESH_SECS — max baton age in seconds to honor (default 5400 = 90m)
#
# F11 (head-scan): scan baton head for status:, not just line 1.
# F12 (hardening): freshness gate + atomic-completeness + consume-once + reboot guard.
# R-B (baton-direct): no sentinel required; reads /tmp/{role}-active directly.
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

# --- R4: loud-but-rate-limited operator alert --------------------------------
# A HANDED-OFF baton the cron refuses must never be a silent no-op. We write a
# marker ONCE per error fingerprint; `liveness.sh relay <role>` (if present)
# surfaces it as a {ROLE}_RELAY_ERROR / _RELAY_STALL flag on the board.
# Re-alarm only if the fingerprint changes — no per-minute notification poisoning.
notify_once() {  # $1=kind(error|stall)  $2=fingerprint  $3=message
  local marker="/tmp/${ROLE}-relay-$1"
  [ -f "$marker" ] && [ "$(cat "$marker" 2>/dev/null)" = "$2" ] && return 0
  echo "$2" > "$marker"
  echo "$(ts) ALERT($1): $3 [fp $2]" >>"$LOG"
}
clear_alert() { rm -f "/tmp/${ROLE}-relay-error" "/tmp/${ROLE}-relay-stall"; }

# --- R-B: resolve pane + baton from role-active file (no sentinel required) ---
ACTIVE="/tmp/${ROLE}-active"
[ -f "$ACTIVE" ] || exit 0

PANE="$(grep -m1 '^PANE=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
[ -n "$PANE" ] || exit 0

# Derive repo CWD from active file; fall back to pane's current path → git root.
CWD="$(grep -m1 '^CWD=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
if [ -z "$CWD" ]; then
  pane_path="$(tmux display-message -t "$PANE" -p '#{pane_current_path}' 2>/dev/null)"
  d="$pane_path"
  while [ -n "$d" ] && [ "$d" != "/" ]; do
    if git -C "$d" rev-parse --git-dir >/dev/null 2>&1; then
      CWD="$(git -C "$d" rev-parse --show-toplevel 2>/dev/null)"
      break
    fi
    d="$(dirname "$d")"
  done
fi
[ -n "$CWD" ] || { echo "$(ts) could not resolve CWD for $ROLE; skipping" >>"$LOG"; exit 0; }

RELAY="${ORC_RELAY_FILE:-$CWD/docs/sessions/${ROLE}-relay.md}"

# --- Baton checks (all must pass) -------------------------------------------

# 1. Baton HANDED-OFF? (F11: head-scan — rev's baton title is on L1, not the status)
grep -qE '^status:[[:space:]]*HANDED-OFF' "$RELAY" 2>/dev/null || exit 0

# 2. Atomic completeness (F12): must have BOTH status and handed_off_at.
grep -qE '^handed_off_at:' "$RELAY" 2>/dev/null || {
  _bm=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
  notify_once error "incomplete:$_bm" "baton $RELAY HANDED-OFF but missing handed_off_at (malformed writer); skipping"
  exit 0
}

# 2b. AUTHOR GUARD — only the role's OWN session may relay it.
# A force-/clear is the least-reversible action in the loop, so the cron must honor
# only a baton written by the same session that armed this role. The role writes a
# per-session nonce `TOKEN=` into /tmp/<role>-active at arming, and the same value as
# `baton_token:` in its handoff. A baton whose token is missing or != the live active
# token is a FOREIGN write (e.g. a sub-worker that hit its own context limit and wrote a
# stray HANDED-OFF over the baton, 2026-06-21) — refuse loudly, never force-clear.
# Back-compat: a role that armed before this guard has no TOKEN=; don't wedge the loop —
# fall through to today's gates and just log the unauthenticated relay until it re-arms.
active_token="$(grep -m1 '^TOKEN=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
if [ -n "$active_token" ]; then
  baton_token="$(grep -m1 '^baton_token:[[:space:]]*' "$RELAY" 2>/dev/null | awk '{print $2}' | tr -d '"'"'")"
  if [ "$baton_token" != "$active_token" ]; then
    _bm=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
    notify_once error "unauthed:$_bm" "baton $RELAY token mismatch — a non-$ROLE writer (stray worker baton?) tried to force-relay; REFUSED"
    exit 0
  fi
else
  echo "$(ts) baton $RELAY honored WITHOUT author token ($ROLE armed pre-guard); proceeding (legacy back-compat)" >>"$LOG"
fi

# 3. Freshness gate (F12): use baton file mtime — format-agnostic.
baton_age=$(( $(date +%s) - $(stat -c %Y "$RELAY" 2>/dev/null || echo 0) ))
if [ "$baton_age" -ge "$FRESH_SECS" ]; then
  # R4: dark-role stall — a HANDED-OFF + still-unconsumed baton older than
  # 2×FRESH_SECS is a genuinely dark relay; surface it rather than stay silent.
  _bm=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
  _cons="/tmp/${ROLE}-relay-consumed-${PANE//[^a-zA-Z0-9]/_}"
  if [ "$baton_age" -ge "$(( FRESH_SECS * 2 ))" ] \
     && [ "$(cat "$_cons" 2>/dev/null)" != "$_bm" ]; then
    notify_once stall "stall:$_bm" "baton $RELAY HANDED-OFF + unconsumed for ${baton_age}s (>= $((FRESH_SECS*2))s) — $ROLE relay is dark"
  fi
  echo "$(ts) baton $RELAY stale (${baton_age}s >= ${FRESH_SECS}s); refusing relay" >>"$LOG"
  exit 0
fi

# 4. Consume-once (R5/F12): keyed to baton_id (if present) or mtime as fallback.
#    Two batons written in the same second share an mtime; baton_id (uuidgen in
#    the template) disambiguates them. Consume-once marker written atomically
#    (tmp-then-rename) so concurrent cron ticks can't both read "unconsumed".
baton_mtime=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
baton_id="$(grep -m1 '^baton_id:[[:space:]]*' "$RELAY" 2>/dev/null | awk '{print $2}')"
consume_key="${baton_id:-mtime:${baton_mtime}}"
CONSUMED="/tmp/${ROLE}-relay-consumed-${PANE//[^a-zA-Z0-9]/_}"
if [ -f "$CONSUMED" ] && [ "$(cat "$CONSUMED" 2>/dev/null)" = "$consume_key" ]; then
  echo "$(ts) baton $RELAY already consumed (key $consume_key); skipping" >>"$LOG"
  exit 0
fi

# 5. Wrong-pane guard (R-B/R2): if baton carries a baton_pane: field, it must
#    match the pane in the active file. Guards against a stale active file.
#    R2: strip surrounding quotes so `baton_pane: "%0"` == PANE=%0.
baton_pane="$(grep -m1 '^baton_pane:[[:space:]]*' "$RELAY" 2>/dev/null | awk '{print $2}' | tr -d '"'"'")"
if [ -n "$baton_pane" ] && [ "$baton_pane" != "$PANE" ]; then
  echo "$(ts) pane mismatch: active=$PANE baton_pane=$baton_pane; skipping" >>"$LOG"
  exit 0
fi

# 6. Pane still alive?
if ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$PANE"; then
  echo "$(ts) pane $PANE gone; role $ROLE not running" >>"$LOG"
  exit 0
fi

# 7. Pane quiet? Use transcript if available (via active file or session id).
SESSION_ID="$(grep -m1 '^SESSION_ID=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
TRANSCRIPT="$(grep -m1 '^TRANSCRIPT=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"

# If the active file has a session id, find the canonical transcript path.
if [ -n "$SESSION_ID" ]; then
  found="$(find "${HOME}/.claude/projects" -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)"
  [ -n "$found" ] && TRANSCRIPT="$found"
fi

if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$TRANSCRIPT") ))
  [ "$age" -ge "$QUIET_SECS" ] || {
    echo "$(ts) pane $PANE active (transcript ${age}s < ${QUIET_SECS}s); waiting" >>"$LOG"
    exit 0
  }
else
  # No transcript found — use tmux pane last-used as fallback quiet probe.
  pane_activity="$(tmux display-message -t "$PANE" -p '#{pane_last_used}' 2>/dev/null || echo 0)"
  pane_idle=$(( $(date +%s) - ${pane_activity:-0} ))
  [ "$pane_idle" -ge "$QUIET_SECS" ] || {
    echo "$(ts) pane $PANE active (tmux activity ${pane_idle}s < ${QUIET_SECS}s); waiting" >>"$LOG"
    exit 0
  }
fi

# --- All checks passed: relay -------------------------------------------------

if [ "$DRY" = "1" ]; then
  echo "$(ts) DRY RUN: would /clear + $BOOT_CMD pane $PANE (role=$ROLE baton_age=${baton_age}s) [baton-direct, no sentinel]" >>"$LOG"
  exit 0
fi

echo "$(ts) relaying $ROLE in pane $PANE (baton_age=${baton_age}s)" >>"$LOG"
tmux send-keys -t "$PANE" "/clear" Enter
sleep 6
tmux send-keys -t "$PANE" "$BOOT_CMD" Enter
# R5: atomic consume-once write (tmp-then-rename) — prevents a concurrent cron
# tick from also reading "unconsumed" before the marker lands.
_ctmp="${CONSUMED}.tmp.$$"
echo "$consume_key" > "$_ctmp" && mv -f "$_ctmp" "$CONSUMED"
clear_alert   # relay fired — role no longer dark
