#!/usr/bin/env python3
"""Builder close-out gate orchestrator (spec 294).

Shifts three integrator-re-check defects LEFT into the builder close-out so a
builder cannot write `.ready` on work the integrator would bounce:

  CHECK 1  prod migration dry-run   — catch prod-only migration failures
                                       (289 name[]=text[] cast) by replaying the
                                       offline SQL against PROD inside a
                                       BEGIN..ROLLBACK transaction. Prod is never
                                       mutated.
  CHECK 2  affected/sibling tests   — run EVERY test that imports a touched
                                       shared module, not just the spec's own
                                       tests (275 broke 248). Block only on a
                                       genuine regression (base-green, branch-red).
  CHECK 3  migration-authoring lint — delegate to scripts/migration_lint.py
                                       (rev-id length, stale down_revision,
                                       uncast constraint introspection).
  CHECK 4  requires_live_run proof   — when a spec doc declares
                                       `requires_live_run: true`, require a
                                       committed live-run proof artifact
                                       (docs/do-it/live-run-proofs/<spec>.json)
                                       recording a COMPLETED scoped prod run
                                       that persisted >=1 real row, and (when a
                                       db_url is available) re-query prod to
                                       confirm the row is actually there (spec
                                       362 — closes the "code shipped, the
                                       actual data-mutating run never
                                       happened" gap).

CLI:
  python scripts/builder_closeout_check.py --base <sha> --branch <ref> \
      --spec <NNN-slug> [--repo-root .]

Exit non-zero if ANY check fails. Prints a per-check PASS/FAIL summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSIONS_SUBDIR = "api/alembic_supabase/versions"
ARCHIVE_MARKER = "_archive/"
SRC_PREFIXES = ("pipelines/", "api/", "agents/", "scripts/")

# --- CHECK 2 config-faithfulness (spec 308) ---
CONFIG_MIRROR_ROOTS = ("config",)  # gitignored runtime-config tree to mirror

# spec 426 R2 — faithful test env. The canonical suite run (CI: `pytest tests/ api/tests/`)
# collects all modules together, so import-time `os.environ.setdefault("LLM_STUB","1")` in
# sibling modules (e.g. tests/pipelines/amazon/test_409_keepa_search.py) propagates the
# zero-spend stub flags to the whole process before tests that ASSERT them run. Running a
# single affected file in ISOLATION (as CHECK 2 does) loses that side-effect → false
# branch-red. Restore the zero-spend posture the builder/CI run had, WITHOUT overriding a
# value the box set explicitly.
FAITHFUL_TEST_ENV = {"LLM_STUB": "1", "KEEPA_STUB": "1", "SCRAPE_STUB": "1"}
CONFIG_MIRROR_ROOT_FILES = (".env",)  # gitignored root-level runtime files
MIRROR_DENYLIST_PREFIXES = (
    "venv/",
    ".venv/",
    "node_modules/",
    "api/data/",
    "output/",
    ".git/",
)
MIRROR_DENYLIST_NAMES = {"venv", ".venv", "node_modules", "output", ".git"}
MIRROR_SIZE_CAP = 5 * 1024 * 1024  # 5 MiB — config is small; never copy blobs/state
CONFIG_REF_RE = re.compile(
    r"config/|_CONFIG\b|amazon_clients|google_credentials", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _run(cmd, cwd=None, env=None, input_text=None, timeout=600):
    """Run a command, returning (returncode, stdout, stderr)."""
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _git_changed_files(repo_root, base, branch):
    """Files changed between base..branch (added/modified/deleted)."""
    rc, out, err = _run(
        ["git", "diff", "--name-only", f"{base}..{branch}"], cwd=str(repo_root)
    )
    if rc != 0:
        raise RuntimeError(f"git diff failed: {err.strip()}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _resolve_db_url(repo_root, explicit=None):
    """SUPABASE_DB_URL from explicit arg, env, then repo .env."""
    if explicit:
        return explicit
    env_url = os.environ.get("SUPABASE_DB_URL")
    if env_url:
        return env_url
    env_file = Path(repo_root) / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith("SUPABASE_DB_URL="):
                val = line.split("=", 1)[1].strip()
                # Strip matched surrounding quotes — the live .env value is
                # double-quoted; psql would otherwise get a quoted URL and fail
                # with "invalid connection option" (the quote-wrapped-token trap).
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                return val
    return None


def _alembic_cmd(repo_root):
    """Prefer the project venv alembic; fall back to python -m alembic."""
    venv_alembic = Path(repo_root) / ".venv" / "bin" / "alembic"
    if venv_alembic.is_file():
        return [str(venv_alembic)]
    return [sys.executable, "-m", "alembic"]


# ---------------------------------------------------------------------------
# CHECK 1 — prod migration dry-run
# ---------------------------------------------------------------------------
def _changed_migrations(repo_root, base, branch):
    """Changed migration files under versions/ (excluding _archive/)."""
    changed = _git_changed_files(repo_root, base, branch)
    out = []
    for f in changed:
        if f.startswith(VERSIONS_SUBDIR + "/") and ARCHIVE_MARKER not in f:
            if f.endswith(".py") and (Path(repo_root) / f).is_file():
                out.append(f)
    return out


def _parse_revisions(path):
    """Extract (revision, down_revision) from a migration file."""
    text = Path(path).read_text()
    rev_m = re.search(r"^revision\s*[:=].*?['\"]([^'\"]+)['\"]", text, re.MULTILINE)
    down_m = re.search(
        r"^down_revision\s*[:=].*?['\"]([^'\"]+)['\"]", text, re.MULTILINE
    )
    revision = rev_m.group(1) if rev_m else None
    down_revision = down_m.group(1) if down_m else None
    return revision, down_revision


def _prod_alembic_version(db_url):
    """Current version_num from prod alembic_version, or None on failure."""
    rc, out, err = _run(
        ["psql", db_url, "-tAc", "SELECT version_num FROM alembic_version"],
    )
    if rc != 0:
        return None
    return out.strip()


def check_prod_migration_dryrun(repo_root, base, branch, db_url):
    """Replay each changed migration's offline SQL against prod inside a
    BEGIN..ROLLBACK transaction. Returns a list of failure strings (empty =
    pass/skip). NEVER commits to prod.
    """
    repo_root = Path(repo_root)
    failures = []
    migrations = _changed_migrations(repo_root, base, branch)
    if not migrations:
        return failures  # skip — caller notes "no migration touched"

    if not db_url:
        failures.append(
            "migration(s) touched but SUPABASE_DB_URL is missing — cannot verify "
            "the prod-only-failure class (the whole point of this check): "
            + ", ".join(migrations)
        )
        return failures

    api_dir = repo_root / "api"
    alembic = _alembic_cmd(repo_root)
    env = dict(os.environ)
    env["SUPABASE_DB_URL"] = db_url

    head_before = _prod_alembic_version(db_url)

    for mig in migrations:
        revision, down_revision = _parse_revisions(repo_root / mig)
        if not revision or not down_revision:
            failures.append(
                f"{mig}: could not parse revision/down_revision "
                f"(revision={revision!r}, down_revision={down_revision!r})"
            )
            continue

        # 1. generate offline SQL for just this migration step
        rc, sql, err = _run(
            alembic
            + [
                "-c",
                "alembic_supabase.ini",
                "upgrade",
                f"{down_revision}:{revision}",
                "--sql",
            ],
            cwd=str(api_dir),
            env=env,
        )
        if rc != 0:
            failures.append(
                f"{mig}: alembic offline SQL generation failed: {err.strip()[:500]}"
            )
            continue

        # 2. the migration must actually complete: stamp alembic_version to <rev>
        stamp_re = re.compile(
            r"UPDATE\s+alembic_version\s+SET\s+version_num\s*=\s*'"
            + re.escape(revision)
            + r"'",
            re.IGNORECASE,
        )
        if not stamp_re.search(sql):
            failures.append(
                f"{mig}: generated SQL does not contain the final "
                f"`UPDATE alembic_version ... = '{revision}'` — migration would "
                f"not complete (only partial DDL emitted)"
            )

        # 3. run it against prod wrapped in BEGIN..ROLLBACK — assert no ERROR
        wrapped = f"BEGIN;\n{sql}\nROLLBACK;\n"
        rc, out, err = _run(
            ["psql", db_url, "-v", "ON_ERROR_STOP=1", "-f", "-"],
            input_text=wrapped,
        )
        if rc != 0:
            failures.append(
                f"{mig}: prod dry-run raised an ERROR (the prod-only-failure "
                f"class — e.g. 289 name[]=text[]): {err.strip()[:800]}"
            )

    # 4. confirm prod left untouched
    head_after = _prod_alembic_version(db_url)
    if head_before is not None and head_after != head_before:
        failures.append(
            f"prod alembic_version changed during dry-run "
            f"({head_before!r} -> {head_after!r}) — prod was mutated; this must "
            f"never happen (all runs are BEGIN..ROLLBACK)"
        )

    return failures


# ---------------------------------------------------------------------------
# CHECK 2 — affected / sibling test run
# ---------------------------------------------------------------------------
def _touched_src_modules(repo_root, base, branch):
    """*.py touched under src dirs (not tests, not migrations) -> import paths."""
    changed = _git_changed_files(repo_root, base, branch)
    modules = []
    for f in changed:
        if not f.endswith(".py"):
            continue
        if f.startswith("tests/") or f.startswith("api/tests/"):
            continue
        if f.startswith(VERSIONS_SUBDIR + "/"):
            continue
        if not f.startswith(SRC_PREFIXES):
            continue
        if not (Path(repo_root) / f).is_file():
            continue  # deleted file
        mod = f[:-3].replace("/", ".")  # strip .py, slashes -> dots
        modules.append((f, mod))
    return modules


def _test_files(repo_root):
    """All test_*.py / *_test.py files under tests/ and api/tests/."""
    out = []
    for base_dir in ("tests", "api/tests"):
        d = Path(repo_root) / base_dir
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            name = p.name
            if name.startswith("test_") or name.endswith("_test.py"):
                out.append(p)
    return out


def _spec_tokens(spec):
    """Tokens identifying the spec's OWN tests (to exclude)."""
    tokens = set()
    spec = (spec or "").strip()
    if not spec:
        return tokens
    m = re.match(r"^(\d+)", spec)
    if m:
        tokens.add(m.group(1))
    # slug leaf: last hyphen-delimited word, and the bare slug
    slug = re.sub(r"^\d+[-_]?", "", spec)
    if slug:
        tokens.add(slug.replace("-", "_"))
        leaf = slug.split("-")[-1]
        if leaf:
            tokens.add(leaf)
    return {t for t in tokens if t}


