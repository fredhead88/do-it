#!/bin/bash
# standing-role-heartbeat.sh — spec 256 R1: cron liveness heartbeat for standing roles.
#
# Sibling to scripts/orc-idle-watch.sh (durable idle-stall watchdog for orc) and
# scripts/orc-relay-watch.sh (baton-direct relay for all roles).
#
# A standing role (watcher, and rev as a documented opt-in candidate) that goes quiet
# WITHOUT hitting the context ceiling has NO backstop — no relay fires, no nudge fires.
# The 2026-06-25→28 watcher dark gap (3 days, ~146 commits + specs 236–249 unwatched) is
# the proof. This script is the machine guarantee that replaces in-session sleep re-arms.
#
# Fires for a given role when ALL of the following hold:
#   (a) the role's active pane file (/tmp/<role>-active) exists and points at a live pane
#   (b) the pane's transcript is stale beyond HEARTBEAT_THRESHOLD
#   (c) NO fresh HANDED-OFF baton for this role is present in docs/sessions/<role>-relay.md
#       (if baton is HANDED-OFF and fresh, the relay-watch is about to handle it — defer)
#   (d) poke-cap not yet hit for this incident (3-poke guard; resets on pane activity)
#
# On fire: types /<role> into the pane so the role re-boots and resumes sweeping.
# Does NOT /clear — the role's relay protocol sends /clear only at a context ceiling
# (baton + relay-watch). A heartbeat poke boots a fresh sweep in the existing session.
#
# Two-stage deduplication (mirrors orc-idle-watch.sh):
#   Stage 1 — POKE (up to HEARTBEAT_MAX_POKES per incident): send /<role> to pane.
#             Backoff of HEARTBEAT_BACKOFF_SECS between pokes.
#   Stage 2 — STALL (once per incident, after max pokes): raise liveness flag
#             {ROLE^^}_HEARTBEAT_STALL and log loudly; poking stops.
#
# Registered standing roles (pane files must exist for the heartbeat to fire):
#   watcher — /tmp/watcher-active (primary; the 3-day gap motivated this spec)
#   rev     — /tmp/rev-active     (documented opt-in candidate — add a second cron line)
#
# Env overrides (for testing):
#   DRY=1                  — log intended actions; send no keys, write no flags
#   ROLE=watcher           — which role to monitor (default: watcher)
#   HEARTBEAT_THRESHOLD    — seconds idle before first poke (default 3600 = 60min)
#   HEARTBEAT_BACKOFF_SECS — min seconds between re-pokes of same incident (default 1800 = 30min)
#   HEARTBEAT_MAX_POKES    — pokes before stall alert + stop (default 3)
#   BATON_FRESH_SECS       — max baton age to treat as fresh (default 5400 = 90m)
#   HEARTBEAT_LOG          — override log path
#   HEARTBEAT_LOCK         — override lock path
#   HEARTBEAT_HOME         — operator home (where ~/.claude/ lives); auto-resolved from
#                             active-file owner if not set (mirrors orc-idle-watch.sh spec 221)
#   HEARTBEAT_ALARM_DIR    — dir where R2 fail-loud alarm flags are written.
#                             Default: ${LIVENESS_FLAG:-${HOME}/.claude/ledger/liveness}
#   HEARTBEAT_ATTEST       — 1 (default) enables per-tick cron_runs self-attestation; 0 disables (tests)
#   HEARTBEAT_ATTEST_PY    — python interpreter for the attest call. Default: python
#   HEARTBEAT_NO_FALLBACK  — 1 disables R1 log fallback (forces R2 hard-fail when LOG is
#                             unwritable; used by R2 tests). Default: 0
#
# Security: no hardcoded secrets. All reads from filesystem only.

set -u
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-python3}"

