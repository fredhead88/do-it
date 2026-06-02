# DO-IT v2 — think-shapes, the review loop, and the orchestrator relay

The record of the 2026-06-02 design pass. Rationale lives in `DESIGN.md`; the
runnable rules in `../DO-IT.md`. This doc captures *what changed and why*, including
the mid-session pivot, so a future session doesn't re-litigate it.

## The three problems this pass closed

1. **No casual capture for small stuff.** One-line bugs and nits had nowhere to
   land — `think` wanted a topic, `planner` wanted a dump.
2. **No accounting after the ship.** When `orc` ships 7–12 specs you lose the
   thread of what you designed and walk away hoping.
3. **No clean orchestrator-to-orchestrator handover.** `orc` saturates after a
   couple of specs; the operator was using a generic, three-month-old consolidation
   skill (`ginug`) that was never built to carry a live run.

## The shape we landed on

**Three bootable sessions, one per stage:**

- `planner` (stage 1, optional) — triage a dump into briefs.
- `think` (stage 2) — **the worker seat where a human sits.** Several at once.
- `orc` (stage 3, singleton) — build, grade, integrate, ship.

`think` is the one with internal variety. It has **shapes** (brainstorm / walk
review cards / claim a brief / collect small bugs) and **two outbound handoffs** it
performs itself: hand over a spec (via the `handover` *helper*) and send a memo
(advisory, to `orc` or `planner`).

### The pivot (important context for future me)

This pass first built `collect` and `memo` as their own skills. The operator
stopped it mid-build: *collect is a type of thinking; memo is something a thinker
does — neither is a separate seat to sit in.* So both were reversed and folded into
`think`. `handover` stays as an internal helper `think` invokes (kept separate only
because its header-validation logic is worth isolating; you never boot it). Net
bootable surface went from a planned six skills back to three. `drop` was renamed
along the way and then absorbed — the advisory action is now just "send a memo."

## Addition 1 — collect (a shape of `think`)

A shape of `think`, the inverse of brainstorm: low-touch across many small items,
with the thinking **deferred** to one synthesis pass. Capture phase: fire items, it
records each in a working list, light grouping + light background research only, no
interrogation. Synthesize phase (`collect done`): organize, peel anything too big
into a brief, resolve questions in one batch, emit one comprehensive spec
(per-cluster intent + acceptance), hand it over. Skips the brainstorm ceremony, not
the spec contract.

**Session-scoped (settled 2026-06-02, shipped 2.1.0).** Collect originally shipped
in 2.0.0 as a *persistent cross-session pile* (`*.collecting.md` in a `collect-inbox`
lane). That was over-built for one human on one machine: the only thing persistence
bought was surviving a mid-collect crash, and keeping the pile "honest" across
sessions dragged in a whole lifecycle (counters, an `_archive/`, and a per-item
"discharge" protocol drafted on the way here). All of it removed. Collect now runs
and finishes inside one session — the running list is an in-session working doc, no
lane, no message type. If the session dies mid-collect the jots are lost; that's the
accepted trade. (The discharge-map / `status: discharged` apparatus never reached a
release — it was superseded by this decision before shipping.)

## Addition 2 — the review loop

`orc` writes a tiny `NNN-<slug>.review.md` card into `BRIEF_INBOX` for **every**
shipped spec (what changed, where to look, ~4-6 things to eyeball, the blind
grader's verdict). A `think` session in review mode walks them: happy → archive;
unhappy → corrective spec back to `orc` (`supersedes:` the original). The loop
closes through a human opening a session — never a machine edge into a dead one.

## Addition 3 — the orchestrator relay

At its ~70% checkpoint `orc` writes a **baton** (`docs/sessions/orc-relay.md`,
`status: HANDED-OFF`) carrying the session-volatile state the plan file doesn't:
in-flight workers + their branches, git/worktree state, deploy state, blocks, next
action. A fresh `orc` reads it in first moves, **reconciles against the actual tree
(baton = hint, filesystem = truth)** — adopting finished workers, re-dispatching
dead ones — confirms the prior session is closed, and stamps it `RESUMED`. The
`HANDED-OFF`→`RESUMED` handshake is what enforces the singleton across the seam.
This replaces `ginug` for orchestrator handover.

## Hardening from the operator's correctness questions

The operator pushed on whether transfer is "automatic." It isn't — it's
**pull-on-boot by a human-launched session**. Two rules make that dependable:

- **Re-scan every turn.** `orc` (and `planner`) re-`ls` their lane at the top of
  each turn, so a spec/memo arriving mid-run is seen the same day, not next boot.
- **A read must be provable.** A memo is "read" only once the session states how it
  affected the plan (on the board / in the ledger). Memos are archived when their
  guidance is folded in (one-line reason), **decoupled from any spec** — so a memo
  can neither rot unread nor be archived before use.

## Spec hygiene — a spec ships with questions resolved (2.1.0)

The spec structure used to list an "Open questions" section. That's backwards: a
thinking session *is* the place questions get resolved, so a spec carrying built-in
open questions just pushes the thinker's unfinished work onto the orchestrator. The
rule now: a finished spec has **no open-questions section** — if a question is
genuinely open, the spec isn't ready (keep thinking), or it's a real fork the user
must pick (put it to them now, fold in the answer). This is about the *artifact*,
not the work — asking the user questions while thinking is the whole job. The
orchestrator may still raise *new* questions later from its broader, code-level view
of how the change collides with other moving parts; that's expected and lives on its
side of the seam, not pre-loaded into the spec. Removed the section from both the
spec structure and the readiness self-check in `think`.

## Net surface change

- **0 net new skills** (collect + memo folded into `think`; `handover` stays a
  helper). Bootable seats: `planner`, `think`, `orc`.
- **+1 in-repo relay file** (`docs/sessions/orc-relay.md`). (Collect adds no lane —
  it's session-scoped; the 2.0.0 `collect-inbox` was retired in 2.1.0.)
- **+ message types:** `review`, relay baton; memo lane now depends on
  reader (orc → spec-inbox, planner → brief-inbox).
- **+ `think` shapes** (review, collect) and **+ outbound handoffs** (spec, memo).
- **+ `orc`:** review-card emission, relay write/read+reconcile, per-turn re-scan,
  memo acknowledge-then-archive.

## Deliberately NOT added

- No event bus / watcher — pull-on-boot + per-turn re-scan fits one machine, one
  human.
- No lock server for the singleton — the `HANDED-OFF`→`RESUMED` handshake suffices.
- No review-card fast-skip — every spec gets a card; completeness is the point.
- No resumable sessions anywhere — piles, cards, and the baton are files; sessions
  stay one-shot.
