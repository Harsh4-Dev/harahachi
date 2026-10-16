"""Admin dashboard v2: /admin — server-rendered HTML following DESIGN.md.

Glass-tab navigation over cards/pages:
  * Status   — tunnel/public URL, active backends, mock mode, uptime, IPs
  * Users    — list + drill-down (profile, targets, body-fat, recent meals + totals)
  * Devices  — paired devices, last seen, active user, revoke, pairing QR
  * Backends — registry CRUD (with presets), active vision/text switches, test
  * Requests — last 200 log rows, filterable client-side
  * Push     — subscription count, send a test notification
  * Bus Board / Contributors (v7) — the second phone app's server, read and
    managed server-to-server (busboard.py); its token never reaches the browser

Endpoints (all admin-session guarded except the login + captcha):
  GET  /admin                         -> login or dashboard
  GET  /admin/captcha                 -> proof-of-work challenge for the login form
  POST /admin/login  /admin/logout    (login = password + solved captcha)
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
  GET  /admin/api/nim                 -> NIM provider + per-task model slots (key masked)
  POST /admin/api/nim/slot            -> set a slot's model/fallback (stored in DB settings)
  POST /admin/api/nim/test            -> 1-token ping of a slot's model
  POST /admin/api/nim/key             -> store/replace the NVIDIA key (write-only, encrypted)
  POST /admin/api/nim/key/clear       -> forget the stored key
  GET  /admin/api/busboard            -> Bus Board stats + server-rendered panels (30 s cache)
  POST /admin/api/busboard/contributors/{id_hash}/block|unblock   (confirm=1)
  POST /admin/api/busboard/snapshot   (confirm=1)

v4 security (serverless-safe, enforced by tests):
  * Session cookie = HMAC(SEKISHO_SESSION_SECRET) — valid on every instance.
    Docker without the env falls back to a random secret persisted in the DB.
    On Vercel the login fails CLOSED when the password/secret is missing.
  * Cookie flags: HttpOnly; SameSite=Strict; Secure (always on Vercel, and on
    any https request), Path=/admin.
  * Every state-changing admin request needs the session's CSRF token
    (X-CSRF-Token header, or csrf_token form field) and a same-host Origin.
  * Login lockout is DB-backed (per IP + global) so it holds across instances.
    v7: the login also needs a solved self-hosted proof-of-work captcha
    (captcha.py, verified before the password; a failure counts as a failed login).
  * Admin pages carry a strict CSP with a per-request nonce for the one inline
    script. The NVIDIA key (env or stored) never appears in any admin response
    (masked hint only); Bus Board writes are CSRF-guarded and audited (ops_audit).
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
from urllib.parse import urlsplit

import qrcode
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from . import accounts, backends, busboard, captcha, db, feedback, push, security
from .config import config
from .net import best_pairing_host, classify_ips, public_url
from .models import valid_date
from .nutrition import compute_targets
from .templates import dashboard_html, login_html

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
        cur.execute("UPDATE devices SET revoked=1, active_user_id=NULL WHERE device_id=?", (device_id,))
    # A revoked phone must stop receiving the account's reminders too.
    push.unsubscribe_device(device_id)


def backends_payload() -> dict:
    summary = backends.active_summary()
    return {
        "backends": backends.list_backends(),
        "active_text_id": summary["text"]["id"] if summary["text"] else None,
        "active_vision_id": summary["vision"]["id"] if summary["vision"] else None,
        "presets": backends.PRESETS,
        "provider": backends.provider_mode(),
        "nim": backends.nim_payload(),
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


def _lock_state(lock_until, hard, now: float | None = None) -> str:
    """'' (not locked) | 'temp' | 'hard' from an auth_locks row's columns."""
    if hard:
        return "hard"
    if lock_until and int(lock_until) > int(now or time.time()):
        return "temp"
    return ""