ROLE="${ROLE:-watcher}"
DRY="${DRY:-0}"
HEARTBEAT_THRESHOLD="${HEARTBEAT_THRESHOLD:-3600}"      # 60 min idle before first poke
HEARTBEAT_BACKOFF_SECS="${HEARTBEAT_BACKOFF_SECS:-1800}" # 30 min between re-pokes
HEARTBEAT_MAX_POKES="${HEARTBEAT_MAX_POKES:-3}"
BATON_FRESH_SECS="${BATON_FRESH_SECS:-5400}"            # 90 min
LOG="${HEARTBEAT_LOG:-/tmp/${ROLE}-heartbeat.log}"
LOCK="${HEARTBEAT_LOCK:-/tmp/${ROLE}-heartbeat.lock}"
# spec 279 R2 (ROLE=builder only): the builder fleet is MULTI-pane.
HEARTBEAT_BUILDER_GLOB="${HEARTBEAT_BUILDER_GLOB:-/tmp/builder-*-active}"
HB_STATE_DIR="${HEARTBEAT_STATE_DIR:-/tmp}"   # per-builder poke/backoff/stall markers

# ── spec 363: new env knobs (alarm dir, attest, fallback) ────────────────────
HEARTBEAT_ALARM_DIR="${HEARTBEAT_ALARM_DIR:-${LIVENESS_FLAG:-${HOME}/.claude/ledger/liveness}}"
HEARTBEAT_ATTEST="${HEARTBEAT_ATTEST:-1}"
HEARTBEAT_ATTEST_PY="${HEARTBEAT_ATTEST_PY:-python}"
HEARTBEAT_NO_FALLBACK="${HEARTBEAT_NO_FALLBACK:-0}"
HB_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── spec 363: R2 — fail-loud alarm helper ────────────────────────────────────
# _hb_alarm role artifact reason
# Writes a flag file AND echoes to stderr so cron mail / log surfaces see it.
_hb_alarm() {
  local _role="$1" _artifact="$2" _reason="$3"
  local _ts; _ts="$(date -u +%FT%TZ)"
  local _msg="${_ts} role=${_role} UNWRITABLE artifact=${_artifact} reason=${_reason} — heartbeat cannot run; manual fix (e.g. sudo rm the root-owned file)."
  mkdir -p "$HEARTBEAT_ALARM_DIR" 2>/dev/null
  echo "$_msg" > "${HEARTBEAT_ALARM_DIR}/${_role^^}_HEARTBEAT_UNWRITABLE" 2>/dev/null || true
  echo "$_msg" >&2
}

# ── spec 400 R3-adjacent: R2 unresolvable-freshness alarm ─────────────────────
# Raised when the heartbeat cannot measure the role's real activity (no SESSION_ID
# and no resolvable transcript). Distinct from the UNWRITABLE alarm: this says the
# monitor is BLIND, so it must not report healthy. Idempotent (overwrites its flag);
# self-clears on the next tick that resolves a real transcript.
_hb_alarm_unresolvable() {
  local _role="$1" _artifact="$2"
  local _ts; _ts="$(date -u +%FT%TZ)"
  local _msg="${_ts} role=${_role} UNRESOLVABLE artifact=${_artifact} reason=freshness-unresolvable-no-session-id — heartbeat cannot measure ${_role} activity (no SESSION_ID/transcript); escalating to poke/alarm instead of reporting healthy. spec 400 R2. Fix: arm ${_role} with SESSION_ID=\$CLAUDE_CODE_SESSION_ID."
  mkdir -p "$HEARTBEAT_ALARM_DIR" 2>/dev/null
  echo "$_msg" > "${HEARTBEAT_ALARM_DIR}/${_role^^}_HEARTBEAT_UNRESOLVABLE" 2>/dev/null || true
  echo "$_msg" >&2
}

