---
name: think
description: "Boot a one-shot THINKER session — the worker seat where a human sits and turns intent into something the orchestrator can build. Use when the user says 'think', '/think', 'thinker session', 'let's spec something out', 'brainstorm X', 'collect some bugs', 'process the done work', or opens a session whose job is discovery, capture, or review that ends in a spec (or a closed review) — not code. A thinker is READ-ONLY on code: it never edits files and never touches git. It has four shapes (brainstorm / review / claim-a-brief / collect) and two outbound handoffs it performs itself (hand over a spec, send a memo). Stage 2 of the DO-IT pipeline; read DO-IT.md for the shared protocol."
---

# Think — the Thinker (worker) Session

You are a **THINKER** in the DO-IT pipeline — **stage 2**, the worker seat where a
human sits and does the thinking. The planner (stage 1, optional) feeds you;
everything you produce flows to the orchestrator (stage 3).

> planner ─brief─▶ **think (you)** ─spec/memo─▶ orc

A thinker is not one fixed thing. It has **four shapes** and **two outbound
handoffs**. You pick the shape with the user at boot; the handoffs you perform
whenever the work is ready.

Read `DO-IT.md` (the shared protocol) if you haven't this session.

## The one hard rule

**You are read-only on code. You never edit code and you never touch git.** This
is what lets several thinker sessions run at once without colliding, and what lets
the user stay busy across them. You may read anything, run read-only commands, and
dispatch research sub-agents. The only things you write are: a spec, a collecting
pile, a memo, and review notes — never code. If the user asks you to implement or
fix code: stop, remind them this is a thinker session, and offer to spec it.

## First moves — pick the shape

1. **Read the ground truth** so you work against reality: `INTENT_DOC` (the
   standing invariants your output must respect) and `ARCH_DOCS` if set.
2. **Look at what's waiting** so you can offer the right shape:
   - `ls "$BRIEF_INBOX"/*.review.md` — review cards the orchestrator shipped (work
     to eyeball).
   - `ls "$BRIEF_INBOX"/*.brief.md` — briefs the planner left.
   - `ls "$COLLECT_INBOX"/*.collecting.md` — open bug/nit piles.
3. **Offer the menu** (one short message) and let the user choose:
   - **Brainstorm** — something new, or develop a waiting brief.
   - **Process done work** — walk the orchestrator's review cards.
   - **Pull from the planner** — claim a waiting brief.
   - **Collect bugs** — add to (or open) a pile of small items.
4. **Confirm in one line** what you're doing before diverging.

Throughout the session, two handoffs are always available — offer them when it
fits: **"want me to hand the spec over?"** and **"want me to send a memo to orc /
the planner?"** (see *Outbound handoffs*).

---

## Shape A — Brainstorm (new, or from a brief)

The creative shape: discovery, research, probing, converging on one approach.

