#!/usr/bin/env bash
# DO-IT setup: create the inbox lanes, install the skills, and check that
# the CONFIG block in DO-IT.md has been filled in. Safe to re-run (idempotent).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$ROOT/skills"
SKILLS_DST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
SPEC_INBOX="${SPEC_INBOX:-$HOME/.claude/spec-inbox}"
BRIEF_INBOX="${BRIEF_INBOX:-$HOME/.claude/brief-inbox}"
LEDGER_DIR="${DOIT_LEDGER_DIR:-$HOME/.claude/ledger}"

echo "DO-IT setup"
echo "  skills  -> $SKILLS_DST"
echo "  inboxes -> $SPEC_INBOX , $BRIEF_INBOX"
echo "  ledger  -> $LEDGER_DIR (bus; the repo holds a generated mirror)"

# 1. Inbox lanes (+ archives) and the bus ledger. Collect is session-scoped — no lane.
mkdir -p "$SPEC_INBOX/_archive" "$BRIEF_INBOX/_archive" "$LEDGER_DIR"

# 2. Install skills (symlink so edits in the repo take effect live)
mkdir -p "$SKILLS_DST"
# `planner`, `handover`, `drop`, `memo`, `collect` are no longer standalone skills:
# planner folded into `think` (intake/triage shape), `handover` is now `spec-handover`,
# collect/memo are think shapes/actions. Remove stale links from earlier installs.
for stale in planner handover drop memo collect; do
  if [ -L "$SKILLS_DST/$stale" ]; then rm -f "$SKILLS_DST/$stale"; echo "  removed stale link: $stale"; fi
done
for d in think spec-handover orc; do
  ln -sfn "$SKILLS_SRC/$d" "$SKILLS_DST/$d"
  echo "  linked skill: $d"
done

# 3. CONFIG sanity check — refuse to claim "done" if placeholders remain
PLACEHOLDERS=$(grep -nE '/path/to/your/repo' "$ROOT/DO-IT.md" || true)
if [ -n "$PLACEHOLDERS" ]; then
  echo
  echo "  ACTION NEEDED: edit the CONFIG block at the top of DO-IT.md."
  echo "  Still on placeholder values:"
  echo "$PLACEHOLDERS" | sed 's/^/    /'
  echo
  echo "  Set at least REPO_ROOT and INTENT_DOC before running orc."
  exit 1
fi

echo
echo "Done. Lanes created, skills linked, CONFIG looks filled in."
echo "Next: open a session and say 'think' (spec something) or 'orc' (build the inbox)."
