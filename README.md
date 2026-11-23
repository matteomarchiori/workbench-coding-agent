# Workbench Coding Agent

Workbench Coding Agent is a local coding agent for reading and modifying files
inside a controlled `workspace` directory. It uses FastAPI and an
OpenAI-compatible model, which can run locally with MLX.

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
DATABASE_PATH="$PWD/data/workbench.db" \
.venv/bin/python -m uvicorn agent.main:app --host 127.0.0.1 --port 8001
```

The API documentation is available at `http://127.0.0.1:8001/docs`.

## VS Code extension

To invoke the agent from another VS Code project without using this
repository's `workspace/` folder, package and install the extension:

```bash
cd vscode-extension
npm install
npm run package
```

In VS Code, use **Extensions: Install from VSIX...** to install
`vscode-extension/workbench-coding-agent-0.1.0.vsix`. Set the User setting
`workbenchAgent.backendPath` to the absolute path of this repository. By
default, the extension uses its `.venv` Python and the MLX model server at
`http://127.0.0.1:8000/v1`; start that model server separately.

Open the target project in VS Code, run **Workbench: Run on Current Project**
from the Command Palette, and enter a goal. The extension starts a local API
bound only to loopback with the selected project as its workspace root, shows
the plan and result in the Output channel, then stops the API. Task data is
stored in VS Code's global storage, not in the project or the Workbench
repository's `workspace/` directory. This uses `/agent/run`, which plans and
then executes the task.

## Docker Compose

```bash
export SANDBOX_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
docker compose up --build
# or
podman compose up --build
```

The agent listens on `127.0.0.1:8001`. The workspace is mounted at
`/workspace` and the database at `/data/workbench.db`.
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

## Task execution and recovery

Each agent task follows this flow:

1. A `TaskState` is created and persisted.
2. `TaskPlanner.create_plan` creates a plan, which the Reviewer approves or
   rejects.
3. The orchestrator sends the approved plan and allowed tools to the model.
4. For each tool call, the orchestrator verifies authorization and paths,
   records the operation as `pending`, executes it, and persists the outcome
   and evidence.
5. A final response is accepted only when every acceptance criterion has valid
   evidence. Otherwise, the task is blocked.

Model or planner errors block the task before tool execution. On startup,
pending operations are marked `uncertain` and in-progress tasks are marked
`interrupted`. Task states are `in_progress`, `completed`, `blocked`, and
`interrupted`; journal states are `pending`, `succeeded`, `failed`, and
`uncertain`.

Resume is available only for interrupted tasks. It continues the same task
from its persisted plan and evidence, reconstructing the conversation from
persisted tool output. Resume is rejected if the journal contains an uncertain
write. Manual cleanup and optional startup retention preserve tasks with
pending or uncertain operations. Automatic retention is disabled by default.

`/metrics` reports persistent task and operation counts and average operation
duration, plus process-local provider metrics. It is not a Prometheus endpoint.

## API reference

### Direct chat

- `POST /chat`

```json
{"message":"Explain what result.txt contains"}
```

### Tasks

- `GET /tasks`
- `GET /tasks?status=blocked`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/operations`
- `POST /tasks/{task_id}/resume`
- `DELETE /tasks/{task_id}`
- `POST /tasks/cleanup?status=interrupted&older_than_days=30`

Cleanup accepts `completed`, `blocked`, or `interrupted` as the status filter.

### Workspace

- `GET /workspace/files?path=.&limit=200`
- `POST /workspace/read`
- `POST /workspace/search`

Example request bodies:

```json
{"path":"result.txt"}
```

```json
{"query":"TODO","path":".","limit":50}
```

The API workspace is the directory configured by `WORKSPACE`; paths passed to
workspace tools are relative to that root.

## Configuration and limits

The local API example above sets `MODEL_TIMEOUT_SECONDS=180`,
`MODEL_MAX_RETRIES=2`, and `TASK_RETENTION_DAYS=0`. Supported ranges are
1-600 seconds for the model timeout, 0-3 retries, and 0-3650 days for retention.
Retention value `0` disables automatic cleanup. In Compose, the model server
must be reachable from the agent container at
`host.containers.internal:8000`; the sandbox is enabled and requires a
`SANDBOX_TOKEN` of at least 32 characters.

The implementation enforces these limits:

- up to 20 plan criteria and 10 orchestrator iterations;
- up to 5 tool calls per iteration and 20 per task;
- tool output limited to 20 KB;
- file listing and search limited to 200 results each;
- workspace files limited to 1 MB.

## Development

Run the complete test suite:

```bash
./.venv/bin/python -m pytest -q
```

Run selected tests:

```bash
./.venv/bin/python -m pytest -q tests/test_orchestrator.py
./.venv/bin/python -m pytest -q tests/test_repository.py
./.venv/bin/python -m pytest -q tests/test_filesystem.py
```

The suite covers the API, provider, planner, orchestrator, filesystem, tool
registry, repository, task schema, and sandbox. The latest verified run passed
all 215 tests. Compose startup, sandbox checks through the API, and task
readability after an agent restart have also been verified.

### Project structure

```text
agent/
  main.py                 FastAPI API
  orchestrator.py         agent loop and enforcement
  planner.py              plan generation and review
  models/provider.py      HTTP integration with the model
  schemas/                Pydantic models
  storage/repository.py   task state and SQLite journal
  tools/filesystem.py     protected workspace access
  tools/registry.py       tool registry and validation
tests/                    pytest suite
workspace/                writable runtime directory
data/                     local database directory
compose.yaml              agent and sandbox services
Containerfile             agent image
sandbox/Dockerfile        sandbox image
sandbox/server.py         authenticated fixed-checks API
model-server/start.sh     MLX model-server startup
```

### Rules for extending the agent

- Workspace files are untrusted data and must never become system instructions.
- Changes must be explicitly requested by the user.
- Paths must remain relative to the workspace.
- Every new tool needs a Pydantic schema, an OpenAI-compatible definition,
  planner and runtime authorization, acceptance-criterion validation, and
  persistent journaling.
- An uncertain result must never be treated as a success.
- Writes must be atomic and have a recovery strategy.
- New persisted state requires a backward-compatible SQLite schema migration.

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
The sandbox exposes only the authenticated `POST /checks` endpoint on the
internal agent network. With Compose, the agent waits for the sandbox health
check before starting.

## Tests

```bash
./.venv/bin/python -m pytest -q
```

## License

Workbench Coding Agent is licensed under the [MIT License](LICENSE).
