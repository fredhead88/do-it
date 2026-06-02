# DO-IT — Shared Protocol

The single source of truth for the DO-IT pipeline. Every skill points here
instead of restating the rules, so they can't drift apart. Read this once; the
skills assume you know it.

DO-IT moves work from raw idea → organized topic → spec → built-and-shipped
feature, across **separate, one-shot Claude Code sessions** on one machine that
pass typed messages through a shared filesystem inbox.

Three bootable sessions: **planner** (stage 1, optional), **think** (stage 2, the
worker seat — several at once, in different shapes), **orc** (stage 3, singleton).

```
                        ┌─ shapes of THINK (stage 2, the worker seat) ─┐
PLANNER ─briefs─▶ brief-inbox ─▶ │ brainstorm · review · collect-pile │ ─spec─▶ spec-inbox ─▶ ORC
 (stage 1)        ▲              └───────────────────────────────────┘                       (stage 3)
                  │                                                                     plan→fan out→grade
   review card ◀──┘── per shipped spec ◀──────────────────────────────────────────────── →integrate→ship
   (think review: happy → archive · unhappy → corrective spec ─▶ spec-inbox)                     │
                                                                                    orc-relay baton ─▶ next ORC
   memo ─advisory─▶ orc or planner (context, never a work item)              (one orchestrator at a time)
```

---

## CONFIG — fill this in once per project

The only place project-specific values live. `setup.sh` checks these are no
longer placeholders. Edit the block, don't scatter paths through the skills.

```yaml
REPO_ROOT:      /path/to/your/repo          # the working tree the orchestrator owns
INTENT_DOC:     docs/INTENT.md              # standing invariants; final arbiter of "done"
ARCH_DOCS:      docs/architecture/          # optional: living architecture map
DEPLOY_CMD:     ./deploy.sh                 # how the orchestrator ships; "" if none
SPEC_INBOX:     ~/.claude/spec-inbox        # orchestrator's lane (keep default)
BRIEF_INBOX:    ~/.claude/brief-inbox       # thinker's lane — briefs + review cards (keep default)
RELAY_BATON:    docs/sessions/orc-relay.md  # in-repo; how one orchestrator hands to the next
ORC_MODEL:      opus                        # orchestrator session model
WORKER_MODEL:   sonnet                      # default sub-session model (floor)
DELICACY:       cautious                    # cautious | bold — see "Bias to act"
```

If `REPO_ROOT` or `INTENT_DOC` don't resolve, a booting skill drops into a
one-time setup interview to write this block rather than failing on a bad path.

---

## The lanes

State lives in **where a file sits**, not in any manifest (a shared mutable
manifest would just be a second thing to keep in sync). Two inbox folders, split
by who reads them, plus an in-repo relay file:

- **`SPEC_INBOX`** — the orchestrator's lane. Holds `*.spec.md` (actionable),
  `memo-*.md` (advisory context for orc), `*.bounced.md` (needs the human).
- **`BRIEF_INBOX`** — the thinker's lane. Holds `*.brief.md`, `*.review.md` (a card
  the orchestrator wrote about a shipped spec, for a thinker to walk), `memo-*.md`
  (advisory context for the planner), and `triage-receipt.md`.
- **`RELAY_BATON`** (`docs/sessions/orc-relay.md`, in-repo) — how a saturated
  orchestrator hands its live run to a fresh orchestrator session. Not an inbox; a
  single file, overwritten each handover, committed with the work.

Each lane has an `_archive/` subfolder for consumed items. `ls *.md` in a lane =
what's still pending. Nothing is ever `rm`'d — finished items move to `_archive/`.

### How items actually get picked up (it's pull, not push)

There is no daemon and no message bus. Transfer happens because a **human launches
a session** and that session's **first moves tell it to `ls` its lane and read
what's pending.** So the honest guarantee is: a spec or memo is picked up *on the
next boot of the session that reads that lane*, and mid-run *if that session
re-scans* — not the instant it's written. Two rules make this dependable rather
than hopeful:

- **Re-scan every turn, not just at boot.** The reading session (`orc`, `planner`)
  does a cheap `ls` of its lane at the top of each turn and surfaces new arrivals
  on its status board. This is what lets a spec handed over *while orc is running*
  get noticed the same afternoon instead of next boot.
