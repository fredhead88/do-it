# Verification Loop — Setup

This document covers a fresh-box setup from scratch.

---

## 1. google-chrome-stable (.deb recipe)

The harness uses `channel: 'chrome'` in Playwright, which requires `google-chrome-stable`
(not Chromium). The standard `apt install chromium-browser` won't work.

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

### Wedged dpkg lock

If `apt install` hangs with "waiting for dpkg lock", do NOT kill apt or dpkg — that
corrupts the package DB. Find and kill only the blocking `needrestart` hook:

```bash
ps aux | grep needrestart
kill <needrestart-pid>
# apt will then continue normally
```

---

## 2. npm install

```bash
cd path/to/do-it/verification-loop
npm install
# Installs playwright ^1.60.0 (no system browser download needed — chrome is already installed)
```

---

## 3. Config file

Copy `config/example.json` to `config/<your-project>.json` and fill in your values.
See `config/README.md` for field documentation.

---

## 4. Required credentials

The harness reads these from the environment. Names are configurable via the config
file (see `config/README.md`). Source them before running:

```bash
export VERIFIER_USER=verifier@your-app.example.com
export VERIFIER_PASS=<password>
export API_KEY=<bearer-token>

# Or source from your project's .env:
set -a; source /path/to/your/.env; set +a
```

The verifier account should be read-only, provisioned specifically for this harness.
Session TTL is 7 days — the harness re-acquires `storageState` once per calendar day.

---

## 5. Selfcheck

Run this after setup to confirm everything is wired:

```bash
set -a; source /path/to/your/.env; set +a
node -e "
import('./lib/selfcheck.mjs').then(async m => {
  const cfg = (await import('./lib/config.mjs')).loadConfig('your-project');
  const fs = (await import('node:fs')).default;
  const r = m.selfcheck(cfg, process.env, fs);
  console.log(r.ok ? 'SELFCHECK OK' : 'FAIL: ' + r.failures.join('; '));
});
"
```

Expected: `SELFCHECK OK`

---

## 6. Cron line (cost-aware tick)

The tick is cheap on idle ticks (no new sha → no browser spun). A 30-minute cadence
catches ships within half an hour.

```cron
*/30 * * * * <user> set -a; source /path/to/your/.env; set +a; \
  node /path/to/do-it/verification-loop/tick.mjs --config <your-project> >> /tmp/vloop.log 2>&1
```

Add to `/etc/cron.d/verification-loop`:

```bash
cat > /etc/cron.d/verification-loop << 'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
*/30 * * * * <user> set -a; source /path/to/your/.env; set +a; node /path/to/do-it/verification-loop/tick.mjs --config <your-project> >> /tmp/vloop.log 2>&1
EOF
chmod 644 /etc/cron.d/verification-loop
```

### Smoke-test before enabling cron

```bash
set -a; source /path/to/your/.env; set +a

# 1. Dry-run — observes but writes nothing
node /path/to/do-it/verification-loop/tick.mjs --config <your-project> --dry-run --force

# 2. Single-spec single-criterion smoke test (fast, <60s)
node /path/to/do-it/verification-loop/tick.mjs \
  --config <your-project> \
  --spec <NNN-slug> \
  --criterion "returns 200" \
  --force
```

---

## 7. Key paths

| Path | Purpose |
|------|---------|
| `verification-loop/` | Harness root |
| `verification-loop/config/<project>.json` | Project-specific config |
| `verification-loop/runs/<date>/` | Per-day run artifacts |
| `verification-loop/runs/<date>/PROGRESS.jsonl` | Tick event log |
| `verification-loop/runs/<date>/VERIFICATION-LEDGER.jsonl` | Per-criterion verdicts |
| `verification-loop/runs/<date>/NEEDS-HUMAN.jsonl` | Escalations requiring human review |
| `~/.claude/ledger/verified/` | Verifier-owned verdict namespace (builder can't write here) |
| `/tmp/vloop.log` | Cron tick stdout log |

---

## 8. Memory / swap notes

Playwright (headless Chrome) uses ~200–400MB per browser instance. On constrained
boxes (2–4GB RAM), add swap before running:

```bash
# 4GB swap (adjust size as needed)
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
```

If a tick is OOM-killed mid-run, it resumes safely on the next tick
(reads `PROGRESS.jsonl` to skip already-CONFIRMED criteria).
