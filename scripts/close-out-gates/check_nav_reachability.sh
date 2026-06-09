#!/usr/bin/env bash
# check_nav_reachability.sh — orphan-nav pre-ship gate (reference implementation).
#
# A surface can be built + populated yet ORPHANED: no sidebar link, reachable only
# by typing the URL. In the reference deployment this recurred 4× before the gate
# existed. Wire this into your close-out so it FAILS when a spec adds a NEW route
# that nothing in your nav/sidebar links to.
#
# Tuned for a Next.js App Router layout out of the box; override the two paths via
# env for any other framework that has (a) a file-routing dir and (b) a nav file:
#   NAV_SIDEBAR   nav/sidebar source file to grep   (default below)
#   NAV_APP_DIR   the file-routing root             (default below)
#
# Rule: for every newly-added page.tsx between BASE and HEAD, derive the route, walk
# from the LEAF up to the first STATIC segment (the surface's own identity, dropping
# (route-groups) and [dynamic] params), and require THAT segment to be grep-able in
# the nav file. A drill-down child passes via its linked parent; a brand-new
# top-level surface whose own segment isn't linked FAILS.
#
# Usage:  check_nav_reachability.sh [BASE_REF] [HEAD_REF]
# Exit:   0 = every new route reachable (or none added); 1 = orphan(s) found.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
SIDEBAR="${NAV_SIDEBAR:-dashboard/src/components/app-sidebar.tsx}"
APP_DIR="${NAV_APP_DIR:-dashboard/src/app}"

BASE="${1:-}"
HEAD_REF="${2:-HEAD}"
if [ -z "$BASE" ]; then
  if git rev-parse --verify -q origin/master >/dev/null; then BASE="origin/master"; else BASE="master"; fi
fi

if [ ! -f "$SIDEBAR" ]; then
  echo "FAIL: nav file not found at $SIDEBAR (set NAV_SIDEBAR)"; exit 1
fi

mapfile -t NEW_PAGES < <(git diff --name-only --diff-filter=A "$BASE" "$HEAD_REF" -- "$APP_DIR" 2>/dev/null | grep -E '/page\.tsx$' || true)

if [ "${#NEW_PAGES[@]}" -eq 0 ]; then
  echo "PASS: no new page.tsx routes added ($BASE..$HEAD_REF)"; exit 0
fi

orphans=()
for p in "${NEW_PAGES[@]}"; do
  route="${p#"$APP_DIR"/}"; route="${route%/page.tsx}"
  reachable=0
  IFS='/' read -ra segs <<< "$route"
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
  echo "Add a nav link (or confirm it's a drill-down whose parent IS linked) before close-out."
  exit 1
fi

echo "PASS: all ${#NEW_PAGES[@]} new route(s) reachable from $SIDEBAR"
exit 0
