# Adversarial Audit Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cross-host, auto-routing adversarial audit skill (`adversarial-audit`) that critiques specs, execution plans, and code via Codex as a cross-vendor reviewer, with a production-sacred discipline layer, critique-only (never edits).

**Architecture:** A `SKILL.md` entry point drives: scope gate → classify (spec/plan/code) → attack-surface map → dispatch to a runner subagent that calls `codex exec` (read-only, portable-timeout-wrapped, stderr-filtered) → profile-specific critique → discipline pass → post-critic hallucination re-check → report. Falls back to an in-session Claude panel with a loud banner if Codex is unreachable. Bash libs hold the load-bearing host-detection and partner-invocation logic; markdown references hold the attack profiles and discipline rules.

**Tech Stack:** Bash (POSIX + perl `alarm` timeout shim, macOS-safe), Codex CLI 0.133 (`gpt-5.5`), Claude Code skill format (SKILL.md + references/ + lib/).

**Skill home:** `~/.claude/skills/adversarial-audit/` (already contains `DESIGN.md`). Not committed to any product repo.

---

### Task 1: Portable timeout + host detection lib

**Files:**
- Create: `~/.claude/skills/adversarial-audit/lib/detect-host.sh`

- [ ] **Step 1: Write `detect-host.sh`**

```bash
#!/usr/bin/env bash
# Prints "claude" or "codex" — the host we are running INSIDE.
# Priority: explicit override -> Codex env -> Claude env -> PPID walk.
# Codex is checked before Claude because Claude env vars leak into a nested
# Codex process but not vice-versa.
set -euo pipefail

detect_host() {
  if [ -n "${ADVERSARIAL_AUDIT_HOST:-}" ]; then
    echo "$ADVERSARIAL_AUDIT_HOST"; return
  fi
  if [ -n "${CODEX_THREAD_ID:-}${CODEX_CI:-}" ]; then echo "codex"; return; fi
  if [ -n "${CLAUDE_CODE_ENTRYPOINT:-}${CLAUDE_AGENT_SDK_VERSION:-}" ]; then
    echo "claude"; return
  fi
  # PPID walk, up to 8 levels; innermost match wins.
  local pid=$PPID lvl=0 comm
  while [ "$pid" -gt 1 ] && [ "$lvl" -lt 8 ]; do
    comm=$(ps -o comm= -p "$pid" 2>/dev/null || true)
    case "$comm" in
      *codex*) echo "codex"; return ;;
      *claude*) echo "claude"; return ;;
    esac
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || echo 1)
    lvl=$((lvl+1))
  done
  echo "claude"  # default assumption
}

detect_host
```

- [ ] **Step 2: Verify it runs and returns a host**

