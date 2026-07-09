# Changelog

All notable changes to DO-IT are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry links to the dated design doc in `docs/` that holds the *why*; this file
is the terse *what*. Tags mark the commit each version shipped at, so
`git checkout v1.0.0` gets you that release.

## [4.7.1] — 2026-07-09 — Docs: README rewritten for the v4.7.0 model

Docs-only. The README still described the pre-4.0 two-role model (a single `orc`
that builds). Rewritten for the current pipeline: parallel builders in isolated
worktrees, the lean integrator (`orc` alias), the build lane (`assigned → building →
gating → ready`), the detached blind grader, the machine-global bus, rev/watcher, and
the standing-role cron automation. No code change.

## [4.7.0] — 2026-07-09 — Gating, standing-role liveness, and close-out hardening

Consolidated release bringing the distro current with live (folds the never-cut
4.1–4.6). **45 DO-IT-system specs** since 4.0.0 — additive + hardening; no role
contract removed or renamed, so a minor bump. Headline: the `.gating` lifecycle
state and its detached grader; a full standing-role liveness layer; and a rebuilt,
environment-faithful close-out pipeline.

**Config note:** the bus root default is now `~/.claude/` (machine-global). The
parallel-builder model (4.0.0) needs a bus shared across worktrees, so a repo-relative
`.do-it/` bus only fits a single-integrator setup. CONFIG §0 documents both.

### Added
- **`.gating` lifecycle state** (spec 300) — a pane-independent detached grader
  (`scripts/gating-watch.sh`) checks a builder's pushed branch from a fresh checkout
  at `ready_sha` and writes the verdict; builders free at push instead of blocking.
  New status between `building` and `ready`.
- **Standing-role liveness machinery** (256/279/297/298/315/363/400/401) —
  `scripts/standing-role-heartbeat.sh`, `watcher_sweep_liveness.sh`,
  `builder_lifecycle_reconcile.sh`: heartbeats key on real progress, not pane
  existence; sentinel self-heal treats tmux as ground truth.
- **Per-role nudge** (278/281/287/299/301/319/348) — `scripts/doit-nudge.sh` pokes an
  idle role pane when its lane has unconsumed work; presence-aware; consume-once +
  forged-baton quarantine. See `scripts/CRON-SETUP.md`.
- **Handover criterion↔evidence validator** (285) — `scripts/ci/handover_validate.py`
  enforces typed acceptance-criteria evidence rules; shifted left into `think`.
- **Thinker bus-first isolation** (253) — `scripts/ci/check_thinker_isolation.sh`.
- **`operator-ops` ephemeral role** (399) — runs exactly one prod data mutation and
  dies. Documented in DO-IT.md; no standalone skill yet.
- **Close-out grader internals** — `builder_closeout_check.py`,
  `builder_closeout_verdict.py`, `orc_deploy_verdict.py`, `migration_lint.py`,
  `scripts/lib/pane_send.sh`; verifier modules `rejectgate.mjs` / `revprobe.mjs`; new
  ledger/gating/verify/close-out test suites.
- **`scripts/CRON-SETUP.md`** — the standing cron lines (nudge/gating/heartbeat/
  reconcile/sweep), genericized with `REPO_ROOT`/`PYTHON`/`BUS_ROOT` overrides.

### Changed
- **Ledger truthfulness + state model** (282/402) — real states, stale-field lint,
  verified-reconcile; the aggregator can't derive CONFIRMED from a partial map.
- **Close-out gate rebuilt** (283/294/296/308/357/404/426) — runs in a dispatched
  sub-agent against the real environment (not a clean worktree); third `owed-data`
  verdict stops live-PG ACs false-failing; malformed lane files escalate instead of
  bouncing a good branch.
- **Lean integrator** (284/286/398/406/407/408) — actual-diff conflict footprints,
  migration-head reconciliation at merge, deploy coalescing (WIP=1 governs merge only).
- **Builder fan-out enforcement + post-ship recycle** (288/369/299).
- **Relay** canonicalized to baton-direct with author-token guard; `relay-watch.sh`
  rewritten (+418 lines).
- Role skills and `DO-IT.md` updated throughout; "orc" terminology → "integrator"
  (`orc` remains an alias). Version header → 4.7.0.

