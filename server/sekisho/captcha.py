"""Self-hosted proof-of-work captcha for the admin login (ALTCHA protocol idea).

No third-party service, no cookies, no stored state at issue time:

* ``issue()`` picks a random ``number`` in ``[0, maxnumber]`` and a ``salt`` that
  carries its own expiry (``<hex>?expires=<epoch>``), and returns
  ``{algorithm: "SHA-256", challenge: sha256(salt + number), salt, maxnumber,
  signature: HMAC-SHA256(challenge)}``. The number itself is never sent.
* The login page brute-forces ``number`` in the browser (crypto.subtle) and
  posts ``base64(JSON{algorithm, challenge, number, salt, signature})`` as the
  ``altcha`` form field next to the password.
* ``verify()`` checks, in order: shape, algorithm, HMAC signature (the server
  issued it), expiry (<= TTL, the expiry is inside the hashed salt so it cannot
  be extended), the hash (the client did the work), and finally single use — a
  DB-backed replay set (``counters`` bucket ``captcha:<challenge>``, atomic
  INSERT .. ON CONFLICT .. RETURNING) shared by every instance.

The HMAC key is derived from the admin session secret with HKDF and its own
purpose label, so it never equals the cookie-signing key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALGORITHM = "SHA-256"
TTL_SECONDS = 300
MAX_PAYLOAD_CHARS = 2048
_INFO = b"sekisho/admin-login-captcha/hmac-sha256/v1"
_SALT_RE = re.compile(r"^[0-9a-f]{24}\?expires=(\d{1,12})$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

REASONS = {
    "missing": "The anti-bot check did not run. This page needs JavaScript enabled.",
    "malformed": "The anti-bot check answer was malformed. Please try again.",
    "bad_signature": "The anti-bot check was not issued by this server. Please reload and try again.",
    "expired": "The anti-bot check expired. Please try again.",
    "wrong_solution": "The anti-bot check answer was wrong. Please try again.",
    "replayed": "That anti-bot check was already used. Please try again.",
}


def _cfg():
    from .config import config
    return config


def derive_key(base_key: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(base_key)


def _sign(key: bytes, challenge: str) -> str:
    return hmac.new(key, challenge.encode("ascii"), hashlib.sha256).hexdigest()


def _hash(salt: str, number: int) -> str:
    return hashlib.sha256(f"{salt}{number}".encode("utf-8")).hexdigest()


def issue(base_key: bytes, now: float | None = None, maxnumber: int | None = None) -> dict:
    now = int(now or time.time())
    maxnumber = int(maxnumber or _cfg().captcha_maxnumber)
    salt = f"{secrets.token_hex(12)}?expires={now + TTL_SECONDS}"
    number = secrets.randbelow(maxnumber + 1)
    challenge = _hash(salt, number)
    return {"algorithm": ALGORITHM, "challenge": challenge, "maxnumber": maxnumber, "salt": salt,
            "signature": _sign(derive_key(base_key), challenge)}


def decode_payload(raw: str) -> dict | None:
    """The ``altcha`` field: base64(JSON) as the ALTCHA widget sends it (raw JSON also accepted)."""
    raw = (raw or "").strip()
    if not raw or len(raw) > MAX_PAYLOAD_CHARS:
        return None
    text = raw
    if not raw.startswith("{"):
        try:
            text = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeError):
            return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def verify(raw: str, base_key: bytes, now: float | None = None, consume: bool = True) -> str | None:
    """None when the solution is valid (and now consumed), else a reason code (see REASONS)."""
    if not (raw or "").strip():
        return "missing"
    data = decode_payload(raw)
    if data is None:
        return "malformed"
    algorithm, challenge = data.get("algorithm"), data.get("challenge")
    salt, signature, number = data.get("salt"), data.get("signature"), data.get("number")
    if algorithm != ALGORITHM or not isinstance(challenge, str) or not isinstance(salt, str) \
            or not isinstance(signature, str) or isinstance(number, bool) or not isinstance(number, int):
        return "malformed"
    if not _HEX64.match(challenge) or not _HEX64.match(signature):
        return "malformed"
    m = _SALT_RE.match(salt)
    if m is None:
        return "malformed"
    # 1. issued by us (constant-time)
    if not hmac.compare_digest(signature, _sign(derive_key(base_key), challenge)):
        return "bad_signature"
    # 2. still fresh (the expiry is part of the hashed, signed salt)
    now = int(now or time.time())
    expires = int(m.group(1))
    if expires <= now or expires - now > TTL_SECONDS + 5:
        return "expired"
    # 3. the work was done
    if not 0 <= number <= max(_cfg().captcha_maxnumber, 0) or \
            not hmac.compare_digest(_hash(salt, number), challenge):
        return "wrong_solution"
    # 4. single use, across every instance
    if consume:
        from . import security
        if security.bump_counter("captcha:" + challenge, 0, expires + 60) != 1:
            return "replayed"
    return None


def solve(challenge: dict) -> dict:
    """Reference solver (tests / tools): the payload the login page would post."""
    for n in range(int(challenge["maxnumber"]) + 1):
        if _hash(challenge["salt"], n) == challenge["challenge"]:
            return {"algorithm": challenge["algorithm"], "challenge": challenge["challenge"], "number": n,
                    "salt": challenge["salt"], "signature": challenge["signature"]}
    raise ValueError("no solution in range")


def encode_payload(solution: dict) -> str:
    return base64.b64encode(json.dumps(solution).encode("utf-8")).decode("ascii")
