from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import SessionLocal, initialize_database
from app.services.infra.model_manifest import (
    apply_model_manifest,
    load_model_manifest,
    persist_model_settings,
    preload_manifest_models,
)
from app.storage import get_storage
from app.storage.media import VersionedMediaFiles
from app.tasks import task_manager
from app.version import APP_VERSION

settings = get_settings()
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def provision_models_from_manifest() -> None:
    """Seed portable defaults and warm local model caches before accepting work."""
    manifest_path = settings.model_manifest_path
    if manifest_path is None:
        return
    try:
        manifest = load_model_manifest(manifest_path)
        with SessionLocal() as db:
            applied = apply_model_manifest(db, manifest)
            preload = preload_manifest_models(db, manifest) if settings.auto_provision_models else []
            persist_model_settings(
                db,
                manifest_path,
                environment_path=settings.environment_file_path,
            )
        for item in applied:
            logger.info(
                "Model manifest %s: %s -> %s (%s)",
                item["action"],
                item["kind"],
                item["provider"],
                manifest_path,
            )
        for item in preload:
            if item["status"] in {"error", "dependency_missing", "missing"}:
                logger.error(
                    "Model preload failed for %s/%s: %s",
                    item["kind"],
                    item["provider"],
                    item["error"],
                )
            elif item["status"] == "ready":
                logger.info("Model preload ready: %s/%s", item["kind"], item["provider"])
    except Exception:
        # Keep the API available so the required-setup screen can explain and
        # repair an invalid manifest or a provider download failure.
        logger.exception("Unable to apply model manifest %s", manifest_path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.maintenance_mode:
        # Migration verification must not provision models, alter schema,
        # dispatch old queued tasks, reconcile history, or collect assets.
        logger.warning("Maintenance startup: business API and background workers are disabled")
        yield
        return
    from app.services.execution import configure_native_threads
    configure_native_threads()
    initialize_database()
    from app.services.mcp_server import recover_operations
    recover_operations()
    provision_models_from_manifest()
    task_manager.start()
    task_manager.recover_interrupted()
    from app.services.file_jobs import file_worker
    file_worker.start()
    stop_maintenance = asyncio.Event()
    async def maintain_history():
        from app.services.history_assets import collect_idle_history_assets
        from app.services.workflow_assets import collect_idle_workflow_assets
        while not stop_maintenance.is_set():
            try:
                await asyncio.wait_for(stop_maintenance.wait(), timeout=600)
            except TimeoutError:
                await asyncio.to_thread(collect_idle_history_assets)
                await asyncio.to_thread(collect_idle_workflow_assets)
                from app.services.mcp_files import collect_assets
                await asyncio.to_thread(collect_assets)
    maintenance = asyncio.create_task(maintain_history())
    try:
        from app.services.mcp_server import manager
        app.state.mcp_manager = manager(app)
        async with app.state.mcp_manager.run():
            yield
    finally:
        if getattr(app.state, "mcp_pending_writes", None):
            _, unfinished = await asyncio.wait(app.state.mcp_pending_writes, timeout=25)
            for future in unfinished:
                future.cancel()
        stop_maintenance.set()
        await maintenance
        await task_manager.shutdown()
        await asyncio.to_thread(file_worker.shutdown)


app = FastAPI(
    title=settings.app_name,
    description="Non-destructive AI-assisted manga translation and editing workflow",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/media", VersionedMediaFiles(directory=get_storage().root), name="media")

app.include_router(api_router, prefix=settings.api_prefix)
from app.services.mcp_server import install as install_mcp  # noqa: E402

install_mcp(app)


@app.middleware("http")
async def maintenance_gate(request: Request, call_next):
    if settings.maintenance_mode and request.url.path != "/health":
        return JSONResponse(
            status_code=503,
            content={"detail": "服务正在维护校验，任务与业务操作暂未开放", "maintenance": True},
            headers={"Retry-After": "60"},
        )
    return await call_next(request)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {
        "status": "maintenance" if settings.maintenance_mode else "ok",
        "service": settings.app_name,
        "version": APP_VERSION,
    }


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})
