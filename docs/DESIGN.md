# DO-IT Pipeline — Design Record & Decision Log

**Status:** Approved design — ready for implementation plan
**Date:** 2026-06-03
**Supersedes:** `~/.claude/do-it/SPEC.md` (2026-05-31 draft, now stale + orphaned)
**Scope:** Redesign of the spec pipeline (`planner`/`think`/`handover`/`orc`) into a
leaner, loss-proof system, plus a single shared operating doc (`DO-IT.md`).

## What this doc is

This is the **design record** — the *why*, the trade-offs, and the dated decision
log. The living *what* — the rules sessions obey every run — lives in `DO-IT.md`.
Same split as a feature spec vs `docs/INTENT.md`: this goes stale and gets a
decision appended; `DO-IT.md` is always current. When you change the pipeline, you
edit `DO-IT.md` **and** append a decision here (see *Evolving DO-IT*).

## The problems we're fixing

1. **Drift & bloat.** The shared protocol doc the original design called for was
   never built, so every skill restated the lanes/roles/naming independently and
   drifted. `orc` reached 418 lines; prerequisites were declared nowhere.
2. **Things go to die.** Work sits in a not-done state in some file/folder nobody is
   watching — most painfully *drafts* and *handed-over-but-unpicked specs*.
3. **Clutter.** 5 roles, ~13 file types, stub files (`register.yml`/`accept.yml`),
   an over-built deploy-blocker subsystem.

## The model (one line)

> **The bus holds everything in flight and its state; the repo holds code plus a
> committed snapshot of that state; orc is the only thing that commits.**

## Roles — 3 (was 5)

`think` → `handover` → `orc`. `planner` is **deleted** (folded into `think`); `drop`
never existed (memos are a `think` action).

- **think** — read-only on code. Discovery/brainstorm → spec; review of shipped work;
  and now **intake/triage** of a raw dump. Safe to run several at once.
- **handover** — the atomic, self-verifying drop of a finished spec into the bus +
  the index.
- **orc** — the singleton integrator: the only session that owns the working tree,
  commits, and deploys.

## Two homes

```
~/.claude/                     # BUS — machine-global, reachable from any worktree
  spec-inbox/                  #   actionable specs + memos + control
  brief-inbox/                 #   lightweight briefs + review cards
  ledger/  NNN-<slug>.yml       #   THE LIVE INDEX — one file per spec, the master

<your-repo>/                   # REPO — durable, committed, versioned
  DO-IT.md                     #   operating protocol (the living "what")
  docs/DESIGN.md               #   this file (the "why" + decision log)
  docs/do-it/specs/            #   spec docs
  docs/do-it/plans/            #   execution plans
  docs/do-it/ledger/OUTSTANDING.md  #   GENERATED committed mirror of the bus ledger
  scripts/spec_ledger.py       #   renders bus ledger → mirror; --check validates
```

Two homes, each with one reason: the **bus must be outside any repo** (a thinker in
one worktree hands to an orc in another); the **repo holds the committed record**.
Everything else that was scattered (the `superpowers/` nesting, the orphaned
`SPEC.md`) consolidates here.

## The index — one numbered list, statuses not files

The single durable answer to "what's outstanding?" is the **ledger**: one
`NNN-<slug>.yml` per spec, **born `registered` at handover**, living in the bus so
it's current the instant handover runs — no orc required. Renderable any time via
`spec_ledger.py`; orc commits the mirror (`OUTSTANDING.md`) for git history.

**Lifecycle:** `registered → planned → merged → shipped → accepted`, plus
`bounced`, `held`, `superseded`, `retired`.

**Everything is a status, never a separate file.** This is the core principle. Every
not-done state lives as a status on the one numbered list, so nothing can sit in a
folder nobody watches:

| Old separate artifact | Now |
|---|---|
| `register.yml` | handover writes the `registered` entry directly |
| `accept.yml` | review writes `accepted` directly (entry is in the bus, not code — read-only-on-code preserved) |
| `.bounced.md` | status `bounced` (+ `bounce_reason`, `needs`) |
| `blockers/<id>.yml` | status `held` (+ reason) |
| `.in-progress` | status `building` + the relay baton |

