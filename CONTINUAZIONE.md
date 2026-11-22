# LocalCoder Development Handoff

Handoff notes for continuing development of the local coding agent.
Last verified: the complete pytest suite passed with **215 tests**.

Image builds, a live Podman run, an end-to-end API call, and an agent restart
were verified. The persisted task and journal remained readable after restart.

The sandbox uses a fixed boundary: pytest with no arguments and AST parsing,
only on the user's explicit request. It does not accept shell commands or
arbitrary commands. In the current workspace, pytest returned exit code 5
(`no tests ran`): the runner correctly reported that there were no tests, and
the AST check was marked skipped because there were no Python files to check.
This is expected because `./workspace` does not contain the LocalCoder project
test suite.

## Project goal

LocalCoder is a local coding agent made up of:

- a FastAPI API;
- a model-based planner and a reviewer using the same model;
- an orchestrator that authorizes and records tool operations;
- tools restricted to the workspace filesystem;
- SQLite persistence for tasks and operations;
- a local MLX model server.

The main service is still under active development. The current priority is to
complete the end-to-end flow and make recovery after interruptions or errors
reliable.

## Current status

### Implemented

- FastAPI API in [agent/main.py](agent/main.py).
- OpenAI-compatible model client in [agent/models/provider.py](agent/models/provider.py).
- Plan generation and review through Planner/Reviewer in
  [agent/planner.py](agent/planner.py).
- Iterative execution, budget limits, and tool authorization in
  [agent/orchestrator.py](agent/orchestrator.py).
- Supported operations:
  - `list_workspace_files`
  - `read_workspace_file`
  - `search_workspace_code`
  - `write_workspace_file`
- Filesystem protections in [agent/tools/filesystem.py](agent/tools/filesystem.py):
  - workspace confinement;
  - rejection of absolute paths and traversal;
  - rejection of symlinks;
  - 1 MB file limit;
  - atomic writes;
  - backups of existing files;
  - concurrent-modification check before overwrite.
- SQLite persistence and legacy-state migration in
  [agent/storage/repository.py](agent/storage/repository.py).
- Operation journal with `pending`, `succeeded`, `failed`, and `uncertain` states.
- Automatic startup recovery:
  - pending operations are marked uncertain;
  - in-progress tasks are marked interrupted.
- Main containers described in [compose.yaml](compose.yaml).
- Unit and in-process integration tests in [tests/](tests/).

### Known limits

- The sandbox exposes only `POST /checks`, protected by a bearer token and not
  published on the host.
- `run_workspace_checks` runs pytest with fixed arguments and AST analysis,
  with time and output limits.
- Workspace pytest tests run in the non-root sandbox container; the workspace
  is mounted read-write.
- Resume continues the same task and its plan/evidence; it reconstructs the
  conversation from persisted tool output.
- Resume is deliberately blocked if a write is in the uncertain state.
- The sandbox is reachable only by the agent on the internal network and does
  not publish host ports.
- `/metrics` exposes persistent task/operation counts and average operation
  duration, along with process-local provider metrics; it is not a Prometheus
  endpoint.
- Timeouts, retries, and retention are configurable.
  `TASK_RETENTION_DAYS=0` disables automatic cleanup.
- Manual cleanup preserves tasks with `pending` or `uncertain` operations.
- Startup retention considers only terminal tasks and preserves tasks with
  `pending` or `uncertain` journal entries.
- The planner accepts at most 20 criteria; the orchestrator iteration limit is
  10.
- The orchestrator enforces a maximum of 5 tool calls per iteration and 20 in
  total.
- Tool outputs are limited to 20 KB.
- Listing and search are limited to 200 results each.
- `write_workspace_file` creates backups only when overwriting existing files.

## Local development

### Python environment

From the project root:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install pytest
```

Start the API:

```bash
MODEL_BASE_URL=http://127.0.0.1:8000/v1 \
MODEL_NAME=Qwen/Qwen3-8B-MLX-4bit \
WORKSPACE="$PWD/workspace" \
DATABASE_PATH="$PWD/data/localcoder.db" \
.venv/bin/uvicorn agent.main:app --reload --host 127.0.0.1 --port 8001
```

### Model server

On macOS with MLX:

```bash
cd model-server
./start.sh
```

The script uses `model-server/.venv` and serves the model on port 8000.

### Compose

Create a random token of at least 32 characters and start with a Docker- or
Podman-compatible runtime:

```bash
export SANDBOX_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
docker compose up --build
# or
podman compose up --build
```

The agent is exposed on `127.0.0.1:8001`.

The model must be reachable from the container at
`host.containers.internal:8000`. Optional settings:
`MODEL_TIMEOUT_SECONDS` (1-600, default 180), `MODEL_MAX_RETRIES` (0-3, default
2), and `TASK_RETENTION_DAYS` (0-3650, default 0). A non-zero retention value
enables startup cleanup of expired terminal tasks. The sandbox is enabled in
Compose and requires `SANDBOX_TOKEN`; when running locally without the sandbox
service, keep it disabled.

## API

### Health and metrics

- `GET /health`
- `GET /health/model`
- `GET /metrics`

### Direct chat

- `POST /chat`

```json
{"message":"Explain what workspace/result.txt contains"}
```

### Agent

- `POST /agent/run`

```json
{"message":"Read result.txt"}
```

To run the fixed sandbox checks:

```json
{"message":"Run the project tests"}
```

The API workspace is the directory configured by `WORKSPACE`; paths passed to
tools are relative to that root.

### Tasks

- `GET /tasks`
- `GET /tasks?status=blocked`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/operations`
- `POST /tasks/{task_id}/resume`
- `DELETE /tasks/{task_id}`
- `POST /tasks/cleanup?status=interrupted&older_than_days=30`

