import json

from pydantic import ValidationError

from agent.tools.filesystem import (
    list_files,
    read_file,
    search_code,
    write_file,
)

from agent.schemas.tools import (
    ListFilesArgs,
    ReadFileArgs,
    SearchCodeArgs,
    RunWorkspaceChecksArgs,
    WriteFileArgs,
)

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_workspace_files",
            "description": "Elenca i file presenti nel workspace.",
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
            "description": "Legge un file nel workspace.",
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
            "description": "Cerca una stringa nei file del workspace.",
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
                "Esegue nel sandbox pytest senza argomenti e un controllo "
                "statico della sintassi Python. Non accetta comandi o parametri."
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
            "name": "write_workspace_file",
            "description": (
                "Crea un file nel workspace oppure lo sovrascrive "
                "se overwrite è true. Prima della sovrascrittura "
                "crea un backup del contenuto originale."
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

        if len(arguments) > 10_000:
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
    "run_workspace_checks": (RunWorkspaceChecksArgs, None),
}