Run: `bash ~/.claude/skills/adversarial-audit/lib/detect-host.sh`
Expected: prints `claude` (we're inside Claude Code).

- [ ] **Step 3: Verify override works**

Run: `ADVERSARIAL_AUDIT_HOST=codex bash ~/.claude/skills/adversarial-audit/lib/detect-host.sh`
Expected: prints `codex`.

- [ ] **Step 4: Commit** (skill home is not a git repo by default; skip if `git rev-parse` fails)

```bash
cd ~/.claude/skills/adversarial-audit && git rev-parse --is-inside-work-tree 2>/dev/null \
  && git add lib/detect-host.sh && git commit -m "feat(adv-audit): host detection lib" || echo "no git repo, skipping commit"
```

---

### Task 2: Partner invocation lib (the engine)

**Files:**
- Create: `~/.claude/skills/adversarial-audit/lib/call-external.sh`

- [ ] **Step 1: Write `call-external.sh`**

```bash
#!/usr/bin/env bash
# Usage: call-external.sh <prompt_file> <out_file>
# Sends the prompt to the cross-vendor partner (Codex) and writes the review
# markdown to <out_file>. Exit codes:
#   0 = cross-host review succeeded (out_file populated)
#   1 = recursion refused (we are already inside an audit dispatch)
#   2 = degraded: partner unreachable/failed (caller must fall back in-session)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="${1:?prompt_file required}"
OUT_FILE="${2:?out_file required}"
TIMEOUT="${ADVERSARIAL_AUDIT_TIMEOUT:-600}"
MODEL="${ADVERSARIAL_AUDIT_MODEL:-gpt-5.5}"

# Anti-recursion: refuse if a partner call is already in flight.
if [ "${ADVERSARIAL_AUDIT_DEPTH:-0}" -ge 1 ]; then
  echo "adversarial-audit: recursion refused (DEPTH=${ADVERSARIAL_AUDIT_DEPTH})" >&2
  exit 1
fi
export ADVERSARIAL_AUDIT_DEPTH=1

# Portable timeout: gtimeout -> timeout -> perl alarm (macOS-safe).
run_with_timeout() {
  local secs="$1"; shift
  if command -v gtimeout >/dev/null 2>&1; then gtimeout "$secs" "$@"; return $?; fi
  if command -v timeout  >/dev/null 2>&1; then timeout  "$secs" "$@"; return $?; fi
  perl -e 'my $s=shift; eval { local $SIG{ALRM}=sub{die"timeout\n"}; alarm $s; exec @ARGV };' "$secs" "$@"
}

HOST="$(bash "$HERE/detect-host.sh")"
# Cross-vendor: if inside Claude, partner is Codex. (Codex-host -> claude -p is
# a future rung; for this machine the partner is always Codex.)
if [ "$HOST" = "codex" ]; then
  echo "adversarial-audit: host is codex; cross-host partner not configured" >&2
  exit 2
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "adversarial-audit: codex CLI not found" >&2
  exit 2
fi

STDERR_FILE="$(mktemp)"
# stdin = prompt; -o = review markdown out; stderr captured & discarded (MCP noise).
if run_with_timeout "$TIMEOUT" codex exec --json -m "$MODEL" \
      -c model_reasoning_effort=high --sandbox read-only --skip-git-repo-check \
      -o "$OUT_FILE" - < "$PROMPT_FILE" >/dev/null 2>"$STDERR_FILE"; then
  if [ -s "$OUT_FILE" ]; then rm -f "$STDERR_FILE"; exit 0; fi
fi
# Surface only the last real error line, not the MCP transport spam.
grep -v -E 'rmcp::|AuthRequired|oauth-protected-resource|Transport channel' "$STDERR_FILE" | tail -3 >&2 || true
rm -f "$STDERR_FILE"
exit 2
```

- [ ] **Step 2: Verify the recursion guard**

Run: `ADVERSARIAL_AUDIT_DEPTH=1 bash ~/.claude/skills/adversarial-audit/lib/call-external.sh /dev/null /tmp/o.md; echo "exit=$?"`
Expected: `exit=1` and a "recursion refused" message.

- [ ] **Step 3: Verify a real cross-host round-trip**

```bash
printf 'Reply with exactly: ENGINE_OK\n' > /tmp/adv-prompt.md
bash ~/.claude/skills/adversarial-audit/lib/call-external.sh /tmp/adv-prompt.md /tmp/adv-out.md
echo "exit=$?"; echo "---out---"; cat /tmp/adv-out.md
```
Expected: `exit=0`, and `/tmp/adv-out.md` contains `ENGINE_OK`. No MCP/rmcp noise printed.

- [ ] **Step 4: Verify degraded signal when codex is masked**

```bash
PATH=/usr/bin:/bin bash ~/.claude/skills/adversarial-audit/lib/call-external.sh /tmp/adv-prompt.md /tmp/adv-out2.md; echo "exit=$?"
```
Expected: `exit=2` (codex not on PATH → degraded), caller will fall back in-session.

- [ ] **Step 5: Commit** (same git guard as Task 1)

```bash
cd ~/.claude/skills/adversarial-audit && git rev-parse --is-inside-work-tree 2>/dev/null \
  && git add lib/call-external.sh && git commit -m "feat(adv-audit): cross-host partner invocation engine" || echo "no git repo"
```

---

### Task 3: Runner subagent spec

**Files:**
- Create: `~/.claude/skills/adversarial-audit/references/runner.md`

- [ ] **Step 1: Write `runner.md`** — a spec the main skill hands to a subagent so Codex's JSONL/stderr never enters the main context.

````markdown
# Adversarial Audit — Runner Subagent

You are a THIN dispatch runner. You do NOT interpret findings. Your only job:
launch the cross-host partner via the engine lib, then return the compact
review markdown.

## Inputs (from the dispatching skill)
- `PROMPT_FILE`: absolute path to the critique prompt
- `OUT_FILE`: absolute path the review markdown should be written to

## Steps
1. Run, capturing the exit code:
   ```bash
   bash ~/.claude/skills/adversarial-audit/lib/call-external.sh "$PROMPT_FILE" "$OUT_FILE"; echo "ENGINE_EXIT=$?"
   ```
2. Take a mutation snapshot guard (the partner is read-only, but verify):
   ```bash
   git -C "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" status --porcelain 2>/dev/null | head
   ```
   If files changed that you did not expect, STOP and report `MUTATION_DETECTED`.
3. Return EXACTLY one of:
   - On `ENGINE_EXIT=0`: the full contents of `OUT_FILE`, prefixed with the line
     `RUNNER_RESULT: cross-host-ok`
   - On `ENGINE_EXIT=2`: the single line `RUNNER_RESULT: degraded`
   - On `ENGINE_EXIT=1`: the single line `RUNNER_RESULT: recursion-refused`

## Hard rules
- Never read or echo the raw JSONL / stderr. Only the `-o` output file matters.
- Never edit any file. Never run the partner with a writable sandbox.
- Never add commentary. Your output is consumed by another agent.
````

- [ ] **Step 2: Verify the file is well-formed**

Run: `sed -n '1,5p' ~/.claude/skills/adversarial-audit/references/runner.md`
Expected: shows the runner heading and intro.

- [ ] **Step 3: Commit** (git guard)

```bash
cd ~/.claude/skills/adversarial-audit && git rev-parse --is-inside-work-tree 2>/dev/null \
  && git add references/runner.md && git commit -m "feat(adv-audit): runner subagent spec" || echo "no git repo"
```

---

### Task 4: Attack profiles

**Files:**
- Create: `~/.claude/skills/adversarial-audit/references/profiles.md`

- [ ] **Step 1: Write `profiles.md`** with the three profiles (concrete prompt bodies).

````markdown
# Attack Profiles

Each profile is a prompt body the dispatching skill fills with the artifact and
sends to the partner. All profiles MUST return findings in the schema defined in
`discipline.md` and obey its severity rubric.

## SPEC profile
> You are an adversarial spec reviewer. Assume this spec will be handed to an
> engineer who will build EXACTLY what it says and nothing more. Your job: find
> every place they would have to guess.
> Probe: hidden assumptions; requirements that are not testable; missing or
> vague acceptance criteria; undefined error/failure scenarios; scope ambiguity
> (in vs out); data/API contracts given as names but not full schemas.
> Triage every finding as ERROR (spec is wrong/contradictory), RISK (spec is
> silent on something that will bite), or PREFERENCE (style).
> Kill criterion you are testing against: "No ambiguity an engineer would need
> to resolve." Do not stop until you have enumerated the sections you reviewed
> and either found a concrete concern in each or justified why it is airtight.
> Verdict: READY / REVISE / RETHINK.

## PLAN profile
> You are an adversarial execution-plan reviewer. Assume this plan will FAIL.
> Prove how. Attack these axes: scope correctness; missing steps; dependency
> ordering; ROLLBACK story (is there one? is it first?); BLAST RADIUS (what
> breaks if a step is wrong, and who is affected); success criteria; cost.
> Production-sacred rules this plan must satisfy — flag any violation:
> (a) reproduce-before-fixing is present for any bug-fix step;
> (b) no same-session fix-the-fix; (c) fixes are upstream, not duplicated in
> 2+ callers; (d) recent history of touched files was checked.
> Verdict: PROCEED / REVISE / RETHINK.

## CODE profile
> You are an adversarial code reviewer running multiple hostile lenses. Apply
> each lens and attribute findings to it:
> - SABOTEUR: "I will break this in production." (unvalidated input, races,
>   swallowed errors, leaks, off-by-one/overflow)
> - SECURITY AUDITOR: OWASP — injection, broken auth, IDOR, secrets, insecure
>   defaults.
> - SKEPTIC: correctness/completeness — unhandled paths, "works on my machine"
>   masquerading as verification.
> - ARCHITECT (only if change is medium/large): boundary violations, coupling,
>   does the design serve the stated goal.
> - MINIMALIST (only if change is large): what can be deleted without losing
>   the goal; abstractions with a single call site.
> Cite file:line for every finding or mark it [UNVERIFIED] and cap it at P2.
> Verdict: SHIP / REVIEW_NEEDED / DO_NOT_MERGE.

## Risk-weighted reviewer ladder (CODE)
Choose lens set by RISK first, size second:
- Any diff touching `opt_outs`, SMS-send paths, RC webhook 200-return, DB
  schema, or that pushes a file past its size cap => force ALL five lenses,
  regardless of line count.
- Else by size: <50 lines/1-2 files => Saboteur+Security+Skeptic; 50-200
  lines/3-5 files => add Architect; 200+ lines or 5+ files => add Minimalist.
````

- [ ] **Step 2: Verify all three profiles present**

Run: `grep -c -E '^## (SPEC|PLAN|CODE) profile' ~/.claude/skills/adversarial-audit/references/profiles.md`
Expected: `3`

- [ ] **Step 3: Commit** (git guard)

```bash
cd ~/.claude/skills/adversarial-audit && git rev-parse --is-inside-work-tree 2>/dev/null \
  && git add references/profiles.md && git commit -m "feat(adv-audit): spec/plan/code attack profiles" || echo "no git repo"
```

---

### Task 5: Discipline layer

**Files:**
- Create: `~/.claude/skills/adversarial-audit/references/discipline.md`

- [ ] **Step 1: Write `discipline.md`** (severity, falsification, schema, calibration).

````markdown
# Discipline Layer (applies to every profile)

## Severity rubric (production-sacred)
- **P0** — ships to a prod-used app WITHOUT a reproduce step, a rollback, or
  opt-out preservation; or directly breaks an SMS-send / webhook / data path.
  Requires `[EXISTING_DEFECT]` evidence (a real, demonstrated defect).
- **P1** — real defect or real cost, not immediately prod-breaking. `[PLAN_RISK]`
  findings are capped here.
- **P2** — should fix soon. Uncited code findings cap here.
- **P3** — nit / polish.

## Falsification gate
A finding may only be P0/P1 if ONE cheap read-only command was actually run to
confirm it (grep/cat/git). State the command and its result in the finding.
Otherwise cap at P2 and label `[UNVERIFIED]`. This enforces reproduce-before-fix
at the review layer.

## Finding schema (every finding)
```
[Pn] <title>                                  [EXISTING_DEFECT|PLAN_RISK] (confidence: low|med|high)
  class:     <defect class, e.g. injection / missing-rollback / untestable-req>
  where:     <file:line or spec section>
  root cause:<the cause, not the symptom>
  evidence:  <command run + result, or quoted artifact text>
  impact:    <quantified blast radius>
  fix:       <ordered remediation>
```

## Calibration rules
- Over-flagging everything a blocker is itself a failure. If >40% of findings
  are P0/P1, re-justify or downgrade.
- Cross-promotion: a finding raised by 2+ lenses is promoted ONE level — but a
  P3 can never reach P0 by promotion alone (cap two-weak-signal chains at P1).
- No "LGTM with no findings" AND no manufactured noise: if an artifact is
  genuinely clean, state the single most fragile assumption and stop.

## Regressions awareness (code/plan)
If the target repo has `docs/architecture/regressions.md`, grep it for the area
the artifact touches and flag repeat-offender zones in the report header.
````

- [ ] **Step 2: Verify schema + rubric present**

Run: `grep -c -E 'P0|Falsification gate|Finding schema|Calibration' ~/.claude/skills/adversarial-audit/references/discipline.md`
Expected: `>= 4`

- [ ] **Step 3: Commit** (git guard)

```bash
cd ~/.claude/skills/adversarial-audit && git rev-parse --is-inside-work-tree 2>/dev/null \
  && git add references/discipline.md && git commit -m "feat(adv-audit): production-sacred discipline layer" || echo "no git repo"
```

---

### Task 6: SKILL.md entry point (orchestration)

**Files:**
- Create: `~/.claude/skills/adversarial-audit/SKILL.md`

- [ ] **Step 1: Write `SKILL.md`** with frontmatter + the full flow.

````markdown
---
name: adversarial-audit
description: Use when you want a vicious, cross-vendor adversarial audit of a spec, an execution plan, or code — before committing to it. Routes critique to Codex (a different vendor model) so blind spots aren't shared, applies a production-sacred severity discipline, and is critique-only (never edits). Trigger on "adversarially audit / red-team / tear apart this spec|plan|code", or "audit uncommitted".
---

# Adversarial Audit

Cross-vendor, critique-only adversarial review. One entry, auto-routes.

## Flow (follow in order)

1. **Scope gate.** Confirm: what artifact, what is in/out of scope, what
   severity bar matters most. If the user pasted nothing, ask for the artifact
   or accept "audit uncommitted" (then use `git diff`).

2. **Classify** the artifact as `spec`, `plan`, or `code` (mixed => dominant;
   ambiguous => ASK, do not guess):
   - numbered steps / phases / "we will" => plan
   - requirements / "the system shall" / acceptance criteria => spec
   - code syntax / diff markers / file paths => code

3. **Attack-surface map.** Before critiquing, enumerate the artifact's
   components, data flows, trust boundaries, and entry points. List them.

4. **Build the critique prompt:** load the matching profile from
   `references/profiles.md` and append `references/discipline.md`. For `code`,
   apply the risk-weighted reviewer ladder. Write the assembled prompt to
   `/tmp/adv-audit-prompt-$$.md` and the artifact content inline.

5. **Dispatch cross-host** via the runner. Launch a subagent with the spec in
   `references/runner.md`, passing `PROMPT_FILE=/tmp/adv-audit-prompt-$$.md` and
   `OUT_FILE=/tmp/adv-audit-out-$$.md`.
   - `RUNNER_RESULT: cross-host-ok` => use the returned review.
   - `RUNNER_RESULT: degraded` => **print the banner below**, then run the
     critique yourself in-session as a multi-persona panel (same profile +
     discipline), clearly marked as a same-model review.
   - `RUNNER_RESULT: recursion-refused` => stop; you are already inside an audit.

   Degraded banner (print verbatim):
   > ⚠️  DEGRADED — cross-vendor principle bypassed. Codex was unreachable, so
   > this is a SAME-MODEL (Claude) review. Treat findings with extra skepticism
   > and re-run with Codex available before trusting a clean verdict.

6. **Discipline pass.** Re-rank every finding against the severity rubric and
   falsification gate. Drop or downgrade anything that fails calibration.

7. **Post-critic hallucination re-check.** For EACH P0 the partner raised, run
   ONE cheap read-only command (grep/cat/git) to confirm it against ground
   truth. If it cannot be confirmed, downgrade and label `[UNVERIFIED]`. State
   which commands you ran.

8. **Report.** Output: a one-line verdict, a header noting any
   regressions-ledger hits, then findings sorted P0→P3 in the schema. End with:
   "Critique-only — no files were modified. Fix in a fresh session
   (no same-session fix-the-fix)."

## Hard rules
- NEVER edit files. NEVER suggest an auto-apply loop.
- NEVER run the partner with a writable sandbox.
- If classify is ambiguous, ASK.
- Codex auth gotcha (only if `codex exec` starts 404-ing): add
  `forced_login_method = "chatgpt"` to `~/.codex/config.toml`. Not needed today.
````

- [ ] **Step 2: Verify frontmatter + all 8 flow steps present**

Run: `grep -c -E '^[0-9]+\. \*\*' ~/.claude/skills/adversarial-audit/SKILL.md; head -3 ~/.claude/skills/adversarial-audit/SKILL.md`
Expected: count `8`, and first lines show `---` / `name: adversarial-audit`.

- [ ] **Step 3: Commit** (git guard)

```bash
cd ~/.claude/skills/adversarial-audit && git rev-parse --is-inside-work-tree 2>/dev/null \
  && git add SKILL.md && git commit -m "feat(adv-audit): SKILL.md orchestration entry point" || echo "no git repo"
```

---

### Task 7: End-to-end verification (all paths)

**Files:** none created — this is the Definition-of-Done gate.

- [ ] **Step 1: Confirm skill is discoverable**

Run: `ls ~/.claude/skills/adversarial-audit/{SKILL.md,references/{runner,profiles,discipline}.md,lib/{detect-host,call-external}.sh}`
Expected: all six files listed, no errors.

- [ ] **Step 2: CODE path, live cross-host.** In a fresh Claude Code session, invoke the skill on a small real diff in ram-ai:

```
/adversarial-audit  audit uncommitted
```
Expected: classifies as `code`, runner returns `cross-host-ok` (Codex output, NOT a Claude same-model review), findings come back in schema, verdict printed, no files modified.

- [ ] **Step 3: Risk-ladder trigger.** Stage a tiny change touching an `opt_outs` reference; re-run. Expected: all five lenses fire regardless of size.

- [ ] **Step 4: SPEC path.** Run the skill against an existing `docs/` spec. Expected: classifies `spec`, surfaces ≥1 real ambiguity, ERROR/RISK/PREFERENCE triage present.

- [ ] **Step 5: Degraded path.** Re-run `audit uncommitted` with codex masked: `PATH=/usr/bin:/bin` is not reachable from inside the skill, so instead temporarily rename the codex binary OR set `ADVERSARIAL_AUDIT_HOST=codex` (forces the "partner not configured" => degraded branch). Expected: the DEGRADED banner prints and an in-session panel still produces findings.

- [ ] **Step 6: Post-critic re-check fires.** Confirm the report names the read-only commands run to confirm each P0.

- [ ] **Step 7: Definition of Done — state explicitly in wrap-up:**
  - [ ] All six files exist and skill is discoverable
  - [ ] Cross-host CODE path verified returning real Codex output
  - [ ] SPEC path verified
  - [ ] Degraded fallback verified (banner + in-session findings)
  - [ ] No file was modified by any run (critique-only confirmed)
  - [ ] Risk-weighted ladder verified on an `opt_outs`-touching diff

---

## Self-Review (completed during planning)

- **Spec coverage:** engine (Tasks 1-2), runner isolation (Task 3), three
  profiles + risk ladder (Task 4), discipline layer incl. falsification gate /
  schema / calibration / post-critic re-check / regressions awareness (Tasks 5-6),
  degraded path + banner (Tasks 2,6), critique-only (Tasks 3,6), macOS timeout
  shim + stderr filter (Task 2). All spec sections map to a task.
- **Placeholders:** none — every script and prompt body is concrete.
- **Consistency:** exit-code contract (0/1/2) is identical across call-external.sh,
  runner.md, and SKILL.md; `RUNNER_RESULT:` tokens match between runner.md and
  SKILL.md step 5; file paths consistent throughout.
