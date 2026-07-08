"""Sekisho v2 smoke suite — exercises EVERY endpoint against the mock/dummy
backends (no GPU required) and prints a PASS/FAIL table.

Run:  python tests_smoke.py            (from the server/ directory)
      python -m pytest tests_smoke.py  (also works — each check is asserted)

It spins up the FastAPI app with a throwaway SQLite DB in a temp dir, drives it
with fastapi.testclient.TestClient, and covers v1 + v2 happy paths AND failure
cases (wrong PIN, no_user 401, bad barcode, oversized image, unknown route,
lockout, all-backends-dead failover, timeouts honored).
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import struct
import zlib
from pathlib import Path

# ---- configure a throwaway DB + deterministic env BEFORE importing sekisho ----
_TMP = tempfile.mkdtemp(prefix="sekisho_smoke_")
os.environ["SEKISHO_DB_PATH"] = str(Path(_TMP) / "smoke.db")
os.environ["SEKISHO_ADMIN_PASSWORD"] = "smoke-admin-pw"
os.environ["SEKISHO_ALLOW_MOCK"] = "1"
os.environ.pop("SEKISHO_VLLM_URL", None)
os.environ.pop("SEKISHO_BACKENDS_JSON", None)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sekisho import db  # noqa: E402
from sekisho.config import config  # noqa: E402

config.ensure_admin_password()
db.init_db(config.db_file)

from fastapi.testclient import TestClient  # noqa: E402
from sekisho import backends, push, feedback  # noqa: E402
from sekisho.app import app  # noqa: E402
from sekisho.nutrition import compute_targets  # noqa: E402
from sekisho.models import ProfileIn  # noqa: E402

client = TestClient(app)

# ---------------------------------------------------------------------------
# result table
# ---------------------------------------------------------------------------
_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(cond), detail))
    return bool(cond)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _png_bytes(w: int = 8, h: int = 8) -> bytes:
    """A minimal valid PNG so mime sniffing + upload validation pass."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x7f\x7f\x7f" * w for _ in range(h))
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


PNG = _png_bytes()


def pair_device(name: str = "phone") -> str:
    """Admin-login, mint a pairing token, claim it, return the device key."""
    r = client.post("/admin/login", data={"password": "smoke-admin-pw"}, follow_redirects=False)
    cookie = r.cookies.get("sekisho_admin")
    assert cookie, "admin login failed"
    pr = client.post("/admin/api/pair", cookies={"sekisho_admin": cookie})
    token = pr.json()["token"]
    cr = client.post("/api/v1/pair/claim", json={"token": token, "device_name": name})
    assert cr.status_code == 201, cr.text
    return cr.json()["device_key"], cookie


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def relax(key: str) -> None:
    """Clear the in-memory rate-limit window for a device so the long smoke run
    isn't throttled by its own volume. (The dedicated 429 test uses a device we
    never relax.)"""
    from sekisho import security
    dev = security.resolve_device(key)
    if dev:
        security._hits.pop(dev["device_id"], None)


# ===========================================================================
# V1: pairing, profile, meals, analyze, barcode, suggestions, calendar, workouts
# ===========================================================================
DEVICE_KEY, ADMIN_COOKIE = pair_device("smoke-phone")
check("v1 pair/claim -> device_key", DEVICE_KEY.startswith("hh_"), DEVICE_KEY[:6])

# no_user before registering
r = client.get("/api/v1/meals", headers=bearer(DEVICE_KEY))
check("no_user 401 before login", r.status_code == 401 and r.json()["error"]["code"] == "no_user",
      f"{r.status_code} {r.json().get('error',{}).get('code')}")

# register (v2 account) with an initial profile
prof = {"name": "Kenji", "age": 30, "sex": "m", "height_cm": 178, "weight_kg": 74.0,
        "activity": "moderate", "goal": "cut"}
r = client.post("/api/v1/auth/register", headers=bearer(DEVICE_KEY),
                json={"username": "kenji", "pin": "1234", "profile": prof})
check("v2 auth/register 201", r.status_code == 201 and r.json()["user"]["username"] == "kenji",
      str(r.status_code))

# me
r = client.get("/api/v1/auth/me", headers=bearer(DEVICE_KEY))
check("v2 auth/me returns user+profile", r.status_code == 200 and r.json()["user"]["username"] == "kenji"
      and r.json()["profile"] is not None, str(r.status_code))

# profile GET has targets (Mifflin here — no body fat)
r = client.get("/api/v1/profile", headers=bearer(DEVICE_KEY))
check("v1 GET /profile has targets", r.status_code == 200 and "targets" in r.json(), str(r.status_code))

# meals: create + list totals
r = client.post("/api/v1/meals", headers=bearer(DEVICE_KEY),
                json={"dish": "Dal & rice", "calories": 500, "protein_g": 18, "carbs_g": 80,
                      "fat_g": 10, "source": "manual"})
meal_id = r.json().get("id")
check("v1 POST /meals 201", r.status_code == 201 and meal_id, str(r.status_code))

r = client.post("/api/v1/workouts", headers=bearer(DEVICE_KEY),
                json={"kind": "run", "minutes": 30, "calories_burned": 300})
check("v1 POST /workouts 201", r.status_code == 201, str(r.status_code))

r = client.get("/api/v1/meals", headers=bearer(DEVICE_KEY))
tot = r.json()["totals"]
check("v1 GET /meals totals+burned", r.status_code == 200 and tot["calories"] == 500 and tot["burned"] == 300,
      str(tot))

# analyze (mock) — multi-item + points in range
r = client.post("/api/v1/meals/analyze", headers=bearer(DEVICE_KEY),
                files={"image": ("m.png", PNG, "image/png")}, data={"hint": "thali", "portion": "regular"})
aj = r.json()
items = aj.get("items", [])
sum_cal = sum(i["calories"] for i in items)
pts_ok = all(0.0 <= i["point"]["x"] <= 1.0 and 0.0 <= i["point"]["y"] <= 1.0
             for i in items if i.get("point"))
has_points = any(i.get("point") for i in items)
check("v1 analyze mock multi-item", r.status_code == 200 and len(items) >= 3, f"{r.status_code} items={len(items)}")
check("v1 analyze items point in 0..1", has_points and pts_ok, f"has_points={has_points}")
check("v1 analyze totals == sum(items)", aj["calories"] == sum_cal, f"{aj['calories']} vs {sum_cal}")

# barcode: known (mock), unknown, cache
r = client.get("/api/v1/food/barcode/8901058000290", headers=bearer(DEVICE_KEY))
check("v1 barcode known/mock 200", r.status_code == 200 and "found" in r.json(), str(r.status_code))
r2 = client.get("/api/v1/food/barcode/8901058000290", headers=bearer(DEVICE_KEY))
check("v1 barcode cache 200", r2.status_code == 200 and r2.json().get("source") in ("cache", "mock", "openfoodfacts"),
      str(r2.json().get("source")))
r = client.get("/api/v1/food/barcode/abc", headers=bearer(DEVICE_KEY))
check("v1 barcode bad 400", r.status_code == 400 and r.json()["error"]["code"] == "bad_barcode", str(r.status_code))

# suggestions (rule-based; uses user history)
r = client.get("/api/v1/suggestions", headers=bearer(DEVICE_KEY))
check("v1 suggestions 200 (user-keyed, no crash)", r.status_code == 200 and "suggestions" in r.json(),
      str(r.status_code))

# calendar
r = client.get("/api/v1/calendar", headers=bearer(DEVICE_KEY))
check("v1 calendar 200", r.status_code == 200 and isinstance(r.json().get("days"), list), str(r.status_code))

