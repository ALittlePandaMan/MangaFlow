"""Standard MCP transport, business tools and durable mutation receipts."""
import asyncio
import json
import logging
import math
import time
from urllib.parse import quote

import httpx
import jsonschema
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import MCPOperation
from app.services.mcp_access import MCPAccessMiddleware, read_settings
from app.services.mcp_catalog import CUSTOM
from app.services.mcp_context import (
    coordination_credentials,
    expected_versions,
    operation_identifier,
    operation_source,
    workflow_action,
    workflow_run_id,
)
from app.services.mcp_files import collect_assets
from app.services.mcp_files import router as file_router
from app.services.mcp_review_plan import review_plan
from app.services.mcp_tools import paint, preview, state
from app.services.mcp_workflow_definitions import INSTRUCTIONS, VERSION, workflow_get, workflow_list
from app.services.workflow_state import fingerprint
from fastapi import HTTPException
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

logger = logging.getLogger(__name__)


class ToolRateLimiter:
    """Bound aggregate invocation bursts before accepting any business work.

    Consumed synchronously on the server event loop, with constant-size state
    shared by every client and both endpoint aliases in this worker.
    """

    def __init__(self, rate=60, capacity=120, clock=None):
        self.rate, self.capacity = rate, capacity
        self.clock = clock or time.monotonic
        self.updated = self.clock()
        self.tokens = float(capacity)

    def retry_after(self):
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + max(0, now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0
        return (1 - self.tokens) / self.rate


def redact_credentials(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if key.lower() in {
            "api_key", "encrypted_api_key", "access_token", "refresh_token", "authorization", "password", "client_secret",
        } and item else redact_credentials(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    return value


def result(data, image=None, error=False, *, text_data=None):
    data = redact_credentials(data if isinstance(data, dict) else {"items": data})
    text_value = data if text_data is None else redact_credentials(text_data)
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(text_value, ensure_ascii=False, separators=(",", ":"))),
        *([image] if image else [])], structuredContent=data, isError=error)


def begin_operation(identifier, name, arguments):
    digest = fingerprint({"name": name, "arguments": arguments})
    with SessionLocal() as db:
        run_id = arguments.get("workflow_run_id") or arguments.get("run_id")
        if not run_id and name in {"task_retry", "task_cancel"}:
            from app.models import ProcessingTask
            task = db.get(ProcessingTask, arguments.get("task_id"))
            if task:
                run_id = task.payload.get("options", {}).get("workflow_run_id")
        try:
            db.add(MCPOperation(id=identifier, fingerprint=digest, status="RUNNING",
                result={"_request": {"tool": name, **({"image_ids": arguments["image_ids"]} if "image_ids" in arguments else {}),
                    **({"action": arguments["action"]} if "action" in arguments else {})}}, workflow_run_id=run_id))
            db.commit()
            return None
        except IntegrityError:
            db.rollback()
        previous = db.get(MCPOperation, identifier)
        if previous.fingerprint != digest:
            raise HTTPException(409, "operation_id was used for different arguments")
        if previous.status == "RUNNING":
            return result({"operation_id": identifier, "status": "RUNNING", "next_tool": "operation_status"})
        if previous.status != "COMPLETED":
            return result({"error": {"code": "operation_in_progress_or_unknown", "retryable": False,
                "message": "Inspect operation_status and project/task state; never blindly replay an interrupted mutation."}}, error=True)
        return types.CallToolResult.model_validate(previous.result)


def finish_operation(identifier, response):
    with SessionLocal() as db:
        row = db.get(MCPOperation, identifier)
        row.status = "UNKNOWN" if response.structuredContent.get("error", {}).get("code") == "operation_unknown" else "COMPLETED"
        row.result = {**response.model_dump(mode="json"), **({key: row.result[key] for key in ("_request", "_admission", "_coordination") if key in row.result} if row.status == "UNKNOWN" else {})}
        db.commit()


