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
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from . import db
from .chat_assistant import remaining, today_totals

VAPID_SUBJECT = "mailto:admin@harahachi.local"

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
    """Return (public_key_b64url, private_key_pem). Generates + persists once."""
    pub = db.get_setting("vapid_public_key")
    priv = db.get_setting("vapid_private_key")
    if pub and priv:
        return pub, priv

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
# Subscription storage
# ---------------------------------------------------------------------------
def subscribe(user_id: int, device_id: str, subscription: dict, tz_offset_min: int) -> None:
    from .security import now_iso
    endpoint = str(subscription.get("endpoint") or "")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO push_subscriptions "
            "(user_id, device_id, endpoint, subscription, tz_offset_min, created_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(user_id, device_id) DO UPDATE SET "
            "endpoint=excluded.endpoint, subscription=excluded.subscription, "
            "tz_offset_min=excluded.tz_offset_min",
            (user_id, device_id, endpoint, json.dumps(subscription), tz_offset_min, now_iso()),
        )


def unsubscribe(user_id: int, device_id: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM push_subscriptions WHERE user_id=? AND device_id=?",
            (user_id, device_id),
        )


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

    _pub, priv_pem = ensure_vapid_keys()
    webpush(
        subscription_info=subscription,
        data=json.dumps(payload),
        vapid_private_key=priv_pem,
        vapid_claims={"sub": VAPID_SUBJECT},
    )


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
        try:
            send_web_push(json.loads(s["subscription"]), payload)
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
    return {"sent": sent, "pruned": pruned, "total": len(subs), "errors": errors}


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


async def run_due(now_utc: datetime | None = None) -> int:
    """Fire any reminders whose local time hits 15:00, 18:00 or 22:00 and haven't
    been sent yet today. Returns the number of notifications sent. Exposed for the
    smoke test to force a tick.

    Catch-up: a tick evaluates every subscription against its current local hour,
    so if the process was asleep across a slot boundary the reminder still fires
    the first tick within that local hour (once — the per-slot/day marker guards
    against repeats). It won't back-fire slots whose hour has fully passed."""
    now_utc = now_utc or datetime.now(timezone.utc)
    sent = 0
    pruned = 0
    _slot_col = {"15": "last_sent_15", "18": "last_sent_18", "22": "last_sent_22"}
    for sub in all_subscriptions():
        hour, local_date = _local_hour(now_utc, sub["tz_offset_min"])
        slot = "15" if hour == 15 else "18" if hour == 18 else "22" if hour == 22 else None
        if slot is None:
            continue
        col = _slot_col[slot]
        if sub.get(col) == local_date:
            continue  # already sent this slot today
        payload = build_reminder(sub["user_id"], slot)
        if payload is None:
            # Mark as handled so we don't re-evaluate every minute this hour.
            _mark_sent(sub["id"], col, local_date)
            continue
        result = send_to_subscriptions([sub], payload)
        sent += result["sent"]
        pruned += result["pruned"]
        # Only mark-sent if the subscription still exists (wasn't pruned).
        if result["pruned"] == 0:
            _mark_sent(sub["id"], col, local_date)
    _status["last_tick"] = now_utc.isoformat()
    _status["last_tick_sent"] = sent
    _status["last_pruned"] = pruned
    _status["ticks"] += 1
    return sent


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
    vapid_ok = bool(db.get_setting("vapid_public_key") and db.get_setting("vapid_private_key"))
    return {
        "running": _status["running"],
        "started_at": _status["started_at"],
        "last_tick": _status["last_tick"],
        "last_tick_sent": _status["last_tick_sent"],
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


def start_scheduler() -> None:
    global _task
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
