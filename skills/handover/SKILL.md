---
name: handover
description: "Drop a finished spec into the orchestrator's inbox so a separate orchestrator session can pick it up by reading a file path. Use when the user says 'handover', '/handover', 'hand this spec over', 'ship the spec', 'send this to the orchestrator', or any time a spec written in this session needs to reach the orchestrator on the same machine. Validates the spec header (refuses to drop without an intent: and acceptance criteria) and places the file atomically. NO git, NO commit — the orchestrator owns git. Part of the DO-IT pipeline; read DO-IT.md for the shared protocol."
---

# Handover — Drop the Spec, Don't Push It

You wrote a spec in this thinker session. The orchestrator (a separate session on
this machine) needs it. The simplest reliable transfer: validate the header, copy
the file into the shared inbox, done. **This is the user's commit moment** — when
they invoke handover, they're deciding "build this." There is no second gate on
the orchestrator side; don't add one.

Read `DO-IT.md` for the shared protocol (lanes, numbering, header fields). This
skill just does the drop.

## When this is the wrong skill

If the spec hasn't been written yet, stop — go back to `think`. Handover moves a
*finished* spec; it doesn't write one.

## Protocol

### Step 1 — Validate the header (fail here, not at build time)

The spec must carry this header. **Refuse to drop and tell the user what's missing
if any required field is absent or empty:**

```
topic:          one line
intent:         1-2 plain sentences — what success MEANS and WHY
                (REQUIRED, non-empty; this is the why, distinct from acceptance)
source_brief:   NNN | none
code_snapshot:  <git sha the spec was audited against>
acceptance:     N criteria, N > 0 (REQUIRED)
target_paths:   files/dirs in scope
supersedes:     NNN | none   # set only on a resubmission after a bounce
ignore:         e.g. "the rejected-alternatives section is context, not a requirement"
```

Hard checks (these are the whole reason handover validates instead of the
orchestrator discovering it later):
- `intent:` present and non-empty.
- `acceptance:` has at least one criterion.
- If either fails, **do not drop.** Say exactly which field is missing and stop.

Sanity-nudge (warn, don't block): if `intent:` reads like a restated *what*
("add X", "build Y") rather than a *why*, point it out — the orchestrator's blind
grader judges against this sentence, so a weak intent yields a weak grade.

### Step 2 — Make sure the inbox exists

```bash
mkdir -p "$SPEC_INBOX" "$SPEC_INBOX/_archive"
```

(`SPEC_INBOX` from the CONFIG block in `DO-IT.md`.)

### Step 3 — Allocate the number and place the file atomically

Per DO-IT.md numbering: `NNN = max(live + _archive in SPEC_INBOX) + 1`,
zero-padded to 3. Then write-tmp-then-rename so no reader sees a partial file:

```bash
cp <spec-path> "$SPEC_INBOX/NNN-<slug>.spec.md.tmp"
mv "$SPEC_INBOX/NNN-<slug>.spec.md.tmp" "$SPEC_INBOX/NNN-<slug>.spec.md"
```

One person launches every session by hand, so there's no real write race — plain
allocation is fine. If a same-numbered file somehow already exists, bump to NNN+1
and tell the user.

### Step 4 — Verify

```bash
ls -la "$SPEC_INBOX"/*.spec.md
```

Confirm your file is there and non-empty. Other pending specs in the inbox are
NORMAL — it's a queue, not a single slot. They're other handovers; leave them
alone, just note the count.

### Step 5 — Tell the user, briefly

One or two lines: the file landed at `<path>`, the inbox holds N pending spec(s),
and the orchestrator will pick it up on its next boot or turn. No paste-block to
copy — the orchestrator reads the inbox directly. You're done.

## Notes

- The inbox lives outside any git repo (default `~/.claude/spec-inbox`) so any
  session on the machine can reach it regardless of which repo it's in.
- This skill does **no git operations**. If asked "should I also push?", the
  answer is no — the orchestrator commits on its own clean branch; one source of
  truth is cleaner than two.
- Nothing is deleted, ever. The orchestrator archives the spec into
  `_archive/` on close; that frozen copy is what its grader audits against.
