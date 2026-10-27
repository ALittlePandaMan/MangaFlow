"""Bounded comic extraction; PDFium lives exclusively in a child process."""
from __future__ import annotations

import json
import re
import shutil
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def natural_key(name):
    return tuple((1, int(part)) if part.isdecimal() else (0, part.casefold())
                 for part in re.split(r"(\d+)", name))


def safe_member(name):
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or ":" in normalized or ".." in path.parts or "\x00" in normalized:
        raise ValueError("压缩包包含不安全的文件路径")
    return path


def validate_image(path, max_pixels):
    with Image.open(path) as image:
        if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > max_pixels:
            raise ValueError(f"图片格式或像素超出限制：{path.name}")
        image.verify()
    with Image.open(path) as image:
        image.load()  # CRC/header validation alone does not detect truncated pixel data.


def extract_comic(source, directory, settings, check=lambda: None, progress=lambda value: None):
    rar = source.suffix.lower() in {".rar", ".cbr"}
    if rar:
        import rarfile
        if not shutil.which("unrar"):
            raise ValueError("RAR 导入需要安装 unrar")
        rarfile.UNRAR_TOOL = "unrar"
        archive = rarfile.RarFile(source)
    else:
        archive = zipfile.ZipFile(source)
    directory.mkdir(parents=True, exist_ok=True)
    maximum = settings.max_import_uncompressed_mb * 1024**2
    total = 0
    with archive:
        if rar and len(archive.volumelist()) > 1:
            raise ValueError("暂不支持分卷压缩包")
        if not rar and "project.json" in archive.namelist():
            raise ValueError("这是 MangaFlow 源项目包，请使用“导入源项目”")
        entries = []
        names = set()
        declared = 0
        all_entries = archive.infolist()
        if len(all_entries) > settings.max_import_pages * 10:
            raise ValueError("压缩包文件数量超过限制")
        for entry in all_entries:
            check()
            path = safe_member(entry.filename)
            mode = (getattr(entry, "external_attr", 0) >> 16) & 0xFFFF
            if stat.S_ISLNK(mode) or (rar and (entry.is_symlink() or getattr(entry, "file_redir", None))):
                raise ValueError("压缩包不允许符号链接")
            if (entry.needs_password() if rar else entry.flag_bits & 1):
                raise ValueError("暂不支持加密压缩包")
            if (entry.isdir() if rar else entry.is_dir()):
                continue
            declared += entry.file_size
            if declared > maximum:
                raise ValueError("压缩包解压总量超过限制")
            if "__MACOSX" in path.parts or path.name.startswith(".") or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            folded = path.as_posix().casefold()
            if folded in names:
                raise ValueError("压缩包包含重复的图片路径")
            names.add(folded)
            if len(path.name) > 255:
                raise ValueError("图片文件名过长")
            entries.append(entry)
            if len(entries) > settings.max_import_pages:
                raise ValueError("导入页数超过限制")
        if not entries:
            raise ValueError("压缩包中没有支持的图片")
        entries.sort(key=lambda item: (natural_key(item.filename.replace("\\", "/")), item.filename))
        result = []
        for index, entry in enumerate(entries):
            target = directory / f"{index:06d}{PurePosixPath(entry.filename).suffix.lower()}"
            with archive.open(entry) as reader, target.open("xb") as writer:
                while block := reader.read(1024**2):
                    check()
                    total += len(block)
                    if total > maximum:
                        raise ValueError("压缩包实际解压总量超过限制")
                    writer.write(block)
            validate_image(target, settings.max_import_page_pixels)
            result.append((target, safe_member(entry.filename).name))
            progress((index + 1) / len(entries))
        return result


def render_pdf(source, directory, max_pages, max_pixels, max_bytes):
    import pypdfium2 as pdfium
    directory.mkdir(parents=True, exist_ok=True)
    total = 0
    result = []
    with pdfium.PdfDocument(source) as document:
        if not 0 < len(document) <= max_pages:
            raise ValueError("PDF 页数超过限制或没有页面")
        for index in range(len(document)):
            page = document[index]
            try:
                w, h = page.get_size()
                scale = 300 / 72
                if w * scale * h * scale > max_pixels:
                    raise ValueError("PDF 页面像素超过限制")
                bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 255))
                try:
                    target = directory / f"{index:06d}.png"
                    image = bitmap.to_pil()
                    try:
                        image.convert("RGB").save(target, compress_level=3)
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
            total += target.stat().st_size
            if total > max_bytes:
                raise ValueError("PDF 渲染总量超过限制")
            result.append([target.name, f"{index + 1:04d}.png"])
            print(json.dumps({"progress": (index + 1) / len(document)}), flush=True)
    (directory / "pages.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    try:
        render_pdf(Path(sys.argv[1]), Path(sys.argv[2]), *map(int, sys.argv[3:6]))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
        sys.exit(1)
