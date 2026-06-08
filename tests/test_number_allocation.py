"""Regression guard for the bus number-allocation pattern.

Recurring bug (fixed 2026-06-08): the hand-rolled allocator used `grep -oP '^\\d{3}'`
(or a bare `^[0-9]{3}`), which grabs the first three digits of *any* filename.
Grandfathered date-stem files (`2026-05-31-...`) read as "202" out of the year and
allocated ~203 — and once a bad `203-` file exists it becomes the new max and poisons
every future allocation.

- v3.2.2 fixed the *pattern*: `^[0-9]{3}(?=-)` — the `(?=-)` requires a hyphen right
  after the three digits, so a year no longer matches.
- v3.3.0 moved allocation OUT of the skills into `spec_ledger.py next-num` (atomic,
  bus-wide-locked). So the pattern now lives in the helper, and the skills must
  delegate to it — they must NOT carry any inline `grep`/`max+1` (which would bypass
  the lock and re-open the race). Behavioral coverage of the helper is in
  `test_next_num.py`; these tests guard the pattern semantics and the skill delegation.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
LEDGER_SRC = (ROOT / "scripts" / "spec_ledger.py").read_text()
ALLOCATOR_SKILLS = [
    SKILLS / "spec-handover" / "SKILL.md",
    SKILLS / "think" / "SKILL.md",
]

# The buggy forms the fix removed — must appear nowhere, skills or helper.
BAD_PATTERNS = [r"^\d{3}'", r"grep -oP '^[0-9]{3}'"]


def test_helper_carries_hardened_pattern():
    # The one place the pattern lives now is the allocator regex in spec_ledger.py.
    assert r'r"^([0-9]{3})-"' in LEDGER_SRC, "spec_ledger.py _NUM_RE not hardened"
    for bad in BAD_PATTERNS:
        assert bad not in LEDGER_SRC, f"spec_ledger.py contains buggy pattern `{bad}`"


def test_helper_scans_every_bus_dir():
    # Briefs and specs share one number space; the allocator must scan all five lanes.
    for lane in ("SPEC_INBOX", "BRIEF_INBOX", "LEDGER_DIR", "_archive"):
        assert lane in LEDGER_SRC, f"spec_ledger.py allocator does not reference {lane}"


def test_skills_delegate_to_next_num_with_no_inline_grep():
    # Post-v3.3.0 the skills must call the locked helper, not re-implement allocation.
    for skill in ALLOCATOR_SKILLS:
        text = skill.read_text()
        assert "next-num" in text, f"{skill.name} does not call `next-num`"
        assert "grep -oP" not in text, (
            f"{skill.name} still carries an inline grep — that bypasses the bus lock "
            f"and re-opens the double-book race"
        )
        for bad in BAD_PATTERNS:
            assert bad not in text, f"{skill.name} still contains buggy pattern `{bad}`"


def _run_pattern(pattern: str, names: list[str]) -> str:
    """Max NNN that `grep -oP <pattern>` extracts from a filename listing."""
    listing = "\n".join(names) + "\n"
    out = subprocess.run(
        ["grep", "-oP", pattern],
        input=listing,
        capture_output=True,
        text=True,
    ).stdout
    nums = sorted(int(n) for n in out.split())
    return str(nums[-1]) if nums else ""


def test_pattern_ignores_date_stems():
    # The genuine max here is 108; the buggy pattern would return 202 (the year).
    names = [
        "2026-05-31-old-thing-spec.md",  # grandfathered date-stem
        "2026-06-04-another-spec.md",
        "108-fc-throughput-spec.md",
        "107-bridge.yml",
    ]
    assert _run_pattern("^[0-9]{3}(?=-)", names) == "108"
    # Prove the buggy pattern really would have mis-fired (documents the bug).
    assert _run_pattern(r"^\d{3}", names) == "202"


def test_pattern_handles_empty_and_briefs():
    names = ["110-fc-coverage.brief.md", "109-asin-spec.md"]
    assert _run_pattern("^[0-9]{3}(?=-)", names) == "110"
    assert _run_pattern("^[0-9]{3}(?=-)", []) == ""


def test_no_stray_grep_oP_in_skills():
    # Belt-and-suspenders: no literal grep -oP survives in either allocator skill.
    stray = re.compile(r"grep -oP")
    for skill in ALLOCATOR_SKILLS:
        assert not stray.search(skill.read_text()), (
            f"{skill.name} has a stray grep -oP line"
        )
