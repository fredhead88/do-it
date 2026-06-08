# Review Loop v2 — Plan 1 (Ledger Substrate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the closure verdict *derived* in `spec_ledger.py` — a spec's terminal state is computed by joining the build ledger with the verifier's per-criterion verdicts, never hand-written — so "build says done / prod says hollow" becomes un-representable.

**Architecture:** Two pure functions (`resolve_spec_verdict`, `effective_status`) are the core; `cmd_verify` derives the spec-level verdict from a per-criterion map instead of trusting a caller; `render` computes effective status per record and surfaces a top `NEEDS-REWORK` section + an `awaiting-prod` bucket; `cmd_set` refuses to hand-write `accepted`; a new `alert` subcommand carries the time-based 48h floor so `--check` stays time-invariant for CI.

**Tech Stack:** Python 3.14, PyYAML, pytest. All changes are in `scripts/spec_ledger.py` + `tests/`. No browser, no network, no prod — fully unit-testable.

**Scope note:** This is Plan 1 of 3 for v3.4.0 (design: `docs/2026-06-08-review-loop-prod-verdict-design.md`). Plan 2 = the executable Playwright assertion engine + card-schema parsing (where the A1 live proof lands). Plan 3 = ops crons + the `rev` session + `rev-watch/`. Plan 1 produces working, fully-tested software on its own and is the foundation the other two build on. **Do not bump the version or CHANGELOG here** — that happens when Plan 3 lands the full release.

---

## File Structure

- **Modify** `scripts/spec_ledger.py`:
  - Add `VALID_CRITERION_VERDICT` + `resolve_spec_verdict()` near `VALID_VERDICT` (line 90).
  - Add `effective_status()` + `_shipped_at()` in the helpers block (near `_merged_at`, line 228).
  - Rewrite `cmd_verify()` (line 647) to derive the spec verdict from the criteria map.
  - Add an `accepted` refusal in `cmd_set()` (line 558).
  - Rewrite `render()` (line 272) to compute effective status and bucket by it.
  - Add `cmd_alert()` + wire `alert` into `main()` (line 718).
- **Create** `tests/test_review_loop_v2.py` — all Plan 1 tests.

---

## Task 1: Aggregation function `resolve_spec_verdict`

**Files:**
- Modify: `scripts/spec_ledger.py` (after line 90, the `VALID_VERDICT` definition)
- Test: `tests/test_review_loop_v2.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_review_loop_v2.py`:

```python
"""Tests for Review Loop v2 — Plan 1 (derived ledger verdict)."""

import importlib.util
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    spec = importlib.util.spec_from_file_location("spec_ledger_rlv2", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_spec_verdict(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    assert sl.resolve_spec_verdict({}) is None
    assert sl.resolve_spec_verdict({"c1": "CONFIRMED"}) == "CONFIRMED"
    assert sl.resolve_spec_verdict({"c1": "CONFIRMED", "c2": "CONFIRMED"}) == "CONFIRMED"
    assert sl.resolve_spec_verdict({"c1": "CONFIRMED", "c2": "REJECTED"}) == "REJECTED"
    # not-applicable is excluded from the all-pass test
    assert sl.resolve_spec_verdict({"c1": "CONFIRMED", "c2": "not-applicable"}) == "CONFIRMED"
    # a spec of only not-applicable has nothing observable -> incomplete
    assert sl.resolve_spec_verdict({"c1": "not-applicable"}) is None
    # not-run is incomplete, not a pass
    assert sl.resolve_spec_verdict({"c1": "CONFIRMED", "c2": "not-run"}) is None
    # a single REJECTED dominates everything
    assert sl.resolve_spec_verdict({"c1": "not-run", "c2": "REJECTED"}) == "REJECTED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py::test_resolve_spec_verdict -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'resolve_spec_verdict'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/spec_ledger.py`, immediately after `VALID_VERDICT = {"CONFIRMED", "REJECTED"}` (line 90):

```python
# Per-criterion verdicts (richer than the spec-level CONFIRMED/REJECTED). The
# verification loop's HOLLOW/MISSING/REGRESSION all map to REJECTED before they
# reach here; `not-applicable` marks a criterion that is legitimately unobservable
# (no test-tenant data) so one data-gap can't freeze a spec forever; `not-run`
# means not-yet-observed (incomplete).
VALID_CRITERION_VERDICT = {"CONFIRMED", "REJECTED", "not-applicable", "not-run"}


def resolve_spec_verdict(criteria: dict) -> str | None:
    """Aggregate a per-criterion verdict map into a spec-level verdict.

    REJECTED if any criterion is REJECTED; CONFIRMED iff there is >=1 observable
    criterion and every observable (non-`not-applicable`) one is CONFIRMED;
    otherwise None (incomplete -> renders as `awaiting-prod`).
    """
    if not criteria:
        return None
    vals = list(criteria.values())
    if any(v == "REJECTED" for v in vals):
        return "REJECTED"
    observable = [v for v in vals if v != "not-applicable"]
    if observable and all(v == "CONFIRMED" for v in observable):
        return "CONFIRMED"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py::test_resolve_spec_verdict -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add scripts/spec_ledger.py tests/test_review_loop_v2.py
git commit -m "feat(ledger): resolve_spec_verdict aggregation for per-criterion verdicts"
```

---

## Task 2: `effective_status` (the derived join)

**Files:**
- Modify: `scripts/spec_ledger.py` (after `_hours_since`, line 239)
- Test: `tests/test_review_loop_v2.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_loop_v2.py`:

```python
def test_effective_status(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    # pre-shipped lifecycle is unchanged
    assert sl.effective_status({"status": "building"}, None) == "building"
    # shipped + no verdict -> awaiting-prod
    assert sl.effective_status({"status": "shipped"}, None) == "awaiting-prod"
    # shipped + CONFIRMED -> accepted (derived, never stored)
    assert sl.effective_status({"status": "shipped"}, {"verdict": "CONFIRMED"}) == "accepted"
    # shipped + REJECTED -> needs-rework
    assert sl.effective_status({"status": "shipped"}, {"verdict": "REJECTED"}) == "needs-rework"
    # shipped + open needs_human (no verdict) -> needs-human
    assert sl.effective_status({"status": "shipped"}, {"needs_human": "taste"}) == "needs-human"
    # legacy records already stored as accepted still read as accepted
    assert sl.effective_status({"status": "accepted"}, None) == "accepted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py::test_effective_status -v`
Expected: FAIL — `AttributeError: ... 'effective_status'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/spec_ledger.py`, after `_hours_since` (line 239):

```python
def effective_status(rec: dict, verdict: dict | None) -> str:
    """The closure status, COMPUTED from the build record + the verifier verdict.

    `accepted` is never stored — it is derived here from `shipped ∧ CONFIRMED`,
    so the build ledger and the verifier verdict can never disagree. Pre-shipped
    records (and legacy records already stored as `accepted`) return their own
    stored status unchanged.
    """
    status = rec.get("status")
    if status != "shipped":
        return status  # pre-shipped lifecycle, and legacy stored `accepted`
    v = (verdict or {}).get("verdict")
    if v == "CONFIRMED":
        return "accepted"
    if v == "REJECTED":
        return "needs-rework"
    if (verdict or {}).get("needs_human"):
        return "needs-human"
    return "awaiting-prod"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py::test_effective_status -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add scripts/spec_ledger.py tests/test_review_loop_v2.py
git commit -m "feat(ledger): effective_status derives accepted from shipped + CONFIRMED"
```

---

## Task 3: `cmd_verify` derives the spec verdict from the criteria map

**Files:**
- Modify: `scripts/spec_ledger.py` — `cmd_verify` (line 647)
- Test: `tests/test_review_loop_v2.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_loop_v2.py`:

```python
def test_cmd_verify_derives_from_criteria(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify(
        ["100-x", "--judge", "codex", "--evidence", "e.json",
         "--criterion", "c1=CONFIRMED", "--criterion", "c2=REJECTED"]
    )
    capsys.readouterr()
    assert rc == 0
    rec = yaml.safe_load((sl._verified_path("100-x")).read_text())
    assert rec["criteria"] == {"c1": "CONFIRMED", "c2": "REJECTED"}
    assert rec["verdict"] == "REJECTED"  # derived, not supplied


def test_cmd_verify_all_confirmed_is_confirmed(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    sl.cmd_verify(["100-x", "--judge", "codex", "--evidence", "e",
                   "--criterion", "c1=CONFIRMED"])
    sl.cmd_verify(["100-x", "--judge", "codex", "--evidence", "e",
                   "--criterion", "c2=CONFIRMED"])
    capsys.readouterr()
    rec = yaml.safe_load((sl._verified_path("100-x")).read_text())
    assert rec["criteria"] == {"c1": "CONFIRMED", "c2": "CONFIRMED"}
    assert rec["verdict"] == "CONFIRMED"


def test_cmd_verify_rejects_bad_criterion_verdict(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify(["100-x", "--judge", "c", "--evidence", "e",
                        "--criterion", "c1=MAYBE"])
    err = capsys.readouterr().err
    assert rc == 1 and "MAYBE" in err


def test_cmd_verify_refuses_disagreeing_positional(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify(["100-x", "CONFIRMED", "--judge", "c", "--evidence", "e",
                        "--criterion", "c1=REJECTED"])
    err = capsys.readouterr().err
    assert rc == 1 and "refus" in err.lower()


def test_cmd_verify_legacy_positional_still_works(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_verify(["100-x", "CONFIRMED", "--judge", "c", "--evidence", "e"])
    capsys.readouterr()
    assert rc == 0
    rec = yaml.safe_load((sl._verified_path("100-x")).read_text())
    assert rec["verdict"] == "CONFIRMED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py -k cmd_verify -v`
