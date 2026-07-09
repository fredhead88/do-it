#!/usr/bin/env python3
"""Spec build-status ledger — render + validate.

The DO-IT pipeline's durable answer to "did we write any specs that never got
built?". Reads one small fact-file per spec from the BUS ledger
(~/.claude/ledger/, override with DOIT_LEDGER_DIR) and produces a grouped,
read-only view (OUTSTANDING.md) written into the repo mirror
(docs/do-it/ledger/, override with DOIT_MIRROR_DIR). The view is GENERATED —
never hand-edited — so it cannot drift from the per-spec facts.

State lives in the per-spec files (consistent with DO-IT's "state is a status on
the one index"); this script only reads them and renders. The only file it
writes is the generated OUTSTANDING.md mirror.

Usage:
    python scripts/spec_ledger.py            # render to stdout + write OUTSTANDING.md
    python scripts/spec_ledger.py --render   # (same as default)
    python scripts/spec_ledger.py --all      # include accepted/superseded in the printout
    python scripts/spec_ledger.py --check     # validate records; exit non-zero on any violation

    # atomically allocate the next SHARED bus number (specs + briefs) AND reserve
    # it under one machine-global lock, so concurrent sessions can't double-book.
    # Prints only the zero-padded number. The reservation is the artifact itself:
    #   spec  -> births the registered ledger record (do NOT also call `register`)
    #   brief -> writes the brief file into brief-inbox (think fills its body)
    python scripts/spec_ledger.py next-num --kind brief --slug my-topic
    python scripts/spec_ledger.py next-num --kind spec  --slug my-topic \
        --title T --intent I --spec-file docs/do-it/specs/x.md [--source-brief N]

    # write a record (so nobody hand-edits YAML — the source of indentation /
    # missing-field bugs). Both refuse to write anything that wouldn't pass --check.
    # `register` takes an explicit id; prefer `next-num --kind spec` at handover so
    # allocation + birth happen atomically:
    python scripts/spec_ledger.py register NNN-slug --title T --intent I \
        --spec-file docs/do-it/specs/x.md [--source-brief N] [--by handover]
    python scripts/spec_ledger.py set NNN-slug <status> --by WHO \
        [--reason R] [--superseded-by ID] [--needs N] [--field key=value ...]

    # record a verifier verdict (verifier-owned namespace, builder can't overwrite):
    python scripts/spec_ledger.py verify NNN-slug CONFIRMED \
        --judge codex --evidence runs/2026-01-01/evidence/c1-primary.json
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# Ledger MASTERS live in the bus (machine-global), overridable for tests.
LEDGER_DIR = Path(os.environ.get("DOIT_LEDGER_DIR", Path.home() / ".claude" / "ledger"))
# The generated mirror is committed into the repo.
_MIRROR_DIR = Path(
    os.environ.get("DOIT_MIRROR_DIR", REPO_ROOT / "docs" / "do-it" / "ledger")
)
OUTSTANDING_MD = _MIRROR_DIR / "OUTSTANDING.md"

# The other two bus lanes (machine-global, overridable for tests). The allocator
# scans these alongside the ledger so spec and brief numbers draw from one space.
SPEC_INBOX = Path(
    os.environ.get("DOIT_SPEC_INBOX", Path.home() / ".claude" / "spec-inbox")
)
BRIEF_INBOX = Path(
    os.environ.get("DOIT_BRIEF_INBOX", Path.home() / ".claude" / "brief-inbox")
)

# Genuine bus numbers are 3 digits FOLLOWED BY A HYPHEN. The trailing `-` is
# load-bearing: without it the year in a grandfathered date-stem file
# (`2026-05-31-...`) reads as "202" and poisons allocation. The sanity ceiling
# trips when the computed next number leaves the genuine low-100s sequence —
# the signature of a mis-numbered/date-stem file having inflated the max.
_NUM_RE = re.compile(r"^([0-9]{3})-")
# Poison guard is RELATIVE, not absolute. The genuine sequence grows without bound
# (specs passed 150 long ago — a fixed ceiling false-trips on every real allocation),
# so we don't cap the number. `_NUM_RE`'s trailing hyphen already stops a `2026-` year
# reading as 202; the only remaining poison is a wildly mis-numbered 3-digit file,
# which shows up as an outlier JUMP above the second-highest number. Refuse a jump
# larger than this gap (the live sequence is dense, gaps of 1–2).
ALLOC_GAP_CEILING = 40


# Verifier-owned namespace — the builder's glob("*.yml") never touches this subdir.
# VERIFIED_DIR is derived from LEDGER_DIR so tests can override it via DOIT_LEDGER_DIR.
def _get_verified_dir() -> Path:
    return LEDGER_DIR / "verified"


VALID_VERDICT = {"CONFIRMED", "REJECTED"}

# Per-criterion verdicts (richer than the spec-level CONFIRMED/REJECTED). The
# verification loop's HOLLOW/MISSING/REGRESSION all map to REJECTED before they
# reach here; `not-applicable` marks a criterion that is legitimately unobservable
# (no test-tenant data) so one data-gap can't freeze a spec forever; `not-run`
# means not-yet-observed (incomplete); `integration-owed` (spec 402 R4) marks an
# external-I/O criterion whose real observed call has not happened yet — an
# observable non-CONFIRMED value that blocks a spec-level pass (like not-run).
VALID_CRITERION_VERDICT = {
    "CONFIRMED",
    "REJECTED",
    "not-applicable",
    "not-run",
    "integration-owed",
}


def resolve_spec_verdict(
    criteria: dict, waivers: dict | None = None, declared=None
) -> str | None:
    """Aggregate a per-criterion verdict map into a spec-level verdict.

    REJECTED if any criterion is REJECTED; CONFIRMED iff there is >=1 observable
    criterion and every observable (non-`not-applicable`) one is CONFIRMED;
    otherwise None (incomplete -> renders as `awaiting-verify`).

    Spec 402 R1 (full-declared-set gate — omission ≠ pass): `declared` is the FULL
    set of criterion ids the spec declared (or None for legacy records that never
    declared it). REJECTED still wins even when the set is incomplete (a definitive
    rejection is a blocking signal, not an acceptance claim). But a spec-level
    CONFIRMED is only derivable when EVERY declared criterion is present in the map:
    if any declared id is missing, return None (incomplete). This closes the live
    false-accept (spec 390): rev recorded 4 of 12 declared criteria, all CONFIRMED,
    and the aggregator — blind to the declared count — derived CONFIRMED. A declared
    criterion that is present but `not-run`/`integration-owed` already blocks via the
    all-CONFIRMED check below; only OMISSION needed the extra gate. `declared=None`
    reproduces the exact pre-402 behavior.

    Spec 168 (Derived-Verdict Guard): a criterion that carries a VALID owner-waiver
    (R3b — names both the criterion and the human) is treated as satisfied for the
    purpose of the aggregate, so a recorded human override is the ONLY way a standing
    REJECTED criterion stops blocking `accepted` short of being flipped with evidence.
    A waiver that does not validate (missing criterion id or human) is ignored — it
    cannot silently clear a red criterion.
    """
    if not criteria:
        return None
    waivers = waivers or {}
    effective: list[str] = []
    for cid, v in criteria.items():
        if v == "REJECTED" and _waiver_is_valid(waivers.get(cid), cid):
            # An owner-waiver naming this criterion + a human treats it as cleared.
            effective.append("CONFIRMED")
        else:
            effective.append(v)
    if any(v == "REJECTED" for v in effective):
        # REJECTED wins even if the declared set is incomplete (spec 402 R1).
        return "REJECTED"
    # Spec 402 R1: an incomplete map (a declared criterion never recorded) can never
    # derive a spec-level pass — omission is not a pass.
    if declared:
        missing = [d for d in declared if d not in criteria]
        if missing:
            return None
    observable = [v for v in effective if v != "not-applicable"]
    if observable and all(v == "CONFIRMED" for v in observable):
        return "CONFIRMED"
    return None


def _waiver_is_valid(waiver, criterion_id: str) -> bool:
    """An owner-waiver clears a REJECTED criterion only when it NAMES both the
    criterion AND the human (spec 168 R3b). A generic spec-level stamp, or a waiver
    missing either field, never validates. `waiver` is the per-criterion mapping
    stored under the verdict's `waivers` key, e.g.
        waivers: {c7: {criterion: c7, human: ephraim, reason: "..."}}
    """
    if not isinstance(waiver, dict):
        return False
    named = str(waiver.get("criterion") or "").strip()
    human = str(waiver.get("human") or "").strip()
    return named == criterion_id and bool(human)


def derived_verdict(verdict: dict | None) -> str | None:
    """The TRUSTED spec-level verdict for a verified/ record (spec 168 R1).

    When the record carries a per-criterion `criteria` map, the spec-level verdict
    is DERIVED from it (honouring owner-waivers), and any stored `verdict` field
    that contradicts the criteria is IGNORED. Only a criteria-free legacy record
    falls back to its stored spec-level `verdict`. This is what makes `accepted`
    un-spoofable: a builder-side grader can stamp `verdict: CONFIRMED`, but if a
    criterion is REJECTED the join derives REJECTED regardless of the stamp.

    Spec 168 R5 (criteria-free non-rev guard): when the record has NO criteria
    (absent or empty {}) and the judge is NOT 'rev' and NOT an owner-waiver, the
    stored verdict is UNTRUSTED — return None (awaiting-verify) rather than CONFIRMED.
    Only a rev-authored criteria-free record keeps the legacy fallback, because rev
    is the sole role authorised to attest acceptance without per-criterion evidence.
    Non-rev judges (blind-closeout, blind-closeout-opus, codex, …) that write a
    bare spec-level CONFIRMED without criteria are treated as not-yet-accepted.
    """
    if not verdict:
        return None
    criteria = verdict.get("criteria")
    if criteria:
        # Spec 402 R1: honour the persisted declared set so a partial (omission)
        # map can't derive a spec-level pass. Legacy records lack
        # `declared_criteria` → declared=None → exactly the pre-402 behavior.
        return resolve_spec_verdict(
            criteria,
            verdict.get("waivers"),
            declared=verdict.get("declared_criteria"),
        )
    # criteria is absent or empty — legacy fallback path.
    # A non-rev, non-owner-waiver judge's criteria-free CONFIRMED is untrusted:
    # it cannot convey acceptance without per-criterion evidence (spec 168 R5).
    # REJECTED from any judge is always honoured — that is a blocking signal, not
    # an acceptance claim, so non-rev judges may still reject a spec.
    stored = verdict.get("verdict")
    if stored == "CONFIRMED":
        judge = str(verdict.get("judge") or "").strip()
        _trusted = judge == "rev" or judge.startswith("owner-waiver:")
        if not _trusted:
            return None
    return stored


@contextmanager
def _record_lock(path: Path):
    """Advisory flock on a per-record lockfile so concurrent same-role ticks
    can't lose-update. Cross-role safety is already guaranteed by the separate
    verified/ dir (A1); this covers intra-role races."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lockfile = path.with_suffix(path.suffix + ".lock")
    fh = open(lockfile, "w")  # noqa: WPS515
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


