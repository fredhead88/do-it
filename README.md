# DO-IT — a spec pipeline for parallel, one-shot Claude Code sessions

Move work from raw idea → spec → shipped feature across **separate, disposable
Claude Code sessions** that hand each other typed messages through a shared
filesystem bus. Think in one session, build in several more, integrate in one —
all at once.

## Why this exists

If you drive Claude Code hard, you hit three walls:

1. **One session can't both think and build well.** Brainstorming wants a
   read-only, exploratory headspace; building wants a focused executor that owns
   its files. Cram both into one chat and they interfere — and the chat fills up
   with reading and scratch work until quality drops.
2. **One builder is a bottleneck.** You have five specs ready and one session
   grinding them one at a time while you watch.
3. **You sit and wait.** Thinking and research have dead time — while one session
   researches, you could be feeding the next.

DO-IT splits the work into a handful of **session roles**, each a thin Claude Code
skill that boots a session into one job:

- **`think`** *(the worker seat)* — a read-only session where you sit and turn intent
  into a *spec*. It has shapes (brainstorm, walk review cards, **intake/triage a raw
  dump**, collect small bugs) and hands its own work over. Run three or four at once;
  by the time you circle back, one's ready. You're never idle.
- **`builder`** *(the muscle, ×N)* — parallel sessions, each claims **one** spec from
  the build lane, builds it end-to-end **in its own git worktree**, self-checks, pushes
  the branch, and frees itself for the next. Builders never touch `master` and never
  step on each other — worktree isolation + a one-writer-per-spec lane make it safe to
  run several at once.
- **`integrator`** *(the singleton; `orc` is its alias)* — the only session that owns
  the working tree and commits. It picks specs out of the inbox, derives each spec's
  file footprint, assigns them into the build lane, then re-checks each finished branch
  against current `master`, merges one at a time, and ships. It **never builds** — it
  stays lean by reading only the ledger and the lane files, never a build artifact.
- **`rev`** *(the review twin)* — a standing, read-only reviewer that drives shipped
  work to a verified verdict and files correctives. **`watcher`** — reviews the *loop
  itself*. Both below.

```
think ──spec──▶ inbox ──▶ integrator ──assign──▶ build lane ──▶ builder ×N (own worktrees)
(×N, worker seat)         (one, owns git tree)                        │
                                  ▲                                   ▼
                          merge/ship ◀── ready ◀── detached grader ◀── push
                                  │
                            rev (review twin) · watcher (reviews the loop)
```

`spec-handover` (drop a spec into the inbox + open its ledger record — your commit
moment) is a helper a `think` session invokes, not a seat you boot into. (The old
`planner` stage folded into `think` as the intake/triage shape; `orc` survives as an
alias for `integrator`.)

Sessions are **one-shot and disposable**. Nothing is resumed mid-thought; no message
loops back into a dead session. State lives entirely in the **bus** — a machine-global
folder (`~/.claude/`) where a file's *location is its state*, so there's no database
and nothing to keep in sync. The bus is machine-global (not repo-relative) on purpose:
parallel builders live in separate worktrees and must share one bus.

## What makes it more than copy-paste

A handful of ideas do the real work:

- **A blind grader that runs on its own.** The session that built something is the
  worst judge of whether it's right — it spent its whole context trying to make it
  right. So when a builder pushes, it flips its lane file to `gating` and **frees
  immediately**; a separate, pane-independent grader (`gating-watch`) checks the pushed
  branch from a *fresh checkout* — it never saw the build, the diff, or the builder's
  reasoning, only the goal and the typed acceptance criteria — and writes the verdict
  (`ready` on pass, `rework` on fail). Honest because it's blind; fast because it's
  detached.
- **Intent, written as a real *why*.** Every spec carries one or two sentences: what
  success means and *why* — separate from the test checklist. That sentence is what the
  grader judges against.
- **A build lane with one writer per step.** Between the integrator and the builders is
  a lane of files that move `assigned → building → gating → ready`. Each transition has
  exactly one writer at the instant it happens, so up to several builders + the grader +
  the integrator can advance *different* specs at once and never corrupt anything.
- **No silent stalls.** The nightmare is handing off work, walking away, and finding in
  the morning that a session asked a trivial question at minute two and sat idle all
  night. The integrator proceeds on anything safe (flagging its assumptions), parks only
  the one blocked task while the rest keep running, and makes any genuine wait loud and
  timestamped. Standing roles are watched by cron heartbeats that key on *real progress*,
  not "the pane still exists" — a role that goes quiet surfaces in minutes.
