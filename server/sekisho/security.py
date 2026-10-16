"""Device keys, hashing, pairing tokens, and DB-backed abuse limits.

v4: every limit is a **fixed-window counter in the database** (table
``counters``), never process memory, so it holds across the many short-lived
instances Vercel runs (and across Docker restarts):

* per-device request rate     bucket ``rl:dev:<device_id>``   window = 1 minute
* daily LLM caps               bucket ``llm:user:<id>`` / ``llm:dev:<device_id>`` /
                               ``llm:global``                   window = UTC day
* PIN failures                 bucket ``pinfail:user:<name>`` (until success) /
                               ``pinfail:dev:<device_id>`` (UTC day)
* registrations per device     bucket ``reg:dev:<device_id>``   window = UTC day
* admin login lockout          bucket ``adm:ip:<ip>`` / ``adm:all``  window = 15 min

A single ``INSERT .. ON CONFLICT DO UPDATE .. RETURNING`` statement makes the
increment atomic on both SQLite (>= 3.35) and Postgres.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time
from datetime import datetime, timedelta, timezone

from . import db

PAIRING_TTL_SECONDS = 300
RATE_LIMIT = 30           # requests (default; config.rate_limit_per_min overrides)
RATE_WINDOW = 60.0        # seconds

ADMIN_LOCK_WINDOW = 15 * 60   # seconds
ADMIN_LOCK_PER_IP = 5         # failed logins per IP per window
ADMIN_LOCK_GLOBAL = 50        # failed logins across all IPs per window


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- keys ----
def new_device_key() -> str:
    return "hh_" + secrets.token_hex(16)  # hh_<32 hex>


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_device_id() -> str:
    return "dev_" + secrets.token_hex(8)


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---- PIN hashing (per-user salt, PBKDF2) ----
def new_pin_salt() -> str:
    return secrets.token_hex(16)


def hash_pin(pin: str, salt: str) -> str:
    """Salted PBKDF2-HMAC-SHA256 of the PIN. Cheap-ish (100k) — PINs are short."""
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return dk.hex()


# Fixed salt for the dummy PBKDF2 run on unknown usernames, so a login for a
# name that does not exist costs the same time as a wrong PIN for one that does.
DUMMY_PIN_SALT = "0" * 32


def dummy_pin_hash(pin: str) -> None:
    hash_pin(pin, DUMMY_PIN_SALT)


# ---- PIN brute-force lockout (per account AND per device) ----
# Failures are counted with bump_counter (one atomic INSERT .. ON CONFLICT ..
# RETURNING, never read-modify-write). Every LOCK step escalates; the counter is
# only reset by a successful login (or an admin unlock), never by a lock.
#   5 failures -> 1 min, 10 -> 15 min, 15 -> 1 h, 20 -> locked until an admin unlocks.
# The attempt is counted BEFORE the PIN is verified, so a concurrent burst can
# never verify more than PIN_HARD_LOCK_AT guesses.
PIN_LOCK_STEPS = {5: 60, 10: 15 * 60, 15: 60 * 60}
PIN_HARD_LOCK_AT = 20
_FAR_FUTURE = 10 * 365 * 86400

# Back-compat names (first lock step).
LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


def pin_subject_user(username: str) -> str:
    """Per-account lock subject. Keyed by the *username* (not the id) so unknown
    usernames lock exactly like real ones — lockout never reveals existence."""
    return "user:" + username


def pin_subject_device(device_id: str) -> str:
    return "dev:" + device_id


def _pin_win(subject: str, now: float) -> int:
    # Device failures are counted per UTC day (a phone's occasional typos must
    # not add up forever); account failures persist until a successful login.
    return int(now // 86400) if subject.startswith("dev:") else 0


def pin_lock_remaining(subject: str, now: float | None = None) -> int:
    """0 = not locked, >0 = seconds left, -1 = locked until an admin unlocks."""
    now = now or time.time()
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT lock_until, hard FROM auth_locks WHERE subject=?", (subject,)
        ).fetchone()
    if row is None:
        return 0
    if row["hard"]:
        return -1
    rem = int(row["lock_until"]) - int(now)
    return rem if rem > 0 else 0


def pin_count_attempt(subject: str, now: float | None = None) -> int:
    """Atomically count one PIN attempt for ``subject``; returns the new count."""
    now = now or time.time()
    win = _pin_win(subject, now)
    return bump_counter("pinfail:" + subject, win, int(now) + _FAR_FUTURE)


def pin_uncount_attempt(subject: str, now: float | None = None) -> None:
    """Atomically give back a counted attempt (a successful login on a device)."""
    now = now or time.time()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE counters SET n = n - 1 WHERE bucket=? AND win=? AND n > 0",
            ("pinfail:" + subject, _pin_win(subject, now)),
        )


def pin_failures(subject: str, now: float | None = None) -> int:
    now = now or time.time()
    return read_counter("pinfail:" + subject, _pin_win(subject, now))


def pin_apply_lock(subject: str, n: int, now: float | None = None) -> int:
    """Apply the escalating lock for the n-th failure. Returns the lock that now
    applies (0 none, >0 seconds, -1 hard)."""
    now = int(now or time.time())
    if n >= PIN_HARD_LOCK_AT:
        until, hard = 0, 1
    elif n in PIN_LOCK_STEPS:
        until, hard = now + PIN_LOCK_STEPS[n], 0
    else:
        return 0
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO auth_locks (subject, lock_until, hard, locked_at) VALUES (?,?,?,?) "
            "ON CONFLICT(subject) DO UPDATE SET lock_until=excluded.lock_until, "
            "hard=CASE WHEN auth_locks.hard=1 THEN 1 ELSE excluded.hard END, "
            "locked_at=excluded.locked_at",
            (subject, until, hard, now_iso()),
        )
    return -1 if hard else until - now


def pin_reset(subject: str) -> None:
    """Clear failures + any lock (successful login / admin unlock)."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM counters WHERE bucket=?", ("pinfail:" + subject,))
        cur.execute("DELETE FROM auth_locks WHERE subject=?", (subject,))


