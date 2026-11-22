import json
import pytest
from agent.schemas.tasks import (
    AcceptanceCriterion,
    TaskEvidence,
    TaskPlan,
    TaskState,
)


def make_task():
    return TaskState(
        goal="Leggi result.txt",
        iteration=1,
        plan=TaskPlan(
            criteria=[
                AcceptanceCriterion(
                    criterion_id="read_result",
                    description="Leggi result.txt",
                    required_tool="read_workspace_file",
                    required_path="result.txt",
                    satisfied=True,
                    evidence_index=0,
                )
            ]
        ),
    )


def make_evidence(path="result.txt"):
    return TaskEvidence(
        iteration=1,
        tool="read_workspace_file",
        arguments=json.dumps({"path": path}),
        success=True,
        validated=True,
        result={"ok": True, "result": "contenuto"},
    )


def test_valid_evidence_can_complete():
    task = make_task()
    task.evidence.append(make_evidence())

    assert task.can_complete("Ho letto il file")


def test_wrong_path_cannot_complete():
    task = make_task()
    task.evidence.append(make_evidence("other.txt"))

    assert not task.can_complete("Ho letto il file")


def test_missing_path_cannot_complete():
    task = make_task()
    evidence = make_evidence()
    evidence.arguments = "{}"
    task.evidence.append(evidence)

    assert not task.can_complete("Ho letto il file")


def test_invalid_result_cannot_complete():
    task = make_task()

    task.record_tool(
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path": "result.txt"}',
        result={"ok": True, "result": ["formato", "errato"]},
    )

    assert not task.evidence[0].validated
    assert not task.can_complete("Ho letto il file")


def test_missing_evidence_cannot_complete():
    task = make_task()

    assert not task.can_complete("Ho letto il file")


def test_invalid_evidence_index_cannot_complete():
    task = make_task()
    task.plan.criteria[0].evidence_index = 99
    task.evidence.append(make_evidence())

    assert not task.can_complete("Ho letto il file")


def test_record_tool_validates_matching_evidence():
    task = make_task()
    task.plan.criteria[0].satisfied = False
    task.plan.criteria[0].evidence_index = None

    task.record_tool(
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path": "result.txt"}',
        result={"ok": True, "result": "contenuto"},
    )

    assert task.evidence[0].success
    assert task.evidence[0].validated
    assert task.plan.criteria[0].satisfied
    assert task.plan.criteria[0].evidence_index == 0


def test_record_tool_does_not_accept_wrong_path():
    task = make_task()
    task.plan.criteria[0].satisfied = False

    task.record_tool(
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path": "other.txt"}',
        result={"ok": True, "result": "contenuto"},
    )

    assert task.evidence[0].validated
    assert not task.plan.criteria[0].satisfied


def test_record_tool_rejects_invalid_result():
    task = make_task()
    task.plan.criteria[0].satisfied = False

    task.record_tool(
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path": "result.txt"}',
        result={"ok": True, "result": ["formato", "errato"]},
    )

    assert task.evidence[0].success
    assert not task.evidence[0].validated
    assert not task.plan.criteria[0].satisfied
    assert task.errors == ["read_workspace_file: invalid result format"]
    assert task.tool_calls == 1


def test_record_tool_handles_tool_failure():
    task = make_task()
    task.plan.criteria[0].satisfied = False

    task.record_tool(
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path": "result.txt"}',
        result={"ok": False, "error": "File not found"},
    )

    assert not task.evidence[0].success
    assert not task.evidence[0].validated
    assert not task.plan.criteria[0].satisfied
    assert task.errors
    assert task.tool_calls == 1


def test_tool_authorization_accepts_required_tool():
    task = TaskState(
        goal="Leggi result.txt",
        plan=TaskPlan(
            criteria=[
                AcceptanceCriterion(
                    criterion_id="read_result",
                    description="Leggi result.txt",
                    required_tool="read_workspace_file",
                    required_path="result.txt",
                )
            ]
        ),
    )

    assert task.is_tool_authorized(
        "read_workspace_file",
        '{"path": "result.txt"}',
    )


def test_tool_authorization_rejects_wrong_tool():
    task = TaskState(
        goal="Leggi result.txt",
        plan=TaskPlan(
            criteria=[
                AcceptanceCriterion(
                    criterion_id="read_result",
                    description="Leggi result.txt",
                    required_tool="read_workspace_file",
                    required_path="result.txt",
                )
            ]
        ),
    )

    assert not task.is_tool_authorized(
        "write_workspace_file",
        '{"path": "result.txt", "content": "test"}',
    )


def test_tool_authorization_rejects_wrong_path():
    task = TaskState(
        goal="Leggi result.txt",
        plan=TaskPlan(
            criteria=[
                AcceptanceCriterion(
                    criterion_id="read_result",
                    description="Leggi result.txt",
                    required_tool="read_workspace_file",
                    required_path="result.txt",
                )
            ]
        ),
    )

    assert not task.is_tool_authorized(
        "read_workspace_file",
        '{"path": "other.txt"}',
    )


