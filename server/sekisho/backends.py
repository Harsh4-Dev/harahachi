"""LLM backend registry + failover-aware OpenAI-compatible client (v2).

NVIDIA API, vLLM, LM Studio and plain OpenAI all speak the same chat-completions
surface, so the server keeps a *registry* of backends instead of a single URL.
Two active pointers (settings) select the text and vision backend; on error or
timeout the client fails over to the next enabled backend with the needed
capability, and only mocks after every candidate fails.

This module replaces the old single-URL ``vllm_client`` internals. It exposes:
  * registry CRUD (used by admin)
  * ``chat_completion`` / ``analyze_image`` / ``passthrough_*`` used by app routes
  * ``active_summary`` for /health and the admin status card

v4 adds a second provider mode, **NVIDIA NIM** (the only provider on Vercel):
per-task model slots (analyze / body / chat / insights) resolved DB setting >
env ``NIM_MODEL_<TASK>`` > default, each with an optional fallback model tried
once. The key comes from ``NVIDIA_API_KEY`` only — never stored, never returned.
Every outbound base URL passes ``check_backend_url`` (https allowlist on Vercel)
and every request is bounded by one deadline (``SEKISHO_LLM_BUDGET``).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from urllib.parse import urlsplit

import httpx

from . import db

# ---- prompts (shared with app routes) ------------------------------------
ANALYZE_SYSTEM_PROMPT = (
    "You are a nutrition vision analyzer specialized in Indian home and restaurant "
    "plates. Look at the food photo and respond with ONLY a single JSON object, no "
    "prose, no markdown fences. Schema:\n"
    '{"dish": string, "calories": int, "protein_g": int, "carbs_g": int, '
    '"fat_g": int, "fiber_g": int or omitted, "sugar_g": int or omitted, '
    '"sodium_mg": int or omitted, "confidence": float 0..1, '
    '"question": string or null, '
    '"items": [{"name": string, "calories": int, "protein_g": int, '
    '"carbs_g": int, "fat_g": int, "fiber_g": int or omitted, '
    '"sugar_g": int or omitted, "sodium_mg": int or omitted, '
    '"point": {"x": float 0..1, "y": float 0..1} or omitted}]}\n'
    "ITEMIZE THE PLATE. Indian meals are almost always a MAIN plus SIDES: itemize "
    "every visible component separately — e.g. dal, rice, roti/chapati (give the "
    "count like 'Roti x2'), sabzi/curry, curd/raita, pickle, papad, salad, sweet. "
    "For a thali list each katori; for biryani list the biryani and the raita/salan; "
    "for dosa list the dosa, sambar and chutneys. 'items' is REQUIRED and must have "
    "one entry per component, each with its OWN calories, protein_g, carbs_g and "
    "fat_g. The top-level calories/protein_g/carbs_g/fat_g MUST equal the sum of the "
    "items. Estimate realistic values for the serving as photographed. When you can, "
    "ALSO estimate fiber_g, sugar_g and sodium_mg per item (omit any you can't ground); "
    "the top-level fiber_g/sugar_g/sodium_mg is the sum of the items' values.\n"
    "For each item, ALSO give 'point' = the approximate NORMALIZED center of that "
    "component in the image, where x is 0 (left) .. 1 (right) and y is 0 (top) .. 1 "
    "(bottom), so the app can pin the label next to that part of the dish. OMIT "
    "'point' for an item when you cannot ground its location reliably.\n"
    "Ask ONE clarifying question in 'question' (and set confidence < 0.85) only when "
    "a HIGH-CALORIE ambiguity would materially change the total — e.g. is there ghee "
    "or extra oil on the roti/rice, is it paneer or tofu, is the rice a small or "
    "large serving, is the gravy cream-based. Otherwise set question to null and "
    "confidence >= 0.85. Use the user's hint/portion to refine your estimate. "
    "Output JSON only."
)

SUGGEST_SYSTEM_PROMPT = (
    "You are a nutrition assistant. You will receive the user's remaining calorie/"
    "macro budget for today and a list of meals they have eaten before. Pick and "
    "rank the meals that best fit the remaining budget and write a short friendly "
    "reason for each (mention how it fits the remaining kcal/protein). Respond with "
    'ONLY JSON: {"suggestions": [{"dish": str, "calories": int, "protein_g": int, '
    '"carbs_g": int, "fat_g": int, "reason": str}]}. No prose, no fences.'
)


class BackendUnavailable(Exception):
    pass


class BackendURLNotAllowed(ValueError):
    """A backend base_url failed the SSRF allowlist."""


# Back-compat alias — old code imported VLLMUnavailable.
VLLMUnavailable = BackendUnavailable

LLM_MAX_TOKENS_CAP = 1024
BACKEND_KINDS = ("vllm", "nvidia", "lmstudio", "openai")

# /llm/v1 passthrough: the only request keys forwarded upstream. Everything else
# (n, best_of, logprobs, tools, model, ...) is dropped; n is forced to 1.
PASSTHROUGH_KEYS = ("messages", "temperature", "top_p", "max_tokens", "stop")


def validate_passthrough_payload(payload: dict) -> str | None:
    """Bound a /llm/v1/chat/completions body. Returns an error message or None.

    Same limits as POST /chat: <= 20 messages, <= 8000 characters in total,
    plain-text content only."""
    from .models import CHAT_MAX_MESSAGES, CHAT_MAX_TOTAL_CHARS
    msgs = payload.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return "messages must be a non-empty list"
    if len(msgs) > CHAT_MAX_MESSAGES:
        return f"at most {CHAT_MAX_MESSAGES} messages"
    total = 0
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant") \
                or not isinstance(m.get("content"), str):
            return "each message needs role system|user|assistant and string content"
        total += len(m["content"])
    if total > CHAT_MAX_TOTAL_CHARS:
        return f"messages exceed {CHAT_MAX_TOTAL_CHARS} characters in total"
    for key, lo, hi in (("temperature", 0.0, 2.0), ("top_p", 0.0, 1.0)):
        if key in payload and payload[key] is not None:
            v = payload[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                return f"{key} must be a number in [{lo}, {hi}]"
    stop = payload.get("stop")
    if stop is not None:
        stops = [stop] if isinstance(stop, str) else stop
        if not isinstance(stops, list) or len(stops) > 4 or \
                not all(isinstance(x, str) and len(x) <= 64 for x in stops):
            return "stop must be a string or up to 4 short strings"
    return None


# Presets surfaced in the admin UI.
PRESETS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "lmstudio": "http://host.docker.internal:1234/v1",
    "vllm": "http://host.docker.internal:8000/v1",
    "openai": "https://api.openai.com/v1",
}

# ---------------------------------------------------------------------------
# NVIDIA NIM per-task model slots (v4)
# ---------------------------------------------------------------------------
# Model IDs verified against NVIDIA's hosted API (integrate.api.nvidia.com /v1/models
# and docs.api.nvidia.com reference pages, 2026-10). The spec's analyze default
# `qwen/qwen2.5-vl-72b-instruct` is NOT served by the hosted API (reference page
# 404, absent from /v1/models), so analyze uses the verified Llama 3.2 90B Vision.
TASKS = ("analyze", "body", "chat", "insights")
VISION_TASKS = ("analyze", "body")
NIM_DEFAULT_MODELS = {
    "analyze": "meta/llama-3.2-90b-vision-instruct",
    "body": "meta/llama-3.2-90b-vision-instruct",
    "chat": "meta/llama-3.3-70b-instruct",
    "insights": None,  # falls back to the chat slot
}
NIM_DEFAULT_FALLBACKS = {
    "analyze": "meta/llama-3.2-11b-vision-instruct",
    "body": "meta/llama-3.2-11b-vision-instruct",
    "chat": "meta/llama-3.2-90b-vision-instruct",
    "insights": None,  # falls back to the chat slot's fallback
}
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")


def _cfg():
    from .config import config
    return config


def provider_mode() -> str:
    return _cfg().provider_mode()


def nim_mode() -> bool:
    return provider_mode() == "nim"


def valid_model_id(model: str) -> bool:
    return bool(_MODEL_RE.match(model or ""))


def slot_config(task: str) -> dict:
    """Resolve a task's model + fallback: DB setting > env > default."""
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}")
    env_model = (os.environ.get(f"NIM_MODEL_{task.upper()}") or "").strip() or None
    env_fb = (os.environ.get(f"NIM_FALLBACK_{task.upper()}") or "").strip() or None
    db_model = db.get_setting(f"nim_model_{task}")
    db_fb = db.get_setting(f"nim_fallback_{task}")

    if db_model:
        model, source = db_model, "admin"
    elif env_model:
        model, source = env_model, "env"
    elif NIM_DEFAULT_MODELS[task]:
        model, source = NIM_DEFAULT_MODELS[task], "default"
    else:  # insights -> chat
        chat = slot_config("chat")
        model, source = chat["model"], "chat"

    if db_fb is not None and db_fb != "":
        fallback = None if db_fb == "none" else db_fb
    elif env_fb:
        fallback = None if env_fb == "none" else env_fb
    elif NIM_DEFAULT_FALLBACKS[task]:
        fallback = NIM_DEFAULT_FALLBACKS[task]
    elif task == "insights":
        fallback = slot_config("chat")["fallback"]
    else:
        fallback = None
    if fallback == model:
        fallback = None
    return {"task": task, "model": model, "fallback": fallback, "source": source,
            "env_model": env_model, "default_model": NIM_DEFAULT_MODELS[task]}


