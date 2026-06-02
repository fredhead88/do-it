# Report Format — read this before writing the final report

The reader is tired and may read ONLY the top. The whole report must let them
get three things in ~10 seconds and stop: **what it's about · why it matters ·
what to do.** Technical detail lives BELOW a rule, for when they care.

This is the presentation layer. The cross-host critic returns findings in the
`discipline.md` schema (P0–P3); you TRANSLATE that into the format below.

## Severity markers (glyph + word tag — never rely on color)
Filled→empty gives a grayscale severity gradient; the word tag removes ambiguity.

| internal | shown as | means |
|---|---|---|
| P0 | `■ BLOCKER` | data loss / prod break / opt-out or compliance / ships unsafe — do not ship |
| P1 | `▲ HIGH`    | real defect or cost — fix this cycle |
| P2 | `● MED`     | should fix soon |
| P3 | `· LOW`     | nit / polish |

## Verdict mapping (profile verdict => canonical VERDICT line)

Profiles emit their own verdict tokens; translate to the canonical four below.
The VERDICT line in the report is ALWAYS one of the right-hand values.

| profile verdict (spec/plan/code) | canonical VERDICT |
|---|---|
| `READY` / `PROCEED` / `SHIP`     | `SHIP IT — clean` |
| `REVISE`                         | `REVISE`          |
| `REVIEW_NEEDED`                  | `REVIEW NEEDED`   |
| `RETHINK` / `DO_NOT_MERGE`       | `DO NOT SHIP`     |

If the partner emits a token not in this table, map by severity of the worst
finding (any P0 => `DO NOT SHIP`; P1 => `REVISE`; else `REVIEW NEEDED`).

## The template (emit exactly this shape)

```
# <VERDICT> — <artifact in 4–6 plain words>

**What it's about:** <one plain sentence — what was audited, no jargon>
**Why it matters:** <one plain sentence — the single worst consequence>
**What to do:** <one plain sentence — the ONE next action>

> **<n> blocker · <n> worth-fixing · <n> minor.** Stop here if that's all you have time for.
<if a regressions-ledger hit: one plain italic sentence here, else omit>

---

## The worst thing

<one short plain-language paragraph: what breaks, who it hits. No file:line, no codes.>

**Fix:** <one line.>

---

## Everything else (only if you care)

### ■ BLOCKER-1 — <short title>
**Where:** `file:line`
**Why it matters:** <one sentence>
**Fix:** <one line>

### ▲ HIGH-1 — <short title>
**Where:** `file:line`
**Why it matters:** <one sentence>
**Fix:** <one line>

<● MED / · LOW the same way; add **Evidence:** or **Root cause:** only when it earns its place>

---
_Critique-only — no files were modified. Fix in a fresh session (no same-session fix-the-fix)._
```

## Hard rules
- **VERDICT line** is one of: `DO NOT SHIP`, `REVISE`, `REVIEW NEEDED`, or `SHIP IT — clean`.
- **The top three lines are sacred:** one sentence each, plain English. BANNED above "The worst thing": P-codes, `file:line`, lens names, "schema", "FK", any acronym a tired reader must decode.
- **Severity-sorted, worst first.** Never file-order.
- **"The worst thing"** shows only the single highest-severity finding as prose. If there is NO blocker/high (clean or only minor), replace this whole section with one reassuring line and skip to detail.
- **Detail findings are scannable, not monoliths:** heading with glyph+tag+ID, then 2–4 bold inline labels — one short line each. Never a multi-line blockquote. Never every field stacked in a code block.
- **One blank line between every block.** Use `---` between major sections, not between every finding's fields.
- **Keep the count honest.** If everything is a blocker, the top line is worthless — apply the calibration rule in `discipline.md` first.
- **Falsified findings** (a P0 the post-critic re-check killed): do NOT list among findings. If worth mentioning, one line at the very bottom: "Checked and cleared: <thing> — not a real issue because <reason>."
- **Degraded run:** print the DEGRADED banner ABOVE the `# VERDICT` line, untouched.
