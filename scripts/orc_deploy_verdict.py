"""orc_deploy_verdict.py — deploy log → compact verdict for the DO-IT integrator.

Runs (or ingests) a deploy command and emits ONLY a compact JSON verdict to
stdout so the integrator never has to ingest the raw log.  The raw log is
written to --log-file for optional offline inspection.

Usage examples
--------------
# Run a real deploy and capture verdict:
    python scripts/orc_deploy_verdict.py --command "./deploy.sh --api-only" \\
        --log-file output/deploy-logs/deploy-api.log

# Re-summarise an already-captured log:
    python scripts/orc_deploy_verdict.py --from-log output/deploy-logs/deploy-api.log

# With an explicit health-check URL:
    python scripts/orc_deploy_verdict.py --command "./deploy.sh" \\
        --health-url http://<your-host>:8000/health

Output (stdout only, <= 5 lines):
    {"deployed_sha": "abc1234", "health_check": "pass", "note": "...", "log_file": "..."}
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Repo root is one level above this script.
_REPO_ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_LOG_DIR = _REPO_ROOT / "output" / "deploy-logs"
_DEFAULT_LOG_FILE = _DEFAULT_LOG_DIR / "deploy-latest.log"

# Patterns that indicate a successful deploy.
_SUCCESS_PATTERNS = [
    re.compile(r"health check passed", re.IGNORECASE),
    re.compile(r"HTTP[/ ]+200", re.IGNORECASE),
    re.compile(r"\bOK\b"),
    re.compile(r"deploy\s+(complete|succeeded|successful)", re.IGNORECASE),
    re.compile(r"deployment\s+(complete|succeeded|successful)", re.IGNORECASE),
]

# Patterns that indicate a failure — any of these forces health_check="fail".
_FAILURE_PATTERNS = [
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
    re.compile(r"\bfailure\b", re.IGNORECASE),
    re.compile(r"traceback", re.IGNORECASE),
    re.compile(r"non-?zero exit", re.IGNORECASE),
    re.compile(r"exit code [^0]", re.IGNORECASE),
]

# Pattern to extract a deployed git SHA from the log.
_SHA_PATTERN = re.compile(
    r"(?:deployed|HEAD is now at|HEAD:|revision|commit)[^\w]*([0-9a-f]{7,40})\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core functions (importable for unit tests)
# ---------------------------------------------------------------------------


def run_command(command: str) -> tuple[str, int]:
    """Run *command* in a shell, return (combined_output, returncode)."""
    result = subprocess.run(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.stdout, result.returncode


def write_log(log_text: str, log_path: Path) -> None:
    """Write *log_text* to *log_path*, creating parent dirs as needed."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(log_text, encoding="utf-8")


def extract_sha(log_text: str) -> str:
    """Return the first git SHA found near a deploy marker, or 'unknown'."""
    m = _SHA_PATTERN.search(log_text)
    if m:
        return m.group(1)
    # Fall back to the repo's current HEAD.
    try:
        result = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        sha = result.stdout.strip()
        if sha and result.returncode == 0:
            return sha
    except Exception:
        pass
    return "unknown"


def check_health_url(url: str) -> bool:
    """Return True if GET *url* returns HTTP 200, False otherwise."""
    try:
        with urlopen(url, timeout=10) as resp:
            return resp.status == 200
    except (URLError, Exception):
        return False


def derive_health_from_log(log_text: str, returncode: int | None = None) -> str:
    """Return 'pass' or 'fail' based on log content and optional returncode."""
    # A non-zero return code is an immediate failure.
    if returncode is not None and returncode != 0:
        return "fail"

    # Any explicit failure marker forces fail (conservative).
    for pattern in _FAILURE_PATTERNS:
        if pattern.search(log_text):
            return "fail"

    # Require at least one success marker.
    for pattern in _SUCCESS_PATTERNS:
        if pattern.search(log_text):
            return "pass"

    return "fail"


def build_note(
    health: str,
    command: str | None,
    returncode: int | None,
    health_url: str | None,
) -> str:
    """Compose a single-line human note for the verdict."""
    source = command.split()[0] if command else "log"
    if health == "pass":
        if health_url:
            return f"{source} completed; health {health_url} 200"
        return f"{source} completed; health check passed"
    if returncode is not None and returncode != 0:
        return f"{source} exited with code {returncode}"
    return f"{source} finished with failure markers in log"


def produce_verdict(
    *,
    command: str | None = None,
    from_log: str | None = None,
    log_file: Path,
    health_url: str | None = None,
) -> dict:
    """Obtain log text, write it, and return the compact verdict dict."""
    returncode: int | None = None

    if from_log:
        log_text = Path(from_log).read_text(encoding="utf-8")
        # If --log-file differs from --from-log, write a copy.
        if Path(from_log).resolve() != log_file.resolve():
            write_log(log_text, log_file)
        else:
            # Ensure parent dirs exist even if file is already there.
            log_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        cmd = command or "./deploy.sh"
        log_text, returncode = run_command(cmd)
        write_log(log_text, log_file)

    sha = extract_sha(log_text)

    if health_url:
        health = "pass" if check_health_url(health_url) else "fail"
    else:
        health = derive_health_from_log(log_text, returncode)

    note = build_note(health, command, returncode, health_url)

    return {
        "deployed_sha": sha,
        "health_check": health,
        "note": note,
        "log_file": str(log_file),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run (or ingest) a deploy and emit a compact JSON verdict.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--command",
        default="./deploy.sh",
        help='Deploy command to run (default: "./deploy.sh").',
    )
    mode.add_argument(
        "--from-log",
        metavar="PATH",
        help="Parse an already-captured log file instead of running a command.",
    )
    p.add_argument(
        "--log-file",
        metavar="PATH",
        default=str(_DEFAULT_LOG_FILE),
        help=f"Where to write the full raw log (default: {_DEFAULT_LOG_FILE}).",
    )
    p.add_argument(
        "--health-url",
        metavar="URL",
        help="If given, derive health from an HTTP GET to this URL (200 = pass).",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    log_file = Path(args.log_file)

    verdict = produce_verdict(
        command=None if args.from_log else args.command,
        from_log=args.from_log,
        log_file=log_file,
        health_url=args.health_url,
    )

    # Exactly one line of JSON to stdout.
    print(json.dumps(verdict))


if __name__ == "__main__":
    main()
