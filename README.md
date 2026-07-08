# 腹八分 Harahachi — photo-first calorie & macro tracker

> *Hara hachi bu* — eat until you are eight-tenths full.

Harahachi is a two-part system:

| Part | Codename | What it is | Where it runs |
|------|----------|-----------|---------------|
| `server/` | **Sekisho (関所)** | FastAPI edge-AI gateway: device pairing, auth, rate limiting, vLLM vision proxy, admin dashboard | Laptop / lab server (college network) |
| `app/` | **Harahachi (腹八分)** | Installable PWA calorie tracker, styled as a journey across an old Japanese sakura map | Phone (browser → home screen) |

## The improved brief (what we're building)

**Core loop:** the user photographs their dish → the phone sends it to Sekisho →
Sekisho forwards to a vLLM vision model (or mock) → structured JSON comes back
(dish name, calories, protein/carbs/fat, confidence, clarifying question if
unsure) → a glass confirmation card appears → on confirm, the day's intake
updates and the traveler marker advances along the map road.

**Onboarding (one-time, fun):** framed as obtaining your *tegata* (Edo travel
permit) at the checkpoint — a short scroll-style flow collecting age, sex,
height, weight, activity level, and goal (cut / maintain / bulk). Computes
BMR/TDEE (Mifflin-St Jeor) and sets the daily calorie + macro targets. A hanko
seal stamp animation completes it. Editable later at the Tegata station.

**Daily journey:** calorie goal = one day's walk between two stations on the
road. Meals move the traveler forward; hitting 100% arrives at the destination;
overshoot walks past it into a vermillion "overshoot" stretch. Macro bars
(protein/carbs/fat) render as three smaller parallel paths.

**Intelligence:** analyses the user's history to suggest meals *they've eaten
before* that fit the remaining calories/macros for today ("From your past
travels: dal + roti fits your remaining 620 kcal and 32g protein").

**Other stations:** calendar heat-history (Koyomi), gym/fitness logging with
calorie burn credited back to the journey (Dōjō), a store (Ichiba) with merch
placeholders and healthy-option deep links to Swiggy/Zomato/Google Maps search.

## Deployment: ONE Docker container + Cloudflare Tunnel (works anywhere)

The server ships as a **single image** (no compose) that runs everything,
supervised by an entrypoint:

| Process inside the container | Bind | Role |
|------------------------------|------|------|
| `uvicorn` (sekisho) | `127.0.0.1:8420` | The FastAPI app — loopback only, never directly reachable |
| `nginx` | `:80` | Front door: serves the PWA (`/srv/app`, SPA fallback) + proxies `/api/ /pair /admin /llm/` to uvicorn (`client_max_body_size 16m`) |
| `cloudflared` | outbound | Tunnel exposing `:80` as a public `https://*.trycloudflare.com` URL |

| nginx route | Goes to |
|-------------|---------|
| `/` | The Harahachi phone app (COPYed to `/srv/app` at build time, SPA fallback) — an installable PWA with camera access |
| `/api/`, `/pair/` | Sekisho data + pairing endpoints |
| `/llm/v1/` | OpenAI-compatible passthrough to vLLM (device-key gated; vLLM never exposed) |
| `/admin` | Sekisho admin GUI (pairing QR, devices, vLLM status, logs) |

**Connectivity — the tunnel, works from anywhere:** `cloudflared` dials *out* to
Cloudflare (no router ports, home/college IP never exposed) and gets a public
`https://<random>.trycloudflare.com` URL, printed prominently in the logs. Open
it on the phone **anywhere in the world**, "Add to Home Screen" (real Cloudflare
cert → camera + PWA secure-context work), and pair via `/admin` — **the QR
encodes the tunnel URL**. LAN/hotspot still works with `TUNNEL=off` via
`http://<laptop-ip>/`.

## Security model

- Every device gets its own API key, issued by scanning a **pairing QR** shown
  on the Sekisho admin dashboard. Keys are hashed at rest (SHA-256).
- All app→server calls send `Authorization: Bearer <device-key>`.
- Per-device rate limiting; pairing QR codes expire after 5 minutes.
- vLLM itself is never exposed — only Sekisho talks to it; the `/llm/v1/`
  passthrough validates the device key, enforces rate limits, caps max_tokens,
  and pins the model before forwarding.
- **Outbound-only tunnel** — no inbound ports opened; **Cloudflare terminates
  TLS** with a real cert (no self-signed warning), so PWA install + camera work
  out of the box.

## Running it

```bash
# 1. Build the phone app (COPYed into the image at /srv/app).
cd app && npm install && npm run build

# 2. Build the single image. Build context is the REPO ROOT so app/dist is visible.
cd ..
docker build -f server/Dockerfile -t harahachi .

# 3. Run it. TUNNEL=cloudflare (default) prints the public https URL in the logs.
docker run -p 80:80 -v hh-data:/data harahachi
#   → open the printed https://…trycloudflare.com URL on the phone (anywhere),
#     install the PWA, then pair at that URL + /admin.

# LAN-only instead of a tunnel (phone on same WiFi/hotspot):
docker run -p 80:80 -v hh-data:/data \
  -e TUNNEL=off -e SEKISHO_PUBLIC_URL=http://<laptop-ip> harahachi

# With a host-GPU vLLM backend:
docker run -p 80:80 -v hh-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e SEKISHO_VLLM_URL=http://host.docker.internal:8000/v1 \
  -e SEKISHO_VLLM_MODEL=Qwen/Qwen2.5-VL-7B-Instruct harahachi

# Dev without Docker
cd server && pip install -r requirements.txt && python -m sekisho   # :8420
cd app && npm run dev
```

See [DESIGN.md](DESIGN.md) for the visual system and [API.md](API.md) for the
server contract.
