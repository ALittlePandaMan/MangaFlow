"""Reviewed, pinned font assets. Clients can choose IDs, never download URLs."""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, Lock

import httpx

GOOGLE_REVISION = "45e3d5269e4a17d823f6d84470bc084dc5ae46f6"
GOOGLE = f"https://raw.githubusercontent.com/google/fonts/{GOOGLE_REVISION}/ofl/"
LXGW = "https://raw.githubusercontent.com/lxgw/LxgwWenKai/7ebd0771fda4285299670f83c398f32f32c7bef8/"
LICENSE_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "font-licenses"


@dataclass(frozen=True)
class FontPreset:
    id: str
    name: str
    family: str
    description: str
    languages: tuple[str, ...]
    category: str
    url: str
    size_bytes: int
    sha256: str
    source_url: str
    license_url: str

    @property
    def filename(self) -> str:
        original = self.url.rsplit("/", 1)[-1]
        family = "".join(character for character in self.family if character.isalnum())
        normalized = "".join(character.lower() for character in Path(original).stem if character.isalnum())
        # The renderer resolves by family in the filename. Some upstream files
        # have an English filename but a Chinese typographic-family name.
        return original if family.lower() in normalized else f"{family}-{original}"


PRESETS = (
    FontPreset("lxgw-wenkai", "霞鹜文楷", "LXGW WenKai", "自然的楷书笔触，适合对白、旁白与日常手写。", ("zh-Hans", "zh-Hant", "ja", "en"), "handwriting",
        LXGW + "fonts/TTF/LXGWWenKai-Regular.ttf", 25928940, "2d73eea94ad808999d90c0000c239a84a33b09d4bc6ab6c5b8bbf1eda8a7fc8b",
        "https://github.com/lxgw/LxgwWenKai", LXGW + "OFL.txt"),
    FontPreset("lxgw-wenkai-tc", "霞鹜文楷 TC", "LXGW WenKai TC", "面向繁体中文的文楷字形，适合对白与旁白。", ("zh-Hant", "zh-Hans", "ja", "en"), "handwriting",
        GOOGLE + "lxgwwenkaitc/LXGWWenKaiTC-Regular.ttf", 13110528, "4fcc5aec11cbbf737b0cfab7b63796f7f280087a7a656b2f13342cd5e5318d95",
        "https://github.com/lxgw/LxgwWenkaiTC", GOOGLE + "lxgwwenkaitc/OFL.txt"),
    FontPreset("zcool-kuaile", "站酷快乐体", "ZCOOL KuaiLe", "活泼的手写风格，适合轻松对白与趣味标题。", ("zh-Hans", "en"), "display",
        GOOGLE + "zcoolkuaile/ZCOOLKuaiLe-Regular.ttf", 1514968, "812a6fc1fe54b6d73a419245c32dfeba8aa33104d5be90d1cf6af082007cb71d",
        "https://github.com/googlefonts/zcool-kuaile", GOOGLE + "zcoolkuaile/OFL.txt"),
    FontPreset("zcool-xiaowei", "站酷小薇体", "站酷小薇体", "带有文艺感的宋体风格，适合叙述、独白与章节标题。", ("zh-Hans", "en"), "serif",
        GOOGLE + "zcoolxiaowei/ZCOOLXiaoWei-Regular.ttf", 6313808, "a42b620140f493db42f741351dfbf343c0936d58588ee8004b8b2a218d997ff1",
        "https://github.com/googlefonts/zcool-xiaowei", GOOGLE + "zcoolxiaowei/OFL.txt"),
    FontPreset("ma-shan-zheng", "马善政毛笔楷体", "Ma Shan Zheng", "有力度的毛笔笔触，适合强调、拟声词与古风标题。", ("zh-Hans", "en"), "display",
        GOOGLE + "mashanzheng/MaShanZheng-Regular.ttf", 5857936, "6d2546bb189c732a8ca29af9e22457b152387d158aa459e4ac2ce1e51788b7fb",
        "https://github.com/googlefonts/mashanzheng", GOOGLE + "mashanzheng/OFL.txt"),
    FontPreset("zen-maru-gothic", "Zen Maru Gothic", "Zen Maru Gothic", "柔和清晰的日文圆体，适合日常对白。", ("ja", "en"), "sans-serif",
        GOOGLE + "zenmarugothic/ZenMaruGothic-Regular.ttf", 3832756, "a0c0b53543e0993ae2225e629c833f3d51495ad31720694ff112ce4ce11111ef",
        "https://github.com/googlefonts/zen-marugothic", GOOGLE + "zenmarugothic/OFL.txt"),
    FontPreset("yomogi", "Yomogi", "Yomogi", "轻盈的日文手写体，适合便条、内心独白与轻声对白。", ("ja", "en"), "handwriting",
        GOOGLE + "yomogi/Yomogi-Regular.ttf", 4045904, "ec280f473a03187292905618f24fdb08a6dcbf8620b52637e3b589fe745259b9",
        "https://github.com/satsuyako/YomogiFont", GOOGLE + "yomogi/OFL.txt"),
    FontPreset("comic-neue", "Comic Neue", "Comic Neue", "清晰自然的英文漫画字体，适合气泡对白。", ("en",), "handwriting",
        GOOGLE + "comicneue/ComicNeue-Regular.ttf", 57248, "a0ee5a37c8b27c4db0700137d928598b1e23b0089e1546a8961909176b779360",
        "https://github.com/crozynski/comicneue", GOOGLE + "comicneue/OFL.txt"),
    FontPreset("bangers", "Bangers", "Bangers", "醒目的英文漫画标题体，适合大写拟声词与动作标题。", ("en",), "display",
        GOOGLE + "bangers/Bangers-Regular.ttf", 93148, "4160a7311de9342674cce9160cde9fcbb30f48190397d86ff1b70b455af65824",
        "https://github.com/googlefonts/bangers", GOOGLE + "bangers/OFL.txt"),
)
BY_ID = {preset.id: preset for preset in PRESETS}
BY_FILENAME = {preset.filename: preset for preset in PRESETS}
_locks = {preset.id: Lock() for preset in PRESETS}
_slots = BoundedSemaphore(2)


