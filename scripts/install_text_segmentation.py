"""Explicitly install/verify the optional, separately licensed CTD ONNX weights."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
import urllib.request
from pathlib import Path

from app.services.inpainting.text_segmentation import MODEL_BYTES, MODEL_FILENAME, MODEL_SHA256, MODEL_URL


def verify(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != MODEL_BYTES:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == MODEL_SHA256


def install(directory: Path, download: bool = False) -> Path:
    destination = directory / MODEL_FILENAME
    if verify(destination):
        return destination
    if not download:
        raise ValueError("Model absent or invalid. Review docs/translation-quality.md, then pass --download.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as stream:
            temporary = Path(stream.name)
            with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
                received = 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > MODEL_BYTES:
                        raise ValueError("Model exceeds the pinned release size")
                    stream.write(chunk)
        if not verify(temporary):
            raise ValueError("Model release checksum/size mismatch; existing file was not replaced")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--download", action="store_true", help="Download after reviewing the model's upstream terms")
    args = parser.parse_args()
    print(f"Verified: {install(args.model_dir, args.download)}")
