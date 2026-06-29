# DO-IT — Pipeline Operating Protocol

**Version:** 4.0.0 · history: `CHANGELOG.md` · rationale: `DESIGN.md`

The single source of truth for how the spec pipeline works. Every role-skill
(`think`, `spec-handover`, `builder`, `integrator(orc)`) reads this and obeys it — they
do **not** restate its rules. This is the *what* (always current); the *why* + decision
log is `DESIGN.md`. When you change the pipeline, follow §8.

> **v4.0.0 — parallel builders + lean integrator.** The old singleton `orc` role split
> in two: **builders** (N parallel sessions, each building one spec to a `ready` branch
> in its own worktree and self-running the close-out gate) feed one lean **integrator**
> (the revised orc — singleton; reads only the ledger + lane files, speculative-re-checks
> each ready branch against current master, merges WIP=1, deploys one at a time, owns
> git-tree custodianship). `/orc` is preserved as an **alias** for the integrator.

> **This is a template.** Fill in every `<…>` in §0 CONFIG below for your project,
> then delete this line. The CONFIG table is the ONLY thing you adapt — the rest of
> the protocol and all skills are project-agnostic and read their specifics
> from this table. See `README.md` for setup.

---

## 0. CONFIG — the one adapter (fill this in)

Every project-specific path or command lives here. The skills reference these keys
by name ("the Intent doc (CONFIG)"), never literals. A `—` means "this project
doesn't have one" and the skills skip the corresponding step.

| Key | Value |
|-----|-------|
| Repo root | `<absolute path to your repo, e.g. /home/you/myrepo>` |
| Bus root | `.do-it/` (relative to repo root — the default; keep unless you must move it) |
| Spec lane | `.do-it/spec-inbox/` |
| Brief lane | `.do-it/brief-inbox/` |
| Ledger (master) | `.do-it/ledger/` |
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
env vars if you moved either: `DOIT_LEDGER_DIR` (default `.do-it/ledger/`) and
`DOIT_MIRROR_DIR` (default `docs/do-it/ledger/`). Run it from the repo root.

**`.do-it/` is gitignored.** Add `.do-it/` to your `.gitignore`. The bus is working
state, not code — keeping it untracked is what lets a read-only `think` session write
to it without "touching code." Only the rendered mirror (under your docs tree) is
committed.

## 1. The map

```
dump ─▶ think ─spec─▶ handover ─▶ spec-inbox + ledger ─▶ orc ─plan─▶ fan out ─▶ integrate ─▶ deploy
        (intake/triage, brainstorm, review)                         (singleton; only committer)
```

- **think** — read-only on code. Discovery/brainstorm → spec; review of shipped
  work; **intake/triage** of a dump. Reads `brief-inbox`, writes specs + briefs +
  memos. Safe to run several at once.
- **handover** (`spec-handover`) — the atomic, self-verifying drop of a finished spec
  into the bus + the ledger (§4). Writes `spec-inbox` + `ledger` only.
- **orc** — the singleton integrator. The ONLY session that owns the working tree,
  commits, and deploys. Reads everything; advances the ledger; renders the mirror.

## 2. The message bus

Two lanes (by audience) + the ledger, all under `Bus root` (CONFIG). State **is** file
location — no manifest.

| Type | File | Author | Reader |
|------|------|--------|--------|
| Brief (lightweight) | `NNN-<slug>.brief.md` | think | think |
| Claimed brief | `NNN-<slug>.brief.claimed.md` | think | think |
| Spec | `NNN-<slug>-spec.md` | handover | orc |
| Memo (advisory, never a work item) | `memo-<topic>.md` | think | orc |
| Review card | `<slug>.review.md` | orc | think |
| Ledger record (master) | `ledger/NNN-<slug>.yml` | handover→orc→think | all |
| Relay baton | `Relay baton` (CONFIG) | orc | orc |

