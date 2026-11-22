import asyncio
from unittest.mock import AsyncMock, patch
import json

from agent.orchestrator import AgentOrchestrator

from agent.schemas.tasks import AcceptanceCriterion, TaskPlan, TaskState


class FakeProvider:
    model = "fake-model"


def test_orchestrator_does_not_execute_tools_when_planner_fails():
    provider = FakeProvider()
    orchestrator = AgentOrchestrator(provider)

    orchestrator.planner.create_plan = AsyncMock(
        side_effect=ValueError("Planner could not produce an approved plan")
    )

    with patch("agent.orchestrator.execute_tool") as mock_execute:
        result = asyncio.run(orchestrator.run("Modifica README.md"))

    assert result["iterations"] == 0
    assert result["tool_calls"] == 0
    assert result["trace"] == []
    assert result["task"].status == "blocked"
    assert mock_execute.call_count == 0


def test_orchestrator_blocks_tool_not_authorized_by_plan():
    provider = FakeProvider()
    orchestrator = AgentOrchestrator(provider)

    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_readme",
                description="Leggi README.md",
                required_tool="read_workspace_file",
                required_path="README.md",
            )
        ]
    )

    orchestrator.planner.create_plan = AsyncMock(return_value=plan)

    provider.complete = AsyncMock(
        return_value={
            "finish_reason": "tool_calls",
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "write_workspace_file",
                            "arguments": (
                                '{"path":"README.md",'
                                '"content":"Modifica non autorizzata",'
                                '"overwrite":false}'
                            ),
                        },
                    }
                ],
            },
        }
    )

    with patch("agent.orchestrator.execute_tool") as mock_execute:
        result = asyncio.run(orchestrator.run("Leggi README.md"))

    assert result["task"].status == "blocked"
    assert result["tool_calls"] == 0
    assert result["trace"] == []
    assert mock_execute.call_count == 0
    assert any("not authorized" in error for error in result["task"].errors)


import pytest


@pytest.mark.parametrize(
    "path",
    [
        "other.txt",
        "../secret.txt",
        "../../etc/passwd",
        "/etc/passwd",
    ],
)
def test_orchestrator_blocks_unauthorized_read_paths(path):
    provider = FakeProvider()
    orchestrator = AgentOrchestrator(provider)

    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_readme",
                description="Leggi README.md",
                required_tool="read_workspace_file",
                required_path="README.md",
            )
        ]
    )

    orchestrator.planner.create_plan = AsyncMock(return_value=plan)

    provider.complete = AsyncMock(
        return_value={
            "finish_reason": "tool_calls",
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_456",
                        "type": "function",
                        "function": {
                            "name": "read_workspace_file",
                            "arguments": json.dumps({"path": path}),
                        },
                    }
                ],
            },
        }
    )

    with patch("agent.orchestrator.execute_tool") as mock_execute:
        result = asyncio.run(orchestrator.run("Leggi README.md"))

    assert result["task"].status == "blocked"
    assert result["tool_calls"] == 0
    assert result["trace"] == []
    assert mock_execute.call_count == 0

def test_orchestrator_completes_authorized_read():
    provider = FakeProvider()
    orchestrator = AgentOrchestrator(provider)

    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_result",
                description="Leggi result.txt",
                required_tool="read_workspace_file",
                required_path="result.txt",
            )
        ]
    )

    orchestrator.planner.create_plan = AsyncMock(return_value=plan)

    provider.complete = AsyncMock(
        side_effect=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_789",
                            "type": "function",
                            "function": {
                                "name": "read_workspace_file",
                                "arguments": '{"path":"result.txt"}',
                            },
                        }
                    ],
                },
            },
            {
                "finish_reason": "stop",
                "message": {
                    "content": "Il file contiene il risultato atteso.",
                    "tool_calls": [],
                },
            },
        ]
    )

    with patch(
        "agent.orchestrator.execute_tool",
        return_value=json.dumps(
            {"ok": True, "result": "contenuto del file"}
        ),
    ) as mock_execute:
        result = asyncio.run(orchestrator.run("Leggi result.txt"))

    assert result["task"].status == "completed"
    assert result["tool_calls"] == 1
    assert result["task"].plan.criteria[0].satisfied
    assert result["task"].evidence[0].validated
    assert mock_execute.call_count == 1

def test_orchestrator_does_not_complete_after_tool_failure():
    provider = FakeProvider()
    orchestrator = AgentOrchestrator(provider)

    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_result",
                description="Leggi result.txt",
                required_tool="read_workspace_file",
                required_path="result.txt",
            )
        ]
    )

    orchestrator.planner.create_plan = AsyncMock(return_value=plan)

    first_response = {
        "finish_reason": "tool_calls",
        "message": {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_failure",
                    "type": "function",
                    "function": {
                        "name": "read_workspace_file",
                        "arguments": '{"path":"result.txt"}',
                    },
                }
            ],
        },
    }

    premature_response = {
        "finish_reason": "stop",
        "message": {
            "content": "Ho letto il file correttamente.",
            "tool_calls": [],
        },
    }

    async def fake_complete(*args, **kwargs):
        if provider.complete.await_count == 1:
            return first_response
        return premature_response

    provider.complete = AsyncMock(side_effect=fake_complete)
    with patch(
        "agent.orchestrator.execute_tool",
        return_value=json.dumps(
            {"ok": False, "error": "File not found"}
        ),
    ) as mock_execute:
        result = asyncio.run(orchestrator.run("Leggi result.txt"))

    assert result["task"].status == "blocked"
    assert result["tool_calls"] == 1
    assert not result["task"].plan.criteria[0].satisfied
    assert not result["task"].evidence[0].validated
    assert mock_execute.call_count == 1