# ---- registration limit (per device) ----
REGISTER_PER_DEVICE_PER_DAY = 3


def register_allowed(device_id: str, now: float | None = None) -> bool:
    now = now or time.time()
    day = int(now // 86400)
    return bump_counter("reg:dev:" + device_id, day, (day + 2) * 86400) <= REGISTER_PER_DEVICE_PER_DAY


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
    """Validate & consume a single-use pairing token. Returns (device_id, device_key).

    The consume is a conditional UPDATE (``used=0`` guard + rowcount check), so two
    instances racing on the same token can never both mint a device.
    """
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
        # consume it (atomically)
        cur.execute("UPDATE pairing_tokens SET used=1 WHERE token=? AND used=0", (token,))
        if cur.rowcount != 1:
            return None

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


def resolve_device(key: str, client: tuple[str, str] | None = None) -> dict | None:
    """Return device row dict for a bearer key, or None if invalid/revoked.

    Includes ``active_user_id`` (the logged-in user for this device, or None).
    ``client`` = validated (kind, version) from X-Harahachi-Client; stored in the
    same UPDATE that bumps last_seen (no extra write).
    """
    key_hash = hash_key(key)
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT device_id, name, revoked, active_user_id FROM devices WHERE key_hash=?",
            (key_hash,),
        ).fetchone()
        if row is None or row["revoked"]:
            return None
        if client is not None:
            cur.execute(
                "UPDATE devices SET last_seen=?, req_count=req_count+1, client_kind=?, "
                "client_version=? WHERE device_id=?",
                (now_iso(), client[0], client[1], row["device_id"]),
            )
        else:
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


# ---------------------------------------------------------------------------
# DB-backed fixed-window counters (shared by every instance)
# ---------------------------------------------------------------------------
def bump_counter(bucket: str, win: int, expires: int) -> int:
    """Atomically increment (bucket, win) and return the new count."""
    with db.cursor() as cur:
        row = cur.execute(
            "INSERT INTO counters (bucket, win, n, expires) VALUES (?,?,1,?) "
            "ON CONFLICT(bucket, win) DO UPDATE SET n = counters.n + 1 "
            "RETURNING n",
            (bucket, int(win), int(expires)),
        ).fetchone()
    return int(row["n"])


def read_counter(bucket: str, win: int) -> int:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT n FROM counters WHERE bucket=? AND win=?", (bucket, int(win))
        ).fetchone()
    return int(row["n"]) if row else 0


