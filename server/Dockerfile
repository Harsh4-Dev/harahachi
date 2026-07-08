# Sekisho (関所) — single-container Harahachi deployment.
#
# ONE image runs everything: uvicorn (FastAPI) + nginx (front door) + cloudflared
# (outbound tunnel). No docker-compose, no self-signed certs — Cloudflare
# terminates TLS at the edge.
#
# IMPORTANT: build context is the REPO ROOT (so app/dist can be COPYed in):
#     docker build -f server/Dockerfile -t harahachi .
#     docker run -p 8080:80 -v hh-data:/data harahachi
#
# ROOTLESS: the container runs as a non-root USER (uid 10001). nginx keeps its
# pid + temp files under /tmp and binds :80 via a file capability (no host caps
# needed). /data is the only writable volume. See DEPLOY_DGX.md.
#
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SEKISHO_PORT=8420 \
    SEKISHO_HOST=127.0.0.1 \
    SEKISHO_DB_PATH=/data/sekisho.db \
    TUNNEL=cloudflare

# --- System deps: nginx + cloudflared + curl + libcap (for install) -------- #
# cloudflared is pulled from Cloudflare's official static binary (arch-aware).
# libcap2-bin gives us setcap so nginx can bind :80 while running as a non-root
# USER (CAP_NET_BIND_SERVICE), keeping the container rootless with no host caps.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends nginx curl ca-certificates libcap2-bin; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) cf_arch=amd64 ;; \
        arm64) cf_arch=arm64 ;; \
        armhf) cf_arch=arm ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/cloudflared \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cf_arch}"; \
    chmod +x /usr/local/bin/cloudflared; \
    cloudflared --version; \
    rm -f /etc/nginx/sites-enabled/default; \
    setcap 'cap_net_bind_service=+ep' "$(command -v nginx)"; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python deps first (layer caching) ------------------------------------- #
COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- App package + config + supervisor ------------------------------------- #
COPY server/sekisho ./sekisho
COPY server/nginx/nginx.conf /etc/nginx/nginx.conf
COPY server/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# --- The built phone PWA (run `npm run build` in app/ first) --------------- #
# Copied from the repo-root build context into nginx's web root.
COPY app/dist /srv/app

# --- Rootless user + writable data dir ------------------------------------- #
# A dedicated non-root account owns /data (the only writable volume) and /tmp
# (nginx pid/temp + tunnel scratch). App code in /app + /srv stays read-only.
RUN set -eux; \
    useradd --uid 10001 --create-home --shell /usr/sbin/nologin sekisho; \
    mkdir -p /data; \
    chown -R sekisho:sekisho /data; \
    chmod 700 /data
VOLUME ["/data"]

# uvicorn stays on loopback; cloudflared is outbound-only. Only the nginx front
# door is exposed — default :80 inside, but NGINX_PORT can move it to a high port.
EXPOSE 80

USER 10001

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