**Naming — one rule, no exceptions:** `NNN-<slug>` — numbered, hyphens, **never a
dot before the type** (`-spec.md`, never `.spec.md`; the inbox glob is `*-spec.md`,
so a dotted name is silently never seen). Allocate `NNN = max(live + _archive) + 1`,
zero-padded to 3.

**Atomic drop:** write `<name>.tmp` in the target dir, then rename into place; on a
name collision the loser retries `NNN+1`. Readers ignore `*.tmp`.

## 3. The index — one numbered list, statuses not files

The durable answer to "what's outstanding?" is the **ledger**: one
`NNN-<slug>.yml` per spec in the bus (`Bus root/ledger/`), **born `registered` at
handover** (§4) so it's current the instant handover runs — no orc needed. Render any
time with the Renderer (CONFIG); validate with `--check`.

**Lifecycle:** `registered → planned → building → merged → shipped → accepted`, plus
`held`, `bounced`, `superseded`, `retired`. Advance a record inline at the loop point
where the transition happens; append (never rewrite) a `history:` entry.

**Everything is a status, never a separate file.** Every not-done state lives on the
one list, so nothing can rot in a folder no one watches:

| Situation | Status (not a file) |
|-----------|---------------------|
| Handed over, not yet picked up | `registered` |
| Can't be built | `bounced` (+ `bounce_reason`, `needs`) — loud |
| Deliberately paused | `held` (+ `held_reason`) — loud |
| Replaced by a corrective spec | `superseded` (+ `superseded_by`) |
| Abandoned | `retired` |

**Ironclad tracking (the guarantee):** a handover can't confirm receipt — so pickup
proof is the status *leaving* `registered`. A handed-over-but-unpicked spec stays
`registered` forever and renders loud on the one list. Not-picked-up is impossible to
hide; that — not the drop — is the guarantee.

**Task-list mirror (the dashboard):** orc keeps a harness task list at **spec level**
(`registered/planned → pending`, `building/merged/shipped → in_progress`,
`accepted → completed`), **rebuilt from the ledger on every boot**. Display only —
the ledger is the source of truth; never the reverse.

## 4. Handover — the atomic write

Handover is ONE self-verifying action. It either fully lands or errors loudly — no
partial state:

1. place the numbered spec into the Spec lane (CONFIG) as `NNN-<slug>-spec.md`
   (atomic, §2);
2. write the ledger master `Bus root/ledger/NNN-<slug>.yml` with `spec_id`, `title`,
   `intent`, `status: registered`, `handed_over_at`, `spec_file`, and an opening
   `history:` entry — **directly, no stub**;
3. confirm both exist and are non-empty; on any failure, report the partial state.

No git — handover writes the bus only; orc commits the spec doc + mirror.

## 5. State & archive

File location is state: live in a lane = pending; `_archive/` = done/consumed.
`_archive/` is **append-only — never `rm`**; the archived spec is the frozen
as-handed-over snapshot the close-out grader audits against. Cross-lane lineage is the
`source_brief:` header on a spec (one-way is enough).

## 6. Prime directives

- **Throughput via parallelism.** Fan out as many workers as real dependencies allow;
  the cap is dependencies, not a number. The integration lane is WIP=1.
- **Lean orchestrator.** Push every read/build/analysis to a sub-session that returns
  a tiny summary. A bloated orchestrator is a failed orchestrator.
- **Nothing lost / no silent stalls.** Every not-done state is on the one list; every
  wait is loud and timestamped; bias to act on anything a `git revert` undoes.

## 7. Evolving DO-IT (the self-hosting ritual)

When you propose a pipeline change: **read this file** (the rule now) → **read
`DESIGN.md`** (why it's this way, what was rejected) → **change this file AND append a
dated decision to `DESIGN.md`.** Never silently. The pipeline evolves the way you work.

## 8. Why / decisions

The rationale, trade-offs, and dated decision log live in **`DESIGN.md`** (same
folder). This file is the *what*; that one is the *why*.
