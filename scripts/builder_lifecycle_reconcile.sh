#!/bin/bash
# builder_lifecycle_reconcile.sh — builder bus self-healing reconciler (spec 298).
#
# Treats LIVE tmux as ground truth and heals two drift classes on every tick:
#
#   R1 — sentinel reconcile (FIRST): over /tmp/builder-*-active, normalize ids to
#        the scripts/builder-id.sh scheme (strip leading % from PANE), dedup by
#        PANE (one pane = one canonical builder, keep newest by mtime), and reap
#        sentinels whose PANE is not in the live-pane set (MOVE to a reaped dir,
#        never silently delete). After R1: exactly one canonical sentinel per live
#        pane.
#
#   R2 — lane reclaim (AFTER R1, so it sees healed sentinels): for each
#        ~/.claude/build-lane/*.building.md whose claimer has no live sentinel/pane,
#        return it to .assigned — guarded by a fresh-relay defer (HANDED-OFF/RECYCLE
#        baton) and a stateful grace window — then advance the ledger building→planned.
#
# HERMETICITY (hard rule): every external path/command is env-overridable. No live
# /tmp/builder-* state or ~/.claude state is touched in test.
#
# Env overrides (defaults preserve prod):
#   RECONCILE_SENTINEL_GLOB   /tmp/builder-*-active
#   RECONCILE_LANE_DIR        $HOME/.claude/build-lane
#   RECONCILE_LEDGER_DIR      $HOME/.claude/ledger        (passed as DOIT_LEDGER_DIR)
#   RECONCILE_LOG             /tmp/builder-reconcile.log
#   RECONCILE_LOCK            <RECONCILE_LOG>.lock
#   RECONCILE_REAPED_DIR      /tmp/builder-reaped
#   RECONCILE_REPO_DOCS       ${REPO_ROOT}/docs/sessions   (fallback baton dir)
#   RECONCILE_DRY             0 (1 = log every decision, mutate NOTHING)
#   RECLAIM_GRACE_SECS        180
#   BATON_FRESH_SECS          5400
#   RECONCILE_LIVE_PANES      (test stub: space-separated pane ids INSTEAD of tmux;
#                              honored even when SET-but-EMPTY = "no live panes")
#   SPEC_LEDGER_BIN           "<venv python> <repo>/scripts/spec_ledger.py"
set -u
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-python3}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

RECONCILE_SENTINEL_GLOB="${RECONCILE_SENTINEL_GLOB:-/tmp/builder-*-active}"
RECONCILE_LANE_DIR="${RECONCILE_LANE_DIR:-$HOME/.claude/build-lane}"
RECONCILE_LEDGER_DIR="${RECONCILE_LEDGER_DIR:-$HOME/.claude/ledger}"
RECONCILE_LOG="${RECONCILE_LOG:-/tmp/builder-reconcile.log}"
RECONCILE_LOCK="${RECONCILE_LOCK:-${RECONCILE_LOG}.lock}"
RECONCILE_REAPED_DIR="${RECONCILE_REAPED_DIR:-/tmp/builder-reaped}"
RECONCILE_REPO_DOCS="${RECONCILE_REPO_DOCS:-${REPO_ROOT}/docs/sessions}"
RECONCILE_DRY="${RECONCILE_DRY:-0}"
RECLAIM_GRACE_SECS="${RECLAIM_GRACE_SECS:-180}"
BATON_FRESH_SECS="${BATON_FRESH_SECS:-5400}"
SPEC_LEDGER_BIN="${SPEC_LEDGER_BIN:-${PYTHON:-python3} $REPO_ROOT/scripts/spec_ledger.py}"

GLOB_DIR="$(dirname "$RECONCILE_SENTINEL_GLOB")"

# --- one flock so two ticks never race (mirrors orc-relay-watch.sh) -----------
mkdir -p "$(dirname "$RECONCILE_LOCK")" 2>/dev/null || true
exec 9>"$RECONCILE_LOCK"
flock -n 9 || exit 0

ts() { date -u +%FT%TZ; }

# canonical builder id from a pane/id token (spec 279 builder-id.sh rule):
# strip a leading '%' then map every non-alnum char to '_'.
canon_id() { local p="${1#%}"; printf '%s' "$p" | tr -c 'a-zA-Z0-9' '_'; }

mkdir -p "$(dirname "$RECONCILE_LOG")" 2>/dev/null || true
log() { printf '%s reconcile: %s\n' "$(ts)" "$1" >>"$RECONCILE_LOG"; }

