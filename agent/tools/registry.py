import json

from pydantic import ValidationError

from agent.tools.filesystem import (
    list_files,
    read_file,
    search_code,
    edit_file,
    write_file,
)

from agent.schemas.tools import (
    ListFilesArgs,
    ReadFileArgs,
    SearchCodeArgs,
    RunWorkspaceChecksArgs,
    EditFileArgs,
    WriteFileArgs,
)

MAX_TOOL_ARGUMENT_CHARS = 6_000_000

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_workspace_files",
            "description": "Lists files in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_workspace_file",
            "description": "Reads a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_workspace_code",
            "description": "Searches for a string in workspace files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_workspace_checks",
            "description": (
                "Runs pytest without arguments and a static Python syntax "
                "check in the sandbox. Accepts no commands or parameters."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_workspace_file",
            "description": (
                "Makes a precise text replacement in an existing workspace "
                "file. The old_text must occur exactly once. Creates a backup "
                "before changing the file. Set old_text and new_text equal "
                "when no change is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_workspace_file",
            "description": (
                "Creates a file in the workspace or overwrites it when "
                "overwrite is true. Before overwriting, creates a backup "
                "of the original contents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_tool(name: str, arguments: str) -> str:
    try:
        if name not in TOOL_DEFINITIONS_BY_NAME:
            raise ValueError(f"Tool not allowed: {name}")

        if len(arguments) > MAX_TOOL_ARGUMENT_CHARS:
            raise ValueError("Tool arguments too large")

        args = json.loads(arguments)

        if not isinstance(args, dict):
            raise ValueError("Arguments must be a JSON object")

        schema, function = TOOL_DEFINITIONS_BY_NAME[name]

        validated = schema.model_validate(args)
        values = validated.model_dump()

        if function is None:
            raise ValueError(f"{name} must be dispatched through the sandbox")

        if name == "list_workspace_files":
            result = function(
                relative_path=values["path"],
                limit=values["limit"],
            )

        elif name == "read_workspace_file":
            result = function(
                relative_path=values["path"],
            )

        elif name == "search_workspace_code":
            result = function(
                query=values["query"],
                relative_path=values["path"],
                limit=values["limit"],
            )

        elif name == "write_workspace_file":
            result = function(
                relative_path=values["path"],
                content=values["content"],
                overwrite=values["overwrite"],
            )

        elif name == "edit_workspace_file":
            result = function(
                relative_path=values["path"],
                old_text=values["old_text"],
                new_text=values["new_text"],
            )

        return json.dumps(
            {"ok": True, "result": result},
            ensure_ascii=False,
        )

    except json.JSONDecodeError:
        error = "Invalid JSON arguments"

    except ValidationError as exc:
        error = {
            "type": "validation_error",
            "details": exc.errors(include_url=False),
        }

    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        error = str(exc)

    return json.dumps(
        {"ok": False, "error": error},
        ensure_ascii=False,
    )


TOOL_DEFINITIONS_BY_NAME = {
    "list_workspace_files": (ListFilesArgs, list_files),
    "read_workspace_file": (ReadFileArgs, read_file),
    "search_workspace_code": (SearchCodeArgs, search_code),
    "write_workspace_file": (WriteFileArgs, write_file),
    "edit_workspace_file": (EditFileArgs, edit_file),
    "run_workspace_checks": (RunWorkspaceChecksArgs, None),
}