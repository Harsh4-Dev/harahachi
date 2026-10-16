"""Server-to-server ops API for the private Bansho console: ``/api/ops/v1``.

Contract: ``D:\\linux_workspace\\bansho\\SPEC.md`` ("Harahachi — base /api/ops/v1").

Auth (enforced by the ``ops_gate`` middleware in app.py BEFORE routing, so an
unauthorized caller cannot even learn which paths exist):

* ``Authorization: Bearer <SEKISHO_OPS_TOKEN>``, constant-time compare.
* Token unset, shorter than 48 chars, or wrong -> **404** ``{"detail": "Not Found"}``
  (byte-identical to an unknown route). Never 401/403.
* No CORS on these routes (the CORS middleware is bypassed for /api/ops/*).
* Not subject to the per-device rate limiter; its own generous limit instead.
* The token never appears in a response, log line or error message, and these
  requests are not written to the request log or the request counters.

Every write (``POST /actions``) is recorded in ``ops_audit``.

All analytics SQL is portable (SQLite + Postgres): day bucketing is done with
string comparisons against precomputed UTC boundaries of SEKISHO_TZ-local days
(``usage.bucket_case``), never with dialect date functions.
"""
from __future__ import annotations

import hmac
import json
import math
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import accounts, admin, backends, db, feedback, push, security, usage
from .config import config

router = APIRouter(prefix="/api/ops/v1")

OPS_PREFIX = "/api/ops"
OPS_RATE_PER_MIN = 300
_ops_limiter = security.RateLimiter(limit=OPS_RATE_PER_MIN, window_s=60, prefix="rl:ops:")

MEAL_SOURCE_SERIES = {
    "photo": "meals_photo",
    "barcode": "meals_barcode",
    "manual": "meals_manual",
    "chat": "meals_chat",
    "quicklog": "meals_quicklog",
    # Accepted suggestions (Honjin / reminder banner) and pre-v6 quick-logs.
    "suggestion": "meals_quicklog",
}
SERIES_KEYS = (
    "new_users", "dau", "wau", "mau", "meals", "meals_photo", "meals_barcode", "meals_manual", "meals_chat",
    "meals_quicklog", "ai_calls", "ai_errors", "ai_tokens_in", "ai_tokens_out", "cap_hits",
    "requests", "errors_5xx", "p95_ms", "pushes_sent", "pushes_failed",
)
NIM_SLOTS = backends.TASKS  # analyze | body | chat | insights


# ---------------------------------------------------------------------------
# gate helpers (used by app.ops_gate)
# ---------------------------------------------------------------------------
def is_ops_path(path: str) -> bool:
    return path == OPS_PREFIX or path.startswith(OPS_PREFIX + "/")


def authorized(header: str | None) -> bool:
    """Constant-time check of ``Authorization: Bearer <SEKISHO_OPS_TOKEN>``.
    False whenever the token is unset or shorter than 48 characters."""
    if not config.ops_enabled():
        return False
    header = header or ""
    if header[:7].lower() != "bearer ":
        return False
    sent = header[7:].strip().encode("utf-8", "replace")
    return hmac.compare_digest(sent, config.ops_token.encode("utf-8"))


def rate_ok() -> bool:
    try:
        return _ops_limiter.hit("all")
    except Exception:  # noqa: BLE001 - a DB hiccup must not lock the owner out
        return True


def not_found() -> JSONResponse:
    """Exactly what FastAPI answers for an unknown route."""
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _n(cur, sql: str, params=()) -> int:
    row = cur.execute(sql, params).fetchone()
    return int((row["n"] if row is not None else 0) or 0)