# workouts list
r = client.get("/api/v1/workouts", headers=bearer(DEVICE_KEY))
check("v1 GET /workouts 200", r.status_code == 200 and len(r.json()["workouts"]) == 1, str(r.status_code))

# delete meal -> 204
r = client.delete(f"/api/v1/meals/{meal_id}", headers=bearer(DEVICE_KEY))
check("v1 DELETE /meals 204", r.status_code == 204, str(r.status_code))

# health
r = client.get("/api/v1/health")
check("v1 GET /health 200 mock", r.status_code == 200 and r.json()["vllm"] in ("mock", "connected"), str(r.json().get("vllm")))

# /llm/v1 with no backend -> 503 (never mocked)
r = client.post("/llm/v1/chat/completions", headers=bearer(DEVICE_KEY),
                json={"messages": [{"role": "user", "content": "hi"}]})
check("/llm/v1 503 no-backend", r.status_code == 503 and r.json()["error"]["code"] == "vllm_unavailable",
      str(r.status_code))

# unknown route 404
r = client.get("/api/v1/nope", headers=bearer(DEVICE_KEY))
check("unknown route 404", r.status_code == 404, str(r.status_code))

# oversized image 413
big = b"\x89PNG\r\n\x1a\n" + b"0" * (9 * 1024 * 1024)
r = client.post("/api/v1/meals/analyze", headers=bearer(DEVICE_KEY),
                files={"image": ("big.png", big, "image/png")})
check("analyze oversized 413", r.status_code == 413 and r.json()["error"]["code"] == "image_too_large",
      str(r.status_code))

# missing bearer 401
r = client.get("/api/v1/meals")
check("missing bearer 401", r.status_code == 401, str(r.status_code))

# ===========================================================================
# V2 accounts: login / logout / lockout
# ===========================================================================
relax(DEVICE_KEY)
r = client.post("/api/v1/auth/logout", headers=bearer(DEVICE_KEY))
check("v2 logout 200", r.status_code == 200, str(r.status_code))
r = client.get("/api/v1/meals", headers=bearer(DEVICE_KEY))
check("no_user 401 after logout", r.status_code == 401 and r.json()["error"]["code"] == "no_user", str(r.status_code))

# wrong PIN
r = client.post("/api/v1/auth/login", headers=bearer(DEVICE_KEY), json={"username": "kenji", "pin": "9999"})
check("v2 login wrong PIN 401", r.status_code == 401 and r.json()["error"]["code"] == "bad_credentials",
      str(r.status_code))

# lockout: register a victim then hammer wrong PINs
client.post("/api/v1/auth/register", headers=bearer(DEVICE_KEY),
            json={"username": "lockme", "pin": "1111"})
client.post("/api/v1/auth/logout", headers=bearer(DEVICE_KEY))
lock_status = None
for i in range(6):
    rr = client.post("/api/v1/auth/login", headers=bearer(DEVICE_KEY), json={"username": "lockme", "pin": "0000"})
    lock_status = rr
check("v2 lockout after 5 bad attempts (429)",
      lock_status.status_code == 429 and lock_status.json()["error"]["code"] == "locked_out",
      str(lock_status.status_code))
# even correct PIN is locked out now
r = client.post("/api/v1/auth/login", headers=bearer(DEVICE_KEY), json={"username": "lockme", "pin": "1111"})
check("v2 correct PIN still locked (429)", r.status_code == 429, str(r.status_code))

# good login restores access
r = client.post("/api/v1/auth/login", headers=bearer(DEVICE_KEY), json={"username": "kenji", "pin": "1234"})
check("v2 login good 200", r.status_code == 200 and r.json()["user"]["username"] == "kenji", str(r.status_code))

# ===========================================================================
# V2: Katch-McArdle math + body-photo
# ===========================================================================
relax(DEVICE_KEY)
p = ProfileIn(name="t", age=30, sex="m", height_cm=178, weight_kg=70.0,
              activity="sedentary", goal="maintain", body_fat_pct=20.0)
t = compute_targets(p)
# BMR = 370 + 21.6*56 = 1579.6 ; TDEE sedentary *1.2 = 1895.52 ; maintain
km_bmr_ok = abs((t.calories / 1.2) - 1579.6) < 2.0  # calories ~= bmr*1.2 for maintain
check("v2 Katch-McArdle BMR 1579.6 (70kg@20%)", km_bmr_ok, f"targets.cal={t.calories} implied_bmr={t.calories/1.2:.1f}")

r = client.post("/api/v1/profile/body-photo", headers=bearer(DEVICE_KEY),
                files={"image": ("body.png", PNG, "image/png")})
bj = r.json()
check("v2 body-photo mock estimate", r.status_code == 200 and "body_fat_pct" in bj and bj.get("source") == "mock"
      and len(bj.get("range", [])) == 2, str(r.status_code))

# ===========================================================================
# V2: Backends CRUD + failover with a faked dead primary
# ===========================================================================
relax(DEVICE_KEY)
# Add a dead primary (unreachable) marked vision, then verify analyze still
# falls back to mock (all backends fail -> mock-last).
b_dead = backends.create_backend(name="dead", kind="vllm",
                                 base_url="http://127.0.0.1:9/v1", api_key=None,
                                 model="dead-model", vision=True, enabled=True)
backends.set_active("vision", b_dead["id"])
backends.set_active("text", b_dead["id"])
check("v2 backend create + list", any(b["name"] == "dead" for b in backends.list_backends()),
      f"count={len(backends.list_backends())}")

r = client.post("/api/v1/meals/analyze", headers=bearer(DEVICE_KEY),
                files={"image": ("m.png", PNG, "image/png")}, data={"hint": "dosa"})
check("v2 analyze failover -> mock when primary dead", r.status_code == 200 and len(r.json().get("items", [])) >= 2,
      str(r.status_code))

# /llm/v1 now has an (unreachable) active text backend -> 503 on connect fail, not a hang
import time as _time
_t0 = _time.time()
r = client.post("/llm/v1/chat/completions", headers=bearer(DEVICE_KEY),
                json={"messages": [{"role": "user", "content": "hi"}]})
_elapsed = _time.time() - _t0
check("/llm/v1 503 dead backend (fast, timeout honored)", r.status_code == 503 and _elapsed < 15,
      f"{r.status_code} in {_elapsed:.1f}s")

# analyze with mock DISABLED + dead backend -> 503 clean JSON
config.allow_mock = False
r = client.post("/api/v1/meals/analyze", headers=bearer(DEVICE_KEY),
                files={"image": ("m.png", PNG, "image/png")}, data={"hint": "dosa"})
check("analyze 503 when all backends fail + mock off",
      r.status_code == 503 and r.json()["error"]["code"] == "vllm_unavailable", str(r.status_code))
r = client.post("/api/v1/chat", headers=bearer(DEVICE_KEY),
                json={"messages": [{"role": "user", "content": "hi"}]})
check("chat 503 when backend fails + mock off",
      r.status_code == 503 and r.json()["error"]["code"] == "vllm_unavailable", str(r.status_code))
config.allow_mock = True

# clean up: remove dead backends so chat mock works
for b in backends.list_backends():
    backends.delete_backend(b["id"])
backends.set_active("text", None)
backends.set_active("vision", None)

# backend CRUD update + delete round-trip
b1 = backends.create_backend(name="lm", kind="lmstudio", base_url="http://host.docker.internal:1234/v1",
                            api_key="secret", model="local-model", vision=False, enabled=True)
