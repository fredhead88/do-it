# Verification Loop — Setup

This document covers a fresh-box setup from scratch. On the current Hetzner box, all of these steps are already done. Read this when provisioning a new box or debugging a broken setup.

---

## 1. google-chrome-stable (.deb recipe)

The harness uses `channel: 'chrome'` in Playwright, which requires `google-chrome-stable` (not Chromium). The standard `apt install chromium-browser` won't work.

```bash
# One-time key + repo setup
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
  | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg

echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] \
  https://dl.google.com/linux/chrome/deb/ stable main" \
  > /etc/apt/sources.list.d/google-chrome.list

apt update && apt install -y google-chrome-stable

# Verify
google-chrome-stable --version
# Expected: Google Chrome 12x.x.x.x
which google-chrome-stable
# Expected: /usr/bin/google-chrome-stable
```

### Wedged dpkg lock (the yak-shave that burned 30 minutes)

If `apt install` hangs with "waiting for dpkg lock", do NOT kill apt or dpkg — that corrupts the package DB. Instead, find and kill only the hanging `needrestart` hook:

```bash
# Find the culprit
ps aux | grep needrestart

# Kill only the needrestart process (not apt/dpkg)
kill <needrestart-pid>

# apt will then continue normally
```

Never `kill -9 apt` or `kill -9 dpkg` — always kill only the blocked post-install hook.

---

## 2. npm install

```bash
cd ~/.claude/verification-loop
npm install
# This installs playwright ^1.60.0 (no system browser download needed — chrome is already installed)
```

---

## 3. Required credentials (from `<repo root>/.env`)

The harness reads these environment variables. They must be set before running any tick.

| Var | Description |
|-----|-------------|
| `VERIFIER_USER` | Email for the verifier test account (`verifier@albertscott.com`) |
| `VERIFIER_PASS` | Password for the verifier test account |
| `API_KEY` | Bearer token for the AS backend API |

These are already in `<repo root>/.env` on the Hetzner box. Source them before running:

```bash
set -a; source <repo root>/.env; set +a
node ~/.claude/verification-loop/tick.mjs --dry-run
```

The verifier account (`verifier@albertscott.com`) is provisioned in `AUTH_USERS_JSON` on the Vercel frontend project. It is read-only. Session TTL is 7 days — the harness re-acquires `storageState` once per calendar day, not on a timer.

---

## 4. Selfcheck

Run this after setup to confirm everything is wired:

```bash
set -a; source <repo root>/.env; set +a
node -e "
import('./lib/selfcheck.mjs').then(async m => {
  import('./lib/config.mjs').then(cfg => {
    const r = m.selfcheck(cfg.loadConfig(), process.env, (await import('node:fs')).default);
    console.log(r.ok ? 'SELFCHECK OK' : 'FAIL: ' + r.failures.join('; '));
  });
});
"
```

Expected: `SELFCHECK OK`

---

## 5. Cron line (cost-aware tick)

The tick is designed to be cheap on idle ticks (no new sha → no browser spin). A 30-minute cadence catches ships within half an hour.

```cron
*/30 * * * * root set -a; source <repo root>/.env; set +a; \
  node /home/albert/.claude/verification-loop/tick.mjs >> /tmp/vloop.log 2>&1
```

Add to `/etc/cron.d/verification-loop`:

```bash
cat > /etc/cron.d/verification-loop << 'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
*/30 * * * * root set -a; source <repo root>/.env; set +a; node /home/albert/.claude/verification-loop/tick.mjs >> /tmp/vloop.log 2>&1
EOF
chmod 644 /etc/cron.d/verification-loop
```

### Smoke-test before enabling cron

```bash
set -a; source <repo root>/.env; set +a

# 1. Dry-run — observes but writes nothing
node /home/albert/.claude/verification-loop/tick.mjs --dry-run --force

# 2. Single-spec single-criterion smoke test (fast, <60s)
node /home/albert/.claude/verification-loop/tick.mjs \
  --spec 064-asin-page-unmapped-asin-blank \
  --criterion "returns 200" \
  --force
```

---

## 6. Key paths

| Path | Purpose |
|------|---------|
| `~/.claude/verification-loop/` | Harness root |
| `~/.claude/verification-loop/config/<your-project>.json` | AS-specific config |
| `~/.claude/verification-loop/runs/<date>/` | Per-day run artifacts |
| `~/.claude/verification-loop/runs/<date>/PROGRESS.jsonl` | Tick event log |
| `~/.claude/verification-loop/runs/<date>/VERIFICATION-LEDGER.jsonl` | Per-criterion verdicts |
| `~/.claude/verification-loop/runs/<date>/NEEDS-EPHRAIM.jsonl` | Escalations |
| `~/.claude/ledger/verified/` | Verifier-owned verdict namespace (builder can't write here) |
| `/tmp/vloop.log` | Cron tick stdout log |

---

## 7. Notes on the memory/swap (Hetzner)

The box had zero swap before 2026-06-03; heavy Python pipelines caused OOM-killed tmux sessions. 16GB swap was added. Playwright (headless Chrome) itself is ~200–400MB per browser instance — fine on a 4GB box. The harness closes the browser after each probe call.

If a tick is OOM-killed mid-run, it resumes safely on the next tick (reads `PROGRESS.jsonl` to skip already-CONFIRMED criteria).
