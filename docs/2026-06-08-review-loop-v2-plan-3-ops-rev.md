# Review Loop v2 — Plan 3 (Ops + the `rev` Session) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the standing `rev` review session and the operational safety net: a role-parameterized relay so `rev` self-relays exactly like `orc` (and can NEVER reboot a `rev` pane as `/orc`), a liveness watchdog that alarms when the verifier or `rev` dies silently, and the durable NEEDS-HUMAN projection so correctives reach orc's board.

**Architecture:** The existing `relay-watch/` scripts become **role-parameterized** (`ROLE` env, default `orc` — fully backward compatible): the sentinel, active-pane file, baton path, log/lock, and boot command are all derived from `ROLE`. A new `skills/rev/SKILL.md` boots the review session (drives the verifier, spot-checks, writes per-criterion verdicts, files correctives, self-relays via `ROLE=rev`). A new `relay-watch/liveness.sh` is the dead-man's switch. `spec_ledger.py` gains a durable NEEDS-HUMAN projection in `render`. `think` sheds its review shape; `DO-IT.md`'s role map becomes orc/rev/think.

**Tech Stack:** Python 3.14 + pytest (spec_ledger), Bash (relay/liveness, tested via dry-run harness), Node already covered in Plan 2. Markdown for skills/DO-IT.

**Scope note:** Plan 3 of 3 for the v3.4.0 design (`docs/2026-06-08-review-loop-prod-verdict-design.md`), on top of v3.4.0 (ledger) + v3.5.0 (verifier). Ships as **v3.6.0** — the release that completes Review Loop v2. The **live standup** (registering the `rev` hook + cron on the AS box, booting `rev` in tmux, the end-to-end A1 proof) is the **sync**, run against the AS instance by/with the orc — Task 8 documents it; it does not run in this repo. Deferred items (deployed_sha gate, interaction_traces, rework_count, severity) stay deferred per the design.

**Motivating field fact (2026-06-08):** the orc relay silently broke because its PostToolUse hook was unregistered, with no alarm — a missing-hook looked identical to a quiet session. Plan 3's liveness watchdog (Task 4) exists to make that class loud, and its setup-verify checks the hook is actually wired.

---

## File Structure

- **Modify** `relay-watch/orc-token-watch.py` — `ROLE` env (default `orc`) drives `ACTIVE`, the sentinel name, the baton path, and the injected boot command. Behavior identical when `ROLE` is unset.
- **Modify** `relay-watch/relay-watch.sh` — `ROLE` env (default `orc`) drives the due-glob, relay-file, log, lock, and the reboot command (`/orc` or `/rev`).
- **Create** `relay-watch/liveness.sh` — the dead-man's switch: `VERIFIER_DOWN` (PROGRESS.jsonl stale) + `REV_DOWN`/`ORC_DOWN` (active-pane file points at a dead pane) + a hook-registered check. Writes a durable flag the ledger render surfaces.
- **Modify** `scripts/spec_ledger.py` — `render` projects unresolved NEEDS-HUMAN items + the liveness flags into the board.
- **Create** `skills/rev/SKILL.md` — the standing review session.
- **Modify** `skills/think/SKILL.md` — remove "Shape B — Review".
- **Modify** `DO-IT.md` — role map → orc / rev / think; review doctrine points at rev + the executable gate.
- **Modify** `relay-watch/SETUP.md` — document the `ROLE=rev` hook + cron + liveness cron.
- **Create** tests: `relay-watch/test/relay-role.test.sh` (dry-run, both roles), `relay-watch/test/token-role.test.py` (sentinel/boot per role), and a `tests/test_needs_human_projection.py` (python) for the render projection.
- **Modify** `DO-IT.md` version + `CHANGELOG.md` (Task 7).

---

## Task 1: Role-parameterize `relay-watch.sh`

**Files:**
- Modify: `relay-watch/relay-watch.sh`
- Create: `relay-watch/test/relay-role.test.sh`

- [ ] **Step 1: Write the failing dry-run test**

Create `relay-watch/test/relay-role.test.sh`:

