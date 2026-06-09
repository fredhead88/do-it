# Verification Loop — Config

Each project drops in its own `<project>.json` here. Copy `example.json`
as your starting point and fill in the values. The config name is passed
to the tick as `--config <name>` (without the `.json` extension).

## Fields

| Field | Required | Description |
|-------|----------|-------------|
| `prod_base` | yes | Base URL of your deployed app |
| `api_base` | yes | Base URL for backend API calls |
| `api_key_env` | yes | Name of the env var holding the Bearer token |
| `auth.mode` | yes | Authentication mode — currently `test_user` |
| `auth.login_path` | yes | Login endpoint path (relative to `prod_base`) |
| `auth.user_env` | yes | Name of the env var holding the verifier email |
| `auth.pass_env` | yes | Name of the env var holding the verifier password |
| `page_map` | yes | Map of page name → path, e.g. `{"home": "/", "dashboard": "/app"}` |
| `verify_periods` | no | List of period labels to check per criterion (default: `["primary"]`) |
| `repo_root` | no | Path to the git repo — used for sha detection via `git rev-parse` |
| `spec_ledger_py` | no | Path to `spec_ledger.py` — used by the `verify` subcommand |
| `ledger_base` | no | Path to the bus ledger dir (default: `~/.claude/ledger`) |
| `python_bin` | no | Python interpreter for calling `spec_ledger.py` (default: `python3`) |
| `version_file` | no | Path to `.version.json` written by your deploy script |

All path fields accept env-var overrides:

| Config field | Env override |
|---|---|
| `repo_root` | `VLOOP_REPO_ROOT` |
| `spec_ledger_py` | `SPEC_LEDGER_PY` |
| `ledger_base` | `LEDGER_BASE` |
| `python_bin` | `VLOOP_PYTHON_BIN` |
| `version_file` | `VLOOP_VERSION_FILE` |

Credentials (`VERIFIER_USER`, `VERIFIER_PASS`, `API_KEY`) are always read
from the environment — they are never put in the config file.

## One config per project

If you run DO-IT on multiple repos from the same machine, create a
`<project>.json` per project and call the tick with `--config <project>`.
The `your-project.json` used in the reference implementation lives outside
this repo (on the operator's machine at `~/.claude/verification-loop/config/`)
to keep project-specific values off the public bus.
