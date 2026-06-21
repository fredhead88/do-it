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

1. **Allocate the number AND birth the record in one atomic command — never
   hand-roll a grep, never compute `max+1` yourself.** `next-num` is the single
   source of truth: under one machine-global lock it scans every bus dir with the
   correct pattern (3 digits *followed by a hyphen* — so the year in a
   grandfathered `2026-...` date-stem file can't read as 202), computes the next
   number, **and births the `registered` ledger record before returning** — so a
   concurrent `think`/handover session blocks until the reservation is on disk and
   sees the next number, never the same one (this is what killed the 110
   double-book). It refuses anything that wouldn't pass `--check`, and refuses an
   absurd JUMP (the top number sitting far above the second-highest — the signature
   of a poisoning file, not a fixed ceiling) telling you to hunt the offender first.

   ```bash
   # Prints ONLY the zero-padded number, e.g. 109. The ledger record is now born;
   # do NOT also call `register` — next-num already did. Capture the number:
   NNN=$(python scripts/spec_ledger.py next-num --kind spec --slug <slug> \
     --title "<first content line of the spec>" \
     --intent "<the spec's intent: line, verbatim>" \
     --spec-file docs/do-it/specs/<spec-filename>.md  [--source-brief NNN]) \
     || { echo "allocation refused — read the error, fix it, retry"; exit 1; }
   ```

   If it exits non-zero it printed the reason on stderr (poisoned max, missing
   field, slug collision) — STOP and fix that; do not invent a number.

2. **Place the spec file** into `~/.claude/spec-inbox/` as `${NNN}-<slug>-spec.md`
   — **hyphen before `spec`, never a dot** (the orc glob is `*-spec.md`). Write
   `…tmp` first, then rename. The number is already claimed by the ledger record
   from step 1, so there is no collision to retry here — the file just gets named
   after the number you were handed.

3. **Confirm both landed, or fail loudly.** Step 1 exiting 0 with a number means
   the record is born; re-confirm with `test -s ~/.claude/ledger/${NNN}-<slug>.yml`.
   Also confirm the spec file is in place
   (`test -s ~/.claude/spec-inbox/${NNN}-<slug>-spec.md`). If the record is present
   but the file isn't, the handover half-landed — place the file (the number is
   already yours); never report a half-landed handover as done.

4. **Confirm to the user, one line:** "Handed over as `NNN-<slug>` — it's
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
