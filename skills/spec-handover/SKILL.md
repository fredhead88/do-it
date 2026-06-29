---
name: spec-handover
description: Hand a finished spec over to the orchestrator. Use when the user says 'spec handover', '/spec-handover', 'hand this spec over', 'ship the spec', 'send this to the orchestrator', 'get this spec across', or any time a spec doc written this session needs to reach the orchestrator session. One atomic, self-verifying action — places the numbered spec in the bus AND writes its ledger record, or errors loudly. NO git. If the spec isn't written yet, this is the wrong skill.
---

# Spec Handover — the atomic write

**Prerequisites:** the DO-IT pipeline — your project's `DO-IT.md` (operating protocol;
its §0 CONFIG names the Spec lane, Bus root, and Spec docs dir used below), the `think`
and `orc` skills. Read DO-IT.md **§2 (bus + naming)** and **§4 (handover)** — this
skill *is* §4; the rules below don't restate them, they execute them. Resolve every
"(CONFIG)" against §0.

You're in a `think` session, the spec is written (under the Spec docs dir, CONFIG).
This skill gets it to the orchestrator. It is **one self-verifying action**: the spec
lands discoverably **and** its ledger record is born, or it fails loudly. No partial
state, no manual relay, no git (orc commits on its own branch).

## What "ready" means (refuse otherwise)

The spec header must have a non-empty `intent:` and ≥1 acceptance criterion. If either
is missing, do **not** hand over — say why and send the user back to finish.

## The action

(`BUS` below = your Bus root from CONFIG, default `.do-it`; run from the repo root.)

1. **Allocate the number.** `NNN = max(live + _archive in the Spec lane) + 1`,
   zero-padded to 3. Start at `001` on an empty bus.

2. **Place the spec atomically** into the Spec lane (CONFIG, `$BUS/spec-inbox/`) as
   `NNN-<slug>-spec.md` — **hyphen before `spec`, never a dot** (the orc glob is
   `*-spec.md`). Write `…tmp` first, then rename; on a name collision retry `NNN+1`.

3. **Write the ledger record directly** — `$BUS/ledger/NNN-<slug>.yml`
   (tmp-then-rename), born `registered`:

   ```yaml
   spec_id:        NNN-<slug>
   title:          <first content line of the spec>
   intent:         <the spec's intent: line, verbatim>
   status:         registered
   handed_over_at: <ISO 8601, now>
   spec_file:      <Spec docs dir>/<spec-filename>.md
   source_brief:   NNN | null
   history:
     - at: <ISO 8601, now>
       status: registered
       by: handover
   ```

4. **Verify both landed, or fail loudly.** Confirm the spec file exists and is
   non-empty AND the ledger record exists and is non-empty:

   ```bash
   BUS=.do-it
   test -s $BUS/spec-inbox/NNN-<slug>-spec.md && \
   test -s $BUS/ledger/NNN-<slug>.yml && echo "HANDOVER OK: NNN-<slug>" || \
   echo "HANDOVER FAILED — partial state, do NOT report success"
   ```
   If it failed, tell the user exactly which of the two is missing — never report a
   half-landed handover as done.

5. **Confirm to the user, one line:** "Handed over as `NNN-<slug>` — it's `registered`
   in the ledger; orc picks it up on its next boot/turn." No paste-block relay is
   needed: the ledger is live the instant this runs, and orc scans for new specs every
   turn (DO-IT.md §3). If the user *wants* to nudge orc now, they can — but a sitting
   spec can't hide: it renders as `registered` until orc advances it.

## Notes

- The bus (`Bus root`, CONFIG) is gitignored on purpose — working state, not code.
  Writing here is **not** touching code, so it's allowed from a read-only `think`
  session.
- Orc commits the spec doc + regenerates the mirror on its side. This skill does no
  git. If asked "should I also push?" — no.
- If the orchestrator runs on a *different machine* (a bus inside a repo only reaches
  same-machine sessions on that checkout), fall back to a git push of the spec + a
  shared ledger location. Same machine / same checkout: always the bus.
