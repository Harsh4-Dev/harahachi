"""Web-push reminders (v2).

VAPID keys are generated once and persisted in the settings table (under the
data dir). Subscriptions are per user+device. An asyncio scheduler fires three
per-user local reminders — 15:00 ("have a glass of water" when today's intake is
below half the target), 18:00 ("X kcal / Yg protein to go + a fitting regular
meal from history") and 22:00 (day-close recap, or "don't forget to log") —
using each subscription's tz_offset_min to compute local time. 15:00 is skipped
when the user is already past half their water target; 18:00 is skipped when the
calorie goal is already met; 22:00 becomes a nudge when nothing was logged.

pywebpush is imported lazily so the module (and tests) load even if it's absent;
webpush() is monkeypatchable for the smoke test.

v4 - two ways to drive the same idempotent ``run_due()`` pass:
  * Docker/dev: the in-process asyncio loop (``start_scheduler``), every 60 s.
  * Vercel: NO background loop (instances freeze between requests). An external
    pinger calls ``GET /api/cron/tick`` (Bearer CRON_SECRET) every 10-15 min and a
    once-daily Vercel Cron is the safety net. Each subscription's slot is CLAIMED
    with a conditional UPDATE before sending, so concurrent/duplicate ticks on
    different instances can never double-send.
VAPID keys come from ``VAPID_PUBLIC_KEY``/``VAPID_PRIVATE_KEY`` env when set
(required on Vercel); otherwise (Docker) they are generated once and persisted.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import db
from .chat_assistant import remaining, today_totals

VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT") or "mailto:admin@harahachi.local"


class PushUnconfigured(RuntimeError):
    """No VAPID keys available (Vercel without VAPID_* env)."""


def _utcnow() -> datetime:
    """Clock seam - the smoke tests monkeypatch this to force a slot hour."""
    return datetime.now(timezone.utc)

log = logging.getLogger("sekisho.push")

# Scheduler observability — surfaced on the /admin Status + Push pages so a dead
# task or a stuck send is visible instead of silently failing.
_status: dict = {
    "running": False,
    "started_at": None,   # ISO — when the loop task last (re)started
    "last_tick": None,    # ISO — last time run_due() completed
    "last_tick_sent": 0,  # notifications sent on the last tick
    "ticks": 0,           # total ticks since boot
    "restarts": 0,        # times the supervised task was auto-restarted
    "last_error": None,   # last exception string (loop or send)
    "last_pruned": 0,     # subscriptions pruned on the last tick (404/410)
}


# ---------------------------------------------------------------------------
# VAPID keys (generated once, persisted)
# ---------------------------------------------------------------------------
def ensure_vapid_keys() -> tuple[str, str]:
    """Return (public_key_b64url, private_key). Env wins; else DB; else (Docker
    only) generate + persist once. On Vercel without env keys -> PushUnconfigured."""
    env_pub = (os.environ.get("VAPID_PUBLIC_KEY") or "").strip()
    env_priv = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip().replace("\\n", "\n")
    if env_pub and env_priv:
        return env_pub, env_priv
    pub = db.get_setting("vapid_public_key")
    priv = db.get_setting("vapid_private_key")
    if pub and priv:
        return pub, priv
    from .config import config
    if config.on_vercel:
        raise PushUnconfigured("VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are not set")

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import base64

    key = ec.generate_private_key(ec.SECP256R1())
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    # Uncompressed public point -> base64url (application server key format).
    pub_point = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    pub_b64 = base64.urlsafe_b64encode(pub_point).rstrip(b"=").decode("ascii")

    db.set_setting("vapid_public_key", pub_b64)
    db.set_setting("vapid_private_key", priv_pem)
    return pub_b64, priv_pem


def vapid_public_key() -> str:
    pub, _ = ensure_vapid_keys()
    return pub


# ---------------------------------------------------------------------------
# Subscription validation (SSRF guard, M1)
# ---------------------------------------------------------------------------
# The server POSTs to whatever endpoint a subscription names, so only the real
# browser push services are allowed: https, default port, no userinfo, and a
# host on this list (exact, or a subdomain of a "*." entry).
PUSH_HOSTS_EXACT = frozenset({
    "fcm.googleapis.com",                 # Chrome / Android / Edge (FCM)
    "updates.push.services.mozilla.com",  # Firefox
    "web.push.apple.com",                 # Safari / iOS
})
PUSH_HOST_SUFFIXES = (
    ".push.services.mozilla.com",
    ".notify.windows.com",                # Edge legacy (WNS)
    ".push.apple.com",
)
MAX_SUBSCRIPTION_JSON = 1024              # bytes of the stored JSON
MAX_SUBS_PER_USER = 5                     # oldest dropped beyond this
SEND_TIMEOUT = 5                          # seconds per push-service request
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


class BadSubscription(ValueError):
    """The subscription is not a well-formed browser push subscription."""


def _b64url_decode(value: object, field: str, max_chars: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > max_chars or not _B64URL_RE.fullmatch(value):
        raise BadSubscription(f"keys.{field} must be base64url")
    try:
        return base64.urlsafe_b64decode(value.rstrip("=") + "=" * (-len(value.rstrip("=")) % 4))
    except (binascii.Error, ValueError):
        raise BadSubscription(f"keys.{field} must be base64url")


def push_host_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host in PUSH_HOSTS_EXACT or any(host.endswith(sfx) for sfx in PUSH_HOST_SUFFIXES)


def validate_subscription(sub: object) -> dict:
    """Return a normalized ``{endpoint, keys:{p256dh, auth}}`` or raise BadSubscription."""
    if not isinstance(sub, dict):
        raise BadSubscription("subscription must be an object")
    endpoint = sub.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 800:
        raise BadSubscription("subscription.endpoint required")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise BadSubscription("subscription.endpoint is not a valid URL")
    if parts.scheme != "https":
        raise BadSubscription("subscription.endpoint must be https")
    if parts.username or parts.password or port not in (None, 443):
        raise BadSubscription("subscription.endpoint must not carry credentials or a port")
    if not push_host_allowed(parts.hostname or ""):
        raise BadSubscription("subscription.endpoint is not a known browser push service")
    keys = sub.get("keys")
    if not isinstance(keys, dict):
        raise BadSubscription("subscription.keys required")
    p256dh = _b64url_decode(keys.get("p256dh"), "p256dh", 100)
    auth = _b64url_decode(keys.get("auth"), "auth", 32)
    if len(p256dh) != 65 or p256dh[0] != 4:
        raise BadSubscription("keys.p256dh must be an uncompressed P-256 public key")
    if not 8 <= len(auth) <= 32:
        raise BadSubscription("keys.auth has the wrong length")
    clean = {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}
    if len(json.dumps(clean)) > MAX_SUBSCRIPTION_JSON:
        raise BadSubscription("subscription is too large")
    return clean


# ---------------------------------------------------------------------------
# Subscription storage
# ---------------------------------------------------------------------------
def subscribe(user_id: int, device_id: str, subscription: dict, tz_offset_min: int) -> None:
    """Store a VALIDATED subscription (raises BadSubscription) for user+device,
    keeping at most MAX_SUBS_PER_USER per user (the oldest are dropped)."""
    from .security import now_iso
    clean = validate_subscription(subscription)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO push_subscriptions "
            "(user_id, device_id, endpoint, subscription, tz_offset_min, created_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(user_id, device_id) DO UPDATE SET "
            "endpoint=excluded.endpoint, subscription=excluded.subscription, "
            "tz_offset_min=excluded.tz_offset_min, created_at=excluded.created_at",
            (user_id, device_id, clean["endpoint"], json.dumps(clean), tz_offset_min, now_iso()),
        )
        cur.execute(
            "DELETE FROM push_subscriptions WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM push_subscriptions WHERE user_id=? "
            " ORDER BY created_at DESC, id DESC LIMIT ?)",
            (user_id, user_id, MAX_SUBS_PER_USER),
        )


def unsubscribe(user_id: int, device_id: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM push_subscriptions WHERE user_id=? AND device_id=?",
            (user_id, device_id),
        )


def unsubscribe_device(device_id: str, keep_user_id: int | None = None) -> int:
    """Drop every subscription bound to a device (logout / account switch /
    revoke), optionally keeping the one owned by ``keep_user_id``."""
    with db.cursor() as cur:
        if keep_user_id is None:
            cur.execute("DELETE FROM push_subscriptions WHERE device_id=?", (device_id,))
        else:
            cur.execute("DELETE FROM push_subscriptions WHERE device_id=? AND user_id<>?",
                        (device_id, keep_user_id))
        return max(0, cur.rowcount or 0)


def subscription_count() -> int:
    with db.cursor() as cur:
        row = cur.execute("SELECT COUNT(*) c FROM push_subscriptions").fetchone()
    return int(row["c"])


def all_subscriptions() -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM push_subscriptions").fetchall()
    return [dict(r) for r in rows]


def subscriptions_for_user(user_id: int) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM push_subscriptions WHERE user_id=?", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def _prune_subscription(sub_id: int) -> None:
    """Delete a subscription the push service has rejected as gone (404/410)."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM push_subscriptions WHERE id=?", (sub_id,))
    log.info("pruned dead push subscription id=%s", sub_id)


