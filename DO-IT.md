# DO-IT — Pipeline Operating Protocol

**Version:** 4.7.0 · history: `CHANGELOG.md` · rationale: `DESIGN.md`

The single source of truth for how the spec pipeline works. Every role-skill
(`think`, `spec-handover`, `builder`, `integrator`) reads this and obeys it — they do
**not** restate its rules. This is the *what* (always current); the *why* + decision log
is `DESIGN.md`. When you change the pipeline, follow §9.

> **v4.0.0 — parallel builders + lean integrator (spec 252); v4.7.0 adds `.gating`
> detached close-out state (spec 300).** The old singleton `orc` role split in two:
> **builders** (N parallel Opus sessions, each building one spec, drafting the review
> card, pushing, and flipping to `.gating` — then freeing immediately) feed one lean
> **integrator** (the revised orc — singleton; reads only the ledger + lane files,
> speculative-re-checks each `.ready` branch against current master, merges WIP=1,
> deploys one at a time, owns git-tree custodianship). A **pane-independent detached
> grader** (`gating-watch` cron) grades the pushed branch and produces the `.ready`
> (PASS) or `.rework` (FAIL) verdict. `/orc` is preserved as an **alias** for the
> integrator. The shared cross-role interface (lane suffixes, field names, state order)
> is normative in `252-CONTRACT.md` — when a skill and the contract disagree, the
> contract wins.

---

## 0. CONFIG — the one adapter (fill this in)

Every project-specific path or command lives here. The skills reference these keys
by name ("the Intent doc (CONFIG)"), never literals. A `—` means "this project
doesn't have one" and the skills skip the corresponding step.

| Key | Value |
|-----|-------|
| Repo root | `<absolute path to your repo, e.g. /home/you/myrepo>` |
| Bus root | `~/.claude/` (machine-global — **required** for the parallel-builder model so builders in separate worktrees share one bus; a single-integrator setup MAY instead use repo-relative `.do-it/`) |
| Spec lane | `<Bus root>/spec-inbox/` |
| Brief lane | `<Bus root>/brief-inbox/` |
| Ledger (master) | `<Bus root>/ledger/` |
| Build lane | `<Bus root>/build-lane/` |
| Ledger mirror (committed) | `<docs/do-it/ledger/OUTSTANDING.md — where the rendered view is committed>` |
| Spec docs | `<docs/do-it/specs/>` |
| Plans | `<docs/do-it/plans/>` |
| Intent doc | `<docs/INTENT.md — your north-star/invariants doc, or — if none>` |
| Architecture docs | `<docs/architecture/ — dir or explicit list, or — if none>` |
| Session handoff | `<docs/sessions/last-handoff.md — or — if none>` |
| Relay baton | `<docs/sessions/orc-relay.md — or — if none>` |
| Renderer | `<python spec_ledger.py — adjust interpreter + path; e.g. .venv/bin/python scripts/spec_ledger.py>` |
| Deploy recipe | `<the exact deploy command(s), or — if this project doesn't deploy (library/local)>` |
| Regression ledger | `<.claude/bugs/ + trigger_map.yaml — or — if none>` |
| Git standards | `<a conventions doc, or — to use conventional-commits defaults>` |

**Bus + renderer wiring.** The renderer (`spec_ledger.py`) reads the ledger masters
from `Bus root/ledger/` and writes the committed mirror. Point it at your bus with
env vars if you moved it: `DOIT_LEDGER_DIR` (default `~/.claude/ledger/`) and
`DOIT_MIRROR_DIR` (default `docs/do-it/ledger/`). Run it from the repo root. The
standing-role scripts likewise honour `REPO_ROOT` / `PYTHON` env overrides (they
default `REPO_ROOT` to the repo they live in and `PYTHON` to `python3`).

**The machine-global bus is gitignored by nature** (it lives under `~/.claude/`, outside
any repo). If you instead run repo-relative (`.do-it/`), add `.do-it/` to `.gitignore` —
the bus is working state, not code, which is what lets a read-only `think` session write
to it without "touching code." Only the rendered mirror (under your docs tree) is committed.

## 1. The map

```
dump ─▶ think ─spec─▶ handover ─▶ spec-inbox + ledger ─▶ integrator ─assign─▶ build-lane
        (intake/triage, brainstorm)                       (singleton; only committer)   │
                                                                  ▲                       ▼
                                                                  │              builder ×N (parallel)
                                                merge/deploy ◀─ready─ [grader] ◀─gating─ (own worktree)
                                                                  │
                                                      rev (review twin, read-only)
```

- **think** — read-only on code. Discovery/brainstorm → spec; **intake/triage** of a
  dump (absorbs the old planner). Reads `brief-inbox`, writes specs + briefs + memos to
  the bus only (**never** into the repo — bus-first authoring, §2 staging lanes). Safe
  to run several at once. (Review of shipped work moved to `rev`.)
- **handover** — the atomic, self-verifying drop of a finished spec into the bus +
  the ledger (§4). Writes `spec-inbox` + `ledger` only.
- **integrator** (the revised `orc`; `/orc` is an **alias**) — the singleton. The ONLY
  session that owns the working tree, commits, and deploys. **Reads ONLY the ledger +
  build-lane files — never a build artifact** (it stays structurally lean). Derives each
  spec's `writes:` footprint, assigns it into the build lane off a frozen `base_sha`,
  speculative-re-checks every `.ready` branch against **current** master, merges WIP=1,
  deploys one at a time, advances the ledger, renders the mirror, and is the accountable
  **git-tree custodian**. It scans `*.ready.md` (for merge) and `*.rework.md` (convert
  to rework-flagged `.assigned`) — it **explicitly ignores `*.gating.md`**, which belongs
  to the detached grader. It is a **pure integrator — it never builds, even when idle.**