def install(app):
    server = Server("MangaFlow", version=VERSION, instructions=INSTRUCTIONS)
    for name, field in (("project_import", "images"), ("project_import_status", "asset_ids")):
        definition = next(tool for tool in CUSTOM if tool.name == name)
        definition.inputSchema["properties"][field]["maxItems"] = get_settings().max_import_pages
    definitions = {tool.name: tool for tool in CUSTOM}
    pending_writes = set()
    app.state.mcp_pending_writes = pending_writes
    app.state.mcp_tool_limiter = ToolRateLimiter()

    @server.list_tools()
    async def list_tools():
        return list(definitions.values())

    @server.list_prompts()
    async def list_prompts():
        return [types.Prompt(name=item["id"], description=item["description"]) for item in workflow_list()["workflows"]]

    @server.get_prompt()
    async def get_prompt(name, arguments=None):
        if name not in {item["id"] for item in workflow_list()["workflows"]}:
            raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message="Unknown prompt: " + name))
        if arguments:
            raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message="This workflow prompt takes no arguments; pass execution options to workflow_start"))
        return types.GetPromptResult(messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text=workflow_get(name)["instructions"]))])

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        definition = definitions.get(name)
        if not definition:
            return result({"error": {"code": "unknown_tool", "message": name, "retryable": False}}, error=True)
        retry_after = app.state.mcp_tool_limiter.retry_after()
        if retry_after:
            return result({"error": {"code": "rate_limited", "status": 429, "retryable": True,
                "retry_after_seconds": max(1, math.ceil(retry_after)),
                "message": "Too many MCP tool calls; wait and retry with the same operation_id."}}, error=True)
        vtoken = expected_versions.set(arguments.get("expected_versions", {}))
        stoken = operation_source.set("MCP")
        wtoken = workflow_run_id.set(arguments.get("workflow_run_id"))
        atoken = workflow_action.set(name)
        otoken = operation_identifier.set(None)
        ctoken = coordination_credentials.set(arguments)
        try:
            jsonschema.validate(arguments, definition.inputSchema)
            if name == "workflow_advance" and "image_id" in arguments and "image_ids" in arguments:
                raise HTTPException(422, "Supply image_id OR image_ids")
            if not definition.annotations.readOnlyHint and name != "operation_status":
                candidate = arguments["operation_id"]
                previous = await run_in_threadpool(begin_operation, candidate, name, arguments)
                if previous is not None:
                    return previous
                operation_identifier.set(candidate)
                # A disconnected MCP request must not cancel a mutation whose
                # provider/DB work was already accepted. Retain the future and
                # publish its receipt independently of the transport lifetime.
                future = asyncio.create_task(execute(name, arguments, candidate))
                pending_writes.add(future)
                future.add_done_callback(pending_writes.discard)
                try:
                    return await asyncio.wait_for(asyncio.shield(future), timeout=20)
                except TimeoutError:
                    return result({"operation_id": candidate, "status": "RUNNING", "next_tool": "operation_status"})
            return await execute(name, arguments)
        except (HTTPException, ValueError, jsonschema.ValidationError) as exc:
            return failure(exc)
        finally:
            expected_versions.reset(vtoken)
            operation_source.reset(stoken)
            workflow_run_id.reset(wtoken)
            workflow_action.reset(atoken)
            operation_identifier.reset(otoken)
            coordination_credentials.reset(ctoken)

    # The SDK's call_tool decorator converts handler exceptions to execution
    # errors. Unknown tool names are protocol errors, so check them outside that
    # wrapper before dispatching any business operation.
    business_handler = server.request_handlers[types.CallToolRequest]

    async def protocol_call(request):
        name = request.params.name
        if name not in definitions:
            raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message="Unavailable tool: " + name +
                "; MangaFlow MCP 2.0 exposes comic tools only. Update the official connector via /mangaflow/setup and read workflow_get.",
                data={"code": "unknown_tool", "retryable": False}))
        return await business_handler(request)

    server.request_handlers[types.CallToolRequest] = protocol_call
    for request_type in (types.ListToolsRequest, types.ListPromptsRequest):
        original_handler = server.request_handlers[request_type]

        async def list_without_cursor(request, handler=original_handler):
            if request and request.params and request.params.cursor is not None:
                raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message="Invalid cursor: this list has no further pages"))
            return await handler(request)

        server.request_handlers[request_type] = list_without_cursor

    def failure(exc):
        status = exc.status_code if isinstance(exc, HTTPException) else 422
        code = {404: "not_found", 409: "conflict", 410: "expired", 413: "too_large"}.get(status, "invalid_request")
        return result({"error": {"code": code, "status": status,
            "message": str(getattr(exc, "detail", exc)), "retryable": False}}, error=True)

    async def execute(name, arguments, identifier=None):
        try:
            response = await dispatch(name, arguments)
        except (HTTPException, ValueError, jsonschema.ValidationError) as exc:
            response = failure(exc)
        except Exception:
            logger.exception("MCP tool failed: %s", name)
            response = result({"error": {"code": "operation_unknown", "message": "Inspect current state and operation_status before retrying",
                "retryable": False}}, error=True)
        if identifier:
            await run_in_threadpool(finish_operation, identifier, response)
        return response

    async def dispatch(name, args):
        from app.services import mcp_actions, mcp_workflows
        from app.services.mcp_directory_import import import_limits, import_project
        from app.services.mcp_directory_import import status as import_status
        from app.services.mcp_page_edit import edit, resolve_regions
        from app.services.mcp_workflow_runtime import advance
        if args.get("workflow_run_id") and name != "mask_paint":
            await run_in_threadpool(mcp_workflows.validate_call, args, None, name)
        handlers = {"workflow_start": mcp_workflows.start, "workflow_status": mcp_workflows.status,
            "workflow_review_page": mcp_workflows.review_page, "workflow_begin_correction": mcp_workflows.begin_correction,
            "workflow_export": mcp_workflows.export_run, "workflow_advance": advance,
            "project_list": mcp_actions.project_list, "project_import": import_project,
            "project_import_status": import_status, "page_edit": edit,
            "task_cancel": mcp_actions.cancel, "task_retry": mcp_actions.retry}
        if name in handlers:
            data = await run_in_threadpool(handlers[name], args)
            if name in {"project_import", "project_import_status", "workflow_export"} and data.get("job"):
                data = {**data, "job": mcp_actions.compact_task(data["job"], "file")}
            if name == "workflow_status" and not args.get("include_details"):
                # Keep every actionable parameter for text-only clients, without
                # repeating the same next steps and historical task metadata.
                text_data = {key: data[key] for key in ("run_id", "page_count", "counts", "current_page_id",
                    "configuration_stale", "blockers", "next_steps", "offset", "next_offset", "exports")}
                if "batch" in data:
                    text_data["batch"] = data["batch"]
                if "coordination" in data:
                    text_data["coordination"] = data["coordination"]
                if "configuration_change" in data:
                    text_data["configuration_change"] = data["configuration_change"]
                batch_pages = {identifier for value in data["next_steps"] if value["tool"] == "workflow_advance"
                    for identifier in value["arguments"].get("image_ids", [])}
                text_data["pages"] = [{**{key: item[key] for key in ("image_id", "version", "status", "execution_stage", "remaining_rounds")},
                    **({"assignment": item["assignment"]} if "assignment" in item else {}),
                    **({"next_steps": item["next_steps"]} if item["next_steps"] != data["next_steps"] and item["image_id"] not in batch_pages else {})} for item in data["pages"]]
                return result(data, text_data=text_data)
            return result(data)
        if name == "workflow_get":
            return result(workflow_get(args.get("workflow_id", "translate_and_review"), role=args.get("role", "coordinator")))
        if name == "page_state":
            return result(await run_in_threadpool(state, **args))
        if name == "page_preview":
            metadata, picture = await run_in_threadpool(preview, args)
            return result(metadata, picture)
        if name == "page_review_plan":
            return result(await run_in_threadpool(review_plan, args))
        if name == "mask_paint":
            def paint_region():
                values = dict(args)
                identifier = args.get("image_id") or next(iter(args["expected_versions"]))
                with SessionLocal() as db:
                    row = resolve_regions(db, identifier, [args["region_id"]])[0]
                    if row.locked:
                        raise HTTPException(409, "Locked regions cannot be edited by MCP")
                    values["region_id"] = row.id
                if values.get("workflow_run_id"):
                    mcp_workflows.validate_call(values, None, name)
                change = paint(values)
                with SessionLocal() as db:
                    from app.services.workflow_state import current_page_version
                    return {"image_id": identifier, "region_id": row.id, "version": current_page_version(db, identifier),
                        **change, "next_tool": "workflow_advance" if change["changed"] else "page_state"}
            return result(await run_in_threadpool(paint_region))
        if name == "mcp_status":
            settings = await run_in_threadpool(read_settings)
            return result({"enabled": settings["enabled"], "public_url": settings["public_url"], "api_version": "2.0.0",
                "tool_count": 20, "installation": {"guide_path": "/mangaflow/setup", "installer_path": "/mangaflow/install.mjs",
                    "minimum_connector_version": "2.0.0", "default_transport": "stdio"},
                "connection_capabilities": {"transport": "http", "local_import": False, "local_download": False,
                    "local_transfer_status": False, "local_transfer_cancel": False, "native_image_content": True,
                    "directory_import_identity": True, "client_image_viewing": "unknown", "existing_projects_and_assets": True,
                    "parallel_page_claims": True, "subagent_orchestration": "client", "child_image_viewing": "unknown",
                    "missing_local_files": "Install/update the official local connector using /mangaflow/setup, reconnect, then use local_import."},
                "import_limits": import_limits()})
        if name == "operation_status":
            from app.services.mcp_operations import status as operation_status
            return result(await run_in_threadpool(operation_status, args))
        if name == "task_wait":
            path = "/tasks/" if args["kind"] == "processing" else "/file-jobs/"
            deadline = time.monotonic() + min(20, args.get("seconds", 0))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                while True:
                    response = await client.get(get_settings().api_prefix + path + quote(args["task_id"], safe=""))
                    if response.status_code >= 400:
                        raise HTTPException(response.status_code, "Task not found")
                    data = response.json()
                    if data["status"] in {"COMPLETED", "FAILED", "CANCELLED", "PAUSED"} or time.monotonic() >= deadline:
                        return result(mcp_actions.compact_task(data, args["kind"]))
                    await asyncio.sleep(min(.5, max(0, deadline-time.monotonic())))
        raise HTTPException(404, "Unknown tool")

    class Transport:
        async def __call__(self, scope, receive, send):
            await app.state.mcp_manager.handle_request(scope, receive, send)

    app.include_router(file_router)
    from app.services.mcp_setup import router as setup_router
    app.include_router(setup_router)
    app.router.routes.append(Route("/mangaflow", Transport(), methods=["GET", "POST", "DELETE"]))
    app.router.routes.append(Route("/mcp", Transport(), methods=["GET", "POST", "DELETE"]))
    app.add_middleware(MCPAccessMiddleware)
    app.state.mcp_server = server


def manager(app):
    return StreamableHTTPSessionManager(app.state.mcp_server, stateless=True, json_response=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False))


def recover_operations():
    from app.services.mcp_files import recover_uploads
    recover_uploads()
    read_settings()
    from sqlalchemy import update
    with SessionLocal() as db:
        db.execute(update(MCPOperation).where(MCPOperation.status == "RUNNING").values(status="UNKNOWN"))
        db.commit()
    collect_assets()
