import sqlite3
from datetime import datetime, timezone

import pytest

from agent.schemas.tasks import TaskState
from agent.storage.repository import TaskRepository


@pytest.fixture
def repository(tmp_path):
    return TaskRepository(tmp_path / "test.db")


def make_task():
    return TaskState(goal="Read result.txt")


def test_repository_saves_and_loads_task(repository):
    task = make_task()

    repository.save(task)

    loaded = repository.get(task.task_id)

    assert loaded is not None
    assert loaded.task_id == task.task_id
    assert loaded.goal == task.goal
    assert loaded.status == "in_progress"


def test_repository_returns_none_for_missing_task(repository):
    assert repository.get("missing-id") is None


def test_repository_updates_existing_task(repository):
    task = make_task()
    repository.save(task)

    task.iteration = 3
    task.tool_calls = 2
    task.block("Model error")

    repository.save(task)

    loaded = repository.get(task.task_id)

    assert loaded is not None
    assert loaded.iteration == 3
    assert loaded.tool_calls == 2
    assert loaded.status == "blocked"
    assert loaded.errors == ["Model error"]


def test_repository_lists_tasks(repository):
    first = make_task()
    second = make_task()
    second.block("Test error")

    repository.save(first)
    repository.save(second)

    tasks = repository.list_tasks()

    assert len(tasks) == 2
    assert {task.task_id for task in tasks} == {
        first.task_id,
        second.task_id,
    }


def test_repository_filters_by_status(repository):
    active = make_task()
    blocked = make_task()
    blocked.block("Test error")

    repository.save(active)
    repository.save(blocked)

    tasks = repository.list_tasks(status="blocked")

    assert len(tasks) == 1
    assert tasks[0].task_id == blocked.task_id


def test_repository_respects_limit(repository):
    for _ in range(3):
        repository.save(make_task())

    assert len(repository.list_tasks(limit=2)) == 2


def test_repository_rejects_invalid_limit(repository):
    with pytest.raises(ValueError):
        repository.list_tasks(limit=0)

    with pytest.raises(ValueError):
        repository.list_tasks(limit=1001)


def test_repository_rejects_invalid_status(repository):
    with pytest.raises(ValueError):
        repository.list_tasks(status="invalid")


def test_repository_deletes_task(repository):
    task = make_task()
    repository.save(task)

    assert repository.delete(task.task_id)
    assert repository.get(task.task_id) is None
    assert not repository.delete(task.task_id)


def test_repository_persists_across_instances(tmp_path):
    database = tmp_path / "persistent.db"

    first_repository = TaskRepository(database)
    task = make_task()
    first_repository.save(task)

    second_repository = TaskRepository(database)
    loaded = second_repository.get(task.task_id)

    assert loaded is not None
    assert loaded.goal == task.goal



def test_repository_marks_in_progress_tasks_as_interrupted(repository):
    task = make_task()
    task.iteration = 2
    repository.save(task)

    updated = repository.mark_interrupted()

    assert updated == 1

    loaded = repository.get(task.task_id)

    assert loaded is not None
    assert loaded.status == "interrupted"
    assert loaded.iteration == 2
    assert any("Process terminated" in error for error in loaded.errors)


def test_repository_does_not_interrupt_completed_tasks(repository):
    task = make_task()
    task.plan = None
    task.status = "completed"
    task.final_response = "Completed"
    repository.save(task)

    updated = repository.mark_interrupted()

    assert updated == 0

    loaded = repository.get(task.task_id)

    assert loaded is not None
    assert loaded.status == "completed"


def test_repository_interruption_is_idempotent(repository):
    task = make_task()
    repository.save(task)

    assert repository.mark_interrupted() == 1
    assert repository.mark_interrupted() == 0

    loaded = repository.get(task.task_id)

    assert loaded is not None
    assert loaded.status == "interrupted"
    assert len(loaded.errors) == 1


def test_repository_lists_interrupted_tasks(repository):
    task = make_task()
    repository.save(task)
    repository.mark_interrupted()

    tasks = repository.list_tasks(status="interrupted")

    assert len(tasks) == 1
    assert tasks[0].task_id == task.task_id



