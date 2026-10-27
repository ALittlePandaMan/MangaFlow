"""Exports consume detached snapshots and never publish render results."""
import shutil
from pathlib import Path
from types import SimpleNamespace

from app.services.export_archive import ExportArchive
from app.services.exporting import export_project
from app.services.layout import FontResolver
from app.services.rendering.pillow_renderer import RENDER_OUTPUT_VERSION, PillowRenderer
from app.services.rendering.snapshot import render_input
from app.storage import get_storage
from app.utils.image_metadata import load_rgb_with_metadata, save_png_with_metadata
from PIL import Image


class FrozenFontResolver(FontResolver):
    def __init__(self, paths):
        self.paths = list(map(Path, paths))
        self._coverage = {}
        self.refresh()

    def refresh(self):
        from app.services.font_library import font_library_revision
        self._library_revision = font_library_revision()


def current_render(state):
    """Reuse only pixels bound to every current visual input and renderer version."""
    page, rows = state["page"], state["regions"]
    if not page.get("rendered_path") or not get_storage().absolute(page["rendered_path"]).is_file():
        return False
    if set((page.get("metadata_json") or {}).get("text_layer_region_ids", [])) != set(rows):
        return False
    for row in rows.values():
        region = SimpleNamespace(**row)
        data = region.layout_data or {}
        if data.get("render_input") != render_input(region):
            return False
        if region.visible and region.translated_text.strip() and data.get("render_output_version") != RENDER_OUTPUT_VERSION:
            return False
    return True


def export_snapshot(snapshot, kind, directory, check, progress):
    directory.mkdir(parents=True, exist_ok=True)
    project = SimpleNamespace(**snapshot["project"])
    project.pages = []
    for state in snapshot["pages"]:
        page = SimpleNamespace(**state["page"])
        page.regions = sorted((SimpleNamespace(**row) for row in state["regions"].values()),
                              key=lambda row: (row.reading_order, row.created_at, row.id))
        project.pages.append(page)
    storage = get_storage()
    renderer = None
    files, fonts = [], set()
    total = len(project.pages)
    render_end = .8 if kind in {"translated", "cbz", "psd", "text_layer"} else 0
    progress(0, "rendering" if render_end else "packaging", 0, total)
    for index, page in enumerate(project.pages):
        check()
        if kind in {"translated", "cbz", "psd", "text_layer"}:
            state = snapshot["pages"][index]
            reusable = state["_reuse_render"] if "_reuse_render" in state else current_render(state)
            if kind != "psd" and reusable:
                files.append(storage.absolute(page.text_layer_path if kind == "text_layer" else page.rendered_path))
                progress(.8 * (index + 1) / total, "rendering", index + 1, total)
                continue
            root = directory / f"page-{index:04d}"
            root.mkdir(exist_ok=True)
            background = storage.absolute(page.clean_path or page.original_path)
            # No text means there is nothing to fit, rasterize or save twice.
            if kind not in {"psd", "text_layer"} and not any(row.visible and row.translated_text.strip() for row in page.regions):
                with Image.open(background) as header:
                    is_png = header.format == "PNG"
                if is_png:
                    files.append(background)
                else:
                    image, metadata = load_rgb_with_metadata(background)
                    try:
                        save_png_with_metadata(image, root / "translated.png", metadata, compress_level=3)
                    finally:
                        image.close()
                    files.append(root / "translated.png")
                progress(.8 * (index + 1) / total, "rendering", index + 1, total)
                continue
            if renderer is None:
                renderer = PillowRenderer()
                if snapshot.get("_font_paths"):
                    renderer.layout_engine.fonts = FrozenFontResolver(snapshot["_font_paths"])
            result = renderer.render(background, page.regions, root / "translated.png", root / "text.png",
                region_tiles=root / "tiles" if kind == "psd" else None, check=check,
                progress=lambda value, index=index: progress(.8 * (index + value * (.65 if kind == "psd" else 1)) / total,
                                                            "rendering", index, total))
            contract = snapshot.get("workflow_contract")
            if contract:
                reviewed = next(item for item in contract["report"]["pages"] if item["image_id"] == page.id)
                if reviewed["review"]["outcome"] == "passed":
                    from PIL import ImageChops
                    with Image.open(storage.absolute(page.rendered_path)) as expected, Image.open(root / "translated.png") as actual:
                        if expected.size != actual.size or ImageChops.difference(expected.convert("RGB"), actual.convert("RGB")).getbbox():
                            raise ValueError(f"Export rendering differs from reviewed pixels for page {page.id}; review again before export")
            page.rendered_path, page.text_layer_path = storage.relative(root / "translated.png"), storage.relative(root / "text.png")
            if kind == "psd":
                from app.services.psd_export import export_psd
                output = root / f"{index + 1:04d}.psd"
                fonts.update(export_psd(page, result, root, output, check,
                    progress=lambda value, index=index: progress(.8 * (index + .65 + .35 * value) / total,
                                                                "rendering", index, total)))
                files.append(output)
            else:
                files.append(root / ("text.png" if kind == "text_layer" else "translated.png"))
        elif kind == "clean":
            files.append(storage.absolute(page.clean_path or page.original_path))
        if render_end:
            progress(render_end * (index + 1) / total, "rendering", index + 1, total)
    check()
    progress(render_end, "packaging", 0, total)
    if kind in {"project", "json", "masks"}:
        # An estimate includes every archived project asset. Repeated references
        # may make it conservative; progress never claims completion before close.
        asset_sizes = []
        def count(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key not in {"source_text", "translated_text", "text", "prompt", "raw_response"}:
                        count(item)
            elif isinstance(value, list):
                for item in value:
                    count(item)
            elif isinstance(value, str) and value.startswith("file-jobs/"):
                path = storage.absolute(value)
                if path.is_file():
                    asset_sizes.append(path.stat().st_size)
        count(snapshot)
        total_bytes, written = max(1, sum(asset_sizes)), 0
        def packed(size):
            nonlocal written
            written += size
            progress(render_end + (.99 - render_end) * min(1, written / total_bytes), "packaging")
        generated = export_project(project, [kind], storage, check=check, on_bytes=packed)
        output = directory / (kind + ".zip")
        shutil.move(generated, output)
    elif kind == "psd" and len(files) == 1:
        output = directory / "pages.psd"
        shutil.move(files[0], output)
    else:
        output = directory / ("pages.cbz" if kind == "cbz" else "pages.zip")
        total_bytes, written, completed_pages = max(1, sum(path.stat().st_size for path in files)), 0, 0
        def packed(size):
            nonlocal written
            written += size
            progress(render_end + (.99 - render_end) * min(1, written / total_bytes), "packaging", completed_pages, total)
        with ExportArchive(output, check=check, on_bytes=packed) as archive:
            for index, _page in enumerate(project.pages):
                check()
                if kind == "psd":
                    archive.write(files[index], f"{index + 1:04d}.psd")
                elif kind == "clean":
                    archive.write(files[index], f"{index + 1:04d}{files[index].suffix}")
                else:
                    archive.write(files[index], f"{index + 1:04d}.png")
                completed_pages = index + 1
                progress(render_end + (.99 - render_end) * min(1, written / total_bytes), "packaging", completed_pages, total)
            if fonts:
                archive.writestr("fonts.txt", "\n".join(sorted(fonts)))
    progress(.99, "packaging", total, total)
    result = {"filename": f"{project.name}{output.suffix}", "page_count": len(project.pages)}
    if kind == "psd":
        result.update(fonts=sorted(fonts), psd_photoshop_verified=False)
    return output, result
