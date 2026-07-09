#!/usr/bin/env bash
# watcher_sweep_liveness.sh — spec 400 R3: external sweep-cadence assertion.
#
# The standing-role heartbeat only proves a PANE exists; it cannot prove the
# watcher actually SWEEPS. This independent assertion alarms when the watcher
# has not completed a genuine sweep within SWEEP_MAX_AGE, OR when the sweep
# timestamp artifact is missing entirely (never-swept => alarm, not silence).
# It reads a last-genuine-sweep timestamp the watcher writes at close-of-sweep
# (config-repo SKILL.md R3a step, operator-applied) — it never reads the pane or
# transcript, so a green heartbeat cannot mask a stale sweep.
#
# Cron (OPERATOR-INSTALLED into the user crontab, sibling to the heartbeat line):
#   */30 * * * * ${REPO_ROOT}/scripts/watcher_sweep_liveness.sh >> /tmp/watcher-sweep-liveness.log 2>&1
# (and add a REQUIRED_ACTIVATIONS entry in deploy.sh — operator/integrator lane.)
#
# Env overrides (tests + tuning):
#   ROLE=watcher                  — role name (default watcher)
#   WATCHER_SWEEP_TS_FILE         — sweep timestamp artifact (default /tmp/<role>-last-sweep)
#   SWEEP_MAX_AGE                 — max sweep age in seconds before alarm (default 5400 = 90min)
#   LIVENESS_FLAG                 — alarm/flag dir (default ~/.claude/ledger/liveness)
#   WATCHER_SWEEP_LOG             — log path
#
# Security: no secrets; filesystem reads only.
set -u

ROLE="${ROLE:-watcher}"
SWEEP_TS_FILE="${WATCHER_SWEEP_TS_FILE:-/tmp/${ROLE}-last-sweep}"
SWEEP_MAX_AGE="${SWEEP_MAX_AGE:-5400}"
FLAG_DIR="${LIVENESS_FLAG:-${HOME}/.claude/ledger/liveness}"
FLAG="${FLAG_DIR}/${ROLE^^}_SWEEP_STALE"
LOG="${WATCHER_SWEEP_LOG:-/tmp/${ROLE}-sweep-liveness.log}"

ts() { date -u +%FT%TZ; }
mkdir -p "$FLAG_DIR" 2>/dev/null
NOW=$(date +%s)

# Missing artifact => never swept => alarm (NOT silence).
if [ ! -f "$SWEEP_TS_FILE" ]; then
  msg="$(ts) ${ROLE^^}_SWEEP_STALE: no sweep timestamp at ${SWEEP_TS_FILE} — ${ROLE} has NEVER recorded a genuine sweep (missing => stale, not healthy). spec 400 R3."
  echo "$msg" > "$FLAG" 2>/dev/null || true
  echo "$msg" >> "$LOG" 2>/dev/null || true
  echo "$msg" >&2
  exit 0
fi

# Parse timestamp: accept epoch seconds or ISO-8601; fall back to file mtime.
raw="$(head -n1 "$SWEEP_TS_FILE" 2>/dev/null | tr -d '[:space:]')"
if [[ "$raw" =~ ^[0-9]+$ ]]; then
  last="$raw"
else
  last="$(date -u -d "$raw" +%s 2>/dev/null || echo 0)"
fi
if [ "${last:-0}" -le 0 ]; then
  last="$(stat -c %Y "$SWEEP_TS_FILE" 2>/dev/null || echo 0)"
fi

age=$(( NOW - last ))
if [ "$age" -gt "$SWEEP_MAX_AGE" ]; then
  msg="$(ts) ${ROLE^^}_SWEEP_STALE: last genuine ${ROLE} sweep ${age}s ago (> ${SWEEP_MAX_AGE}s) — ${ROLE} is not sweeping. spec 400 R3."
  echo "$msg" > "$FLAG" 2>/dev/null || true
  echo "$msg" >> "$LOG" 2>/dev/null || true
  echo "$msg" >&2
else
  rm -f "$FLAG" 2>/dev/null || true
  echo "$(ts) ${ROLE} sweep fresh (${age}s <= ${SWEEP_MAX_AGE}s); no alarm" >> "$LOG" 2>/dev/null || true
fi
exit 0
