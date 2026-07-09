#!/usr/bin/env bash
# write_deploy_manifest.sh — F2 deploy manifest (ORC-RESET STEP 8).
# NOTE: reference close-out gate — adapt DEPLOY_SERVER / cron prefix / health path to your deploy.
#
# "Is it live? which host? which sha?" was re-derived every rev tick; Hetzner-vs-droplet
# confusion cost 3 dark ticks. orc runs this on every deploy; it writes ONE manifest at a
# known bus path so rev READS deploy ground-truth instead of re-probing.
#
# Captures {master_sha, prod_serving_sha, vercel_ready_sha, backend_restart_ts,
# alembic_head, prod_host, cron_inventory, match} — and VERIFIES master_sha against the
# actually-serving /version sha (the acceptance: manifest sha == serving sha).
#
# Usage:  write_deploy_manifest.sh [--vercel-sha <sha>]
# Exit:   0 if master_sha == prod_serving_sha (deploy landed); 1 on mismatch/unreachable.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Default preserves prod path; DEPLOY_MANIFEST_PATH lets the ship hook (spec 281 R1)
# and tests point the manifest at a sandbox without touching the real one.
MANIFEST="${DEPLOY_MANIFEST_PATH:-$HOME/.claude/deploy-manifest.json}"
# Backend prod = the host orc deploys to (deploy.sh SERVER). /version is UNAUTH on the
# box but 401-walled at the public edge — so read it the way deploy.sh verifies: ssh +
# curl localhost. Overridable, defaults to the droplet.
SERVER="${DEPLOY_SERVER:-<user>@<your-host>}"
VERCEL_SHA="unknown"
[ "${1:-}" = "--vercel-sha" ] && VERCEL_SHA="${2:-unknown}"

MASTER_SHA="$(git rev-parse --short HEAD)"
MASTER_COMMITTED_AT="$(git show -s --format=%cI "$MASTER_SHA" 2>/dev/null || echo unknown)"
TS="$(date -u +%FT%TZ)"

# local alembic head(s) — a clean deploy has exactly one (script_location is relative
# to api/, so invoke from there)
ALEMBIC_HEAD="$(cd api && "$REPO_ROOT/.venv/bin/alembic" -c alembic_supabase.ini heads 2>/dev/null \
  | awk '{print $1}' | tr '\n' ',' | sed 's/,$//')"
[ -z "$ALEMBIC_HEAD" ] && ALEMBIC_HEAD="unknown"

# prod serving sha — ssh to the backend host, curl the local /version (same as deploy.sh)
PROD_SHA="unknown"; PROD_HOST="${SERVER#*@}"; DEPLOYED_AT="unknown"
body="$(ssh -o ConnectTimeout=12 -o BatchMode=yes "$SERVER" \
  "curl -sf http://127.0.0.1:8000/version 2>/dev/null" 2>/dev/null)" || body=""
if [ -n "$body" ]; then
  PROD_SHA="$(printf '%s' "$body" | grep -oE '"sha"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"sha"[^"]*"//;s/"$//')"
  DEPLOYED_AT="$(printf '%s' "$body" | grep -oE '"deployed_at"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"deployed_at"[^"]*"//;s/"$//')"
  [ -z "$PROD_SHA" ] && PROD_SHA="unknown"
fi

