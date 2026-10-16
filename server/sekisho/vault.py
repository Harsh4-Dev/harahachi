"""Encrypted secrets kept in the ``settings`` table (v7).

Used for credentials the owner manages from the admin (today: the NVIDIA API
key set on the Backends tab). Never plaintext:

* The master key is env ``SEKISHO_SECRETS_KEY`` (>= 32 chars). A 32-byte Fernet
  key is derived from it with HKDF-SHA256 and a purpose label, so the same env
  value can never collide with another use.
* Each secret is stored as a Fernet token (AES-128-CBC + HMAC-SHA256) under the
  settings key ``vault:<name>``.
* Without a usable master key, :func:`put` refuses (fail closed) and
  :func:`get` returns None — nothing is ever written in the clear.
* A token that no longer decrypts (master key rotated) reads as None and its
  state is ``undecryptable`` so the admin can say why.

Decrypted values are cached in-process for :data:`CACHE_TTL` seconds (other
instances pick up a change within that window); writes invalidate the cache.
"""
from __future__ import annotations

import base64
import time

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import db

PREFIX = "vault:"
CACHE_TTL = 30.0
_HKDF_SALT = b"sekisho/vault/salt/v1"
_HKDF_INFO = b"sekisho/settings-vault/fernet-key/v1"

_fernet_cache: dict = {"src": None, "f": None}
_value_cache: dict[str, tuple[float, str | None, str]] = {}


class VaultUnavailable(RuntimeError):
    """SEKISHO_SECRETS_KEY is missing/too short: secrets cannot be stored."""


def _cfg():
    from .config import config
    return config


def problem() -> str | None:
    return _cfg().secrets_storage_problem()


def available() -> bool:
    return problem() is None


def derive_fernet_key(master: str) -> bytes:
    """HKDF-SHA256(master, salt, info) -> 32 bytes -> urlsafe base64 (Fernet format)."""
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=_HKDF_INFO).derive(
        master.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _fernet() -> Fernet | None:
    if not available():
        return None
    master = _cfg().secrets_key
    if _fernet_cache["src"] != master:
        _fernet_cache["f"] = Fernet(derive_fernet_key(master))
        _fernet_cache["src"] = master
        _value_cache.clear()
    return _fernet_cache["f"]


def invalidate() -> None:
    _value_cache.clear()


def put(name: str, plaintext: str) -> None:
    f = _fernet()
    if f is None:
        raise VaultUnavailable(problem() or "secret storage unavailable")
    token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    db.set_setting(PREFIX + name, token)
    invalidate()


def delete(name: str) -> None:
    db.delete_setting(PREFIX + name)
    invalidate()


def _load(name: str) -> tuple[str | None, str]:
    """(plaintext or None, state) — state: missing | ok | undecryptable | locked."""
    token = db.get_setting(PREFIX + name)
    if not token:
        return None, "missing"
    f = _fernet()
    if f is None:
        return None, "locked"
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8"), "ok"
    except (InvalidToken, ValueError, UnicodeError):
        return None, "undecryptable"


def _cached(name: str) -> tuple[str | None, str]:
    now = time.monotonic()
    hit = _value_cache.get(name)
    if hit and now - hit[0] < CACHE_TTL and _fernet_cache["src"] == (_cfg().secrets_key or None):
        return hit[1], hit[2]
    if not available():
        # No master key: never touch the DB on the hot path (provider_mode() asks often).
        value, state = None, "locked"
    else:
        value, state = _load(name)
    _value_cache[name] = (now, value, state)
    return value, state


def get(name: str) -> str | None:
    return _cached(name)[0]


def state(name: str) -> str:
    """missing | ok | undecryptable | locked (stored but no master key) | disabled."""
    if not available():
        return "locked" if db.get_setting(PREFIX + name) else "disabled"
    return _cached(name)[1]
