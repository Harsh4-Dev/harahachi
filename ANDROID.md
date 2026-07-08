# 📱 Building the Harahachi Android app — a beginner's guide

You'll install Android Studio, build your first APK, and put it on your
phone. Basics are explained along the way, so you'll understand *what* each
step does, not just copy commands. Time: ~45–60 min (mostly downloads).

---

## 0. The concepts (2 minutes — worth reading)

- **APK** (*Android Package*) — a single file (`.apk`) that IS an Android
  app. Installing an app = copying an APK onto the phone. We're going to
  produce `app-debug.apk` and install it by hand ("sideloading").
- **Android Studio** — Google's official editor for Android apps. We use it
  mostly as an installer for the build tools and a big "Build" button.
- **Android SDK** (*Software Development Kit*) — the compilers, libraries,
  and phone emulators. Android Studio downloads it for you.
- **Gradle** — the build system for Android projects. It reads the project's
  recipe files (`build.gradle`), downloads dependencies, and produces the
  APK. The first build is slow (it downloads a lot); later ones are fast.
- **Capacitor** — the trick Harahachi uses: the app you saw in the browser
  (HTML/JS) gets wrapped inside a thin native Android shell with a WebView
  (an embedded browser) plus native permissions (camera). So the Android
  project at `app\android` is mostly generated — you rarely edit it.
- **Debug vs release APK** — a *debug* APK is unsigned-for-store, meant for
  testing; perfect for you and friends. A *release* APK is signed with your
  own key for the Play Store — not needed now.

The pipeline you're about to run:

```
app\src (React code) → npm run build → app\dist (web files)
     → npx cap sync → app\android (native project, web files copied in)
     → Gradle → app-debug.apk → your phone
```

---

## 1. Install Android Studio

1. Download from **https://developer.android.com/studio** (big green
   button — the Windows `.exe`, ~1.1 GB).
2. Run the installer. Accept the defaults on every screen (keep
   "Android Virtual Device" checked — that's the phone emulator).
3. First launch opens a **Setup Wizard**:
   - Choose **Standard** setup.
   - It lists what it will download (Android SDK, platform tools, an
     emulator image — several GB). Click Next.
   - **Accept all license agreements** (click each item on the left, then
     the "Accept" radio button) → Finish. Wait for the downloads.

✅ Checkpoint: you reach the "Welcome to Android Studio" window.

> **What just happened:** Android Studio installed the SDK to
> `C:\Users\<you>\AppData\Local\Android\Sdk` and a JDK (Java) it bundles.
> Gradle will find both automatically when you open a project.

---

## 2. Prepare the project

Open PowerShell in `D:\linux_workspace\calorietracker`:

```powershell
cd app
npm install        # only if you haven't before
npm run apk:sync   # = npm run build + npx cap sync android
```

> **What this does:** builds the web app into `app\dist`, then Capacitor
> copies those files into the Android project (`app\android\app\src\main\assets`)
> and updates its native plugin config. **Re-run `npm run apk:sync` every
> time the web app changes**, before rebuilding the APK.

✅ Checkpoint: ends with something like `Sync finished`.

---

## 3. Open the project and build the APK

1. In Android Studio's welcome window: **Open** → navigate to
   `D:\linux_workspace\calorietracker\app\android` (the `android` folder
   itself, not `app\`) → OK.
2. If asked "Trust project?" → Trust.
3. Now **wait**. Bottom status bar shows "Gradle sync" then progress bars —
   first time takes 5–15 min (it downloads Gradle itself + all Android
   libraries). Don't click anything until the bar goes quiet.
   ✅ Checkpoint: bottom-right says something like "Gradle sync finished".
4. Menu: **Build → Build App Bundle(s) / APK(s) → Build APK(s)**.
5. When the "APK(s) generated successfully" balloon appears, click
   **locate** — it opens the folder with your APK:

```
app\android\app\build\outputs\apk\debug\app-debug.apk
```

🎉 That file is the Harahachi app.

> **Terminal alternative** (after Android Studio has installed the SDK once):
> `cd app && npm run apk:build` does the same thing headlessly.

---

## 4. Put it on your phone

**Option A — simplest (no cable):**
1. Send `app-debug.apk` to yourself (Google Drive, WhatsApp "document",
   email — anything).
2. Open it on the phone → Android warns "install unknown apps" → allow your
   browser/file manager this permission → Install.
3. If **Play Protect** pops up ("app from unknown developer") → "Install
   anyway". This is normal for any APK not from the Play Store.

**Option B — USB cable (what real developers use):**
1. On the phone: Settings → About phone → tap **Build number 7 times** →
   "You are now a developer!" Then Settings → System → **Developer options**
   → enable **USB debugging**.
2. Plug the phone into the PC, allow the "Trust this computer?" prompt.
3. In Android Studio, your phone appears in the device dropdown (top
   toolbar) → press the green **Run ▶** button. It builds, installs, and
   launches in one go. (This is also how you'd see live logs later.)

**Option C — no phone handy:** use the emulator. Device dropdown → Device
Manager → the pre-made virtual phone → ▶. Note: the emulator has a fake
camera, so photo analysis gets a test pattern — fine for poking around.

✅ Checkpoint: the sakura map appears on your phone.

---

## 5. First run of the app

1. Make sure the **server is running** (see [SETUP.md](SETUP.md) Part 1) and
   you have its public URL.
2. The app walks you through onboarding (name → body → goal → hanko stamp).
3. At the checkpoint screen: **scan the pairing QR** — from the `/admin`
   page, or (once your account is the keeper) from another phone's
   "Invite a traveler" screen. Inside the APK the QR is *required* — it
   carries the server address the app will talk to.
4. Create username + PIN → stamp → you're on the map. Since you're the
   first registered user, your account is automatically the
   **checkpoint keeper** (admin) — check Tegata for the 関 card.

---

## 6. When you change the code later

| You changed… | Do this |
|---|---|
| Web app code (`app\src`) | `npm run apk:sync`, then Build APK again (or Run ▶) |
| Server code only | Nothing — rebuild/restart the Docker container; the APK is unaffected |
| Android-native config (`app\android`) | Just Build APK again |

Friends only need a **new APK** when the web app changes; server changes
reach them instantly.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| Gradle sync fails with license errors | Android Studio → Settings → search "SDK Manager" → SDK Tools tab → check "Google Play Licenses"/accept prompts; or Tools → SDK Manager → re-accept licenses |
| "SDK location not found" | Open the project via Android Studio once (it writes `local.properties` pointing at the SDK) |
| Sync stuck / weird errors | File → Invalidate Caches → Invalidate and Restart. Also check File → Settings → Build Tools → Gradle uses the "Embedded JDK" |
| Build works, app shows white screen | You forgot `npm run apk:sync` after changing web code — the shell has stale/no web assets |
| Camera doesn't prompt | Android Settings → Apps → Harahachi → Permissions → Camera → Allow. First camera use should prompt automatically |
| "App not installed" on the phone | An older copy with a different signature exists — uninstall the old Harahachi first |
| Phone can't reach the server | The QR carries the server URL — is the Docker container running, and is the tunnel URL current? (Named tunnel = stable URL; see SETUP.md) |
| Emulator is slow | Normal on first boot; give it a minute, or prefer a real phone |

---

## What you learned

An Android app is just an APK; Android Studio + SDK + Gradle build it;
Capacitor wraps a web app in a native WebView shell with real permissions;
debug builds sideload freely; `sync → build → install` is the whole loop.
That's the same loop professional hybrid-app teams ship with.
