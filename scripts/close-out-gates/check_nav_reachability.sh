#!/usr/bin/env bash
# check_nav_reachability.sh — F3 orphan-nav pre-ship gate (ORC-RESET STEP 6).
#
# A surface can be built + populated yet ORPHANED: no sidebar link, reachable
# only by typing the URL. This recurred 4× (124, 125/127/128, 075). The close-out
# must fail when a spec adds a NEW dashboard route that nothing in app-sidebar.tsx
# links to.
#
# Deterministic rule: for every newly-added page.tsx between BASE and HEAD, derive
# the route segments (dropping (route-groups) and [dynamic] params). Walk from the
# LEAF upward to the first STATIC segment — that segment (the surface's own identity)
# must be grep-able in app-sidebar.tsx. An ancestor prefix being linked is NOT enough.
# A drill-down child (e.g. cash/[settlementId]) passes via its parent (`cash`, the
# first static segment above the dynamic leaf); a brand-new top-level surface
# (e.g. /stockout-risk) whose own segment isn't linked FAILS.
#
# Usage:   check_nav_reachability.sh [BASE_REF] [HEAD_REF]
#   BASE_REF defaults to origin/master (fallback master), HEAD_REF to HEAD.
# Exit:    0 = every new route reachable (or no new routes); 1 = orphan(s) found.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
SIDEBAR="dashboard/src/components/app-sidebar.tsx"
APP_DIR="dashboard/src/app"

BASE="${1:-}"
HEAD_REF="${2:-HEAD}"
if [ -z "$BASE" ]; then
  if git rev-parse --verify -q origin/master >/dev/null; then BASE="origin/master"; else BASE="master"; fi
fi

if [ ! -f "$SIDEBAR" ]; then
  echo "FAIL: sidebar not found at $SIDEBAR"; exit 1
fi

# Newly-ADDED page.tsx files (status A) under the app dir.
mapfile -t NEW_PAGES < <(git diff --name-only --diff-filter=A "$BASE" "$HEAD_REF" -- "$APP_DIR" 2>/dev/null | grep -E '/page\.tsx$' || true)

if [ "${#NEW_PAGES[@]}" -eq 0 ]; then
  echo "PASS: no new page.tsx routes added ($BASE..$HEAD_REF)"; exit 0
fi

orphans=()
for p in "${NEW_PAGES[@]}"; do
  # route = path under app dir, minus /page.tsx
  route="${p#"$APP_DIR"/}"; route="${route%/page.tsx}"
  reachable=0
  # split into segments; a segment counts if static (not a (group) or [param])
  IFS='/' read -ra segs <<< "$route"
  # walk from the LEAF upward; the first STATIC segment is the surface's own
  # identity and must be grep-able. (group)/[param] segments are skipped.
  identity=""
  for (( i=${#segs[@]}-1; i>=0; i-- )); do
    s="${segs[$i]}"
    case "$s" in
      \(*\)|\[*\]|"") continue ;;   # route-group / dynamic / empty — skip
      *) identity="$s"; break ;;
    esac
  done
  if [ -n "$identity" ] && grep -qF "$identity" "$SIDEBAR"; then reachable=1; fi
  if [ "$reachable" -eq 0 ]; then orphans+=("$route  ($p)"); fi
done

if [ "${#orphans[@]}" -gt 0 ]; then
  echo "FAIL: ${#orphans[@]} new route(s) ORPHANED — not grep-able in $SIDEBAR:"
  for o in "${orphans[@]}"; do echo "  ✗ $o"; done
  echo "Add a sidebar link (or confirm it's a drill-down whose parent IS linked) before close-out."
  exit 1
fi

echo "PASS: all ${#NEW_PAGES[@]} new route(s) reachable from $SIDEBAR"
exit 0
