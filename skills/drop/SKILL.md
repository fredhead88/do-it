---
name: drop
description: "Drop an advisory memo into the orchestrator's inbox — a 'this might affect how you're thinking' note that is context, never a work item. Use when the user says 'drop', '/drop', 'leave a memo for the orchestrator', 'note this for orc', or when a thinker session realizes something relevant to other in-flight work mid-brainstorm. A memo is updatable in place and is structurally NOT a spec — the orchestrator never builds from it. An ADVANCED add-on to the DO-IT pipeline; read DO-IT.md for the shared protocol."
---

# Drop — Advisory Memo

You are dropping a **memo** into the orchestrator's inbox: advisory context, not
work. "This might affect how you're thinking about X." The orchestrator reads
memos as standing context and **never builds from one**. That separation is the
whole point — if it's actionable, it's a spec and belongs in `handover`, not here.

Read `DO-IT.md` for the shared protocol.

## When this is the right skill

- A thinker realizes mid-brainstorm that something affects other in-flight work
  (a shared constraint, a gotcha, a decision that ripples).
- The user wants to leave the orchestrator a heads-up that isn't a build request.

## When it is the WRONG skill

If you're trying to get something *built*, this is the wrong skill — that's a
spec, via `handover`. A memo with requirements smuggled into it is a bug. If you
notice you're writing "the orchestrator should build/add/change…", stop: that's a
spec.

## Protocol

### Step 1 — One topic per memo

A memo is keyed by topic, not numbered. Pick a stable `<topic>` slug — if a memo
on this topic already exists, you'll **update it in place**, not make a second one.

### Step 2 — Write it

```bash
mkdir -p "$SPEC_INBOX"
```

File: `SPEC_INBOX/memo-<topic>.md`, with a `last_updated:` header so the
orchestrator can tell a stale memo from a fresh one:

```
last_updated: <ISO timestamp>
topic: <topic>

<the advisory content — what you noticed and why it might matter to in-flight
work. Context and implications, NOT instructions.>
```

Write tmp-then-rename so no reader sees a partial file:

```bash
# write to memo-<topic>.md.tmp, then:
mv "$SPEC_INBOX/memo-<topic>.md.tmp" "$SPEC_INBOX/memo-<topic>.md"
```

If updating an existing memo, refresh `last_updated:` and keep the topic slug.

### Step 3 — Confirm, briefly

One line to the user: memo on `<topic>` dropped/updated at the path. Done. The
orchestrator picks it up as context on its next turn; the memo is archived by the
orchestrator when the related spec closes, so it won't rot in the lane.
