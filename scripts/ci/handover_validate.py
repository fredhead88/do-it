#!/usr/bin/env python3
"""Spec-handover validator — Guardrail Phase 5, T1 (spec 205).

Parses a spec's acceptance criteria and AUTO-FAILS four criterion_type↔evidence
mismatches that have each caused a real production or process incident:

  Rule 1 (ui):           a UI criterion proved only by grep/rg/file-read  → FAIL
                         (UI must have screenshot path + interaction trace)
  Rule 2 (observed-data):a data criterion whose evidence references sqlite:/// → FAIL
                         (observed-data requires a live_db-gated PG test reference)
                         SPECIAL: when SUPABASE_DB_URL is absent → WARN (exit 2),
                         not hard-fail, so the validator runs in PG-less envs.
  Rule 3 (cron):         a cron/scheduled criterion closeable by commit/code-path
                         alone, with no "row appears after next fire" assertion → FAIL
  Rule 4 (financial):    a financial criterion closed by self-attestation ("matches
                         Console" / "matches dashboard" / "matches report") with no
                         abs(reported - canonical) <= 0.01 tolerance → FAIL

DB identity guard: when SUPABASE_DB_URL is set, runs a read-only
`select current_database()` and refuses to count a probe as satisfied unless the
returned DB name matches the declared target database name (the value after the last
'/' in SUPABASE_DB_URL, before any query string). Mismatch → FAIL.

Exit codes:
  0 — all criteria pass
  1 — hard FAIL (names the offending AC)
  2 — WARN path (observed-data without SUPABASE_DB_URL, or DB identity
      check could not run but is inconclusive rather than confirmed-wrong)

Historical incidents (the criterion for each rule is proved by reintroducing it):
  Rule 1: UI-on-grep (pain-atlas pattern #5, sev 82)
  Rule 2: data-on-SQLite (api/tests/conftest.py:14 default; health_known_debt.md x4)
  Rule 3: Prime-Day cron-closed-on-commit loss
  Rule 4: Goya April reported -$84,444 vs canonical +$23,673.60 (5x COGS fan-out)

Criterion-type vocabulary (DO-IT.md ~line 88, extended by spec 205):
  ui | backend | observed-data | financial | cron

  Note: "cron" / "scheduled" are a sub-kind of observed-data. Either
  `criterion_type: cron` or cron-indicator keywords in the evidence text trigger
  Rule 3 (in addition to the base observed-data check).

Spec of record: ~/.claude/spec-inbox/205-guardrail-phase5-process-enforcement-spec.md
Parent design: docs/do-it/specs/2026-06-22-guardrail-genesis-kit-design.md (Phase 5)

Usage:
  python scripts/ci/handover_validate.py <spec.md>
  python scripts/ci/handover_validate.py <spec.md> --db-target <dbname>
  python scripts/ci/handover_validate.py <spec.md> --skip-db-check
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# ── Exit codes ───────────────────────────────────────────────────────────────

EXIT_OK = 0
EXIT_FAIL = 1  # hard FAIL — criterion<->evidence mismatch (names offending AC)
EXIT_WARN = 2  # soft WARN — PG-less env / DB identity inconclusive

# ── Criterion-type vocabulary ─────────────────────────────────────────────────

VALID_TYPES = frozenset({"ui", "backend", "observed-data", "financial", "cron"})

# ── Regex patterns ────────────────────────────────────────────────────────────

# Matches `AC<n> [type]:` (inline) or `AC<n>:` (needs default type lookup).
# Also tolerates leading `- ` or `* ` bullet prefixes.
_RE_AC_HEADER = re.compile(
    r"^[-*\s]*"
    r"AC(?P<num>\d+)"
    r"(?:\s*\[(?P<inline_type>[^\]]+)\])?"
    r"\s*:",
    re.IGNORECASE,
)

# Matches `criterion_type:` as a YAML-ish key (block level or inside AC body).
_RE_CTYPE_KEY = re.compile(
    r"^\s*criterion_type\s*:\s*(?P<val>\S+)",
    re.IGNORECASE,
)

# ── Rule-specific markers ─────────────────────────────────────────────────────

# Rule 1 (ui) — bad evidence: only grep/rg/static file-read
_RE_UI_BAD_EVIDENCE = re.compile(
    r"\b(grep|rg|file[_-]?read|cat\s|head\s|tail\s|static[_-]?read)\b",
    re.IGNORECASE,
)
_RE_UI_SCREENSHOT = re.compile(
    r"\b(screenshot|\.png\b|\.jpg\b|\.jpeg\b|shoot\.mjs"
    r"|chrome[_-]?devtools|devtools[_-]?screenshot|take_screenshot)\b",
    re.IGNORECASE,
)
_RE_UI_INTERACTION = re.compile(
    r"\b(click|fill|navigate|interaction[_-]?trace"
    r"|chrome[_-]?devtools|devtools[_-]?trace|console[_-]?trace"
    r"|selenium|playwright|puppeteer)\b",
    re.IGNORECASE,
)

# Rule 2 (observed-data) — bad evidence: sqlite reference
_RE_DATA_BAD_SQLITE = re.compile(
    r"sqlite:///|sqlite://\S*|:memory:|fixture[_-]?only|conftest\.py",
    re.IGNORECASE,
)
# Good evidence: live_db marker or SUPABASE_DB_URL-gated PG reference
_RE_DATA_GOOD_LIVE = re.compile(
    r"\blive_db\b"
    r"|\bSUPABASE_DB_URL\b"
    r"|\bpostgres(?:ql)?\b"
    r"|@pytest\.mark\.live_db"
    r"|\blive[-_]?db[-_]?tier\b"
    r"|\bpg[_-]?parity\b",
    re.IGNORECASE,
)

# Rule 3 (cron) — cron-indicator keywords in criterion text
_RE_CRON_KEYWORDS = re.compile(
    r"\b(cron|schedule[d]?|every\s+\d+[hHmMsS]|nightly|hourly|daily|fire[sd]?)\b",
    re.IGNORECASE,
)
# Bad: closeable by commit/code-path alone (no post-fire row assertion)
_RE_CRON_BAD = re.compile(
    r"\b(commit|code[_-]?path|merged|pushed|git\s+log|git\s+show"
    r"|grep\s+.*\.py|grep\s+.*\.sh)\b",
    re.IGNORECASE,
)
# Good: post-fire row assertion
_RE_CRON_GOOD_ROWS = re.compile(
    r"(?:"
    r"row[s]?\s+(?:appear|inserted|after|produced|present|count)"
    r"|after\s+(?:next\s+)?fire"
    r"|rows?\s+after"
    r"|queryable\s+rows?"
    r"|select\s+count"
    r"|row[_-]?count\s+assert"
    r"|assert.*row"
    r"|produced\s+queryable"
    r"|next[_-]?fire\s+row"
    r")",
    re.IGNORECASE,
)

# Rule 4 (financial) — bad: self-attestation without cent-comparison
_RE_FIN_BAD_ATTESTATION = re.compile(
    r"\b(?:matches\s+console|matches\s+dashboard|matches\s+report"
    r"|self[_-]?attest|visually\s+confirm[s]?"
    r"|looks\s+(?:right|correct)"
    r"|confirmed\s+by\s+(?:eye|visual|screenshot\s+only))\b",
    re.IGNORECASE,
)
# Good: canonical cent-comparison (abs(reported - canonical) <= 0.01 or equivalent)
_RE_FIN_GOOD_CENT = re.compile(
    r"(?:"
    r"abs\s*\(\s*reported\s*[-−]\s*canonical\s*\)"
    r"|abs\s*\(\s*\w+\s*[-−]\s*\w+\s*\)\s*<=?\s*0\.0*1"
    r"|cent[_-]?comparison"
    r"|canonical[_-]?endpoint\s+cent"
    r"|<=?\s*\$?0\.01\b"
    r"|≤\s*\$?0\.01\b"
    r"|within\s+\$?0\.01\b"
    r"|canonical\s+cent[_-]?diff"
    r")",
    re.IGNORECASE,
)


# ── Data structures ───────────────────────────────────────────────────────────


class Criterion(NamedTuple):
    label: str  # e.g. "AC2"
    ctype: str | None  # declared criterion_type (lowercased), or None
    text: str  # full body text of the criterion (header + body lines)
    line: int  # 1-based line number in the spec where AC was found


# ── Spec parser ───────────────────────────────────────────────────────────────


def parse_criteria(spec_text: str) -> list[Criterion]:
    """Extract every ACn criterion from the spec.

    Supports two layouts:
      A) Inline type:  `AC1 [ui]: ...`
      B) Block default: a `criterion_type: ui` key before the first AC in a T-section
         applies as the default for all ACs in that section until overridden.

    Returns a list of Criterion objects in document order.
    """
    lines = spec_text.splitlines()
    criteria: list[Criterion] = []

    # Track per-section default criterion_type (reset at each `## T<n>` heading).
    section_default_type: str | None = None
    # Accumulation state
    current_ac: str | None = None
    current_type: str | None = None
    current_start: int = 0
    current_lines: list[str] = []

    def _flush() -> None:
        nonlocal current_ac, current_type, current_start, current_lines
        if current_ac is not None:
            body = "\n".join(current_lines)
            criteria.append(
                Criterion(
                    label=current_ac,
                    ctype=current_type,
                    text=body,
                    line=current_start,
                )
            )
        current_ac = None
        current_type = None
        current_start = 0
        current_lines = []

    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()

        # Section heading resets the block-default type.
        if re.match(r"^##\s+T\d+", stripped):
            _flush()
            section_default_type = None
            continue

        # Block-level `criterion_type:` key (outside an AC body, sets section default).
        ctype_m = _RE_CTYPE_KEY.match(raw)
        if ctype_m and current_ac is None:
            section_default_type = ctype_m.group("val").strip().lower()
            continue

        # AC header?
        ac_m = _RE_AC_HEADER.match(stripped)
        if ac_m:
            _flush()
            current_ac = f"AC{ac_m.group('num')}"
            current_start = lineno
            inline_type = ac_m.group("inline_type")
            if inline_type:
                current_type = inline_type.strip().lower()
            else:
                current_type = section_default_type
            current_lines = [raw]
            continue

        # Inside an AC body: capture criterion_type override or any evidence.
        if current_ac is not None:
            ctype_inline = _RE_CTYPE_KEY.match(raw)
            if ctype_inline:
                current_type = ctype_inline.group("val").strip().lower()
            current_lines.append(raw)

    _flush()
    return criteria


# ── DB identity guard ─────────────────────────────────────────────────────────


def _db_target_from_url(url: str) -> str:
    """Extract the database name from a postgres URL (last path component)."""
    url = url.split("?")[0]
    return url.rstrip("/").rsplit("/", 1)[-1]


def run_db_identity_check(db_url: str) -> tuple[str | None, str | None]:
    """Run `select current_database()` and return (dbname, error_msg).

    Tries psycopg2 first; falls back to subprocess psql; returns (None, error)
    if neither is available or fails.  Read-only — no write of any kind.
    The only DB call this validator makes is this single read-only probe.
    """
    # Attempt 1: psycopg2
    try:
        import psycopg2  # type: ignore[import]

        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("SELECT current_database()")
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
        if row:
            return str(row[0]), None
        return None, "current_database() returned no rows"
    except ImportError:
        pass
    except Exception as exc:
        return None, f"psycopg2 error: {exc}"

    # Attempt 2: subprocess psql
    try:
        result = subprocess.run(
            ["psql", db_url, "-tAc", "SELECT current_database()"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            name = result.stdout.strip()
            if name:
                return name, None
            return None, "psql returned empty output"
        return None, f"psql exit {result.returncode}: {result.stderr.strip()}"
    except FileNotFoundError:
        pass
    except Exception as exc:
        return None, f"psql subprocess error: {exc}"

    return None, "neither psycopg2 nor psql available — skipping DB identity check"


# ── Rule result type ──────────────────────────────────────────────────────────


class RuleResult(NamedTuple):
    passed: bool
    message: str  # human-readable reason (empty if passed)
    is_warn: bool  # True -> EXIT_WARN rather than EXIT_FAIL


# ── Rule evaluators ───────────────────────────────────────────────────────────


def check_missing_type(c: Criterion) -> RuleResult:
    """Every criterion MUST declare a criterion_type."""
    if not c.ctype:
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} at line {c.line}: missing criterion_type. "
                f"Declare it inline (`{c.label} [ui]:`) or via a block "
                f"`criterion_type:` key before the first AC in its section. "
                f"Valid types: " + ", ".join(sorted(VALID_TYPES))
            ),
            is_warn=False,
        )
    if c.ctype not in VALID_TYPES:
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} at line {c.line}: unknown criterion_type "
                f"'{c.ctype}'. Valid types: " + ", ".join(sorted(VALID_TYPES))
            ),
            is_warn=False,
        )
    return RuleResult(passed=True, message="", is_warn=False)


def check_ui_rule(c: Criterion) -> RuleResult:
    """Rule 1 (ui): must have screenshot + interaction trace; grep/rg alone -> FAIL.

    Historical incident: UI-on-grep (pain-atlas pattern #5, sev 82).
    """
    text = c.text
    has_screenshot = bool(_RE_UI_SCREENSHOT.search(text))
    has_interaction = bool(_RE_UI_INTERACTION.search(text))
    has_grep_evidence = bool(_RE_UI_BAD_EVIDENCE.search(text))

    if has_grep_evidence and not has_screenshot and not has_interaction:
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} (ui) at line {c.line}: FAIL Rule 1 — "
                f"UI criterion proved only by grep/rg/file-read. "
                f"UI evidence MUST include a screenshot artifact path "
                f"(e.g. .png, shoot.mjs) AND an interaction trace "
                f"(click/fill/chrome-devtools sequence). "
                f"Re-creates the UI-on-grep failure: pain-atlas pattern #5 sev 82."
            ),
            is_warn=False,
        )
    if not has_screenshot or not has_interaction:
        missing = []
        if not has_screenshot:
            missing.append("screenshot path (.png / shoot.mjs / devtools screenshot)")
        if not has_interaction:
            missing.append("interaction trace (click/fill/navigate/devtools sequence)")
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} (ui) at line {c.line}: FAIL Rule 1 — "
                f"UI criterion missing required evidence: "
                + "; ".join(missing)
                + ". Both are required to close a UI criterion."
            ),
            is_warn=False,
        )
    return RuleResult(passed=True, message="", is_warn=False)


def check_observed_data_rule(c: Criterion, pg_url_present: bool) -> RuleResult:
    """Rule 2 (observed-data): live_db PG test required; sqlite:/// -> FAIL.

    SPECIAL: when SUPABASE_DB_URL is absent -> WARN (exit 2), not hard-fail,
    so the validator can run in PG-less CI environments while Phase 2 is still
    being stood up.

    Historical incident: sqlite:/// default in api/tests/conftest.py:14
    (written into health_known_debt.md x4).
    """
    text = c.text
    has_sqlite = bool(_RE_DATA_BAD_SQLITE.search(text))
    has_live_db = bool(_RE_DATA_GOOD_LIVE.search(text))

    # sqlite:/// is always wrong, even without SUPABASE_DB_URL set
    if has_sqlite and not has_live_db:
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} (observed-data) at line {c.line}: FAIL Rule 2 — "
                f"observed-data criterion references sqlite:/// or fixture-only run. "
                f"Observed-data MUST be proved by a SUPABASE_DB_URL-gated live_db "
                f"Postgres test (the tier Phase 2 / spec 202 stands up). "
                f"Re-creates the SQLite-masks-PG class: api/tests/conftest.py:14."
            ),
            is_warn=False,
        )

    if not has_live_db:
        if not pg_url_present:
            # Degrade to WARN: Phase 2 tier may not be present yet
            return RuleResult(
                passed=False,
                message=(
                    f"{c.label} (observed-data) at line {c.line}: WARN Rule 2 — "
                    f"no live_db PG test reference found in evidence AND "
                    f"SUPABASE_DB_URL is not set in the environment. "
                    f"Cannot hard-fail in a PG-less environment (the Phase 2 "
                    f"live_db tier may not yet be present). "
                    f"Set SUPABASE_DB_URL or add a `@pytest.mark.live_db`-gated "
                    f"Postgres test reference when the PG-parity tier is available."
                ),
                is_warn=True,
            )
        # SUPABASE_DB_URL IS present -> Phase 2 tier expected -> hard-fail
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} (observed-data) at line {c.line}: FAIL Rule 2 — "
                f"no live_db PG test reference in evidence (SUPABASE_DB_URL is "
                f"set so the Phase 2 tier is expected to exist). "
                f"Add a @pytest.mark.live_db-gated Postgres test reference."
            ),
            is_warn=False,
        )

    return RuleResult(passed=True, message="", is_warn=False)


def check_cron_rule(c: Criterion) -> RuleResult:
    """Rule 3 (cron): must assert rows after next fire; commit/code-path alone -> FAIL.

    Activation: criterion_type is 'cron', OR the criterion text contains
    cron-indicator keywords (schedule/nightly/every Nh/fired).

    Historical incident: Prime-Day cron-closed-on-commit loss (irreversible
    capture window lost because the cron criterion was closed before the job's
    next fire produced any queryable rows).
    """
    text = c.text
    is_cron = c.ctype == "cron" or bool(_RE_CRON_KEYWORDS.search(text))
    if not is_cron:
        return RuleResult(passed=True, message="", is_warn=False)

    has_commit_evidence = bool(_RE_CRON_BAD.search(text))
    has_row_assertion = bool(_RE_CRON_GOOD_ROWS.search(text))

    if has_commit_evidence and not has_row_assertion:
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} (cron) at line {c.line}: FAIL Rule 3 — "
                f"cron/scheduled criterion is closeable by a commit/code-path check "
                f"with no 'rows appear after next fire' assertion. "
                f"A cron criterion MUST assert that queryable rows are produced after "
                f"the job's next actual fire, not merely that the code path exists. "
                f"Re-creates the Prime-Day cron-closed-on-commit loss."
            ),
            is_warn=False,
        )
    if not has_row_assertion and c.ctype == "cron":
        # Explicitly declared cron with no row assertion at all
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} (cron) at line {c.line}: FAIL Rule 3 — "
                f"criterion_type is 'cron' but evidence has no post-fire row "
                f"assertion ('rows appear after next fire', select count, etc.). "
                f"Add a post-fire row-count assertion to close this criterion."
            ),
            is_warn=False,
        )
    return RuleResult(passed=True, message="", is_warn=False)


def check_financial_rule(c: Criterion) -> RuleResult:
    """Rule 4 (financial): canonical cent-comparison required; self-attestation -> FAIL.

    Historical incident: Goya April reported -$84,444 vs canonical +$23,673.60
    (5x COGS fan-out); handover was accepted on self-attestation "matches Console"
    with no canonical cent-diff.
    """
    text = c.text
    has_attestation = bool(_RE_FIN_BAD_ATTESTATION.search(text))
    has_cent_compare = bool(_RE_FIN_GOOD_CENT.search(text))

    if has_attestation and not has_cent_compare:
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} (financial) at line {c.line}: FAIL Rule 4 — "
                f"financial criterion closed by self-attestation "
                f"('matches Console' / 'matches dashboard' / etc.) with no "
                f"canonical cent-comparison. "
                f"Financial evidence MUST include: abs(reported - canonical) <= 0.01 "
                f"(or equivalent <=/$0.01 tolerance against the canonical Profit/cash "
                f"endpoint or the spec-159 canonical view). "
                f"Re-creates: Goya April reported -$84,444 vs canonical +$23,673.60 "
                f"(5x COGS fan-out accepted on 'matches Console' self-attestation)."
            ),
            is_warn=False,
        )
    if not has_cent_compare:
        return RuleResult(
            passed=False,
            message=(
                f"{c.label} (financial) at line {c.line}: FAIL Rule 4 — "
                f"financial criterion contains no canonical cent-comparison. "
                f"Evidence must include: abs(reported - canonical) <= 0.01 "
                f"(or within $0.01 / <=0.01 against the canonical endpoint)."
            ),
            is_warn=False,
        )
    return RuleResult(passed=True, message="", is_warn=False)


# ── requires_live_run marker nudge (spec 362 R1) ──────────────────────────────

# Matches a spec-level `requires_live_run: true` marker (status block key).
_RE_REQUIRES_LIVE_RUN = re.compile(
    r"^\s*requires_live_run\s*:\s*true\b",
    re.IGNORECASE | re.MULTILINE,
)

# Detects an observed-data AC body naming a pipelines/ surface.
_RE_PIPELINES_SURFACE = re.compile(r"pipelines/")


def check_requires_live_run_marker(
    spec_text: str, criteria: list[Criterion]
) -> RuleResult | None:
    """R1 (spec 362): WARN when an observed-data AC names a pipelines/ surface and
    the requires_live_run marker is absent. Returns a WARN RuleResult, or None.

    Spec-level check — run once per invocation, not per criterion. Never a hard
    fail: the validator can't judge whether a scoped live run is actually
    warranted, only nudge the thinker/orc to set the marker deliberately (or
    record why not).
    """
    has_pipelines_observed_data = any(
        c.ctype == "observed-data" and _RE_PIPELINES_SURFACE.search(c.text)
        for c in criteria
    )
    if not has_pipelines_observed_data:
        return None

    if _RE_REQUIRES_LIVE_RUN.search(spec_text):
        return None

    return RuleResult(
        passed=False,
        message=(
            "requires_live_run marker missing: an [observed-data] AC names a "
            "pipelines/ surface but the spec does not declare "
            "`requires_live_run: true` — set it deliberately (this pipeline may "
            "need a scoped prod run before .ready per spec 362) or record why not."
        ),
        is_warn=True,
    )


# ── DB identity guard evaluator ───────────────────────────────────────────────


def check_db_identity(db_url: str, declared_target: str | None) -> RuleResult:
    """DB identity guard: select current_database() must match declared target.

    Returns FAIL if identity is confirmed wrong, WARN if check cannot run.
    This is the ONLY DB call the validator makes, and it is read-only.
    """
    actual_name, error = run_db_identity_check(db_url)

    if error and "neither psycopg2 nor psql" in error:
        return RuleResult(
            passed=False,
            message=(
                f"DB identity guard: WARN — {error}. "
                f"Probe evidence cannot be fully credited without identity verification."
            ),
            is_warn=True,
        )

    if error:
        return RuleResult(
            passed=False,
            message=(
                f"DB identity guard: FAIL — could not verify current_database(): "
                f"{error}. Probe evidence is not credited."
            ),
            is_warn=False,
        )

    if declared_target and actual_name and actual_name != declared_target:
        return RuleResult(
            passed=False,
            message=(
                f"DB identity guard: FAIL — connected database is '{actual_name}' "
                f"but declared target is '{declared_target}'. "
                f"Refusing to credit probe evidence against the wrong DB. "
                f"Check SUPABASE_DB_URL or pass --db-target to override."
            ),
            is_warn=False,
        )

    return RuleResult(passed=True, message="", is_warn=False)


# ── Thinker-isolation guard (spec 253 R6 — wired choke point) ──────────────────

# Repo root: scripts/ci/handover_validate.py → parents[2] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def run_thinker_isolation_guard() -> tuple[int, list[str]]:
    """Run scripts/ci/check_thinker_isolation.sh against the repo root.

    Spec 253 R6: the bus-first authoring rule is honor-system unless a guard runs
    at an automatic choke point. `spec-handover` invokes THIS validator at pre-flight,
    so running the guard here makes the choke point real (not a doc claim).

    Returns (exit_code, stray_files). exit_code 0 == clean; 1 == violation.
    The guard only ever reports — it never deletes; the repo-owner adjudicates.
    A missing/unrunnable guard degrades to (0, []) so the validator still runs in
    environments where the guard script is absent.
    """
    guard = _REPO_ROOT / "scripts" / "ci" / "check_thinker_isolation.sh"
    if not guard.exists():
        return 0, []
    try:
        result = subprocess.run(
            ["bash", str(guard), str(_REPO_ROOT)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001 — never let the guard break handover validation
        return 0, []
    strays = [
        line.split("STRAY:", 1)[1].strip()
        for line in (result.stdout + result.stderr).splitlines()
        if "STRAY:" in line
    ]
    return result.returncode, strays


# ── Validator orchestrator ────────────────────────────────────────────────────


def validate_spec(
    spec_path: Path,
    db_target_override: str | None = None,
    skip_db_check: bool = False,
) -> int:
    """Parse the spec at spec_path and evaluate all rules. Returns the exit code."""

    try:
        spec_text = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"[handover-validate] ERROR: cannot read spec file: {exc}",
            file=sys.stderr,
        )
        return EXIT_FAIL

    criteria = parse_criteria(spec_text)
    if not criteria:
        print(
            f"[handover-validate] WARN: no acceptance criteria (ACn: patterns) found "
            f"in {spec_path}. Is this a valid spec?",
            file=sys.stderr,
        )
        # A spec with no ACs is not a hard-fail (may be a brief or docs-only page).
        return EXIT_OK

    db_url = os.environ.get("SUPABASE_DB_URL", "")
    pg_url_present = bool(db_url)

    # ── DB identity guard (runs once per invocation, not per AC) ────────────
    db_identity_hard_fail = False
    db_identity_warned = False
    if pg_url_present and not skip_db_check:
        declared_target = db_target_override or _db_target_from_url(db_url)
        db_result = check_db_identity(db_url, declared_target)
        if not db_result.passed:
            if db_result.is_warn:
                db_identity_warned = True
                print(
                    f"[handover-validate] WARN: {db_result.message}",
                    file=sys.stderr,
                )
            else:
                db_identity_hard_fail = True
                print(
                    f"[handover-validate] FAIL: {db_result.message}",
                    file=sys.stderr,
                )

    # ── Per-criterion evaluation ─────────────────────────────────────────────
    failures: list[RuleResult] = []
    warns: list[RuleResult] = []

    for c in criteria:
        # Step 0: criterion_type must be declared and valid.
        r = check_missing_type(c)
        if not r.passed:
            (warns if r.is_warn else failures).append(r)
            continue

        ctype = c.ctype  # non-None, validated

        if ctype == "ui":
            r = check_ui_rule(c)
        elif ctype == "observed-data":
            r = check_observed_data_rule(c, pg_url_present)
        elif ctype == "cron":
            r = check_cron_rule(c)
        elif ctype == "financial":
            r = check_financial_rule(c)
        else:
            # "backend" and any future type: no structural evidence constraint.
            r = RuleResult(passed=True, message="", is_warn=False)

        # For any criterion (regardless of declared type) whose text contains cron
        # keywords, also run Rule 3 — so a `backend` AC about a cron job can't sneak
        # through without a post-fire row assertion.
        if r.passed and ctype != "cron" and _RE_CRON_KEYWORDS.search(c.text):
            r3 = check_cron_rule(c)
            if not r3.passed:
                r = r3

        if not r.passed:
            (warns if r.is_warn else failures).append(r)

    # ── Spec-level checks (run once per invocation, not per criterion) ───────
    live_run_result = check_requires_live_run_marker(spec_text, criteria)
    if live_run_result is not None:
        warns.append(live_run_result)

    # ── Report ───────────────────────────────────────────────────────────────

    db_status = "ok"
    if db_identity_hard_fail:
        db_status = "FAIL"
    elif db_identity_warned:
        db_status = "WARN"
    elif not pg_url_present:
        db_status = "skipped (no SUPABASE_DB_URL)"
    elif skip_db_check:
        db_status = "skipped (--skip-db-check)"

    # ── Thinker-isolation guard (spec 253 R6) — run at this handover choke point ──
    iso_rc, iso_strays = run_thinker_isolation_guard()
    iso_status = "clean" if iso_rc == 0 else f"VIOLATION ({len(iso_strays)} stray)"

    print("\n=== Handover Validator (spec 205 T1) ===")
    print(f"  Spec:               {spec_path}")
    print(f"  Criteria found:     {len(criteria)}")
    print(f"  SUPABASE_DB_URL:    {'SET' if pg_url_present else 'not set'}")
    print(f"  DB identity check:  {db_status}")
    print(f"  Thinker isolation:  {iso_status}")
    print(f"  Hard failures:      {len(failures)}")
    print(f"  Warnings:           {len(warns)}")

    # Surface a thinker-isolation violation LOUDLY (spec 253 R6). Advisory only:
    # a pre-existing stray doc (often authored by another session) must not block
    # this thinker's otherwise-valid handover — the repo-owner adjudicates and
    # lands/removes it. The criterion verdict below is unaffected.
    if iso_rc != 0 and iso_strays:
        print(
            "\n========================================================\n"
            "  THINKER-ISOLATION WARNING (spec 253 R6) — bus-first rule\n"
            "========================================================\n"
            "  Doc(s) authored into the shared checkout instead of the bus:",
            file=sys.stderr,
        )
        for f in iso_strays:
            print(f"    STRAY: {f}", file=sys.stderr)
        print(
            "  These should live in ~/.claude/spec-staging/ or ~/.claude/think-staging/\n"
            "  until the repo-owner lands them on the correct branch. Not auto-deleted —\n"
            "  the repo-owner adjudicates. (This warning does not block handover.)\n",
            file=sys.stderr,
        )

    if failures or db_identity_hard_fail:
        print("\n--- FAILURES ---", file=sys.stderr)
        for r in failures:
            print(f"  FAIL: {r.message}", file=sys.stderr)
        if db_identity_hard_fail:
            print(
                "  FAIL: DB identity check failed — probe evidence not credited "
                "(see FAIL message above).",
                file=sys.stderr,
            )
        print(
            "\nHandover BLOCKED. Fix the criterion evidence or declare a matching "
            "criterion_type before handing over this spec.",
            file=sys.stderr,
        )
        print()
        return EXIT_FAIL

    if warns or db_identity_warned:
        print("\n--- WARNINGS (non-blocking) ---")
        for r in warns:
            print(f"  WARN: {r.message}")
        print(
            "\nHandover proceeding with warnings. "
            "Resolve WARN items before the Phase 2 PG-parity tier is confirmed present."
        )
        print()
        return EXIT_WARN

    print(
        f"\nAll {len(criteria)} criterion/criteria pass handover validation.\n"
        f"  Rule 1 (ui):            no UI criterion proved only by grep/rg\n"
        f"  Rule 2 (observed-data): no observed-data criterion on sqlite\n"
        f"  Rule 3 (cron):          no cron criterion closeable before next fire\n"
        f"  Rule 4 (financial):     no financial criterion on self-attestation\n"
    )
    return EXIT_OK


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Spec-handover validator (spec 205 T1) — "
            "criterion_type <-> evidence coupling"
        )
    )
    parser.add_argument(
        "spec",
        metavar="SPEC_PATH",
        help="path to the spec .md file to validate",
    )
    parser.add_argument(
        "--db-target",
        default=None,
        metavar="DBNAME",
        help=(
            "Override the DB name extracted from SUPABASE_DB_URL for the identity "
            "check. Default: last path component of SUPABASE_DB_URL."
        ),
    )
    parser.add_argument(
        "--skip-db-check",
        action="store_true",
        help="Skip the DB identity guard entirely (for testing without a live DB).",
    )
    args = parser.parse_args()

    return validate_spec(
        Path(args.spec),
        db_target_override=args.db_target,
        skip_db_check=args.skip_db_check,
    )


if __name__ == "__main__":
    sys.exit(main())
