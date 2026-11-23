import json
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.models.provider import ModelProvider
from agent.schemas.tasks import TaskPlan
import posixpath


def validate_plan_paths(plan: TaskPlan) -> None:
    required_path_tools = {
        "read_workspace_file",
        "write_workspace_file",
        "edit_workspace_file",
    }
    optional_path_tools = {
        "search_workspace_code",
    }

    for criterion in plan.criteria:
        path = criterion.required_path

        criterion_keys = [
            (criterion.required_tool, criterion.required_path)
            for criterion in plan.criteria
        ]

        if len(criterion_keys) != len(set(criterion_keys)):
            raise ValueError("Duplicate tool and path criteria")

        if criterion.required_tool in required_path_tools and not path:
            raise ValueError(f"Missing required path: {criterion.criterion_id}")

        if criterion.required_tool in required_path_tools | optional_path_tools:
            if path is None:
                continue

            if len(path) > 500:
                raise ValueError("Required path exceeds 500 characters")

            if not path or path.startswith("/") or "\\" in path:
                raise ValueError(f"Invalid required path: {path}")

            parts = path.split("/")
            if any(part in ("", "..") for part in parts):
                raise ValueError(f"Invalid workspace path: {path}")

            normalized = posixpath.normpath(path)

            if (
                criterion.required_tool in required_path_tools
                and normalized == "."
            ):
                raise ValueError(f"Missing required path: {criterion.criterion_id}")

            if normalized == ".." or normalized.startswith("../"):
                raise ValueError(f"Invalid workspace path: {path}")

        elif path is not None:
            raise ValueError(
                f"Unexpected required_path for tool "
                f"{criterion.required_tool}: {path}"
            )


ALLOWED_TOOLS = {
    "list_workspace_files",
    "read_workspace_file",
    "search_workspace_code",
    "write_workspace_file",
    "edit_workspace_file",
}
SANDBOX_TOOL = "run_workspace_checks"

PLANNER_SYSTEM_PROMPT = """
You are the Planner for a local coding agent.

Your only task is to turn the user's request into a structured work plan.

Do not perform operations.
Do not invent results.
Do not claim that tasks have already been completed.

Return only a JSON object with this structure:

{
  "criteria": [
    {
      "criterion_id": "unique_identifier",
      "description": "Requirement description",
      "required_tool": "tool_name",
      "required_path": null
    }
  ]
}

AVAILABLE TOOLS:

- list_workspace_files:
  Lists files in the workspace.

- read_workspace_file:
  Reads a file.
  Requires required_path to contain the file's relative path.

- search_workspace_code:
  Searches for a string in the code. It can receive required_path with
  the file or directory to search; if omitted, it searches the entire workspace.
  Do not use it to read or analyze the complete contents of a file.

- write_workspace_file:
  Creates a file or replaces its entire contents.
  Requires required_path to contain the file's relative path.
  Use it only when the user requests creating a file or replacing it fully.

- edit_workspace_file:
  Makes a precise text replacement in an existing file.
  Requires required_path with the file's relative path.
  old_text must match exactly once. Use equal old_text and new_text when
  the requested conditional change is not needed.

{{SANDBOX_TOOL_INSTRUCTIONS}}

RULES:

1. Create one criterion for each requested operational outcome.
2. Do not create criteria for operations that were not requested.
3. Use only the available tools.
4. Always specify required_path when reading or writing a file.
5. If a search is limited to a file or directory, specify required_path;
   otherwise, it may be null.
6. If the user asks to analyze a specific file, use read_workspace_file,
   not search_workspace_code.
7. If the user asks to add or correct content based on a file's contents,
   plan to read the file first, then use edit_workspace_file on that file.
8. Use paths relative to the workspace.
9. Criterion identifiers must be unique.
10. Do not add any text outside the JSON.
11. Do not propose file changes if the user only asks for analysis,
    explanations, or reading.
12. Specify the path of every file to be changed. Prefer
    edit_workspace_file for changes to existing files.
13. Use run_workspace_checks only when the user explicitly asks to run
    tests or checks.
14. For a requested new document, describe its purpose and requested topics
    in the criterion; do not include the full document text in the plan.
"""