# ── spec 363: R1 — recover a writable log (handles root-owned /tmp files) ────
# Test appendability BEFORE anything tries to write. `: >> file` appends zero
# bytes — fails only if the path is unwritable/uncreatable; safe to test.
if ! ( : >> "$LOG" ) 2>/dev/null; then
  if [ "$HEARTBEAT_NO_FALLBACK" != "1" ]; then
    # Switch to owner-scoped fallback: /tmp/watcher-heartbeat.1000.log
    # /tmp is sticky so we cannot remove the root-owned original, but we CAN
    # create a new file with our UID suffix that we own.
    _hb_fallback="${LOG%.log}.$(id -u).log"
    { echo "$(date -u +%FT%TZ) WARN: configured log ${LOG} not appendable (root-owned?); falling back to ${_hb_fallback}"; } >> "$_hb_fallback" 2>/dev/null || true
    echo "$(date -u +%FT%TZ) WARN: configured log ${LOG} not appendable; falling back to ${_hb_fallback}" >&2
    # Drop an idempotent info alarm so monitoring can surface the poisoned path.
    _hb_alarm "$ROLE" "$LOG" "log-unwritable-recovered-to-fallback"
    LOG="$_hb_fallback"
    # Re-test the fallback itself.
    if ! ( : >> "$LOG" ) 2>/dev/null; then
      _hb_alarm "$ROLE" "$LOG" "log-unwritable"
      exit 1
    fi
  else
    # Fallback disabled (HEARTBEAT_NO_FALLBACK=1) → R2 hard-fail.
    _hb_alarm "$ROLE" "$LOG" "log-unwritable"
    exit 1
  fi
fi

# ── spec 363: R2 — pre-check lock writeability BEFORE acquiring it ────────────
# `: >> $LOCK` proves the path is openable without disturbing a concurrently-held
# flock (flock is advisory). A TRUE unwritable path (bad dir, wrong owner) alarms
# + exits 1. Benign contention (another instance holds the lock) still passes this
# test and falls through to the real `flock -n 9 || exit 0` below.
if ! ( : >> "$LOCK" ) 2>/dev/null; then
  _hb_alarm "$ROLE" "$LOCK" "lock-unwritable"
  exit 1
fi

exec 9>"$LOCK"
flock -n 9 || exit 0

# ── spec 363: R3 — self-attest each tick into cron_runs ──────────────────────
# Registered via EXIT trap so ALL exit paths (watcher, rev, builder) attest.
# R2 exit 1 paths run BEFORE this trap is armed — a dead heartbeat does NOT
# attest, which is correct: the cron_runs monitor then catches the absence.
_hb_attested=0
_hb_attest() {
  [ "$_hb_attested" -eq 1 ] && return
  _hb_attested=1
  if [ "${HEARTBEAT_ATTEST:-1}" != "0" ]; then
    ( cd "$HB_REPO_ROOT" && PYTHONPATH="$HB_REPO_ROOT" "$HEARTBEAT_ATTEST_PY" \
        -m scripts.ops.cron_attest --attest --job "${ROLE}_heartbeat" \
      ) >/dev/null 2>&1 || true
  fi
}
trap _hb_attest EXIT

ts() { date -u +%FT%TZ; }

# shellcheck source=lib/pane_send.sh
source "$(dirname "$0")/lib/pane_send.sh"
# shellcheck source=doit_presence_gate.sh
source "$(dirname "$0")/doit_presence_gate.sh"

# ── spec 279 R2: ROLE=builder MULTI-pane heartbeat ────────────────────────────
# Generalizes the single-pane watcher/rev heartbeat to the N-pane builder fleet:
# loops every ${HEARTBEAT_BUILDER_GLOB} active file and re-pokes a stale builder
# pane (transcript idle beyond threshold, no fresh HANDED-OFF baton), honoring the
# SAME backoff / poke-cap / stall-flag / relay-collision guards as the single-pane
# path — with per-builder-id state keys. The single-pane watcher/rev flow below is
# unchanged; ROLE=builder dispatches here and exits.
_hb_live_panes() {  # echo live tmux pane ids, or the HEARTBEAT_LIVE_PANES override (test hook)
  if [ -n "${HEARTBEAT_LIVE_PANES:-}" ]; then printf '%s\n' $HEARTBEAT_LIVE_PANES
  else tmux list-panes -a -F '#{pane_id}' 2>/dev/null; fi
}

