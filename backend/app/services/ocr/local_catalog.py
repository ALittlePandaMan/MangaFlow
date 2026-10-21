"""Koharu-aligned, revision-pinned crop OCR assets. No arbitrary model code."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

LOCAL_OCR_DEFAULT_MAX_TOKENS = 2048
LOCAL_OCR_MAX_TOKENS = 2048


@dataclass(frozen=True)
class LocalOCRModel:
    repository: str
    revision: str
    files: tuple[str, ...]
    code_hashes: tuple[tuple[str, str], ...] = ()
    auxiliary: tuple[str, str, tuple[str, ...]] | None = None


# Same checkpoints as koharu-rs/koharu @ 4df6a4f785e8c2056e25e112eb7b89ccf2f040cc.
LOCAL_OCR_MODELS = {
    "paddleocr-vl-1.6": LocalOCRModel(
        "PaddlePaddle/PaddleOCR-VL-1.6", "66317acc4c9fc17bd154591ce650735cd2855f3e",
        ("config.json", "generation_config.json", "model.safetensors", "preprocessor_config.json",
         "processor_config.json", "chat_template.jinja", "tokenizer.json", "tokenizer_config.json",
         "tokenizer.model", "special_tokens_map.json", "added_tokens.json")),
    "baberu-ocr": LocalOCRModel(
        "genshiai-daichi/baberu-ocr", "d9cc13153e9a1cd8fdfa3b7b1cc329da2020aeae",
        ("config.json", "generation_config.json", "model.safetensors", "tokenizer/vocab.json",
         "tokenizer/tokenizer_config.json", "configuration_baberu.py", "modeling_baberu.py", "tokenization_baberu.py"),
        (("configuration_baberu.py", "1746d4c5f20851a7f1db1ab996c7e4d6c0e22a30068e932951b114d47dce0a36"),
         ("modeling_baberu.py", "5d3035a42da6bea48870904700a5a40b6aefe17b01a4c158a97903b74035f88a"),
         ("tokenization_baberu.py", "e3045630779ce61d71877cefb6034172e2defd5e0be5611c45dedbaebcdf035c")),
        ("facebook/dinov2-base", "f9e44c814b77203eaa57a6bdbbd535f21ede1415", ("config.json", "preprocessor_config.json"))),
    "hayai-ocr": LocalOCRModel(
        "JustANormalTinkerer/hayai-ocr-v2", "4a4ce477c9a8841f208b94e1d9ed5c0938965e05",
        ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
         "configuration_hayai.py", "modeling_hayai.py"),
        (("configuration_hayai.py", "47abd38cf1bae7aef27d01f5b8b4aa0960a7bc625a8afad79c4762ff5e5ed970"),
         ("modeling_hayai.py", "3d78976206549964abd55f776ab059e002adc72d2167daf168e46a12a5f4ae62")),
        ("google/siglip2-base-patch16-naflex", "b53b807d3a2d5e2b3911292f2d69e5341cdc064c", ("config.json", "preprocessor_config.json"))),
}


def local_ocr_python() -> Path:
    configured = os.environ.get("MANGAFLOW_OCR_PYTHON")
    if configured:
        return Path(configured)
    bundled = Path("/opt/local-ocr/bin/python")
    if bundled.is_file():
        return bundled
    root = get_settings().model_dir / "ocr-runtime"
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def verify_code(directory: Path, model: LocalOCRModel) -> None:
    for name, expected in model.code_hashes:
        with (directory / name).open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                raise ValueError(f"OCR source checksum mismatch: {name}")


def resolve_assets(provider: str, *, download: bool = False) -> tuple[Path, Path | None]:
    from huggingface_hub import snapshot_download

    spec = LOCAL_OCR_MODELS[provider]
    cache = get_settings().model_dir / "huggingface" / "hub"

    def resolve(repository, revision, files):
        arguments = dict(repo_id=repository, revision=revision, allow_patterns=list(files), cache_dir=cache, max_workers=2)
        try:
            directory = Path(snapshot_download(**arguments, local_files_only=True))
            if any(not (directory / filename).is_file() for filename in files):
                raise FileNotFoundError("incomplete local snapshot")
        except (OSError, ValueError):
            if not download:
                raise FileNotFoundError(f"OCR assets missing: {repository}; install the selected OCR model first") from None
            directory = Path(snapshot_download(**arguments, local_files_only=False))
        if any(not (directory / filename).is_file() for filename in files):
            raise FileNotFoundError(f"OCR assets incomplete: {repository}; install the selected OCR model first")
        return directory

    directory = resolve(spec.repository, spec.revision, spec.files)
    verify_code(directory, spec)
    auxiliary = resolve(*spec.auxiliary) if spec.auxiliary else None
    return directory, auxiliary


def validate_local_config(provider: str, config: dict) -> None:
    spec = LOCAL_OCR_MODELS[provider]
    if config.get("model", spec.repository) != spec.repository or config.get("revision", spec.revision) != spec.revision:
        raise ValueError("本地 OCR 使用固定模型及版本，请通过 OCR 类型切换模型。")
    for name, default, minimum, maximum in (("max_new_tokens", LOCAL_OCR_DEFAULT_MAX_TOKENS, 16, LOCAL_OCR_MAX_TOKENS), ("timeout", 180, 10, 600)):
        value = config.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or int(value) != value:
            raise ValueError(f"{name} 必须是 {minimum}–{maximum} 的整数")
    if config.get("device", "auto") not in {"auto", "cpu", "cuda", "cuda:0"}:
        raise ValueError("本地 OCR 设备只支持 auto、cpu 或 cuda:0")