def _pct(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile of a list of ints (None when empty)."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(q * len(s)) - 1))
    return int(s[k])


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _int_param(request: Request, name: str, default: int, lo: int, hi: int) -> int | JSONResponse:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        return _err("bad_request", f"{name} must be an integer", 400)
    return max(lo, min(hi, v))


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _slot_usage(rows) -> dict:
    """{slot: {calls, errors, p50_ms, p95_ms, tokens_in, tokens_out}} for every slot."""
    acc: dict[str, dict] = {s: {"ms": [], "calls": 0, "errors": 0, "tin": 0, "tout": 0}
                            for s in usage.SLOTS}
    for r in rows:
        a = acc.setdefault(r["slot"], {"ms": [], "calls": 0, "errors": 0, "tin": 0, "tout": 0})
        a["calls"] += 1
        if not r["ok"]:
            a["errors"] += 1
        if r["ms"] is not None:
            a["ms"].append(int(r["ms"]))
        a["tin"] += int(r["tokens_in"] or 0)
        a["tout"] += int(r["tokens_out"] or 0)
    return {s: {"calls": a["calls"], "errors": a["errors"],
                "p50_ms": _pct(a["ms"], 0.50), "p95_ms": _pct(a["ms"], 0.95),
                "tokens_in": a["tin"], "tokens_out": a["tout"]}
            for s, a in acc.items()}


# ---------------------------------------------------------------------------
# GET /summary
# ---------------------------------------------------------------------------
@router.get("/summary")
async def ops_summary():
    now = datetime.now(timezone.utc)
    today = usage.local_today(now)
    lo, hi = usage.day_bounds_utc(today, 1)
    since24 = _utc(now - timedelta(hours=24))

    db_ok = True
    try:
        with db.cursor() as cur:
            cur.execute("SELECT 1 AS n").fetchone()
    except Exception:  # noqa: BLE001
        db_ok = False

    with db.cursor() as cur:
        totals = {
            "users": _n(cur, "SELECT COUNT(*) AS n FROM users"),
            "devices_active": _n(cur, "SELECT COUNT(*) AS n FROM devices WHERE revoked=0"),
            "devices_revoked": _n(cur, "SELECT COUNT(*) AS n FROM devices WHERE revoked<>0"),
            "meals": _n(cur, "SELECT COUNT(*) AS n FROM meals"),
            "workouts": _n(cur, "SELECT COUNT(*) AS n FROM workouts"),
            "weights": _n(cur, "SELECT COUNT(*) AS n FROM weights"),
            "push_subs": _n(cur, "SELECT COUNT(*) AS n FROM push_subscriptions"),
            "keepers": _n(cur, "SELECT COUNT(*) AS n FROM users WHERE is_admin=1"),
        }
        ai = cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) AS e "
            "FROM llm_calls WHERE at>=? AND at<?", (lo, hi)).fetchone()
        stats = {r["metric"]: int(r["n"]) for r in cur.execute(
            "SELECT metric, n FROM daily_stats WHERE day=?", (today.isoformat(),)).fetchall()}
        today_out = {
            "new_users": _n(cur, "SELECT COUNT(*) AS n FROM users WHERE created_at>=? AND created_at<?",
                            (lo, hi)),
            "active_users": _n(cur, "SELECT COUNT(*) AS n FROM user_active_days WHERE day=?",
                               (today.isoformat(),)),
            "meals": _n(cur, "SELECT COUNT(*) AS n FROM meals WHERE eaten_at>=? AND eaten_at<?", (lo, hi)),
            "ai_calls": int(ai["n"] or 0),
            "ai_errors": int(ai["e"] or 0),
            "cap_hits": stats.get("cap_hits", 0),
            "requests": stats.get("requests", 0),
            "errors_5xx": stats.get("errors_5xx", 0),
        }
        slot_rows = cur.execute(
            "SELECT slot, ok, ms, tokens_in, tokens_out FROM llm_calls WHERE at>=?", (since24,)
        ).fetchall()
        client_rows = cur.execute(
            "SELECT client_kind AS k, client_version AS v, COUNT(*) AS n FROM devices "
            "WHERE revoked=0 GROUP BY client_kind, client_version"
        ).fetchall()
        # Most-logged dishes over the last 30 days (exact dish text; the console
        # shows these as bars). Aggregate only - no user attached.
        dish_lo = _utc(now - timedelta(days=30))
        top_dishes = [
            {"name": r["dish"], "count": int(r["n"])}
            for r in cur.execute(
                "SELECT dish, COUNT(*) AS n FROM meals WHERE eaten_at>=? AND dish IS NOT NULL "
                "AND dish<>'' GROUP BY dish ORDER BY n DESC, dish LIMIT 10", (dish_lo,)
            ).fetchall()
        ]

    clients = {"apk": 0, "pwa": 0, "web": 0, "unknown": 0}
    client_versions: dict[str, dict[str, int]] = {k: {} for k in clients}
    for r in client_rows:
        k = r["k"] if r["k"] in usage.CLIENT_KINDS else "unknown"
        clients[k] += int(r["n"])
        ver = r["v"] or "unknown"
        client_versions[k][ver] = client_versions[k].get(ver, 0) + int(r["n"])

    ai_by_slot = _slot_usage(slot_rows)
    # Which model each slot is configured to use right now (passthrough rides on
    # the chat model). Best effort: a config read must never break the summary.
    for slot, entry in ai_by_slot.items():
        try:
            entry["model"] = backends.slot_config("chat" if slot == "passthrough" else slot)["model"]
        except Exception:  # noqa: BLE001
            entry["model"] = None

    try:
        nim_ok = bool(backends.is_configured())
    except Exception:  # noqa: BLE001
        nim_ok = False
    try:
        push_ok = push.vapid_configured()
    except Exception:  # noqa: BLE001
        push_ok = False

    return {
        "generated_at": now.isoformat(),
        "version": admin.APP_VERSION,
        "tz": config.tz_name,
        "totals": totals,
        "today": today_out,
        "ai_by_slot": ai_by_slot,
        "clients": clients,
        "client_versions": client_versions,
        "top_dishes": top_dishes,
        "health": {"db_ok": db_ok, "nim_configured": nim_ok, "push_configured": push_ok},
    }