- **Reading is acknowledged, not silent.** A memo isn't "read" until the session
  says, in its board/ledger, *how* it affected the plan ("memo-roadmap: noted —
  sequencing X before Y"). An item read with no visible effect is the failure mode;
  the acknowledgement is the receipt.

---

## Message types

| Type | File | Written by | Read by | Numbered | Mutable |
|------|------|-----------|---------|----------|---------|
| Brief | `NNN-<slug>.brief.md` | planner | think | yes (brief counter) | draft only |
| Claimed brief | `NNN-<slug>.brief.claimed.md` | think | human / orc | mirrors brief | adds `claimed_at:` |
| Spec | `NNN-<slug>.spec.md` | think (via `handover` helper) | orc | yes (spec counter) | no |
| Review card | `NNN-<slug>.review.md` | orc | think (review mode) | mirrors spec | no |
| Memo | `memo-<topic>.md` | think / planner | orc (in `SPEC_INBOX`) / planner (in `BRIEF_INBOX`) | **no** | **yes** (`last_updated:`) |
| Relay baton | `docs/sessions/orc-relay.md` | orc | next orc | no (single file) | overwritten per handover |
| Bounce | `NNN-<slug>.bounced.md` | orc | human | mirrors spec | no |

The **review card**
mirrors the number of the spec it reviews (no separate counter) so the two are
obviously paired. (`think`'s **collect** shape keeps its running list in-session
only — it's not an inbox message and never persists across sessions; see the `think`
skill.) A **memo's** lane depends on its reader — orc-directed memos go
in `SPEC_INBOX`, planner-directed memos in `BRIEF_INBOX`. The **relay baton** is
not an inbox message — it's a single in-repo file one orchestrator leaves for the
next (see "Handing over to the next orchestrator" in the `orc` skill).

**Numbering is simple.** One person launches every session by hand, so there is
no real write race to defend against. To allocate a number: `NNN = max(live +
_archive in this lane) + 1`, zero-padded to 3. Each lane counts independently.
Memos are never numbered — they're standing context, not work units. If you ever
do see two files with the same number (you won't, in practice), the human picks;
no machinery needed.

**Writing a file** is plain: write `<name>.tmp` in the target folder, then
`mv` it to its real name (so no reader ever sees a half-written file). That's the
only atomicity that matters here.

---

## Lifecycle rules

- **Brief → claimed at pickup, not archived.** When a thinker picks up a brief it
  renames it to `NNN-<slug>.brief.claimed.md` and adds a `claimed_at:` timestamp.
  It is archived only when the resulting spec is delivered. **Why:** a one-shot
  thinker can die mid-thought (laptop sleeps, terminal closes). A claimed-but-not-
  delivered brief left visible with a timestamp is the only way anyone can later
  notice "brief 003 was claimed two hours ago and never produced a spec — that
  session died." Archiving at pickup would erase that trace and silently lose the
  work item. The orchestrator and the human both surface stale claims on boot.
- **Spec → archived at delivery-close.** When the orchestrator finishes a spec it
  `mv`s the spec to `SPEC_INBOX/_archive/`. That frozen copy is the
  "as-handed-over" snapshot the close-out grader audits against.
- **Memo → acknowledged on read, archived when folded in.** A memo is *not* tied to
  a spec (it may map to none, or to several). The reading session (orc, or the
  planner) does two things: on first read it **acknowledges** the memo — states in
  its board/ledger how the guidance affects the plan, so a read is provable, not
  silent — and once it has actually folded that guidance into a decision or plan
  (or judged it no longer applies) it `mv`s the memo to `_archive/` with a one-line
  reason. This decouples a memo's life from any spec, so it can neither rot
  unread nor get archived before it was used.
- **Collect → no lifecycle (it's session-scoped).** Collect has no inbox file and
  no archive: the running list lives in the `think` session and is consumed into one
  spec before the session ends (see the `think` skill). The only persisted artifact
  is the spec it produces, which lives the normal spec lifecycle.
- **Review card → archived when the thinker walks it.** `orc` writes one per
  shipped spec into `BRIEF_INBOX`. A `think` session in review mode walks it with
  you; on "happy" it archives the card, on "unhappy" it writes a corrective spec
  *and* archives the card. An old un-walked `*.review.md` is a real signal — work
  shipped that you never eyeballed — so it stays visible until walked.

---

## Intent: the "why", recorded once and graded at the end

Two different things are both called "intent". Keep them separate.

**Per-spec intent** — the goal of *this* work item. A required `intent:` header on
every spec: 1–2 plain sentences saying *what success means and why*. It is
**distinct from acceptance criteria**: criteria are the test, intent is the
target. A feature can pass every criterion and still miss the point. `handover`
refuses to drop a spec whose `intent:` is missing or empty. Write a real *why*
("reps waste ~10 min/day scrolling, give them a date filter"), not a restated
*what* ("add a date filter").

**Standing intent** — the project's invariants, in `INTENT_DOC`. The non-
negotiables across all work. The orchestrator reads it every session and it is
the **final arbiter of "done"** — a feature can satisfy a wrong spec perfectly.

**The close-out grader (the one real check).** The session that builds something
is the worst judge of whether it's right — it spent its whole context trying to
make it right. So before closing a spec, the orchestrator dispatches **one fresh
sub-session that never saw the build**, handing it only three things:

1. the frozen `intent:` (from the archived spec),
2. the acceptance criteria,
3. the actual diff that shipped.

The grader returns a plain verdict — *"matches intent: yes/no, because…"* — and
checks the shipped behavior against `INTENT_DOC` too. On "no", the orchestrator
surfaces it loudly to the human. The grader is honest because it's blind, not
because anyone launched it by hand; it's an internal sub-session like any worker.

---

## No silent stalls

The nightmare: you hand off work, walk away, and in the morning find the
orchestrator asked a trivial question at minute two and sat idle all night while
you thought it was building. The guards, in order:

1. **Bias to act — tuned by `DELICACY`.** Most "questions" are really assumptions
   the orchestrator should state and proceed on. The line:
   - **`bold`** — proceed on anything reversible (undoable by `git revert` or a
     re-run), stating one `ASSUMPTION:` line per guess.
   - **`cautious`** (default) — proceed on small reversible mechanics, but **stop
     and ask** when the spec is *valid but genuinely ambiguous about what you
     want* (e.g. "add a filter" — by date? by status?). A spec passing every
     validity check can still be unclear about intent; that case is worth a
     question, because building the wrong thing confidently wastes your review
     time even when the code is reversible.
2. **Front-load questions in one batch** at intake — never a trickle discovered
   hours apart.
3. **A question blocks only its own task, never the session.** Everything not
   gated by the answer keeps running. The session is "idle" only if *all* work
   depends on the one unanswered question.
4. **Blocked is LOUD.** When genuinely waiting, the top line of the status board
   becomes `⛔ WAITING ON YOU since <HH:MM> — <question>`, and the orchestrator
   fires a push notification if it can. No wait is ever silent.
5. **The ledger is the morning-after truth.** Progress is written to the plan
   file as it happens, including `BLOCKED at <time> on <question>`. The real state
   is always auditable, never a vibe.

**Guessed-vs-waited report.** At the end of a run the orchestrator states what it
did: *"I guessed on these 3 things (flagged in the plan), I waited on these 2."*
You read that and re-tune `DELICACY` next time. Your guessing is always visible,
never buried.

---

## When the orchestrator can't build a spec

No retry machinery. A bounce is just a message to the human, because the thinker
that wrote the spec is already gone (one-shot) — there's no dead session to send
it back to.

- **If you're in the session:** the orchestrator tells you plainly why the spec
  won't build and what it'd need, and you decide. Most of the time you fix it on
  the spot or wave it through with an assumption.
- **If you're away:** it writes `NNN-<slug>.bounced.md` with the reason, so the
  message survives. On its next boot the orchestrator **halts and surfaces any
  un-acknowledged bounce** before processing past it — no silent rot.

A spec won't build when: a path/route/table it depends on is gone; it violates a
standing invariant unacknowledged; or it has no testable acceptance criteria.
Everything else, the orchestrator proceeds on per the `DELICACY` rule above.

---

## `think` has shapes, not siblings

`think` (stage 2) is the one worker seat where a human sits; it is not a single
fixed behaviour. You pick a **shape** at boot. Crucially these are shapes of one
skill, not separate skills — an earlier draft split "collect" and "memo" into their
own skills and that was a mistake: they're things a thinker *does*, not separate
seats to sit in.

- **Brainstorm** — discovery/research/probing on something new, or on a claimed
  brief; converges on one approach → a spec.
- **Review** — walk the orchestrator's review cards (see "The review loop").
- **Claim a brief** — a thin entry to Brainstorm on whatever the planner queued.
- **Collect** — the persistent small-stuff pile (below).

And a thinker performs two **outbound handoffs** itself, offered whenever the work
is ready — neither is a separate skill you boot:

- **Hand over a spec** → to `orc`, via the `handover` helper (validates the header,
  places it atomically). This is the commit moment.
- **Send a memo** → to `orc` (`SPEC_INBOX/memo-*.md`) or the planner
  (`BRIEF_INBOX/memo-*.md`): advisory context, never a work item.

### Collect — capture many small items, synthesize one spec

Not every item is worth a brainstorm. The steady drip of one-line bugs and nits
needs somewhere to land *without* interrupting you. Collect is that spot — a
distinct interaction shape, the inverse of brainstorm: low-touch across many items,
with the thinking **deferred** to a single synthesis pass at the end. It is
**session-scoped** — capture and synthesize within the one session; nothing
persists, no inbox, no counters:

- **Capture (default).** You fire items; the thinker records each in an in-session
  working list and does only *light* work — group, dedupe, note the likely
  file/route, light background research (read the named file, confirm the route),
  flag anything too big. It does **not** interrogate you; one line of acknowledgement
  per item. Deferring the questions is the whole point.
- **Synthesize (`collect done` / "organize it").** The thinking happens once, over
  everything captured: the thinker groups it, peels anything too big into a **brief**
  (not jammed into the batch), **resolves its questions with you in one batch**,
  writes **one comprehensive spec** (per-cluster `intent:` + acceptance criteria —
  same contract any spec meets, including no open questions), and hands it over.
  Collect skips the brainstorm ceremony, not the spec contract. If the session ends
  before synthesis, the jots are lost — the accepted trade for zero lane machinery.

## The review loop — accounting for what shipped

The nightmare at the *other* end of the pipeline: `orc` ships 7–12 specs in a run
and you lose the thread of what you even designed, then walk away hoping each one
landed right. The fix is a short card per ship and a way to loop back.

- **`orc` writes a review card per shipped spec.** At close-out — after grading,
  integrating, and shipping — it drops a tiny, human-readable
  `NNN-<slug>.review.md` (numbered after the spec) into `BRIEF_INBOX`. Contents:
  one line of what shipped + the frozen `intent:`, where to look (routes / files /
  preview URL), **N concrete things to eyeball**, and the blind grader's verdict.
  It lands in the thinker's lane because the thinker is who reads it. **Every**
  shipped spec gets one — so 10 overnight ships leave you 10 cards to sweep, and
  nothing ships unaccounted-for.
- **A `think` session walks the cards** in review mode. It pulls up each card,
  walks you through the eyeball items, and:
  - **happy** → archives the card;
  - **unhappy** → writes a corrective spec to `SPEC_INBOX` (intent: "prior ship
    missed X, correct it") and archives the card.

That corrective spec re-enters `orc` like any other — the loop closes through you
opening a `think` session, never a machine edge writing back into a dead one.
This is the "anything loop-like routes through the human" invariant in action.

## The orchestrator relay — passing a live run to the next orc

An orchestrator saturates its context after a couple of specs, and only one orc may
run at a time — so when it fills up you pass the whole run to a *fresh* orc, you
don't spin up a second. A generic session-summary skill is the wrong tool; it isn't
built to carry a live run. The orchestrator writes a purpose-built **relay baton**.

The principle is DO-IT's own — **state is the filesystem.** The plan file already
holds the ledger; the baton (`RELAY_BATON`, default `docs/sessions/orc-relay.md`)
carries only the *session-volatile* bits the plan file doesn't: which workers were
in-flight and as what branches (those sub-sessions die with the session that
launched them), git/worktree state, deploy state, current blocks, and the single
next action.

- **Outgoing:** at the ~70% checkpoint, write the baton stamped `status: HANDED-OFF`
  and stop. It commits with the rest of the work, so the run stays auditable.
- **Incoming:** the next orc reads the baton in its first moves and **reconciles it
  against the actual tree — the baton is a hint, the filesystem is truth.** For
  each in-flight worker it checks the branch and *adopts the result if finished,
  re-dispatches if not.* It confirms the prior session is closed, then stamps the
  baton `RESUMED` so it can't be claimed twice. This is the only sanctioned way to
  move a live run between sessions, and the `HANDED-OFF`→`RESUMED` handshake is what
  keeps two orchestrators from ever overlapping.

## Invariants (don't break these)

- **One-shot sessions.** Nothing is ever resumed; no message edge writes back into
  a dead session. Anything loop-like routes through the human.
- **Lean orchestrator.** Its scarce resource is its own context. Every read,
  build, and analysis is pushed to a sub-session that returns a tiny summary. A
  bloated orchestrator is a failed orchestrator.
- **Throughput via parallelism.** The orchestrator fans out as many concurrent
  workers as real dependencies allow — no fixed cap — specifically so it keeps
  pace with the rate specs arrive. (The *integration* lane is one-at-a-time; the
  *worker* fan-out is not.)
- **Singleton orchestrator.** Never two owning the same working tree. A live run
  moves between sessions only via the relay baton's `HANDED-OFF`→`RESUMED`
  handshake.
- **Thinkers are read-only on code.** Only the orchestrator touches the tree.
- **Three bootable sessions, not more.** planner (stage 1), think (stage 2, the
  worker seat with shapes — brainstorm/review/claim-brief/collect), orc (stage 3).
  Collect and memo are things a thinker *does*, not separate skills; `handover` is
  an internal helper a thinker invokes, not a seat you boot into.
- **Memos are never actionable** — advisory context only, structurally separate from
  specs. They're acknowledged on read and archived once folded in (not coupled to a
  spec). If you're writing "should build/add/change…", it's a spec, not a memo.
- **Pull-on-boot, re-scanned each turn.** Nothing is event-driven; reading sessions
  pick work up on boot and re-scan their lane every turn. A read is only real once
  it's acknowledged in the board/ledger.
- **Review cards are read-only artifacts** — `orc` writes them, the thinker walks
  them; a card never carries requirements (an unhappy walk produces a *spec*).
- **`INTENT_DOC` is the final arbiter of done** — above any single spec.