def _is_own_spec_test(test_path, spec_tokens):
    name = test_path.name
    for tok in spec_tokens:
        if tok and tok in name:
            return True
    return False


def _affected_tests(repo_root, modules, test_paths, spec_tokens):
    """Test files that import any touched module (minus the spec's own tests)."""
    affected = {}  # path -> reason
    for tp in test_paths:
        if _is_own_spec_test(tp, spec_tokens):
            continue
        try:
            text = tp.read_text()
        except Exception:
            continue
        for src_file, mod in modules:
            leaf = mod.split(".")[-1]
            patterns = [
                rf"from\s+{re.escape(mod)}\s+import",
                rf"import\s+{re.escape(mod)}\b",
                # looser: import of the module leaf (e.g. `import keyword_collection`
                # or `from x.keyword_collection import`)
                rf"\b(?:import|from)\s+\S*\b{re.escape(leaf)}\b",
            ]
            if any(re.search(p, text) for p in patterns):
                affected[tp] = mod
                break
    return affected


def _run_pytest(test_file, cwd, repo_root):
    """Run a single test file. Returns (passed: bool, output: str)."""
    env = dict(os.environ)
    # Pin PYTHONPATH to ONLY the target repo so an inherited path (e.g. the real
    # repo's scripts/) cannot shadow a sandboxed base-worktree module and mask a
    # regression (the base/branch both-red false "pre-existing" failure mode).
    env["PYTHONPATH"] = str(repo_root)
    # spec 426 R2: faithful zero-spend stub env (setdefault → never override an explicit value)
    for _k, _v in FAITHFUL_TEST_ENV.items():
        env.setdefault(_k, _v)
    # spec 426: bound the per-test wall-clock. A sibling test that makes a real
    # network/DB call the faithful stub env does NOT cover (e.g. a direct Supabase
    # connection) would otherwise hang until the 600s _run default and raise an
    # UNCAUGHT subprocess.TimeoutExpired that crashes the whole close-out gate →
    # the opaque bare "regrade" this spec exists to kill. Catch it and return a
    # diagnosable could-not-run result; the base-vs-branch comparison then treats a
    # both-sides timeout as pre-existing (not a regression).
    try:
        _pytest_timeout = int(os.environ.get("CLOSEOUT_PYTEST_TIMEOUT", "180"))
    except ValueError:
        _pytest_timeout = 180
    try:
        rc, out, err = _run(
            [sys.executable, "-m", "pytest", str(test_file), "-q"],
            cwd=str(cwd),
            env=env,
            timeout=_pytest_timeout,
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"TEST TIMED OUT after {_pytest_timeout}s (could-not-run: likely a real "
            f"network/DB call not covered by the faithful stub env) — {test_file}"
        )
    return rc == 0, (out + "\n" + err)