upd = backends.update_backend(b1["id"], name="lm-renamed")
check("v2 backend update", upd and upd["name"] == "lm-renamed" and upd["has_key"], str(upd))
backends.delete_backend(b1["id"])
check("v2 backend delete", not any(b["id"] == b1["id"] for b in backends.list_backends()), "")

# ===========================================================================
# V2: Chat assistant — "I ate 2 idlis and sambar" logs a meal, no json fence
# ===========================================================================
relax(DEVICE_KEY)
r = client.post("/api/v1/chat", headers=bearer(DEVICE_KEY),
                json={"messages": [{"role": "user", "content": "I ate 2 idlis and sambar"}]})
cj = r.json()
performed = cj.get("action")
reply = cj.get("reply", "")
check("v2 chat parses+executes log_meal", r.status_code == 200 and performed
      and performed.get("action") == "log_meal", str(performed))
check("v2 chat reply has no ```json fence", "```" not in reply, reply[:60])
# verify the meal actually landed
r = client.get("/api/v1/meals", headers=bearer(DEVICE_KEY))
logged = any("idli" in m["dish"].lower() for m in r.json()["meals"])
check("v2 chat meal actually persisted", logged, "")

# ---- recommendation intent ("what should I eat") PROPOSES, does NOT log ----
relax(DEVICE_KEY)
r = client.get("/api/v1/meals", headers=bearer(DEVICE_KEY))
_meals_before = len(r.json()["meals"])
r = client.post("/api/v1/chat", headers=bearer(DEVICE_KEY),
                json={"messages": [{"role": "user", "content": "what should I eat for dinner?"}]})
cj = r.json()
rec = cj.get("action")
check("v2 chat recommendation returns a proposed log_meal action",
      r.status_code == 200 and rec and rec.get("action") == "log_meal"
      and rec.get("proposed") is True, str(rec))
check("v2 chat proposed action carries dish + macros",
      bool(rec) and isinstance(rec.get("dish"), str) and rec["dish"]
      and all(isinstance(rec.get(k), int) for k in ("calories", "protein_g", "carbs_g", "fat_g")),
      str(rec))
check("v2 chat proposed reply has no ```json fence", "```" not in cj.get("reply", ""),
      cj.get("reply", "")[:60])
r = client.get("/api/v1/meals", headers=bearer(DEVICE_KEY))
check("v2 chat recommendation did NOT log a meal server-side",
      len(r.json()["meals"]) == _meals_before, f"{_meals_before}->{len(r.json()['meals'])}")

# ===========================================================================
# V2: Push — subscribe + monkeypatched scheduler tick
# ===========================================================================
relax(DEVICE_KEY)
r = client.get("/api/v1/push/vapid-public-key")
check("v2 vapid public key (no auth)", r.status_code == 200 and len(r.json().get("public_key", "")) > 20, "")

sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}}
r = client.post("/api/v1/push/subscribe", headers=bearer(DEVICE_KEY),
                json={"subscription": sub, "tz_offset_min": 0})
check("v2 push subscribe 201", r.status_code == 201 and push.subscription_count() >= 1, str(r.status_code))

# monkeypatch send + force an 18:00 local tick
_sent = []
push.send_web_push = lambda subscription, payload: _sent.append(payload)  # type: ignore
from datetime import datetime as _dt, timezone as _tz
import asyncio as _aio
n18 = _aio.run(push.run_due(_dt(2026, 7, 7, 18, 0, tzinfo=_tz.utc)))
check("v2 push scheduler 18:00 tick fires", n18 >= 1 and _sent and _sent[-1].get("slot") == "18",
      f"sent={n18}")

# ---- 15:00 water-reminder slot fires when today's water is below half target ----
_sent.clear()
n15 = _aio.run(push.run_due(_dt(2026, 7, 7, 15, 0, tzinfo=_tz.utc)))
check("v4 push scheduler 15:00 water slot fires (water < half target)",
      n15 >= 1 and _sent and _sent[-1].get("slot") == "15", f"sent={n15}")
check("v4 water reminder body mentions water",
      bool(_sent) and "water" in _sent[-1].get("body", "").lower(), str(_sent[-1] if _sent else None))
# Once past half the daily target, the 15:00 slot is skipped.
_target = feedback.water_target_ml(1)
client.post("/api/v1/water", headers=bearer(DEVICE_KEY), json={"ml": _target})  # well over half
with db.cursor() as _c:  # clear the per-slot marker so run_due re-evaluates
    _c.execute("UPDATE push_subscriptions SET last_sent_15=NULL")
_sent.clear()
n15b = _aio.run(push.run_due(_dt(2026, 7, 7, 15, 0, tzinfo=_tz.utc)))
check("v4 push 15:00 water slot SKIPPED once past half target", n15b == 0, f"sent={n15b}")
# reset the water we added so the later v3 water tests start from a clean 0
with db.cursor() as _c:
    _c.execute("DELETE FROM water_log WHERE user_id=?", (1,))

# ---- scheduler_status() exposes observability fields ----
_ss = push.scheduler_status()
check("v4 scheduler_status shape (last_tick/next_fire/ticks/vapid)",
      {"running", "last_tick", "next_fire", "ticks", "vapid_configured", "restarts"} <= set(_ss),
      str(sorted(_ss)))

# unsubscribe
r = client.delete("/api/v1/push/subscribe", headers=bearer(DEVICE_KEY))
check("v2 push unsubscribe 204", r.status_code == 204, str(r.status_code))

# ===========================================================================
# V2: Admin pages render + backends test endpoint
# ===========================================================================
ac = {"sekisho_admin": ADMIN_COOKIE}
r = client.get("/admin", cookies=ac)
check("admin dashboard 200 + tabs", r.status_code == 200 and 'data-tab="backends"' in r.text
      and 'page-users' in r.text, str(r.status_code))
r = client.get("/admin/api/state", cookies=ac)
sj = r.json()
check("admin state has devices/backend/logs", r.status_code == 200 and "devices" in sj and "backend" in sj
      and "logs" in sj, str(r.status_code))
r = client.get("/admin/api/users", cookies=ac)
check("admin users list", r.status_code == 200 and len(r.json()["users"]) >= 1, str(r.status_code))
uid = r.json()["users"][0]["id"]
r = client.get(f"/admin/api/users/{uid}", cookies=ac)
check("admin user drill-down", r.status_code == 200 and "day_totals" in r.json() and "meals" in r.json(),
      str(r.status_code))
# dedicated per-user page payload (/full): pagination + weights/water/workouts/streaks/adherence/devices
r = client.get(f"/admin/api/users/{uid}/full?page=0&per_page=5", cookies=ac)
fj = r.json()
check("admin per-user /full page has all sections",
      r.status_code == 200 and {"meals_page", "weights", "water", "workouts", "devices",
                                 "streaks", "weekly_adherence", "targets"} <= set(fj), str(sorted(fj)))
check("admin per-user /full pagination meta", isinstance(fj["meals_page"].get("total_pages"), int)
      and fj["meals_page"].get("per_page") == 5, str(fj.get("meals_page")))
check("admin per-user /full adherence is a weekly series",
      isinstance(fj["weekly_adherence"], list) and all("adherence_pct" in w for w in fj["weekly_adherence"]),
      str(fj["weekly_adherence"][:1]))
r = client.get("/admin/api/users/9999999/full", cookies=ac)
check("admin per-user /full unknown -> 404", r.status_code == 404, str(r.status_code))
r = client.get("/admin/api/backends", cookies=ac)
check("admin backends list + presets", r.status_code == 200 and "presets" in r.json()
      and r.json()["presets"]["nvidia"].startswith("https://integrate.api.nvidia.com"), str(r.status_code))
