"""Entry point: `python -m sekisho`.

Initializes the DB, prints detected LAN/Tailscale IPs + the admin URL (and the
generated admin password if none was supplied), then serves the FastAPI app.
"""
from __future__ import annotations

import sys

import uvicorn

from . import db
from .config import config
from .net import classify_ips


def _enable_utf8_console() -> None:
    """Windows consoles default to cp1252 and choke on kanji. Reconfigure to UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def _banner() -> None:
    ips = classify_ips()
    port = config.port
    line = "─" * 58
    print()
    print(f"  関所  Sekisho edge-AI gateway  ·  v2.0")
    print(f"  {line}")
    if ips["tailscale"]:
        for ip in ips["tailscale"]:
            print(f"  Tailscale : http://{ip}:{port}   (recommended for QR)")
    if ips["lan"]:
        for ip in ips["lan"]:
            print(f"  LAN       : http://{ip}:{port}   (hotspot mode)")
    if ips["other"]:
        for ip in ips["other"]:
            print(f"  Other     : http://{ip}:{port}")
    print(f"  Local     : http://127.0.0.1:{port}")
    print(f"  {line}")
    print(f"  Admin     : http://127.0.0.1:{port}/admin")
    if config.admin_password_generated:
        print(f"  Admin PW  : {config.admin_password}   (generated — set SEKISHO_ADMIN_PASSWORD to fix)")
    else:
        print(f"  Admin PW  : (from SEKISHO_ADMIN_PASSWORD)")
    print(f"  Database  : {db.backend_kind()}")
    from . import backends
    if backends.provider_mode() == "nim":
        src = backends.nvidia_key_source()
        state = {"env": "key from NVIDIA_API_KEY", "admin": "key stored from the admin"}.get(src, "no key yet")
        print(f"  LLM       : NVIDIA NIM ({state})")
    elif config.vllm_url:
        print(f"  vLLM      : {config.vllm_url}  model={config.vllm_model or '?'}")
    else:
        print(f"  vLLM      : not configured — mock analyzer active"
              f" (SEKISHO_ALLOW_MOCK={'1' if config.allow_mock else '0'})")
    print(f"  {line}")
    print()


def main() -> None:
    _enable_utf8_console()
    config.ensure_admin_password()
    db.init_from_config()  # Postgres when DATABASE_URL is set, else SQLite at SEKISHO_DB_PATH
    _banner()
    # import the app after DB init so module-level code is safe
    from .app import app

    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
