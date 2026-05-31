# DO-IT — Shared Protocol

The single source of truth for the DO-IT pipeline. Every skill points here
instead of restating the rules, so they can't drift apart. Read this once; the
skills assume you know it.

DO-IT moves work from raw idea → organized topic → spec → built-and-shipped
feature, across **separate, one-shot Claude Code sessions** on one machine that
pass typed messages through a shared filesystem inbox.

```
PLANNER ──briefs──▶ brief-inbox ──▶ THINKER ──spec──▶ spec-inbox ──▶ ORCHESTRATOR
  (optional)                          (one-shot)                      plan → fan out
                                                                      workers → grade
                                                                      → integrate → ship
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
BRIEF_INBOX:    ~/.claude/brief-inbox       # thinker's lane (keep default)
ORC_MODEL:      opus                        # orchestrator session model
WORKER_MODEL:   sonnet                      # default sub-session model (floor)
DELICACY:       cautious                    # cautious | bold — see "Bias to act"
```

If `REPO_ROOT` or `INTENT_DOC` don't resolve, a booting skill drops into a
one-time setup interview to write this block rather than failing on a bad path.

---

## The two lanes

State lives in **where a file sits**, not in any manifest (a shared mutable
manifest would just be a second thing to keep in sync). Two inbox folders, split
by who reads them:

- **`SPEC_INBOX`** — the orchestrator's lane. Holds `*.spec.md` (actionable),
  `memo-*.md` (advisory context), `*.bounced.md` (needs the human).
- **`BRIEF_INBOX`** — the thinker's lane. Holds `*.brief.md` and
  `triage-receipt.md`.

Each lane has an `_archive/` subfolder for consumed items. `ls *.md` in a lane =
what's still pending. Nothing is ever `rm`'d — finished items move to `_archive/`.

---

## Message types

| Type | File | Written by | Read by | Numbered | Mutable |
|------|------|-----------|---------|----------|---------|
| Brief | `NNN-<slug>.brief.md` | planner | thinker | yes (brief counter) | draft only |
| Claimed brief | `NNN-<slug>.brief.claimed.md` | thinker | human / orc | mirrors brief | adds `claimed_at:` |
| Spec | `NNN-<slug>.spec.md` | handover | orchestrator | yes (spec counter) | no |
| Memo | `memo-<topic>.md` | drop / planner | orchestrator | **no** | **yes** (`last_updated:`) |
| Bounce | `NNN-<slug>.bounced.md` | orchestrator | human | mirrors spec | no |

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
- **Memo → archived when its topic's spec closes.** Keeps stale memos from rotting
  in the lane and misleading a future orchestrator.

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
- **Singleton orchestrator.** Never two owning the same working tree.
- **Thinkers are read-only on code.** Only the orchestrator touches the tree.
- **Memos are never actionable** — advisory context only, structurally separate
  from specs.
- **`INTENT_DOC` is the final arbiter of done** — above any single spec.