class FontInstallError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@lru_cache(maxsize=128)
def _verified(path: str, size: int, modified: int, digest: str) -> bool:
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == digest


def installed(preset: FontPreset, directory: Path) -> bool:
    path = directory / preset.filename
    try:
        signature = path.stat()
        return (not path.is_symlink() and path.is_file() and signature.st_size == preset.size_bytes
                and _verified(str(path), signature.st_size, signature.st_mtime_ns, preset.sha256))
    except OSError:
        return False


def catalog(directory: Path) -> list[dict]:
    return [{"id": preset.id, "name": preset.name, "family": preset.family, "description": preset.description,
             "languages": preset.languages, "category": preset.category, "size_bytes": preset.size_bytes,
             "license": "OFL-1.1", "source_url": preset.source_url, "license_url": preset.license_url,
             "installed_filename": preset.filename if installed(preset, directory) else None} for preset in PRESETS]


def install(preset_id: str, directory: Path) -> Path:
    preset = BY_ID.get(preset_id)
    if preset is None:
        raise FontInstallError("未找到该字体预设。", 404)
    lock = _locks[preset_id]
    if not lock.acquire(blocking=False):
        raise FontInstallError("该字体正在下载，请稍后刷新。", 409)
    acquired = False
    temporary = directory / f".preset-{uuid.uuid4().hex}.part"
    license_temporary = directory / f".license-{uuid.uuid4().hex}.part"
    try:
        destination = directory / preset.filename
        if not installed(preset, directory):
            if destination.exists() or destination.is_symlink():
                raise FontInstallError("同名字体文件已存在，未覆盖。请先移除该文件后重试。", 409)
            acquired = _slots.acquire(blocking=False)
            if not acquired:
                raise FontInstallError("正在下载其他字体，请稍后重试。", 429)
            digest = hashlib.sha256()
            written = 0
            started = time.monotonic()
            with httpx.stream("GET", preset.url, follow_redirects=False,
                              timeout=httpx.Timeout(20, connect=10), headers={"User-Agent": "MangaFlow-FontInstaller/1"}) as response:
                response.raise_for_status()
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes(64 * 1024):
                        written += len(chunk)
                        if written > preset.size_bytes or time.monotonic() - started > 180:
                            raise FontInstallError("字体下载未完成或文件大小异常，请重试。")
                        digest.update(chunk)
                        output.write(chunk)
            if written != preset.size_bytes or digest.hexdigest() != preset.sha256:
                raise FontInstallError("字体文件校验失败，未安装，请重试。")
            from fontTools.ttLib import TTFont, TTLibError

            try:
                with TTFont(temporary) as font:
                    font["name"]
                    font["cmap"]
            except (TTLibError, KeyError):
                raise FontInstallError("字体文件无效，未安装。") from None
        # Keep the upstream copyright and full license beside the unmodified
        # binary. Publish only verified files; interruptions leave no font entry.
        license_bytes = (LICENSE_DIRECTORY / f"{preset.id}.txt").read_bytes()
        licenses = directory / "licenses"
        licenses.mkdir(exist_ok=True)
        license_temporary.write_bytes(license_bytes)
        license_temporary.replace(licenses / f"{preset.id}.txt")
        if temporary.exists():
            # Same-directory hard link is atomic and cannot overwrite an upload
            # or another process's concurrently installed asset.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if not installed(preset, directory):
                    raise FontInstallError("同名字体文件已存在，未覆盖。", 409) from None
        return destination
    except FontInstallError:
        raise
    except httpx.HTTPError:
        raise FontInstallError("字体下载失败，请检查网络连接后重试。") from None
    except OSError:
        raise FontInstallError("无法保存字体，请检查字体目录的可用空间和写入权限。", 500) from None
    finally:
        temporary.unlink(missing_ok=True)
        license_temporary.unlink(missing_ok=True)
        if acquired:
            _slots.release()
        lock.release()
