# LocalCoder

LocalCoder is a local coding agent for reading and modifying files inside a
controlled `workspace` directory. It uses FastAPI and an OpenAI-compatible
model, which can run locally with MLX.

## Features

- Direct chat with the model.
- Plans validated by a Planner and a Reviewer.
- Task execution with acceptance criteria and an iteration limit.
- Tools restricted to the workspace:
  - list files;
  - read files;
  - search source code;
  - create or overwrite files with backups.
- SQLite persistence for tasks and tool-call results.
- Automatic recovery of tasks and operations interrupted by a restart.
- Explicit resume for interrupted tasks without uncertain writes.
- Endpoints for task operations, cancellation, and cleanup.
- Resume of the same task, including recovery of persisted evidence.
- Bounded retries for temporary provider errors and configurable timeouts.
- `/metrics` endpoint with operation counts and average duration.
- Cleanup that preserves journals with pending or uncertain operations.
- Optional automatic retention of terminal tasks, disabled by default.
- `run_workspace_checks` sandbox tool: pytest with fixed arguments and Python
  AST checks.
- No arbitrary command execution or model-supplied command arguments.

## Quick start

Create the Python environment and install dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest
```

Start the MLX model server:

```bash
cd model-server
./start.sh
```

From the project root, start the API:

```bash
MODEL_BASE_URL=http://127.0.0.1:8000/v1 \
MODEL_NAME=Qwen/Qwen3-8B-MLX-4bit \
SANDBOX_ENABLED=false \
MODEL_TIMEOUT_SECONDS=180 \
MODEL_MAX_RETRIES=2 \
TASK_RETENTION_DAYS=0 \
WORKSPACE="$PWD/workspace" \
DATABASE_PATH="$PWD/data/localcoder.db" \
.venv/bin/python -m uvicorn agent.main:app --host 127.0.0.1 --port 8001
```

The API documentation is available at `http://127.0.0.1:8001/docs`.

## Docker Compose

```bash
export SANDBOX_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
docker compose up --build
# or
podman compose up --build
```

The agent listens on `127.0.0.1:8001`. The workspace is mounted at
`/workspace` and the database at `/data/localcoder.db`.
`TASK_RETENTION_DAYS` enables startup cleanup of expired terminal tasks; `0`
(the default) disables automatic retention. `SANDBOX_TOKEN` is required by
Compose; do not save it in project files. The sandbox endpoint is not exposed
on the host.

## Main API endpoints

```bash
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/health/model
curl http://127.0.0.1:8001/metrics

curl -X POST http://127.0.0.1:8001/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"message":"Read result.txt"}'

curl -X POST http://127.0.0.1:8001/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"message":"Run the project tests"}'

curl http://127.0.0.1:8001/tasks
curl http://127.0.0.1:8001/tasks/TASK_ID
curl http://127.0.0.1:8001/tasks/TASK_ID/operations

curl -X POST http://127.0.0.1:8001/tasks/TASK_ID/resume
curl -X DELETE http://127.0.0.1:8001/tasks/TASK_ID
curl -X POST 'http://127.0.0.1:8001/tasks/cleanup?status=interrupted&older_than_days=30'
```

## Architecture

```text
HTTP client
    |
FastAPI (agent/main.py)
    |
AgentOrchestrator
    |-- TaskPlanner + Reviewer
    |-- ModelProvider
    |-- ToolRegistry
    |-- TaskRepository
            |
            +-- SQLite tasks
            +-- SQLite operation_journal
```

## Workspace security

- Paths must be relative.
- `..`, absolute paths, and symlinks are rejected.
- Files are limited to 1 MB.
- Writes use atomic temporary files.
- Overwrites create a backup.
- The model cannot call tools outside the approved plan criteria.
- Shell commands are not exposed by the agent.
- File contents are untrusted data and must not be treated as instructions.

## Sandbox

The `run_workspace_checks` tool is available when `SANDBOX_ENABLED=true`.
It always runs only the following checks:

- `python -m pytest -q -p no:cacheprovider` from the workspace root, with a
  120-second timeout;
- AST parsing of `.py` files without executing them, up to 500 files and 50 MB
  total.

The model cannot supply commands, pytest arguments, or paths. Running the tests
must be explicitly requested; output and execution time are limited, and tool
calls are journaled. Pytest executes workspace test code: the container runs as
a non-root user with a read-only root filesystem, CPU and memory limits, and an
internal network; the shared workspace remains writable for tests.
If the workspace contains no tests, pytest returns exit code 5 (`no tests ran`);
if there are no Python files, the AST check is marked `skipped`.

The sandbox requires a bearer token shared between the containers. Do not
expose the service directly to the host or reuse the token for other services.
With Compose, the agent waits for the sandbox health check before starting.

## Tests

```bash
./.venv/bin/python -m pytest -q
```

The current suite covers the provider, planner, orchestrator, filesystem, tool
registry, repository, task schema, sandbox, and API.

## License

LocalCoder is licensed under the [MIT License](LICENSE).
