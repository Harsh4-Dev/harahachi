"""SQLite storage layer for Sekisho.

v1 keyed all app data per ``device_id``. v2 introduces user **accounts**: the
data (profiles/meals/workouts/push) is now keyed by ``user_id`` while the device
key remains the transport credential. ``devices.active_user_id`` records which
user a paired device is currently logged in as.

``migrate()`` runs on every boot: it adds the v2 columns/tables idempotently and,
for any pre-existing device that has data but no account, auto-creates a user and
moves that device's data onto it (see API.md v2 "Migration").
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_db_path: Path | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    key_hash        TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    last_seen       TEXT,
    req_count       INTEGER NOT NULL DEFAULT 0,
    revoked         INTEGER NOT NULL DEFAULT 0,
    active_user_id  INTEGER
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    pin_hash        TEXT NOT NULL,
    pin_salt        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    lock_until      TEXT,
    -- phone-admin ("checkpoint keeper"). NOT auto-granted — set only via the web
    -- admin Users toggle or the SEKISHO_ADMIN_USERS env (applied at startup).
    is_admin        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pairing_tokens (
    token       TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id         INTEGER PRIMARY KEY,
    name            TEXT,
    age             INTEGER,
    sex             TEXT,
    height_cm       INTEGER,
    weight_kg       REAL,
    activity        TEXT,
    goal            TEXT,
    body_fat_pct    REAL,
    body_fat_goal_pct REAL,
    -- v3: goal-weight tracking + calorie pace (kg/week)
    weight_goal_kg  REAL,
    pace_kg_per_week REAL,
    updated_at      TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS meals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    dish        TEXT NOT NULL,
    calories    INTEGER NOT NULL,
    protein_g   INTEGER NOT NULL,
    carbs_g     INTEGER NOT NULL,
    fat_g       INTEGER NOT NULL,
    -- v3: extended nutrients (optional through the whole chain)
    fiber_g     INTEGER,
    sugar_g     INTEGER,
    sodium_mg   INTEGER,
    eaten_at    TEXT NOT NULL,
    source      TEXT NOT NULL,
    -- v3: one-tap favorites for quick re-logging
    favorite    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS workouts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    minutes         INTEGER NOT NULL,
    calories_burned INTEGER NOT NULL,
    note            TEXT,
    done_at         TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS request_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT,
    device_name TEXT,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    status      INTEGER NOT NULL,
    at          TEXT NOT NULL,
    -- v4: per-request attribution + response time for the richer admin log.
    user_id     INTEGER,
    duration_ms INTEGER
);

-- Packaged-food (barcode) lookup cache. Shared across users — a barcode
-- resolves to the same product regardless of who scanned it.
CREATE TABLE IF NOT EXISTS food_barcodes (
    code        TEXT PRIMARY KEY,
    found       INTEGER NOT NULL DEFAULT 0,
    payload     TEXT,
    source      TEXT,
    fetched_at  TEXT NOT NULL
);

-- LLM backend registry (v2). One row per configured OpenAI-compatible backend.
CREATE TABLE IF NOT EXISTS backends (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    api_key     TEXT,
    model       TEXT NOT NULL,
    vision      INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

-- Simple key/value settings (active_text_backend, active_vision_backend,
-- vapid keys, bootstrap markers).
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT
);

-- Web-push subscriptions (v2): per user + device.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    device_id     TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    subscription  TEXT NOT NULL,
    tz_offset_min INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_sent_15  TEXT,
    last_sent_18  TEXT,
    last_sent_22  TEXT,
    UNIQUE (user_id, device_id)
);

-- Weight log (v3): one row per (user, date) — last write for a date wins.
CREATE TABLE IF NOT EXISTS weights (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    date        TEXT NOT NULL,
    weight_kg   REAL NOT NULL,
    logged_at   TEXT NOT NULL,
    UNIQUE (user_id, date),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Water intake log (v3): running per-day total in ml (floored at 0).
CREATE TABLE IF NOT EXISTS water_log (
    user_id     INTEGER NOT NULL,
    date        TEXT NOT NULL,
    water_ml    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, date),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_meals_user_time ON meals(user_id, eaten_at);
CREATE INDEX IF NOT EXISTS idx_workouts_user_time ON workouts(user_id, done_at);
CREATE INDEX IF NOT EXISTS idx_reqlog_at ON request_log(at);
CREATE INDEX IF NOT EXISTS idx_weights_user_date ON weights(user_id, date);
"""


