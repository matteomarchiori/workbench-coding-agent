import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from agent.models.provider import ModelProvider
from agent.orchestrator import AgentOrchestrator
from agent.schemas.chat import (
    ChatRequest,
    ChatResponse,
    AgentRunRequest,
    AgentRunResponse,
)
from agent.storage.repository import TaskRepository
from agent.tools.filesystem import (
    list_files,
    read_file,
    search_code,
)
from agent.schemas.workspace import (
    ReadFileRequest,
    SearchRequest,
)

logger = logging.getLogger("localcoder")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

provider = ModelProvider()
repository = TaskRepository(os.getenv("DATABASE_PATH", "data/localcoder.db"))
orchestrator = AgentOrchestrator(provider, repository)


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository.mark_pending_operations_uncertain()
    repository.mark_interrupted()
    retention_days = int(os.getenv("TASK_RETENTION_DAYS", "0"))
    deleted = repository.cleanup_expired_tasks(retention_days)
    if deleted:
        logger.info("Startup retention removed %d expired tasks", deleted)
    yield


app = FastAPI(
    title="Local Coding Agent",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "agent-core",
    }


@app.get("/health/model")
async def model_health():
    available = await provider.health_check()

    return {
        "model": provider.model,
        "available": available,
    }


@app.get("/metrics")
async def metrics():
    return {
        **repository.metrics(),
        "provider": provider.metrics(),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        response = await provider.generate(
            [
                {
                    "role": "user",
                    "content": request.message,
                }
            ]
        )

        return ChatResponse(
            response=response,
            model=provider.model,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Model provider error: {str(exc)}",
        )


@app.post("/agent/run", response_model=AgentRunResponse)
async def agent_run(request: AgentRunRequest):
    try:
        return await orchestrator.run(request.message)

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Agent execution error: {str(exc)}",
        )


@app.get("/tasks")
async def list_tasks(
    status: str | None = None,
    limit: int = 100,
):
    try:
        tasks = repository.list_tasks(status=status, limit=limit)
        return {"tasks": tasks, "count": len(tasks)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = repository.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return task


@app.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str):
    if not repository.delete(task_id):
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )
    return None


@app.post("/tasks/cleanup")
async def cleanup_tasks(status: str, older_than_days: int = 30):
    try:
        deleted = repository.cleanup(status=status, older_than_days=older_than_days)
        return {"deleted": deleted, "status": status}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/tasks/{task_id}/resume", response_model=AgentRunResponse)
async def resume_task(task_id: str):
    try:
        task = repository.prepare_resume(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    logger.info("Resuming task %s from iteration %s", task_id, task.iteration)

    try:
        return await orchestrator.run(task.goal, task=task)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Agent execution error: {str(exc)}",
        )


@app.get("/tasks/{task_id}/operations")
async def list_task_operations(task_id: str):
    task = repository.get(task_id)

    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    operations = repository.list_operations(task_id)
    return {
        "task_id": task_id,
        "operations": operations,
        "count": len(operations),
    }


@app.get("/workspace/files")
async def workspace_files(path: str = ".", limit: int = 200):
    try:
        return {"files": list_files(path, limit)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/workspace/read")
async def workspace_read(request: ReadFileRequest):
    try:
        return {
            "path": request.path,
            "content": read_file(request.path),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/workspace/search")
async def workspace_search(request: SearchRequest):
    try:
        return {
            "results": search_code(
                request.query,
                request.path,
                request.limit,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