### Fixed
- Relay-watch runs as the correct user so it can see builder panes (293); root-owned
  heartbeat log no longer silently breaks watcher cron (363); Codex-skeptic /tmp leak
  bounded (397).

### Removed
- Superseded standalone tests (`test_next_num`, `test_number_allocation`,
  `test_needs_human_projection`, `test_review_loop_v2`) — consolidated into
  `test_spec_ledger*.py`.

### Notes
- The 7 deploy-time gates (280/361/362/397/407/408/427) live in the project's own
  `deploy.sh`, which this distro does not ship; the portable close-out gate
  (`scripts/close-out-gates/write_deploy_manifest.sh`) is included as a reference
  template to adapt.

## [4.0.0] — 2026-06-29 — Parallel builders + lean integrator (spec 252)

**Breaking role-split: parallel builders + lean integrator.** The `orc` singleton now
divides into N parallel **builders** (each owns one spec end-to-end in its own worktree)
feeding one lean **integrator** (the revised orc). This is the most significant structural
change since DO-IT 2.0.0.

### Added
- **`builder` skill** (`skills/builder/SKILL.md`) — new bootable session. Parallel (N at
  once); claims exactly ONE `.assigned` spec from the build lane via atomic rename; builds
  in its own git worktree off the integrator's `base_sha`; runs the full close-out
  evidence gate (blind, two verdicts, evidence-bound) IN ITS OWN WORKTREE; writes the
  identity-stamped review card; pushes the branch; renames the lane file to `.ready`. A
  builder never touches master, never deploys, never touches another spec's files.
- **Build lane** (`<bus-root>/build-lane/`) — new `assigned → building → ready` message
  lane between the integrator and the builder pool. Normative field names and lane suffixes
  live in the contract section of DO-IT.md §4.
- **`ready` ledger state** — new status a builder writes after self-gating. Sits between
  `building` and `merged`; the integrator reads `.ready` lane files, runs the speculative
  re-check against current master, then merges.
- **`build-lane/` to `.gitignore`** — add `<bus-root>/build-lane/` (it is working state,
  not code; same rule as the rest of the bus).

### Changed
- **`orc` → lean integrator** — the revised `orc` skill no longer builds. It reads ONLY
  the ledger rows and build-lane files — never a build artifact, diff, or transcript. It
  derives each spec's `writes:` footprint, assigns into the build lane off a frozen
  `base_sha`, speculative-re-checks every `.ready` branch against current master, merges
  WIP=1, deploys one at a time, and advances the ledger. `/orc` is preserved as an alias.
- **Close-out gate moved orc → builder** — the blind two-verdict typed-evidence gate
  (formerly run by orc after integration) now runs in the builder's own worktree before
  the lane file reaches `.ready`. The integrator's role shrinks to speculative re-check +
  merge.
- **`DO-IT.md`** — version line updated to v4.0.0; §1 map and role descriptions
  reflect the builder/integrator split; build lane added to CONFIG.
- **`think` + `spec-handover` skills** — prerequisites updated to reference DO-IT.md §0
  CONFIG table; think gains a fourth shape (review); descriptions updated for v4.

### Breaking
- A standalone orc session that builds in the shared working tree is no longer compliant.
  The integrator never builds; all building happens in builder-owned worktrees.
- The close-out gate now runs per-builder; a spec that ships without a builder review card
  is a protocol violation.

## [3.11.0] — 2026-06-21

**Non-building roles (watcher) hardened — findings can't die untracked; self-audit
discipline.** Two watcher self-audits surfaced gaps in how the standing process-reviewer
operates. Folded both into the `watcher` skill (+ a matching `think` obligation):