DRY=0
[ "$RECONCILE_DRY" = "1" ] && DRY=1

_live_panes() {  # honor RECONCILE_LIVE_PANES even when SET-but-EMPTY (no live panes)
  if [ -n "${RECONCILE_LIVE_PANES+x}" ]; then
    local p
    for p in ${RECONCILE_LIVE_PANES:-}; do printf '%s\n' "$p"; done
  else
    tmux list-panes -a -F '#{pane_id}' 2>/dev/null
  fi
}
is_live_pane() { [ -n "$1" ] && _live_panes | grep -qx "$1"; }

_field() { grep -m1 "^$2=" "$1" 2>/dev/null | cut -d= -f2-; }

# move a sentinel to the reaped audit dir, preserving its basename (collision-safe).
reap_to_audit() {  # $1=file  $2=why
  local f="$1" why="$2" base dest
  base="$(basename "$f")"
  dest="$RECONCILE_REAPED_DIR/$base"
  if [ "$DRY" = 1 ]; then
    log "[DRY] would reap $f -> $dest ($why)"
    return 0
  fi
  mkdir -p "$RECONCILE_REAPED_DIR"
  [ -e "$dest" ] && dest="${dest}.$(date -u +%s).$$"
  mv -f "$f" "$dest" && log "REAPED $f -> $dest ($why)"
}

log "tick start (dry=$DRY) live_panes=[$(_live_panes | tr '\n' ' ')]"

# ============================================================ R1: sentinel reconcile
declare -A G_FILES   # pane -> newline-joined file list
declare -A M_MTIME   # file -> mtime epoch

for f in $RECONCILE_SENTINEL_GLOB; do
  [ -e "$f" ] || continue
  pane="$(_field "$f" PANE)"
  if [ -z "$pane" ]; then
    log "skip $f: no PANE= line"
    continue
  fi
  M_MTIME["$f"]="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
  G_FILES["$pane"]+="$f"$'\n'
done

for pane in "${!G_FILES[@]}"; do
  files=()
  while IFS= read -r ln; do [ -n "$ln" ] && files+=("$ln"); done <<<"${G_FILES[$pane]}"

  if ! is_live_pane "$pane"; then
    log "pane $pane NOT live -> reaping ${#files[@]} sentinel(s)"
    for f in "${files[@]}"; do reap_to_audit "$f" "dead-pane $pane"; done
    continue
  fi

  # pane is live: keep the newest by mtime, drop the rest
  newest=""; newest_mt=-1
  for f in "${files[@]}"; do
    mt="${M_MTIME[$f]:-0}"
    if [ "$mt" -gt "$newest_mt" ]; then newest_mt="$mt"; newest="$f"; fi
  done
  for f in "${files[@]}"; do
    [ "$f" = "$newest" ] && continue
    log "pane $pane dedup: dropping older alias $f (survivor $newest)"
    reap_to_audit "$f" "dup-of-pane $pane"
  done

  # normalize the survivor to the canonical id/name
  canon="$(canon_id "$pane")"
  expected="$GLOB_DIR/builder-${canon}-active"
  cur_bid="$(_field "$newest" BUILDER_ID)"
  if [ "$newest" != "$expected" ] || [ "$cur_bid" != "$canon" ]; then
    cwd="$(_field "$newest" CWD)"
    token="$(_field "$newest" TOKEN)"
    if [ "$DRY" = 1 ]; then
      log "[DRY] would normalize $newest -> $expected (BUILDER_ID $cur_bid -> $canon)"
    else
      tmp="${expected}.tmp.$$"
      {
        printf 'PANE=%s\n' "$pane"
        printf 'CWD=%s\n' "$cwd"
        printf 'TOKEN=%s\n' "$token"
        printf 'BUILDER_ID=%s\n' "$canon"
      } >"$tmp"
      mv -f "$tmp" "$expected"
      [ "$newest" != "$expected" ] && rm -f "$newest"
      log "NORMALIZED pane $pane -> $expected (BUILDER_ID $cur_bid -> $canon, TOKEN preserved)"
    fi
  else
    log "pane $pane survivor $newest already canonical"
  fi
done

