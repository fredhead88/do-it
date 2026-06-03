#!/usr/bin/env python3
"""Spec build-status ledger — render + validate.

The DO-IT pipeline's durable answer to "did we write any specs that never got
built?". Reads one small fact-file per spec under docs/superpowers/ledger/ plus
shared blocker records under docs/superpowers/ledger/blockers/, and produces a
grouped, read-only view (OUTSTANDING.md). The view is GENERATED — never
hand-edited — so it cannot drift from the per-spec facts.

State lives in the per-spec files (consistent with DO-IT's "state is where the
file sits"); this script only reads them and renders. The only file it writes is
the generated OUTSTANDING.md.

Usage:
    python scripts/spec_ledger.py            # render to stdout + write OUTSTANDING.md
    python scripts/spec_ledger.py --render   # (same as default)
    python scripts/spec_ledger.py --all      # include accepted/superseded in the printout
    python scripts/spec_ledger.py --check     # validate records; exit non-zero on any violation
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# LEDGER_DIR defaults to <repo>/docs/superpowers/ledger (the DO-IT CONFIG default);
# override with the DOIT_LEDGER_DIR env var to point at any project's ledger.
LEDGER_DIR = Path(
    os.environ.get("DOIT_LEDGER_DIR", REPO_ROOT / "docs" / "superpowers" / "ledger")
)
BLOCKERS_DIR = LEDGER_DIR / "blockers"
OUTSTANDING_MD = LEDGER_DIR / "OUTSTANDING.md"

VALID_STATUS = {
    "registered",
    "planned",
    "building",
    "merged",
    "shipped",
    "accepted",
    "held",
    "superseded",
    "unknown",
}

# stored status -> read bucket
OUTSTANDING_STATUSES = {
    "registered",
    "planned",
    "building",
    "merged",
    "held",
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


def load_blockers() -> dict[str, dict]:
    blockers = {}
    if not BLOCKERS_DIR.exists():
        return blockers
    for path in sorted(BLOCKERS_DIR.glob("*.yml")):
        b = _load_yaml(path)
        b["_file"] = path.name
        blockers[b.get("id") or path.stem] = b
    return blockers


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


def validate(records: list[dict], blockers: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    for rec in records:
        sid = rec.get("spec_id", rec.get("_file", "?"))
        status = rec.get("status")
        if status not in VALID_STATUS:
            errors.append(f"{sid}: invalid status {status!r}")
        if status == "held" and not (rec.get("held_reason") or "").strip():
            errors.append(f"{sid}: status=held requires a non-empty held_reason")
        if status == "superseded" and not (rec.get("superseded_by") or "").strip():
            errors.append(f"{sid}: status=superseded requires superseded_by")
        blk = rec.get("deploy_blocked_by")
        if blk:
            b = blockers.get(blk)
            if b is None:
                errors.append(
                    f"{sid}: deploy_blocked_by={blk!r} points at a missing blocker"
                )
            elif b.get("status") == "resolved":
                errors.append(
                    f"{sid}: deploy_blocked_by={blk!r} points at an already-resolved blocker"
                )
    return errors


# ----------------------------------------------------------------------- render


def _line(rec: dict) -> str:
    sid = rec.get("spec_id", "?")
    title = rec.get("title", "")
    return f"{sid} — {title}".rstrip(" —")


def render(records: list[dict], blockers: dict[str, dict], include_all: bool) -> str:
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
    L.append("     files in docs/superpowers/ledger/ and re-run the script. -->")
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

    # --- open blockers + their merged-undeployed rollup ---
    open_blockers = {
        bid: b for bid, b in blockers.items() if b.get("status") != "resolved"
    }
    if open_blockers:
        L.append("## 🚧 Deploy blockers")
        for bid, b in open_blockers.items():
            blocked = [r for r in outstanding if r.get("deploy_blocked_by") == bid]
            since = b.get("opened_at", "?")
            L.append(
                f"### [{bid}] {b.get('summary', '')}  — open since {since} "
                f"(scope: {b.get('scope', '?')})"
            )
            if blocked:
                L.append(
                    f"_{len(blocked)} spec(s) merged-undeployed, all blocked on this:_"
                )
                for r in blocked:
                    L.append(f"- ⛔ {_line(r)} — code merged, **NOT LIVE**")
            else:
                L.append("_No specs currently pointing at this blocker._")
            L.append("")

    # --- outstanding ---
    L.append(f"## Outstanding ({len(outstanding)})")
    if not outstanding:
        L.append("_None._")
    for r in outstanding:
        status = r.get("status")
        if status == "held":
            L.append(
                f"- ⏸ {_line(r)} — **HELD**: {r.get('held_reason', '(no reason!)')}"
            )
        elif status == "merged":
            blk = r.get("deploy_blocked_by")
            if blk:
                L.append(
                    f"- ⛔ {_line(r)} — code merged, **NOT LIVE** (blocked: {blk})"
                )
            else:
                hrs = _hours_since(_merged_at(r))
                if hrs is not None and hrs > STALE_MERGED_HOURS:
                    days = round(hrs / 24, 1)
                    L.append(
                        f"- ⚠ {_line(r)} — merged {days}d, **no blocker — "
                        f"why isn't this live?**"
                    )
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


# ------------------------------------------------------------------------- main


def main() -> int:
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
    blockers = load_blockers()

    errors = validate(records, blockers)

    if args.check:
        if errors:
            print("LEDGER CHECK FAILED:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print(f"Ledger OK — {len(records)} record(s), {len(blockers)} blocker(s).")
        return 0

    # render mode (default)
    if errors:
        # surface but don't block a render — the view should still show what it can
        print("WARNING — ledger has validation errors (run --check):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)

    body = render(records, blockers, include_all=args.all)
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    OUTSTANDING_MD.write_text(body)
    sys.stdout.write(body)
    print(f"\n[wrote {OUTSTANDING_MD.relative_to(REPO_ROOT)}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