```bash
#!/usr/bin/env bash
# Dry-run test: relay-watch.sh must boot the pane with the ROLE's command.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../relay-watch.sh"
TMP="$(mktemp -d)"
fail=0

run_role() {
  local role="$1" expect="$2"
  local sid="testsid-$role"
  # Fake sentinel + baton + transcript so the watcher reaches the DRY decision.
  printf 'PANE=%%99\nSESSION_ID=%s\nTRANSCRIPT=%s/t.jsonl\nCWD=%s\nCONTEXT=1\n' "$sid" "$TMP" "$TMP" > "/tmp/${role}-handoff-due-${sid}"
  mkdir -p "$TMP/docs/sessions"
  printf 'status: HANDED-OFF\n' > "$TMP/docs/sessions/${role}-relay.md"
  : > "$TMP/t.jsonl"; touch -d '120 seconds ago' "$TMP/t.jsonl"
  # Pretend the pane is alive by stubbing tmux on PATH.
  local out
  out="$(ROLE="$role" ORC_WATCH_DRY=1 ORC_QUIET_SECS=1 PATH="$HERE/stub:$PATH" \
        ORC_RELAY_FILE="$TMP/docs/sessions/${role}-relay.md" bash "$SCRIPT" 2>&1; cat /tmp/${role}-relay-watch.log 2>/dev/null)"
  if echo "$out" | grep -q "$expect"; then echo "ok: ROLE=$role -> $expect"; else echo "FAIL: ROLE=$role expected '$expect', got: $out"; fail=1; fi
  rm -f "/tmp/${role}-handoff-due-${sid}" "/tmp/${role}-relay-watch.log"
}

# tmux stub that always reports our fake pane alive
mkdir -p "$HERE/stub"
cat > "$HERE/stub/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  list-panes) echo "%99" ;;
  send-keys) echo "SEND $*" ;;
esac
STUB
chmod +x "$HERE/stub/tmux"

run_role orc "/orc"
run_role rev "/rev"
rm -rf "$TMP" "$HERE/stub"
exit $fail
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd /home/albert/do-it && bash relay-watch/test/relay-role.test.sh`
Expected: FAIL for `ROLE=rev` (current script is hardcoded to `/orc` and `/tmp/orc-handoff-due-*`).

- [ ] **Step 3: Role-parameterize `relay-watch.sh`**

Replace the head of `relay-watch/relay-watch.sh` (lines 17-29) so role drives everything; the loop body's `RELAY` and the send-keys command use role-derived values:

```bash
set -u

ROLE="${ROLE:-orc}"
BOOT_CMD="${ROLE_BOOT_CMD:-/$ROLE}"
QUIET_SECS="${ORC_QUIET_SECS:-45}"
DRY="${ORC_WATCH_DRY:-0}"
LOG="/tmp/${ROLE}-relay-watch.log"
LOCK="/tmp/${ROLE}-relay-watch.lock"

exec 9>"$LOCK"
flock -n 9 || exit 0

ts() { date -u +%FT%TZ; }

for sentinel in /tmp/${ROLE}-handoff-due-*; do
  [ -e "$sentinel" ] || exit 0

  PANE="" SESSION_ID="" TRANSCRIPT="" CWD="" CONTEXT=""
  # shellcheck disable=SC1090
  . "$sentinel"

  RELAY="${ORC_RELAY_FILE:-$CWD/docs/sessions/${ROLE}-relay.md}"
```

And the two `send-keys`/DRY lines (62-66) become role-aware:

```bash
  if [ "$DRY" = "1" ]; then
    echo "$(ts) DRY RUN: would /clear + $BOOT_CMD pane $PANE (context was ${CONTEXT:-?})" >>"$LOG"
    continue
  fi

  echo "$(ts) restarting $ROLE in pane $PANE (session $SESSION_ID, context ${CONTEXT:-?})" >>"$LOG"
  tmux send-keys -t "$PANE" "/clear" Enter
  sleep 6
  tmux send-keys -t "$PANE" "$BOOT_CMD" Enter
  rm -f "$sentinel"
```

(Default `ROLE=orc` reproduces the exact prior behavior — backward compatible.)

- [ ] **Step 4: Run it — expect pass**

Run: `cd /home/albert/do-it && bash relay-watch/test/relay-role.test.sh`
Expected: `ok: ROLE=orc -> /orc` and `ok: ROLE=rev -> /rev`.

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add relay-watch/relay-watch.sh relay-watch/test/relay-role.test.sh
git commit -m "feat(relay): role-parameterize relay-watch.sh (ROLE=rev boots /rev, never /orc)"
```

---

## Task 2: Role-parameterize `orc-token-watch.py`

**Files:**
- Modify: `relay-watch/orc-token-watch.py`
- Create: `relay-watch/test/token-role.test.py`

- [ ] **Step 1: Write the failing test**

Create `relay-watch/test/token-role.test.py`:

```python
import importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "orc-token-watch.py"