- **A review loop that closes.** Ship a dozen specs overnight and you lose track of what
  you even designed. So every shipped spec leaves a short, human-readable *review card* —
  what changed, where to look, a few things to eyeball, the grader's verdict. `rev` (or
  you, in a `think` review session) walks the cards; anything that missed becomes a
  corrective spec straight back to the integrator. The loop closes through a fresh
  session, never a resumed one.
- **An integrator relay.** One integrator runs at a time and eventually fills its
  context. Instead of "summarize and hope," it writes a purpose-built *baton* — which
  builders were mid-flight and on what branches, git/deploy state, the next action — that
  a fresh integrator reads and **reconciles against the actual tree** before continuing.
  The baton is a hint; the filesystem is the truth.
- **A ledger that can't lie about "done."** Work vanishes in the seam between "handed
  over" and "shipped" — a spec put on hold whose hold is quietly released, or merged code
  stuck behind a broken deploy that still reads as finished. So every spec gets one small
  status record advanced at each step — `registered → planned → building → gating →
  ready → merged → shipped → accepted`, plus `held` / `bounced` / `rework` — and `merged`
  is never `shipped` (that takes a *verified* deploy). Records live in the bus
  (`~/.claude/ledger/`) and are written only through `spec_ledger.py register` / `set` —
  never hand-edited, so they can't get malformed or skip a field; the repo holds a
  *generated* mirror that can't drift. One command answers "anything we wrote but never
  built?"

## Quickstart

```bash
git clone https://github.com/fredhead88/do-it.git
cd do-it
# 1. Edit the CONFIG block at the top of DO-IT.md (Repo root, Intent doc, Deploy, …)
# 2. Install the skills, create the bus lanes, make the scripts executable:
./setup.sh
```

Then, in Claude Code:

- Say **`think`** to spec something out. When it's ready, **`spec-handover`**.
- Say **`integrator`** (or **`orc`**) in one session to assign + integrate what's waiting.
- Say **`builder`** in one or more other sessions to build the assigned specs in parallel.

That's the whole core: **the role skills + one shared protocol doc (`DO-IT.md`)**. The
standing automation (relay, nudge, detached grader, heartbeats) is optional cron — see
[`scripts/CRON-SETUP.md`](scripts/CRON-SETUP.md).

## The shapes a `think` session can take

`think` is one skill with several shapes — you pick one at boot. They are *not*
separate skills:

- **Brainstorm** — design something new (or develop a claimed brief) → a spec.
- **Review** — walk the integrator's review cards; archive the good, send back a
  corrective spec on anything that missed.
- **Intake / triage** — sort a raw dump into topics; handle some now, park the rest
  as lightweight briefs (this absorbs the old `planner` stage).
- **Collect** — low-touch capture of many small bugs/nits in one session; on
  `collect done` it synthesizes them into one comprehensive spec. Session-scoped.

And a thinker performs two handoffs itself (offered when the work is ready, not booted
as their own skills): **hand over a spec** (to the integrator, via the `spec-handover`
helper) and **send a memo** (advisory context, never a work item).

## The roles at a glance

| Role | Boots with | What it does | Touches git? |
|------|-----------|--------------|--------------|
| `think` | `think` | intent → spec; brainstorm / review / triage / collect | never (read-only) |
| `spec-handover` | (helper) | drop a spec in the inbox + open its ledger record | no |
| `builder` | `builder` | claim one spec, build it in its own worktree, push, self-gate | own worktree/branch only |
| `integrator` | `integrator` / `orc` | assign, re-check, merge, deploy, advance the ledger | **the only committer** |
| `rev` | `rev` | drive shipped work to a verified verdict; file correctives | never (read-only) |
| `watcher` | `watcher` | review the *loop* itself; propose systemic guards | never (read-only) |

There's also **`operator-ops`**, a documented ephemeral role that runs exactly one prod
data mutation and dies — reached for when a spec needs a one-off privileged run.

## How it's organized

