# Adversarial Audit — Composite Skill Design

A cross-host, auto-routing adversarial audit skill that attacks **specs**,
**execution plans**, and **code** with a production-sacred discipline layer.
Synthesized from a 10-repo audit (see Provenance). Tuned to Ephraim's flow:
solo operator, Claude Code with `--dangerously-skip-permissions`, production
apps used by paid employees where a bad review that greenlights a regression
costs real money.

## Goals

- **One command, no flags to remember.** `/adversarial-audit` classifies the
  input as spec / plan / code and runs the matching attack profile.
- **Cross-vendor critique.** The reviewing model is a *different vendor* from
  the one that produced the artifact — Codex (`codex exec`) is the partner,
  because correlated blind spots are the core failure mode of self-review.
- **Critique-only, never edits.** Surfaces findings and stops. No auto-apply,
  no fix loop. (Direct rejection of the dementev auto-apply engine, which
  violates the no-fix-the-fix rule in CLAUDE.md.)
- **Production-sacred severity.** Findings are scored by blast radius on a
  prod-used app, not generic CVSS.

## Non-Goals (YAGNI)

- No auto-apply / self-healing loop. No suggested patches.
- No API-key model fan-out (adversarial-spec style). CLI subscription only
  (`codex exec`), to keep cost bounded and setup zero.
- No HTML dashboard, no VoltAgent catalog coupling, no 16-phase ceremony.
- No Gemini dependency (not installed) — Gemini is an optional future cascade
  rung, not required.

## Architecture

```
/adversarial-audit  (entry: SKILL.md)
        │
        ▼
  [1] Scope gate ......... confirm artifact, in/out of scope, severity bar
        │
  [2] Classify ........... spec | plan | code | mixed   (thin heuristic)
        │
  [3] Attack-surface map . enumerate components / data flows / trust
        │                  boundaries / entry points BEFORE critiquing
        ▼
  [4] Dispatch to partner via runner subagent
        │   host-detect → codex exec (read-only, timeout-wrapped)
        │   anti-recursion depth guard
        │   ── unreachable? ─► in-session Claude panel + DEGRADED banner
        ▼
  [5] Profile-specific critique  (spec / plan / code prompt)
        │
  [6] Discipline pass .... severity rubric, falsification gate,
        │                  CWE-style schema, calibration, confidence
        ▼
  [7] Post-critic re-check  in-session Claude re-greps every P0 vs ground
        │                   truth (catches confident cross-host false +ve)
        ▼
  [8] Report ............. ranked findings, verdict, NO file edits
```

### Components

| Unit | Purpose | Depends on |
|---|---|---|
| `SKILL.md` | Entry point: scope gate, classifier, profile selection, discipline pass, report assembly | `lib/`, `references/` |
| `references/runner.md` | Thin subagent spec that launches `codex exec`, isolates its JSONL firehose, returns a compact result. Never interprets findings. | `lib/call-external.sh` |
| `references/profiles.md` | The three attack profiles (spec / plan / code) — prompt bodies + per-profile checklists | — |
| `references/discipline.md` | Severity rubric, falsification gate, finding schema, calibration rules | — |
| `lib/detect-host.sh` | Host detection: override → Codex-env → Claude-env → PPID walk | — |
| `lib/call-external.sh` | Partner invocation, timeout wrapper, cascade, exit-code contract, degraded banner | `detect-host.sh` |

### Engine (cross-host)

- **Partner invocation** (lifted from dementev + robertoecf, hardened):
  `cat $prompt_file | timeout "$TIMEOUT" codex exec --json -m <model> \
   -c model_reasoning_effort=high --sandbox read-only --skip-git-repo-check \
   -o $out_file -` — prompt via **stdin/heredoc**, not argv (fixes robertoecf's
  quoting/size footgun). Default `read-only` sandbox (uncommitted WIP +
  gitignored secrets make `workspace-write` unsafe here).
- **Runner subagent** isolates Codex's multi-MB JSONL so only the compact
  review markdown (~few KB) crosses into the main session.
- **Host detection priority:** `ADVERSARIAL_AUDIT_HOST` override → Codex env
  markers → Claude env markers → PPID walk (Codex checked before Claude because
  Claude env vars leak into nested Codex, not vice-versa).
- **Anti-recursion:** `ADVERSARIAL_AUDIT_DEPTH` set to 1 before partner call;
  `≥1` refuses. Prevents the partner from invoking the skill on itself.
- **Timeout-wrapped** (closes robertoecf's documented hang — it reads a
  timeout var but never applies it). **macOS has no `timeout`/`gtimeout`** —
  use a portable shim (perl `alarm`, or background-PID + `kill` fallback;
  prefer `gtimeout` if present). The bare `timeout` the source repos assume
  will fail on this machine.
- **Auth verified working as-is** (Codex 0.133.0, `gpt-5.5`, 2026-06-01 smoke
  test returned cleanly). `forced_login_method = "chatgpt"` is NOT required
  here and must NOT be added — it's documented only as a remediation IF a
  future `codex exec` 404 appears.
- **Stderr noise filter.** `codex exec` emits MCP transport warnings (e.g. a
  Vercel MCP `AuthRequired` error) to stderr that are unrelated to the review.
  The runner must strip stderr/MCP noise and return only the review markdown
  from the `-o` output file.