# --- CHECK 2 config-faithfulness helpers (spec 308) -------------------------


def _main_checkout(repo_root):
    """Canonical (main) checkout = first entry of `git worktree list --porcelain`.

    The main worktree holds the gitignored runtime config the box has; a builder's
    linked worktree does not. Returns a Path, or None on failure.
    """
    rc, out, _err = _run(["git", "worktree", "list", "--porcelain"], cwd=str(repo_root))
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("worktree "):
            return Path(line.split(" ", 1)[1].strip())
    return None


def _is_denied(rel_str):
    """True if a relative path is under a denylisted heavy/state path."""
    if any(
        rel_str == p.rstrip("/") or rel_str.startswith(p)
        for p in MIRROR_DENYLIST_PREFIXES
    ):
        return True
    return bool(set(Path(rel_str).parts) & MIRROR_DENYLIST_NAMES)


def _candidate_config_files(checkout):
    """Runtime-config candidate files under the mirror scope (pre-filter)."""
    checkout = Path(checkout)
    out = []
    for root in CONFIG_MIRROR_ROOTS:
        d = checkout / root
        if d.is_dir():
            out.extend(p for p in d.rglob("*") if p.is_file())
    for fn in CONFIG_MIRROR_ROOT_FILES:
        p = checkout / fn
        if p.is_file():
            out.append(p)
    return out