# create a backend via admin form, then test it (dead -> ok:false but clean)
r = client.post("/admin/api/backends", cookies=ac,
                data={"name": "adm", "kind": "vllm", "base_url": "http://127.0.0.1:9/v1",
                      "model": "m", "vision": "1", "enabled": "1"})
new_id = r.json()["backend"]["id"]
check("admin backend create via form", r.status_code == 200 and r.json()["ok"], str(r.status_code))
r = client.post(f"/admin/api/backends/{new_id}/test", cookies=ac)
check("admin backend test (dead -> ok:false, no hang)", r.status_code == 200 and r.json()["ok"] is False,
      str(r.json().get("error"))[:40])
# ---- admin backend EDIT form (prefilled update, api_key masked/preserved) ----
r = client.post("/admin/api/backends", cookies=ac,
                data={"name": "editme", "kind": "vllm", "base_url": "http://127.0.0.1:9/v1",
                      "model": "m0", "api_key": "secret-key", "vision": "0", "enabled": "1"})
edit_id = r.json()["backend"]["id"]
check("admin backend create carries has_key (masked)", r.json()["backend"]["has_key"] is True
      and "api_key" not in r.json()["backend"], str(r.json()["backend"]))
# Update name+model WITHOUT sending api_key -> stored key must be preserved.
r = client.post(f"/admin/api/backends/{edit_id}", cookies=ac,
                data={"name": "edited", "model": "m1", "enabled": "0"})
bk = r.json().get("backend", {})
check("admin backend edit updates fields + keeps key",
      r.status_code == 200 and bk.get("name") == "edited" and bk.get("model") == "m1"
      and bk.get("enabled") in (0, False) and bk.get("has_key") is True, str(bk))
# Explicitly clear the key with an empty api_key.
r = client.post(f"/admin/api/backends/{edit_id}", cookies=ac, data={"api_key": ""})
check("admin backend edit can clear api_key", r.json()["backend"]["has_key"] is False, str(r.json().get("backend")))
client.post(f"/admin/api/backends/{edit_id}/delete", cookies=ac)

r = client.post(f"/admin/api/backends/{new_id}/delete", cookies=ac)
check("admin backend delete via form", r.status_code == 200, str(r.status_code))
# admin auth guard — use a fresh cookie-less client (TestClient persists cookies)
_bare = TestClient(app)
r = _bare.get("/admin/api/state")
check("admin api guarded (401 no cookie)", r.status_code == 401, str(r.status_code))

# ---- request log carries per-user attribution + response time (Requests page) ----
r = client.get("/admin/api/state", cookies=ac)
_logs = r.json().get("logs", [])
check("admin request log rows carry username + duration_ms columns",
      bool(_logs) and all(("username" in l and "duration_ms" in l) for l in _logs[:20]),
      str(_logs[0]) if _logs else "no logs")

# ---- admin push test: all-subs AND scoped to a single user ----
r = client.post("/admin/api/push/test", cookies=ac)
check("admin push test endpoint 200", r.status_code == 200 and "sent" in r.json(), str(r.status_code))
# Re-subscribe (monkeypatched send is still active) to exercise send-to-user.
relax(DEVICE_KEY)
_dev_uid = client.get("/api/v1/auth/me", headers=bearer(DEVICE_KEY)).json()["user"]["id"]
client.post("/api/v1/push/subscribe", headers=bearer(DEVICE_KEY),
            json={"subscription": {"endpoint": "https://push.example/u1", "keys": {"p256dh": "x", "auth": "y"}},
                  "tz_offset_min": 0})
r = client.post("/admin/api/push/test", cookies=ac, data={"user_id": str(_dev_uid)})
check("admin push test to specific user X sends",
      r.status_code == 200 and r.json().get("ok") is True and r.json().get("sent", 0) >= 1, str(r.json()))
r = client.post("/admin/api/push/test", cookies=ac, data={"user_id": "9999999"})
check("admin push test to user with no subs -> ok:false, sent 0",
      r.status_code == 200 and r.json().get("ok") is False and r.json().get("sent") == 0, str(r.json()))
client.delete("/api/v1/push/subscribe", headers=bearer(DEVICE_KEY))

# ===========================================================================
# Rate limit -> 429 (30/min/device). Fresh device to not disturb others.
# ===========================================================================
rl_key, _ = pair_device("rl")
client.post("/api/v1/auth/register", headers=bearer(rl_key), json={"username": "rluser", "pin": "2222"})
got_429 = False
for i in range(40):
    rr = client.get("/api/v1/health", headers=bearer(rl_key))  # health has no auth; use profile instead
    rr = client.get("/api/v1/profile", headers=bearer(rl_key))
    if rr.status_code == 429:
        got_429 = True
        break
check("rate limit 429 after 30/min", got_429, "")


# ===========================================================================
# V3: weight + trend, water floor, nutrients, favorites/recent, streaks,
#     insights math, export CSV, eta_date + pace-based targets
# ===========================================================================
relax(DEVICE_KEY)

# ---- pace-based targets + eta_date (unit-level math) ----
import datetime as _date
from sekisho.nutrition import eta_date as _eta, _bmr as _bmrf, ACTIVITY_FACTORS as _AF
p_pace = ProfileIn(name="p", age=30, sex="m", height_cm=178, weight_kg=74.0,
                   activity="moderate", goal="cut", weight_goal_kg=65.0, pace_kg_per_week=0.5)
t_pace = compute_targets(p_pace)
_tdee = _bmrf(p_pace) * _AF["moderate"]
_expected = round(_tdee - 0.5 * 7700 / 7)
check("v3 pace-based target = TDEE - pace_delta", abs(t_pace.calories - _expected) <= 1,
      f"{t_pace.calories} vs {_expected}")
# flat cut differs from pace-based
p_flat = ProfileIn(name="p", age=30, sex="m", height_cm=178, weight_kg=74.0,
                   activity="moderate", goal="cut")
check("v3 pace target differs from flat %", t_pace.calories != compute_targets(p_flat).calories,
      f"pace={t_pace.calories} flat={compute_targets(p_flat).calories}")
# clamp >=1200
p_clamp = ProfileIn(name="p", age=25, sex="f", height_cm=150, weight_kg=45.0,
                    activity="sedentary", goal="cut", weight_goal_kg=40.0, pace_kg_per_week=1.5)
check("v3 pace target clamped to >=1200", compute_targets(p_clamp).calories >= 1200,
      str(compute_targets(p_clamp).calories))
# eta_date: 9kg / 0.5 = 18 weeks = 126 days
_eta_val = _eta(p_pace, _date.date(2026, 7, 7))
check("v3 eta_date math (9kg@0.5/wk -> +126d)", _eta_val == "2026-11-10", str(_eta_val))
check("v3 eta_date None without pace", _eta(p_flat) is None, "")

# ---- persist pace profile + GET /profile returns eta_date + recomputed target ----
prof_pace = {"name": "Kenji", "age": 30, "sex": "m", "height_cm": 178, "weight_kg": 74.0,
             "activity": "moderate", "goal": "cut", "weight_goal_kg": 65.0, "pace_kg_per_week": 0.5}
r = client.put("/api/v1/profile", headers=bearer(DEVICE_KEY), json=prof_pace)
check("v3 PUT /profile pace 200 + eta_date", r.status_code == 200 and r.json().get("eta_date"),
      str(r.json().get("eta_date")))
