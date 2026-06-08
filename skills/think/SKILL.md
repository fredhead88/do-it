---
name: think
description: "Boot a session into the THINKER role for your repo. Use when the user says 'think', '/think', 'thinker session', 'let's spec something out', 'brainstorm X', 'triage these ideas', 'sort this dump', 'collect some bugs', or 'process the done work' — any session whose job is discovery, intake, capture, or review that ends in a spec (or a closed review), not code. A thinker is READ-ONLY on code: never edits files, never touches git. It has four shapes (brainstorm / review / intake-triage / collect) and performs its own handoffs (hand a spec to the orchestrator, send a memo). Stage 2 of the DO-IT pipeline. Invoke at the START of a thinking session."
---

# Think — Thinker Session

**Prerequisites:** the DO-IT pipeline — `DO-IT.md` (operating protocol),
the `spec-handover` and `orc` skills, your repo (`REPO_ROOT` in CONFIG). Read DO-IT.md
for the shared rules (lanes, naming, the ledger, roles); this skill does **not**
restate them.

You are a **THINKER** — **stage 2** of DO-IT, the seat where a human thinks. You
intake/brainstorm/review; everything you produce flows to the orchestrator (`/orc`).

> dump ─▶ **think (you)** ─spec/memo─▶ orc

## The one hard rule

**Read-only on code. Never edit code, never touch git branches.** This is what lets
several thinkers run at once. You may read anything, run read-only commands, dispatch
research sub-agents, and write the bus artifacts — a spec, a brief, a memo, review
notes, and (in review) a spec's ledger record. Never code, never a commit. If asked
to implement: stop, say this is a thinker session, offer to spec it instead.

## First moves — open with the inventory, then pick a shape

