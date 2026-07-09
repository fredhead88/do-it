#!/usr/bin/env bash
# check_data_consumers.sh — F9 cross-spec data-dependency derivation (ORC-RESET STEP 7).
#
# When a spec declares `populates: <table>`, its close-out must re-verify the SURFACES
# that CONSUME that table — not just its own criteria. 126 shipped data; 125's consuming
# panel went stale yet read `shipped`. This derives the consumer set from the code (no
# hand-maintained map, per the audit): every router/lib that references the table, and —
# for a lib hit — the routers that import that lib module (one hop).
#
# Usage:  check_data_consumers.sh <table_or_view_name>
# Output: the consuming router files (→ their routes), one per line, for the close-out
#         to re-verify. Exit 0 always (it's a derivation, not a gate); empty output =
#         no code consumer found (state that in the card).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
TABLE="${1:?usage: check_data_consumers.sh <table_name>}"
RDIR="api/app/routers"
LDIR="api/app/lib"

# direct references in routers
mapfile -t direct_routers < <(grep -rlF --include="*.py" "$TABLE" "$RDIR" 2>/dev/null | sort -u)

# references in lib → find routers importing that lib module (one hop)
mapfile -t lib_hits < <(grep -rlF --include="*.py" "$TABLE" "$LDIR" 2>/dev/null | sort -u)
indirect_routers=()
for lib in "${lib_hits[@]}"; do
  # api/app/lib/foo/bar.py -> module token "bar" and "foo.bar" for import matching
  base="$(basename "$lib" .py)"
  rel="${lib#api/app/}"; mod="${rel%.py}"; mod="${mod//\//.}"   # app.lib.foo.bar
  short="${mod#app.}"                                           # lib.foo.bar
  while IFS= read -r r; do indirect_routers+=("$r"); done < <(
    grep -rlE --include="*.py" "import .*\b${base}\b|from .*${short}\b|from .*\b${base}\b" "$RDIR" 2>/dev/null | sort -u
  )
done

# Direct router refs are high-confidence consumers — re-verify ALL of these.
# Indirect (via a shared lib) can be noisy when the lib is broadly imported, so
# label them; the close-out re-verifies direct always, indirect by judgment.
_emit() { # $1=file $2=confidence
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