def _run(role, pane, sid, transcript):
    env = {**os.environ, "ROLE": role, "TMUX_PANE": pane, "ORC_WATCH_THRESHOLD": "1"}
    hook_in = json.dumps({"session_id": sid, "cwd": "/tmp", "transcript_path": transcript})
    return subprocess.run([sys.executable, str(HOOK)], input=hook_in, env=env,
                          capture_output=True, text=True)


def test_rev_role_writes_rev_sentinel_and_boot(tmp_path):
    # active file for ROLE=rev must name this pane
    Path("/tmp/rev-active").write_text("PANE=%7\n")
    # a transcript with a usage block over threshold
    t = tmp_path / "sid1.jsonl"
    t.write_text(json.dumps({"message": {"usage": {"input_tokens": 999999}}}) + "\n")
    r = _run("rev", "%7", "sid1", str(t))
    assert r.returncode == 0
    sentinel = Path("/tmp/rev-handoff-due-sid1")
    assert sentinel.exists()
    # the injected message must reference the rev baton + /rev, not orc
    out = r.stdout
    assert "rev-relay.md" in out and "/rev" in out
    sentinel.unlink(); Path("/tmp/rev-active").unlink()


def test_wrong_pane_is_noop(tmp_path):
    Path("/tmp/rev-active").write_text("PANE=%7\n")
    t = tmp_path / "sid2.jsonl"
    t.write_text(json.dumps({"message": {"usage": {"input_tokens": 999999}}}) + "\n")
    r = _run("rev", "%DIFFERENT", "sid2", str(t))
    assert r.returncode == 0 and r.stdout.strip() == ""
    assert not Path("/tmp/rev-handoff-due-sid2").exists()
    Path("/tmp/rev-active").unlink()
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd /home/albert/do-it && python3 -m pytest relay-watch/test/token-role.test.py -v`
Expected: FAIL — the hook is hardcoded to `/tmp/orc-active`, `/tmp/orc-handoff-due-*`, `orc-relay.md`, `/orc`.

- [ ] **Step 3: Role-parameterize `orc-token-watch.py`**

At the top (after `THRESHOLD`, line 26-27), derive role-scoped names:

```python
THRESHOLD = int(os.environ.get("ORC_WATCH_THRESHOLD", "400000"))
ROLE = os.environ.get("ROLE", "orc")
BOOT_CMD = os.environ.get("ROLE_BOOT_CMD", f"/{ROLE}")
ACTIVE = f"/tmp/{ROLE}-active"
```

Change the sentinel name (line 100):

```python
    sentinel = f"/tmp/{ROLE}-handoff-due-{sid or 'unknown'}"
```

Change the injected message (lines 113-120) to use role-derived baton + boot, and reference the role's skill:

```python
    msg = (
        f"{ROLE.upper()} CONTEXT WATCH: live context is {ctx:,} tokens "
        f"(threshold {THRESHOLD:,}). This is an observable relay signal per the {ROLE} "
        "skill. Finish ONLY the current atomic step, then write the relay baton "
        f"(docs/sessions/{ROLE}-relay.md, status: HANDED-OFF, tmp-then-rename) and STOP "
        f"— do not start new work. The relay watcher will /clear this pane and boot a "
        f"fresh {BOOT_CMD} automatically; you do not need to tell the user to do it."
    )
