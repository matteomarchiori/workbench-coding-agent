const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { spawn } = require("node:child_process");
const vscode = require("vscode");

const MODEL_TIMEOUT_SECONDS = 180;
const MODEL_WRITE_TIMEOUT_SECONDS = 600;
const MAX_API_RESPONSE_BYTES = 2_000_000;
let outputChannel;

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function selectWorkspaceFolder() {
  const folders = vscode.workspace.workspaceFolders ?? [];

  if (folders.length === 0) {
    throw new Error("Open a project folder in VS Code before starting the workbench.");
  }

  if (folders.length === 1) {
    return folders[0];
  }

  const selected = await vscode.window.showQuickPick(
    folders.map((folder) => ({
      label: folder.name,
      description: folder.uri.fsPath,
      folder,
    })),
    { placeHolder: "Choose the project to run the workbench on" },
  );

  return selected?.folder;
}

function resolvePythonPath(config, backendPath) {
  const configuredPath = config.get("pythonPath", "").trim();

  if (configuredPath) {
    if (!path.isAbsolute(configuredPath) || !fs.existsSync(configuredPath)) {
      throw new Error("workbenchAgent.pythonPath must point to an existing Python executable.");
    }
    return configuredPath;
  }

  const defaultPath = process.platform === "win32"
    ? path.join(backendPath, ".venv", "Scripts", "python.exe")
    : path.join(backendPath, ".venv", "bin", "python");

  if (!fs.existsSync(defaultPath)) {
    throw new Error(
      `Python was not found at ${defaultPath}. Configure workbenchAgent.pythonPath.`,
    );
  }

  return defaultPath;
}

async function getFreePort() {
  const server = net.createServer();

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("Unable to assign a local port to the API.");
  }

  const { port } = address;
  await new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });

  return port;
}

function addOutput(buffer, chunk) {
  return (buffer + chunk.toString()).slice(-16_000);
}

