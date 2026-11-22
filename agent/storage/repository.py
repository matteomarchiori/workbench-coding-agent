import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from agent.schemas.tasks import TaskState


TaskStatus = Literal["in_progress", "completed", "blocked", "interrupted"]
INTERRUPTED_MESSAGE = (
    "Processo terminato durante l'esecuzione. "
    "Verificare le operazioni effettuate prima di qualsiasi ripresa."
)


class TaskRepository:
    def __init__(self, database_path: str | Path | None = None):
        self.database_path = Path(
            database_path
            or os.getenv("DATABASE_PATH", "/data/workbench.db")
        )

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _create_tasks_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN (
                        'in_progress',
                        'completed',
                        'blocked',
                        'interrupted'
                    )),
                iteration INTEGER NOT NULL CHECK(iteration >= 0),
                tool_calls INTEGER NOT NULL CHECK(tool_calls >= 0),
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _create_operations_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operation_journal (
                operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL
                    REFERENCES tasks(task_id) ON DELETE CASCADE,
                call_id TEXT NOT NULL,
                iteration INTEGER NOT NULL CHECK(iteration >= 1),
                tool TEXT NOT NULL,
                arguments TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN (
                        'pending',
                        'succeeded',
                        'failed',
                        'uncertain'
                    )),
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(task_id, call_id)
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_operations_task
            ON operation_journal(task_id, operation_id)
            """
        )

    def begin_operation(
        self,
        task_id: str,
        call_id: str,
        iteration: int,
        tool: str,
        arguments: str,
    ) -> dict:
        if not call_id or not tool:
            raise ValueError("call_id and tool are required")

        if iteration < 1:
            raise ValueError("iteration must be >= 1")

        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO operation_journal (
                    task_id, call_id, iteration, tool, arguments,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    task_id,
                    call_id,
                    iteration,
                    tool,
                    arguments,
                    now,
                    now,
                ),
            )

            row = connection.execute(
                """
                SELECT *
                FROM operation_journal
                WHERE task_id = ? AND call_id = ?
                """,
                (task_id, call_id),
            ).fetchone()

        return dict(row)

    def finish_operation(
        self,
        task_id: str,
        call_id: str,
        status: Literal["succeeded", "failed", "uncertain"],
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        if status not in ("succeeded", "failed", "uncertain"):
            raise ValueError("Invalid terminal operation status")

        result_json = (
            None
            if result is None
            else json.dumps(result, ensure_ascii=False)
        )
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE operation_journal
                SET status = ?,
                    result_json = ?,
                    error = ?,
                    updated_at = ?
                WHERE task_id = ?
                  AND call_id = ?
                  AND status = 'pending'
                """,
                (
                    status,
                    result_json,
                    error,
                    now,
                    task_id,
                    call_id,
                ),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    "Operation not found or no longer pending"
                )

    def get_operation(
        self,
        task_id: str,
        call_id: str,
    ) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM operation_journal
                WHERE task_id = ? AND call_id = ?
                """,
                (task_id, call_id),
            ).fetchone()

        if row is None:
            return None

        operation = dict(row)
        operation["result"] = (
            json.loads(operation["result_json"])
            if operation["result_json"] is not None
            else None
        )
        return operation

    def list_operations(self, task_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM operation_journal
                WHERE task_id = ?
                ORDER BY operation_id
                """,
                (task_id,),
            ).fetchall()

        operations = []

        for row in rows:
            operation = dict(row)
            operation["result"] = (
                json.loads(operation["result_json"])
                if operation["result_json"] is not None
                else None
            )
            operations.append(operation)

        return operations

    def mark_pending_operations_uncertain(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        message = (
            "Processo terminato durante l'operazione: "
            "l'esito non può essere determinato automaticamente."
        )

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE operation_journal
                SET status = 'uncertain',
                    error = ?,
                    updated_at = ?
                WHERE status = 'pending'
                """,
                (message, now),
            )

        return cursor.rowcount

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")

            table = connection.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table' AND name = 'tasks'
                """
            ).fetchone()

            if table is None:
                self._create_tasks_table(connection)
            elif "'interrupted'" not in table["sql"]:
                # Migrazione atomica: conserva tutte le attività esistenti.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "ALTER TABLE tasks RENAME TO tasks_legacy"
                )
                self._create_tasks_table(connection)

                connection.execute(
                    """
                    INSERT INTO tasks (
                        task_id, goal, status, iteration,
                        tool_calls, state_json, created_at, updated_at
                    )
                    SELECT
                        task_id, goal, status, iteration,
                        tool_calls, state_json, created_at, updated_at
                    FROM tasks_legacy
                    """
                )

                connection.execute("DROP TABLE tasks_legacy")

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_status
                ON tasks(status)
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_updated
                ON tasks(updated_at)
                """
            )

            self._create_operations_table(connection)

    def mark_interrupted(self) -> int:
        now = datetime.now(timezone.utc).isoformat()

        updated = 0

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            rows = connection.execute(
                """
                SELECT task_id, state_json
                FROM tasks
                WHERE status = 'in_progress'
                """
            ).fetchall()

            for row in rows:
                task = TaskState.model_validate_json(row["state_json"])
                task.status = "interrupted"

                if INTERRUPTED_MESSAGE not in task.errors:
                    task.errors.append(INTERRUPTED_MESSAGE)

                connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'interrupted',
                        state_json = ?,
                        updated_at = ?
                    WHERE task_id = ?
                      AND status = 'in_progress'
                    """,
                    (task.model_dump_json(), now, task.task_id),
                )
                updated += 1

        return updated

    def prepare_resume(self, task_id: str) -> TaskState:
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state_json FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()

            if row is None:
                raise KeyError(task_id)

            task = TaskState.model_validate_json(row["state_json"])

            if task.status != "interrupted":
                raise ValueError(
                    "Only interrupted tasks can be resumed; "
                    f"current status: {task.status}"
                )

            operations = connection.execute(
                """
                SELECT call_id, iteration, tool, arguments, status, result_json
                FROM operation_journal
                WHERE task_id = ?
                ORDER BY operation_id
                """,
                (task_id,),
            ).fetchall()

            if any(
                operation["tool"] == "write_workspace_file"
                and operation["status"] == "uncertain"
                for operation in operations
            ):
                raise ValueError(
                    "Cannot automatically resume an interrupted write operation"
                )

            known_call_ids = {
                evidence.call_id
                for evidence in task.evidence
                if evidence.call_id is not None
            }

            for operation in operations:
                if operation["status"] not in ("succeeded", "failed"):
                    continue

                call_id = operation["call_id"]

                if call_id in known_call_ids:
                    continue

                matching_evidence = next(
                    (
                        evidence
                        for evidence in task.evidence
                        if evidence.iteration == operation["iteration"]
                        and evidence.tool == operation["tool"]
                        and evidence.arguments == operation["arguments"]
                    ),
                    None,
                )

                if matching_evidence is not None:
                    matching_evidence.call_id = call_id
                    known_call_ids.add(call_id)
                    continue

                if operation["result_json"] is None:
                    continue

                result = json.loads(operation["result_json"])

                if not isinstance(result, dict):
                    continue

                task.iteration = max(task.iteration, operation["iteration"])
                task.record_tool(
                    iteration=operation["iteration"],
                    tool=operation["tool"],
                    arguments=operation["arguments"],
                    result=result,
                    call_id=call_id,
                )
                known_call_ids.add(call_id)

            task.status = "in_progress"
            task.errors = [
                error for error in task.errors if error != INTERRUPTED_MESSAGE
            ]
            task.final_response = None

            connection.execute(
                """
                UPDATE tasks
                SET status = 'in_progress',
                    iteration = ?,
                    tool_calls = ?,
                    state_json = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    task.iteration,
                    task.tool_calls,
                    task.model_dump_json(),
                    now,
                    task_id,
                ),
            )

        return task

    def save(self, task: TaskState) -> None:
        now = datetime.now(timezone.utc).isoformat()
        state_json = task.model_dump_json()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id,
                    goal,
                    status,
                    iteration,
                    tool_calls,
                    state_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    goal = excluded.goal,
                    status = excluded.status,
                    iteration = excluded.iteration,
                    tool_calls = excluded.tool_calls,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    task.task_id,
                    task.goal,
                    task.status,
                    task.iteration,
                    task.tool_calls,
                    state_json,
                    now,
                    now,
                ),
            )

    def get(self, task_id: str) -> TaskState | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json
                FROM tasks
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()

        if row is None:
            return None

        return TaskState.model_validate_json(row["state_json"])

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        limit: int = 100,
    ) -> list[TaskState]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")

        if status is not None and status not in (
            "in_progress",
            "completed",
            "blocked",
            "interrupted",
        ):
            raise ValueError("Invalid task status")

        with self._connect() as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT state_json
                    FROM tasks
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT state_json
                    FROM tasks
                    WHERE status = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()

        return [
            TaskState.model_validate_json(row["state_json"])
            for row in rows
        ]

    def delete(self, task_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM tasks WHERE task_id = ?",
                (task_id,),
            )
        return cursor.rowcount > 0

    def cleanup(
        self,
        status: TaskStatus,
        older_than_days: int,
    ) -> int:
        if status not in ("completed", "blocked", "interrupted"):
            raise ValueError("Invalid cleanup status")

        if not 1 <= older_than_days <= 3650:
            raise ValueError("older_than_days must be between 1 and 3650")

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
        ).isoformat()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM tasks
                WHERE status = ?
                  AND updated_at < ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM operation_journal
                      WHERE operation_journal.task_id = tasks.task_id
                        AND operation_journal.status IN ('pending', 'uncertain')
                  )
                """,
                (status, cutoff),
            )

        return cursor.rowcount

    def cleanup_expired_tasks(self, retention_days: int) -> int:
        if not 0 <= retention_days <= 3650:
            raise ValueError("retention_days must be between 0 and 3650")

        if retention_days == 0:
            return 0

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM tasks
                WHERE status IN ('completed', 'blocked', 'interrupted')
                  AND updated_at < ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM operation_journal
                      WHERE operation_journal.task_id = tasks.task_id
                        AND operation_journal.status IN ('pending', 'uncertain')
                  )
                """,
                (cutoff,),
            )

        return cursor.rowcount

    def metrics(self) -> dict[str, dict[str, int]]:
        with self._connect() as connection:
            task_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
            operation_rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM operation_journal
                GROUP BY status
                """
            ).fetchall()
            duration_row = connection.execute(
                """
                SELECT COALESCE(
                    AVG((julianday(updated_at) - julianday(created_at)) * 86400),
                    0
                ) AS average_seconds
                FROM operation_journal
                WHERE status != 'pending'
                """
            ).fetchone()

        return {
            "tasks": {row["status"]: row["count"] for row in task_rows},
            "operations": {
                row["status"]: row["count"] for row in operation_rows
            },
            "operation_average_seconds": round(
                max(0, duration_row["average_seconds"]),
                3,
            ),
        }