Expected: FAIL — `test_cmd_verify_derives_from_criteria` asserts `verdict == "REJECTED"` but the current code writes the positional `verdict` (which is now required and absent → argparse SystemExit), and bad-criterion-verdict isn't validated.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `cmd_verify` (line 647) with:

```python
def cmd_verify(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="spec_ledger.py verify")
    ap.add_argument("spec_id")
    ap.add_argument("verdict", nargs="?")  # optional: derived from --criterion when given
    ap.add_argument("--judge", required=True)  # codex | claude-fallback
    ap.add_argument("--evidence", required=True)  # ref to the typed evidence artifact
    ap.add_argument(
        "--criterion",
        action="append",
        default=[],
        metavar="ID=VERDICT",
        help="per-criterion verdict; spec-level verdict is DERIVED from the full map",
    )
    a = ap.parse_args(argv)

    now = _now_iso()
    path = _verified_path(a.spec_id)
    rec = _load_yaml(path) if path.exists() else {"spec_id": a.spec_id, "history": []}

    if a.criterion:
        crit = rec.setdefault("criteria", {})
        for kv in a.criterion:
            if "=" not in kv:
                return _die(f"--criterion must be ID=VERDICT, got {kv!r}")
            k, v = kv.split("=", 1)
            if v not in VALID_CRITERION_VERDICT:
                return _die(
                    f"invalid criterion verdict {v!r} for {k!r} "
                    f"(one of {sorted(VALID_CRITERION_VERDICT)})"
                )
            crit[k] = v
        spec_verdict = resolve_spec_verdict(crit)
        # The verifier may pass a spec-level verdict only if it agrees with the
        # derived one — we refuse a caller-supplied verdict that overrides the map.
        if a.verdict is not None and a.verdict != spec_verdict:
            return _die(
                f"refusing caller-supplied spec verdict {a.verdict!r}: the criteria "
                f"map derives {spec_verdict!r}"
            )
    else:
        # Legacy path: explicit spec-level verdict, no per-criterion map.
        if a.verdict is None:
            return _die("verify needs either a verdict or one or more --criterion")
        if a.verdict not in VALID_VERDICT:
            return _die(
                f"invalid verdict {a.verdict!r} (one of {sorted(VALID_VERDICT)})"
            )
        spec_verdict = a.verdict

    rec["verdict"] = spec_verdict
    rec["judge"] = a.judge
    rec["evidence_ref"] = a.evidence
    rec["at"] = now
    rec.setdefault("history", []).append(
        {"at": now, "verdict": spec_verdict, "judge": a.judge}
    )
    with _record_lock(path):  # advisory flock — same as register/set
        _write_verdict(path, rec)
    print(f"{a.spec_id} verified -> {spec_verdict}")
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py -k cmd_verify -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the existing ledger suite to confirm no regression**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_spec_ledger.py -v`
Expected: PASS (the legacy positional path is preserved)

- [ ] **Step 6: Commit**

```bash
cd /home/albert/do-it
git add scripts/spec_ledger.py tests/test_review_loop_v2.py
git commit -m "feat(ledger): cmd_verify derives spec verdict from criteria map, refuses override"
```

---

## Task 4: `cmd_set` refuses to hand-write `accepted`

**Files:**
- Modify: `scripts/spec_ledger.py` — `cmd_set` (line 558, just after the `VALID_STATUS` check at line 575)
- Test: `tests/test_review_loop_v2.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_loop_v2.py`:

