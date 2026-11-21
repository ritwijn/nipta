"""Cashfree Payouts V2 adapter: standard transfer, status lookup, webhook signature, 2FA signature.

Contracts follow architecture.md section 6.7. Request body field names for the beneficiary block and the
2FA padding scheme must be confirmed against the event account (integration gate).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import httpx

from ..config import get_settings
from .http import client

API_VERSION = "2024-01-01"
PROVIDER = "cashfree"


@dataclass
class ProviderResult:
    http_status: int
    status: str | None
    status_code: str | None
    cf_transfer_id: str | None
    transfer_id: str | None
    raw: dict = field(default_factory=dict)


class ProviderUncertain(Exception):
    """Timeout, network error or 5xx: the transfer may or may not exist. Never retry with a new ID."""


class ProviderRejected(Exception):
    """Definitive 4xx: the request was refused and no transfer was created."""

    def __init__(self, http_status: int, code: str | None, message: str | None):
        super().__init__(f"{http_status} {code}: {message}")
        self.http_status = http_status
        self.code = code
        self.message = message


class ProviderDuplicateTransferId(Exception):
    """Cashfree refused a transfer_id it has already seen. Reconcile with a status lookup."""


_REDACT = {"bank_account_number", "beneficiary_account", "account_number", "x-client-secret"}


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("[redacted]" if k in _REDACT else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def cf_signature() -> str | None:
    s = get_settings()
    if not s.cashfree_2fa_public_key_path:
        return None
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_public_key(Path(s.cashfree_2fa_public_key_path).read_bytes())
    message = f"{s.cashfree_payouts_client_id}.{int(time.time())}".encode()
    encrypted = key.encrypt(  # type: ignore[union-attr]
        message, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None)
    )
    return base64.b64encode(encrypted).decode()


def headers(request_id: str | None = None) -> dict:
    s = get_settings()
    h = {
        "x-client-id": s.cashfree_payouts_client_id or "",
        "x-client-secret": s.cashfree_payouts_client_secret or "",
        "x-api-version": API_VERSION,
        "x-request-id": request_id or uuid.uuid4().hex,
        "Content-Type": "application/json",
    }
    sig = cf_signature()
    if sig:
        h["x-cf-signature"] = sig
    return h


def paise_to_rupees(paise: int) -> float:
    # Decimal keeps the conversion exact; JSON needs a number with two decimals.
    return float((Decimal(paise) / 100).quantize(Decimal("0.01")))


def _parse(resp: httpx.Response) -> ProviderResult:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return ProviderResult(
        http_status=resp.status_code,
        status=body.get("status"),
        status_code=body.get("status_code"),
        cf_transfer_id=str(body["cf_transfer_id"]) if body.get("cf_transfer_id") is not None else None,
        transfer_id=body.get("transfer_id"),
        raw=redact(body),
    )


def _is_duplicate(body: dict) -> bool:
    text = " ".join(str(body.get(k, "")) for k in ("code", "message", "type")).lower()
    return "already exist" in text or "duplicate" in text or "already been used" in text


def create_transfer(transfer_id: str, amount_paise: int, beneficiary: dict) -> ProviderResult:
    """beneficiary: {cashfree_beneficiary_id} or {account_holder_name, bank_account_number, ifsc}."""
    if beneficiary.get("cashfree_beneficiary_id"):
        details = {"beneficiary_id": beneficiary["cashfree_beneficiary_id"]}
    else:
        details = {
            "beneficiary_name": beneficiary["account_holder_name"],
            "beneficiary_instrument_details": {
                "bank_account_number": beneficiary["bank_account_number"],
                "bank_ifsc": beneficiary["ifsc"],
            },
        }
    payload = {
        "transfer_id": transfer_id,
        "transfer_amount": paise_to_rupees(amount_paise),
        "transfer_currency": "INR",
        "transfer_mode": "banktransfer",
        "beneficiary_details": details,
    }
    s = get_settings()
    try:
        with client(PROVIDER, s.cashfree_base_url) as c:
            resp = c.post("/payout/transfers", json=payload, headers=headers())
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ProviderUncertain(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code >= 500:
        raise ProviderUncertain(f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code == 409 or _is_duplicate(body):
            raise ProviderDuplicateTransferId(body.get("message"))
        raise ProviderRejected(resp.status_code, body.get("code"), body.get("message"))
    return _parse(resp)


def get_transfer(transfer_id: str) -> ProviderResult | None:
    """Status lookup. None means Cashfree has no transfer with this ID (yet)."""
    s = get_settings()
    try:
        with client(PROVIDER, s.cashfree_base_url) as c:
            resp = c.get("/payout/transfers", params={"transfer_id": transfer_id}, headers=headers())
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ProviderUncertain(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code == 404:
        return None
    if resp.status_code >= 500:
        raise ProviderUncertain(f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if "not found" in str(body.get("message", "")).lower() or body.get("code") == "transfer_not_found":
            return None
        raise ProviderRejected(resp.status_code, body.get("code"), body.get("message"))
    return _parse(resp)


def verify_webhook(raw_body: bytes, timestamp: str | None, signature: str | None) -> bool:
    secret = get_settings().cashfree_webhook_secret
    if not secret or not timestamp or not signature:
        return False
    digest = hmac.new(secret.encode(), timestamp.encode() + raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def sign_webhook(raw_body: bytes, timestamp: str, secret: str) -> str:
    """Helper for tests and local replay tooling."""
    digest = hmac.new(secret.encode(), timestamp.encode() + raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()
