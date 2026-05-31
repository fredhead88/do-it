# DO-IT — a spec pipeline for parallel, one-shot Claude Code sessions

Move work from raw idea → spec → shipped feature across **separate, disposable
Claude Code sessions** that hand each other typed messages through a shared
filesystem inbox. Think in one session, build in another, run several at once.

## Why this exists

If you drive Claude Code hard, you hit two walls:

1. **One session can't both think and build well.** Brainstorming wants a
   read-only, exploratory headspace; building wants a focused executor that owns
   the git tree. Cram both into one chat and they interfere — and the chat fills
   up with reading and scratch work until quality drops.
2. **You sit and wait.** A single session means you watch one thing happen. But
   thinking and research have dead time — while one session researches, you could
   be feeding the next.

DO-IT splits the work into roles, each a thin Claude Code skill that boots a
session into a job:

- **`think`** — a read-only session that brainstorms and writes a *spec*. Run
  three or four at once; by the time you circle back, one's ready. You're never
  idle.
- **`handover`** — drops a finished spec into the orchestrator's inbox. This is
  your commit moment: "build this."
- **`orc`** — the orchestrator. Picks specs out of the inbox, fans out parallel
  sub-sessions to build them, **grades the result with a fresh session that never
  saw the build**, integrates, and ships — while staying lean by pushing all the
  heavy work to those sub-sessions.

```
think  ──spec──▶  spec-inbox  ──▶  orc  ──▶  plan ▶ fan out workers ▶ grade ▶ ship
(×N, parallel)    (a folder)        (one, owns the git tree)
```

Sessions are **one-shot and disposable**. Nothing is ever resumed; no message
loops back into a dead session. Anything loop-like routes through you. The inbox
is just a folder — the file's location *is* the state, so there's no database and
nothing to keep in sync.

## What makes it more than copy-paste

Three ideas do the real work:

- **A blind grader.** The session that built something is the worst judge of
  whether it's right — it spent its whole context trying to make it right. So
  before closing, `orc` hands a *fresh* session only the goal, the acceptance
  criteria, and the diff, and asks "does this match?" Honest because it's blind.
- **Intent, written as a real *why*.** Every spec carries one or two sentences:
  what success means and *why* — separate from the test checklist. That sentence
  is what the grader judges against.
- **No silent stalls.** The nightmare is handing off work, walking away, and
  finding in the morning that the orchestrator asked a trivial question at minute
  two and sat idle all night. `orc` proceeds on anything safe (flagging its
  assumptions), parks only the one blocked task while the rest keep running, makes
  any genuine wait loud and timestamped, and reports at the end exactly what it
  guessed versus what it waited on.

## Quickstart

```bash
git clone https://github.com/fredhead88/do-it.git
cd do-it
# 1. Edit the CONFIG block at the top of DO-IT.md (REPO_ROOT, INTENT_DOC, …)
# 2. Install the skills and create the inboxes:
./setup.sh
```

Then, in Claude Code:

- Say **`think`** to spec something out. When it's done, **`handover`**.
- In another session, say **`orc`** to build whatever's waiting in the inbox.

That's the whole core: **three skills + one shared protocol doc (`DO-IT.md`)**.

## Advanced add-ons (optional)

Two more skills help once you're running a lot of parallel work. Skip them until
you feel the need:

- **`planner`** — intake/triage. Turns a raw dump (ideas, meeting notes,
  transcripts) into discrete *briefs* for thinker sessions, with a receipt that
  accounts for every item so nothing is silently dropped.
- **`drop`** — leaves the orchestrator an *advisory memo* ("this might affect how
  you're thinking"). A memo is context, never a work item — the orchestrator never
  builds from one.

## How it's organized

```
do-it/
├── DO-IT.md          # the shared protocol — CONFIG block + all the rules
├── setup.sh          # creates the inboxes, links the skills, checks CONFIG
├── skills/
│   ├── think/        # core
│   ├── handover/     # core
│   ├── orc/          # core
│   ├── planner/      # advanced
│   └── drop/         # advanced
└── docs/DESIGN.md    # the full design rationale and the decisions behind it
```

Each skill is a single `SKILL.md` and stays thin — it points at `DO-IT.md` for
the shared rules instead of restating them, so the roles can't drift apart.

## Design rationale

The full reasoning — why one-shot sessions, why a filesystem inbox over a
database, where the design was deliberately *cut* back from something more
elaborate, and the decisions behind each pillar — is in
[`docs/DESIGN.md`](docs/DESIGN.md).

## License

MIT — see [LICENSE](LICENSE).
