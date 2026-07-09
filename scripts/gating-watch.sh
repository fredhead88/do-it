#!/usr/bin/env bash
# scripts/gating-watch.sh — Spec 300: pane-independent detached close-out grader loop.
#
# Purpose: For each ~/.claude/build-lane/*.gating.md, runs the spec-296 blind
# close-out grader against the PUSHED branch (fresh checkout at ready_sha —
# NOT the builder's worktree, which may be gone), then atomically applies the
# verdict:
#   PASS → .gating → .ready  + ledger gating→ready
#   FAIL → .gating → .rework + ledger gating→rework
#
# Liveness backstop (spec 297): heartbeat per grade; stall flag + ALARM_CMD if
# inert; reap of a stuck .gating after GATING_MAX_REDISPATCH retries.
#
# Seams (all ENV-overridable — tests inject stubs, no real LLM/git/cron needed):
#
#   LANE_DIR          default: $HOME/.claude/build-lane
#   LIVENESS_DIR      default: $HOME/.claude/ledger/liveness
#   LEDGER_SET_CMD    default: ${PYTHON:-python3}
#                              ${REPO_ROOT}/scripts/spec_ledger.py set
#                     invoked: $LEDGER_SET_CMD <spec_id> <status> --by gating-watch ...
#   GRADER_CMD        default: claude -p
#                     invoked: $GRADER_CMD "<prompt>" → verdict JSON on stdout
#   VERDICT_CMD       default: ${PYTHON:-python3}
#                              ${REPO_ROOT}/scripts/builder_closeout_verdict.py
#                     invoked: echo "$json" | $VERDICT_CMD → prints ready/regrade; exit 0=ready
#   CLOSEOUT_CHECK_CMD  default: ${PYTHON:-python3}
#                                ${REPO_ROOT}/scripts/builder_closeout_check.py
#                     invoked: $CLOSEOUT_CHECK_CMD --base <sha> --branch <b> --spec <id>
#   CHECKOUT_CMD      default: internal git-fetch + detached worktree at ready_sha
#                     if set:  $CHECKOUT_CMD <repo_root> <ready_sha> <branch>
#                              → prints tmpdir on stdout; caller rm -rf's it
#   REPO_ROOT         default: ${REPO_ROOT}
#   GATING_STALE_SECS default: 1800  (30 min)
#   GATING_MAX_REDISPATCH default: 3
#   ALARM_CMD         default: ${PYTHON:-python3}
#                              ${REPO_ROOT}/scripts/ops/send_idle_stall_alert.py
#   LOCK_FILE         default: /tmp/gating-watch.lock
#
# Real dependency: spec_ledger.py set requires 'gating' status in VALID_STATUS
# (added by a sibling spec worker — tests are independent via LEDGER_SET_CMD stub).
# Cron install + REQUIRED_ACTIVATIONS are integrator activation steps, not this script.

set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-python3}"

# ── PATH hardening (spec 300 corrective, 2026-07-03) ──────────────────────────
# Cron runs with a minimal PATH (/usr/bin:/bin) that excludes ~/.local/bin, where
# the `claude` CLI (GRADER_CMD default) lives. Without this the grader invocation
# fails "command not found" → every .gating spec silently wedges (the 2026-07-03
# 332 stall, ~3h). Prepend the user bin dir so `claude`/`codex` resolve under cron.
export PATH="$HOME/.local/bin:$PATH"

# ── Seams ─────────────────────────────────────────────────────────────────────
LANE_DIR="${LANE_DIR:-$HOME/.claude/build-lane}"
LIVENESS_DIR="${LIVENESS_DIR:-$HOME/.claude/ledger/liveness}"
LEDGER_SET_CMD="${LEDGER_SET_CMD:-${PYTHON:-python3} ${REPO_ROOT}/scripts/spec_ledger.py set}"
GRADER_CMD="${GRADER_CMD:-claude -p}"
VERDICT_CMD="${VERDICT_CMD:-${PYTHON:-python3} ${REPO_ROOT}/scripts/builder_closeout_verdict.py}"
REASON_COMPOSE_CMD="${REASON_COMPOSE_CMD:-${PYTHON:-python3} ${REPO_ROOT}/scripts/builder_closeout_verdict.py --compose-rework-reason}"
CLOSEOUT_CHECK_CMD="${CLOSEOUT_CHECK_CMD:-${PYTHON:-python3} ${REPO_ROOT}/scripts/builder_closeout_check.py}"
CHECKOUT_CMD="${CHECKOUT_CMD:-}"
GATING_STALE_SECS="${GATING_STALE_SECS:-1800}"
GATING_MAX_REDISPATCH="${GATING_MAX_REDISPATCH:-3}"
ALARM_CMD="${ALARM_CMD:-true}"   # no-op default; set to your own alerter command
LOCK_FILE="${LOCK_FILE:-/tmp/gating-watch.lock}"