r = client.get("/api/v1/profile", headers=bearer(DEVICE_KEY))
gj = r.json()
check("v3 GET /profile has eta_date + pace fields", r.status_code == 200 and gj.get("eta_date")
      and gj.get("pace_kg_per_week") == 0.5, str(gj.get("eta_date")))

# ---- weight log + 7-day moving-average trend ----
relax(DEVICE_KEY)
for i, wkg in enumerate([74.0, 73.8, 73.9, 73.6, 73.5, 73.4, 73.2, 73.0]):
    d = f"2026-06-{10+i:02d}"
    r = client.post("/api/v1/weight", headers=bearer(DEVICE_KEY), json={"weight_kg": wkg, "date": d})
    assert r.status_code == 201, r.text
r = client.get("/api/v1/weight?from=2026-06-01&to=2026-06-30", headers=bearer(DEVICE_KEY))
wj = r.json()
check("v3 weight entries + trend returned", r.status_code == 200 and len(wj["entries"]) == 8
      and len(wj["trend"]) == 8, f"entries={len(wj.get('entries',[]))}")
# 7-day MA: 8th point averages entries 2..8 (last 7)
_last7 = [73.8, 73.9, 73.6, 73.5, 73.4, 73.2, 73.0]
_exp_avg = round(sum(_last7) / 7, 2)
check("v3 weight 7-day MA correct on 8th point", abs(wj["trend"][-1]["avg_kg"] - _exp_avg) < 0.011,
      f"{wj['trend'][-1]['avg_kg']} vs {_exp_avg}")
# re-log same date overwrites (unique per user+date)
client.post("/api/v1/weight", headers=bearer(DEVICE_KEY), json={"weight_kg": 72.5, "date": "2026-06-10"})
r = client.get("/api/v1/weight?from=2026-06-10&to=2026-06-10", headers=bearer(DEVICE_KEY))
check("v3 weight same-date overwrites", len(r.json()["entries"]) == 1
      and r.json()["entries"][0]["weight_kg"] == 72.5, str(r.json()["entries"]))

# ---- water: add, negative undo, floor at 0 ----
relax(DEVICE_KEY)
r = client.post("/api/v1/water", headers=bearer(DEVICE_KEY), json={"ml": 750})
wj = r.json()
check("v3 water add 201", r.status_code == 201 and wj["water_ml"] == 750
      and wj["water_target_ml"] >= 2000, str(wj))
r = client.post("/api/v1/water", headers=bearer(DEVICE_KEY), json={"ml": 500})
check("v3 water accumulates", r.json()["water_ml"] == 1250, str(r.json()["water_ml"]))
r = client.post("/api/v1/water", headers=bearer(DEVICE_KEY), json={"ml": -250})
check("v3 water negative undo", r.json()["water_ml"] == 1000, str(r.json()["water_ml"]))
r = client.post("/api/v1/water", headers=bearer(DEVICE_KEY), json={"ml": -99999})
check("v3 water floors at 0 (never negative)", r.json()["water_ml"] == 0, str(r.json()["water_ml"]))
# water target = 35 ml/kg, min 2000. 74kg -> 2590
check("v3 water target = 35ml/kg (74kg -> 2590)", r.json()["water_target_ml"] == 2590,
      str(r.json()["water_target_ml"]))
# water shows up in day totals
r = client.post("/api/v1/water", headers=bearer(DEVICE_KEY), json={"ml": 500})
_today = _date.datetime.now(_date.timezone.utc).strftime("%Y-%m-%d")
r = client.get(f"/api/v1/meals?date={_today}", headers=bearer(DEVICE_KEY))
tot = r.json()["totals"]
check("v3 day totals include water_ml + target", tot["water_ml"] == 500
      and tot["water_target_ml"] == 2590, str({k: tot[k] for k in ('water_ml', 'water_target_ml')}))

# ---- extended nutrients through the chain ----
relax(DEVICE_KEY)
# analyze (mock) now includes fiber/sugar/sodium, top-level = sum(items)
r = client.post("/api/v1/meals/analyze", headers=bearer(DEVICE_KEY),
                files={"image": ("m.png", PNG, "image/png")}, data={"hint": "thali"})
aj = r.json()
_fib_sum = sum((i.get("fiber_g") or 0) for i in aj["items"])
_sod_sum = sum((i.get("sodium_mg") or 0) for i in aj["items"])
check("v3 analyze has extended nutrients", "fiber_g" in aj and "sodium_mg" in aj
      and aj["fiber_g"] == _fib_sum and aj["sodium_mg"] == _sod_sum,
      f"fiber={aj.get('fiber_g')} sum={_fib_sum}")
# manual meal with nutrients -> stored + in totals
r = client.post("/api/v1/meals", headers=bearer(DEVICE_KEY),
                json={"dish": "Oats bowl", "calories": 350, "protein_g": 12, "carbs_g": 55,
                      "fat_g": 8, "fiber_g": 9, "sugar_g": 14, "sodium_mg": 120, "source": "manual"})
oats_id = r.json()["id"]
check("v3 POST /meals stores nutrients", r.status_code == 201 and r.json()["fiber_g"] == 9
      and r.json()["sodium_mg"] == 120, str(r.json().get("fiber_g")))
r = client.get(f"/api/v1/meals?date={_today}", headers=bearer(DEVICE_KEY))
tot = r.json()["totals"]
check("v3 day totals include fiber/sugar/sodium", tot["fiber_g"] >= 9 and tot["sugar_g"] >= 14
      and tot["sodium_mg"] >= 120, str({k: tot[k] for k in ('fiber_g', 'sugar_g', 'sodium_mg')}))
# barcode carries nutrients through the mock + OFF mapping. The mock product is
# deterministic (works offline); assert it carries the v3 nutrient fields.
from sekisho.barcode import mock_product as _mockprod, normalize_off_product as _normoff
_mp = _mockprod("8901058000290")
check("v3 barcode mock product carries fiber/sugar/sodium",
      _mp.per_100g.fiber_g is not None and _mp.per_100g.sodium_mg is not None
      and _mp.per_serving.sugar_g is not None, str(_mp.per_100g.model_dump()))
# OFF mapping: sodium(g)->mg, fiber/sugars from _100g fields
_off = _normoff("123", {"product_name": "X", "nutriments": {
    "energy-kcal_100g": 400, "proteins_100g": 8, "carbohydrates_100g": 60, "fat_100g": 15,
    "fiber_100g": 4, "sugars_100g": 20, "sodium_100g": 0.5}, "serving_size": "50 g"})
check("v3 barcode OFF maps fiber/sugar/sodium (g->mg)",
      _off.per_100g.fiber_g == 4 and _off.per_100g.sugar_g == 20 and _off.per_100g.sodium_mg == 500,
      str(_off.per_100g.model_dump()))

# ---- favorites + recent (dedup by dish, favorites first) ----
relax(DEVICE_KEY)
# log same dish thrice + a distinct dish once
for _ in range(3):
    client.post("/api/v1/meals", headers=bearer(DEVICE_KEY),
                json={"dish": "Poha", "calories": 270, "protein_g": 6, "carbs_g": 46, "fat_g": 7})
r = client.post("/api/v1/meals", headers=bearer(DEVICE_KEY),
                json={"dish": "Upma", "calories": 250, "protein_g": 6, "carbs_g": 40, "fat_g": 8})