def _webpush_status(exc: Exception) -> int | None:
    """Best-effort HTTP status from a pywebpush WebPushException (has .response)."""
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) if resp is not None else None


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def send_web_push(subscription: dict, payload: dict) -> None:
    """Send one web-push notification. Monkeypatched in tests.

    Raises on failure (pywebpush raises WebPushException). Callers that want to
    prune dead endpoints inspect the exception via ``_webpush_status``.
    """
    from pywebpush import webpush  # lazy import

    subscription = validate_subscription(subscription)  # never POST anywhere else
    _pub, priv = ensure_vapid_keys()
    webpush(
        subscription_info=subscription,
        data=json.dumps(payload),
        vapid_private_key=_vapid_signer(priv),
        vapid_claims={"sub": VAPID_SUBJECT},
        timeout=SEND_TIMEOUT,
        requests_session=_no_redirect_session(),
    )


def _no_redirect_session():
    """A requests Session that never follows redirects (a push service answering
    3xx must not bounce us to an internal URL) and ignores proxy env vars."""
    import requests

    class _NoRedirect(requests.Session):
        def request(self, method, url, **kwargs):  # type: ignore[override]
            kwargs["allow_redirects"] = False
            return super().request(method, url, **kwargs)

    sess = _NoRedirect()
    sess.trust_env = False
    # Do NOT set max_redirects = 0: requests still runs its redirect generator
    # once with allow_redirects=False (to fill response._next), and a limit of 0
    # makes that raise TooManyRedirects. allow_redirects=False already ensures
    # no redirect is ever followed.
    return sess


