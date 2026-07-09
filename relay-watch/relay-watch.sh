#!/bin/bash
# relay-watch.sh — DO-IT v3.8 baton-direct relay.
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
# spec 279: LOG/LOCK overridable so the ROLE=builder multi-pane tests sandbox
# their state (defaults preserve prod behavior exactly).
LOG="${ORC_WATCH_LOG:-/tmp/${ROLE}-relay-watch.log}"
LOCK="${ORC_WATCH_LOCK:-/tmp/${ROLE}-relay-watch.lock}"
# spec 279 R1/R4 (ROLE=builder only): the builder fleet is MULTI-pane.
STATE_DIR="${ORC_WATCH_STATE_DIR:-/tmp}"               # builder log/markers/registry live here
BUILDER_GLOB="${ORC_BUILDER_GLOB:-/tmp/builder-*-active}"
UNREG_FRESH="${ORC_UNREG_FRESH_SECS:-600}"             # R4: transcript-fresh window for orphan alarm
REG_TTL="${ORC_BUILDER_REG_TTL:-7200}"                 # R4: prune a seen-pane registry entry past this idle age
# spec 369 R2: inline-build detector wiring
BUILD_LANE_DIR="${BUILD_LANE_DIR:-$HOME/.claude/build-lane}"
INLINE_BUILD_NUDGE_BACKOFF_SECS="${INLINE_BUILD_NUDGE_BACKOFF_SECS:-900}"
INLINE_BUILD_NUDGE_MAX_POKES="${INLINE_BUILD_NUDGE_MAX_POKES:-3}"

exec 9>"$LOCK"
flock -n 9 || exit 0

ts() { date -u +%FT%TZ; }

# spec 287 R2 — route the relay /clear+boot through the verifying sender so a
# failed submit is detected (composer-check), logged loudly, and NOT marked
# consumed (retried next tick) instead of silently leaving a role past its
# context ceiling (the 656k symptom). Sourced AFTER ts() so pane_send_message
# inherits this script's log-timestamp format.
# shellcheck source=lib/pane_send.sh
source "$(dirname "$0")/lib/pane_send.sh"
# shellcheck source=doit_presence_gate.sh
source "$(dirname "$0")/doit_presence_gate.sh"

# spec 287 R2 — verified relay submit: send /clear, wait for the reboot to settle,
# then send the boot command, each via the composer-checked sender. Returns 0 only
# if BOTH submits verified. Args: <pane> <boot_cmd> <label>.
relay_submit() {
  local pane="$1" boot="$2" label="${3:-relay}"
  pane_send_message "$pane" "/clear" "$LOG" "${label}-clear" || return 1
  sleep 6
  pane_send_message "$pane" "$boot" "$LOG" "${label}-boot" || return 1
  return 0
}

# --- R4: loud-but-rate-limited operator alert --------------------------------
# A HANDED-OFF baton the cron refuses must never be a silent no-op. We write a
# marker ONCE per error fingerprint; `liveness.sh relay <role>` (if present)
# surfaces it as a {ROLE}_RELAY_ERROR / _RELAY_STALL flag on the board.
# Re-alarm only if the fingerprint changes — no per-minute notification poisoning.
notify_once() {  # $1=kind(error|stall|refused)  $2=fingerprint  $3=message
  local marker="/tmp/${ROLE}-relay-$1"
  [ -f "$marker" ] && [ "$(cat "$marker" 2>/dev/null)" = "$2" ] && return 0
  echo "$2" > "$marker"
  # spec 319 R2: a token-mismatch/no-token REFUSAL is a successful guard action,
  # not a failure — log it below error level (distinct marker /tmp/${ROLE}-relay-refused,
  # so the board's RELAY_ERROR flag keyed off -relay-error is NOT raised).
  local level; case "$1" in refused) level="REFUSED" ;; *) level="ALERT($1)" ;; esac
  echo "$(ts) $level: $3 [fp $2]" >>"$LOG"
}
clear_alert() { rm -f "/tmp/${ROLE}-relay-error" "/tmp/${ROLE}-relay-stall"; }

