#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../liveness.sh"
TMP="$(mktemp -d)"; fail=0

# Stale PROGRESS.jsonl -> VERIFIER_DOWN flag written
mkdir -p "$TMP/runs/2026-06-08"
: > "$TMP/runs/2026-06-08/PROGRESS.jsonl"; touch -d '200 minutes ago' "$TMP/runs/2026-06-08/PROGRESS.jsonl"
LIVENESS_FLAG="$TMP/flags" VL_RUNS_DIR="$TMP/runs" VERIFIER_STALE_MIN=90 bash "$SCRIPT" verifier
if grep -rq VERIFIER_DOWN "$TMP/flags" 2>/dev/null; then echo "ok: VERIFIER_DOWN raised"; else echo "FAIL: no VERIFIER_DOWN"; fail=1; fi

# Fresh PROGRESS -> no flag
rm -rf "$TMP/flags"; touch "$TMP/runs/2026-06-08/PROGRESS.jsonl"
LIVENESS_FLAG="$TMP/flags" VL_RUNS_DIR="$TMP/runs" VERIFIER_STALE_MIN=90 bash "$SCRIPT" verifier
if grep -rq VERIFIER_DOWN "$TMP/flags" 2>/dev/null; then echo "FAIL: false VERIFIER_DOWN"; fail=1; else echo "ok: fresh -> silent"; fi

rm -rf "$TMP"; exit $fail