def users_list_data() -> list[dict]:
    """User list with counts, goal, last activity, the phone-admin flag, and the
    PIN-lockout state (failed_logins since the last success, locked)."""
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT u.id, u.username, u.created_at, u.is_admin, "
            "(SELECT COUNT(*) FROM meals m WHERE m.user_id=u.id) AS meal_count, "
            "(SELECT COUNT(*) FROM workouts w WHERE w.user_id=u.id) AS workout_count, "
            "(SELECT MAX(eaten_at) FROM meals m WHERE m.user_id=u.id) AS last_meal, "
            "(SELECT goal FROM profiles p WHERE p.user_id=u.id) AS goal, "
            "(SELECT c.n FROM counters c WHERE c.bucket = 'pinfail:user:' || u.username "
            " AND c.win = 0) AS failed_logins, "
            "(SELECT l.lock_until FROM auth_locks l WHERE l.subject = 'user:' || u.username) AS lock_until, "
            "(SELECT l.hard FROM auth_locks l WHERE l.subject = 'user:' || u.username) AS lock_hard "
            "FROM users u ORDER BY u.id"
        ).fetchall()
    out = []
    now = time.time()
    for r in rows:
        d = dict(r)
        d["is_admin"] = bool(d["is_admin"])
        d["failed_logins"] = int(d["failed_logins"] or 0)
        d["locked"] = _lock_state(d.pop("lock_until"), d.pop("lock_hard"), now)
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
COOKIE_PATH = "/admin"
SESSION_TTL = 12 * 3600
_STARTED_AT = time.time()
APP_VERSION = "2.0"


def _db_size_str() -> str:
    """Human-readable size of the store (SQLite file incl. -wal/-shm, or Postgres DB)."""
    return db.size_str()


# ---------------------------------------------------------------------------
# Sessions: HMAC with a secret shared by every instance
# ---------------------------------------------------------------------------
def _session_key() -> bytes | None:
    """HMAC key for admin cookies. Env SEKISHO_SESSION_SECRET (required on Vercel);
    Docker/dev without it: a random secret persisted in the DB (stable across
    restarts and workers). None => sessions impossible (fail closed)."""
    if config.session_secret:
        return config.session_secret.encode("utf-8")
    if config.on_vercel:
        return None
    stored = db.get_setting("admin_session_secret")
    if not stored:
        stored = secrets.token_urlsafe(48)
        db.set_setting("admin_session_secret", stored)
    return stored.encode("utf-8")


def _sign(key: bytes, purpose: str, payload: str) -> str:
    return hmac.new(key, f"{purpose}|{payload}".encode(), hashlib.sha256).hexdigest()


def _make_session() -> str:
    key = _session_key()
    if key is None:
        raise RuntimeError("admin sessions disabled: no SEKISHO_SESSION_SECRET")
    payload = f"{int(time.time())}.{secrets.token_hex(12)}"
    return f"{payload}.{_sign(key, 'sess', payload)}"


def _session_payload(token: str | None) -> str | None:
    """The verified session payload, or None if missing/forged/expired."""
    if not token or token.count(".") != 2:
        return None
    key = _session_key()
    if key is None:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(key, "sess", payload)):
        return None
    try:
        issued = int(payload.split(".", 1)[0])
    except ValueError:
        return None
    if not (0 <= time.time() - issued < SESSION_TTL):
        return None
    return payload


def _valid_session(token: str | None) -> bool:
    return _session_payload(token) is not None


def csrf_token_for(session_token: str | None) -> str:
    payload = _session_payload(session_token)
    key = _session_key()
    if payload is None or key is None:
        return ""
    return _sign(key, "csrf", payload)[:48]


def _authed(request: Request) -> bool:
    return _valid_session(request.cookies.get(COOKIE))


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else ""
    if config.on_vercel or peer in ("127.0.0.1", "::1"):
        if request.headers.get("x-forwarded-proto", "").lower() == "https":
            return True
        if '"https"' in request.headers.get("cf-visitor", ""):
            return True
    return False


