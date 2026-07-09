#!/bin/bash
# doit-nudge.sh — DO-IT v3.9.1 cross-role nudge.
#
# Cron runs this every minute (one cron line per role via ROLE= env var).
# When a role's inbox contains unconsumed inbound work AND the pane has been
# idle >=QUIET_SECS, it types a single self-describing nudge line into the pane.
# On the next tick the role runs its existing boot-step inbox scan.
#
# This script sits BESIDE orc-relay-watch.sh — it never /clear-s, never reboots,
# and defers when a fresh HANDED-OFF baton is pending (R4).
#
# Roles supported (set $ROLE): orc, rev
# (think has no standing pane and is never poked)
#
# Env overrides:
#   NUDGE_DRY=1          — log decisions + what would be typed; send no keys
#   ROLE_WATCH_DRY=1     — alias for NUDGE_DRY=1 (mirrors relay naming)
#   ORC_QUIET_SECS       — transcript quiet gate in seconds for rev (default 45)
#   NUDGE_ORC_IDLE_SECS  — orc idle threshold for human-active gate (default 900)
#   NUDGE_BACKOFF_SECS   — min seconds between re-pokes of same artifact (default 600)
#   NUDGE_MAX_POKES      — pokes before stall-alert + stop (default 3)
#   BATON_FRESH_SECS     — relay baton freshness gate in seconds (default 5400=90m)
#   ORC_WATCH_DRY        — same as NUDGE_DRY (backward compat alias)
#
# Poke-count state:  /tmp/${ROLE}-nudge-poke-<artifact-fingerprint>
# Backoff state:     /tmp/${ROLE}-nudge-last-<artifact-fingerprint>
# Stall alert:       /tmp/${ROLE}-nudge-stall-<artifact-fingerprint>
# Lock:              /tmp/${ROLE}-nudge.lock
# Log:               /tmp/${ROLE}-nudge.log
#
# Artifact identity:
#   - spec artifacts: filename+mtime+ledger_status — so a ledger status advance
#     (e.g. registered→planned, rework→shipped) is treated as a consume signal:
#     the prior poke-count is abandoned and the spec is re-evaluated at the new
#     status. If it's no longer action-owed at the new status, it disappears from
#     the set. If it's still action-owed, it gets a fresh poke count.
#   - memo/corrective artifacts: filename+mtime (unchanged — no ledger status).
#
# ORC action-owed semantics (R1 — refined v3.9.1):
#   INCLUDE: registered+no-plan (un-picked-up), rework (rebuild owed),
#            live correctives, orc-routed memos.
#   EXCLUDE: planned, building (orc already acknowledged/started),
#            held, bounced, accepted, shipped, retired.
#   Rework signals come from BOTH spec-inbox spec files AND brief-inbox
#   review cards (the latter catches rework specs with no spec-inbox file).
#   Spec IDs are de-duplicated so a spec present in both places is listed once.
#
# ORC human-active gate (R1 — new v3.9.1):
#   Detects a recent human (non-tool-result) turn in the orc session transcript.
#   A JSONL user-type entry whose content contains a text block (not tool_result)
#   is a genuine human message. If the most recent human turn is within
#   NUDGE_ORC_HUMAN_WINDOW seconds (default 300=5 min), orc nudge is deferred.
#   Fallback: if no transcript is readable, use NUDGE_ORC_IDLE_SECS (default 900)
#   idle threshold instead of the 45s rev gate.
set -u

ROLE="${ROLE:-orc}"
QUIET_SECS="${ORC_QUIET_SECS:-45}"
ORC_IDLE_SECS="${NUDGE_ORC_IDLE_SECS:-900}"
ORC_HUMAN_WINDOW="${NUDGE_ORC_HUMAN_WINDOW:-300}"
BACKOFF="${NUDGE_BACKOFF_SECS:-600}"
MAX_POKES="${NUDGE_MAX_POKES:-3}"
FRESH_SECS="${BATON_FRESH_SECS:-5400}"
# Support both NUDGE_DRY and the relay-style ORC_WATCH_DRY alias
DRY="${NUDGE_DRY:-${ORC_WATCH_DRY:-${ROLE_WATCH_DRY:-0}}}"

# ── Path overrides (spec 278) ────────────────────────────────────────────────
# Defaults preserve prod behavior exactly; tests sandbox the whole script by
# pointing these at a temp dir, so a dry-run never reads or writes real state.
STATE_DIR="${NUDGE_STATE_DIR:-/tmp}"               # pane-active + nudge markers/log live here
BUS_ROOT="${NUDGE_BUS_ROOT:-${HOME}/.claude}"      # spec-inbox, corrective-inbox, ledger, build-lane
BUILD_LANE_DIR="${NUDGE_BUILD_LANE:-${BUS_ROOT}/build-lane}"
LOG="${NUDGE_LOG:-${STATE_DIR}/${ROLE}-nudge.log}"
LOCK="${NUDGE_LOCK:-${STATE_DIR}/${ROLE}-nudge.lock}"

exec 9>"$LOCK"
flock -n 9 || exit 0

ts() { date -u +%FT%TZ; }

# shellcheck source=lib/pane_send.sh
source "$(dirname "$0")/lib/pane_send.sh"
# shellcheck source=doit_presence_gate.sh
source "$(dirname "$0")/doit_presence_gate.sh"

# ---------------------------------------------------------------------------
# notify_once / clear_alert — same pattern as relay-watch; keyed by nudge
# ---------------------------------------------------------------------------
notify_once_nudge() {  # $1=kind  $2=fingerprint  $3=message
  local marker="${STATE_DIR}/${ROLE}-nudge-$1-$2"
  [ -f "$marker" ] && [ "$(cat "$marker" 2>/dev/null)" = "$2" ] && return 0
  echo "$2" > "$marker"
  echo "$(ts) ALERT($1): $3 [fp $2]" >>"$LOG"
}
clear_nudge_alert() {  # $1=fingerprint
  rm -f "${STATE_DIR}/${ROLE}-nudge-stall-$1"
}

