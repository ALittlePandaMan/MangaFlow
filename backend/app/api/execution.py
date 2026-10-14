from fastapi import APIRouter

from app.services.execution import ExecutionPolicy, execution_runtime

router = APIRouter(tags=["execution"])


@router.get("/execution")
def execution_status() -> dict:
    return execution_runtime().snapshot()


@router.put("/execution")
def update_execution_policy(policy: ExecutionPolicy) -> dict:
    runtime = execution_runtime()
    runtime.update(policy)
    return runtime.snapshot()