# ---------------------------------------------------------------------------
# GET /timeseries?days=30
# ---------------------------------------------------------------------------
@router.get("/timeseries")
async def ops_timeseries(request: Request):
    days = _int_param(request, "days", 30, 1, 90)
    if isinstance(days, JSONResponse):
        return days
    today = usage.local_today()
    first = today - timedelta(days=days - 1)
    day_list = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    bounds = usage.day_bounds_utc(first, days)
    lo, hi = bounds[0], bounds[-1]
    series: dict[str, list] = {k: [0] * days for k in SERIES_KEYS}

    def _bkt(col: str):
        return usage.bucket_case(col, bounds)

    with db.cursor() as cur:
        case, cp = _bkt("created_at")
        for r in cur.execute(
            f"SELECT bkt, COUNT(*) AS n FROM (SELECT {case} AS bkt FROM users "
            "WHERE created_at>=? AND created_at<?) t GROUP BY bkt", cp + [lo, hi]
        ).fetchall():
            if r["bkt"] is not None:
                series["new_users"][int(r["bkt"])] = int(r["n"])

        case, cp = _bkt("eaten_at")
        for r in cur.execute(
            f"SELECT bkt, src, COUNT(*) AS n FROM (SELECT {case} AS bkt, source AS src FROM meals "
            "WHERE eaten_at>=? AND eaten_at<?) t GROUP BY bkt, src", cp + [lo, hi]
        ).fetchall():
            if r["bkt"] is None:
                continue
            i, n = int(r["bkt"]), int(r["n"])
            series["meals"][i] += n
            key = MEAL_SOURCE_SERIES.get(r["src"] or "", "meals_manual")
            series[key][i] += n

        case, cp = _bkt("at")
        for r in cur.execute(
            f"SELECT bkt, COUNT(*) AS n, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS e, "
            "SUM(COALESCE(tokens_in,0)) AS tin, SUM(COALESCE(tokens_out,0)) AS tout "
            f"FROM (SELECT {case} AS bkt, ok, tokens_in, tokens_out FROM llm_calls "
            "WHERE at>=? AND at<?) t GROUP BY bkt", cp + [lo, hi]
        ).fetchall():
            if r["bkt"] is None:
                continue
            i = int(r["bkt"])
            series["ai_calls"][i] = int(r["n"] or 0)
            series["ai_errors"][i] = int(r["e"] or 0)
            series["ai_tokens_in"][i] = int(r["tin"] or 0)
            series["ai_tokens_out"][i] = int(r["tout"] or 0)

        idx = {d: i for i, d in enumerate(day_list)}
        # DAU, plus WAU/MAU as distinct users over the trailing 7 / 30 days ending
        # on each day - so reach back 29 days before the first one shown.
        window_start = (first - timedelta(days=29)).isoformat()
        active: dict[str, set] = {}
        for r in cur.execute(
            "SELECT day, user_id FROM user_active_days WHERE day>=? AND day<=?",
            (window_start, day_list[-1]),
        ).fetchall():
            active.setdefault(r["day"], set()).add(r["user_id"])
        for i, d in enumerate(day_list):
            series["dau"][i] = len(active.get(d, ()))
            day = first + timedelta(days=i)
            for key, span in (("wau", 7), ("mau", 30)):
                users: set = set()
                for back in range(span):
                    users |= active.get((day - timedelta(days=back)).isoformat(), set())
                series[key][i] = len(users)

        hists: dict[int, dict[str, int]] = {}
        for r in cur.execute(
            "SELECT day, metric, n FROM daily_stats WHERE day>=? AND day<=?",
            (day_list[0], day_list[-1]),
        ).fetchall():
            i = idx.get(r["day"])
            if i is None:
                continue
            m, n = r["metric"], int(r["n"])
            if m.startswith("lat_"):
                hists.setdefault(i, {})[m] = n
            elif m in ("cap_hits", "requests", "errors_5xx", "pushes_sent", "pushes_failed"):
                series[m][i] = n
        for i, h in hists.items():
            series["p95_ms"][i] = usage.p95_from_hist(h)

    return {"days": day_list, "tz": config.tz_name, "series": series}


