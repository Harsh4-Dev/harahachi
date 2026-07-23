<div align="center">

# 腹八分

### **H A R A H A C H I**

*Eat until you are eight-tenths full.*

**A photo-first calorie & macro tracker, styled as a journey across an old Japanese map.**
Snap your dish → a vision LLM itemizes it around the food → one hanko stamp logs it.
Self-hosted. Private. Your AI, your data, your server.

<br>

![React](https://img.shields.io/badge/React_18-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Capacitor](https://img.shields.io/badge/Android_APK-3DDC84?style=for-the-badge&logo=android&logoColor=white)

![Tests](https://img.shields.io/badge/server_tests-152%2F152-C73E3A?style=flat-square)
![Self-hosted](https://img.shields.io/badge/self--hosted-100%25-7BA05B?style=flat-square)
![LLM](https://img.shields.io/badge/LLM-vLLM_·_LM_Studio_·_NVIDIA_NIM-2D4A63?style=flat-square)

</div>

---

## 🗾 What it is

Your daily calorie goal is a **day's walk along the Tōkaidō road** on a dense
ukiyo-e map — ink mountains, pine forests, villages, temples, a deep indigo
sea. Every meal moves your traveler forward. Workouts extend the road.
Landing at the destination station means you ate *hara hachi bu* — just enough.

There is no tab bar. You **tap a hanko seal to zoom into a region**, swipe
down to close it (true 1:1 finger tracking, flick to dismiss), and swipe the
header to travel to the next station. Every panel is glassmorphism with a
user-tunable frost.

| | Station | What lives there |
|---|---|---|
| 本 | **Honjin** | Today's journey bar, macro paths, water cups, streak chip, suggestions from meals you actually eat |
| 茶 | **Chaya** | 📷 Camera dish analysis · 🏷 barcode scanner · one-tap quick-log · manual entry |
| 暦 | **Koyomi** | Calendar heat-map · weight trend chart · weekly insights · the goshuin stamp book |
| 道 | **Dōjō** | Workout timer (MET-calculated calories, chronometer notification with Pause/Stop) |
| 市 | **Ichiba** | Merch + healthy delivery deep links |
| 手 | **Tegata** | Profile & targets · body-fat silhouettes/photo estimate · goal pace with arrival date · reminders · CSV export |
| ⛩ | **Annai** | The chat guide: *"I ate 2 idlis and sambar"* logs it; *"what should I eat tonight?"* proposes — you stamp to confirm |

<details>
<summary><b>✨ Full feature list</b></summary>

- **Camera analysis that surrounds the food** — one glass chip per component
  (dal · rice · roti ×2 · curd) pinned to its spot with a leader line, portion
  cycling ×0.5–×2, one clarifying question when it matters ("ghee on the rotis?")
- **Barcode scanning** via OpenFoodFacts, server-proxied and cached
- **Accounts** — username + PIN, per-user isolation, logout/switch
- **Checkpoint keeper** — admin from your phone: pair friends by showing a QR,
  see everyone's day and streaks, revoke devices, switch AI backends live
- **The feedback loop** — weight trend, goal pace → calorie target computed
  from it (Katch-McArdle with body-fat), arrival date, streaks with red temple
  stamps and gold milestones, weekly insights in the guide's voice, water with
  reminders, fiber/sugar/sodium detail
- **Reminders** — 6 pm goal nudge suggesting *your* regular meals, 3 pm water,
  10 pm recap (web push on PWA; in-app banners in the APK)
- **Multi-LLM backend registry** — vLLM, LM Studio, NVIDIA NIM side by side
  with one-click active switching, health tests, and automatic failover; a
  deterministic mock keeps everything working with no GPU at all
- **Offline-first** — logs queue locally and sync when the server returns;
  every failure is a popup with retry, never a stuck screen
- **Sakura-petal loaders**, staggered glass animations, reduced-motion support
</details>

## 🚀 Quick start

```bash
# 1 · Build the phone app (it gets baked into the server image)
cd app && npm install && npm run build && cd ..

# 2 · One container: FastAPI + nginx + Cloudflare tunnel
docker build -f server/Dockerfile -t harahachi .
docker run -d --name hh --restart unless-stopped -p 8080:80 -v hh-data:/data harahachi

# 3 · Grab your worldwide URL + admin password
docker logs -f hh
```

Open the printed `https://….trycloudflare.com` URL on your phone → onboard →
scan the pairing QR from `/admin` → **Add to Home Screen**. Done.

**Manual mode** (drive it yourself): `docker run -e MANUAL=1 …`, then
`docker exec -it hh bash` → `start-server` · `start-tunnel` · `hh-status` · `stop-all`

**Android APK**: `cd app && npm run apk:sync` → open `app/android` in Android
Studio → Build APK. Beginner walkthrough in [ANDROID.md](ANDROID.md).

## 🏗 Architecture

```
 Phone (PWA / APK)                       Your machine — ONE container
┌──────────────────┐   HTTPS via Cloudflare   ┌───────────────────────────────┐
│  Harahachi app   │   tunnel (outbound-only, │  nginx :80   ◄── cloudflared  │
│  camera · chat   │   no open ports, no      │   ├─ /        the app         │
│  offline queue   │ ───────────────────────► │   ├─ /api/    Sekisho FastAPI │
│                  │   Bearer device key      │   ├─ /admin   dashboard GUI   │
└──────────────────┘                          │   └─ /llm/v1  guarded proxy   │
                                              │  SQLite · backends: vLLM /    │
                                              │  LM Studio / NVIDIA NIM       │
                                              └───────────────────────────────┘
```

🔒 **Security**: per-device keys from single-use QR tokens (hashed at rest),
per-device rate limits, PIN accounts with lockout, server-enforced admin,
LLM backends never exposed, tunnel outbound-only — no router ports, no exposed IP.
Runs **rootless** (non-root uid) for shared/DGX hosts.

## 📚 Documentation

| | |
|---|---|
| [SETUP.md](SETUP.md) | Zero → phone, step by step with checkpoints |
| [ANDROID.md](ANDROID.md) | APK building for beginners (concepts explained) |
| [DEPLOY_DGX.md](DEPLOY_DGX.md) | Shared-server/rootless deploy, manual mode, docker cheat-sheet, zip deploy |
| [DESIGN.md](DESIGN.md) | The locked design system — palette, glass, map art |
| [API.md](API.md) | Full Sekisho API contract (v1–v3) |

## 🧪 Testing

`server/tests_smoke.py` — **152 checks**, success *and* failure paths
(PIN lockout, backend failover, rate limits, migrations, streak boundaries):

```bash
cd server && python tests_smoke.py
```

<div align="center">
<br>

*Built with the belief that tracking food should feel like a journey, not a chore.*

腹八分目 · **eight-tenths is enough**

</div>