```

(Default `ROLE=orc` reproduces the exact prior strings — backward compatible.)

- [ ] **Step 4: Run it — expect pass**

Run: `cd /home/albert/do-it && python3 -m pytest relay-watch/test/token-role.test.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add relay-watch/orc-token-watch.py relay-watch/test/token-role.test.py
git commit -m "feat(relay): role-parameterize orc-token-watch.py (rev sentinel + /rev boot)"
```

---

## Task 3: Durable NEEDS-HUMAN projection in the ledger render

**Files:**
- Modify: `scripts/spec_ledger.py` — `render` (the needs_human bucket)
- Create: `tests/test_needs_human_projection.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_needs_human_projection.py`:

```python
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    spec = importlib.util.spec_from_file_location("spec_ledger_nh", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_projects_unresolved_needs_human_from_store(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    nh = sl.LEDGER_DIR / "needs-human"
    nh.mkdir(parents=True, exist_ok=True)
    (nh / "200-x.yml").write_text(
        "spec_id: 200-x\nreason: TASTE\nnote: color looks off\nresolved: false\n"
    )
    (nh / "201-y.yml").write_text(
        "spec_id: 201-y\nreason: stale\nnote: done\nresolved: true\n"
    )
    body = sl.render(sl.load_records(), include_all=False)
    assert "NEEDS-HUMAN" in body
    assert "200-x" in body and "color looks off" in body
    assert "201-y" not in body  # resolved -> dropped
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd /home/albert/do-it && python3 -m pytest tests/test_needs_human_projection.py -v`
Expected: FAIL — no needs-human store projection in render.

- [ ] **Step 3: Implement the projection**

Add a loader near `load_verdicts` in `scripts/spec_ledger.py`:

```python
def load_needs_human() -> list[dict]:
    """Unresolved escalations from the durable needs-human store
    (LEDGER_DIR/needs-human/*.yml, written by rev/the verifier)."""
    out: list[dict] = []
    nhdir = LEDGER_DIR / "needs-human"
    if not nhdir.exists():
        return out
    for path in sorted(nhdir.glob("*.yml")):
        rec = _load_yaml(path)
        if not rec.get("resolved"):
            out.append(rec)
    return out
```

In `render`, after the existing `needs_human` record-based section, project the store too. Add near the top of `render` (where `verdicts = load_verdicts()` is):

```python
    nh_store = load_needs_human()
```

And render a section (place it right after the `## ⚠ ... couldn't classify` block, before Outstanding):

```python
    if nh_store:
        L.append(f"## 🙋 NEEDS-HUMAN — escalations awaiting you ({len(nh_store)})")
        for r in nh_store:
            note = r.get("note") or ""
            L.append(f"- {r.get('spec_id', '?')} — **{r.get('reason', '?')}**" + (f": {note}" if note else ""))
        L.append("")
```

- [ ] **Step 4: Run it — expect pass + no regression**

Run: `cd /home/albert/do-it && python3 -m pytest tests/ -q`
Expected: PASS (all, including the new projection test).

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add scripts/spec_ledger.py tests/test_needs_human_projection.py
git commit -m "feat(ledger): render projects the durable needs-human store (unresolved only)"
```

---

## Task 4: The liveness watchdog (the dead-man's switch + hook-registered check)

**Files:**
- Create: `relay-watch/liveness.sh`
- Create: `relay-watch/test/liveness.test.sh`

- [ ] **Step 1: Write the failing test**

Create `relay-watch/test/liveness.test.sh`:

```bash
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
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd /home/albert/do-it && bash relay-watch/test/liveness.test.sh`
Expected: FAIL — `liveness.sh` does not exist.

- [ ] **Step 3: Implement `relay-watch/liveness.sh`**

```bash
#!/usr/bin/env bash
# liveness.sh — dead-man's switch for the review loop. Run by cron.
#   liveness.sh verifier   — VERIFIER_DOWN if PROGRESS.jsonl is stale
#   liveness.sh pane <role> — {ROLE}_DOWN if /tmp/<role>-active points at a dead pane
#   liveness.sh hook <role> <settings.json> — {ROLE}_HOOK_MISSING if not registered
# Flags are written under LIVENESS_FLAG (default ~/.claude/ledger/liveness) so the
# ledger render surfaces them. A cleared condition removes its flag.
set -u
FLAG_DIR="${LIVENESS_FLAG:-$HOME/.claude/ledger/liveness}"
mkdir -p "$FLAG_DIR"
ts() { date -u +%FT%TZ; }
raise() { echo "$(ts) $2" > "$FLAG_DIR/$1"; }
clear() { rm -f "$FLAG_DIR/$1"; }

case "${1:-}" in
  verifier)
    RUNS="${VL_RUNS_DIR:-$HOME/do-it/verification-loop/runs}"
    STALE_MIN="${VERIFIER_STALE_MIN:-90}"
    latest="$(ls -1dt "$RUNS"/*/PROGRESS.jsonl 2>/dev/null | head -1)"
    if [ -z "$latest" ]; then raise VERIFIER_DOWN "no PROGRESS.jsonl found under $RUNS"; exit 0; fi
    age_min=$(( ( $(date +%s) - $(stat -c %Y "$latest") ) / 60 ))
    if [ "$age_min" -gt "$STALE_MIN" ]; then raise VERIFIER_DOWN "PROGRESS.jsonl stale ${age_min}m (> ${STALE_MIN}m)"; else clear VERIFIER_DOWN; fi
    ;;
  pane)
    role="${2:?role}"; active="/tmp/${role}-active"
    [ -f "$active" ] || { clear "${role^^}_DOWN"; exit 0; }  # not armed -> not "down"
    pane="$(grep -oP '(?<=PANE=).*' "$active" 2>/dev/null)"
    if [ -n "$pane" ] && ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$pane"; then
      raise "${role^^}_DOWN" "$active points at dead pane $pane"
    else clear "${role^^}_DOWN"; fi
    ;;
  hook)
    role="${2:?role}"; settings="${3:?settings.json}"
    if grep -q "${role}-token-watch\|ROLE=${role}.*token-watch\|token-watch.*ROLE=${role}" "$settings" 2>/dev/null \
       || { [ "$role" = orc ] && grep -q "orc-token-watch" "$settings" 2>/dev/null; }; then
      clear "${role^^}_HOOK_MISSING"
    else raise "${role^^}_HOOK_MISSING" "no $role token-watch hook in $settings (relay silently dead)"; fi
    ;;
  *) echo "usage: liveness.sh verifier | pane <role> | hook <role> <settings.json>" >&2; exit 2 ;;