# ---------------------------------------------------------------------------
# GET /retention?weeks=8
# ---------------------------------------------------------------------------
@router.get("/retention")
async def ops_retention(request: Request):
    weeks = _int_param(request, "weeks", 8, 1, 26)
    if isinstance(weeks, JSONResponse):
        return weeks
    today = usage.local_today()
    this_monday = today - timedelta(days=today.weekday())
    week_starts = [this_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    end_monday = this_monday + timedelta(weeks=1)
    ubounds = [usage.day_start_utc(ws) for ws in week_starts] + [usage.day_start_utc(end_monday)]
    dbounds = [ws.isoformat() for ws in week_starts] + [end_monday.isoformat()]
    ucase, ucp = usage.bucket_case("created_at", ubounds)
    dcase, dcp = usage.bucket_case("day", dbounds)

    with db.cursor() as cur:
        sizes = {int(r["bkt"]): int(r["n"]) for r in cur.execute(
            f"SELECT bkt, COUNT(*) AS n FROM (SELECT {ucase} AS bkt FROM users "
            "WHERE created_at>=? AND created_at<?) t GROUP BY bkt",
            ucp + [ubounds[0], ubounds[-1]],
        ).fetchall() if r["bkt"] is not None}
        active: dict[tuple[int, int], int] = {}
        for r in cur.execute(
            f"SELECT c.bkt AS cohort, a.wk AS wk, COUNT(DISTINCT a.user_id) AS n "
            f"FROM (SELECT id, {ucase} AS bkt FROM users WHERE created_at>=? AND created_at<?) c "
            f"JOIN (SELECT user_id, {dcase} AS wk FROM user_active_days WHERE day>=? AND day<?) a "
            "ON a.user_id = c.id GROUP BY c.bkt, a.wk",
            ucp + [ubounds[0], ubounds[-1]] + dcp + [dbounds[0], dbounds[-1]],
        ).fetchall():
            if r["cohort"] is not None and r["wk"] is not None:
                active[(int(r["cohort"]), int(r["wk"]))] = int(r["n"])

    cohorts = []
    for i, ws in enumerate(week_starts):
        size = sizes.get(i, 0)
        counts: list[int | None] = []
        pcts: list[float | None] = []
        for k in range(1, weeks + 1):
            w = i + k
            if w > weeks - 1:  # that week has not started yet
                counts.append(None)
                pcts.append(None)
                continue
            n = active.get((i, w), 0)
            counts.append(n)
            pcts.append(round(100.0 * n / size, 1) if size else None)
        cohorts.append({"week_start": ws.isoformat(), "size": size, "active": counts,
                        "retention_pct": pcts})
    return {"weeks": weeks, "tz": config.tz_name, "cohorts": cohorts}


# ---------------------------------------------------------------------------
# GET /funnel
# ---------------------------------------------------------------------------
@router.get("/funnel")
async def ops_funnel():
    with db.cursor() as cur:
        return {
            "paired_devices": _n(cur, "SELECT COUNT(*) AS n FROM devices"),
            "registered_users": _n(cur, "SELECT COUNT(*) AS n FROM users"),
            "first_meal": _n(cur, "SELECT COUNT(DISTINCT user_id) AS n FROM meals"),
            "active_3_days": _n(cur, "SELECT COUNT(*) AS n FROM (SELECT user_id FROM user_active_days "
                                     "GROUP BY user_id HAVING COUNT(*) >= 3) t"),
            "active_7_days": _n(cur, "SELECT COUNT(*) AS n FROM (SELECT user_id FROM user_active_days "
                                     "GROUP BY user_id HAVING COUNT(*) >= 7) t"),
        }


# ---------------------------------------------------------------------------
# GET /users?q=&page=&sort=
# ---------------------------------------------------------------------------
_USER_SORTS = {
    "created_at": "created_at DESC",
    "last_active": "last_active DESC",
    "meals": "meals DESC",
    "ai_calls_7d": "ai_calls_7d DESC",
    "username": "username ASC",
    "id": "id ASC",
}


@router.get("/users")
async def ops_users(request: Request):
    page = _int_param(request, "page", 1, 1, 100000)
    if isinstance(page, JSONResponse):
        return page
    per_page = _int_param(request, "per_page", 50, 1, 200)
    if isinstance(per_page, JSONResponse):
        return per_page
    sort = (request.query_params.get("sort") or "created_at").strip()
    invert = sort.startswith("-")
    key = sort.lstrip("-")
    if key not in _USER_SORTS:
        return _err("bad_sort", "sort must be one of " + ", ".join(_USER_SORTS) + " (prefix - to invert)", 400)
    order = _USER_SORTS[key]
    if invert:
        order = order.replace(" DESC", " __A").replace(" ASC", " DESC").replace(" __A", " ASC")

    q = (request.query_params.get("q") or "").strip()[:64]
    where, wparams = "", []
    if q:
        where = "WHERE LOWER(u.username) LIKE ? ESCAPE '\\'"
        wparams.append("%" + _like_escape(q.lower()) + "%")
        if q.isdigit():
            where += " OR u.id = ?"
            wparams.append(int(q))

    since7 = _utc(datetime.now(timezone.utc) - timedelta(days=7))
    with db.cursor() as cur:
        total = _n(cur, f"SELECT COUNT(*) AS n FROM users u {where}", wparams)
        rows = cur.execute(
            "SELECT u.id AS id, u.username AS username, u.created_at AS created_at, "
            "u.is_admin AS is_admin, "
            "COALESCE((SELECT MAX(a.day) FROM user_active_days a WHERE a.user_id=u.id), '') AS last_active, "
            "(SELECT COUNT(*) FROM meals m WHERE m.user_id=u.id) AS meals, "
            "(SELECT COUNT(*) FROM llm_calls l WHERE l.user_id=u.id AND l.at>=?) AS ai_calls_7d, "
            "(SELECT d.client_kind FROM devices d WHERE d.active_user_id=u.id AND d.revoked=0 "
            " ORDER BY COALESCE(d.last_seen,'') DESC LIMIT 1) AS client_kind, "
            "(SELECT k.lock_until FROM auth_locks k WHERE k.subject = 'user:' || u.username) AS lock_until, "
            "(SELECT k.hard FROM auth_locks k WHERE k.subject = 'user:' || u.username) AS lock_hard "
            f"FROM users u {where} ORDER BY {order}, id ASC LIMIT ? OFFSET ?",
            [since7] + wparams + [per_page, (page - 1) * per_page],
        ).fetchall()
        rows = [dict(r) for r in rows]

    users = []
    for r in rows:
        lock_state = admin._lock_state(r["lock_until"], r["lock_hard"])
        users.append({
            "id": r["id"],
            "username": r["username"],
            "created_at": r["created_at"],
            "last_active": r["last_active"] or None,
            "meals": int(r["meals"] or 0),
            "streak_current": feedback.streaks(r["id"])["current"],
            "client_kind": r["client_kind"],
            "is_admin": bool(r["is_admin"]),
            "locked": bool(lock_state),
            "lock_state": lock_state,
            "ai_calls_7d": int(r["ai_calls_7d"] or 0),
        })
    return {"users": users, "total": total, "page": page, "per_page": per_page, "sort": sort}


# ---------------------------------------------------------------------------
# GET /users/{id}
# ---------------------------------------------------------------------------
@router.get("/users/{user_id}")
async def ops_user_detail(user_id: int):
    data = admin.user_page_data(user_id, page=0, per_page=30)
    if data is None:
        return _err("not_found", "No such user", 404)
    username = data["user"]["username"]
    since30 = _utc(datetime.now(timezone.utc) - timedelta(days=30))
    with db.cursor() as cur:
        devices = [dict(r) for r in cur.execute(
            "SELECT device_id, name, created_at, last_seen, req_count, revoked, client_kind, client_version "
            "FROM devices WHERE active_user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()]
        ai_rows = cur.execute(
            "SELECT slot, ok, ms, tokens_in, tokens_out FROM llm_calls WHERE user_id=? AND at>=?",
            (user_id, since30),
        ).fetchall()
        act = cur.execute(
            "SELECT MAX(day) AS last, COUNT(*) AS n FROM user_active_days WHERE user_id=?", (user_id,)
        ).fetchone()
    for d in devices:
        d["revoked"] = bool(d["revoked"])

    subject = security.pin_subject_user(username)
    rem = security.pin_lock_remaining(subject)
    lock_state = "hard" if rem < 0 else ("temp" if rem > 0 else "")
    profile = data["profile"]
    body_fat = None
    if profile:
        body_fat = {"pct": profile.get("body_fat_pct"), "goal_pct": profile.get("body_fat_goal_pct")}

    return {
        "user": data["user"],
        "profile": profile,
        "targets": data["targets"],
        "body_fat": body_fat,
        "day_totals": data["day_totals"],
        "meals": data["meals"],
        "weights": data["weights"],
        "water": data["water"],
        "workouts": data["workouts"],
        "streaks": data["streaks"],
        "weekly_adherence": data["weekly_adherence"],
        "devices": devices,
        "auth": {"failed_logins": security.pin_failures(subject), "locked": bool(lock_state),
                 "lock_state": lock_state, "lock_seconds_left": rem if rem > 0 else None},
        "ai_usage_30d": _slot_usage(ai_rows),
        "last_active": (act["last"] if act else None) or None,
        "active_days_total": int((act["n"] if act else 0) or 0),
    }


# ---------------------------------------------------------------------------
# GET /requests?status=&route=&limit=200
# ---------------------------------------------------------------------------
_STATUS_CLASS_RE = re.compile(r"^([1-5])xx$")


@router.get("/requests")
async def ops_requests(request: Request):
    limit = _int_param(request, "limit", 200, 1, 500)
    if isinstance(limit, JSONResponse):
        return limit
    where: list[str] = []
    params: list = []
    status = (request.query_params.get("status") or "").strip().lower()
    if status:
        m = _STATUS_CLASS_RE.match(status)
        if m:
            base = int(m.group(1)) * 100
            where.append("rl.status>=? AND rl.status<?")
            params += [base, base + 100]
        elif status == "error":
            where.append("rl.status>=?")
            params.append(400)
        elif status.isdigit() and len(status) == 3:
            where.append("rl.status=?")
            params.append(int(status))
        else:
            return _err("bad_status", "status must be a code (e.g. 500), a class (5xx) or 'error'", 400)
    route = (request.query_params.get("route") or "").strip()[:120]
    if route:
        where.append("rl.path LIKE ? ESCAPE '\\'")
        params.append("%" + _like_escape(route) + "%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with db.cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT rl.id AS id, rl.at AS at, rl.method AS method, rl.path AS path, rl.status AS status, "
            "rl.duration_ms AS duration_ms, rl.device_id AS device_id, rl.device_name AS device_name, "
            "rl.user_id AS user_id, u.username AS username "
            f"FROM request_log rl LEFT JOIN users u ON u.id = rl.user_id {clause} "
            "ORDER BY rl.id DESC LIMIT ?",
            params + [limit],
        ).fetchall()]
    return {"requests": rows}


# ---------------------------------------------------------------------------
# GET /audit?limit=200
# ---------------------------------------------------------------------------
@router.get("/audit")
async def ops_audit(request: Request):
    limit = _int_param(request, "limit", 200, 1, 1000)
    if isinstance(limit, JSONResponse):
        return limit
    with db.cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT id, at, action, target, params_json, result FROM ops_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()]
    return {"audit": rows}


# ---------------------------------------------------------------------------
# POST /actions
# ---------------------------------------------------------------------------
class _ActionError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


def _audit(action: str, target: str | None, params: dict, result: str) -> None:
    try:
        pj = json.dumps(params, sort_keys=True, default=str)[:1000]
    except Exception:  # noqa: BLE001
        pj = "{}"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO ops_audit (at, action, target, params_json, result) VALUES (?,?,?,?,?)",
            (security.now_iso(), action[:64], (target or None) and str(target)[:128], pj, result[:200]),
        )


