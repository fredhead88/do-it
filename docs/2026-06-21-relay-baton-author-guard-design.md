# Relay-baton author guard — a sub-worker must not be able to force-relay a role

Status: ready for orchestrator
intent: A live role session is force-cleared and re-booted ONLY by its own authenticated handoff — a stray sub-worker (or any other writer) can never forge a baton that yanks the orchestrator out of a live integration mid-work.
Date: 2026-06-21
Scope (in): the DO-IT relay-watch cron (`scripts/orc-relay-watch.sh`) and the baton
arming + template in the `orc` / `rev` / `watcher` skills; the public do-it repo's
copies of the same. (out): the relay *trigger* logic (token-level-agnostic baton-direct
relay, spec 165) and baton *parse* (F11 head-scan) — those stay as-is; this changes only
WHO the cron will honor a baton from.
Audited against: `scripts/orc-relay-watch.sh` (v3.8 baton-direct, read 2026-06-21),
`~/.claude/skills/orc/SKILL.md` (arming step 0 L55-56; baton template L468+),
`~/.claude/skills/rev/SKILL.md` (arming L38-41; template L100-109),
`~/.claude/skills/watcher/SKILL.md` (arming/relay), the 076 role-guard precedent in
`scripts/spec_ledger.py` (`_REV_FORBIDDEN`, `ROLE` env).

## TL;DR

The relay-watch cron force-`/clear`s a live role session whenever its baton file
(`docs/sessions/<role>-relay.md`) reads `status: HANDED-OFF`, is fresh, and the pane
matches — but it never checks WHO wrote the baton. On 2026-06-21 a background deploy
sub-worker, hitting its own context limit, wrote a `HANDED-OFF` deploy-handoff *over*
the orc baton; the cron read it as a genuine orc handoff and force-cleared a live orc
from 423k → 95k context mid-integration of 5 concurrent diffs + a P0 data fix. Give the
baton an unforgeable per-session author token written at arming; have the cron honor
only a baton whose token matches the live role's active-file token, refusing (loudly)
anything else.

## Why this exists

The cron is the single point that decides to force-clear and re-boot a role session —
the highest-leverage, least-reversible action in the loop. It currently trusts the baton
file's *content* with no authentication of its *author*. The relay was deliberately made
token-level-agnostic in v3.8 (relay on any HANDED-OFF flip, spec 165) — which is correct
for legitimate handoffs but means the only thing standing between a stray file write and
a force-clear of a live session is "nobody else writes this file," a convention.

Who it hurts: the orchestrator (and by symmetry rev/watcher) at its highest-judgment
moment. Observed once (2026-06-21), operator-waived the watcher's ≥2-incident bar to act
now because the blast radius is a live force-clear mid-integration.

### Observed incident (evidence, all UTC 2026-06-21)
- ~07:58–08:00Z: orc pane `%8` at 423,607 tok, 5-worker fan-out in flight.
- `/tmp/orc-relay-watch.log`: `08:07:01Z relaying orc in pane %8 (baton_age=36s)`.
- Recovering orc's own baton note: *"a DEPLOY SUB-WORKER wrongly overwrote this baton
  with a HANDED-OFF deploy-handoff when it hit its OWN context limit. That was a worker
  bug, NOT an orc relay. This orc session is STILL ACTIVE (no ORC CONTEXT WATCH hook
  received)."*
- 08:10Z: orc pane `%8` at 95,252 tok — confirmed force-cleared, fresh boot.

## Current state (audited)

- `scripts/orc-relay-watch.sh` runs per-role every minute. Gates a relay on: (1) baton
  `^status:[[:space:]]*HANDED-OFF`; (2) `handed_off_at:` present (atomic-completeness);
  (3) freshness (mtime < `BATON_FRESH_SECS`); (4) consume-once (keyed `baton_id` or
  mtime); (5) **optional** pane match — only enforced `if [ -n "$baton_pane" ]`, so a
  baton omitting `baton_pane:` skips this guard; (6) pane alive; (7) pane quiet. None of
  these authenticate the writer.
- Arming (`/tmp/<role>-active`) carries `PANE=` + `CWD=` (+ optional `SESSION_ID=`,
  `TRANSCRIPT=`) — no per-session secret.
- The baton template (orc/rev/watcher skills) carries `status`, `handed_off_at`,
  `baton_id` (uuidgen, for consume-once disambiguation), `baton_pane`, plus role payload.
  `baton_id` is NOT an author proof — it's written fresh by whoever writes the baton.
- Precedent: the ledger has a hard role guard (076) — `spec_ledger.py` refuses
  `set`/`register`/`next-num` when `ROLE` ≠ orc (`_REV_FORBIDDEN`, exit 3). The ledger
  could be guarded at the writer because its only writer is that one CLI. The baton is
  hand-written markdown, so the equivalent guard must live at the **consumer (the cron)**.

## Requirements

### R1 — Cron honors only an author-authenticated baton (primary guard)

