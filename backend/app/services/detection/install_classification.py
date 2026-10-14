"""Explicit, pinned model installation; never called from a web request."""

from __future__ import annotations

import hashlib
import os
from uuid import uuid4

from app.core.config import get_settings
from app.services.detection.classification import MODEL_RELATIVE_PATH, MODEL_REPOSITORY, MODEL_REVISION, MODEL_SHA256
from huggingface_hub import hf_hub_download


def main() -> None:
    destination = get_settings().model_dir / MODEL_RELATIVE_PATH
    if destination.is_file():
        with destination.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() == MODEL_SHA256:
                print(f"Classification weights verified: {destination}")
                return
    source = hf_hub_download(MODEL_REPOSITORY, "model.safetensors", revision=MODEL_REVISION)
    with open(source, "rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != MODEL_SHA256:
            raise ValueError("Downloaded classification model checksum mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}-{uuid4().hex}.tmp")
    try:
        import shutil

        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Classification weights installed: {destination}")


if __name__ == "__main__":
    main()