@contextmanager
def _bus_lock():
    """ONE machine-global lock that serializes number *allocation* across both
    lanes (specs + briefs). Distinct from `_record_lock`, which is per-record and
    therefore cannot serialize allocation: two allocators racing for a NEW number
    have no shared record path to contend on, so they'd both grab the same max+1.
    Held across scan -> compute -> reserve so a concurrent caller blocks until the
    reservation is on disk and visible to its scan."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    lockfile = LEDGER_DIR / ".alloc.lock"
    fh = open(lockfile, "w")  # noqa: WPS515
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def _bus_dirs() -> list[Path]:
    """All five bus lanes the allocator scans (live + _archive of each inbox,
    plus the ledger). Missing dirs are skipped."""
    return [
        SPEC_INBOX,
        SPEC_INBOX / "_archive",
        LEDGER_DIR,
        BRIEF_INBOX,
        BRIEF_INBOX / "_archive",
    ]


def _scan_bus_numbers() -> list[int]:
    """All distinct NNN across every bus dir, descending. Matches 3 digits FOLLOWED
    by a hyphen (so a `2026-...` year never reads as 202). Empty bus -> []."""
    nums: set[int] = set()
    for d in _bus_dirs():
        if not d.exists():
            continue
        for entry in d.iterdir():
            m = _NUM_RE.match(entry.name)
            if m:
                nums.add(int(m.group(1)))
    return sorted(nums, reverse=True)


def scan_bus_max() -> int:
    """Highest NNN across every bus dir. Returns 0 on an empty bus."""
    nums = _scan_bus_numbers()
    return nums[0] if nums else 0


def scan_bus_top_two() -> tuple[int, int]:
    """(highest, second-highest) distinct NNN across the bus; second is 0 when fewer
    than two distinct numbers exist. Used to detect a poisoned max by its jump."""
    nums = _scan_bus_numbers()
    if not nums:
        return 0, 0
    return nums[0], (nums[1] if len(nums) > 1 else 0)


VALID_STATUS = {
    "registered",
    "planned",
    "building",
    "gating",  # spec 300: pushed, under pane-independent close-out grader (building→gating→ready)
    "ready",  # spec 252: built + self-gated, awaiting integrator merge
    "merged",
    "shipped",
    "accepted",  # legacy only — blocked by cmd_set; effective_status derives it from shipped ∧ CONFIRMED
    # spec 282 R2: real intermediate lifecycle states the parallel model produced.
    "awaiting-data",  # merged/live; owes only a data backfill / cron-populate / re-verify — NO code, so no builder claims it (the 261 shape)
    "awaiting-verify",  # shipped + live, awaiting rev's verdict (the derived shipped∧¬CONFIRMED render also uses this name)
    "held",
    "bounced",
    "rework",
    "superseded",
    "retired",
    "unknown",
}

# stored status -> read bucket
OUTSTANDING_STATUSES = {
    "registered",
    "planned",
    "building",
    "gating",  # spec 300: pushed, under pane-independent close-out grader (building→gating→ready)
    "ready",  # spec 252: non-terminal — built but not yet merged
    "merged",
    "held",
    "bounced",
    "rework",
    "unknown",
}
STALE_MERGED_HOURS = 24
AWAITING_PROD_FLOOR_HOURS = 48

# spec 282 R3 — a record at/after ship must not carry a held_reason that still
# asserts a pre-ship reality. These are the post-ship states the lint checks, and
# the free-text markers that betray a stale pre-ship assertion (the 269 pattern).
_POST_SHIP_STATES = {"shipped", "awaiting-verify", "awaiting-data", "accepted"}
_STALE_HELD_REASON_MARKERS = (
    "not re-deployed",
    "not redeployed",
    "pending operator",
    "awaiting operator",
    "not deployed",
    "awaiting deploy",
    "pending deploy",
    "pre-ship",
)

# spec 282 R4 — a CONFIRMED for an observed-data/migration-bearing criterion must
# cite a LIVE-DB observation, never a manifest/git-ancestry artifact (which lags
# under the fast cadence — a data verdict read off it can be wrong). These markers
# distinguish a live prod-DB query (alembic_version, a SELECT, psql, the live-db
# URL gate) from a "manifest shows / merged sha / git ancestry" claim.
# Only STRONG, specific live-DB markers — a bare "row count" / "count(*)" phrase is
# deliberately NOT a marker (it reads fine in a manifest sentence); a real live-DB
# observation cites the version table, the prod URL gate, an actual SELECT…FROM, a
# psql/catalog probe, or an explicit live-db: ref.
_LIVE_DB_EVIDENCE_RE = re.compile(
    r"(alembic_version|supabase_db_url|\bpsql\b|live-?db:|\bselect\b.+\bfrom\b|"
    r"information_schema|pg_catalog|\\dt\b)",
    re.IGNORECASE | re.DOTALL,
)


def _evidence_has_live_db_observation(evidence: str) -> bool:
    """True if the evidence ref cites a live prod-DB observation (spec 282 R4)."""
    return bool(_LIVE_DB_EVIDENCE_RE.search(evidence or ""))


# spec 402 R2 — typed-evidence registry. A criterion set CONFIRMED must cite
# evidence whose SHAPE matches its declared type, not merely bear the label. The
# type flags GATE the verdict: a cron criterion needs cron-run evidence, a
# financial one needs a reconciliation/cent-tolerance observation, a UI one needs a
# render/DOM observation, an observed-data one needs a live-DB query. The regexes
# mirror the existing `_LIVE_DB_EVIDENCE_RE`.
_CRON_EVIDENCE_RE = re.compile(
    r"(cron_runs|last[_-]?run|post[- ]?fire|fired at|exit_code|\bSELECT\b.+\bcron)",
    re.I | re.S,
)
_FINANCIAL_EVIDENCE_RE = re.compile(
    r"(cent[- ]?tol|within .*cent|±\s*\$?0\.0|reconcil|settlement.*(match|tie)|"
    r"abs\(.+\)\s*[<≤])",
    re.I,
)
_UI_EVIDENCE_RE = re.compile(
    r"(screenshot|rendered|\bDOM\b|selector|playwright|clicked|viewport|"
    r"observed .*render)",
    re.I,
)

# spec 402 R4 — an external-I/O criterion's CONFIRMED must reference a REAL observed
# call (a live request/response), not a stub/mock/fixture. If the evidence looks
# stub-only and carries no real-call marker, the criterion must be recorded as
# `integration-owed` instead of CONFIRMED.
_REAL_CALL_EVIDENCE_RE = re.compile(
    r"(real[- ]?call|live call|request[- ]?id|http/?\d|status 2\d\d|"
    r"actual .*(response|candidates?)|observed .*(call|response)|api returned)",
    re.I,
)
_STUB_ONLY_RE = re.compile(
    r"(stub|mock|monkeypatch|patched|fixture|pytest|test suite|responses\.add)",
    re.I,
)

# Valid evidence types (spec 402 R2). observed-data reuses the spec-282 live-DB
# gate; external-io (spec 402 R4) is gated separately by the real-call check.
_EVIDENCE_TYPE_RE = {
    "observed-data": _LIVE_DB_EVIDENCE_RE,
    "cron": _CRON_EVIDENCE_RE,
    "financial": _FINANCIAL_EVIDENCE_RE,
    "ui": _UI_EVIDENCE_RE,
}
_VALID_CRITERION_TYPES = {
    "observed-data",
    "cron",
    "financial",
    "ui",
    "external-io",
}
# Human-facing description of the evidence each type demands (for the refusal message).
_EVIDENCE_TYPE_SHAPE = {
    "observed-data": "a LIVE prod-DB observation (alembic_version / a SELECT…FROM against $SUPABASE_DB_URL / psql / catalog probe)",
    "cron": "a cron-run observation (cron_runs row / last-run / post-fire / exit_code / a SELECT against a cron table)",
    "financial": "a reconciliation observation (cent-tolerance / settlement tie-out / |diff| within ±$0.0x)",
    "ui": "a rendered-UI observation (screenshot / DOM / selector / playwright / viewport render)",
    "external-io": "≥1 observed REAL call (request-id / http status 2xx / actual response), not a stub/mock/fixture",
}


def manifest_trusted_for_ship(written_at, shipped_at) -> bool:
    """R3 (spec 402): a deploy-manifest's match is only trustworthy for verifying a
    spec if the manifest was written at/after the spec shipped. Returns False
    (UNTRUSTED) when the manifest predates the ship (stale) or written_at is
    unparseable/None. `shipped_at` None ⇒ nothing to be stale against ⇒ True."""
    wa = _parse_ts(written_at)
    if wa is None:
        return False
    sa = _parse_ts(shipped_at)
    if sa is None:
        return True
    return wa >= sa


def manifest_trusted(manifest: dict, shipped_at) -> bool:
    """R3 (spec 402) thin wrapper — reads `written_at` off a deploy-manifest dict."""
    return manifest_trusted_for_ship((manifest or {}).get("written_at"), shipped_at)


# F5 — contract binding. A verdict that asserts a hardcoded $ figure records the
# contract version it held under (`contract_version` on the verdict). When the
# fee/agreement contract is bumped, that verdict is stale-BY-DESIGN, not a
# regression: it flips to `needs-revalidation` (re-verify under the new contract),
# never a false `regression`. The single signal a contract bump updates:
_CONTRACT_VERSION_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "contract_version.txt"
)


def current_contract_version() -> str | None:
    """The canonical contract version (env override for tests). None ⇒ contract
    versioning not in use, so no verdict is ever flipped to needs-revalidation."""
    env = os.environ.get("CONTRACT_VERSION")
    if env is not None:
        return env.strip() or None
    try:
        return _CONTRACT_VERSION_FILE.read_text().strip() or None
    except OSError:
        return None


# --------------------------------------------------------------------------- IO


def _load_yaml(path: Path) -> dict:
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a mapping, got {type(data).__name__}")
    return data


def load_records() -> list[dict]:
    if not LEDGER_DIR.exists():
        return []
    out = []
    for path in sorted(LEDGER_DIR.glob("*.yml")):
        rec = _load_yaml(path)
        rec["_file"] = path.name
        out.append(rec)
    return out


# --------------------------------------------------------------------- helpers


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in (None,):  # try ISO first
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            break
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _merged_at(rec: dict) -> datetime | None:
    """When did this record reach `merged`? Read from history, fall back to None."""
    for entry in reversed(rec.get("history") or []):
        if isinstance(entry, dict) and entry.get("status") == "merged":
            return _parse_ts(entry.get("at"))
    return None


def _shipped_at(rec: dict) -> datetime | None:
    """When did this record reach `shipped`? From history, else None."""
    for entry in reversed(rec.get("history") or []):
        if isinstance(entry, dict) and entry.get("status") == "shipped":
            return _parse_ts(entry.get("at"))
    return None


def _hours_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def effective_status(rec: dict, verdict: dict | None) -> str:
    """The closure status, COMPUTED from the build record + the verifier verdict.

    `accepted` is never stored — it is derived here from `shipped ∧ CONFIRMED`,
    so the build ledger and the verifier verdict can never disagree. Pre-shipped
    records (and legacy records already stored as `accepted`) return their own
    stored status unchanged.

    Precedence for shipped records: REJECTED → needs_human → CONFIRMED → awaiting-verify.
    (spec 282 R1: a shipped record with no trusted CONFIRMED verdict renders as
    `awaiting-verify` — the same name as the settable R2 state, unifying the derived
    "shipped, awaiting rev's verdict" and the explicit one. Render already consults
    `~/.claude/ledger/verified/*.yml` via load_verdicts → a real CONFIRMED still
    derives `accepted`; this only fixes the LABEL of the not-yet-verified bucket.)
    An open needs_human beats CONFIRMED but not REJECTED (a definitive rejection wins).

    Spec 168 (Derived-Verdict Guard): the spec-level verdict consumed here is the
    DERIVED one (`derived_verdict`) — any standing REJECTED criterion blocks
    `accepted` regardless of a stored spec-level `verdict: CONFIRMED` from any judge.
    A builder-side grader can no longer spoof `accepted` over rev's red criteria.
    """
    status = rec.get("status")
    if status != "shipped":
        return status  # pre-shipped lifecycle, and legacy stored `accepted`
    # F5: a $-asserting verdict whose contract_version drifted from current is
    # stale-by-design — re-verify under the new contract, never a false regression.
    cv = (verdict or {}).get("contract_version")
    if cv is not None:
        cur = current_contract_version()
        if cur is not None and cv != cur:
            return "needs-revalidation"
    v = derived_verdict(verdict)
    if v == "REJECTED":
        return "needs-rework"
    if (verdict or {}).get("needs_human"):
        return "needs-human"
    if v == "CONFIRMED":
        return "accepted"
    return "awaiting-verify"


# --------------------------------------------------------------------- validate


def validate(records: list[dict]) -> list[str]:
    errors: list[str] = []
    for rec in records:
        sid = rec.get("spec_id", rec.get("_file", "?"))
        status = rec.get("status")
        if status not in VALID_STATUS:
            errors.append(f"{sid}: invalid status {status!r}")
        if status == "held" and not (rec.get("held_reason") or "").strip():
            errors.append(f"{sid}: status=held requires a non-empty held_reason")
        if status == "bounced" and not (rec.get("bounce_reason") or "").strip():
            errors.append(f"{sid}: status=bounced requires a non-empty bounce_reason")
        if status == "rework" and not (rec.get("rework_reason") or "").strip():
            errors.append(f"{sid}: status=rework requires a non-empty rework_reason")
        if status == "superseded" and not (rec.get("superseded_by") or "").strip():
            errors.append(f"{sid}: status=superseded requires superseded_by")
    return errors


def stale_held_reason_records(records: list[dict]) -> list[tuple[str, str]]:
    """spec 282 R3 — post-ship records carrying a held_reason that still asserts a
    PRE-ship reality (the 269 pattern: status advanced, free-text note left behind).
    Returned as (spec_id, held_reason); the `lint` command exits non-zero on any.
    Kept OUT of validate()/--check (the deploy gate) so a pre-existing stale record
    can't red-fail a deploy; the cmd_set auto-supersede prevents NEW ones, and this
    dedicated lint surfaces the legacy offenders for the integrator to clean."""
    out: list[tuple[str, str]] = []
    for rec in records:
        if rec.get("status") not in _POST_SHIP_STATES:
            continue
        hr = (rec.get("held_reason") or "").strip()
        if hr and any(p in hr.lower() for p in _STALE_HELD_REASON_MARKERS):
            out.append((rec.get("spec_id", rec.get("_file", "?")), hr))
    return out


# ----------------------------------------------------------------------- render


def _line(rec: dict) -> str:
    sid = rec.get("spec_id", "?")
    title = rec.get("title", "")
    return f"{sid} — {title}".rstrip(" —")


def render(records: list[dict], include_all: bool) -> str:
    verdicts = load_verdicts()
    nh_store = load_needs_human()
    eff = {
        r.get("spec_id", r.get("_file", "?")): effective_status(
            r, verdicts.get(r.get("spec_id"))
        )
        for r in records
    }

    def _eff(r):
        return eff[r.get("spec_id", r.get("_file", "?"))]

    needs_human = [
        r
        for r in records
        if (
            r.get("needs_human")
            or r.get("status") == "unknown"
            or _eff(r) == "needs-human"
        )
        and _eff(r) != "needs-rework"
    ]
    needs_rework = [r for r in records if _eff(r) == "needs-rework"]
    needs_reval = [r for r in records if _eff(r) == "needs-revalidation"]
    ready_to_merge = [r for r in records if r.get("status") == "ready"]
    gating = [r for r in records if r.get("status") == "gating"]  # spec 300
    outstanding = [
        r
        for r in records
        if r.get("status") in OUTSTANDING_STATUSES
        and r.get("status") != "ready"  # spec 252: ready has its own distinct section
        and r.get("status") != "gating"  # spec 300: gating has its own distinct section
        and not (r.get("needs_human") or r.get("status") == "unknown")
    ]
    # spec 282 R1/R2: a shipped record with no trusted CONFIRMED verdict derives
    # "awaiting-verify" (same bucket as the explicitly-settable R2 state); a record
    # SET to awaiting-data renders in its own bucket (merged/live, owes only data).
    awaiting_verify = [r for r in records if _eff(r) == "awaiting-verify"]
    awaiting_data = [r for r in records if _eff(r) == "awaiting-data"]
    accepted = [r for r in records if _eff(r) == "accepted"]
    superseded = [r for r in records if r.get("status") == "superseded"]

    L: list[str] = []
    L.append(
        "<!-- DO NOT EDIT — generated by scripts/spec_ledger.py. Edit the per-spec"
    )
    L.append("     files in ~/.claude/ledger/ and re-run the script. -->")
    L.append("")
    L.append("# Spec Build-Status — what's outstanding")
    L.append("")
    L.append(
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"from {len(records)} spec record(s)._"
    )
    L.append("")
    flags = load_liveness()
    for flag in flags:
        L.append(f"> 🚨 **{flag}**")
    if flags:
        L.append("")

    # --- prod-verified hollow: the loudest thing on the board ---
    if needs_rework:
        L.append(f"## ❌ NEEDS-REWORK — prod-verified hollow ({len(needs_rework)})")
        for r in needs_rework:
            v = verdicts.get(r.get("spec_id")) or {}
            L.append(f"- ❌ {_line(r)} — **REJECTED** (judge: {v.get('judge', '?')})")
        L.append("")

    # --- F5: contract drifted under a $-asserting verdict — re-verify, NOT a regression ---
    if needs_reval:
        L.append(
            f"## 🔄 NEEDS-REVALIDATION — contract bumped under a $-verdict ({len(needs_reval)})"
        )
        cur = current_contract_version()
        for r in needs_reval:
            v = verdicts.get(r.get("spec_id")) or {}
            L.append(
                f"- 🔄 {_line(r)} — held under contract {v.get('contract_version', '?')}, "
                f"now {cur} (re-verify under the new contract)"
            )
        L.append("")

    # --- could-not-classify first (backfill ambiguity) ---
    if needs_human:
        L.append(f"## ⚠ {len(needs_human)} spec(s) I couldn't classify — eyeball these")
        for r in needs_human:
            note = r.get("note") or r.get("needs_human_reason") or ""
            L.append(f"- {_line(r)}" + (f"  · {note}" if note else ""))
        L.append("")

    if nh_store:
        L.append(f"## 🙋 NEEDS-HUMAN — escalations awaiting you ({len(nh_store)})")
        for r in nh_store:
            note = r.get("note") or ""
            L.append(
                f"- {r.get('spec_id', '?')} — **{r.get('reason', '?')}**"
                + (f": {note}" if note else "")
            )
        L.append("")

    # --- outstanding ---
    L.append(f"## Outstanding ({len(outstanding)})")
    if not outstanding:
        L.append("_None._")
    for r in outstanding:
        status = r.get("status")
        if status == "bounced":
            L.append(
                f"- ⛔ {_line(r)} — **BOUNCED**: {r.get('bounce_reason', '(no reason!)')}"
                + (f" · needs: {r['needs']}" if r.get("needs") else "")
            )
        elif status == "rework":
            L.append(
                f"- 🔁 {_line(r)} — **REWORK (→ orc)**: {r.get('rework_reason', '(no reason!)')}"
            )
        elif status == "held":
            L.append(
                f"- ⏸ {_line(r)} — **HELD**: {r.get('held_reason', '(no reason!)')}"
            )
        elif status == "merged":
            hrs = _hours_since(_merged_at(r))
            if hrs is not None and hrs > STALE_MERGED_HOURS:
                days = round(hrs / 24, 1)
                L.append(f"- ⚠ {_line(r)} — merged {days}d, **why isn't this live?**")
            else:
                L.append(f"- ◐ {_line(r)} — merged, awaiting deploy")
        else:
            L.append(f"- ○ {_line(r)} — {status}")
    L.append("")

    # --- spec 300: gating — pushed, under pane-independent close-out grader ---
    L.append(f"## 🕓 Gating (detached close-out) ({len(gating)})")
    if not gating:
        L.append("_None._")
    for r in gating:
        L.append(
            f"- 🕓 {_line(r)} — **GATING** (close-out grader running; PASS→ready, FAIL→rework)"
        )
    L.append("")

    # --- spec 252: ready to merge — distinct from building, before merged/shipped ---
    L.append(f"## 🟢 Ready to merge (awaiting integrator) ({len(ready_to_merge)})")
    if not ready_to_merge:
        L.append("_None._")
    for r in ready_to_merge:
        branch = r.get("branch", "")
        ready_sha = r.get("ready_sha", "")
        suffix_parts = []
        if branch:
            suffix_parts.append(f"branch: {branch}")
        if ready_sha:
            suffix_parts.append(f"sha: {ready_sha[:8]}")
        suffix = "  · " + ", ".join(suffix_parts) if suffix_parts else ""
        L.append(f"- 🟢 {_line(r)} — **READY**{suffix}")
    L.append("")

    # --- spec 282 R2: merged/live but owes only data (no code to claim) ---
    L.append(f"## Awaiting data ({len(awaiting_data)})")
    if not awaiting_data:
        L.append("_None._")
    for r in awaiting_data:
        note = r.get("awaiting_data_reason") or r.get("note") or ""
        L.append(
            f"- 📊 {_line(r)} — **AWAITING-DATA** (no code; backfill/cron/re-verify)"
            + (f": {note}" if note else "")
        )
    L.append("")

    # --- shipped/live, awaiting rev's verdict (spec 282 R1: renamed from
    #     "awaiting prod-verification"; the derived shipped∧¬CONFIRMED and the
    #     explicitly-set awaiting-verify state share this one truthful bucket) ---
    L.append(f"## Awaiting verification ({len(awaiting_verify)})")
    if not awaiting_verify:
        L.append("_None._")
    for r in awaiting_verify:
        card = r.get("review_card")
        suffix = f"  · card: {card}" if card else ""
        L.append(f"- 🚀 {_line(r)}{suffix}")
    L.append("")

    # --- accepted / superseded (only under --all, except a count) ---
    L.append(
        f"## Accepted ({len(accepted)})"
        + ("" if include_all else " — use --all to list")
    )
    if include_all:
        for r in accepted:
            L.append(f"- ✅ {_line(r)}")
        L.append("")
        L.append(f"## Superseded ({len(superseded)})")
        for r in superseded:
            L.append(f"- ↪ {_line(r)} → {r.get('superseded_by', '?')}")
    L.append("")

    return "\n".join(L).rstrip() + "\n"


# ------------------------------------------------------------------- write (helper)
#
# The ONLY supported way to create/advance a record. Sessions call these instead of
# hand-editing YAML — which is what produced the indentation breakage and the
# missing-superseded_by bugs. Every write is re-validated with the SAME validate()
# the --check gate uses, so an invalid record can't be born.

# status -> the field that must accompany it (enforced by validate()).
_REASON_FIELD = {
    "held": "held_reason",
    "bounced": "bounce_reason",
    "rework": "rework_reason",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_path(spec_id: str) -> Path:
    return LEDGER_DIR / f"{spec_id}.yml"


def _die(msg: str) -> "int":
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _write_record(path: Path, rec: dict) -> None:
    """Atomic, valid-YAML write (tmp-then-rename). Strips internal keys, keeps order."""
    clean = {k: v for k, v in rec.items() if not k.startswith("_")}
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(
            clean, fh, sort_keys=False, allow_unicode=True, default_flow_style=False
        )
    os.replace(tmp, path)


def _registered_record(
    spec_id: str, title: str, intent: str, spec_file: str, source_brief, by: str
) -> dict:
    """Build a born-`registered` ledger record. Shared by `register` (explicit id)
    and `next-num` (allocated id) so both birth identical, valid records."""
    now = _now_iso()
    sb = source_brief
    if sb is not None:
        try:
            sb = int(sb)
        except ValueError:
            pass
    return {
        "spec_id": spec_id,
        "title": title,
        "intent": intent,
        "status": "registered",
        "handed_over_at": now,
        "spec_file": spec_file,
        "source_brief": sb,
        "history": [{"at": now, "status": "registered", "by": by}],
    }


def cmd_register(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="spec_ledger.py register")
    ap.add_argument("spec_id")
    ap.add_argument("--title", required=True)
    ap.add_argument("--intent", required=True)
    ap.add_argument("--spec-file", dest="spec_file", required=True)
    ap.add_argument("--source-brief", dest="source_brief")
    ap.add_argument("--by", default="handover")
    a = ap.parse_args(argv)

    path = _record_path(a.spec_id)
    if path.exists():
        return _die(f"{a.spec_id} already exists — use `set` to advance it")
    rec = _registered_record(
        a.spec_id, a.title, a.intent, a.spec_file, a.source_brief, a.by
    )
    errs = validate([rec])
    if errs:
        return _die("refusing to write — " + "; ".join(errs))
    with _record_lock(path):
        # re-read-modify-write inside the lock to avoid lost updates
        if path.exists():
            fresh = _load_yaml(path)
            fresh.update({k: v for k, v in rec.items() if k != "history"})
            fresh.setdefault("history", []).extend(
                rec["history"][len(fresh.get("history", [])) :]
            )
            rec = fresh
        _write_record(path, rec)
    print(f"registered {a.spec_id}")
    return 0


def _clean_slug(raw: str) -> str | None:
    s = raw.strip().strip("-")
    if not s or "/" in s or os.sep in s:
        return None
    return s


def cmd_next_num(argv: list[str]) -> int:
    """Atomically allocate the next shared bus number AND reserve it, under the
    bus-wide lock, so concurrent sessions can't double-book (the 110 collision).

    The reservation IS the artifact — no placeholder, no reaper:
      * spec  -> births the real `registered` ledger record (same as `register`,
                 but with an allocated id). Caller then places the spec file named
                 after the printed number. Do NOT also call `register`.
      * brief -> writes the brief file itself into brief-inbox (the persistent
                 artifact that claims the number); the think session fills its body.

    Prints the zero-padded 3-digit number on stdout, nothing else, on success.
    """
    ap = argparse.ArgumentParser(prog="spec_ledger.py next-num")
    ap.add_argument("--kind", choices=["spec", "brief"], required=True)
    ap.add_argument("--slug", required=True)
    # Required for --kind spec (the record is born now, so it needs its fields):
    ap.add_argument("--title")
    ap.add_argument("--intent")
    ap.add_argument("--spec-file", dest="spec_file")
    ap.add_argument("--source-brief", dest="source_brief")
    ap.add_argument("--by", default="handover")
    a = ap.parse_args(argv)

    slug = _clean_slug(a.slug)
    if slug is None:
        return _die(f"bad slug {a.slug!r}")
    if a.kind == "spec":
        missing = [f for f in ("title", "intent", "spec_file") if not getattr(a, f)]
        if missing:
            return _die(
                "spec allocation needs --"
                + ", --".join(m.replace("_", "-") for m in missing)
                + " (the record is born now, so it must be complete)"
            )

    with _bus_lock():
        hi, second = scan_bus_top_two()
        nxt = hi + 1
        if second and (hi - second) > ALLOC_GAP_CEILING:
            return _die(
                f"highest bus number {hi} is {hi - second} above the next-highest "
                f"({second}) — far past the dense live sequence, the signature of a "
                f"mis-numbered file poisoning the max. Hunt it before allocating:\n"
                f"    ls ~/.claude/spec-inbox ~/.claude/spec-inbox/_archive ~/.claude/ledger \\\n"
                f"       ~/.claude/brief-inbox ~/.claude/brief-inbox/_archive | sort -n | tail\n"
                f"fix the offender, then re-run. Refusing to bake {nxt} onto a poisoned max."
            )
        nnn = f"{nxt:03d}"
        spec_id = f"{nnn}-{slug}"

        if a.kind == "spec":
            path = _record_path(spec_id)
            if path.exists():
                return _die(f"{spec_id} already exists — pick a different slug")
            rec = _registered_record(
                spec_id, a.title, a.intent, a.spec_file, a.source_brief, a.by
            )
            errs = validate([rec])
            if errs:
                return _die("refusing to write — " + "; ".join(errs))
            _write_record(path, rec)
        else:  # brief
            BRIEF_INBOX.mkdir(parents=True, exist_ok=True)
            brief_path = BRIEF_INBOX / f"{spec_id}.brief.md"
            if brief_path.exists():
                return _die(f"{brief_path.name} already exists — pick a different slug")
            tmp = brief_path.with_suffix(brief_path.suffix + ".tmp")
            tmp.write_text(
                f"---\ntopic: {slug}\nproblem:\nstatus: develop-later\n---\n\n"
                "<!-- think fills topic + problem (one paragraph: who hurts and how) -->\n"
            )
            os.replace(tmp, brief_path)

    print(nnn)
    return 0


def cmd_set(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="spec_ledger.py set")
    ap.add_argument("spec_id")
    ap.add_argument("status")
    ap.add_argument("--by", required=True)
    ap.add_argument("--reason", help="for held / bounced / rework")
    ap.add_argument("--superseded-by", dest="superseded_by", help="for superseded")
    ap.add_argument("--needs", help="optional, for bounced")
    ap.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="extra metadata, e.g. --field shipped_sha=abc --field review_card=x.review.md",
    )
    a = ap.parse_args(argv)

    if a.status not in VALID_STATUS:
        return _die(f"invalid status {a.status!r} (one of {sorted(VALID_STATUS)})")
    if a.status == "accepted":
        return _die(
            "accepted is computed-only — it is derived from shipped ∧ a CONFIRMED "
            "prod verdict (see effective_status); do not set it by hand"
        )
    path = _record_path(a.spec_id)
    if not path.exists():
        return _die(f"no ledger record {a.spec_id} — use `register` first")
    rec = _load_yaml(path)

    rec["status"] = a.status
    if a.status in _REASON_FIELD and a.reason:
        rec[_REASON_FIELD[a.status]] = a.reason
    if a.superseded_by:
        rec["superseded_by"] = a.superseded_by
    if a.needs:
        rec["needs"] = a.needs
    # spec 282 R3: advancing to a post-ship state SUPERSEDES any stale pre-ship
    # held_reason (the 269 pattern — status moved on but the note still says
    # "pending operator" / "not re-deployed"). Preserve it under held_reason_cleared
    # for the audit trail, then drop the live field so the record can't contradict
    # its own status (and so the R3 --check lint stays green for a clean transition).
    _clear_stale_held_reason = False
    if a.status in _POST_SHIP_STATES:
        _hr = (rec.get("held_reason") or "").strip()
        if _hr and any(p in _hr.lower() for p in _STALE_HELD_REASON_MARKERS):
            rec["held_reason_cleared"] = _hr
            rec.pop("held_reason", None)
            _clear_stale_held_reason = (
                True  # also applied to the re-read rec inside the lock
            )
    for kv in a.field:
        if "=" not in kv:
            return _die(f"--field must be KEY=VALUE, got {kv!r}")
        k, v = kv.split("=", 1)
        if k == "owed_data_acs":
            # spec 357: owed-data verdict carries a LIST of deferred observed-data ACs;
            # gating-watch passes it as a compact JSON array string. Store as a real list.
            try:
                parsed = json.loads(v)
            except (ValueError, TypeError):
                parsed = None
            rec[k] = parsed if isinstance(parsed, list) else v
        else:
            rec[k] = v
    new_entry = {"at": _now_iso(), "status": a.status, "by": a.by}
    rec.setdefault("history", []).append(new_entry)

    errs = validate([rec])
    if errs:  # e.g. rework with no --reason, superseded with no --superseded-by
        return _die("refusing to write — " + "; ".join(errs))

    # Collect the scalar field updates (excluding history — merged separately).
    updates = {k: v for k, v in rec.items() if k != "history"}
    prev_status = None
    with _record_lock(path):
        # Re-read inside the lock so concurrent writers don't lose each other's
        # history entries (both may have read the pre-lock snapshot). The locked
        # re-read also gives the authoritative PREVIOUS status (spec 278 R3).
        if path.exists():
            rec = _load_yaml(path)
            prev_status = rec.get("status")
            rec.update(updates)
            # spec 282 R3: `updates` can only add/overwrite keys; a superseded stale
            # held_reason must be explicitly DELETED from the re-read record too.
            if _clear_stale_held_reason:
                rec.pop("held_reason", None)
            rec.setdefault("history", []).append(new_entry)
        _write_record(path, rec)
    print(f"{a.spec_id} → {a.status}")
    # Spec 278 R3 — deterministic integrator→rev poke on a real transition INTO
    # `shipped`. Fires on registered→…→shipped AND rework→…→shipped (prev != shipped),
    # never on a no-op `set shipped` over an already-shipped row. Best-effort.
    if a.status == "shipped" and prev_status != "shipped":
        _poke_rev_on_ship(a.spec_id)
    return 0


def _poke_rev_on_ship(spec_id: str) -> None:
    """Side-effect of a ship transition: refresh the deploy-manifest, THEN poke
    rev — and only if the manifest confirms prod is serving the just-shipped sha
    (spec 278 R3 + spec 281 R1).

    Under the fast parallel cadence the manifest written on a slower deploy
    cadence was routinely stale when rev read it, so rev was poked to verify
    against a sha that wasn't live yet — "match:no was a lie" (incidents 269/270/
    255). R1 makes the ship event itself produce the truth it pokes on, in strict
    order:
      1. run scripts/write_deploy_manifest.sh so the manifest records the
         just-shipped sha with a fresh written_at;
      2. parse it and log the manifest write (with its written_at) BEFORE any
         poke line;
      3. fire the rev poke (carrying shipped_sha) ONLY if the manifest's
         prod_serving_sha equals the shipped sha (match:yes); otherwise suppress
         it with a `deploy-not-landed` log.

    Absence/parse-failure of the manifest is NOT a mismatch — it falls back to
    firing the poke, preserving the spec-278 behavior (a manifest we can't read
    must not silence a real ship).

    Best-effort by contract — a manifest or poke failure must NEVER affect the
    ledger write that already succeeded. The poke delivery itself
    (scripts/poke_rev_on_ship.sh) owns pane resolution, the corrective-278
    real-ledger guard, relay-collision guard, NUDGE_DRY handling, and liveness.

    Env overrides (defaults preserve prod):
      DEPLOY_MANIFEST_PATH   — manifest file (default ~/.claude/deploy-manifest.json)
      SHIP_HOOK_MANIFEST_CMD — manifest writer cmd (default the bundled script)
      SHIP_HOOK_LOG          — ship-hook ordering log (default ~/.claude/ship-hook.log)
    """
    scripts_dir = Path(__file__).resolve().parent
    poke_script = scripts_dir / "poke_rev_on_ship.sh"
    if not poke_script.exists():
        return

    def _delegate_poke(extra_env: dict | None = None) -> None:
        """Fire poke_rev_on_ship.sh, which owns the corrective-278 real-ledger
        guard + pane resolution + NUDGE_DRY/liveness. Used directly when the
        manifest dance is not applicable (preserves spec-278 behavior)."""
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        try:
            subprocess.run(
                ["bash", str(poke_script), spec_id],
                timeout=20,
                capture_output=True,
                text=True,
                env=env,
            )
        except Exception:  # noqa: BLE001
            pass

    # Run the manifest dance ONLY for a genuine prod ship (the integrator runs
    # `set shipped` against the canonical ~/.claude/ledger, no real-ledger
    # override) OR when a test explicitly opts in by pointing the manifest at a
    # sandbox. A fixture ship that merely overrides the real-ledger to a sandbox
    # (the AC4 corrective-278 tests) must NOT ssh to prod or touch the real
    # manifest — it delegates straight to the poke (whose own guard then runs).
    real_ledger_overridden = (
        "PANE_POKE_REAL_LEDGER_DIR" in os.environ
        or "NUDGE_REAL_LEDGER_DIR" in os.environ
    )
    manifest_opt_in = (
        "SHIP_HOOK_MANIFEST_CMD" in os.environ or "DEPLOY_MANIFEST_PATH" in os.environ
    )
    if real_ledger_overridden and not manifest_opt_in:
        _delegate_poke()
        return

    try:
        manifest_path = Path(
            os.environ.get(
                "DEPLOY_MANIFEST_PATH",
                str(Path.home() / ".claude" / "deploy-manifest.json"),
            )
        )
        log_path = Path(
            os.environ.get(
                "SHIP_HOOK_LOG", str(Path.home() / ".claude" / "ship-hook.log")
            )
        )

        def _log(msg: str) -> None:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                with log_path.open("a") as fh:
                    fh.write(f"{ts} ship-hook[{spec_id}]: {msg}\n")
            except Exception:  # noqa: BLE001
                pass

        # 1. Refresh the manifest FIRST (best-effort; exit 1 on mismatch still
        #    writes the file, so we read the file, not the exit code).
        manifest_cmd = os.environ.get("SHIP_HOOK_MANIFEST_CMD")
        argv = (
            shlex.split(manifest_cmd)
            if manifest_cmd
            else ["bash", str(scripts_dir / "write_deploy_manifest.sh")]
        )
        try:
            subprocess.run(argv, timeout=90, capture_output=True, text=True)
        except Exception as e:  # noqa: BLE001 — ship must not fail on manifest
            _log(f"manifest writer failed ({e!r})")

        # 2. Parse the manifest and log the write BEFORE any poke line.
        master_sha = prod_serving_sha = written_at = match = None
        try:
            data = json.loads(manifest_path.read_text())
            master_sha = data.get("master_sha")
            prod_serving_sha = data.get("prod_serving_sha")
            written_at = data.get("written_at")
            match = data.get("match")
        except Exception:  # noqa: BLE001 — absent/unreadable manifest
            data = None

        shipped_sha = master_sha or ""

        if data is None:
            # No readable manifest — fall back to firing (absence != mismatch).
            _log("manifest absent/unreadable; firing poke (fallback, preserves 278)")
            should_poke = True
        else:
            _log(
                f"manifest written written_at={written_at} master={master_sha} "
                f"prod_serving={prod_serving_sha} match={match}"
            )
            # Gate: prod must be serving the just-shipped sha. The manifest writer
            # already computes the authoritative `match` (prefix-compares short/full
            # shas) — trust it whenever present (an explicit match=no must suppress,
            # even if raw prefixes happen to overlap). Only when the field is
            # absent/unrecognized do we fall back to an explicit prefix compare.
            if match in ("yes", "no"):
                should_poke = match == "yes"
            else:
                should_poke = (
                    bool(shipped_sha)
                    and bool(prod_serving_sha)
                    and prod_serving_sha not in ("", "unknown")
                    and (
                        prod_serving_sha.startswith(shipped_sha)
                        or shipped_sha.startswith(prod_serving_sha)
                    )
                )
            if not should_poke:
                _log(
                    f"deploy-not-landed: prod_serving={prod_serving_sha} != "
                    f"shipped={shipped_sha}; poke suppressed"
                )

        if not should_poke:
            return

        # 3. Fire the rev poke, carrying the shipped sha.
        _log(f"poke fired shipped_sha={shipped_sha}")
        _delegate_poke({"SHIPPED_SHA": shipped_sha} if shipped_sha else None)
    except Exception:  # noqa: BLE001 — ship must not fail because a poke did
        pass


# ----------------------------------------------------------------- verify (verifier-owned namespace)


def _verified_path(spec_id: str) -> Path:
    return _get_verified_dir() / f"{spec_id}.yml"


def load_needs_human() -> list[dict]:
    """Unresolved escalations from the durable needs-human store
    (LEDGER_DIR/needs-human/*.yml, written by rev/the verifier)."""
    out: list[dict] = []
    nhdir = LEDGER_DIR / "needs-human"
    if not nhdir.exists():
        return out
    for path in sorted(nhdir.glob("*.yml")):
        try:
            rec = _load_yaml(path)
        except Exception as e:
            out.append({"spec_id": path.stem, "reason": "PARSE_ERROR", "note": str(e)})
            continue
        if not rec.get("resolved"):
            out.append(rec)
    return out


def load_liveness() -> list[str]:
    """Active dead-man's-switch flags written by relay-watch/liveness.sh."""
    d = LEDGER_DIR / "liveness"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.is_file():
            out.append(f"{p.name}: {p.read_text().strip()}")
    return out


def load_verdicts() -> dict[str, dict]:
    """spec_id -> verdict record. Read-only mirror of the verifier namespace."""
    out: dict[str, dict] = {}
    vdir = _get_verified_dir()
    if not vdir.exists():
        return out
    for path in sorted(vdir.glob("*.yml")):
        rec = _load_yaml(path)
        out[rec.get("spec_id", path.stem)] = rec
    return out


def _write_verdict(path: Path, rec: dict) -> None:
    """Atomic write into the verified/ subdir."""
    vdir = _get_verified_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in rec.items() if not k.startswith("_")}
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(
            clean, fh, sort_keys=False, allow_unicode=True, default_flow_style=False
        )
    os.replace(tmp, path)


def cmd_verify(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="spec_ledger.py verify")
    ap.add_argument("spec_id")
    ap.add_argument(
        "verdict", nargs="?"
    )  # optional: derived from --criterion when given
    ap.add_argument("--judge", required=True)  # codex | claude-fallback
    ap.add_argument("--evidence", required=True)  # ref to the typed evidence artifact
    ap.add_argument(
        "--contract-version",
        default=None,
        help="F5: the contract version a $-asserting verdict held under; a later "
        "contract bump flips this verdict to needs-revalidation (not regression)",
    )
    ap.add_argument(
        "--criterion",
        action="append",
        default=[],
        metavar="ID=VERDICT",
        help="per-criterion verdict; spec-level verdict is DERIVED from the full map",
    )
    # spec 282 R4: a criterion typed observed-data / migration-bearing may only be
    # CONFIRMED against a LIVE-DB observation (alembic_version + a prod table/row
    # query), never a manifest- or git-ancestry-only artifact (which lags under the
    # fast cadence). List such criteria here; for the legacy whole-spec path use
    # --observed-data.
    ap.add_argument(
        "--observed-data-criterion",
        dest="observed_data_criteria",
        action="append",
        default=[],
        metavar="ID",
        help="mark criterion ID as observed-data/migration-bearing (CONFIRMED needs live-DB evidence)",
    )
    ap.add_argument(
        "--observed-data",
        action="store_true",
        help="legacy whole-spec: this verdict is observed-data/migration-bearing (CONFIRMED needs live-DB evidence)",
    )
    # spec 402 R1 — the FULL declared criterion set. Without it a partial map
    # (rev records 4 of 12, all CONFIRMED) derives a false spec-level CONFIRMED (the
    # 390 false-accept). Give it as a count (expands to {c1..cN}) OR an explicit id
    # list; it persists on the record so it sticks across incremental calls.
    ap.add_argument(
        "--criteria-count",
        dest="criteria_count",
        type=int,
        default=None,
        help="spec 402 R1: total declared criteria N; expands to the set {c1..cN}",
    )
    ap.add_argument(
        "--declared-criteria",
        dest="declared_criteria",
        default=None,
        metavar="c1,c2,c3",
        help="spec 402 R1: comma-separated FULL declared criterion id set",
    )
    # spec 402 R2 — typed-evidence registry. A criterion's CONFIRMED must cite
    # evidence whose SHAPE matches its type. Persisted so the gate holds across calls.
    ap.add_argument(
        "--criterion-type",
        dest="criterion_types",
        action="append",
        default=[],
        metavar="ID=TYPE",
        help="spec 402 R2: type a criterion (observed-data|cron|financial|ui|external-io); "
        "CONFIRMED must then match that evidence shape",
    )
    # spec 402 R4 — shorthand for type external-io (parallel to --observed-data-criterion).
    ap.add_argument(
        "--external-io-criterion",
        dest="external_io_criteria",
        action="append",
        default=[],
        metavar="ID",
        help="spec 402 R4: mark criterion ID as external-I/O (CONFIRMED needs an observed real call, else set =integration-owed)",
    )
    a = ap.parse_args(argv)

    now = _now_iso()
    path = _verified_path(a.spec_id)
    rec = _load_yaml(path) if path.exists() else {"spec_id": a.spec_id, "history": []}

    # spec 402 R1 — resolve the FULL declared criterion set (flags XOR persisted).
    # Once declared via a flag it PERSISTS on the record, so it sticks across the
    # incremental calls rev makes; a later call may omit the flag and still be gated.
    if a.criteria_count is not None and a.declared_criteria is not None:
        return _die(
            "pass EITHER --criteria-count N OR --declared-criteria c1,c2,... "
            "(not both — they name the same declared set two ways)"
        )
    declared_from_flag = None
    if a.criteria_count is not None:
        if a.criteria_count < 1:
            return _die(f"--criteria-count must be >= 1, got {a.criteria_count}")
        declared_from_flag = [f"c{i}" for i in range(1, a.criteria_count + 1)]
    elif a.declared_criteria is not None:
        declared_from_flag = [
            c.strip() for c in a.declared_criteria.split(",") if c.strip()
        ]
    if declared_from_flag is not None:
        declared = sorted(set(declared_from_flag))  # canonicalize + persist
        rec["declared_criteria"] = declared
    else:
        declared = rec.get("declared_criteria")  # persisted, or None (legacy)

    missing: list[str] = []  # spec 402 R1 — omitted declared ids, named in messages
    if a.criterion:
        # Pass 1: validate all entries WITHOUT mutating crit (so a bad entry
        # never partially applies its predecessors).
        for kv in a.criterion:
            if "=" not in kv:
                return _die(f"--criterion must be ID=VERDICT, got {kv!r}")
            _k, _v = kv.split("=", 1)
            if _v not in VALID_CRITERION_VERDICT:
                return _die(
                    f"invalid criterion verdict {_v!r} for {_k!r} "
                    f"(one of {sorted(VALID_CRITERION_VERDICT)})"
                )
        # Pass 2: all entries are valid — apply them.
        crit = rec.setdefault("criteria", {})
        for kv in a.criterion:
            k, v = kv.split("=", 1)
            crit[k] = v
        # Spec 168 R1/R3 + spec 402 R1: derive HONOURING owner-waivers AND the full
        # declared set (an incomplete map can no longer derive a spec-level pass).
        spec_verdict = resolve_spec_verdict(crit, rec.get("waivers"), declared)
        # spec 402 R1 fail-safe (critical): without a KNOWN declared set, a
        # spec-level CONFIRMED is un-trustworthy — the aggregator is blind to any
        # criteria that were simply never recorded (the 390 false-accept: 4 of 12
        # recorded, all CONFIRMED). Refuse and force rev to declare N first.
        if declared is None and (
            resolve_spec_verdict(crit, rec.get("waivers"), None) == "CONFIRMED"
        ):
            return _die(
                f"REFUSED (spec 402 R1): cannot derive a spec-level CONFIRMED for "
                f"{a.spec_id} without the FULL declared criterion set — a partial "
                f"map (e.g. 4 of 12 criteria, all CONFIRMED) would false-accept (the "
                f"390 incident). Pass --criteria-count N or --declared-criteria "
                f"c1,c2,... to declare the full set."
            )
        # spec 402 R1 missing-name: name the omitted declared criteria in messages.
        missing = [d for d in declared if d not in crit] if declared else []
        # The verifier may pass a spec-level verdict only if it agrees with the
        # derived one — we refuse a caller-supplied verdict that overrides the map.
        if a.verdict is not None and a.verdict != spec_verdict:
            _miss = f" (missing declared criteria: {missing})" if missing else ""
            return _die(
                f"refusing caller-supplied spec verdict {a.verdict!r}: the criteria "
                f"map derives {spec_verdict!r}{_miss}"
            )
    else:
        # Legacy path: explicit spec-level verdict, no per-criterion map.
        if a.verdict is None:
            return _die("verify needs either a verdict or one or more --criterion")
        if a.verdict not in VALID_VERDICT:
            return _die(
                f"invalid verdict {a.verdict!r} (one of {sorted(VALID_VERDICT)})"
            )
        # Spec 168 R2 (write-time block): even on the legacy path, a spec-level
        # CONFIRMED is REFUSED while the record carries a standing REJECTED criterion
        # without a valid owner-waiver. This is the hole `blind-closeout` walked
        # through (criteria already on the record from rev, then a bare CONFIRMED
        # stamp). A non-contradicting CONFIRMED (no red criteria) still writes.
        if a.verdict == "CONFIRMED":
            standing = rec.get("criteria") or {}
            derived = resolve_spec_verdict(standing, rec.get("waivers"))
            if derived == "REJECTED":
                red = [
                    cid
                    for cid, v in standing.items()
                    if v == "REJECTED"
                    and not _waiver_is_valid((rec.get("waivers") or {}).get(cid), cid)
                ]
                return _die(
                    f"REFUSED (spec 168): cannot stamp spec-level CONFIRMED on "
                    f"{a.spec_id} — criteria {red} stand REJECTED. A builder-side "
                    f"grader may CONFIRM individual criteria with evidence "
                    f"(`--criterion {red[0]}=CONFIRMED --evidence ...`) or an owner "
                    f"may waive a named criterion (`waive {a.spec_id} --criterion "
                    f"{red[0]} --human NAME --reason ...`); it may NOT override a "
                    f"standing rev REJECTED with a bare spec-level stamp."
                )
        spec_verdict = a.verdict

    # spec 402 R2/R4 — typed-evidence registry + GATE (generalizes the spec-282
    # observed-data block below). A criterion set CONFIRMED must cite evidence whose
    # SHAPE matches its declared type; the type is persisted so the gate holds across
    # calls. Build the registry from persisted ∪ this-call flags.
    criterion_types = dict(rec.get("criterion_types") or {})
    # back-compat: legacy persisted observed_data/external_io lists mirror in.
    for _cid in rec.get("observed_data_criteria", []) or []:
        criterion_types.setdefault(_cid, "observed-data")
    for _cid in rec.get("external_io_criteria", []) or []:
        criterion_types.setdefault(_cid, "external-io")
    for kv in a.criterion_types:
        if "=" not in kv:
            return _die(f"--criterion-type must be ID=TYPE, got {kv!r}")
        _cid, _ctype = (s.strip() for s in kv.split("=", 1))
        if _ctype not in _VALID_CRITERION_TYPES:
            return _die(
                f"invalid criterion type {_ctype!r} for {_cid!r} "
                f"(one of {sorted(_VALID_CRITERION_TYPES)})"
            )
        criterion_types[_cid] = _ctype
    # the two shorthands map onto the registry (and keep their legacy audit lists).
    for _cid in a.observed_data_criteria:
        criterion_types[_cid] = "observed-data"
    for _cid in a.external_io_criteria:
        criterion_types[_cid] = "external-io"

    # The GATE: for every criterion set CONFIRMED in THIS call, its type's evidence
    # shape is required. external-io additionally needs an observed REAL call (R4).
    applied_ids = [kv.split("=", 1)[0] for kv in a.criterion]
    for cid in applied_ids:
        if crit.get(cid) != "CONFIRMED":
            continue
        ctype = criterion_types.get(cid)
        if ctype is None:
            continue  # untyped criterion — no evidence-shape gate
        if ctype == "external-io":
            # spec 402 R4: a stub/mock/fixture-only ref with no real-call marker is
            # refused — an external-I/O criterion needs ≥1 observed real call.
            ev = a.evidence or ""
            if _STUB_ONLY_RE.search(ev) and not _REAL_CALL_EVIDENCE_RE.search(ev):
                return _die(
                    f"REFUSED (spec 402 R4): external-I/O criterion {cid!r} needs "
                    f"≥1 observed real call — the --evidence looks stub/mock/"
                    f"fixture-only. Either record the real-call observation in "
                    f"--evidence (request-id / http status 2xx / actual response) "
                    f"or set `--criterion {cid}=integration-owed`. "
                    f"Got --evidence: {a.evidence!r}"
                )
        else:
            rx = _EVIDENCE_TYPE_RE.get(ctype)
            if rx is not None and not rx.search(a.evidence or ""):
                return _die(
                    f"REFUSED (spec 402 R2): criterion {cid!r} typed {ctype!r} "
                    f"cannot be CONFIRMED on this evidence — it must cite "
                    f"{_EVIDENCE_TYPE_SHAPE.get(ctype, ctype)}. "
                    f"Got --evidence: {a.evidence!r}"
                )

    # spec 282 R4 preserved — the legacy WHOLE-SPEC observed-data gate: a whole-spec
    # CONFIRMED without a LIVE-DB observation is refused (manifest/git-ancestry lags).
    if a.observed_data and spec_verdict == "CONFIRMED":
        if not _evidence_has_live_db_observation(a.evidence):
            return _die(
                "REFUSED (spec 282 R4): observed-data/migration-bearing whole-spec "
                f"CONFIRMED on {a.spec_id} cannot rest on manifest- or git-ancestry-"
                "only evidence. A data/migration verdict must cite a LIVE prod-DB "
                "observation (e.g. alembic_version + a table/row SELECT against "
                f"$SUPABASE_DB_URL). Got --evidence: {a.evidence!r}"
            )

    # persist the typed-evidence registry + audit lists (spec 402 R2/R4).
    if criterion_types:
        rec["criterion_types"] = dict(sorted(criterion_types.items()))
    if a.observed_data_criteria:
        rec["observed_data_criteria"] = sorted(
            set(rec.get("observed_data_criteria", [])) | set(a.observed_data_criteria)
        )
    if a.external_io_criteria:
        rec["external_io_criteria"] = sorted(
            set(rec.get("external_io_criteria", [])) | set(a.external_io_criteria)
        )

    # Item 1: do not write `verdict: null` when criteria are present but incomplete.
    if spec_verdict is None:
        rec.pop("verdict", None)  # clear any stale value from a prior run
    else:
        rec["verdict"] = spec_verdict
    rec["judge"] = a.judge
    rec["evidence_ref"] = a.evidence
    if a.contract_version is not None:
        rec["contract_version"] = a.contract_version  # F5 contract binding
    rec["at"] = now
    # history records the derived value; None means incomplete at this point in time
    rec.setdefault("history", []).append(
        {
            "at": now,
            "verdict": spec_verdict if spec_verdict is not None else "incomplete",
            "judge": a.judge,
        }
    )
    with _record_lock(path):  # advisory flock — same as register/set
        _write_verdict(path, rec)
    if spec_verdict is None:
        # spec 402 R1: name the omitted declared criteria that keep this incomplete.
        _miss = f" — missing declared criteria: {missing}" if missing else ""
        print(f"{a.spec_id} criteria updated (no verdict yet — incomplete){_miss}")
    else:
        print(f"{a.spec_id} verified -> {spec_verdict}")
    return 0


def cmd_waive(argv: list[str]) -> int:
    """Record an owner-waiver for a single REJECTED criterion (spec 168 R3b).

    The ONLY non-evidence path past a standing REJECTED criterion: an explicit
    human override that NAMES the criterion AND the human. Stored on the verdict
    record under `waivers: {<criterion>: {criterion, human, reason, at}}`. After a
    valid waiver, `derived_verdict` treats that criterion as cleared, so the spec
    can re-derive to CONFIRMED if every other observable criterion is green. A
    waiver lacking the criterion id or the human is refused — never a generic stamp.
    """
    ap = argparse.ArgumentParser(prog="spec_ledger.py waive")
    ap.add_argument("spec_id")
    ap.add_argument("--criterion", required=True, help="the criterion id being waived")
    ap.add_argument("--human", required=True, help="the named human who waived it")
    ap.add_argument("--reason", required=True, help="why the red criterion is accepted")
    a = ap.parse_args(argv)

    criterion = (a.criterion or "").strip()
    human = (a.human or "").strip()
    reason = (a.reason or "").strip()
    if not criterion:
        return _die("waive needs a non-empty --criterion (name the exact criterion)")
    if not human:
        return _die("waive needs a non-empty --human (name the human who waived it)")
    if not reason:
        return _die("waive needs a non-empty --reason")

    path = _verified_path(a.spec_id)
    if not path.exists():
        return _die(
            f"no verdict record for {a.spec_id} — a waiver only applies to an "
            f"existing verified/ record with criteria"
        )
    rec = _load_yaml(path)
    criteria = rec.get("criteria") or {}
    if criterion not in criteria:
        return _die(
            f"criterion {criterion!r} is not on {a.spec_id}'s criteria map "
            f"(have: {sorted(criteria)}) — a waiver must name a real criterion"
        )
    now = _now_iso()
    waiver = {
        "criterion": criterion,
        "human": human,
        "reason": reason,
        "at": now,
    }
    with _record_lock(path):
        rec = _load_yaml(path)
        rec.setdefault("waivers", {})[criterion] = waiver
        # Re-derive the spec-level verdict now that the waiver may clear the criterion.
        rec["verdict"] = resolve_spec_verdict(rec.get("criteria") or {}, rec["waivers"])
        if rec["verdict"] is None:
            rec.pop("verdict", None)
        rec["at"] = now
        rec.setdefault("history", []).append(
            {
                "at": now,
                "verdict": "owner-waiver",
                "judge": f"owner-waiver:{human}",
                "criterion": criterion,
            }
        )
        _write_verdict(path, rec)
    print(f"{a.spec_id} criterion {criterion} waived by {human}")
    return 0


def cmd_alert(argv: list[str]) -> int:
    """Time-based ops alert (kept OUT of --check, which must stay deterministic).

    Flags any spec that has been `awaiting-verify` longer than the floor — i.e.
    shipped but the verifier has produced no verdict. Exit 1 if any are stale.
    """
    ap = argparse.ArgumentParser(prog="spec_ledger.py alert")
    ap.add_argument("--floor-hours", type=int, default=AWAITING_PROD_FLOOR_HOURS)
    a = ap.parse_args(argv)

    verdicts = load_verdicts()
    stale: list[tuple[str, float]] = []
    for rec in load_records():
        if effective_status(rec, verdicts.get(rec.get("spec_id"))) != "awaiting-verify":
            continue
        hrs = _hours_since(_shipped_at(rec))
        if hrs is not None and hrs > a.floor_hours:
            stale.append((rec.get("spec_id", "?"), hrs))

    if not stale:
        print("alert OK — no spec stuck awaiting-verify.")
        return 0
    for sid, hrs in stale:
        print(f"STALE: {sid} — awaiting-verify for {round(hrs / 24, 1)}d (no verdict)")
    return 1


# -------------------------------------------------------- observable-criterion heuristic

_PRESENCE_RE = re.compile(
    r"\b(exists?|is (?:present|implemented|added|available)|"
    r"a [a-z-]+ (?:component|button|menu|card|field|page) (?:exists|is added))\b",
    re.IGNORECASE,
)
_ACTION_HINT = re.compile(
    r"\b(given|when|then|click|hover|type|select|navigat|renders?|displays?)\b",
    re.IGNORECASE,
)


def observable_warnings(records: list[dict]) -> list[str]:
    """Return soft warnings for presence-phrased (non-observable) acceptance criteria."""
    out: list[str] = []
    for rec in records:
        sid = rec.get("spec_id", rec.get("_file", "?"))
        for crit in rec.get("acceptance_criteria") or []:
            text = str(crit)
            if _PRESENCE_RE.search(text) and not _ACTION_HINT.search(text):
                out.append(
                    f"{sid}: non-observable criterion (presence-phrased): {text!r}"
                )
    return out


# ------------------------------------------------------------------------- main


# Builder-only ledger mutations. ROLE=rev (the verifier) may NEVER allocate a
# number or write a build status — its only legitimate write is `verify` (into the
# verified/ namespace). Enforcing 076 by a guard, not convention: a rev that called
# `set rework`/`next-num` once shipped an outage (076 → 2026-06-07). `verify`,
# render, `--check`, and `alert` stay open to rev (read-only / verified-namespace).
_REV_FORBIDDEN = {"next-num", "register", "set"}


def _role_guard(sub: str) -> None:
    if os.environ.get("ROLE") == "rev" and sub in _REV_FORBIDDEN:
        sys.stderr.write(
            f"REFUSED: ROLE=rev may not run `{sub}` — the verifier is read-only on the "
            f"build ledger (076). rev's only write is `verify` (verified/ namespace). "
            f"File a corrective-inbox entry; orc/think converts it to a rework row.\n"
        )
        raise SystemExit(3)


def cmd_lint(argv: list[str]) -> int:
    """spec 282 R3 — flag post-ship records whose held_reason still asserts a
    pre-ship state (the 269 pattern: status advanced but the note left behind says
    "pending operator" / "not re-deployed"). Exit 1 naming them; 0 if clean. Kept
    SEPARATE from the deploy-gating --check so a legacy stale record can't red-fail
    a deploy; cmd_set auto-supersedes the field on any new post-ship transition."""
    argparse.ArgumentParser(prog="spec_ledger.py lint").parse_args(argv)
    stale = stale_held_reason_records(load_records())
    if not stale:
        print("lint OK — no post-ship record carries a stale pre-ship held_reason.")
        return 0
    print(
        "LEDGER LINT FAILED — stale pre-ship held_reason (spec 282 R3):",
        file=sys.stderr,
    )
    for sid, hr in stale:
        print(f"  - {sid}: {hr[:160]}", file=sys.stderr)
    return 1


def main() -> int:
    argv = sys.argv[1:]
    if argv:
        _role_guard(argv[0])
    if argv and argv[0] == "next-num":
        return cmd_next_num(argv[1:])
    if argv and argv[0] == "register":
        return cmd_register(argv[1:])
    if argv and argv[0] == "set":
        return cmd_set(argv[1:])
    if argv and argv[0] == "verify":
        return cmd_verify(argv[1:])
    if argv and argv[0] == "waive":
        return cmd_waive(argv[1:])
    if argv and argv[0] == "alert":
        return cmd_alert(argv[1:])
    if argv and argv[0] == "lint":
        return cmd_lint(argv[1:])

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--render", action="store_true", help="render (default)")
    ap.add_argument(
        "--all", action="store_true", help="include terminal records in printout"
    )
    ap.add_argument(
        "--check", action="store_true", help="validate records; non-zero on error"
    )
    ap.add_argument(
        "--count-actionable",
        action="store_true",
        help=(
            "print a single integer: the number of ledger records whose status is "
            "in the actionable (non-terminal) set {registered, planned, building, rework}. "
            "Exits 0. Intended for machine consumers (e.g. orc-idle-watch.sh) that must "
            "never grep human render text."
        ),
    )
    args = ap.parse_args()

    records = load_records()

    errors = validate(records)

    if args.count_actionable:
        # Machine-readable count for the idle watchdog.  Print ONLY an integer —
        # nothing else — so the caller can set QUEUED=$(...) directly without grep.
        # Terminal/non-actionable states (accepted, superseded, held, bounced,
        # retired, unknown, merged, shipped, …) are excluded.
        # `held` is deliberately excluded: orc-paused on a real blocker; a nudge
        # would be noise — spec 226 open-question resolution.
        ACTIONABLE = {"registered", "planned", "building", "rework"}
        count = sum(1 for r in records if r.get("status") in ACTIONABLE)
        print(count)
        return 0

    if args.check:
        if errors:
            print("LEDGER CHECK FAILED:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        for w in observable_warnings(records):
            print(f"  ~ {w}", file=sys.stderr)
        print(f"Ledger OK — {len(records)} record(s).")
        return 0

    # render mode (default)
    if errors:
        # surface but don't block a render — the view should still show what it can
        print("WARNING — ledger has validation errors (run --check):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)

    body = render(records, include_all=args.all)
    _MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    OUTSTANDING_MD.write_text(body)
    sys.stdout.write(body)
    print(f"\n[wrote {OUTSTANDING_MD}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