- **Degraded path:** Codex unreachable → in-session Claude multi-persona panel,
  prefixed with a verbatim banner: `⚠️ DEGRADED — cross-vendor principle
  bypassed; this is a same-model review.` Exit-code contract 0 / 1 / 2.

### Attack profiles

**Spec** (from adversarial-spec tech/PRD prompts):
- Probes: hidden assumptions, untestable requirements, missing acceptance
  criteria, undefined error scenarios, scope ambiguity, full API/data
  contracts (not just endpoints).
- ERROR / RISK / PREFERENCE triage on every finding.
- Kill criterion: "No ambiguity an engineer would need to resolve."
- Anti-sycophancy: never accept a clean verdict without forced enumeration of
  sections reviewed + concrete concerns (adversarial-spec `--press`).

**Plan** (from robertoecf plan-review + dementev plan attack):
- Frame: "Assume this plan will fail. Prove it."
- Axes: scope, missing steps, dependency ordering, **rollback story**,
  **blast radius**, success criteria, cost.
- Bakes in CLAUDE.md rules: Rollback-First, reproduce-before-fix, "fix upstream
  not in every caller," check recent history on touched files.
- Verdict: PROCEED / REVISE / RETHINK.

**Code** (personas from alirezarezvani + lenses from poteto):
- Lenses: Saboteur ("break it in production"), Security Auditor (OWASP/auth/
  secrets/IDOR), Skeptic (correctness/unhandled paths), Architect (boundaries/
  coupling), Minimalist (what can be deleted).
- **Risk-weighted reviewer ladder** (poteto size ladder, but risk-first): any
  diff touching `opt_outs`, SMS-send, webhook-200-return, schema, or file-size
  caps is forced to the max lens set regardless of line count. Pure size only
  decides the ladder for low-risk diffs.
- No-LGTM discipline, but **calibrated** — see below.
- Verdict: SHIP / REVIEW_NEEDED / DO_NOT_MERGE.

### Discipline layer (cross-cutting)

1. **Production-sacred severity rubric.** P0 = ships to a prod-used app without
   a reproduce step, rollback, or opt-out preservation. P1 = real defect/cost.
   P2 = should-fix. P3 = nit.
2. **Falsification gate.** Any finding capped at P1 unless one cheap read-only
   command was actually run to confirm it. Enforces reproduce-before-fix at the
   review layer. (agent-review-panel Rule 4.)
3. **Finding schema (CWE-style, from snailsploit).** Each finding: id,
   defect-class, root-cause (not symptom), repro/trigger, quantified impact,
   ordered remediation, **confidence (low/med/high)**.
4. **Calibration.** Over-flagging everything a blocker is itself a logged
   failure ("when CVSS lies"). Cross-promotion-on-consensus is capped so two
   weak NOTEs cannot chain to CRITICAL.
5. **Defect typing.** `[EXISTING_DEFECT]` vs `[PLAN_RISK]`; P0 requires an
   existing-defect with evidence.
6. **Post-critic hallucination re-check.** Before surfacing, a cheap in-session
   Claude pass re-greps every P0 the partner raised against ground truth.
   Catches confident cross-host false positives — the precise "bad review
   greenlights/masks a regression" risk.
7. **Regressions-aware.** For code/plan, scan `docs/architecture/regressions.md`
   (when present in the target repo) and flag repeat-offender areas.

## Error handling

- Partner timeout / non-zero exit → degraded path with banner, never a silent
  same-model review.
- Zero or multiple session-id matches on resume → fail closed (we don't use the
  resume loop, but the runner's session capture follows dementev's fail-closed
  rule).
- Classifier ambiguous → ask the user (don't guess between spec/plan/code).
- No file mutations expected; runner takes a pre/post `git status --porcelain`
  snapshot and hard-stops + reports if the read-only partner somehow wrote
  anything.

## Testing / verification (how we'll prove it works)

- **Spec path:** run against an existing `docs/` spec in ram-ai; confirm it
  surfaces ≥1 real ambiguity and routes to the spec profile.
- **Plan path:** paste a multi-step plan; confirm rollback/blast-radius axes
  fire and verdict returns.
- **Code path:** run "audit uncommitted" on a small real diff; confirm
  cross-host dispatch to Codex actually executes (check the runner returns
  Codex output, not Claude), risk-weighted ladder triggers on a seeded
  `opt_outs`-touching change, and the post-critic re-check runs.
- **Degraded path:** temporarily break Codex auth env; confirm the banner
  prints and the in-session panel still produces findings.
- Each path exercised end-to-end before the skill is called done.

## Provenance (audited repos)

Engine: robertoecf/adversarial-review, dementev-dev/adversarial-review,
poteto/noodle. Spec attack: zscole/adversarial-spec. Discipline: 
wan-huiyan/agent-review-panel, alirezarezvani/claude-skills. Attack-mindset:
yechao-zhang/red-team-agent-skills, Masriyan/Claude-Code-CyberSecurity-Skill,
SnailSploit/Claude-Red, Eyadkelleh/awesome-claude-skills-security.

Deliberately NOT copied: dementev auto-apply loop; agent-review-panel 16-phase
ceremony + HTML dashboard + VoltAgent coupling; adversarial-spec API-key fan-out
and its fragile substring `[AGREE]` consensus check.
