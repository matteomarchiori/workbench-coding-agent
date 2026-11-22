from typing import Any

from pydantic import BaseModel, Field

from agent.schemas.tasks import TaskState


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)


class ChatResponse(BaseModel):
    response: str
    model: str


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)


class AgentRunResponse(BaseModel):
    response: str
    model: str
    iterations: int
    tool_calls: int
    trace: list[dict[str, Any]]
    task: TaskState
