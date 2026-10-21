"""Private, checksummed request results within one frozen stage lineage.

Validated outputs and allowlisted received translation responses are stored,
never request bodies, URLs or credentials.
This is not server idempotency: an unanswered request still requires the
existing explicit unknown-outcome acknowledgement before it may be retried.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from app.services.base import ProviderError
from app.services.workflow_state import fingerprint

MAX_RESULT_BYTES = 4 * 1024**2
logger = logging.getLogger(__name__)
current_cloud_results: ContextVar[CloudResultCache | None] = ContextVar("cloud_request_results", default=None)


class CloudResultCorrupt(ProviderError):
    def __init__(self):
        super().__init__("已保存的云端结果校验失败；请确认重试，可能需要重新请求并产生费用。")


class CloudResultCache:
    def __init__(self, root: Path, directory: Path, *, allow_invalid: bool = False):
        root = root.resolve()
        if not directory.resolve().is_relative_to(root):
            raise ValueError("Cloud results must stay inside private workflow storage")
        for path in (directory, *directory.parents):
            if path.is_symlink():
                raise ValueError("Cloud results cannot use symlinks")
            if path == root:
                break
        self.directory, self.allow_invalid = directory, allow_invalid
        self.lock = threading.RLock()

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Invalid cloud-result fingerprint")
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict | None:
        with self.lock:
            path = self._path(key)
            if not path.exists():
                return None
            try:
                if path.is_symlink() or path.stat().st_size > MAX_RESULT_BYTES:
                    raise CloudResultCorrupt()
                value = json.loads(path.read_text(encoding="utf-8"))
                if (value.get("version") != 1 or value.get("key") != key or not isinstance(value.get("result"), dict)
                        or fingerprint(value["result"]) != value.get("sha256")):
                    raise CloudResultCorrupt()
                return deepcopy(value["result"])
            except (ValueError, TypeError, AttributeError, CloudResultCorrupt):
                if not self.allow_invalid or path.is_symlink():
                    raise CloudResultCorrupt() from None
                # Preserve the damaged result for inspection. Only a previous
                # acknowledged cloud outcome permits paying for a replacement.
                path.replace(path.with_suffix(f".{uuid4().hex}.corrupt"))
                return None

    def put(self, key: str, result: dict) -> None:
        with self.lock:
            previous = self.get(key)
            if previous is not None and any(result.get(key) != value for key, value in previous.items()):
                raise ValueError("A saved cloud result must not be rewritten")
            body = json.dumps({"version":1,"key":key,"result":result,"sha256":fingerprint(result)},
                              ensure_ascii=False,sort_keys=True,allow_nan=False).encode("utf-8")
            if len(body) > MAX_RESULT_BYTES:
                raise ValueError("Cloud result exceeds the durable cache limit")
            self.directory.mkdir(parents=True,exist_ok=True)
            target = self._path(key)
            temporary = self.directory / f".{uuid4().hex}.tmp"
            try:
                with os.fdopen(os.open(temporary,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600),"wb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(target)
                if os.name != "nt":
                    descriptor = os.open(self.directory,os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            finally:
                temporary.unlink(missing_ok=True)

    def discard_saved(self) -> None:
        """After the WHOLE stage is durable, its request duplicates can expire.

        Never remove unknown/corrupt forensic files or an arbitrary JSON path.
        Failure to collect must not fail a successfully checkpointed stage.
        """
        with self.lock:
            try:
                if not self.directory.is_dir() or self.directory.is_symlink():
                    return
                for index,path in enumerate(self.directory.iterdir()):
                    if index>=1024:
                        break
                    if re.fullmatch(r"[a-f0-9]{64}\.json",path.name) and not path.is_symlink() and path.is_file():
                        path.unlink()
            except OSError:
                logger.debug("Deferred completed cloud request cache cleanup",exc_info=True)

    def archive_received(self, key: str) -> None:
        """Preserve a paid response/result before an explicitly authorized retry."""
        if not self.allow_invalid:
            raise ValueError("Replacing a received paid response requires acknowledgement")
        with self.lock:
            path = self._path(key)
            if path.is_symlink():
                raise CloudResultCorrupt()
            if path.is_file():
                path.replace(path.with_suffix(f".{uuid4().hex}.received"))