def test_tool_authorization_rejects_path_traversal():
    task = TaskState(
        goal="Leggi result.txt",
        plan=TaskPlan(
            criteria=[
                AcceptanceCriterion(
                    criterion_id="read_result",
                    description="Leggi result.txt",
                    required_tool="read_workspace_file",
                    required_path="result.txt",
                )
            ]
        ),
    )

    assert not task.is_tool_authorized(
        "read_workspace_file",
        '{"path": "../result.txt"}',
    )


def test_task_plan_rejects_duplicate_tool_and_path():
    with pytest.raises(ValueError, match="Duplicate tool and path"):
        TaskPlan(
            criteria=[
                AcceptanceCriterion(
                    criterion_id="read_1",
                    description="Prima lettura",
                    required_tool="read_workspace_file",
                    required_path="README.md",
                ),
                AcceptanceCriterion(
                    criterion_id="read_2",
                    description="Seconda lettura",
                    required_tool="read_workspace_file",
                    required_path="README.md",
                ),
            ]
        )


def test_task_plan_accepts_different_paths():
    plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="read_1",
                description="Leggi README",
                required_tool="read_workspace_file",
                required_path="README.md",
            ),
            AcceptanceCriterion(
                criterion_id="read_2",
                description="Leggi config",
                required_tool="read_workspace_file",
                required_path="config.json",
            ),
        ]
    )

    assert len(plan.criteria) == 2


def test_task_plan_rejects_duplicate_criterion_ids():
    with pytest.raises(ValueError, match="Duplicate criterion IDs"):
        TaskPlan(
            criteria=[
                AcceptanceCriterion(
                    criterion_id="duplicate",
                    description="Prima lettura",
                    required_tool="read_workspace_file",
                    required_path="README.md",
                ),
                AcceptanceCriterion(
                    criterion_id="duplicate",
                    description="Seconda lettura",
                    required_tool="read_workspace_file",
                    required_path="config.json",
                ),
            ]
        )


@pytest.mark.parametrize(
    "field",
    ["criterion_id", "description", "required_tool"],
)
@pytest.mark.parametrize("value", ["", "   "])
def test_acceptance_criterion_rejects_empty_strings(field, value):
    data = {
        "criterion_id": "read_result",
        "description": "Leggi result.txt",
        "required_tool": "read_workspace_file",
        "required_path": "result.txt",
    }

    data[field] = value

    with pytest.raises(ValueError):
        AcceptanceCriterion(**data)


@pytest.mark.parametrize("path", ["", "   "])
def test_acceptance_criterion_rejects_empty_required_path(path):
    with pytest.raises(ValueError, match="required_path"):
        AcceptanceCriterion(
            criterion_id="read_result",
            description="Leggi result.txt",
            required_tool="read_workspace_file",
            required_path=path,
        )


def test_task_plan_rejects_empty_criteria():
    with pytest.raises(ValueError):
        TaskPlan(criteria=[])


def test_task_plan_rejects_more_than_20_criteria():
    criteria = [
        AcceptanceCriterion(
            criterion_id=f"criterion_{i}",
            description=f"Operazione {i}",
            required_tool="read_workspace_file",
            required_path=f"file_{i}.txt",
        )
        for i in range(21)
    ]

    with pytest.raises(ValueError):
        TaskPlan(criteria=criteria)


def test_acceptance_criterion_rejects_negative_evidence_index():
    with pytest.raises(ValueError):
        AcceptanceCriterion(
            criterion_id="c1",
            description="Leggere il file",
            required_tool="read_workspace_file",
            required_path="README.md",
            evidence_index=-1,
        )


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_acceptance_criterion_rejects_invalid_evidence_index_types(value):
    with pytest.raises(ValueError):
        AcceptanceCriterion(
            criterion_id="c1",
            description="Leggere il file",
            required_tool="read_workspace_file",
            required_path="README.md",
            evidence_index=value,
        )


@pytest.mark.parametrize("value", [0, 1, -1, "true", None])
def test_acceptance_criterion_rejects_invalid_satisfied_types(value):
    if value is False or value is True:
        return

    with pytest.raises(ValueError):
        AcceptanceCriterion(
            criterion_id="c1",
            description="Leggere il file",
            required_tool="read_workspace_file",
            required_path="README.md",
            satisfied=value,
        )


def test_acceptance_criterion_accepts_valid_numeric_fields():
    criterion = AcceptanceCriterion(
        criterion_id="c1",
        description="Leggere il file",
        required_tool="read_workspace_file",
        required_path="README.md",
        satisfied=True,
        evidence_index=0,
    )

    assert criterion.satisfied is True
    assert criterion.evidence_index == 0


def test_acceptance_criterion_rejects_satisfied_without_evidence():
    with pytest.raises(ValueError):
        AcceptanceCriterion(
            criterion_id="c1",
            description="Leggere il file",
            required_tool="read_workspace_file",
            required_path="README.md",
            satisfied=True,
        )


def test_acceptance_criterion_rejects_evidence_when_not_satisfied():
    with pytest.raises(ValueError):
        AcceptanceCriterion(
            criterion_id="c1",
            description="Leggere il file",
            required_tool="read_workspace_file",
            required_path="README.md",
            satisfied=False,
            evidence_index=0,
        )