- **builder** (new; N parallel Opus sessions, sub-agents on Sonnet) — claims ONE
  `.assigned` spec via atomic rename to `.building`, builds it in its **own git worktree**
  off the integrator's `base_sha`, drafts the identity-stamped review card, pushes its
  branch, and **flips `.building`→`.gating`** (adding `gating_at`, `ready_sha`,
  `card_path`) — then **frees immediately** to claim the next `.assigned`. The builder
  does **not** run the close-out gate or wait for a verdict; the pane-independent
  **detached gating-watch grader** produces the verdict (`gating`→`ready` on PASS;
  `gating`→`rework` on FAIL). A builder **never** checks out or commits to master, never
  deploys, never plans/touches another spec, never edits any `*-relay.md` except its own
  `builder-<id>-relay.md`. Builders are **stateless** — they own no spec across sessions;
  all return-paths route through the integrator to the pool (§3, R12).
- **rev** — the standing reviewer (the integrator's twin). Drives the verification loop,
  reads rendered-page evidence, writes per-criterion verdicts to the verifier namespace
  (`~/.claude/ledger/verified/`), files correctives into the needs-human store.
  Read-only on code; never commits; never authors specs. Self-relays on its own
  `ROLE=rev` watcher. `accepted` is **derived** from `shipped ∧ CONFIRMED`, not set
  by hand. **rev addresses the integrator, never a builder** — correctives re-enter via
  the integrator → build lane → pool (R12).

## 2. The message bus

Three lanes (by audience) + the ledger + thinker-owned staging lanes. State **is** file
location — no manifest. The **build lane** (`~/.claude/build-lane/`) is the
integrator↔builder interface added in v4.0.0.

| Type | File | Author | Reader |
|------|------|--------|--------|
| Brief (lightweight) | `NNN-<slug>.brief.md` | think | think |
| Claimed brief | `NNN-<slug>.brief.claimed.md` | think | think |
| **Spec staging (pre-number)** | `spec-staging/<slug>-spec.md` | think | spec-handover |
| Spec | `NNN-<slug>-spec.md` | handover (moved from staging) | integrator |
| **Research/design staging** | `think-staging/<slug>.md` (+ `target_path:`) | think | integrator (lands at `target_path`) |
| Memo (advisory, never a work item) | `memo-<topic>.md` | think | integrator |
| **Build-lane: assigned** | `build-lane/NNN-<slug>.assigned.md` | integrator | builder |
| **Build-lane: building** | `build-lane/NNN-<slug>.building.md` | builder (claims via atomic rename of `.assigned`) | integrator |
| **Build-lane: gating** | `build-lane/NNN-<slug>.gating.md` | builder (renames `.building`→`.gating` at push) | gating-watch cron |
| **Build-lane: ready** | `build-lane/NNN-<slug>.ready.md` | grader (gating-watch; renames `.gating`→`.ready` on PASS) | integrator |
| **Build-lane: dead-letter** | `build-lane/_dead/NNN-<slug>.assigned.md` | watchdog (Phase 2) | integrator + human |
| Review card (mirrors the spec, identity-stamped) | `<slug>.review.md` | builder | integrator + rev |
| Triage account | shown in-session on multi-item dumps | think | human |
| Ledger record (master) | `ledger/NNN-<slug>.yml` | handover→builder→integrator→think | all |
| Integrator relay baton | `docs/sessions/orc-relay.md` | integrator | integrator |
| Builder relay baton (per pane) | `docs/sessions/builder-<id>-relay.md` | builder `<id>` | builder `<id>` |

### The build lane (`~/.claude/build-lane/` + `_dead/`) — integrator↔builder

State **is the filename suffix**; transitions are atomic `mv` (tmp-then-rename); readers
ignore `*.tmp`. The filename stem is `NNN-<slug>`. (Normative: `252-CONTRACT.md` §1.)

| Suffix | Written by | Means | Required frontmatter |
|--------|-----------|-------|----------------------|
| `.assigned.md` | integrator | dispatchable — a builder may claim it | `spec_id`, `base_sha`, `writes:` (list), `plan_hint?` |
| `.building.md` | builder (claims `.assigned`→`.building` by atomic rename) | a builder owns it | adds `claimed_by` (tmux pane / session id), `claimed_at` (ISO-8601 UTC), `worktree`, `branch` |
| `.gating.md` | builder (renames `.building`→`.gating` at push) | pushed; under pane-independent detached close-out grader | adds `gating_at` (ISO-8601 UTC), `ready_sha`, `card_path` |
| `.ready.md` | grader (gating-watch; renames `.gating`→`.ready` on PASS) | graded PASS — awaiting integrator merge | adds `graded_by`, `graded_at` |
| `_dead/…assigned.md` | watchdog (Phase 2) | reclaimed `BUILDER_MAX_RETRIES`× | adds `retry_count`, `last_dead_reason` |

- **Claim is an atomic rename** `mv NNN.assigned.md NNN.building.md`: the builder that
  wins the rename owns the spec; a loser's `mv` fails and it picks another `.assigned`.
  Safe by construction — exactly one builder can win.
- **Footprint conflict gate (integrator):** the integrator NEVER writes/releases two
  overlapping-`writes:` `.assigned` files concurrently. A spec is in-flight (its
  `writes:` footprint locked) while its lane file is `.building`, `.gating`, or `.ready`;
  `.gating` files count as in-flight — the footprint stays locked while the detached
  grader runs. The **gravity set** —
  `api/app/config.py`, `api/app/deps.py`, `CLAUDE.md` — is
  exclusive: at most one in-flight builder may hold any member. Verticals (api + FE +
  pipeline) go WHOLE to one builder. (Enforcement code is Phase 2; Phase 1 applies it by
  hand.) **`api/alembic_supabase/` is NOT a gravity member (spec 286):** the migration
  head is reconciled at merge by `scripts/rechain_migration.py` (the integrator rewrites
  each migration's `down_revision` onto the live head before the single-head gate), so two
  migration-bearing specs may build in parallel. Semantically-coupled migrations (one's
  DDL depends on another's) must declare a sequential dependency so the integrator
  sequences that pair.
- **Branch naming is `feat/NNN-<slug>`** (not `feat/spec-NNN`). The git-janitor /
  worktree-reaper globs match `feat/NNN-*`.
- Coordination state lives in the machine-global bus (`~/.claude/build-lane/`), **never
  inside a worktree** (worktrees get removed).

**Bus-first authoring (spec 253) — thinkers never write into the repo working tree.**

The `spec-staging/` lane is the thinker's staging area for specs in progress.
`spec-handover` reads from here, allocates `NNN`, and **moves** the file to
`spec-inbox/NNN-<slug>-spec.md`. The repo-owner (the integrator) creates
`docs/do-it/specs/NNN-…` on master when it picks the spec up to assign it — see §4. The
thinker never places a file under `<repo root>`.

The `think-staging/` lane holds research/design docs that carry a `target_path:` header.
The repo-owner lands them at that path on its branch. Thinker never writes them into the
repo directly.

For the rare operator-approved code task, a thinker uses an **ephemeral worktree off
master** at `~/.claude/think-worktrees/think-<slug>/` on branch `think/<slug>`, hands
the branch to the integrator, and removes the worktree on handoff.

**Enforcement (wired, not honor-system):** `scripts/ci/check_thinker_isolation.sh`
flags any untracked `docs/do-it/specs/*-spec.md`, `docs/do-it/plans/*.md`, or
`docs/business|architecture/**/*.md` that landed in the shared checkout without going
through the integrator's landing step. It is invoked automatically at two choke points:
(1) **handover pre-flight** — `scripts/ci/handover_validate.py` (the validator the
`spec-handover` skill runs before allocating a number) runs the guard against the repo
root and surfaces any stray file loudly (advisory — the spec's criterion verdict is
unaffected, so a pre-existing stray from another session can't block a valid handover);
(2) **the nudge cron** — `scripts/doit-nudge.sh` runs the guard on every `ROLE=orc` tick
and raises a deduplicated alert (keyed by the stray-file set) into the orc nudge log.
Exits 0 on a clean tree, exits 1 on violation (reports + names files; does **not**
auto-delete — the repo-owner adjudicates).

**Naming — one rule, no exceptions:** `NNN-<slug>` — numbered, hyphens, **never a
dot before the type** (`-spec.md`, never `.spec.md`; the inbox glob is `*-spec.md`,
so a dotted name is silently never seen). Allocate `NNN = max(live + _archive) + 1`,
zero-padded to 3. Both lanes are numbered so "001 shipped, where's 003?" is a
followable list. (Pre-2026-06-03 specs keep their date-stem ids — grandfathered.)

**Atomic drop:** write `<name>.tmp` in the target dir, then rename into place; on a
name collision the loser retries `NNN+1`. Readers ignore `*.tmp`.

**The review card mirrors the spec (the close-out contract).** A card carries **one
`components:` row per spec acceptance-criterion — no omissions** (done + how verified,
or not-done + why). Two independent machine passes guard it before the human: the
**builder** blind-audits the card against the spec **in its own worktree** (folded into
the close-out grader it runs — nothing reaches `.ready` with an incomplete card), and
rev re-confirms completeness and re-verifies each row from the read-only seat **before**
surfacing only the residual (can't-machine-check items + not-done dispositions) to the
human. Human last, not first. A card that omits or contradicts the spec returns to the
integrator as `rework` (§3) → re-assigned to the pool, never to the human.

**The card is self-identifying (the "nothing lost between builders" guarantee — `252-CONTRACT.md` §3).**
Because the builder that wrote a card is gone by the time a verdict/corrective binds to
it, every card MUST carry an identity block so the binding survives *through the
integrator*:

```
spec_id:   NNN-<slug>
built_by:  <builder tmux pane / session id>
branch:    feat/NNN-<slug>
base_sha:  <integrator-provided snapshot the worktree branched from>
ready_sha: <tip of the pushed branch>
```

A card missing ANY identity field is incomplete → the builder's close-out gate
auto-fails it. The card body (`intent`, `shipped`, `look_at`, `surfaces`, one
typed-evidence `components:` row per criterion, the blind-grader line) is otherwise
unchanged; only the identity block is added and the *author* moved orc→builder.

**Evidence-bound close-out gate (R2 — hard rule).** `shipped` is impossible until
every `components:` row carries type-matching observed evidence from the surface the
builder rendered. **Who runs this gate moved orc→builder in v4.0.0 — it now runs in the
builder's own worktree against its own running surface, verbatim.** The contract below
is unchanged; only the runner moved. (The integrator does NOT re-run this full gate — it
runs the narrower speculative re-check + smoke against current master, §8 / `252-CONTRACT.md` §5.)

- Each row carries `criterion_type: ui | backend | observed-data | financial`,
  `evidence:` (the observation), and `evidence_type:` (must match the type — see
  coupling table below). The spec also declares `surfaces:` listing which dashboard
  surfaces it touched; the builder augments from changed-files → routes.
- **UI criterion** → `evidence_type: screenshot+interaction_trace`. The close-out gate
  **drives** the interaction (clicks, types, hovers) and records the observation. A
  grep or code-reference is AUTO-FAIL — it cannot confirm rendered behaviour.
- **Backend criterion** → `evidence_type: curl_status+body_excerpt`. A signed evidence
  record `{url, status, body_sha256, body_excerpt}` is the artifact. This format is
  shared with the standing verification-loop harness so both speakers read the same
  evidence.
- **Observed-data criterion** → `evidence_type: live_db_postgres_test`. Must be a
  `SUPABASE_DB_URL`-gated `live_db` pytest (real Postgres). A SQLite/fixture-only run
  is AUTO-FAIL. For **cron** sub-kinds (detected by `cron`/`schedule`/`every Nh`
  language): the criterion may not be closed before the job's next fire produces
  queryable rows — a commit/code-path check with no post-fire row assertion is
  AUTO-FAIL.
- **Financial criterion** → `evidence_type: canonical_cent_comparison`. Requires an
  explicit `abs(reported − canonical) ≤ $0.01` check against the canonical
  Profit/cash endpoint or the spec-159 canonical view. Self-attestation ("matches
  Console") with no cent-diff is AUTO-FAIL.

**Criterion-type ↔ evidence coupling table** (enforced by `scripts/ci/handover_validate.py`):

| criterion_type | Required evidence | AUTO-FAIL condition |
|---|---|---|
| `ui` | screenshot artifact path + interaction_trace (click/fill/console sequence) | grep / rg / static file-read as sole evidence |
| `backend` | curl_status + body_excerpt `{url, status, body_sha256, body_excerpt}` | missing signed artifact |
| `observed-data` | `SUPABASE_DB_URL`-gated `live_db` Postgres test | sqlite:/// / fixture-only run |
| `observed-data` (cron) | post-fire row assertion (rows appear after next scheduled fire) | commit or code-path check with no row assertion |
| `financial` | canonical cent-comparison: `abs(reported − canonical) ≤ $0.01` vs canonical endpoint / spec-159 view | self-attestation ("matches Console") with no cent-diff |

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
the builder can't obtain in-session; (d) it's a true fork only the human can decide. **Every
other reason — "deferred", "wasn't sure", "gated on a refactor", "felt risky" — is not
a disposition, it's unfinished work**, and the default is to build it. The three
legitimate non-(a) reasons are all **loud** — they convert to a human question or a
`held` blocker the human sees — so a whole section can never go missing behind a quiet
"deferred." The blind close-out grader (now run by the **builder** in its worktree)
**enforces** this: it doesn't score the not-dones, it *challenges* the weak ones and
sends them back as "build these," and the builder completes them before the branch
reaches `.ready`. Reversibility is what makes this safe to be aggressive — a wrong build
is one `git revert`.

**Deferrals surface first, the moment you open a thinker.** A legitimate not-done
lives loud — as a `not-done` row on the shipped review card, or as `held` on the
ledger — and the `/think` boot inventory **leads with these, by name, before the
normal counts**, so a deferred piece can't hide inside the review queue until you
notice the page never changed.

## 3. The index — one numbered list, statuses not files

The durable answer to "what's outstanding?" is the **ledger**: one
`NNN-<slug>.yml` per spec in the bus (`~/.claude/ledger/`), **born `registered` at
handover** (§4) so it's current the instant handover runs — no orc needed. Render
any time: `.venv/bin/python scripts/spec_ledger.py` (writes the committed mirror);
validate with `--check`.

**Lifecycle (v4.7.0 — `gating` inserted between `building` and `ready`):**
`registered → planned → building → gating → ready → merged → shipped → accepted`,
plus `held`, `bounced`, `rework`, `superseded`, `retired`. Advance a record inline at
the loop point where the transition happens; append (never rewrite) a `history:` entry.

- `planned` — the integrator assigned it (`writes:` footprint + `base_sha` recorded).
- `building` — a builder claimed it (`.assigned`→`.building`); carries `claimed_by`,
  `claimed_at`, `worktree`, `branch`.
- **`gating`** — the builder pushed the branch, flipped `.building`→`.gating`, and freed;
  the detached pane-independent gating-watch grader is checking the pushed branch
  (spec-294 mechanical checks + spec-296 blind two-verdict gate). Carries `gating_at`,
  `ready_sha`. Non-terminal / outstanding; in-flight (its `writes:` footprint stays
  locked). Renders in the mirror between `building` and `ready`.
- **`ready`** — the detached grader passed the branch (`gating`→`ready`); carries
  `graded_by`, `graded_at`. It is **non-terminal / outstanding**
  (∈ `OUTSTANDING_STATUSES`) and renders **distinctly** in the mirror ("Ready to merge —
  awaiting integrator"), ordered after `gating` and before `merged`. A `ready` record
  sitting is build throughput the integrator is wasting — it leads the integrator board.
- `merged` — the integrator speculative-re-checked the branch against current master,
  merged WIP=1 `--no-ff`; carries `shipped_sha`. Still not done (merged-undeployed).
- `shipped` — **delivered to the spec's execution host** (+ `deployed_at`). Delivery is
  per-surface (spec 408), so `shipped` has two honest meanings, both = "actually running
  on its host":
  - **PROD-SURFACE** spec (droplet code — footprint touches `api/**`, `pipelines/**`,
    `agents/**`, `config/**`, or any `scripts/**` path NOT on the orchestration allowlist):
    `shipped` = **verified deploy**. The droplet runs the rsynced copy, so merge ≠ delivery;
    it must be deployed. Multiple merged PROD specs may be **batch-drained** by ONE deploy —
    `deploy.sh --ship-batch "<ids>"` advances them all to `shipped` with a shared
    `shipped_sha`/`deployed_at` (or none, on a failed deploy — never a partial `shipped`).
  - **NON-PROD** spec (orchestration-box only — footprint is PURELY `.claude/**`, `docs/**`,
    or an allowlisted DO-IT machinery script): `shipped` = **merge landed on live master**,
    verified by `deploy.sh --ship-nonprod <id>` (refuses if the merge isn't an ancestor of
    HEAD). This box runs the live `<repo root>` checkout, so the merge IS the delivery
    — no `deploy.sh` rsync. Surface is decided by the **fail-safe classifier**
    `deploy.sh --classify "<footprint>"`; a mixed or unrecognised footprint → PROD-SURFACE
    (**when in doubt, deploy** — never route droplet code around delivery).

**New fields (v4.0.0, all optional / free-form, validated by `--check`):** `writes`
(list of repo-relative paths/globs the spec may modify), `claimed_by`, `claimed_at`,
`worktree`, `branch`, `ready_sha`, `retry_count`.

**Transition ownership — multi-writer safety (must hold; `252-CONTRACT.md` §2):** each
spec is its own `NNN.yml`, so different specs never contend. Every transition has exactly
**one** writer at the instant it happens — the **builder** writes exactly
`building → gating` (+ `gating_at`, `ready_sha`, `card_path`); the **grader
(gating-watch)** writes `gating → ready` (PASS; + `graded_by`, `graded_at`) |
`gating → rework` (FAIL; + `rework_reason`); the **integrator** writes exactly
`ready → merged → shipped` (+ `shipped_sha`, `deployed_at`). All writes are atomic
tmp-then-rename with append-only `history:`. → up to 3 builders + 1 grader + the
integrator advancing *different* rows concurrently can never corrupt the ledger.

**`bounced` vs `rework` — two different rejections, two directions.** Both mean "can't go
forward as-is; returned to sender, loud, with a reason" — but who sent it and who fixes
it differs, so they're two words:
- **`bounced`** = **integrator → human.** The integrator can't assign/build the spec
  (path gone, invariant violated, no testable criteria, fundamentally ambiguous). The
  thinker is gone, so this is a message to *you*; you re-spec or fix. (+ `bounce_reason`,
  `needs`.)
- **`rework`** = **rev / speculative re-check → integrator → the build pool.** A review
  found the shipped card omits spec criteria or its claims don't hold, OR the
  integrator's speculative re-check failed the branch against current master. The work
  isn't accepted. **Per R12 the integrator does NOT rebuild it — it re-assigns the same
  spec to the build lane as a `rework`-flagged `.assigned`** (carrying `rework_reason` +
  the original branch ref) for **any free builder** to claim. Same record, no new number.
  (Builders are stateless — never "back to the builder who built it".)

**Everything is a status, never a separate file.** Every not-done state lives on the
one list, so nothing can rot in a folder no one watches:

| Situation | Status (not a file) |
|-----------|---------------------|
| Handed over, not yet picked up | `registered` |
| Integrator assigned it to the build lane | `planned` (+ `writes`, `base_sha`) |
| A builder claimed + is building it | `building` (+ `claimed_by`, `worktree`, `branch`) |
| Builder pushed — detached grader is checking the branch | `gating` (+ `gating_at`, `ready_sha`) — in-flight, footprint locked |
| Detached grader passed — awaiting the integrator's merge | `ready` (+ `graded_by`, `graded_at`) — loud on the integrator board |
| Integrator can't assign/build the spec → back to the human | `bounced` (+ `bounce_reason`, `needs`) — loud |
| Review / speculative re-check sent it back → re-assigned to the build pool | `rework` (+ `rework_reason`) — loud |
| Deliberately paused | `held` (+ `held_reason`) — loud |
| Replaced by a corrective spec | `superseded` (+ `superseded_by`) |
| Abandoned | `retired` |

**Ironclad tracking (the guarantee):** a handover can't confirm receipt — so pickup
proof is the status *leaving* `registered`. A handed-over-but-unpicked spec stays
`registered` forever and renders loud on the one list. Not-picked-up is impossible
to hide; that — not the drop — is the guarantee.

**Task-list mirror (the dashboard):** the integrator keeps a harness task list at **spec
level** (`registered/planned → pending`, `building/gating/ready/merged/shipped → in_progress`,
`accepted → completed`), **rebuilt from the ledger on every boot**. Display only —
the ledger is the source of truth; never the reverse.

## 4. Handover — the atomic write

Handover is ONE self-verifying action. It either fully lands or errors loudly — no
partial state:

1. read the staged spec from `~/.claude/spec-staging/<slug>-spec.md` (written there by
   the thinker — never from `docs/`);
2. allocate `NNN` and **move** the file to `spec-inbox/NNN-<slug>-spec.md` (write tmp,
   rename, remove staging copy);
3. write the ledger master `ledger/NNN-<slug>.yml` with `spec_id`, `title`,
   `intent`, `status: registered`, `handed_over_at`, `spec_file`, and an opening
   `history:` entry — **directly, no stub**;
4. confirm both exist and are non-empty; on any failure, report the partial state.

No git — handover writes the bus only; **the integrator creates
`docs/do-it/specs/NNN-<slug>-spec.md` on master when it picks the spec up to assign it**
(copy from `spec-inbox/`, committed before recording `base_sha` so the builder's worktree
branches off a base that already carries the spec doc). This is the one place the doc
enters the repo, controlled by the only committer.

## 5. State & archive

File location is state: live in a lane = pending; `_archive/` = done/consumed.
`_archive/` is **append-only — never `rm`**; the archived spec is the frozen
as-handed-over snapshot the close-out grader audits against. Cross-lane lineage is
the `source_brief:` header on a spec (one-way is enough).

## 6. Prime directives

- **Throughput via parallelism.** Run as many parallel **builders** as the footprint
  conflict gate allows (≤3 on the 4GB box); the cap is overlapping footprints, not a
  number. **WIP=1 governs the MERGE, not delivery cadence (spec 408).** The integrator
  merges one `.ready` branch at a time — serially, speculative-re-checked against current
  master, revert-able (WIP=1 on MERGE is preserved exactly, it's the risky step). But
  DEPLOY is **decoupled**: it is a **batch drain** of merged-undeployed PROD-surface specs
  — several may ship on ONE deploy (`deploy.sh --ship-batch`), and **NON-PROD
  (orchestration-box) specs skip `deploy.sh` entirely**, delivered by the merge itself
  (`deploy.sh --ship-nonprod`). Route each spec with the fail-safe classifier
  (`deploy.sh --classify "<writes footprint>"`); **mixed/unrecognised → PROD-SURFACE, when
  in doubt deploy** — never route droplet code around delivery (the shipped-but-not-
  delivered outage class, spec 280 R2 / 278 / 253). A broken batch deploy is revert-able
  via `deploy.sh --rollback`, then git-bisect across the individually re-checked merges;
  keep batches drain-sized so bisect stays tractable.
- **Lean integrator.** The integrator reads **only** the ledger + build-lane files —
  never a build artifact, diff, or worker summary. A bloated integrator reintroduces the
  bottleneck the v4.0.0 split exists to kill. **The integrator never builds, even when
  idle** — idle is the cheaper failure.
- **Prod data-ops leave the integrator.** The integrator NEVER runs a prod data mutation
  (relabel, config repoint, cash re-ingest) inline; it writes an op card and routes to the
  ops lane. operator-ops owns the mutation.
- **Speculative re-check before every merge.** A `.ready` branch is re-proved against
  *current* master (rebase + pre-gates + regression subset + smoke) before the merge
  commit touches master; semantic breakage that merges clean is the #1 silent killer
  (§8 / `252-CONTRACT.md` §5).
- **Stateless return routing.** Builders own no spec across sessions; `rework` and rev
  correctives re-enter via the integrator → build lane → **any** free builder, never a
  named/dead builder (§3, R12).
- **Nothing lost / no silent stalls.** Every not-done state is on the one list;
  every wait is loud and timestamped; bias to act on anything a `git revert` undoes.

## 7. Cross-role nudge (spec 175 — v3.9.0)

`scripts/doit-nudge.sh` is the presence-based cross-role notifier. It runs every minute
via three cron lines (`ROLE=orc`, `ROLE=rev`, `ROLE=builder`) and pokes a live, idle pane
when it has unconsumed inbound work. A fourth deterministic poke (integrator→rev on ship)
fires inline from the ledger, not the cron — see "Ship→rev poke" below.

### Posture lever & presence gate (spec 348) — quiet when a human is driving

Every role pane is *both* the autonomous loop *and* the seat a human takes when driving.
The keystroke-injectors (nudge, relay-watch, heartbeat, orc-idle-watch, ship→rev poke) must
never type into a pane a human is actively using. Two primitives govern this, and **every**
injector routes its send decision through both (`scripts/doit_presence_gate.sh`):

- **One posture lever** — a single canonical token file `~/.claude/doit-notify-posture`
  holding one of:
  - **`off`** — no injector sends any keystroke to any pane, ever. (Durable lane/ledger
    state still updates; the passive badge still renders — `off` means "no keystrokes," not
    "no visibility.")
  - **`autonomous`** — the historical always-on machinery: injectors fire on idle panes and
    ignore human presence (for headless / cron runs).
  - **`auto`** (**the default** when the file is absent, empty, or malformed — fail to the
    presence-safe posture, never to silent-off and never to always-drive) — per-pane presence
    gating: an **attended** pane receives **zero** keystrokes; a genuinely-unattended pane keeps
    today's full machinery. Changing the file changes behaviour on the next tick — no cron edit,
    no restart.

- **One shared presence gate** — in posture `auto`, a pane is **attended** when its transcript
  (resolved via `SESSION_ID`) shows a human turn (a `user` entry carrying a *text* block, not a
  `tool_result`) newer than `ATTENDED_WINDOW` (default 300s; the relay `/clear`+boot — the most
  destructive action — uses a more conservative multiple of that one base). Fallback when no
  transcript resolves: tmux `pane_last_used` recency. **Attended ⇒ the injector sends nothing and
  logs the suppression with the measured human-turn age; the relay reboot never `/clear`s an
  attended pane regardless of context-ceiling state.** A suppressed poke mutates no lane/ledger
  and loses no work (the durable channel is truth; the role re-scans on its own tick).

**Unattended liveness is preserved:** in `auto`, an unattended pane still gets heartbeat revive,
relay-at-ceiling, and nudges — so a genuinely-dead role is never stranded (the 3-day-watcher-gap
class stays closed). `autonomous` behaves exactly as today for all panes.

**Passive attended surface (badge):** a pull-not-push digest of outstanding work (`.ready`
awaiting merge, orc-owed, rework counts) is written to `~/.claude/doit-status` and the tmux
`status-right` on every tick, in **all** postures including `off`. It never sends keys or steals
the input line — the operator glances at it and pulls work on their own rhythm.

### Roles and standing panes

| Role | Standing pane | Poke target |
|------|---------------|-------------|
| integrator (`/orc`) | `/tmp/orc-active` | spec-inbox, corrective-inbox, integrator-routed memos, **`.ready` lane files** |
| rev  | `/tmp/rev-active` | ledger `Awaiting prod-verification` set + rev-routed memos |
| builder (×N) | `/tmp/builder-<id>-active` (one per pane) | dispatchable `.assigned` work, free of footprint conflict |
| think | none (human-initiated) | never poked; memos with `to: think` wait |

> **Builder pane file is `/tmp/orc-active`'s sibling** — the integrator's pane file is
> unchanged (`/tmp/orc-active`, the alias) so the existing `ROLE=orc` cron line keeps
> working untouched. The `ROLE=builder` nudge branch (poke idle builder panes when
> conflict-free, unclaimed `.assigned` work waits, with per-pane relay-collision guard)
> is **IMPLEMENTED** (spec 278): `ROLE=builder` loops every `/tmp/builder-<id>-active`
> pane and pokes an idle one only when an unclaimed `*.assigned.md` exists whose `writes:`
> footprint does NOT overlap any current `.building` footprint (else it logs
> `skip-on-conflict` and waits). Each builder pane has its **own** relay baton
> `docs/sessions/builder-<id>-relay.md` and its own `/tmp/builder-<id>-active` file; a
> builder relay is a planned handoff that preserves the `.building` claim + worktree +
> branch (so the successor resumes the *same* spec — a relay is not a death).

### The `📨 DO-IT nudge:` marker

When the nudge pokes a pane it types a single line:

```
📨 DO-IT nudge: N waiting — <names>. Run your inbox scan before continuing.
```

On seeing this marker, the role runs its existing boot-step inbox scan immediately,
then resumes. **No `/clear`, no reboot.** The marker is defined in each role's
`SKILL.md` (`~/.claude/skills/{orc,rev,builder}/SKILL.md`).

### `to:` field — memo routing

`memo-*.md` files carry an optional `to:` header that routes to the named role's pane.

| File type | Default `to:` | Who sees it |
|-----------|---------------|-------------|
| `spec-inbox/memo-*.md` | `orc` | orc (unless overridden) |
| `brief-inbox/memo-*.md` | `think` | nobody poked (think is human-initiated) |
| any memo with `to: rev` | explicit | rev only |
| any memo with `to: orc` | explicit | orc only |
| any memo with `to: think` | explicit | nobody poked; waits in inbox |

### Integrator `.ready` poke (spec 278 R1)

`ROLE=orc` enumerates `~/.claude/build-lane/*.ready.md` as outstanding artifacts and pokes
the integrator pane the same way it pokes for spec-inbox / corrective work — subject to the
same backoff / 3-poke-cap / relay-collision guards. **Consumed** = the `.ready.md` leaves the
lane (merged → archived) OR its ledger row leaves `ready`. Identity is `filename+mtime` (a
re-flip = new mtime = re-pokes). This is the gap that silently stalled the loop on 2026-06-29
(finished `.ready` branches sat unmerged because nothing re-poked the idle integrator).

### Ship→rev poke (spec 278 R3)

When a spec is advanced to `shipped` (`scripts/spec_ledger.py set <id> shipped`), the ledger
fires `scripts/poke_rev_on_ship.sh <id>` as a best-effort side-effect — a **deterministic**
integrator→rev poke that doesn't wait for rev's own cron tick. It fires only on a real
transition INTO `shipped` (`prev != shipped`), so a reworked spec (`rework → … → shipped`)
re-pokes and a stale prior verdict can't suppress it, while a no-op `set shipped` over an
already-shipped row does not. It honors the rev relay-collision guard and `NUDGE_DRY`, and
pokes only when `/tmp/rev-active` is live. **It also gates on the spec existing in the real
ledger** (`~/.claude/ledger/<id>.yml`, decoupled from any `DOIT_LEDGER_DIR` override): a
live-pane poke is a real-world side effect, so a fixture/test ship into a tmp ledger never
resolves to a keystroke on the live rev pane (corrective-278). A poke failure never affects
the ledger write.

### Consume / escalate contract

- **Consumed** = artifact `mv`d to `_archive/` (spec/corrective) or ledger row leaves
  `Awaiting prod-verification`. No separate ack store — presence is truth.
- **Re-poke backoff:** at most once per `NUDGE_BACKOFF_SECS` (default 600s) per artifact
  identity (filename+mtime). A busy role is reminded but not spammed.
- **3-poke cap:** after `NUDGE_MAX_POKES` (default 3) un-consumed pokes for the same
  artifact, a `{ROLE}_RELAY_STALL`-style alert is emitted and poking stops. Resume only
  when the artifact changes identity (new mtime) or is consumed.
- **Relay-collision guard:** if a fresh `HANDED-OFF` baton (< `BATON_FRESH_SECS = 90m`)
  is present for this role, the nudge defers for that tick — the relay reboot's own boot
  scan surfaces the inbox. Never both send keys in the same tick.

### Durable state is truth — pokes are a latency optimization (spec 287)

**The authoritative cross-role state is the durable files** — the build lane
(`.assigned`/`.building`/`.ready`), the ledger (`~/.claude/ledger/*.yml`, atomic +
append-only history), and the spec/brief/corrective inboxes. A pane-poke (the typed
`📨 DO-IT nudge:` / ship-poke / relay) is **only a latency optimization** that tells a
role to run its scan *now* instead of on its next tick. It is **not a queue and not a
channel of record.**

The consequence is a hard bound on what a dropped poke can cost: a role re-evaluates
its durable inbox/lane/ledger every cron tick **and at the top of every turn** (the
per-turn scan backstop — orc and rev both do this), so a missed poke costs **≤ 1 tick +
backoff of latency, never lost work.** Because of this:

- **A failed poke must never be counted as a delivered one.** Every pane-poker bumps its
  poke/backoff counter (and the 3-poke stall cap) **only on a verified submit**; a failed
  send leaves the counter untouched and is retried next tick (spec 287 R1). Counting a
  transport failure as a delivery would burn the retry budget and fake a stall on a poke
  that never landed.
- **The one poke whose failure actually hurts — the relay `/clear`+boot — is verified.**
  `orc-relay-watch.sh` only writes the consume-once marker after the submit is confirmed
  landed; a failed relay is logged + alerted and retried, never silently dropped (spec 287
  R2). An unverified relay is the silent context-ceiling-overrun path.
- **Do NOT "fix" a flaky poke by building a redundant queued channel.** The durable lane/
  inbox/ledger already *is* that queue. The correct fix for transport flakiness is the
  scan backstop + honest accounting above — not a second source of truth to keep in sync.

### Env overrides

| Variable | Default | Meaning |
|----------|---------|---------|
| `NUDGE_DRY=1` or `ROLE_WATCH_DRY=1` | 0 | log decisions + what would be typed; send no keys |
| `ORC_QUIET_SECS` | 45 | transcript silence before poke |
| `NUDGE_BACKOFF_SECS` | 600 | min seconds between re-pokes of same artifact |
| `NUDGE_MAX_POKES` | 3 | pokes before stall alert + stop |
| `BATON_FRESH_SECS` | 5400 | relay baton freshness (must match relay-watch setting) |
| `DOIT_POSTURE_FILE` | `~/.claude/doit-notify-posture` | canonical posture token (`off`\|`autonomous`\|`auto`; absent/malformed⇒`auto`) — the one lever (spec 348) |
| `ATTENDED_WINDOW` | 300 | seconds of human-turn recency that marks a pane attended (relay uses a conservative multiple) |
| `DOIT_STATUS_FILE` | `~/.claude/doit-status` | passive badge digest target (also mirrored to tmux `status-right`) |

### Crontab lines (orc installs after verifying)

```cron
* * * * * ROLE=orc <repo root>/scripts/doit-nudge.sh >> /tmp/orc-nudge.log 2>&1
* * * * * ROLE=rev <repo root>/scripts/doit-nudge.sh >> /tmp/rev-nudge.log 2>&1
* * * * * ROLE=builder <repo root>/scripts/doit-nudge.sh >> /tmp/builder-nudge.log 2>&1
```

(The `ROLE=builder` line loops every `/tmp/builder-<id>-active` pane in one tick; no
per-pane cron entry is needed. The ship→rev poke needs no cron line — it fires inline from
`spec_ledger.py`.)

## 8. Standing-role reliability — heartbeat + worktree reaper (spec 256 — v3.10.0; wrapped by the 252 git-janitor in v4.0.0)

Two machine guarantees keep standing roles alive and the worktree list tractable.
**The 252 git-janitor (`scripts/git_janitor.sh`) WRAPS these — it does NOT duplicate
them.** The integrator (the accountable git-tree custodian, R11) invokes the janitor,
which *calls* `scripts/worktree-reaper.sh` and relies on the existing heartbeat; the
janitor only ADDS the merge-lock-guarded gc cadence, disk-watch, primary-checkout
integrity assertion, and the recovery runbook around them. Re-implementing the reaper or
the heartbeat in the janitor is forbidden.

### Liveness heartbeat (R1)

`scripts/standing-role-heartbeat.sh` revives a standing role that has gone quiet
below the context ceiling — the 2026-06-25→28 watcher gap (3 days, 146 commits +
specs 236–249 unwatched) is the proof an in-session `sleep` re-arm cannot deliver
this guarantee.

Fires for a given role when ALL hold: (a) `/tmp/<role>-active` exists + points at a
live pane; (b) the pane's transcript is stale > `HEARTBEAT_THRESHOLD` (default 60 min);
(c) no fresh `HANDED-OFF` baton is present (relay-watch owns that case). On fire: types
`/<role>` into the pane. Does NOT `/clear` — the relay protocol sends `/clear` at the
context ceiling; the heartbeat just reboots the sweep in the existing session.

Poke deduplication mirrors `orc-idle-watch.sh`: up to `HEARTBEAT_MAX_POKES` (default 3)
pokes per incident, `HEARTBEAT_BACKOFF_SECS` (default 30 min) between pokes, then
`{ROLE^^}_HEARTBEAT_STALL` liveness flag raised and poking stops.

| Role | Active file | Heartbeat log | Status |
|------|-------------|---------------|--------|
| watcher | `/tmp/watcher-active` | `/tmp/watcher-heartbeat.log` | **enabled** |
| rev | `/tmp/rev-active` | `/tmp/rev-heartbeat.log` | opt-in (uncomment cron line) |

Cron line (every 30 min):
```cron
*/30 * * * * root ROLE=watcher <repo root>/scripts/standing-role-heartbeat.sh >> /tmp/watcher-heartbeat.log 2>&1
```

Install: `sudo bash deploy/cron/install_standing_role_heartbeat_cron.sh`

Dry-run test:
```bash
DRY=1 ROLE=watcher <repo root>/scripts/standing-role-heartbeat.sh
tail -20 /tmp/watcher-heartbeat.log
```

**Watcher cadence note:** the watcher SKILL.md instructs the watcher NOT to self-arm
a `sleep`-based re-poke — the heartbeat cron owns liveness. A watcher that wrote a
self-arm before this cron was installed must remove that arm to avoid a double-poke;
the existing nudge backoff / relay-collision guards make the overlap harmless if it
occurs while the transition is in flight.

**Builder heartbeat (spec 279 R2):** builders are also covered — `ROLE=builder` makes
`standing-role-heartbeat.sh` loop every `/tmp/builder-*-active` pane (multi-pane), so
all N parallel builder panes are revived by a single cron entry. Install:
`sudo bash deploy/cron/install_standing_role_heartbeat_cron.sh` (the builder line is
already in `deploy/cron/standing_role_heartbeat.cron`).

### Builder id allocation (spec 279 R3)

**The single canonical scheme:** a builder's id is the tmux pane number extracted from
`$TMUX_PANE`. The pane variable has the form `%13`; stripping the leading `%` yields
the bare id `13`. The script `scripts/builder-id.sh` encapsulates this — every
role-skill and installer that needs a builder id calls that script.

Full identifiers derived from id `N`:

| Artifact | Path |
|----------|------|
| Active file | `/tmp/builder-N-active` |
| Relay baton | `docs/sessions/builder-N-relay.md` |
| `claimed_by` field | `builder-N` |

**Why this scheme is collision-free:** tmux pane ids are unique per tmux server — two
live panes can never share the same `%N`. A builder that reboots inside the same pane
gets the same id, so it picks up its own active file and relay baton without clobbering
a different live builder. Re-boot stability and collision-freedom are both structural
guarantees, not conventions.

**Prior ad-hoc scheme retired:** the mixed `15` / `b1` / `uuid` naming used in earlier
sessions is superseded by this scheme. Use `scripts/builder-id.sh`; never assign a
builder id by hand.

### Worktree reaper (R2)

`scripts/worktree-reaper.sh` removes worktrees whose branch is **fully merged into
master AND the tree is clean AND untouched > N days**, then deletes the merged local
branch and runs `git worktree prune`. Motivation: 114 worktrees accumulated
(2026-06-28 audit); `git worktree prune` reclaimed zero (directories still existed).

Safety invariants (NEVER violated):
- Never touches a worktree with uncommitted or untracked changes.
- Never touches an unmerged branch.
- Never touches the main worktree or `/tmp/` paths.
- Refuses to run if `.git/MERGE_HEAD` exists (active integration in progress).

**252 R11 cross-reference (v4.0.0):** this reaper owns the worktree+branch cleanup
sub-task of 252's git-janitor. The 252 janitor (`scripts/git_janitor.sh`) **invokes this
script** rather than re-implementing the reaper; the janitor retains the gc cadence,
disk-watch, primary-checkout integrity assertion, custody charter, and recovery runbook.
The integrator is the accountable custodian (its skill carries the custody checklist +
recovery runbook). Branch naming is now `feat/NNN-<slug>`; the reaper's branch glob is
reconciled to `feat/NNN-*` in Phase 1c (the janitor build).

Cron line (daily at 02:15 UTC):
```cron
15 2 * * * root flock -n /var/lock/doit-worktree-reaper.lock bash -c "cd <repo root> && <repo root>/scripts/worktree-reaper.sh >> /tmp/worktree-reaper.log 2>&1"
```

Install: `sudo bash deploy/cron/install_worktree_reaper_cron.sh`

Dry-run test:
```bash
<repo root>/scripts/worktree-reaper.sh --dry-run
tail -30 /tmp/worktree-reaper.log
```

Report format (last line of the log after each run):
```
<timestamp> worktree-reaper: reaped=N skipped=M root=<path> stale_days=7 branches=[feat/spec-124 feat/spec-137 ...]
```

## 9. Evolving DO-IT (the self-hosting ritual)

When you propose a pipeline change: **read this file** (the rule now) → **read
`DESIGN.md`** (why it's this way, what was rejected) → **change this file, append a
dated decision to `DESIGN.md`, and add a `CHANGELOG.md` entry + bump the version line
above** (semver: new capability → minor, fix/clarification → patch, breaking
role/bus/naming change → major). Never silently. The pipeline evolves the way you work.

## 10. Why / decisions

The rationale, trade-offs, and dated decision log live in **`DESIGN.md`** (same
folder). This file is the *what*; that one is the *why*.
