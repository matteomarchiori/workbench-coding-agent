import json
import posixpath
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required_tool: str = Field(min_length=1)
    required_path: str | None = None

    satisfied: bool = Field(default=False, strict=True)
    evidence_index: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def validate_non_empty_strings(self):
        fields = {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "required_tool": self.required_tool,
        }

        for name, value in fields.items():
            if not value.strip():
                raise ValueError(f"{name} cannot be empty or whitespace")

        return self

    @model_validator(mode="after")
    def validate_required_path(self):
        if self.required_path is not None and not self.required_path.strip():
            raise ValueError("required_path cannot be empty or whitespace")

        return self

    @model_validator(mode="after")
    def validate_evidence_consistency(self):
        if self.satisfied and self.evidence_index is None:
            raise ValueError("Satisfied criteria must have evidence")

        if not self.satisfied and self.evidence_index is not None:
            raise ValueError("Unsatisfied criteria cannot have evidence")

        return self


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[AcceptanceCriterion] = Field(
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_unique_criteria(self):
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]

        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Duplicate criterion IDs")

        criterion_keys = [
            (criterion.required_tool, criterion.required_path)
            for criterion in self.criteria
        ]

        if len(criterion_keys) != len(set(criterion_keys)):
            raise ValueError("Duplicate tool and path criteria")

        return self


class TaskEvidence(BaseModel):
    iteration: int = Field(ge=1, strict=True)
    call_id: str | None = None
    tool: str
    arguments: str
    success: bool
    validated: bool = False
    result: Any