heartbeat_builder_panes() {
  mkdir -p "$HB_STATE_DIR" 2>/dev/null
  local now; now=$(date +%s)
  shopt -s nullglob
  local any=0 af
  for af in $HEARTBEAT_BUILDER_GLOB; do
    any=1
    local bid pane bcwd sid
    bid="$(grep -m1 '^BUILDER_ID=' "$af" 2>/dev/null | cut -d= -f2-)"
    [ -z "$bid" ] && bid="$(basename "$af" | sed -E 's/^builder-(.*)-active$/\1/')"
    pane="$(grep -m1 '^PANE=' "$af" 2>/dev/null | cut -d= -f2-)"
    bcwd="$(grep -m1 '^CWD=' "$af" 2>/dev/null | cut -d= -f2-)"
    sid="$(grep -m1 '^SESSION_ID=' "$af" 2>/dev/null | cut -d= -f2-)"
    [ -n "$pane" ] || { echo "$(ts) builder[$bid]: no PANE in $af; skip" >>"$LOG"; continue; }

    # OPHOME for transcript search (mirrors single-pane resolution, spec 221 R2b).
    local ophome
    if [ -n "${HEARTBEAT_HOME:-}" ]; then ophome="$HEARTBEAT_HOME"
    else
      local owner; owner="$(stat -c %U "$af" 2>/dev/null || true)"
      if [ -n "$owner" ] && [ "$owner" != "root" ]; then ophome="$(getent passwd "$owner" 2>/dev/null | cut -d: -f6)"; fi
      [ -n "${ophome:-}" ] || ophome="$HOME"
    fi
    local flagdir="${LIVENESS_FLAG:-${ophome}/.claude/ledger/liveness}"

    # (a) pane alive?
    if ! _hb_live_panes | grep -qx "$pane"; then
      echo "$(ts) builder[$bid]: pane $pane dead (active file stale); skip" >>"$LOG"; continue; fi

    # (b) transcript idle?
    local tr=""; local idle
    if [ -n "$sid" ]; then
      local found; found="$(find "${ophome}/.claude/projects" -name "${sid}.jsonl" 2>/dev/null | head -1)"
      [ -n "$found" ] && tr="$found"
    fi
    if [ -n "$tr" ] && [ -f "$tr" ]; then
      idle=$(( now - $(stat -c %Y "$tr" 2>/dev/null || echo "$now") ))
    else
      local pa; pa="$(tmux display-message -t "$pane" -p '#{pane_last_used}' 2>/dev/null || echo "$now")"
      idle=$(( now - ${pa:-$now} ))
    fi
    if [ "$idle" -lt "$HEARTBEAT_THRESHOLD" ]; then
      echo "$(ts) builder[$bid]: transcript fresh (${idle}s < ${HEARTBEAT_THRESHOLD}s); no poke needed" >>"$LOG"; continue; fi

    # (c) fresh HANDED-OFF baton ⇒ relay-watch owns this case ⇒ defer.
    local relay="${bcwd}/docs/sessions/builder-${bid}-relay.md"
    if [ -f "$relay" ] && grep -qE '^status:[[:space:]]*HANDED-OFF' "$relay" 2>/dev/null; then
      local bage=$(( now - $(stat -c %Y "$relay" 2>/dev/null || echo 0) ))
      if [ "$bage" -lt "$BATON_FRESH_SECS" ]; then
        echo "$(ts) builder[$bid]: fresh HANDED-OFF baton (${bage}s) — relay-watch owns it; heartbeat defers" >>"$LOG"; continue; fi
    fi

    # (d) incident fingerprint + per-builder-id poke state
    local bucket=$(( idle / 60 ))
    local fp="${pane//[^a-zA-Z0-9]/_}:builder:${bid}:bucket:${bucket}"
    local pcf="${HB_STATE_DIR}/builder-heartbeat-pokes-${bid}"
    local fpf="${HB_STATE_DIR}/builder-heartbeat-fp-${bid}"
    local lpf="${HB_STATE_DIR}/builder-heartbeat-last-${bid}"
    echo "$(ts) builder[$bid]: idle-stall idle=${idle}s (>= ${HEARTBEAT_THRESHOLD}s) pane=${pane}" >>"$LOG"

    local saved; saved="$(cat "$fpf" 2>/dev/null || true)"
    if [ "$saved" != "$fp" ]; then echo "0" > "$pcf"; echo "$fp" > "$fpf"; rm -f "$lpf"; fi
    local pc; pc="$(cat "$pcf" 2>/dev/null || echo 0)"; case "$pc" in ''|*[!0-9]*) pc=0;; esac

    # Stall: max pokes already sent — raise per-builder flag and stop.
    if [ "$pc" -ge "$HEARTBEAT_MAX_POKES" ]; then
      local sm="${HB_STATE_DIR}/builder-heartbeat-stall-${bid}"
      if [ ! -f "$sm" ] || [ "$(cat "$sm" 2>/dev/null)" != "$fp" ]; then
        echo "$fp" > "$sm"
        echo "$(ts) STALL: builder[$bid] unresponsive after ${HEARTBEAT_MAX_POKES} pokes (idle=${idle}s); raising BUILDER_HEARTBEAT_STALL-${bid}" >>"$LOG"
        if [ "$DRY" != "1" ]; then
          mkdir -p "$flagdir"
          echo "$(ts) builder[$bid] unresponsive — ${pc} pokes sent, idle=${idle}s. Manual reboot required." > "${flagdir}/BUILDER_HEARTBEAT_STALL-${bid}"
        else
          echo "$(ts) DRY: would raise BUILDER_HEARTBEAT_STALL-${bid} flag in ${flagdir}" >>"$LOG"
        fi
      else
        echo "$(ts) builder[$bid]: stall flag already raised for fp=${fp}; deduped" >>"$LOG"
      fi
      continue
    fi

    # Backoff
    local lp; lp="$(cat "$lpf" 2>/dev/null || echo 0)"; case "$lp" in ''|*[!0-9]*) lp=0;; esac
    if [ "$lp" -gt 0 ] && [ "$(( now - lp ))" -lt "$HEARTBEAT_BACKOFF_SECS" ]; then
      echo "$(ts) builder[$bid]: backoff ($(( now - lp ))s < ${HEARTBEAT_BACKOFF_SECS}s); waiting" >>"$LOG"; continue; fi

    # Presence gate — prefer TRANSCRIPT= from active file; $tr is the SESSION_ID-resolved path
    local _hb_tr; _hb_tr="$(grep -m1 '^TRANSCRIPT=' "$af" 2>/dev/null | cut -d= -f2-)"
    [ -z "$_hb_tr" ] && _hb_tr="$tr"
    dpg_gate "builder" "$_hb_tr" "$pane" "$LOG" "heartbeat-builder" || continue

    # Poke
    local nc=$(( pc + 1 ))
    if [ "$DRY" = "1" ]; then
      echo "$(ts) DRY: would poke builder[$bid] pane ${pane} with '/builder' (poke ${nc}/${HEARTBEAT_MAX_POKES}, idle=${idle}s, threshold=${HEARTBEAT_THRESHOLD}s)" >>"$LOG"
      echo "$nc" > "$pcf"; echo "$now" > "$lpf"
      rm -f "${flagdir}/BUILDER_HEARTBEAT_STALL-${bid}" "${HB_STATE_DIR}/builder-heartbeat-stall-${bid}" 2>/dev/null || true
    elif pane_send_message "$pane" "/builder" "$LOG" "heartbeat-poke(builder-$bid)"; then
      # spec 287 R1 — only a VERIFIED submit bumps the poke count + backoff stamp;
      # a failed send leaves both unchanged so the next tick retries (and the
      # 3-poke stall cap counts genuine deliveries, not phantom sends).
      echo "$nc" > "$pcf"; echo "$now" > "$lpf"
      rm -f "${flagdir}/BUILDER_HEARTBEAT_STALL-${bid}" "${HB_STATE_DIR}/builder-heartbeat-stall-${bid}" 2>/dev/null || true
    else
      echo "$(ts) WARN: builder[$bid] heartbeat poke failed or did not submit to pane ${pane} — count/backoff NOT advanced, will retry next tick" >>"$LOG"
    fi
  done
  shopt -u nullglob
  [ "$any" -eq 0 ] && echo "$(ts) ROLE=builder: no builder-*-active panes; nothing to heartbeat" >>"$LOG"
}