def _gitignored_subset(checkout, paths):
    """Subset of `paths` (abs, under `checkout`) that git ignores in `checkout`.

    Returns a set of relative-path strings. Copying ONLY gitignored files can never
    dirty `git status`, so the mirror is invisible to git by construction.
    """
    checkout = Path(checkout)
    rels = []
    for p in paths:
        try:
            rels.append(str(Path(p).relative_to(checkout)))
        except ValueError:
            continue
    if not rels:
        return set()
    rc, out, _err = _run(
        ["git", "check-ignore", "--stdin"],
        cwd=str(checkout),
        input_text="\n".join(rels) + "\n",
    )
    # rc 0 => some ignored; rc 1 => none ignored; rc>1 => error (treat as none)
    if rc not in (0, 1):
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def _present_config_files(worktree):
    """Gitignored runtime-config files actually present in `worktree` (sorted rels)."""
    cands = _candidate_config_files(worktree)
    return sorted(_gitignored_subset(worktree, cands))


def _provision_runtime_config(main_checkout, target_worktree):
    """Mirror gitignored runtime-config from `main_checkout` into `target_worktree`
    so config-gated code takes the box's path (spec 308 R1/R2).

    Copies gitignored files ONLY (never dirties git status), scoped to the config
    roots, denylist + size capped, and never copies onto the main checkout itself.
    Returns the list of relative paths actually mirrored (possibly empty).
    """
    if main_checkout is None or target_worktree is None:
        return []
    main_checkout = Path(main_checkout)
    target_worktree = Path(target_worktree)
    try:
        if main_checkout.resolve() == target_worktree.resolve():
            return []  # never mirror onto self
    except OSError:
        return []
    capped = []
    for p in _candidate_config_files(main_checkout):
        rel = str(p.relative_to(main_checkout))
        if _is_denied(rel):
            continue
        try:
            if p.stat().st_size > MIRROR_SIZE_CAP:
                continue
        except OSError:
            continue
        capped.append(rel)
    ignored = _gitignored_subset(main_checkout, [main_checkout / r for r in capped])
    mirrored = []
    for rel in capped:
        if rel not in ignored:
            continue  # tracked file — already present in the worktree; never copy
        src = main_checkout / rel
        dst = target_worktree / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            mirrored.append(rel)
        except OSError:
            continue
    return mirrored


