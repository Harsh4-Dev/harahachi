# Deploying Sekisho / Harahachi on a DGX (rootless Docker)

This guide runs the whole stack — FastAPI (uvicorn) + nginx front door + an
optional Cloudflare tunnel — as a **single rootless container**, suitable for a
shared DGX box where you are a regular member of the `docker` group and have **no
root / sudo**. The container image runs as a non-root `USER` (uid 10001), keeps
all runtime scratch under `/tmp`, and writes persistent data only to a named
volume. No host directories, ports below your mapping, or capabilities are
assumed.

vLLM (or any OpenAI-compatible backend) is expected to already be running on the
host; the container reaches it via `host.docker.internal`.

---

## 0. Prerequisites

- Membership in the `docker` group (`id -nG | grep docker`). No `sudo` needed.
- The repo cloned somewhere in your home dir.
- The phone PWA built into `app/dist` (`cd app && npm ci && npm run build`) — the
  Dockerfile copies it into nginx's web root.
- (Optional) A vLLM server reachable on the host, e.g. `http://127.0.0.1:8000/v1`.
- (Optional) A Cloudflare **named-tunnel token** for a stable public hostname.

---

## 1. Clone

```bash
git clone <your-fork-or-origin-url> harahachi
cd harahachi
```

## 2. Build

The build context is the **repo root** (so `app/dist` can be copied in):

```bash
docker build -f server/Dockerfile -t harahachi:latest .
```

What the image does for rootless operation:

- Adds a non-root account `sekisho` (uid **10001**) and runs `USER 10001`.
- `setcap cap_net_bind_service=+ep` on the nginx binary, so nginx can bind
  container port **:80** as a non-root user with **no host capabilities**.
- nginx pid + temp dirs live under `/tmp/nginx`; the tunnel URL + cloudflared
  logs live under `/tmp`. The only writable persistent path is the `/data`
  volume (owned by uid 10001).
- All ports are env-configurable: `NGINX_PORT` (front door, default 80),
  `SEKISHO_PORT` (uvicorn loopback, default 8420).

## 3. Run (as a regular docker-group user)

### LAN-only (no tunnel) — simplest first run

```bash
docker run -d --name harahachi \
  --restart unless-stopped \
  -p 8080:80 \
  -v hh-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e TUNNEL=off \
  -e SEKISHO_ADMIN_PASSWORD='choose-a-strong-one' \
  -e SEKISHO_VLLM_URL='http://host.docker.internal:8000/v1' \
  -e SEKISHO_VLLM_MODEL='your-model-id' \
  harahachi:latest
```

- `-p 8080:80` maps host **8080** to the container's nginx on **80**. Pick any
  free host port; `8080` avoids needing a privileged host port. Reach the app at
  `http://<dgx-lan-ip>:8080/` and the admin dashboard at
  `http://<dgx-lan-ip>:8080/admin`.
- `--add-host host.docker.internal:host-gateway` is what lets the container call
  a vLLM server running on the **host** (Linux Docker has no implicit
  `host.docker.internal`). Point `SEKISHO_VLLM_URL` at it.
- `-v hh-data:/data` is a named volume; the SQLite DB (`/data/sekisho.db`)
  survives rebuilds.

If host port 80/8080 is taken you can also move nginx **inside** the container
to a high port and map it straight through, e.g.
`-e NGINX_PORT=8080 -p 8080:8080`.

### Public, stable URL — Cloudflare named tunnel

Create a named tunnel in the Cloudflare Zero Trust dashboard, route your hostname
to `http://localhost:80`, and copy its **token**:

```bash
docker run -d --name harahachi \
  --restart unless-stopped \
  -p 8080:80 \
  -v hh-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e TUNNEL=token \
  -e TUNNEL_TOKEN='eyJ...your-named-tunnel-token...' \
  -e SEKISHO_PUBLIC_URL='https://harahachi.example.com' \
  -e SEKISHO_ADMIN_PASSWORD='choose-a-strong-one' \
  -e SEKISHO_VLLM_URL='http://host.docker.internal:8000/v1' \
  -e SEKISHO_VLLM_MODEL='your-model-id' \
  harahachi:latest
```