def test_acceptance_criterion_accepts_satisfied_with_evidence():
    criterion = AcceptanceCriterion(
        criterion_id="c1",
        description="Leggere il file",
        required_tool="read_workspace_file",
        required_path="README.md",
        satisfied=True,
        evidence_index=0,
    )

    assert criterion.satisfied is True
    assert criterion.evidence_index == 0


def test_record_tool_updates_satisfied_and_evidence_index_together():
    task = TaskState(goal="Leggere README.md", iteration=1)
    task.plan = TaskPlan(
        criteria=[
            AcceptanceCriterion(
                criterion_id="c1",
                description="Leggere README",
                required_tool="read_workspace_file",
                required_path="README.md",
            )
        ]
    )

    task.record_tool(
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path": "README.md"}',
        result={"ok": True, "result": "Contenuto README"},
    )

    criterion = task.plan.criteria[0]

    assert criterion.satisfied is True
    assert criterion.evidence_index == 0


def test_record_tool_does_not_satisfy_criterion_with_wrong_tool():
    task = make_task()

    criterion = task.plan.criteria[0]
    criterion.satisfied = False
    criterion.evidence_index = None

    task.record_tool(
        iteration=1,
        tool="list_workspace_files",
        arguments='{"path": "."}',
        result={"ok": True, "result": ["result.txt"]},
    )

    criterion = task.plan.criteria[0]

    assert criterion.satisfied is False
    assert criterion.evidence_index is None
    assert task.evidence[0].validated is True

def test_record_tool_does_not_satisfy_criterion_with_wrong_path():
    task = make_task()

    criterion = task.plan.criteria[0]
    criterion.satisfied = False
    criterion.evidence_index = None

    task.record_tool(
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path": "other.txt"}',
        result={"ok": True, "result": "Altro contenuto"},
    )

    assert criterion.satisfied is False
    assert criterion.evidence_index is None
    assert task.evidence[0].validated is True

def test_wrong_tool_evidence_cannot_complete():
    task = make_task()

    task.evidence.append(
        TaskEvidence(
            iteration=1,
            tool="list_workspace_files",
            arguments='{"path": "."}',
            success=True,
            validated=True,
            result={"ok": True, "result": ["result.txt"]},
        )
    )

    assert not task.can_complete("Ho letto il file")

def test_evidence_rejects_zero_iteration():
    with pytest.raises(ValueError):
        TaskEvidence(
            iteration=0,
            tool="read_workspace_file",
            arguments='{"path": "result.txt"}',
            success=True,
            validated=True,
            result={"ok": True, "result": "contenuto"},
        )


def test_evidence_rejects_negative_iteration():
    with pytest.raises(ValueError):
        TaskEvidence(
            iteration=-1,
            tool="read_workspace_file",
            arguments='{"path": "result.txt"}',
            success=True,
            validated=True,
            result={"ok": True, "result": "contenuto"},
        )

@pytest.mark.parametrize("field", ["iteration", "tool_calls"])
def test_task_counters_reject_negative_values(field):
    with pytest.raises(ValueError):
        TaskState(goal="Test", **{field: -1})

def test_task_rejects_more_evidence_than_tool_calls():
    with pytest.raises(ValueError, match="tool_calls cannot be lower"):
        TaskState(
            goal="Test",
            tool_calls=0,
            evidence=[make_evidence()],
        )

def test_record_tool_rejects_future_iteration():
    task = make_task()

    with pytest.raises(
        ValueError,
        match="Evidence iteration cannot exceed task iteration",
    ):
        task.record_tool(
            iteration=2,
            tool="read_workspace_file",
            arguments='{"path": "result.txt"}',
            result={"ok": True, "result": "contenuto"},
        )

    assert task.evidence == []
    assert task.tool_calls == 0


@pytest.mark.parametrize("iteration", [0, -1])
def test_record_tool_rejects_invalid_iteration(iteration):
    task = make_task()

    with pytest.raises(ValueError):
        task.record_tool(
            iteration=iteration,
            tool="read_workspace_file",
            arguments='{"path": "result.txt"}',
            result={"ok": True, "result": "contenuto"},
        )

    assert task.evidence == []

def test_record_tool_rejects_non_dict_result():
    task = make_task()

    with pytest.raises(ValueError, match="Tool result must be a dictionary"):
        task.record_tool(
            iteration=1,
            tool="read_workspace_file",
            arguments='{"path": "result.txt"}',
            result=None,
        )

    assert task.evidence == []
    assert task.tool_calls == 0

def test_record_tool_does_not_accept_wrong_tool():
    task = make_task()
    task.plan.criteria[0].satisfied = False
    task.plan.criteria[0].evidence_index = None

    task.record_tool(
        iteration=1,
        tool="list_workspace_files",
        arguments='{"path": "."}',
        result={"ok": True, "result": ["result.txt"]},
    )

    assert task.evidence[0].validated
    assert not task.plan.criteria[0].satisfied
    assert task.plan.criteria[0].evidence_index is None