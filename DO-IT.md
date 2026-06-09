# DO-IT — Pipeline Operating Protocol

**Version:** 3.7.0 · history: `CHANGELOG.md` · rationale: `docs/DESIGN.md`

The single source of truth for how the spec pipeline works. Every role-skill
(`think`, `spec-handover`, `orc`, `rev`, `watcher`) reads this and obeys it — they do **not** restate its
rules. This is the *what* (always current); the *why* + decision log is `DESIGN.md`.
When you change the pipeline, follow §7.

---

## 0. CONFIG — fill in once per project

The only place project-specific values live; `setup.sh` checks `REPO_ROOT` is no longer
a placeholder. Edit this block — don't scatter paths through the skills.

```yaml
REPO_ROOT:      /path/to/your/repo          # the working tree the orchestrator owns
INTENT_DOC:     docs/INTENT.md              # standing invariants; final arbiter of "done"
ARCH_DOCS:      docs/architecture/          # optional: living architecture map
DEPLOY_CMD:     ./deploy.sh                 # how the orchestrator ships; "" if none
BUS_ROOT:       ~/.claude                   # machine-global; reachable from any worktree
SPEC_INBOX:     ~/.claude/spec-inbox        # orchestrator's lane (keep default)
BRIEF_INBOX:    ~/.claude/brief-inbox       # thinker's lane — briefs + review cards
LEDGER_DIR:     ~/.claude/ledger            # BUS: build-status masters, one file per spec
LEDGER_MIRROR:  docs/do-it/ledger/OUTSTANDING.md  # in-repo: generated, committed snapshot
SPEC_DOCS:      docs/do-it/specs/           # spec docs (plans: docs/do-it/plans/)
RELAY_BATON:    docs/sessions/orc-relay.md  # in-repo: orchestrator-to-orchestrator handoff
RENDERER:       python scripts/spec_ledger.py [--check]
ORC_MODEL:      opus
WORKER_MODEL:   sonnet                      # default sub-session model (floor)
DELICACY:       cautious                    # cautious | bold — see "Bias to act"
```

## 1. The map

```
dump ─▶ think ─spec─▶ handover ─▶ spec-inbox + ledger ─▶ orc ─plan─▶ fan out ─▶ integrate ─▶ deploy
        (intake/triage, brainstorm)                               (singleton; only committer)
                                                                              │
                                                                  rev (review twin, read-only)
```

- **think** — read-only on code. Discovery/brainstorm → spec; **intake/triage** of a
  dump (absorbs the old planner). Reads `brief-inbox`, writes specs + briefs + memos.
  Safe to run several at once.
- **handover** — the atomic, self-verifying drop of a finished spec into the bus +
  the ledger (§4). Writes `spec-inbox` + `ledger` only.
- **orc** — the singleton integrator. The ONLY session that owns the working tree,
  commits, and deploys. Reads everything; advances the ledger; renders the mirror.