def reset_counters(prefix: str) -> None:
    """Delete every counter whose bucket starts with ``prefix`` (tests/admin)."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM counters WHERE substr(bucket,1,?)=?", (len(prefix), prefix))


def purge_expired_counters(now: float | None = None) -> int:
    now = int(now or time.time())
    with db.cursor() as cur:
        cur.execute("DELETE FROM counters WHERE expires < ?", (now,))
        return max(0, cur.rowcount or 0)


class RateLimiter:
    """Stateless fixed-window limiter: all state lives in the DB, so any number of
    instances (or fresh objects) share the same counts."""

    def __init__(self, limit: int | None = None, window_s: int = 60, prefix: str = "rl:dev:"):
        self._limit = limit
        self.window_s = window_s
        self.prefix = prefix

    @property
    def limit(self) -> int:
        if self._limit is not None:
            return self._limit
        from .config import config
        return config.rate_limit_per_min

    def hit(self, key: str, now: float | None = None) -> bool:
        now = now or time.time()
        win = int(now // self.window_s)
        n = bump_counter(self.prefix + key, win, (win + 2) * self.window_s)
        return n <= self.limit


_device_limiter = RateLimiter()


def rate_limit_ok(device_id: str) -> bool:
    return _device_limiter.hit(device_id)


def llm_cap_ok(subject: str, now: float | None = None, cap: int | None = None) -> bool:
    """One daily cap on calls that spend NIM credits (bucket ``llm:<subject>``)."""
    from .config import config
    cap = config.daily_llm_cap if cap is None else cap
    if cap <= 0:
        return True
    now = now or time.time()
    day = int(now // 86400)
    return bump_counter("llm:" + subject, day, (day + 2) * 86400) <= cap


def llm_caps_exceeded(user_id: int | None, device_id: str | None,
                      now: float | None = None) -> str | None:
    """Check the per-user, per-device and global daily LLM caps (in that order).

    Returns the name of the cap that is exhausted ("user" | "device" | "global")
    or None when the call may spend credits. Checked in order and short-circuited,
    so a request refused by its own user/device cap never eats the global budget.
    """
    from .config import config
    now = now or time.time()
    if user_id is not None and not llm_cap_ok(f"user:{user_id}", now, config.daily_llm_cap):
        return "user"
    if device_id is not None and not llm_cap_ok(f"dev:{device_id}", now, config.device_daily_llm_cap):
        return "device"
    if not llm_cap_ok("global", now, config.global_daily_llm_cap):
        return "global"
    return None


# ---------------------------------------------------------------------------
# Admin login lockout (DB-backed)
# ---------------------------------------------------------------------------
def _admin_window(now: float | None = None) -> int:
    return int((now or time.time()) // ADMIN_LOCK_WINDOW)


def admin_locked(ip: str) -> int:
    """Seconds until unlock if this IP (or the whole admin) is locked, else 0."""
    now = time.time()
    win = _admin_window(now)
    if read_counter("adm:ip:" + ip, win) >= ADMIN_LOCK_PER_IP or \
            read_counter("adm:all", win) >= ADMIN_LOCK_GLOBAL:
        return int((win + 1) * ADMIN_LOCK_WINDOW - now) + 1
    return 0


def admin_login_failed(ip: str) -> None:
    win = _admin_window()
    exp = (win + 2) * ADMIN_LOCK_WINDOW
    bump_counter("adm:ip:" + ip, win, exp)
    bump_counter("adm:all", win, exp)


def admin_login_succeeded(ip: str) -> None:
    with db.cursor() as cur:
        cur.execute("DELETE FROM counters WHERE bucket=?", ("adm:ip:" + ip,))


def client_ip(request) -> str:
    """Best-effort client IP. Trust X-Forwarded-For only where the proxy in front
    is ours: Vercel (it overwrites XFF to prevent spoofing) or the container's
    nginx on loopback."""
    from .config import config
    peer = request.client.host if request.client else ""
    trusted = config.on_vercel
    try:
        trusted = trusted or ipaddress.ip_address(peer).is_loopback
    except ValueError:
        pass
    if trusted:
        xff = request.headers.get("x-forwarded-for", "")
        first = xff.split(",")[0].strip()
        if first:
            return first[:64]
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            return real[:64]
    return peer or "unknown"