**Pickup proof = status leaving `registered`.** A handed-over spec nobody picked up
stays `registered` forever and renders loud — you can't miss it on the one list.
This replaces "ironclad handover" (a drop can't confirm receipt) with **ironclad
tracking** (a not-picked-up spec is impossible to hide).

**Native task-list mirror (the dashboard).** Orc maintains a harness task list
(`TaskCreate`/`TaskUpdate`) at **spec level** — one task per spec, `received →
planned → shipped → accepted` — for the live in-session checklist. It is **display
only and session-volatile**: rebuilt from the ledger on every boot, never the source
of truth. The task list is the dashboard; the ledger is the database.

## Handover — the atomic write

One self-verifying action: place the numbered spec discoverably **and** write its
`registered` ledger entry **and** confirm both on disk, or **error loudly**. No
partial state, no manual multi-step ritual.

## Naming — one rule, no exceptions

`NNN-<slug>` — **numbered, hyphens, never a dot before the type**. Both lanes
numbered (briefs already were; specs now are too) so "001 shipped, where's 003?" is a
followable running list. Allocation = `max(live + archive) + 1`; collision →
retry `NNN+1`. This permanently kills the `.spec.md`/`-spec.md` silent-loss trap and
lets every per-skill hyphen warning be deleted.

## File types: 13 → 8

**Kept:** `NNN-<slug>.brief.md` (lightweight) · `NNN-<slug>.brief.claimed.md` ·
`NNN-<slug>-spec.md` · `memo-<topic>.md` · `NNN-<slug>.review.md` ·
`ledger/NNN-<slug>.yml` · `orc-relay.md` · `triage` account (in-session, on dumps).
**Killed:** `register.yml` · `accept.yml` · `blockers/<id>.yml` · `.in-progress` ·
`.bounced.md` · the planner roadmap memo.

## Death-trap fixes

The index protects everything *after* handover; the remaining graveyards were all
*before* it. Fixes:

| Trap | Fix |
|---|---|
| Bounced specs (a dead-letter file) | a loud **status** on the one list |
| Briefs sitting unclaimed | **think's boot inventory** surfaces them every session |
| Drafts dying (session ends pre-handover) | a thinker **must hand over, park, or discard — never vanish** (rule; session-end hook is possible later hardening) |
| Stale / silently-read memos | surfaced with "last touched N days ago — still true?" |

Accepted, by-design losses (chosen to avoid index flood): collect jots (session-
scoped) and raw ideas never captured.

## think absorbs the planner

- **Boot inventory.** Every `/think` opens with what's waiting — e.g. "6 open briefs ·
  3 review cards · 2 stale claims" — then the menu. This makes the pre-handover lane
  loud and is what kills the unclaimed-brief trap.
- **Intake/triage.** Dump a pile into `think`; it groups into topics, handles some in
  this session, and **parks the rest as lightweight numbered briefs** (topic +
  problem + "develop later" — no heavy schema).
- **Park-and-point, never spawn.** Respecting one-shot sessions, think parks the brief
  and tells you "parked as brief 007 — open a fresh `/think` when you want." You launch
  it.
- **Dump account.** For multi-item dumps, intake ends with a one-shot account (each
  item → handled now / parked as brief NNN / dropped + reason) so nothing drops.

## The three simplifications

1. **Everything is a status, never a file** (13 → 8 types; also the main death-trap
   fix — one lever, both problems).
2. **One naming rule** (`NNN-<slug>`, hyphens, no dots; both lanes numbered).
3. **One doc, one folder** (`DO-IT.md` + `docs/do-it/`) — deletes the duplicated
   tables that bloated `orc`.

## Evolving DO-IT (the self-hosting ritual)

When you propose a pipeline change: **read `DO-IT.md`** (the rule now) → **read this
`DESIGN.md`** (why, what was rejected) → **change `DO-IT.md` and append a dated
decision below.** Never silently. The system evolves the way you work.

## `DO-IT.md` section skeleton (what to build)

0. CONFIG — repo root, bus paths, INTENT/arch paths, deploy command (one place).
1. The map — roles, flow, which lane each reads/writes.
2. The message bus — lanes, message-type table, the one naming rule, atomic drop.
3. The index — ledger contract, statuses, ironclad tracking, the task-list mirror.
4. Handover — the atomic, self-verifying write.
5. State & archive — file location = state; `_archive/` append-only, never `rm`.
6. Prime directives — throughput via parallelism · lean orchestrator · nothing-lost.
7. Evolving DO-IT — the iteration ritual above.
8. Pointer to `DESIGN.md`.

## Decision log

- **2026-06-03 — Entry point is handover, not idea-level.** Rejected tracking every
  uttered idea: a noisy index is one you stop trusting, which *causes* loss. Keep the
  index born at handover; protect pre-handover work with surfacing (boot inventory,
  park-or-discard), not tracking.
- **2026-06-03 — Index lives in the bus, repo gets a generated mirror.** Makes the
  index live the instant handover runs (no orc dependency) and kills both stub file
  types; orc remains the only committer. Cost (master outside git) mitigated by the
  existing `~/.claude` backup + the committed mirror.
- **2026-06-03 — Keep numbered specs.** Reversed an earlier "drop counters" call:
  the running number is the human-followable face of the index.
- **2026-06-03 — Collapse planner into think; lightweight briefs; keep the dump
  account.** Removes a role and ~3 file types; preserves the nothing-dropped guarantee
  only where it's needed (multi-item dumps).
- **2026-06-03 — Native task list is spec-level, display-only, rebuilt from the
  ledger.** Never the source of truth (it's session-volatile).
- **2026-06-03 (implemented) — Grandfather existing ids.** The ~50 archived specs and
  72 ledger records keep their `YYYY-MM-DD-<slug>` ids; **new** specs use `NNN-<slug>`
  (numbering starts at `001`). The renderer keys on whatever `spec_id` is present, so
  mixed ids coexist — a bulk renumber would be risky and pointless.
- **2026-06-03 (implemented) — Deleted `scripts/spec_ledger_backfill.py`.** A
  superseded one-shot: it seeded the original repo-side ledger from the review queue
  and wrote blocker records — both concepts this redesign removed (handover now writes
  records directly; blockers are a `held` status). The 72 records it produced live on
  in the bus.

- **2026-06-04 — Review card mirrors the spec; review is a two-gate funnel, human
  last.** The thin card (4-6 free-form eyeball items + grader verdict) had no contract
  binding it to the spec, so orc could silently omit a requirement and the thinker had
  nothing to reconcile against — the human was the *first* checker. Now the card
  carries **one `components:` row per spec acceptance-criterion** (done + how verified,
  or not-done + why), making "did anything get lost?" countable. The `/think` review
  becomes: **Gate 1** reconcile spec↔card (missing row → un-walkable), **Gate 2**
  independently re-verify each row from the read-only seat, **then** surface only the
  residual (can't-machine-check items + not-done dispositions) to the human as a
  compressed verdict. Cost to orc is near-zero — the criteria are already enumerated as
  typed contracts in the plan, so the card is mostly a copy, and it auto-scales.
- **2026-06-04 — Two independent machine passes, not one.** Orc records its own
  `check:` per row (it built it, it can curl it); the thinker re-checks independently in
  a different session. Chosen over "thinker is the only checker" because production is
  sacred and orc curling its own endpoint is cheap — orc claims, thinker confirms, human
  eyeballs the residual.
- **2026-06-04 — `bounced` is the general "back to orc, with reason" channel.** Widened
  from "can't build" to also carry a review that found the card incomplete or its claims
  contradicted. Chosen over routing every such case through a corrective spec (which
  conflates "the card is wrong" with "the work is wrong," and spins up a spec for what
  may be a card fix) and over inventing a new status (the system's ethos is "everything
  is a status, no new machinery" — `bounced` already renders loud on orc's board with
  `reason`/`needs` fields and fits "orc, this needs you again").
- **2026-06-04 — Card-completeness audit folded into orc's blind close-out grader,
  in-session.** Rather than relying on the thinker's Gate 1 as the primary catch (a
  days-later cold-session bounce), orc **drafts the card first**, then its existing
  blind close-out sub-agent returns **two** verdicts — matches-intent AND card-mirrors-
  spec (every criterion has a row; claims square with the diff). Nothing ships until
  both pass, so the omission is caught while orc's context is hot. Chosen over a
  separate card-audit skill (more machinery than a solo operator needs; the blind
  grader already exists and already has the criteria + diff in hand). The thinker's
  Gate 1 demotes from primary catch to independent backstop — the same two-independent-
  passes principle, now applied to card completeness too. Required one reorder: the card
  is drafted *before* the close-out grade, since the auditor needs something to audit.

- **2026-06-04 — No quiet descope; the grader challenges weak `not-done`s instead of
  scoring them.** The hole in the card-mirror design: `not-done + why` was an
  honest-looking field that's actually a free escape hatch — orc could write "deferred
  / gated on a refactor / wasn't sure" and the human only discovers the missing section
  at review. A *do-it* system was quietly becoming a *do-most-of-it* system. Fix: a
  hard bar on `not-done` (only spec-out-of-scope · irreversible-without-authorization ·
  hard external blocker · true human fork survive), the three non-out-of-scope reasons
  are all **loud** (human question / `held`, never a silent card row), and the blind
  close-out grader is told to be a **challenger** — any weak not-done returns "build
  these," orc completes it in-session, re-grades. Chosen over leaving `not-done` as a
  free-text disposition (the original hole) and over a human-only veto at review (too
  late, and the human shouldn't be the first line). **Non-destructive by construction:**
  reversibility (`git revert`) makes aggressive building cheap, and the re-grade loop
  terminates because any surviving not-done must be whitelisted-and-loud — no afternoon
  burned on thrash. This is the teeth behind "the point of the system is to literally
  do it."

- **2026-06-04 — Deferrals lead the thinker boot inventory.** The bite the operator
  named: spec a feature → a piece is deferred → you find out only when the page is
  unchanged. The boot inventory counted "N review cards" with a partial card
  indistinguishable from a clean one. Fix: the boot inventory now **greps cards for
  `not-done` rows and the ledger for `held`, and surfaces those first by name** before
  the normal counts. No new artifact — the not-done row on the card and the `held`
  status already carry it; this just promotes them to the top of the one place the
  operator reliably looks (a thinker session). Chosen over a separate "blocker" file
  type (violates "everything is a status, no new files") and over a push notification
  alone (the operator asked specifically for the thinker boot to lead with it).

- **2026-06-04 — Split `bounced` into `bounced` + `rework` (reverses the same-day
  "widen bounced" decision above).** Widening `bounced` to mean both "orc can't build
  this spec → human" and "review sent the card back → orc" made one word point two
  directions with two readers — the operator (correctly) found it unintelligible, which
  is itself the signal that the abstraction was wrong. Now: **`bounced` = orc→human**
  (unbuildable spec, keeps its original single meaning + `bounce_reason`/`needs`);
  **`rework` = thinker→orc** (shipped card omits criteria or claims don't verify;
  `rework_reason`). Each word has one reader and one direction. Unifying frame for both:
  "rejected, returned to sender, loud, with a reason." Cost: one new status value, wired
  into `spec_ledger.py` (`VALID_STATUS`, `OUTSTANDING_STATUSES`, a `rework_reason`
  validation, a loud 🔁 render branch). Chosen over keeping the unified word + a
  contextual `to:` field (still forces the reader to infer direction every time).
- **2026-06-04 — `rework` leads both work surfaces.** It's the operator's recurring
  bite — a spec he thought was shipped, sent back. So orc's First-moves halt-check
  **looks for `rework` first** (ahead of new specs — it's owed work on something thought
  done), the status board gets a dedicated `REWORK:` line that leads when non-empty, and
  the renderer surfaces it in the outstanding bucket. Same spec record round-trips
  `shipped → rework → shipped`; no new number.
- **2026-06-08 — number allocation matches `^[0-9]{3}(?=-)`, not `^\d{3}`.** The
  hand-rolled allocator grabbed the first three digits of any string, so
  grandfathered date-stem files (`2026-...`) read as "202" and allocated ~203;
  worse, the error was self-poisoning — a bad `203-` file became the new max and
  propagated forward (it bit the brief allocator and leaked `source_brief: 203`
  into a spec). The `(?=-)` lookahead requires a hyphen right after the three
  digits so a year can't match. Chosen over stripping date-stems from the bus
  (they're deliberately grandfathered) or a numeric-range filter (more brittle
  than the one-character anchor). Allocators also scan every bus dir — briefs and
  specs share one number space — and refuse a max ≥150 as a poison tripwire.
- **2026-06-08 (v3.3.0) — allocation is atomic via `next-num` under a bus-wide
  lock; the reservation IS the artifact.** The pattern fix above cured the *misread*
  but not the *race*: two sessions computing `max+1` inline both grabbed the same
  number (110 was double-booked between two `think` sessions). The per-record
  `flock` that `register`/`set` use can't help — allocators racing for a NEW number
  have no shared record path to contend on. Fix: one machine-global lock
  (`ledger/.alloc.lock`) held across scan → compute → reserve, exposed as
  `spec_ledger.py next-num`. **Reserve-as-artifact, not a placeholder:** for specs
  `next-num` births the real `registered` ledger record (it's just `register` with
  an allocated id — same fields, same validation); for briefs it writes the brief
  file. Chosen over a `reserved`-status stub + reaper (rejected: drags in a new
  status, `validate`/`render`/`--check` changes, and an orphan-cleanup lifecycle —
  whereas a born record / a parked brief are both already-valid resting states, so
  there's nothing to reap). Consequence: spec handover allocates **and** registers
  in one call (no separate `register` step), and the old "name-collision → retry
  `NNN+1`" dance is gone because numbers are now handed out distinct. Sequencing
  constraint: the helper must exist in the `spec_ledger.py` a skill invokes before
  that skill is flipped to call it — the public repo ships them together; a separate
  running instance lands/deploys the helper first.

## Rejected alternatives

- **Idea-level / pre-handover index** — flood risk; a noisy list is abandoned.
- **Skills as thin shells over one giant DO-IT.md** — one 400-line file is worse to
  iterate than a small shared doc + small role skills (approach C chosen).
- **Handover writes the repo working tree directly** — violates "only orc touches the
  tree"; reintroduces worktree collisions.
- **Shared deploy-blocker subsystem** — over-built for a single deploy target; `held` + reason
  covers it.
- **Per-lane NNN-collision machinery, bounce retry budget, `.in-progress` marker** —
  ceremony beyond a solo operator's need; simple retry + statuses + relay baton cover
  the same ground.
- **Native task list as the tracker** — session-volatile; would re-create the exact
  death-trap this redesign kills.
