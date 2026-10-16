"""Configuration loaded from environment variables (see API.md §Server internals + v4).

One codebase, two deployments, switched by env:

* **Docker / dev** (``VERCEL`` unset): SQLite file, in-process push scheduler,
  backend registry (vLLM / LM Studio / NIM), mock analyzer allowed, admin
  password auto-generated + printed when unset.
* **Vercel** (``VERCEL=1``, set automatically by the platform): Postgres via
  ``DATABASE_URL``, no background loop (``/api/cron/tick``), NVIDIA NIM only,
  mock off unless ``SEKISHO_ALLOW_MOCK=1``, and every secret comes from env and
  fails CLOSED when missing (admin login refused, cron 401, push disabled).

Secrets are ``repr=False`` so they never appear in a logged/printed Config.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def _on_vercel() -> bool:
    return _env_bool("VERCEL", False)


# Verified limits (see DEPLOY_VERCEL.md "Verified platform limits"):
#   * Vercel function request body: 4.5 MB (413 FUNCTION_PAYLOAD_TOO_LARGE above it)
#   * NIM hosted API inline base64 image: < 180_000 chars (larger needs the NVCF assets API)
VERCEL_BODY_LIMIT = 4_500_000
DOCKER_BODY_LIMIT = 16 * 1024 * 1024  # matches nginx client_max_body_size 16m
NIM_INLINE_B64_LIMIT = 180_000
NIM_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
CRON_SECRET_MIN_LEN = 32
OPS_TOKEN_MIN_LEN = 48
SECRETS_KEY_MIN_LEN = 32
BUSBOARD_TOKEN_MIN_LEN = 48
BUSBOARD_MAX_TIMEOUT = 8.0
# Admin-login proof-of-work: the browser tries on average maxnumber/2 SHA-256
# hashes. 150k ~= 1-3 s on a laptop with crypto.subtle (measured in Chrome).
CAPTCHA_DEFAULT_MAXNUMBER = 150_000


@dataclass
class Config:
    port: int = field(default_factory=lambda: int(os.environ.get("SEKISHO_PORT", "8420")))
    host: str = field(default_factory=lambda: os.environ.get("SEKISHO_HOST", "0.0.0.0"))
    vllm_url: str | None = field(default_factory=lambda: os.environ.get("SEKISHO_VLLM_URL") or None)
    vllm_model: str | None = field(default_factory=lambda: os.environ.get("SEKISHO_VLLM_MODEL") or None)
    admin_password: str = field(default_factory=lambda: os.environ.get("SEKISHO_ADMIN_PASSWORD", ""),
                                repr=False)

    # ---- deployment mode ----
    on_vercel: bool = field(default_factory=_on_vercel)
    # Mock responses: allowed by default in Docker/dev, OFF by default on Vercel
    # (the app gets a clean 503 + retry popup instead of fake numbers).
    allow_mock: bool = field(default_factory=lambda: _env_bool("SEKISHO_ALLOW_MOCK", not _on_vercel()))

    # ---- storage ----
    # SEKISHO_DB_PATH is the documented name (Docker mounts it on /data); SEKISHO_DB kept as a fallback.
    db_path: str = field(
        default_factory=lambda: os.environ.get("SEKISHO_DB_PATH")
        or os.environ.get("SEKISHO_DB", "sekisho.db")
    )
    # Postgres (Neon via the Vercel Marketplace sets DATABASE_URL; POSTGRES_URL as a fallback).
    # Unset => SQLite at db_path.
    database_url: str | None = field(
        default_factory=lambda: (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip() or None,
        repr=False,
    )

    # ---- secrets (env only on Vercel; fail closed) ----
    session_secret: str = field(default_factory=lambda: os.environ.get("SEKISHO_SESSION_SECRET", ""), repr=False)
    cron_secret: str = field(default_factory=lambda: os.environ.get("CRON_SECRET", ""), repr=False)
    nvidia_api_key: str = field(default_factory=lambda: os.environ.get("NVIDIA_API_KEY", "").strip(), repr=False)
    # Server-to-server ops API (/api/ops/v1, consumed by the private Bansho
    # console). Unset or shorter than OPS_TOKEN_MIN_LEN => the whole surface 404s.
    ops_token: str = field(default_factory=lambda: os.environ.get("SEKISHO_OPS_TOKEN", "").strip(), repr=False)
    # Fernet master key for secrets the admin stores in the DB (the NVIDIA key set
    # from the Backends tab). >= 32 chars; unset => storing secrets is disabled.
    secrets_key: str = field(default_factory=lambda: os.environ.get("SEKISHO_SECRETS_KEY", "").strip(), repr=False)

    # ---- Bus Board (second phone app) ops API, called server-to-server ----
    busboard_url: str = field(default_factory=lambda: (os.environ.get("BUSBOARD_URL") or "").strip().rstrip("/"))
    busboard_token: str = field(default_factory=lambda: os.environ.get("BUSBOARD_OPS_TOKEN", "").strip(),
                                repr=False)
    busboard_timeout: float = field(
        default_factory=lambda: max(0.2, min(BUSBOARD_MAX_TIMEOUT, _env_float("BUSBOARD_TIMEOUT", BUSBOARD_MAX_TIMEOUT)))
    )

    # ---- admin login captcha (self-hosted proof-of-work, ALTCHA-style) ----
    captcha_maxnumber: int = field(
        default_factory=lambda: max(100, min(5_000_000, _env_int("SEKISHO_CAPTCHA_MAXNUMBER",
                                                                 CAPTCHA_DEFAULT_MAXNUMBER)))
    )

    # IANA timezone that defines "a day" for analytics (DAU, timeseries,
    # retention, the ops summary's "today").
    tz_name: str = field(default_factory=lambda: (os.environ.get("SEKISHO_TZ") or "Asia/Kolkata").strip())

    # ---- LLM provider ----
    # "nim" (NVIDIA NIM per-task model slots, key from NVIDIA_API_KEY) or
    # "registry" (v2 backend registry with failover). Empty => auto: nim on Vercel
    # or whenever NVIDIA_API_KEY is set, else registry (Docker default, unchanged).
    provider: str = field(default_factory=lambda: os.environ.get("SEKISHO_PROVIDER", "").strip().lower())
    nim_base_url: str = field(
        default_factory=lambda: (os.environ.get("NIM_BASE_URL") or NIM_DEFAULT_BASE_URL).rstrip("/")
    )
    nim_max_image_b64: int = field(default_factory=lambda: _env_int("NIM_MAX_IMAGE_B64", NIM_INLINE_B64_LIMIT))
    # SSRF guard: outbound LLM base URLs must be https + one of these hosts when
    # enforcement is on (always on Vercel; opt-in for Docker where http vLLM on
    # host.docker.internal is the norm).
    backend_allowlist: list[str] = field(
        default_factory=lambda: sorted({"integrate.api.nvidia.com",
                                        *[h.lower() for h in _env_list("SEKISHO_BACKEND_ALLOWLIST")]})
    )
    enforce_backend_allowlist: bool = field(
        default_factory=lambda: _env_bool("SEKISHO_ENFORCE_BACKEND_ALLOWLIST", _on_vercel())
    )

    # ---- timeouts ----
    vllm_timeout: float = field(default_factory=lambda: _env_float("SEKISHO_VLLM_TIMEOUT", 45))
    # Explicit timeouts so a slow/dead backend can never hang a request — the app
    # gets a clean 503 JSON envelope and shows a popup instead of a stuck screen.
    # connect is short (fail fast to the next backend); the totals differ per op.
    connect_timeout: float = field(default_factory=lambda: _env_float("SEKISHO_CONNECT_TIMEOUT", 3))
    analyze_timeout: float = field(default_factory=lambda: _env_float("SEKISHO_ANALYZE_TIMEOUT", 45))
    chat_timeout: float = field(default_factory=lambda: _env_float("SEKISHO_CHAT_TIMEOUT", 30))
    # Total wall-clock budget for all LLM attempts (primary + fallback/failover) in
    # ONE request. Kept under the app's 65 s client timeout (TIMEOUT_LONG) and far
    # under the Vercel Hobby maxDuration (300 s), so the app always receives our
    # 503 envelope instead of a client abort or a platform 504.
    llm_budget: float = field(default_factory=lambda: _env_float("SEKISHO_LLM_BUDGET", 55))

    # ---- request limits / abuse caps ----
    max_body_bytes: int = field(
        default_factory=lambda: _env_int("SEKISHO_MAX_BODY_BYTES",
                                         VERCEL_BODY_LIMIT if _on_vercel() else DOCKER_BODY_LIMIT)
    )
    rate_limit_per_min: int = field(default_factory=lambda: _env_int("SEKISHO_RATE_LIMIT", 30))
    # Daily caps on calls that spend LLM credits (analyze, body-photo, chat,
    # insights, suggestions, /llm/v1). All three are checked on every such call:
    # per user, per paired device (stops account cycling on one phone) and a
    # global ceiling for the whole deployment (bounds the bill). 0 = unlimited.
    daily_llm_cap: int = field(default_factory=lambda: _env_int("SEKISHO_DAILY_LLM_CAP", 200))
    device_daily_llm_cap: int = field(default_factory=lambda: _env_int("SEKISHO_DEVICE_DAILY_LLM_CAP", 200))
    global_daily_llm_cap: int = field(default_factory=lambda: _env_int("SEKISHO_GLOBAL_DAILY_LLM_CAP", 2000))

    # ---- CORS ----
    # Extra allowed origins (dev servers etc.). The deployment origin(s),
    # capacitor://localhost and https://localhost are always allowed.
    cors_origins: list[str] = field(default_factory=lambda: _env_list("SEKISHO_CORS_ORIGINS"))

    # Origin embedded in the pairing QR + returned to the app. Behind nginx this is the
    # https://<lan-ip> front door. Empty => auto-detect at request time (see net.public_url).
    public_url: str | None = field(default_factory=lambda: (os.environ.get("SEKISHO_PUBLIC_URL") or "").rstrip("/") or None)

    # Set at runtime if no admin password was provided via env (Docker/dev only).
    admin_password_generated: bool = False

    def ensure_admin_password(self) -> None:
        """Docker/dev convenience: generate + print a password when unset.

        NEVER on Vercel — there the admin login fails closed instead (see
        ``admin_login_disabled_reason``), because a generated password would be
        different on every instance and printed into shared platform logs.
        """
        if self.on_vercel:
            return
        if not self.admin_password:
            self.admin_password = secrets.token_urlsafe(9)
            self.admin_password_generated = True

    def admin_login_disabled_reason(self) -> str | None:
        """Why admin login is refused (fail closed), or None if it is allowed."""
        if not self.admin_password:
            return ("Admin login is disabled: SEKISHO_ADMIN_PASSWORD is not set. "
                    "Set it in the Vercel project's Environment Variables and redeploy.")
        if self.on_vercel:
            if len(self.admin_password) < 12:
                return ("Admin login is disabled: SEKISHO_ADMIN_PASSWORD must be at least "
                        "12 characters on a public deployment.")
            if not self.session_secret or len(self.session_secret) < 32:
                return ("Admin login is disabled: SEKISHO_SESSION_SECRET is not set "
                        "(or shorter than 32 characters).")
        return None

    def cron_secret_problem(self) -> str | None:
        """Why the cron endpoint must refuse to run (fail closed), or None.

        On Vercel the secret guards a public URL that anyone can hit, so it must
        be long and random (the guide's ``openssl rand -hex 32`` gives 64 chars).
        """
        if not self.cron_secret:
            return "CRON_SECRET is not set."
        if self.on_vercel and len(self.cron_secret) < CRON_SECRET_MIN_LEN:
            return (f"CRON_SECRET must be at least {CRON_SECRET_MIN_LEN} characters on Vercel "
                    "(generate one with: openssl rand -hex 32). Cron ticks are refused until it is.")
        return None

    def ops_enabled(self) -> bool:
        """The ops API exists only with a long (>= 48 chars) SEKISHO_OPS_TOKEN."""
        return len(self.ops_token or "") >= OPS_TOKEN_MIN_LEN

    def secrets_storage_problem(self) -> str | None:
        """Why admin-managed secrets cannot be stored (encrypted), or None."""
        if not self.secrets_key:
            return ("SEKISHO_SECRETS_KEY is not set, so keys cannot be stored encrypted. "
                    "Set it (32+ random characters) in the environment and redeploy.")
        if len(self.secrets_key) < SECRETS_KEY_MIN_LEN:
            return (f"SEKISHO_SECRETS_KEY must be at least {SECRETS_KEY_MIN_LEN} characters; "
                    "storing keys is disabled until it is.")
        return None

    def busboard_problem(self) -> str | None:
        """Why the Bus Board section cannot connect (shown in the admin), or None."""
        if not self.busboard_url:
            return "BUSBOARD_URL is not set."
        if not self.busboard_token:
            return "BUSBOARD_OPS_TOKEN is not set."
        if len(self.busboard_token) < BUSBOARD_TOKEN_MIN_LEN:
            return f"BUSBOARD_OPS_TOKEN must be at least {BUSBOARD_TOKEN_MIN_LEN} characters."
        from urllib.parse import urlsplit
        try:
            p = urlsplit(self.busboard_url)
        except ValueError:
            return "BUSBOARD_URL is not a valid URL."
        if p.scheme not in ("http", "https") or not p.hostname:
            return "BUSBOARD_URL must be an http(s) URL."
        if p.username or p.password:
            return "BUSBOARD_URL must not contain credentials."
        if self.on_vercel and p.scheme != "https":
            return "BUSBOARD_URL must use https on a public deployment (the token travels with every call)."
        return None

    def provider_mode(self) -> str:
        if self.provider in ("nim", "registry"):
            return self.provider
        return "nim" if (self.on_vercel or self.nvidia_api_key) else "registry"

    def cors_allowed_origins(self) -> list[str]:
        origins = {"capacitor://localhost", "https://localhost"}
        origins.update(o.rstrip("/") for o in self.cors_origins)
        if self.public_url:
            origins.add(self.public_url)
        for var in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL", "VERCEL_BRANCH_URL"):
            host = (os.environ.get(var) or "").strip().rstrip("/")
            if host:
                origins.add(host if host.startswith("https://") else f"https://{host}")
        return sorted(origins)

    @property
    def db_file(self) -> Path:
        return Path(self.db_path).resolve()


config = Config()
