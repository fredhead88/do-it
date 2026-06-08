"""Regression guard for the bus number-allocation pattern.

Recurring bug (fixed 2026-06-08, v3.2.2): the hand-rolled allocator used
`grep -oP '^\\d{3}'` (or a bare `^[0-9]{3}`), which grabs the first three digits
of *any* filename. Grandfathered date-stem files (`2026-05-31-...`) read as "202"
out of the year and allocated ~203 — and once a bad `203-` file exists it becomes
the new max and poisons every future allocation.

The fix is one character class plus a lookahead: `^[0-9]{3}(?=-)` — the `(?=-)`
requires a hyphen right after the three digits, so a year no longer matches. These
tests fail if either skill regresses the pattern, or if the pattern itself stops
behaving.
"""

import re
import subprocess
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"
ALLOCATORS = [
    SKILLS / "spec-handover" / "SKILL.md",
    SKILLS / "think" / "SKILL.md",
]

GOOD = r"grep -oP '^[0-9]{3}(?=-)'"
# The buggy forms the fix removed.
BAD_PATTERNS = [r"grep -oP '^\d{3}'", r"grep -oP '^[0-9]{3}'"]


def test_skills_carry_hardened_pattern():
    for skill in ALLOCATORS:
        text = skill.read_text()
        assert GOOD in text, f"{skill.name} missing hardened allocator `{GOOD}`"
        for bad in BAD_PATTERNS:
            assert bad not in text, f"{skill.name} still contains buggy pattern `{bad}`"


def test_skills_scan_every_bus_dir():
    # Briefs and specs share one number space; both allocators must scan brief-inbox
    # AND spec-inbox AND the ledger so the two lanes can't collide.
    for skill in ALLOCATORS:
        text = skill.read_text()
        for bus_dir in ("spec-inbox", "ledger", "brief-inbox"):
            assert bus_dir in text, f"{skill.name} allocator does not scan {bus_dir}"


def _run_allocator(pattern: str, names: list[str]) -> str:
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
    assert _run_allocator("^[0-9]{3}(?=-)", names) == "108"
    # Prove the buggy pattern really would have mis-fired (documents the bug).
    assert _run_allocator(r"^\d{3}", names) == "202"


def test_pattern_handles_empty_and_briefs():
    names = ["110-fc-coverage.brief.md", "109-asin-spec.md"]
    assert _run_allocator("^[0-9]{3}(?=-)", names) == "110"
    assert _run_allocator("^[0-9]{3}(?=-)", []) == ""


def test_extracted_command_lines_use_lookahead():
    # Every literal grep -oP line in the allocators must use the lookahead form.
    grep_line = re.compile(r"grep -oP '([^']*)'")
    for skill in ALLOCATORS:
        for m in grep_line.finditer(skill.read_text()):
            assert m.group(1) == "^[0-9]{3}(?=-)", (
                f"{skill.name} has a non-hardened grep -oP: {m.group(1)!r}"
            )