# ---------------------------------------------------------------------------
# Footprint-conflict helpers (spec 278 R2) — parse a lane file's `writes:` list
# and decide whether two footprints overlap (so a builder is only poked toward
# conflict-free work, mirroring the integrator's conflict gate).
# ---------------------------------------------------------------------------
_lane_writes() {  # $1=lane file → one writes-entry per line (un-globbed list members)
  awk '
    /^writes:[[:space:]]*$/ { inw=1; next }
    inw==1 {
      if ($0 ~ /^[A-Za-z_]/) { inw=0 }          # next top-level key ends the block
      else if ($0 ~ /^[[:space:]]*-[[:space:]]*/) {
        line=$0
        sub(/^[[:space:]]*-[[:space:]]*/,"",line)
        sub(/[[:space:]]+$/,"",line)
        gsub(/["'\'']/,"",line)
        if (line != "") print line
      }
    }
  ' "$1" 2>/dev/null
}
_norm_path() {  # strip trailing glob/dir so a path can be prefix-compared
  local p="$1"; p="${p%/\*\*}"; p="${p%/\*}"; p="${p%\*\*}"; p="${p%/}"; printf '%s' "$p"
}
_footprints_conflict() {  # $1=laneA $2=laneB → return 0 if any writes overlap
  local a b na nb
  while IFS= read -r a; do
    [ -z "$a" ] && continue
    na="$(_norm_path "$a")"; [ -z "$na" ] && continue
    while IFS= read -r b; do
      [ -z "$b" ] && continue
      nb="$(_norm_path "$b")"; [ -z "$nb" ] && continue
      # overlap = identical path, or one is a directory-prefix of the other
      if [ "$na" = "$nb" ] || [ "${na#"$nb"/}" != "$na" ] || [ "${nb#"$na"/}" != "$nb" ]; then
        return 0
      fi
    done < <(_lane_writes "$2")
  done < <(_lane_writes "$1")
  return 1
}

# ---------------------------------------------------------------------------
# spec 284 R1 — conflict gate keys on a .building's ACTUAL touched files, not its
# declared `writes:` globs. F1: 271 declared `writes: ["tests/**"]` and the gate
# false-conflicted 278's single test file → it silently refused to poke a free
# builder. A builder being built has a real diff; comparing the CANDIDATE's
# declared writes against the .building's ACTUAL files (its narrowest-honest
# footprint) means an over-broad declared glob can't strand a free builder.
# When a .building's actual diff isn't resolvable (no worktree/base, or nothing
# touched yet), we fall back to its declared writes — never LESS strict than today.
# ---------------------------------------------------------------------------
_building_actual_files() {  # $1=.building lane file → actual touched paths (committed+working+untracked vs base_sha)
  local lf="$1" wt base
  wt="$(grep -m1 '^worktree:[[:space:]]*' "$lf" 2>/dev/null | sed 's/^worktree:[[:space:]]*//' | tr -d '"'"'")"
  base="$(grep -m1 '^base_sha:[[:space:]]*' "$lf" 2>/dev/null | awk '{print $2}')"
  [ -n "$wt" ] && [ -d "$wt" ] && [ -n "$base" ] || return 1
  git -C "$wt" rev-parse --verify -q "$base^{commit}" >/dev/null 2>&1 || return 1
  { git -C "$wt" diff --name-only "$base" 2>/dev/null
    git -C "$wt" ls-files --others --exclude-standard 2>/dev/null
  } | sed '/^$/d' | sort -u
}
_footprints_conflict_actual() {  # $1=candidate .assigned (declared) $2=.building (actual-or-declared)
  local building_files
  building_files="$(_building_actual_files "$2")"
  # No resolvable/non-empty actual diff → fall back to declared-vs-declared (today's behavior).
  if [ -z "$building_files" ]; then
    _footprints_conflict "$1" "$2"; return $?
  fi
  local a na b nb
  while IFS= read -r a; do
    [ -z "$a" ] && continue
    na="$(_norm_path "$a")"; [ -z "$na" ] && continue
    while IFS= read -r b; do
      [ -z "$b" ] && continue
      nb="$(_norm_path "$b")"; [ -z "$nb" ] && continue
      if [ "$na" = "$nb" ] || [ "${na#"$nb"/}" != "$na" ] || [ "${nb#"$na"/}" != "$nb" ]; then
        return 0
      fi
    done <<< "$building_files"
  done < <(_lane_writes "$1")
  return 1
}

# ---------------------------------------------------------------------------
# ROLE=builder branch (spec 278 R2) — MULTI-pane. Unlike orc/rev (one standing
# pane) there are N builder panes; this loops every ${STATE_DIR}/builder-*-active
# and pokes an idle one only when a conflict-free, unclaimed .assigned waits.
# Dispatches here BEFORE the single-pane resolution below and exits.
# ---------------------------------------------------------------------------
handle_builder_nudge() {
  # spec 298 R3 — run the lifecycle reconciler BEFORE iterating sentinels so a
  # reservation is never computed against aliased/dead state within the same tick.
  # BUILDER_RECONCILE_BIN is overridable so tests can inject a stub; non-fatal
  # so a reconcile failure never kills the nudge pass.
  local _recon="${BUILDER_RECONCILE_BIN:-$(dirname "$0")/builder_lifecycle_reconcile.sh}"
  if [ -x "$_recon" ]; then
    echo "$(ts) ROLE=builder: running lifecycle reconcile before nudge pass" >>"$LOG"
    RECONCILE_DRY="$DRY" RECONCILE_LOG="$LOG" "$_recon" >>"$LOG" 2>&1 || \
      echo "$(ts) reconcile pass failed (non-fatal); continuing nudge" >>"$LOG"
  fi

  local glob="${NUDGE_BUILDER_GLOB:-${STATE_DIR}/builder-*-active}"
  local now; now=$(date +%s)
  shopt -s nullglob
  local any=0
  # spec 284 R3 (plan_hint): cross-pane dedup. The per-pane loop independently
  # first-picks the same .assigned, and the backoff is keyed per (pane, artifact),
  # so with N idle panes and one dispatchable spec ALL N got poked toward it (only
  # one can win the atomic claim — the rest are wasted pokes / thrash). Reserve a
  # spec the moment one pane selects it so a given .assigned pokes exactly ONE builder.
  local poked_specs=" "
  local af
  for af in $glob; do
    any=1
    local bid pane bcwd
    bid="$(grep -m1 '^BUILDER_ID=' "$af" 2>/dev/null | cut -d= -f2-)"
    [ -z "$bid" ] && bid="$(basename "$af" | sed -E 's/^builder-(.*)-active$/\1/')"
    pane="$(grep -m1 '^PANE=' "$af" 2>/dev/null | cut -d= -f2-)"
    bcwd="$(grep -m1 '^CWD=' "$af" 2>/dev/null | cut -d= -f2-)"
    [ -n "$pane" ] || { echo "$(ts) builder[$bid]: no PANE in $af; skip" >>"$LOG"; continue; }

    # R5 (spec 279, absorbs brief 070): skip a builder already mid-build.
    # If this pane's BUILDER_ID already owns an in-flight .building lane file,
    # poking it toward a new .assigned risks thrash / abandonment of that work —
    # only genuinely FREE builders get nudged. claimed_by is `builder-<id>` (and we
    # also tolerate a bare `<id>` from the pre-279 mixed scheme), anchored so a bid
    # of "1" never matches a "builder-15" claim.
    local owns_building="" bf
    for bf in "$BUILD_LANE_DIR"/*.building.md; do
      [ -e "$bf" ] || continue
      if grep -qE "^claimed_by:[[:space:]]*(builder-)?${bid}[[:space:]]*$" "$bf" 2>/dev/null; then
        owns_building="$(basename "$bf")"; break
      fi
    done
    if [ -n "$owns_building" ]; then
      echo "$(ts) builder[$bid]: skip — builder-${bid} mid-build (${owns_building}); not poking" >>"$LOG"
      continue
    fi

    # Per-pane relay-collision guard: fresh HANDED-OFF baton defers this pane.
    if [ -n "$bcwd" ]; then
      local baton="${bcwd}/docs/sessions/builder-${bid}-relay.md"
      if grep -qE '^status:[[:space:]]*HANDED-OFF' "$baton" 2>/dev/null; then
        local age=$(( now - $(stat -c %Y "$baton" 2>/dev/null || echo 0) ))
        if [ "$age" -lt "$FRESH_SECS" ]; then
          echo "$(ts) builder[$bid]: relay pending (baton ${age}s); deferring" >>"$LOG"
          continue
        fi
      fi
    fi

    # Presence gate: per-pane — resolve transcript from active file (TRANSCRIPT= line preferred, SESSION_ID fallback)
    local _bid_tr; _bid_tr="$(grep -m1 '^TRANSCRIPT=' "$af" 2>/dev/null | cut -d= -f2-)"
    if [ -z "$_bid_tr" ]; then
      local _bid_sid; _bid_sid="$(grep -m1 '^SESSION_ID=' "$af" 2>/dev/null | cut -d= -f2-)"
      if [ -n "$_bid_sid" ]; then
        local _bid_found; _bid_found="$(find "${HOME}/.claude/projects" -name "${_bid_sid}.jsonl" 2>/dev/null | head -1)"
        [ -n "$_bid_found" ] && _bid_tr="$_bid_found"
      fi
    fi
    dpg_gate "builder" "$_bid_tr" "$pane" "$LOG" "nudge-builder" || continue

    # Find a conflict-free, unclaimed, not-yet-reserved .assigned for this pane.
    local picked="" conflicted=""
    local cand
    for cand in "$BUILD_LANE_DIR"/*.assigned.md; do
      [ -e "$cand" ] || continue
      local cbn; cbn="$(basename "$cand")"
      # R3: a spec already reserved for another pane this tick is not re-poked.
      case "$poked_specs" in *" $cbn "*)
        echo "$(ts) builder[$bid]: skip — $cbn already poked to another builder this tick" >>"$LOG"
        continue;;
      esac
      local clash=0 bf
      for bf in "$BUILD_LANE_DIR"/*.building.md; do
        [ -e "$bf" ] || continue
        # R1: compare the candidate's declared writes against the .building's ACTUAL files.
        if _footprints_conflict_actual "$cand" "$bf"; then clash=1; break; fi
      done
      if [ "$clash" -eq 1 ]; then
        conflicted="$cbn"
        echo "$(ts) builder[$bid]: skip-on-conflict — $cbn footprint overlaps a .building's actual diff" >>"$LOG"
        continue
      fi
      picked="$cand"; break
    done

    if [ -z "$picked" ]; then
      if [ -n "$conflicted" ]; then
        # R1: a refused assignment is surfaced louder than a buried log line, so a
        # free builder stranded behind a (possibly over-broad) in-flight footprint
        # is visible on the operator board rather than silently idle.
        notify_once_nudge "laneconflict" "builderconflict_${bid}_${conflicted//[^a-zA-Z0-9._-]/_}" \
          "builder[$bid] idle but every dispatchable .assigned conflicts with an in-flight .building (last: $conflicted) — lane may be stalled behind a footprint overlap; check declared writes vs actual diff"
      else
        echo "$(ts) builder[$bid]: no conflict-free .assigned; nothing to poke" >>"$LOG"
      fi
      continue
    fi
    local pname; pname="$(basename "$picked")"
    # R3: reserve this spec so no other pane is poked toward it this tick.
    poked_specs="${poked_specs}${pname} "
    local fp_safe; fp_safe="$(printf '%s:%s' "$bid" "$pname" | tr -c 'a-zA-Z0-9._-' '_')"

    # Backoff + 3-poke cap, keyed per (pane, artifact).
    local pcf="${STATE_DIR}/builder-nudge-poke-${fp_safe}" lpf="${STATE_DIR}/builder-nudge-last-${fp_safe}"
    local pc=0 lp=0
    [ -f "$pcf" ] && pc="$(cat "$pcf" 2>/dev/null || echo 0)"
    [ -f "$lpf" ] && lp="$(cat "$lpf" 2>/dev/null || echo 0)"
    if [ "$pc" -ge "$MAX_POKES" ]; then
      notify_once_nudge "stall" "builderstall_${fp_safe}" \
        "builder[$bid] nudge stall — '$pname' poked $pc× without claim; manual review"
      echo "$(ts) builder[$bid]: stall cap ($pc>=$MAX_POKES) for '$pname'; not poking" >>"$LOG"
      continue
    fi
    local elapsed=$(( now - lp ))
    if [ "$lp" -gt 0 ] && [ "$elapsed" -lt "$BACKOFF" ]; then
      echo "$(ts) builder[$bid]: backoff (${elapsed}s<${BACKOFF}s) for '$pname'; skip" >>"$LOG"
      continue
    fi

    # Liveness + idle gates — bypassed under DRY so a dry-run reports the decision.
    if [ "$DRY" != "1" ]; then
      if ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$pane"; then
        echo "$(ts) builder[$bid]: pane $pane gone; skip" >>"$LOG"; continue
      fi
      local pact pidle
      pact="$(tmux display-message -t "$pane" -p '#{pane_last_used}' 2>/dev/null || echo 0)"
      pidle=$(( now - ${pact:-0} ))
      if [ "$pidle" -lt "$QUIET_SECS" ]; then
        echo "$(ts) builder[$bid]: pane active (${pidle}s<${QUIET_SECS}s); waiting" >>"$LOG"; continue
      fi
    fi

    local line="📨 DO-IT nudge: 1 dispatchable spec — ${pname%.assigned.md}. Run your claim/inbox scan before continuing."
    if [ "$DRY" = "1" ]; then
      echo "$(ts) DRY RUN: would poke builder[$bid] pane $pane: $line" >>"$LOG"
      echo "DRY RUN: would poke builder[$bid] pane $pane: $line"
      continue
    fi
    echo "$(ts) nudging builder[$bid] pane $pane: $line" >>"$LOG"
    # spec 287 R1 — only a VERIFIED submit bumps the poke count + backoff stamp;
    # a failed send leaves both unchanged so the next tick retries (the 3-poke
    # stall cap counts genuine deliveries, not phantom sends).
    if pane_send_message "$pane" "$line" "$LOG" "nudge"; then
      local nc=$(( pc + 1 ))
      echo "$nc" > "${pcf}.tmp.$$" && mv -f "${pcf}.tmp.$$" "$pcf"
      echo "$now" > "${lpf}.tmp.$$" && mv -f "${lpf}.tmp.$$" "$lpf"
      echo "$(ts) builder[$bid]: poke count for '$pname' now $nc" >>"$LOG"
    else
      echo "$(ts) WARN: builder[$bid] nudge send failed to $pane — count/backoff NOT advanced, will retry next tick" >>"$LOG"
    fi
  done
  shopt -u nullglob
  [ "$any" -eq 0 ] && echo "$(ts) ROLE=builder: no builder-*-active panes; nothing to do" >>"$LOG"
}

if [ "$ROLE" = "builder" ]; then
  handle_builder_nudge
  exit 0
fi

# ---------------------------------------------------------------------------
# R-B style: resolve pane from /tmp/{role}-active
# ---------------------------------------------------------------------------
ACTIVE="${NUDGE_ACTIVE_FILE:-${STATE_DIR}/${ROLE}-active}"
[ -f "$ACTIVE" ] || { echo "$(ts) no active file for $ROLE; skipping" >>"$LOG"; exit 0; }

PANE="$(grep -m1 '^PANE=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
[ -n "$PANE" ] || { echo "$(ts) no PANE in $ACTIVE; skipping" >>"$LOG"; exit 0; }

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

SESSION_ID="$(grep -m1 '^SESSION_ID=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
TRANSCRIPT="$(grep -m1 '^TRANSCRIPT=' "$ACTIVE" 2>/dev/null | cut -d= -f2-)"
if [ -n "$SESSION_ID" ]; then
  found="$(find "${HOME}/.claude/projects" -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)"
  [ -n "$found" ] && TRANSCRIPT="$found"
fi

# ---------------------------------------------------------------------------
# R4 — relay-collision guard: if a fresh HANDED-OFF baton exists, defer.
# The relay-watch cron will reboot the pane; its boot scan surfaces the inbox.
# ---------------------------------------------------------------------------
RELAY="${CWD}/docs/sessions/${ROLE}-relay.md"
if grep -qE '^status:[[:space:]]*HANDED-OFF' "$RELAY" 2>/dev/null; then
  baton_age=$(( $(date +%s) - $(stat -c %Y "$RELAY" 2>/dev/null || echo 0) ))
  if [ "$baton_age" -lt "$FRESH_SECS" ]; then
    echo "$(ts) relay pending for $ROLE (baton_age ${baton_age}s); deferring nudge this tick" >>"$LOG"
    exit 0
  fi
fi

dpg_gate "$ROLE" "$TRANSCRIPT" "$PANE" "$LOG" "nudge-$ROLE" || exit 0

# ---------------------------------------------------------------------------
# Thinker-isolation guard (spec 253 R6) — continuous enforcement at the orc tick.
#
# The bus-first authoring rule (thinkers write only to ~/.claude/, never the repo)
# is honor-system unless a guard runs at an automatic choke point. The orc owns the
# shared checkout and is the adjudicator, so its every-minute tick is the natural
# place to surface any thinker-authored doc that landed in the working tree.
#
# This SURFACES loudly (loud log line + a deduplicated alert keyed by the stray-file
# set) — it never auto-deletes; the repo-owner adjudicates (R6). It runs independently
# of whether there is outstanding inbox work, and never blocks the nudge flow.
# ---------------------------------------------------------------------------
if [ "$ROLE" = "orc" ]; then
  ISOLATION_GUARD="$(dirname "$0")/ci/check_thinker_isolation.sh"
  if [ -x "$ISOLATION_GUARD" ] || [ -f "$ISOLATION_GUARD" ]; then
    iso_out="$(bash "$ISOLATION_GUARD" "$CWD" 2>&1)"; iso_rc=$?
    if [ "$iso_rc" -ne 0 ]; then
      iso_files="$(echo "$iso_out" | grep 'STRAY:' | sed 's/.*STRAY:[[:space:]]*//' | tr '\n' ' ')"
      iso_fp="$(printf '%s' "$iso_files" | md5sum 2>/dev/null | cut -d' ' -f1)"
      [ -z "$iso_fp" ] && iso_fp="unknown"
      notify_once_nudge "isolation" "$iso_fp" \
        "thinker-isolation VIOLATION in ${CWD}: stray bus-doc(s) in shared checkout: ${iso_files}(owner adjudicates; not auto-deleted)"
      echo "$(ts) thinker-isolation guard: VIOLATION — stray: ${iso_files}" >>"$LOG"
    else
      echo "$(ts) thinker-isolation guard: clean" >>"$LOG"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# R1 — compute outstanding inbound work for this role
#
# Ledger-aware probe: reads ~/.claude/ledger/*.yml ONCE per tick via a single
# Python call (cheaper than spec_ledger.py full render; no markdown parse).
# Emits lines:
#   ORC_OWED:<spec_id>:<status>  — orc must act: registered+no-plan OR rework
#   SHIPPED:<spec_id>            — shipped but not yet accepted (rev must review)
#
# ORC action-owed = registered (no plan_file) | rework
# EXCLUDED from orc: planned, building, held, bounced, accepted, shipped, retired
# (planned/building = orc already acknowledged; held/bounced = not orc's turn)
#
# Status-advance-consume: spec fingerprints include the ledger status so that
# advancing from registered→planned clears the poke-count for the old status
# and the spec is re-evaluated. At the new status (planned) it's excluded →
# it disappears. If it later moves to rework it gets a fresh count.
#
# Rework signals come from BOTH spec-inbox spec files AND brief-inbox review
# cards. Brief-inbox review cards catch rework specs that have no spec-inbox
# file (e.g. specs that shipped before rework and had their spec-inbox file
# cleared). Spec IDs are de-duplicated: one artifact per spec_id.
# ---------------------------------------------------------------------------
SPEC_INBOX="${BUS_ROOT}/spec-inbox"
# CORR_INBOX honors a direct env override (e.g. tests pointing at a temp dir)
# in addition to the BUS_ROOT-derived default — spec 405 R2 test hermeticity.
CORR_INBOX="${CORR_INBOX:-${BUS_ROOT}/corrective-inbox}"
BRIEF_INBOX="${BUS_ROOT}/brief-inbox"
LEDGER_DIR="${BUS_ROOT}/ledger"

# ---------------------------------------------------------------------------
# corrective_is_open — canonical resolved/open convention (spec 405 R2).
#
# Correctives have no ledger status (see header comment, line ~37), so a
# resolved-but-never-moved corrective file used to keep inflating the "owed"
# count forever (rev reads this as outstanding work that doesn't exist).
#
# A corrective is OPEN unless ANY of the following is true in its frontmatter
# (case-insensitive on the value) — in which case it is RESOLVED and MUST be
# excluded from the owed set:
#   - status: one of resolved | done | closed | archived
#   - resolved: true | yes
#   - resolved_by: <non-empty>
#   - spec: <non-empty>            (folded into a spec)
#   - linked_spec: <non-empty>     (folded into a spec)
# (Living under _archive/ is excluded upstream by the find scope and is not
# re-checked here.)
#
# $1 = corrective file path. Returns 0 = OPEN, 1 = RESOLVED.
# ---------------------------------------------------------------------------
corrective_is_open() {
  local f="$1" val
  [ -f "$f" ] || return 0

  # $1 = frontmatter key regex (case-insensitive) → prints normalized value:
  # strips the key prefix, quotes, and surrounding whitespace, lowercases it.
  local _quote_chars='"'\''' # a double-quote and a single-quote, no nesting tricks
  _fm_val() {
    grep -m1 -iE "^${1}:[[:space:]]*" "$f" 2>/dev/null \
      | sed -E "s/^[^:]*:[[:space:]]*//" \
      | tr -d "${_quote_chars}" \
      | tr -d '[:space:]' \
      | tr '[:upper:]' '[:lower:]'
  }

  val="$(_fm_val status)"
  case "$val" in resolved|done|closed|archived) return 1 ;; esac

  val="$(_fm_val resolved)"
  case "$val" in true|yes) return 1 ;; esac

  val="$(_fm_val resolved_by)"
  [ -n "$val" ] && return 1

  val="$(_fm_val spec)"
  [ -n "$val" ] && return 1

  val="$(_fm_val linked_spec)"
  [ -n "$val" ] && return 1

  return 0
}

# ---------------------------------------------------------------------------
# --count-correctives (spec 405 R2) — debug/test surface: print ONLY the
# integer count of OPEN correctives in $CORR_INBOX and exit, running none of
# the rest of the nudge. Honors CORR_INBOX env override so tests can sandbox
# it against a temp dir. Must be defined after corrective_is_open (above) and
# after CORR_INBOX (above) and before any pane/lock-dependent work below.
# ---------------------------------------------------------------------------
for _arg in "$@"; do
  if [ "$_arg" = "--count-correctives" ]; then
    _open_count=0
    while IFS= read -r -d '' _cf; do
      corrective_is_open "$_cf" && _open_count=$(( _open_count + 1 ))
    done < <(find "$CORR_INBOX" -maxdepth 1 -name 'corrective-*.md' -print0 2>/dev/null)
    echo "$_open_count"
    exit 0
  fi
done

outstanding=()   # artifact display names
fingerprints=()  # parallel array: fingerprint for consume-marker keying

add_artifact() {  # $1=display-name  $2=fingerprint
  outstanding+=("$1")
  fingerprints+=("$2")
}

artifact_fp() {  # $1=filepath → print "basename:mtime"
  local base mtime
  base="$(basename "$1")"
  mtime="$(stat -c %Y "$1" 2>/dev/null || echo 0)"
  echo "${base}:${mtime}"
}

# spec_artifact_fp: includes ledger status so status advance = consume signal.
# $1=filepath  $2=ledger_status  → "basename:mtime:status"
spec_artifact_fp() {
  local base mtime
  base="$(basename "$1")"
  mtime="$(stat -c %Y "$1" 2>/dev/null || echo 0)"
  echo "${base}:${mtime}:${2}"
}

# One Python call per tick: read ledger YAML files and classify each spec.
# Output: one line per spec, prefixed ORC_OWED: or SHIPPED:
# ORC_OWED  = registered (no plan_file) | rework
# SHIPPED   = shipped  (orc done, awaiting rev acceptance)
# All other statuses (planned, building, held, bounced, accepted, retired,
# superseded, merged) are not action-owed for orc.
_ledger_statuses="$(python3 - "$LEDGER_DIR" <<'PYEOF' 2>/dev/null
import sys, os, yaml
ledger_dir = sys.argv[1]
try:
    entries = os.listdir(ledger_dir)
except OSError:
    sys.exit(0)
for fn in sorted(entries):
    if not fn.endswith('.yml') or fn.endswith('.lock'):
        continue
    path = os.path.join(ledger_dir, fn)
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception:
        continue
    if not data or 'spec_id' not in data:
        continue
    spec_id = data['spec_id']
    # Also emit short numeric prefix for lookup by brief-inbox review cards
    short_id = spec_id.split('-')[0] if '-' in spec_id else spec_id
    status = data.get('status', '')
    plan_file = data.get('plan_file') or ''
    if status == 'registered' and not plan_file.strip():
        # registered + no plan = un-picked-up; orc owes pick-up
        print(f'ORC_OWED:{spec_id}:{status}')
        print(f'SHORT:{short_id}:{spec_id}:{status}')
    elif status == 'rework':
        # orc owes the rebuild
        print(f'ORC_OWED:{spec_id}:{status}')
        print(f'SHORT:{short_id}:{spec_id}:{status}')
    elif status == 'shipped':
        print(f'SHIPPED:{spec_id}')
        print(f'SHORT_SHIPPED:{short_id}:{spec_id}')
PYEOF
)"

# Load into associative arrays for O(1) lookup
# `=()` initializes them as SET so an EMPTY/unreadable ledger can't crash the
# probe under `set -u` (spec 278 reliability — a bare `declare -A` on bash 5.x
# makes `${#arr[@]}` / `${!arr[@]}` "unbound" when no key is ever assigned).
declare -A _orc_owed_ids=()        # spec_id -> status string
declare -A _orc_owed_by_short=()   # short_id -> "spec_id:status"
declare -A _shipped_ids=()         # spec_id -> 1
declare -A _shipped_by_short=()    # short_id -> spec_id
while IFS= read -r _line; do
  case "$_line" in
    ORC_OWED:*)
      _rest="${_line#ORC_OWED:}"
      _sid="${_rest%:*}"
      _sstat="${_rest##*:}"
      _orc_owed_ids["$_sid"]="$_sstat"
      ;;
    SHORT:*)
      _rest="${_line#SHORT:}"
      _short="${_rest%%:*}"
      _rest2="${_rest#*:}"
      _orc_owed_by_short["$_short"]="$_rest2"
      ;;
    SHIPPED:*)
      _shipped_ids["${_line#SHIPPED:}"]=1
      ;;
    SHORT_SHIPPED:*)
      _rest="${_line#SHORT_SHIPPED:}"
      _short="${_rest%%:*}"
      _full="${_rest#*:}"
      _shipped_by_short["$_short"]="$_full"
      ;;
  esac
done <<< "$_ledger_statuses"

echo "$(ts) ledger probe: ${#_orc_owed_ids[@]} orc-owed, ${#_shipped_ids[@]} shipped" >>"$LOG"

if [ "$ROLE" = "orc" ]; then
  # Track which spec_ids have already been added (for dedup across spec-inbox and brief-inbox).
  declare -A _added_spec_ids=()

  # spec-inbox: *-spec.md files that are ORC_OWED (registered+no-plan OR rework).
  # Derive spec_id from filename: strip trailing -spec.md.
  # Files with no ledger record (old date-slug specs, pre-ledger) are ignored.
  # Fingerprint includes ledger status: status advance clears prior poke-count.
  while IFS= read -r -d '' f; do
    base="$(basename "$f")"
    spec_id="${base%-spec.md}"
    if [ -n "${_orc_owed_ids[$spec_id]+_}" ]; then
      _stat="${_orc_owed_ids[$spec_id]}"
      add_artifact "$base" "$(spec_artifact_fp "$f" "$_stat")"
      _added_spec_ids["$spec_id"]=1
    fi
  done < <(find "$SPEC_INBOX" -maxdepth 1 -name '*-spec.md' -print0 2>/dev/null)

  # brief-inbox: *.review.md files where ledger status = rework.
  # These catch rework specs that have no spec-inbox file (spec was cleared after
  # first ship but then sent back for rework by rev).
  # The spec: front-matter field can be a full slug OR a short numeric id.
  while IFS= read -r -d '' f; do
    card_spec="$(grep -m1 '^spec:[[:space:]]*' "$f" 2>/dev/null | sed 's/^spec:[[:space:]]*//' | tr -d '[:space:]')"
    [ -z "$card_spec" ] && continue
    # Resolve to full spec_id + status
    _spec_entry=""
    _spec_status=""
    if [ -n "${_orc_owed_ids[$card_spec]+_}" ]; then
      _spec_entry="$card_spec"
      _spec_status="${_orc_owed_ids[$card_spec]}"
    else
      # card_spec may be a short numeric id
      _short_num="${card_spec%%[^0-9]*}"
      if [ -n "$_short_num" ] && [ -n "${_orc_owed_by_short[$_short_num]+_}" ]; then
        _combined="${_orc_owed_by_short[$_short_num]}"
        _spec_entry="${_combined%:*}"
        _spec_status="${_combined##*:}"
      fi
    fi
    if [ -n "$_spec_entry" ] && [ -z "${_added_spec_ids[$_spec_entry]+_}" ]; then
      add_artifact "$(basename "$f")" "$(spec_artifact_fp "$f" "$_spec_status")"
      _added_spec_ids["$_spec_entry"]=1
    fi
  done < <(find "$BRIEF_INBOX" -maxdepth 1 -name '*.review.md' -print0 2>/dev/null)

  # spec-inbox/memo-*.md: to: orc OR to: absent (spec-inbox memos default to orc)
  while IFS= read -r -d '' f; do
    to_val="$(grep -m1 '^to:[[:space:]]*' "$f" 2>/dev/null | awk '{print $2}' | tr -d '[:space:]')"
    # Include if to: is 'orc', empty value, or field absent entirely
    if [ -z "$to_val" ] || [ "$to_val" = "orc" ]; then
      add_artifact "$(basename "$f")" "$(artifact_fp "$f")"
    fi
  done < <(find "$SPEC_INBOX" -maxdepth 1 -name 'memo-*.md' -print0 2>/dev/null)

  # corrective-inbox: all corrective-*.md files not in _archive AND still OPEN
  # (spec 405 R2 — a resolved corrective left on disk must not inflate owed count).
  while IFS= read -r -d '' f; do
    corrective_is_open "$f" && add_artifact "$(basename "$f")" "$(artifact_fp "$f")"
  done < <(find "$CORR_INBOX" -maxdepth 1 -name 'corrective-*.md' -print0 2>/dev/null)

  # brief-inbox/memo-*.md with to: orc
  while IFS= read -r -d '' f; do
    to_val="$(grep -m1 '^to:[[:space:]]*' "$f" 2>/dev/null | awk '{print $2}' | tr -d '[:space:]')"
    if [ "$to_val" = "orc" ]; then
      add_artifact "$(basename "$f")" "$(artifact_fp "$f")"
    fi
  done < <(find "$BRIEF_INBOX" -maxdepth 1 -name 'memo-*.md' -print0 2>/dev/null)

  # R1 (spec 278) — build-lane *.ready.md: a builder handed a branch to the
  # integrator and it sits unmerged. THE STALL BUG: §7 documents this poke target
  # but it was never enumerated, so a .ready file never poked the integrator.
  # "Consumed" = the .ready.md leaves the lane (merged → archived) OR its ledger
  # row leaves `ready`; either way it disappears from this set. Identity is
  # filename+mtime (a re-flip = new mtime = re-pokes), backoff/cap/relay as usual.
  while IFS= read -r -d '' f; do
    add_artifact "$(basename "$f")" "$(artifact_fp "$f")"
  done < <(find "$BUILD_LANE_DIR" -maxdepth 1 -name '*.ready.md' -print0 2>/dev/null)

elif [ "$ROLE" = "rev" ]; then
  # Review cards: *.review.md files whose spec has ledger status=shipped.
  # The card's spec_id comes from the "spec:" front-matter header line.
  # The field may be a full slug or a short numeric prefix — check both.
  # Cards whose spec is already accepted (or any other non-shipped status)
  # are archival debt — not poked.
  while IFS= read -r -d '' f; do
    card_spec="$(grep -m1 '^spec:[[:space:]]*' "$f" 2>/dev/null | sed 's/^spec:[[:space:]]*//' | tr -d '[:space:]')"
    [ -z "$card_spec" ] && continue
    _is_shipped=0
    if [ -n "${_shipped_ids[$card_spec]+_}" ]; then
      _is_shipped=1
    else
      _short_num="${card_spec%%[^0-9]*}"
      if [ -n "$_short_num" ] && [ -n "${_shipped_by_short[$_short_num]+_}" ]; then
        _is_shipped=1
      fi
    fi
    if [ "$_is_shipped" = "1" ]; then
      add_artifact "$(basename "$f")" "$(artifact_fp "$f")"
    fi
  done < <(find "$BRIEF_INBOX" -maxdepth 1 -name '*.review.md' -print0 2>/dev/null)

  # brief-inbox/memo-*.md with to: rev
  while IFS= read -r -d '' f; do
    to_val="$(grep -m1 '^to:[[:space:]]*' "$f" 2>/dev/null | awk '{print $2}' | tr -d '[:space:]')"
    if [ "$to_val" = "rev" ]; then
      add_artifact "$(basename "$f")" "$(artifact_fp "$f")"
    fi
  done < <(find "$BRIEF_INBOX" -maxdepth 1 -name 'memo-*.md' -print0 2>/dev/null)

  # corrective-inbox: artifacts explicitly routed to rev — OPEN ones only
  # (spec 405 R2 — a resolved rev-routed corrective must not inflate rev's owed count).
  while IFS= read -r -d '' f; do
    corrective_is_open "$f" || continue
    to_val="$(grep -m1 '^to:[[:space:]]*' "$f" 2>/dev/null | awk '{print $2}' | tr -d '[:space:]')"
    if [ "$to_val" = "rev" ]; then
      add_artifact "$(basename "$f")" "$(artifact_fp "$f")"
    fi
  done < <(find "$CORR_INBOX" -maxdepth 1 -name 'corrective-*.md' -print0 2>/dev/null)
fi

# ---------------------------------------------------------------------------
# R1 AC1.1 — empty set → exit before any pane interaction
# ---------------------------------------------------------------------------
if [ "${#outstanding[@]}" -eq 0 ]; then
  echo "$(ts) nothing outstanding for $ROLE; no keys sent" >>"$LOG"
  exit 0
fi

echo "$(ts) $ROLE outstanding set (${#outstanding[@]}): ${outstanding[*]}" >>"$LOG"

# ---------------------------------------------------------------------------
# R3 — backoff + cap: determine which artifacts can be poked this tick
# ---------------------------------------------------------------------------
now=$(date +%s)
to_poke_names=()
to_poke_fps=()

for i in "${!outstanding[@]}"; do
  name="${outstanding[$i]}"
  fp="${fingerprints[$i]}"
  # sanitize fingerprint for use in filename
  fp_safe="${fp//[^a-zA-Z0-9._-]/_}"

  poke_count_f="${STATE_DIR}/${ROLE}-nudge-poke-${fp_safe}"
  last_poke_f="${STATE_DIR}/${ROLE}-nudge-last-${fp_safe}"

  poke_count=0
  [ -f "$poke_count_f" ] && poke_count="$(cat "$poke_count_f" 2>/dev/null || echo 0)"
  last_poke=0
  [ -f "$last_poke_f" ]  && last_poke="$(cat "$last_poke_f" 2>/dev/null || echo 0)"

  # 3-poke cap: stall alert + skip
  if [ "$poke_count" -ge "$MAX_POKES" ]; then
    stall_fp="stall_${fp_safe}"
    notify_once_nudge "stall" "$stall_fp" \
      "${ROLE} nudge stall — artifact '$name' poked $poke_count× without consume; manual review needed"
    echo "$(ts) stall cap reached for '$name' (pokes=$poke_count >= max=$MAX_POKES); not poking" >>"$LOG"
    continue
  fi

  # Backoff: re-poke only if BACKOFF seconds have passed since last poke
  elapsed=$(( now - last_poke ))
  if [ "$last_poke" -gt 0 ] && [ "$elapsed" -lt "$BACKOFF" ]; then
    echo "$(ts) backoff active for '$name' (elapsed ${elapsed}s < ${BACKOFF}s); skipping this tick" >>"$LOG"
    continue
  fi

  to_poke_names+=("$name")
  to_poke_fps+=("$fp")
done

# All artifacts either at cap or in backoff → nothing to poke
if [ "${#to_poke_names[@]}" -eq 0 ]; then
  echo "$(ts) all outstanding artifacts in backoff or at stall cap; no keys sent" >>"$LOG"
  exit 0
fi

# ---------------------------------------------------------------------------
# Pane liveness check  (DRY bypasses the hard exit so a dry-run reports its
# full decision — including the final WOULD-poke line — without a live pane.)
# ---------------------------------------------------------------------------
if ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$PANE"; then
  if [ "$DRY" = "1" ]; then
    echo "$(ts) DRY: pane $PANE not live — would normally skip; continuing for dry-run report" >>"$LOG"
  else
    echo "$(ts) pane $PANE gone; role $ROLE not running; no keys sent" >>"$LOG"
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# R2 — transcript quiet-gate + human-active gate
#
# For REV: use the existing 45s mtime gate (transcript touched = activity).
#
# For ORC: two-layer gate:
#   Layer 1 — human-active detection: scan the session transcript JSONL to
#   find the most recent turn where type='user' AND message.content contains
#   a text block (not just a tool_result). This is a genuine human message.
#   If such a turn is within ORC_HUMAN_WINDOW seconds (default 300=5 min),
#   defer — orc is in a live human-driven conversation.
#   Mechanism: transcript JSONL, type='user', content[*].type='text' present.
#   Layer 2 — idle threshold fallback: if no transcript is readable (no JSONL
#   found), fall back to ORC_IDLE_SECS (default 900s) instead of QUIET_SECS=45s.
#
# DRY (spec 278): a dry-run skips the quiet/idle gate entirely so it always
# reports its WOULD-poke decision without a live, idle pane (otherwise AC1-AC4
# could only be observed against a real session). Non-DRY behavior is unchanged.
# ---------------------------------------------------------------------------
if [ "$DRY" = "1" ]; then
  echo "$(ts) DRY: skipping quiet/idle gate; reporting decision" >>"$LOG"
elif [ "$ROLE" = "orc" ]; then
  if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    # ORC idle gate: dpg_gate already handled human-presence; check agent activity
    _transcript_age=$(( now - $(stat -c %Y "$TRANSCRIPT") ))
    if [ "$_transcript_age" -lt "$ORC_IDLE_SECS" ]; then
      echo "$(ts) orc idle gate: transcript ${_transcript_age}s < ${ORC_IDLE_SECS}s (agent active); waiting" >>"$LOG"
      exit 0
    fi
  else
    # No transcript — use long idle threshold via tmux
    pane_activity="$(tmux display-message -t "$PANE" -p '#{pane_last_used}' 2>/dev/null || echo 0)"
    pane_idle=$(( now - ${pane_activity:-0} ))
    if [ "$pane_idle" -lt "$ORC_IDLE_SECS" ]; then
      echo "$(ts) orc idle gate (no transcript, tmux fallback): pane idle ${pane_idle}s < ${ORC_IDLE_SECS}s; waiting" >>"$LOG"
      exit 0
    fi
  fi
else
  # REV: original 45s mtime gate (unchanged)
  if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    age=$(( now - $(stat -c %Y "$TRANSCRIPT") ))
    if [ "$age" -lt "$QUIET_SECS" ]; then
      echo "$(ts) pane $PANE active (transcript ${age}s < ${QUIET_SECS}s); waiting" >>"$LOG"
      exit 0
    fi
  else
    pane_activity="$(tmux display-message -t "$PANE" -p '#{pane_last_used}' 2>/dev/null || echo 0)"
    pane_idle=$(( now - ${pane_activity:-0} ))
    if [ "$pane_idle" -lt "$QUIET_SECS" ]; then
      echo "$(ts) pane $PANE active (tmux activity ${pane_idle}s < ${QUIET_SECS}s); waiting" >>"$LOG"
      exit 0
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Build nudge line
# ---------------------------------------------------------------------------
n="${#to_poke_names[@]}"
names_str="$(IFS=', '; echo "${to_poke_names[*]}")"
nudge_line="📨 DO-IT nudge: ${n} waiting — ${names_str}. Run your inbox scan before continuing."

# ---------------------------------------------------------------------------
# R2 — send keys (or dry-run)
# ---------------------------------------------------------------------------
if [ "$DRY" = "1" ]; then
  echo "$(ts) DRY RUN: would type into pane $PANE (role=$ROLE): $nudge_line" >>"$LOG"
  echo "$(ts) DRY RUN: would type into pane $PANE (role=$ROLE): $nudge_line"
  exit 0
fi

echo "$(ts) nudging $ROLE pane $PANE: $nudge_line" >>"$LOG"
# spec 287 R1 — a FAILED submit must NOT bump the poke counters (that would burn
# the 3-poke stall budget on a phantom delivery and fake a stall). Log and bail
# so the next cron tick retries with the budget intact; the durable inbox/lane
# state is unchanged, so nothing is lost — only a tick of latency.
if ! pane_send_message "$PANE" "$nudge_line" "$LOG" "nudge"; then
  echo "$(ts) WARN: nudge send failed or did not submit to pane $PANE — poke counts NOT advanced, will retry next tick" >>"$LOG"
  exit 0
fi

# ---------------------------------------------------------------------------
# R3 — update poke-count + last-poked markers (atomic write like relay)
# ---------------------------------------------------------------------------
for i in "${!to_poke_fps[@]}"; do
  fp="${to_poke_fps[$i]}"
  fp_safe="${fp//[^a-zA-Z0-9._-]/_}"

  poke_count_f="${STATE_DIR}/${ROLE}-nudge-poke-${fp_safe}"
  last_poke_f="${STATE_DIR}/${ROLE}-nudge-last-${fp_safe}"

  poke_count=0
  [ -f "$poke_count_f" ] && poke_count="$(cat "$poke_count_f" 2>/dev/null || echo 0)"
  new_count=$(( poke_count + 1 ))

  _ptmp="${poke_count_f}.tmp.$$"
  echo "$new_count" > "$_ptmp" && mv -f "$_ptmp" "$poke_count_f"

  _ltmp="${last_poke_f}.tmp.$$"
  echo "$now" > "$_ltmp" && mv -f "$_ltmp" "$last_poke_f"

  # Clear any stall alert for this artifact if it was previously stalled but
  # consumed and now re-appears with a new fingerprint (new mtime).
  clear_nudge_alert "stall_${fp_safe}"

  echo "$(ts) poke count for '${to_poke_names[$i]}' now $new_count (last_poke $now)" >>"$LOG"
done
