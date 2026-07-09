"""
builder_closeout_verdict.py — Spec 296 R1-AC2 / Spec 357

Decision logic for the builder close-out gate.  The ``closeout-grader``
sub-agent returns a structured VERDICT dict; this module inspects that dict
and produces a three-way ``"ready"`` / ``"owed-data"`` / ``"regrade"``
decision WITHOUT any build log text entering the calling context.

Keep in sync with: do-it-starter/skills/builder/SKILL.md (close-out section)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Pass-value coercion + JSON extraction (2026-07-03 corrective)
# ---------------------------------------------------------------------------
# The gating-watch grader PROMPT asks claude for boolean `<true|false>` for
# matches_intent / card_ok, but the original decision logic compared against the
# string "yes" — so the real (boolean) grader output ALWAYS regraded. Every spec
# reaching this code false-failed close-out (surfaced by spec 334, the first spec
# to run the live prompt→parser path after the cron-PATH grader fix). Accept both
# the real boolean form and the legacy string forms, fail-closed on anything else.
_TRUTHY_PASS: frozenset[str] = frozenset({"yes", "true", "pass", "ok"})


def _is_pass(val) -> bool:
    """True iff *val* is a passing signal: boolean True, or a truthy string
    ("yes"/"true"/"pass"/"ok", case-insensitive). Everything else (False, None,
    "fail", "no", numbers) is fail-closed."""
    if val is True:
        return True
    if isinstance(val, str):
        return val.strip().lower() in _TRUTHY_PASS
    return False


def extract_verdict_json(raw: str) -> dict:
    """Parse the grader's stdout into a dict, tolerating the prose preamble and
    ```json code fences that `claude -p` commonly emits despite a bare-JSON
    instruction. Tries strict parse, then a fenced block, then the outermost
    brace span. Raises ValueError if no JSON object is recoverable."""
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError("no JSON object found in grader output")


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

ALLOWED_TOP_KEYS: frozenset[str] = frozenset(
    {
        "pre_gates",
        "checks",
        "matches_intent",
        "matches_intent_reason",
        "card_ok",
        "card_ok_reason",
        "draft_card",
        "owed_data_acs",
    }
)

LOG_BEARING_KEYS: frozenset[str] = frozenset(
    {
        "log",
        "logs",
        "transcript",
        "stdout",
        "stderr",
        "pytest_output",
        "psql_output",
        "build_log",
        "raw",
        "output",
    }
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def verdict_has_no_logs(verdict: dict) -> bool:
    """Return True iff no key anywhere in *verdict* (recursively) is in
    LOG_BEARING_KEYS (case-insensitive).

    Only dict KEYS are checked; the ``draft_card`` value string is not scanned.
    """

    def _scan(obj) -> bool:
        """Return True if a log-bearing key is found."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in LOG_BEARING_KEYS:
                    return True
                if _scan(v):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if _scan(item):
                    return True
        return False

    return not _scan(verdict)


def verdict_decision(verdict: dict) -> str:
    """Return ``"ready"``, ``"owed-data"``, or ``"regrade"``.

    Three-way outcome:
    - ``"regrade"`` — any code/intent/card gate fails (fail-closed).
    - ``"owed-data"`` — all gates pass but ``owed_data_acs`` is a non-empty list
      of AC strings deferred to post-merge observed-data verification.
    - ``"ready"`` — all gates pass and no owed-data ACs remain.

    A failing code gate ALWAYS returns ``"regrade"`` regardless of
    ``owed_data_acs`` — owed data can never mask a broken build.
    """
    pre_gates = verdict.get("pre_gates")
    checks = verdict.get("checks")

    if not pre_gates or not checks:
        return "regrade"

    for val in pre_gates.values():
        if not _is_pass(val):
            return "regrade"

    for val in checks.values():
        if not _is_pass(val):
            return "regrade"

    if not _is_pass(verdict.get("matches_intent")):
        return "regrade"

    if not _is_pass(verdict.get("card_ok")):
        return "regrade"

    owed = verdict.get("owed_data_acs")
    if isinstance(owed, list) and owed:
        return "owed-data"

    return "ready"


def compose_rework_reason(
    verdict: dict, closeout_excerpt: str = "",
    checks_result: str = "", pre_gate_result: str = "",
) -> str:
    """Build a DIAGNOSABLE rework reason from a grader verdict dict (spec 426 R4).

    Names exactly which gate failed and includes the grader's own reason strings
    plus, when the mechanical check failed, an excerpt of the real check output.
    NEVER returns a bare "regrade"/empty token.
    """
    parts: list[str] = []
    pre = verdict.get("pre_gates") or {}
    checks = verdict.get("checks") or {}
    failed_pre = [k for k, v in pre.items() if not _is_pass(v)]
    failed_checks = [k for k, v in checks.items() if not _is_pass(v)]
    if failed_pre:
        parts.append("pre-gate FAILED: " + ", ".join(sorted(failed_pre)))
    if failed_checks:
        parts.append("mechanical check FAILED: " + ", ".join(sorted(failed_checks)))
    if not _is_pass(verdict.get("matches_intent")):
        r = str(verdict.get("matches_intent_reason") or "").strip()
        parts.append("matches_intent=false" + (f": {r}" if r else ""))
    if not _is_pass(verdict.get("card_ok")):
        r = str(verdict.get("card_ok_reason") or "").strip()
        parts.append("card_ok=false" + (f": {r}" if r else ""))
    excerpt = (closeout_excerpt or "").strip()
    if failed_checks and excerpt:
        parts.append("check output tail: " + excerpt[-800:])
    if not parts:
        # verdict parsed but nothing flagged not-pass (e.g. owed/unknown) — still be specific
        detail = []
        if checks_result:
            detail.append(f"mechanical={checks_result}")
        if pre_gate_result:
            detail.append(f"pre_gate={pre_gate_result}")
        parts.append("close-out FAILED (" + ("; ".join(detail) or "no gate flagged; verdict lacked reasons") + ")")
    return " | ".join(parts)


def validate_and_decide(verdict: dict) -> tuple[str, list[str]]:
    """Return *(decision, problems)*.

    *decision* is always computed (independent of problems).
    *problems* is a list of human-readable strings:
    - ``"verdict carries log-bearing field"`` when ``verdict_has_no_logs`` is False
    - ``"unexpected top-level key: <k>"`` for each unrecognised top-level key
    """
    decision = verdict_decision(verdict)

    problems: list[str] = []

    if not verdict_has_no_logs(verdict):
        problems.append("verdict carries log-bearing field")

    for k in verdict:
        if k not in ALLOWED_TOP_KEYS:
            problems.append(f"unexpected top-level key: {k}")

    return decision, problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a builder close-out verdict dict (spec 296 R1-AC2 / spec 357). "
            "Prints 'ready', 'owed-data', or 'regrade' on stdout. "
            "Problems are printed to stderr. "
            "Exit 0 when decision is 'ready' or 'owed-data' AND no problems."
        )
    )
    parser.add_argument(
        "--verdict",
        metavar="PATH",
        help="Path to JSON verdict file (omit to read from stdin)",
    )
    parser.add_argument(
        "--compose-rework-reason",
        action="store_true",
        help="Print a diagnosable rework reason from a verdict JSON (spec 426 R4). "
             "Reads verdict from stdin (or --verdict). Never raises.",
    )
    parser.add_argument(
        "--closeout-file",
        metavar="PATH",
        help="Path to the file containing closeout check output (for --compose-rework-reason)",
    )
    parser.add_argument(
        "--checks-result",
        metavar="STR",
        default="",
        help="Mechanical checks result string (for --compose-rework-reason)",
    )
    parser.add_argument(
        "--pre-gate-result",
        metavar="STR",
        default="",
        help="Pre-gate result string (for --compose-rework-reason)",
    )
    args = parser.parse_args(argv)

    if args.compose_rework_reason:
        # spec 426 R4: compose a DIAGNOSABLE rework reason — NEVER raises
        try:
            if args.verdict:
                raw = Path(args.verdict).read_text()
            else:
                raw = sys.stdin.read()
            try:
                verdict = extract_verdict_json(raw)
            except Exception:
                verdict = {}
            closeout_excerpt = ""
            if args.closeout_file:
                try:
                    closeout_excerpt = Path(args.closeout_file).read_text()[-4000:]
                except Exception:
                    pass
            print(compose_rework_reason(
                verdict,
                closeout_excerpt=closeout_excerpt,
                checks_result=args.checks_result or "",
                pre_gate_result=args.pre_gate_result or "",
            ))
        except Exception:
            pass
        return 0

    if args.verdict:
        raw = Path(args.verdict).read_text()
    else:
        raw = sys.stdin.read()

    verdict = extract_verdict_json(raw)
    decision, problems = validate_and_decide(verdict)

    print(decision)

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 1

    return 0 if decision in {"ready", "owed-data"} else 1


if __name__ == "__main__":
    sys.exit(main())
