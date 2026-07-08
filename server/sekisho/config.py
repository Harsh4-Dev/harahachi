"""Configuration loaded from environment variables (see API.md §Server internals)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    port: int = field(default_factory=lambda: int(os.environ.get("SEKISHO_PORT", "8420")))
    host: str = field(default_factory=lambda: os.environ.get("SEKISHO_HOST", "0.0.0.0"))
    vllm_url: str | None = field(default_factory=lambda: os.environ.get("SEKISHO_VLLM_URL") or None)
    vllm_model: str | None = field(default_factory=lambda: os.environ.get("SEKISHO_VLLM_MODEL") or None)
    admin_password: str = field(default_factory=lambda: os.environ.get("SEKISHO_ADMIN_PASSWORD", ""))
    allow_mock: bool = field(default_factory=lambda: _env_bool("SEKISHO_ALLOW_MOCK", True))
    # SEKISHO_DB_PATH is the documented name (Docker mounts it on /data); SEKISHO_DB kept as a fallback.
    db_path: str = field(
        default_factory=lambda: os.environ.get("SEKISHO_DB_PATH")
        or os.environ.get("SEKISHO_DB", "sekisho.db")
    )
    vllm_timeout: float = field(default_factory=lambda: float(os.environ.get("SEKISHO_VLLM_TIMEOUT", "45")))
    # Explicit timeouts so a slow/dead backend can never hang a request — the app
    # gets a clean 503 JSON envelope and shows a popup instead of a stuck screen.
    # connect is short (fail fast to the next backend); the totals differ per op.
    connect_timeout: float = field(default_factory=lambda: float(os.environ.get("SEKISHO_CONNECT_TIMEOUT", "3")))
    analyze_timeout: float = field(default_factory=lambda: float(os.environ.get("SEKISHO_ANALYZE_TIMEOUT", "60")))
    chat_timeout: float = field(default_factory=lambda: float(os.environ.get("SEKISHO_CHAT_TIMEOUT", "30")))
    # Origin embedded in the pairing QR + returned to the app. Behind nginx this is the
    # https://<lan-ip> front door. Empty => auto-detect at request time (see net.public_url).
    public_url: str | None = field(default_factory=lambda: (os.environ.get("SEKISHO_PUBLIC_URL") or "").rstrip("/") or None)

    # Set at runtime if no admin password was provided via env.
    admin_password_generated: bool = False

    def ensure_admin_password(self) -> None:
        if not self.admin_password:
            self.admin_password = secrets.token_urlsafe(9)
            self.admin_password_generated = True

    @property
    def db_file(self) -> Path:
        return Path(self.db_path).resolve()


config = Config()
