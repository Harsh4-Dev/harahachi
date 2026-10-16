"""Server-to-server client for the Bus Board ops API (v7 admin "Bus Board" tabs).

Bus Board is the second phone app's server (hosted separately). Its ops
contract, called ONLY from this server — the token never reaches a browser:

* ``GET  {BUSBOARD_URL}/v1/ops/stats``                        -> stats JSON
* ``POST {BUSBOARD_URL}/v1/ops/contributors/{id_hash}/block``   (and ``/unblock``)
* ``POST {BUSBOARD_URL}/v1/ops/snapshot``
* every call: ``Authorization: Bearer <BUSBOARD_OPS_TOKEN>``; a wrong/missing
  token answers **404** (the surface is invisible), which we report as
  "check the token".

Each call is bounded by ``BUSBOARD_TIMEOUT`` (<= 8 s, wall clock), stats are
cached in-process for :data:`CACHE_TTL` seconds, and every message that leaves
this module is scrubbed of the token.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

CACHE_TTL = 30.0
ERROR_CACHE_TTL = 10.0
MAX_RESPONSE_BYTES = 2_000_000
ID_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")
ACTIONS = ("block", "unblock")

_cache: dict = {"at": 0.0, "value": None, "key": None}


def _cfg():
    from .config import config
    return config


def problem() -> str | None:
    return _cfg().busboard_problem()


def configured() -> bool:
    return problem() is None


def clear_cache() -> None:
    _cache.update(at=0.0, value=None, key=None)


def _scrub(text: str) -> str:
    tok = _cfg().busboard_token
    out = str(text)
    if tok:
        out = out.replace(tok, "***")
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _not_connected(message: str) -> dict:
    return {"connected": False, "configured": False, "error": {"code": "not_configured", "message": message}}


def _fail(code: str, message: str, status: int | None = None) -> dict:
    return {"connected": False, "configured": True,
            "error": {"code": code, "message": _scrub(message), "status": status}}


async def _http(method: str, path: str) -> tuple[int, object]:
    """One bounded call to Bus Board (single choke point; tests use a real stub server)."""
    c = _cfg()
    url = c.busboard_url + path
    timeout = c.busboard_timeout
    headers = {"Authorization": f"Bearer {c.busboard_token}", "Accept": "application/json",
               "User-Agent": "sekisho-admin/7"}

    async def _go():
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(3.0, timeout)),
                                     follow_redirects=False, trust_env=False) as client:
            r = await client.request(method, url, headers=headers)
            body = r.content[:MAX_RESPONSE_BYTES + 1]
            if len(body) > MAX_RESPONSE_BYTES:
                return r.status_code, None  # oversized -> treated as a bad response
            try:
                return r.status_code, json.loads(body.decode("utf-8")) if body else None
            except (ValueError, UnicodeError):
                return r.status_code, None

    return await asyncio.wait_for(_go(), timeout=timeout + 0.5)


def _classify(exc: BaseException) -> dict:
    t = _cfg().busboard_timeout
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return _fail("timeout", f"Bus Board did not answer within {t:g} s. It may be asleep or down; "
                                "try Refresh in a moment.")
    if isinstance(exc, httpx.HTTPError):
        return _fail("unreachable", f"Could not reach Bus Board ({type(exc).__name__}). "
                                    "Check BUSBOARD_URL and that the service is running.")
    return _fail("unreachable", f"Could not reach Bus Board ({type(exc).__name__}).")


def _status_error(status: int) -> dict:
    if status == 404:
        return _fail("bad_token", "Bus Board answered 404: check the token (BUSBOARD_OPS_TOKEN must equal "
                                  "Bus Board's ops token, 48+ characters) and BUSBOARD_URL.", status)
    if status == 429:
        return _fail("rate_limited", "Bus Board is rate-limiting the ops API. Try again in a minute.", status)
    return _fail("http_error", f"Bus Board answered HTTP {status}.", status)


def _clean(data):
    """Strip any echo of the token from the JSON before it can reach a browser."""
    tok = _cfg().busboard_token
    if not tok:
        return data
    text = json.dumps(data)
    return json.loads(text.replace(tok, "***")) if tok in text else data


async def stats(force: bool = False) -> dict:
    """{connected, configured, stats?, fetched_at, cached, age_s, error?}."""
    why = problem()
    if why:
        return _not_connected(why)
    c = _cfg()
    key = (c.busboard_url, c.busboard_token)
    now = time.monotonic()
    cached = _cache["value"]
    if not force and cached is not None and _cache["key"] == key:
        ttl = CACHE_TTL if cached.get("connected") else ERROR_CACHE_TTL
        age = now - _cache["at"]
        if age < ttl:
            return {**cached, "cached": True, "age_s": int(age)}
    try:
        status, data = await _http("GET", "/v1/ops/stats")
    except Exception as e:  # noqa: BLE001 - every failure becomes a clean message
        value = _classify(e)
    else:
        if status >= 400:
            value = _status_error(status)
        elif not isinstance(data, dict):
            value = _fail("bad_response", "Bus Board answered, but not with a stats JSON object.", status)
        else:
            value = {"connected": True, "configured": True, "stats": _clean(data)}
    value["fetched_at"] = _now_iso()
    _cache.update(at=now, value=value, key=key)
    return {**value, "cached": False, "age_s": 0}


def valid_id_hash(id_hash: str) -> bool:
    return bool(ID_HASH_RE.match(id_hash or ""))


async def _post(path: str) -> dict:
    why = problem()
    if why:
        return {"ok": False, "error": {"code": "not_configured", "message": why}}
    try:
        status, data = await _http("POST", path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": _classify(e)["error"]}
    if status >= 400:
        return {"ok": False, "error": _status_error(status)["error"]}
    clear_cache()  # the next stats view shows the effect
    return {"ok": True, "status": status, "result": _clean(data) if isinstance(data, (dict, list)) else None}


async def contributor_action(id_hash: str, action: str) -> dict:
    if action not in ACTIONS:
        return {"ok": False, "error": {"code": "bad_action", "message": "action must be block|unblock"}}
    if not valid_id_hash(id_hash):
        return {"ok": False, "error": {"code": "bad_id_hash",
                                       "message": "id_hash must be 4-128 characters of [A-Za-z0-9_-]"}}
    return await _post(f"/v1/ops/contributors/{quote(id_hash, safe='')}/{action}")


async def snapshot() -> dict:
    return await _post("/v1/ops/snapshot")