def _config_reading_modules(repo_root, modules):
    """Touched source modules whose source references a config-shaped path."""
    out = []
    for src_file, mod in modules:
        try:
            text = (Path(repo_root) / src_file).read_text()
        except OSError:
            continue
        if CONFIG_REF_RE.search(text):
            out.append(mod)
    return out


def config_authoritative_note(repo_root, base, branch, spec):
    """Return a NON-AUTHORITATIVE marker string when CHECK 2 cannot see the box's
    gitignored config (none present in the run worktree) yet a touched source module
    reads a config-shaped path — else None (spec 308 R3, honest labelling).
    """
    repo_root = Path(repo_root)
    modules = _touched_src_modules(repo_root, base, branch)
    if not modules:
        return None
    config_readers = _config_reading_modules(repo_root, modules)
    if not config_readers:
        return None
    if _present_config_files(repo_root):
        return None  # config faithfully present (native or mirrored) — authoritative
    return (
        "NON-AUTHORITATIVE (config-blind): no gitignored runtime config present to "
        "mirror, but touched module(s) read config-shaped paths "
        f"({', '.join(sorted(config_readers))}); worktree-green is NOT box-green here"
    )


def check_affected_sibling_tests(repo_root, base, branch, spec):
    """Run every sibling test that imports a touched shared module. Block only on
    a genuine regression (base-green, branch-red). Returns failure strings.
    """
    repo_root = Path(repo_root)
    failures = []
    modules = _touched_src_modules(repo_root, base, branch)
    if not modules:
        return failures  # nothing shared touched

    spec_tokens = _spec_tokens(spec)
    test_paths = _test_files(repo_root)
    affected = _affected_tests(repo_root, modules, test_paths, spec_tokens)
    if not affected:
        return failures  # caller notes "no affected sibling tests"

    # spec 308 R1: provision runtime config into the branch worktree so
    # config-gated sibling tests see the same config the box's main checkout has.
    main_checkout = _main_checkout(repo_root)
    _provision_runtime_config(main_checkout, repo_root)

    branch_red = []  # (test_file, output) that fail on the branch
    for tp in affected:
        passed, output = _run_pytest(tp, repo_root, repo_root)
        if not passed:
            branch_red.append((tp, output))

    if not branch_red:
        return failures  # all affected siblings green on the branch

    # For each branch-red file, check whether base was green (regression) or
    # already red (pre-existing — do not block).
    base_worktree = None
    try:
        base_worktree = tempfile.mkdtemp(prefix="closeout-base-")
        rc, out, err = _run(
            ["git", "worktree", "add", "--detach", base_worktree, base],
            cwd=str(repo_root),
        )
        if rc != 0:
            failures.append(
                "could not create base worktree to confirm regressions "
                f"({err.strip()[:300]}); branch-red sibling tests: "
                + ", ".join(str(t) for t, _ in branch_red)
            )
            return failures

        base_root = Path(base_worktree)
        # spec 308 R2: provision runtime config into the base worktree so the
        # base-confirmation run also sees the box's config (prevents false "pre-existing").
        _provision_runtime_config(main_checkout, base_root)
        for tp, branch_out in branch_red:
            rel = tp.relative_to(repo_root)
            base_test = base_root / rel
            if not base_test.is_file():
                # test did not exist at base — treat the branch failure as a
                # regression introduced alongside this work.
                failures.append(
                    f"REGRESSION: {rel} fails on branch and did not exist at "
                    f"base — failing output:\n{_tail(branch_out)}"
                )
                continue
            base_passed, _ = _run_pytest(base_test, base_root, base_root)
            if base_passed:
                failures.append(
                    f"REGRESSION: {rel} passes at base {base[:12]} but FAILS on "
                    f"branch (touched shared module: {affected[tp]}):\n"
                    f"{_tail(branch_out)}"
                )
            # base also red -> pre-existing; do not block (noted via stderr)
            else:
                sys.stderr.write(
                    f"  note: {rel} fails on both base and branch "
                    f"(pre-existing, not blocking)\n"
                )
    finally:
        if base_worktree:
            _run(
                ["git", "worktree", "remove", "--force", base_worktree],
                cwd=str(repo_root),
            )

    return failures


