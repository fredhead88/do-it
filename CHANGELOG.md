# Changelog

All notable changes to DO-IT are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry links to the dated design doc in `docs/` that holds the *why*; this file
is the terse *what*. Tags mark the commit each version shipped at, so
`git checkout v1.0.0` gets you that release.

## [3.2.2] — 2026-06-08

### Fixed
- **Number-allocation date-stem bug (recurring).** The hand-rolled allocator
  (`grep -oP '^\d{3}'` then max+1) matched the first three digits of *any*
  string, so grandfathered date-stem files (`2026-05-31-...`) read as "202" and
  allocated ~203 — and once a bad `203-` file existed it became the new max and
  poisoned every future allocation (it had already bitten the brief allocator:
  brief `203-fc-level-inventory-tracking`, which leaked `source_brief: 203` into
  a spec). Fix: `grep -oP '^[0-9]{3}(?=-)'` — the `(?=-)` lookahead requires a
  hyphen right after the three digits, so the year no longer matches. The
  `spec-handover` and `think` skills now carry the exact command, scan every bus
  dir (briefs + specs share one number space), and refuse an allocation ≥150.
  The naming doctrine in `DO-IT.md` §2 records the gotcha.

## [3.2.1] — 2026-06-07

### Fixed
- **Manual-restart race:** if a handoff sentinel was pending and the user
  manually `/clear` + `/orc`'d before the watcher's next tick, the watcher
  would restart the pane again, wiping the fresh session. The orc skill's
  arming step now clears stale sentinels for its own pane at boot (the
  automated path always deletes the sentinel *before* restarting, so any
  sentinel surviving into a new orc's boot is by definition stale).

## [3.2.0] — 2026-06-07

Adds **relay-watch** — the automated orc baton loop. The last manual step in
DO-IT ("hand over the baton", `/clear`, `/orc`) now runs itself.

### Added
- **`relay-watch/` component** — two halves across the session boundary:
  `orc-token-watch.py` (a PostToolUse hook; reads the exact live context size
  from the session transcript's `usage` blocks and past the threshold injects
  a write-the-baton-and-STOP signal) and `relay-watch.sh` (a per-minute cron;
  once the baton reads `HANDED-OFF`, the transcript is quiet, and the pane is
  alive, it sends `/clear` + `/orc` via tmux). Project-agnostic: the sentinel
  carries the repo path, so one cron line serves every DO-IT repo. Threshold
  via `ORC_WATCH_THRESHOLD` (default 400k for 1M windows; ~160k on 200k
  windows beats auto-compaction). See `relay-watch/SETUP.md`.

### Changed
- **`skills/orc/SKILL.md`** — new First-moves step 0 arms the context watch
  (`/tmp/orc-active`, pane-scoped so thinkers and other sessions are never
  touched); the ORC CONTEXT WATCH message is an official relay signal; on that
  signal the orc stops after writing the baton instead of asking the user to
  restart (the watcher does it).

## [3.1.0] — 2026-06-07

Adds the **verification-loop harness** — a project-agnostic autonomous prod verifier
that closes the gap between "orc says done" and "observed green on the running app."

### Added
- **`verification-loop/` harness** — Node.js + Playwright headless browser runner.
  Detects new deploys via sha comparison, probes configured pages, assigns typed
  evidence to each acceptance criterion, and judges cross-vendor (Codex primary,
  Claude fallback). All project-specific values live in a single config file;
  the harness itself has no hardcodes. See `verification-loop/SETUP.md` and
  `verification-loop/config/README.md`.
- **`skills/verification-loop/SKILL.md`** — the verifier skill. Documents the 8-step
  tick, verdict taxonomy, durable state files, and cron/attended usage modes.
- **Verifier-owned `verified/` namespace** in `spec_ledger.py` — verdicts written by
  `spec_ledger.py verify` land in `LEDGER_DIR/verified/<spec_id>.yml`, a subdir the
  builder's `set`/`register` commands never glob. The verifier is the only writer.
- **`spec_ledger.py verify` subcommand** — `verify NNN-slug CONFIRMED --judge codex
  --evidence <ref>` records a verdict with judge identity and evidence reference.
  Invalid verdict tokens are rejected.
- **Advisory flock (`fcntl.flock`)** on every record write — prevents lost updates
  when concurrent tick processes write the same ledger file.
- **`render()` derives done-ness from the verdict namespace** — shipped specs with a
  CONFIRMED verdict render as verified; REJECTED verdicts surface visibly as failed.
- **`observable_warnings()` heuristic in `--check`** — flags presence-phrased
  acceptance criteria (e.g. "a button exists") that the verification loop cannot
  observe. Soft warning only; does not block the check.
- **Evidence-bound close-out gate** in `DO-IT.md` §2 — `shipped` requires type-matched
  observed evidence per criterion (`screenshot+interaction_trace` for UI,
  `curl_status+body_excerpt` for backend). Deterministic pre-gates run before any LLM
  judging; the grader sub-session stays build-blind.

## [3.0.0] — 2026-06-07

**Breaking.** Reconciles the public repo with the running instance it was extracted
from, which had moved ahead. Three load-bearing changes mean v2.x users must re-run
`setup.sh` and stop hand-dropping ledger stub files. Why:
`docs/2026-06-07-v3-reconcile-plan.md`.

### Changed (breaking)
- **The ledger moved to the bus.** Build-status masters now live at
  `~/.claude/ledger/` (machine-global, reachable from any worktree); the repo holds a
  *generated* committed mirror (`docs/do-it/ledger/OUTSTANDING.md`). Previously the
  ledger lived in-repo under `docs/superpowers/ledger/`.
- **A write helper replaces the stub-file dance.** `spec_ledger.py register` (birth)
  and `set` (every transition) are now the only supported way to write a record — each
  re-validates before writing, so malformed or incomplete records can't be born. This
  removes the `.register.yml` / `.accept.yml` stub files that the orchestrator used to
  fold in. (Hand-edited YAML was the source of mixed-indent / missing-field corruption.)
- **`handover` → `spec-handover`.** The skill was renamed; re-run `setup.sh`.

### Removed (breaking)
- **`planner` skill** — folded into `think` as its intake/triage shape.
- **The deploy-blocker subsystem** — replaced by the `held` status + a reason.

### Added
- **`bounced` vs `rework` return paths** as first-class statuses, each with one
  direction and one reader (`bounced` = won't-build → human; `rework` = shipped card
  sent back → orc), enforced by the renderer.
- **No quiet descope** — the blind close-out grader challenges weak `not-done`s
  (forces them built in-session); only spec-out-of-scope or genuinely-loud
  (human-question / `held`) survive.
- **Deferrals surface first** in the `/think` boot inventory, so a shipped-but-partial
  spec can't hide in the review queue.

## [2.3.0] — 2026-06-04

Sharper **orc close-out discipline**. Minor bump: additive to the `orc` skill only;
no existing surface removed. Hardens the quality gate and the return-path handling
that a high-volume session leans on.

### Added
- **Close-out gate (blind, two verdicts)** — every shipped spec gets a fresh blind
  grader (never saw the build) returning a plain *matches-intent: yes/no/partial*
  plus an INTENT.md-invariant check, before the spec is closed. On anything short of
  "yes" the orc surfaces it loudly and fix-forwards rather than closing.
- **Review card as a complete spec mirror** — the card written for the `/think`
  acceptance walk now accounts for every component of the spec (intent verbatim,
  what shipped, look-at URLs, eyeball questions, grader verdict, findings/caveats),
  so acceptance is a checklist, not a re-derivation.
- **`bounced` vs `rework` return paths** — explicit handling for work that comes
  back: `bounced` (won't-build, → human) vs `rework` (shipped-but-corrective, → orc),
  defined against DO-IT.md §3, so review-walk correctives re-enter the build lane
  cleanly without losing the original ship's history.

## [2.2.0] — 2026-06-03

A durable **build-status ledger** that closes the seam between "handed over" and
"shipped." Minor bump: additive across `handover` / `orc` / `think` + a new portable
script; no existing surface removed. Why: `docs/2026-06-03-doit-build-status-ledger-design.md`.

### Added
- **Per-spec build-status records** at `LEDGER_DIR/<spec_id>.yml` — one file per spec
  carrying a lifecycle `status` (registered → planned → building → merged → shipped →
  accepted, plus held / superseded) and an append-only `history`. State stays
  per-file (no manifest); the rollup is rendered, so it can't drift.
- **`scripts/spec_ledger.py`** — renders `LEDGER_DIR/OUTSTANDING.md` (three read
  buckets, deploy-blocker rollup, stale-merged tripwire) and `--check` validates
  records. `DOIT_LEDGER_DIR` env override points it at any project's ledger.
- **Shared deploy-blockers** at `LEDGER_DIR/blockers/<id>.yml`, referenced by specs
  via `deploy_blocked_by` — one object per infra failure, cleared in one edit.
- **Register / accept inbox stubs** (`*.register.yml`, `*.accept.yml`) so the
  no-git `handover` and `think` sessions auto-register and accept specs; only the
  singleton `orc` writes the committed ledger (one writer per record, no clobbering).
- **`LEDGER_DIR`** added to the CONFIG block.

### Changed
- **`handover`** writes a register stub at handover; **`orc`** ingests stubs, advances
  status at each loop point, handles deploy-blockers, and carries a mandatory `LEDGER:`
  board line + close-out gate (`merged` never reads as `shipped` — shipped requires a
  verified deploy); **`think`** writes an accept stub on a happy review walk.

## [2.1.0] — 2026-06-02

Three refinements from live use of the v2 pipeline. No bootable-skill surface change
(minor bump); `collect-inbox` lane retired.

### Changed
- **Collect is now session-scoped — no persistent pile, no lane.** Collect stays a
  distinct `think` shape (low-touch capture across many small items, with the
  thinking deferred to one synthesis pass that emits a single comprehensive spec),
  but it now runs and finishes inside the one session it starts in. The running list
  is an in-session working doc, not a `*.collecting.md` inbox file. **Why:** the only
  thing cross-session persistence bought was surviving a mid-collect crash, and it
  cost a whole file lifecycle to keep "honest" — net negative for one human on one
  machine. This supersedes the 2.0.0 persistent pile *and* the per-item "discharge"
  bookkeeping that briefly existed on the way here (both removed). If a session dies
  mid-collect the jots are lost — the accepted trade.
- **Specs ship with questions resolved — no "open questions" section.** Resolving
  questions is what a thinking session is *for*, so the spec artifact no longer
  carries built-in open questions; a genuinely-open question means keep thinking, or
  put the fork to the user now and fold in the answer. (The orchestrator may still
  raise *new* questions later from its broader, code-level view — that's expected.)
  Removed the "Open questions" item from the spec structure and the readiness
  self-check in `think`.
- **Orc relay triggers on observable signals, not a guessed context %.** The relay
  baton no longer keys on "~50% / ~70% used" — an orchestrator can't actually read
  its own context fraction, so that threshold degraded into a vibe that biased toward
  premature (expensive) handoffs. New rule: default posture is **keep working and
  checkpoint the ledger as you go**; relay only on a real signal — an autocompact /
  context-limit warning, repeated tool failures, visibly degraded output, or an
  explicit user cue — and **do not self-estimate context fraction.**

### Removed
- The **`collect-inbox`** lane, the `*.collecting.md` message type, the collect
  counter, and the discharge-map / `status: discharged` machinery. `setup.sh` no
  longer creates a collect inbox.

## [2.0.0] — 2026-06-02

Consolidated the pipeline around **three bootable sessions** (`planner` → `think`
→ `orc`) and closed two gaps the original left open. Major bump: the skill surface
changed incompatibly (skills removed, `think`'s role redefined).
Design: [`docs/2026-06-02-doit-v2-design.md`](docs/2026-06-02-doit-v2-design.md).

### Added
- **`think` shapes** — `think` is now the stage-2 worker seat with four shapes
  (brainstorm / review / claim-a-brief / collect) plus two outbound handoffs it
  performs itself (hand over a spec via the `handover` helper; send a memo).
- **Review loop** — `orc` writes a human-readable review card per shipped spec into
  the thinker's lane; `think` review mode walks them (happy → archive, unhappy →
  corrective spec back to `orc`).
- **Collect mode** — a persistent cross-session pile (`collect-inbox`) batched into
  one spec on `collect done`.
- **Orchestrator relay** — at the context checkpoint `orc` writes a baton
  (`docs/sessions/orc-relay.md`) the next `orc` reconciles against the tree;
  `HANDED-OFF`→`RESUMED` handshake holds the singleton across the seam.
- **Pull-on-boot hardening** — reading sessions re-scan their lane every turn;
  memos are acknowledged on read and archived once folded in.

### Changed
- Reading transfer is documented honestly as **pull-on-boot, not event-driven**.
- Memo archival is **decoupled from any spec** (archived when its guidance is
  folded in, not when a spec closes); memo lane now depends on its reader
  (`orc` → `spec-inbox`, `planner` → `brief-inbox`).

### Removed
- The standalone **`collect`** and **`memo`** skills — folded into `think` as a
  shape and an action, respectively.
- The **`drop`** skill — its action is now "send a memo" inside `think`.
- Generic `ginug`-style handoff for orchestrator continuity, replaced by the relay.

## [1.0.0] — 2026-05-31

Initial DO-IT pipeline: move work from raw idea → spec → shipped feature across
separate, one-shot Claude Code sessions passing typed messages through a shared
filesystem inbox. Design: [`docs/DESIGN.md`](docs/DESIGN.md).

### Added
- Core three skills: **`think`** (brainstorm → spec), **`handover`** (drop the spec
  into the inbox), **`orc`** (plan → fan out → blind grade → integrate → ship).
- Advanced add-ons: **`planner`** (triage a dump into briefs) and **`drop`**
  (advisory memo).
- The shared protocol (`DO-IT.md`): two lanes, message types, intent + blind
  grader, no-silent-stalls rules, claimed-brief tracking.
- `setup.sh` (creates inboxes, links skills, checks CONFIG) and `docs/DESIGN.md`.

[2.0.0]: https://github.com/fredhead88/do-it/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/fredhead88/do-it/releases/tag/v1.0.0
