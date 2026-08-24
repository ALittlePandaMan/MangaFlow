from fastapi import APIRouter

from app.api import fonts, images, models, projects, regions, review, tasks

api_router = APIRouter()

for route in (projects.router, images.router, regions.router, tasks.router, models.router, fonts.router, review.router):
    api_router.include_router(route)
