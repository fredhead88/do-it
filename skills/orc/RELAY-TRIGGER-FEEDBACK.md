# Skill feedback — orc relay false-trigger (2026-06-02)

For the session tuning these skills. One real incident from a live orc run:

I invoked the "~70% context → write the relay baton and stop" rule during an active, productive
session **with no actual context-pressure signal** — no autocompact warning, no harness context-limit
notice, nothing. I just self-*estimated* that "a lot of work" (≈8 worker dispatches, several large
file reads, multiple builds/deploys) added up to saturation, and reached for the threshold. The orc
skill frames "~50% used / ~70% used" as if the orchestrator can observe its own context fraction, but
in practice the agent **cannot read that number**, so the threshold degrades into a vibe and biases
toward a premature handoff. That's expensive: a relay forces the user to re-boot a fresh `/orc` and pay
the cold-start re-derivation, for no benefit, right when momentum was good. Recommend: (1) stop keying
the relay on an unobservable percentage; trigger it on **observable** signals instead — an actual
autocompact/context-limit warning from the harness, repeated tool failures, visibly degraded output, or
an explicit user cue; (2) add a line telling the orchestrator **not to self-estimate context fraction**;
(3) make the default posture "keep working and checkpoint the ledger as you go" — the relay baton is for
a genuine forced handoff, not a tidy stopping point an idle orchestrator talks itself into.
