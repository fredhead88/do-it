"""Spec 362 — CHECK 4 (requires_live_run outcome verification).

AC2 (backend, pure — no DB): exercise check_live_run_proof's verdict logic
directly, plus the discovery helpers (_spec_requires_live_run,
_load_live_run_proof) against tmp_path fixtures.

AC3 (observed-data, live_db): a single pytest.mark.live_db test, guarded by
SUPABASE_DB_URL, that proves the end-to-end contract against REAL prod
Postgres — creates a throwaway probe table/row, confirms check_live_run_proof
PASSES when the row is really there and FAILS when it isn't, then cleans up.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# --- make the script-under-test importable (scripts/ on sys.path) ----------
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import builder_closeout_check as bcc  # noqa: E402

_HAVE_DB = bool(os.environ.get("SUPABASE_DB_URL"))
_skip_no_db = pytest.mark.skipif(
    not _HAVE_DB, reason="SUPABASE_DB_URL not set — skipping live_db tests"
)


def _valid_proof(**overrides):
    proof = {
        "spec_id": "362-complete-once-on-prod-ship-gate",
        "pipeline": "pipelines/amazon/jobs/xxx.py",
        "target_table": "public.catalog_xxx",
        "run_key": {"column": "run_id", "value": "abc-123"},
        "row_count": 1,
        "completed": True,
        "ran_at": "2026-07-05T12:00:00Z",
        "db_target": "postgres",
    }
    proof.update(overrides)
    return proof


# ---------------------------------------------------------------------------
# AC2 — check_live_run_proof verdict logic (pure, no DB)
# ---------------------------------------------------------------------------
class TestCheckLiveRunProofVerdict:
    def test_marker_true_valid_proof_no_requery_passes(self):
        failures = bcc.check_live_run_proof(True, _valid_proof())
        assert failures == []

    def test_marker_true_proof_none_fails(self):
        failures = bcc.check_live_run_proof(True, None)
        assert failures
        assert "no live-run proof artifact" in failures[0]

    def test_row_count_zero_fails(self):
        failures = bcc.check_live_run_proof(True, _valid_proof(row_count=0))
        assert any("row_count" in f for f in failures)

    def test_completed_false_fails(self):
        failures = bcc.check_live_run_proof(True, _valid_proof(completed=False))
        assert any("completed=true" in f for f in failures)

    def test_missing_target_table_fails(self):
        failures = bcc.check_live_run_proof(True, _valid_proof(target_table=""))
        assert any("target_table" in f for f in failures)

    def test_missing_run_key_fails(self):
        failures = bcc.check_live_run_proof(True, _valid_proof(run_key={}))
        assert any("run_key" in f for f in failures)

    def test_run_key_missing_value_fails(self):
        failures = bcc.check_live_run_proof(
            True, _valid_proof(run_key={"column": "run_id", "value": None})
        )
        assert any("run_key" in f for f in failures)

    def test_marker_false_skips_regardless_of_proof(self):
        assert bcc.check_live_run_proof(False, None) == []
        assert bcc.check_live_run_proof(False, _valid_proof(row_count=0)) == []

    def test_requery_zero_rows_fails(self):
        def stub_query_fn(db_url, target_table, run_key):
            return 0

        failures = bcc.check_live_run_proof(
            True, _valid_proof(), db_url="postgresql://fake", query_fn=stub_query_fn
        )
        assert any("found 0 rows" in f for f in failures)

    def test_requery_one_row_passes(self):
        def stub_query_fn(db_url, target_table, run_key):
            return 1

        failures = bcc.check_live_run_proof(
            True, _valid_proof(), db_url="postgresql://fake", query_fn=stub_query_fn
        )
        assert failures == []

    def test_requery_raises_fails(self):
        def stub_query_fn(db_url, target_table, run_key):
            raise RuntimeError("connection refused")

        failures = bcc.check_live_run_proof(
            True, _valid_proof(), db_url="postgresql://fake", query_fn=stub_query_fn
        )
        assert any("re-query raised" in f for f in failures)

    def test_structural_failure_skips_requery(self):
        calls = []

        def stub_query_fn(db_url, target_table, run_key):
            calls.append(1)
            return 1

        failures = bcc.check_live_run_proof(
            True,
            _valid_proof(row_count=0),
            db_url="postgresql://fake",
            query_fn=stub_query_fn,
        )
        assert failures  # structural failure present
        assert not calls  # re-query never attempted when structural checks fail


# ---------------------------------------------------------------------------
# AC2 — discovery helpers (_spec_requires_live_run, _load_live_run_proof)
# ---------------------------------------------------------------------------
class TestDiscoveryHelpers:
    def test_spec_requires_live_run_true(self, tmp_path):
        specs_dir = tmp_path / "docs" / "do-it" / "specs"
        specs_dir.mkdir(parents=True)
        (specs_dir / "999-foo-spec.md").write_text(
            "# Foo Spec\n\nstatus: registered\nrequires_live_run: true\n"
        )
        assert bcc._spec_requires_live_run(tmp_path, "999-foo") is True

    def test_spec_requires_live_run_false_no_marker(self, tmp_path):
        specs_dir = tmp_path / "docs" / "do-it" / "specs"
        specs_dir.mkdir(parents=True)
        (specs_dir / "999-foo-spec.md").write_text("# Foo Spec\n\nstatus: registered\n")
        assert bcc._spec_requires_live_run(tmp_path, "999-foo") is False

    def test_spec_requires_live_run_missing_doc(self, tmp_path):
        assert bcc._spec_requires_live_run(tmp_path, "999-nope") is False

    def test_spec_requires_live_run_case_insensitive(self, tmp_path):
        specs_dir = tmp_path / "docs" / "do-it" / "specs"
        specs_dir.mkdir(parents=True)
        (specs_dir / "999-foo-spec.md").write_text("Requires_Live_Run: TRUE\n")
        assert bcc._spec_requires_live_run(tmp_path, "999-foo") is True

    def test_load_live_run_proof_roundtrip(self, tmp_path):
        proofs_dir = tmp_path / "docs" / "do-it" / "live-run-proofs"
        proofs_dir.mkdir(parents=True)
        proof = _valid_proof()
        import json

        (proofs_dir / "362-complete-once-on-prod-ship-gate.json").write_text(
            json.dumps(proof)
        )
        loaded = bcc._load_live_run_proof(
            tmp_path, "362-complete-once-on-prod-ship-gate"
        )
        assert loaded == proof

    def test_load_live_run_proof_missing_returns_none(self, tmp_path):
        assert bcc._load_live_run_proof(tmp_path, "999-nope") is None

    def test_load_live_run_proof_fallback_glob_by_number(self, tmp_path):
        proofs_dir = tmp_path / "docs" / "do-it" / "live-run-proofs"
        proofs_dir.mkdir(parents=True)
        proof = _valid_proof()
        import json

        (proofs_dir / "999-some-other-slug.json").write_text(json.dumps(proof))
        loaded = bcc._load_live_run_proof(tmp_path, "999-different-slug")
        assert loaded == proof


# ---------------------------------------------------------------------------
# AC3 — live_db: end-to-end contract against REAL prod Postgres
# ---------------------------------------------------------------------------
@pytest.mark.live_db
@_skip_no_db
class TestLiveRunProofAgainstProd:
    PROBE_TABLE = "public._ci_live_run_proof_probe"
    PROBE_TOKEN = "362-ac3-probe"

    def test_requery_confirms_and_rejects_real_prod_rows(self):
        import psycopg2

        db_url = os.environ["SUPABASE_DB_URL"]
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS public._ci_live_run_proof_probe "
                    "(run_id text, note text)"
                )
                cur.execute(
                    "DELETE FROM public._ci_live_run_proof_probe WHERE run_id = %s",
                    (self.PROBE_TOKEN,),
                )
                cur.execute(
                    "INSERT INTO public._ci_live_run_proof_probe (run_id, note) "
                    "VALUES (%s, %s)",
                    (self.PROBE_TOKEN, "spec-362 CHECK4 AC3 probe"),
                )
            conn.commit()

            proof = {
                "spec_id": "362-complete-once-on-prod-ship-gate",
                "pipeline": "pipelines/amazon/jobs/xxx.py",
                "target_table": self.PROBE_TABLE,
                "run_key": {"column": "run_id", "value": self.PROBE_TOKEN},
                "row_count": 1,
                "completed": True,
                "ran_at": "2026-07-05T12:00:00Z",
                "db_target": "postgres",
            }

            # Positive: real re-query finds the persisted row -> PASS.
            failures = bcc.check_live_run_proof(
                True, proof, db_url=db_url, query_fn=bcc._query_persisted_rows
            )
            assert failures == [], (
                f"expected PASS against real persisted row "
                f"({self.PROBE_TABLE}.run_id={self.PROBE_TOKEN!r}), got: {failures}"
            )
            print(
                f"AC3 evidence: {self.PROBE_TABLE} run_id={self.PROBE_TOKEN!r} "
                "confirmed persisted via real prod re-query"
            )

            # Negative: same proof but a run_key value matching nothing -> FAIL.
            missing_proof = dict(proof)
            missing_proof["run_key"] = {
                "column": "run_id",
                "value": "362-ac3-probe-DOES-NOT-EXIST",
            }
            neg_failures = bcc.check_live_run_proof(
                True,
                missing_proof,
                db_url=db_url,
                query_fn=bcc._query_persisted_rows,
            )
            assert neg_failures, "expected FAIL when re-query matches 0 rows"
            assert any("found 0 rows" in f for f in neg_failures)
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM public._ci_live_run_proof_probe WHERE run_id = %s",
                        (self.PROBE_TOKEN,),
                    )
                    cur.execute("DROP TABLE IF EXISTS public._ci_live_run_proof_probe")
                conn.commit()
            finally:
                conn.close()
