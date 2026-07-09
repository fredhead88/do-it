---
name: spec-handover
description: Hand a finished spec over to the orchestrator. Use when the user says 'spec handover', '/spec-handover', 'hand this spec over', 'ship the spec', 'send this to the orchestrator', 'get this spec across', or any time a spec doc written this session needs to reach the orchestrator session. One atomic, self-verifying action — places the numbered spec in the bus AND writes its ledger record, or errors loudly. NO git. If the spec isn't written yet, this is the wrong skill.
---

# Spec Handover — the atomic write

**Prerequisites:** the DO-IT pipeline — `DO-IT.md` (operating protocol),
the `think` and `orc` (the **integrator**; `/orc` alias) skills, and your repo
(`REPO_ROOT` in CONFIG). Read DO-IT.md **§2 (bus + naming)** and **§4 (handover)** —
this skill *is* §4; the rules below don't restate them, they execute them.

You're in a `think` session, the spec is written (in `~/.claude/spec-staging/`). This
skill gets it to the **integrator** (the revised orc — the singleton pickup role;
`/orc` is preserved as its alias). It is **one self-verifying action**: the spec lands
discoverably **and** its ledger record is born, or it fails loudly. No partial state, no
manual relay, no git (the integrator commits on master when it assigns the spec).

**The spec lives in `~/.claude/spec-staging/` — never in `docs/do-it/specs/` or anywhere
under `<repo root>`. If you find the spec elsewhere, stop: the thinker violated
bus-first authoring. Do not hand over a spec sourced from the repo checkout.**

## What "ready" means (refuse otherwise)

The spec header must have a non-empty `intent:` and ≥1 acceptance criterion. If
either is missing, do **not** hand over — say why and send the user back to finish.

**Step 0 — the criterion↔evidence gate (spec 205, ARMED 2026-06-25). Run this
BEFORE allocating a number or placing the file.** From `REPO_ROOT`:

```bash
python scripts/ci/handover_validate.py ~/.claude/spec-staging/<spec-filename>.md
```

- **exit 0** → criteria pass; proceed to allocation.
- **exit 1** → hard FAIL (it names the offending acceptance criterion: a UI
  criterion proved only by grep, an observed-data criterion on sqlite, a cron
  criterion with no post-fire assertion, or a financial criterion with no
  cent-tolerance). **ABORT the handover** — send the user back to fix that
  criterion's evidence type. Do NOT hand over a spec the gate rejected.
- **exit 2** → WARN (e.g. observed-data criterion in a PG-less env where
  `SUPABASE_DB_URL` is unset). Surface the warning to the user and proceed.

This is the enforcement layer spec 205 delivers; without this step the validator
is inert. (Corrective-205-handover-validator-not-armed.)

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
     --spec-file ~/.claude/spec-staging/<spec-filename>.md  [--source-brief B<NNN>]) \
     || { echo "allocation refused — read the error, fix it, retry"; exit 1; }
   ```

   If it exits non-zero it printed the reason on stderr (poisoned max, missing
   field, slug collision) — STOP and fix that; do not invent a number.

2. **Move the spec file** from `~/.claude/spec-staging/<slug>-spec.md` into
   `~/.claude/spec-inbox/` as `${NNN}-<slug>-spec.md` — **hyphen before `spec`,
   never a dot** (the orc glob is `*-spec.md`). Copy to `…tmp`, rename into place,
   then remove the staging file. The number is already claimed by the ledger record
   from step 1, so there is no collision to retry here — the file just gets named
   after the number you were handed. After this step, no copy of the spec exists
   under `docs/` or `<repo root>` — the only copy is in `spec-inbox/`.

3. **Confirm both landed, or fail loudly.** Step 1 exiting 0 with a number means
   the record is born; re-confirm with `test -s ~/.claude/ledger/${NNN}-<slug>.yml`.
   Also confirm the spec file is in place
   (`test -s ~/.claude/spec-inbox/${NNN}-<slug>-spec.md`). If the record is present
   but the file isn't, the handover half-landed — place the file (the number is
   already yours); never report a half-landed handover as done.

4. **Confirm to the user, one line:** "Handed over as `NNN-<slug>` — it's
   `registered` in the ledger; the integrator (`/orc`) picks it up on its next boot/turn."
   No paste-block relay is needed: the ledger is live the instant this runs, and the
   integrator scans for new specs every turn (DO-IT.md §3). If the user *wants* to nudge
   the integrator now, they can — but a sitting spec can't hide: it renders as
   `registered` until the integrator advances it.

## Notes

- The bus (`~/.claude/...`) is outside any repo on purpose — reachable from any
  worktree. Writing here is **not** touching code, so it's allowed from a read-only
  `think` session.
- Orc commits the spec doc + regenerates the mirror on its side. This skill does no
  git. If asked "should I also push?" — no.
- If the orchestrator is on a *different machine*, the inbox approach doesn't reach
  it; fall back to a git push. Same machine: always the bus.
- **Isolation check:** if `git status` for `<repo root>` shows an untracked
  `docs/do-it/specs/*-spec.md` after handover, a violation occurred — the guard
  (`scripts/ci/check_thinker_isolation.sh`) names the offending file. Report it to
  the user; the repo-owner adjudicates. Do not auto-delete.
