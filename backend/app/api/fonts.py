from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import get_settings
from app.services import font_presets
from app.services.font_library import font_family, notify_fonts_changed
from app.storage import get_storage

router = APIRouter(prefix="/fonts", tags=["fonts"])
SUPPORTED_FONT_SUFFIXES = {".ttf", ".otf"}


def _font_directory() -> Path:
    directory = get_storage().root / "fonts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _font_entry(path: Path) -> dict[str, str]:
    relative = get_storage().relative(path)
    entry = {
        "name": font_family(path),
        "filename": path.name,
        "path": relative,
        "url": get_storage().media_url(relative) or "",
    }
    preset = font_presets.BY_FILENAME.get(path.name)
    if preset and font_presets.installed(preset, path.parent):
        # A pinned font may put a localized alias first in its name table.
        # Preserve legacy uploads, but use the reviewed, filename-compatible
        # family for presets so selecting them never falls back to Noto.
        entry.update(name=preset.family, preset_id=preset.id, source_url=preset.source_url,
                     license_url=get_storage().media_url(f"fonts/licenses/{preset.id}.txt") or "")
    return entry


@router.get("/presets")
def list_font_presets() -> list[dict]:
    return font_presets.catalog(_font_directory())


@router.post("/presets/{preset_id}/install", status_code=201)
def install_font_preset(preset_id: str) -> dict[str, str]:
    try:
        path = font_presets.install(preset_id, _font_directory())
        notify_fonts_changed()
        return _font_entry(path)
    except font_presets.FontInstallError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("")
def list_fonts() -> list[dict[str, str]]:
    fonts: list[dict[str, str]] = []
    for path in sorted(_font_directory().iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_FONT_SUFFIXES:
            continue
        try:
            fonts.append(_font_entry(path))
        except Exception:
            # A damaged file should not make the entire font picker unavailable.
            continue
    return fonts


@router.post("", status_code=201)
def upload_font(file: UploadFile = File(...)) -> dict[str, str]:
    original_name = Path(file.filename or "font.otf").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_FONT_SUFFIXES:
        raise HTTPException(422, "Only TrueType (.ttf) and OpenType (.otf) fonts are supported")

    directory = _font_directory()
    temporary = directory / f".upload-{uuid.uuid4().hex}{suffix}"
    maximum = get_settings().max_upload_mb * 1024 * 1024
    written = 0
    try:
        with temporary.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > maximum:
                    raise HTTPException(413, f"Font exceeds the {get_settings().max_upload_mb} MB limit")
                output.write(chunk)
        try:
            family = font_family(temporary, fallback=Path(original_name).stem)
        except Exception as exc:
            raise HTTPException(400, "The uploaded file is not a valid TrueType/OpenType font") from exc

        # Keep the family in the stored filename so the renderer can resolve it
        # by family name without persisting machine-specific absolute paths.
        family_stem = "".join(character for character in family if character.isalnum())[:120]
        original_stem = "".join(character for character in Path(original_name).stem if character.isalnum())[:120]
        destination = directory / f"{family_stem or original_stem or 'CustomFont'}{suffix}"
        temporary.replace(destination)
        notify_fonts_changed()
        return _font_entry(destination)
    except HTTPException:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(500, "Unable to save the font") from exc


@router.delete("/{filename}", status_code=204)
def delete_font(filename: str) -> None:
    if Path(filename).name != filename or Path(filename).suffix.lower() not in SUPPORTED_FONT_SUFFIXES:
        raise HTTPException(404, "Font not found")
    target = (_font_directory() / filename).resolve()
    if target.parent != _font_directory().resolve() or not target.is_file():
        raise HTTPException(404, "Font not found")
    target.unlink()
    notify_fonts_changed()