def _same_origin(request: Request) -> bool:
    """Reject cross-site form/fetch posts. A missing Origin is tolerated (non-browser
    clients); the CSRF token is still required for every state change."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "")
    try:
        return (urlsplit(origin).hostname or "").lower() == (urlsplit("//" + host).hostname or "").lower()
    except ValueError:
        return False


def admin_csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    )


def _html(body: str, status: int = 200, nonce: str | None = None) -> HTMLResponse:
    nonce = nonce or secrets.token_urlsafe(16)
    resp = HTMLResponse(body, status_code=status)
    resp.headers["Content-Security-Policy"] = admin_csp(nonce)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------------------
# Auth pages
# ---------------------------------------------------------------------------
def _login_page(message: str = "", status: int = 200) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    reason = config.admin_login_disabled_reason()
    if reason:
        return _html(login_html(reason, disabled=True, on_vercel=config.on_vercel, nonce=nonce),
                     status=503 if status == 200 else status, nonce=nonce)
    return _html(login_html(message, on_vercel=config.on_vercel, nonce=nonce), status=status, nonce=nonce)


def _captcha_base_key() -> bytes | None:
    """Root secret for the login captcha HMAC (HKDF-derived inside captcha.py,
    with its own purpose label, so it never equals the cookie-signing key)."""
    return _session_key()


@router.get("/admin/captcha")
async def admin_captcha():
    """Issue one proof-of-work challenge for the login form (stateless, signed)."""
    reason = config.admin_login_disabled_reason()
    key = None if reason else _captcha_base_key()
    if key is None:
        return JSONResponse({"error": {"code": "login_disabled", "message": reason or "admin login disabled"}},
                            status_code=503, headers={"Cache-Control": "no-store"})
    return JSONResponse(captcha.issue(key), headers={"Cache-Control": "no-store"})


@router.get("/admin", response_class=HTMLResponse)
async def admin_index(request: Request):
    token = request.cookies.get(COOKIE)
    if not _valid_session(token):
        return _login_page()
    nonce = secrets.token_urlsafe(16)
    return _html(dashboard_html(nonce=nonce, csrf=csrf_token_for(token)), nonce=nonce)


@router.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...), altcha: str = Form(default="")):
    """Password + proof-of-work captcha. Order: fail-closed config -> same origin ->
    lockout -> captcha (a failure counts as a failed login) -> password."""
    reason = config.admin_login_disabled_reason()
    if reason:
        return _login_page(reason, status=503)
    if not _same_origin(request):
        return _login_page("Cross-site login blocked.", status=403)
    ip = security.client_ip(request)
    wait = security.admin_locked(ip)
    if wait:
        return _login_page(f"Too many failed attempts. Try again in {wait // 60 + 1} min.", status=429)
    key = _captcha_base_key()
    problem = captcha.verify(altcha, key) if key is not None else "bad_signature"
    if problem:
        security.admin_login_failed(ip)
        return _login_page(captcha.REASONS.get(problem, "Anti-bot check failed."), status=401)
    if hmac.compare_digest(password.encode("utf-8"), config.admin_password.encode("utf-8")):
        security.admin_login_succeeded(ip)
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie(COOKIE, _make_session(), max_age=SESSION_TTL, path=COOKIE_PATH,
                        httponly=True, samesite="strict",
                        secure=config.on_vercel or _is_https(request))
        return resp
    security.admin_login_failed(ip)
    return _login_page("Incorrect password.", status=401)


@router.post("/admin/logout")
async def admin_logout(request: Request):
    guard = await _guard_write(request)
    if guard:
        # An unauthenticated/forged logout is harmless: just bounce to the login page.
        return RedirectResponse("/admin", status_code=303)
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite="strict",
                       secure=config.on_vercel or _is_https(request))
    return resp


def _guard(request: Request) -> JSONResponse | None:
    if not _authed(request):
        return JSONResponse({"error": {"code": "unauthorized", "message": "login required"}}, status_code=401)
    return None


async def _guard_write(request: Request) -> JSONResponse | None:
    """Session + same-origin + CSRF token, for every state-changing admin route."""
    token = request.cookies.get(COOKIE)
    if not _valid_session(token):
        return JSONResponse({"error": {"code": "unauthorized", "message": "login required"}}, status_code=401)
    if not _same_origin(request):
        return JSONResponse({"error": {"code": "csrf_failed", "message": "cross-site request blocked"}},
                            status_code=403)
    sent = request.headers.get("x-csrf-token", "")
    if not sent:
        try:
            form = await request.form()
            sent = str(form.get("csrf_token") or "")
        except Exception:
            sent = ""
    expected = csrf_token_for(token)
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        return JSONResponse({"error": {"code": "csrf_failed", "message": "missing or invalid CSRF token"}},
                            status_code=403)
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
                "d.revoked, d.active_user_id, u.username AS active_username, "
                "l.lock_until AS pin_lock_until, l.hard AS pin_lock_hard "
                "FROM devices d LEFT JOIN users u ON u.id = d.active_user_id "
                "LEFT JOIN auth_locks l ON l.subject = 'dev:' || d.device_id "
                "ORDER BY d.created_at DESC"
            ).fetchall()
        ]
    _now = time.time()
    for d in devices:
        d["pin_locked"] = _lock_state(d.pop("pin_lock_until"), d.pop("pin_lock_hard"), _now)
    with db.cursor() as cur:
        logs = [
            dict(r)
            for r in cur.execute(
                "SELECT rl.device_name, rl.method, rl.path, rl.status, rl.at, "
                "rl.duration_ms, u.username AS username "
                "FROM request_log rl LEFT JOIN users u ON u.id = rl.user_id "
                "ORDER BY rl.id DESC LIMIT 200"
            ).fetchall()
        ]
    ips = {"lan": [], "tailscale": [], "other": []} if config.on_vercel else classify_ips()
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
            "provider": backends.provider_mode(),
        },
        "deployment": "vercel" if config.on_vercel else "docker",
        "db_kind": db.backend_kind(),
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
    guard = await _guard_write(request)
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
    guard = await _guard_write(request)
    if guard:
        return guard
    revoke_device(device_id)
    return {"ok": True}


@router.post("/admin/api/devices/unlock")
async def admin_device_unlock(request: Request, device_id: str = Form(...)):
    """Clear a device's PIN-failure count + lock (CSRF-protected)."""
    guard = await _guard_write(request)
    if guard:
        return guard
    accounts.unlock_device(device_id)
    return {"ok": True, "device_id": device_id}


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
    if day is not None and day != "" and not valid_date(day):
        return JSONResponse({"error": {"code": "bad_date", "message": "day must be YYYY-MM-DD"}},
                            status_code=400)
    data = user_page_data(user_id, page=page, per_page=per_page, day=day)
    if data is None:
        return JSONResponse({"error": {"code": "not_found", "message": "no such user"}}, status_code=404)
    return data


