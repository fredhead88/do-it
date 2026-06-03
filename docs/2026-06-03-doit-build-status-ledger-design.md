Build-Status Ledger — closing the handed-over→shipped seam

Status:  Shipped (v2.2.0)
Date:    2026-06-03

## The problem

DO-IT's lanes encode *which stage* a spec is in by where its file sits: brief-inbox
→ spec-inbox → `_archive/`. But file-location stops telling the truth the moment a
spec is picked up. Whether an archived spec was **built, deployed, or accepted** lived
only in the orchestrator's head and in a hand-maintained queue that drifts. Specs fell
through the seam between "handed over" and "shipped":

- a spec put **on hold** whose hold was later released — with neither transition ever
  surfacing to the human, who hit the un-built page weeks later;
- a spec **handed over that never entered the build queue at all**;
- a batch **merged but undeployed** behind an infrastructure blocker, silently reading
  as "done."

"Did we write any specs we never built?" required cross-reading three uncorrelated
places and trusting memory.

## The principle it had to respect

DO-IT's founding rule is *"state lives in where a file sits, not in any manifest — a
shared mutable manifest would just be a second thing to keep in sync."* A naive
status table violates that and drifts exactly as the hand-maintained queue did.

## The design

**One file per spec, not a manifest.** `LEDGER_DIR/<spec_id>.yml` carries a `status`
(registered → planned → building → merged → shipped → accepted, plus held / superseded)
and an append-only `history`. State is still "one field in the spec's own file" —
state-is-where-the-file-sits, applied per spec. No two specs share a row; no two
sessions write the same file.

**The view is generated, never hand-edited.** `scripts/spec_ledger.py` renders
`LEDGER_DIR/OUTSTANDING.md` from the per-spec files, so the rollup *cannot* drift from
the facts. `--check` validates invariants (held needs a reason; a `deploy_blocked_by`
must point at a live blocker). It answers the original question in one command.

**`merged` is never `shipped`.** A record only reaches `shipped` on a *verified* deploy.
Merged-undeployed renders as a distinct, loud "NOT LIVE" state, with a stale-merged
tripwire (`merged` with no blocker past 24h → "why isn't this live?").

**Deploy-blockers are shared objects.** `LEDGER_DIR/blockers/<id>.yml`; specs reference
them by id (`deploy_blocked_by`) instead of copying the text. One infra failure blocking
N specs is one object, cleared in one edit.

**Only the orchestrator writes the ledger.** `handover` and `think` do no git, so they
emit tiny inbox stubs (`*.register.yml` at handover, `*.accept.yml` on a happy review
walk) that the singleton `orc` folds into the committed ledger on pickup. Exactly one
writer per record → clobbering is impossible by construction, even with parallel
thinkers.

## Surfaces changed

- **DO-IT.md** — "The build-status ledger" subsection under The lanes; four new message
  types (register stub, accept stub, ledger record, blocker record); `LEDGER_DIR` in
  CONFIG.
- **handover** — writes the register stub at handover.
- **orc** — ingests stubs in first-moves + every turn; advances status at each loop
  point; deploy-blocker open/resolve; mandatory `LEDGER:` board line + close-out gate.
- **think** — writes the accept stub on a happy review walk.
- **scripts/spec_ledger.py** — portable render + `--check`; `DOIT_LEDGER_DIR` env
  override so it runs against any project's ledger.

Reference deployment: the Albert Scott repo, where a one-time backfill seeded 71
historical records and the live deploy-blocker, surfacing the two dropped specs above.
