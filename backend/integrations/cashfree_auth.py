"""Shared Cashfree request signing.

x-cf-signature is required only when the calling IP is not whitelisted. The scheme used here
(RSA-OAEP encryption of "<client_id>.<unix_timestamp>" with the account's 2FA public key,
base64-encoded) follows Cashfree's 2FA public-key method; confirm against the event account.
"""
from __future__ import annotations

import base64
import time
from functools import lru_cache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


@lru_cache(maxsize=4)
def _load_public_key(path: str):
    with open(path, "rb") as fh:
        return serialization.load_pem_public_key(fh.read())


def cf_signature(client_id: str, public_key_path: str, now: int | None = None) -> str:
    key = _load_public_key(public_key_path)
    message = f"{client_id}.{int(now if now is not None else time.time())}".encode()
    encrypted = key.encrypt(
        message,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None),
    )
    return base64.b64encode(encrypted).decode()


def auth_headers(client_id: str, client_secret: str, public_key_path: str = "") -> dict[str, str]:
    headers = {
        "x-client-id": client_id,
        "x-client-secret": client_secret,
        "Content-Type": "application/json",
    }
    if public_key_path:
        headers["x-cf-signature"] = cf_signature(client_id, public_key_path)
    return headers