if [ "$ROLE" = "builder" ]; then
  heartbeat_builder_panes
  exit 0
fi

# ── Resolve active pane file ──────────────────────────────────────────────────
ACTIVE="/tmp/${ROLE}-active"
if [ ! -f "$ACTIVE" ]; then
  echo "$(ts) no ${ACTIVE} — ${ROLE} not armed; skipping" >>"$LOG"
  exit 0
fi

PANE="$(grep -m1 '^PANE=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
if [ -z "$PANE" ]; then
  echo "$(ts) ${ACTIVE} has no PANE= line; skipping" >>"$LOG"
  exit 0
fi

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
if [ -z "$CWD" ]; then
  echo "$(ts) could not resolve CWD for ${ROLE}; skipping" >>"$LOG"
  exit 0
fi

RELAY_FILE="${CWD}/docs/sessions/${ROLE}-relay.md"

# ── Resolve operator home (mirrors orc-idle-watch.sh spec 221 R2b) ───────────
if [ -n "${HEARTBEAT_HOME:-}" ]; then
  OPHOME="$HEARTBEAT_HOME"
else
  active_owner="$(stat -c %U "$ACTIVE" 2>/dev/null || true)"
  if [ -n "$active_owner" ] && [ "$active_owner" != "root" ]; then
    OPHOME="$(getent passwd "$active_owner" 2>/dev/null | cut -d: -f6)"
  fi
  [ -n "${OPHOME:-}" ] || OPHOME="$HOME"
