from fastapi import APIRouter

from app.api import (
    execution,
    file_jobs,
    fonts,
    history,
    images,
    mcp_settings,
    models,
    preferences,
    projects,
    regions,
    review,
    tasks,
)

api_router = APIRouter()
api_router.include_router(execution.router)
api_router.include_router(file_jobs.router)
api_router.include_router(mcp_settings.router)

for route in (projects.router, images.router, regions.router, history.router, tasks.router, models.router, fonts.router, review.router, preferences.router):
    api_router.include_router(route)