- **rev** — the standing reviewer (orc's twin). Drives the verification loop, reads
  rendered-page evidence, writes per-criterion verdicts to the verifier namespace,
  files correctives. Read-only on code; never commits; never authors specs. Self-relays
  on its own `ROLE=rev` watcher.
- **watcher** — the standing process reviewer (rev's twin, one level up). rev reviews
  the shipped *product*; the watcher reviews the *loop* — whether the build/review
  machine is itself producing defects, churn, or invisible work — and proposes
  systemic guards via a `/think` handover. Read-only on code/git/bus; never registers
  an NNN (076); evidence-bound; one-proposal-per-session quota. Self-relays on its own
  `ROLE=watcher`.

## 2. The message bus

Two lanes (by audience) + the ledger. State **is** file location — no manifest.

| Type | File | Author | Reader |
|------|------|--------|--------|
| Brief (lightweight) | `NNN-<slug>.brief.md` | think | think |
| Claimed brief | `NNN-<slug>.brief.claimed.md` | think | think |
| Spec | `NNN-<slug>-spec.md` | handover | orc |
| Memo (advisory, never a work item) | `memo-<topic>.md` | think | orc |
| Review card (mirrors the spec) | `<slug>.review.md` | orc | rev |
| Triage account | shown in-session on multi-item dumps | think | human |
| Ledger record (master) | `ledger/NNN-<slug>.yml` | handover→orc→think | all |
| Relay baton | `docs/sessions/orc-relay.md` | orc | orc |

**Naming — one rule, no exceptions:** `NNN-<slug>` — numbered, hyphens, **never a
dot before the type** (`-spec.md`, never `.spec.md`; the inbox glob is `*-spec.md`,
so a dotted name is silently never seen). Both lanes are numbered so "001 shipped,
where's 003?" is a followable list. (Pre-2026-06-03 specs keep their date-stem ids
— grandfathered.)

**Allocate with `spec_ledger.py next-num` — the single source of truth, never a
hand-rolled grep.** Briefs and specs share **one** number space. `next-num` takes a
machine-global lock, scans every bus dir matching **3 digits followed by a hyphen**
(`^[0-9]{3}(?=-)` — the `(?=-)` is load-bearing: without it the year in a
grandfathered `2026-...` file reads as "202" and allocates ~203, which then becomes
the new max and poisons every future allocation), and **reserves the number as it
returns it** — births the `registered` ledger record (spec) or writes the brief
file (brief). Two consequences this fixes: (1) the *misread* — date-stems can't
inflate the max, and a computed number ≥150 is refused as poison; (2) the *race* —
two sessions can no longer both compute `max+1` and double-book (the 110 collision),
because the first reservation is on disk before the lock releases. A per-record lock
can't do this; allocation needs the one bus-wide lock `next-num` holds.

**Atomic drop (non-allocating writes):** write `<name>.tmp` in the target dir, then
rename into place. Readers ignore `*.tmp`. (Allocation itself no longer races, so the
old "loser retries `NNN+1`" dance is gone — `next-num` hands out distinct numbers.)

**The review card mirrors the spec (the close-out contract).** A card carries **one
`components:` row per spec acceptance-criterion — no omissions** (done + how verified,
or not-done + why). Two independent passes guard it before the human: orc blind-audits
the card against the spec **in-session** (folded into its close-out grader — nothing
ships with an incomplete card), and the **executable verifier** (driven by `rev`) runs
the per-criterion verdict — `rev` spot-checks the residual (taste, layout, interactions
the machine can't fully judge) and files correctives to orc where needed. Closure is the
derived `accepted` (shipped ∧ CONFIRMED). The thinker is no longer in the closure path.
Human last, not first. A card that omits or contradicts the spec goes back to orc as
`rework` (§3), never to the human.

**Evidence-bound close-out gate (hard rule).** `shipped` is impossible until every
`components:` row carries type-matching observed evidence from the deployed surface:

- Each row carries `criterion_type: ui | backend`, `evidence:` (the observation), and
  `evidence_type:` (must match the type — see below). The spec also declares
  `surfaces:` listing which app surfaces it touched; orc augments from changed-files →
  routes.
- **UI criterion** → `evidence_type: screenshot+interaction_trace`. The close-out gate
  **drives** the interaction (clicks, types, hovers) and records the observation. A
  grep or code-reference is AUTO-FAIL — it cannot confirm rendered behaviour.
- **Backend criterion** → `evidence_type: curl_status+body_excerpt`. A signed evidence
  record `{url, status, body_sha256, body_excerpt}` is the artifact. This format is
  shared with the standing verification-loop harness so both speakers read the same
  evidence.
- **Deterministic pre-gates run before any LLM judging:** (1) build passes, (2) every
  route in `look_at:` returns HTTP 200, (3) at least one screenshot is non-blank. If
  any pre-gate fails, the spec is rejected without calling the LLM.
- **Regression subset:** the gate re-runs the prior-accepted criteria of every surface
  named in `surfaces:` — a cheap targeted re-check, not the full ledger — so a
  recurring breakage on a touched surface is caught before the new work is accepted.
- **The gate stays build-blind.** The grader sub-session never saw the build, the diff,
  or the builder's reasoning. It receives only the typed artifact. Feeding the
  builder's explanation to the grader is gameable and defeats the independence invariant.

**No quiet descope — the point of the system is to *do it*.** A component is `done`,
or its `not-done` clears a hard bar: (a) the spec itself put it out of scope; (b) it's
irreversible without authorization; (c) it's hard-blocked on an external dependency
orc can't obtain in-session; (d) it's a true fork only the human can decide. **Every
other reason — "deferred", "wasn't sure", "gated on a refactor", "felt risky" — is not
a disposition, it's unfinished work**, and the default is to build it. The three
legitimate non-(a) reasons are all **loud** — they convert to a human question or a
`held` blocker the human sees — so a whole section can never go missing behind a quiet
"deferred." The blind close-out grader **enforces** this: it doesn't score the
not-dones, it *challenges* the weak ones and sends them back as "build these," and orc
completes them in-session before anything ships. Reversibility is what makes this
safe to be aggressive — a wrong build is one `git revert`.

**Deferrals surface first, the moment you open a thinker.** A legitimate not-done
lives loud — as a `not-done` row on the shipped review card, or as `held` on the
ledger — and the `/think` boot inventory **leads with these, by name, before the
normal counts**, so a deferred piece can't hide inside the review queue until you
notice the page never changed.

## 3. The index — one numbered list, statuses not files

The durable answer to "what's outstanding?" is the **ledger**: one
`NNN-<slug>.yml` per spec in the bus (`~/.claude/ledger/`), **born `registered` at
handover** (§4) so it's current the instant handover runs — no orc needed. Render
any time: `python scripts/spec_ledger.py` (writes the committed mirror);
validate with `--check`.

**Lifecycle:** `registered → planned → building → merged → shipped → accepted`,
plus `held`, `bounced`, `rework`, `superseded`, `retired`. Advance a record inline at
the loop point where the transition happens; append (never rewrite) a `history:` entry.

**`bounced` vs `rework` — two different rejections, two directions, one reader each.**
Both mean "can't go forward as-is; returned to sender, loud, with a reason" — but who
sent it and who fixes it differs, so they're two words:
- **`bounced`** = **orc → human.** Orc can't build the spec (path gone, invariant
  violated, no testable criteria, fundamentally ambiguous). The thinker is gone, so
  this is a message to *you*; you re-spec or fix. (+ `bounce_reason`, `needs`.)
- **`rework`** = **rev → orc.** The `rev` reviewer (or the executable verifier) found
  the shipped card omits spec criteria or orc's verification claims don't hold. The work
  isn't accepted; *orc* rebuilds the card / builds the missing piece and re-ships.
  (+ `rework_reason`.)

**Everything is a status, never a separate file.** Every not-done state lives on the
one list, so nothing can rot in a folder no one watches:

| Situation | Status (not a file) |
|-----------|---------------------|
| Handed over, not yet picked up | `registered` |
| Orc can't build the spec → back to the human | `bounced` (+ `bounce_reason`, `needs`) — loud |
| rev / verifier sent the shipped card back to orc (incomplete / claims don't hold) | `rework` (+ `rework_reason`) — loud |
| Deliberately paused | `held` (+ `held_reason`) — loud |
| Replaced by a corrective spec | `superseded` (+ `superseded_by`) |
| Abandoned | `retired` |

**Ironclad tracking (the guarantee):** a handover can't confirm receipt — so pickup
proof is the status *leaving* `registered`. A handed-over-but-unpicked spec stays
`registered` forever and renders loud on the one list. Not-picked-up is impossible
to hide; that — not the drop — is the guarantee.

**Task-list mirror (the dashboard):** orc keeps a harness task list at **spec
level** (`registered/planned → pending`, `building/merged/shipped → in_progress`,
`accepted → completed`), **rebuilt from the ledger on every boot**. Display only —
the ledger is the source of truth; never the reverse.

## 4. Handover — the atomic write

Handover is ONE self-verifying action. It either fully lands or errors loudly — no
partial state:

1. place the numbered spec into `spec-inbox/` as `NNN-<slug>-spec.md` (atomic, §2);
2. write the ledger master `ledger/NNN-<slug>.yml` with `spec_id`, `title`,
   `intent`, `status: registered`, `handed_over_at`, `spec_file`, and an opening
   `history:` entry — **directly, no stub**;
3. confirm both exist and are non-empty; on any failure, report the partial state.

No git — handover writes the bus only; orc commits the spec doc + mirror.

## 5. State & archive

File location is state: live in a lane = pending; `_archive/` = done/consumed.
`_archive/` is **append-only — never `rm`**; the archived spec is the frozen
as-handed-over snapshot the close-out grader audits against. Cross-lane lineage is
the `source_brief:` header on a spec (one-way is enough).

## 6. Prime directives

- **Throughput via parallelism.** Fan out as many workers as real dependencies
  allow; the cap is dependencies, not a number. The integration lane is WIP=1.
- **Lean orchestrator.** Push every read/build/analysis to a sub-session that
  returns a tiny summary. A bloated orchestrator is a failed orchestrator.
- **Nothing lost / no silent stalls.** Every not-done state is on the one list;
  every wait is loud and timestamped; bias to act on anything a `git revert` undoes.

## 7. Evolving DO-IT (the self-hosting ritual)

When you propose a pipeline change: **read this file** (the rule now) → **read
`DESIGN.md`** (why it's this way, what was rejected) → **change this file, append a
dated decision to `DESIGN.md`, and add a `CHANGELOG.md` entry + bump the version line
above** (semver: new capability → minor, fix/clarification → patch, breaking
role/bus/naming change → major). Never silently. The pipeline evolves the way you work.

## 8. Why / decisions

The rationale, trade-offs, and dated decision log live in **`DESIGN.md`** (same
folder). This file is the *what*; that one is the *why*.