upma_id = r.json()["id"]
# favorite the single Upma
r = client.post(f"/api/v1/meals/{upma_id}/favorite", headers=bearer(DEVICE_KEY))
check("v3 favorite meal 200", r.status_code == 200 and r.json()["favorite"] is True, str(r.status_code))
r = client.get("/api/v1/meals/recent", headers=bearer(DEVICE_KEY))
recent = r.json()["recent"]
names = [x["dish"] for x in recent]
check("v3 recent deduped (one Poha entry, count 3)",
      names.count("Poha") == 1 and next(x for x in recent if x["dish"] == "Poha")["count"] == 3,
      str([(x['dish'], x['count']) for x in recent[:4]]))
check("v3 recent favorites-first", recent[0]["dish"] == "Upma" and recent[0]["favorite"] is True,
      str(names[:3]))
check("v3 recent capped at <=20", len(recent) <= 20, str(len(recent)))
# unfavorite
r = client.delete(f"/api/v1/meals/{upma_id}/favorite", headers=bearer(DEVICE_KEY))
check("v3 unfavorite 200", r.status_code == 200 and r.json()["favorite"] is False, str(r.status_code))
# favorite a non-existent meal -> 404
r = client.post("/api/v1/meals/99999999/favorite", headers=bearer(DEVICE_KEY))
check("v3 favorite missing meal 404", r.status_code == 404, str(r.status_code))

# ===========================================================================
# V3: streaks on a fresh seeded user (exact on-target rule boundaries)
# ===========================================================================
relax(DEVICE_KEY)
STREAK_KEY, _ = pair_device("streaker")
client.post("/api/v1/auth/register", headers=bearer(STREAK_KEY),
            json={"username": "streaker", "pin": "3333",
                  "profile": {"name": "S", "age": 30, "sex": "m", "height_cm": 175,
                              "weight_kg": 70.0, "activity": "sedentary", "goal": "maintain"}})
relax(STREAK_KEY)
# target for this profile:
_sp = ProfileIn(name="S", age=30, sex="m", height_cm=175, weight_kg=70.0,
                activity="sedentary", goal="maintain")
_TGT = compute_targets(_sp).calories


def _log_on(day, cals):
    client.post("/api/v1/meals", headers=bearer(STREAK_KEY),
                json={"dish": "seed", "calories": cals, "protein_g": 0, "carbs_g": 0,
                      "fat_g": 0, "eaten_at": f"{day}T12:00:00"})


# 3 consecutive on-target days (exactly target, then comfortably in-band)
_log_on("2026-05-01", _TGT)              # exactly target -> on (<=target boundary)
_log_on("2026-05-02", int(_TGT * 0.8))
_log_on("2026-05-03", int(_TGT * 0.6))
# a day at exactly 50% -> on (>=50% boundary)
_log_on("2026-05-04", int(_TGT * 0.5) + 1)  # >=50%
# a day at 49% -> NOT on (breaks streak)
_log_on("2026-05-06", int(_TGT * 0.49))
# an over-target day -> not on
_log_on("2026-05-07", int(_TGT * 1.2))
r = client.get("/api/v1/streaks", headers=bearer(STREAK_KEY))
sj = r.json()
stamp_days = {s["date"]: s["kind"] for s in sj["stamps"]}
check("v3 streaks: exactly-target day earns stamp", stamp_days.get("2026-05-01") == "on_target",
      str(stamp_days.get("2026-05-01")))
check("v3 streaks: 50% boundary earns stamp", "2026-05-04" in stamp_days, str("2026-05-04" in stamp_days))
check("v3 streaks: 49% day earns NO stamp", "2026-05-06" not in stamp_days, "")
check("v3 streaks: over-target earns NO stamp", "2026-05-07" not in stamp_days, "")
check("v3 streaks: best run = 4 (May 1-4)", sj["best"] == 4, f"best={sj['best']}")
check("v3 streaks: first_log stamp present",
      any(s["kind"] == "first_log" and s["date"] == "2026-05-01" for s in sj["stamps"]), "")
# milestone_7: seed 7 consecutive on-target days on a second fresh user
M7_KEY, _ = pair_device("miles")
client.post("/api/v1/auth/register", headers=bearer(M7_KEY),
            json={"username": "miles", "pin": "4444",
                  "profile": {"name": "M", "age": 30, "sex": "m", "height_cm": 175,
                              "weight_kg": 70.0, "activity": "sedentary", "goal": "maintain"}})
relax(M7_KEY)
for i in range(8):
    d = f"2026-04-{10+i:02d}"
    client.post("/api/v1/meals", headers=bearer(M7_KEY),
                json={"dish": "seed", "calories": int(_TGT * 0.7), "protein_g": 0, "carbs_g": 0,
                      "fat_g": 0, "eaten_at": f"{d}T12:00:00"})
r = client.get("/api/v1/streaks", headers=bearer(M7_KEY))
kinds = {s["date"]: s["kind"] for s in r.json()["stamps"]}
check("v3 streaks: milestone_7 on 7th consecutive day", kinds.get("2026-04-16") == "milestone_7",
      str(kinds.get("2026-04-16")))

# ===========================================================================
# V3: insights math on a seeded week + summary present
# ===========================================================================
relax(M7_KEY)
# The M7 user has a clean week 2026-04-13..2026-04-19 (Mon-Sun) fully on-target.
r = client.get("/api/v1/insights?week=2026-04-15", headers=bearer(M7_KEY))
ij = r.json()
_expected_avg = int(_TGT * 0.7)
check("v3 insights week bounds Mon-Sun", ij["week_start"] == "2026-04-13"
      and ij["week_end"] == "2026-04-19", f"{ij['week_start']}..{ij['week_end']}")
check("v3 insights avg calories correct", ij["averages"]["calories"] == _expected_avg,
      f"{ij['averages']['calories']} vs {_expected_avg}")
check("v3 insights adherence 100% (all on-target)", ij["adherence_pct"] == 100.0,
      str(ij["adherence_pct"]))
check("v3 insights has macro split + summary", "macro_split_pct" in ij and ij.get("summary")
      and len(ij["summary"]) > 10, str(bool(ij.get("summary"))))
# seeded Apr 10-17; the Mon-Sun week 13..19 contains Apr 13,14,15,16,17 = 5 days
check("v3 insights days_logged counts in-week days", ij["days_logged"] == 5, str(ij["days_logged"]))

# ===========================================================================
# V3: export CSV — content + attachment header + proper escaping
# ===========================================================================
relax(DEVICE_KEY)
# seed a meal whose dish name contains a comma + quote to exercise CSV escaping
client.post("/api/v1/meals", headers=bearer(DEVICE_KEY),
            json={"dish": 'Dal, rice & "ghee"', "calories": 500, "protein_g": 18,
                  "carbs_g": 80, "fat_g": 12})
r = client.get("/api/v1/export.csv", headers=bearer(DEVICE_KEY))
ct = r.headers.get("content-type", "")
cd = r.headers.get("content-disposition", "")
body = r.text
check("v3 export.csv content-type + attachment", "text/csv" in ct and "attachment" in cd
      and ".csv" in cd, f"{ct} | {cd}")
check("v3 export.csv has header + record rows", body.startswith("type,id,")
      and "\nmeal," in body, body.splitlines()[0][:30])
check("v3 export.csv escapes commas/quotes in dish",
      '"Dal, rice & ""ghee"""' in body, "escaping")
# parse it back with csv to prove it's valid
import csv as _csv
import io as _io
_rows = list(_csv.reader(_io.StringIO(body)))
_types = {row[0] for row in _rows[1:] if row}
check("v3 export.csv parses + covers meal/workout/weight/water",
      len(_rows) > 1 and {"meal", "weight", "water"}.issubset(_types),
      str(sorted(_types)))