def _tail(text, n=25):
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------------------
# CHECK 3 — migration lint (delegated)
# ---------------------------------------------------------------------------
def check_migration_lint(repo_root, base, branch):
    """Call migration_lint.lint_migration on each changed migration file.
    Returns failure strings. Degrades gracefully if migration_lint is absent.
    """
    repo_root = Path(repo_root)
    failures = []
    migrations = _changed_migrations(repo_root, base, branch)
    if not migrations:
        return failures

    scripts_dir = str(repo_root / "scripts")
    added = False
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
        added = True
    try:
        from migration_lint import lint_migration  # type: ignore
    except ImportError:
        sys.stderr.write(
            "  note: scripts/migration_lint.py not importable in this worktree "
            "— CHECK 3 degraded (the sibling worker provides it; present in the "
            "merged tree). Skipping lint.\n"
        )
        return failures
    finally:
        if added and scripts_dir in sys.path:
            sys.path.remove(scripts_dir)

    versions_dir = str(repo_root / VERSIONS_SUBDIR)
    for mig in migrations:
        try:
            violations = lint_migration(str(repo_root / mig), versions_dir)
        except Exception as exc:  # be robust to lint internal errors
            failures.append(f"{mig}: migration_lint raised {exc!r}")
            continue
        for v in violations or []:
            failures.append(f"{mig}: {v}")
    return failures


# ---------------------------------------------------------------------------
# CHECK 4 — requires_live_run outcome verification (spec 362)
# ---------------------------------------------------------------------------
LIVE_RUN_MARKER_RE = re.compile(
    r"^\s*requires_live_run:\s*true\b", re.IGNORECASE | re.MULTILINE
)


def _find_spec_doc(repo_root, spec):
    """Locate the committed spec doc for `spec` (e.g. '362-slug') under
    docs/do-it/specs/. Prefers an exact `<spec>*.md` match, else falls back to
    `<NNN>-*.md` by leading number. Returns a Path or None.
    """
    repo_root = Path(repo_root)
    specs_dir = repo_root / "docs" / "do-it" / "specs"
    try:
        if not specs_dir.is_dir():
            return None
        exact = sorted(specs_dir.glob(f"{spec}*.md"))
        if exact:
            return exact[0]
        m = re.match(r"^(\d+)", spec or "")
        if m:
            by_num = sorted(specs_dir.glob(f"{m.group(1)}-*.md"))
            if by_num:
                return by_num[0]
    except OSError:
        return None
    return None