### Changed
- `skills/watcher/SKILL.md`:
  - **Re-verify own proposals (First-move step 3):** before sweeping, re-check every recent
    proposal — grep for the guard it proposed, never trust the ACK/archive name. "ACK'd",
    "routed", and "implemented+verified" are distinct; "closed" only when the guard is live.
    (A watcher had reported a cron-interpreter finding "closed end-to-end" when it was only
    parked — a hollow verification about its own work.)
  - **Quota "session" defined:** one proposal per *context* (per `/clear`-boot), not per
    sweep; chained sweeps share one quota, a relay resets it. Removes the "fresh session"
    rationalization.
  - **Signal integrity (step 4):** bless the signals the watcher actually uses (`git log`,
    ledger render, relay-watch logs, `*-active` for pane resolution — never a hard-coded pane
    id); demote `loop-observation/` to "only if fresh" (it had gone 10 days stale).
  - **Lane fix + durable intake:** advisories go to `brief-inbox/memo-watcher-*` (canonical),
    not `spec-inbox/`; a finding becomes a tracked numbered brief / spec / **logged drop** —
    never an unnumbered orphan. The non-building-role twin of rev's corrective-inbox.
- `skills/think/SKILL.md`: boot inventory lists `memo-watcher-*` as **mandatory-triage** —
  every one is converted to a numbered brief / spec / logged drop, never left to sit.

## [3.10.0] — 2026-06-21

**Relay canonicalized to baton-direct + an author-token guard.** The distributed
`relay-watch.sh` was still the older **sentinel model**: it looped hook-written
`/tmp/<role>-handoff-due-*` files and relayed only if a sentinel *and* a HANDED-OFF
baton existed. The sentinel was dropped only at/above the hard line, so a deliberate
handoff in the soft..hard band left no sentinel and never relayed — a 77-minute live
wedge (2026-06-09). The live Albert Scott instance had already retired the sentinel for
the **baton-direct** model (the cron reads `/tmp/<role>-active` + the baton directly,
relaying at any token level) but that was never ported back here. Baton-direct opened one
hazard — a stray sub-worker that hits its own context limit and stamps a HANDED-OFF over
the baton could force-clear a live role — now closed by the **author-token guard**: the
role writes a per-session `TOKEN=` into `/tmp/<role>-active` at arming and the same value
as `baton_token:` in its handoff; the cron force-clears ONLY on a match, else refuses
loudly (`*_RELAY_ERROR`). Back-compat: a role armed before the guard has no token and
falls through with a logged note. Why: `docs/2026-06-21-relay-baton-author-guard-design.md`.

### Changed
- `relay-watch/relay-watch.sh` — replaced the sentinel loop with the baton-direct cron:
  reads `/tmp/<role>-active` directly (no sentinel), plus the F11/F12 hardening (head-scan,
  atomic-completeness, freshness gate, R5 atomic consume-once, R2 wrong-pane guard) and the
  new step 2b author-token gate.
- `relay-watch/orc-token-watch.py` — synced to the live hook; the sentinel it drops is now
  only the hook's own re-nag dedup marker, no longer a cron trigger.
- `skills/{orc,rev,watcher}/SKILL.md` — arming step 0 writes `PANE`+`CWD`+`TOKEN=$(uuidgen)`
  to `/tmp/<role>-active`; baton templates carry `baton_token:` (= that TOKEN).
- `relay-watch/SETUP.md` — documents the baton-direct flow, the author guard, and the
  foreign-writer failure mode.

## [3.9.0] — 2026-06-21

**rev close-out gate for data-outcome criteria.** A criterion whose proof is a
cron/pipeline/backfill/scheduled job has no rendered surface — the verifier can prove a
page, never a job that runs later. rev had no rule for the class, so it closed such
criteria on the commit/deploy. A 2026-06-18 daily price-snapshot cron fix was marked done
while prod captured nothing for ~2 days (committed, never observed running). Originated as
a watcher longitudinal candidate; picked up via `/think`.

### Added
- `skills/rev/SKILL.md` — "Data-outcome criteria — verify on OBSERVED prod data" gate:
  CONFIRMED requires a dated count/freshness query showing the expected rows landed
  at/after the next scheduled run; until then the criterion stays in
  `Awaiting prod-verification` (a full scheduling interval there is correct, not a stall).
  Because `accepted = shipped ∧ CONFIRMED`, holding the verdict keeps a cron/pipeline spec
  from flipping `accepted` on deploy.

## [3.8.1] — 2026-06-21