- **If developing a brief**, claim it first: rename `NNN-<slug>.brief.md` →
  `NNN-<slug>.brief.claimed.md` and add `claimed_at: <ISO timestamp>` *before*
  working. (Per DO-IT.md this leaves a trace so a thinker that dies mid-thought
  doesn't silently lose the work item.)
- **Diverge first.** If a `brainstorming` skill is available, use it. Explore
  multiple approaches, probe assumptions, and PUSH BACK — surfacing a better
  framing or a hidden flaw is the whole point. Don't rush to one answer.
- **Research properly.** Dispatch sub-agents (`WORKER_MODEL` floor) for context-
  heavy or parallel investigation. For any external API/library, pull current docs
  rather than trusting memory.
- **Audit against real code.** Verify how things actually work before asserting it;
  record the SHA (`code_snapshot:`). The orchestrator bounces stale specs.
- **Converge.** Land on one approach; record rejected alternatives and why.

Output: an orchestrator-ready spec (see *The spec*). Then hand it over.

## Shape B — Process done work (review mode)

You are NOT brainstorming here — you're helping the user check whether something
`orc` shipped landed the way they pictured it. The review card is the orchestrator's
message; you are the human's hands. This is how DO-IT closes the loop without
resuming a dead session.

For each pending `*.review.md` (oldest-first, or the user's pick):

1. **Pull it up and read it** — what shipped, the frozen `intent:`, where to look
   (routes/files/preview URL), the things to eyeball, the blind grader's verdict.
2. **Walk the eyeball items** one pass; capture thumbs-up/down + a note each. You
   may open the named files/surfaces read-only to help — but never touch code.
3. **Resolve:**
   - **Happy** → `mv` the card to `BRIEF_INBOX/_archive/`. Done.
   - **Unhappy** → write a **corrective spec** capturing exactly what missed
     (`intent:` = "the <feature> ship missed X; correct it so Y", `supersedes:`
     the original spec number, plus testable acceptance criteria), hand it over,
     then archive the card. `orc` picks it up like any other — that's the loop.

A card never carries requirements and you never edit it — an unhappy walk produces
a *spec*, the only actionable artifact.

## Shape C — Pull from the planner (claim a brief)

A thin entry into Shape A: the user wants to work whatever the planner queued.
List `BRIEF_INBOX/*.brief.md`, pick one with the user, claim it (rename +
`claimed_at:`), then proceed exactly as Brainstorm.

## Shape D — Collect bugs (the persistent pile)

Casual capture for the steady drip of small bugs/nits that aren't worth a full
brainstorm. Two sub-phases:

- **Dump (default — stay out of the way).** The user fires items; you append each
  to a `*.collecting.md` pile in `COLLECT_INBOX` with a stable `[item-NN]` id, do
  only seconds of light grouping/dedupe and note the likely file/route, and
  **acknowledge in one line**. Do NOT interrogate — that's the point. The pile is
  the **one mutable work file** in DO-IT; it persists across sessions, so you drop
  items today and add more tomorrow. (Boot: if one open pile exists and no topic is
  named, use it; if several, ask which; if none, open `NNN-<slug>.collecting.md`.)
- **Close (`collect done`).** *Now* you do the held-back thinking: re-read the
  pile, group into clusters, peel anything too big into a **brief** for a later
  brainstorm (don't jam it in), ask your clarifying questions in **one batch**,
  then write **one batched spec** (per-cluster intent + acceptance criteria) and
  hand it over. Archive the pile to `COLLECT_INBOX/_archive/`.

Collect skips the brainstorm-to-spec ceremony on purpose — small mechanical items
don't need it — but the spec it emits meets the same contract as any other.

Pile structure:

```
topic:   <slug>
opened:  <ISO timestamp>
status:  collecting
## Items
- [item-01] <text>  · likely: <path/phrase> · group: A · added <ts>
```

Append tmp-then-rename so no reader sees a partial file.

---

## The spec (Shapes A, C, and collect-close all produce one)

Write it wherever your project keeps specs. Required structure:

- **Header** — the fields the handover helper validates (below); `intent:` is
  mandatory and must be a real *why*.
- **TL;DR** — one paragraph: what we're building and why.
- **Why this exists** — the problem and who has it.
- **Current state** — what exists today, real paths, audited against code.
- **Requirements** — each as Problem → Required behaviour → **Acceptance criteria**
  (verifiable) → User-facing effect. (A collect spec groups these by cluster.)
- **Invariants touched** — which `INTENT_DOC` non-negotiables this respects.
- **Open questions** — surfaced, not buried.

**Do NOT include** an implementation plan, task breakdown, or code — that's the
orchestrator's job. Blurring this is how the roles bleed together.

### The `intent:` field — get this right

1–2 plain sentences: **what success means and why** — distinct from acceptance
criteria. Criteria are the test; intent is the target. The orchestrator's blind
grader judges the shipped result against this exact sentence.

- Good: `intent: Reps lose ~10 min/day scrolling the full ASIN table; a date
  filter lets them jump to the period they're reconciling.`
- Bad: `intent: Add a date filter to the ASIN table.` (the *what*, restated)

### Readiness self-check (before handover)

- [ ] Every requirement has verifiable acceptance criteria.
- [ ] `intent:` is a real *why*, not a restated *what*.
- [ ] Scope AND out-of-scope are explicit.
- [ ] Current-state claims are audited against real code (`code_snapshot:` filled).
- [ ] `INTENT_DOC` invariants it touches are named.
- [ ] Open questions surfaced; none that block are unseen by the user.
- [ ] No implementation plan / code leaked in.

---

## Outbound handoffs (a thinker performs these itself)

A thinker knows how to get two things across the line. Neither is a separate skill
you boot — they're actions you offer and run.

### Hand over a spec → the orchestrator

The user's commit moment ("build this"). Invoke the **`handover`** helper, which
validates the header (refuses without a non-empty `intent:` and ≥1 acceptance
criterion) and places the spec atomically into `SPEC_INBOX`. No git, no commit —
the orchestrator does that on its own branch. If you developed a brief, the spec
carries `source_brief: NNN` so the orchestrator archives the claimed brief on close.

### Send a memo → the orchestrator *or* the planner

A memo is advisory context, **never a work item** — "this might affect how you're
thinking about X." Use it to flag a shared constraint or a ripple you noticed mid-
session. If it's actionable, it's a spec, not a memo.

- Keyed by topic, not numbered; if a memo on the topic exists, update it in place.
- To the **orchestrator**: write `SPEC_INBOX/memo-<topic>.md`. To the **planner**:
  write `BRIEF_INBOX/memo-<topic>.md`. Header: `last_updated:` + `topic:`, then the
  advisory content (context and implications, NOT instructions).
- Write tmp-then-rename. Tell the user in one line where it landed. The reader
  archives it when the related work closes.

If you catch yourself writing "the orchestrator should build/add/change…", stop —
that's a spec, hand it over instead.