def set_slot(task: str, model: str | None, fallback: str | None) -> dict:
    """Admin edit: empty model/fallback reverts to env/default; fallback 'none' disables."""
    if task not in TASKS:
        raise ValueError("bad_task")
    model = (model or "").strip()
    fallback = (fallback or "").strip()
    if model and not valid_model_id(model):
        raise ValueError("bad_model")
    if fallback and fallback != "none" and not valid_model_id(fallback):
        raise ValueError("bad_model")
    if model:
        db.set_setting(f"nim_model_{task}", model)
    else:
        db.delete_setting(f"nim_model_{task}")
    if fallback:
        db.set_setting(f"nim_fallback_{task}", fallback)
    else:
        db.delete_setting(f"nim_fallback_{task}")
    return slot_config(task)


def mask_key(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 12:
        return "••••"
    return f"{key[:6]}…{key[-4:]}"


def scrub(text: str) -> str:
    """Remove any secret that might be echoed back in an upstream error."""
    key = _cfg().nvidia_api_key
    out = str(text)
    if key:
        out = out.replace(key, "***")
    return out


def nim_payload() -> dict:
    """Admin view of the NIM provider. The key is NEVER included — only a mask."""
    c = _cfg()
    return {
        "provider": provider_mode(),
        "base_url": c.nim_base_url,
        "key_configured": bool(c.nvidia_api_key),
        "key_hint": mask_key(c.nvidia_api_key),
        "allowlist": c.backend_allowlist,
        "allowlist_enforced": c.enforce_backend_allowlist,
        "max_image_b64": c.nim_max_image_b64,
        "slots": {t: slot_config(t) for t in TASKS},
    }


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------
def check_backend_url(url: str) -> str:
    """Validate an outbound LLM base URL. Returns the normalized URL or raises.

    Always: http(s) only, no userinfo. When enforcement is on (always on Vercel):
    https only, host must be in the allowlist, default port only.
    """
    c = _cfg()
    url = (url or "").strip().rstrip("/")
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError as e:
        raise BackendURLNotAllowed(f"invalid URL: {e}")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise BackendURLNotAllowed("base_url must be an http(s) URL")
    if p.username or p.password:
        raise BackendURLNotAllowed("credentials in base_url are not allowed")
    if c.enforce_backend_allowlist:
        if p.scheme != "https":
            raise BackendURLNotAllowed("base_url must use https")
        if p.hostname.lower() not in c.backend_allowlist:
            raise BackendURLNotAllowed(
                f"host {p.hostname!r} is not in the backend allowlist "
                f"({', '.join(c.backend_allowlist)}); extend SEKISHO_BACKEND_ALLOWLIST to permit it")
        if port not in (None, 443):
            raise BackendURLNotAllowed("non-default ports are not allowed")
    return url


# ---------------------------------------------------------------------------
# HTTP layer (single choke point — monkeypatched by the smoke tests)
# ---------------------------------------------------------------------------
async def _http_post_json(url: str, payload: dict, headers: dict, timeout: "httpx.Timeout") -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        r = await client.post(url, json=payload, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = None
    return r.status_code, data


async def _http_get_json(url: str, headers: dict, timeout: "httpx.Timeout") -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        r = await client.get(url, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = None
    return r.status_code, data


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def bootstrap() -> None:
    """Seed the registry once from env, then pick active backends.

    Priority: ``SEKISHO_BACKENDS_JSON`` (list of backend dicts) then the legacy
    ``SEKISHO_VLLM_URL`` / ``SEKISHO_VLLM_MODEL``. Runs only when the registry is
    empty (marker in settings) so admin edits are never clobbered. Entries that
    fail the SSRF allowlist are skipped.
    """
    if db.get_setting("backends_bootstrapped") == "1":
        _ensure_active()
        return

    seeded = False
    raw = os.environ.get("SEKISHO_BACKENDS_JSON")
    if raw:
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    try:
                        create_backend(
                            name=str(it.get("name") or it.get("kind") or "backend"),
                            kind=str(it.get("kind") or "openai"),
                            base_url=str(it.get("base_url") or ""),
                            api_key=it.get("api_key"),
                            model=str(it.get("model") or ""),
                            vision=bool(it.get("vision", False)),
                            enabled=bool(it.get("enabled", True)),
                        )
                        seeded = True
                    except BackendURLNotAllowed:
                        continue
        except Exception:
            pass

    if not seeded:
        legacy_url = os.environ.get("SEKISHO_VLLM_URL")
        legacy_model = os.environ.get("SEKISHO_VLLM_MODEL")
        if legacy_url and legacy_model:
            try:
                create_backend(
                    name="vLLM (env)", kind="vllm", base_url=legacy_url,
                    api_key=os.environ.get("SEKISHO_VLLM_KEY"),
                    model=legacy_model, vision=True, enabled=True,
                )
                seeded = True
            except BackendURLNotAllowed:
                pass

    db.set_setting("backends_bootstrapped", "1")
    _ensure_active()


def _ensure_active() -> None:
    """Point active_text/active_vision at a sensible enabled backend if unset."""
    rows = list_backends(include_disabled=False)
    if not rows:
        return
    if db.get_setting("active_text_backend") is None:
        db.set_setting("active_text_backend", str(rows[0]["id"]))
    vis = next((r for r in rows if r["vision"]), None)
    if db.get_setting("active_vision_backend") is None and vis:
        db.set_setting("active_vision_backend", str(vis["id"]))


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------
def _row_to_public(r) -> dict:
    return {
        "id": r["id"], "name": r["name"], "kind": r["kind"], "base_url": r["base_url"],
        "has_key": bool(r["api_key"]), "model": r["model"],
        "vision": bool(r["vision"]), "enabled": bool(r["enabled"]),
    }


def create_backend(name, kind, base_url, api_key, model, vision, enabled) -> dict:
    from .security import now_iso
    base_url = check_backend_url(base_url)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO backends (name, kind, base_url, api_key, model, vision, enabled, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, kind, base_url, (api_key or None), model,
             1 if vision else 0, 1 if enabled else 0, now_iso()),
        )
        bid = cur.lastrowid
        r = cur.execute("SELECT * FROM backends WHERE id=?", (bid,)).fetchone()
    _ensure_active()
    return _row_to_public(r)


def update_backend(bid: int, **fields) -> dict | None:
    allowed = {"name", "kind", "base_url", "api_key", "model", "vision", "enabled"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "base_url":
            v = check_backend_url(str(v))
        if k == "kind" and v not in BACKEND_KINDS:
            raise ValueError("bad_kind")
        if k in ("vision", "enabled"):
            v = 1 if v else 0
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get_backend(bid)
    vals.append(bid)
    with db.cursor() as cur:
        cur.execute(f"UPDATE backends SET {', '.join(sets)} WHERE id=?", vals)
        r = cur.execute("SELECT * FROM backends WHERE id=?", (bid,)).fetchone()
    return _row_to_public(r) if r else None


def delete_backend(bid: int) -> None:
    with db.cursor() as cur:
        cur.execute("DELETE FROM backends WHERE id=?", (bid,))
    # If it was active, clear that pointer and re-pick.
    for key in ("active_text_backend", "active_vision_backend"):
        if db.get_setting(key) == str(bid):
            db.set_setting(key, None)
    _ensure_active()


def get_backend(bid: int) -> dict | None:
    with db.cursor() as cur:
        r = cur.execute("SELECT * FROM backends WHERE id=?", (bid,)).fetchone()
    return _row_to_public(r) if r else None


def _get_backend_full(bid: int) -> dict | None:
    with db.cursor() as cur:
        r = cur.execute("SELECT * FROM backends WHERE id=?", (bid,)).fetchone()
    return dict(r) if r else None


def list_backends(include_disabled: bool = True) -> list[dict]:
    with db.cursor() as cur:
        if include_disabled:
            rows = cur.execute("SELECT * FROM backends ORDER BY id").fetchall()
        else:
            rows = cur.execute("SELECT * FROM backends WHERE enabled=1 ORDER BY id").fetchall()
    return [_row_to_public(r) for r in rows]


def set_active(kind: str, bid: int | None) -> None:
    key = "active_vision_backend" if kind == "vision" else "active_text_backend"
    db.set_setting(key, str(bid) if bid is not None else None)


def _active_id(need_vision: bool) -> int | None:
    key = "active_vision_backend" if need_vision else "active_text_backend"
    val = db.get_setting(key)
    try:
        return int(val) if val is not None else None
    except ValueError:
        return None


def _candidate_chain(need_vision: bool) -> list[dict]:
    """Enabled backends to try, active first, then the rest (capability-filtered)."""
    with db.cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM backends WHERE enabled=1 ORDER BY id"
        ).fetchall()]
    if need_vision:
        rows = [r for r in rows if r["vision"]]
    active = _active_id(need_vision)
    if active is not None:
        rows.sort(key=lambda r: 0 if r["id"] == active else 1)
    return rows


def is_configured(need_vision: bool = False) -> bool:
    if nim_mode():
        return bool(_cfg().nvidia_api_key)
    return bool(_candidate_chain(need_vision))


# ---------------------------------------------------------------------------
# Active summary (health / admin)
# ---------------------------------------------------------------------------
def _nim_summary_entry(task: str) -> dict:
    c = _cfg()
    s = slot_config(task)
    return {"id": None, "name": "NVIDIA NIM", "kind": "nvidia", "base_url": c.nim_base_url,
            "has_key": bool(c.nvidia_api_key), "model": s["model"],
            "vision": task in VISION_TASKS, "enabled": bool(c.nvidia_api_key)}


def active_summary() -> dict:
    if nim_mode():
        on = 1 if _cfg().nvidia_api_key else 0
        return {"text": _nim_summary_entry("chat"), "vision": _nim_summary_entry("analyze"),
                "count": 1, "enabled_count": on, "provider": "nim"}
    text_id = _active_id(False)
    vis_id = _active_id(True)
    text = get_backend(text_id) if text_id else None
    vision = get_backend(vis_id) if vis_id else None
    return {
        "text": text,
        "vision": vision,
        "count": len(list_backends()),
        "enabled_count": len(list_backends(include_disabled=False)),
        "provider": "registry",
    }


def active_text_model() -> str | None:
    if nim_mode():
        return slot_config("chat")["model"] if _cfg().nvidia_api_key else None
    bid = _active_id(False)
    b = _get_backend_full(bid) if bid else None
    return b["model"] if b else None


# ---------------------------------------------------------------------------
# HTTP calls
# ---------------------------------------------------------------------------
def _headers(backend: dict) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if backend.get("api_key"):
        h["Authorization"] = f"Bearer {backend['api_key']}"
    return h


def _op_total(op: str) -> float:
    c = _cfg()
    if op == "analyze":
        return c.analyze_timeout
    if op in ("chat", "passthrough"):
        return c.chat_timeout
    return c.vllm_timeout


def _timeout(op: str = "chat", remaining: float | None = None) -> "httpx.Timeout":
    """Explicit httpx timeout with a short connect so a dead backend fails fast
    (letting failover kick in), and a per-operation total that never exceeds what
    is left of the request's LLM budget (so we answer 503, never a platform 504).

    op: 'analyze' (vision) | 'chat' | 'passthrough' (chat budget).
    """
    c = _cfg()
    total = _op_total(op)
    if remaining is not None:
        total = max(1.0, min(total, remaining))
    return httpx.Timeout(total, connect=min(c.connect_timeout, total))


def _new_deadline() -> float:
    return time.monotonic() + max(5.0, _cfg().llm_budget)


def _content_of(status: int, data) -> str:
    if status >= 400 or not isinstance(data, dict):
        raise BackendUnavailable(f"upstream HTTP {status}")
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise BackendUnavailable("upstream response missing choices[0].message.content")


async def _one_completion(backend: dict, messages: list[dict], max_tokens: int, temperature: float,
                          op: str, remaining: float | None = None, slot: str | None = None) -> str:
    """One upstream chat-completions call. Every attempt (primary, fallback,
    failover) is recorded as one ``llm_calls`` row (v6 ops instrumentation)."""
    from . import usage
    slot = slot or ("analyze" if op == "analyze" else "chat")
    t0 = time.perf_counter()
    status: int | None = None
    data = None
    try:
        base = check_backend_url(backend["base_url"])
        payload = {
            "model": backend["model"],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        status, data = await _http_post_json(f"{base}/chat/completions", payload, _headers(backend),
                                             _timeout(op, remaining))
        content = _content_of(status, data)
    except Exception as e:
        tin, tout = usage.usage_tokens(data)
        usage.record_llm_call(slot, backend.get("model"), False, int((time.perf_counter() - t0) * 1000),
                              tin, tout, usage.error_code_for(e, status))
        raise
    tin, tout = usage.usage_tokens(data)
    usage.record_llm_call(slot, backend.get("model"), True, int((time.perf_counter() - t0) * 1000),
                          tin, tout)
    return content


def _nim_backend(model: str) -> dict:
    c = _cfg()
    return {"base_url": c.nim_base_url, "api_key": c.nvidia_api_key, "model": model}


async def _nim_completion(task: str, messages: list[dict], max_tokens: int, temperature: float,
                          op: str, deadline: float) -> str:
    c = _cfg()
    if not c.nvidia_api_key:
        raise BackendUnavailable("NVIDIA_API_KEY is not set")
    s = slot_config(task)
    models = [s["model"]] + ([s["fallback"]] if s["fallback"] else [])
    last_err: Exception | None = None
    for model in models:  # primary, then the slot's fallback ONCE
        remaining = deadline - time.monotonic()
        if remaining < 2.0:
            break
        try:
            return await _one_completion(_nim_backend(model), messages, max_tokens, temperature,
                                         op, remaining - 0.5, slot=task)
        except Exception as e:  # noqa: BLE001 — try the fallback model
            last_err = e
    raise BackendUnavailable(scrub(str(last_err)) if last_err else "LLM budget exhausted")


async def chat_completion(
    messages: list[dict], *, need_vision: bool = False, max_tokens: int = 700,
    temperature: float = 0.4, op: str = "chat", task: str | None = None,
) -> str:
    """Run a chat completion.

    NIM mode: the task's slot model, then its fallback model once.
    Registry mode: failover across enabled capable backends (v2 behaviour).
    Both are bounded by one per-request deadline (config.llm_budget).
    """
    deadline = _new_deadline()
    if nim_mode():
        task = task or ("analyze" if need_vision else "chat")
        return await _nim_completion(task, messages, max_tokens, temperature, op, deadline)
    chain = _candidate_chain(need_vision)
    if not chain:
        raise BackendUnavailable("no enabled backend with the needed capability")
    slot = task or ("analyze" if need_vision else "chat")
    last_err: Exception | None = None
    for backend in chain:
        remaining = deadline - time.monotonic()
        if remaining < 2.0:
            break
        try:
            return await _one_completion(backend, messages, max_tokens, temperature, op, remaining - 0.5,
                                         slot=slot)
        except Exception as e:  # noqa: BLE001 — try the next backend
            last_err = e
            continue
    raise BackendUnavailable(str(last_err) if last_err else "all backends failed")


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                return json.loads(candidate)
            except Exception:
                pass
    raise BackendUnavailable("could not parse JSON from model output")


def prepare_image(image_bytes: bytes, mime: str) -> tuple[bytes, str]:
    """NIM mode: re-encode to an EXIF-free JPEG under the inline base64 limit.
    Registry mode (vLLM/LM Studio): pass through unchanged."""
    if nim_mode():
        from . import images
        return images.fit_for_nim(image_bytes, _cfg().nim_max_image_b64)
    return image_bytes, mime


async def analyze_image(image_bytes: bytes, mime: str, hint: str, portion: str) -> dict:
    # Pillow work is CPU-bound: keep it off the event loop.
    image_bytes, mime = await asyncio.to_thread(prepare_image, image_bytes, mime)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    user_text = "Analyze this meal photo."
    if hint:
        user_text += f" Hint: {hint}."
    if portion:
        user_text += f" Portion size: {portion}."
    messages = [
        {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]
    content = await chat_completion(messages, need_vision=True, max_tokens=800, temperature=0.3,
                                    op="analyze", task="analyze")
    return _extract_json(content)


async def phrase_suggestions(remaining: dict, candidates: list[dict]) -> list[dict]:
    user = json.dumps({"remaining": remaining, "candidate_meals": candidates})
    messages = [
        {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    content = await chat_completion(messages, max_tokens=600, temperature=0.5, task="insights")
    data = _extract_json(content)
    return data.get("suggestions", [])


# ---------------------------------------------------------------------------
# /llm/v1 passthrough — uses the active TEXT backend (NIM: the chat slot), pins its model.
# ---------------------------------------------------------------------------
def _active_text_backend_full() -> dict | None:
    if nim_mode():
        if not _cfg().nvidia_api_key:
            return None
        return _nim_backend(slot_config("chat")["model"])
    bid = _active_id(False)
    return _get_backend_full(bid) if bid else None


async def passthrough_models() -> tuple[int, dict]:
    backend = _active_text_backend_full()
    if not backend:
        raise BackendUnavailable("no active text backend")
    if nim_mode():
        # Only the pinned model is usable through this proxy — don't proxy the
        # whole NIM catalog (and don't spend a round-trip on it).
        return 200, {"object": "list", "data": [
            {"id": backend["model"], "object": "model", "owned_by": "nvidia"}]}
    base = check_backend_url(backend["base_url"])
    status, body = await _http_get_json(f"{base}/models", _headers(backend), _timeout("passthrough"))
    if body is None:
        body = {"data": [{"id": backend["model"], "object": "model"}]}
    return status, body


async def passthrough_chat_completions(payload: dict) -> tuple[int, dict]:
    backend = _active_text_backend_full()
    if not backend:
        raise BackendUnavailable("no active text backend")
    base = check_backend_url(backend["base_url"])
    # Forward ONLY whitelisted keys (H2): no n/best_of/logprobs/tools/model etc.
    body = {k: payload[k] for k in PASSTHROUGH_KEYS if k in payload}
    body["model"] = backend["model"]  # pin active text backend's model
    body["n"] = 1
    mt = body.get("max_tokens")
    try:
        mt = int(mt) if mt is not None else LLM_MAX_TOKENS_CAP
    except (TypeError, ValueError):
        mt = LLM_MAX_TOKENS_CAP
    body["max_tokens"] = max(1, min(mt, LLM_MAX_TOKENS_CAP))
    from . import usage
    t0 = time.perf_counter()
    try:
        status, data = await _http_post_json(f"{base}/chat/completions", body, _headers(backend),
                                             _timeout("passthrough", _cfg().llm_budget))
    except Exception as e:
        usage.record_llm_call("passthrough", backend.get("model"), False,
                              int((time.perf_counter() - t0) * 1000), error_code=usage.error_code_for(e))
        raise
    _ok = status < 400 and isinstance(data, dict)
    _tin, _tout = usage.usage_tokens(data)
    usage.record_llm_call("passthrough", backend.get("model"), _ok, int((time.perf_counter() - t0) * 1000),
                          _tin, _tout, None if _ok else usage.error_code_for(None, status))
    if data is None:
        raise BackendUnavailable("backend returned non-JSON response")
    if status >= 500:
        raise BackendUnavailable(f"upstream HTTP {status}")
    return status, data


# ---------------------------------------------------------------------------
# Health + admin test
# ---------------------------------------------------------------------------
_health_cache: dict = {"at": 0.0, "value": (False, None)}


async def check_health() -> tuple[bool, str | None]:
    """(connected, model) — best-effort ping of the active text backend.

    NIM mode caches the result for 60 s per instance so a public /health can't be
    used to hammer the provider."""
    backend = _active_text_backend_full()
    if not backend:
        return False, None
    if nim_mode() and time.monotonic() - _health_cache["at"] < 60 \
            and _health_cache["value"][1] == backend["model"]:
        return _health_cache["value"]
    result: tuple[bool, str | None] = (False, backend["model"])
    try:
        base = check_backend_url(backend["base_url"])
        status, _ = await _http_get_json(f"{base}/models", _headers(backend),
                                         httpx.Timeout(4.0, connect=3.0))
        if status == 200:
            result = (True, backend["model"])
    except Exception:
        pass
    if nim_mode():
        _health_cache.update(at=time.monotonic(), value=result)
    return result


async def _ping(backend: dict) -> dict:
    result: dict = {"ok": False, "models": [], "ping": None, "error": None}
    try:
        base = check_backend_url(backend["base_url"])
        if not nim_mode():
            ms, mdata = await _http_get_json(f"{base}/models", _headers(backend),
                                             httpx.Timeout(10.0, connect=3.0))
            if ms == 200 and isinstance(mdata, dict):
                result["models"] = [m.get("id") for m in mdata.get("data", []) if m.get("id")]
        ps, pdata = await _http_post_json(
            f"{base}/chat/completions",
            {"model": backend["model"], "messages": [{"role": "user", "content": "ping"}],
             "max_tokens": 1, "temperature": 0.0},
            _headers(backend), httpx.Timeout(20.0, connect=3.0),
        )
        if ps >= 400:
            detail = ""
            if isinstance(pdata, dict):
                detail = str(pdata.get("detail") or pdata.get("error") or pdata.get("title") or "")[:160]
            raise BackendUnavailable(f"HTTP {ps} {detail}".strip())
        result["ping"] = "ok"
        result["ok"] = True
        if nim_mode():
            result["models"] = [backend["model"]]
    except Exception as e:  # noqa: BLE001
        result["error"] = scrub(str(e))[:300]
    return result


async def test_backend(bid: int) -> dict:
    """Ping a registry backend: list models + a 1-token completion."""
    backend = _get_backend_full(bid)
    if not backend:
        return {"ok": False, "error": "backend not found"}
    return await _ping(backend)


async def test_nim_slot(task: str) -> dict:
    """1-token ping of a NIM slot's primary model (spends ~1 token of credit)."""
    if task not in TASKS:
        return {"ok": False, "error": "unknown task"}
    if not _cfg().nvidia_api_key:
        return {"ok": False, "error": "NVIDIA_API_KEY is not set"}
    s = slot_config(task)
    out = await _ping(_nim_backend(s["model"]))
    out["model"] = s["model"]
    return out
