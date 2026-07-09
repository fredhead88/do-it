# Standing-role cron wiring (v4.7.0)

DO-IT's standing roles (integrator, rev, builders, watcher) and the detached grader
are kept alive and moving by a set of per-minute cron jobs. They are **optional** —
DO-IT runs fine hand-driven — but without them an idle role pane won't wake on new
work, and the `.gating` state won't advance on its own.

All scripts honour env overrides so you don't edit them:

| Var | Default | Meaning |
|-----|---------|---------|
| `REPO_ROOT` | the repo the script lives in | your checkout |
| `PYTHON` | `python3` | interpreter (point at a venv if you use one) |
| `BUS_ROOT` / `DOIT_LEDGER_DIR` | `~/.claude` | the machine-global bus |
| `ALARM_CMD` | `true` (no-op) | your alerter for stall/inert conditions |
| `DEPLOY_SERVER` | — | set if a close-out gate SSHes to your host |

Install with `crontab -e`. Replace `<REPO>` with your absolute repo root and, if you
use a venv, prefix `PYTHON=<REPO>/.venv/bin/python`. Log paths are examples.

```cron
# --- relay: resume a context-saturated role from its baton (all standing roles) ---
* * * * *            REPO_ROOT=<REPO> <REPO>/relay-watch/relay-watch.sh
* * * * * ROLE=rev   REPO_ROOT=<REPO> <REPO>/relay-watch/relay-watch.sh
* * * * * ROLE=watcher REPO_ROOT=<REPO> <REPO>/relay-watch/relay-watch.sh
* * * * * ROLE=builder REPO_ROOT=<REPO> <REPO>/relay-watch/relay-watch.sh

# --- pane liveness: keep the standing panes alive ---
*/5 * * * * <REPO>/relay-watch/liveness.sh pane orc
*/5 * * * * <REPO>/relay-watch/liveness.sh pane rev
*/5 * * * * <REPO>/relay-watch/liveness.sh pane watcher --standing

# --- nudge: poke an idle role pane when its lane has unconsumed work ---
* * * * * ROLE=rev     REPO_ROOT=<REPO> <REPO>/scripts/doit-nudge.sh    >> /tmp/rev-nudge.log 2>&1
* * * * * ROLE=builder REPO_ROOT=<REPO> <REPO>/scripts/doit-nudge.sh    >> /tmp/builder-nudge.log 2>&1
# ROLE=orc nudge: enable once you've confirmed its owed-set is scoped to pickup work
#   (specs + ready branches), not the whole advisory backlog — else it spams.
# * * * * * ROLE=orc NUDGE_ORC_IDLE_SECS=150 REPO_ROOT=<REPO> <REPO>/scripts/doit-nudge.sh >> /tmp/orc-nudge.log 2>&1

# --- detached grader: advance .gating -> .ready/.rework ---
* * * * * REPO_ROOT=<REPO> <REPO>/scripts/gating-watch.sh >> /tmp/gating-watch.log 2>&1

# --- standing-role heartbeat + builder sentinel reconcile + watcher sweep ---
*/30 * * * * ROLE=watcher REPO_ROOT=<REPO> <REPO>/scripts/standing-role-heartbeat.sh >> /tmp/watcher-heartbeat.log 2>&1
*/30 * * * * ROLE=builder REPO_ROOT=<REPO> <REPO>/scripts/standing-role-heartbeat.sh >> /tmp/builder-heartbeat.log 2>&1
* * * * * REPO_ROOT=<REPO> <REPO>/scripts/builder_lifecycle_reconcile.sh >> /tmp/builder-reconcile.log 2>&1
*/30 * * * * REPO_ROOT=<REPO> <REPO>/scripts/watcher_sweep_liveness.sh >> /tmp/watcher-sweep-liveness.log 2>&1
```

Notes:
- These send keys into **tmux** panes (via `scripts/lib/pane_send.sh`), so the role
  sessions must run in tmux with the pane titles the scripts expect (see each role
  skill). Run the cron as the **same user** that owns the tmux server (a wrong-user
  cron sees no panes — the spec-293 failure mode).
- The `ROLE=orc` nudge ships **disabled** deliberately: enable it only after confirming
  its outstanding-set is scoped to genuine pickup work, or it will poke every advisory
  memo/corrective every minute.