# spec 319 R1: capture a refused (unauthenticated) baton instead of leaving it in
# the role relay path to be re-read and re-alerted every rewrite. Move it
# byte-for-byte into a sibling dir the relay cron never consumes, under a
# clobber-safe name encoding label + original mtime + fingerprint, so distinct
# forgeries accumulate for later attribution. Only the ALREADY-REFUSED path calls
# this — a token-MATCHED baton is never passed here, so quarantine can never move a
# valid handoff. mv within docs/sessions is same-fs → atomic + byte-identical.
quarantine_baton() {  # $1=baton path  $2=label(role or builder-<id>)  $3=fingerprint
  local src="$1" label="$2" fp="$3"
  [ -f "$src" ] || return 0
  local qdir; qdir="$(dirname "$src")/_relay-quarantine"
  mkdir -p "$qdir" 2>/dev/null || {
    echo "$(ts) quarantine: mkdir $qdir failed; leaving $src in place" >>"$LOG"; return 1; }
  local mtime fpsafe base dst n
  mtime="$(stat -c %Y "$src" 2>/dev/null || echo 0)"
  fpsafe="${fp//[^a-zA-Z0-9]/_}"
  base="${label}-relay.${mtime}.${fpsafe}"
  dst="${qdir}/${base}.md"; n=1
  while [ -e "$dst" ]; do dst="${qdir}/${base}.${n}.md"; n=$((n+1)); done
  if mv "$src" "$dst" 2>/dev/null; then
    echo "$(ts) quarantined refused baton $src → $dst" >>"$LOG"
  else
    echo "$(ts) quarantine: mv $src failed; baton left in place" >>"$LOG"; return 1
  fi
}

# ── spec 279 R1+R4: ROLE=builder MULTI-pane relay sweep ───────────────────────
# Unlike orc/rev/watcher (one standing pane), there are N builder panes. This
# loops every ${BUILDER_GLOB} active file, resolves each pane + its baton
# (<cwd>/docs/sessions/builder-<id>-relay.md), and applies the SAME check chain
# the single-pane path uses (HANDED-OFF / completeness / author-token guard /
# freshness / consume-once / wrong-pane / quiet) — per pane, with per-builder-id
# state keys so two panes never share a consume/alert marker. It mirrors the
# proven multi-pane branch in doit-nudge.sh:138 rather than refactoring the live
# single-pane orc/rev/watcher flow (which stays byte-for-byte unchanged).
# After the relay pass it runs the R4 alive-but-unregistered orphan sweep.
_live_panes() {  # echo live tmux pane ids, or the ORC_LIVE_PANES override (test hook)
  if [ -n "${ORC_LIVE_PANES:-}" ]; then
    printf '%s\n' $ORC_LIVE_PANES
  else
    tmux list-panes -a -F '#{pane_id}' 2>/dev/null
  fi
}
_transcript_for() {  # $1=session_id → echo transcript path if resolvable
  [ -n "$1" ] || return 0
  find "${HOME}/.claude/projects" -name "${1}.jsonl" 2>/dev/null | head -1
}
_pane_capture() {  # $1=pane → visible screen text (ORC_PANE_CAPTURE_DIR/<sanitized> overrides — test hook)
  if [ -n "${ORC_PANE_CAPTURE_DIR:-}" ]; then
    local f="${ORC_PANE_CAPTURE_DIR}/${1//[^a-zA-Z0-9]/_}"
    [ -f "$f" ] && cat "$f"
  else
    tmux capture-pane -p -t "$1" 2>/dev/null
  fi
}
_notify_once_b() {  # $1=key  $2=kind(error|stall|refused)  $3=fingerprint  $4=message
  local marker="${STATE_DIR}/builder-relay-$2-$1"
  [ -f "$marker" ] && [ "$(cat "$marker" 2>/dev/null)" = "$3" ] && return 0
  echo "$3" > "$marker"
  # spec 319 R2: refusal logs below error (distinct marker builder-relay-refused-<id>).
  local level; case "$2" in refused) level="REFUSED" ;; *) level="ALERT($2)" ;; esac
  echo "$(ts) $level builder[$1]: $4 [fp $3]" >>"$LOG"
}