Problem: the cron force-clears on baton content from any writer.

Required behaviour: at arming, the role writes a random per-session token to its active
file (`TOKEN=<nonce>` in `/tmp/<role>-active`). When the role writes its handoff baton it
includes `baton_token: <the same nonce>` (read back from its own active file, so the role
need not "remember" it). The cron adds a gate, before relaying: read `TOKEN=` from
`/tmp/<role>-active` and `baton_token:` from the baton; relay only if both are present and
equal. A baton with a missing or mismatched token is refused and surfaced via the existing
`notify_once error` channel (a `<ROLE>_RELAY_ERROR` board flag), never a silent skip.

- Acceptance criteria:
  - A baton containing `status: HANDED-OFF` + `handed_off_at:` but **no** `baton_token:`
    does NOT trigger a relay; `/tmp/<role>-relay-error` is written once and the log shows
    a refusal naming the missing token. (Reproduces the incident shape: a worker handoff
    lacking the nonce.)
  - A baton with a `baton_token:` that does not match `TOKEN=` in the live active file
    does NOT trigger a relay; same loud-refusal behaviour.
  - A legitimate self-relay (the role writes its own baton with the token from its own
    `/tmp/<role>-active`) DOES relay exactly as today (clear + re-boot), and the fresh
    boot re-arms a NEW token. Prove with `ORC_WATCH_DRY=1`: matching token → "would
    /clear + /<role>"; absent/mismatched token → refusal line.
  - Back-compat: a role that armed before this change (active file has no `TOKEN=`) must
    not hard-fail the cron. Define the behaviour explicitly (recommended: if the active
    file has no `TOKEN=`, fall back to today's gates AND emit a one-time
    `<ROLE>_RELAY_UNAUTHED` notice so the gap is visible until every live role re-arms).
  - Severity: high (a live force-clear of the integrator mid-work).

### R2 — Workers never write a role baton (defense in depth)

Problem: the failure originated from a sub-worker's reflexive "I hit my limit → write a
handoff" landing in the orc baton path.

Required behaviour: a sub-worker (deploy or otherwise) that needs to record a context-limit
handoff writes a **worker-scoped** file (e.g. `docs/sessions/worker-<id>-handoff.md`), never
`docs/sessions/<role>-relay.md`. The orc's worker-dispatch instructions state the worker
handoff path explicitly so a worker is never left to choose the relay file by reflex.

- Acceptance criteria:
  - The `orc` skill's worker-dispatch section names the worker handoff path and states
    that the relay baton is orc-only.
  - No worker-dispatch template instructs (or leaves room to infer) writing
    `docs/sessions/<role>-relay.md`.
  - Severity: medium (R1 already blocks the bad relay; this stops the collision at source).

### R3 — Optional: live-session heartbeat before force-boot (consider, may defer)

Problem (watcher open question): even with R1, a future path could force-boot a still-alive
session, risking the "two orcs on one baton" hazard.

Required behaviour (if cheap): before sending `/clear`, the cron confirms the pane is not
an actively-progressing session that never armed a matching token. R1 + the existing
quiet-gate (check #7) already cover the observed case; orc/think may judge R3 redundant.

- Acceptance criteria: either implemented with a named heartbeat signal, OR the spec's
  plan records why R1 + quiet-gate make it unnecessary. Severity: low.

## Surface / propagation

- `scripts/orc-relay-watch.sh` — the cron gate (R1) is the load-bearing change.
- `~/.claude/skills/{orc,rev,watcher}/SKILL.md` — arming writes `TOKEN=`; baton template
  gains `baton_token:` read from the active file. (orc/rev/watcher all self-relay, so all
  three need the token; the guard must be uniform or a tokenless rev/watcher baton trips
  the back-compat path forever.)
- Public do-it repo (`~/do-it/`) — mirror the script + skill changes; ship as a versioned
  release (this is a relay-hardening change in the same family as v3.8.0). Keep the AS
  instance and the do-it copy in step.
- Cron install: the per-role cron lines are unchanged (same script path); no new cron.

## Invariants touched (docs/INTENT.md)

This is DO-IT loop infrastructure, not product data — it touches no Profit/settlement
invariant. It strengthens the operational guarantee that a role session is force-cleared
only by its own authenticated handoff (the relay-safety property the v3.8 baton-direct
design assumed but did not enforce).

## Rejected alternatives

- **True file-level role guard (watcher option 1).** Can't stop a raw markdown write the
  way the 076 CLI guard stops a ledger write; the baton has no single guarded writer.
  Moved the guard to the consumer instead.
- **Mandatory `baton_pane:` (reject batons without it).** Catches a worker that omits the
  field, but a worker copying the orc template (with `baton_pane`) still passes — weaker
  than a per-session token. Kept pane-match as-is.
- **`baton_id` as the author proof.** `baton_id` is written fresh by any writer for
  consume-once disambiguation; it proves nothing about authorship.