def _vapid_signer(priv: str):
    """py_vapid signer from a PEM (Docker-generated) or base64url raw/DER key (env).

    pywebpush's string path only understands base64url raw/DER, so a PEM must be
    loaded explicitly (passing the PEM string straight through fails to parse)."""
    from py_vapid import Vapid
    if "BEGIN" in priv:
        return Vapid.from_pem(priv.encode("ascii"))
    return Vapid.from_string(private_key=priv)


def send_to_subscriptions(subs: list[dict], payload: dict) -> dict:
    """Send ``payload`` to each subscription row, pruning any the service reports
    as gone (404/410) and collecting errors. Returns a result summary dict.

    Shared by the admin "send test" endpoint and the scheduler tick so send +
    prune + error-logging behave identically everywhere.
    """
    sent = 0
    pruned = 0
    errors: list[str] = []
    for s in subs:
        # Re-validate at send time: rows stored before the allowlist existed
        # (or written behind the API's back) are skipped and pruned, never sent.
        try:
            sub_info = validate_subscription(json.loads(s["subscription"]))
        except (BadSubscription, ValueError, TypeError) as e:
            errors.append(f"invalid: {str(e)[:120]}")
            _prune_subscription(s["id"])
            pruned += 1
            continue
        try:
            send_web_push(sub_info, payload)
            sent += 1
        except Exception as e:  # noqa: BLE001
            status = _webpush_status(e)
            msg = f"{status or '?'}: {str(e)[:160]}"
            errors.append(msg)
            _status["last_error"] = msg
            log.warning("web-push send failed (sub id=%s): %s", s.get("id"), msg)
            if status in (404, 410):
                _prune_subscription(s["id"])
                pruned += 1
    try:  # v6 ops analytics: per-day push counters (never breaks a send)
        from . import usage
        usage.bump_stats({"pushes_sent": sent, "pushes_failed": len(subs) - sent})
    except Exception:  # noqa: BLE001
        pass
    return {"sent": sent, "pruned": pruned, "total": len(subs), "errors": errors}


def vapid_configured() -> bool:
    """True when web push can sign messages (env keys, or keys persisted in the DB)."""
    if os.environ.get("VAPID_PUBLIC_KEY") and os.environ.get("VAPID_PRIVATE_KEY"):
        return True
    return bool(db.get_setting("vapid_public_key") and db.get_setting("vapid_private_key"))


def _fitting_meal(user_id: int, rem_kcal: int) -> str | None:
    """A regular meal from history whose kcal fits under the remaining budget."""
    from .suggestions import rank_candidates
    from .models import Targets
    rem = remaining(user_id)
    if rem is None:
        return None
    cands = rank_candidates(user_id, rem, limit=1)
    if cands:
        c = cands[0]
        return f"{c['dish']} (~{c['calories']} kcal)"
    return None


