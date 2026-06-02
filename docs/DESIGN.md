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
- **Two lanes, by reader:** `spec-inbox` (the orchestrator's — specs, orc-directed
  memos, bounces) and `brief-inbox` (the thinker's — briefs, review cards, and
  planner-directed memos). Plus an in-repo relay file (`docs/sessions/orc-relay.md`)
  for orchestrator-to-orchestrator handover — a single file, not an inbox. (`think`'s
  collect shape is session-scoped and has no lane.)

## Three sessions, not six skills

There are exactly **three bootable sessions**, one per stage:

| Stage | Session | Does | Output |
|------|---------|------|--------|
| 1 *(optional)* | `planner` | Triage a raw dump into topics | briefs + a triage receipt + a roadmap memo |
| 2 | `think` | The worker seat — sit and turn intent into something buildable | a spec (or a closed/corrected review) |
| 3 | `orc` | Plan, fan out, grade, integrate, ship; write review cards | merged + deployed code + review cards |

`think` is the one with internal variety. It has **shapes** — brainstorm, walk
review cards, claim a brief, collect small bugs — and **two outbound handoffs** it
performs itself: hand over a spec (via the `handover` *helper* — a routine it
invokes, not a seat you boot) and send a memo (advisory, to orc or planner).

An earlier draft made `collect` and `memo` their own skills. That was a mistake and
was reversed: collecting bugs and leaving a memo are things a *thinker does*, not
separate seats a human sits in. Folding them back into `think` is why the bootable
surface is three, not six. (The advisory-note action was once a skill called
`drop`; it's now just "send a memo" inside `think`.)

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

### Casual capture, without a meeting (collect mode)

Brainstorm and triage both assume work arrives as something worth sitting down
with — a topic, or a dump. Neither fits the steady drip of one-line bugs and nits
you notice mid-day. Those had nowhere to land, so they interrupted a session or got
lost. **Collect mode** is the missing low-friction spot — and it's a *shape of
`think`*, not its own skill, because gathering bugs is something a thinker does.

Its design is two phases on purpose. The **capture phase** stays out of your way —
it records each item and does only seconds of background grouping/research, never an
interrogation, because the moment capture costs effort you stop capturing. The
**synthesize phase** (`collect done`) is where the thinking finally happens, once,
over everything captured: organize, resolve every question in one batch, emit a
single comprehensive spec, hand it over.

It skips the *brainstorm ceremony* deliberately — routing a one-line fix through a
full design session is the friction collect exists to remove — but not the spec
contract: the batch spec carries a real per-cluster `intent:` + acceptance
criteria like any other, and anything that turns out to need real design is peeled
off as a *brief* instead of jammed in.

> Decision: collect is a mode of `think`, not a separate skill (an earlier draft
> that made it its own skill was reversed). It is **session-scoped** — capture and
> synthesize in one session, nothing persists. The 2.0.0 cross-session *persistent
> pile* was retired in 2.1.0: persistence only bought mid-crash survival and cost a
> whole file lifecycle to keep honest — not worth it for one human on one machine.

### Reading is pull-on-boot, and a read must be provable

There is no daemon and no message bus here. A spec or memo is picked up because a
human launches the session that reads that lane and its first moves say to `ls` and
read. So the honest guarantee is "picked up on the next boot of that session, and
mid-run if it re-scans" — not "instantly." Two rules keep that from being wishful:
the reading session **re-scans its lane every turn** (so a spec handed over while
orc is running is seen that afternoon, not next boot), and **a read is only real
once acknowledged** — the session must state how a memo affected its plan, on the
board or in the ledger. An item read with no visible effect is the silent-ignore
failure mode the acknowledgement closes.

Memos used to be archived "when the topic's spec closes" — fragile, since a memo
may map to no spec or to several. Now a memo is archived when its guidance has been
*folded in* (with a one-line reason), decoupled from any spec, so it can neither rot
unread nor vanish before it was used.

> Decision: lean on pull-on-boot + per-turn re-scan + an explicit read receipt,
> rather than building an event system the one-machine, one-human reality doesn't
> need.

### Passing a live orchestrator to the next one (the relay)

An orchestrator saturates its context after a couple of specs, and the singleton
rule means you can't add a second to share the load — you replace it with a fresh
one. The reflex is to reach for a generic "summarize the session" skill, but that
isn't built to carry a *live run*: in-flight workers, half-integrated branches, a
pending deploy.

The relay baton fits DO-IT's own principle — state is the filesystem. The plan file
already holds the ledger; the baton (`docs/sessions/orc-relay.md`) adds only the
session-volatile bits the plan file can't: which workers were mid-flight and on what
branches (those die with the session), git/worktree state, deploy state, blocks, and
the one next action. The incoming orchestrator treats the baton as a **hint and the
tree as truth** — it verifies each claimed in-flight worker against its branch and
adopts-or-re-dispatches — which is exactly how the pipeline already handles dead
background sub-sessions everywhere else.

> Decision: a purpose-built baton + a `HANDED-OFF`→`RESUMED` handshake, not a
> generic handoff doc and not a lock server. The handshake is the only thing
> enforcing the singleton across the seam, and it's enough because one human
> launches every session.

### Closing the loop at the far end (review cards + `think` review mode)

The blind grader checks whether a build matches its spec. But a *human* still
needs to see whether the shipped thing matches what they pictured — and when
`orc` ships 7–12 specs in a run, you lose the thread of what you designed and
walk away hoping. So every shipped spec gets a short **review card**: what
changed, where to look, a handful of things to eyeball, the grader's verdict.
Every spec, no fast-skip — a missing card would be exactly the ship you forgot
to check.

The card closes the loop through the role that already exists. Opening a `think`
session in review mode, you walk the cards; a happy one is archived, an unhappy
one becomes a *corrective spec* back to `orc`. Reusing `think` (read-only, ends
in a spec) is what keeps this from being a new role — and routing the re-think
through a human-launched session is the same one-shot discipline as everywhere
else: no edge ever writes back into a dead session.

> Decision: review cards ride in `brief-inbox` (the thinker reads them), and the
> loop is closed by `think`, not a new skill. The only new surface is the card
> itself and one `think` shape.

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
