import pytest

from agent.planner import validate_plan_paths
from agent.schemas.tasks import AcceptanceCriterion, TaskPlan
import asyncio
from agent.planner import ALLOWED_TOOLS


def make_plan(tool, path):
    return TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="test",
                description="Test criterion",
                required_tool=tool,
                required_path=path,
            )
        ]
    )


@pytest.mark.parametrize(
    "tool",
    [
        "read_workspace_file",
        "write_workspace_file",
        "edit_workspace_file",
    ],
)
@pytest.mark.parametrize(
    "path",
    [
        "result.txt",
        "src/main.py",
        "src/components/App.py",
        "folder/subfolder/file.py",
    ],
)
def test_accepts_valid_relative_paths(tool, path):
    validate_plan_paths(make_plan(tool, path))


@pytest.mark.parametrize(
    "tool",
    [
        "read_workspace_file",
        "write_workspace_file",
        "edit_workspace_file",
    ],
)
@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "/etc/passwd",
        "../../etc/passwd",
        "../secret.txt",
        "folder/../../../secret.txt",
        r"folder\secret.txt",
        "a" * 501,
    ],
)
def test_rejects_invalid_paths(tool, path):
    with pytest.raises(ValueError):
        validate_plan_paths(make_plan(tool, path))


@pytest.mark.parametrize(
    "tool",
    [
        "search_workspace_code",
    ],
)
@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs",
        "src/components",
        ".",
    ],
)
def test_accepts_valid_paths_for_search_tool(tool, path):
    validate_plan_paths(make_plan(tool, path))


@pytest.mark.parametrize(
    "tool",
    [
        "search_workspace_code",
    ],
)
@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc",
        "../outside",
        "folder/../outside",
        r"folder\outside",
        "a" * 501,
    ],
)
def test_rejects_invalid_paths_for_search_tool(tool, path):
    with pytest.raises(ValueError):
        validate_plan_paths(make_plan(tool, path))


def test_rejects_unexpected_path_for_list_tool():
    with pytest.raises(ValueError, match="Unexpected required_path"):
        validate_plan_paths(make_plan("list_workspace_files", "README.md"))


@pytest.mark.parametrize(
    "tool",
    [
        "list_workspace_files",
        "search_workspace_code",
    ],
)
def test_accepts_missing_path_for_other_tools(tool):
    validate_plan_paths(make_plan(tool, None))


def test_write_workspace_file_is_allowed():
    assert "write_workspace_file" in ALLOWED_TOOLS
    assert "edit_workspace_file" in ALLOWED_TOOLS

import json

from agent.planner import TaskPlanner


class FakeProvider:
    model = "fake-model"

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0
        self.messages = []

    async def complete(self, messages, tools=None):
        response = self.responses[self.calls]
        self.calls += 1
        self.messages.append(messages)

        return {
            "message": {
                "content": json.dumps(response)
            }
        }

def test_planner_accepts_requested_file_write():
    provider = FakeProvider(
        responses=[
            {
                "criteria": [
                    {
                        "criterion_id": "write_readme",
                        "description": "Modify README.md",
                        "required_tool": "write_workspace_file",
                        "required_path": "README.md",
                    }
                ]
            },
            {
                "approved": True,
                "issues": [],
            },
        ]
    )

    planner = TaskPlanner(provider)

    plan = asyncio.run(
        planner.create_plan("Modify the README.md file")
    )

    assert len(plan.criteria) == 1
    assert plan.criteria[0].required_tool == "write_workspace_file"
    assert plan.criteria[0].required_path == "README.md"
    assert provider.calls == 2

def test_planner_retries_after_reviewer_rejection():
    provider = FakeProvider(
        responses=[
            # First plan: incorrect path
            {
                "criteria": [
                    {
                        "criterion_id": "write_readme",
                        "description": "Modify README.md",
                        "required_tool": "write_workspace_file",
                        "required_path": "docs/README.md",
                    }
                ]
            },
            # Il Reviewer rifiuta
            {
                "approved": False,
                "issues": ["The requested path is incorrect"],
            },
            # Second plan: correct path
            {
                "criteria": [
                    {
                        "criterion_id": "write_readme",
                        "description": "Modify README.md",
                        "required_tool": "write_workspace_file",
                        "required_path": "README.md",
                    }
                ]
            },
            # Il Reviewer approva
            {
                "approved": True,
                "issues": [],
            },
        ]
    )

    planner = TaskPlanner(provider)

    plan = asyncio.run(
        planner.create_plan("Modify the README.md file")
    )

    assert plan.criteria[0].required_path == "README.md"
    assert plan.criteria[0].required_tool == "write_workspace_file"
    assert provider.calls == 4

