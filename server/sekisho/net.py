"""LAN / Tailscale IP detection for startup banner and QR pairing URL."""
from __future__ import annotations

import os
import socket

# The container entrypoint writes the Cloudflare tunnel URL here once cloudflared
# has printed its assigned https://*.trycloudflare.com hostname. We re-read this
# file lazily on every pairing/QR request because the tunnel URL only appears a
# few seconds AFTER the server boots — it must not be cached at import time.
# Default lives under /tmp so a rootless (non-root USER) container can write it
# without any host-permission assumptions; override with SEKISHO_TUNNEL_URL_FILE.
TUNNEL_URL_FILE = os.environ.get("SEKISHO_TUNNEL_URL_FILE", "/tmp/sekisho_public_url")


def _tunnel_url() -> str | None:
    """The Cloudflare tunnel origin, if the entrypoint has published one."""
    try:
        with open(TUNNEL_URL_FILE, "r", encoding="utf-8") as fh:
            url = fh.read().strip().rstrip("/")
        return url or None
    except (OSError, ValueError):
        return None


def _all_ipv4() -> list[str]:
    addrs: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addrs.add(info[4][0])
    except Exception:
        pass
    # A UDP "connect" trick to find the primary outbound interface IP.
    for probe in ("8.8.8.8", "1.1.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 80))
            addrs.add(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return sorted(a for a in addrs if not a.startswith("127."))


# The fixed gateway IP of the Windows built-in Mobile Hotspot. When the laptop is
# sharing its hotspot, the phone reaches the server at exactly this address, so it is
# the single best host to encode in the pairing QR.
HOTSPOT_IP = "192.168.137.1"


def classify_ips() -> dict[str, list[str]]:
    lan, tailscale, other = [], [], []
    for ip in _all_ipv4():
        if ip.startswith("100."):
            tailscale.append(ip)
        elif ip.startswith(("192.168.", "10.")) or ip.startswith("172."):
            lan.append(ip)
        else:
            other.append(ip)
    # Surface the hotspot gateway first in the LAN list when it is up.
    if HOTSPOT_IP in lan:
        lan.remove(HOTSPOT_IP)
        lan.insert(0, HOTSPOT_IP)
    return {"lan": lan, "tailscale": tailscale, "other": other}


def best_pairing_host() -> str:
    """Best guess IP to embed in the pairing QR.

    Preference order: Windows Mobile Hotspot gateway (the phone's actual route to us) →
    Tailscale → other LAN → anything else.
    """
    c = classify_ips()
    if HOTSPOT_IP in c["lan"]:
        return HOTSPOT_IP
    if c["tailscale"]:
        return c["tailscale"][0]
    if c["lan"]:
        return c["lan"][0]
    if c["other"]:
        return c["other"][0]
    return "127.0.0.1"


def public_url(port: int) -> str:
    """The origin the app should call and the pairing QR should encode.

    Resolution order (see API.md §Deployment):
      1. SEKISHO_PUBLIC_URL env — explicit override (manual/LAN fronting).
      2. The Cloudflare tunnel URL written to /tmp/sekisho_public_url by the
         entrypoint once the tunnel is up. Re-read lazily so the QR picks it up
         after boot.
      3. LAN fallback: http://<detected-host>:<port> served directly by uvicorn
         (dev mode, or TUNNEL=off with nginx on :80 — then callers front it).
    """
    from .config import config

    if config.public_url:
        return config.public_url
    tunnel = _tunnel_url()
    if tunnel:
        return tunnel
    return f"http://{best_pairing_host()}:{port}"
