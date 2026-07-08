# Sekisho (関所) — Harahachi edge-AI gateway

FastAPI server (v2): device pairing, **user accounts** (PIN login on top of
pairing), Bearer auth + rate limiting, photo→macro analysis (vision LLM with a
deterministic mock fallback), **body-composition estimate from a photo**,
packaged-food barcode lookup, meals/profile/workouts/calendar, history-mined
suggestions, a **multi-backend LLM registry with failover** (NVIDIA API / vLLM /
LM Studio / OpenAI), an **Annai assistant chat** that can log meals/workouts,
**web-push reminders**, an OpenAI-compatible passthrough, and a map-styled,
tabbed `/admin` dashboard. It is the `server/` half of the Harahachi project;
the phone PWA lives in `../app`.

## What's new in v2

- **Accounts** — the account owns the data now (profile/meals/workouts keyed by
  `user_id`); the device key remains the transport credential. Register/login
  with a username + 4–8 digit PIN (salted PBKDF2, 5 bad tries → 60s lockout).
  An existing v1 database is auto-migrated: each paired device gets an
  auto-created user and its data is moved onto it.
- **Body composition** — `profile` gains `body_fat_pct` / `body_fat_goal_pct`;
  when body fat is set, targets switch from Mifflin-St Jeor to **Katch-McArdle**.
  `POST /profile/body-photo` returns an editable body-fat estimate (photo never
  stored).
- **LLM backend registry** — a `backends` table of OpenAI-compatible endpoints
  with `active_vision`/`active_text` pointers and automatic **failover**; mock is
  only used after every capable backend fails.
- **Annai chat** — `POST /chat` returns `{reply, action}`; the model (or the
  rule-based mock) may embed ONE fenced json action that the server executes and
  strips ("I ate 2 idlis and sambar" logs the meal).
- **Push reminders** — VAPID keys auto-generated + persisted; per-user 18:00 /
  22:00 local reminders via an asyncio scheduler.

## What's new in v3 — the feedback loop

- **Weight + trend** — `POST /weight {weight_kg, date?}` (one row per user+date,
  last write wins) / `GET /weight?from&to` → `{entries, trend}` where `trend` is
  the **7-day trailing moving average**. Profile gains `weight_goal_kg` +
  `pace_kg_per_week` (0.25/0.5/0.75); when both are set (and point the same way as
  `goal`), targets recompute the calorie deficit/surplus **from the pace**
  (7700 kcal/kg, clamped ≥1200) instead of the flat ±20/10%. Base BMR selection
  (Katch-McArdle when body-fat known, else Mifflin) is unchanged. `GET /profile`
  now returns `eta_date` (projected goal date at current pace).
- **Water** — `POST /water {ml}` (negative to undo, floored at 0). Day totals in
  `GET /meals?date` gain `water_ml` + `water_target_ml` (35 ml/kg, min 2000).
- **Extended nutrients** — optional `fiber_g / sugar_g / sodium_mg` flow through
  analyze (vision prompt + mock), barcode (OFF `fiber/sugars/sodium` mapping,
  sodium g→mg), manual meals, and day totals.
- **Quick-log** — `POST/DELETE /meals/{id}/favorite`; `GET /meals/recent` →
  deduped list (favorites first, then most-frequent recent dishes, ≤20).
- **Streaks & stamp book** — `GET /streaks` → `{current, best, stamps[]}`; a day
  earns its stamp when calories land ≤ target **and** ≥ 50% of it. Stamp kinds:
  `first_log`, `on_target`, `milestone_7`, `milestone_30`.
- **Weekly insights** — `GET /insights?week=YYYY-MM-DD` → averages, adherence %,
  best/worst day, weight delta, macro split %, and a `summary` written by the
  active text backend in Annai's voice (rule-based mock otherwise).
- **Export** — `GET /export.csv` → all of the user's meals, workouts, weights and
  water as one CSV (proper quoting, `Content-Disposition: attachment`).
