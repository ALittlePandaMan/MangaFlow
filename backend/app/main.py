from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import fonts, images, models, projects, regions, review, tasks
from app.core.config import get_settings
from app.core.database import initialize_database
from app.storage import get_storage
from app.tasks import task_manager

settings = get_settings()
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
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

for router in (projects.router, images.router, regions.router, tasks.router, models.router, fonts.router, review.router):
    app.include_router(router, prefix=settings.api_prefix)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})