# ── Single-instance lock ───────────────────────────────────────────────────────
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

# ── Shared libs (spec 404: R3) ─────────────────────────────────────────────────
# Source load_env.sh and gating_preflight.sh so CLOSEOUT_CHECK_CMD inherits env
# (e.g. SUPABASE_DB_URL) from ${REPO_ROOT}/.env.  Guard each source so that if
# the libs are absent the script still runs — only the env-load is skipped.
_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
[[ -f "${_SELF_DIR}/lib/load_env.sh" ]] && source "${_SELF_DIR}/lib/load_env.sh" \
    || echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") gating-watch: WARNING lib/load_env.sh not found — env not loaded" >&2
# shellcheck source=/dev/null
[[ -f "${_SELF_DIR}/lib/gating_preflight.sh" ]] && source "${_SELF_DIR}/lib/gating_preflight.sh" \
    || echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") gating-watch: WARNING lib/gating_preflight.sh not found — preflight skipped" >&2
# Load env once so subprocesses inherit it; gating_load_env returns 0 even if .env absent.
# If gating_preflight.sh was missing the function won't exist; guard with 'declare -f'.
if declare -f gating_load_env >/dev/null 2>&1; then
    gating_load_env || true
fi

# ── Helpers ────────────────────────────────────────────────────────────────────
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

mkdir -p "$LIVENESS_DIR"

COMPLETIONS_LOG="${LIVENESS_DIR}/gating-watch-completions"
STALL_FLAG="${LIVENESS_DIR}/GATING_WATCH_STALL"

_log() { echo "$(ts) gating-watch: $*" >&2; }

_append_completion() {
    local spec_id="$1" result="$2"
    echo "$(ts) gating-watch graded ${spec_id} result=${result}" >> "$COMPLETIONS_LOG"
    rm -f "$STALL_FLAG"
}

# _do_checkout <repo_root> <sha> <branch> → prints tmpdir path on stdout
_do_checkout() {
    local repo_root="$1" sha="$2" branch="$3" tmpdir
    if [[ -n "$CHECKOUT_CMD" ]]; then
        "$CHECKOUT_CMD" "$repo_root" "$sha" "$branch"
    else
        tmpdir=$(mktemp -d)
        git -C "$repo_root" fetch --quiet origin "${branch}" 2>/dev/null || true
        git -C "$repo_root" worktree add --detach --quiet "$tmpdir" "$sha"
        echo "$tmpdir"
    fi
}

# _parse_field <file> <key> → trims whitespace from first matching "key: value" line
_parse_field() {
    sed -n "s/^${2}:[[:space:]]*//p" "$1" | head -1 | tr -d '[:space:]'
}

# _escalate_malformed <file> — spec 404 R1b: distinct alarm for missing frontmatter fields.
# Writes GATING_MALFORMED board flag, fires deduped alarm, records completion.
# Never silent-skips; caller must return 0 after invoking this.
_escalate_malformed() {
    local f="$1"
    local id missing="" field
    id=$(basename "$f")

    # Recompute which required fields are missing
    local _sid _bsha _rsha _br
    _sid=$(_parse_field "$f" "spec_id")
    _bsha=$(_parse_field "$f" "base_sha")
    _rsha=$(_parse_field "$f" "ready_sha")
    _br=$(_parse_field "$f" "branch")
    [[ -z "$_sid"  ]] && missing="${missing:+$missing }spec_id"
    [[ -z "$_bsha" ]] && missing="${missing:+$missing }base_sha"
    [[ -z "$_rsha" ]] && missing="${missing:+$missing }ready_sha"
    [[ -z "$_br"   ]] && missing="${missing:+$missing }branch"

    # Durable, distinct board-flag record (overwrite-safe — last write wins)
    echo "$(ts) malformed $f missing: $missing" >> "${LIVENESS_DIR}/GATING_MALFORMED"

    # Deduped alarm — fire only if sentinel absent or older than GATING_STALE_SECS
    local sentinel="${LIVENESS_DIR}/.malformed-alarm-${id}"
    local do_alarm=true
    if [[ -f "$sentinel" ]]; then
        local s_mtime s_age
        s_mtime=$(stat -c %Y "$sentinel" 2>/dev/null || echo 0)
        s_age=$(( $(date +%s) - s_mtime ))
        (( s_age <= GATING_STALE_SECS )) && do_alarm=false
    fi
    if [[ "$do_alarm" == "true" ]]; then
        # shellcheck disable=SC2086
        $ALARM_CMD --reason malformed-frontmatter --file "$f" --missing "$missing" 2>/dev/null || true
        touch "$sentinel"
    fi

    _log "MALFORMED $f: missing required frontmatter ($missing) → distinct alarm raised (NOT silent SKIP)"
    _append_completion "$id" "malformed"
}