def test_repository_migrates_legacy_database(tmp_path):
    database = tmp_path / "legacy.db"
    task = make_task()
    now = datetime.now(timezone.utc).isoformat()

    # Ricrea lo schema precedente, privo dello stato interrupted.
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN (
                        'in_progress',
                        'completed',
                        'blocked'
                    )),
                iteration INTEGER NOT NULL CHECK(iteration >= 0),
                tool_calls INTEGER NOT NULL CHECK(tool_calls >= 0),
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_tasks_status ON tasks(status)
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_tasks_updated ON tasks(updated_at)
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, goal, status, iteration,
                tool_calls, state_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.goal,
                task.status,
                task.iteration,
                task.tool_calls,
                task.model_dump_json(),
                now,
                now,
            ),
        )

    # Initialization must migrate the existing database.
    repository = TaskRepository(database)

    loaded = repository.get(task.task_id)

    assert loaded is not None
    assert loaded.task_id == task.task_id
    assert loaded.goal == task.goal
    assert loaded.status == "in_progress"

    # The new SQL constraint must accept interrupted.
    assert repository.mark_interrupted() == 1

    interrupted = repository.get(task.task_id)

    assert interrupted is not None
    assert interrupted.status == "interrupted"

    # Existing indexes must be retained.
    with sqlite3.connect(database) as connection:
        indexes = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                """
            )
        }

    assert "idx_tasks_status" in indexes
    assert "idx_tasks_updated" in indexes


def test_repository_records_operation_lifecycle(repository):
    task = make_task()
    repository.save(task)

    operation = repository.begin_operation(
        task_id=task.task_id,
        call_id="call_001",
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path":"result.txt"}',
    )

    assert operation["status"] == "pending"

    repository.finish_operation(
        task_id=task.task_id,
        call_id="call_001",
        status="succeeded",
        result={"ok": True, "result": "content"},
    )

    loaded = repository.get_operation(task.task_id, "call_001")

    assert loaded["status"] == "succeeded"
    assert loaded["result"] == {
        "ok": True,
        "result": "content",
    }


def test_repository_rejects_duplicate_operation(repository):
    task = make_task()
    repository.save(task)

    arguments = '{"path":"result.txt"}'

    repository.begin_operation(
        task.task_id,
        "call_duplicate",
        1,
        "read_workspace_file",
        arguments,
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.begin_operation(
            task.task_id,
            "call_duplicate",
            1,
            "read_workspace_file",
            arguments,
        )


def test_repository_recovers_pending_operations(repository):
    task = make_task()
    repository.save(task)

    repository.begin_operation(
        task.task_id,
        "call_pending",
        1,
        "write_workspace_file",
        '{"path":"result.txt","content":"test"}',
    )

    assert repository.mark_pending_operations_uncertain() == 1

    operation = repository.get_operation(
        task.task_id,
        "call_pending",
    )

    assert operation["status"] == "uncertain"
    assert "Process terminated" in operation["error"]


def test_repository_does_not_change_terminal_operations(repository):
    task = make_task()
    repository.save(task)

    repository.begin_operation(
        task.task_id,
        "call_success",
        1,
        "read_workspace_file",
        '{"path":"result.txt"}',
    )

    repository.finish_operation(
        task.task_id,
        "call_success",
        "succeeded",
        result={"ok": True, "result": "test"},
    )

    assert repository.mark_pending_operations_uncertain() == 0

    operation = repository.get_operation(
        task.task_id,
        "call_success",
    )

    assert operation["status"] == "succeeded"


def test_repository_lists_operations_in_order(repository):
    task = make_task()
    repository.save(task)

    for call_id in ("call_1", "call_2"):
        repository.begin_operation(
            task.task_id,
            call_id,
            1,
            "read_workspace_file",
            '{"path":"result.txt"}',
        )

    operations = repository.list_operations(task.task_id)

    assert [item["call_id"] for item in operations] == [
        "call_1",
        "call_2",
    ]


def test_repository_deletes_operations_with_task(repository):
    task = make_task()
    repository.save(task)

    repository.begin_operation(
        task.task_id,
        "call_delete",
        1,
        "read_workspace_file",
        '{"path":"result.txt"}',
    )

    assert repository.delete(task.task_id)
    assert repository.list_operations(task.task_id) == []


def test_repository_cleanup_deletes_matching_status(repository):
    task = make_task()
    task.status = "completed"
    repository.save(task)

    deleted = repository.cleanup(status="completed", older_than_days=1)

    assert deleted == 0
    assert repository.get(task.task_id) is not None


def test_repository_cleanup_deletes_old_terminal_task(repository):
    task = make_task()
    task.status = "completed"
    repository.save(task)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE tasks SET updated_at = '2000-01-01T00:00:00+00:00' "
            "WHERE task_id = ?",
            (task.task_id,),
        )

    assert repository.cleanup(status="completed", older_than_days=1) == 1
    assert repository.get(task.task_id) is None


def test_repository_cleanup_rejects_invalid_status(repository):
    with pytest.raises(ValueError, match="Invalid cleanup status"):
        repository.cleanup(status="in_progress", older_than_days=30)


def test_repository_cleanup_retains_tasks_with_uncertain_operations(repository):
    task = make_task()
    task.status = "interrupted"
    repository.save(task)
    repository.begin_operation(
        task.task_id,
        "call_uncertain_cleanup",
        1,
        "write_workspace_file",
        '{"path":"result.txt","content":"new"}',
    )
    repository.mark_pending_operations_uncertain()

    with repository._connect() as connection:
        connection.execute(
            "UPDATE tasks SET updated_at = '2000-01-01T00:00:00+00:00' "
            "WHERE task_id = ?",
            (task.task_id,),
        )

    assert repository.cleanup(status="interrupted", older_than_days=1) == 0
    assert repository.get(task.task_id) is not None
    assert repository.get_operation(
        task.task_id,
        "call_uncertain_cleanup",
    )["status"] == "uncertain"


def test_repository_automatic_retention_is_disabled_by_default(repository):
    task = make_task()
    task.status = "completed"
    repository.save(task)

    assert repository.cleanup_expired_tasks(0) == 0
    assert repository.get(task.task_id) is not None


def test_repository_automatic_retention_removes_only_safe_expired_tasks(
    repository,
):
    expired = make_task()
    expired.status = "completed"
    repository.save(expired)

    unresolved = make_task()
    unresolved.status = "interrupted"
    repository.save(unresolved)
    repository.begin_operation(
        unresolved.task_id,
        "call_retention",
        1,
        "write_workspace_file",
        '{"path":"result.txt","content":"new"}',
    )
    repository.mark_pending_operations_uncertain()

    with repository._connect() as connection:
        for task in (expired, unresolved):
            connection.execute(
                "UPDATE tasks SET updated_at = '2000-01-01T00:00:00+00:00' "
                "WHERE task_id = ?",
                (task.task_id,),
            )

    assert repository.cleanup_expired_tasks(1) == 1
    assert repository.get(expired.task_id) is None
    assert repository.get(unresolved.task_id) is not None


def test_repository_metrics_counts_tasks_and_operations(repository):
    task = make_task()
    repository.save(task)
    repository.begin_operation(
        task.task_id,
        "call_metrics",
        1,
        "read_workspace_file",
        '{"path":"result.txt"}',
    )
    repository.finish_operation(
        task.task_id,
        "call_metrics",
        "succeeded",
        result={"ok": True, "result": "content"},
    )

    metrics = repository.metrics()
    assert metrics["tasks"] == {"in_progress": 1}
    assert metrics["operations"] == {"succeeded": 1}
    assert metrics["operation_average_seconds"] >= 0






def test_repository_records_operation_lifecycle(repository):
    task = make_task()
    repository.save(task)

    operation = repository.begin_operation(
        task_id=task.task_id,
        call_id="call_001",
        iteration=1,
        tool="read_workspace_file",
        arguments='{"path":"result.txt"}',
    )

    assert operation["status"] == "pending"

    repository.finish_operation(
        task_id=task.task_id,
        call_id="call_001",
        status="succeeded",
        result={"ok": True, "result": "content"},
    )

    loaded = repository.get_operation(task.task_id, "call_001")

    assert loaded["status"] == "succeeded"
    assert loaded["result"] == {
        "ok": True,
        "result": "content",
    }


def test_repository_rejects_duplicate_operation(repository):
    task = make_task()
    repository.save(task)

    arguments = '{"path":"result.txt"}'

    repository.begin_operation(
        task.task_id,
        "call_duplicate",
        1,
        "read_workspace_file",
        arguments,
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.begin_operation(
            task.task_id,
            "call_duplicate",
            1,
            "read_workspace_file",
            arguments,
        )


def test_repository_recovers_pending_operations(repository):
    task = make_task()
    repository.save(task)

    repository.begin_operation(
        task.task_id,
        "call_pending",
        1,
        "write_workspace_file",
        '{"path":"result.txt","content":"test"}',
    )

    assert repository.mark_pending_operations_uncertain() == 1

    operation = repository.get_operation(
        task.task_id,
        "call_pending",
    )

    assert operation["status"] == "uncertain"
    assert "Process terminated" in operation["error"]


def test_repository_does_not_change_terminal_operations(repository):
    task = make_task()
    repository.save(task)

    repository.begin_operation(
        task.task_id,
        "call_success",
        1,
        "read_workspace_file",
        '{"path":"result.txt"}',
    )

    repository.finish_operation(
        task.task_id,
        "call_success",
        "succeeded",
        result={"ok": True, "result": "test"},
    )

    assert repository.mark_pending_operations_uncertain() == 0

    operation = repository.get_operation(
        task.task_id,
        "call_success",
    )

    assert operation["status"] == "succeeded"


def test_repository_lists_operations_in_order(repository):
    task = make_task()
    repository.save(task)

    for call_id in ("call_1", "call_2"):
        repository.begin_operation(
            task.task_id,
            call_id,
            1,
            "read_workspace_file",
            '{"path":"result.txt"}',
        )

    operations = repository.list_operations(task.task_id)

    assert [item["call_id"] for item in operations] == [
        "call_1",
        "call_2",
    ]


def test_repository_deletes_operations_with_task(repository):
    task = make_task()
    repository.save(task)

    repository.begin_operation(
        task.task_id,
        "call_delete",
        1,
        "read_workspace_file",
        '{"path":"result.txt"}',
    )

    assert repository.delete(task.task_id)
    assert repository.list_operations(task.task_id) == []
