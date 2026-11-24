"""Pluggable credential encryption boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet

from backend.config import AppConfig


class SecretCipher(Protocol):
    """Minimal encrypt/decrypt contract so storage remains platform-agnostic."""

    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...


class FernetCipher:
    """Symmetric Fernet cipher with empty-string passthrough."""

    def __init__(self, key: bytes | str) -> None:
        key_bytes = key.encode("utf-8") if isinstance(key, str) else key
        self._fernet = Fernet(key_bytes)

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


class KeyFileCipher:
    """File-backed Fernet cipher suitable for local storage and mounted volumes."""

    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path
        self._cipher: FernetCipher | None = None

    def _get_cipher(self) -> FernetCipher:
        if self._cipher is not None:
            return self._cipher

        if self.key_path.exists():
            key_bytes = self.key_path.read_bytes().strip()
        else:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key_bytes = Fernet.generate_key()
            self.key_path.write_bytes(key_bytes)
            if os.name != "nt":
                os.chmod(self.key_path, 0o600)

        self._cipher = FernetCipher(key_bytes)
        return self._cipher

    def encrypt(self, plaintext: str) -> str:
        return self._get_cipher().encrypt(plaintext)

    def decrypt(self, ciphertext: str) -> str:
        return self._get_cipher().decrypt(ciphertext)


def create_cipher(config: AppConfig) -> SecretCipher:
    """Select an environment-supplied key first, otherwise a local key file."""
    env_key = os.getenv("INKWELL_SECRET_KEY", "").strip()
    if env_key:
        return FernetCipher(env_key)
    return KeyFileCipher(config.key_path)
