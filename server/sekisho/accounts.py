"""User accounts (v2): register / login / logout / me.

Device keys stay the transport credential; a **user account** owns the data.
``devices.active_user_id`` records the logged-in user for a device. PINs are
stored salted + PBKDF2-hashed (6-8 digits). Wrong PINs escalate a lock per
account AND per device: 1 min -> 15 min -> 1 h -> locked until an admin unlocks.
"""
from __future__ import annotations

import hmac
import time
from datetime import datetime, timezone

from . import db, security


class AccountError(Exception):
    def __init__(self, code: str, message: str, status: int):
        self.code = code
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_public(row) -> dict:
    """Shape a user row for API responses (never leaks pin_hash/salt)."""
    keys = row.keys()
    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"],
        "is_admin": bool(row["is_admin"]) if "is_admin" in keys else False,
    }


def register(username: str, pin: str, device_id: str | None = None) -> dict:
    """Create a user. Raises AccountError('username_taken') on conflict and
    AccountError('rate_limited') past REGISTER_PER_DEVICE_PER_DAY on one device.

    New users are NEVER admin. Adminship ("checkpoint keeper") is granted ONLY
    via the web admin dashboard's Users toggle.
    """
    if device_id is not None and not security.register_allowed(device_id):
        raise AccountError(
            "rate_limited",
            f"Too many new accounts from this device today "
            f"(max {security.REGISTER_PER_DEVICE_PER_DAY}). Try again tomorrow.",
            429,
        )
    with db.cursor() as cur:
        exists = cur.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if exists:
            raise AccountError("username_taken", "That username is already taken", 409)
        salt = security.new_pin_salt()
        cur.execute(
            "INSERT INTO users (username, pin_hash, pin_salt, created_at, is_admin) VALUES (?,?,?,?,?)",
            (username, security.hash_pin(pin, salt), salt, security.now_iso(), 0),
        )
        uid = cur.lastrowid
        row = cur.execute(
            "SELECT id, username, created_at, is_admin FROM users WHERE id=?", (uid,)
        ).fetchone()
    # Failures recorded against this name while it did not exist must not lock
    # the brand-new account.
    security.pin_reset(security.pin_subject_user(username))
    return _user_public(row)


def _locked_error(remaining: int) -> AccountError:
    if remaining < 0:
        return AccountError(
            "locked_out",
            "Too many wrong PINs. This sign-in is locked until the checkpoint admin unlocks it.",
            429,
        )
    if remaining >= 120:
        human = f"{(remaining + 59) // 60} min"
    else:
        human = f"{remaining}s"
    return AccountError("locked_out", f"Too many attempts. Try again in {human}.", 429)


def login(username: str, pin: str, device_id: str | None = None) -> dict:
    """Verify credentials with escalating lockout. Returns the user dict or raises.

    * Failures are counted atomically (security.bump_counter) per account AND
      per device, BEFORE the PIN is checked, so a concurrent burst can never
      verify more than PIN_HARD_LOCK_AT guesses.
    * 5 -> 1 min, 10 -> 15 min, 15 -> 1 h, 20 -> locked until an admin unlocks.
      A lock never resets the counter; only a successful login (or the admin
      Unlock action) does.
    * Unknown usernames get the same PBKDF2 cost, the same error and the same
      lockout as real ones, so neither timing nor lockout reveals existence.

    Every DB write is committed before the error is raised (each helper uses
    its own cursor block), so the counts hold on Postgres and across instances.
    """
    now = time.time()
    usubj = security.pin_subject_user(username)
    subjects = [usubj]
    if device_id is not None:
        subjects.insert(0, security.pin_subject_device(device_id))

    # 1) Already locked? Refuse without counting or verifying.
    for subj in subjects:
        rem = security.pin_lock_remaining(subj, now)
        if rem:
            raise _locked_error(rem)

    # 2) Count this attempt first (atomic).
    counts = {subj: security.pin_count_attempt(subj, now) for subj in subjects}
    if any(n > security.PIN_HARD_LOCK_AT for n in counts.values()):
        for subj, n in counts.items():
            security.pin_apply_lock(subj, n, now)
        raise _locked_error(-1)

    # 3) Verify (dummy PBKDF2 for unknown names -> equal timing).
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT id, username, pin_hash, pin_salt, created_at, is_admin "
            "FROM users WHERE username=?",
            (username,),
        ).fetchone()
    if row is None:
        security.dummy_pin_hash(pin)
        ok = False
    else:
        ok = hmac.compare_digest(security.hash_pin(pin, row["pin_salt"]), row["pin_hash"])

    if ok:
        security.pin_reset(usubj)
        if device_id is not None:
            security.pin_uncount_attempt(security.pin_subject_device(device_id), now)
        return _user_public(row)

    # 4) Failure: escalate any lock this count crosses.
    worst = 0
    for subj, n in counts.items():
        lock = security.pin_apply_lock(subj, n, now)
        if lock < 0:
            worst = -1
        elif lock > 0 and worst >= 0:
            worst = max(worst, lock)
    if worst:
        raise _locked_error(worst)
    raise AccountError("bad_credentials", "Unknown username or PIN", 401)


def unlock(user_id: int) -> bool:
    """Admin action: clear a user's PIN failures + lock. False if no such user."""
    with db.cursor() as cur:
        row = cur.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return False
    security.pin_reset(security.pin_subject_user(row["username"]))
    return True


def unlock_device(device_id: str) -> None:
    """Admin action: clear a device's PIN failures + lock."""
    security.pin_reset(security.pin_subject_device(device_id))


def get_user(user_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT id, username, created_at, is_admin FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if row is None:
        return None
    return _user_public(row)


def is_admin(user_id: int | None) -> bool:
    """True iff the user exists and holds the phone-admin (keeper) flag."""
    if user_id is None:
        return False
    with db.cursor() as cur:
        row = cur.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row["is_admin"]) if row else False


def set_admin(user_id: int, value: bool) -> bool:
    """Grant/revoke the phone-admin flag. Returns False if the user doesn't exist."""
    with db.cursor() as cur:
        row = cur.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return False
        cur.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if value else 0, user_id))
    return True