# _hold_infra <file> <spec_id> <reason> — spec 404 R2: env/infra could-not-run → HOLD.
# Does NOT create .rework; does NOT call ledger rework; leaves .gating in place.
# Writes GATING_INFRA_HELD board flag, fires deduped alarm, records completion.
_hold_infra() {
    local f="$1" spec_id="$2" reason="$3"

    # Durable, distinct board-flag record
    echo "$(ts) infra-held $spec_id reason: $reason" >> "${LIVENESS_DIR}/GATING_INFRA_HELD"

    # Deduped alarm
    local sentinel="${LIVENESS_DIR}/.infra-alarm-${spec_id}"
    local do_alarm=true
    if [[ -f "$sentinel" ]]; then
        local s_mtime s_age
        s_mtime=$(stat -c %Y "$sentinel" 2>/dev/null || echo 0)
        s_age=$(( $(date +%s) - s_mtime ))
        (( s_age <= GATING_STALE_SECS )) && do_alarm=false
    fi
    if [[ "$do_alarm" == "true" ]]; then
        # shellcheck disable=SC2086
        $ALARM_CMD --reason infra-could-not-run --spec "$spec_id" --detail "$reason" 2>/dev/null || true
        touch "$sentinel"
    fi

    _log "HOLD $spec_id: close-out check COULD NOT RUN (env/infra: $reason) → held as .gating, NOT rework"
    _append_completion "$spec_id" "infra-held"
}