@router.post("/admin/api/users/{user_id}/unlock")
async def admin_user_unlock(request: Request, user_id: int):
    """Clear a user's PIN-failure count + lock, including the hard lock applied
    after 20 wrong PINs (CSRF-protected like every admin write)."""
    guard = await _guard_write(request)
    if guard:
        return guard
    if not accounts.unlock(user_id):
        return JSONResponse({"error": {"code": "not_found", "message": "no such user"}}, status_code=404)
    return {"ok": True, "id": user_id, "locked": "", "failed_logins": 0}


@router.post("/admin/api/users/{user_id}/admin")
async def admin_user_set_admin(request: Request, user_id: int, is_admin: str = Form(...)):
    """Grant/revoke the phone-admin ("keeper") flag for a user from the dashboard."""
    guard = await _guard_write(request)
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
    guard = await _guard_write(request)
    if guard:
        return guard
    if kind not in backends.BACKEND_KINDS:
        return JSONResponse({"error": {"code": "bad_kind", "message": "invalid backend kind"}}, status_code=400)
    try:
        b = backends.create_backend(
            name=name.strip(), kind=kind, base_url=base_url.strip(),
            api_key=(api_key.strip() or None), model=model.strip(),
            vision=vision in ("1", "true", "on"), enabled=enabled in ("1", "true", "on"),
        )
    except backends.BackendURLNotAllowed as e:
        return JSONResponse({"error": {"code": "backend_url_not_allowed", "message": str(e)}}, status_code=400)
    return {"ok": True, "backend": b}


