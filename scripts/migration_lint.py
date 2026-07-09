"""migration_lint.py — static linter for Alembic migration files.

Three checks:
  (a) revision id <= 32 chars (alembic_version is varchar(32)).
  (b) down_revision matches the current chain head (excluding candidate files).
  (c) Missing ::text casts on Postgres catalog introspection tokens:
        - contype not followed by ::text
        - attname not followed by ::text
        - name[]-typed catalog column compared to text[] without ::text[] cast

CLI:
    python scripts/migration_lint.py <migration.py> [<more.py>] [--versions-dir DIR]

Importable:
    from scripts.migration_lint import lint_migration
    violations = lint_migration("path/to/0001_foo.py")
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _parse_migration_meta(path: str) -> tuple[str | None, str | tuple | None]:
    """Return (revision, down_revision) from a migration file via AST.

    down_revision may be:
        str   -- single parent
        None  -- genesis (no parent)
        tuple -- merge migration (multiple parents)
    Returns (None, None) on parse failure.
    """
    try:
        source = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path)
    except (OSError, SyntaxError):
        return None, None

    revision: str | None = None
    down_revision: str | tuple | None = None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue

            if target.id == "revision":
                val = node.value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    revision = val.value

            elif target.id == "down_revision":
                val = node.value
                if isinstance(val, ast.Constant):
                    # None literal or a string
                    down_revision = val.value  # type: ignore[assignment]
                elif isinstance(val, ast.Tuple):
                    # ("rev1", "rev2") merge migration
                    parts: list[str] = []
                    for elt in val.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            parts.append(elt.value)
                    down_revision = tuple(parts)

    return revision, down_revision


# ---------------------------------------------------------------------------
# Rule (a): rev-id length
# ---------------------------------------------------------------------------

def _check_rev_id_length(path: str, revision: str) -> list[str]:
    """Violation if revision id exceeds 32 chars (alembic_version varchar(32))."""
    if len(revision) > 32:
        return [
            f"[a] rev-id too long: '{revision}' is {len(revision)} chars"
            f" (max 32 — alembic_version varchar(32) limit)"
        ]
    return []


# ---------------------------------------------------------------------------
# Rule (b): down_revision == current chain head
# ---------------------------------------------------------------------------

def _scan_chain(versions_dir: Path, exclude_paths: set[str]) -> dict[str, str | tuple | None]:
    """Return mapping revision_id -> down_revision for non-excluded, non-archive migrations.

    Only scans *.py directly in versions_dir (not recursive), which naturally
    excludes the _archive/ subdirectory.
    """
    chain: dict[str, str | tuple | None] = {}
    for f in versions_dir.glob("*.py"):
        if f.name.startswith("__"):
            continue
        # Defence-in-depth: skip archive files even if called with rglob
        if "_archive" in f.parts:
            continue
        resolved = str(f.resolve())
        if resolved in exclude_paths:
            continue
        rev, down_rev = _parse_migration_meta(str(f))
        if rev is None:
            continue
        chain[rev] = down_rev
    return chain


def _find_all_heads(chain: dict[str, str | tuple | None]) -> set[str]:
    """Return all revisions that no other file references as a down_revision."""
    all_revisions = set(chain.keys())
    referenced: set[str] = set()
    for down_rev in chain.values():
        if down_rev is None:
            pass
        elif isinstance(down_rev, tuple):
            referenced.update(r for r in down_rev if r)
        elif isinstance(down_rev, str) and down_rev:
            referenced.add(down_rev)
    return all_revisions - referenced


def _check_down_revision(
    path: str,
    down_revision: str | tuple | None,
    versions_dir: Path,
    exclude_paths: set[str],
) -> list[str]:
    """Violation if candidate's down_revision does not match the current chain head.

    When a candidate file is excluded from the chain, excluding it may create an
    "orphan head" — the revision the candidate was previously pointing to loses its
    only consumer and becomes a spurious second head.  We detect this by comparing
    the set of observed heads against the candidate's own down_revision:

        observed_heads = {real_tip, candidate.down_revision}  (stale case)
        observed_heads = {real_tip}                           (clean case)

    If exactly one head remains after removing the candidate's down_revision from
    the observed set, that is the real tip and the candidate is stale.
    """
    chain = _scan_chain(versions_dir, exclude_paths)
    if not chain:
        # No other migrations in the chain — genesis candidate, nothing to check
        return []

    observed_heads = _find_all_heads(chain)

    if not observed_heads:
        return [
            f"[b] chain in {versions_dir} has zero detectable heads"
            f" (circular dependency?)"
        ]

    # Normalise candidate down_revision as a set for uniform handling
    if down_revision is None:
        cand_parents: set[str] = set()
    elif isinstance(down_revision, str):
        cand_parents = {down_revision} if down_revision else set()
    else:  # tuple
        cand_parents = {r for r in down_revision if r}

    if len(observed_heads) == 1:
        # Clean chain: one head, straightforward check
        (real_head,) = observed_heads
        if not cand_parents:
            return [
                f"[b] down_revision is None but chain head is '{real_head}';"
                f" expected down_revision = '{real_head}'"
            ]
        if real_head not in cand_parents:
            return [
                f"[b] stale down_revision: {sorted(cand_parents)!r}"
                f" — expected current head '{real_head}'"
            ]
        return []

    # Multiple heads observed.  Common cause: the candidate was a mid-chain file
    # whose removal orphaned the revision it previously pointed to (that revision
    # is now a "spurious" head alongside the real tip).
    #
    # Heuristic: remove any head that equals one of the candidate's parents — those
    # are the orphaned heads.  The remaining head(s) are the real tip(s).
    real_heads = observed_heads - cand_parents

    if len(real_heads) == 1:
        (real_head,) = real_heads
        if real_head not in cand_parents:
            # Candidate's down_revision != real tip → stale
            return [
                f"[b] stale down_revision: {sorted(cand_parents)!r}"
                f" — expected current head '{real_head}'"
                f" (orphaned heads detected: {sorted(observed_heads - real_heads)!r})"
            ]
        # Candidate's down_revision IS the real head (shouldn't happen after the
        # set subtraction, but be safe)
        return []

    # Ambiguous: multiple non-parent heads; can't determine single real head
    return [
        f"[b] could not determine a unique chain head in {versions_dir}"
        f" (observed heads: {sorted(observed_heads)!r})"
    ]


# ---------------------------------------------------------------------------
# Rule (c): ::text cast checks
# ---------------------------------------------------------------------------

# (c1) contype not immediately followed by ::text
# Rationale: on PG17/18, bare `char` vs `text` operator is ambiguous in some
# contexts; genesis note explicitly requires contype::text in catalog queries.
_RE_CONTYPE_UNCAST = re.compile(r"\bcontype\b(?!::text)")

# (c2) attname not immediately followed by ::text
# Rationale: pg_attribute.attname is type `name`; array_agg(attname) returns
# name[] which cannot be compared to text[] without a cast (the 289 prod failure).
_RE_ATTNAME_UNCAST = re.compile(r"\battname\b(?!::text)")

# (c3) Other pg_catalog name-typed columns in aggregate/comparison context without
# ::text cast.  Only flagged when the column appears prefixed by a table alias
# (\w+\.) to avoid false-positives on user schema columns with the same name.
#
# Targets three sub-patterns:
#   c3a: array_agg(<alias>.<name_col>  without ::text immediately after the col
#   c3b: <alias>.<name_col> = ANY(  without ::text before = ANY
#   c3c: array_agg(<alias>.<name_col>[...]) = ARRAY[  without ::text

_CATALOG_NAME_COLS = r"(?:conname|relname|nspname|proname|rolname)"

# c3a: aggregate of uncast name-type catalog col (alias-qualified)
_RE_C3_AGG_UNCAST = re.compile(
    r"array_agg\s*\(\s*\w+\." + _CATALOG_NAME_COLS + r"\b(?!::text)",
    re.IGNORECASE | re.DOTALL,
)

# c3b: alias-qualified name-type catalog col in = ANY(...) comparison without ::text
_RE_C3_ANY_UNCAST = re.compile(
    r"\w+\." + _CATALOG_NAME_COLS + r"\b(?!::text)\s*=\s*ANY\s*\(",
    re.IGNORECASE,
)

# c3c: array_agg(alias.name_col[...]) result compared to text[] literal
_RE_C3_AGG_CMP = re.compile(
    r"array_agg\s*\(\s*\w+\." + _CATALOG_NAME_COLS + r"\b(?!::text)[^)]*\)\s*=\s*ARRAY\[",
    re.IGNORECASE | re.DOTALL,
)


def _check_cast_rules(path: str) -> list[str]:
    """Rule (c): flag uncast contype / attname / name-type catalog columns."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return [f"[c] could not read file: {exc}"]

    violations: list[str] = []
    lines = text.splitlines()

    # c1 + c2: line-by-line so we can report exact line numbers
    for lineno, line in enumerate(lines, start=1):
        snippet = line.strip()[:80]
        if _RE_CONTYPE_UNCAST.search(line):
            violations.append(
                f"[c1] line {lineno}: `contype` without `::text` cast — '{snippet}'"
            )
        if _RE_ATTNAME_UNCAST.search(line):
            violations.append(
                f"[c2] line {lineno}: `attname` without `::text` cast — '{snippet}'"
            )

    # c3: full-file multi-line scan (DOTALL for SQL strings spanning newlines)
    for pattern, label in [
        (_RE_C3_AGG_UNCAST, "array_agg of uncast name-type catalog column"),
        (_RE_C3_ANY_UNCAST, "alias-qualified name-type catalog column in = ANY() without ::text"),
        (_RE_C3_AGG_CMP, "array_agg(name-type) = ARRAY[text] without ::text[]"),
    ]:
        for m in pattern.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            snippet = m.group(0).replace("\n", " ").strip()[:80]
            violations.append(f"[c3] line {lineno}: {label} — '{snippet}'")

    return violations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lint_migration(path: str, versions_dir: str | None = None) -> list[str]:
    """Lint a single Alembic migration file.

    Args:
        path:         Path to the migration .py file.
        versions_dir: Directory containing the full migration chain for head
                      detection. Defaults to the directory containing ``path``.

    Returns:
        A list of human-readable violation strings.  Empty list = clean.
    """
    p = Path(path).resolve()
    vdir = Path(versions_dir).resolve() if versions_dir else p.parent

    revision, down_revision = _parse_migration_meta(str(p))

    violations: list[str] = []

    if revision is None:
        violations.append(
            f"[a/b] could not parse `revision` from file"
            f" (syntax error or missing assignment): {p.name}"
        )
        violations.extend(_check_cast_rules(str(p)))
        return violations

    violations.extend(_check_rev_id_length(str(p), revision))
    violations.extend(_check_down_revision(str(p), down_revision, vdir, {str(p)}))
    violations.extend(_check_cast_rules(str(p)))

    return violations


