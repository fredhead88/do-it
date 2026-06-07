# v3.0.0 — reconcile the public repo to the running instance

**Status:** Plan, awaiting go · **Type:** breaking release (v2.3.0 → v3.0.0)
**Goal:** Bring `github.com/fredhead88/do-it` up to the Albert Scott running instance,
which has diverged ahead. After this, the public product == what's actually run.

## Why breaking

The public repo is an earlier architecture. Three load-bearing changes are breaking:
the ledger moves out of the repo to the bus; the `.register.yml`/`.accept.yml` stub
files are replaced by the `register`/`set` helper; and `planner` + the blockers
subsystem are removed. Anyone on v2.x must re-run `setup.sh` and stop hand-dropping
stubs — hence the major bump.

## Source of truth

The AS canonical copies: `/opt/albert-scott/docs/do-it/{DO-IT.md,DESIGN.md,CHANGELOG.md}`,
`~/.claude/skills/{think,orc,spec-handover}`, `/opt/albert-scott/scripts/spec_ledger.py`.
The port = copy these in, then **genericize** (the delicate part).

## Genericization rules (strip every Albert-Scott specific)

- `/opt/albert-scott` → `$REPO_ROOT` (the DO-IT.md CONFIG block).
- `deploy.sh` / droplet IP / Vercel / `bluedot-webhook` → `$DEPLOY_CMD` + generic wording.
- "the Albert Scott repo" in skill descriptions → "your repo" (fixes the current leak
  in the published `orc` skill).
- AS people / INTENT specifics / client names → generic examples.
- Keep as-is (already generic): the bus paths `~/.claude/{ledger,spec-inbox,brief-inbox}`.

## Changes, file by file

1. **`scripts/spec_ledger.py`** — helper already added (this session, uncommitted).
   Still to do: default `LEDGER_DIR` → `~/.claude/ledger` (bus); write the generated
   mirror to an in-repo path; **remove the blockers subsystem** (`load_blockers`,
   `deploy_blocked_by`, the blocker render section + validate checks).
2. **`DO-IT.md`** — replace with the AS protocol (genericized): bus + generated mirror,
   `rework`/`bounced` split, no-quiet-descope bar, deferrals-surface-first, the helper
   as the write mechanism, the self-hosting §7 with CHANGELOG/version bump. Drop
   planner + blockers + stub-fold language. Keep the CONFIG block with placeholders.
3. **`docs/DESIGN.md`** — port the AS decision log (genericized); add the v3.0.0 entry.
4. **`skills/think/SKILL.md`** — AS version: boot inventory leads with deferrals;
   review walk writes via `spec_ledger.py set` (no `.accept.yml`); intake/triage shape
   absorbs the planner.
5. **`skills/orc/SKILL.md`** — AS version: fix the "Albert Scott" leak; ledger advances
   via the helper; `bounced` vs `rework`; close-out gate (already present).
6. **`skills/spec-handover/SKILL.md`** — rename from `handover/`; register via the
   helper (no `.register.yml` stub).
7. **`skills/planner/`** — **delete** (folded into `think` as intake/triage).
8. **`setup.sh`** — skill list → `think, orc, spec-handover` (drop `planner`, rename
   `handover`); create the bus inboxes + `~/.claude/ledger`; keep `aaaudit` if desired.
9. **`README.md`** — rewrite to the 3-role bus model; drop the planner stage, the stub
   model, and the blockers pillar; update the layout tree and quickstart.
10. **`CHANGELOG.md`** — `## [3.0.0]` entry: breaking (bus ledger, helper replaces
    stubs, planner + blockers removed), plus the additive close-out/review items.
11. **Release** — commit (conventional, scoped), `git tag v3.0.0`, `git push && git
    push --tags`. **Outward-facing public release → I confirm with you before this step.**

## Verification before release

- `python scripts/spec_ledger.py --check` green on a temp `DOIT_LEDGER_DIR`.
- `register` + `set` round-trip (incl. refusal cases) pass.
- `setup.sh` dry sanity: links the three skills, no `planner`, creates the bus dirs.
- grep the repo for `albert`/`/opt/` → zero hits (genericization complete).
