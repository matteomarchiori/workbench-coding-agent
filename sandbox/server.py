import asyncio
import ast
import hmac
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException

WORKSPACE = Path(os.getenv("WORKSPACE", "/workspace")).resolve()
MAX_PYTHON_FILES = 500
MAX_WORKSPACE_ENTRIES = 10_000
MAX_SOURCE_BYTES = 1_000_000
MAX_TOTAL_SOURCE_BYTES = 50_000_000
MAX_CHECK_OUTPUT_CHARS = 1_500
PYTEST_TIMEOUT_SECONDS = 120
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

app = FastAPI(title="Workbench Coding Agent Sandbox", version="0.1.0")


def _authorized(authorization: str | None) -> bool:
    token = os.getenv("SANDBOX_TOKEN", "")

    if len(token) < 32 or not token.isascii() or not authorization:
        return False

    scheme, separator, supplied_token = authorization.partition(" ")
    return (
        separator == " "
        and scheme.casefold() == "bearer"
        and supplied_token.isascii()
        and hmac.compare_digest(token, supplied_token)
    )


def _child_environment() -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "HOME": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }


def _truncate(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[:MAX_CHECK_OUTPUT_CHARS]


def _pytest_check() -> dict:
    started = time.monotonic()

    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            cwd=WORKSPACE,
            env=_child_environment(),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PYTEST_TIMEOUT_SECONDS,
            check=False,
        )
        return {
            "name": "pytest",
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "timed_out": False,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": _truncate(completed.stdout),
            "stderr": _truncate(completed.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": "pytest",
            "exit_code": 124,
            "passed": False,
            "timed_out": True,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": _truncate(exc.stdout),
            "stderr": _truncate(exc.stderr),
        }


def _python_syntax_check() -> dict:
    started = time.monotonic()
    errors = []
    python_files = 0
    source_bytes = 0
    workspace_entries = 0
    limit_reached = False

    def record_walk_error(error: OSError) -> None:
        errors.append(f"Workspace scan failed: {error}")

    for root, directories, filenames in os.walk(
        WORKSPACE,
        followlinks=False,
        onerror=record_walk_error,
    ):
        directories[:] = [
            name
            for name in directories
            if name not in IGNORED_DIRS
            and not (Path(root) / name).is_symlink()
        ]

        workspace_entries += len(directories) + len(filenames)

        if workspace_entries > MAX_WORKSPACE_ENTRIES:
            errors.append(
                f"Workspace entry count exceeds {MAX_WORKSPACE_ENTRIES}"
            )
            break

        for filename in filenames:
            path = Path(root) / filename

            if not filename.endswith(".py") or path.is_symlink():
                continue

            python_files += 1

            if python_files > MAX_PYTHON_FILES:
                errors.append(
                    f"Python file count exceeds {MAX_PYTHON_FILES}"
                )
                limit_reached = True
                break

            try:
                file_size = path.stat().st_size

                if file_size > MAX_SOURCE_BYTES:
                    errors.append(f"{path.relative_to(WORKSPACE)} exceeds 1 MB")
                    continue

                source_bytes += file_size

                if source_bytes > MAX_TOTAL_SOURCE_BYTES:
                    errors.append("Total Python source size exceeds the limit")
                    limit_reached = True
                    break

                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                location = f":{exc.lineno}" if isinstance(exc, SyntaxError) else ""
                errors.append(
                    f"{path.relative_to(WORKSPACE)}{location}: {exc}"
                )

        if limit_reached:
            break

    return {
        "name": "python_syntax",
        "exit_code": 1 if errors else 0,
        "passed": not errors,
        "skipped": python_files == 0 and not errors,
        "timed_out": False,
        "duration_seconds": round(time.monotonic() - started, 3),
        "files_checked": min(python_files, MAX_PYTHON_FILES),
        "errors": [error[:250] for error in errors[:10]],
    }


def _run_fixed_checks() -> list[dict]:
    return [_pytest_check(), _python_syntax_check()]


@app.get("/health")
async def health():
    return {"status": "ok", "service": "workbench-coding-agent-sandbox"}


@app.post("/checks")
async def run_checks(authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")

    checks = await asyncio.to_thread(_run_fixed_checks)
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }
