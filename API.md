# Sekisho API Contract (v1)

Base URL: `http://<server>:8420/api/v1`
Auth: `Authorization: Bearer <device_key>` on everything except pairing claim.
All bodies JSON unless noted. Errors: `{ "error": { "code": str, "message": str } }`.

## Pairing (how a phone joins)

1. Admin opens `/admin` on the laptop → "Pair new device" → server creates a
   short-lived pairing token and renders a QR containing:
   `{"v":1,"url":"http://100.x.y.z:8420","token":"<pairing_token>"}`
2. Phone scans QR (or types the code) →
   `POST /pair/claim` `{ "token": str, "device_name": str }`
   → `201 { "device_id": str, "device_key": str }`  (key shown once; app stores it)
3. Tokens expire in 300s and are single-use.

## Endpoints

### `GET /health`  (no auth)
`{ "status": "ok", "vllm": "connected" | "mock", "model": str | null }`

### `POST /meals/analyze`
multipart/form-data: `image` (jpeg/png/webp, ≤8MB), optional `hint` (text),
optional `portion` ("small"|"regular"|"large").
Response `200`:
```json
{
  "dish": "Paneer butter masala with 2 rotis",
  "calories": 640,
  "protein_g": 24, "carbs_g": 58, "fat_g": 32,
  "confidence": 0.78,
  "question": "Was the paneer portion closer to 100g or 200g?",
  "items": [ { "name": "Paneer butter masala", "calories": 420 },
             { "name": "Roti x2", "calories": 220 } ]
}
```
`question` is null when confidence ≥ 0.85. Server prompts the vLLM vision
model with a strict-JSON system prompt and validates/repairs the output
(pydantic). If vLLM is unreachable → deterministic mock analyzer (keyed off
image hash + hint) so the app always works. `503` only if mock disabled.

**Complex/Indian meals**: the vision prompt MUST instruct itemization — a
plate is often a main plus sides (dal + rice + 2 roti + sabzi + curd + pickle
+ papad; thali; biryani + raita). `items[]` is required, one entry per
component with its own `calories`, `protein_g`, `carbs_g`, `fat_g`, and the
top-level numbers are the sum. Each item also carries an OPTIONAL
`point: {"x": 0..1, "y": 0..1}` — the approximate center of that component in
the image (normalized) — so the app can pin each label next to its part of the
dish. Ask the vision model for it; omit when the model can't ground reliably.
The mock analyzer emits plausible spread-out points. Ask ONE clarifying `question` when a
high-calorie ambiguity exists (ghee on roti? paneer vs tofu? portion of
rice?). The mock analyzer must also return multi-item plates for realism.

### `GET /food/barcode/{code}` — packaged-food lookup
Proxies OpenFoodFacts server-side (`https://world.openfoodfacts.org/api/v2/product/{code}.json`),
normalizes, and caches results in SQLite. Response `200`:
```json
{ "found": true, "code": "8901058000290",
  "product": { "name": "Maggi Masala Noodles", "brand": "Nestlé",
    "serving": "70 g",
    "per_serving": { "calories": 313, "protein_g": 7, "carbs_g": 42, "fat_g": 13 },
    "per_100g":    { "calories": 447, "protein_g": 10, "carbs_g": 60, "fat_g": 18 } } }
```
`found:false` (200) when the barcode is unknown. Deterministic mock product
when the server has no internet and mock is enabled. Auth + rate limit apply.

### `POST /meals`  — log a confirmed meal
`{ "dish": str, "calories": int, "protein_g": int, "carbs_g": int, "fat_g": int,
   "eaten_at": iso8601?, "source": "photo"|"manual"|"suggestion" }`
→ `201` with the stored meal (`id`, `eaten_at` filled).

### `GET /meals?date=YYYY-MM-DD` / `GET /meals?from=&to=`
→ `{ "meals": [...], "totals": { "calories": int, "protein_g": ..., "carbs_g": ..., "fat_g": ... } }`

### `DELETE /meals/{id}` → `204`

### `GET /suggestions?date=YYYY-MM-DD`
Server computes remaining kcal/macros vs profile targets, mines this device's
meal history for repeatable meals that fit, asks the LLM to rank/phrase them
(mock: rule-based). →
`{ "remaining": { "calories": int, "protein_g": int, ... },
   "suggestions": [ { "dish": str, "calories": int, "protein_g": int,
                      "carbs_g": int, "fat_g": int, "reason": str } ] }`

### `PUT /profile` / `GET /profile`
`{ "name": str, "age": int, "sex": "m"|"f"|"x", "height_cm": int,
   "weight_kg": float, "activity": "sedentary"|"light"|"moderate"|"active",
   "goal": "cut"|"maintain"|"bulk" }`
GET response additionally includes computed
`targets: { "calories": int, "protein_g": int, "carbs_g": int, "fat_g": int }`
(Mifflin-St Jeor TDEE; cut −20%, bulk +10%; protein 1.6 g/kg).

### `POST /workouts` / `GET /workouts?date=`
`{ "kind": str, "minutes": int, "calories_burned": int, "note": str? }`
Burned calories are returned in `GET /meals` totals as `burned` so the app can
extend the day's road.

### `GET /calendar?month=YYYY-MM`
`{ "days": [ { "date": "...", "calories": int, "target": int,
               "burned": int, "status": "under"|"on"|"over"|"empty" } ] }`

### `POST /chat`  (optional passthrough)
OpenAI-style `{ "messages": [...] }` → proxied to vLLM with guardrails
(nutrition-coach system prompt, 512-token cap). Mock replies if no vLLM.

## v2: Accounts, body composition, backends, assistant, push

### Accounts (user login on top of device pairing)
Device keys remain the transport credential; a **user account** now owns the
data (profile/meals/workouts/etc all keyed by `user_id`, not device).
- `POST /auth/register` `{ "username": str, "pin": str(4-8 digits), "profile": {...} }`
  → `201 { "user": {...} }` and the device's active user becomes this user.
- `POST /auth/login` `{ "username": str, "pin": str }` → `200 { "user", "profile" }`;
  sets the device's active user. PINs hashed (per-user salt). 5 bad attempts →
  60s lockout.
- `POST /auth/logout` → device has no active user; data endpoints return 401
  `{code:"no_user"}` until login.
- `GET /auth/me` → active user + profile. Switching accounts = login again.
- Migration: existing per-device data moves to an auto-created user per device.

### Body composition (in profile)
`profile` gains `body_fat_pct: float|null` and `body_fat_goal_pct: float|null`.
When `body_fat_pct` is set, targets switch from Mifflin-St Jeor to
**Katch-McArdle** (BMR = 370 + 21.6 × lean mass kg) with the same goal/activity
modifiers.
- `POST /profile/body-photo` — multipart `image` (full-body photo) → vision LLM
  estimates `{ "body_fat_pct": float, "range": [lo, hi], "confidence": float,
  "notes": str }` (strict JSON prompt, pydantic-validated; deterministic mock
  fallback). The app shows an EDITABLE result screen before saving via
  `PUT /profile`. Never store the photo.

### LLM backend registry (NVIDIA API / vLLM / LM Studio)
All three speak OpenAI-compatible chat completions; the server manages a
registry instead of a single URL:
- Table `backends`: `{ id, name, kind: "vllm"|"nvidia"|"lmstudio"|"openai",
  base_url, api_key?, model, vision: bool, enabled }`. Bootstrap from
  `SEKISHO_BACKENDS_JSON` env or legacy `SEKISHO_VLLM_URL/MODEL`.
- Settings: `active_vision_backend`, `active_text_backend`. **Failover**: if
  the active backend errors/times out, try the next enabled backend with the
  needed capability (vision for analyze/body-photo); mock only after all fail.
- Admin endpoints (admin session): CRUD `/admin/backends`, switch active,
  `POST /admin/backends/{id}/test` (1-token ping + models list). Presets in
  the admin UI: NVIDIA `https://integrate.api.nvidia.com/v1`, LM Studio
  `http://host.docker.internal:1234/v1`, vLLM `http://host.docker.internal:8000/v1`.
- `/health` reports the active backends. `/llm/v1/*` uses the active text
  backend and pins its model.

### Assistant chat (案内人 Annai) — `POST /chat` v2
`{ "messages": [{role, content}...] }` → `{ "reply": str, "action": null | {...} }`.
Server injects a system prompt with: profile + targets, today's totals and
remaining macros, last 14 days of meal history (names+kcal), and a short app
manual (stations, how logging works). The LLM may embed ONE action as a fenced
```json block, which the server parses, EXECUTES, strips from the reply, and
returns in `action`:
- `{ "action": "log_meal", "dish": str, "calories": int, "protein_g": int,
  "carbs_g": int, "fat_g": int }` → logs it (so "I ate 2 idlis and sambar" just works)
- `{ "action": "log_workout", "kind": str, "minutes": int, "calories_burned": int }`
- `{ "action": "set_goal", "goal": "cut"|"maintain"|"bulk" }`
Rule-based mock when no backend (can still parse "I ate <food>" for common
Indian foods). Cap 512 tokens; nutrition-coach + app-guide persona.

### Push reminders
- `POST /push/subscribe` `{ subscription: <WebPush JSON>, tz_offset_min: int }`
  (per user+device); `DELETE /push/subscribe`. VAPID keys auto-generated once
  and persisted under `/data`; `GET /push/vapid-public-key` (no auth).
- Server scheduler (asyncio) fires per user at local **18:00** — "誰 X kcal
  and Yg protein to go — <a regular meal from their history that fits> would
  land you on target" — and **22:00** — day-close recap (hit/under/over + a
  gentle hara-hachi-bu line). Skips if the goal is already met (18:00) or
  nothing logged (22:00 becomes "don't forget to log"). `POST /push/test`
  (admin) sends one immediately. App shows the same reminder as an in-app
  glass banner when push is unavailable (APK webview / denied permission).

### Admin dashboard v2 (all controls + data + logs)
Same design system, new cards/pages: **Users** (list, profile, targets,
body-fat, meals/workout counts, last activity; drill into a user's recent
meals/day totals), **Devices** (paired, last seen, active user, revoke),
**Backends** (registry CRUD, active switches, test buttons, health),
**Request log** (live tail, filter by device/route/status), **Push** (subscriptions,
send test), **Tunnel/status** (public URL, uptime, vLLM/mock mode), and the
existing pairing QR + glass slider.

## v3: The feedback loop (weight, streaks, quick-log, insights, water, export)

- **Weight**: `POST /weight {weight_kg, date?}` / `GET /weight?from&to` →
  `{entries:[{date,weight_kg}], trend:[{date,avg_kg}]}` (7-day moving avg).
  Profile gains `weight_goal_kg` + `pace_kg_per_week` (0.25/0.5/0.75); targets
  recompute the calorie deficit/surplus from pace (7700 kcal ≈ 1 kg) instead
  of the flat ±20/10%, clamped to ≥1200 kcal. GET /profile returns
  `eta_date` for reaching the goal at current pace.
- **Water**: `POST /water {ml}` (negative allowed to undo); day totals in
  `GET /meals?date` gain `water_ml` + `water_target_ml` (35 ml/kg, min 2000).
- **Extended nutrients**: meals/analyze/barcode/manual gain optional
  `fiber_g, sugar_g, sodium_mg` (vision prompt + OFF mapping + mock); totals
  include them.
- **Quick-log**: `POST/DELETE /meals/{id}/favorite`; `GET /meals/recent` →
  deduped list (favorites first, then most-frequent recent dishes, ≤20) for
  one-tap re-logging.
- **Streaks & stamp book**: `GET /streaks` → `{current, best,
  stamps:[{date, kind: "on_target"|"milestone_7"|"milestone_30"|"first_log"}]}`.
  A day earns its stamp when calories land ≤ target (and ≥ 50% of it).
- **Insights**: `GET /insights?week=YYYY-MM-DD(any day in week)` → averages
  (kcal/macros/water), adherence %, best/worst day, weight delta, macro
  split %, and `summary` (LLM-written in Annai's voice; rule-based mock).
- **Export**: `GET /export.csv` (device auth) → all of the user's meals,
  workouts, weights, water as CSV.

## LLM API passthrough — `/llm/v1/*` (NOT under /api/v1)

OpenAI-compatible endpoint surface (`/llm/v1/chat/completions`, `/llm/v1/models`)
forwarded to the configured vLLM backend, so clients on the phone can use the
LLM API directly through nginx. Rules:
- Same `Authorization: Bearer <device_key>` auth + per-device rate limit.
- `model` field is overridden with `SEKISHO_VLLM_MODEL`; `max_tokens` capped
  at 1024. Streaming may be rejected (400) for simplicity.
- `503` with a clear message when no vLLM is configured — this route is never
  mocked.

## Deployment: ONE Docker container + Cloudflare Tunnel (works anywhere)

A single image (no compose) runs everything, supervised by an entrypoint:

- Base python:3.11-slim with `nginx` and `cloudflared` installed. The built
  phone app is COPYed into the image at `/srv/app` at build time.
- Entrypoint starts: uvicorn (`python -m sekisho`, 127.0.0.1:8420) → nginx
  (port 80: serves `/srv/app` with SPA fallback; proxies `/api/`, `/admin`,
  `/pair`, `/llm/` to 127.0.0.1:8420; `client_max_body_size 16m`) → and, when
  `TUNNEL=cloudflare` (default on), `cloudflared tunnel --url
  http://127.0.0.1:80`, parsing the assigned `https://*.trycloudflare.com`
  URL, exporting it as the pairing/public origin, and printing it big in the
  logs. Any process dying kills the container (restart policy handles it).
- **Why this is safe**: the tunnel is outbound-only (no router ports opened,
  home/college IP never exposed), Cloudflare terminates TLS (real cert, so
  PWA + camera secure-context just work), and the app-level security stays:
  per-device Bearer keys, hashed at rest, rate limits, admin password, vLLM
  never directly reachable. Optional `TUNNEL_TOKEN` env supports a named
  Cloudflare tunnel (stable domain) for later.
- Env: `SEKISHO_DB_PATH=/data/sekisho.db` (volume `/data`), `TUNNEL`
  (`cloudflare`|`off`), `SEKISHO_PUBLIC_URL` (manual override when TUNNEL=off
  and you front it yourself), plus the existing vLLM/admin vars. vLLM on the
  host is reachable via `host.docker.internal`.
- Run: `docker build -t harahachi . && docker run -p 80:80 -v hh-data:/data harahachi`
  → logs print the public `https://…trycloudflare.com` URL → open it on the
  phone anywhere in the world, install, pair via `/admin` QR (QR encodes the
  tunnel URL). LAN/hotspot still works via `http://<laptop-ip>/` when
  preferred.
- The old docker-compose.yml / self-signed-cert path is removed.

## Admin dashboard (`/admin`, same design system as the app)

- Login with `SEKISHO_ADMIN_PASSWORD` (env, default generated & printed once).
- Cards: vLLM status, paired devices (name, last seen, request count, revoke),
  pair-new-device QR (auto-refreshing), recent request log, and the glass
  transparency slider.

## Server internals (build guidance)

- Python 3.11+, FastAPI + uvicorn, SQLite via `sqlite3`/SQLModel (file
  `sekisho.db`), `qrcode` for QR PNGs, `httpx` for vLLM calls.
- Device keys: `hh_<32 hex>`; store SHA-256 only. Rate limit: 30 req/min/device
  (in-memory sliding window is fine).
- Config via env: `SEKISHO_PORT` (8420), `SEKISHO_VLLM_URL`,
  `SEKISHO_VLLM_MODEL`, `SEKISHO_ADMIN_PASSWORD`, `SEKISHO_ALLOW_MOCK` (1).
- CORS: allow all origins (device key is the real gate; PWA may be served
  from a different origin during dev).
- Data is per-device (device_id foreign key) — one phone = one user profile.
- Runnable as `python -m sekisho`; print LAN + Tailscale-looking IPs and the
  admin URL on startup.
