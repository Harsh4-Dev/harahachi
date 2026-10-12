"""Vercel Python function entrypoint for Sekisho (API.md v4).

vercel.json rewrites /api/*, /admin, /admin/*, /pair/*, /llm/* here; the ASGI
app sees the ORIGINAL request path, so FastAPI routing is unchanged. The same
``server/sekisho`` package also runs in the Docker container (``python -m
sekisho``) — behaviour is switched by env (``VERCEL=1`` is set by the platform).

Local check (repo root):  VERCEL=1 DATABASE_URL=... uvicorn api.index:app
"""
from __future__ import annotations

import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from sekisho.app import app, warm_up  # noqa: E402

try:
    # Eager cold-start init (DB pool + idempotent migrations + registry bootstrap).
    # A misconfiguration (e.g. no DATABASE_URL) must not crash the import: each
    # request then answers a clean 503 {"error": {"code": "server_misconfigured"}}.
    warm_up()
except Exception as exc:  # noqa: BLE001
    print(f"[sekisho] warm-up failed: {exc}", file=sys.stderr)

__all__ = ["app"]
