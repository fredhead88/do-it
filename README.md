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

DO-IT splits the work into **two session roles**, each a thin Claude Code skill that
boots a session into a job:

- **`think`** *(the worker seat)* — a read-only session where you sit and turn intent
  into a *spec*. It has shapes (brainstorm, walk review cards, **intake/triage a raw
  dump**, collect small bugs) and hands its own work over. Run three or four at once;
  by the time you circle back, one's ready. You're never idle.
- **`orc`** *(the orchestrator)* — picks specs out of the inbox, fans out parallel
  sub-sessions to build them, **grades the result with a fresh session that never saw
  the build**, integrates, and ships — staying lean by pushing all the heavy work to
  those sub-sessions. Singleton; the only session that commits.

```
think  ──spec──▶  spec-inbox  ──▶  orc  ──▶  plan ▶ fan out ▶ grade ▶ ship
(×N, the worker seat)  (a folder)     (one, owns the git tree)
```

`spec-handover` (drop a spec into the inbox — your commit moment) is a helper a
`think` session invokes, not a separate seat you boot into. (The old `planner` stage
is gone — it folded into `think` as the intake/triage shape.)

Sessions are **one-shot and disposable**. Nothing is ever resumed; no message
loops back into a dead session. Anything loop-like routes through you. The inbox
is just a folder — the file's location *is* the state, so there's no database and
nothing to keep in sync.

## What makes it more than copy-paste

A handful of ideas do the real work:

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
- **A review loop that closes.** Ship 10 specs overnight and you lose track of
  what you even designed. So `orc` leaves a short, human-readable *review card*
  for every spec it ships — what changed, where to look, a handful of things to
  eyeball, the grader's verdict. Later you open a `think` session in review mode,
  walk the cards, and on anything that missed it writes a corrective spec straight
  back to `orc`. The loop closes through you, never through a resumed session.
- **An orchestrator relay.** One orchestrator runs at a time, and it saturates its
  context after a couple of specs. Instead of a generic "summarize and hope," it
  writes a purpose-built *baton* — which workers were mid-flight and on what
  branches, git/deploy state, the next action — that a fresh `orc` reads and
  **reconciles against the actual tree** before continuing. The baton is a hint;
  the filesystem is the truth.
- **A ledger that can't lie about "done."** Work vanishes in the seam between
  "handed over" and "shipped" — a spec put on hold whose hold is quietly released, or
  merged code stuck behind a broken deploy that still reads as finished. So every spec
  gets one small status record (`held` / `bounced` / `rework` / `merged` / `shipped` /
  …) that `orc` advances at each step, and `merged` is never `shipped` (that takes a
  *verified* deploy). Records live in the **bus** (`~/.claude/ledger/`) and are written
  only through `spec_ledger.py register` / `set` — never hand-edited, so they can't get
  malformed or skip a required field; the repo holds a *generated* mirror that can't
  drift. One command answers "anything we wrote but never built?"

## Quickstart

```bash
git clone https://github.com/fredhead88/do-it.git
cd do-it
# 1. Edit the CONFIG block at the top of DO-IT.md (REPO_ROOT, INTENT_DOC, …)
# 2. Install the skills and create the inboxes:
./setup.sh
```

Then, in Claude Code:

- Say **`think`** to spec something out. When it's done, **`spec-handover`**.
- In another session, say **`orc`** to build whatever's waiting in the inbox.

That's the whole core: **three skills + one shared protocol doc (`DO-IT.md`)**.

## The shapes a `think` session can take

`think` is one skill with several shapes — you pick one at boot. They are *not*
separate skills:

- **Brainstorm** — design something new (or develop a claimed brief) → a spec.
- **Review** — walk the orchestrator's review cards; archive the good, send back a
  corrective spec on anything that missed.
- **Intake / triage** — sort a raw dump into topics; handle some now, park the rest
  as lightweight briefs (this absorbs the old `planner` stage).
- **Collect** — low-touch capture of many small bugs/nits in one session; on
  `collect done` it synthesizes them into one comprehensive spec. Session-scoped —
  nothing persists between sessions.

And a thinker performs two handoffs itself (offered when the work is ready, not
booted as their own skills): **hand over a spec** (to `orc`, via the `spec-handover`
helper) and **send a memo** (advisory context to `orc`, never a work item).

## How it's organized