# ===========================================================================
# V3: admin per-tab dense scene backgrounds (different composition per tab)
# ===========================================================================
r = client.get("/admin", cookies=ac)
_dash = r.text
_scene_ids = ["scene-status", "scene-users", "scene-devices", "scene-backends",
              "scene-requests", "scene-push"]
check("v3 admin has 6 per-tab scene layers", all(sid in _dash for sid in _scene_ids), "")
from sekisho.templates import scene_svg as _scene
_scenes = {t: _scene(t) for t in ["status", "users", "devices", "backends", "requests", "push"]}
check("v3 admin scenes are all distinct compositions",
      len(set(_scenes.values())) == 6, f"unique={len(set(_scenes.values()))}")
check("v3 admin scene has dense elements (cartouche+matcha+torii)",
      "腹" in _scenes["status"] and "var(--matcha)" in _scenes["status"], "")

# ===========================================================================
# KEEPER (phone-admin): NO auto-admin, 403 gate, pair/overview/detail,
# revoke, backend switch, and the web admin grant/revoke toggle.
# DEVICE_KEY == kenji (first user -> NOT admin until granted below).
# STREAK_KEY == streaker (later user -> NOT admin).
# ===========================================================================
import base64 as _b64
relax(DEVICE_KEY)
relax(STREAK_KEY)

# ---- NO user is admin by default — not even the first-registered one ----
r = client.get("/api/v1/auth/me", headers=bearer(DEVICE_KEY))
check("keeper: first user is NOT auto-admin (is_admin false)",
      r.status_code == 200 and r.json()["user"]["is_admin"] is False,
      str(r.json().get("user", {}).get("is_admin")))
_kenji_uid = r.json()["user"]["id"]
r = client.get("/api/v1/auth/me", headers=bearer(STREAK_KEY))
check("keeper: later user is not admin",
      r.status_code == 200 and r.json()["user"]["is_admin"] is False,
      str(r.json().get("user", {}).get("is_admin")))

# ---- keeper routes 403 for the (still non-admin) first user ----
relax(DEVICE_KEY)
r = client.get("/api/v1/keeper/overview", headers=bearer(DEVICE_KEY))
check("keeper: non-admin first user gets 403 before grant",
      r.status_code == 403 and r.json().get("error", {}).get("code") == "not_keeper",
      str(r.status_code))

# ---- grant kenji admin via the web admin toggle (the ONLY grant path now) ----
r = client.post(f"/admin/api/users/{_kenji_uid}/admin", cookies=ac, data={"is_admin": "1"})
check("keeper: web admin toggle grants first user admin",
      r.status_code == 200 and r.json().get("is_admin") is True, str(r.status_code))
relax(DEVICE_KEY)
r = client.get("/api/v1/auth/me", headers=bearer(DEVICE_KEY))
check("keeper: granted first user now reports is_admin true",
      r.status_code == 200 and r.json()["user"]["is_admin"] is True,
      str(r.json().get("user", {}).get("is_admin")))

# ---- non-admin -> 403 not_keeper on EVERY /keeper/* route ----
_kroutes = [
    ("POST", "/api/v1/keeper/pair", None),
    ("GET", "/api/v1/keeper/overview", None),
    ("GET", "/api/v1/keeper/users/1", None),
    ("POST", "/api/v1/keeper/devices/dev_nope/revoke", None),
    ("GET", "/api/v1/keeper/backends", None),
    ("POST", "/api/v1/keeper/backends/active", {"slot": "text", "backend_id": "none"}),
]
_bad403 = []
for _m, _p, _d in _kroutes:
    rr = client.request(_m, _p, headers=bearer(STREAK_KEY), data=_d)
    if not (rr.status_code == 403 and rr.json().get("error", {}).get("code") == "not_keeper"):
        _bad403.append(f"{_p}->{rr.status_code}")
check("keeper: non-admin 403 not_keeper on all 6 routes", not _bad403, ",".join(_bad403))

# ---- keeper pair mints {token,url,qr_png_base64,expires_in} + token claims ----
relax(DEVICE_KEY)
r = client.post("/api/v1/keeper/pair", headers=bearer(DEVICE_KEY))
pj = r.json()
check("keeper pair shape {token,url,qr_png_base64,expires_in}",
      r.status_code == 200 and set(pj) == {"token", "url", "qr_png_base64", "expires_in"}
      and isinstance(pj.get("qr_png_base64"), str) and len(pj["qr_png_base64"]) > 0,
      str(sorted(pj)))
check("keeper pair qr_png_base64 decodes to a PNG",
      _b64.b64decode(pj["qr_png_base64"])[:8] == b"\x89PNG\r\n\x1a\n", "")
r = client.post("/api/v1/pair/claim", json={"token": pj["token"], "device_name": "keeper-paired"})
check("keeper pair token claims successfully (201 + device_key)",
      r.status_code == 201 and r.json()["device_key"].startswith("hh_"), str(r.status_code))
_revoke_dev_id = r.json()["device_id"]

# ---- overview shape ----
relax(DEVICE_KEY)
r = client.get("/api/v1/keeper/overview", headers=bearer(DEVICE_KEY))
oj = r.json()
check("keeper overview top-level {users,devices,backend,push_subs,public_url}",
      r.status_code == 200 and set(oj) == {"users", "devices", "backend", "push_subs", "public_url"},
      str(sorted(oj)))
check("keeper overview users[] item shape",
      len(oj["users"]) > 0 and set(oj["users"][0]) == {
          "id", "username", "goal", "meals_today_calories", "target",
          "streak_current", "last_activity"},
      str(sorted(oj["users"][0]) if oj["users"] else []))
check("keeper overview devices[] item shape",
      len(oj["devices"]) > 0 and set(oj["devices"][0]) == {
          "id", "name", "last_seen", "active_username"},
      str(sorted(oj["devices"][0]) if oj["devices"] else []))
check("keeper overview backend {mode,active_text,active_vision}",
      set(oj["backend"]) == {"mode", "active_text", "active_vision"}
      and oj["backend"]["mode"] in ("connected", "mock"), str(oj["backend"]))
check("keeper overview push_subs int + public_url str",
      isinstance(oj["push_subs"], int) and isinstance(oj["public_url"], str), str(oj["public_url"]))

# ---- user detail (profile/targets/recent meals/day totals) ----
relax(DEVICE_KEY)
r = client.get("/api/v1/keeper/users/1", headers=bearer(DEVICE_KEY))
uj = r.json()
check("keeper user detail shape {user,profile,targets,meals,day_totals}",
      r.status_code == 200 and set(uj) == {"user", "profile", "targets", "meals", "day_totals"},
      str(sorted(uj)))
r = client.get("/api/v1/keeper/users/9999999", headers=bearer(DEVICE_KEY))
check("keeper user detail unknown -> 404", r.status_code == 404, str(r.status_code))

# ---- backends payload + active switch (reuses admin internals) ----
relax(DEVICE_KEY)
r = client.get("/api/v1/keeper/backends", headers=bearer(DEVICE_KEY))
bj = r.json()
check("keeper backends payload shape",
      r.status_code == 200 and {"backends", "active_text_id", "active_vision_id", "presets"} <= set(bj),
      str(sorted(bj)))
# create a backend via admin, switch active-text to it via keeper, verify, clean up
r = client.post("/admin/api/backends", cookies=ac,
                data={"name": "KeeperSwitch", "kind": "vllm", "base_url": "http://127.0.0.1:9/v1",
                      "model": "m", "vision": "0", "enabled": "1"})