def _user_id(params: dict) -> int:
    v = params.get("user_id")
    if isinstance(v, bool):
        raise _ActionError("bad_params", "user_id must be an integer")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    raise _ActionError("bad_params", "user_id must be an integer")


def _act_revoke_device(p: dict) -> tuple[str, dict]:
    did = p.get("device_id")
    if not isinstance(did, str) or not did.strip() or len(did) > 64:
        raise _ActionError("bad_params", "device_id is required")
    did = did.strip()
    with db.cursor() as cur:
        row = cur.execute("SELECT revoked FROM devices WHERE device_id=?", (did,)).fetchone()
    if row is None:
        raise _ActionError("not_found", "No such device", 404)
    admin.revoke_device(did)
    return did, {"device_id": did, "revoked": True}


def _act_keeper(value: bool):
    def run(p: dict) -> tuple[str, dict]:
        uid = _user_id(p)
        if not accounts.set_admin(uid, value):
            raise _ActionError("not_found", "No such user", 404)
        return str(uid), {"user_id": uid, "is_admin": value}
    return run


def _act_unlock_user(p: dict) -> tuple[str, dict]:
    uid = _user_id(p)
    if not accounts.unlock(uid):
        raise _ActionError("not_found", "No such user", 404)
    return str(uid), {"user_id": uid, "locked": False, "failed_logins": 0}