def _columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    return {r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return (
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def migrate() -> None:
    """Bring a v1 database up to the v2 schema, moving per-device data to users.

    Idempotent — safe to run on every boot and on a fresh DB.
    """
    from .security import hash_pin, new_pin_salt, now_iso

    assert _conn is not None
    with _lock:
        cur = _conn.cursor()

        # --- Detect a legacy (v1) profiles table keyed by device_id. ---
        legacy_profiles = False
        legacy_meals = False
        legacy_workouts = False
        if _table_exists(cur, "profiles"):
            legacy_profiles = "device_id" in _columns(cur, "profiles") and \
                "user_id" not in _columns(cur, "profiles")
        if _table_exists(cur, "meals"):
            legacy_meals = "device_id" in _columns(cur, "meals") and \
                "user_id" not in _columns(cur, "meals")
        if _table_exists(cur, "workouts"):
            legacy_workouts = "device_id" in _columns(cur, "workouts") and \
                "user_id" not in _columns(cur, "workouts")

        legacy = legacy_profiles or legacy_meals or legacy_workouts

        if not legacy:
            # Not a v1 data schema: just ensure everything current exists,
            # then top up any missing columns.
            _conn.executescript(SCHEMA)
            _ensure_columns(cur)
            _conn.commit()
            cur.close()
            return

        # ---------- v1 -> v2 data migration ----------
        # Rename legacy tables aside, create the new ones, then move data.
        if legacy_profiles:
            cur.execute("ALTER TABLE profiles RENAME TO _v1_profiles")
        if legacy_meals:
            cur.execute("ALTER TABLE meals RENAME TO _v1_meals")
        if legacy_workouts:
            cur.execute("ALTER TABLE workouts RENAME TO _v1_workouts")

        _conn.executescript(SCHEMA)
        _ensure_columns(cur)

        # Every device that has ANY legacy data gets an auto-created user.
        device_ids: set[str] = set()
        if legacy_profiles:
            device_ids.update(
                r["device_id"] for r in cur.execute("SELECT DISTINCT device_id FROM _v1_profiles")
            )
        if legacy_meals:
            device_ids.update(
                r["device_id"] for r in cur.execute("SELECT DISTINCT device_id FROM _v1_meals")
            )
        if legacy_workouts:
            device_ids.update(
                r["device_id"] for r in cur.execute("SELECT DISTINCT device_id FROM _v1_workouts")
            )
        # Also give every paired device an account even if it had no data yet,
        # so login is possible immediately after upgrade.
        device_ids.update(r["device_id"] for r in cur.execute("SELECT device_id FROM devices"))

        dev_to_user: dict[str, int] = {}
        for did in sorted(device_ids):
            # Username derived from the device (unique). PIN default "0000"
            # (migrated accounts — the app can prompt a reset). Salted+hashed.
            base = f"device_{did[:12]}" if did else f"device_{len(dev_to_user)}"
            username = base
            suffix = 1
            while cur.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                username = f"{base}_{suffix}"
                suffix += 1
            salt = new_pin_salt()
            cur.execute(
                "INSERT INTO users (username, pin_hash, pin_salt, created_at) VALUES (?,?,?,?)",
                (username, hash_pin("0000", salt), salt, now_iso()),
            )
            uid = cur.lastrowid
            dev_to_user[did] = uid
            cur.execute("UPDATE devices SET active_user_id=? WHERE device_id=?", (uid, did))

        # Move profiles.
        if legacy_profiles:
            for r in cur.execute("SELECT * FROM _v1_profiles").fetchall():
                uid = dev_to_user.get(r["device_id"])
                if uid is None:
                    continue
                cols = r.keys()
                cur.execute(
                    "INSERT OR REPLACE INTO profiles "
                    "(user_id, name, age, sex, height_cm, weight_kg, activity, goal, "
                    " body_fat_pct, body_fat_goal_pct, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uid, r["name"], r["age"], r["sex"], r["height_cm"], r["weight_kg"],
                        r["activity"], r["goal"],
                        r["body_fat_pct"] if "body_fat_pct" in cols else None,
                        r["body_fat_goal_pct"] if "body_fat_goal_pct" in cols else None,
                        r["updated_at"],
                    ),
                )
            cur.execute("DROP TABLE _v1_profiles")

        # Move meals.
        if legacy_meals:
            for r in cur.execute("SELECT * FROM _v1_meals").fetchall():
                uid = dev_to_user.get(r["device_id"])
                if uid is None:
                    continue
                cur.execute(
                    "INSERT INTO meals (user_id, dish, calories, protein_g, carbs_g, fat_g, eaten_at, source) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (uid, r["dish"], r["calories"], r["protein_g"], r["carbs_g"], r["fat_g"],
                     r["eaten_at"], r["source"]),
                )
            cur.execute("DROP TABLE _v1_meals")

        # Move workouts.
        if legacy_workouts:
            for r in cur.execute("SELECT * FROM _v1_workouts").fetchall():
                uid = dev_to_user.get(r["device_id"])
                if uid is None:
                    continue
                cur.execute(
                    "INSERT INTO workouts (user_id, kind, minutes, calories_burned, note, done_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (uid, r["kind"], r["minutes"], r["calories_burned"], r["note"], r["done_at"]),
                )
            cur.execute("DROP TABLE _v1_workouts")

        _conn.commit()
        cur.close()


