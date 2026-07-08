"""User accounts (v2): register / login / logout / me.

Device keys stay the transport credential; a **user account** owns the data.
``devices.active_user_id`` records the logged-in user for a device. PINs are
stored salted + PBKDF2-hashed. Five bad attempts lock an account for 60s.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

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


def register(username: str, pin: str) -> dict:
    """Create a user. Raises AccountError('username_taken') on conflict.

    New users are NEVER admin by default. Adminship ("checkpoint keeper") is
    granted ONLY via the web admin Users toggle or the ``SEKISHO_ADMIN_USERS``
    env var (comma-separated usernames, applied idempotently at startup).
    """
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
    return _user_public(row)


def login(username: str, pin: str) -> dict:
    """Verify credentials with lockout. Returns the user dict or raises AccountError."""
    now = _now()
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT id, username, pin_hash, pin_salt, created_at, failed_count, lock_until, is_admin "
            "FROM users WHERE username=?",
            (username,),
        ).fetchone()
        if row is None:
            raise AccountError("bad_credentials", "Unknown username or PIN", 401)

        # Locked?
        if row["lock_until"]:
            try:
                lock_until = datetime.fromisoformat(row["lock_until"])
            except ValueError:
                lock_until = None
            if lock_until and now < lock_until:
                remaining = int((lock_until - now).total_seconds()) + 1
                raise AccountError(
                    "locked_out", f"Too many attempts. Try again in {remaining}s.", 429
                )

        good = security.hash_pin(pin, row["pin_salt"]) == row["pin_hash"]
        if not good:
            failed = row["failed_count"] + 1
            lock_until_val = None
            if failed >= security.LOCKOUT_MAX_ATTEMPTS:
                lock_until_val = (now + timedelta(seconds=security.LOCKOUT_SECONDS)).isoformat()
                failed = 0  # reset the counter once locked
            cur.execute(
                "UPDATE users SET failed_count=?, lock_until=? WHERE id=?",
                (failed, lock_until_val, row["id"]),
            )
            if lock_until_val:
                raise AccountError(
                    "locked_out",
                    f"Too many attempts. Locked for {security.LOCKOUT_SECONDS}s.",
                    429,
                )
            raise AccountError("bad_credentials", "Unknown username or PIN", 401)

        # Success: clear failure state.
        cur.execute(
            "UPDATE users SET failed_count=0, lock_until=NULL WHERE id=?", (row["id"],)
        )
    return _user_public(row)


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


def apply_admin_users_env() -> list[str]:
    """Grant admin to the usernames in ``SEKISHO_ADMIN_USERS`` (comma-separated).

    Idempotent and ADDITIVE — it only grants, never revokes, so re-running on
    every boot is safe and never clobbers flags set from the web dashboard.
    Returns the list of usernames that existed and were (re)granted.
    """
    raw = os.environ.get("SEKISHO_ADMIN_USERS", "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    granted: list[str] = []
    if not names:
        return granted
    with db.cursor() as cur:
        for name in names:
            row = cur.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
            if row is not None:
                cur.execute("UPDATE users SET is_admin=1 WHERE username=?", (name,))
                granted.append(name)
    return granted
