# Deploying Harahachi + Sekisho on Vercel (free Hobby plan)

This guide takes you from a GitHub repository to a working
`https://<project>.vercel.app` with the phone app, the Sekisho API, the
`/admin` dashboard, a Postgres database (Neon), NVIDIA NIM for the AI
features, and push reminders. It is a **fresh start**: nobody has an account
yet, and everyone registers a new one in the app.

You will not need a terminal for most of it. The only commands are for
generating secrets, and you can run them on any computer that has Python.

> **Status of the Postgres path:** the SQLite path is covered by the full
> automated test suite. The Postgres (Neon) path was reviewed line by line,
> but it will be **first verified on the live Neon database**. Treat your
> first deploy as the verification run and work through
> [Post-deploy check](#7-post-deploy-check) carefully before you invite
> anyone.

---

## How it fits together

```
phone (PWA or APK) ──https──▶ https://<project>.vercel.app
                                 ├─ /            static app (app/dist, built by Vercel)
                                 ├─ /api/*  ─┐
                                 ├─ /admin* ─┤  one Python function: api/index.py
                                 ├─ /pair/* ─┤  (FastAPI app from server/sekisho)
                                 └─ /llm/*  ─┘
                                                 ├─▶ Neon Postgres  (DATABASE_URL)
                                                 └─▶ NVIDIA NIM     (NVIDIA_API_KEY)
cron-job.org or GitHub Actions ── every 15 min ──▶ GET /api/cron/tick  (push reminders)
```

`vercel.json` at the repo root already holds the build settings, the
rewrites, the security headers, the function's `maxDuration`, and a
once-daily safety-net cron, so you don't type any of them into the dashboard.

---

## 1. What you need

| Thing | Where | Cost |
|---|---|---|
| This repository on **GitHub** (public or private) | github.com | free |
| A **Vercel** account, signed up with GitHub | vercel.com/signup | Hobby plan, free |
| An **NVIDIA API key** | [build.nvidia.com](https://build.nvidia.com): sign in, open any model page (for example *llama-3.3-70b-instruct*), click **Get API Key**. The key starts with `nvapi-`. | free credits |
| A computer with **Python 3** | only to generate secrets and VAPID keys | free |
| *(optional)* a **cron-job.org** account | [cron-job.org](https://cron-job.org) | free |

Keep the NVIDIA key private. It is only ever stored in Vercel's environment
variables. Sekisho never writes it to the database and never returns it from
any endpoint (the admin page shows a masked hint such as `nvapi-…a1b2`).

---

## 2. Create the Vercel project

1. Go to **vercel.com → Add New… → Project**.
2. Under *Import Git Repository*, pick this repo. If it isn't listed, click
   **Adjust GitHub App Permissions** and grant access to it.
3. On the *Configure Project* screen:
   * **Framework Preset:** `Other`.
   * **Root Directory:** leave it as the repo root (`./`). Do **not** pick `app` or `server`.
   * **Build and Output Settings:** leave everything alone. `vercel.json`
     sets them: install `npm --prefix app ci`, build `npm --prefix app run build`,
     output `app/dist`.
   * **Environment Variables:** skip this for now. You will add them in step 4.
4. Click **Deploy**. The first deploy builds fine. Until the database and
   secrets exist, the API answers `503 server_misconfigured`, which is
   expected at this point.

Note your production URL, for example `https://harahachi.vercel.app`. This
guide calls it `https://<project>.vercel.app`.

> Python version: Vercel uses **Python 3.12** by default. The function's
> dependencies come from `requirements.txt` at the repo root, which is kept
> pinned to the same versions as `server/requirements.txt`.

---

## 3. Add Neon Postgres (Storage tab)

1. Open your project → **Storage** tab → **Create Database** (or *Browse
   Marketplace*) → choose **Neon** (Serverless Postgres) → **Continue**.
2. Pick **Create New Neon Account** if asked, accept the terms, choose a
   region close to your Vercel function region (default `iad1` = US East,
   so pick a US East region), choose the **Free** plan, and name the database.
3. On the database page click **Connect Project**, choose this project, tick
   **Production** (also Preview/Development if you want), and click **Connect**.
4. Confirm the variables arrived: **Settings → Environment Variables**. You
   should see `DATABASE_URL` (the **pooled** connection string, with
   `-pooler` in the host name) and `DATABASE_URL_UNPOOLED` (direct), plus
   several `PG*` / `POSTGRES_*` variables.

Sekisho reads **`DATABASE_URL`**, falling back to `POSTGRES_URL` if
`DATABASE_URL` is missing. That is the pooled URL, which is what a serverless
function should use. You don't create any tables yourself: on its first cold
start the function creates the schema. The migrations are idempotent and
serialized with a Postgres advisory lock, so several instances starting at
once is safe.

---

## 4. Environment variables

Add these under **Project → Settings → Environment Variables**, scoped to
**Production** (add them to Preview too if you use preview deployments).
**Environment variable changes only apply to new deployments**, so redeploy
after you change them (step 5).

### Generate the secrets first

Run these on any computer with Python 3. Each run prints a new random value.

```bash
# SEKISHO_SESSION_SECRET and CRON_SECRET (run once for each, use different values)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# SEKISHO_ADMIN_PASSWORD (or use your password manager, 12+ characters)
python -c "import secrets; print(secrets.token_urlsafe(18))"

# VAPID keys for push reminders (needs the 'cryptography' package:
#   pip install cryptography)
python server/tools/gen_vapid.py
```

`server/tools/gen_vapid.py` prints two lines,
`VAPID_PUBLIC_KEY=...` and `VAPID_PRIVATE_KEY=...`. The private key is a PEM
squeezed onto **one line**, with each newline written as a literal `\n`.
Paste it into Vercel exactly as printed: Sekisho converts the `\n` back into
newlines when it loads the key.

### Required

| Name | What it does | How to get it |
|---|---|---|
| `DATABASE_URL` | Postgres connection (pooled). Without it every API call answers `503 server_misconfigured`. | Injected automatically by the Neon integration (step 3). Don't type it. |
| `SEKISHO_ADMIN_PASSWORD` | Password for `/admin`. **Must be at least 12 characters on Vercel**, otherwise admin login is refused. Never auto-generated on Vercel. | Password manager, or the `token_urlsafe(18)` command above. |
| `SEKISHO_SESSION_SECRET` | HMAC key that signs admin session cookies, shared by every instance. **Must be at least 32 characters**, otherwise admin login is refused. | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `CRON_SECRET` | Protects `GET /api/cron/tick` (`Authorization: Bearer <CRON_SECRET>`, constant-time compare). If unset, the endpoint always answers 401. Vercel's own daily cron sends it automatically. | `python -c "import secrets; print(secrets.token_urlsafe(32))"` (Vercel recommends 16+ characters) |
| `NVIDIA_API_KEY` | Key for NVIDIA NIM (`https://integrate.api.nvidia.com/v1`). Photo analysis, body-fat estimate, Annai chat and insights all use it. If it is missing, those features return a clean 503 and the app shows its retry popup. | build.nvidia.com → **Get API Key** (`nvapi-…`) |

### Recommended (push reminders)

| Name | What it does | How to get it |
|---|---|---|
| `VAPID_PUBLIC_KEY` | Web-push public key (base64url of the raw P-256 point). | `python server/tools/gen_vapid.py` |
| `VAPID_PRIVATE_KEY` | Web-push private key (one-line PEM with literal `\n`). On Vercel push is disabled until both VAPID keys are set. | `python server/tools/gen_vapid.py` |
| `VAPID_SUBJECT` | Contact URI sent to the push services. The default is `mailto:admin@harahachi.local`. Set a real one, because some push services reject fake addresses. | e.g. `mailto:you@example.com` |

### Optional (private ops console, Bansho)

| Name | What it does | How to get it |
|---|---|---|
| `SEKISHO_OPS_TOKEN` | Bearer token for the server-to-server ops API at `/api/ops/v1/*` (analytics + admin actions for the private Bansho console). Use **at least 48 characters**. If it is unset, shorter than 48 characters, or wrong, every `/api/ops/*` path answers `404` as if it did not exist. Compared in constant time. Never logged or echoed, and no CORS, so only a server can call it. Put the **same value** in Bansho's `HARAHACHI_OPS_TOKEN`. | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SEKISHO_TZ` | IANA timezone that defines "a day" for ops analytics (DAU, today's totals, timeseries, weekly retention cohorts). Default `Asia/Kolkata`. | e.g. `Asia/Kolkata`, `Europe/London` |

### Optional (defaults are fine)

| Name | Default | What it does |
|---|---|---|
| `NIM_MODEL_ANALYZE` | `meta/llama-3.2-90b-vision-instruct` | Model for food-photo analysis. |
| `NIM_MODEL_BODY` | `meta/llama-3.2-90b-vision-instruct` | Model for body-fat photo estimate. |
| `NIM_MODEL_CHAT` | `meta/llama-3.3-70b-instruct` | Model for Annai chat. |
| `NIM_MODEL_INSIGHTS` | same as chat | Model for the weekly insight summary. |
| `NIM_FALLBACK_ANALYZE` / `_BODY` / `_CHAT` / `_INSIGHTS` | 11B vision for analyze/body, 90B vision for chat, chat's fallback for insights | Model tried once if the main one fails. Set to `none` to disable. |
| `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NIM endpoint. The host must be on the HTTPS allowlist. |
| `NIM_MAX_IMAGE_B64` | `180000` | Largest inline base64 image sent to NIM. Bigger photos are re-encoded down to fit. |
| `SEKISHO_DAILY_LLM_CAP` | `200` | Per-user daily cap on Annai chat (`/api/v1/chat`) and `/llm/v1` calls to protect your NIM credits. `0` disables it. |
| `SEKISHO_RATE_LIMIT` | `30` | Requests per minute per device, counted in the database. |
| `SEKISHO_CORS_ORIGINS` | *(empty)* | Extra allowed origins, comma-separated, e.g. a dev server. The deployment origin, `capacitor://localhost` and `https://localhost` are always allowed. |
| `SEKISHO_PUBLIC_URL` | auto | Origin printed in the pairing QR. Auto = `VERCEL_PROJECT_PRODUCTION_URL`. Set it if you add a custom domain. |
| `SEKISHO_LLM_BUDGET` | `55` | Total seconds for all NIM attempts (main model plus fallback) in one request. |
| `SEKISHO_ANALYZE_TIMEOUT` / `SEKISHO_CHAT_TIMEOUT` / `SEKISHO_CONNECT_TIMEOUT` | `45` / `30` / `3` | Per-call NIM timeouts in seconds. |
| `SEKISHO_MAX_BODY_BYTES` | `4500000` on Vercel | Requests above this get a clean 413. Keep it at or below Vercel's 4.5 MB. |
| `SEKISHO_PG_POOL_MAX` | `4` | Postgres connections per function instance. |
| `SEKISHO_BACKEND_ALLOWLIST` | *(empty)* | Extra HTTPS hosts allowed as LLM base URLs. `integrate.api.nvidia.com` is always allowed. |
| `SEKISHO_ENFORCE_BACKEND_ALLOWLIST` | on (Vercel) | SSRF guard. Leave it on. |
| `SEKISHO_PROVIDER` | `nim` on Vercel | Leave unset. |
| `SEKISHO_ALLOW_MOCK` | off on Vercel | **Leave unset in production.** If `1`, the app gets fake nutrition numbers when NIM fails. |

**Set automatically by Vercel (don't add them):** `VERCEL`, `VERCEL_URL`,
`VERCEL_PROJECT_PRODUCTION_URL`, `VERCEL_BRANCH_URL`.
**Never set** `VITE_DEMO`: it builds the app in offline demo mode.

---

## 5. Deploy

After adding the variables: **Deployments** tab → the latest deployment →
**⋯ → Redeploy**. Alternatively, push any commit to the production branch,
which deploys it automatically.

Watch the build log. `npm ci` and the Vite build run first, then Vercel
installs the Python requirements for `api/index.py`.

---

## 6. First-login checks

1. **Health:** open `https://<project>.vercel.app/api/v1/health`. You
   should get JSON with `"status": "ok"`. If you get
   `{"error":{"code":"server_misconfigured",…}}` instead, the message names
   the missing piece (usually `DATABASE_URL`).
2. **Admin:** open `https://<project>.vercel.app/admin` and log in with
   `SEKISHO_ADMIN_PASSWORD`. If the page says *Admin login is disabled: …*,
   the message tells you which variable is missing or too short. After 5
   failed logins from one IP, admin login is locked for up to 15 minutes.
3. **Backends tab:** check the line
   `Provider: nim · https://integrate.api.nvidia.com/v1 · key: set (nvapi-…xxxx)`.
   It should also list the four slots **analyze / body / chat / insights**
   with their models. Click **Test** on each slot. `ok · <model>` means the
   key and model work. `FAIL: …` means look at
   [Troubleshooting](#13-troubleshooting).
4. **Push tab:** the scheduler line should read `VAPID ok`. `VAPID missing`
   means one of the two VAPID variables is not set.

---

## 7. Post-deploy check

Do this once on every fresh deployment before you share the URL.

1. `GET https://<project>.vercel.app/api/v1/health` returns `"status": "ok"`.
2. Log into `/admin`.
3. **Pair a phone:** in `/admin` → **Devices** → *Pair a new device*, click **New pairing QR**. On the
   phone, open `https://<project>.vercel.app` (or the APK) and scan it (see
   step 9).
4. **Register a test user** in the app (any username plus a PIN).
5. **Log one meal**. A manual entry is enough, and a photo also exercises NIM.
6. In `/admin` → **Users**, confirm the test user is listed with a
   **meal count of 1**. Click the user to see the meal in their detail view.

If all six steps pass, the Postgres path works end to end on Neon: schema
creation, inserts with generated ids, upserts, and reads. You can delete the
test meal in the app afterwards.

---

## 8. The 15-minute cron (push reminders)

Reminders go out at **15:00** (water), **18:00** (calories left) and
**22:00** (day close), in each phone's local time. On Vercel nothing runs in
the background, so something has to call `GET /api/cron/tick` regularly.
Each call is idempotent: a reminder is "claimed" in the database before it is
sent, so extra or duplicate calls never double-send.

`vercel.json` already includes a **once-daily** Vercel Cron (`0 13 * * *`)
as a safety net. That is the most the Hobby plan allows, and it may fire
anywhere within that hour. To get all three daily reminders you need an
external pinger every 10–15 minutes. Pick **one** of the two options below.

### Option A: cron-job.org (recommended, most punctual)

1. Sign up at [cron-job.org](https://cron-job.org) and open the console.
2. **Create cronjob**.
3. **Title:** `Sekisho tick`.
   **URL:** `https://<project>.vercel.app/api/cron/tick`.
4. **Execution schedule:** every **15 minutes**.
5. Open the **Advanced** tab:
   * **Request method:** `GET`.
   * **Headers:** add one header. Key `Authorization`, value
     `Bearer <your CRON_SECRET>`: the word `Bearer`, one space, then the secret.
   * **Timeout:** 30 s or more.
6. Optionally, turn on email notification on failure.
7. Save, then use **Test run**. You want **HTTP 200** with a body like
   `{"ok":true,"sent":0,"subscriptions":…}`. A **401** means the header is
   wrong.

### Option B: GitHub Actions (already in the repo)

`.github/workflows/cron-tick.yml` calls the endpoint every 15 minutes and
**fails loudly on any non-2xx**. It does nothing until you configure it:

1. GitHub repo → **Settings → Secrets and variables → Actions**.
2. **Secrets** tab → **New repository secret**: name `CRON_SECRET`, value =
   the same value as in Vercel.
3. **Variables** tab → **New repository variable**: name `SEKISHO_URL`,
   value `https://<project>.vercel.app` (no trailing slash).
4. **Actions** tab → *Sekisho cron tick* → **Run workflow** to test it now.

GitHub can delay scheduled workflows by several minutes, more at busy times,
and may skip runs or disable schedules on inactive repositories. A reminder
still fires on the first tick within its local hour, so moderate delays are
fine. For on-the-minute delivery, use cron-job.org.

> Neon's free compute suspends after 5 minutes of inactivity, and every tick
> wakes it. A 15-minute tick keeps the database awake roughly a third of the
> time, which is well inside the free 100 CU-hours/month at 0.25 CU. If you
> ever get close to the limit, tick every 30 minutes instead: reminders still
> land within their hour.

---

## 9. Pair the phones (new URL)

Every phone pairs once against the Vercel URL:

* **PWA users:** open `https://<project>.vercel.app` in the phone's browser,
  install it (Add to Home Screen), then on the *Pair* screen scan the QR from
  `/admin` → **Devices**, or type the address shown with the QR. The QR
  encodes the production URL plus a single-use code that expires after **5
  minutes**.
* **APK users:** the APK must talk to `https://<project>.vercel.app`. On the
  *Pair* screen, scan the QR or enter that address. A phone that was paired to
  an older address shows **"This phone needs to be paired again"** and goes
  straight to pairing.
* After pairing, each person **registers a new account** (username + PIN).

Only `https://` addresses are accepted, except `localhost` during development.

---

## 10. Make yourself keeper (phone admin)

New accounts are never admins. To manage the checkpoint from your phone:

1. Register your account in the app.
2. `/admin` → **Users** → click your user → next to *Checkpoint keeper: no*, click **Grant admin**.
3. In the app, the keeper screens (overview, pair another phone, revoke) now
   unlock for that account.

Keeper rights can only be granted here, from the admin dashboard. There is
deliberately no environment-variable shortcut (it let anyone who registered a
listed username first become keeper).

---

## 11. Rotating secrets

Every change below follows the same pattern: change the value in **Vercel →
Settings → Environment Variables**, then **Redeploy**. Changes do not reach
running deployments.

| Secret | Steps | Side effects |
|---|---|---|
| `SEKISHO_ADMIN_PASSWORD` | Change it, then redeploy. | Existing admin sessions stay valid until they expire (12 h). To kick everyone out now, also rotate the session secret. |
| `SEKISHO_SESSION_SECRET` | Generate a new one, then redeploy. | All admin sessions become invalid, so everyone logs in again. |
| `CRON_SECRET` | Change it in Vercel **and** in your pinger (the cron-job.org header, or the GitHub secret) at the same time, then redeploy. | Ticks return 401 until both sides match. |
| `NVIDIA_API_KEY` | Create a new key on build.nvidia.com, update it in Vercel, redeploy, check **Backends → Test**, then revoke the old key at NVIDIA. | None. |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | Run `python server/tools/gen_vapid.py` and update both. | Existing push subscriptions stop working, so users must turn reminders off and on again in the app. Only rotate if the key leaked. |
| Neon password | In the Neon console, reset the role password. The integration updates `DATABASE_URL`, then redeploy. | Brief connection errors until the redeploy. |
| A phone's device key | `/admin` → **Devices** → **Revoke**. | That phone sees "paired again" and must re-pair. |

---

## 12. Verified platform limits

Checked against the official docs on **2026-10-10**. Limits change, so
re-check the linked pages if something behaves differently.

| Limit | Value | What Sekisho does | Source |
|---|---|---|---|
| Vercel Hobby function **max duration** (Fluid compute) | 300 s default and maximum | `vercel.json` sets `maxDuration: 300`. Every NIM call has its own timeout, with a total budget of 55 s per request, so the app gets a clean 503 rather than a platform 504. | [vercel.com/docs/functions/limitations](https://vercel.com/docs/functions/limitations#max-duration) |
| Vercel function **request/response body** | 4.5 MB (`413 FUNCTION_PAYLOAD_TOO_LARGE`) | The app downscales photos before upload. The server rejects anything over 4.5 MB with its own 413. | [vercel.com/docs/functions/limitations](https://vercel.com/docs/functions/limitations#request-body-size) |
| Vercel Hobby **cron jobs** | Up to 100 per project, **at most once per day**, fires anywhere within the scheduled hour (±59 min). More frequent expressions fail the deploy. | One daily safety-net cron in `vercel.json`, plus an external 15-minute pinger (step 8). | [vercel.com/docs/cron-jobs/usage-and-pricing](https://vercel.com/docs/cron-jobs/usage-and-pricing) |
| Vercel cron **auth** | If `CRON_SECRET` is set, Vercel sends `Authorization: Bearer <CRON_SECRET>` | `/api/cron/tick` checks exactly this. | [vercel.com/docs/cron-jobs/manage-cron-jobs](https://vercel.com/docs/cron-jobs/manage-cron-jobs#securing-cron-jobs) |
| Vercel Python runtime | 3.12 default (3.13, 3.14 available), 500 MB uncompressed bundle | Bundles only `api/` + `server/sekisho` (see `excludeFiles`). | [vercel.com/docs/functions/runtimes/python](https://vercel.com/docs/functions/runtimes/python) |
| **NIM inline image** | ~**180,000 base64 characters** (larger images go through NVIDIA's assets API) | Images are re-encoded with Pillow to fit `NIM_MAX_IMAGE_B64` (default 180000). | **Not in NVIDIA's official reference pages.** The figure comes from NVIDIA API-catalog sample code (`assert len(image_b64) < 180_000`, "use the assets API" for larger), reproduced e.g. at [mer.vin/2024/05/nvidia-nim-examples](https://mer.vin/2024/05/nvidia-nim-examples/). Treat it as a conservative working limit. It can be changed with `NIM_MAX_IMAGE_B64`. |
| Neon pooled vs direct | `DATABASE_URL` = pooled (PgBouncer, transaction mode, `-pooler` host). `DATABASE_URL_UNPOOLED` = direct. | Sekisho uses the pooled URL, never server-side prepares, and uses only transaction-scoped advisory locks, all of which are safe in transaction mode. | [neon.com/docs/guides/vercel-managed-integration](https://neon.com/docs/guides/vercel-managed-integration), [neon.com/docs/connect/connection-pooling](https://neon.com/docs/connect/connection-pooling) |
| Neon connections (0.25 CU) | `max_connections` 104 (97 usable). The pooler accepts up to 10,000 client connections. | Up to `SEKISHO_PG_POOL_MAX` (4) connections per function instance, through the pooler. | [neon.com/docs/connect/connection-pooling](https://neon.com/docs/connect/connection-pooling) |
| Neon Free plan | 1 GB storage per project, 100 CU-hours/month per project, scale to zero after 5 min (cannot be disabled) | The first request after idle is a bit slower while compute wakes. | [neon.com/docs/introduction/plans](https://neon.com/docs/introduction/plans) |

---

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `503 {"error":{"code":"server_misconfigured", …}}` on every API call | A required setting is missing, usually `DATABASE_URL` (Neon not connected to this environment). The message says which. | Step 3 (Connect Project for Production), then redeploy. |
| `/admin` says *Admin login is disabled: …* | `SEKISHO_ADMIN_PASSWORD` is unset or shorter than 12 characters, or `SEKISHO_SESSION_SECRET` is unset or shorter than 32. | Fix the variable and redeploy. |
| `/admin` says too many attempts / locked | 5 failed logins from your IP (or 50 overall) within 15 minutes. | Wait up to 15 minutes. |
| Cron tick **401** (cron-job.org, GitHub run fails with *HTTP 401*) | The header doesn't match `CRON_SECRET`: typo, missing `Bearer `, or the secret was rotated on only one side. Also 401 if `CRON_SECRET` is not set in Vercel at all. | Make both sides identical and redeploy after changing Vercel. Test with `curl -i -H "Authorization: Bearer $CRON_SECRET" https://<project>.vercel.app/api/cron/tick`. |
| GitHub workflow never runs | `SEKISHO_URL` repository **variable** not set (the job is skipped on purpose), or GitHub disabled schedules after 60 days without repo activity. | Set the variable, or re-enable the workflow in the Actions tab. |
| Backends → **Test** says `FAIL` with **401/403** | NVIDIA key wrong, revoked, or not set for this environment. | Check `NVIDIA_API_KEY` (no spaces or quotes), redeploy. |
| Backends → **Test** shows `FAIL` mentioning **429**, and AI features return 503 | NVIDIA rate limit, or your NIM credits are used up. Sekisho turns the upstream failure into its standard 503, and the app shows its retry popup. | Wait and retry. Check your credits on build.nvidia.com. |
| App gets **429** `daily_cap` | The user hit `SEKISHO_DAILY_LLM_CAP` (default 200 chat / `/llm/v1` calls per day). | Raise the variable and redeploy, or wait until the next UTC day. |
| App gets **429** `rate_limited` | More than `SEKISHO_RATE_LIMIT` (30) requests per minute from one device. | Wait a minute. Raise the limit if it's legitimate. |
| Photo analysis returns 503 after ~55 s | NIM slow or down. The request budget (`SEKISHO_LLM_BUDGET`) ran out. | Retry. Pick a faster model for the slot in the Backends tab. |
| `504 FUNCTION_INVOCATION_TIMEOUT` | Something ran past the 300 s `maxDuration`. It should not happen, because NIM calls are capped far below that. | Check the function logs (Vercel → Logs). Lower `SEKISHO_*_TIMEOUT` values if you raised them. |
| `413` on photo upload | Photo bigger than 4.5 MB reached the server (old app version or a huge image). | Update the app, which downscales before upload. |
| Browser console shows `Refused to load … because it violates the Content Security Policy` | The app's CSP (`vercel.json` → `headers`) allows only same-origin scripts and connections, plus Google Fonts. The admin page has its own strict CSP. | Expected for third-party scripts. If you deliberately add one, extend the CSP in `vercel.json` and redeploy. |
| `remaining connection slots are reserved` / `too many connections` | You're using the **direct** URL, or many instances at once. | Make sure `DATABASE_URL` is the **pooled** one (`-pooler` in the host). Lower `SEKISHO_PG_POOL_MAX` to 2. |
| Neon "compute time quota exceeded" | 100 CU-hours/month used up. | Tick every 30 min instead of 15, or upgrade Neon. |
| Phone says **"This phone needs to be paired again"** | The device was revoked, or it was paired to another address. | Pair again (step 9). The account and history stay on the server. |
| No reminders arrive | No pinger set up, VAPID keys missing, or the phone never allowed notifications. | Step 8. Check the `/admin` → **Push** tab (configured? last tick?). Turn reminders off and on in the app. |