fi
if [ ! -d "${OPHOME}/.claude/projects" ]; then
  echo "$(ts) WARN: ${OPHOME}/.claude/projects missing — transcript search may fail (set HEARTBEAT_HOME). spec 256 R1." >>"$LOG"
fi

FLAG_DIR="${LIVENESS_FLAG:-${OPHOME}/.claude/ledger/liveness}"

# ── (a) Pane alive check ──────────────────────────────────────────────────────
if ! _hb_live_panes | grep -qx "$PANE"; then
  echo "$(ts) pane ${PANE} is dead — ${ROLE} not running (active file stale); skipping" >>"$LOG"
  exit 0
fi

# ── (b) Transcript idle check ─────────────────────────────────────────────────
# Prefer the canonical SESSION_ID → .jsonl transcript path (spec 221 R2b pattern).
# spec 400 R2: when NO transcript resolves, freshness is UNRESOLVABLE — the monitor
# is BLIND. It must NEVER conclude "fresh / no poke" from pane_last_used (an idle
# pane and an actively-sweeping pane read identically). Instead it raises a loud
# UNRESOLVABLE alarm and escalates to the poke path (subject to the unchanged
# baton-defer + presence/posture gates). The §15 monitor-inert fix.
SESSION_ID="$(grep -m1 '^SESSION_ID=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
TRANSCRIPT="$(grep -m1 '^TRANSCRIPT=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"

if [ -n "$SESSION_ID" ]; then
  found="$(find "${OPHOME}/.claude/projects" -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)"
  [ -n "$found" ] && TRANSCRIPT="$found"
fi

NOW=$(date +%s)
UNRESOLVABLE_FLAG="${FLAG_DIR}/${ROLE^^}_HEARTBEAT_UNRESOLVABLE"
FRESHNESS_RESOLVED=0
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
  transcript_mtime=$(stat -c %Y "$TRANSCRIPT" 2>/dev/null || echo "$NOW")
  idle_secs=$(( NOW - transcript_mtime ))
  FRESHNESS_RESOLVED=1