**Allocation poison-guard made relative (was a stale absolute ceiling).** The
`next-num` sanity guard refused any allocation where the next number was `≥ 150`,
on the assumption the genuine sequence lived in the low 100s. Real sequences grow
without bound — once specs passed 150 the guard false-tripped on **every** real
allocation, making `next-num` dead for spec handover. The trailing-hyphen `_NUM_RE`
already prevents a `2026-` date-stem year from reading as 202, so the only remaining
poison is a wildly mis-numbered file — now detected by its **jump** above the
second-highest number (`max − second_highest > 40`), which never goes stale.

### Fixed
- `scripts/spec_ledger.py` — replaced `ALLOC_SANITY_CEILING = 150` (absolute) with
  `ALLOC_GAP_CEILING = 40` (relative gap). Added `_scan_bus_numbers()` /
  `scan_bus_top_two()`; `next-num` now refuses on an outlier jump, not an absolute
  value. `scan_bus_max()` unchanged in behaviour.
- `skills/spec-handover/SKILL.md`, `skills/think/SKILL.md` — prose updated from the
  stale `≥150` framing to the relative-jump signature.

## [3.8.0] — 2026-06-11

**Relay baton hardening (right-sized).** Fixes the silent auto-relay failures that
left a role's context-ceiling handoff dead for hours, after two adversarial audit
rounds rejected a heavier CLI/state-machine as over-built for a watched one-box.
Design + the considered-and-rejected alternatives:
`docs/2026-06-11-v3.8-relay-hardening-and-goal-loop-design.md`.

### Fixed
- **F11 — rev had no baton field template.** `skills/rev/SKILL.md` now carries an
  explicit baton template with `handed_off_at:` (the field the relay cron requires);
  previously rev's prose-only instruction produced `updated_at:` and the cron skipped
  every minute (a role dark ~42h in one observed window). orc already had a template.

### Added
- **Loud-but-rate-limited relay failure (R3).** `relay-watch.sh` no longer silently
  logs-and-skips a HANDED-OFF-but-malformed baton: it writes a `/tmp/<role>-relay-error`
  marker ONCE per error fingerprint (no per-minute poisoning).
- **Dark-role stall alert (R4).** A HANDED-OFF + unconsumed baton older than 2× the
  freshness window writes `/tmp/<role>-relay-stall` — a dark relay surfaces in hours.
- **`liveness.sh relay <role>`** surfaces both markers as `<ROLE>_RELAY_ERROR` /
  `<ROLE>_RELAY_STALL` flags on the board (the existing watchdog channel).

### Notes
- Deliberately NOT included (considered + rejected — see the design doc): a
  `relay_baton` writer-CLI / typed state machine (over-built, new single-point-of-
  failure + race surface for a solo watched one-box), and a `/goal`-driven autonomous
  "retry until rev clears" loop (deferred to its own design; validate `/goal`'s
  `/clear` behaviour first).
- Incidents specific to the baton-direct relay variant (CWD-in-arming, `baton_pane`
  unquoting) are fixed in that variant where it runs; this release targets the
  sentinel-based reader shipped here.

## [3.7.0] — 2026-06-09

**Loop self-repair — the `watcher` role, a relay-deadlock fix, and predictive
gates.** Born out of a full night of watching the orc↔rev↔think loop run live;
the design doc records the dated incidents behind each change:
`docs/2026-06-09-loop-self-repair-v3.7.md`.

### Added
- **`watcher`** — a standing process-reviewer skill (`skills/watcher/SKILL.md`),
  rev's twin one level up: rev reviews the shipped *product*, the watcher reviews
  the *loop* (is the build/review machine itself producing defects, churn, or
  invisible work?). Read-only on code/git/bus, never registers an NNN, evidence-
  bound (every proposal cites dated incidents), biased to leave-it-alone, and
  capped by a hard one-proposal-per-session / three-open quota so it can't churn
  the rules. Self-relays on its own baton like orc/rev. `setup.sh` links it.
- **Reference close-out gates** (`scripts/close-out-gates/`): orphan-nav
  reachability (a page built but unreachable — recurred 4×), cross-spec
  data-dependency derivation (a data spec ships while a downstream surface goes
  stale), and a deploy manifest (one written record of prod ground-truth so the
  reviewer reads it instead of re-probing host/sha each tick). Project-shaped —
  all paths env-overridable, no hardcoded hosts.
