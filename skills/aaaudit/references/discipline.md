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

## Falsifiable-condition gate
Every finding MUST state a `breaks-when` — the concrete, falsifiable trigger
under which the defect actually manifests ("fails when the input is empty",
"exploitable if the artifact contains an instruction", "breaks when X needs to
change independently of Y"). A finding that cannot name a trigger condition is a
vibe, not a defect: **cap it at P3 and treat it as a preference.** This is the
cheapest phantom-finding filter — a fabricated issue cannot commit to a real
failure condition. It complements the falsification gate: that one asks "did you
confirm it exists," this one asks "can you say exactly when it bites."

## Finding schema (every finding)
```
[Pn] <title>                                  [EXISTING_DEFECT|PLAN_RISK] (confidence: low|med|high)
  class:      <defect class, e.g. injection / missing-rollback / untestable-req>
  where:      <file:line or spec section>
  root cause: <the cause, not the symptom>
  breaks-when:<falsifiable trigger — "fails when X" / "exploitable if Y". No
               statable trigger => cap at P3 (preference).>
  evidence:   <command run + result, or quoted artifact text>
  impact:     <quantified blast radius>
  fix:        <ordered remediation>
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