class TaskState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str
    status: Literal["in_progress", "completed", "blocked", "interrupted"] = "in_progress"

    plan: TaskPlan | None = None

    iteration: int = Field(default=0, ge=0, strict=True)
    tool_calls: int = Field(default=0, ge=0, strict=True)

    evidence: list[TaskEvidence] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    final_response: str | None = None

    @staticmethod
    def valid_tool_result(tool: str, result: dict) -> bool:
        value = result.get("result")

        if tool == "list_workspace_files":
            return isinstance(value, list) and all(
                isinstance(item, str) for item in value
            )

        if tool == "read_workspace_file":
            return isinstance(value, str)

        if tool == "search_workspace_code":
            return isinstance(value, list) and all(
                isinstance(item, dict) for item in value
            )

        if tool == "write_workspace_file":
            return (
                isinstance(value, dict)
                and value.get("created") is True
                and value.get("overwritten") is False
                or isinstance(value, dict)
                and value.get("created") is False
                and value.get("overwritten") is True
                and isinstance(value.get("backup"), str)
            )

        if tool == "edit_workspace_file":
            return (
                isinstance(value, dict)
                and value.get("changed") is False
                and value.get("backup") is None
                or isinstance(value, dict)
                and value.get("changed") is True
                and isinstance(value.get("backup"), str)
            )

        if tool == "run_workspace_checks":
            checks = value.get("checks") if isinstance(value, dict) else None
            names = {
                check.get("name")
                for check in checks
                if isinstance(check, dict)
            } if isinstance(checks, list) else set()
            return (
                isinstance(value, dict)
                and isinstance(value.get("passed"), bool)
                and names == {"pytest", "python_syntax"}
                and isinstance(checks, list)
                and len(checks) == 2
                and all(
                    isinstance(check, dict)
                    and isinstance(check.get("name"), str)
                    and isinstance(check.get("passed"), bool)
                    and isinstance(check.get("exit_code"), int)
                    and (check["exit_code"] == 0) == check["passed"]
                    for check in checks
                )
                and value["passed"] == all(check["passed"] for check in checks)
            )

        return False

    @staticmethod
    def normalize_path(path: str) -> str | None:
        if not path or path.startswith("/"):
            return None

        normalized = posixpath.normpath(path)

        if normalized == ".." or normalized.startswith("../"):
            return None

        return normalized

    @model_validator(mode="after")
    def validate_tool_calls_consistency(self):
        if self.tool_calls < len(self.evidence):
            raise ValueError("tool_calls cannot be lower than evidence count")

        return self

    def is_tool_authorized(
        self,
        tool: str,
        arguments: str,
    ) -> bool:
        if self.plan is None:
            return False

        try:
            parsed_arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return False

        if not isinstance(parsed_arguments, dict):
            return False

        criterion = next(
            (
                item
                for item in self.plan.criteria
                if not item.satisfied
            ),
            None,
        )
        if criterion is None or criterion.required_tool != tool:
            return False

        if criterion.required_path is None:
            return True

        actual_path = parsed_arguments.get("path")

        if not isinstance(actual_path, str):
            return False

        actual_normalized = self.normalize_path(actual_path)
        required_normalized = self.normalize_path(criterion.required_path)

        return (
            actual_normalized is not None
            and required_normalized is not None
            and actual_normalized == required_normalized
        )

    def record_tool(
        self,
        iteration: int,
        tool: str,
        arguments: str,
        result: dict,
        call_id: str | None = None,
    ) -> None:
        if not isinstance(result, dict):
            raise ValueError("Tool result must be a dictionary")
        if iteration < 1:
            raise ValueError("Evidence iteration must be at least 1")

        if iteration > self.iteration:
            raise ValueError("Evidence iteration cannot exceed task iteration")
        success = result.get("ok") is True

        evidence_index = len(self.evidence)

        self.evidence.append(
            TaskEvidence(
                iteration=iteration,
                call_id=call_id,
                tool=tool,
                arguments=arguments,
                success=success,
                result=result,
            )
        )

        self.tool_calls += 1

        if not success:
            self.errors.append(f"{tool}: {result.get('error', 'Unknown error')}")
            return

        validated = self.valid_tool_result(tool, result)

        self.evidence[evidence_index].validated = validated

        if not validated:
            self.errors.append(f"{tool}: invalid result format")
            return

        if self.plan is None:
            return

        try:
            parsed_arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return

        if not isinstance(parsed_arguments, dict):
            return

        actual_path = parsed_arguments.get("path")

        for criterion in self.plan.criteria:
            if criterion.satisfied:
                continue

            if criterion.required_tool != tool:
                continue

            if criterion.required_path is not None:
                if not isinstance(actual_path, str):
                    continue

                actual_normalized = self.normalize_path(actual_path)
                required_normalized = self.normalize_path(criterion.required_path)

                if (
                    actual_normalized is None
                    or required_normalized is None
                    or actual_normalized != required_normalized
                ):
                    continue

            updated_criterion = AcceptanceCriterion(
                **{
                    **criterion.model_dump(),
                    "satisfied": True,
                    "evidence_index": evidence_index,
                }
            )

            criterion_index = self.plan.criteria.index(criterion)
            self.plan.criteria[criterion_index] = updated_criterion

    def has_successful_evidence(self) -> bool:
        return any(item.success for item in self.evidence)

    def can_complete(self, response: str) -> bool:
        if self.plan is None:
            return False

        if not response or not response.strip():
            return False

        for criterion in self.plan.criteria:
            if not criterion.satisfied:
                return False

            index = criterion.evidence_index

            if index is None or not (0 <= index < len(self.evidence)):
                return False

            evidence = self.evidence[index]

            if not evidence.success or not evidence.validated:
                return False

            if evidence.tool != criterion.required_tool:
                return False

            if criterion.required_path is not None:
                try:
                    arguments = json.loads(evidence.arguments)
                except (json.JSONDecodeError, TypeError):
                    return False

                if not isinstance(arguments, dict):
                    return False

                actual_path = arguments.get("path")

                if not isinstance(actual_path, str):
                    return False

                if self.normalize_path(actual_path) != self.normalize_path(
                    criterion.required_path
                ):
                    return False

        return True

    def complete(self, response: str) -> None:
        if not self.can_complete(response):
            raise ValueError("Cannot complete task: acceptance criteria not satisfied")

        self.status = "completed"
        self.final_response = response

    def block(self, reason: str) -> None:
        self.status = "blocked"
        self.errors.append(reason)
