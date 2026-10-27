from fastapi import APIRouter
from pydantic import BaseModel

from app.services.mcp_access import read_settings, write_settings

router = APIRouter(prefix="/mcp-settings", tags=["mcp-settings"])


class SettingsUpdate(BaseModel):
    enabled: bool | None = None
    public_url: str | None = None


@router.get("")
def get_mcp_settings():
    return read_settings()


@router.put("")
def update_mcp_settings(payload: SettingsUpdate):
    return write_settings(payload.model_dump(exclude_none=True))


@router.post("/rotate")
def rotate_mcp_key():
    return write_settings({}, rotate=True)


@router.get("/changes")
def external_changes(project_id: str):
    from sqlalchemy import func, select

    from app.core.database import SessionLocal
    from app.models import ImagePage, MCPOperation
    with SessionLocal() as db:
        return {"revision": str(db.scalar(select(func.count()).select_from(MCPOperation).where(MCPOperation.status == "COMPLETED"))) + ":" + str(
            db.scalar(select(func.max(ImagePage.updated_at)).where(ImagePage.project_id == project_id)))}


@router.get("/connector")
def connector_download():
    import io
    import zipfile

    from fastapi.responses import Response

    from app.services.mcp_setup import CONNECTOR_FILES, connector_directory
    directory = connector_directory()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in (*CONNECTOR_FILES, "install.mjs"):
            archive.write(directory / name, "mangaflow-mcp/" + name)
    return Response(output.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="mangaflow-mcp.zip"'})


@router.get("/uploads")
def active_uploads():
    from datetime import timedelta

    from sqlalchemy import or_, select

    from app.core.database import SessionLocal
    from app.models import MCPAsset
    from app.models.entities import utcnow
    with SessionLocal() as db:
        return [{"id": row.id, "filename": row.filename, "status": row.status,
            "progress": min(.99, row.size/row.expected_size) if row.expected_size else None}
            for row in db.scalars(select(MCPAsset).where(MCPAsset.expected_size > 0,
                or_(MCPAsset.status == "UPLOADING", MCPAsset.updated_at > utcnow()-timedelta(seconds=30))))]