relay_builder_panes() {
  mkdir -p "$STATE_DIR" 2>/dev/null
  local REGDIR="${STATE_DIR}/builder-pane-registry"
  mkdir -p "$REGDIR" 2>/dev/null
  local now; now=$(date +%s)
  shopt -s nullglob
  local af any=0
  local covered=" "   # raw pane ids covered by a valid active file THIS tick (R4)

  for af in $BUILDER_GLOB; do
    any=1
    local bid pane bcwd sid
    bid="$(grep -m1 '^BUILDER_ID=' "$af" 2>/dev/null | cut -d= -f2-)"
    [ -z "$bid" ] && bid="$(basename "$af" | sed -E 's/^builder-(.*)-active$/\1/')"
    pane="$(grep -m1 '^PANE=' "$af" 2>/dev/null | cut -d= -f2-)"
    bcwd="$(grep -m1 '^CWD=' "$af" 2>/dev/null | cut -d= -f2-)"
    sid="$(grep -m1 '^SESSION_ID=' "$af" 2>/dev/null | cut -d= -f2-)"
    [ -n "$pane" ] || { echo "$(ts) builder[$bid]: no PANE in $af; skip" >>"$LOG"; continue; }

    # R4 memory: record pane→builder so a later id-clobber can be detected as an orphan.
    local psafe="${pane//[^a-zA-Z0-9]/_}"
    printf '%s|%s|%s\n' "$bid" "$sid" "$now" > "${REGDIR}/pane_${psafe}.tmp.$$" \
      && mv -f "${REGDIR}/pane_${psafe}.tmp.$$" "${REGDIR}/pane_${psafe}"
    covered="${covered}${pane} "

    # Resolve CWD if absent (pane path → git root), same fallback as the single-pane path.
    if [ -z "$bcwd" ]; then
      local pp d
      pp="$(tmux display-message -t "$pane" -p '#{pane_current_path}' 2>/dev/null)"
      d="$pp"
      while [ -n "$d" ] && [ "$d" != "/" ]; do
        if git -C "$d" rev-parse --git-dir >/dev/null 2>&1; then
          bcwd="$(git -C "$d" rev-parse --show-toplevel 2>/dev/null)"; break
        fi
        d="$(dirname "$d")"
      done
    fi
    [ -n "$bcwd" ] || { echo "$(ts) builder[$bid]: could not resolve CWD; skip" >>"$LOG"; continue; }

    # ── spec 296 R4: in-flight close-out brake (consolidated nudge point — brief 078
    #    plugs the inline-build detector in here too, so a builder gets ONE nudge/cadence) ──
    local _co_tr; _co_tr="$(_transcript_for "$sid")"
    if [ -n "$_co_tr" ] && [ -f "$_co_tr" ] && _live_panes | grep -qx "$pane"; then
      NUDGE_DRY="$DRY" STATE_DIR="$STATE_DIR" \
        "$(dirname "$0")/builder_closeout_nudge.sh" "$bid" "$pane" "$_co_tr" "$sid" >>"$LOG" 2>&1 || true
    fi

    # ── spec 369 R2: in-flight inline-build detector ──────────────────────────
    # Resolve the spec footprint from the claimed build-lane file so we have a
    # --writes arg for builder_inline_detect.py.  BUILD_LANE_DIR is overridable
    # for hermetic tests (global default set at script top).  nullglob is already
    # active from the outer for-loop so an empty glob expands to nothing.
    if [ -n "$_co_tr" ] && [ -f "$_co_tr" ] && _live_panes | grep -qx "$pane"; then
      local _ib_lane _ib_writes
      _ib_writes=""
      for _ib_lane in "${BUILD_LANE_DIR}"/*.building.md; do
        if grep -qm1 "claimed_by: builder-${bid}" "$_ib_lane" 2>/dev/null; then
          # writes:    [a.py, b.py, c.py]  →  a.py,b.py,c.py
          _ib_writes="$(grep -m1 '^writes:' "$_ib_lane" 2>/dev/null \
            | sed 's/^writes:[[:space:]]*//' \
            | tr -d '[]' \
            | sed 's/,[[:space:]]*/,/g;s/^[[:space:]]*//;s/[[:space:]]*$//')"
          break
        fi
      done
      if [ -n "$_ib_writes" ]; then
        local _id_rc
        _id_rc=0
        python3 "$(dirname "$0")/builder_inline_detect.py" \
          --transcript "$_co_tr" --writes "$_ib_writes" >>"$LOG" 2>&1 || _id_rc=$?
        if [ "$_id_rc" -eq 10 ]; then
          # exit-code 10 == INLINE_BUILD — send dedup-guarded nudge (spec 284/287)
          # Dedup key suffix "inline-build" is DISTINCT from closeout's key so the
          # two nudge budgets are independent.
          local _ib_key
          _ib_key="$(printf '%s:inline-build' "$bid" | tr -c 'a-zA-Z0-9._-' '_')"
          local _ib_pcf="${STATE_DIR}/builder-nudge-poke-${_ib_key}"
          local _ib_lpf="${STATE_DIR}/builder-nudge-last-${_ib_key}"
          local _ib_pc _ib_lp _ib_now
          _ib_pc=0; _ib_lp=0
          _ib_now="$(date +%s)"
          [ -f "$_ib_pcf" ] && _ib_pc="$(cat "$_ib_pcf" 2>/dev/null || echo 0)"
          [ -f "$_ib_lpf" ] && _ib_lp="$(cat "$_ib_lpf" 2>/dev/null || echo 0)"
          if [ "$_ib_pc" -ge "$INLINE_BUILD_NUDGE_MAX_POKES" ]; then
            echo "$(ts) INLINE_BUILD_NUDGE_STALL builder[$bid] poke cap ($_ib_pc>=$INLINE_BUILD_NUDGE_MAX_POKES)" >>"$LOG"
          elif [ "$_ib_lp" -gt 0 ] && [ "$(( _ib_now - _ib_lp ))" -lt "$INLINE_BUILD_NUDGE_BACKOFF_SECS" ]; then
            echo "$(ts) INLINE_BUILD_NUDGE_BACKOFF builder[$bid] backoff skip ($(( _ib_now - _ib_lp ))s < ${INLINE_BUILD_NUDGE_BACKOFF_SECS}s)" >>"$LOG"
          elif [ "$DRY" = "1" ]; then
            echo "$(ts) DRY RUN: would inline-build nudge builder[$bid] pane $pane (fan-out Sonnet workers)" >>"$LOG"
          else
            local _ib_text
            _ib_text="fan out Sonnet workers — you're building inline; the push-time provenance gate will block. Dispatch Sonnet workers for the remaining footprint."
            local _ib_send_rc
            _ib_send_rc=1
            pane_send_message "$pane" "$_ib_text" "$LOG" "inline-build-nudge"
            _ib_send_rc=$?
            # spec 287: only advance count/backoff on a VERIFIED successful send
            if [ "$_ib_send_rc" -eq 0 ]; then
              local _ib_nc
              _ib_nc=$(( _ib_pc + 1 ))
              echo "$_ib_nc" > "${_ib_pcf}.tmp.$$" && mv -f "${_ib_pcf}.tmp.$$" "$_ib_pcf"
              echo "$_ib_now" > "${_ib_lpf}.tmp.$$" && mv -f "${_ib_lpf}.tmp.$$" "$_ib_lpf"
              echo "$(ts) INLINE_BUILD_NUDGE_SENT builder[$bid] pane $pane (poke $_ib_nc)" >>"$LOG"
            else
              echo "$(ts) WARN builder[$bid] inline-build nudge send failed to $pane — count/backoff NOT advanced, will retry next tick" >>"$LOG"
            fi
          fi
        fi
      fi
    fi

    local RELAY="${bcwd}/docs/sessions/builder-${bid}-relay.md"

    # 1. baton HANDED-OFF or RECYCLE? (head-scan — recognise either status)
    local bstatus_line bstatus
    bstatus_line="$(head -20 "$RELAY" 2>/dev/null | grep -m1 "^status:[[:space:]]*\(HANDED-OFF\|RECYCLE\)")"
    if [ -z "$bstatus_line" ]; then
      echo "$(ts) builder[$bid]: baton not HANDED-OFF/RECYCLE; skip" >>"$LOG"; continue
    fi
    bstatus="$(printf '%s\n' "$bstatus_line" | awk '{print $2}')"
    # 2. atomic completeness — HANDED-OFF needs handed_off_at; RECYCLE needs recycle_at
    if [ "$bstatus" = "HANDED-OFF" ]; then
      grep -qE '^handed_off_at:' "$RELAY" 2>/dev/null || {
        _notify_once_b "$bid" error "incomplete:$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)" \
          "baton $RELAY HANDED-OFF but missing handed_off_at (malformed writer); skipping"
        continue; }
    else
      grep -qE '^recycle_at:' "$RELAY" 2>/dev/null || {
        _notify_once_b "$bid" error "incomplete:$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)" \
          "baton $RELAY RECYCLE but missing recycle_at (malformed writer); skipping"
        continue; }
    fi
    # 2b. AUTHOR GUARD — only the builder's own session may relay it (spec 209 class).
    local atok btok
    atok="$(grep -m1 '^TOKEN=' "$af" 2>/dev/null | cut -d= -f2-)"
    local _bbm
    if [ -z "$atok" ]; then
      _bbm=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
      _notify_once_b "$bid" refused "no-token:$_bbm" \
        "baton $RELAY REFUSED — builder[$bid] active file has no TOKEN= (re-run First Moves step 0 to arm)"
      quarantine_baton "$RELAY" "builder-$bid" "no-token:$_bbm"
      continue; fi
    btok="$(grep -m1 '^baton_token:[[:space:]]*' "$RELAY" 2>/dev/null | awk '{print $2}' | tr -d '"'"'")"
    if [ "$btok" != "$atok" ]; then
      _bbm=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
      _notify_once_b "$bid" refused "unauthed:$_bbm" \
        "baton $RELAY token mismatch — a foreign writer (stray worker baton?) tried to force-relay builder[$bid]; REFUSED"
      quarantine_baton "$RELAY" "builder-$bid" "unauthed:$_bbm"
      continue; fi
    # 3. freshness gate (file mtime — format-agnostic)
    local bage; bage=$(( now - $(stat -c %Y "$RELAY" 2>/dev/null || echo 0) ))
    if [ "$bage" -ge "$FRESH_SECS" ]; then
      echo "$(ts) builder[$bid]: baton stale (${bage}s >= ${FRESH_SECS}s); refusing relay" >>"$LOG"
      continue; fi
    # 4. consume-once — spec 301: when baton_id is ABSENT, key on baton CONTENT
    #    (token + completeness-stamp + status), NOT file mtime. A pure re-touch that
    #    advances mtime while status stays HANDED-OFF (the post-boot re-poke) then
    #    yields the SAME key → the relay dedups it instead of re-/clear+/builder-ing
    #    the SAME live pane every tick. baton_id (rev) keeps precedence unchanged.
    local bidn bhoat brcat bstamp consume_key CONSUMED
    bidn="$(grep -m1 '^baton_id:[[:space:]]*' "$RELAY" 2>/dev/null | awk '{print $2}')"
    bhoat="$(grep -m1 '^handed_off_at:' "$RELAY" 2>/dev/null | awk '{print $2}' | tr -d '"'"'")"
    brcat="$(grep -m1 '^recycle_at:' "$RELAY" 2>/dev/null | awk '{print $2}' | tr -d '"'"'")"
    bstamp="$bhoat"; [ "$bstatus" = "RECYCLE" ] && bstamp="$brcat"
    consume_key="${bidn:-tok:${btok}:${bstatus}:${bstamp}}"
    CONSUMED="${STATE_DIR}/builder-relay-consumed-${bid}"
    if [ -f "$CONSUMED" ] && [ "$(cat "$CONSUMED" 2>/dev/null)" = "$consume_key" ]; then
      echo "$(ts) builder[$bid]: baton already consumed (key $consume_key); skip" >>"$LOG"
      continue; fi
    # 5. wrong-pane guard
    local bpane; bpane="$(grep -m1 '^baton_pane:[[:space:]]*' "$RELAY" 2>/dev/null | awk '{print $2}' | tr -d '"'"'")"
    if [ -n "$bpane" ] && [ "$bpane" != "$pane" ]; then
      echo "$(ts) builder[$bid]: pane mismatch active=$pane baton_pane=$bpane; skip" >>"$LOG"
      continue; fi
    # 6. pane still alive?
    if ! _live_panes | grep -qx "$pane"; then
      echo "$(ts) builder[$bid]: pane $pane gone; skip" >>"$LOG"; continue; fi
    # 7. pane quiet? (transcript via SESSION_ID, else pane_last_used fallback)
    local btr age
    btr="$(_transcript_for "$sid")"
    if [ -n "$btr" ] && [ -f "$btr" ]; then
      age=$(( now - $(stat -c %Y "$btr") ))
    else
      local pa; pa="$(tmux display-message -t "$pane" -p '#{pane_last_used}' 2>/dev/null || echo 0)"
      age=$(( now - ${pa:-0} ))
    fi
    if [ "$age" -lt "$QUIET_SECS" ]; then
      echo "$(ts) builder[$bid]: pane active (transcript ${age}s < ${QUIET_SECS}s); waiting" >>"$LOG"
      continue; fi

    # Presence gate — relay uses wider window; prefer TRANSCRIPT= from active file over SESSION_ID path
    local _btr_gate; _btr_gate="$(grep -m1 '^TRANSCRIPT=' "$af" 2>/dev/null | cut -d= -f2-)"
    [ -z "$_btr_gate" ] && _btr_gate="$btr"
    local _relay_win; _relay_win=$(( ${ATTENDED_WINDOW:-300} * ${DPG_RELAY_WINDOW_MULT:-2} ))
    dpg_gate "builder" "$_btr_gate" "$pane" "$LOG" "relay-builder" "$_relay_win" || continue

    # ── all checks passed: relay (HANDED-OFF) or recycle (RECYCLE) ──
    if [ "$DRY" = "1" ]; then
      echo "$(ts) DRY RUN: would /clear + /builder pane $pane (builder[$bid] ${bstatus} baton_age=${bage}s) [baton-direct]" >>"$LOG"
      continue
    fi
    # spec 287 R2 — only a VERIFIED /clear+boot writes the consume-once marker; a
    # failed relay/recycle is logged + alerted and the baton is left UNCONSUMED so
    # the next tick retries, instead of silently leaving the builder past its context
    # ceiling. Both modes boot the same plain /builder command (clear-to-empty reboot).
    if [ "$bstatus" = "RECYCLE" ]; then
      echo "$(ts) recycling builder[$bid] in pane $pane (baton_age=${bage}s)" >>"$LOG"
      if relay_submit "$pane" "/builder" "builder-recycle[$bid]"; then
        local ctmp="${CONSUMED}.tmp.$$"
        echo "$consume_key" > "$ctmp" && mv -f "$ctmp" "$CONSUMED"
        rm -f "${STATE_DIR}/builder-relay-error-${bid}" "${STATE_DIR}/builder-relay-stall-${bid}"
      else
        echo "$(ts) RECYCLE FAILED: builder[$bid] /clear or /builder did not submit to pane $pane — baton NOT consumed, will retry next tick" >>"$LOG"
        notify_once error "builder-recycle-fail:${bid}:${consume_key}" "builder[$bid] recycle to $pane failed to submit (pane dead or bracket-paste?); retrying"
      fi
    else
      echo "$(ts) relaying builder[$bid] in pane $pane (baton_age=${bage}s)" >>"$LOG"
      if relay_submit "$pane" "/builder" "builder-relay[$bid]"; then
        local ctmp="${CONSUMED}.tmp.$$"
        echo "$consume_key" > "$ctmp" && mv -f "$ctmp" "$CONSUMED"
        rm -f "${STATE_DIR}/builder-relay-error-${bid}" "${STATE_DIR}/builder-relay-stall-${bid}"
      else
        echo "$(ts) RELAY FAILED: builder[$bid] /clear or /builder did not submit to pane $pane — baton NOT consumed, will retry next tick" >>"$LOG"
        notify_once error "builder-relay-fail:${bid}:${consume_key}" "builder[$bid] relay to $pane failed to submit (pane dead or bracket-paste?); retrying"
      fi
    fi
  done

  # ── R4: alive-but-unregistered builder alarm ────────────────────────────────
  # A pane we have SEEN as a builder (registry) but which now has NO active file
  # resolving to it, yet is still alive with a fresh transcript, is the A2 clobber
  # (a colliding id overwrote its /tmp/builder-<id>-active). Surface it loudly —
  # the relay/nudge/lane crons all key off the active file and would silently skip it.
  local reg
  for reg in "$REGDIR"/pane_*; do
    [ -e "$reg" ] || continue
    case "$reg" in *.tmp.*) continue;; esac
    local rpane rbid rsid rseen
    rpane="$(basename "$reg" | sed 's/^pane_//')"     # sanitized pane id
    IFS='|' read -r rbid rsid rseen < "$reg" 2>/dev/null
    # Covered by an active file this tick? (covered holds raw pane ids)
    local is_covered=0 p
    for p in $covered; do
      [ "${p//[^a-zA-Z0-9]/_}" = "$rpane" ] && { is_covered=1; break; }
    done
    [ "$is_covered" -eq 1 ] && continue
    # Not covered — find the raw live pane id matching this sanitized registry pane.
    local live_match=""
    for p in $(_live_panes); do
      [ "${p//[^a-zA-Z0-9]/_}" = "$rpane" ] && { live_match="$p"; break; }
    done
    if [ -z "$live_match" ]; then
      # Pane is dead → builder exited legitimately; prune past the grace TTL.
      [ "$(( now - ${rseen:-0} ))" -ge "$REG_TTL" ] && rm -f "$reg"
      continue
    fi
    # Pane alive but unregistered — fresh transcript ⇒ actively building ⇒ alarm.
    local rtr rage=999999
    rtr="$(_transcript_for "$rsid")"
    [ -n "$rtr" ] && [ -f "$rtr" ] && rage=$(( now - $(stat -c %Y "$rtr") ))
    if [ "$rage" -lt "$UNREG_FRESH" ]; then
      _notify_once_b "$rpane" unregistered "unreg:${rpane}:${rsid}" \
        "UNREGISTERED_BUILDER: pane ${live_match} (last seen as builder[$rbid], session ${rsid}) is alive + fresh (${rage}s) but has NO /tmp/builder-*-active — likely an id-collision clobber; relay/nudge/lane crons are blind to it"
      echo "$(ts) UNREGISTERED_BUILDER pane=${live_match} was-builder=${rbid} fresh=${rage}s" >>"$LOG"
    else
      [ "$(( now - ${rseen:-0} ))" -ge "$REG_TTL" ] && rm -f "$reg"
    fi
  done

  # ── R4 (first-encounter): catch the clobber-at-first-boot window the registry
  # can't remember. The spec AC is "a live pane with a fresh transcript and NO
  # matching active file". There is no tmux pane→role marker, so flagging EVERY
  # uncovered live pane would false-positive orc/rev/watcher/shell panes. Instead
  # we confirm the pane IS a builder by its SCREEN: capture-pane and require the
  # builder status-board header (`BUILDER: …`) the builder skill emits every turn.
  # Only a pane that visibly is a builder is flagged — never a non-builder pane.
  local lp
  for lp in $(_live_panes); do
    local lpsafe="${lp//[^a-zA-Z0-9]/_}"
    local cov=0 q
    for q in $covered; do [ "${q//[^a-zA-Z0-9]/_}" = "$lpsafe" ] && { cov=1; break; }; done
    [ "$cov" -eq 1 ] && continue
    # Already alarmed via the registry pass this tick? don't double-fire.
    [ -f "${STATE_DIR}/builder-relay-unregistered-${lpsafe}" ] && continue
    local cap; cap="$(_pane_capture "$lp")"
    [ -n "$cap" ] || continue
    if printf '%s' "$cap" | grep -qE '^BUILDER:[[:space:]]'; then
      _notify_once_b "$lpsafe" unregistered "unreg-firstseen:${lpsafe}" \
        "UNREGISTERED_BUILDER: pane ${lp} shows a live builder board but has NO /tmp/builder-*-active and no prior registry record — an id-collision clobber at first boot; relay/nudge/lane crons are blind to it"
      echo "$(ts) UNREGISTERED_BUILDER (first-seen) pane=${lp}" >>"$LOG"
    fi
  done

  shopt -u nullglob
  [ "$any" -eq 0 ] && echo "$(ts) ROLE=builder: no builder-*-active panes; nothing to relay" >>"$LOG"
}