def build_reminder(user_id: int, slot: str) -> dict | None:
    """Build the notification payload for a slot ('15', '18' or '22'), or None to skip."""
    rem = remaining(user_id)
    tot = today_totals(user_id)

    if slot == "15":
        # Water nudge — fire only when the user is below half their daily target.
        from . import feedback
        from datetime import datetime as _dt, timezone as _tz
        target = feedback.water_target_ml(user_id)
        today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        drunk = feedback.water_for_day(user_id, today)
        if target <= 0 or drunk >= target // 2:
            return None  # already past halfway -> skip
        litres_left = max(0, target - drunk) / 1000.0
        return {
            "title": "水 Harahachi",
            "body": f"Have a glass of water — you're at {drunk} ml of {target} ml today "
                    f"({litres_left:.1f} L to go).",
            "slot": "15",
        }

    if slot == "18":
        if rem is None:
            return None  # no profile/targets -> nothing meaningful to say
        if rem.calories <= 0 and rem.protein_g <= 0:
            return None  # goal already met -> skip
        meal = _fitting_meal(user_id, rem.calories)
        body = f"{rem.calories} kcal and {rem.protein_g}g protein to go today."
        if meal:
            body += f" {meal} would land you on target."
        return {"title": "誰 Harahachi", "body": body, "slot": "18"}

    if slot == "22":
        if tot["calories"] == 0:
            return {"title": "Harahachi", "body": "Don't forget to log today's meals.", "slot": "22"}
        if rem is None:
            return {"title": "Harahachi",
                    "body": f"You logged {tot['calories']} kcal today. Rest well.", "slot": "22"}
        if rem.calories > 0:
            status = f"{rem.calories} kcal under target — a light day."
        elif rem.calories == 0:
            status = "right on target today. Beautifully done."
        else:
            status = "a touch over target today."
        return {"title": "Harahachi",
                "body": f"Day's close: {status} Hara hachi bu — eat to eight-tenths tomorrow.",
                "slot": "22"}
    return None


def _local_hour(now_utc: datetime, tz_offset_min: int) -> tuple[int, str]:
    local = now_utc + timedelta(minutes=tz_offset_min)
    return local.hour, local.strftime("%Y-%m-%d")


# Wall-clock budget for one scheduler pass. Vercel kills the function at
# maxDuration (300 s); stop well before so housekeeping + the response still
# happen. Unsent subscriptions are not claimed and go out on the next tick.
RUN_DUE_BUDGET = 200.0


async def run_due(now_utc: datetime | None = None, budget_s: float = RUN_DUE_BUDGET) -> int:
    """Fire any reminders whose local time hits 15:00, 18:00 or 22:00 and haven't
    been sent yet today. Returns the number of notifications sent. Exposed for the
    smoke test to force a tick.

    Catch-up: a tick evaluates every subscription against its current local hour,
    so if the process was asleep across a slot boundary the reminder still fires
    the first tick within that local hour (once — the per-slot/day marker guards
    against repeats). It won't back-fire slots whose hour has fully passed."""
    now_utc = now_utc or _utcnow()
    deadline = time.monotonic() + budget_s
    sent = 0
    pruned = 0
    _slot_col = {"15": "last_sent_15", "18": "last_sent_18", "22": "last_sent_22"}
    for sub in all_subscriptions():
        if time.monotonic() > deadline:
            _status["last_error"] = "tick budget exhausted; remaining reminders deferred"
            break
        hour, local_date = _local_hour(now_utc, sub["tz_offset_min"])
        slot = "15" if hour == 15 else "18" if hour == 18 else "22" if hour == 22 else None
        if slot is None:
            continue
        col = _slot_col[slot]
        if sub.get(col) == local_date:
            continue  # already sent this slot today
        # Claim the (subscription, slot, local day) atomically BEFORE doing any
        # work: only one tick - on any instance - wins; the rest skip.
        if not _claim(sub["id"], col, local_date):
            continue
        payload = build_reminder(sub["user_id"], slot)
        if payload is None:
            continue  # claimed = handled; don't re-evaluate this hour
        result = await asyncio.to_thread(send_to_subscriptions, [sub], payload)
        sent += result["sent"]
        pruned += result["pruned"]
    _status["last_tick"] = now_utc.isoformat()
    _status["last_tick_sent"] = sent
    _status["last_pruned"] = pruned
    _status["ticks"] += 1
    try:
        db.set_setting("push_last_tick", now_utc.isoformat())
        db.set_setting("push_last_tick_sent", str(sent))
    except Exception:  # noqa: BLE001 - observability only
        pass
    return sent


