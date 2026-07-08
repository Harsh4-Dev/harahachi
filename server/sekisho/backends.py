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
"""
from __future__ import annotations

import base64
import json
import os
import re

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


# Back-compat alias — old code imported VLLMUnavailable.
VLLMUnavailable = BackendUnavailable

LLM_MAX_TOKENS_CAP = 1024

# Presets surfaced in the admin UI.
PRESETS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "lmstudio": "http://host.docker.internal:1234/v1",
    "vllm": "http://host.docker.internal:8000/v1",
    "openai": "https://api.openai.com/v1",
}


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def bootstrap() -> None:
    """Seed the registry once from env, then pick active backends.

    Priority: ``SEKISHO_BACKENDS_JSON`` (list of backend dicts) then the legacy
    ``SEKISHO_VLLM_URL`` / ``SEKISHO_VLLM_MODEL``. Runs only when the registry is
    empty (marker in settings) so admin edits are never clobbered.
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
        except Exception:
            pass

    if not seeded:
        legacy_url = os.environ.get("SEKISHO_VLLM_URL")
        legacy_model = os.environ.get("SEKISHO_VLLM_MODEL")
        if legacy_url and legacy_model:
            create_backend(
                name="vLLM (env)", kind="vllm", base_url=legacy_url,
                api_key=os.environ.get("SEKISHO_VLLM_KEY"),
                model=legacy_model, vision=True, enabled=True,
            )
            seeded = True

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
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO backends (name, kind, base_url, api_key, model, vision, enabled, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, kind, base_url.rstrip("/"), (api_key or None), model,
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
            v = str(v).rstrip("/")
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
    return bool(_candidate_chain(need_vision))


# ---------------------------------------------------------------------------
# Active summary (health / admin)
# ---------------------------------------------------------------------------
def active_summary() -> dict:
    text_id = _active_id(False)
    vis_id = _active_id(True)
    text = get_backend(text_id) if text_id else None
    vision = get_backend(vis_id) if vis_id else None
    return {
        "text": text,
        "vision": vision,
        "count": len(list_backends()),
        "enabled_count": len(list_backends(include_disabled=False)),
    }


def active_text_model() -> str | None:
    bid = _active_id(False)
    b = _get_backend_full(bid) if bid else None
    return b["model"] if b else None


# ---------------------------------------------------------------------------
# HTTP calls
# ---------------------------------------------------------------------------
def _headers(backend: dict) -> dict:
    h = {"Content-Type": "application/json"}
    if backend.get("api_key"):
        h["Authorization"] = f"Bearer {backend['api_key']}"
    return h


def _timeout(op: str = "chat") -> "httpx.Timeout":
    """Explicit httpx timeout with a short connect so a dead backend fails fast
    (letting failover kick in), and a per-operation read/total budget so a slow
    backend can't hang the request past the app's popup threshold.

    op: 'analyze' (vision, ~60s) | 'chat' (~30s) | 'passthrough' (chat budget).
    """
    from .config import config
    if op == "analyze":
        total = config.analyze_timeout
    elif op in ("chat", "passthrough"):
        total = config.chat_timeout
    else:
        total = config.vllm_timeout
    return httpx.Timeout(total, connect=config.connect_timeout)


async def _one_completion(backend: dict, messages: list[dict], max_tokens: int, temperature: float, op: str) -> str:
    base = backend["base_url"].rstrip("/")
    payload = {
        "model": backend["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=_timeout(op)) as client:
        r = await client.post(f"{base}/chat/completions", json=payload, headers=_headers(backend))
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"]


async def chat_completion(
    messages: list[dict], *, need_vision: bool = False, max_tokens: int = 700,
    temperature: float = 0.4, op: str = "chat",
) -> str:
    """Run a chat completion with failover across enabled capable backends."""
    chain = _candidate_chain(need_vision)
    if not chain:
        raise BackendUnavailable("no enabled backend with the needed capability")
    last_err: Exception | None = None
    for backend in chain:
        try:
            return await _one_completion(backend, messages, max_tokens, temperature, op)
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


async def analyze_image(image_bytes: bytes, mime: str, hint: str, portion: str) -> dict:
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
    content = await chat_completion(messages, need_vision=True, max_tokens=800, temperature=0.3, op="analyze")
    return _extract_json(content)


async def phrase_suggestions(remaining: dict, candidates: list[dict]) -> list[dict]:
    user = json.dumps({"remaining": remaining, "candidate_meals": candidates})
    messages = [
        {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    content = await chat_completion(messages, max_tokens=600, temperature=0.5)
    data = _extract_json(content)
    return data.get("suggestions", [])


# ---------------------------------------------------------------------------
# /llm/v1 passthrough — uses the active TEXT backend, pins its model.
# ---------------------------------------------------------------------------
def _active_text_backend_full() -> dict | None:
    bid = _active_id(False)
    return _get_backend_full(bid) if bid else None


async def passthrough_models() -> tuple[int, dict]:
    backend = _active_text_backend_full()
    if not backend:
        raise BackendUnavailable("no active text backend")
    base = backend["base_url"].rstrip("/")
    async with httpx.AsyncClient(timeout=_timeout("passthrough")) as client:
        r = await client.get(f"{base}/models", headers=_headers(backend))
        try:
            body = r.json()
        except Exception:
            body = {"data": [{"id": backend["model"], "object": "model"}]}
    return r.status_code, body


async def passthrough_chat_completions(payload: dict) -> tuple[int, dict]:
    backend = _active_text_backend_full()
    if not backend:
        raise BackendUnavailable("no active text backend")
    base = backend["base_url"].rstrip("/")
    body = dict(payload)
    body["model"] = backend["model"]  # pin active text backend's model
    mt = body.get("max_tokens")
    try:
        mt = int(mt) if mt is not None else LLM_MAX_TOKENS_CAP
    except (TypeError, ValueError):
        mt = LLM_MAX_TOKENS_CAP
    body["max_tokens"] = max(1, min(mt, LLM_MAX_TOKENS_CAP))
    async with httpx.AsyncClient(timeout=_timeout("passthrough")) as client:
        r = await client.post(f"{base}/chat/completions", json=body, headers=_headers(backend))
        try:
            data = r.json()
        except Exception:
            raise BackendUnavailable("backend returned non-JSON response")
    return r.status_code, data


# ---------------------------------------------------------------------------
# Health + admin test
# ---------------------------------------------------------------------------
async def check_health() -> tuple[bool, str | None]:
    """(connected, model) — best-effort ping of the active text backend."""
    backend = _active_text_backend_full()
    if not backend:
        return False, None
    base = backend["base_url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=3.0)) as client:
            r = await client.get(f"{base}/models", headers=_headers(backend))
            if r.status_code == 200:
                return True, backend["model"]
    except Exception:
        pass
    return False, backend["model"]


async def test_backend(bid: int) -> dict:
    """Ping a backend: list models + a 1-token completion. Returns a result dict."""
    backend = _get_backend_full(bid)
    if not backend:
        return {"ok": False, "error": "backend not found"}
    base = backend["base_url"].rstrip("/")
    result: dict = {"ok": False, "models": [], "ping": None, "error": None}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
            mr = await client.get(f"{base}/models", headers=_headers(backend))
            if mr.status_code == 200:
                try:
                    data = mr.json()
                    result["models"] = [m.get("id") for m in data.get("data", []) if m.get("id")]
                except Exception:
                    result["models"] = []
            # 1-token ping.
            pr = await client.post(
                f"{base}/chat/completions",
                json={"model": backend["model"],
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 1, "temperature": 0.0},
                headers=_headers(backend),
            )
            pr.raise_for_status()
            result["ping"] = "ok"
            result["ok"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)[:300]
    return result