`SEKISHO_PUBLIC_URL` is the hostname baked into the pairing QR, so phones pair
against the tunnel origin. (With `TUNNEL=cloudflare`, the default, you instead
get an ephemeral `https://*.trycloudflare.com` URL printed in the logs — handy
for a quick test, not for a permanent install.)

Get the admin password / pairing URL from the logs:

```bash
docker logs harahachi | head -40
```

---

## 4. Keep it alive without root

You are in the `docker` group but have no systemd root access. Two options:

### A. Docker restart policy (used above)

`--restart unless-stopped` already brings the container back after a crash or a
Docker daemon restart. This is the least-effort option and needs nothing else.

### B. systemd **user** unit (survives your logout if lingering is enabled)

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/harahachi.service <<'EOF'
[Unit]
Description=Harahachi (Sekisho) container
After=default.target

[Service]
Restart=always
ExecStart=/usr/bin/docker start -a harahachi
ExecStop=/usr/bin/docker stop harahachi

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now harahachi.service
# Optional: keep the user manager running after you log out (may need admin once):
loginctl enable-linger "$USER"
```

### C. nohup fallback (no systemd at all)

If even the user manager is unavailable, the `--restart` policy plus a boot-time
`docker start` is enough. For a foreground supervisor you can `nohup`:

```bash
nohup docker start -a harahachi >~/harahachi.log 2>&1 &
```

---

## 5. Updating (git pull flow)

```bash
cd harahachi
git pull
cd app && npm ci && npm run build && cd ..     # rebuild the PWA if it changed
docker build -f server/Dockerfile -t harahachi:latest .
docker stop harahachi && docker rm harahachi
# re-run the same `docker run ...` command from step 3
```

The `/data` named volume is untouched by the rebuild, so users, meals, backends,
VAPID keys and push subscriptions all persist across updates. The DB schema
self-migrates on boot (idempotent `ALTER`s), so a newer image reads an older DB
cleanly.

---

## 6. First-run admin & keeper setup

Adminship is **not** granted automatically to the first user. Grant it either:

- from the **web admin dashboard** (`/admin` → Users → *Grant admin*), or
- at startup via `-e SEKISHO_ADMIN_USERS='alice,bob'` (comma-separated
  usernames) — applied idempotently on every boot; it only ever **adds** the
  flag, never revokes flags set from the dashboard.

---

## 7. Environment variable reference

| Variable | Default | Purpose |
|---|---|---|
| `NGINX_PORT` | `80` | Front-door port **inside** the container. |
| `SEKISHO_PORT` | `8420` | uvicorn loopback port (nginx upstream). |
| `SEKISHO_HOST` | `127.0.0.1` | uvicorn bind addr (keep on loopback). |
| `SEKISHO_DB_PATH` | `/data/sekisho.db` | SQLite path on the writable volume. |
| `SEKISHO_ADMIN_PASSWORD` | *(generated)* | Web-admin password; printed if unset. |
| `SEKISHO_ADMIN_USERS` | *(none)* | Comma-separated usernames granted keeper at boot. |
| `SEKISHO_VLLM_URL` | *(none)* | OpenAI-compatible backend base URL. |
| `SEKISHO_VLLM_MODEL` | *(none)* | Model id for that backend. |
| `SEKISHO_PUBLIC_URL` | *(auto)* | Origin baked into the pairing QR. |
| `SEKISHO_TUNNEL_URL_FILE` | `/tmp/sekisho_public_url` | Where the tunnel URL is published. |
| `TUNNEL` | `cloudflare` | `cloudflare` (quick), `token` (named), or `off` (LAN). |
| `TUNNEL_TOKEN` | *(none)* | Named-tunnel token when `TUNNEL=token`. |

---

## 8. Troubleshooting

- **`docker: permission denied` on the socket** — you are not in the `docker`
  group yet, or need a fresh login. `id -nG` should list `docker`.
- **App can't reach vLLM** — confirm `--add-host host.docker.internal:host-gateway`
  is present and vLLM is bound on the host (not only inside another container).
  Test from inside: `docker exec harahachi curl -s http://host.docker.internal:8000/v1/models`.