def _claim(sub_id: int, col: str, local_date: str) -> bool:
    with db.cursor() as cur:
        cur.execute(
            f"UPDATE push_subscriptions SET {col}=? WHERE id=? AND ({col} IS NULL OR {col}<>?)",
            (local_date, sub_id, local_date),
        )
        return cur.rowcount == 1


def _mark_sent(sub_id: int, col: str, local_date: str) -> None:
    with db.cursor() as cur:
        cur.execute(f"UPDATE push_subscriptions SET {col}=? WHERE id=?", (local_date, sub_id))


def _next_fire_utc(now_utc: datetime, tz_offset_min: int) -> datetime:
    """Next UTC instant at which this subscription's local clock hits 15:00,
    18:00 or 22:00 (whichever comes first)."""
    local_now = now_utc + timedelta(minutes=tz_offset_min)
    candidates = []
    for hh in (15, 18, 22):
        cand = local_now.replace(hour=hh, minute=0, second=0, microsecond=0)
        if cand <= local_now:
            cand += timedelta(days=1)
        candidates.append(cand)
    nearest_local = min(candidates)
    return nearest_local - timedelta(minutes=tz_offset_min)


def scheduler_status() -> dict:
    """Observable scheduler state for the admin dashboard: running flag, last
    tick, next fire time(s), counters and the last error."""
    now = datetime.now(timezone.utc)
    subs = all_subscriptions()
    next_fires = sorted(
        _next_fire_utc(now, s["tz_offset_min"]).isoformat() for s in subs
    )
    vapid_ok = vapid_configured()
    from .config import config
    last_tick = _status["last_tick"] or db.get_setting("push_last_tick")
    if _status["last_tick"]:
        last_sent = _status["last_tick_sent"]
    else:
        last_sent = int(db.get_setting("push_last_tick_sent") or 0)
    return {
        "mode": "cron" if config.on_vercel else "loop",
        "running": _status["running"],
        "started_at": _status["started_at"],
        "last_tick": last_tick,
        "last_tick_sent": last_sent,
        "ticks": _status["ticks"],
        "restarts": _status["restarts"],
        "last_error": _status["last_error"],
        "last_pruned": _status["last_pruned"],
        "next_fire": next_fires[0] if next_fires else None,
        "subscriptions": len(subs),
        "vapid_configured": vapid_ok,
    }


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------
_task: asyncio.Task | None = None


async def _loop() -> None:
    _status["running"] = True
    _status["started_at"] = datetime.now(timezone.utc).isoformat()
    log.info("push scheduler loop started")
    try:
        while True:
            try:
                await run_due()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — keep the loop alive on any error
                _status["last_error"] = str(e)[:200]
                log.exception("push scheduler tick failed")
            await asyncio.sleep(60)  # check every minute
    finally:
        _status["running"] = False


def _on_task_done(task: "asyncio.Task") -> None:
    """If the loop task dies unexpectedly (not a clean cancel), log it and spin a
    fresh one so reminders don't silently stop for the life of the process."""
    global _task
    if task.cancelled():
        return
    exc = task.exception()
    _status["running"] = False
    if exc is not None:
        _status["last_error"] = f"loop died: {str(exc)[:180]}"
        log.error("push scheduler task died: %s — restarting", exc)
    _task = None
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _status["restarts"] += 1
            _task = loop.create_task(_loop())
            _task.add_done_callback(_on_task_done)
    except RuntimeError:
        pass


async def cron_tick() -> dict:
    """One serverless scheduler pass + housekeeping (GET /api/cron/tick)."""
    from . import security
    now = _utcnow()
    sent = await run_due(now)
    housekeeping: dict = {}
    try:
        housekeeping["counters_purged"] = security.purge_expired_counters()
        security.purge_expired_tokens()
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM request_log WHERE id NOT IN "
                "(SELECT id FROM request_log ORDER BY id DESC LIMIT 500)"
            )
            # LLM text cache: keep only today's and yesterday's rows.
            cutoff = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            cur.execute("DELETE FROM llm_cache WHERE day < ?", (cutoff,))
    except Exception as e:  # noqa: BLE001
        housekeeping["error"] = str(e)[:160]
    return {"ok": True, "at": now.isoformat(), "sent": sent,
            "subscriptions": subscription_count(), "housekeeping": housekeeping}


def start_scheduler() -> None:
    """Start the in-process loop - Docker/dev only. Never on Vercel."""
    global _task
    from .config import config
    if config.on_vercel:
        return
    if _task is not None:
        return
    try:
        ensure_vapid_keys()
    except Exception:
        log.exception("failed to ensure VAPID keys at scheduler start")
    _task = asyncio.create_task(_loop())
    _task.add_done_callback(_on_task_done)


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
    _status["running"] = False
