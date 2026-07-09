# 腹八分 Harahachi — photo-first calorie & macro tracker

> *Hara hachi bu* — eat until you are eight-tenths full.

Point your phone at your plate. A vision LLM reads the dish — **itemized, with
each component labelled around the food** (dal · rice · roti ×2 · curd), asks
one clarifying question when it matters ("ghee on the rotis?"), and one hanko
stamp logs it against your day. Your daily calorie goal is a journey across an
old Japanese sakura map: every meal walks your traveler down the road; workouts
extend it; eight-tenths full lands you at the destination station.

Self-hosted, private, and free: your data lives on **your** machine, the AI is
**your** vLLM / LM Studio / NVIDIA API endpoint (or a built-in mock so
everything works with no GPU at all), and the phone connects from anywhere in
the world through an outbound-only Cloudflare tunnel.

---

## The two halves

| Part | Codename | What it is | Runs on |
|------|----------|-----------|---------|
| `app/` | **Harahachi (腹八分)** | Mobile app — installable PWA + Capacitor Android APK. Ukiyo-e game-map navigation, glassmorphism UI, full offline queue | Your phone |
| `server/` | **Sekisho (関所)** | FastAPI edge-AI gateway: device pairing, accounts, multi-LLM backend registry with failover, push reminders, full admin dashboard | Laptop / home server / DGX — one Docker container |

## Features

**Logging**
- 📷 **Camera dish analysis** — full-screen capture; the LLM response *surrounds*
  the food: one glass chip per component pinned to its spot with a leader line,
  portion cycling (×0.5–×2), a clarifying-question chip, total bar + stamp
- 🏷 **Barcode scanner** — packaged food via OpenFoodFacts (server-proxied + cached)
- ✍️ Manual entry, one-tap **quick-log** of your regulars (favorites + frequency-ranked recents)
- 💬 **Annai (案内人) chat** — "I ate 2 idlis and sambar" logs it; "what should I
  eat tonight?" *proposes* (confirm chip — never logs without your stamp);
  answers "what's left today?" and app-help questions

**The feedback loop**
- ⚖️ Weight log with 7-day trend chart, goal weight + weekly pace →
  **calorie target computed from your pace** (Katch-McArdle when body-fat is
  known) with an arrival date
- 🔥 **Streaks & the goshuin stamp book** — a red temple stamp per on-target
  day, gold milestones at 7/30
- 📊 Weekly insights: averages, adherence %, best/worst day, macro split, and a
  summary written in Annai's voice
- 💧 Water tracking with reminders; fiber/sugar/sodium behind a "more" tap
- 🏋️ **Workout timer** — pick the type (MET-based, calories computed live), timer
  docks under the calorie ribbon Dynamic-Island style, and in the APK an
  **Android chronometer notification** keeps ticking with Pause/Stop buttons
  even when the app is closed
- ⏰ Push reminders (6 pm goal nudge with a meal *you actually eat*, 3 pm water,
  10 pm recap) — web push on PWA, in-app banners in the APK

**Body & accounts**
- 🧍 Body-fat via sumi-line **silhouette picker** (current + goal) or a photo the
  LLM estimates from (editable result, photo never stored)
- 👥 Multi-user accounts (username + PIN, per-user data isolation), logout/switch
- 🗝 **Checkpoint keeper** — in-app admin mode: pair new phones by showing a QR
  from *your* phone, see everyone's day/streaks, revoke devices, switch AI
  backends. Server-enforced, granted only via the dashboard

**The world**
- 🗾 Dense ukiyo-e map: ink mountains, pine forests, villages, temples, torii,
  province borders, deep indigo sea with coastal banding, drifting petals,
  cartouche — all inline SVG in one palette
- 🧭 No tab bar: tap a hanko seal to zoom into its region; swipe down to close
  (1:1 finger tracking, flick to dismiss), swipe the header to the next station
- 🌸 Sakura-petal loaders, user-adjustable glassmorphism transparency

## Quick start

```bash
# 1. Build the phone app (baked into the server image)
cd app && npm install && npm run build && cd ..

# 2. One container: FastAPI + nginx + Cloudflare tunnel
docker build -f server/Dockerfile -t harahachi .
docker run -d --name hh --restart unless-stopped -p 8080:80 -v hh-data:/data harahachi
docker logs -f hh    # prints your public https://…trycloudflare.com URL + admin password
```

Open the URL on your phone → onboard → scan the pairing QR from `/admin` →
done. Prefer driving it by hand? `docker run -e MANUAL=1 …`, then
`docker exec -it hh bash` and use `start-server` / `start-tunnel` / `hh-status`.

**No AI backend?** Everything works in mock mode. **Have one?** Admin →
Backends → add vLLM / LM Studio / NVIDIA API (presets included), mark it
vision-capable, set active — with automatic failover.

**Android APK:** `cd app && npm run apk:sync`, open `app/android` in Android
Studio, Build APK. See [ANDROID.md](ANDROID.md) for the beginner walkthrough.

## Documentation

| Doc | What's in it |
|-----|--------------|
| [SETUP.md](SETUP.md) | Step-by-step from zero: server, pairing, phone install, backends, troubleshooting |
| [ANDROID.md](ANDROID.md) | Android Studio + APK build for beginners (concepts explained) |
| [DEPLOY_DGX.md](DEPLOY_DGX.md) | Rootless deployment on a shared server (non-root docker user), manual mode, docker cheat-sheet, zip-based deploy without GitHub |
| [DESIGN.md](DESIGN.md) | The locked design system: palette, glass rules, map art spec, every screen |
| [API.md](API.md) | The full Sekisho API contract (v1–v3) |

## Architecture

```
 Phone (PWA / APK)                    Your machine (one container)
┌─────────────────┐   HTTPS (Cloudflare tunnel,   ┌──────────────────────────┐
│  Harahachi app   │   outbound-only, no ports    │  nginx :80  ← cloudflared │
│  camera · chat   │ ───────────────────────────► │    ├─ /        app files  │
│  offline queue   │   Bearer device key          │    ├─ /api/    sekisho    │
└─────────────────┘                               │    ├─ /admin   dashboard  │
                                                  │    └─ /llm/v1  LLM proxy  │
                                                  │  sekisho (FastAPI, :8420  │
                                                  │   loopback) → SQLite      │
                                                  │   └─ backends: vLLM / LM  │
                                                  │      Studio / NVIDIA API  │
                                                  └──────────────────────────┘
```

**Security model:** per-device API keys issued by QR (SHA-256 at rest, 5-min
single-use pairing tokens), per-device rate limiting, PIN-protected accounts
with lockout, admin actions server-enforced, the LLM backend never exposed —
and the tunnel is outbound-only, so no router ports and no exposed home IP.

## Tech stack & testing

React 18 + TypeScript (strict) + Vite + Capacitor 8 · FastAPI + SQLite ·
nginx + cloudflared in one rootless container (non-root uid, works on shared
DGX-class hosts) · hand-rolled CSS + inline SVG (no UI framework).

`server/tests_smoke.py` — **152 checks** across every endpoint including
failure paths (lockout, failover, rate limits, migrations). Run:
`cd server && python tests_smoke.py`.

## Status & roadmap

Actively developed. Deferred by choice: Google Fit / Apple Health sync
(needs native plugins), recipe builder, Play Store release signing.
