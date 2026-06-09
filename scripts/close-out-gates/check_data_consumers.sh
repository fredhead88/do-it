#!/usr/bin/env bash
# check_data_consumers.sh — cross-spec data-dependency derivation (reference impl).
#
# When a spec declares it `populates: <table>`, its close-out should re-verify the
# SURFACES that CONSUME that table — not just its own criteria. In the reference
# deployment a data spec shipped its table while a downstream panel went stale yet
# read `shipped`. This derives the consumer set from the code (no hand-maintained
# map): every router/lib that references the table, plus the routers that import a
# referencing lib module (one hop).
#
# Tuned for a Python routers/lib split; override via env for your layout:
#   CONSUMERS_ROUTERS_DIR   where request handlers live   (default below)
#   CONSUMERS_LIB_DIR       shared library modules        (default below)
#
# Usage:  check_data_consumers.sh <table_or_view_name>
# Output: consuming handler files (DIRECT vs indirect), one per line, for the
#         close-out to re-verify. Exit 0 always (a derivation, not a gate); empty
#         output = no code consumer found (state that in the card).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
TABLE="${1:?usage: check_data_consumers.sh <table_name>}"
RDIR="${CONSUMERS_ROUTERS_DIR:-api/app/routers}"
LDIR="${CONSUMERS_LIB_DIR:-api/app/lib}"

mapfile -t direct_routers < <(grep -rlF --include="*.py" "$TABLE" "$RDIR" 2>/dev/null | sort -u)

mapfile -t lib_hits < <(grep -rlF --include="*.py" "$TABLE" "$LDIR" 2>/dev/null | sort -u)
indirect_routers=()
for lib in "${lib_hits[@]}"; do
  base="$(basename "$lib" .py)"
  while IFS= read -r r; do indirect_routers+=("$r"); done < <(
    grep -rlE --include="*.py" "import .*\b${base}\b|from .*\b${base}\b" "$RDIR" 2>/dev/null | sort -u
  )
done

_emit() {
  local f="$1" c="$2"
  local prefix; prefix="$(grep -m1 -oE 'prefix="[^"]*"' "$f" | sed 's/prefix="//;s/"//')"
  printf '%-8s %s    route_prefix=%s\n' "$c" "$f" "${prefix:-?}"
}
declare -A seen
for f in "${direct_routers[@]}"; do
  [ -n "$f" ] && [ -z "${seen[$f]:-}" ] && { _emit "$f" DIRECT; seen[$f]=1; }
done
for f in "${indirect_routers[@]}"; do
  [ -n "$f" ] && [ -z "${seen[$f]:-}" ] && { _emit "$f" indirect; seen[$f]=1; }
done
