from fastapi.testclient import TestClient

import agent.main as main
from agent.schemas.tasks import TaskState
from agent.storage.repository import TaskRepository


class FakeProvider:
    model = "fake-model"

    async def health_check(self):
        return True

    def metrics(self):
        return {
            "requests": 0,
            "successes": 0,
            "errors": 0,
            "attempts": 0,
            "retries": 0,
            "average_duration_seconds": 0,
        }


class FakeOrchestrator:
    async def run(self, message, task=None):
        task = task or TaskState(goal=message)
        task.status = "completed"
        task.final_response = "Completata"
        task.complete("Completata")
        return {
            "response": "Completata",
            "model": "fake-model",
            "iterations": 1,
            "tool_calls": 0,
            "trace": [],
            "task": task,
        }


def test_operations_endpoint_and_delete(tmp_path, monkeypatch):
    repository = TaskRepository(tmp_path / "api.db")
    task = TaskState(goal="Leggi result.txt")
    repository.save(task)
    repository.begin_operation(
        task.task_id,
        "call_1",
        1,
        "read_workspace_file",
        '{"path":"result.txt"}',
    )

    monkeypatch.setattr(main, "repository", repository)
    monkeypatch.setattr(main, "provider", FakeProvider())
    monkeypatch.setattr(main, "orchestrator", FakeOrchestrator())

    with TestClient(main.app) as client:
        operations = client.get(f"/tasks/{task.task_id}/operations")
        assert operations.status_code == 200
        assert operations.json()["count"] == 1

        deleted = client.delete(f"/tasks/{task.task_id}")
        assert deleted.status_code == 204

        missing = client.get(f"/tasks/{task.task_id}")
        assert missing.status_code == 404


def test_resume_rejects_non_interrupted_task(tmp_path, monkeypatch):
    repository = TaskRepository(tmp_path / "resume.db")
    task = TaskState(goal="Leggi result.txt")
    task.status = "blocked"
    repository.save(task)

    monkeypatch.setattr(main, "repository", repository)
    monkeypatch.setattr(main, "orchestrator", FakeOrchestrator())

    with TestClient(main.app) as client:
        response = client.post(f"/tasks/{task.task_id}/resume")

    assert response.status_code == 409
    assert "interrupted" in response.json()["detail"]


def test_resume_rejects_uncertain_write(tmp_path, monkeypatch):
    repository = TaskRepository(tmp_path / "resume-write.db")
    task = TaskState(goal="Modifica result.txt")
    repository.save(task)
    repository.begin_operation(
        task.task_id,
        "call_write",
        1,
        "write_workspace_file",
        '{"path":"result.txt","content":"test"}',
    )
    repository.mark_pending_operations_uncertain()
    task.status = "interrupted"
    repository.save(task)

    monkeypatch.setattr(main, "repository", repository)
    monkeypatch.setattr(main, "orchestrator", FakeOrchestrator())

    with TestClient(main.app) as client:
        response = client.post(f"/tasks/{task.task_id}/resume")

    assert response.status_code == 409
    assert "write operation" in response.json()["detail"]


def test_resume_preserves_task_and_evidence(tmp_path, monkeypatch):
    repository = TaskRepository(tmp_path / "resume-same-task.db")
    task = TaskState(goal="Leggi result.txt")
    task.status = "interrupted"
    repository.save(task)

    class ResumeCapture:
        received_task = None

        async def run(self, message, task=None):
            self.received_task = task
            return {
                "response": "Completata",
                "model": "fake-model",
                "iterations": task.iteration,
                "tool_calls": task.tool_calls,
                "trace": [],
                "task": task,
            }

    orchestrator = ResumeCapture()
    monkeypatch.setattr(main, "repository", repository)
    monkeypatch.setattr(main, "orchestrator", orchestrator)

    with TestClient(main.app) as client:
        response = client.post(f"/tasks/{task.task_id}/resume")

    assert response.status_code == 200
    assert orchestrator.received_task.task_id == task.task_id
    assert orchestrator.received_task.status == "in_progress"
    assert repository.get(task.task_id).status == "in_progress"


def test_startup_marks_pending_work_interrupted(tmp_path, monkeypatch):
    repository = TaskRepository(tmp_path / "startup-recovery.db")
    task = TaskState(goal="Leggi result.txt")
    repository.save(task)
    repository.begin_operation(
        task.task_id,
        "call_pending",
        1,
        "read_workspace_file",
        '{"path":"result.txt"}',
    )
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app):
        recovered_task = repository.get(task.task_id)
        operation = repository.get_operation(task.task_id, "call_pending")

    assert recovered_task.status == "interrupted"
    assert operation["status"] == "uncertain"


def test_metrics_endpoint_reports_repository_counts(tmp_path, monkeypatch):
    repository = TaskRepository(tmp_path / "metrics-api.db")
    task = TaskState(goal="Leggi result.txt")
    repository.save(task)
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.json()["tasks"] == {"interrupted": 1}
    assert response.json()["provider"]["requests"] == 0


def test_startup_retention_is_opt_in_and_preserves_uncertain_tasks(
    tmp_path,
    monkeypatch,
):
    repository = TaskRepository(tmp_path / "retention-startup.db")
    expired = TaskState(goal="Completata")
    expired.status = "completed"
    repository.save(expired)

    uncertain = TaskState(goal="Scrittura interrotta")
    uncertain.status = "interrupted"
    repository.save(uncertain)
    repository.begin_operation(
        uncertain.task_id,
        "call_retained",
        1,
        "write_workspace_file",
        '{"path":"result.txt","content":"new"}',
    )
    repository.mark_pending_operations_uncertain()

    with repository._connect() as connection:
        for task in (expired, uncertain):
            connection.execute(
                "UPDATE tasks SET updated_at = '2000-01-01T00:00:00+00:00' "
                "WHERE task_id = ?",
                (task.task_id,),
            )

    monkeypatch.setattr(main, "repository", repository)
    monkeypatch.setenv("TASK_RETENTION_DAYS", "1")

    with TestClient(main.app):
        pass

    assert repository.get(expired.task_id) is None
    assert repository.get(uncertain.task_id) is not None
