"""PostgreSQL transaction assertions shared by editor/pipeline regressions."""
from app.models import ImagePage, Project, TextRegion
from sqlalchemy import select


def assert_rows_unlocked(engine, project_id, page_id=None):
    with engine.connect() as connection:
        connection.execute(select(Project.id).where(Project.id == project_id).with_for_update(nowait=True))
        query = select(ImagePage.id).where(ImagePage.project_id == project_id)
        if page_id is not None:
            query = query.where(ImagePage.id == page_id)
        pages = list(connection.scalars(query.with_for_update(nowait=True)))
        list(connection.scalars(select(TextRegion.id).where(TextRegion.image_id.in_(pages)).with_for_update(nowait=True)))