Available states: `in_progress`, `completed`, `blocked`, and `interrupted`.
Resume is allowed only for `interrupted` tasks and is rejected if an uncertain
write exists. Cleanup accepts `completed`, `blocked`, or `interrupted`.

### Workspace

- `GET /workspace/files?path=.&limit=200`
- `POST /workspace/read`
- `POST /workspace/search`

Read example:

```json
{"path":"result.txt"}
```

Search example:

```json
{"query":"TODO","path":".","limit":50}
```

## Execution flow

1. A `TaskState` is created and saved.
2. `TaskPlanner.create_plan` generates the plan.
3. The Reviewer approves or rejects the plan.
4. The orchestrator sends the valid plan and allowed tools to the model.
5. For each tool call, it:
   - verifies that the tool is in the plan;
   - validates the requested path;
   - records the operation as `pending`;
   - executes the tool;
   - records `succeeded`, `failed`, or `uncertain`;
   - adds the evidence to the task.
6. The final response is accepted only when every criterion has valid evidence.
7. The task is marked `completed` on success and `blocked` otherwise.

A model or planner error blocks the task before execution.

## Rules to preserve

- Workspace files are untrusted data and must never become system instructions.
- Changes must be explicitly requested by the user.
- Paths must be relative to the workspace.
- Every new tool must have:
  - a Pydantic schema;
  - an OpenAI-compatible definition;
  - planner authorization;
  - runtime authorization in the orchestrator;
  - criterion validation;
  - persistent journaling.
- An uncertain result must not be treated as a success.
- Writes must be atomic and have a recovery strategy.
- A new persisted state requires a backward-compatible SQLite schema migration.

## Relevant structure

```text
agent/
  main.py                 FastAPI API
  orchestrator.py         agent loop and enforcement
  planner.py              plan generation/review
  models/provider.py      HTTP integration with the model
  schemas/                Pydantic models
  storage/repository.py   task state and SQLite journal
  tools/filesystem.py     protected workspace access
  tools/registry.py       tool registry and validation
tests/                    pytest suite
workspace/                writable runtime directory
data/localcoder.db        local database
compose.yaml              agent/sandbox services
Containerfile             agent image
sandbox/Dockerfile        sandbox image
sandbox/server.py         authenticated fixed-checks API
model-server/start.sh     MLX model-server startup
```

## Tests

Run the complete suite:

```bash
./.venv/bin/python -m pytest -q
```

Run selected tests:

```bash
./.venv/bin/python -m pytest -q tests/test_orchestrator.py
./.venv/bin/python -m pytest -q tests/test_repository.py
./.venv/bin/python -m pytest -q tests/test_filesystem.py
```

At the last test run:

```text
215 passed in 1.88s
```

Podman Compose validated the configuration; images were built, the sandbox
became healthy, the tool was called through the API, and persistent task
listing succeeded after a controlled agent restart. The workspace test runner
correctly reported that the workspace contained no tests.

## Suggested steps completed

1. Exposed the operation journal through `GET /tasks/{task_id}/operations`.
2. Added API tests using `TestClient`, a mock provider, and a temporary
   repository.
3. Added an operational README covering startup, API, architecture, and
   security.
4. Added a minimal lifecycle with task cancellation and cleanup by status/age.
5. Added task and tool-call logging with `task_id` and `call_id`.
6. Hardened tool-call validation and provider fields.
7. Added same-task resume with transcript reconstruction and reconciliation of
   persisted operations not yet copied into task state.
8. Added FastAPI lifespan recovery, configurable provider timeouts/retries,
   and aggregate task/operation metrics.
9. Ensured cleanup does not delete tasks with pending or uncertain operations.
10. Added fault-injection tests: if a journal update fails after a tool call,
    the task is blocked and the operation is not considered successful.
11. Added opt-in automatic retention and in-process provider metrics.
12. Added a sandbox with an authenticated endpoint and fixed checks, without a
    shell interface.
13. **Real deployment:** validated with Podman Compose on November 22, 2026.
    The API reached the host's MLX model, ran the protected sandbox tool, and
    kept tasks readable after a controlled restart.