def _act_set_model(p: dict) -> tuple[str, dict]:
    slot = p.get("slot")
    model = p.get("model")
    fallback = p.get("fallback")
    if slot not in NIM_SLOTS:
        raise _ActionError("bad_slot", "slot must be analyze|body|chat|insights")
    if not isinstance(model, str) or (fallback is not None and not isinstance(fallback, str)):
        raise _ActionError("bad_params", "model must be a string (empty = revert to default); "
                                         "fallback an optional string ('none' disables)")
    try:
        cfg = backends.set_slot(slot, model, fallback)
    except ValueError as e:
        code = str(e) if str(e) in ("bad_task", "bad_model") else "bad_params"
        raise _ActionError(code, "invalid model id" if code == "bad_model" else "invalid slot")
    return slot, {"slot": slot, "config": cfg}


def _act_push_test(p: dict) -> tuple[str, dict]:
    uid = _user_id(p)
    if accounts.get_user(uid) is None:
        raise _ActionError("not_found", "No such user", 404)
    res = admin.send_test_push(uid)
    out = {"user_id": uid, "sent": res.get("sent", 0), "total": res.get("total", 0),
           "pruned": res.get("pruned", 0), "errors": res.get("errors", [])}
    if "message" in res:
        out["message"] = res["message"]
    out["_ok"] = bool(res.get("ok"))
    return str(uid), out