- **Admin backgrounds** — the `/admin` dashboard now renders a dense ukiyo-e
  scene per tab (overlapping shaded mountains with snow caps, pine forests,
  village/pagoda clusters, torii, ink boats, kasumi mist, seigaiha water, red
  legend cartouche), a **different composition per tab** so Status/Users/Devices/
  Backends/Requests/Push each read as another province. All inline server-rendered
  SVG in the existing palette; glass cards stay readable above it.

## Quickstart — ONE Docker container + Cloudflare Tunnel (works anywhere)

No compose, no self-signed certs. A single image runs uvicorn + nginx +
cloudflared together, supervised by an entrypoint. Cloudflare gives you a real
HTTPS URL over an **outbound-only** tunnel, so the phone can reach the server
from anywhere in the world — no router ports, no home/college IP exposed.

```bash
# 1. Build the phone app first (it is COPYed into the image at /srv/app).
cd app && npm install && npm run build

# 2. Build the image. NOTE: build context is the REPO ROOT so app/dist is visible.
cd ..
docker build -f server/Dockerfile -t harahachi .

# 3. Run it. The default TUNNEL=cloudflare prints a public https URL in the logs.
docker run -p 80:80 -v hh-data:/data harahachi
```

The logs print a big banner:

```
===================================================================
  関所 Sekisho is live at:

      https://random-words-1234.trycloudflare.com

  Open it on your phone (anywhere in the world), install the PWA,
  then pair via https://random-words-1234.trycloudflare.com/admin
===================================================================
```

Open that URL on the phone → "Add to Home Screen" (installs the PWA, camera +
full screen work because Cloudflare serves a real cert) → open `/admin` on the
laptop, click **Pair a new device**, and scan the QR. **The QR encodes the tunnel
URL**, so the app talks to the same public origin.

### What runs inside the one container

| Process       | Bind            | Role                                                             |
|---------------|-----------------|-----------------------------------------------------------------|
| `uvicorn`     | `127.0.0.1:8420`| The FastAPI app (loopback only — never directly reachable).     |
| `nginx`       | `:80`           | Front door: serves `/srv/app` (SPA fallback) + proxies `/api/ /pair /admin /llm/` to uvicorn; `client_max_body_size 16m`. |
| `cloudflared` | outbound        | Tunnel exposing `:80` to `https://*.trycloudflare.com`.         |

If **any** of the three dies, the entrypoint exits the container so your
`--restart` policy brings it back cleanly.

### Why this is safe

- **Outbound-only tunnel** — cloudflared dials *out* to Cloudflare; no inbound
  ports are opened on your router and your home/college IP is never exposed.
- **Real TLS at the edge** — Cloudflare terminates HTTPS with a valid cert, so
  PWA install + `getUserMedia` camera (secure-context only) just work.
- **App-level security is unchanged** — per-device `Bearer hh_<32hex>` keys
  (stored SHA-256-hashed), 30 req/min/device rate limit, admin password, and the
  vLLM backend is never directly reachable (only the gated `/llm/` passthrough).

### Tunnel modes

| `TUNNEL`     | Behaviour                                                                                   |
|--------------|---------------------------------------------------------------------------------------------|
| `cloudflare` | **(default)** Quick tunnel → random `https://*.trycloudflare.com`; URL parsed from the log and used for the QR/pairing origin. |
| `off`        | No tunnel. Reach the app over LAN at `http://<laptop-ip>/`. Set `SEKISHO_PUBLIC_URL=http://<laptop-ip>` so the QR points there. |
| named tunnel | Set `TUNNEL_TOKEN=<token>` (from the Cloudflare dashboard) for a **stable domain**; also set `SEKISHO_PUBLIC_URL=https://your.domain` so the QR encodes it. |

```bash
# LAN-only (no tunnel), phone on the same WiFi/hotspot:
docker run -p 80:80 -v hh-data:/data \
  -e TUNNEL=off -e SEKISHO_PUBLIC_URL=http://192.168.29.99 harahachi

# Named/stable Cloudflare tunnel:
docker run -p 80:80 -v hh-data:/data \
  -e TUNNEL_TOKEN=eyJ... -e SEKISHO_PUBLIC_URL=https://harahachi.example.com harahachi
```

### Connecting LLM backends (registry + failover)

v2 keeps a **registry** of OpenAI-compatible backends instead of a single URL.
All of NVIDIA API / vLLM / LM Studio / OpenAI speak the same chat-completions
surface. Two active pointers select the **text** and **vision** backend; on
error/timeout the client fails over to the next enabled capable backend, and
mocks only after all fail.

**Bootstrap** the registry from env (runs once, admin edits are never clobbered):

```bash
# Preferred: a JSON list of backends.
-e SEKISHO_BACKENDS_JSON='[
  {"name":"NVIDIA","kind":"nvidia","base_url":"https://integrate.api.nvidia.com/v1",
   "api_key":"nvapi-XXXX","model":"meta/llama-3.2-90b-vision-instruct","vision":true},
  {"name":"LM Studio","kind":"lmstudio","base_url":"http://host.docker.internal:1234/v1",
   "model":"local-model","vision":false}
]'

# Legacy fallback (still works): single vLLM URL/model.
-e SEKISHO_VLLM_URL=http://host.docker.internal:8000/v1 \
-e SEKISHO_VLLM_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
```

Backends on the host (vLLM, LM Studio) are reached via `host.docker.internal`
(add `--add-host host.docker.internal:host-gateway` on Linux). After boot you can
add/edit/switch/test backends live in **`/admin` → Backends** — it has presets
(NVIDIA `https://integrate.api.nvidia.com/v1`, LM Studio
`http://host.docker.internal:1234/v1`, vLLM `http://host.docker.internal:8000/v1`),
active vision/text switches, and a **Test** button (models list + 1-token ping).

Without any backend the deterministic mock analyzer serves realistic multi-item
Indian plates (with per-item `point` overlays) and the rule-based Annai mock can
still log common Indian foods, so the whole app works end-to-end on any laptop.

### Fail-fast behaviour

Every backend call has explicit timeouts so a slow/dead backend can never hang a
request — the app gets a clean JSON error envelope and shows a popup instead of a
stuck screen. Connect ≈ 3s (so failover kicks in fast), total ≈ 60s for
analyze / body-photo and ≈ 30s for chat. When all backends fail **and mock is
disabled**, `analyze`, `chat`, and `body-photo` return `503
{"error":{"code":"vllm_unavailable", ...}}`. `/llm/v1/*` is never mocked (always
503 when unavailable).

## How the public URL is resolved

`net.public_url()` picks the pairing/QR origin in this order:

1. `SEKISHO_PUBLIC_URL` env — explicit override.
2. Contents of `/run/sekisho_public_url` — the tunnel URL, written by the
   entrypoint once cloudflared reports its hostname. Re-read lazily on each
   pairing request, because the tunnel URL only appears a few seconds *after*
   boot.
3. LAN fallback — `http://<detected-ip>:<port>` (dev mode / `TUNNEL=off`).

## Dev mode — no Docker

```bash
cd server
python -m venv .venv && .venv\Scripts\activate           # Windows
pip install -r requirements.txt
python -m sekisho                                        # serves on :8420
#   http://127.0.0.1:8420/admin   (admin password printed in the startup banner)
```

In dev there is no tunnel file, so the pairing QR falls back to
`http://<detected-ip>:8420`. Camera/PWA install need HTTPS, so use the Docker +
tunnel path for on-phone testing.

## Environment variables

| Var                     | Default            | Purpose                                                     |
|-------------------------|--------------------|-------------------------------------------------------------|
| `TUNNEL`                | `cloudflare`       | `cloudflare` (quick tunnel), `off` (LAN), or set `TUNNEL_TOKEN` for named. |
| `TUNNEL_TOKEN`          | *(unset)*          | Cloudflare named-tunnel token (stable domain).              |
| `SEKISHO_PORT`          | `8420`             | uvicorn listen port (loopback inside the container).        |
| `SEKISHO_HOST`          | `127.0.0.1` (Docker)| Bind address. Dev default `0.0.0.0`.                        |
| `SEKISHO_DB_PATH`       | `/data/sekisho.db` | SQLite file (Docker mounts `/data` as a volume).            |
| `SEKISHO_PUBLIC_URL`    | auto/tunnel        | Explicit pairing origin. Needed for `TUNNEL=off` and named tunnels. |
| `SEKISHO_ADMIN_PASSWORD`| generated & printed| `/admin` login password.                                    |
| `SEKISHO_BACKENDS_JSON` | *(unset)*          | JSON list of backends to seed the registry once (see above). |
| `SEKISHO_VLLM_URL`      | *(unset → mock)*   | Legacy single-backend base, e.g. `http://host.docker.internal:8000/v1` (seeds one vision backend). |
| `SEKISHO_VLLM_MODEL`    | *(unset)*          | Legacy model id for `SEKISHO_VLLM_URL`.                     |
| `SEKISHO_VLLM_KEY`      | *(unset)*          | Optional API key for the legacy backend.                    |
| `SEKISHO_ALLOW_MOCK`    | `1`                | `0` makes analyze/chat/body-photo return 503 and barcode misses instead of mocking. |
| `SEKISHO_CONNECT_TIMEOUT`| `3`               | Connect timeout (s) for backend calls — fail fast into failover. |
| `SEKISHO_ANALYZE_TIMEOUT`| `60`              | Total timeout (s) for analyze / body-photo.                 |
| `SEKISHO_CHAT_TIMEOUT`  | `30`               | Total timeout (s) for chat + `/llm/v1` passthrough.         |
| `SEKISHO_VLLM_TIMEOUT`  | `45`               | Fallback total timeout (s) for other backend calls.         |

Push VAPID keys are **auto-generated on first boot and persisted** in the
`settings` table under the data dir — no env var needed. `GET
/api/v1/push/vapid-public-key` (no auth) returns the public key for the app.

## API surface

Full contract in [`../API.md`](../API.md). Under `/api/v1`: `health`, `pair/claim`,
`meals/analyze`, `food/barcode/{code}`, `meals` (CRUD + totals with `burned`),
`profile` (Mifflin-St Jeor targets), `workouts`, `suggestions`, `calendar`, `chat`.
Under `/llm/v1`: OpenAI-compatible `models` + `chat/completions` passthrough —
device-key gated, model pinned to `SEKISHO_VLLM_MODEL`, `max_tokens` capped at
1024, streaming rejected (400), and **503 (never mocked)** when no vLLM is
configured.

- **`GET /food/barcode/{code}`** proxies OpenFoodFacts v2 server-side, normalizes
  to `{ name, brand, serving, per_serving, per_100g }` (computing `per_serving`
  from `per_100g` × serving grams when OFF omits it), and caches every result
  (positive and negative) in the `food_barcodes` SQLite table. Unknown codes
  return `found:false`; with no internet and mock enabled it returns a
  deterministic mock product. Auth + rate limit apply.
- **`POST /meals/analyze`** itemizes the plate: Indian meals come back as a main
  plus sides (dal + rice + roti + sabzi + curd + pickle + papad, thali, biryani +
  raita…), each `items[]` entry carrying its own macros with the top-level totals
  equal to the sum. It asks ONE clarifying question on high-calorie ambiguity
  (ghee, paneer-vs-tofu, rice portion). The mock analyzer emits the same
  multi-item shape so the app's overlay UI works without a GPU.

### v2 endpoints

- **Accounts** — `POST /auth/register` `{username, pin, profile?}` → `201
  {user}` (device's active user becomes this user); `POST /auth/login`
  `{username, pin}` → `{user, profile}`; `POST /auth/logout` → `{ok:true}`;
  `GET /auth/me` → `{user, profile}`. Data endpoints return `401
  {"error":{"code":"no_user"}}` until login. 5 bad PINs → `429 locked_out` for 60s.
- **Body photo** — `POST /profile/body-photo` (multipart `image`) → `{body_fat_pct,
  range:[lo,hi], confidence, notes, source:"vllm"|"mock"}`. Editable on the app,
  then saved via `PUT /profile`. The photo is never stored.
- **Chat (Annai)** — `POST /chat` `{messages:[{role,content}]}` → `{reply,
  action, source}`. `action` is `null` or the executed action, e.g.
  `{"action":"log_meal","meal":{...}}` / `log_workout` / `set_goal`. The fenced
  json block is stripped from `reply`.
- **Push** — `GET /push/vapid-public-key` (no auth); `POST /push/subscribe`
  `{subscription, tz_offset_min}` → `201`; `DELETE /push/subscribe` → `204`.

### v3 endpoints

- **Weight** — `POST /weight {weight_kg, date?}` → `201 {date, weight_kg}`;
  `GET /weight?from&to` → `{entries:[{date,weight_kg}], trend:[{date,avg_kg}]}`.
- **Water** — `POST /water {ml}` (negative undo, floor 0) → `201 {date, water_ml,
  water_target_ml}`.
- **Quick-log** — `POST /meals/{id}/favorite` / `DELETE /meals/{id}/favorite` →
  `{ok, id, favorite}`; `GET /meals/recent` → `{recent:[{dish, calories, protein_g,
  carbs_g, fat_g, fiber_g, sugar_g, sodium_mg, last_eaten_at, favorite, count}]}`.
- **Streaks** — `GET /streaks` → `{current, best, stamps:[{date, kind}]}`.
- **Insights** — `GET /insights?week=YYYY-MM-DD` → `{week_start, week_end,
  days_logged, target_calories, averages:{calories,protein_g,carbs_g,fat_g,
  water_ml}, adherence_pct, best_day, worst_day, weight_delta_kg,
  macro_split_pct:{protein,carbs,fat}, summary}`.
- **Export** — `GET /export.csv` → CSV attachment of meals/workouts/weights/water.
- **Profile** now carries `weight_goal_kg`, `pace_kg_per_week` and (on GET/PUT)
  `eta_date`; meals/analyze/barcode carry optional `fiber_g/sugar_g/sodium_mg`;
  `GET /meals` totals gain `fiber_g/sugar_g/sodium_mg/water_ml/water_target_ml`.

### Admin dashboard (`/admin`, tabbed, same design system)

Glass-tab navigation: **Status** (public/tunnel URL, active backends, mock mode,
uptime, IPs), **Users** (list + drill-down: profile, targets, body fat, recent
meals + day totals), **Devices** (paired, active user, last seen, revoke, pairing
QR), **Backends** (registry CRUD with presets, active vision/text switches, Test
button), **Requests** (last 200, client-side filters), **Push** (subscription
count, send test). Login with `SEKISHO_ADMIN_PASSWORD` (generated + printed if
unset). Admin JSON APIs live under `/admin/api/*` (session-cookie gated);
`POST /admin/backends/{id}/test` and `POST /admin/push/test` are the live checks.

## Tests — full smoke suite (no GPU)

`server/tests_smoke.py` drives **every** endpoint against the mock/dummy backends
with a throwaway SQLite DB and prints a PASS/FAIL table (v1 + v2 happy paths,
failover with a faked-dead backend, wrong PIN / lockout / no_user, oversized
image, bad barcode, unknown route, rate-limit 429, Katch-McArdle math, chat
meal-logging, push scheduler tick, v1→v2 migration, admin pages):

```bash
cd server
python tests_smoke.py          # -> "57/57 PASS" and exits non-zero on any failure
# (also importable under pytest as test_smoke_all_pass if pytest is installed)
```

Auth: `Authorization: Bearer hh_<32hex>` on everything except `pair/claim` and
`push/vapid-public-key`. Keys are stored SHA-256-hashed; PINs salted + PBKDF2.
Rate limit: 30 requests/min/device. Pairing tokens expire in 300s and are
single-use.