def test_planner_fails_after_three_rejections():
    provider = FakeProvider(
        responses=[
            {
                "criteria": [
                    {
                        "criterion_id": "write_readme",
                        "description": "Modify README.md",
                        "required_tool": "write_workspace_file",
                        "required_path": "README.md",
                    }
                ]
            },
            {"approved": False, "issues": ["Incorrect path"]},
            {
                "criteria": [
                    {
                        "criterion_id": "write_readme",
                        "description": "Modify README.md",
                        "required_tool": "write_workspace_file",
                        "required_path": "README.md",
                    }
                ]
            },
            {"approved": False, "issues": ["Incorrect operation"]},
            {
                "criteria": [
                    {
                        "criterion_id": "write_readme",
                        "description": "Modify README.md",
                        "required_tool": "write_workspace_file",
                        "required_path": "README.md",
                    }
                ]
            },
            {"approved": False, "issues": ["Plan is still invalid"]},
        ]
    )

    planner = TaskPlanner(provider)

    with pytest.raises(ValueError, match="Plan is still invalid"):
        asyncio.run(
            planner.create_plan("Modify the README.md file")
        )

    assert provider.calls == 6

@pytest.mark.parametrize(
    "tool,path",
    [
        ("read_workspace_file", "README.md"),
        ("write_workspace_file", "README.md"),
        ("search_workspace_code", None),
        ("list_workspace_files", None),
    ],
)
def test_planner_rejects_duplicate_tool_path_criteria(tool, path):
    criteria = [
        {
            "criterion_id": "criterion_1",
            "description": "First operation",
            "required_tool": tool,
            "required_path": path,
        },
        {
            "criterion_id": "criterion_2",
            "description": "Second operation",
            "required_tool": tool,
            "required_path": path,
        },
    ]

    provider = FakeProvider(
        responses=[
            {"criteria": criteria},
        ]
    )

    planner = TaskPlanner(provider)

    with pytest.raises(ValueError, match="Duplicate tool and path"):
        asyncio.run(planner.create_plan("Run two operations"))


def test_planner_accepts_requested_file_edit():
    provider = FakeProvider(
        responses=[
            {
                "criteria": [
                    {
                        "criterion_id": "edit_readme",
                        "description": "Update README.md",
                        "required_tool": "edit_workspace_file",
                        "required_path": "README.md",
                    }
                ]
            },
            {"approved": True, "issues": []},
        ]
    )

    plan = asyncio.run(
        TaskPlanner(provider).create_plan("Add a missing section to README.md")
    )

    assert plan.criteria[0].required_tool == "edit_workspace_file"
    assert plan.criteria[0].required_path == "README.md"
    assert provider.calls == 2


def test_planner_accepts_document_creation_without_document_body_in_plan():
    provider = FakeProvider(
        responses=[
            {
                "criteria": [
                    {
                        "criterion_id": "create_contributing",
                        "description": (
                            "Create an English CONTRIBUTING.md with "
                            "guidelines for contributing via pull requests."
                        ),
                        "required_tool": "write_workspace_file",
                        "required_path": "CONTRIBUTING.md",
                    }
                ]
            },
            {"approved": True, "issues": []},
        ]
    )

    plan = asyncio.run(
        TaskPlanner(provider).create_plan(
            "Add CONTRIBUTING.md with guidelines for contributing via PRs. In English."
        )
    )

    assert plan.criteria[0].required_tool == "write_workspace_file"
    assert plan.criteria[0].required_path == "CONTRIBUTING.md"
    reviewer_instructions = provider.messages[1][0]["content"]
    assert "Do not require the plan to contain a draft, full text" in (
        reviewer_instructions
    )
    assert provider.calls == 2


def test_planner_accepts_search_scoped_to_readme():
    provider = FakeProvider(
        responses=[
            {
                "criteria": [
                    {
                        "criterion_id": "search_readme",
                        "description": "Analyze README.md",
                        "required_tool": "search_workspace_code",
                        "required_path": "README.md",
                    }
                ]
            },
            {"approved": True, "issues": []},
        ]
    )

    plan = asyncio.run(
        TaskPlanner(provider).create_plan(
            "Analyze the README.md. Add sections if anything is missing"
        )
    )

    assert plan.criteria[0].required_tool == "search_workspace_code"
    assert plan.criteria[0].required_path == "README.md"
    assert provider.calls == 2


def test_planner_only_allows_fixed_checks_when_sandbox_is_enabled():
    plan_response = {
        "criteria": [
            {
                "criterion_id": "run_checks",
                "description": "Run the fixed tests",
                "required_tool": "run_workspace_checks",
                "required_path": None,
            }
        ]
    }

    disabled_planner = TaskPlanner(
        FakeProvider([plan_response]),
        sandbox_enabled=False,
    )
    with pytest.raises(ValueError, match="Unauthorized tool"):
        asyncio.run(disabled_planner.create_plan("Run the tests"))

    enabled_planner = TaskPlanner(
        FakeProvider([plan_response, {"approved": True, "issues": []}]),
        sandbox_enabled=True,
    )
    plan = asyncio.run(
        enabled_planner.create_plan("Run the project tests")
    )
    assert plan.criteria[0].required_tool == "run_workspace_checks"