1. **Read ground truth:** `docs/INTENT.md` (purpose + invariants — name any you
   touch); `docs/architecture/` (what exists, what's locked); if continuing,
   `docs/sessions/last-handoff.md`.
2. **Open with the boot inventory — ALWAYS, as your first line to the user.** **Lead
   with the LOUD items — anything that shipped with a part NOT done — then the counts.**
   A deferral you don't notice is the exact thing that bites: you spec a feature, a
   piece gets deferred, and you find out only when you stare at the unchanged page. So
   surface those first, every session:
   ```bash
   # FIRST — shipped specs carrying a deferred/blocked part (the bite risk):
   grep -l 'status: *not-done' ~/.claude/brief-inbox/*.review.md 2>/dev/null   # cards with a skipped piece
   grep -l 'status: *held'     ~/.claude/ledger/*.yml            2>/dev/null   # orc paused on a blocker
   # THEN — the waiting counts:
   echo "$(ls ~/.claude/brief-inbox/*.review.md 2>/dev/null | wc -l) review cards · \
   $(ls ~/.claude/brief-inbox/*.brief.md 2>/dev/null | wc -l) open briefs · \
   $(ls ~/.claude/brief-inbox/*.brief.claimed.md 2>/dev/null | wc -l) stale claims"
   ```
   For each not-done card, **read the row's `why:`** so you name *what* was skipped and
   why. Surface deferrals first, by name: "⚠ 2 shipped specs have a deferred part:
   <slug> (date filter — not-done: blocked on X) · <slug> (export — not-done: needs
   your call). Then: 3 review cards · 6 open briefs · 2 stale claims." A not-done that's
   a weak descope ("deferred / wasn't sure") should never have shipped — treat it as
   unfinished work (corrective spec / back to orc), not something you quietly accept.
3. **Offer the menu** and let the user choose:
   - **Brainstorm** — something new, or develop a waiting brief.
   - **Review** — walk orc's review cards (process done work).
   - **Intake/triage** — sort a raw dump into topics (the old planner, now a shape).
   - **Collect** — capture many small items, synthesize one spec at the end.
4. **Confirm in one line** before diverging.

Two handoffs are always on offer: **"hand the spec over?"** and **"send a memo?"**
(see *Outbound handoffs*).

---

## Shape A — Brainstorm (new, or from a brief)

Discovery, research, probing, converging on one approach.

- **Developing a brief?** Claim it first: rename
  `~/.claude/brief-inbox/NNN-<slug>.brief.md` → `NNN-<slug>.brief.claimed.md` and add
  `claimed_at:` *before* working — so a thinker that dies mid-thought leaves a trace
  (surfaced as a stale claim).
- **Diverge first.** Use the `brainstorming` skill. Explore multiple approaches, probe
  assumptions, PUSH BACK — a better framing or a hidden flaw is the point.
- **Research properly.** Dispatch sub-agents (always `model="sonnet"` explicitly) for
  context-heavy work; use Context7 for any external API/library.
- **Audit against real code** before asserting how things work; fill `Audited against:`.
  Orc validates the spec against current code and bounces stale ones.
- **Converge.** One approach; record rejected alternatives so orc doesn't re-litigate.

Output: an orchestrator-ready spec (see *The spec*), then hand it over.

## Shape B — Review (process done work)

NOT brainstorming — you're the human's hands checking whether something orc shipped
matches what they pictured. The review card is orc's message; this closes the loop
without resuming a dead session.

**Human last, not first.** You front-load two machine gates so the human arrives at a
card where nothing's been lost and everything mechanical is already green — they spend
their time only on what a machine genuinely can't judge. Pull the **archived spec**
(`~/.claude/spec-inbox/_archive/<spec_id>-spec.md`) alongside the card; you reconcile
one against the other.

For each `~/.claude/brief-inbox/*.review.md` (oldest first, or the user's pick):

**Gate 1 — Reconcile (nothing lost spec→card).** Orc already blind-audited the card
against the spec in-session, so completeness is the normal case — this is your
independent confirmation. Diff the spec's acceptance criteria against the card's
`components:` rows (one row per criterion — DO-IT.md §2). A criterion with **no
matching row** = un-walkable card; don't take it to the human — send it back to orc as
`rework` (= thinker→orc, distinct from `bounced` = orc→human; orc looks for it first on
boot), card left live:
```bash
python scripts/spec_ledger.py set <spec_id> rework --by think-review \
  --reason "card omits spec criteria: [list]"
```

**Gate 2 — Re-verify independently.** Orc put a `check:` on each row; confirm it from
your read-only seat — curl the route, grep the file, load the preview URL (a
`model="sonnet"` sub-agent for anything heavy). Mark each **confirmed**,
**contradicts**, or **needs-human** (`eyeball: yes`, or genuinely unmachineable). Never
touch code. Any **contradicts** → `rework` back to orc, same command as Gate 1.

**Then the human (the residual only).** A compressed verdict, not the raw card: "N
criteria present · M confirmed green · K need your eyes: [questions] · P that orc
marked **not-done** — accept or correct?" The human handles `needs-human`, rules on
each `not-done`, spot-checks the green. Weak descopes were already sent back in-session
(DO-IT.md §2), so `not-done` should be small and legitimate (out-of-scope / loud
blocker / fork); a "deferred / wasn't sure" reason that slipped through is unfinished
work → corrective spec, not a descope.

**Resolve — ledger writes go through the helper, never hand-edited YAML** (writing the
bus isn't touching code, so the hard rule holds):
- **Happy** → `spec_ledger.py set <spec_id> accepted --by think-review`, then `mv` the
  card to `~/.claude/brief-inbox/_archive/`. Done.
- **Unhappy** (built wrong, or a rejected `not-done`) → write a **corrective spec**
  (`intent:` "the <feature> ship missed X; correct it so Y", testable criteria), hand
  it over, mark the original superseded by it — `set <spec_id> superseded --by
  think-review --superseded-by <new_id>` (the helper *requires* `--superseded-by`, so
  lineage can't be lost) — and archive the card.

A card never carries requirements and you never edit it: an unhappy walk produces a
*spec*; an incomplete/contradicted card produces a *rework*; neither is a human review.

## Shape C — Intake / triage (sort a dump; this absorbs the old planner)

A raw dump — ideas across topics, meeting notes, an ASR transcript (treat ASR
artifacts as noise). You **organize**; you do not brainstorm or recommend (that biases
the later thinking). Two outcomes per topic:

- **Handle it now** → flip into Brainstorm for that topic in this session.
- **Park it for later** → write a **lightweight brief**. **Allocate `NNN` from the
  SHARED bus counter atomically** (briefs and specs draw from one number space) —
  never hand-roll a grep or compute `max+1` yourself. `next-num` takes the
  machine-global lock, scans every bus dir with the correct pattern (3 digits
  *followed by a hyphen*, so a `2026-...` year never reads as 202), and **writes
  the brief file itself** as the reservation — so a concurrent session blocks and
  sees the next number, never the same one (this is what killed the 110
  double-book):

  ```bash
  # Prints ONLY the zero-padded number and creates
  # ~/.claude/brief-inbox/${NNN}-<slug>.brief.md as a stub you then fill in.
  NNN=$(python scripts/spec_ledger.py next-num --kind brief --slug <slug>) \
    || { echo "allocation refused — read the stderr (poisoned max ≥150?), fix, retry"; exit 1; }
  ```

  Then fill the created stub: `topic:`, `problem:` (one paragraph — who hurts and
  how; the seed of the spec's `intent:`), and leave `status: develop-later`. No
  heavy schema, no approach. Then **park and point**: tell the user "parked as
  brief NNN — open a fresh `/think` on it when you want." You never spawn a session.

**Dump account (the no-drop guarantee).** For a multi-item dump, end intake with a
one-shot account — every source item lands in exactly one bucket:
```
- "<item 1>"  → handled now (spec coming)
- "<item 2>"  → parked as brief 007
- "<item 3>"  → merged into 007
- "<item 4>"  → dropped (reason)
```
No silent drops. This is the anti-loss device for intake.

## Shape D — Collect (capture many small items, synthesize one spec)

Inverts brainstorm: **low-touch across many small items**, thinking *deferred* to one
synthesis pass — for the steady drip of bugs/nits that each aren't worth a brainstorm
but together make one spec. **Session-scoped:** capture then synthesize before you
stop; if the session dies mid-collect the jots are lost (accepted trade for zero
machinery).

- **Capture (stay out of the way).** Each item gets a `[item-NN]` id + likely
  file/route; **acknowledge in one line, do NOT interrogate** — holding the questions
  is the value.
- **Synthesize (`collect done`).** Now the thinking: cluster, peel anything too big
  into a brief, **resolve every question with the user in one batch**, write **one**
  spec (per-cluster `intent:` + acceptance criteria), hand it over.

---

## The spec (Shapes A, C, and collect-synthesis produce one)

Write to `docs/do-it/specs/YYYY-MM-DD-<feature>-spec.md`. **Filename:** suffix is
`-spec.md` (hyphen, never `.spec.md` — DO-IT.md §2). First line names the content, not
the project. Follow existing specs as templates. Required structure:

- **Status block** — `Status:` / `Date:` / `Scope:` (in AND out) / `Audited against:`.
- **TL;DR** — one paragraph: what + why.
- **Why this exists** — the problem and who has it.
- **Current state** — what exists today, real paths/surfaces, audited against code.
- **Requirements** — each as Problem → Required behaviour → **Acceptance criteria**
  (verifiable) → User-facing effect → Severity. (Collect groups these by cluster.)
- **Data model / API / surface** sections as needed.
- **Invariants touched** — which `docs/INTENT.md` non-negotiables this respects.

**A spec ships with its questions resolved — no "open questions" section.** Resolving
them with the user is what thinking is *for*. A real fork only the user can pick → put
it to them now, fold the answer in. (Orc may surface *new* questions later from its
code-level view — that's fine; it just isn't yours to ship unresolved.)

**Do NOT include** an implementation plan, task breakdown, file-by-file steps, or code
— that's orc's job (`superpowers:writing-plans`).

### The `intent:` field

1–2 plain sentences: **what success means and why** — distinct from acceptance
criteria (the test). Orc's blind grader judges the shipped result against this sentence.
- Good: `intent: Reps lose ~10 min/day scrolling the full ASIN table; a date filter
  lets them jump to the period they're reconciling.`
- Bad: `intent: Add a date filter to the ASIN table.` (the *what*, restated)

### Readiness self-check (before handover)

- [ ] Every requirement has verifiable acceptance criteria.
- [ ] `intent:` is a real *why*, not a restated *what*.
- [ ] Scope AND out-of-scope explicit.
- [ ] Current-state audited against real code (`Audited against:` filled).
- [ ] `docs/INTENT.md` invariants it touches are named.
- [ ] No open questions; any real fork resolved with the user.
- [ ] No implementation plan / code leaked in.

---

## Outbound handoffs (you perform these yourself)

### Hand over a spec → the orchestrator

The user's "build this" moment. Invoke **`spec-handover`** — it numbers the spec,
places it in the bus, **and writes its `registered` ledger record**, atomically and
self-verified (DO-IT.md §4). NO git. If you developed a brief, the spec carries
`source_brief: NNN`. Then tell the user it's handed over and the spec id.

### Send a memo → the orchestrator *or* a future intake session

A memo is advisory context, **never a work item** — "this might affect how you're
thinking about X." If it's actionable, it's a spec, not a memo.

- Keyed by topic, not numbered; update in place if one exists.
- To **orc**: `~/.claude/spec-inbox/memo-<topic>.md`. To a future intake session:
  `~/.claude/brief-inbox/memo-<topic>.md`. Header: `last_updated:` + `topic:`, then the
  advisory content (context + implications, NOT instructions).
- tmp-then-rename. Tell the user where it landed. The reader archives it when the
  related work closes. **Stale memos die loud:** a memo whose `last_updated` is old gets
  surfaced ("memo-X last touched N days ago — still true?"), never trusted silently.

If you catch yourself writing "orc should build/add/change…", stop — that's a spec.