def test_orchestrator_blocks_when_provider_fails():
    provider = FakeProvider()
    orchestrator = AgentOrchestrator(provider)

    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_result",
                description="Leggi result.txt",
                required_tool="read_workspace_file",
                required_path="result.txt",
            )
        ]
    )

    orchestrator.planner.create_plan = AsyncMock(return_value=plan)
    provider.complete = AsyncMock(
        side_effect=RuntimeError("Model unavailable")
    )

    with patch("agent.orchestrator.execute_tool") as mock_execute:
        result = asyncio.run(orchestrator.run("Leggi result.txt"))

    assert result["task"].status == "blocked"
    assert result["iterations"] == 1
    assert result["tool_calls"] == 0
    assert result["trace"] == []
    assert result["task"].errors
    assert mock_execute.call_count == 0

def test_orchestrator_handles_tool_exception():
    provider = FakeProvider()
    orchestrator = AgentOrchestrator(provider)

    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_result",
                description="Leggi result.txt",
                required_tool="read_workspace_file",
                required_path="result.txt",
            )
        ]
    )

    orchestrator.planner.create_plan = AsyncMock(return_value=plan)

    tool_response = {
        "finish_reason": "tool_calls",
        "message": {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_exception",
                    "type": "function",
                    "function": {
                        "name": "read_workspace_file",
                        "arguments": '{"path":"result.txt"}',
                    },
                }
            ],
        },
    }

    final_response = {
        "finish_reason": "stop",
        "message": {
            "content": "Attività completata.",
            "tool_calls": [],
        },
    }

    provider.complete = AsyncMock(
        side_effect=[tool_response, final_response]
    )

    with patch(
        "agent.orchestrator.execute_tool",
        side_effect=RuntimeError("Unexpected tool error"),
    ) as mock_execute:
        result = asyncio.run(orchestrator.run("Leggi result.txt"))

    assert result["task"].status == "blocked"
    assert result["tool_calls"] == 1
    assert len(result["task"].evidence) == 1
    assert not result["task"].evidence[0].success
    assert not result["task"].plan.criteria[0].satisfied
    assert mock_execute.call_count == 1

def test_orchestrator_journals_uncertain_tool_exception(tmp_path):
    from agent.storage.repository import TaskRepository

    provider = FakeProvider()
    repository = TaskRepository(tmp_path / "journal.db")
    orchestrator = AgentOrchestrator(provider, repository)

    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_result",
                description="Leggi result.txt",
                required_tool="read_workspace_file",
                required_path="result.txt",
            )
        ]
    )

    orchestrator.planner.create_plan = AsyncMock(return_value=plan)

    provider.complete = AsyncMock(
        return_value={
            "finish_reason": "tool_calls",
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_uncertain",
                        "type": "function",
                        "function": {
                            "name": "read_workspace_file",
                            "arguments": '{"path":"result.txt"}',
                        },
                    }
                ],
            },
        }
    )

    with patch(
        "agent.orchestrator.execute_tool",
        side_effect=RuntimeError("Unexpected failure"),
    ):
        result = asyncio.run(orchestrator.run("Leggi result.txt"))

    assert result["task"].status == "blocked"

    operations = repository.list_operations(result["task"].task_id)

    assert len(operations) == 1
    assert operations[0]["call_id"] == "call_uncertain"
    assert operations[0]["status"] == "uncertain"
    assert "Unexpected failure" in operations[0]["error"]


def test_orchestrator_resumes_task_with_existing_evidence(tmp_path):
    from agent.storage.repository import TaskRepository

    provider = FakeProvider()
    repository = TaskRepository(tmp_path / "resume.db")
    orchestrator = AgentOrchestrator(provider, repository)
    task = TaskState(goal="Leggi result.txt")
    task.plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_result",
                description="Leggi result.txt",
                required_tool="read_workspace_file",
                required_path="result.txt",
            )
        ]
    )
    task.iteration = 1
    task.record_tool(
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path":"result.txt"}',
        result={"ok": True, "result": "contenuto"},
        call_id="call_saved",
    )
    repository.save(task)
    repository.mark_interrupted()
    resumed = repository.prepare_resume(task.task_id)

    provider.complete = AsyncMock(
        return_value={
            "finish_reason": "stop",
            "message": {
                "content": "Il file contiene contenuto.",
                "tool_calls": [],
            },
        }
    )
    orchestrator.planner.create_plan = AsyncMock(
        side_effect=AssertionError("Resume must keep the original plan")
    )

    result = asyncio.run(orchestrator.run(resumed.goal, task=resumed))

    assert result["task"].task_id == task.task_id
    assert result["task"].status == "completed"
    assert result["tool_calls"] == 1
    messages = provider.complete.await_args.kwargs["messages"]
    assert messages[-2]["role"] == "assistant"
    assert messages[-2]["tool_calls"][0]["id"] == "call_saved"
    assert messages[-1]["tool_call_id"] == "call_saved"


