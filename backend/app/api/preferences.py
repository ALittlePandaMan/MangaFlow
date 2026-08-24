from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.services.infra.model_manifest import (
    ManifestPreferences,
    ModelManifestError,
    load_model_manifest,
    persist_shortcut_preferences,
)

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("")
def get_preferences() -> ManifestPreferences:
    path = get_settings().model_manifest_path
    if path is None or not Path(path).is_file():
        return ManifestPreferences()
    try:
        return load_model_manifest(path).preferences or ManifestPreferences()
    except ModelManifestError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("")
def update_preferences(payload: ManifestPreferences) -> ManifestPreferences:
    try:
        return persist_shortcut_preferences(get_settings().model_manifest_path, payload.shortcuts)
    except ModelManifestError as exc:
        raise HTTPException(422, str(exc)) from exc
