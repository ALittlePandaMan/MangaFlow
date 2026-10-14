"""Remove cached repairs without rerunning the inpainting model."""
from __future__ import annotations

from uuid import uuid4

from app.models import ImagePage, TextRegion
from app.storage import get_storage
from app.utils.image_metadata import load_rgb_with_metadata
from PIL import Image, ImageChops


def restore_deleted_pixels(page: ImagePage, removed: list[TextRegion], remaining: list[TextRegion]) -> bool:
    # An invalid clean image (e.g. after a source frame edit) stays invalid. A
    # delete is not permission to run repair on all the other edited masks.
    if not page.clean_path or not any(region.pixel_mask_path for region in removed):
        return True
    storage=get_storage()
    size=(page.width,page.height)
    try:
        union=Image.new("L",size,0)
        for region in removed:
            if not region.pixel_mask_path:
                continue
            with Image.open(storage.absolute(region.pixel_mask_path)) as source:
                if source.size != size:
                    return False
                union=ImageChops.lighter(union,source.convert("L"))
        box=union.getbbox()
        if box is None:
            return True
        restore=union.crop(box).point(lambda value: 255 if value > 0 else 0)
        # Do not undo another region's repair in an overlapping mask.
        for region in remaining:
            if not region.pixel_mask_path:
                continue
            with Image.open(storage.absolute(region.pixel_mask_path)) as source:
                if source.size != size:
                    return False
                protected=source.convert("L").crop(box).point(lambda value: 255 if value > 0 else 0)
                restore=ImageChops.subtract(restore,protected)
        clean,metadata=load_rgb_with_metadata(storage.absolute(page.clean_path))
        original,_=load_rgb_with_metadata(storage.absolute(page.original_path))
        if clean.size != size or original.size != size:
            return False
        clean.paste(original.crop(box),box,restore)
        output=storage.page_dir(page.project_id,page.id)/"clean"/f"delete-{uuid4().hex}.png"
        clean.save(output,"PNG",compress_level=1,**metadata)
        page.clean_path=storage.relative(output)
        metadata_json=dict(page.metadata_json or {})
        versions=dict(metadata_json.get("artifact_versions",{}))
        versions["clean"]=output.stat().st_mtime_ns
        metadata_json["artifact_versions"]=versions
        page.metadata_json=metadata_json
        return True
    except (OSError,ValueError):
        # A damaged/missing legacy cache cannot be patched safely. Retain the
        # existing full rebuild path as a correctness fallback.
        return False