- **F5 contract binding** in `spec_ledger.py`: `verify --contract-version` stamps
  the contract a `$`-asserting verdict held under; bumping the contract flips that
  verdict to a new **needs-revalidation** state (re-verify under the new contract)
  instead of a false regression. Absent contract file ⇒ inert (backward compatible).

### Fixed
- **Relay deadlock (the night's worst find).** The relay sentinel was dropped only
  at the hard token threshold, so an agent that handed off *below* it — a deliberate
  early handoff, or after a soft nudge — left no sentinel; the cron never read its
  `HANDED-OFF` baton and the session sat wedged forever. Observed live on **both**
  twins at once (orc @371k, rev @384k, neither relayed). Fix: a configurable soft
  line (`ORC_WATCH_SOFT`, default 0.9× threshold) **arms the sentinel at the soft
  line**, so any handoff at or above it relays; the hard line only escalates the
  nudge. (Residual, documented: a deliberate handoff *below* the soft line still
  needs a manual restart — the full baton-scan trigger is noted for a follow-up.)
- **Relay F11/F12.** The gate matched `head -1 … status: HANDED-OFF`, but the `rev`
  baton's status is on line 3 (H1 title on line 1) — so a `rev` self-relay could
  *never* fire. Now scans the baton head. Plus a freshness gate (refuse a baton
  older than `BATON_FRESH_SECS`, default 90m), atomic-completeness, a consume-once
  marker (no double-`/clear`), and a newest-sentinel-per-pane identity guard.

### Changed
- **076 role guard** is now enforced in `spec_ledger.py`: `next-num`/`register`/`set`
  are refused for `ROLE` in `{rev, watcher}` — only `orc` writes the build ledger.
  A guard, not a convention (a non-builder writing the ledger once shipped an outage).
- Role map is now **orc / rev / think / watcher**.

## [3.6.0] — 2026-06-08

**Review Loop v2 — Part 3 of 3 (complete): ops + the standing `rev` session.**

### Added
- **`rev`** — the standing reviewer skill (`skills/rev/SKILL.md`): drives the
  verifier, writes per-criterion verdicts, files correctives, self-relays. orc's
  twin — one builds, one reviews.
- **Role-parameterized relay** (`ROLE` env on `orc-token-watch.py` + `relay-watch.sh`):
  `rev` self-relays via its own sentinel/baton/boot (`/rev`), and can never reboot a
  `rev` pane as `/orc`. Default `ROLE=orc` is byte-for-byte the prior behavior.
- **`relay-watch/liveness.sh`** — the dead-man's switch: `VERIFIER_DOWN` (PROGRESS
  stale), `ROLE_DOWN` (active pane dead), `*_HOOK_MISSING` (the relay hook isn't
  registered — the exact silent break seen 2026-06-08). Flags surface loudly in the
  ledger render.
