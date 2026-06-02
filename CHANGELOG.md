# Changelog

All notable changes to DO-IT are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry links to the dated design doc in `docs/` that holds the *why*; this file
is the terse *what*. Tags mark the commit each version shipped at, so
`git checkout v1.0.0` gets you that release.

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
