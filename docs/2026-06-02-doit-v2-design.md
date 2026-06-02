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

## Addition 1 — collect mode (persistent pile)

A shape of `think`. Dump phase: fire items, it appends to a `*.collecting.md` pile
in `COLLECT_INBOX`, light grouping only, no interrogation; the pile **persists
across sessions**. Close phase (`collect done`): organize, peel anything too big
into a brief, ask questions in one batch, emit one batched spec (per-cluster intent
+ acceptance), hand it over, archive the pile. Skips the brainstorm ceremony, not
the spec contract.

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

## Net surface change

- **0 net new skills** (collect + memo folded into `think`; `handover` stays a
  helper). Bootable seats: `planner`, `think`, `orc`.
- **+1 lane** (`collect-inbox`), **+1 in-repo relay file** (`docs/sessions/orc-relay.md`).
- **+ message types:** `collecting`, `review`, relay baton; memo lane now depends on
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
