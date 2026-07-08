# 腹八分 Harahachi — Complete Setup Guide

From a fresh laptop to logging meals with your phone camera, step by step.
Written for Windows; Linux/Mac differences are noted inline.

---

## 0. What you need before starting

| # | Requirement | Where to get it | Check it works |
|---|------------|-----------------|----------------|
| 1 | **Node.js 18+** and npm | https://nodejs.org (LTS installer) | `node -v` prints v18+ |
| 2 | **Docker Desktop** | https://docker.com/products/docker-desktop | `docker info` shows "Server:" with no error (Docker Desktop must be OPEN and running) |
| 3 | Python 3.11+ | https://python.org — only needed for dev mode (Part 6) | `python --version` |
| 4 | An Android phone or iPhone | — | — |
| 5 | *(optional)* Android Studio | https://developer.android.com/studio — only for the APK (Part 4) | — |
| 6 | *(optional)* An AI backend | vLLM on a GPU box / LM Studio / NVIDIA API key | see Part 5 |

> **No AI backend is required.** Without one, Sekisho serves a realistic
> deterministic mock — every feature works, numbers are plausible fakes.

---

## 1. Build and run the server (one Docker container)

Open a terminal (PowerShell) in the project root `calorietracker\`.

**Step 1.1 — Build the phone app** (it gets baked into the server image):

```powershell
cd app
npm install          # first time only, ~2 min
npm run build        # produces app\dist — must end with no errors
cd ..
```

✅ Checkpoint: the folder `app\dist\` now exists and contains `index.html`.

**Step 1.2 — Build the server image.** From the repo root (important — the
build context must see `app/dist`):

```powershell
docker build -f server/Dockerfile -t harahachi .
```

✅ Checkpoint: ends with `naming to docker.io/library/harahachi`.

**Step 1.3 — Run it:**

```powershell
docker run -p 80:80 -v hh-data:/data harahachi
```

Within ~10 seconds the logs print a banner like:

```
  PUBLIC URL : https://plain-words-here.trycloudflare.com
  ADMIN      : https://plain-words-here.trycloudflare.com/admin
  ADMIN PASSWORD : k3x9...   (printed once — copy it now)
```

That URL works from **anywhere in the world** — no router setup, your IP is
never exposed, Cloudflare provides the HTTPS certificate.

✅ Checkpoint: open the PUBLIC URL on your **laptop** browser → the Harahachi
map loads. Open `/admin` → the Sekisho dashboard asks for the password.

**Things to know:**
- The URL is **random on every container restart**. For a permanent URL,
  create a (free) named tunnel at https://one.dash.cloudflare.com → Networks
  → Tunnels, copy its token, and run with `-e TUNNEL_TOKEN=<token>`.
- Set your own admin password: add `-e SEKISHO_ADMIN_PASSWORD=yourpassword`.
- LAN-only (no internet dependence, phone must join the same WiFi/hotspot):
  `docker run -p 80:80 -v hh-data:/data -e TUNNEL=off -e SEKISHO_PUBLIC_URL=http://<laptop-ip> harahachi`
  (find your IP with `ipconfig` → IPv4 Address). Note: over plain HTTP the
  phone camera won't work — LAN mode is mainly for admin/testing; use the
  tunnel or the APK for camera on phones.
- Your data lives in the `hh-data` Docker volume and survives restarts.
  Full reset: `docker volume rm hh-data`.

---

## 2. Pair your phone (2 minutes)

1. **Laptop**: open `<public-url>/admin`, enter the admin password.
2. Click the **Devices** tab → **Pair new device**. A QR code appears
   (valid **5 minutes**, one use — just generate a fresh one if it expires).
3. **Phone**: open the same public URL in Chrome (Android) / Safari (iPhone).
4. Complete onboarding — it's one-time:
   - your name →
   - sex, age, height, weight (big steppers/sliders) →
   - body silhouettes: pick "you now" and "your goal" (or skip / photo-estimate later) →
   - activity level + goal (cut/maintain/bulk) — your daily kcal target previews here →
   - stamp the red hanko.
5. At the checkpoint screen tap **Scan QR with camera** → point at the
   laptop's QR. (Fallback: "Enter code manually" — type the token shown
   under the QR; the server address travels inside the QR.)
6. Create your account: pick a username + a 4–8 digit PIN → stamp.
   You land on the map. Done.

**Install it like an app:** Chrome (Android) shows an "Add to Home screen /
Install" prompt — accept it. iPhone: Share button → "Add to Home Screen".
It now opens full-screen with its own icon and camera access.

**More people?** Each person can create their own account (login screen →
"new traveler") — data is fully separated per account. Extra phones: pair
each device once via a new QR, then log in.

---

## 3. Daily use — 60-second tour