def _act_pairing_token(_p: dict) -> tuple[str, dict]:
    return "", admin.pairing_with_qr_png()


ACTIONS = {
    "revoke_device": _act_revoke_device,
    "grant_keeper": _act_keeper(True),
    "revoke_keeper": _act_keeper(False),
    "unlock_user": _act_unlock_user,
    "set_model": _act_set_model,
    "push_test": _act_push_test,
    "pairing_token": _act_pairing_token,
}


@router.post("/actions")
async def ops_actions(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("bad_request", "Body must be a JSON object", 400)
    if not isinstance(body, dict) or not isinstance(body.get("action"), str):
        return _err("bad_request", 'Body must be a JSON object with a string "action"', 400)
    action = body["action"].strip()
    params = {k: v for k, v in body.items() if k != "action"}
    handler = ACTIONS.get(action)
    if handler is None:
        _audit(action or "?", None, params, "error:unknown_action")
        return _err("unknown_action", "action must be one of " + ", ".join(ACTIONS), 400)
    try:
        target, payload = handler(params)
    except _ActionError as e:
        _audit(action, None, params, f"error:{e.code}")
        return _err(e.code, e.message, e.status)
    ok = bool(payload.pop("_ok", True))
    _audit(action, target, params, "ok" if ok else "failed")
    return {"ok": ok, "action": action, **payload}
