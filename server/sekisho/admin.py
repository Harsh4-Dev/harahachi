"""Admin dashboard v2: /admin — server-rendered HTML following DESIGN.md.

Glass-tab navigation over cards/pages:
  * Status   — tunnel/public URL, active backends, mock mode, uptime, IPs
  * Users    — list + drill-down (profile, targets, body-fat, recent meals + totals)
  * Devices  — paired devices, last seen, active user, revoke, pairing QR
  * Backends — registry CRUD (with presets), active vision/text switches, test
  * Requests — last 200 log rows, filterable client-side
  * Push     — subscription count, send a test notification

Endpoints (all admin-session guarded except the login/QR image):
  GET  /admin                         -> login or dashboard
  POST /admin/login  /admin/logout
  GET  /admin/api/state               -> status + devices + logs + ips + push
  POST /admin/api/pair                -> new pairing token
  GET  /admin/api/qr                  -> PNG QR for a token
  POST /admin/api/revoke              -> revoke a device
  GET  /admin/api/users               -> user list (with counts + last activity)
  GET  /admin/api/users/{id}          -> user drill-down (profile/targets/meals)
  GET  /admin/api/backends            -> registry list + active pointers + presets
  POST /admin/api/backends            -> create
  POST /admin/api/backends/{id}       -> update
  POST /admin/api/backends/{id}/delete
  POST /admin/api/backends/{id}/test  -> ping + models
  POST /admin/api/backends/active     -> switch active vision/text
  GET  /admin/api/push                -> subscription count
  POST /admin/api/push/test           -> send one test push now
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import secrets
import time
from datetime import datetime, timezone

import qrcode
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from . import accounts, backends, db, feedback, push, security
from .config import config
from .net import best_pairing_host, classify_ips, public_url
from .nutrition import compute_targets
from .templates import LOGIN_HTML, dashboard_html

router = APIRouter()


# ===========================================================================
# Shared internals — reused by BOTH the session-guarded /admin/api/* routes
# and the device-key-guarded /api/v1/keeper/* routes (see app.py). Keep the
# actual work here so the two surfaces never drift apart.
# ===========================================================================
def make_pairing() -> dict:
    """Mint a single-use pairing token + its JSON QR payload."""
    security.purge_expired_tokens()
    token, ttl = security.create_pairing_token()
    url = public_url(config.port)
    payload = json.dumps({"v": 1, "url": url, "token": token})
    return {"token": token, "url": url, "expires_in": ttl, "qr_payload": payload}


def qr_png_base64(token: str) -> str:
    """Base64-encoded PNG of the pairing QR for a token (no data: prefix)."""
    url = public_url(config.port)
    payload = json.dumps({"v": 1, "url": url, "token": token})
    img = qrcode.make(payload)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def pairing_with_qr_png() -> dict:
    """Keeper-flavoured pairing: {token, url, qr_png_base64, expires_in}."""
    p = make_pairing()
    return {
        "token": p["token"],
        "url": p["url"],
        "qr_png_base64": qr_png_base64(p["token"]),
        "expires_in": p["expires_in"],
    }


def revoke_device(device_id: str) -> None:
    with db.cursor() as cur:
        cur.execute("UPDATE devices SET revoked=1 WHERE device_id=?", (device_id,))


def backends_payload() -> dict:
    summary = backends.active_summary()
    return {
        "backends": backends.list_backends(),
        "active_text_id": summary["text"]["id"] if summary["text"] else None,
        "active_vision_id": summary["vision"]["id"] if summary["vision"] else None,
        "presets": backends.PRESETS,
    }


def set_active_backend(slot: str, backend_id: str) -> str | None:
    """Point an active slot (text|vision) at a backend id (or clear it).

    Returns an error code string on bad input, else None on success.
    """
    if slot not in ("text", "vision"):
        return "bad_slot"
    try:
        bid = int(backend_id) if backend_id not in ("", "none", "null") else None
    except ValueError:
        bid = None
    backends.set_active(slot, bid)
    return None


def users_list_data() -> list[dict]:
    """User list with counts, goal, last activity, and the phone-admin flag."""
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT u.id, u.username, u.created_at, u.is_admin, "
            "(SELECT COUNT(*) FROM meals m WHERE m.user_id=u.id) AS meal_count, "
            "(SELECT COUNT(*) FROM workouts w WHERE w.user_id=u.id) AS workout_count, "
            "(SELECT MAX(eaten_at) FROM meals m WHERE m.user_id=u.id) AS last_meal, "
            "(SELECT goal FROM profiles p WHERE p.user_id=u.id) AS goal "
            "FROM users u ORDER BY u.id"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_admin"] = bool(d["is_admin"])
        out.append(d)
    return out


def user_detail_data(user_id: int) -> dict | None:
    """Full drill-down for one user: profile, targets, recent meals, day totals.

    Returns None if the user doesn't exist.
    """
    with db.cursor() as cur:
        u = cur.execute(
            "SELECT id, username, created_at, is_admin FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if u is None:
            return None
        prow = cur.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        meals = [
            dict(r)
            for r in cur.execute(
                "SELECT dish, calories, protein_g, carbs_g, fat_g, eaten_at, source FROM meals "
                "WHERE user_id=? ORDER BY eaten_at DESC LIMIT 20",
                (user_id,),
            ).fetchall()
        ]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trow = cur.execute(
            "SELECT COALESCE(SUM(calories),0) c, COALESCE(SUM(protein_g),0) p, "
            "COALESCE(SUM(carbs_g),0) cb, COALESCE(SUM(fat_g),0) f FROM meals "
            "WHERE user_id=? AND substr(eaten_at,1,10)=?",
            (user_id, today),
        ).fetchone()
        brow = cur.execute(
            "SELECT COALESCE(SUM(calories_burned),0) b FROM workouts "
            "WHERE user_id=? AND substr(done_at,1,10)=?",
            (user_id, today),
        ).fetchone()

    profile = None
    targets = None
    if prow is not None:
        from .models import ProfileIn
        try:
            p = ProfileIn(
                name=prow["name"] or "", age=prow["age"], sex=prow["sex"],
                height_cm=prow["height_cm"], weight_kg=prow["weight_kg"],
                activity=prow["activity"], goal=prow["goal"],
                body_fat_pct=prow["body_fat_pct"], body_fat_goal_pct=prow["body_fat_goal_pct"],
            )
            profile = p.model_dump()
            targets = compute_targets(p).model_dump()
        except Exception:
            profile = {k: prow[k] for k in prow.keys()}

    return {
        "user": {"id": u["id"], "username": u["username"], "created_at": u["created_at"],
                 "is_admin": bool(u["is_admin"])},
        "profile": profile,
        "targets": targets,
        "meals": meals,
        "day_totals": {
            "calories": trow["c"], "protein_g": trow["p"],
            "carbs_g": trow["cb"], "fat_g": trow["f"], "burned": brow["b"],
        },
    }


def user_page_data(user_id: int, page: int = 0, per_page: int = 20,
                   day: str | None = None) -> dict | None:
    """Full per-user admin page payload: profile+targets+body data, paginated meal
    history (optional day filter), weights, water, workouts, streaks/stamps,
    weekly adherence series, this user's devices, and the admin flag.

    Returns None if the user doesn't exist.
    """
    from .models import ProfileIn
    page = max(0, int(page))
    per_page = max(1, min(100, int(per_page)))
    offset = page * per_page

    with db.cursor() as cur:
        u = cur.execute(
            "SELECT id, username, created_at, is_admin FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if u is None:
            return None
        prow = cur.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()

        # --- paginated meal history (optional single-day filter) ---
        where = "WHERE user_id=?"
        params: list = [user_id]
        if day:
            where += " AND substr(eaten_at,1,10)=?"
            params.append(day)
        total_meals = cur.execute(
            f"SELECT COUNT(*) c FROM meals {where}", params
        ).fetchone()["c"]
        meals = [
            dict(r) for r in cur.execute(
                "SELECT id, dish, calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, "
                f"sodium_mg, eaten_at, source, favorite FROM meals {where} "
                "ORDER BY eaten_at DESC LIMIT ? OFFSET ?",
                params + [per_page, offset],
            ).fetchall()
        ]

        weights = [
            dict(r) for r in cur.execute(
                "SELECT date, weight_kg FROM weights WHERE user_id=? ORDER BY date DESC LIMIT 30",
                (user_id,),
            ).fetchall()
        ]
        water = [
            dict(r) for r in cur.execute(
                "SELECT date, water_ml FROM water_log WHERE user_id=? ORDER BY date DESC LIMIT 14",
                (user_id,),
            ).fetchall()
        ]
        workouts = [
            dict(r) for r in cur.execute(
                "SELECT id, kind, minutes, calories_burned, note, done_at FROM workouts "
                "WHERE user_id=? ORDER BY done_at DESC LIMIT 20",
                (user_id,),
            ).fetchall()
        ]
        devices = [
            dict(r) for r in cur.execute(
                "SELECT device_id, name, created_at, last_seen, req_count, revoked "
                "FROM devices WHERE active_user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        ]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trow = cur.execute(
            "SELECT COALESCE(SUM(calories),0) c, COALESCE(SUM(protein_g),0) p, "
            "COALESCE(SUM(carbs_g),0) cb, COALESCE(SUM(fat_g),0) f FROM meals "
            "WHERE user_id=? AND substr(eaten_at,1,10)=?",
            (user_id, today),
        ).fetchone()
        brow = cur.execute(
            "SELECT COALESCE(SUM(calories_burned),0) b FROM workouts "
            "WHERE user_id=? AND substr(done_at,1,10)=?",
            (user_id, today),
        ).fetchone()

    profile = None
    targets = None
    if prow is not None:
        try:
            p = ProfileIn(
                name=prow["name"] or "", age=prow["age"], sex=prow["sex"],
                height_cm=prow["height_cm"], weight_kg=prow["weight_kg"],
                activity=prow["activity"], goal=prow["goal"],
                body_fat_pct=prow["body_fat_pct"], body_fat_goal_pct=prow["body_fat_goal_pct"],
                weight_goal_kg=prow["weight_goal_kg"] if "weight_goal_kg" in prow.keys() else None,
                pace_kg_per_week=prow["pace_kg_per_week"] if "pace_kg_per_week" in prow.keys() else None,
            )
            profile = p.model_dump()
            targets = compute_targets(p).model_dump()
        except Exception:
            profile = {k: prow[k] for k in prow.keys()}

    # streaks + adherence open their own cursors — run AFTER releasing the one above.
    streak = feedback.streaks(user_id)
    adherence = feedback.weekly_adherence(user_id, weeks=8)

    total_pages = (total_meals + per_page - 1) // per_page if total_meals else 1
    return {
        "user": {"id": u["id"], "username": u["username"], "created_at": u["created_at"],
                 "is_admin": bool(u["is_admin"])},
        "profile": profile,
        "targets": targets,
        "day_totals": {
            "calories": trow["c"], "protein_g": trow["p"],
            "carbs_g": trow["cb"], "fat_g": trow["f"], "burned": brow["b"],
        },
        "meals": meals,
        "meals_page": {"page": page, "per_page": per_page, "total": total_meals,
                       "total_pages": total_pages, "day": day},
        "weights": weights,
        "water": water,
        "workouts": workouts,
        "devices": devices,
        "streaks": {"current": streak["current"], "best": streak["best"],
                    "stamps": streak["stamps"]},
        "weekly_adherence": adherence,
    }


async def keeper_overview() -> dict:
    """Fleet snapshot for the phone-admin overview screen.

    Shape: users[], devices[], backend{}, push_subs, public_url. Reuses the
    same device/backend/user sources as the web dashboard.
    """
    connected, _ = await backends.check_health()
    summary = backends.active_summary()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with db.cursor() as cur:
        urows = [
            dict(r)
            for r in cur.execute(
                "SELECT u.id, u.username, "
                "(SELECT goal FROM profiles p WHERE p.user_id=u.id) AS goal, "
                "(SELECT COALESCE(SUM(calories),0) FROM meals m "
                " WHERE m.user_id=u.id AND substr(m.eaten_at,1,10)=?) AS meals_today, "
                "(SELECT MAX(eaten_at) FROM meals m WHERE m.user_id=u.id) AS last_activity "
                "FROM users u ORDER BY u.id",
                (today,),
            ).fetchall()
        ]
        devices = [
            dict(r)
            for r in cur.execute(
                "SELECT d.device_id AS id, d.name, d.last_seen, u.username AS active_username "
                "FROM devices d LEFT JOIN users u ON u.id = d.active_user_id "
                "WHERE d.revoked = 0 ORDER BY d.created_at DESC"
            ).fetchall()
        ]

    # target + current streak open their own cursors — compute AFTER releasing
    # the cursor above (db.cursor's lock is not reentrant).
    users = []
    for ur in urows:
        users.append({
            "id": ur["id"],
            "username": ur["username"],
            "goal": ur["goal"],
            "meals_today_calories": ur["meals_today"],
            "target": feedback.target_calories(ur["id"]),
            "streak_current": feedback.streaks(ur["id"])["current"],
            "last_activity": ur["last_activity"],
        })

    return {
        "users": users,
        "devices": devices,
        "backend": {
            "mode": "connected" if connected else "mock",
            "active_text": summary["text"],
            "active_vision": summary["vision"],
        },
        "push_subs": push.subscription_count(),
        "public_url": public_url(config.port),
    }

COOKIE = "sekisho_admin"
# Sessions signed with a per-process secret so the cookie can't be forged.
_SESSION_SECRET = secrets.token_bytes(32)
_STARTED_AT = time.time()
APP_VERSION = "2.0"


def _db_size_str() -> str:
    """Human-readable on-disk size of the SQLite DB (incl. -wal/-shm if present)."""
    from pathlib import Path
    try:
        base = str(config.db_file)
        total = sum(
            Path(base + suffix).stat().st_size
            for suffix in ("", "-wal", "-shm")
            if Path(base + suffix).exists()
        )
    except Exception:
        return "?"
    size = float(total)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _make_session() -> str:
    payload = str(int(time.time()))
    sig = hmac.new(_SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _valid_session(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(_SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return (time.time() - int(payload)) < 12 * 3600  # 12h expiry
    except ValueError:
        return False


def _authed(request: Request) -> bool:
    return _valid_session(request.cookies.get(COOKIE))


# ---------------------------------------------------------------------------
# Auth pages
# ---------------------------------------------------------------------------
@router.get("/admin", response_class=HTMLResponse)
async def admin_index(request: Request):
    if not _authed(request):
        return HTMLResponse(LOGIN_HTML)
    return HTMLResponse(dashboard_html())


@router.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    if config.admin_password and hmac.compare_digest(password, config.admin_password):
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie(COOKIE, _make_session(), httponly=True, samesite="lax", max_age=12 * 3600)
        return resp
    return HTMLResponse(LOGIN_HTML.replace("<!--ERR-->", "Incorrect password."), status_code=401)


@router.post("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


def _guard(request: Request) -> JSONResponse | None:
    if not _authed(request):
        return JSONResponse({"error": {"code": "unauthorized", "message": "login required"}}, status_code=401)
    return None


def _uptime_str() -> str:
    s = int(time.time() - _STARTED_AT)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Status / devices / logs
# ---------------------------------------------------------------------------
@router.get("/admin/api/state")
async def admin_state(request: Request):
    guard = _guard(request)
    if guard:
        return guard

    connected, model = await backends.check_health()
    summary = backends.active_summary()

    with db.cursor() as cur:
        devices = [
            dict(r)
            for r in cur.execute(
                "SELECT d.device_id, d.name, d.created_at, d.last_seen, d.req_count, "
                "d.revoked, d.active_user_id, u.username AS active_username "
                "FROM devices d LEFT JOIN users u ON u.id = d.active_user_id "
                "ORDER BY d.created_at DESC"
            ).fetchall()
        ]
        logs = [
            dict(r)
            for r in cur.execute(
                "SELECT rl.device_name, rl.method, rl.path, rl.status, rl.at, "
                "rl.duration_ms, u.username AS username "
                "FROM request_log rl LEFT JOIN users u ON u.id = rl.user_id "
                "ORDER BY rl.id DESC LIMIT 200"
            ).fetchall()
        ]
    ips = classify_ips()
    return {
        "backend": {
            "connected": connected,
            "model": model,
            "active_text": summary["text"],
            "active_vision": summary["vision"],
            "count": summary["count"],
            "enabled": summary["enabled_count"],
            "mode": "connected" if connected else "mock",
            "allow_mock": config.allow_mock,
        },
        "devices": devices,
        "logs": logs,
        "ips": ips,
        "host": best_pairing_host(),
        "port": config.port,
        "public_url": public_url(config.port),
        "uptime": _uptime_str(),
        "push_subscriptions": push.subscription_count(),
        "scheduler": push.scheduler_status(),
        "db_size": _db_size_str(),
        "version": APP_VERSION,
    }


@router.post("/admin/api/pair")
async def admin_pair(request: Request):
    guard = _guard(request)
    if guard:
        return guard
    return make_pairing()


@router.get("/admin/api/qr")
async def admin_qr(request: Request, token: str):
    if not _authed(request):
        return Response(status_code=401)
    url = public_url(config.port)
    payload = json.dumps({"v": 1, "url": url, "token": token})
    img = qrcode.make(payload)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/admin/api/revoke")
async def admin_revoke(request: Request, device_id: str = Form(...)):
    guard = _guard(request)
    if guard:
        return guard
    revoke_device(device_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@router.get("/admin/api/users")
async def admin_users(request: Request):
    guard = _guard(request)
    if guard:
        return guard
    return {"users": users_list_data()}


@router.get("/admin/api/users/{user_id}")
async def admin_user_detail(request: Request, user_id: int):
    guard = _guard(request)
    if guard:
        return guard
    data = user_detail_data(user_id)
    if data is None:
        return JSONResponse({"error": {"code": "not_found", "message": "no such user"}}, status_code=404)
    return data


@router.get("/admin/api/users/{user_id}/full")
async def admin_user_full(request: Request, user_id: int,
                          page: int = 0, per_page: int = 20, day: str | None = None):
    """Dedicated per-user page payload (paginated meals + full activity)."""
    guard = _guard(request)
    if guard:
        return guard
    data = user_page_data(user_id, page=page, per_page=per_page, day=day)
    if data is None:
        return JSONResponse({"error": {"code": "not_found", "message": "no such user"}}, status_code=404)
    return data


@router.post("/admin/api/users/{user_id}/admin")
async def admin_user_set_admin(request: Request, user_id: int, is_admin: str = Form(...)):
    """Grant/revoke the phone-admin ("keeper") flag for a user from the dashboard."""
    guard = _guard(request)
    if guard:
        return guard
    value = is_admin in ("1", "true", "on", "yes")
    if not accounts.set_admin(user_id, value):
        return JSONResponse({"error": {"code": "not_found", "message": "no such user"}}, status_code=404)
    return {"ok": True, "id": user_id, "is_admin": value}


# ---------------------------------------------------------------------------
# Backends registry
# ---------------------------------------------------------------------------
@router.get("/admin/api/backends")
async def admin_backends(request: Request):
    guard = _guard(request)
    if guard:
        return guard
    return backends_payload()


@router.post("/admin/api/backends")
async def admin_backend_create(
    request: Request,
    name: str = Form(...), kind: str = Form(...), base_url: str = Form(...),
    model: str = Form(...), api_key: str = Form(default=""),
    vision: str = Form(default="0"), enabled: str = Form(default="1"),
):
    guard = _guard(request)
    if guard:
        return guard
    if kind not in ("vllm", "nvidia", "lmstudio", "openai"):
        return JSONResponse({"error": {"code": "bad_kind", "message": "invalid backend kind"}}, status_code=400)
    b = backends.create_backend(
        name=name.strip(), kind=kind, base_url=base_url.strip(),
        api_key=(api_key.strip() or None), model=model.strip(),
        vision=vision in ("1", "true", "on"), enabled=enabled in ("1", "true", "on"),
    )
    return {"ok": True, "backend": b}


@router.post("/admin/api/backends/{bid}")
async def admin_backend_update(
    request: Request, bid: int,
    name: str = Form(default=None), kind: str = Form(default=None),
    base_url: str = Form(default=None), model: str = Form(default=None),
    api_key: str = Form(default=None), vision: str = Form(default=None),
    enabled: str = Form(default=None),
):
    guard = _guard(request)
    if guard:
        return guard
    fields: dict = {}
    if name is not None:
        fields["name"] = name.strip()
    if kind is not None:
        fields["kind"] = kind
    if base_url is not None:
        fields["base_url"] = base_url.strip()
    if model is not None:
        fields["model"] = model.strip()
    if api_key is not None:
        # Empty string clears the key; a value sets it.
        fields["api_key"] = api_key.strip() or None
    if vision is not None:
        fields["vision"] = vision in ("1", "true", "on")
    if enabled is not None:
        fields["enabled"] = enabled in ("1", "true", "on")
    # api_key=None is a legit "clear"; update_backend skips None, so handle specially.
    updated = backends.update_backend(bid, **{k: v for k, v in fields.items() if v is not None or k == "api_key"})
    if "api_key" in fields and fields["api_key"] is None:
        with db.cursor() as cur:
            cur.execute("UPDATE backends SET api_key=NULL WHERE id=?", (bid,))
        updated = backends.get_backend(bid)
    if updated is None:
        return JSONResponse({"error": {"code": "not_found", "message": "no such backend"}}, status_code=404)
    return {"ok": True, "backend": updated}


@router.post("/admin/api/backends/{bid}/delete")
async def admin_backend_delete(request: Request, bid: int):
    guard = _guard(request)
    if guard:
        return guard
    backends.delete_backend(bid)
    return {"ok": True}


@router.post("/admin/api/backends/{bid}/test")
async def admin_backend_test(request: Request, bid: int):
    guard = _guard(request)
    if guard:
        return guard
    result = await backends.test_backend(bid)
    return result


@router.post("/admin/api/backends/active")
async def admin_backend_active(request: Request, slot: str = Form(...), backend_id: str = Form(...)):
    guard = _guard(request)
    if guard:
        return guard
    err_code = set_active_backend(slot, backend_id)
    if err_code:
        return JSONResponse({"error": {"code": err_code, "message": "slot must be text|vision"}}, status_code=400)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------
@router.get("/admin/api/push")
async def admin_push(request: Request):
    guard = _guard(request)
    if guard:
        return guard
    return {"subscriptions": push.subscription_count()}


@router.post("/admin/api/push/test")
async def admin_push_test(request: Request, user_id: str = Form(default="")):
    """Send a test push to ALL subscriptions, or just one user's if user_id is set.

    Dead endpoints (404/410) are pruned by push.send_to_subscriptions.
    """
    guard = _guard(request)
    if guard:
        return guard
    target_uid: int | None = None
    if user_id.strip():
        try:
            target_uid = int(user_id)
        except ValueError:
            return JSONResponse({"error": {"code": "bad_user", "message": "user_id must be int"}}, status_code=400)
    subs = push.subscriptions_for_user(target_uid) if target_uid is not None else push.all_subscriptions()
    if not subs:
        scope = f"user {target_uid}" if target_uid is not None else "any user"
        return {"ok": False, "sent": 0, "total": 0,
                "message": f"no push subscriptions to send to ({scope})"}
    payload = {"title": "関 Harahachi",
               "body": "Test notification from the admin dashboard. Push is working.",
               "slot": "test"}
    result = push.send_to_subscriptions(subs, payload)
    return {"ok": result["sent"] > 0, "sent": result["sent"], "total": result["total"],
            "pruned": result["pruned"], "errors": result["errors"][:5]}


@router.get("/admin/api/push/status")
async def admin_push_status(request: Request):
    guard = _guard(request)
    if guard:
        return guard
    return push.scheduler_status()
