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
for d in think spec-handover orc builder rev watcher verification-loop; do
  ln -sfn "$SKILLS_SRC/$d" "$SKILLS_DST/$d"
  echo "  linked skill: $d"
done

# 3. Verification-loop harness: remind user to configure if not done yet
VL_CONFIG="$ROOT/verification-loop/config"
if [ ! -f "$VL_CONFIG/example.json" ] || ls "$VL_CONFIG"/*.json 2>/dev/null | grep -vq 'example.json'; then
  :  # at least one project config exists — nothing to do
else
  echo
  echo "  NOTE: verification-loop/config/ has only example.json."
  echo "  Copy it to config/<your-project>.json and fill in your values."
  echo "  Then: cd verification-loop && npm install"
fi

# 3b. Executable bits for scripts + CI validators
chmod +x "$ROOT"/scripts/*.sh "$ROOT"/scripts/*.py "$ROOT"/scripts/ci/*.sh "$ROOT"/scripts/ci/*.py \
         "$ROOT"/scripts/lib/*.sh "$ROOT"/relay-watch/*.sh "$ROOT"/scripts/close-out-gates/*.sh 2>/dev/null || true
echo "  made scripts/ + relay-watch/ executable (incl. ci/ validators)"

# 3c. Standing-role automation: relay + nudge + gating-watch + heartbeat (optional cron)
if ! crontab -l 2>/dev/null | grep -q relay-watch.sh; then
  echo
  echo "  OPTIONAL: standing-role automation (relay/nudge/gating-watch/heartbeat) is not installed."
  echo "  Relay hook: relay-watch/SETUP.md (one settings.json hook + cron line)."
  echo "  Cron block: scripts/CRON-SETUP.md (nudge, detached grader, liveness — REPO_ROOT/PYTHON overridable)."
fi

# 4. CONFIG sanity check — refuse to claim "done" if placeholders remain
PLACEHOLDERS=$(grep -nE '<absolute path to your repo|<docs/do-it/|<the exact deploy' "$ROOT/DO-IT.md" || true)
if [ -n "$PLACEHOLDERS" ]; then
  echo
  echo "  ACTION NEEDED: fill in the CONFIG table at the top of DO-IT.md."
  echo "  Still on placeholder values:"
  echo "$PLACEHOLDERS" | sed 's/^/    /'
  echo
  echo "  Set at least Repo root, Ledger mirror, and Deploy recipe before running orc."
  exit 1
fi

echo
echo "Done. Lanes created, skills linked, CONFIG looks filled in."
echo "Next: open a session and say 'think' (spec something) or 'orc' (build the inbox)."