# ============================================================ R2: lane reclaim
if [ -d "$RECONCILE_LANE_DIR" ]; then
  for lane in "$RECONCILE_LANE_DIR"/*.building.md; do
    [ -e "$lane" ] || continue
    base="$(basename "$lane")"
    spec_id="${base%.building.md}"

    cb="$(grep -m1 '^claimed_by:' "$lane" 2>/dev/null | sed 's/^claimed_by:[[:space:]]*//')"
    cb="${cb%%[[:space:]]*}"
    id="$(canon_id "${cb#builder-}")"
    if [ -z "$id" ]; then
      log "lane $base: no claimed_by id; skipping"
      continue
    fi

    # liveness: canonical sentinel exists AND its PANE is live
    sentinel="$GLOB_DIR/builder-${id}-active"
    claimer_live=0
    if [ -f "$sentinel" ]; then
      spane="$(_field "$sentinel" PANE)"
      is_live_pane "$spane" && claimer_live=1
    fi
    if [ "$claimer_live" = 1 ]; then
      log "lane $base: claimer builder-$id live; ok"
      continue
    fi

    # --- relay-defer: a fresh HANDED-OFF/RECYCLE baton means a resume is mid-flight
    wt="$(grep -m1 '^worktree:' "$lane" 2>/dev/null | sed 's/^worktree:[[:space:]]*//')"
    wt="${wt%%[[:space:]]*}"
    baton_defer=0
    for baton in "$wt/docs/sessions/builder-${id}-relay.md" "$RECONCILE_REPO_DOCS/builder-${id}-relay.md"; do
      [ -n "$baton" ] && [ -f "$baton" ] || continue
      grep -Eq '^status:[[:space:]]*(HANDED-OFF|RECYCLE)' "$baton" || continue
      bm="$(stat -c %Y "$baton" 2>/dev/null || echo 0)"
      age=$(( $(date +%s) - bm ))
      if [ "$age" -lt "$BATON_FRESH_SECS" ]; then
        log "lane $base: fresh relay/recycle baton $baton (age ${age}s); not reclaiming"
        baton_defer=1
        break
      fi
    done
    [ "$baton_defer" = 1 ] && continue

    # --- grace: stamp on first absent observation, reclaim only past the window
    stamp="$(grep -m1 '^claimer_absent_since:' "$lane" 2>/dev/null | sed 's/^claimer_absent_since:[[:space:]]*//')"
    stamp="${stamp%%[[:space:]]*}"
    if [ -z "$stamp" ]; then
      if [ "$DRY" = 1 ]; then
        log "[DRY] lane $base: claimer builder-$id absent; would start grace"
      else
        tmp="${lane}.tmp.$$"
        cp "$lane" "$tmp"
        printf 'claimer_absent_since: %s\n' "$(ts)" >>"$tmp"
        mv -f "$tmp" "$lane"
        log "lane $base: claimer builder-$id absent; grace started"
      fi
      continue
    fi
    absent_epoch="$(date -d "$stamp" +%s 2>/dev/null || echo 0)"
    age=$(( $(date +%s) - absent_epoch ))
    if [ "$age" -lt "$RECLAIM_GRACE_SECS" ]; then
      log "lane $base: claimer builder-$id absent ${age}s < grace ${RECLAIM_GRACE_SECS}s; waiting"
      continue
    fi

    # --- RECLAIM ---
    assigned="${lane%.building.md}.assigned.md"
    if [ "$DRY" = 1 ]; then
      log "[DRY] RECLAIM lane $base -> $(basename "$assigned"); would ledger-set $spec_id planned"
      continue
    fi
    tmp="${assigned}.tmp.$$"
    grep -v '^claimer_absent_since:' "$lane" >"$tmp"
    printf 'reclaimed_from: builder-%s @ %s (claimer pane dead, no pending relay)\n' "$id" "$(ts)" >>"$tmp"
    mv -f "$tmp" "$assigned"
    rm -f "$lane"
    log "RECLAIMED $base -> $(basename "$assigned") (claimer builder-$id pane dead, no pending relay)"

    # advance the ledger building -> planned (auto-appends history)
    if DOIT_LEDGER_DIR="$RECONCILE_LEDGER_DIR" $SPEC_LEDGER_BIN set "$spec_id" planned \
        --by builder-lifecycle-reconcile \
        --reason "reclaimed: claimer builder-$id pane dead, no pending relay" >>"$RECONCILE_LOG" 2>&1; then
      log "ledger $spec_id -> planned"
    else
      log "WARN ledger set $spec_id planned FAILED (see above)"
    fi
  done
fi

log "tick done"
exit 0
