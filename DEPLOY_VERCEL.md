# Deploying Sekisho on Vercel (free Hobby plan)

Vercel hosts **only the Sekisho server**: the API the Harahachi phone app
talks to, and the private **admin site** at `https://<project>.vercel.app/admin`.
This is a **server-only** deployment. No web app is hosted here. Harahachi
ships inside the Android APK, and the phone points it at your Vercel URL.

The admin site is the single owner console for **both phone apps**:

* **Harahachi:** users, devices and pairing QR, NVIDIA NIM models and API key,
  request log, push reminders.
* **Bus Board** (hosted separately, e.g. on Render): health, traffic,
  live streams, journeys, ping outcomes, contributors (block / unblock). Sekisho
  calls Bus Board's ops API server-to-server, so its token never reaches your
  browser.

You won't need a terminal for most of this. The only commands generate
secrets, and any computer with Python 3 can run them.

> **Postgres status:** the full automated test suite covers the SQLite path.
> The Postgres (Neon) path was reviewed line by line, but its first real
> verification is your first deploy on Neon. Work through
> [Post-deploy check](#7-post-deploy-check) before you invite anyone.

---

## How it fits together

```
Harahachi APK ──https──▶ https://<project>.vercel.app
                            ├─ /api/*   ─┐
                            ├─ /admin*  ─┤  one Python function: api/index.py
                            ├─ /pair/*  ─┤  (FastAPI app from server/sekisho)
                            └─ /llm/*   ─┘
                                            ├─▶ Neon Postgres  (DATABASE_URL)
                                            ├─▶ NVIDIA NIM     (key from the admin or NVIDIA_API_KEY)
                                            └─▶ Bus Board ops API (BUSBOARD_URL + BUSBOARD_OPS_TOKEN)
you (browser) ──https──▶ /admin   password + proof-of-work captcha
cron-job.org or GitHub Actions ── every 15 min ──▶ GET /api/cron/tick  (push reminders)
```

`vercel.json` at the repo root already contains the settings: the install
and build steps are no-ops (nothing to build), every path is rewritten to the
one function, and it sets `maxDuration` and a once-daily safety-net cron. You
don't type any of this into the dashboard.

---

## 1. What you need

| Thing | Where | Cost |
|---|---|---|
| This repository on **GitHub** (public or private) | github.com | free |
| A **Vercel** account, signed up with GitHub | vercel.com/signup | Hobby plan, free |
| An **NVIDIA API key** | [build.nvidia.com](https://build.nvidia.com): sign in, open any model page (for example *llama-3.3-70b-instruct*), click **Get API Key**. The key starts with `nvapi-`. | free credits |
| A computer with **Python 3** | only to generate secrets and VAPID keys | free |
| *(optional)* your **Bus Board** server URL and its ops token | wherever Bus Board runs | — |
| *(optional)* a **cron-job.org** account | [cron-job.org](https://cron-job.org) | free |

Keep the NVIDIA key private. You can paste it into the admin, where it is
stored **encrypted**, or keep it in Vercel's environment variables. Either
way, Sekisho never returns it from any endpoint, never logs it, and the admin
only shows a masked hint like `nvapi-…a1b2`.

---

## 2. Create the Vercel project

1. Go to **vercel.com → Add New… → Project**.
2. Under *Import Git Repository*, pick this repo. If it isn't listed, click
   **Adjust GitHub App Permissions** and grant access to it.
3. On the *Configure Project* screen:
   * **Framework Preset:** `Other`.
   * **Root Directory:** leave it as the repo root (`./`).
   * **Build and Output Settings:** leave everything alone. `vercel.json`
     sets them, and they are no-ops because there is no web app to build.
   * **Environment Variables:** skip for now. You add them in step 4.
4. Click **Deploy**. The first deploy succeeds. Until the database and
   secrets exist, the API answers `503 server_misconfigured` and `/admin`
   says *Admin login is disabled*. That is expected at this stage.

Write down your production URL, for example `https://harahachi.vercel.app`.
This guide calls it `https://<project>.vercel.app`.

> Python version: Vercel uses **Python 3.12** by default. The function's
> dependencies come from `requirements.txt` at the repo root, which is pinned
> to the same versions as `server/requirements.txt`.

---

## 3. Add Neon Postgres (Storage tab)

1. Open your project → **Storage** tab → **Create Database** (or *Browse
   Marketplace*) → choose **Neon** (Serverless Postgres) → **Continue**.
2. Pick **Create New Neon Account** if asked and accept the terms. Choose a
   region near your Vercel function region. The default is `iad1` (US East),
   so pick a US East region. Choose the **Free** plan and name the database.
3. On the database page click **Connect Project**, choose this project, tick
   **Production** (and Preview/Development if you want them), and click **Connect**.
4. Check that the variables arrived under **Settings → Environment Variables**.
   You should see `DATABASE_URL` (the **pooled** connection string, with
   `-pooler` in the host name) and `DATABASE_URL_UNPOOLED` (direct).

Sekisho reads **`DATABASE_URL`**, falling back to `POSTGRES_URL`. That is the
pooled URL, which is what a serverless function should use. You don't create
any tables. The function builds the schema on its first cold start, with
idempotent migrations serialized by a Postgres advisory lock.

---

## 4. Environment variables

Add these under **Project → Settings → Environment Variables**, scoped to
**Production**. Add them to Preview as well if you use preview deployments.
**Changes only take effect on a new deployment**, so redeploy after editing
them (step 5).

### Generate the secrets first

Run these on any computer with Python 3. Every run prints a fresh random value.
**Use a different value for each variable.**

```bash
# SEKISHO_ADMIN_PASSWORD (or use your password manager, 12+ characters)
python -c "import secrets; print(secrets.token_urlsafe(18))"

# SEKISHO_SESSION_SECRET, SEKISHO_SECRETS_KEY and CRON_SECRET (one run for each)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# BUSBOARD_OPS_TOKEN (only if you connect Bus Board; 48+ characters)
python -c "import secrets; print(secrets.token_urlsafe(48))"

# VAPID keys for push reminders (needs: pip install cryptography)
python server/tools/gen_vapid.py
```

`server/tools/gen_vapid.py` prints two lines, `VAPID_PUBLIC_KEY=...` and
`VAPID_PRIVATE_KEY=...`. The private key is a PEM squeezed onto **one line**,
with each newline written as a literal `\n`. Paste it exactly as printed.
Sekisho turns the `\n` back into newlines when it loads the key.

### Required

| Name | What it does | How to get it |
|---|---|---|
| `DATABASE_URL` | Postgres connection (pooled). Without it, every API call answers `503 server_misconfigured`. | The Neon integration injects it (step 3). Don't type it. |
| `SEKISHO_ADMIN_PASSWORD` | Password for `/admin`. **At least 12 characters on Vercel**, or admin login stays disabled. Never auto-generated on Vercel. | Password manager, or the `token_urlsafe(18)` command. |
| `SEKISHO_SESSION_SECRET` | Signs admin session cookies and, through a separately derived key, the login captcha. Every instance shares it. **At least 32 characters**, or admin login stays disabled. | `token_urlsafe(32)` |
| `SEKISHO_SECRETS_KEY` | Master key for secrets you store from the admin (today: the NVIDIA API key). A Fernet key is derived from it with HKDF, and the key is stored encrypted in Postgres. **At least 32 characters**. If it is unset, the admin's key field is disabled with a clear message, and Sekisho never stores a key in plaintext. | `token_urlsafe(32)`, not the same value as the session secret |
| `CRON_SECRET` | Protects `GET /api/cron/tick` (`Authorization: Bearer <CRON_SECRET>`, constant-time compare). **At least 32 characters on Vercel.** If unset, the endpoint always answers 401. Vercel's daily cron sends it automatically. | `token_urlsafe(32)` |

### The NVIDIA API key: from the admin, or from the environment

Photo analysis, body-fat estimates, Annai chat and insights all need an NVIDIA
key. Pick **one** way to provide it:

* **From the admin (recommended):** set `SEKISHO_SECRETS_KEY`, deploy, then go
  to `/admin` → **Backends** → *NVIDIA API key*, paste the key and click
  **Save key**. The field is write-only: after saving you only see the last
  4 characters. **Test** sends a 1-token ping, and **Clear** removes the key.
  To replace the key, paste a new one. No redeploy needed.
* **From the environment:** add `NVIDIA_API_KEY` in Vercel and redeploy.
  **The environment always wins.** While it is set, the admin shows
  *Set in Vercel environment* and disables the field.

Without a key, the AI features return a clean 503 and the app shows its retry
popup.

### Bus Board (optional, for the Bus Board tabs)

| Name | What it does | How to get it |
|---|---|---|
| `BUSBOARD_URL` | Base URL of the Bus Board server, e.g. `https://<bus-board>.onrender.com` (no trailing slash). **Must be `https://` on Vercel.** | Your Bus Board deployment |
| `BUSBOARD_OPS_TOKEN` | Bearer token for Bus Board's ops API (`/v1/ops/*`). **At least 48 characters.** It must be the **same value** as the ops token configured on the Bus Board server. It stays on the server: Sekisho calls Bus Board itself, and the browser never sees the token. | `token_urlsafe(48)`, set on both servers |
| `BUSBOARD_TIMEOUT` | Seconds per Bus Board call. Default and maximum **8**. | Leave unset |

If either of the first two variables is missing (or the token is too short),
the **Bus Board** tab says *Bus Board not connected* and names what's missing.
Stats are cached for 30 s per instance, and **Refresh** fetches them again.

### Recommended (push reminders)

| Name | What it does | How to get it |
|---|---|---|
| `VAPID_PUBLIC_KEY` | Web-push public key (base64url of the raw P-256 point). | `python server/tools/gen_vapid.py` |
| `VAPID_PRIVATE_KEY` | Web-push private key (one-line PEM with literal `\n`). On Vercel, push stays off until both VAPID keys are set. | `python server/tools/gen_vapid.py` |
| `VAPID_SUBJECT` | Contact URI sent to the push services. Default `mailto:admin@harahachi.local`. Set a real one, because some push services reject fake addresses. | e.g. `mailto:you@example.com` |

### Optional (defaults are fine)

| Name | Default | What it does |
|---|---|---|
| `NIM_MODEL_ANALYZE` | `meta/llama-3.2-90b-vision-instruct` | Model for food-photo analysis. You can also change it per slot in the admin. |
| `NIM_MODEL_BODY` | `meta/llama-3.2-90b-vision-instruct` | Model for the body-fat photo estimate. |
| `NIM_MODEL_CHAT` | `meta/llama-3.3-70b-instruct` | Model for Annai chat. |
| `NIM_MODEL_INSIGHTS` | same as chat | Model for the weekly insight summary. |
| `NIM_FALLBACK_ANALYZE` / `_BODY` / `_CHAT` / `_INSIGHTS` | 11B vision for analyze/body, 90B vision for chat, chat's fallback for insights | Model tried once if the main one fails. `none` disables it. |
| `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NIM endpoint. The host must be on the HTTPS allowlist. |
| `NIM_MAX_IMAGE_B64` | `180000` | Largest inline base64 image sent to NIM. Larger photos get re-encoded to fit. |
| `SEKISHO_DAILY_LLM_CAP` | `200` | Per-user daily cap on AI calls, to protect your NIM credits. `0` disables it. |
| `SEKISHO_RATE_LIMIT` | `30` | Requests per minute per device, counted in the database. |
| `SEKISHO_CAPTCHA_MAXNUMBER` | `150000` | Difficulty of the admin-login proof-of-work. The browser does on average half this many SHA-256 hashes, about 1–3 s on a laptop. |
| `SEKISHO_PUBLIC_URL` | auto | Origin encoded in the pairing QR. Auto means `https://` + `VERCEL_PROJECT_PRODUCTION_URL`. Set this if you add a custom domain. |
| `SEKISHO_OPS_TOKEN` | *(unset)* | Enables Sekisho's own server-to-server ops API at `/api/ops/v1/*` (48+ characters). Leave it unset unless you run your own ops client. While unset, every `/api/ops/*` path answers 404. |
| `SEKISHO_TZ` | `Asia/Kolkata` | IANA timezone that defines "a day" for usage statistics. |
| `SEKISHO_CORS_ORIGINS` | *(empty)* | Extra allowed origins, comma-separated. `capacitor://localhost` and `https://localhost` (the APK) are always allowed. |
| `SEKISHO_LLM_BUDGET` | `55` | Total seconds for all NIM attempts in one request. |
| `SEKISHO_ANALYZE_TIMEOUT` / `SEKISHO_CHAT_TIMEOUT` / `SEKISHO_CONNECT_TIMEOUT` | `45` / `30` / `3` | Per-call NIM timeouts in seconds. |
| `SEKISHO_MAX_BODY_BYTES` | `4500000` on Vercel | Requests above this get a clean 413. Keep it at or below Vercel's 4.5 MB. |
| `SEKISHO_PG_POOL_MAX` | `4` | Postgres connections per function instance. |
| `SEKISHO_BACKEND_ALLOWLIST` | *(empty)* | Extra HTTPS hosts allowed as LLM base URLs. `integrate.api.nvidia.com` is always allowed. |
| `SEKISHO_ALLOW_MOCK` | off on Vercel | **Leave it unset in production.** When `1`, the app gets fake nutrition numbers if NIM fails. |

**Vercel sets these automatically (don't add them):** `VERCEL`, `VERCEL_URL`,
`VERCEL_PROJECT_PRODUCTION_URL`, `VERCEL_BRANCH_URL`.

---

## 5. Deploy

After adding the variables, open the **Deployments** tab → latest deployment
→ **⋯ → Redeploy**. Pushing a commit to the production branch also deploys.

---

## 6. First-login checks

1. **Health:** open `https://<project>.vercel.app/api/v1/health`. You should
   get `{"status": "ok"}`. If you get `server_misconfigured` instead, the
   message names the missing piece, usually `DATABASE_URL`.
2. **Admin:** open `https://<project>.vercel.app/admin`. The login page first
   runs a small **proof-of-work captcha** in your browser (self-hosted, with
   no third-party service and no cookies). The text under the password field
   shows progress, then *Anti-bot check passed*. Enter `SEKISHO_ADMIN_PASSWORD`
   and sign in.
   * The captcha needs JavaScript. With JavaScript off, the sign-in button
     stays disabled and the page explains why.
   * Each puzzle works once and expires after 5 minutes. If you leave the
     page open, it solves a new one automatically.
   * A failed captcha counts as a failed login. After 5 failures from one IP,
     or 50 overall, within 15 minutes, login locks for up to 15 minutes.
   * If the page says *Admin login is disabled: …*, the message names the
     variable that is missing or too short.
3. **Backends tab → NVIDIA API key:** paste your key and click **Save key**
   (or confirm it reads *Set in Vercel environment*). Click **Test**.
   `ok · <model>` means the key works. In *NVIDIA NIM model slots*, check the
   four slots **analyze / body / chat / insights** and click **Test** on each.
4. **Push tab:** the scheduler line should read `VAPID ok`.
5. **Bus Board tab** (if configured): it should show *Connected*, Bus Board's
   version and uptime, and the request table.

---

## 7. Post-deploy check

Run through this once on every fresh deployment, before you share the URL.

1. `GET https://<project>.vercel.app/api/v1/health` returns `"status": "ok"`.
2. Log in to `/admin` (password + captcha).
3. **Pair a phone:** `/admin` → **Devices** → *Pair a new device*, click
   **New pairing QR**, and scan it with the Harahachi APK (step 9). The QR
   encodes `https://<project>.vercel.app` (your production URL) plus a
   single-use code.
4. **Register a test user** in the app (any username plus a PIN).
5. **Log one meal**. A manual entry is enough, and a photo also exercises NIM.
6. In `/admin` → **Users**, confirm the test user appears with a **meal
   count of 1**.

If all six steps pass, the Postgres path works end to end on Neon.

---

## 8. The 15-minute cron (push reminders)

Reminders go out at **15:00** (water), **18:00** (calories left) and
**22:00** (day close), in each phone's local time. Nothing runs in the
background on Vercel, so something has to call `GET /api/cron/tick`
regularly. Every call is idempotent, because a reminder is claimed in the
database before it is sent.

`vercel.json` includes a **once-daily** Vercel Cron (`0 13 * * *`) as a
safety net. That is the most the Hobby plan allows. To get all three
reminders you need an external pinger every 10–15 minutes. Pick **one**:

### Option A: cron-job.org (recommended, most punctual)

1. Sign up at [cron-job.org](https://cron-job.org) → **Create cronjob**.
2. **URL:** `https://<project>.vercel.app/api/cron/tick`, schedule every **15 minutes**.
3. **Advanced** tab: method `GET`, header `Authorization` = `Bearer <your CRON_SECRET>`, timeout 30 s or more.
4. Save, then click **Test run**. You want **HTTP 200** with `{"ok":true,…}`. A **401** means the header is wrong.

### Option B: GitHub Actions (already in the repo)

`.github/workflows/cron-tick.yml` calls the endpoint every 15 minutes and
fails loudly on any non-2xx response. To configure it:

1. GitHub repo → **Settings → Secrets and variables → Actions**.
2. **Secrets** → new secret `CRON_SECRET`, using the same value as in Vercel.
3. **Variables** → new variable `SEKISHO_URL` = `https://<project>.vercel.app` (no trailing slash).
4. **Actions** → *Sekisho cron tick* → **Run workflow** to test.

> Neon's free compute suspends after 5 idle minutes, and every tick wakes it.
> A 15-minute tick stays well inside the free 100 CU-hours per month. If you
> get close, tick every 30 minutes instead.

---

## 9. Pair the phones

* **Harahachi (APK):** on the *Pair* screen, scan the QR from `/admin` →
  **Devices**, or type `https://<project>.vercel.app`. The code is single-use
  and expires after **5 minutes**. A phone that was paired to an older address
  shows **"This phone needs to be paired again"** and goes straight to pairing.
  Only `https://` addresses are accepted, except `localhost` during development.
* After pairing, each person **registers an account** (username + PIN).
* **Bus Board** phones talk to the Bus Board server, not to Sekisho. The
  admin only reads and manages Bus Board through its ops API.

---

## 10. Make yourself keeper (phone admin)

New accounts are never admins. To manage the checkpoint from your phone:

1. Register your account in the app.
2. `/admin` → **Users** → click your user → **Grant admin**.
3. The keeper screens in the app (overview, pair another phone, revoke) now
   unlock for that account. You can only grant keeper rights here.

---

## 11. Bus Board tabs

* **Bus Board:** health (version, boot id, uptime), requests per route with
  p50/p95 latency and 4xx/5xx counts, live SSE streams, active vehicles per
  route, feed size (stops, unverified stops, routes, version), top journeys,
  runtime info. **Snapshot** asks Bus Board to persist its state now.
* **Contributors:** ping outcomes (accepted / rejected by reason), the trust
  histogram, and the top contributors. **Block** and **Unblock** each need a
  second click to confirm.

Every Bus Board action is CSRF-protected like any other admin action, and is
written to the audit trail (`ops_audit`, actions `admin.busboard.*`). The
latest entries appear at the bottom of the Contributors tab. If Bus Board
answers **404**, the tab says *check the token*: `BUSBOARD_OPS_TOKEN` doesn't
match Bus Board's ops token, or `BUSBOARD_URL` is wrong.

---

## 12. Rotating secrets

Change the value in **Vercel → Settings → Environment Variables**, then
**Redeploy**. Running deployments don't pick up changes.

| Secret | Steps | Side effects |
|---|---|---|
| `SEKISHO_ADMIN_PASSWORD` | Change it, then redeploy. | Existing admin sessions stay valid until they expire (12 h). To sign everyone out now, also rotate the session secret. |
| `SEKISHO_SESSION_SECRET` | Generate a new one, then redeploy. | Every admin session ends, and captchas issued before the change stop verifying. |
| `SEKISHO_SECRETS_KEY` | Generate a new one, redeploy, then **paste the NVIDIA key again** in the admin. | Keys stored with the old master key can no longer be decrypted. The admin says so (*Stored key unreadable*) until you save the key again. |
| NVIDIA key (stored in the admin) | Create a new key on build.nvidia.com, paste it in **Backends → NVIDIA API key → Save key**, click **Test**, then revoke the old key at NVIDIA. | None, and no redeploy needed. |
| `NVIDIA_API_KEY` (env) | Update it in Vercel, redeploy, **Test**, then revoke the old key. | None. |
| `BUSBOARD_OPS_TOKEN` | Change it on **both** Bus Board and Sekisho, then redeploy both. | The Bus Board tab says *check the token* until both sides match. |
| `CRON_SECRET` | Change it in Vercel **and** in your pinger, then redeploy. | Ticks return 401 until both sides match. |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | Run `python server/tools/gen_vapid.py` and update both. | Existing push subscriptions stop working. Only rotate these if the key leaked. |
| Neon password | Reset the role password in Neon. The integration updates `DATABASE_URL`. Then redeploy. | Brief connection errors until the redeploy finishes. |
| A phone's device key | `/admin` → **Devices** → **Revoke**. | That phone must pair again. |

---

## 13. Verified platform limits

Checked against the official docs on **2026-10-10**. Re-check the linked
pages if something behaves differently.

| Limit | Value | What Sekisho does | Source |
|---|---|---|---|
| Vercel Hobby function **max duration** (Fluid compute) | 300 s | `vercel.json` sets `maxDuration: 300`. NIM calls have a 55 s total budget per request, and Bus Board calls are capped at 8 s. | [vercel.com/docs/functions/limitations](https://vercel.com/docs/functions/limitations#max-duration) |
| Vercel function **request/response body** | 4.5 MB | The app downscales photos. The server rejects anything over 4.5 MB with its own 413. | [vercel.com/docs/functions/limitations](https://vercel.com/docs/functions/limitations#request-body-size) |
| Vercel Hobby **cron jobs** | at most once per day, within the scheduled hour | One daily safety-net cron, plus an external 15-minute pinger (step 8). | [vercel.com/docs/cron-jobs/usage-and-pricing](https://vercel.com/docs/cron-jobs/usage-and-pricing) |
| Vercel cron **auth** | Vercel sends `Authorization: Bearer <CRON_SECRET>` | `/api/cron/tick` checks exactly this. | [vercel.com/docs/cron-jobs/manage-cron-jobs](https://vercel.com/docs/cron-jobs/manage-cron-jobs#securing-cron-jobs) |
| Vercel Python runtime | 3.12 default, 500 MB uncompressed bundle | Bundles only `api/` + `server/sekisho` (see `excludeFiles`). | [vercel.com/docs/functions/runtimes/python](https://vercel.com/docs/functions/runtimes/python) |
| **NIM inline image** | ~**180,000 base64 characters** | Images are re-encoded to fit `NIM_MAX_IMAGE_B64`. | Not in NVIDIA's reference pages. The figure comes from NVIDIA API-catalog sample code (`assert len(image_b64) < 180_000`). Treat it as a conservative working limit. |
| Neon pooled vs direct | `DATABASE_URL` is pooled (PgBouncer, transaction mode) | Uses the pooled URL, no server-side prepares, and only transaction-scoped advisory locks. | [neon.com/docs/connect/connection-pooling](https://neon.com/docs/connect/connection-pooling) |
| Neon Free plan | 1 GB storage, 100 CU-hours/month, scale to zero after 5 min | The first request after an idle period is slower while compute wakes. | [neon.com/docs/introduction/plans](https://neon.com/docs/introduction/plans) |

---

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `503 server_misconfigured` on every API call | A required setting is missing, usually `DATABASE_URL`. The message says which. | Step 3, then redeploy. |
| `/admin` says *Admin login is disabled: …* | `SEKISHO_ADMIN_PASSWORD` is unset or shorter than 12 characters, or `SEKISHO_SESSION_SECRET` is unset or shorter than 32. | Fix the variable and redeploy. |
| Login page: *Could not complete the anti-bot check* | The browser couldn't fetch `/admin/captcha` (offline, or login disabled), or a blocker interfered with the inline script. | Reload. Allow JavaScript for the site. |
| *The anti-bot check expired / was already used* | The page sat open too long, or the form was resubmitted. | Wait for *Anti-bot check passed* and sign in again. |
| `/admin` says too many attempts / locked | 5 failed logins (wrong password **or** failed captcha) from your IP, or 50 overall, within 15 minutes. | Wait up to 15 minutes. |
| Backends → NVIDIA API key: *SEKISHO_SECRETS_KEY is not set* | No master key, so storage is disabled (Sekisho never stores a key in plaintext). | Set `SEKISHO_SECRETS_KEY` (32+ characters), redeploy, and save the key again. Or use `NVIDIA_API_KEY`. |
| Backends → *Stored key unreadable* | `SEKISHO_SECRETS_KEY` changed after the key was saved. | Paste the key again and click **Save key**. |
| Backends → **Test** says `FAIL` with **401/403** | NVIDIA key wrong or revoked. | Save the right key (or fix `NVIDIA_API_KEY` and redeploy). |
| **Test** shows `FAIL` with **429**, and AI features return 503 | NVIDIA rate limit, or NIM credits used up. | Wait and retry. Check your credits on build.nvidia.com. |
| Bus Board tab: *Bus Board not connected* | `BUSBOARD_URL` or `BUSBOARD_OPS_TOKEN` is missing, the token is shorter than 48 characters, or the URL is `http://` on Vercel. | Set both and redeploy. |
| Bus Board tab: *check the token* | Bus Board answered 404, because the token doesn't match Bus Board's ops token or the URL is wrong. | Make both tokens identical and check the URL. |
| Bus Board tab: *did not answer within 8 s* | Bus Board is asleep (free Render instances spin down) or down. | Wait a moment and click **Refresh**. |
| Cron tick **401** | The header doesn't match `CRON_SECRET`, or `CRON_SECRET` isn't set. | Make both sides identical, then redeploy. |
| App gets **429** `daily_cap` | The user hit `SEKISHO_DAILY_LLM_CAP`. | Raise it and redeploy, or wait until the next day. |
| App gets **429** `rate_limited` | More than `SEKISHO_RATE_LIMIT` requests per minute from one device. | Wait a minute. |
| Photo analysis returns 503 after ~55 s | NIM is slow or down, and the request budget ran out. | Retry, or pick a faster model for the slot. |
| `remaining connection slots are reserved` | You're using the **direct** URL, or many instances at once. | Use the pooled `DATABASE_URL`. Lower `SEKISHO_PG_POOL_MAX` to 2. |
| Phone says **"This phone needs to be paired again"** | The device was revoked, or it was paired to another address. | Pair again (step 9). The account and its history stay on the server. |
| No reminders arrive | No pinger, VAPID keys missing, or notifications not allowed on the phone. | Step 8. Check the `/admin` → **Push** tab. |