@router.post("/admin/api/backends/{bid}")
async def admin_backend_update(
    request: Request, bid: int,
    name: str = Form(default=None), kind: str = Form(default=None),
    base_url: str = Form(default=None), model: str = Form(default=None),
    api_key: str = Form(default=None), vision: str = Form(default=None),
    enabled: str = Form(default=None),
):
    guard = await _guard_write(request)
    if guard:
        return guard
    fields: dict = {}
    if name is not None:
        fields["name"] = name.strip()
    if kind is not None:
        if kind not in backends.BACKEND_KINDS:
            return JSONResponse({"error": {"code": "bad_kind", "message": "invalid backend kind"}},
                                status_code=400)
        fields["kind"] = kind
    if base_url is not None:
        fields["base_url"] = base_url.strip()
    if model is not None:
        fields["model"] = model.strip()
    # Newer FastAPI coerces an EMPTY form field to its default (None), so detect
    # "api_key was sent" from the raw form: empty string clears, a value sets.
    raw_form = await request.form()
    if "api_key" in raw_form:
        fields["api_key"] = str(raw_form.get("api_key") or "").strip() or None
    if vision is not None:
        fields["vision"] = vision in ("1", "true", "on")
    if enabled is not None:
        fields["enabled"] = enabled in ("1", "true", "on")
    # api_key=None is a legit "clear"; update_backend skips None, so handle specially.
    try:
        updated = backends.update_backend(bid, **{k: v for k, v in fields.items() if v is not None or k == "api_key"})
    except backends.BackendURLNotAllowed as e:
        return JSONResponse({"error": {"code": "backend_url_not_allowed", "message": str(e)}}, status_code=400)
    if "api_key" in fields and fields["api_key"] is None:
        with db.cursor() as cur:
            cur.execute("UPDATE backends SET api_key=NULL WHERE id=?", (bid,))
        updated = backends.get_backend(bid)
    if updated is None:
        return JSONResponse({"error": {"code": "not_found", "message": "no such backend"}}, status_code=404)
    return {"ok": True, "backend": updated}


@router.post("/admin/api/backends/{bid}/delete")
async def admin_backend_delete(request: Request, bid: int):
    guard = await _guard_write(request)
    if guard:
        return guard
    backends.delete_backend(bid)
    return {"ok": True}


@router.post("/admin/api/backends/{bid}/test")
async def admin_backend_test(request: Request, bid: int):
    guard = await _guard_write(request)
    if guard:
        return guard
    result = await backends.test_backend(bid)
    return result


@router.post("/admin/api/backends/active")
async def admin_backend_active(request: Request, slot: str = Form(...), backend_id: str = Form(...)):
    guard = await _guard_write(request)
    if guard:
        return guard
    err_code = set_active_backend(slot, backend_id)
    if err_code:
        return JSONResponse({"error": {"code": err_code, "message": "slot must be text|vision"}}, status_code=400)
    return {"ok": True}


# ---------------------------------------------------------------------------
# NVIDIA NIM model slots (v4)
# ---------------------------------------------------------------------------
@router.get("/admin/api/nim")
async def admin_nim(request: Request):
    guard = _guard(request)
    if guard:
        return guard
    return backends.nim_payload()


@router.post("/admin/api/nim/slot")
async def admin_nim_slot(request: Request, task: str = Form(...), model: str = Form(default=""),
                         fallback: str = Form(default="")):
    guard = await _guard_write(request)
    if guard:
        return guard
    try:
        slot = backends.set_slot(task, model, fallback)
    except ValueError as e:
        code = str(e) if str(e) in ("bad_task", "bad_model") else "bad_request"
        msg = "task must be analyze|body|chat|insights" if code == "bad_task" else "invalid model id"
        return JSONResponse({"error": {"code": code, "message": msg}}, status_code=400)
    return {"ok": True, "slot": slot}


@router.post("/admin/api/nim/test")
async def admin_nim_test(request: Request, task: str = Form(...)):
    guard = await _guard_write(request)
    if guard:
        return guard
    return await backends.test_nim_slot(task)


def _audit(action: str, target: str | None, params: dict, result: str) -> None:
    """Admin writes share the ops API's audit trail (ops_audit, "admin." actions)."""
    try:
        from .ops import _audit as _ops_audit
        _ops_audit(action, target, params, result)
    except Exception:  # noqa: BLE001 - auditing must never break the action itself
        pass


@router.post("/admin/api/nim/key")
async def admin_nim_key_set(request: Request):
    """Store / replace the NVIDIA API key (write-only, Fernet-encrypted in settings).

    The raw form is parsed by hand (no pydantic field) so no validation error can
    ever echo the submitted value; the response carries only the masked status."""
    guard = await _guard_write(request)
    if guard:
        return guard
    form = await request.form()
    value = str(form.get("api_key") or "")
    try:
        status = backends.set_stored_nvidia_key(value)
    except backends.KeyStoreError as e:
        _audit("admin.nim_key_set", None, {}, f"error:{e.code}")
        return JSONResponse({"error": {"code": e.code, "message": e.message}, "key": backends.nim_key_status()},
                            status_code=e.status)
    _audit("admin.nim_key_set", None, {}, "ok")
    return {"ok": True, "key": status}