# ROLE=builder dispatches to the multi-pane sweep and exits BEFORE the single-pane
# resolution below (mirrors doit-nudge.sh). orc/rev/watcher fall through unchanged.
if [ "$ROLE" = "builder" ]; then
  relay_builder_panes
  exit 0
fi

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
# Spec 209 (2026-06-22): the legacy tokenless back-compat path is removed. A missing or
# empty active_token (role armed pre-guard) is NOW ALSO a refusal: any session still
# armed without a TOKEN= must re-arm (run the First Moves step 0 again) before it can
# relay. This closes the only remaining way a forged tokenless baton could force-clear.
active_token="$(grep -m1 '^TOKEN=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
if [ -z "$active_token" ]; then
  _bm=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
  notify_once refused "no-token:$_bm" "baton $RELAY REFUSED — $ROLE active file has no TOKEN= (role not fully armed; re-run First Moves step 0 to arm)"
  quarantine_baton "$RELAY" "$ROLE" "no-token:$_bm"
  exit 0
fi
baton_token="$(grep -m1 '^baton_token:[[:space:]]*' "$RELAY" 2>/dev/null | awk '{print $2}' | tr -d '"'"'")"
if [ "$baton_token" != "$active_token" ]; then
  _bm=$(stat -c %Y "$RELAY" 2>/dev/null || echo 0)
  notify_once refused "unauthed:$_bm" "baton $RELAY token mismatch — a non-$ROLE writer (stray worker baton?) tried to force-relay; REFUSED"
  quarantine_baton "$RELAY" "$ROLE" "unauthed:$_bm"
  exit 0
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