REVIEWER_SYSTEM_PROMPT = """
You are the Reviewer for a local coding agent.

Determine whether a work plan fully satisfies the user's original request.

You will receive:
- the original request;
- the proposed plan.

Evaluate the plan as a sequence of future actions; do not require the
analysis or changes to have already been performed.

Plans describe operations using a criterion description, tool, and path.
The agent produces file contents when it executes the authorized write tool.
Do not require the plan to contain a draft, full text, or detailed prose for
a file it is supposed to create.

Check that:
1. Every requested operational outcome is represented.
2. No necessary operations are missing.
3. The selected tools match the operations.
4. File paths are correct when specified.
5. No unrequested operations are included.
6. When the user asks to add or correct a file based on its contents,
   the plan includes reading the file before editing that same file.
7. edit_workspace_file and write_workspace_file are included only when
   the user explicitly requests a change, including a change conditional
   on the analysis.
8. Every write operation has a specific path.
9. run_workspace_checks is included only when the user explicitly asks
   to run tests or checks.
10. A criterion that identifies the requested document purpose and topics,
    the write tool, and the target path is sufficient; do not request the
    document body in the plan.

Return JSON only:

{
  "approved": true,
  "issues": []
}

If the plan is incomplete:

{
  "approved": false,
  "issues": [
    "Description of the missing requirement"
  ]
}

Do not invent information.
Do not approve incomplete plans.
"""


class PlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    issues: list[str] = Field(default_factory=list, max_length=10)


class TaskPlanner:
    def __init__(
        self,
        provider: ModelProvider,
        sandbox_enabled: bool = False,
    ):
        self.provider = provider
        self.allowed_tools = set(ALLOWED_TOOLS)
        sandbox_instructions = ""

        if sandbox_enabled:
            self.allowed_tools.add(SANDBOX_TOOL)
            sandbox_instructions = (
                "- run_workspace_checks:\n"
                "  Runs pytest without arguments and a static Python "
                "syntax check in the isolated sandbox.\n"
                "  Accepts no custom commands; required_path must be null."
            )
        else:
            sandbox_instructions = (
                "run_workspace_checks is not available in this instance."
            )

        self.system_prompt = PLANNER_SYSTEM_PROMPT.replace(
            "{{SANDBOX_TOOL_INSTRUCTIONS}}",
            sandbox_instructions,
        )

    async def review_plan(
        self,
        goal: str,
        plan: TaskPlan,
    ) -> PlanReview:
        result = await self.provider.complete(
            messages=[
                {
                    "role": "system",
                    "content": REVIEWER_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        f"ORIGINAL REQUEST:\n{goal}\n\n"
                        f"PROPOSED PLAN:\n"
                        f"{json.dumps(plan.model_dump(), ensure_ascii=False, indent=2)}"
                    ),
                },
            ],
            tools=None,
        )

        content = result["message"].get("content")

        if not isinstance(content, str) or not content.strip():
            raise ValueError("Reviewer returned empty content")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("Reviewer returned invalid JSON") from exc

        return PlanReview.model_validate(data)

    async def create_plan(self, goal: str) -> TaskPlan:
        max_attempts = 3  # Initial plan plus two revisions
        feedback = None

        for attempt in range(max_attempts):
            user_content = goal

            if feedback:
                user_content += (
                    "\n\nREVIEW OF THE PREVIOUS PLAN:\n"
                    + "\n".join(f"- {issue}" for issue in feedback)
                    + "\n\nRevise the plan to address this feedback."
                )

            result = await self.provider.complete(
                messages=[
                    {
                        "role": "system",
                        "content": self.system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_content,
                    },
                ],
                tools=None,
            )

            message = result["message"]
            content = message.get("content")

            if not isinstance(content, str) or not content.strip():
                raise ValueError("Planner returned empty content")

            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError("Planner returned invalid JSON") from exc

            plan = TaskPlan.model_validate(data)

            for criterion in plan.criteria:
                if criterion.required_tool not in self.allowed_tools:
                    raise ValueError(
                        f"Unauthorized tool in plan: " f"{criterion.required_tool}"
                    )

                if (
                    criterion.required_tool == "read_workspace_file"
                    and not criterion.required_path
                ):
                    raise ValueError("File reading criterion requires a path")

            validate_plan_paths(plan)

            review = await self.review_plan(goal, plan)

            if review.approved and not review.issues:
                return plan

            feedback = review.issues or [
                "The plan was not approved. "
                "Review its coverage of the original request."
            ]

        raise ValueError(
            "Planner could not produce an approved plan after 2 revisions. "
            "Last reviewer feedback: " + "; ".join(feedback or [])
        )