def lint_migrations(paths: list[str], versions_dir: str | None = None) -> dict[str, list[str]]:
    """Lint multiple candidate migration files simultaneously.

    All candidates are excluded from the chain-head computation together, so a
    batch of new migrations won't spuriously flag each other's down_revision.

    Returns:
        Mapping path -> list of violation strings.
    """
    resolved = [str(Path(p).resolve()) for p in paths]
    vdir = (
        Path(versions_dir).resolve()
        if versions_dir is not None
        else Path(resolved[0]).parent
    )
    exclude_all = set(resolved)

    results: dict[str, list[str]] = {}
    for orig, rp in zip(paths, resolved):
        rev, down_rev = _parse_migration_meta(rp)
        viols: list[str] = []
        if rev is None:
            viols.append(
                f"[a/b] could not parse `revision` from file"
                f" (syntax error or missing assignment): {Path(rp).name}"
            )
            viols.extend(_check_cast_rules(rp))
        else:
            viols.extend(_check_rev_id_length(rp, rev))
            viols.extend(_check_down_revision(rp, down_rev, vdir, exclude_all))
            viols.extend(_check_cast_rules(rp))
        results[orig] = viols

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Static linter for Alembic migration files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "migrations",
        nargs="+",
        metavar="migration.py",
        help="Migration file(s) to lint.",
    )
    parser.add_argument(
        "--versions-dir",
        metavar="DIR",
        default=None,
        help=(
            "Directory containing the migration chain for chain-head detection. "
            "Defaults to the directory containing the first migration file."
        ),
    )
    args = parser.parse_args()

    if len(args.migrations) == 1:
        results = {args.migrations[0]: lint_migration(args.migrations[0], args.versions_dir)}
    else:
        results = lint_migrations(args.migrations, args.versions_dir)

    any_violation = False
    for path, viols in results.items():
        if viols:
            any_violation = True
            print(f"FAIL {path}")
            for v in viols:
                print(f"  {v}")
        else:
            print(f"PASS {path}")

    sys.exit(1 if any_violation else 0)


if __name__ == "__main__":
    main()
