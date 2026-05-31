# DO-IT — Design Rationale

The reasoning behind the pipeline: what each piece is for, the decisions that
shaped it, and — just as important — what was deliberately *cut*. The runnable
rules live in [`../DO-IT.md`](../DO-IT.md); this doc is the *why*.

## The problem

A solo operator who drives Claude Code hard runs many workstreams at once. A
single session that both thinks and builds has two failure modes: the two
headspaces interfere (exploration vs. focused execution), and the operator sits
idle watching one thing happen when thinking has natural dead time. The fix is to
split the work into roles that run as separate sessions and pass messages through
a shared folder.

## Shape of the system

```
planner ─briefs─▶ brief-inbox ─▶ think ─spec─▶ spec-inbox ─▶ orc ─▶ build ▶ grade ▶ ship
(optional)                       (×N)          (a folder)    (one, owns git)
```

- **Sessions are one-shot and disposable.** Nothing is ever resumed; no message
  edge writes back into a dead session. Anything loop-like routes through the
  human. This is the load-bearing constraint everything else respects.
- **State is filesystem location.** A file's folder *is* its status — pending in a
  lane, or moved to `_archive/` when consumed. No database, no manifest to keep in
  sync.
- **Two lanes, by reader:** `spec-inbox` (the orchestrator's) and `brief-inbox`
  (the thinker's).

## The five roles

| Role | Session does | Output |
|------|--------------|--------|
| `planner` *(adv.)* | Triage a raw dump into topics | briefs + a triage receipt + a roadmap memo |
| `think` | Brainstorm/research one topic, read-only on code | a spec |
| `handover` | Validate + drop the spec | spec in `spec-inbox` |
| `drop` *(adv.)* | Leave an advisory note | a memo (never a work item) |
| `orc` | Plan, fan out, grade, integrate, ship | merged + deployed code |

Core is **three** (`think` + `handover` + `orc`); `planner` and `drop` are
optional add-ons you reach for only when running a lot of parallel work.

## The pillars, and the decisions behind them

### Parallelism is for *your* time, not the machine's

The orchestrator fans out as many workers as real dependencies allow, with no
fixed cap, so it keeps pace with the rate specs arrive. But the deeper reason to
run several `think` sessions at once is that **it keeps you busy** — while one
researches, you feed the next; you're never sitting watching a single chat. The
integration lane stays one-at-a-time (one orchestrator owns the tree); the *worker*
fan-out does not.

> Decision: the orchestrator parallelizes aggressively to stay ahead of the
> thinkers. The cap is dependencies, not a number.

### A lean orchestrator

The orchestrator's scarce resource is its own context. Every read, build, and
analysis is pushed to a sub-session that returns a tiny summary; the orchestrator
spends context only on coordinating, taking specs, and talking to you. A bloated
orchestrator is a failed one.

### `handover` is the only gate

When you finish thinking and invoke `handover`, *that* is your decision to commit
— "build this." The orchestrator picks specs up and works them; there is **no
second "may I build this?" prompt**. A second gate would just ask you to
re-confirm a thing you already confirmed.

> Decision: one gate, at handover. The old copy-paste relay between sessions is
> gone — the orchestrator reads the inbox directly.

### Intent, recorded as a real *why* — and a blind grader

Every spec carries an `intent:` header: one or two sentences saying what success
means and *why*, distinct from the acceptance checklist. Criteria are the test;
intent is the target. `handover` refuses to drop a spec without it.

The honest enforcement is at the **end**, not the start: the session that built
something is the worst judge of whether it's right, because it spent its whole
context trying to make it right. So before closing a spec, the orchestrator
dispatches **one fresh sub-session that never saw the build** and hands it only
the frozen intent, the acceptance criteria, and the diff. It returns a plain
verdict — *"matches intent: yes/no, because…"* — and also checks against the
project's standing invariants doc. It's honest because it's blind.

> Decision: the grader is an internal sub-session the orchestrator dispatches —
> not an inbox message and not a session you launch. Its independence comes from a
> clean context, not from a human hop.

### No silent stalls — tuned, not absolute

The nightmare is handing off work, walking away, and finding the orchestrator
asked a trivial question at minute two and idled all night. The guards: bias to
act, front-load questions, block only the one dependent task, make any real wait
loud and timestamped, and keep the ledger on disk as the morning-after truth.

But "always proceed on anything reversible" can backfire: a confidently-built
*wrong* feature still costs your review time to catch, even if `git revert` undoes
the code. So the bias is **tuned by a `DELICACY` setting**:

- `bold` — proceed on anything reversible, flagging each guess.
- `cautious` (default) — proceed on small reversible mechanics, but **stop and
  ask** when a spec is *valid but genuinely ambiguous about what you want*.

And the orchestrator **reports what it did** at the end of a run — "I guessed on
these N things, I waited on these M" — so you can re-tune. Your guessing is always
visible, never buried.

> Decision: delicacy is a knob plus an end-of-run report, not a fixed rule.

### Dead sessions don't silently lose work

Because state is filesystem location, a launched-but-unfinished session has no
trace — and a `think` session can die mid-thought (laptop sleeps, terminal
closes). If a claimed brief were archived at pickup, a dead thinker would leave a
brief that looks *completed*, and the work item would vanish.

So a brief is **claimed, not archived, at pickup**: renamed to
`*.brief.claimed.md` with a `claimed_at:` timestamp, and archived only when its
spec is actually delivered. On boot, both `orc` and the operator surface stale
claims — "brief 003 claimed 2h ago, no spec — dead session?" — so nothing
disappears silently.

> Decision: track the *session's* trace via the claimed-brief marker, not just the
> message. This closes the pipeline's worst failure mode.

### When a spec won't build: ask, don't retry

The thinker is gone (one-shot), so there's no dead session to bounce a spec back
to. A bounce is therefore just **a message to the human**: in-session, the
orchestrator says plainly why it won't build and you decide; if you're away, it
writes a `*.bounced.md` note and halts on it at next boot so it can't rot.

## What was deliberately cut

An earlier draft was heavier. Cutting it back was the point.

- **No concurrency machinery.** Earlier drafts had existence-checked renames,
  same-number race handling, and "loser retries N+1" logic. One person launches
  every session by hand — there is no real write race to defend against. Numbering
  is plain `max + 1`; writing is plain tmp-then-rename so no one reads a partial
  file. That's all.
- **No dead-letter state machine.** No transient-vs-poison classifier, no retry
  budget, no auto-retire-after-N, no `retired/` folder. Every bounce already
  routes to the human, so *you* are the retry policy. The dead-letter *file*
  stays (it survives you being away); the state machine is gone.
- **No second build gate.** `handover` is the commit; the orchestrator doesn't
  re-ask.
- **No manifest.** Filesystem location is the only state.

The through-line: this is a one-person, one-machine workflow, and the design is
sized for that — not dressed in the armor of a distributed system it isn't.

## What was kept because it's cheap and real

- **One shared `DO-IT.md`** so the skills point at common rules instead of
  restating them and drifting apart.
- **A standing invariants doc as the final arbiter** of "done" — a feature can
  satisfy a wrong spec perfectly, so the audit checks both the spec's intent and
  the project's invariants.
- **The async bounce *file*** — a real safety net for when you walk away.

## Portability

Project-specific values live in one **CONFIG block** at the top of `DO-IT.md`
(repo root, intent doc, deploy command, models, delicacy). `setup.sh` creates the
inboxes, links the skills, and refuses to claim "done" while CONFIG still holds
placeholders. Skills boot into a one-time setup interview if CONFIG doesn't
resolve, rather than failing on a bad path. Optional dependencies (a
`brainstorming` skill, a planning skill, a handoff skill) degrade gracefully when
absent.
