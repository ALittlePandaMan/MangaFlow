from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import SessionLocal, initialize_database
from app.services.model_manifest import (
    apply_model_manifest,
    load_model_manifest,
    persist_model_settings,
    preload_manifest_models,
)
from app.storage import get_storage
from app.tasks import task_manager

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
    initialize_database()
    provision_models_from_manifest()
    task_manager.start()
    task_manager.recover_interrupted()
    yield
    for running in list(task_manager.running.values()):
        running.cancel()


app = FastAPI(
    title=settings.app_name,
    description="Non-destructive AI-assisted manga translation and editing workflow",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/media", StaticFiles(directory=get_storage().root), name="media")

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})