def _spec_requires_live_run(repo_root, spec) -> bool:
    """True iff the spec doc for `spec` contains a `requires_live_run: true`
    marker line. Missing doc or no marker -> False. Robust to OSError.
    """
    doc = _find_spec_doc(repo_root, spec)
    if doc is None:
        return False
    try:
        text = doc.read_text()
    except OSError:
        return False
    return bool(LIVE_RUN_MARKER_RE.search(text))


def _find_live_run_proof_file(repo_root, spec):
    """Locate docs/do-it/live-run-proofs/<spec>.json (fallback glob
    `<NNN>-*.json`). Returns a Path or None.
    """
    repo_root = Path(repo_root)
    proofs_dir = repo_root / "docs" / "do-it" / "live-run-proofs"
    try:
        if not proofs_dir.is_dir():
            return None
        exact = proofs_dir / f"{spec}.json"
        if exact.is_file():
            return exact
        m = re.match(r"^(\d+)", spec or "")
        if m:
            by_num = sorted(proofs_dir.glob(f"{m.group(1)}-*.json"))
            if by_num:
                return by_num[0]
    except OSError:
        return None
    return None


def _load_live_run_proof(repo_root, spec) -> dict | None:
    """Read + parse docs/do-it/live-run-proofs/<spec>.json. Returns the parsed
    dict, or None if absent/unparseable.
    """
    proof_file = _find_live_run_proof_file(repo_root, spec)
    if proof_file is None:
        return None
    try:
        return json.loads(proof_file.read_text())
    except (OSError, ValueError):
        return None


def check_live_run_proof(
    requires_live_run, proof, db_url=None, query_fn=None
) -> list[str]:
    """CHECK 4 verdict logic. Returns failure strings (empty = pass/skip).

    Pure and DB-free unless db_url AND query_fn are both supplied (AC3 live
    re-query).
    """
    if not requires_live_run:
        return []  # SKIP — not applicable (marker absent)

    if proof is None:
        return [
            "requires_live_run is set but no live-run proof artifact was "
            "committed (expected docs/do-it/live-run-proofs/<spec>.json) — a "
            "scoped prod run that persists >=1 row is required before .ready"
        ]

    failures: list[str] = []

    if proof.get("completed") is not True:
        failures.append(
            "requires_live_run proof does not record completed=true (the "
            "scoped prod run did not finish)"
        )

    row_count = proof.get("row_count")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 1:
        failures.append(
            f"requires_live_run proof records row_count={row_count!r}; a "
            "completed scoped run must persist >=1 real row"
        )

    target_table = proof.get("target_table")
    if not isinstance(target_table, str) or not target_table:
        failures.append("requires_live_run proof missing target_table")

    run_key = proof.get("run_key")
    if (
        not isinstance(run_key, dict)
        or not run_key.get("column")
        or run_key.get("value") in (None, "")
    ):
        failures.append("requires_live_run proof missing run_key {column,value}")

    if db_url and query_fn and not failures:
        try:
            actual = query_fn(db_url, target_table, run_key)
        except Exception as exc:  # be robust to any DB/driver error
            failures.append(f"requires_live_run proof: prod re-query raised: {exc!r}")
        else:
            if actual < 1:
                failures.append(
                    f"requires_live_run proof: prod re-query of {target_table} "
                    f"for {run_key} found 0 rows — the recorded artifact is not "
                    "backed by a real persisted row"
                )

    return failures


