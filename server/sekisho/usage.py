"""Usage instrumentation for the ops API (v6).

Cheap enough for serverless, and portable across SQLite and Postgres:

* ``record_llm_call``   one ``llm_calls`` row per upstream LLM call (slot, model,
                        ok, ms, tokens from the provider's ``usage``, error code).
                        The calling user comes from a request-scoped ContextVar
                        set by the auth dependency (``set_request_user``).
* ``parse_client``      validates ``X-Harahachi-Client: apk|pwa|web/<version>``;
                        the device row is updated in the same UPDATE that already
                        bumps ``last_seen`` (no extra write).
* ``mark_active``       ``user_active_days`` upsert, at most once per user per day
                        per instance (in-process cache), ``ON CONFLICT DO NOTHING``.
* ``bump_stats``        per-day counters (``daily_stats``) that outlive the 500-row
                        request log: requests, 5xx, a latency histogram, cap hits,
                        pushes. One multi-row upsert statement per call.

"A day" is the calendar day in ``SEKISHO_TZ`` (default Asia/Kolkata). Day
bucketing for timestamp columns is done in SQL with plain string comparisons
against precomputed UTC boundaries (see ``day_bounds_utc``/``bucket_case``), so
the same SQL runs on both engines without dialect date functions. Every stored
timestamp is UTC ISO-8601 (``...+00:00`` / ``...Z``), and a 19-char boundary
``YYYY-MM-DDTHH:MM:SS`` orders correctly against all of those suffixes.
"""
from __future__ import annotations

import contextvars
import re
import threading
from datetime import date, datetime, time as dtime, timedelta, timezone, tzinfo

from . import db

# ---------------------------------------------------------------------------
# Timezone / day helpers
# ---------------------------------------------------------------------------
_tz_cache: dict[str, tzinfo] = {}
_FIXED_RE = re.compile(r"^(?:UTC|GMT)?([+-])(\d{1,2}):?(\d{2})$")
_FIXED_FALLBACKS = {"Asia/Kolkata": (5, 30), "Asia/Calcutta": (5, 30)}


def _tz_name() -> str:
    from .config import config
    return config.tz_name or "Asia/Kolkata"


def tz() -> tzinfo:
    """The analytics timezone. Falls back to a fixed offset when the platform
    has no tz database (e.g. Windows without ``tzdata``)."""
    name = _tz_name()
    cached = _tz_cache.get(name)
    if cached is not None:
        return cached
    zone: tzinfo
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError / ValueError
        if name.upper() in ("UTC", "GMT", "Z"):
            zone = timezone.utc
        elif name in _FIXED_FALLBACKS:
            h, m = _FIXED_FALLBACKS[name]
            zone = timezone(timedelta(hours=h, minutes=m))
        else:
            m = _FIXED_RE.match(name)
            if m:
                sign = -1 if m.group(1) == "-" else 1
                zone = timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
            else:
                zone = timezone.utc
    _tz_cache[name] = zone
    return zone


def local_today(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(tz()).date()


def _utc_str(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def day_start_utc(d: date) -> str:
    """UTC instant (``YYYY-MM-DDTHH:MM:SS``) at which local day ``d`` begins."""
    return _utc_str(datetime.combine(d, dtime(0, 0), tzinfo=tz()))


def day_bounds_utc(first: date, n_days: int) -> list[str]:
    """n_days + 1 ascending UTC boundaries: start of each local day, then the end."""
    return [day_start_utc(first + timedelta(days=i)) for i in range(n_days + 1)]


def bucket_case(col: str, bounds: list[str]) -> tuple[str, list[str]]:
    """``CASE WHEN col < b1 THEN 0 WHEN col < b2 THEN 1 ... END`` + its params.

    Rows must already be filtered to ``bounds[0] <= col < bounds[-1]``. Portable:
    only string comparisons (no dialect date functions)."""
    parts = ["CASE"]
    params: list[str] = []
    for i in range(1, len(bounds)):
        parts.append(f" WHEN {col} < ? THEN {i - 1}")
        params.append(bounds[i])
    parts.append(" END")
    return "".join(parts), params


# ---------------------------------------------------------------------------
# Request-scoped user (for llm_calls attribution)
# ---------------------------------------------------------------------------
_request_user: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "sekisho_request_user", default=None)


def set_request_user(user_id: int | None) -> None:
    _request_user.set(user_id)


def request_user() -> int | None:
    return _request_user.get()


# ---------------------------------------------------------------------------
# llm_calls
# ---------------------------------------------------------------------------
SLOTS = ("analyze", "body", "chat", "insights", "passthrough")


def usage_tokens(data) -> tuple[int | None, int | None]:
    """(prompt_tokens, completion_tokens) from an OpenAI-style ``usage`` block."""
    if not isinstance(data, dict):
        return None, None
    u = data.get("usage")
    if not isinstance(u, dict):
        return None, None

    def _int(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            return None
        return int(v)
    return _int(u.get("prompt_tokens")), _int(u.get("completion_tokens"))


def error_code_for(exc: BaseException | None = None, status: int | None = None) -> str:
    """A short, secret-free classification of a failed upstream call."""
    if status is not None and status >= 400:
        return f"http_{int(status)}"
    if exc is None:
        return "bad_response"
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.TransportError):
            return "connect"
    except Exception:  # noqa: BLE001
        pass
    name = type(exc).__name__
    if name == "BackendURLNotAllowed":
        return "url_not_allowed"
    if name == "BackendUnavailable":
        return "bad_response"
    return "error"


def record_llm_call(slot: str, model: str | None, ok: bool, ms: int,
                    tokens_in: int | None = None, tokens_out: int | None = None,
                    error_code: str | None = None, user_id: int | None = None) -> None:
    """Insert one ``llm_calls`` row. Never raises (instrumentation must not break a call)."""
    if slot not in SLOTS:
        slot = "chat"
    try:
        from .security import now_iso
        uid = user_id if user_id is not None else request_user()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO llm_calls (at, user_id, slot, model, ok, ms, tokens_in, tokens_out, error_code) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (now_iso(), uid, slot, (model or "")[:128] or None, 1 if ok else 0, int(ms),
                 tokens_in, tokens_out, (error_code or None) if not ok else None),
            )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# X-Harahachi-Client
