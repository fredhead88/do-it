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

    # write a record (so nobody hand-edits YAML — the source of indentation /
    # missing-field bugs). Both refuse to write anything that wouldn't pass --check:
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
import os
import re
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


# Verifier-owned namespace — the builder's glob("*.yml") never touches this subdir.
# VERIFIED_DIR is derived from LEDGER_DIR so tests can override it via DOIT_LEDGER_DIR.
def _get_verified_dir() -> Path:
    return LEDGER_DIR / "verified"


VALID_VERDICT = {"CONFIRMED", "REJECTED"}


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


VALID_STATUS = {
    "registered",
    "planned",
    "building",
    "merged",
    "shipped",
    "accepted",
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
    "merged",
    "held",
    "bounced",
    "rework",
    "unknown",
}
STALE_MERGED_HOURS = 24


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


def _hours_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


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


# ----------------------------------------------------------------------- render


def _line(rec: dict) -> str:
    sid = rec.get("spec_id", "?")
    title = rec.get("title", "")
    return f"{sid} — {title}".rstrip(" —")


def render(records: list[dict], include_all: bool) -> str:
    verdicts = load_verdicts()
    needs_human = [
        r for r in records if r.get("needs_human") or r.get("status") == "unknown"
    ]
    outstanding = [
        r
        for r in records
        if r.get("status") in OUTSTANDING_STATUSES
        and not (r.get("needs_human") or r.get("status") == "unknown")
    ]
    awaiting = [r for r in records if r.get("status") == "shipped"]
    accepted = [r for r in records if r.get("status") == "accepted"]
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

    # --- could-not-classify first (backfill ambiguity) ---
    if needs_human:
        L.append(f"## ⚠ {len(needs_human)} spec(s) I couldn't classify — eyeball these")
        for r in needs_human:
            note = r.get("note") or r.get("needs_human_reason") or ""
            L.append(f"- {_line(r)}" + (f"  · {note}" if note else ""))
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

    # --- shipped, awaiting review ---
    L.append(f"## Shipped — awaiting your review ({len(awaiting)})")
    if not awaiting:
        L.append("_None._")
    for r in awaiting:
        sid = r.get("spec_id", "?")
        v = verdicts.get(sid)
        card = r.get("review_card")
        suffix = f"  · card: {card}" if card else ""
        if v and v.get("verdict") == "CONFIRMED":
            L.append(
                f"- ✅ {_line(r)} — **verified** (judge: {v.get('judge')}){suffix}"
            )
        elif v and v.get("verdict") == "REJECTED":
            L.append(
                f"- ❌ {_line(r)} — **REJECTED** (judge: {v.get('judge')}){suffix}"
            )
        else:
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
    now = _now_iso()
    sb = a.source_brief
    if sb is not None:
        try:
            sb = int(sb)
        except ValueError:
            pass
    rec = {
        "spec_id": a.spec_id,
        "title": a.title,
        "intent": a.intent,
        "status": "registered",
        "handed_over_at": now,
        "spec_file": a.spec_file,
        "source_brief": sb,
        "history": [{"at": now, "status": "registered", "by": a.by}],
    }
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
    for kv in a.field:
        if "=" not in kv:
            return _die(f"--field must be KEY=VALUE, got {kv!r}")
        k, v = kv.split("=", 1)
        rec[k] = v
    new_entry = {"at": _now_iso(), "status": a.status, "by": a.by}
    rec.setdefault("history", []).append(new_entry)

    errs = validate([rec])
    if errs:  # e.g. rework with no --reason, superseded with no --superseded-by
        return _die("refusing to write — " + "; ".join(errs))

    # Collect the scalar field updates (excluding history — merged separately).
    updates = {k: v for k, v in rec.items() if k != "history"}
    with _record_lock(path):
        # Re-read inside the lock so concurrent writers don't lose each other's
        # history entries (both may have read the pre-lock snapshot).
        if path.exists():
            rec = _load_yaml(path)
            rec.update(updates)
            rec.setdefault("history", []).append(new_entry)
        _write_record(path, rec)
    print(f"{a.spec_id} → {a.status}")
    return 0


# ----------------------------------------------------------------- verify (verifier-owned namespace)


def _verified_path(spec_id: str) -> Path:
    return _get_verified_dir() / f"{spec_id}.yml"


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
    ap.add_argument("verdict")
    ap.add_argument("--judge", required=True)  # codex | claude-fallback
    ap.add_argument(
        "--evidence", required=True
    )  # path/ref to the typed evidence artifact
    ap.add_argument(
        "--criterion",
        action="append",
        default=[],
        metavar="ID=VERDICT",
        help="optional per-criterion verdicts",
    )
    a = ap.parse_args(argv)
    if a.verdict not in VALID_VERDICT:
        return _die(f"invalid verdict {a.verdict!r} (one of {sorted(VALID_VERDICT)})")
    now = _now_iso()
    path = _verified_path(a.spec_id)
    rec = _load_yaml(path) if path.exists() else {"spec_id": a.spec_id, "history": []}
    rec["verdict"] = a.verdict
    rec["judge"] = a.judge
    rec["evidence_ref"] = a.evidence
    rec["at"] = now
    if a.criterion:
        crit = rec.setdefault("criteria", {})
        for kv in a.criterion:
            if "=" not in kv:
                return _die(f"--criterion must be ID=VERDICT, got {kv!r}")
            k, v = kv.split("=", 1)
            crit[k] = v
    rec.setdefault("history", []).append(
        {"at": now, "verdict": a.verdict, "judge": a.judge}
    )
    with _record_lock(path):  # advisory flock — same as register/set
        _write_verdict(path, rec)
    print(f"{a.spec_id} verified -> {a.verdict}")
    return 0


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


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "register":
        return cmd_register(argv[1:])
    if argv and argv[0] == "set":
        return cmd_set(argv[1:])
    if argv and argv[0] == "verify":
        return cmd_verify(argv[1:])

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--render", action="store_true", help="render (default)")
    ap.add_argument(
        "--all", action="store_true", help="include terminal records in printout"
    )
    ap.add_argument(
        "--check", action="store_true", help="validate records; non-zero on error"
    )
    args = ap.parse_args()

    records = load_records()

    errors = validate(records)

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
