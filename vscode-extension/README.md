# Workbench Coding Agent for VS Code

This extension runs the Workbench API locally with the currently selected
VS Code project as its workspace root. It does not copy files into the
Workbench repository's `workspace/` directory.

## Requirements

- The Workbench repository with its Python dependencies installed.
- A running OpenAI-compatible model server. For the MLX server in this
  repository, run `model-server/start.sh` from the Workbench repository.

## Install

From the Workbench repository:

```bash
cd vscode-extension
npm install
npm run package
```

In VS Code, run **Extensions: Install from VSIX...** and select the generated
`workbench-coding-agent-0.1.0.vsix`.

Configure these User settings in VS Code (JSON):

```json
{
  "workbenchAgent.backendPath": "/absolute/path/to/localcoder"
}
```

The extension uses `<backendPath>/.venv/bin/python` by default on macOS and
Linux, and `<backendPath>/.venv/Scripts/python.exe` on Windows. Set
`workbenchAgent.pythonPath` if the Python environment is elsewhere.
`workbenchAgent.modelBaseUrl` and `workbenchAgent.modelName` can be changed
when using a different model server.

## Use

Open the desired project in VS Code, then run **Workbench: Run on Current
Project** from the Command Palette. Enter a goal. In a multi-root workspace,
choose which project folder to use.

The extension starts a loopback-only API process for that request, sets its
workspace root to the selected project, sends the task, displays the plan and
result in the **Workbench Coding Agent** Output channel, then stops the API.
Task records are stored in the extension's VS Code global storage, separate
from the target project.

File-writing requests use a 600-second model timeout and a 4096-token response
limit by default; the normal model timeout remains 180 seconds.

`/agent/run` plans and then executes the task. Ask for analysis or a plan
without requesting changes when you only want a review. The agent has no shell
tool; writes are limited to its approved workspace tools.