async function startBackend({ backendPath, pythonPath, workspacePath, databasePath, config }) {
  const port = await getFreePort();
  const child = spawn(
    pythonPath,
    [
      "-m",
      "uvicorn",
      "agent.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    {
      cwd: backendPath,
      env: {
        ...process.env,
        WORKSPACE: workspacePath,
        DATABASE_PATH: databasePath,
        MODEL_BASE_URL: config.get("modelBaseUrl", "http://127.0.0.1:8000/v1"),
        MODEL_NAME: config.get("modelName", "Qwen/Qwen3-8B-MLX-4bit"),
        MODEL_TIMEOUT_SECONDS: String(MODEL_TIMEOUT_SECONDS),
        MODEL_WRITE_TIMEOUT_SECONDS: String(MODEL_WRITE_TIMEOUT_SECONDS),
        MODEL_MAX_RETRIES: "2",
        TASK_RETENTION_DAYS: "0",
        SANDBOX_ENABLED: "false",
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  let processOutput = "";
  let spawnError;
  child.stdout.on("data", (chunk) => {
    processOutput = addOutput(processOutput, chunk);
  });
  child.stderr.on("data", (chunk) => {
    processOutput = addOutput(processOutput, chunk);
  });
  child.once("error", (error) => {
    spawnError = error;
  });

  const exitPromise = new Promise((resolve) => {
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });

  const apiUrl = `http://127.0.0.1:${port}`;

  try {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (spawnError) {
        throw new Error(`Failed to start the API: ${spawnError.message}`);
      }

      if (child.exitCode !== null || child.signalCode !== null) {
        const exit = await exitPromise;
        throw new Error(
          `The API exited before becoming available (${exit.code ?? exit.signal}).\n${processOutput}`,
        );
      }

      try {
        const response = await fetch(`${apiUrl}/health`, {
          signal: AbortSignal.timeout(1_000),
        });
        if (response.ok) {
          return { child, exitPromise, apiUrl };
        }
      } catch {
        await delay(250);
      }
    }

    throw new Error(`Timed out while starting the API.\n${processOutput}`);
  } catch (error) {
    await stopBackend({ child, exitPromise });
    throw error;
  }
}

async function stopBackend(backend) {
  if (!backend || !backend.child.pid) {
    return;
  }

  const { child, exitPromise } = backend;
  if (child.exitCode !== null || child.signalCode !== null) {
    return;
  }

  child.kill("SIGTERM");
  const exited = await Promise.race([
    exitPromise.then(() => true),
    delay(5_000).then(() => false),
  ]);

  if (!exited && child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    await exitPromise;
  }
}

async function runAgent(apiUrl, goal) {
  const response = await fetch(`${apiUrl}/agent/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: goal }),
    signal: AbortSignal.timeout(2 * 60 * 60 * 1_000),
  });

  const body = await response.text();
  if (Buffer.byteLength(body, "utf8") > MAX_API_RESPONSE_BYTES) {
    throw new Error("The API response exceeds the allowed size.");
  }

  if (!response.ok) {
    throw new Error(`API workbench (${response.status}): ${body}`);
  }

  return JSON.parse(body);
}

function formatResult(projectPath, goal, result) {
  const lines = [
    `Project: ${projectPath}`,
    `Request: ${goal}`,
    `Status: ${result.task?.status ?? "unknown"}`,
    "",
    "Validated plan:",
  ];

  const criteria = result.task?.plan?.criteria ?? [];
  for (const criterion of criteria) {
    lines.push(`- ${criterion.description}`);
  }

  lines.push("", "Result:", result.response ?? "(no response)");

  if (result.task?.errors?.length) {
    lines.push("", "Details:", ...result.task.errors.map((error) => `- ${error}`));
  }

  return lines.join("\n");
}

async function runCommand(context) {
  const folder = await selectWorkspaceFolder();
  if (!folder) {
    return;
  }

  if (folder.uri.scheme !== "file") {
    throw new Error("This extension requires a project available as a local folder.");
  }

  const config = vscode.workspace.getConfiguration("workbenchAgent");
  const backendPath = config.get("backendPath", "").trim();
  if (!backendPath || !path.isAbsolute(backendPath)) {
    throw new Error(
      "Set workbenchAgent.backendPath to the absolute path of the Workbench Coding Agent repository.",
    );
  }

  const resolvedBackendPath = fs.realpathSync(backendPath);
  if (!fs.existsSync(path.join(resolvedBackendPath, "agent", "main.py"))) {
    throw new Error("workbenchAgent.backendPath does not contain agent/main.py.");
  }

  const goal = await vscode.window.showInputBox({
    title: `Workbench — ${folder.name}`,
    prompt: "Describe what you want the agent to analyze or do in the project",
    placeHolder: "e.g. Analyze the architecture and suggest a plan...",
    ignoreFocusOut: true,
    validateInput: (value) => value.trim().length > 20_000
      ? "The request cannot exceed 20,000 characters."
      : undefined,
  });

  if (!goal?.trim()) {
    return;
  }

  const workspacePath = fs.realpathSync(folder.uri.fsPath);
  const projectId = crypto.createHash("sha256").update(workspacePath).digest("hex").slice(0, 16);
  const databaseDirectory = context.globalStorageUri.fsPath;
  fs.mkdirSync(databaseDirectory, { recursive: true });

  const output = outputChannel;
  output.clear();
  output.appendLine(`Starting workbench for ${workspacePath}`);

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `Workbench: ${folder.name}`,
      cancellable: false,
    },
    async () => {
      let backend;
      try {
        backend = await startBackend({
          backendPath: resolvedBackendPath,
          pythonPath: resolvePythonPath(config, resolvedBackendPath),
          workspacePath,
          databasePath: path.join(databaseDirectory, `tasks-${projectId}.db`),
          config,
        });

        const result = await runAgent(backend.apiUrl, goal.trim());
        output.appendLine(formatResult(workspacePath, goal.trim(), result));
        output.show(true);

        if (result.task?.status === "completed") {
          vscode.window.showInformationMessage("Workbench completed the task. Open Output to view the plan and result.");
        } else {
          vscode.window.showWarningMessage("Workbench did not complete the task. Open Output for details.");
        }
      } catch (error) {
        const details = error instanceof Error ? error.message : String(error);
        output.appendLine(`Error: ${details}`);
        output.show(true);
        throw error;
      } finally {
        await stopBackend(backend);
      }
    },
  ).catch((error) => {
    const message = error instanceof Error ? error.message : String(error);
    vscode.window.showErrorMessage(message);
  });
}

function activate(context) {
  outputChannel = vscode.window.createOutputChannel("Workbench Coding Agent");
  const command = vscode.commands.registerCommand(
    "workbenchAgent.run",
    () => runCommand(context),
  );
  context.subscriptions.push(command, outputChannel);
}

function deactivate() {}

module.exports = { activate, deactivate };