else
  # UNRESOLVABLE: pane_last_used is captured for CONTEXT/logging ONLY — never as
  # a basis for "healthy". HEARTBEAT_PANE_LAST_USED is a hermetic test override.
  pane_activity="${HEARTBEAT_PANE_LAST_USED:-$(tmux display-message -t "$PANE" -p '#{pane_last_used}' 2>/dev/null || echo "$NOW")}"
  idle_secs=$(( NOW - ${pane_activity:-$NOW} ))
fi

if [ "$FRESHNESS_RESOLVED" -eq 1 ]; then
  # Real signal resolved — clear any stale UNRESOLVABLE alarm and honor the fresh gate.
  rm -f "$UNRESOLVABLE_FLAG" 2>/dev/null || true
  if [ "$idle_secs" -lt "$HEARTBEAT_THRESHOLD" ]; then
    echo "$(ts) ${ROLE} transcript fresh (${idle_secs}s < ${HEARTBEAT_THRESHOLD}s threshold); no poke needed" >>"$LOG"
    exit 0
  fi
else
  # spec 400 R2 — freshness UNRESOLVABLE: loud alarm, then escalate to poke-worthy.
  echo "$(ts) WARN: ${ROLE} freshness UNRESOLVABLE (SESSION_ID='${SESSION_ID:-<unset>}', no resolvable transcript) — NOT treating pane_last_used (${idle_secs}s) as healthy; escalating to poke/alarm. arm ${ROLE} with SESSION_ID=\$CLAUDE_CODE_SESSION_ID. spec 400 R2." >>"$LOG"
  _hb_alarm_unresolvable "$ROLE" "$ACTIVE"
  # Force past the fresh gate — an unresolvable heartbeat is poke-worthy, not green.
  idle_secs="$HEARTBEAT_THRESHOLD"
fi

# ── (c) Fresh HANDED-OFF baton check ─────────────────────────────────────────
# If relay-watch is about to handle this (HANDED-OFF baton still fresh), defer.
if [ -f "$RELAY_FILE" ]; then
  if grep -qE '^status:[[:space:]]*HANDED-OFF' "$RELAY_FILE" 2>/dev/null; then
    baton_mtime=$(stat -c %Y "$RELAY_FILE" 2>/dev/null || echo 0)
    baton_age=$(( NOW - baton_mtime ))
    if [ "$baton_age" -lt "$BATON_FRESH_SECS" ]; then
      echo "$(ts) fresh HANDED-OFF baton for ${ROLE} (${baton_age}s < ${BATON_FRESH_SECS}s) — relay-watch owns this; heartbeat defers" >>"$LOG"
      exit 0
    fi
  fi
fi

# ── All conditions met: role is idle beyond threshold, no fresh baton ─────────
# Stable fingerprint keyed to the idle-start minute bucket so multiple cron ticks
# within the same minute produce the same fingerprint (no counter churn).
incident_bucket=$(( idle_secs / 60 ))
INCIDENT_FP="${PANE//[^a-zA-Z0-9]/_}:${ROLE}:bucket:${incident_bucket}"

echo "$(ts) ${ROLE} idle-stall detected: idle=${idle_secs}s (>= ${HEARTBEAT_THRESHOLD}s) pane=${PANE}" >>"$LOG"

# ── (d) Poke-count guard ──────────────────────────────────────────────────────
POKE_COUNT_FILE="/tmp/${ROLE}-heartbeat-pokes"
POKE_FP_FILE="/tmp/${ROLE}-heartbeat-fp"
LAST_POKE_FILE="/tmp/${ROLE}-heartbeat-last-poke"

# Reset poke counter when incident fingerprint changes (fresh idle window).
saved_fp="$(cat "$POKE_FP_FILE" 2>/dev/null || true)"
if [ "$saved_fp" != "$INCIDENT_FP" ]; then
  echo "0" > "$POKE_COUNT_FILE"
  echo "$INCIDENT_FP" > "$POKE_FP_FILE"
  rm -f "$LAST_POKE_FILE"
fi

