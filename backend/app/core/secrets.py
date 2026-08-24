from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretStore:
    """Small local secret envelope; production deployments should inject a KMS key."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, value: str | None) -> str | None:
        return self._fernet.encrypt(value.encode()).decode() if value else None

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Stored API key cannot be decrypted with the configured secret key") from exc


@lru_cache
def get_secret_store() -> SecretStore:
    configured = os.environ.get("MANGAFLOW_SECRET_KEY")
    if configured:
        return SecretStore(configured.encode())
    key_path: Path = get_settings().data_dir / ".secret_key"
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        key_path.chmod(0o600)
    return SecretStore(key)