# ── Per-spec grader ────────────────────────────────────────────────────────────
_grade_spec() {
    local f="$1"
    local spec_id base_sha ready_sha branch card_path

    spec_id=$(_parse_field "$f" "spec_id")
    base_sha=$(_parse_field "$f" "base_sha")
    ready_sha=$(_parse_field "$f" "ready_sha")
    branch=$(_parse_field "$f" "branch")
    card_path=$(_parse_field "$f" "card_path")

    if [[ -z "$spec_id" || -z "$base_sha" || -z "$ready_sha" || -z "$branch" ]]; then
        _escalate_malformed "$f"
        return 0
    fi

    local marker="${LIVENESS_DIR}/.grading-${spec_id}"
    local redispatch_file="${LIVENESS_DIR}/.grading-${spec_id}.redispatch"

    # ── In-progress marker check ───────────────────────────────────────────────
    if [[ -f "$marker" ]]; then
        local marker_pid
        marker_pid=$(awk 'NR==1{print $1}' "$marker" 2>/dev/null || echo 0)

        if kill -0 "$marker_pid" 2>/dev/null; then
            _log "SKIP $spec_id: already grading (PID $marker_pid)"
            return 0
        fi

        # Process dead — check marker age
        local marker_mtime now_ts marker_age
        marker_mtime=$(stat -c %Y "$marker" 2>/dev/null || echo 0)
        now_ts=$(date +%s)
        marker_age=$(( now_ts - marker_mtime ))

        if (( marker_age > GATING_STALE_SECS )); then
            # Stale dead marker → check redispatch counter
            local redispatch_count
            redispatch_count=$(cat "$redispatch_file" 2>/dev/null || echo 0)

            if (( redispatch_count >= GATING_MAX_REDISPATCH )); then
                _log "BOUNCE $spec_id: runner inert after $redispatch_count redispatches — rework"
                local tmp_rework
                tmp_rework="${f%.gating.md}.rework.md.tmp$$"
                {
                    cat "$f"
                    printf '\nrework_reason: gating-runner-inert\n'
                } > "$tmp_rework"
                mv "$tmp_rework" "${f%.gating.md}.rework.md"
                rm -f "$f"
                # shellcheck disable=SC2086
                $LEDGER_SET_CMD "$spec_id" rework --by gating-watch \
                    --reason "gating-runner-inert" 2>/dev/null || true
                echo "$(ts) gating-watch: stall detected for $spec_id" > "$STALL_FLAG"
                rm -f "$marker" "$redispatch_file"
                _append_completion "$spec_id" "reaped"
                return 0
            else
                redispatch_count=$(( redispatch_count + 1 ))
                echo "$redispatch_count" > "$redispatch_file"
                _log "REDISPATCH $spec_id (attempt $redispatch_count)"
                rm -f "$marker"
                # fall through to re-grade
            fi
        else
            # Recently dead marker (race/fast-fail) — remove stale entry and re-grade
            rm -f "$marker"
        fi
    fi

    # ── Write in-progress marker (PID + timestamp) ─────────────────────────────
    echo "$$ $(ts)" > "$marker"

    # ── Heartbeat ─────────────────────────────────────────────────────────────
    echo "$(ts) grading $spec_id" > "${LIVENESS_DIR}/gating-watch-heartbeat"

    # ── Checkout at ready_sha ──────────────────────────────────────────────────
    local tmpdir=""
    if ! tmpdir=$(_do_checkout "$REPO_ROOT" "$ready_sha" "$branch" 2>/dev/null); then
        _log "FAIL $spec_id: checkout failed"
        rm -f "$marker"
        return 0
    fi

    # ── Mechanical checks (spec 294 / spec 404 R2: capture output for could-not-run) ──
    local check_out="${tmpdir}/closeout.out"
    local checks_result="pass"
    # shellcheck disable=SC2086
    if ! (cd "$tmpdir" && $CLOSEOUT_CHECK_CMD \
            --base "$base_sha" --branch "$branch" --spec "$spec_id") >"$check_out" 2>&1; then
        checks_result="fail"
    fi

    # spec 404 R2: if checks failed, classify could-not-run → HOLD (not rework)
    if [[ "$checks_result" == "fail" ]]; then
        local cnr_reason=""
        if declare -f gating_couldnotrun_reason >/dev/null 2>&1 && \
           cnr_reason=$(gating_couldnotrun_reason "$(cat "$check_out" 2>/dev/null)"); then
            _hold_infra "$f" "$spec_id" "$cnr_reason"
            rm -f "$marker"
            [[ -n "$tmpdir" ]] && rm -rf "$tmpdir"
            return 0
        fi
    fi

    # ── Pre-gate: import check (best-effort; vacuous-pass if absent) ───────────
    local pre_gate_result="pass"
    local python_bin="${REPO_ROOT}/.venv/bin/python"
    if [[ -f "${tmpdir}/api/main.py" && -x "${python_bin}" ]]; then
        if ! PYTHONPATH="${tmpdir}" "${python_bin}" \
             -c "from api.main import app" 2>/dev/null; then
            pre_gate_result="fail"
        fi
    fi

    # ── Card contents ──────────────────────────────────────────────────────────
    local card_contents=""
    if [[ -n "$card_path" && -f "$card_path" ]]; then
        card_contents=$(cat "$card_path")
    fi

    # ── Build grader prompt ────────────────────────────────────────────────────
    local prompt
    prompt=$(cat <<PROMPT
You are the blind close-out grader (spec 296). Review the build for the spec below without access to the builder's session.

spec_id: ${spec_id}
branch: ${branch}
base_sha: ${base_sha}
ready_sha: ${ready_sha}

Pre-computed mechanical results (already verified externally):
  closeout_checks: ${checks_result}
  pre_gates: ${pre_gate_result}

Review card draft:
${card_contents}

IMPORTANT — observed-data AC handling:
Acceptance criteria tagged [observed-data], [cron], or [financial] are owed to
post-merge verification and are NOT yours to verify. A blind prompt-only grader
cannot re-run a live-PG data job.
- Judge matches_intent ONLY on code-verifiable [ui]/[backend] ACs plus card
  soundness plus the pre-computed mechanical gates. Do NOT count an
  [observed-data]/[cron]/[financial] AC against matches_intent.
- If ALL unmet ACs are [observed-data]/[cron]/[financial] and the code-verifiable
  ACs are satisfied, set matches_intent to true; the verdict script will return
  "owed-data" when owed_data_acs is non-empty and matches_intent is true.
- You MUST return an owed_data_acs array naming each such deferred AC
  (verbatim AC id + one-line text). Use empty [] when there are none.

Return ONLY a single JSON object — no prose before or after — with these exact keys:
{
  "pre_gates": {"python_import": "${pre_gate_result}"},
  "checks": {"mechanical": "${checks_result}"},
  "matches_intent": <true|false>,
  "matches_intent_reason": "<one sentence>",
  "card_ok": <true|false>,
  "card_ok_reason": "<one sentence>",
  "owed_data_acs": [<deferred AC strings, [] if none>]
}
PROMPT
)

    # ── Call grader ────────────────────────────────────────────────────────────
    # NEVER swallow grader stderr (2026-07-03 corrective): the old `2>/dev/null`
    # hid the real "command not found" and wedged 332 for ~3h with only an opaque
    # "grader command failed". Capture stderr to a temp file and echo it into the
    # log on failure so the actual error is always diagnosable.
    local verdict_json="" grader_err="$tmpdir/grader.stderr" grader_rc=0
    # shellcheck disable=SC2086
    verdict_json=$($GRADER_CMD "$prompt" 2>"$grader_err") || grader_rc=$?
    if [ "$grader_rc" -ne 0 ]; then
        _log "FAIL $spec_id: grader command failed (exit=$grader_rc) — stderr: $(head -c 500 "$grader_err" 2>/dev/null | tr '\n' ' ')"
        rm -f "$marker"
        rm -rf "$tmpdir"
        return 0
    fi

    # ── Decide via verdict script ──────────────────────────────────────────────
    local decision=""
    local exit_code=0
    # shellcheck disable=SC2086
    decision=$(printf '%s' "$verdict_json" | $VERDICT_CMD 2>/dev/null) || exit_code=$?

    local graded_at
    graded_at=$(ts)

    # ── Extract reason strings + owed_data_acs from verdict JSON ───────────────
    local mi_reason co_reason owed_json owed_yaml
    mi_reason=$(printf '%s' "$verdict_json" | python3 -c \
        'import sys,json; d=json.load(sys.stdin); print(d.get("matches_intent_reason",""))' \
        2>/dev/null || true)
    mi_reason="${mi_reason:-}"
    co_reason=$(printf '%s' "$verdict_json" | python3 -c \
        'import sys,json; d=json.load(sys.stdin); print(d.get("card_ok_reason",""))' \
        2>/dev/null || true)
    co_reason="${co_reason:-}"
    owed_json=$(printf '%s' "$verdict_json" | python3 -c \
        'import sys,json; print(json.dumps(json.load(sys.stdin).get("owed_data_acs") or []))' \
        2>/dev/null || echo '[]')
    owed_json="${owed_json:-[]}"
    owed_yaml=$(printf '%s' "$verdict_json" | python3 -c \
        'import sys,json; acs=json.load(sys.stdin).get("owed_data_acs") or []; print("\n".join(f"  - \"{a}\"" for a in acs))' \
        2>/dev/null || true)
    owed_yaml="${owed_yaml:-}"

    if [[ $exit_code -eq 0 && "$decision" == "ready" ]]; then
        # ── PASS: .gating → .ready ────────────────────────────────────────────
        _log "PASS $spec_id → ready"
        local tmp_ready
        tmp_ready="${f%.gating.md}.ready.md.tmp$$"
        {
            cat "$f"
            printf '\ngraded_by: gating-watch\n'
            printf 'graded_at: %s\n' "$graded_at"
        } > "$tmp_ready"
        mv "$tmp_ready" "${f%.gating.md}.ready.md"
        rm -f "$f"
        # shellcheck disable=SC2086
        $LEDGER_SET_CMD "$spec_id" ready --by gating-watch \
            --field "graded_by=gating-watch" \
            --field "graded_at=${graded_at}" 2>/dev/null || true
        _append_completion "$spec_id" "pass"

    elif [[ $exit_code -eq 0 && "$decision" == "owed-data" ]]; then
        # ── OWED-DATA: mergeable but has deferred observed-data ACs ───────────
        local owed_count=0
        owed_count=$(printf '%s' "$owed_json" | python3 -c \
            'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)
        _log "OWED-DATA $spec_id → ready (owed: ${owed_count} ACs)"
        local tmp_ready
        tmp_ready="${f%.gating.md}.ready.md.tmp$$"
        {
            cat "$f"
            printf '\ngraded_by: gating-watch\n'
            printf 'graded_at: %s\n' "$graded_at"
            printf 'owed_data: true\n'
            printf 'owed_data_acs:\n'
            [[ -n "$owed_yaml" ]] && printf '%s\n' "$owed_yaml"
            printf 'matches_intent_reason: %s\n' "$mi_reason"
            printf 'card_ok_reason: %s\n' "$co_reason"
        } > "$tmp_ready"
        mv "$tmp_ready" "${f%.gating.md}.ready.md"
        rm -f "$f"
        # shellcheck disable=SC2086
        $LEDGER_SET_CMD "$spec_id" ready --by gating-watch \
            --field "graded_by=gating-watch" \
            --field "graded_at=${graded_at}" \
            --field "owed_data_acs=${owed_json}" 2>/dev/null || true
        _append_completion "$spec_id" "owed-data"

    else
        # ── FAIL: .gating → .rework ───────────────────────────────────────────
        # spec 426 R4: compose a DIAGNOSABLE reason — name the failing gate + real check
        # output instead of a bare "regrade". Robust JSON parse handles fenced grader output.
        local reason=""
        # shellcheck disable=SC2086
        reason=$(printf '%s' "$verdict_json" | $REASON_COMPOSE_CMD \
                    --closeout-file "$check_out" \
                    --checks-result "$checks_result" \
                    --pre-gate-result "$pre_gate_result" 2>/dev/null) || true
        if [[ -z "$reason" ]]; then
            local _mi_part="${mi_reason:+$mi_reason | }"
            reason="${_mi_part}closeout-grader FAIL (decision=${decision:-unknown}; mechanical=${checks_result}; pre_gate=${pre_gate_result})"
        fi
        _log "FAIL $spec_id → rework (${reason})"
        local tmp_rework
        tmp_rework="${f%.gating.md}.rework.md.tmp$$"
        {
            cat "$f"
            printf '\nrework_reason: %s\n' "$reason"
        } > "$tmp_rework"
        mv "$tmp_rework" "${f%.gating.md}.rework.md"
        rm -f "$f"
        # shellcheck disable=SC2086
        $LEDGER_SET_CMD "$spec_id" rework --by gating-watch \
            --reason "$reason" 2>/dev/null || true
        _append_completion "$spec_id" "fail"
    fi

    # ── Cleanup ────────────────────────────────────────────────────────────────
    rm -f "$marker"
    [[ -n "$tmpdir" ]] && rm -rf "$tmpdir"
    return 0
}

# ── Main scan loop ─────────────────────────────────────────────────────────────
any_gating=false

shopt -s nullglob
gating_files=("${LANE_DIR}"/*.gating.md)
shopt -u nullglob

for f in "${gating_files[@]}"; do
    any_gating=true
    if ! _grade_spec "$f"; then
        _log "grade_spec returned error for $f (continuing)"
    fi
done

# ── Inert alarm (spec 297 / R3 / AC5) ─────────────────────────────────────────
if [[ "$any_gating" == "true" ]]; then
    now_ts=$(date +%s)
    last_completion_age=$(( GATING_STALE_SECS + 1 ))  # assume stale unless proven fresh

    if [[ -f "$COMPLETIONS_LOG" ]]; then
        last_ts_str=$(grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' \
            "$COMPLETIONS_LOG" | tail -1 || true)
        if [[ -n "$last_ts_str" ]]; then
            last_ts=$(date -u -d "$last_ts_str" +%s 2>/dev/null || echo 0)
            last_completion_age=$(( now_ts - last_ts ))
        fi
    fi

    if (( last_completion_age > GATING_STALE_SECS )); then
        _log "INERT: gating files present but no completion in ${GATING_STALE_SECS}s → stall alarm"
        echo "$(ts) gating-watch inert" > "$STALL_FLAG"
        # shellcheck disable=SC2086
        $ALARM_CMD 2>/dev/null || true
    fi
fi