```
do-it/
├── DO-IT.md            # the shared protocol — CONFIG block + all the rules
├── setup.sh            # creates the bus lanes, links the skills, checks CONFIG
├── skills/
│   ├── think/            # the worker seat: brainstorm / review / intake-triage / collect
│   ├── spec-handover/    # helper think invokes to drop a spec in the inbox + ledger
│   ├── builder/          # claim one spec, build in an isolated worktree, push, self-gate
│   ├── orc/              # the integrator: assign, re-check, merge, deploy, ledger
│   ├── rev/              # standing review twin — verified verdicts + correctives
│   ├── watcher/          # reviews the loop itself
│   └── verification-loop/# the autonomous prod verifier skill
├── scripts/
│   ├── spec_ledger.py    # build-status ledger: register/set writes, render, --check
│   ├── gating-watch.sh   # detached blind grader: advance .gating → .ready / .rework
│   ├── doit-nudge.sh     # poke an idle role pane when its lane has unconsumed work
│   ├── standing-role-heartbeat.sh, builder_lifecycle_reconcile.sh, watcher_sweep_liveness.sh
│   ├── ci/               # handover criterion↔evidence validator + thinker-isolation guard
│   ├── close-out-gates/  # portable builder close-out checks (data consumers, nav, manifest)
│   └── CRON-SETUP.md     # the standing-role cron block (relay/nudge/grader/heartbeat)
├── relay-watch/          # the integrator baton loop (hook + cron) + liveness
├── verification-loop/    # the Node harness the prod verifier runs
└── docs/DESIGN.md        # the full design rationale and the decisions behind it
```

Each skill is a single `SKILL.md` and stays thin — it points at `DO-IT.md` for the
shared rules instead of restating them, so the roles can't drift apart.

## Verification Loop

A standing autonomous reviewer that drives shipped work from "integrator says done" to
"verified green on prod." It runs headless against your deployed app, assigns typed
evidence to each acceptance criterion in the spec, judges cross-vendor, and loops to
convergence — filing correctives for hollow specs, escalating taste calls, and never
touching the build.

Three core invariants:

1. **Blind-but-watching.** The verifier never sees the build, the diff, or the
   builder's reasoning — only the typed evidence artifact.
2. **Evidence-type-locked.** A UI criterion requires a DOM or screenshot observation; a
   grep is auto-fail. No criterion closes without observed, type-matched evidence.
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

Full setup: [`verification-loop/SETUP.md`](verification-loop/SETUP.md).
Config reference: [`verification-loop/config/README.md`](verification-loop/config/README.md).

## Watching the loop itself

`rev` reviews the shipped *product*. The **`watcher`** reviews the *loop*: is the
build/review machine itself producing defects, churn, or invisible work? It's rev's twin
one level up — the only role that looks *across* runs rather than within one, so it's the
only one that can see a class of bug recurring or a process drifting. It reads the ledger
history, the relay/heartbeat logs, and a ranked fatal-mistakes registry, and — rarely,
and only with dated evidence — proposes a systemic guard via a `/think` handover. It is
read-only on everything, never registers a spec, and is capped by a hard quota so it
can't churn the rules. A `watcher` session that concludes "loop healthy, no proposal" is
a success, not an idle one.

## Standing-role automation: the loop never stalls

The roles are one-shot, but the *machine* around them is kept alive by a small set of
per-minute cron jobs (all optional — DO-IT runs fine hand-driven):

- **relay-watch** — an integrator (or any standing role) eventually fills its context.
  A PostToolUse hook measures the live context and, past a threshold, tells the session
  to write its baton and stop; a cron then sends `/clear` + the role command to the same
  tmux pane once the baton lands. Each generation retires itself and boots its successor,
  the baton carrying session-volatile state across.
- **doit-nudge** — pokes an *idle-but-live* role pane when its lane has unconsumed work
  (a new spec to pick up, a ready branch to merge). Presence-aware: it stays quiet while
  you're actively driving the pane.
- **gating-watch** — the detached grader; advances `.gating → .ready / .rework` with no
  pane of its own.
- **heartbeat / reconcile / sweep** — prove a standing role is actually *making progress*
  (not just that its pane exists), heal builder-sentinel drift against live tmux, and
  confirm the watcher has done a real sweep — so a role that goes dark surfaces loudly.

Everything is scoped to the panes the skills register and honours `REPO_ROOT` / `PYTHON`
/ `BUS_ROOT` env overrides, so one set of cron lines serves any project. Setup:
[`scripts/CRON-SETUP.md`](scripts/CRON-SETUP.md) (cron block) and
[`relay-watch/SETUP.md`](relay-watch/SETUP.md) (the relay hook).

## Design rationale

The full reasoning — why one-shot sessions, why a filesystem bus over a database, why
the integrator never builds, where the design was deliberately *cut* back from something
more elaborate, and the decisions behind each pillar — is in
[`docs/DESIGN.md`](docs/DESIGN.md).

## License

MIT — see [LICENSE](LICENSE).
