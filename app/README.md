# Harahachi 腹八分 — the phone app

The installable PWA half of Harahachi: a photo-first calorie & macro tracker
styled as a journey across an old Japanese sakura map. This app talks to the
**Sekisho** server (`../server`) over its `/api/v1` contract.

Built with Vite + React + TypeScript and hand-rolled CSS (no UI framework),
following [`../DESIGN.md`](../DESIGN.md).

---

## Quick start (development)

```bash
cd app
npm install
npm run dev        # http://localhost:5173
```

- `npm run build` — type-check (strict) + production build into `dist/`.
- `npm run preview` — serve the built `dist/` locally (http://localhost:4173).
- `npm run typecheck` — types only, no emit.
- `node --experimental-strip-types scripts/v4.check.mjs` — node checks for the
  v4 helpers (image-size maths, pasted-address normalisation, error mapping).
  The full photo pipeline runs in the dev-server console:
  `await window.__hhImagePrepCheck()`.
- Dev-only fault injection (demo mode): `localStorage.setItem('hh.demoFault',
  '413' | '429' | '503' | '401')` makes the demo's photo/chat calls fail like
  the real server, to exercise every error popup.

---

## Demo mode (no server needed)

The app ships with a full in-memory stub of the Sekisho API (realistic sample
meals, workouts, calendar, suggestions), so it demos standalone.

Two ways in:

1. **Button** — on the pairing screen, tap *"or try the demo with sample
   data →"*.
2. **Env flag** — build/run with `VITE_DEMO=1` to launch straight into demo:

   ```bash
   VITE_DEMO=1 npm run dev
   ```

Demo state persists in `localStorage` (namespace `hh.demoState`) so a session
survives reloads. Re-pairing from the Tegata (手形) station leaves demo mode.
Demo mode also gets a **fake account** (`traveler`) so the login / logout /
switch-account UI is fully exercisable, a **canned Annai chat** (canned coach
replies plus a parser that logs "I ate 2 idlis and sambar" against the demo
store), and a deterministic **body-photo estimate** — no server required.

---

## Installing on a phone (production, on Vercel)

The checkpoint runs on **Vercel** (`../API.md` → *v4: Vercel deployment, NVIDIA
NIM only*): one project serves this app (`app/dist`) **and** the API from the
same stable address, `https://<project>.vercel.app`, with a real TLS cert — so
PWA install and the camera's secure-context requirement just work.

1. Open `https://<project>.vercel.app` on the phone, then use the browser menu
   → **"Add to Home Screen"** / **"Install app"**. You now have a full-screen
   phone app with camera access.
2. Pair it (below). The installed PWA is **same-origin** with the API, so it
   only needs the pairing code — no address to type.
3. Or install the **APK** (below) and pair it to the same `https://` address.

Inside the **APK** (Capacitor WebView) the page origin is `https://localhost`,
which can't reach the API, so the APK must be paired with the deployment
address — it comes in the QR, or you type it once. The APK only talks to an
`https://` checkpoint (plain `http://` is refused except for `localhost`
during development).

> **Moving from the old Docker/tunnel setup?** Devices must re-pair to the new
> address. The first request with an old device key gets a 401 and the app
> shows **"This phone needs to be paired again"** → Pairing (your account and
> history come across with the server's data migration).

---

## Pairing with a checkpoint (Sekisho 関所)

First launch runs a short onboarding scroll (the *tegata* travel permit) — name
→ body stats → **body diagram** (body-fat now/goal) → activity + goal — then the
pairing screen, and finally **account creation** (below):

1. On any computer, open the checkpoint's admin page
   (`https://<project>.vercel.app/admin`) → **Pair new device**. A short-lived
   QR appears (valid 5 minutes) together with the deployment address.
2. In the app, tap **Scan QR with camera** and point it at the QR, **or** tap
   **Enter code manually** and paste the token.
   - The QR embeds the **checkpoint address** (the Vercel URL). That stored
     address is what every later API call uses.
   - **PWA:** manual entry needs only the code — it pairs with the site it was
     opened from. (*"Pairing with a different checkpoint?"* reveals an optional
     address field for development.)
   - **APK:** the **Checkpoint address** field is required when typing the code
     by hand. Pasted addresses are cleaned up: whitespace trimmed, `https://`
     added when missing, trailing slashes and pasted `/admin` or `/api/v1`
     suffixes dropped; `http://` is rejected (except `localhost`) with a clear
     message.
3. On success the device key **and address** are stored in `localStorage` and
   your onboarding profile is pushed to the server (`PUT /profile`).

Addresses only ever appear on the pairing and keeper screens. Re-pair any time
from the **Tegata 手形** station (demo mode: *Connect to a checkpoint*).

---

## Accounts (login on top of device pairing)

A **user account** owns your data; the device key is just the transport
credential (`../API.md` v2). The flow:

- **Create** — after pairing, onboarding ends on the account screen: a username
  and a **4–8 digit PIN** on a glass numpad, sealed by the hanko stamp →
  `POST /auth/register` (your onboarding profile rides along). Demo mode creates
  a fake account instead.
- **Login** — when the device is paired but logged out, a **login screen**
  (same tegata-scroll styling) asks for username + PIN, with a **"new traveler →
  create an account"** link. → `POST /auth/login`.
- **Tegata account card** — shows the signed-in username with **Log out**
  (`POST /auth/logout` → login screen) and **Switch account** (log in as someone
  else).
- **Global 401 handling** — any `401 {code:"no_user"}` from the server routes the
  whole app back to the login screen. A `401` for an **unknown or revoked
  device key** (`invalid_key`) drops the dead pairing + active user + all
  volatile caches and shows **"This phone needs to be paired again"** → Pairing.
- **Data isolation** — cached day-data is namespaced by user id, and **all
  volatile caches (day data, offline queue, drafts, demo state) are cleared on
  logout / account switch**, so a fresh account never sees the previous
  session's numbers. The device stays paired; only the account context changes.

---

## Body composition (targets from body fat)

The **body diagram picker** (onboarding step 2.5, editable in Tegata) offers two
rows — "your body now" and "your goal" — each a horizontal scroll of **7 glass
tiles** with sumi-line body silhouettes at ~10/15/20/25/30/35/40 % body fat
(sex-appropriate line art, not photos). The selected tile gets the `--shu` ring;
saving writes `body_fat_pct` / `body_fat_goal_pct` via `PUT /profile`. When body
fat is set, targets switch from Mifflin-St Jeor to **Katch-McArdle** (the server
recomputes; the app shows the new numbers).

**"Or estimate from a photo"** opens the full-screen camera (same capture infra),
takes a full-body shot → `POST /profile/body-photo` → an **editable glass result
screen** (estimated %, adjustable range slider, confidence note) that states the
photo was *analyzed and discarded*. Confirm saves via `PUT /profile`.

---

## Annai chat (案内人 — your road guide)

A floating glass **chat button** (torii glyph, bottom-right, above the map and
inside panels) opens a full-height glass overlay: washi message bubbles (you on
the right, Annai on the left with a tiny hanko avatar), suggestion chips
("What's left today?", "Log my dinner", "What should I eat tonight?", "How does
this app work?"), and a glass input with a `--shu` send button. Messages go to
`POST /chat`; when the reply carries a server-**executed** action (log a meal /
workout, set a goal), an inline **confirmation chip** renders with the stamp
animation and **today's data refreshes live** (the journey bar advances). A
failed send marks your bubble **tap-to-retry**.

---

## Reminders (push + in-app fallback)

Toggle reminders in Tegata. Enabling fetches `GET /push/vapid-public-key` and
subscribes through the service worker's `PushManager`
(`POST /push/subscribe` with `tz_offset_min`); the service worker handles
`push` + `notificationclick` (focus/open the app). The server nudges at local
**18:00** (what's left) and **22:00** (day recap).

Where push is unavailable — **APK WebView, or permission denied** — the app falls
back to an **in-app glass banner** sliding from the top ribbon at 18:00 / 22:00
while the app is open, with the same copy and a one-tap **"log it"** for the
suggested regular meal. The toggle explains the fallback rather than failing.

---

## Failure behaviour (never a stuck screen)

- **One shared error surface** — a glass popup with a clear message, a **Retry**,
  and a sensible **secondary path** ("enter manually" for analyze/barcode, "enter
  code manually" for pairing, "pick from diagram" for the body photo).
- **Client-side timeouts on every call** (`AbortController`): analyze /
  body-photo 65 s, everything else 12 s. **No spinner outlives its timeout.**
- **Server down / offline** → "Sekisho is unreachable — check your connection
  and try again." Meal/workout logs still **enqueue offline** and sync on
  reconnect, and the toast says so.
- **AI photo / chat errors (v4, NVIDIA NIM behind Vercel)** — mapped in
  `src/api/errors.ts`:
  - `413` → "That photo is too large — try again" (Retry re-sends a smaller
    768 px / 100 KB version).
  - `429` daily AI cap → "You've reached today's AI limit — log manually or try
    tomorrow" with **Log manually** (no pointless retry). A plain per-minute
    `429` says to wait a moment and offers Retry.
  - `503` provider unavailable → friendly "resting right now" copy with Retry +
    manual entry. No screen mentions the mock or back-end internals.
- The error popup sits above the full-screen camera views, so capture failures
  are always visible.
- **Chat failure** → tap-to-retry on the bubble; **barcode / analyze failure** →
  retry or manual entry; **pairing failure** → retry or re-scan.
- **Global error boundary** — a render crash shows a glass "something broke —
  reload" card instead of a white screen.

---

## How it works (map = navigation)

There is no tab bar. The full-viewport SVG sakura map *is* the navigation:
tapping a hanko seal pans/zooms the map and slides that station's glass panel
up over the lower ~70%.

| Seal | Station | What it does |
|------|---------|--------------|
| 本 | **Honjin** | Home: journey bar, macro paths, today's meals, suggestions |
| 茶 | **Chaya** | Full-screen camera: Dish (analyze → glass overlay cards → log) or Barcode (scan → product card → log); gallery + manual fallback + history |
| 暦 | **Koyomi** | Month heat-map of adherence; tap a day to see its meals |
| 道 | **Dōjō** | Log workouts; burned kcal extends today's road |
| 市 | **Ichiba** | Merch placeholders + healthy delivery deep links |
| 手 | **Tegata** | Account (login/logout/switch), travel-permit profile + targets, body-composition diagram, reminders toggle, glass slider, pairing |

### Notable behaviours

- **Clean first-open** — every input starts pristine: onboarding name is empty,
  age/height/weight/activity/goal show neutral placeholders (a value only commits
  on touch, Continue is gated per step), and manual meal, workout, chat and
  barcode-manual entry all start empty. Sample data appears **only** in demo mode.
- **Offline resilience** — meal/workout logs are queued in `localStorage` when
  the server is unreachable and flushed automatically on reconnect, with an
  "offline — will sync" glass toast.
- **Glass transparency** — the Tegata slider writes `--glass-alpha` (0.15–0.9)
  to `:root` and persists it; applied before first paint on every launch.
- **Reduced motion** — petal drift, map pans, the analyze shimmer, and the
  barcode scan-line sweep all respect `prefers-reduced-motion`.

---

## Camera & scanner (Chaya's capture mode)

Tapping **Open camera** in Chaya takes over the full screen with a live
rear-camera feed (`getUserMedia`, `facingMode: environment`). Everything over
the feed is glass — the food is the background (DESIGN.md §6.5).

- **Glass top ribbon** (茶屋 Chaya · Capture, close ✕) + **glass bottom bar**
  (shutter ring, gallery button, 料理 **Dish** / バーコード **Barcode** toggle).
- **Photo downscaling (every upload)** — dish photos, body photos and gallery
  picks go through `src/lib/imagePrep.ts` first: decoded with
  `createImageBitmap(…, {imageOrientation: 'from-image'})` (EXIF-upright,
  `<img>` fallback), long edge ≤ 1024 px, JPEG at quality 0.82 → 0.72 → 0.62 →
  0.5, then long edge 896 → 768 → 640, until the upload is **≤ 170 KB** (NVIDIA's
  hosted API rejects large inline images; the server re-encodes as a safety
  net). The re-encode also strips EXIF/GPS. Only the upload shrinks: the
  frozen preview keeps the full-resolution frame, and because the scale is
  uniform the model's normalized 0..1 item points pin to the same spots.
- **Dish mode** — shutter freezes the frame → "reading the dish…" shimmer while
  it POSTs to `/meals/analyze` → the response renders as **floating glass
  overlay cards** over the photo: a title card (editable dish + confidence), one
  deletable / portion-tappable chip per `items[]` component (×0.5→×1→×1.5→×2),
  a total card (kcal + macro chips), a one-tap clarifying-question chip, and the
  vermillion hanko **"add to today's goal"** stamp (stamp animation → `POST
  /meals` → back to Chaya, journey advances).
- **Barcode mode** — same feed with a glass reticle (corner ticks + scan-line
  sweep). Detection prefers the native **`BarcodeDetector`** API and falls back
  to **`@zxing/browser`** (lazy-loaded only when needed). On detect it calls
  `GET /food/barcode/{code}`, then floats a product card (name, brand,
  per-serving kcal/macros, ×0.5/×1/×1.5/×2 serving stepper) with the same hanko
  stamp. `found:false` → a card offering manual entry.
- **Fallbacks** — the file input is both the gallery button and the
  permission-denied / no-camera path (a graceful glass card → "Choose from
  gallery"). In a desktop browser without camera permission you'll see exactly
  this card; the whole overlay flow still works through the gallery path.

The camera needs a **secure context** (HTTPS or `localhost`). On the Vercel
`https://` address (and inside the APK) that's automatic.

---

## Building the APK (Android, via Capacitor)

The app is also packaged as a native Android APK using **Capacitor**. The web
build in `dist/` is copied into a native `android/` Gradle project.

- **appId** `com.harahachi.app`, **appName** `Harahachi`, **webDir** `dist`
  (see `capacitor.config.ts`). `server.androidScheme: https` gives the WebView a
  secure context so `getUserMedia` works.
- The `android/` project is committed and ready. `AndroidManifest.xml` declares
  `INTERNET` + `CAMERA` (and optional camera hardware features). Camera
  permission bridging is handled by Capacitor's built-in `WebChromeClient`
  (`onPermissionRequest` grants `VIDEO_CAPTURE` after the Android runtime
  prompt) — no custom `MainActivity` needed for the camera.
- Because the WebView origin is `https://localhost`, the app **cannot** use
  same-origin API calls; it always uses the `https://<project>.vercel.app`
  address stored at pairing (the server's CORS allows `https://localhost` and
  `capacitor://localhost`).

### Sync the web build into the native project

```bash
cd app
npm run apk:sync      # == npm run build && npx cap sync android
```

### Build the APK

> This repo was delivered **without** running the Gradle build — no Android SDK
> / JDK was present on the build machine, and the multi-GB SDK was intentionally
> not downloaded. Do the build on a machine that has the SDK, either way below.

**A — Android Studio (easiest):**

1. Install Android Studio (bundles the SDK + a JDK).
2. `npm run apk:sync` in `app/`.
3. Open the `app/android/` folder in Android Studio, let Gradle sync, then
   **Run** (or **Build → Build APK(s)**).

**B — Command line (SDK + JDK 17 already installed):**

```bash
# One-time: point the build at your SDK (adjust the path).
export ANDROID_HOME="$HOME/Android/Sdk"          # Windows: setx ANDROID_SDK_ROOT "%LOCALAPPDATA%\Android\Sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
yes | sdkmanager --licenses                      # accept SDK licenses once

cd app
npm run apk:sync                                 # build web + cap sync
npm run apk:build                                # == cd android && gradlew assembleDebug
#   (on Windows PowerShell: cd android; .\gradlew.bat assembleDebug)
```

The debug APK lands at:

```
app/android/app/build/outputs/apk/debug/app-debug.apk
```

Install it with `adb install -r app-debug.apk`, or copy it to the phone. On
first launch, pair against the checkpoint's Vercel address (the QR carries it,
or type `https://<project>.vercel.app` with the code).

---

## Project layout

```
app/
  index.html
  vite.config.ts          # PWA manifest + injectManifest custom service worker
  capacitor.config.ts     # Android shell config (appId, webDir, https scheme)
  android/                # generated Capacitor Android project (Gradle)
  public/                 # favicon.svg, pwa-192/512.png, apple-touch-icon.png
  src/
    main.tsx              # bootstrap: glass alpha, SW register, initial phase
    App.tsx               # shell: map + ribbon + panels + toasts + error popup + routing
    sw.ts                 # custom service worker: precache + push/notificationclick
    api/                  # one typed client (http.ts / demo.ts), storage, types
    lib/                  # targets (Mifflin-St Jeor / Katch-McArdle), format, foodParser,
                          #   imagePrep (upload downscaling), serverUrl (address cleanup)
    map/                  # SakuraMap.tsx (the centerpiece SVG) + stations layout
    state/                # AppContext (client, accounts, queue, toasts, error) + useDay
    components/           # JourneyBar, MacroPath, ErrorBoundary
    capture/              # full-screen camera: Capture, DishOverlay, BarcodeCard,
                          #   useCamera hook, barcode (native + zxing) detector
    body/                 # BodySilhouette, BodyPicker, BodyPhotoCapture (body-fat)
    chat/                 # Annai (floating chat button + full-height overlay)
    push/                 # push.ts (subscribe helpers) + ReminderBanner (in-app)
    stations/             # Honjin, Chaya, Koyomi, Dojo, Ichiba, Tegata
    onboarding/           # Onboarding (tegata scroll) + Pairing + RePair + Account (login/create)
    styles/               # global, map, components, capture, onboarding, account CSS
```

All server access goes through the single typed client in `src/api`
(`HarahachiClient`); `http.ts` is the real client (with per-call `AbortController`
timeouts and global `no_user` / revoked-device handling; `errors.ts` maps
failures to friendly copy), `demo.ts` is the stub — both implement
the identical interface (accounts, body-photo, chat and push included).