esac
```

- [ ] **Step 4: Run it — expect pass**

Run: `cd /home/albert/do-it && bash relay-watch/test/liveness.test.sh`
Expected: `ok: VERIFIER_DOWN raised` and `ok: fresh -> silent`.

- [ ] **Step 5: Surface liveness flags in the ledger render**

Add to `scripts/spec_ledger.py` a loader + a render line. Loader near `load_needs_human`:

```python
def load_liveness() -> list[str]:
    """Active dead-man's-switch flags written by relay-watch/liveness.sh."""
    d = LEDGER_DIR / "liveness"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.is_file():
            out.append(f"{p.name}: {p.read_text().strip()}")
    return out
```

In `render`, right after the generated-timestamp line, surface them loudly:

```python
    for flag in load_liveness():
        L.append(f"> 🚨 **{flag}**")
    if load_liveness():
        L.append("")
```

Add a test to `tests/test_needs_human_projection.py`:

```python
def test_render_surfaces_liveness_flags(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    fl = sl.LEDGER_DIR / "liveness"
    fl.mkdir(parents=True, exist_ok=True)
    (fl / "VERIFIER_DOWN").write_text("2026-06-08T00:00:00Z PROGRESS.jsonl stale 200m")
    body = sl.render(sl.load_records(), include_all=False)
    assert "VERIFIER_DOWN" in body and "🚨" in body
```

- [ ] **Step 6: Run + commit**

Run: `cd /home/albert/do-it && python3 -m pytest tests/ -q && bash relay-watch/test/liveness.test.sh`
Expected: all green.

```bash
cd /home/albert/do-it
git add relay-watch/liveness.sh relay-watch/test/liveness.test.sh scripts/spec_ledger.py tests/test_needs_human_projection.py
git commit -m "feat(ops): liveness watchdog (VERIFIER_DOWN/ROLE_DOWN/HOOK_MISSING) surfaced in render"
```

---

## Task 5: The `rev` session skill

**Files:**
- Create: `skills/rev/SKILL.md`

- [ ] **Step 1: Author `skills/rev/SKILL.md`** (no test — a skill doc; the gate is Step 2's consistency check)

Create `skills/rev/SKILL.md` with this content:

````markdown
---
name: rev
description: Boot a session into the REVIEWER role for your repo. Use when the user says 'rev', '/rev', 'be the reviewer', 'start the review session', 'this is the rev session', or opens a session whose job is to drive the verification loop, watch what's awaiting prod-verification, spot-check the rendered product, write per-criterion verdicts, and file correctives back to the orchestrator. rev is the standing review twin of orc — one builds, one reviews. It runs on Opus, self-relays on a context ceiling exactly like orc (its OWN relay, never orc's), never touches the build tree, never commits, never authors specs. Invoke at the START of a reviewer session.
---

# rev — the standing reviewer (orc's twin)

**Prerequisites:** read `DO-IT.md` (the protocol) and the design
`docs/2026-06-08-review-loop-prod-verdict-design.md`. rev is the *review* half of
the pair; orc is the *build* half. One builds, one reviews.

## What rev is (and is not)

- rev **drives and supervises the verification loop**: the cron ticks the verifier
  (Playwright + the executable `dom_assertion`); rev reads each tick's rendered-page
  evidence, runs spot-checks, **writes per-criterion verdicts**
  (`spec_ledger.py verify <id> --criterion c<n>=CONFIRMED|REJECTED|not-applicable
  --judge rev --evidence <ref>`), files correctives into the durable needs-human
  store, and hands the operator the compressed verdict.
- rev is **read-only on code**. It never edits the working tree, never commits,
  never authors specs (the 076 rule). An unhappy review produces a *corrective for
  orc* (a needs-human entry orc consumes) or, when it's net-new scope, a note for a
  `/think` session — never a spec written by rev.
- rev's verdicts live ONLY in the verifier namespace (`~/.claude/ledger/verified/`)
  and the needs-human store (`~/.claude/ledger/needs-human/`); the build ledger is
  orc's. This is what keeps the derived join honest.

## First moves (every boot)

0. **Arm the context watch (your OWN relay).** Write your pane to `/tmp/rev-active`
   and clear any stale rev sentinels for it — so a fresh rev is never wiped by a
   leftover handoff:
   ```bash
   printf "PANE=%s\n" "$TMUX_PANE" > /tmp/rev-active
   grep -l "PANE=$TMUX_PANE" /tmp/rev-handoff-due-* 2>/dev/null | xargs -r rm -f
   ```
   Your relay is `ROLE=rev` (separate sentinel `/tmp/rev-handoff-due-*`, baton
   `docs/sessions/rev-relay.md`, reboot `/rev`). It can never reboot your pane as
   `/orc`.
1. **Read the board:** `python scripts/spec_ledger.py --render`. Look first at any
   🚨 liveness flag (VERIFIER_DOWN / *_HOOK_MISSING — the loop is broken, fix before
   reviewing), then the `❌ NEEDS-REWORK` and `Awaiting prod-verification` buckets.
2. **Resume the relay baton** if `docs/sessions/rev-relay.md` says HANDED-OFF (stamp
   RESUMED) — a prior rev handed off to you.

## The review loop (steady state)

For each spec in `Awaiting prod-verification`:
- Read the verifier's evidence for it (`~/.claude/ledger/verified/<id>.yml` +
  `verification-loop/runs/<date>/evidence/`). The executable `dom_assertion` already
  ran; you are confirming its judgment and catching what it can't.
- **Spot-check the rendered page yourself** for any criterion the machine can't fully
  judge (taste, layout, interaction beyond declared traces). Load the deployed URL.
- Write the per-criterion verdict. When you find a defect no criterion covered, file
  a needs-human corrective and tell the operator — it becomes orc's work or a new
  spec via `/think` (an unhappy walk produces a spec — never written by you).
- The compressed verdict to the operator: "N criteria, M prod-verified green; K
  needs-human: …" — not the raw card.

## When the context watch fires

The `REV CONTEXT WATCH` message is your relay signal: finish the current atomic
review step, write the baton (`docs/sessions/rev-relay.md`, `status: HANDED-OFF`,
tmp-then-rename) summarizing what's mid-review, then STOP. The watcher `/clear`s and
boots a fresh `/rev` automatically.

## Boundaries (hard)

- Never `git add`/`commit`/touch the working tree. Never run `setup.sh`.
- Never write the build ledger (`set`/`register`) — only `verify` (verdicts) and the
  needs-human store. Never author a spec.
- Never run while you ARE orc — rev and orc are distinct panes/sessions.
````

- [ ] **Step 2: Consistency check**

Run: `cd /home/albert/do-it && grep -c "rev" skills/rev/SKILL.md && grep -q "ROLE=rev" skills/rev/SKILL.md && echo "rev skill references its own relay"`
Confirm the skill never instructs a commit or a build-ledger `set`/`register`:
Run: `! grep -E "git commit|git add|spec_ledger.py (set|register)" skills/rev/SKILL.md && echo "no forbidden actions"`
Expected: both confirmations print.

- [ ] **Step 3: Commit**

```bash
cd /home/albert/do-it
git add skills/rev/SKILL.md
git commit -m "feat(rev): the standing reviewer skill — drives the verifier, self-relays, never commits"
```

---

## Task 6: `think` sheds review; `DO-IT.md` role map → orc/rev/think

**Files:**
- Modify: `skills/think/SKILL.md` (remove "Shape B — Review")
- Modify: `DO-IT.md` (role map + review doctrine)

- [ ] **Step 1: Remove "Shape B — Review" from `skills/think/SKILL.md`**

Open `skills/think/SKILL.md`, find the "Shape B — Review" section (the review shape) and replace it with a one-line pointer:

```markdown
## Shape B — (removed; review now lives in `rev`)

Reviewing shipped work is the `rev` session's job (it drives the verifier and writes
verdicts). A thinker that notices a defect files it as a new spec via the normal
intake/brainstorm shapes — it does not walk review cards.
```

Also remove any "review" entry from the think skill's shape list / description that says think reviews shipped work, so the four shapes become three (brainstorm / intake-triage / collect).

- [ ] **Step 2: Update `DO-IT.md` role map**

In `DO-IT.md` §1 ("The map") and the role bullets, change the role set to orc / rev / think, with rev as the standing review twin. Replace the think bullet's "review of shipped work" clause, and add a rev bullet:

```markdown
- **rev** — the standing reviewer (orc's twin). Drives the verification loop, reads
  rendered-page evidence, writes per-criterion verdicts to the verifier namespace,
  files correctives. Read-only on code; never commits; never authors specs. Self-relays
  on its own `ROLE=rev` watcher.
```

In §2's review-card paragraph and any "/think review re-confirms" language, replace the human-review-via-think doctrine with: the executable verifier (driven by `rev`) writes the per-criterion verdict; closure is the derived `accepted`; `rev`'s spot-check covers the residual; the thinker is no longer in the closure path.

- [ ] **Step 3: Consistency check**

Run: `cd /home/albert/do-it && grep -n "rev" DO-IT.md | head && grep -q "Shape B" skills/think/SKILL.md && echo "think Shape B updated"`
Confirm DO-IT.md names rev in the role map and think no longer claims to review shipped work.

- [ ] **Step 4: Commit**

```bash
cd /home/albert/do-it
git add skills/think/SKILL.md DO-IT.md
git commit -m "docs(roles): think sheds review; DO-IT role map -> orc/rev/think"
```

---

## Task 7: SETUP.md, full verification, version v3.6.0, CHANGELOG

**Files:**
- Modify: `relay-watch/SETUP.md`, `DO-IT.md` (version), `CHANGELOG.md`

- [ ] **Step 1: Document the rev relay + liveness in SETUP.md**

Append a section to `relay-watch/SETUP.md`:

````markdown
## Standing `rev` (the reviewer twin)

`rev` self-relays with the SAME scripts, role-scoped via `ROLE=rev`:

1. Register a second PostToolUse hook entry:
   ```json
   { "matcher": "", "hooks": [ { "type": "command",
     "command": "ROLE=rev python3 /path/to/.../orc-token-watch.py", "timeout": 10 } ] }
   ```
2. Add a second cron line:
   ```
   * * * * * ROLE=rev /path/to/.../relay-watch.sh
   ```
3. Liveness (the dead-man's switch — run every 30 min):
   ```
   */30 * * * * /path/to/.../liveness.sh verifier; /path/to/.../liveness.sh pane orc; /path/to/.../liveness.sh pane rev; /path/to/.../liveness.sh hook orc /path/to/repo/.claude/settings.json; /path/to/.../liveness.sh hook rev /path/to/repo/.claude/settings.json
   ```
   A missing hook (the 2026-06-08 silent break) now raises `*_HOOK_MISSING` on the
   board instead of failing silently.
````

- [ ] **Step 2: Full verification**

Run: `cd /home/albert/do-it && python3 -m pytest tests/ -q && bash relay-watch/test/relay-role.test.sh && python3 -m pytest relay-watch/test/token-role.test.py -q && bash relay-watch/test/liveness.test.sh`
Expected: all green.

Run: `cd /home/albert/do-it/verification-loop && npm test 2>&1 | grep -E "# (pass|fail)"`
Expected: still all pass (Plan 3 doesn't touch the verifier modules).

- [ ] **Step 3: Version + CHANGELOG**

`DO-IT.md`: `**Version:** 3.5.0` → `**Version:** 3.6.0`. Add `CHANGELOG.md`:

```markdown
## [3.6.0] — 2026-06-08

**Review Loop v2 — Part 3 of 3 (complete): ops + the standing `rev` session.**

### Added
- **`rev`** — the standing reviewer skill (`skills/rev/SKILL.md`): drives the
  verifier, writes per-criterion verdicts, files correctives, self-relays. orc's
  twin — one builds, one reviews.
- **Role-parameterized relay** (`ROLE` env on `orc-token-watch.py` + `relay-watch.sh`):
  `rev` self-relays via its own sentinel/baton/boot (`/rev`), and can never reboot a
  `rev` pane as `/orc`. Default `ROLE=orc` is byte-for-byte the prior behavior.
- **`relay-watch/liveness.sh`** — the dead-man's switch: `VERIFIER_DOWN` (PROGRESS
  stale), `ROLE_DOWN` (active pane dead), `*_HOOK_MISSING` (the relay hook isn't
  registered — the exact silent break seen 2026-06-08). Flags surface loudly in the
  ledger render.