| Station | What you do there |
|---|---|
| 茶屋 **Chaya** | Tap the shutter on your dish → labels appear AROUND the food, one per component (dal / rice / roti…) → adjust portions, answer the one question if asked → stamp **add to today's goal**. Toggle **バーコード** for packaged-food barcodes. Quick-log carousel re-logs your regulars in one tap. |
| 本陣 **Honjin** | Your day as a journey along the road. Water cups (+250 ml a tap), streak chip, suggestions from your own history. |
| 暦 **Koyomi** | Calendar heat-map, weekly insights scroll, **Weight** tab (log weigh-ins, see the trend line), **Stamp book** (a red goshuin per on-target day). |
| 道場 **Dōjō** | Log workouts — burned kcal visibly extend today's road. |
| 市場 **Ichiba** | Merch previews + healthy-food deep links (Swiggy/Zomato/Maps). |
| 手形 **Tegata** | Profile & targets, body-fat update (silhouettes or photo), goal weight + weekly pace (shows your arrival date), glass transparency, reminders toggle, CSV export, logout/switch account. |
| ⛩ **Annai** | The chat guide, anywhere: "I ate 2 idlis and sambar" logs it; "what's left today?", "what should I eat tonight?", "how does this app work?" |

Enable **reminders** in Tegata: 6 pm "X kcal to go — your usual … would fit"
and a 10 pm day-close recap.

---

## 4. Android APK (optional — the installed PWA is already app-like)

1. Install **Android Studio** (accept the SDK licenses during setup).
2. ```powershell
   cd app
   npm run apk:sync                 # rebuild web assets + sync android project
   ```
3. Open the folder `app\android` in Android Studio → wait for Gradle sync →
   **Build → Build APK(s)** (or `npm run apk:build` from the terminal once
   the SDK is installed).
4. APK appears at `app\android\app\build\outputs\apk\debug\app-debug.apk`.
   Copy it to the phone, open it, allow "install unknown apps".
5. First launch inside the APK: pairing **requires** scanning the admin QR
   (it carries the server URL — there is no "same origin" inside an APK).

---

## 5. Connect real AI (optional, switchable anytime)

Admin → **Backends** tab → **Add backend** (presets fill the URL for you):

| Kind | Base URL | Model example | Vision? |
|---|---|---|---|
| vLLM | `http://host.docker.internal:8000/v1` | `Qwen/Qwen2.5-VL-7B-Instruct` | ✅ use for photos |
| LM Studio | `http://host.docker.internal:1234/v1` | any vision-capable GGUF | depends on model |
| NVIDIA API | `https://integrate.api.nvidia.com/v1` | e.g. `meta/llama-3.2-90b-vision-instruct` + your API key | ✅ |

Then: mark it **vision-capable** if it is → set it **active** for vision
and/or text → press **Test** (should show latency + models). If a backend
dies mid-day, Sekisho fails over to the next enabled one, then to the mock —
the app never breaks.

Running vLLM on the same machine? Start the container with
`--add-host host.docker.internal:host-gateway` so the container can reach it.

---

## 6. Dev mode (no Docker — for hacking on the code)

```powershell
# Terminal 1 — server on :8420
cd server
python -m venv .venv
.venv\Scripts\activate            # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
python -m sekisho                 # admin password prints in the banner

# Terminal 2 — app with hot reload on :5173
cd app
npm run dev
```

Open `http://localhost:5173`, pair using "Enter code manually" with server
URL `http://127.0.0.1:8420` and a token from `http://127.0.0.1:8420/admin`.
Run the server test suite anytime: `python tests_smoke.py` (expects 103/103).

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `docker: error during connect` | Docker Desktop isn't running — open it, wait for "Engine running" |
| Build fails at `COPY app/dist` | You skipped step 1.1, or built from `server\` — build from the repo ROOT with `-f server/Dockerfile .` |
| Tunnel URL changed | Normal for quick tunnels — use `-e TUNNEL_TOKEN=...` (named tunnel) for a stable URL |
| Phone camera won't open | Camera needs HTTPS → use the tunnel URL or the APK, not `http://<ip>` |
| Pairing QR "expired" | They live 5 minutes — click Pair new device again |
| "Sekisho is unreachable" popup | Container/laptop off or phone offline — logs queue and sync automatically when back |
| Barcode "not found" | OpenFoodFacts doesn't know that product — the card offers manual entry |
| Wrong PIN 5× | 60-second lockout, then try again |
| Forgot admin password | Restart the container with `-e SEKISHO_ADMIN_PASSWORD=<new>` |
| Start completely fresh | `docker volume rm hh-data` (erases ALL users/meals) |
| Port 80 taken | `-p 8080:80` and use `http://<ip>:8080` (tunnel unaffected) |
