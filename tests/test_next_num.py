"""Tests for the atomic shared-bus number allocator (`next-num`).

Guards both failure modes seen live on 2026-06-08:
  1. Misread — the year in a grandfathered date-stem file (`2026-...`) read as 202.
  2. Race — two sessions computing max+1 double-booked the same number (110).

The allocator reserves under one bus-wide lock and the reservation IS the artifact
(registered ledger record for specs; brief file for briefs), so a later caller's
scan sees it without a separate register step.
"""

import importlib.util
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    monkeypatch.setenv("DOIT_SPEC_INBOX", str(tmp_path / "spec-inbox"))
    monkeypatch.setenv("DOIT_BRIEF_INBOX", str(tmp_path / "brief-inbox"))
    spec = importlib.util.spec_from_file_location("spec_ledger_next_num", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _touch(d: Path, name: str):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("x")


def _alloc(sl, capsys, *args) -> tuple[int, str]:
    rc = sl.cmd_next_num(list(args))
    out = capsys.readouterr().out.strip()
    return rc, out


def test_brief_allocation_writes_file_and_prints_number(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    _touch(sl.SPEC_INBOX, "108-prior-spec.md")
    rc, out = _alloc(sl, capsys, "--kind", "brief", "--slug", "fc-coverage")
    assert rc == 0 and out == "109"
    assert (sl.BRIEF_INBOX / "109-fc-coverage.brief.md").exists()


def test_spec_allocation_births_registered_record(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    _touch(sl.BRIEF_INBOX, "108-thing.brief.md")
    rc, out = _alloc(
        sl,
        capsys,
        "--kind",
        "spec",
        "--slug",
        "asin-detail",
        "--title",
        "ASIN detail",
        "--intent",
        "fix the thing",
        "--spec-file",
        "docs/do-it/specs/x.md",
    )
    assert rc == 0 and out == "109"
    rec = yaml.safe_load((sl.LEDGER_DIR / "109-asin-detail.yml").read_text())
    assert rec["status"] == "registered" and rec["spec_id"] == "109-asin-detail"
    assert rec["title"] == "ASIN detail"


def test_shared_counter_across_lanes(monkeypatch, tmp_path, capsys):
    # A brief and a spec draw from ONE number space; the second sees the first's
    # reservation without any separate register step (this is the race fix).
    sl = _load(monkeypatch, tmp_path)
    rc1, n1 = _alloc(sl, capsys, "--kind", "brief", "--slug", "first")
    rc2, n2 = _alloc(
        sl,
        capsys,
        "--kind",
        "spec",
        "--slug",
        "second",
        "--title",
        "T",
        "--intent",
        "I",
        "--spec-file",
        "f.md",
    )
    rc3, n3 = _alloc(sl, capsys, "--kind", "brief", "--slug", "third")
    assert (rc1, rc2, rc3) == (0, 0, 0)
    assert [n1, n2, n3] == ["001", "002", "003"]


def test_date_stem_files_do_not_poison(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    # Grandfathered date-stem specs (live + archive) must NOT read as 202.
    _touch(sl.SPEC_INBOX, "2026-05-31-old-thing-spec.md")
    _touch(sl.SPEC_INBOX / "_archive", "2026-06-04-archived-spec.md")
    _touch(sl.SPEC_INBOX, "108-real-spec.md")
    rc, out = _alloc(sl, capsys, "--kind", "brief", "--slug", "next")
    assert rc == 0 and out == "109"


def test_sanity_ceiling_refuses_poisoned_max(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    _touch(sl.LEDGER_DIR, "203-poison.yml")  # a bad allocation already on disk
    rc = sl.cmd_next_num(["--kind", "brief", "--slug", "x"])
    err = capsys.readouterr().err
    assert rc == 1 and "poisoned" in err and "find ~/.claude" in err


def test_spec_missing_fields_rejected(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_next_num(["--kind", "spec", "--slug", "x", "--title", "only-title"])
    err = capsys.readouterr().err
    assert rc == 1 and "needs" in err


def test_bad_slug_rejected(monkeypatch, tmp_path, capsys):
    sl = _load(monkeypatch, tmp_path)
    rc = sl.cmd_next_num(["--kind", "brief", "--slug", "a/b"])
    assert rc == 1 and "bad slug" in capsys.readouterr().err


def test_scan_matches_grep_semantics(monkeypatch, tmp_path, capsys):
    # scan_bus_max is the Python equivalent of grep -oP '^[0-9]{3}(?=-)'.
    sl = _load(monkeypatch, tmp_path)
    _touch(sl.SPEC_INBOX, "2026-05-31-x-spec.md")  # year -> ignored
    _touch(sl.LEDGER_DIR, "107-a.yml")
    _touch(sl.BRIEF_INBOX, "110-b.brief.md")
    assert sl.scan_bus_max() == 110