```
do-it/
├── DO-IT.md          # the shared protocol — CONFIG block + all the rules
├── setup.sh          # creates the inboxes, links the skills, checks CONFIG
├── skills/
│   ├── think/          # the worker seat: brainstorm / review / intake-triage / collect
│   ├── spec-handover/  # helper think invokes to drop a spec in the inbox + ledger
│   └── orc/            # build, grade, integrate, ship; review cards + relay
├── scripts/
│   └── spec_ledger.py  # build-status ledger: register/set writes, render, --check
└── docs/DESIGN.md      # the full design rationale and the decisions behind it
```

Each skill is a single `SKILL.md` and stays thin — it points at `DO-IT.md` for
the shared rules instead of restating them, so the roles can't drift apart.

## Verification Loop

A standing autonomous reviewer that drives shipped work from "orc says done" to
"verified green on prod." It runs headless against your deployed app, assigns typed
evidence to each acceptance criterion in the spec, judges cross-vendor (Codex primary,
Claude fallback), and loops to convergence — filing correctives for hollow specs,
escalating taste calls, and never touching the build.

Three core invariants:

1. **Blind-but-watching.** The verifier never sees the build, the diff, or the
   builder's reasoning — only the typed evidence artifact.
2. **Evidence-type-locked.** A UI criterion requires a DOM or screenshot observation;
   a grep is auto-fail. No criterion closes without observed, type-matched evidence.
3. **Verifier owns the verdict.** Verdicts live in `~/.claude/ledger/verified/` — a
   namespace the builder's `set`/`register` commands never touch.

The harness is project-agnostic: all project-specific values live in a single
`verification-loop/config/<project>.json`. To set it up:

```bash
cd verification-loop
npm install
cp config/example.json config/<your-project>.json
# fill in prod_base, api_base, page_map, auth, and the path fields
```

Then run: `node tick.mjs --config <your-project> --dry-run --force`

Full setup instructions: [`verification-loop/SETUP.md`](verification-loop/SETUP.md).
Config field reference: [`verification-loop/config/README.md`](verification-loop/config/README.md).

## Watching the loop itself (v3.7)

`rev` reviews the shipped *product*. The **`watcher`** reviews the *loop*: is the
build/review machine itself producing defects, churn, or invisible work? It's rev's
twin one level up — the only role that looks *across* runs rather than within one, so
it's the only one that can see a class of bug recurring or a process drifting. It reads
the ledger history, the relay logs, and a ranked fatal-mistakes registry, and — rarely,
and only with dated evidence — proposes a systemic guard via a `/think` handover. It is
read-only on everything, never registers a spec, and is capped by a hard quota so it
can't churn the rules. A `watcher` session that concludes "loop healthy, no proposal" is
a success, not an idle one. Say **`watcher`** to boot one.

## Relay-watch (v3.2, hardened v3.7): the orc never stops

An orchestrator session eventually fills its context window, and the manual fix
— tell it to hand over the baton, `/clear`, type `/orc` — made *you* the cron
job. `relay-watch/` automates the loop: a PostToolUse hook measures the exact
live context from the session transcript and, past a threshold, tells the orc
to write its relay baton and stop; a per-minute cron then sends `/clear` +
`/orc` to the same tmux pane once the baton lands and the session goes quiet.
Each orc generation retires itself and boots its successor — the baton file
carries the session-volatile state across, same as a manual relay.

Scoped hard: the hook acts only in the pane the orc skill registers at boot,
so thinker sessions and unrelated projects are never touched. One cron line
serves all your DO-IT repos. Setup (a hook entry + a cron line):
[`relay-watch/SETUP.md`](relay-watch/SETUP.md).

v3.7 hardened the trigger after a live deadlock: the sentinel is now armed at a
**soft line** (`ORC_WATCH_SOFT`, default 0.9× threshold), not only the hard
ceiling — so an agent that hands off *early* (a deliberate handoff, or after the
soft nudge) still relays instead of sitting wedged waiting for a hard-threshold
signal that never comes. The cron gate also scans the baton head (not just line
1), refuses stale/half-written batons, and consumes a baton once so it can't
double-`/clear` a freshly booted pane.

## Design rationale

The full reasoning — why one-shot sessions, why a filesystem inbox over a
database, where the design was deliberately *cut* back from something more
elaborate, and the decisions behind each pillar — is in
[`docs/DESIGN.md`](docs/DESIGN.md).

## License

MIT — see [LICENSE](LICENSE).