def _query_persisted_rows(db_url, target_table, run_key) -> int:
    """Real re-query: SELECT count(*) FROM <target_table> WHERE <run_key.column>
    = <run_key.value>. Uses psycopg2.sql composition — identifiers are NEVER
    string-concatenated into SQL (Security Contract: no raw SQL string concat).
    Lets exceptions propagate; the caller (check_live_run_proof) wraps them.
    """
    import psycopg2
    from psycopg2 import sql

    parts = [p for p in target_table.split(".") if p]
    table_ident = sql.Identifier(*parts)
    col_ident = sql.Identifier(run_key["column"])
    query = sql.SQL("SELECT count(*) FROM {table} WHERE {col} = %s").format(
        table=table_ident, col=col_ident
    )
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(query, (run_key["value"],))
            row = cur.fetchone()
            return int(row[0]) if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def run_all(repo_root, base, branch, spec, db_url=None):
    """Run all three checks. Returns exit code (0 all pass, 1 any failure)."""
    repo_root = Path(repo_root).resolve()
    db_url = _resolve_db_url(repo_root, db_url)

    results = []  # (name, failures)

    c1 = check_prod_migration_dryrun(repo_root, base, branch, db_url)
    results.append(("prod-migration-dryrun", c1))

    c2 = check_affected_sibling_tests(repo_root, base, branch, spec)
    results.append(("affected-sibling-tests", c2))

    c3 = check_migration_lint(repo_root, base, branch)
    results.append(("migration-lint", c3))

    requires_live_run = _spec_requires_live_run(repo_root, spec)
    proof = _load_live_run_proof(repo_root, spec) if requires_live_run else None
    c4 = check_live_run_proof(
        requires_live_run, proof, db_url=db_url, query_fn=_query_persisted_rows
    )
    results.append(("live-run-proof", c4))

    nonauth_note = config_authoritative_note(repo_root, base, branch, spec)

    any_failed = False
    no_migration = not _changed_migrations(repo_root, base, branch)
    no_shared = not _touched_src_modules(repo_root, base, branch)

    print("=" * 70)
    print(f"Builder close-out gate — spec {spec}")
    print(f"  base={base}  branch={branch}")
    print("=" * 70)

    for i, (name, failures) in enumerate(results, start=1):
        if failures:
            any_failed = True
            print(f"CHECK {i} [{name}]: FAIL ({len(failures)})")
            for f in failures:
                for j, line in enumerate(f.splitlines()):
                    prefix = "    - " if j == 0 else "      "
                    print(prefix + line)
            if name == "affected-sibling-tests" and nonauth_note:
                print("    - " + nonauth_note)
        else:
            note = ""
            if name == "prod-migration-dryrun" and no_migration:
                note = " (skip — no migration touched)"
            elif name == "affected-sibling-tests":
                if nonauth_note:
                    note = " — " + nonauth_note
                elif no_shared:
                    note = " (no shared source touched)"
            elif name == "migration-lint" and no_migration:
                note = " (skip — no migration touched)"
            elif name == "live-run-proof" and not requires_live_run:
                note = " (skip — spec not marked requires_live_run)"
            print(f"CHECK {i} [{name}]: PASS{note}")

    print("=" * 70)
    print("RESULT:", "FAIL" if any_failed else "PASS")
    return 1 if any_failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Builder close-out gate (spec 294): prod migration dry-run "
        "+ affected sibling test run + migration lint."
    )
    parser.add_argument("--base", required=True, help="base SHA to diff against")
    parser.add_argument("--branch", required=True, help="branch ref under review")
    parser.add_argument("--spec", required=True, help="spec id/slug, e.g. 294-foo")
    parser.add_argument("--repo-root", default=".", help="repo root (default .)")
    parser.add_argument(
        "--db-url",
        default=None,
        help="override SUPABASE_DB_URL (default: env or repo .env)",
    )
    args = parser.parse_args(argv)

    return run_all(
        repo_root=args.repo_root,
        base=args.base,
        branch=args.branch,
        spec=args.spec,
        db_url=args.db_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
