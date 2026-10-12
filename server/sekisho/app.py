"""Sekisho (関所) — FastAPI edge-AI gateway. Implements the API.md contract (v2..v4).

Runs unchanged in two places (API.md v4): the Docker container (``python -m
sekisho``) and a Vercel Python function (``api/index.py`` re-exports ``app``).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import random
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Form, Header, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from . import (
    accounts,
    backends,
    barcode as barcode_lookup,
    body_photo,
    chat_assistant,
    db,
    feedback,
    images,
    push,
    security,
    suggestions as sug,
)
from . import admin
from .accounts import AccountError
from .admin import router as admin_router
from .config import config
from pydantic import ValidationError

from .models import (
    DISH_MAX,
    Analysis,
    CalendarDay,
    CalendarOut,
    ChatIn,
    ChatV2Out,
    LoginIn,
    Meal,
    MealIn,
    MealsOut,
    PairClaimIn,
    PairClaimOut,
    ProfileIn,
    ProfileOut,
    PushSubscribeIn,
    RegisterIn,
    Suggestion,
    SuggestionsOut,
    Targets,
    Totals,
    WaterIn,
    WaterOut,
    WeightIn,
    WeightOut,
    Workout,
    WorkoutIn,
    valid_date,
)
from .mock_analyzer import analyze_mock
from .nutrition import compute_targets, eta_date

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_HINT_CHARS = 200
BARCODE_RE = re.compile(r"[0-9]{6,18}")
MONTH_RE = re.compile(r"[0-9]{4}-[0-9]{2}")

# Same 30/min default as v1, but now DB-backed (security.RateLimiter).
RATE_LIMIT_MSG = "requests/min exceeded for this device"


def warm_up() -> None:
    """Idempotent boot work: DB/migrations + backend registry bootstrap.
    Called from the lifespan AND eagerly by api/index.py (serverless).

    Keeper (phone-admin) is NEVER granted here: the only grant path is the web
    admin dashboard's Users toggle (no env-var grant that a username squatter
    could ride on the next cold start)."""
    db.ensure_init()
    try:
        backends.bootstrap()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app):
    # Bootstrap the backend registry from env and (Docker only) start the push
    # scheduler. On Vercel there is no background loop: /api/cron/tick drives it.
    try:
        warm_up()
    except Exception:
        pass
    if not config.on_vercel:
        try:
            push.start_scheduler()
        except Exception:
            pass
    try:
        yield
    finally:
        push.stop_scheduler()


app = FastAPI(title="Sekisho", version="2.0", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)


def err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


# ---------- request logging middleware ----------
# Stores ONLY method, path (no query string), status, device/user ids and timing.
# Never bodies: no image bytes, PINs, keys, passwords or chat text reach the log.
@app.middleware("http")
async def request_logger(request: Request, call_next):
    import time as _time
    _t0 = _time.perf_counter()
    response = await call_next(request)
    try:
        path = request.url.path
        dev = getattr(request.state, "device", None)
        anonymous_miss = (dev is None and response.status_code in (401, 404)
                          and not admin._valid_session(request.cookies.get(admin.COOKIE)))
        # Unauthenticated 401/404 hits (scanners, bad keys) are NOT logged: they
        # would let anyone flood the admin's request log for free.
        if not anonymous_miss and (path.startswith("/api/") or path.startswith("/admin")
                                   or path.startswith("/llm/")):
            duration_ms = int((_time.perf_counter() - _t0) * 1000)
            with db.cursor() as cur:
                cur.execute(
                    "INSERT INTO request_log "
                    "(device_id, device_name, method, path, status, at, user_id, duration_ms) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        dev["device_id"] if dev else None,
                        dev["name"] if dev else None,
                        request.method,
                        path,
                        response.status_code,
                        security.now_iso(),
                        dev.get("active_user_id") if dev else None,
                        duration_ms,
                    ),
                )
                # Prune to the last ~500 rows now and then (and on every cron tick)
                # instead of on every request — one less round-trip on Postgres.
                if random.random() < 0.05:
                    cur.execute(
                        "DELETE FROM request_log WHERE id NOT IN "
                        "(SELECT id FROM request_log ORDER BY id DESC LIMIT 500)"
                    )
    except Exception:
        pass
    return response


# ---------- request size cap (clean 413 envelope) ----------
_IMAGE_ROUTES = ("/api/v1/meals/analyze", "/api/v1/profile/body-photo")


@app.middleware("http")
async def body_size_limit(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl:
        try:
            too_big = int(cl) > config.max_body_bytes
        except ValueError:
            return err("bad_request", "Invalid Content-Length", 400)
        if too_big:
            if request.url.path in _IMAGE_ROUTES:
                return err("image_too_large",
                           f"Upload exceeds the {config.max_body_bytes // 1000} KB request limit", 413)
            return err("payload_too_large",
                       f"Request body exceeds {config.max_body_bytes // 1000} KB", 413)
    return await call_next(request)


# ---------- security headers on every function response ----------
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if "content-security-policy" not in h:
        h["Content-Security-Policy"] = _API_CSP
    path = request.url.path
    if path.startswith(("/api/", "/admin", "/llm/")) and "cache-control" not in h:
        h["Cache-Control"] = "no-store"
    return response


# ---------- auth dependencies ----------
class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int):
        self.code = code
        self.message = message
        self.status = status


async def require_device(request: Request, authorization: str = Header(default="")) -> dict:
    """Device-key auth only (used by pairing-adjacent + account endpoints)."""
    if not authorization.lower().startswith("bearer "):
        raise AuthError("missing_bearer", "Authorization: Bearer <device_key> required", 401)
    key = authorization.split(" ", 1)[1].strip()
    device = security.resolve_device(key)
    if device is None:
        raise AuthError("invalid_key", "Unknown or revoked device key", 401)
    if not security.rate_limit_ok(device["device_id"]):
        raise AuthError("rate_limited", f"{config.rate_limit_per_min} {RATE_LIMIT_MSG}", 429)
    request.state.device = device
    return device


async def require_user(device: dict = Depends(require_device)) -> dict:
    """Device auth PLUS an active user. Data endpoints require a logged-in user."""
    uid = device.get("active_user_id")
    if uid is None:
        raise AuthError("no_user", "No active user on this device — log in first", 401)
    device["user_id"] = uid
    return device


async def require_keeper(device: dict = Depends(require_device)) -> dict:
    """Device auth PLUS the active user holding the phone-admin ("keeper") flag.

    Enforced server-side on EVERY /keeper/* route: a valid device key is not
    enough — the device's currently logged-in user must be is_admin, else 403
    not_keeper. Never trust the client to gate this.
    """
    uid = device.get("active_user_id")
    if uid is None or not accounts.is_admin(uid):
        raise AuthError("not_keeper", "This device's active user is not a checkpoint keeper", 403)
    device["user_id"] = uid
    return device


@app.exception_handler(AuthError)
async def _auth_error_handler(_request: Request, exc: AuthError):
    return err(exc.code, exc.message, exc.status)


@app.exception_handler(AccountError)
async def _account_error_handler(_request: Request, exc: AccountError):
    return err(exc.code, exc.message, exc.status)


@app.exception_handler(images.ImageRejected)
async def _image_error_handler(_request: Request, exc: images.ImageRejected):
    return err(exc.code, exc.message, exc.status)


@app.exception_handler(db.ConfigError)
async def _config_error_handler(_request: Request, exc: db.ConfigError):
    return err("server_misconfigured", str(exc), 503)


_CAP_MSG = {
    "user": "Daily AI limit reached for this account ({cap}/day). Log manually or try tomorrow.",
    "device": "Daily AI limit reached for this device ({cap}/day). Log manually or try tomorrow.",
    "global": "The server's daily AI budget is used up. Log manually or try tomorrow.",
}


def _llm_cap_or_none(device: dict) -> JSONResponse | None:
    """Per-user + per-device + global daily caps on every call that spends LLM
    credits (analyze, body-photo, chat, insights, suggestions, /llm/v1)."""
    which = security.llm_caps_exceeded(device.get("active_user_id"), device.get("device_id"))
    if which:
        cap = {"user": config.daily_llm_cap, "device": config.device_daily_llm_cap,
               "global": config.global_daily_llm_cap}[which]
        return err("daily_cap", _CAP_MSG[which].format(cap=cap), 429)
    return None


def _bad_date(name: str) -> JSONResponse:
    return err("bad_date", f"{name} must be a YYYY-MM-DD date", 400)


# ================= CRON (serverless scheduler) =================
@app.get("/api/cron/tick")
async def cron_tick(request: Request):
    """One idempotent push-scheduler pass. Auth: ``Authorization: Bearer $CRON_SECRET``
    (constant-time compare). 401 without/with a wrong secret, and when no
    CRON_SECRET is configured (fail closed)."""
    secret = config.cron_secret
    problem = config.cron_secret_problem()
    if problem and secret:
        # Set but too short on Vercel: fail closed with a clear, actionable message.
        return err("server_misconfigured", problem, 503)
    sent = request.headers.get("authorization", "")
    expected = f"Bearer {secret}"
    if not secret or not hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
        return err("unauthorized", "Valid Authorization: Bearer <CRON_SECRET> required", 401)
    return await push.cron_tick()


# ================= HEALTH =================
def _health_detail_allowed(request: Request, authorization: str) -> bool:
    """Backend/model detail only for the web admin session or a keeper device."""
    if admin._valid_session(request.cookies.get(admin.COOKIE)):
        return True
    if authorization.lower().startswith("bearer "):
        device = security.resolve_device(authorization.split(" ", 1)[1].strip())
        if device is not None:
            request.state.device = device
            return accounts.is_admin(device.get("active_user_id"))
    return False


@app.get("/api/v1/health")
async def health(request: Request, authorization: str = Header(default="")):
    # Public liveness only: no provider/model/backend detail for anonymous callers.
    if not _health_detail_allowed(request, authorization):
        return {"status": "ok"}
    connected, model = await backends.check_health()
    summary = backends.active_summary()
    return {
        "status": "ok",
        "vllm": "connected" if connected else "mock",
        "model": model if connected else None,
        "backends": {
            "text": summary["text"],
            "vision": summary["vision"],
            "count": summary["count"],
            "enabled": summary["enabled_count"],
        },
    }


# ================= PAIRING =================
@app.post("/api/v1/pair/claim", status_code=201)
async def pair_claim(body: PairClaimIn):
    security.purge_expired_tokens()
    result = security.claim_pairing_token(body.token, body.device_name)
    if result is None:
        return err("invalid_token", "Pairing token invalid, expired, or already used", 400)
    device_id, device_key = result
    return PairClaimOut(device_id=device_id, device_key=device_key).model_dump()


# ================= ACCOUNTS (v2) =================
@app.post("/api/v1/auth/register", status_code=201)
async def auth_register(body: RegisterIn, device: dict = Depends(require_device)):
    user = accounts.register(body.username, body.pin, device["device_id"])
    # New active user on this phone: the previous account's reminders stop here.
    push.unsubscribe_device(device["device_id"], keep_user_id=user["id"])
    security.set_active_user(device["device_id"], user["id"])
    # Optional initial profile.
    if body.profile is not None:
        _save_profile(user["id"], body.profile)
    return {"user": user}


@app.post("/api/v1/auth/login")
async def auth_login(body: LoginIn, device: dict = Depends(require_device)):
    user = accounts.login(body.username, body.pin, device["device_id"])
    if device.get("active_user_id") != user["id"]:
        # Account switch: drop the previous user's push subscription on this device.
        push.unsubscribe_device(device["device_id"], keep_user_id=user["id"])
    security.set_active_user(device["device_id"], user["id"])
    profile = _load_profile_out(user["id"])
    return {"user": user, "profile": profile.model_dump() if profile else None}


@app.post("/api/v1/auth/logout")
async def auth_logout(device: dict = Depends(require_device)):
    push.unsubscribe_device(device["device_id"])
    security.set_active_user(device["device_id"], None)
    return {"ok": True}


@app.get("/api/v1/auth/me")
async def auth_me(device: dict = Depends(require_device)):
    uid = device.get("active_user_id")
    if uid is None:
        return err("no_user", "No active user on this device — log in first", 401)
    user = accounts.get_user(uid)
    if user is None:
        security.set_active_user(device["device_id"], None)
        return err("no_user", "Active user no longer exists", 401)
    profile = _load_profile_out(uid)
    return {"user": user, "profile": profile.model_dump() if profile else None}


# ================= ANALYZE =================
def _sum_item_nutrients(analysis: Analysis) -> Analysis:
    """Ensure the top-level fiber/sugar/sodium reflect the sum of the items'
    values (None when no item reports that nutrient), so the contract's
    'top-level = sum' rule holds even if a model returns inconsistent numbers."""
    for field in ("fiber_g", "sugar_g", "sodium_mg"):
        vals = [getattr(i, field) for i in analysis.items if getattr(i, field) is not None]
        setattr(analysis, field, sum(vals) if vals else None)
    return analysis


@app.post("/api/v1/meals/analyze")
async def analyze(
    device: dict = Depends(require_user),
    image: UploadFile = File(...),
    hint: str = Form(default=""),
    portion: str = Form(default="regular"),
):
    data = await image.read()
    if not data:
        return err("empty_image", "No image data received", 400)
    if len(data) > MAX_IMAGE_BYTES:
        return err("image_too_large", "Image exceeds 8MB limit", 413)
    if len(hint) > MAX_HINT_CHARS:
        return err("bad_request", f"hint must be at most {MAX_HINT_CHARS} characters", 400)
    if portion not in ("small", "regular", "large"):
        portion = "regular"
    # magic bytes + Pillow parse (never trust content-type); CPU-bound -> thread
    mime = await asyncio.to_thread(images.validate, data)

    if backends.is_configured(need_vision=True):
        capped = _llm_cap_or_none(device)
        if capped:
            return capped
        try:
            raw = await backends.analyze_image(data, mime, hint, portion)
            analysis = Analysis.model_validate(raw)
            analysis = _sum_item_nutrients(analysis)
            return analysis.model_dump()
        except Exception:
            pass  # fall through to mock

    if not config.allow_mock:
        return err("vllm_unavailable", "Vision model unreachable — please retry", 503)

    analysis = analyze_mock(data, hint, portion)
    return analysis.model_dump()


# ================= BARCODE / PACKAGED FOOD =================
@app.get("/api/v1/food/barcode/{code}")
async def food_barcode(code: str, device: dict = Depends(require_user)):
    if not BARCODE_RE.fullmatch(code):
        return err("bad_barcode", "Barcode must be 6-18 digits", 400)
    result = await barcode_lookup.lookup(code)
    return result.model_dump()


# ================= MEALS =================
def _row_to_meal(r) -> Meal:
    keys = r.keys()
    return Meal(
        id=r["id"], dish=r["dish"], calories=r["calories"], protein_g=r["protein_g"],
        carbs_g=r["carbs_g"], fat_g=r["fat_g"],
        fiber_g=r["fiber_g"] if "fiber_g" in keys else None,
        sugar_g=r["sugar_g"] if "sugar_g" in keys else None,
        sodium_mg=r["sodium_mg"] if "sodium_mg" in keys else None,
        eaten_at=r["eaten_at"], source=r["source"],
        favorite=bool(r["favorite"]) if "favorite" in keys else False,
    )


def _log_meal(user_id: int, dish: str, calories: int, protein_g: int, carbs_g: int,
              fat_g: int, eaten_at: str, source: str,
              fiber_g: int | None = None, sugar_g: int | None = None,
              sodium_mg: int | None = None) -> Meal:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO meals (user_id, dish, calories, protein_g, carbs_g, fat_g, "
            "fiber_g, sugar_g, sodium_mg, eaten_at, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, dish, calories, protein_g, carbs_g, fat_g,
             fiber_g, sugar_g, sodium_mg, eaten_at, source),
        )
        meal_id = cur.lastrowid
        row = cur.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
    feedback.cache_invalidate(user_id, "sug:")
    return _row_to_meal(row)


@app.post("/api/v1/meals", status_code=201)
async def create_meal(body: MealIn, device: dict = Depends(require_user)):
    eaten_at = body.eaten_at or security.now_iso()
    meal = _log_meal(device["user_id"], body.dish, body.calories, body.protein_g,
                     body.carbs_g, body.fat_g, eaten_at, body.source,
                     body.fiber_g, body.sugar_g, body.sodium_mg)
    return meal.model_dump()


def _invalidate_suggestions(user_id: int) -> None:
    """A logged/deleted meal changes what's left today: drop cached phrasing."""
    feedback.cache_invalidate(user_id, "sug:")


def _burned_between(user_id: int, start: str, end: str) -> int:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT COALESCE(SUM(calories_burned),0) AS b FROM workouts "
            "WHERE user_id=? AND done_at>=? AND done_at<?",
            (user_id, start, end),
        ).fetchone()
    return int(row["b"])


@app.get("/api/v1/meals")
async def list_meals(request: Request, device: dict = Depends(require_user)):
    user_id = device["user_id"]
    date = request.query_params.get("date")
    from_ = request.query_params.get("from")
    to = request.query_params.get("to")
    for name, val in (("date", date), ("from", from_), ("to", to)):
        if val is not None and not valid_date(val):
            return _bad_date(name)
    if date:
        start, end = f"{date}T00:00:00", f"{date}T23:59:59.999999"
        water_day = date
    elif from_ and to:
        start, end = f"{from_}T00:00:00", f"{to}T23:59:59.999999"
        water_day = to
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start, end = f"{today}T00:00:00", f"{today}T23:59:59.999999"
        water_day = today

    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM meals WHERE user_id=? AND eaten_at>=? AND eaten_at<=? ORDER BY eaten_at ASC",
            (user_id, start, end),
        ).fetchall()

    meals = [_row_to_meal(r) for r in rows]
    totals = Totals(
        calories=sum(m.calories for m in meals),
        protein_g=sum(m.protein_g for m in meals),
        carbs_g=sum(m.carbs_g for m in meals),
        fat_g=sum(m.fat_g for m in meals),
        burned=_burned_between(user_id, start, end + "Z"),
        fiber_g=sum(m.fiber_g or 0 for m in meals),
        sugar_g=sum(m.sugar_g or 0 for m in meals),
        sodium_mg=sum(m.sodium_mg or 0 for m in meals),
        water_ml=feedback.water_for_day(user_id, water_day),
        water_target_ml=feedback.water_target_ml(user_id),
    )
    return MealsOut(meals=meals, totals=totals).model_dump()


@app.delete("/api/v1/meals/{meal_id}", status_code=204)
async def delete_meal(meal_id: int, device: dict = Depends(require_user)):
    with db.cursor() as cur:
        cur.execute("DELETE FROM meals WHERE id=? AND user_id=?", (meal_id, device["user_id"]))
    _invalidate_suggestions(device["user_id"])
    return Response(status_code=204)


# ---- quick-log: favorites + recent (v3) ----
@app.post("/api/v1/meals/{meal_id}/favorite")
async def favorite_meal(meal_id: int, device: dict = Depends(require_user)):
    if not feedback.set_favorite(device["user_id"], meal_id, True):
        return err("not_found", "No such meal for this user", 404)
    return {"ok": True, "id": meal_id, "favorite": True}


@app.delete("/api/v1/meals/{meal_id}/favorite")
async def unfavorite_meal(meal_id: int, device: dict = Depends(require_user)):
    if not feedback.set_favorite(device["user_id"], meal_id, False):
        return err("not_found", "No such meal for this user", 404)
    return {"ok": True, "id": meal_id, "favorite": False}


@app.get("/api/v1/meals/recent")
async def meals_recent(device: dict = Depends(require_user)):
    return {"recent": feedback.recent_dishes(device["user_id"], limit=20)}


# ================= PROFILE =================
def _save_profile(user_id: int, body: ProfileIn) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO profiles (user_id, name, age, sex, height_cm, weight_kg, activity, goal, "
            "body_fat_pct, body_fat_goal_pct, weight_goal_kg, pace_kg_per_week, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, age=excluded.age, sex=excluded.sex, "
            "height_cm=excluded.height_cm, weight_kg=excluded.weight_kg, activity=excluded.activity, "
            "goal=excluded.goal, body_fat_pct=excluded.body_fat_pct, "
            "body_fat_goal_pct=excluded.body_fat_goal_pct, weight_goal_kg=excluded.weight_goal_kg, "
            "pace_kg_per_week=excluded.pace_kg_per_week, updated_at=excluded.updated_at",
            (user_id, body.name, body.age, body.sex, body.height_cm, body.weight_kg,
             body.activity, body.goal, body.body_fat_pct, body.body_fat_goal_pct,
             body.weight_goal_kg, body.pace_kg_per_week, security.now_iso()),
        )


def _profile_in(user_id: int) -> ProfileIn | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return None
    keys = row.keys()
    return ProfileIn(
        name=row["name"] or "", age=row["age"], sex=row["sex"], height_cm=row["height_cm"],
        weight_kg=row["weight_kg"], activity=row["activity"], goal=row["goal"],
        body_fat_pct=row["body_fat_pct"], body_fat_goal_pct=row["body_fat_goal_pct"],
        weight_goal_kg=row["weight_goal_kg"] if "weight_goal_kg" in keys else None,
        pace_kg_per_week=row["pace_kg_per_week"] if "pace_kg_per_week" in keys else None,
    )


def _load_profile_out(user_id: int) -> ProfileOut | None:
    p = _profile_in(user_id)
    if p is None:
        return None
    return ProfileOut(**p.model_dump(), targets=compute_targets(p), eta_date=eta_date(p))


@app.put("/api/v1/profile")
async def put_profile(body: ProfileIn, device: dict = Depends(require_user)):
    _save_profile(device["user_id"], body)
    targets = compute_targets(body)
    return ProfileOut(**body.model_dump(), targets=targets, eta_date=eta_date(body)).model_dump()


@app.get("/api/v1/profile")
async def get_profile(device: dict = Depends(require_user)):
    out = _load_profile_out(device["user_id"])
    if out is None:
        return err("no_profile", "No profile set for this user yet", 404)
    return out.model_dump()


@app.post("/api/v1/profile/body-photo")
async def profile_body_photo(device: dict = Depends(require_user), image: UploadFile = File(...)):
    data = await image.read()
    if not data:
        return err("empty_image", "No image data received", 400)
    if len(data) > MAX_IMAGE_BYTES:
        return err("image_too_large", "Image exceeds 8MB limit", 413)
    mime = await asyncio.to_thread(images.validate, data)
    if backends.is_configured(need_vision=True):
        capped = _llm_cap_or_none(device)
        if capped:
            return capped
    try:
        estimate, source = await body_photo.estimate(data, mime)
    except backends.BackendUnavailable:
        return err("vllm_unavailable", "Vision model unreachable — please retry", 503)
    # Photo is NOT stored — only the estimate is returned.
    out = estimate.model_dump()
    out["source"] = source
    return out


def _get_targets(user_id: int) -> Targets | None:
    p = _profile_in(user_id)
    return compute_targets(p) if p else None


# ================= WEIGHT (v3) =================
@app.post("/api/v1/weight", status_code=201)
async def post_weight(body: WeightIn, device: dict = Depends(require_user)):
    day = body.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    feedback.log_weight(device["user_id"], body.weight_kg, day)
    return {"date": day, "weight_kg": body.weight_kg}


@app.get("/api/v1/weight")
async def get_weight(request: Request, device: dict = Depends(require_user)):
    frm = request.query_params.get("from")
    to = request.query_params.get("to")
    for name, val in (("from", frm), ("to", to)):
        if val is not None and not valid_date(val):
            return _bad_date(name)
    data = feedback.weight_history(device["user_id"], frm, to)
    return WeightOut(**data).model_dump()


# ================= WATER (v3) =================
@app.post("/api/v1/water", status_code=201)
async def post_water(body: WaterIn, device: dict = Depends(require_user)):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_total = feedback.add_water(device["user_id"], body.ml, day)
    return WaterOut(date=day, water_ml=new_total,
                    water_target_ml=feedback.water_target_ml(device["user_id"])).model_dump()


# ================= WORKOUTS =================
def _log_workout(user_id: int, kind: str, minutes: int, calories_burned: int, note: str | None) -> Workout:
    done_at = security.now_iso()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO workouts (user_id, kind, minutes, calories_burned, note, done_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, kind, minutes, calories_burned, note, done_at),
        )
        wid = cur.lastrowid
        row = cur.execute("SELECT * FROM workouts WHERE id=?", (wid,)).fetchone()
    return Workout(id=row["id"], kind=row["kind"], minutes=row["minutes"],
                   calories_burned=row["calories_burned"], note=row["note"], done_at=row["done_at"])


@app.post("/api/v1/workouts", status_code=201)
async def create_workout(body: WorkoutIn, device: dict = Depends(require_user)):
    w = _log_workout(device["user_id"], body.kind, body.minutes, body.calories_burned, body.note)
    return w.model_dump()


@app.get("/api/v1/workouts")
async def list_workouts(device: dict = Depends(require_user), date: str | None = None):
    if date is not None and not valid_date(date):
        return _bad_date("date")
    if date:
        start, end = f"{date}T00:00:00", f"{date}T23:59:59.999999"
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start, end = f"{today}T00:00:00", f"{today}T23:59:59.999999"
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM workouts WHERE user_id=? AND done_at>=? AND done_at<=? ORDER BY done_at ASC",
            (device["user_id"], start, end),
        ).fetchall()
    workouts = [
        Workout(id=r["id"], kind=r["kind"], minutes=r["minutes"], calories_burned=r["calories_burned"],
                note=r["note"], done_at=r["done_at"]).model_dump()
        for r in rows
    ]
    return {"workouts": workouts}


# ================= SUGGESTIONS =================
@app.get("/api/v1/suggestions")
async def get_suggestions(device: dict = Depends(require_user), date: str | None = None):
    user_id = device["user_id"]
    if date is not None and not valid_date(date):
        return _bad_date("date")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day = date or today
    start, end = f"{day}T00:00:00", f"{day}T23:59:59.999999"

    targets = _get_targets(user_id)
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT COALESCE(SUM(calories),0) c, COALESCE(SUM(protein_g),0) p, "
            "COALESCE(SUM(carbs_g),0) cb, COALESCE(SUM(fat_g),0) f FROM meals "
            "WHERE user_id=? AND eaten_at>=? AND eaten_at<=?",
            (user_id, start, end),
        ).fetchone()
    burned = _burned_between(user_id, start, end + "Z")

    if targets is None:
        remaining = Targets(calories=0, protein_g=0, carbs_g=0, fat_g=0)
    else:
        remaining = Targets(
            calories=max(0, targets.calories + burned - row["c"]),
            protein_g=max(0, targets.protein_g - row["p"]),
            carbs_g=max(0, targets.carbs_g - row["cb"]),
            fat_g=max(0, targets.fat_g - row["f"]),
        )

    candidates = sug.rank_candidates(user_id, remaining, limit=4)
    suggestions: list[Suggestion] = []

    if candidates and backends.is_configured():
        # Cached per user per day (invalidated whenever a meal is logged/deleted),
        # so reopening the screen never re-spends LLM credits.
        cache_key = f"sug:{day}"
        cached = feedback.cache_get(user_id, cache_key, today)
        phrased = None
        if cached is not None:
            try:
                phrased = json.loads(cached)
            except ValueError:
                phrased = None
        if phrased is None:
            capped = _llm_cap_or_none(device)
            if capped:
                return capped
        try:
            if phrased is None:
                phrased = await backends.phrase_suggestions(remaining.model_dump(), candidates)
                if isinstance(phrased, list) and phrased:
                    feedback.cache_put(user_id, cache_key, today, json.dumps(phrased[:4]))
            for p in phrased[:4]:
                suggestions.append(Suggestion(
                    dish=str(p.get("dish", "")),
                    calories=int(p.get("calories", 0)),
                    protein_g=int(p.get("protein_g", 0)),
                    carbs_g=int(p.get("carbs_g", 0)),
                    fat_g=int(p.get("fat_g", 0)),
                    reason=str(p.get("reason", "")),
                ))
        except Exception:
            suggestions = []

    if not suggestions:
        suggestions = sug.rule_based_suggestions(user_id, remaining, limit=4)

    return SuggestionsOut(remaining=remaining, suggestions=suggestions).model_dump()


# ================= CALENDAR =================
@app.get("/api/v1/calendar")
async def calendar(device: dict = Depends(require_user), month: str | None = None):
    user_id = device["user_id"]
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    if not MONTH_RE.fullmatch(month):
        return err("bad_month", "month must be YYYY-MM", 400)
    year, mon = (int(x) for x in month.split("-"))
    if not (1 <= mon <= 12 and 1 <= year <= 9999):
        return err("bad_month", "month must be YYYY-MM", 400)

    import calendar as _cal

    days_in_month = _cal.monthrange(year, mon)[1]
    targets = _get_targets(user_id)
    target_cal = targets.calories if targets else 0

    with db.cursor() as cur:
        meal_rows = cur.execute(
            "SELECT substr(eaten_at,1,10) d, SUM(calories) c FROM meals "
            "WHERE user_id=? AND substr(eaten_at,1,7)=? GROUP BY d",
            (user_id, month),
        ).fetchall()
        wk_rows = cur.execute(
            "SELECT substr(done_at,1,10) d, SUM(calories_burned) b FROM workouts "
            "WHERE user_id=? AND substr(done_at,1,7)=? GROUP BY d",
            (user_id, month),
        ).fetchall()
    cal_by_day = {r["d"]: int(r["c"]) for r in meal_rows}
    burn_by_day = {r["d"]: int(r["b"]) for r in wk_rows}

    days: list[CalendarDay] = []
    for d in range(1, days_in_month + 1):
        date_str = f"{year:04d}-{mon:02d}-{d:02d}"
        cals = cal_by_day.get(date_str, 0)
        burned = burn_by_day.get(date_str, 0)
        if cals == 0 and burned == 0:
            status = "empty"
        elif target_cal <= 0:
            status = "on"
        else:
            net_target = target_cal + burned
            ratio = cals / net_target if net_target else 0
            if ratio < 0.9:
                status = "under"
            elif ratio <= 1.1:
                status = "on"
            else:
                status = "over"
        days.append(CalendarDay(date=date_str, calories=cals, target=target_cal, burned=burned, status=status))
    return CalendarOut(days=days).model_dump()


# ================= CHAT (Annai v2) =================
def _num(v) -> int:
    """An LLM-emitted number (int, float or numeric string) -> int; raises on junk."""
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        raise ValueError("not a number")
    return int(round(float(v)))


def _meal_from_action(action: dict) -> MealIn | None:
    """Validate a chat-emitted log_meal through the SAME model/bounds as
    POST /meals (dish <= 120 chars, 0..5000 kcal, macro caps). Out-of-bounds or
    malformed -> None (rejected, never logged or proposed)."""
    try:
        return MealIn(
            dish=str(action.get("dish") or "meal").strip(),
            calories=_num(action.get("calories")),
            protein_g=_num(action.get("protein_g")),
            carbs_g=_num(action.get("carbs_g")),
            fat_g=_num(action.get("fat_g")),
            source="suggestion",
        )
    except (ValidationError, ValueError, TypeError, OverflowError):
        return None


def _workout_from_action(action: dict) -> WorkoutIn | None:
    """Same bounds as POST /workouts (minutes <= 600, burned <= 5000)."""
    try:
        return WorkoutIn(
            kind=str(action.get("kind") or "workout").strip(),
            minutes=_num(action.get("minutes")),
            calories_burned=_num(action.get("calories_burned")),
        )
    except (ValidationError, ValueError, TypeError, OverflowError):
        return None


def _proposed_action(action: dict) -> dict | None:
    """Normalize a PROPOSED action (a recommendation) into the chip shape the app
    confirms. It is NOT executed server-side — the app logs it only if the user
    accepts. Only log_meal recommendations are proposable, and only within the
    POST /meals bounds."""
    if action.get("action") != "log_meal":
        return None
    meal = _meal_from_action(action)
    if meal is None:
        return None
    return {
        "action": "log_meal",
        "proposed": True,
        "dish": meal.dish,
        "calories": meal.calories,
        "protein_g": meal.protein_g,
        "carbs_g": meal.carbs_g,
        "fat_g": meal.fat_g,
    }


def _execute_action(user_id: int, action: dict) -> dict | None:
    """Execute a parsed chat action server-side. Returns the normalized action
    that was performed (with any server-filled fields), or None if invalid or
    out of bounds (rejected, not clamped)."""
    kind = action.get("action")
    try:
        if kind == "log_meal":
            m = _meal_from_action(action)
            if m is None:
                return None
            meal = _log_meal(user_id, m.dish, m.calories, m.protein_g, m.carbs_g, m.fat_g,
                             security.now_iso(), "suggestion")
            return {"action": "log_meal", "meal": meal.model_dump()}
        if kind == "log_workout":
            wk = _workout_from_action(action)
            if wk is None:
                return None
            w = _log_workout(user_id, wk.kind, wk.minutes, wk.calories_burned, None)
            return {"action": "log_workout", "workout": w.model_dump()}
        if kind == "set_goal":
            goal = action.get("goal")
            if goal not in ("cut", "maintain", "bulk"):
                return None
            p = _profile_in(user_id)
            if p is None:
                return {"action": "set_goal", "goal": goal, "applied": False,
                        "note": "no profile yet"}
            p.goal = goal
            _save_profile(user_id, p)
            return {"action": "set_goal", "goal": goal, "applied": True}
    except Exception:
        return None
    return None


@app.post("/api/v1/chat")
async def chat(body: ChatIn, device: dict = Depends(require_user)):
    user_id = device["user_id"]
    msgs = [m.model_dump() for m in body.messages][-12:]

    reply: str
    action: dict | None
    source: str

    if backends.is_configured():
        capped = _llm_cap_or_none(device)
        if capped:
            return capped
        try:
            reply, action = await chat_assistant.backend_reply(user_id, msgs)
            source = "vllm"
        except Exception:
            # Backend failed — mock only if allowed, else fail fast + clean.
            if not config.allow_mock:
                return err("vllm_unavailable", "Chat model unreachable and mock disabled", 503)
            reply, action = chat_assistant.mock_reply(user_id, msgs)
            source = "mock"
    else:
        if not config.allow_mock:
            return err("vllm_unavailable", "Chat model unreachable and mock disabled", 503)
        reply, action = chat_assistant.mock_reply(user_id, msgs)
        source = "mock"

    performed = None
    if action:
        if action.get("proposed"):
            # Recommendation — surface a confirm chip; do NOT log server-side.
            performed = _proposed_action(action)
        else:
            performed = _execute_action(user_id, action)
            if performed is None and action.get("action") in ("log_meal", "log_workout"):
                reply = (reply.strip() + "\n\n(I didn't log that — the details looked out of "
                         "range. Please log it manually.)")

    return ChatV2Out(reply=reply.strip()[:4000], action=performed, source=source).model_dump()


# ================= PUSH (v2) =================
@app.get("/api/v1/push/vapid-public-key")
async def push_vapid_key():
    # No auth — the app needs this before subscribing.
    try:
        return {"public_key": push.vapid_public_key()}
    except push.PushUnconfigured:
        return err("push_unconfigured", "Push notifications are not configured on this server", 503)


@app.post("/api/v1/push/subscribe", status_code=201)
async def push_subscribe(body: PushSubscribeIn, device: dict = Depends(require_user)):
    try:
        push.subscribe(device["user_id"], device["device_id"], body.subscription, body.tz_offset_min)
    except push.BadSubscription as e:
        return err("bad_subscription", str(e), 400)
    return {"ok": True}


@app.delete("/api/v1/push/subscribe", status_code=204)
async def push_unsubscribe(device: dict = Depends(require_user)):
    push.unsubscribe(device["user_id"], device["device_id"])
    return Response(status_code=204)


# ================= STREAKS (v3) =================
@app.get("/api/v1/streaks")
async def get_streaks(device: dict = Depends(require_user)):
    return feedback.streaks(device["user_id"])


# ================= INSIGHTS (v3) =================
@app.get("/api/v1/insights")
async def get_insights(request: Request, device: dict = Depends(require_user)):
    user_id = device["user_id"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week = request.query_params.get("week") or today
    if not valid_date(week):
        return err("bad_week", "week must be a YYYY-MM-DD date", 400)
    try:
        data = feedback.insights(user_id, week)
    except ValueError:
        return err("bad_week", "week must be a YYYY-MM-DD date", 400)

    summary = None
    if backends.is_configured():
        # One LLM summary per user per week per day (cached), capped like chat.
        cache_key = f"ins:{data.get('week_start', week)}"
        summary = feedback.cache_get(user_id, cache_key, today)
        if summary is None:
            capped = _llm_cap_or_none(device)
            if capped:
                return capped
            try:
                msgs = feedback.insights_prompt(data)
                raw = await backends.chat_completion(msgs, max_tokens=200, temperature=0.6, task="insights")
                summary = raw.strip()[:600]
                if summary:
                    feedback.cache_put(user_id, cache_key, today, summary)
            except Exception:
                summary = None
    if not summary:
        summary = feedback.insights_summary(data)
    data["summary"] = summary
    return data


# ================= EXPORT (v3) =================
@app.get("/api/v1/export.csv")
async def export_csv(device: dict = Depends(require_user)):
    csv_text = feedback.export_csv(device["user_id"])
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="harahachi_export.csv"'},
    )


# ================= LLM PASSTHROUGH (/llm/v1/*) =================
@app.get("/llm/v1/models")
async def llm_models(device: dict = Depends(require_device)):
    if backends.active_text_model() is None:
        return err("vllm_unavailable", "No active text backend is configured", 503)
    try:
        status, body = await backends.passthrough_models()
    except Exception:
        return err("vllm_unavailable", "Text backend is unreachable", 503)
    return JSONResponse(status_code=status, content=body)


@app.post("/llm/v1/chat/completions")
async def llm_chat_completions(request: Request, device: dict = Depends(require_device)):
    if backends.active_text_model() is None:
        return err("vllm_unavailable", "No active text backend is configured", 503)
    try:
        payload = await request.json()
    except Exception:
        return err("bad_request", "Body must be JSON", 400)
    if not isinstance(payload, dict):
        return err("bad_request", "Body must be a JSON object", 400)
    if payload.get("stream"):
        return err("streaming_unsupported", "Streaming responses are not supported on this proxy", 400)
    problem = backends.validate_passthrough_payload(payload)
    if problem:
        return err("bad_request", problem, 400)
    capped = _llm_cap_or_none(device)
    if capped:
        return capped
    try:
        status, body = await backends.passthrough_chat_completions(payload)
    except Exception:
        return err("vllm_unavailable", "Text backend is unreachable", 503)
    return JSONResponse(status_code=status, content=body)


# ================= KEEPER (phone-admin) =================
# Device-key-authorized admin surface. Same Bearer device-key auth as the rest
# of /api/v1, but require_keeper adds a hard is_admin gate (403 not_keeper).
# The heavy lifting is shared with the /admin/api/* session routes (see admin.py)
# so the two never drift.
@app.post("/api/v1/keeper/pair")
async def keeper_pair(device: dict = Depends(require_keeper)):
    return admin.pairing_with_qr_png()


@app.get("/api/v1/keeper/overview")
async def keeper_overview(device: dict = Depends(require_keeper)):
    return await admin.keeper_overview()


@app.get("/api/v1/keeper/users/{user_id}")
async def keeper_user_detail(user_id: int, device: dict = Depends(require_keeper)):
    data = admin.user_detail_data(user_id)
    if data is None:
        return err("not_found", "No such user", 404)
    return data


@app.post("/api/v1/keeper/devices/{device_id}/revoke")
async def keeper_revoke(device_id: str, device: dict = Depends(require_keeper)):
    admin.revoke_device(device_id)
    return {"ok": True}


@app.get("/api/v1/keeper/backends")
async def keeper_backends(device: dict = Depends(require_keeper)):
    return admin.backends_payload()


@app.post("/api/v1/keeper/backends/active")
async def keeper_backends_active(
    slot: str = Form(...), backend_id: str = Form(...),
    device: dict = Depends(require_keeper),
):
    err_code = admin.set_active_backend(slot, backend_id)
    if err_code:
        return err(err_code, "slot must be text|vision", 400)
    return {"ok": True}


# mount admin
app.include_router(admin_router)

# CORS last => OUTERMOST middleware, so even 413/429/401 envelopes carry the
# CORS headers the APK (capacitor://localhost / https://localhost) needs to read
# them. Bearer-key auth, no cookies cross-origin => allow_credentials=False.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    max_age=600,
)
