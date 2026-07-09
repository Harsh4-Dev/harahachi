#!/usr/bin/env bash
# Sekisho single-container supervisor.
#
# Starts three processes and ties their lifecycles together — if ANY of them
# dies, the container exits (code != 0) so the restart policy brings it back:
#   1. uvicorn  (sekisho FastAPI)  -> 127.0.0.1:8420, loopback only
#   2. nginx    (front door)       -> :80, serves /srv/app + proxies to uvicorn
#   3. cloudflared (optional)      -> outbound tunnel exposing :80 to the world
#
# TUNNEL modes:
#   cloudflare (default) : quick tunnel (https://*.trycloudflare.com), URL parsed
#                          from cloudflared's log and published to sekisho.
#   token                : if TUNNEL_TOKEN is set, run a NAMED tunnel (stable
#                          domain configured in the Cloudflare dashboard).
#   off                  : no tunnel; reach the app over LAN at http://<ip>/.
set -euo pipefail

# --- Manual / passthrough modes --------------------------------------------- #
# `docker run ... harahachi bash` (any args) -> just exec them (interactive).
# `docker run -e MANUAL=1 ...`               -> idle so you can `docker exec`
#   in and drive things yourself with: start-server, start-tunnel, hh-status,
#   stop-all. Nothing starts automatically in manual mode.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
if [ "${MANUAL:-0}" = "1" ]; then
  echo "[entrypoint] MANUAL mode — nothing started. Exec in and drive it:"
  echo "    docker exec -it <container> bash"
  echo "    start-server     # sekisho + nginx"
  echo "    start-tunnel     # cloudflare tunnel (prints the public URL)"
  echo "    hh-status        # what's running + the URL"
  echo "    stop-all         # stop everything"
  exec sleep infinity
fi

# All runtime scratch lives under /tmp so the container can run as a NON-root
# USER with no writes to /run or /var — no host-permission assumptions.
PUBLIC_URL_FILE="${SEKISHO_TUNNEL_URL_FILE:-/tmp/sekisho_public_url}"
export SEKISHO_TUNNEL_URL_FILE="$PUBLIC_URL_FILE"  # keep sekisho + entrypoint in sync
TUNNEL="${TUNNEL:-cloudflare}"
# Front-door (nginx) and app (uvicorn) ports are both env-configurable.
NGINX_PORT="${NGINX_PORT:-80}"

log() { echo "[entrypoint] $*"; }

# Clear any stale URL from a previous run of the same volume.
mkdir -p "$(dirname "$PUBLIC_URL_FILE")"
: > "$PUBLIC_URL_FILE"

pids=()
term() {
  log "shutting down..."
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap term INT TERM

# --- 1. uvicorn (sekisho) on loopback -------------------------------------- #
# SEKISHO_HOST/PORT pinned to loopback so nginx is the only public listener.
export SEKISHO_HOST=127.0.0.1
export SEKISHO_PORT="${SEKISHO_PORT:-8420}"
log "starting sekisho (uvicorn) on 127.0.0.1:${SEKISHO_PORT}"
python -m sekisho &
pids+=("$!")

# Wait for uvicorn to accept connections before starting nginx.
for _ in $(seq 1 60); do
  if python -c "import socket,sys; s=socket.socket(); s.settimeout(0.5);
sys.exit(0 if s.connect_ex(('127.0.0.1', int('${SEKISHO_PORT}')))==0 else 1)" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

# --- 2. nginx (front door) ------------------------------------------------- #
# Render the config template into /tmp with the configured ports, and create the
# writable temp dirs it needs. Everything nginx touches at runtime is under /tmp,
# so this works unchanged whether the container runs as root or a non-root USER.
mkdir -p /tmp/nginx/client_body /tmp/nginx/proxy /tmp/nginx/fastcgi \
         /tmp/nginx/uwsgi /tmp/nginx/scgi
sed -e "s/__NGINX_PORT__/${NGINX_PORT}/g" \
    -e "s/__SEKISHO_PORT__/${SEKISHO_PORT}/g" \
    /etc/nginx/nginx.conf > /tmp/nginx.conf
log "starting nginx on :${NGINX_PORT} (serving /srv/app, proxying to 127.0.0.1:${SEKISHO_PORT})"
nginx -c /tmp/nginx.conf -g 'daemon off;' &
pids+=("$!")

# --- 3. cloudflared tunnel (optional) -------------------------------------- #
start_cloudflared() {
  local logfile="/tmp/cloudflared.log"
  : > "$logfile"

  if [ -n "${TUNNEL_TOKEN:-}" ]; then
    # Named tunnel: routing/hostname configured in the Cloudflare dashboard.
    log "starting cloudflared NAMED tunnel (TUNNEL_TOKEN set)"
    cloudflared tunnel --no-autoupdate run --token "${TUNNEL_TOKEN}" >>"$logfile" 2>&1 &
    pids+=("$!")
    if [ -n "${SEKISHO_PUBLIC_URL:-}" ]; then
      echo "${SEKISHO_PUBLIC_URL%/}" > "$PUBLIC_URL_FILE"
      banner "${SEKISHO_PUBLIC_URL%/}"
    else
      log "named tunnel active — set SEKISHO_PUBLIC_URL to your tunnel hostname for the QR."
    fi
    return
  fi

  # Quick tunnel: Cloudflare assigns a random https://*.trycloudflare.com URL.
  log "starting cloudflared quick tunnel (https://*.trycloudflare.com)"
  cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${NGINX_PORT}" >>"$logfile" 2>&1 &
  pids+=("$!")

  # Parse the assigned URL out of cloudflared's log and publish it.
  local url=""
  for _ in $(seq 1 60); do
    url="$(grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' "$logfile" 2>/dev/null | head -n1 || true)"
    if [ -n "$url" ]; then
      echo "$url" > "$PUBLIC_URL_FILE"
      banner "$url"
      return
    fi
    sleep 1
  done
  log "WARNING: could not determine trycloudflare URL after 60s; check /tmp/cloudflared.log"
}

banner() {
  local url="$1"
  echo ""
  echo "==================================================================="
  echo "  関所 Sekisho is live at:"
  echo ""
  echo "      $url"
  echo ""
  echo "  Open it on your phone (anywhere in the world), install the PWA,"
  echo "  then pair via ${url}/admin — the QR encodes this URL."
  echo "==================================================================="
  echo ""
}

case "$TUNNEL" in
  off)
    log "TUNNEL=off — no tunnel. Reach the app over LAN at http://<this-host-ip>/"
    if [ -n "${SEKISHO_PUBLIC_URL:-}" ]; then
      echo "${SEKISHO_PUBLIC_URL%/}" > "$PUBLIC_URL_FILE"
    fi
    ;;
  cloudflare|"")
    start_cloudflared
    ;;
  *)
    log "unknown TUNNEL='$TUNNEL' — treating as 'off'"
    ;;
esac

# --- supervise: exit the container if ANY supervised process dies ---------- #
# `wait -n` returns when the first child exits; then we tear the rest down.
wait -n
code=$?
log "a supervised process exited (code $code); stopping container."
term
exit "$code"