_sw_id = r.json()["backend"]["id"]
r = client.post("/api/v1/keeper/backends/active", headers=bearer(DEVICE_KEY),
                data={"slot": "text", "backend_id": str(_sw_id)})
check("keeper backend switch active text -> ok", r.status_code == 200 and r.json().get("ok") is True,
      str(r.status_code))
r = client.get("/api/v1/keeper/backends", headers=bearer(DEVICE_KEY))
check("keeper backend switch reflected in active_text_id", r.json()["active_text_id"] == _sw_id,
      str(r.json().get("active_text_id")))
r = client.post("/api/v1/keeper/backends/active", headers=bearer(DEVICE_KEY),
                data={"slot": "bogus", "backend_id": "none"})
check("keeper backend switch bad slot -> 400", r.status_code == 400, str(r.status_code))
client.post("/api/v1/keeper/backends/active", headers=bearer(DEVICE_KEY),
            data={"slot": "text", "backend_id": "none"})
client.post(f"/admin/api/backends/{_sw_id}/delete", cookies=ac)

# ---- revoke the keeper-paired device ----
relax(DEVICE_KEY)
r = client.post(f"/api/v1/keeper/devices/{_revoke_dev_id}/revoke", headers=bearer(DEVICE_KEY))
check("keeper revoke device -> ok", r.status_code == 200 and r.json().get("ok") is True, str(r.status_code))
r = client.get("/api/v1/keeper/overview", headers=bearer(DEVICE_KEY))
check("keeper revoke removes device from overview",
      _revoke_dev_id not in [d["id"] for d in r.json()["devices"]], str(_revoke_dev_id))

# ---- web admin grant/revoke toggle flips the keeper gate ----
relax(DEVICE_KEY)
relax(STREAK_KEY)
r = client.get("/admin/api/users", cookies=ac)
_streaker = next(u for u in r.json()["users"] if u["username"] == "streaker")
_sid = _streaker["id"]
check("admin users list carries is_admin flag (streaker=false)",
      "is_admin" in _streaker and _streaker["is_admin"] is False, str(_streaker.get("is_admin")))
r = client.post(f"/admin/api/users/{_sid}/admin", cookies=ac, data={"is_admin": "1"})
check("admin grant keeper flag -> ok",
      r.status_code == 200 and r.json().get("is_admin") is True, str(r.status_code))
relax(STREAK_KEY)
r = client.get("/api/v1/keeper/overview", headers=bearer(STREAK_KEY))
check("keeper: granted user now passes gate (200)", r.status_code == 200, str(r.status_code))
r = client.post(f"/admin/api/users/{_sid}/admin", cookies=ac, data={"is_admin": "0"})
check("admin revoke keeper flag -> ok",
      r.status_code == 200 and r.json().get("is_admin") is False, str(r.status_code))
relax(STREAK_KEY)
r = client.get("/api/v1/keeper/overview", headers=bearer(STREAK_KEY))
check("keeper: revoked user gets 403 not_keeper again",
      r.status_code == 403 and r.json().get("error", {}).get("code") == "not_keeper", str(r.status_code))
r = client.post("/admin/api/users/9999999/admin", cookies=ac, data={"is_admin": "1"})
check("admin toggle unknown user -> 404", r.status_code == 404, str(r.status_code))

# ---- SEKISHO_ADMIN_USERS env grants admin idempotently (additive, never revokes) ----
from sekisho import accounts as _accounts
os.environ["SEKISHO_ADMIN_USERS"] = "streaker,does_not_exist"
_granted = _accounts.apply_admin_users_env()
check("env SEKISHO_ADMIN_USERS grants existing user (streaker)",
      "streaker" in _granted and "does_not_exist" not in _granted, str(_granted))
check("env grant reflected in is_admin", _accounts.is_admin(_sid) is True, str(_sid))
# Idempotent: a second apply is a no-op re-grant, and it must not clobber kenji's
# dashboard-set flag (additive only).
_accounts.apply_admin_users_env()
check("env grant is idempotent + additive (kenji keeps admin)",
      _accounts.is_admin(_sid) is True and _accounts.is_admin(_dev_uid) is True, "")
# clean up so the env doesn't leak into later state
os.environ.pop("SEKISHO_ADMIN_USERS", None)
_accounts.set_admin(_sid, False)

# ===========================================================================
# V2: v1 -> v2 migration (isolated DB; saves/restores the global connection)
# ===========================================================================
def _test_migration():
    import sqlite3 as _sql
    mp = Path(tempfile.mkdtemp(prefix="sekisho_mig_")) / "v1.db"
    c = _sql.connect(str(mp))
    c.executescript(
        "CREATE TABLE devices (device_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "key_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, last_seen TEXT, "
        "req_count INTEGER DEFAULT 0, revoked INTEGER DEFAULT 0);"
        "CREATE TABLE profiles (device_id TEXT PRIMARY KEY, name TEXT, age INT, sex TEXT, "
        "height_cm INT, weight_kg REAL, activity TEXT, goal TEXT, updated_at TEXT);"
        "CREATE TABLE meals (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, dish TEXT, "
        "calories INT, protein_g INT, carbs_g INT, fat_g INT, eaten_at TEXT, source TEXT);"
        "INSERT INTO devices VALUES ('dev_legacy0001','oldphone','hashx','2026-01-01T00:00:00',NULL,3,0);"
        "INSERT INTO profiles VALUES ('dev_legacy0001','Old',28,'m',175,72.0,'light','maintain','2026-01-01T00:00:00');"
        "INSERT INTO meals (device_id,dish,calories,protein_g,carbs_g,fat_g,eaten_at,source) "
        "VALUES ('dev_legacy0001','Legacy dal',400,15,60,8,'2026-01-01T12:00:00','manual');"
    )
    c.commit()
    c.close()
    saved = db._conn
    try:
        db._conn = None
        db.init_db(mp)
        with db.cursor() as cur:
            users = cur.execute("SELECT id FROM users").fetchall()
            dev = cur.execute("SELECT active_user_id FROM devices").fetchone()
            meal = cur.execute("SELECT user_id FROM meals").fetchone()
            prof = cur.execute("SELECT user_id FROM profiles").fetchone()
        ok = (len(users) == 1 and dev["active_user_id"] == users[0]["id"]
              and meal["user_id"] == users[0]["id"] and prof["user_id"] == users[0]["id"])
        check("v2 migration: per-device data -> auto-created user", ok,
              f"users={len(users)} linked={dev['active_user_id']}")
    finally:
        db.close_db()
        db._conn = saved  # restore the main suite's connection


_test_migration()


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _print_table() -> int:
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    width = max(len(n) for n, _, _ in _RESULTS) + 2
    print("\n" + "=" * (width + 24))
    print(f"  SEKISHO v2 SMOKE — {passed}/{total} PASS")
    print("=" * (width + 24))
    for name, ok, detail in _RESULTS:
        tag = "PASS" if ok else "FAIL"
        line = f"  [{tag}] {name.ljust(width)}"
        if not ok and detail:
            line += f"  <- {detail}"
        elif ok and detail:
            line += f"  ({detail})"
        print(line)
    print("=" * (width + 24))
    print(f"  {passed}/{total} PASS,  {total - passed} FAIL")
    print("=" * (width + 24) + "\n")
    return total - passed


def test_smoke_all_pass():
    """pytest entrypoint — the module runs all checks at import; assert none failed."""
    fails = [n for n, ok, _ in _RESULTS if not ok]
    assert not fails, f"failed checks: {fails}"


if __name__ == "__main__":
    failures = _print_table()
    sys.exit(1 if failures else 0)