- Durable **needs-human store** projection in `render` (unresolved escalations reach
  orc's board).

### Changed
- `think` sheds its review shape; the role map is now **orc / rev / think**. Review
  of shipped work lives in `rev`; the executable verifier owns the per-criterion
  verdict; closure is the derived `accepted`.

### Deferred (unchanged from the design)
- `deployed_sha` gate (10-min delay interim ships in v3.5.0), interaction_traces,
  rework_count ceiling, severity. Revisit per the design's triggers.
```

- [ ] **Step 4: Commit**

```bash
cd /home/albert/do-it
git add relay-watch/SETUP.md DO-IT.md CHANGELOG.md
git commit -m "chore(release): v3.6.0 — review-loop v2 part 3 (ops + standing rev) complete"
```

---

## Task 8: Live standup (acceptance — run at SYNC, against the AS instance, by/with orc)

**Does NOT run in the public repo.** The AS-instance rollout, sequenced after orc adopts v3.4.0–3.6.0 into `/opt/albert-scott`:

- [ ] Sync the v3.4.0–3.6.0 `spec_ledger.py` + `verification-loop/` + `relay-watch/` into `/opt/albert-scott` (orc-owned tree; orc does this, coherently — see the design's AS-rollout note).
- [ ] Register the `ROLE=rev` PostToolUse hook + cron + the liveness cron (SETUP.md). Confirm `liveness.sh hook rev` reports OK.
- [ ] Boot `/rev` in its own tmux pane; confirm it arms `/tmp/rev-active` and reads the board.
- [ ] **The end-to-end A1 proof** (Plan 2 Task 9): author 106's `verifier:criteria` block, force a tick, confirm 106 lands `REJECTED` → `needs-rework` at the top of the board — A1, dead on the page, finally fails the gate.
- [ ] Confirm a context-ceiling relay fires for both orc and rev without crossing wires.

---

## Self-Review (completed by plan author)

- **Spec coverage:** Plan 3 covers design item 1 (cron is pre-existing; the dead-man's switch is `liveness.sh` — VERIFIER_DOWN + REV_DOWN + the hook-missing alarm), item 5 (durable needs-human store projection), and item 6 (rev skill, role-parameterized self-relay built fresh so it can't reboot as orc, think sheds review, DO-IT role map). The live standup + end-to-end A1 proof is Task 8 at sync.
- **Placeholder scan:** none in Tasks 1-7. Task 8 is an explicit sync-time acceptance gate with concrete steps.
- **Type consistency:** `ROLE`/`BOOT_CMD`/`ACTIVE` env contract is identical across `orc-token-watch.py` and `relay-watch.sh`; `load_needs_human()`/`load_liveness()` return shapes match their render consumers; the liveness flag dir (`LEDGER_DIR/liveness`) and needs-human store (`LEDGER_DIR/needs-human`) are the same paths the watchdog writes and the render reads.
- **Backward-compat:** default `ROLE=orc` reproduces the exact prior relay strings/paths, so the existing (now-fixed) orc relay is unchanged.