- **nginx fails to bind :80** — some hardened runtimes strip `CAP_NET_BIND_SERVICE`
  from the bounding set, defeating the file capability. Set `-e NGINX_PORT=8080`
  and map `-p 8080:8080` to sidestep privileged ports entirely.
- **Reminders not firing** — check the admin **Status** page scheduler card
  (last tick / next fire / VAPID status) and confirm the phone granted
  notification permission and subscribed.

---

## Manual mode — drive the container yourself

Prefer to start things by hand (or debug inside the box)? Run the container
idle, exec in, and use the built-in commands:

```bash
# 1. Start it idle (nothing runs yet). Pick any host port you like (8080 here).
docker run -d --name hh -e MANUAL=1 -p 8080:80 -v hh-data:/data harahachi

# 2. Go inside
docker exec -it hh bash

# 3. Drive it (inside the container)
start-server      # starts sekisho (uvicorn) + nginx  → logs in /tmp/sekisho.log
start-tunnel      # starts the Cloudflare tunnel and PRINTS the public URL
hh-status         # what's up/down + the current public URL
stop-all          # stop tunnel + nginx + sekisho
grep -i password /tmp/sekisho.log   # the admin password
exit              # leave the shell; the container keeps idling
```

Notes:
- `TUNNEL_TOKEN`/`SEKISHO_PUBLIC_URL`/`NGINX_PORT`/`SEKISHO_ADMIN_PASSWORD`
  envs all work in manual mode too — set them on `docker run`.
- `docker run -it --rm -p 8080:80 -v hh-data:/data harahachi bash` also drops
  you straight into a shell (one-off, removed on exit).
- Automatic mode is unchanged: `docker run` without `MANUAL=1` supervises
  everything and exits if any process dies (pair with `--restart unless-stopped`).

## Docker cheat-sheet

```bash
docker build -f server/Dockerfile -t harahachi .   # build (repo root!)
docker run -d --name hh --restart unless-stopped \
  -p 8080:80 -v hh-data:/data harahachi            # run (auto mode)
docker logs -f hh                                  # watch logs / find the URL + admin password
docker exec -it hh bash                            # shell inside
docker exec hh hh-status                           # quick status without a shell
docker stop hh && docker start hh                  # stop / start (new quick-tunnel URL on start!)
docker rm -f hh                                    # remove the container (data survives in the volume)
docker volume inspect hh-data                      # where your data lives
docker volume rm hh-data                           # FULL RESET — erases all users/meals
docker cp hh:/tmp/sekisho.log ./sekisho.log        # pull a log out
git pull && cd app && npm run build && cd .. \
  && docker build -f server/Dockerfile -t harahachi . \
  && docker rm -f hh && docker run -d --name hh --restart unless-stopped \
     -p 8080:80 -v hh-data:/data harahachi          # update flow
```

## No GitHub? Deploy by zip

Everything `docker build` needs is `server/`, the pre-built `app/dist/`, and
the root `.dockerignore`. On the laptop (PowerShell, repo root — rebuild the
app first if you changed it: `cd app && npm run build`):

The repo ships a ready recipe — or just take the generated `harahachi-deploy.zip`.
Transfer it (WinSCP / `scp harahachi-deploy.zip user@dgx:~/`), then on the DGX:

```bash
unzip harahachi-deploy.zip && cd harahachi
docker build -f server/Dockerfile -t harahachi .
# ...everything from here is IDENTICAL to the git-clone flow above
docker run -d --name hh -e MANUAL=1 -p 8080:80 -v hh-data:/data harahachi
docker exec -it hh bash    # then: start-server / start-tunnel / hh-status
```

Updating later = make a fresh zip on the laptop, re-upload, unzip over the old
folder, rebuild the image, `docker rm -f hh` and run again (data survives in
the `hh-data` volume).
