"""Cashfree Secure ID bank account verification (sync). Runs only against the saved beneficiary."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import httpx

from ..config import get_settings
from .cashfree_payouts import ProviderRejected, ProviderUncertain, redact
from .http import client

PROVIDER = "cashfree_secureid"


@dataclass
class VerificationResult:
    account_status: str | None
    name_match_result: str | None
    name_at_bank: str | None
    reference_id: str | None
    raw: dict = field(default_factory=dict)


def verify_bank_account(account_number: str, ifsc: str, name: str) -> VerificationResult:
    s = get_settings()
    h = {
        "x-client-id": s.cashfree_secureid_client_id or "",
        "x-client-secret": s.cashfree_secureid_client_secret or "",
        "x-request-id": uuid.uuid4().hex,
        "Content-Type": "application/json",
    }
    if s.cashfree_secureid_api_version:
        h["x-api-version"] = s.cashfree_secureid_api_version
    from .cashfree_payouts import cf_signature

    sig = cf_signature()
    if sig:
        h["x-cf-signature"] = sig
    try:
        with client(PROVIDER, s.cashfree_base_url) as c:
            resp = c.post("/verification/bank-account/sync",
                          json={"bank_account": account_number, "ifsc": ifsc, "name": name}, headers=h)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ProviderUncertain(str(exc)) from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 500:
        raise ProviderUncertain(f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise ProviderRejected(resp.status_code, body.get("code"), body.get("message"))
    return VerificationResult(
        account_status=body.get("account_status"),
        name_match_result=body.get("name_match_result"),
        name_at_bank=body.get("name_at_bank"),
        reference_id=str(body.get("reference_id")) if body.get("reference_id") is not None else None,
        raw=redact(body),
    )