# 4. Consume-once (R5/F12, spec 301): keyed to baton_id (if present) or, absent it,
#    to baton CONTENT (token + handed_off_at + status) — NOT file mtime. Content is
#    stable across a pure re-touch, so an mtime-advancing rewrite of a still-HANDED-OFF
#    baton dedups instead of re-firing. Consume-once marker written atomically
#    (tmp-then-rename) so concurrent cron ticks can't both read "unconsumed".
baton_id="$(grep -m1 '^baton_id:[[:space:]]*' "$RELAY" 2>/dev/null | awk '{print $2}')"
role_hoat="$(grep -m1 '^handed_off_at:' "$RELAY" 2>/dev/null | awk '{print $2}' | tr -d '"'"'")"
# spec 301: stable, re-touch-idempotent key — token+handed_off_at (this path only
# ever handles HANDED-OFF batons, gated above), NOT file mtime, so a pure re-touch
# that advances mtime while status stays HANDED-OFF does not mint a fresh handoff.
consume_key="${baton_id:-tok:${baton_token}:HANDED-OFF:${role_hoat}}"
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

# Presence gate — relay uses wider window to protect the more-destructive /clear
_relay_win=$(( ${ATTENDED_WINDOW:-300} * ${DPG_RELAY_WINDOW_MULT:-2} ))
dpg_gate "$ROLE" "$TRANSCRIPT" "$PANE" "$LOG" "relay-$ROLE" "$_relay_win" || exit 0

