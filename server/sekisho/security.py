"""Device keys, hashing, pairing tokens, and per-device rate limiting."""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from . import db

PAIRING_TTL_SECONDS = 300
RATE_LIMIT = 30           # requests
RATE_WINDOW = 60.0        # seconds


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- keys ----
def new_device_key() -> str:
    return "hh_" + secrets.token_hex(16)  # hh_<32 hex>


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_device_id() -> str:
    return "dev_" + secrets.token_hex(8)


# ---- PIN hashing (per-user salt, PBKDF2) ----
def new_pin_salt() -> str:
    return secrets.token_hex(16)


def hash_pin(pin: str, salt: str) -> str:
    """Salted PBKDF2-HMAC-SHA256 of the PIN. Cheap-ish (100k) — PINs are short."""
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return dk.hex()


LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


# ---- pairing tokens ----
def create_pairing_token() -> tuple[str, int]:
    token = secrets.token_urlsafe(18)
    created = datetime.now(timezone.utc)
    expires = created + timedelta(seconds=PAIRING_TTL_SECONDS)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO pairing_tokens (token, created_at, expires_at, used) VALUES (?,?,?,0)",
            (token, created.isoformat(), expires.isoformat()),
        )
    return token, PAIRING_TTL_SECONDS


def claim_pairing_token(token: str, device_name: str) -> tuple[str, str] | None:
    """Validate & consume a single-use pairing token. Returns (device_id, device_key)."""
    now = datetime.now(timezone.utc)
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT token, expires_at, used FROM pairing_tokens WHERE token=?",
            (token,),
        ).fetchone()
        if row is None or row["used"]:
            return None
        expires = datetime.fromisoformat(row["expires_at"])
        if now > expires:
            return None
        # consume it
        cur.execute("UPDATE pairing_tokens SET used=1 WHERE token=?", (token,))

        device_id = new_device_id()
        device_key = new_device_key()
        cur.execute(
            "INSERT INTO devices (device_id, name, key_hash, created_at, req_count, revoked) "
            "VALUES (?,?,?,?,0,0)",
            (device_id, device_name.strip() or "phone", hash_key(device_key), now.isoformat()),
        )
    return device_id, device_key


def purge_expired_tokens() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute("DELETE FROM pairing_tokens WHERE expires_at < ? OR used=1", (now,))


def resolve_device(key: str) -> dict | None:
    """Return device row dict for a bearer key, or None if invalid/revoked.

    Includes ``active_user_id`` (the logged-in user for this device, or None).
    """
    key_hash = hash_key(key)
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT device_id, name, revoked, active_user_id FROM devices WHERE key_hash=?",
            (key_hash,),
        ).fetchone()
        if row is None or row["revoked"]:
            return None
        cur.execute(
            "UPDATE devices SET last_seen=?, req_count=req_count+1 WHERE device_id=?",
            (now_iso(), row["device_id"]),
        )
        return {
            "device_id": row["device_id"],
            "name": row["name"],
            "active_user_id": row["active_user_id"],
        }


def set_active_user(device_id: str, user_id: int | None) -> None:
    with db.cursor() as cur:
        cur.execute("UPDATE devices SET active_user_id=? WHERE device_id=?", (user_id, device_id))


# ---- in-memory sliding-window rate limiter ----
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit_ok(device_id: str) -> bool:
    now = time.monotonic()
    dq = _hits[device_id]
    cutoff = now - RATE_WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= RATE_LIMIT:
        return False
    dq.append(now)
    return True