# ---------------------------------------------------------------------------
CLIENT_KINDS = ("apk", "pwa", "web")
_CLIENT_RE = re.compile(r"^(apk|pwa|web)/([0-9A-Za-z][0-9A-Za-z.+_-]{0,31})$")


def parse_client(header: str | None) -> tuple[str, str] | None:
    """``apk/1.2.3`` -> ("apk", "1.2.3"); anything malformed -> None (ignored)."""
    if not header or len(header) > 40:
        return None
    m = _CLIENT_RE.match(header.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


# ---------------------------------------------------------------------------
# Daily active users
# ---------------------------------------------------------------------------
_active_lock = threading.Lock()
_active_seen: dict[int, str] = {}
_ACTIVE_CACHE_MAX = 20_000


def mark_active(user_id: int | None, now: datetime | None = None) -> bool:
    """Record that ``user_id`` was active today. Returns True when a DB write was
    attempted (first time this instance sees the user today). Never raises."""
    if user_id is None:
        return False
    day = local_today(now).isoformat()
    with _active_lock:
        if _active_seen.get(user_id) == day:
            return False
        if len(_active_seen) >= _ACTIVE_CACHE_MAX:
            _active_seen.clear()
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO user_active_days (user_id, day) VALUES (?,?) "
                "ON CONFLICT(user_id, day) DO NOTHING",
                (int(user_id), day),
            )
    except Exception:  # noqa: BLE001
        return False
    with _active_lock:
        _active_seen[user_id] = day
    return True


def reset_active_cache() -> None:
    """Tests: forget which users this instance already marked."""
    with _active_lock:
        _active_seen.clear()


# ---------------------------------------------------------------------------
# daily_stats
# ---------------------------------------------------------------------------
# Request-latency histogram bucket upper bounds (ms). The last bucket is open.
LAT_BUCKETS = (10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000, 3000,
               5000, 10000, 20000, 60000)
LAT_OPEN_MS = 120000  # value reported when p95 lands in the open-ended bucket


def lat_metric(ms: int) -> str:
    for b in LAT_BUCKETS:
        if ms <= b:
            return f"lat_le_{b}"
    return "lat_gt_max"


def lat_bound(metric: str) -> int:
    if metric == "lat_gt_max":
        return LAT_OPEN_MS
    return int(metric[len("lat_le_"):])


def p95_from_hist(hist: dict[str, int], q: float = 0.95) -> int:
    """Percentile (bucket upper bound) from a ``{lat_* metric: n}`` histogram."""
    items = sorted(((lat_bound(k), n) for k, n in hist.items() if n), key=lambda x: x[0])
    total = sum(n for _, n in items)
    if not total:
        return 0
    need = q * total
    acc = 0
    for bound, n in items:
        acc += n
        if acc >= need:
            return bound
    return items[-1][0]


def bump_stats(metrics: dict[str, int], now: datetime | None = None,
               cur=None) -> None:
    """Add ``metrics`` ({metric: n}) to today's counters in ONE statement.
    Pass ``cur`` to reuse an open cursor. Never raises."""
    metrics = {k: int(v) for k, v in metrics.items() if v}
    if not metrics:
        return
    day = local_today(now).isoformat()
    values = ",".join(["(?,?,?)"] * len(metrics))
    params: list = []
    for k, v in metrics.items():
        params.extend((day, k, v))
    sql = (f"INSERT INTO daily_stats (day, metric, n) VALUES {values} "
           "ON CONFLICT(day, metric) DO UPDATE SET n = daily_stats.n + excluded.n")
    try:
        if cur is not None:
            cur.execute(sql, params)
        else:
            with db.cursor() as c:
                c.execute(sql, params)
    except Exception:  # noqa: BLE001
        pass


def request_metrics(status: int, duration_ms: int) -> dict[str, int]:
    m = {"requests": 1, lat_metric(max(0, int(duration_ms))): 1}
    if status >= 500:
        m["errors_5xx"] = 1
    return m