def _ensure_columns(cur: sqlite3.Cursor) -> None:
    """Add v2/v3 columns to existing tables that predate them (idempotent).

    Every ALTER is guarded by a column-existence check so re-running on an
    already-upgraded DB is a no-op and existing rows/data are preserved.
    """
    if "active_user_id" not in _columns(cur, "devices"):
        cur.execute("ALTER TABLE devices ADD COLUMN active_user_id INTEGER")
    # phone-admin flag on users (idempotent — no-op once present).
    if _table_exists(cur, "users") and "is_admin" not in _columns(cur, "users"):
        cur.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if _table_exists(cur, "profiles"):
        pcols = _columns(cur, "profiles")
        if "user_id" in pcols and "body_fat_pct" not in pcols:
            cur.execute("ALTER TABLE profiles ADD COLUMN body_fat_pct REAL")
        if "user_id" in pcols and "body_fat_goal_pct" not in pcols:
            cur.execute("ALTER TABLE profiles ADD COLUMN body_fat_goal_pct REAL")
        # v3: goal weight + pace
        if "user_id" in pcols and "weight_goal_kg" not in pcols:
            cur.execute("ALTER TABLE profiles ADD COLUMN weight_goal_kg REAL")
        if "user_id" in pcols and "pace_kg_per_week" not in pcols:
            cur.execute("ALTER TABLE profiles ADD COLUMN pace_kg_per_week REAL")
    # v4: request-log attribution + response time
    if _table_exists(cur, "request_log"):
        rcols = _columns(cur, "request_log")
        if "user_id" not in rcols:
            cur.execute("ALTER TABLE request_log ADD COLUMN user_id INTEGER")
        if "duration_ms" not in rcols:
            cur.execute("ALTER TABLE request_log ADD COLUMN duration_ms INTEGER")
    # v3: extended nutrients + favorite flag on meals
    if _table_exists(cur, "meals"):
        mcols = _columns(cur, "meals")
        if "user_id" in mcols:
            if "fiber_g" not in mcols:
                cur.execute("ALTER TABLE meals ADD COLUMN fiber_g INTEGER")
            if "sugar_g" not in mcols:
                cur.execute("ALTER TABLE meals ADD COLUMN sugar_g INTEGER")
            if "sodium_mg" not in mcols:
                cur.execute("ALTER TABLE meals ADD COLUMN sodium_mg INTEGER")
            if "favorite" not in mcols:
                cur.execute("ALTER TABLE meals ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
    # v4: 15:00 water-reminder slot column on push subscriptions.
    if _table_exists(cur, "push_subscriptions"):
        pscols = _columns(cur, "push_subscriptions")
        if "last_sent_15" not in pscols:
            cur.execute("ALTER TABLE push_subscriptions ADD COLUMN last_sent_15 TEXT")


def init_db(db_path: Path) -> None:
    global _conn, _db_path
    _db_path = db_path
    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL;")
    _conn.execute("PRAGMA foreign_keys=ON;")
    # NOTE: schema creation is owned by migrate(). Running executescript(SCHEMA)
    # here would fail on a legacy v1 DB, because SCHEMA has indexes on meals/
    # workouts(user_id) while the legacy tables still key by device_id — the
    # index would reference a missing column. migrate() renames legacy tables
    # aside FIRST, then creates the v2 schema, so it is safe on both fresh and
    # upgraded databases.
    _conn.commit()
    migrate()


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    """Serialized cursor access — sqlite connection is shared across threads."""
    assert _conn is not None, "db not initialized"
    with _lock:
        cur = _conn.cursor()
        try:
            yield cur
            _conn.commit()
        finally:
            cur.close()


def get_setting(key: str, default: str | None = None) -> str | None:
    with cursor() as cur:
        row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str | None) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def close_db() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