- Durable **needs-human store** projection in `render` (unresolved escalations reach
  orc's board).

### Changed
- `think` sheds its review shape; the role map is now **orc / rev / think**. Review
  of shipped work lives in `rev`; the executable verifier owns the per-criterion
  verdict; closure is the derived `accepted`.

### Deferred (unchanged from the design)
- `deployed_sha` gate (10-min delay interim ships in v3.5.0), interaction_traces,
  rework_count ceiling, severity. Revisit per the design's triggers.

## [3.5.0] — 2026-06-08

**Review Loop v2 — Part 2 of 3: the executable verifier.** The verifier now
produces verdicts from an executable rendered-page observation, feeding the v3.4.0
derived join.

### Added
- `lib/predicate.mjs`, `lib/assert-dom.mjs`, `lib/cardschema.mjs`, `lib/freshness.mjs`
  (+ `node --test` fixtures). The `dom_assertion` runner catches the A1 class —
  a blank-but-present container (`min_rows`/`count_gte`/`text_matches`, never
  `present`) and a render-throw via `forbid_console`.
- Machine-readable `verifier:criteria` block in the review card; the verifier parses
  it and **fails closed** on a ui criterion with no/invalid `dom_assertion`.

### Changed
- The verifier writes **per-criterion** `CONFIRMED`/`REJECTED`/`not-applicable` via
  `spec_ledger.py verify --criterion`; failures now reach `verified/` (not just
  `NEEDS-HUMAN.jsonl`), so `needs-rework` is reachable.
- A spec is skipped until 10 min after it shipped (interim for the deferred
  `deployed_sha` gate). Part 3 (ops crons + the standing `rev` session) follows.

## [3.4.0] — 2026-06-08

**Review Loop v2 — Part 1 of 3: the derived-verdict ledger substrate.** First
slice of the rendered-page-verdict redesign
(`docs/2026-06-08-review-loop-prod-verdict-design.md`). Closure becomes
*computed* from the join of the build ledger and the verifier's per-criterion
verdicts, so "build says done / prod says hollow" is un-representable. Parts 2
(the executable Playwright assertion engine + the A1 live proof) and 3 (ops crons
+ the standing `rev` review session + `rev-watch/`) follow as later minors.

### Added
- `resolve_spec_verdict()` — aggregates a per-criterion verdict map into a
  spec-level verdict (REJECTED dominates; CONFIRMED iff every observable criterion
  passes; `not-applicable` excluded so one data-gap can't freeze a spec forever).
- `effective_status()` — derives closure: `shipped ∧ CONFIRMED → accepted`,
  `shipped ∧ REJECTED → needs-rework`, open `needs_human` → `needs-human`, else
  `awaiting-prod`. `accepted` is now **computed, never stored**.
- `spec_ledger.py alert` — flags any spec stuck `awaiting-prod` > 48h. Kept OUT of
  `--check`, which stays time-invariant for CI.
- `render` gains a loud top **`❌ NEEDS-REWORK`** section and an `awaiting-prod`
  bucket.

### Changed (behavioural — note before upgrading an instance)
- `cmd_verify` derives the spec-level verdict from `--criterion ID=VERDICT` and
  **refuses a caller-supplied verdict that disagrees** with the derived one. The
  legacy positional-verdict path still works.
- `cmd_set accepted` is now **refused** — `accepted` is computed-only. (`accepted`
  stays in `VALID_STATUS` so legacy records still validate.)
- `render`'s "Shipped — awaiting your review" bucket is renamed
  "Awaiting prod-verification"; a CONFIRMED spec is promoted to Accepted.

## [3.3.0] — 2026-06-08

Adds **atomic shared-bus number allocation** — closes the *race* that v3.2.2's
pattern fix left open.

### Added
- `spec_ledger.py next-num --kind {spec,brief} --slug <slug>` — the single source
  of truth for bus numbers. Under one machine-global lock (`ledger/.alloc.lock`) it
  scans all five bus dirs with `^[0-9]{3}(?=-)`, computes the next number, **and
  reserves it before returning**: a spec births its `registered` ledger record; a
  brief writes its brief file. A concurrent session blocks until the reservation is
  on disk, so two sessions can no longer both grab `max+1` and double-book (the live
  110 collision between two `think` sessions). Refuses a computed number ≥150 as
  poison, and (for specs) anything that wouldn't pass `--check`.
- `DOIT_SPEC_INBOX` / `DOIT_BRIEF_INBOX` env overrides so the allocator's dir set is
  testable in isolation (mirrors the existing `DOIT_LEDGER_DIR`). `tests/test_next_num.py`
  covers the shared counter, date-stem immunity, the ≥150 guard, and real concurrency.

### Changed
- `spec-handover` and `think` now call `next-num` instead of an inline `grep`/`max+1`.
  Spec handover allocates **and** registers in that one atomic call — it no longer
  calls `register` separately. The existing `register` (explicit id) stays for any
  caller that already knows its number.
- **Sequencing note for instances:** `next-num` must exist in the `spec_ledger.py`
  the skills shell out to *before* the skill docs are flipped to call it, or
  allocation hard-fails. The public repo ships them together; a separate running
  instance must land/deploy the helper first, then switch its skills.

### Why
The per-record `flock` register/set use cannot serialize *allocation* — two
allocators racing for a new number have no shared record path to contend on. A
bus-wide lock + reserve-on-return is the fix. The reservation is the artifact
itself (no placeholder, no reaper). See `docs/DESIGN.md`.

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