# match? master is short-sha; prod sha may be short or full — compare on the shorter length
MATCH="no"
if [ "$PROD_SHA" != "unknown" ]; then
  n=${#MASTER_SHA}; [ "${#PROD_SHA}" -lt "$n" ] && n=${#PROD_SHA}
  [ "${MASTER_SHA:0:$n}" = "${PROD_SHA:0:$n}" ] && MATCH="yes"
fi

# P3 (rev prod-reachability): installed cron inventory from PROD. Gives rev sight into what
# crons are actually wired on the box — name + schedule + INTERPRETER PATH per active line.
# The interpreter is the signal: a cron pointing at `.venv` instead of `venv` (the spec-195
# class) ships dead and graded "done"; here it's one field in a file rev already reads. Read
# the same way as /version (ssh + read /etc/cron.d). Best-effort: on ssh/parse failure
# cron_inventory is [] — it never breaks the manifest or its exit code. (Per-job last-run
# timestamps are added below as cron_last_run, sourced from P1's cron_runs table.)
CRON_INVENTORY="[]"
cron_raw="$(ssh -o ConnectTimeout=12 -o BatchMode=yes "$SERVER" \
  'for f in /etc/cron.d/${DOIT_CRON_PREFIX:-doit}*; do [ -f "$f" ] || continue; b=$(basename "$f"); grep -hE "^[0-9*@]" "$f" 2>/dev/null | sed "s#^#${b}\t#"; done' 2>/dev/null)" || cron_raw=""
if [ -n "$cron_raw" ]; then
  parsed="$(printf '%s' "$cron_raw" | "$REPO_ROOT/.venv/bin/python" - <<'PY' 2>/dev/null
import sys, json, re
rows = []
for line in sys.stdin:
    line = line.rstrip("\n")
    if "\t" not in line or not line.strip():
        continue
    cronfile, rest = line.split("\t", 1)
    parts = rest.split(None, 6)          # 5 schedule fields + user + command
    if len(parts) < 7:
        continue
    schedule, cmd = " ".join(parts[:5]), parts[6]
    m = re.search(r"\S*python[0-9.]*", cmd)
    interp = m.group(0) if m else ("bash" if re.search(r"\bbash\b|\.sh\b", cmd) else "other")
    idm = re.search(r"${DOIT_CRON_PREFIX:-doit}-([a-z0-9._-]+)\.lock", cmd)
    if idm:
        ident = idm.group(1)
    else:
        mm = re.search(r"-m\s+([a-zA-Z0-9_.]+)", cmd)
        ident = mm.group(1) if mm else cronfile
    rows.append({"file": cronfile, "id": ident, "schedule": schedule, "interpreter": interp})
print(json.dumps(rows, separators=(",", ":")))
PY
)"
  [ -n "$parsed" ] && CRON_INVENTORY="$parsed"
fi

# P1 (rev prod-reachability): per-job LAST-RUN attestation from the cron_runs table.
# Pairs with the P3 cron_inventory above — inventory says "this cron is wired with
# this interpreter"; cron_last_run says "and it actually fired at T with exit/status".
# A job present in cron_inventory but ABSENT from (or stale in) cron_last_run is the
# silent-death signature rev otherwise had to take on the build worker's word.
# Queried locally via the repo venv against SUPABASE_DB_URL (.env in REPO_ROOT).
# Best-effort: DB unreachable / table not yet migrated → {} (never breaks the manifest).
CRON_LAST_RUN="{}"
clr="$("$REPO_ROOT/.venv/bin/python" - "$REPO_ROOT" <<'PY' 2>/dev/null
import sys, os, json
repo_root = sys.argv[1]
try:
    sys.path.insert(0, repo_root)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(repo_root, ".env"))
    import psycopg2, psycopg2.extras
    from scripts.ops.cron_attest import fetch_cron_last_run
    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit(0)
    conn = psycopg2.connect(dsn, connect_timeout=10,
                            cursor_factory=psycopg2.extras.RealDictCursor)
    # spec 405: fetch_cron_last_run is the single shared code path between the
    # manifest and the test suite — includes rows_written / row_fresh so a
    # status='ok'-but-zero-rows job is no longer misread as fresh.
    out = fetch_cron_last_run(conn)
    conn.close()
    print(json.dumps(out, separators=(",", ":")))
except Exception:
    raise SystemExit(0)
PY
)"
[ -n "$clr" ] && CRON_LAST_RUN="$clr"

tmp="$MANIFEST.tmp.$$"
cat > "$tmp" <<EOF
{
  "written_at": "$TS",
  "master_sha": "$MASTER_SHA",
  "master_committed_at": "$MASTER_COMMITTED_AT",
  "prod_serving_sha": "$PROD_SHA",
  "vercel_ready_sha": "$VERCEL_SHA",
  "backend_restart_ts": "$DEPLOYED_AT",
  "alembic_head": "$ALEMBIC_HEAD",
  "prod_host": "$PROD_HOST",
  "cron_inventory": $CRON_INVENTORY,
  "cron_last_run": $CRON_LAST_RUN,
  "match": "$MATCH"
}
EOF
mv "$tmp" "$MANIFEST"
cat "$MANIFEST"

if [ "$MATCH" = "yes" ]; then
  echo "DEPLOY MANIFEST OK — prod serving $PROD_SHA on $PROD_HOST == master $MASTER_SHA"
  exit 0
fi
echo "DEPLOY MANIFEST MISMATCH — master=$MASTER_SHA prod_serving=$PROD_SHA host=$PROD_HOST (deploy not landed / wrong host)" >&2
exit 1