```python
def test_cmd_set_refuses_accepted(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    sl.cmd_register(["100-x", "--title", "T", "--intent", "I", "--spec-file", "f.md"])
    capsys.readouterr()
    rc = sl.cmd_set(["100-x", "accepted", "--by", "orc"])
    err = capsys.readouterr().err
    assert rc == 1 and "computed-only" in err


def test_cmd_set_other_status_still_works(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    sl.cmd_register(["100-x", "--title", "T", "--intent", "I", "--spec-file", "f.md"])
    capsys.readouterr()
    rc = sl.cmd_set(["100-x", "planned", "--by", "orc"])
    capsys.readouterr()
    assert rc == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py -k cmd_set -v`
Expected: FAIL — `test_cmd_set_refuses_accepted` (cmd_set currently accepts `accepted` since it's in `VALID_STATUS`).

- [ ] **Step 3: Write minimal implementation**

In `cmd_set`, immediately after the existing `VALID_STATUS` check (lines 575-576):

```python
    if a.status not in VALID_STATUS:
        return _die(f"invalid status {a.status!r} (one of {sorted(VALID_STATUS)})")
    if a.status == "accepted":
        return _die(
            "accepted is computed-only — it is derived from shipped ∧ a CONFIRMED "
            "prod verdict (see effective_status); do not set it by hand"
        )
```

(`accepted` stays in `VALID_STATUS` so legacy records already stored as `accepted` still pass `validate()`/`--check`; only *setting* it by hand is refused.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py -k cmd_set -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add scripts/spec_ledger.py tests/test_review_loop_v2.py
git commit -m "feat(ledger): cmd_set refuses hand-written accepted (computed-only)"
```

---

## Task 5: `render` computes effective status — top `NEEDS-REWORK`, `awaiting-prod`, derived `accepted`

**Files:**
- Modify: `scripts/spec_ledger.py` — `render` (line 272)
- Test: `tests/test_review_loop_v2.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_loop_v2.py`:

```python
def _ship(sl, sid, title="X"):
    sl._write_record(
        sl._record_path(sid),
        {"spec_id": sid, "title": title, "status": "shipped",
         "history": [{"at": "2026-06-08T00:00:00Z", "status": "shipped", "by": "orc"}]},
    )


def test_render_rejected_goes_to_top_needs_rework(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _ship(sl, "100-x", "Broken thing")
    sl._write_verdict(sl._verified_path("100-x"),
                      {"spec_id": "100-x", "verdict": "REJECTED", "judge": "codex"})
    body = sl.render(sl.load_records(), include_all=False)
    assert "NEEDS-REWORK" in body
    top = body.split("NEEDS-REWORK")[1].split("##")[0]
    assert "100-x" in top  # listed under the NEEDS-REWORK section


def test_render_confirmed_is_accepted_not_awaiting(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _ship(sl, "100-x")
    sl._write_verdict(sl._verified_path("100-x"),
                      {"spec_id": "100-x", "verdict": "CONFIRMED", "judge": "codex"})
    body = sl.render(sl.load_records(), include_all=True)
    assert "Accepted (1)" in body
    # not sitting in the awaiting-prod bucket
    awaiting = body.split("Awaiting prod-verification")[1].split("##")[0]
    assert "100-x" not in awaiting


def test_render_no_verdict_is_awaiting_prod(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    _ship(sl, "100-x")
    body = sl.render(sl.load_records(), include_all=False)
    assert "Awaiting prod-verification (1)" in body
    assert "NEEDS-REWORK" not in body  # nothing rejected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py -k render -v`
Expected: FAIL — current `render` has no `NEEDS-REWORK` section, labels the bucket "Shipped — awaiting your review", and computes `accepted` from stored status.

- [ ] **Step 3: Write minimal implementation**

Replace the bucket computation at the top of `render` (lines 273-285) with:

```python
    verdicts = load_verdicts()
    eff = {
        r.get("spec_id", r.get("_file", "?")): effective_status(r, verdicts.get(r.get("spec_id")))
        for r in records
    }

    def _eff(r):
        return eff[r.get("spec_id", r.get("_file", "?"))]

    needs_human = [
        r for r in records
        if r.get("needs_human") or r.get("status") == "unknown" or _eff(r) == "needs-human"
    ]
    needs_rework = [r for r in records if _eff(r) == "needs-rework"]
    outstanding = [
        r for r in records
        if r.get("status") in OUTSTANDING_STATUSES
        and not (r.get("needs_human") or r.get("status") == "unknown")
    ]
    awaiting_prod = [r for r in records if _eff(r) == "awaiting-prod"]
    accepted = [r for r in records if _eff(r) == "accepted"]
    superseded = [r for r in records if r.get("status") == "superseded"]
```

Then, immediately after the header lines append the loud NEEDS-REWORK section (insert before the `# --- could-not-classify first` comment at line 301):

```python
    # --- prod-verified hollow: the loudest thing on the board ---
    if needs_rework:
        L.append(f"## ❌ NEEDS-REWORK — prod-verified hollow ({len(needs_rework)})")
        for r in needs_rework:
            v = verdicts.get(r.get("spec_id")) or {}
            L.append(f"- ❌ {_line(r)} — **REJECTED** (judge: {v.get('judge', '?')})")
        L.append("")
```

Replace the "shipped, awaiting review" block (lines 339-358) with the awaiting-prod bucket:

```python
    # --- shipped, awaiting prod-verification ---
    L.append(f"## Awaiting prod-verification ({len(awaiting_prod)})")
    if not awaiting_prod:
        L.append("_None._")
    for r in awaiting_prod:
        card = r.get("review_card")
        suffix = f"  · card: {card}" if card else ""
        L.append(f"- 🚀 {_line(r)}{suffix}")
    L.append("")
```

The Accepted/Superseded block (lines 360-372) is unchanged — it already reads the `accepted`/`superseded` lists, which now come from the derived computation.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py -k render -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `cd /home/albert/do-it && python3 -m pytest tests/ -v`
Expected: PASS (the `test_spec_ledger.py` render tests still pass — `bounced`/`rework` render under Outstanding, unchanged).

- [ ] **Step 6: Commit**

```bash
cd /home/albert/do-it
git add scripts/spec_ledger.py tests/test_review_loop_v2.py
git commit -m "feat(ledger): render computes effective status — NEEDS-REWORK top + awaiting-prod"
```

---

## Task 6: `alert` subcommand for the 48h `awaiting-prod` floor (keep `--check` time-invariant)

**Files:**
- Modify: `scripts/spec_ledger.py` — add `_shipped_at()` (near `_merged_at`, line 233), `cmd_alert()` (near `cmd_verify`), and an `alert` branch in `main()` (line 720)
- Test: `tests/test_review_loop_v2.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_loop_v2.py`:

```python
def test_alert_flags_stale_awaiting_prod(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    sl._write_record(
        sl._record_path("100-x"),
        {"spec_id": "100-x", "title": "Old ship", "status": "shipped",
         "history": [{"at": "2020-01-01T00:00:00Z", "status": "shipped", "by": "orc"}]},
    )
    rc = sl.cmd_alert([])
    out = capsys.readouterr().out
    assert rc == 1 and "100-x" in out and "awaiting-prod" in out


def test_alert_silent_when_fresh(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    # Shipped just now -> well under the 48h floor.
    sl._write_record(
        sl._record_path("100-x"),
        {"spec_id": "100-x", "title": "Fresh", "status": "shipped",
         "history": [{"at": sl._now_iso(), "status": "shipped", "by": "orc"}]},
    )
    rc = sl.cmd_alert([])
    assert rc == 0


def test_check_is_time_invariant(monkeypatch, tmp_path):
    # A spec stale for years must NOT make validate()/--check fail.
    sl = _load(monkeypatch, tmp_path)
    sl._write_record(
        sl._record_path("100-x"),
        {"spec_id": "100-x", "title": "Old", "status": "shipped",
         "history": [{"at": "2020-01-01T00:00:00Z", "status": "shipped", "by": "orc"}]},
    )
    assert sl.validate(sl.load_records()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py -k "alert or time_invariant" -v`
Expected: FAIL — `cmd_alert` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add `_shipped_at` after `_merged_at` (line 233):

```python
def _shipped_at(rec: dict) -> datetime | None:
    """When did this record reach `shipped`? From history, else None."""
    for entry in reversed(rec.get("history") or []):
        if isinstance(entry, dict) and entry.get("status") == "shipped":
            return _parse_ts(entry.get("at"))
    return None
```

Add `AWAITING_PROD_FLOOR_HOURS = 48` next to `STALE_MERGED_HOURS` (line 180):

```python
STALE_MERGED_HOURS = 24
AWAITING_PROD_FLOOR_HOURS = 48
```

Add `cmd_alert` after `cmd_verify`:

```python
def cmd_alert(argv: list[str]) -> int:
    """Time-based ops alert (kept OUT of --check, which must stay deterministic).

    Flags any spec that has been `awaiting-prod` longer than the floor — i.e.
    shipped but the verifier has produced no verdict. Exit 1 if any are stale.
    """
    ap = argparse.ArgumentParser(prog="spec_ledger.py alert")
    ap.add_argument("--floor-hours", type=int, default=AWAITING_PROD_FLOOR_HOURS)
    a = ap.parse_args(argv)

    verdicts = load_verdicts()
    stale: list[tuple[str, float]] = []
    for rec in load_records():
        if effective_status(rec, verdicts.get(rec.get("spec_id"))) != "awaiting-prod":
            continue
        hrs = _hours_since(_shipped_at(rec))
        if hrs is not None and hrs > a.floor_hours:
            stale.append((rec.get("spec_id", "?"), hrs))

    if not stale:
        print("alert OK — no spec stuck awaiting-prod.")
        return 0
    for sid, hrs in stale:
        print(f"STALE: {sid} — awaiting-prod for {round(hrs / 24, 1)}d (no verdict)")
    return 1
```

Wire it into `main()`, after the `verify` branch (line 727):

```python
    if argv and argv[0] == "verify":
        return cmd_verify(argv[1:])
    if argv and argv[0] == "alert":
        return cmd_alert(argv[1:])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_review_loop_v2.py -k "alert or time_invariant" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add scripts/spec_ledger.py tests/test_review_loop_v2.py
git commit -m "feat(ledger): alert subcommand for 48h awaiting-prod floor (check stays time-invariant)"
```

---

## Task 7: Full-suite green + lint + final commit

**Files:** none (verification task)

- [ ] **Step 1: Run the complete test suite**

Run: `cd /home/albert/do-it && python3 -m pytest tests/ -q`
Expected: PASS — all of `test_spec_ledger.py`, `test_next_num.py`, `test_number_allocation.py`, `test_review_loop_v2.py`.

- [ ] **Step 2: Lint + format check**

Run: `cd /home/albert/do-it && ruff check scripts/spec_ledger.py tests/test_review_loop_v2.py && ruff format --check scripts/spec_ledger.py tests/test_review_loop_v2.py`
Expected: "All checks passed!" and "N files already formatted". If format reports changes, run `ruff format scripts/spec_ledger.py tests/test_review_loop_v2.py` and re-run the check.

- [ ] **Step 3: Smoke-test the CLI end-to-end**

Run:

```bash
cd /home/albert/do-it
export DOIT_LEDGER_DIR=/tmp/rlv2/ledger DOIT_MIRROR_DIR=/tmp/rlv2/mirror
rm -rf /tmp/rlv2 && mkdir -p /tmp/rlv2/ledger
python3 scripts/spec_ledger.py register 100-demo --title "Demo" --intent "I" --spec-file f.md
python3 scripts/spec_ledger.py set 100-demo shipped --by orc
python3 scripts/spec_ledger.py verify 100-demo --judge codex --evidence e --criterion c1=CONFIRMED --criterion c2=REJECTED
python3 scripts/spec_ledger.py --render 2>/dev/null | grep -A2 NEEDS-REWORK
python3 scripts/spec_ledger.py set 100-demo accepted --by orc; echo "exit: $?"
```

Expected: the `verify` prints `100-demo verified -> REJECTED`; the render shows `100-demo` under `❌ NEEDS-REWORK`; the final `set ... accepted` prints the computed-only refusal and `exit: 1`.

- [ ] **Step 4: Final commit (if any formatting changed)**

```bash
cd /home/albert/do-it
git add -A
git commit -m "chore(ledger): review-loop v2 plan 1 — lint/format" || echo "nothing to commit"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** Plan 1 covers design items 3 (per-criterion aggregation + REJECTED-as-input), 4 (derived join, `accepted` non-writable + legacy migration, the `alert`/`--check` split), and the ledger half of item 5 (the `needs-human` projection via `effective_status`). Items 1 (cron/watchdog), 2 (Playwright assertion engine + card-schema parsing + the A1 live proof), the verifier *writing* REJECTED, the durable NEEDS-HUMAN *store*, and item 6 (`rev`/`rev-watch`) are explicitly Plans 2-3.
- **Placeholder scan:** none — every step has real code/commands.
- **Type consistency:** `resolve_spec_verdict(dict)->str|None`, `effective_status(rec, verdict|None)->str`, `_shipped_at(rec)->datetime|None`, `cmd_alert(argv)->int` are used consistently across tasks. `VALID_CRITERION_VERDICT` and `AWAITING_PROD_FLOOR_HOURS` are defined before use.
