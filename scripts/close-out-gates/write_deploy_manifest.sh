#!/usr/bin/env bash
# write_deploy_manifest.sh — deploy manifest (reference implementation).
#
# "Is it live? which host? which sha?" gets re-derived on every review tick, and
# host confusion (which box actually serves prod?) burns dark ticks. Run this on
# every deploy: it writes ONE manifest at a known bus path so the reviewer (rev)
# READS deploy ground-truth instead of re-probing ssh/curl each tick.
#
# Captures {master_sha, prod_serving_sha, vercel_ready_sha, backend_restart_ts,
# alembic_head, prod_host, match} and VERIFIES master_sha against the actually-
# serving sha. Adapt the probe to your stack via env — this reference reads a
# JSON {"sha","deployed_at"} from a /version endpoint over ssh+curl:
#   DEPLOY_SERVER   user@host orc deploys to        (REQUIRED — no default)
#   VERSION_URL     served version endpoint          (default http://127.0.0.1:8000/version)
#   ALEMBIC_INI     migration config, relative to api/ (optional; skipped if unset/absent)
#   MANIFEST_PATH   where to write the manifest       (default ~/.claude/deploy-manifest.json)
#
# Usage:  write_deploy_manifest.sh [--vercel-sha <sha>]
# Exit:   0 if master_sha == prod_serving_sha (deploy landed); 1 on mismatch/unreachable.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

MANIFEST="${MANIFEST_PATH:-$HOME/.claude/deploy-manifest.json}"
SERVER="${DEPLOY_SERVER:?set DEPLOY_SERVER=user@host (the box that serves prod)}"
VERSION_URL="${VERSION_URL:-http://127.0.0.1:8000/version}"
VERCEL_SHA="unknown"
[ "${1:-}" = "--vercel-sha" ] && VERCEL_SHA="${2:-unknown}"

MASTER_SHA="$(git rev-parse --short HEAD)"
TS="$(date -u +%FT%TZ)"

# local alembic head(s) — optional. A clean deploy has exactly one.
ALEMBIC_HEAD="unknown"
if [ -n "${ALEMBIC_INI:-}" ] && [ -f "api/$ALEMBIC_INI" ]; then
  ALEMBIC_HEAD="$(cd api && "$REPO_ROOT/.venv/bin/alembic" -c "$ALEMBIC_INI" heads 2>/dev/null \
    | awk '{print $1}' | tr '\n' ',' | sed 's/,$//')"
  [ -z "$ALEMBIC_HEAD" ] && ALEMBIC_HEAD="unknown"
fi

# prod serving sha — ssh to the host, curl the served /version (the same path your
# deploy verifies on). /version is typically unauth on the box, edge-walled publicly.
PROD_SHA="unknown"; PROD_HOST="${SERVER#*@}"; DEPLOYED_AT="unknown"
body="$(ssh -o ConnectTimeout=12 -o BatchMode=yes "$SERVER" \
  "curl -sf '$VERSION_URL' 2>/dev/null" 2>/dev/null)" || body=""
if [ -n "$body" ]; then
  PROD_SHA="$(printf '%s' "$body" | grep -oE '"sha"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"sha"[^"]*"//;s/"$//')"
  DEPLOYED_AT="$(printf '%s' "$body" | grep -oE '"deployed_at"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"deployed_at"[^"]*"//;s/"$//')"
  [ -z "$PROD_SHA" ] && PROD_SHA="unknown"
fi

MATCH="no"
if [ "$PROD_SHA" != "unknown" ]; then
  n=${#MASTER_SHA}; [ "${#PROD_SHA}" -lt "$n" ] && n=${#PROD_SHA}
  [ "${MASTER_SHA:0:$n}" = "${PROD_SHA:0:$n}" ] && MATCH="yes"
fi

tmp="$MANIFEST.tmp.$$"
cat > "$tmp" <<EOF
{
  "written_at": "$TS",
  "master_sha": "$MASTER_SHA",
  "prod_serving_sha": "$PROD_SHA",
  "vercel_ready_sha": "$VERCEL_SHA",
  "backend_restart_ts": "$DEPLOYED_AT",
  "alembic_head": "$ALEMBIC_HEAD",
  "prod_host": "$PROD_HOST",
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