@router.post("/admin/api/nim/key/clear")
async def admin_nim_key_clear(request: Request):
    guard = await _guard_write(request)
    if guard:
        return guard
    if config.nvidia_api_key:
        return JSONResponse({"error": {"code": "env_locked", "message": "NVIDIA_API_KEY is set in the "
                                       "environment; remove it there instead."}, "key": backends.nim_key_status()},
                            status_code=409)
    status = backends.clear_stored_nvidia_key()
    _audit("admin.nim_key_clear", None, {}, "ok")
    return {"ok": True, "key": status}


# ---------------------------------------------------------------------------
# Bus Board (second phone app) — server-to-server via busboard.py; the
# BUSBOARD_OPS_TOKEN never leaves this process.
# ---------------------------------------------------------------------------
def busboard_audit_rows(limit: int = 20) -> list[dict]:
    with db.cursor() as cur:
        return [dict(r) for r in cur.execute(
            "SELECT at, action, target, result FROM ops_audit WHERE substr(action,1,15)=? "
            "ORDER BY id DESC LIMIT ?", ("admin.busboard.", limit)).fetchall()]


@router.get("/admin/api/busboard")
async def admin_busboard(request: Request, refresh: int = 0):
    guard = _guard(request)
    if guard:
        return guard
    from .templates import busboard_panels
    res = await busboard.stats(force=bool(refresh))
    return {**res, "panels": busboard_panels(res, busboard_audit_rows())}


def _confirmed(form) -> bool:
    return str(form.get("confirm") or "") in ("1", "yes", "true")


@router.post("/admin/api/busboard/contributors/{id_hash}/{action}")
async def admin_busboard_contributor(request: Request, id_hash: str, action: str):
    guard = await _guard_write(request)
    if guard:
        return guard
    if action not in busboard.ACTIONS:
        return JSONResponse({"error": {"code": "bad_action", "message": "action must be block|unblock"}},
                            status_code=400)
    if not _confirmed(await request.form()):
        return JSONResponse({"error": {"code": "confirm_required", "message": "confirm=1 is required"}},
                            status_code=400)
    target = id_hash[:128]
    res = await busboard.contributor_action(id_hash, action)
    code = None if res.get("ok") else (res.get("error") or {}).get("code", "failed")
    _audit(f"admin.busboard.{action}", target, {"id_hash": target}, "ok" if code is None else f"error:{code}")
    if code is not None:
        status = 400 if code in ("bad_id_hash", "bad_action") else 503 if code == "not_configured" else 502
        return JSONResponse({"ok": False, "error": res["error"]}, status_code=status)
    return {"ok": True, "action": action, "id_hash": id_hash, "result": res.get("result")}


@router.post("/admin/api/busboard/snapshot")
async def admin_busboard_snapshot(request: Request):
    guard = await _guard_write(request)
    if guard:
        return guard
    if not _confirmed(await request.form()):
        return JSONResponse({"error": {"code": "confirm_required", "message": "confirm=1 is required"}},
                            status_code=400)
    res = await busboard.snapshot()
    code = None if res.get("ok") else (res.get("error") or {}).get("code", "failed")
    _audit("admin.busboard.snapshot", None, {}, "ok" if code is None else f"error:{code}")
    if code is not None:
        return JSONResponse({"ok": False, "error": res["error"]}, status_code=502)
    return {"ok": True, "result": res.get("result")}


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
    guard = await _guard_write(request)
    if guard:
        return guard
    target_uid: int | None = None
    if user_id.strip():
        try:
            target_uid = int(user_id)
        except ValueError:
            return JSONResponse({"error": {"code": "bad_user", "message": "user_id must be int"}}, status_code=400)
    return send_test_push(target_uid)


def send_test_push(target_uid: int | None) -> dict:
    """Send one test notification to a user's subscriptions (or all when None).
    Shared by the admin dashboard and the ops API's push_test action."""
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