if [ "$DRY" = "1" ]; then
  echo "$(ts) DRY RUN: would /clear + $BOOT_CMD pane $PANE (role=$ROLE baton_age=${baton_age}s) [baton-direct, no sentinel]" >>"$LOG"
  exit 0
fi

echo "$(ts) relaying $ROLE in pane $PANE (baton_age=${baton_age}s)" >>"$LOG"
# spec 287 R2 — verify the /clear+boot actually submitted before consuming the
# baton. A blindly-sent relay that doesn't land leaves the role running past its
# context ceiling (the silent 656k overrun); here a failed submit logs loudly,
# raises a relay-fail alert, and leaves the baton UNCONSUMED so the next tick
# retries. Only a verified relay writes the consume-once marker.
if relay_submit "$PANE" "$BOOT_CMD" "relay-${ROLE}"; then
  # R5: atomic consume-once write (tmp-then-rename) — prevents a concurrent cron
  # tick from also reading "unconsumed" before the marker lands.
  _ctmp="${CONSUMED}.tmp.$$"
  echo "$consume_key" > "$_ctmp" && mv -f "$_ctmp" "$CONSUMED"
  # spec 401 R3: per-event relay provenance — a delivered handoff writes an
  # auditable log line, so the baton's resumed_at has a real source and a genuine
  # cron reboot is distinguishable from a hand-typed stamp.
  echo "$(ts) relayed $ROLE pane $PANE baton_id=${baton_id:-none} token=${baton_token} key=${consume_key}" >>"$LOG"
  clear_alert   # relay fired + verified — role no longer dark
else
  echo "$(ts) RELAY FAILED: $ROLE /clear or $BOOT_CMD did not submit to pane $PANE — baton NOT consumed, will retry next tick" >>"$LOG"
  notify_once error "relay-fail:${consume_key}" "relay to $PANE ($ROLE) failed to submit — pane may be dead or in bracket-paste mode; retrying next tick"
fi
