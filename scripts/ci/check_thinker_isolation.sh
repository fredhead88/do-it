#!/usr/bin/env bash
# check_thinker_isolation.sh — Mechanical guard for bus-first thinker authoring (spec 253).
#
# Surfaces any thinker-authored doc that has landed in the shared checkout instead of
# the bus. Reports loudly and exits non-zero on violation; exits 0 on a clean tree.
# Does NOT auto-delete — the repo-owner adjudicates.
#
# Usage:
#   scripts/ci/check_thinker_isolation.sh [<repo-root>]
#
# Wired into:
#   - spec-handover pre-flight (run from REPO_ROOT before allocating a spec number)
#   - doit-nudge.sh cron (ROLE=orc tick)
#   - CI (optional)
#
# Exit codes:
#   0  clean — no thinker-authored docs found in the repo checkout
#   1  violation — one or more files named below; the repo-owner must adjudicates

set -euo pipefail

REPO_ROOT="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# Patterns that should ONLY arrive via the bus (authored by orc on its branch, not
# written directly by a thinker). We check for untracked files matching these globs
# under the repo root, which is the signature of a thinker that bypassed the bus.
DOC_PATTERNS=(
  "docs/do-it/specs/*-spec.md"
  "docs/do-it/plans/*.md"
  "docs/business/**/*.md"
  "docs/architecture/**/*.md"
)

violations=()

for pattern in "${DOC_PATTERNS[@]}"; do
  # Use git ls-files --others (untracked) to find files matching the pattern.
  # --exclude-standard respects .gitignore. We want files that exist on disk but
  # are NOT yet committed — the hallmark of a thinker drop on the wrong branch.
  while IFS= read -r f; do
    [[ -n "$f" ]] && violations+=("$f")
  done < <(
    cd "$REPO_ROOT" && git ls-files --others --exclude-standard -- "$pattern" 2>/dev/null
  )
done

if [[ ${#violations[@]} -eq 0 ]]; then
  echo "check_thinker_isolation: OK — no thinker-authored docs found in checkout"
  exit 0
fi

echo "check_thinker_isolation: VIOLATION — thinker-authored doc(s) landed in the shared checkout."
echo "These files should live in ~/.claude/spec-staging/ or ~/.claude/think-staging/ until"
echo "the repo-owner lands them on the correct branch. The repo-owner must adjudicate:"
echo ""
for f in "${violations[@]}"; do
  echo "  STRAY: $f"
done
echo ""
echo "To resolve: either move the file to the appropriate bus lane and re-run handover,"
echo "or (if the repo-owner confirms it belongs here) stage and commit it on the correct branch."
exit 1