poke_count=$(cat "$POKE_COUNT_FILE" 2>/dev/null || echo 0)
case "$poke_count" in
  ''|*[!0-9]*) poke_count=0 ;;
esac

# Stall: max pokes already sent — raise flag and stop.
if [ "$poke_count" -ge "$HEARTBEAT_MAX_POKES" ]; then
  STALL_MARKER="/tmp/${ROLE}-heartbeat-stall"
  if [ ! -f "$STALL_MARKER" ] || [ "$(cat "$STALL_MARKER" 2>/dev/null)" != "$INCIDENT_FP" ]; then
    echo "$INCIDENT_FP" > "$STALL_MARKER"
    echo "$(ts) STALL: ${ROLE} unresponsive after ${HEARTBEAT_MAX_POKES} pokes (idle=${idle_secs}s). Raising ${ROLE^^}_HEARTBEAT_STALL. Manual intervention needed." >>"$LOG"
    if [ "$DRY" != "1" ]; then
      mkdir -p "$FLAG_DIR"
      echo "$(ts) ${ROLE} unresponsive — ${poke_count} pokes sent, idle=${idle_secs}s. Manual reboot required." \
        > "$FLAG_DIR/${ROLE^^}_HEARTBEAT_STALL"
    else
      echo "$(ts) DRY: would raise ${ROLE^^}_HEARTBEAT_STALL flag in ${FLAG_DIR}" >>"$LOG"
    fi
  else
    echo "$(ts) stall flag already raised for incident fp=${INCIDENT_FP}; deduped" >>"$LOG"
  fi
  exit 0
fi

# Backoff: don't poke more often than HEARTBEAT_BACKOFF_SECS between pokes.
last_poke=$(cat "$LAST_POKE_FILE" 2>/dev/null || echo 0)
case "$last_poke" in
  ''|*[!0-9]*) last_poke=0 ;;
esac
if [ "$last_poke" -gt 0 ]; then
  secs_since_last=$(( NOW - last_poke ))
  if [ "$secs_since_last" -lt "$HEARTBEAT_BACKOFF_SECS" ]; then
    echo "$(ts) backoff: last poke ${secs_since_last}s ago (< ${HEARTBEAT_BACKOFF_SECS}s threshold); waiting" >>"$LOG"
    exit 0
  fi
fi

# ── Send the poke ─────────────────────────────────────────────────────────────
BOOT_CMD="/${ROLE}"
new_count=$(( poke_count + 1 ))

dpg_gate "$ROLE" "$TRANSCRIPT" "$PANE" "$LOG" "heartbeat-$ROLE" || exit 0

if [ "$DRY" = "1" ]; then
  echo "$(ts) DRY: would poke ${ROLE} pane ${PANE} with '${BOOT_CMD}' (poke ${new_count}/${HEARTBEAT_MAX_POKES}, idle=${idle_secs}s, threshold=${HEARTBEAT_THRESHOLD}s)" >>"$LOG"
  _hb_ok=1
elif pane_send_message "$PANE" "$BOOT_CMD" "$LOG" "heartbeat-poke(${ROLE})"; then
  _hb_ok=1
else
  echo "$(ts) WARN: heartbeat poke failed or did not submit to pane ${PANE} (${ROLE}) — count/backoff NOT advanced, will retry next tick" >>"$LOG"
  _hb_ok=0
fi

# spec 287 R1 — only a VERIFIED submit (or DRY) advances the poke count + backoff
# stamp and clears the stall flag; a failed send leaves all three unchanged so the
# next tick retries and the 3-poke stall cap counts genuine deliveries, not phantom
# sends.
if [ "$_hb_ok" -eq 1 ]; then
  echo "$new_count" > "$POKE_COUNT_FILE"
  echo "$NOW" > "$LAST_POKE_FILE"
  # Clear any previously-raised stall flag now that we have actively poked.
  rm -f "$FLAG_DIR/${ROLE^^}_HEARTBEAT_STALL" "/tmp/${ROLE}-heartbeat-stall" 2>/dev/null || true
fi
