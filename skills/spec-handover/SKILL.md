---
name: spec-handover
description: Hand a finished spec over to the orchestrator. Use when the user says 'spec handover', '/spec-handover', 'hand this spec over', 'ship the spec', 'send this to the orchestrator', 'get this spec across', or any time a spec doc written this session needs to reach the orchestrator session. One atomic, self-verifying action — places the numbered spec in the bus AND writes its ledger record, or errors loudly. NO git. If the spec isn't written yet, this is the wrong skill.
---

# Spec Handover — the atomic write

**Prerequisites:** the DO-IT pipeline — `DO-IT.md` (operating protocol),
the `think` and `orc` skills, and your repo (`REPO_ROOT` in CONFIG). Read DO-IT.md
**§2 (bus + naming)** and **§4 (handover)** — this skill *is* §4; the rules below
don't restate them, they execute them.

You're in a `think` session, the spec is written (under `docs/do-it/specs/`). This
skill gets it to the orchestrator. It is **one self-verifying action**: the spec
lands discoverably **and** its ledger record is born, or it fails loudly. No partial
state, no manual relay, no git (orc commits on its own branch).

## What "ready" means (refuse otherwise)

The spec header must have a non-empty `intent:` and ≥1 acceptance criterion. If
either is missing, do **not** hand over — say why and send the user back to finish.

## The action

1. **Allocate the number.** `NNN = max(live + _archive in spec-inbox) + 1`,
   zero-padded to 3. (Pre-2026-06-03 date-stem specs count as 0 — fresh numbering
   starts at `001`.)

2. **Place the spec atomically** into `~/.claude/spec-inbox/` as
   `NNN-<slug>-spec.md` — **hyphen before `spec`, never a dot** (the orc glob is
   `*-spec.md`). Write `…tmp` first, then rename; on a name collision retry `NNN+1`.

3. **Write the ledger record — one command, never hand-edited YAML.** This writes
   `~/.claude/ledger/NNN-<slug>.yml` born `registered`, atomically, and **refuses
   anything that wouldn't pass `--check`** — so a malformed or incomplete record can't
   be born (this is why we don't hand-write the YAML):

   ```bash
   python scripts/spec_ledger.py register NNN-<slug> \
     --title "<first content line of the spec>" \
     --intent "<the spec's intent: line, verbatim>" \
     --spec-file docs/do-it/specs/<spec-filename>.md  [--source-brief NNN]
   ```

4. **Confirm both landed, or fail loudly.** A `registered NNN-<slug>` line means the
   record landed; a non-zero exit + `error:` means it didn't. Also confirm the spec
   file is in place (`test -s ~/.claude/spec-inbox/NNN-<slug>-spec.md`). If either is
   missing, say which — never report a half-landed handover as done.

5. **Confirm to the user, one line:** "Handed over as `NNN-<slug>` — it's
   `registered` in the ledger; orc picks it up on its next boot/turn." No paste-block
   relay is needed: the ledger is live the instant this runs, and orc scans for new
   specs every turn (DO-IT.md §3). If the user *wants* to nudge orc now, they can —
   but a sitting spec can't hide: it renders as `registered` until orc advances it.

## Notes

- The bus (`~/.claude/...`) is outside any repo on purpose — reachable from any
  worktree. Writing here is **not** touching code, so it's allowed from a read-only
  `think` session.
- Orc commits the spec doc + regenerates the mirror on its side. This skill does no
  git. If asked "should I also push?" — no.
- If the orchestrator is on a *different machine*, the inbox approach doesn't reach
  it; fall back to a git push. Same machine: always the bus.
