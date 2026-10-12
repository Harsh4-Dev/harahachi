"""Print VAPID keys in the exact format Sekisho reads (see push.py).

VAPID_PUBLIC_KEY  = base64url (no padding) of the raw uncompressed P-256 point
VAPID_PRIVATE_KEY = PKCS8 PEM on ONE line, newlines written as a literal
                    backslash-n (push.py turns them back into newlines), so the
                    whole value pastes into a single Vercel env-var field.

Usage:  python server/tools/gen_vapid.py
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

BACKSLASH_N = chr(92) + "n"  # literal \n, spelled out so no tool can collapse it

key = ec.generate_private_key(ec.SECP256R1())
pub = key.public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
)
pem = key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode("ascii")

print("VAPID_PUBLIC_KEY=" + base64.urlsafe_b64encode(pub).rstrip(b"=").decode("ascii"))
print("VAPID_PRIVATE_KEY=" + pem.strip().replace("\n", BACKSLASH_N))