def test_resume_reconciles_journal_operation_missing_from_task_state(tmp_path):
    from agent.schemas.tasks import TaskState
    from agent.storage.repository import TaskRepository

    repository = TaskRepository(tmp_path / "resume-reconcile.db")
    task = TaskState(goal="Leggi result.txt")
    task.plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_result",
                description="Leggi result.txt",
                required_tool="read_workspace_file",
                required_path="result.txt",
            )
        ]
    )
    task.iteration = 1
    repository.save(task)
    repository.begin_operation(
        task.task_id,
        "call_durable",
        1,
        "read_workspace_file",
        '{"path":"result.txt"}',
    )
    repository.finish_operation(
        task.task_id,
        "call_durable",
        "succeeded",
        result={"ok": True, "result": "contenuto"},
    )
    repository.mark_interrupted()

    resumed = repository.prepare_resume(task.task_id)

    assert resumed.task_id == task.task_id
    assert resumed.tool_calls == 1
    assert resumed.evidence[0].call_id == "call_durable"
    assert resumed.plan.criteria[0].satisfied


def test_orchestrator_keeps_tool_outcome_uncertain_when_journal_update_fails(
    tmp_path,
    monkeypatch,
):
    from agent.storage.repository import TaskRepository

    provider = FakeProvider()
    repository = TaskRepository(tmp_path / "journal-fault.db")
    orchestrator = AgentOrchestrator(provider, repository)
    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="write_result",
                description="Crea result.txt",
                required_tool="write_workspace_file",
                required_path="result.txt",
            )
        ]
    )
    orchestrator.planner.create_plan = AsyncMock(return_value=plan)
    provider.complete = AsyncMock(
        return_value={
            "finish_reason": "tool_calls",
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_journal_failure",
                        "type": "function",
                        "function": {
                            "name": "write_workspace_file",
                            "arguments": (
                                '{"path":"result.txt","content":"new"}'
                            ),
                        },
                    }
                ],
            },
        }
    )

    original_finish_operation = repository.finish_operation

    def fail_finish_operation(**kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "finish_operation", fail_finish_operation)

    with patch(
        "agent.orchestrator.execute_tool",
        return_value=json.dumps(
            {
                "ok": True,
                "result": {
                    "path": "result.txt",
                    "created": True,
                    "overwritten": False,
                    "backup": None,
                },
            }
        ),
    ) as execute_tool:
        result = asyncio.run(orchestrator.run("Crea result.txt"))

    assert execute_tool.call_count == 1
    assert result["task"].status == "blocked"
    assert result["task"].evidence == []
    assert result["trace"] == []

    operation = repository.get_operation(
        result["task"].task_id,
        "call_journal_failure",
    )
    assert operation["status"] == "pending"

    monkeypatch.setattr(repository, "finish_operation", original_finish_operation)
    assert repository.mark_pending_operations_uncertain() == 1
    operation = repository.get_operation(
        result["task"].task_id,
        "call_journal_failure",
    )
    assert operation["status"] == "uncertain"


def test_orchestrator_dispatches_fixed_checks_to_sandbox(monkeypatch):
    import json

    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_TOKEN", "b" * 40)
    provider = FakeProvider()
    orchestrator = AgentOrchestrator(provider)
    orchestrator.planner.create_plan = AsyncMock(
        return_value=TaskPlan(
            criteria=[
                AcceptanceCriterion(
                    criterion_id="run_checks",
                    description="Esegui i test",
                    required_tool="run_workspace_checks",
                )
            ]
        )
    )
    check_result = {
        "passed": True,
        "checks": [
            {
                "name": "pytest",
                "exit_code": 0,
                "passed": True,
                "timed_out": False,
                "duration_seconds": 0.1,
                "stdout": "tests passed",
                "stderr": "",
            },
            {
                "name": "python_syntax",
                "exit_code": 0,
                "passed": True,
                "timed_out": False,
                "duration_seconds": 0.01,
                "files_checked": 2,
                "errors": [],
            },
        ],
    }
    orchestrator.sandbox.run_checks = AsyncMock(
        return_value=json.dumps({"ok": True, "result": check_result})
    )
    provider.complete = AsyncMock(
        side_effect=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "checks_call",
                            "type": "function",
                            "function": {
                                "name": "run_workspace_checks",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
            },
            {
                "finish_reason": "stop",
                "message": {
                    "content": "I controlli sono passati.",
                    "tool_calls": [],
                },
            },
        ]
    )

    result = asyncio.run(
        orchestrator.run("Esegui i test del progetto")
    )

    assert result["task"].status == "completed"
    assert result["tool_calls"] == 1
    assert result["task"].evidence[0].tool == "run_workspace_checks"
    orchestrator.sandbox.run_checks.assert_awaited_once()
