"""Small projections and task controls for the comic-only public API."""
from app.core.database import SessionLocal
from app.models import ImagePage, MCPWorkflowRun, ProcessingTask, Project
from fastapi import HTTPException
from sqlalchemy import func, select


def compact_task(value, kind="processing"):
    if not isinstance(value, dict):
        from app.schemas.domain import TaskRead
        value = TaskRead.model_validate(value).model_dump(mode="json")
    output = {key: value[key] for key in ("id", "project_id", "image_id", "status", "progress", "message", "error_message", "error_kind", "current_stage")
        if value.get(key) is not None}
    output["kind"] = kind
    if kind == "file":
        if value.get("download_url") or value.get("download_path"):
            output["download_path"] = f"/mangaflow/download/{value['id']}"
        details = value.get("result", {})
        output["result"] = {key: item for key, item in details.items() if key in {
            "page_count", "count", "report_downloads", "error", "error_message", "stage", "completed", "total", "workflow_run_id"}}
        if details.get("page_ids"):
            output["result"]["page_count"] = len(details["page_ids"])
    return output


def project_list(args):
    from app.api.helpers import require_project
    with SessionLocal() as db:
        offset, limit = args.get("offset", 0), args.get("limit", 10)
        if args.get("project_id"):
            project = require_project(db, args["project_id"])
            query = select(ImagePage).where(ImagePage.project_id == project.id)
            count = db.scalar(select(func.count()).select_from(query.subquery()))
            pages = list(db.scalars(query.order_by(ImagePage.order_index, ImagePage.id).offset(offset).limit(limit)))
            return {"project_id": project.id, "name": project.name, "page_count": count,
                "source_language": project.source_language, "target_language": project.target_language,
                "pages": [{key: getattr(page, key) for key in ("id", "filename", "order_index", "status", "width", "height")} for page in pages],
                "runs": [{"run_id": run.id, "workflow_version": run.workflow_version, "page_count": len(run.page_ids)}
                    for run in db.scalars(select(MCPWorkflowRun).where(MCPWorkflowRun.project_id == project.id)
                        .order_by(MCPWorkflowRun.created_at.desc()).limit(5))],
                "next_offset": offset+len(pages) if offset+len(pages) < count else None}
        query = select(Project)
        if args.get("query"):
            query = query.where(Project.name.contains(args["query"], autoescape=True))
        rows = list(db.scalars(query.order_by(Project.updated_at.desc(), Project.id).offset(offset).limit(limit+1)))
        return {"projects": [{"id": item.id, "name": item.name, "source_language": item.source_language,
            "target_language": item.target_language, "page_count": db.scalar(select(func.count(ImagePage.id)).where(ImagePage.project_id == item.id)),
            "latest_run_id": db.scalar(select(MCPWorkflowRun.id).where(MCPWorkflowRun.project_id == item.id).order_by(MCPWorkflowRun.created_at.desc()).limit(1))}
            for item in rows[:limit]], "next_offset": offset+limit if len(rows) > limit else None}


def cancel(args):
    with SessionLocal() as db:
        from app.models import FileJob, MCPOperation
        from app.services.mcp_coordination import coordinator, owners
        task = db.get(FileJob if args["kind"] == "file" else ProcessingTask, args["task_id"])
        if task and args["kind"] == "processing":
            runs = owners(db, task.image_id) if task.image_id else []
            linked = db.get(MCPWorkflowRun, task.payload.get("options", {}).get("workflow_run_id")) if task.payload.get("options", {}).get("workflow_run_id") else None
            for run in [*runs, *([linked] if linked and linked not in runs else [])]:
                coordinator(run)
        elif task:
            op = db.get(MCPOperation, task.result.get("mcp_operation_id")) if task.result.get("mcp_operation_id") else None
            if op and op.workflow_run_id:
                coordinator(db.get(MCPWorkflowRun, op.workflow_run_id))
        if args["kind"] == "file":
            from app.api.file_jobs import cancel_job
            return compact_task(cancel_job(args["task_id"], db), "file")
        from app.api.tasks import cancel_task
        return compact_task(cancel_task(args["task_id"], db))


def retry(args):
    from app.api.tasks import retry_task
    from app.services.mcp_context import workflow_action, workflow_run_id
    with SessionLocal() as db:
        task = db.get(ProcessingTask, args["task_id"])
        if not task:
            raise HTTPException(404, "Task not found")
        if task.status != "FAILED" or task.error_kind == "unknown_cloud_outcome":
            raise HTTPException(409, "Only clearly FAILED processing tasks can be retried")
        run_id = task.payload.get("options", {}).get("workflow_run_id")
        if not run_id or args.get("workflow_run_id", run_id) != run_id:
            raise HTTPException(422, "Retry only tasks belonging to the same official workflow")
        from app.services.mcp_coordination import coordinator
        coordinator(db.get(MCPWorkflowRun, run_id))
        token = workflow_run_id.set(run_id)
        action = workflow_action.set("task_retry")
        try:
            return compact_task(retry_task(task.id, False, db))
        finally:
            workflow_action.reset(action)
            workflow_run_id.reset(token)
