# Loop self-repair (v3.7) — the watcher role, the relay deadlock, and predictive gates

**Date:** 2026-06-09 · **Ships as:** v3.7.0 · **CHANGELOG:** the terse *what*; this is the *why*.

v3.7 is unusual: it came not from a design session but from a full night of *watching the
loop run live* on the reference deployment while real work shipped. A standing observer
tracked the orc↔rev↔think machine tick by tick and accumulated dated friction findings;
this release is the subset of those findings that either wedged a session or shipped a
defect. Each change below cites the incident behind it — that evidence bar is itself one of
the lessons (see the watcher).

## 1. The relay deadlock (the night's worst find)

The relay automation retires a context-saturated orchestrator and boots its successor. The
trigger was a token **sentinel** that a PostToolUse hook dropped only at the *hard* threshold
(default 400k). The cron then relays once the baton reads `HANDED-OFF` and the pane is quiet.

The hole: an agent that hands off *below* the hard threshold leaves **no sentinel**, so the
cron never even reads its baton. This is a **mutual-wait deadlock** — the agent hands off and
waits for the auto-restart; the restart waits for a hard-threshold signal that will never come
because the agent has stopped and its context won't grow. It is the exact silent-overnight
stall the whole design exists to prevent, re-emerging one level down at the relay layer — and
it is triggered by *good* behavior (handing off early, or pausing at a gate).

It fired on **both** standing twins at once: orc handed off at 371k and rev at 384k (rev's
baton literally said "hard relay 400k — HANDED-OFF to preserve budget"), and **neither
relayed** — both would have sat wedged until morning. A soft-line nudge added earlier the same
night made it worse, not better: it told a well-behaved agent to stand down in the 360k–400k
band, i.e. straight into the dead zone. The mitigation manufactured the outage.

**Fix:** arm the sentinel at a configurable **soft line** (`ORC_WATCH_SOFT`, default 0.9×
threshold), not the hard ceiling. Any handoff at or above the soft line now relays; the hard
line only escalates the nudge text. **Residual (honest):** a deliberate handoff *below* the
soft line still won't auto-relay. The fully robust fix is a trigger that fires on
"baton `HANDED-OFF` + pane quiet" independent of token level (drop a sentinel on any
`HANDED-OFF` write, or have the cron scan batons directly using the pane recorded in the
`*-active` file). That is the top follow-up item; it was deliberately deferred rather than
shipped untested at the end of a long run.

### F11/F12 (the same night, the same subsystem)
- **F11:** the cron gate matched `head -1 … status: HANDED-OFF`, but the `rev` baton carries an
  H1 title on line 1 and its status on line 3 — so a `rev` self-relay could *never* fire. The
  bug lay dormant because `rev` rarely saturates; one heavy review night exposed it. Fix: scan
  the baton head, format-agnostic.
- **F12 hardening:** freshness gate (refuse a baton older than `BATON_FRESH_SECS`, default 90m),
  atomic-completeness (status + handed-off-at both present), a consume-once marker keyed to baton
  mtime (no double-`/clear` of a freshly booted pane), and a newest-sentinel-per-pane identity
  guard (the manual-reboot case).

## 2. The `watcher` role — completing the loop

think decides *what* to build; orc *builds*; rev checks the build met spec (product
correctness). Missing: **is the process that produced it getting better or worse?** That's the
watcher — process correctness. It is the only role that looks *across* runs rather than within
one, so it is the only one that can see recurrence and drift. (Proven the same night: orphan-nav
recurred four times; a relay bug re-derived every tick; a deadlock visible only by comparing two
sessions.)

It is not a one-time cleanup job because the substrate keeps moving — model capability, harness
features (background tasks, wake-ups, auto-compaction are all recent), the shapes of the work.
A static operating manual depreciates; a standing observer keeps it adapted.

The guardrails are correctness, not caution — without them a process-reviewer is worse than
nothing (rule churn has real cost; every role re-reads the rules):
- **Bias to leave-it-alone.** Most sweeps need no proposal. "Loop healthy, no proposal" is the
  expected steady state.
- **Evidence-bound** is its falsifiability substitute (it has no prod to grade against): every
  proposal cites named, dated incidents. No inferring an unobserved cause.
- **Read-only on the process.** Proposes; a human + a thinker ratify; it never edits the rules,
  code, git, or the bus, and never registers a spec (076).
- **Hard quota** (one proposal per session, three open max) so it cannot churn the rules.

## 3. Predictive gates and the contract-binding

- **Fatal-mistakes registry** (consumed by the `watcher`, fed into boots): recurrence is only
  visible longitudinally, so the registry consolidates per-class incidents into a ranked,
  *predictive* layer — "surface X had N type-Y regressions, guard for it" — turning a
  post-mortem ledger into a seatbelt. The reference deployment exercised this organically the
  same night (an orphan-nav recurrence and an alembic-multiple-heads hazard were caught
  *before* the bug by a boot that had read the registry).
- **Close-out gates** (`scripts/close-out-gates/`, reference implementations): orphan-nav
  reachability (a page built but unreachable — four incidents), cross-spec data-dependency
  derivation (a data spec ships while a downstream surface goes stale yet reads `shipped`), and
  a deploy manifest (one written record of prod ground-truth so the reviewer reads it instead
  of re-probing host/sha every tick). Project-shaped by nature; all paths env-overridable.
- **F5 contract binding:** verdicts that assert a hardcoded `$` figure go stale *by design* when
  the underlying fee/agreement contract changes — that is not a regression. `verify
  --contract-version` records the contract a verdict held under; a contract bump flips it to
  **needs-revalidation** (re-verify under the new contract), never a false `regression`. Born
  from two verdicts that nearly cried regression when a contract changed under them.

## 4. The 076 guard

The "reviewer never authors" rule (rev/watcher must not allocate spec numbers or write build
status) was previously convention. A non-builder writing the ledger once shipped an outage, so
it is now a tool-level guard: `spec_ledger.py` refuses `next-num`/`register`/`set` for
`ROLE` in `{rev, watcher}`. A reviewer's only write remains `verify`, into the verifier
namespace it owns.
