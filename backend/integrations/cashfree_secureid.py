"""Cashfree Secure ID: Bank Account Verification (sync, V2). Runs only against the SAVED beneficiary.

Contract (official reference, checked 21 Nov 2026):
  POST {base}/verification/bank-account/sync
  headers: x-client-id, x-client-secret, optional x-cf-signature (2FA, needed when the IP isn't whitelisted)
  body:    bank_account, ifsc, name (enables name match), phone (optional)
  returns: reference_id, name_at_bank, bank_name, account_status (VALID|INVALID), account_status_code,
           name_match_score, name_match_result
           (DIRECT_MATCH | GOOD_PARTIAL_MATCH | MODERATE_PARTIAL_MATCH | POOR_PARTIAL_MATCH | NO_MATCH)

A VALID account with a good name match proves the account exists under a similar name. It does not prove
the vendor authorized a change of account, so it never lifts a CHANGED_BENEFICIARY block.
(The official cashfree_verification SDK pins pydantic<2 and has no bank-account method, hence raw httpx.)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import httpx

from ..config import get_settings
from . import cashfree_auth
from .cashfree_payouts import ProviderRejected, ProviderUncertain, redact
from .http import client

PROVIDER = "cashfree_secureid"


@dataclass
class VerificationResult:
    account_status: str | None
    name_match_result: str | None
    name_at_bank: str | None
    reference_id: str | None
    account_status_code: str | None = None
    name_match_score: str | None = None
    bank_name: str | None = None
    request_id: str | None = None
    raw: dict = field(default_factory=dict)


def verify_bank_account(account_number: str, ifsc: str, name: str, phone: str | None = None) -> VerificationResult:
    s = get_settings()
    request_id = f"nipta-bav-{uuid.uuid4().hex[:20]}"
    h = cashfree_auth.auth_headers(s.cashfree_secureid_client_id or "", s.cashfree_secureid_client_secret or "",
                                   s.cashfree_2fa_public_key_path or "")
    h["x-request-id"] = request_id
    if s.cashfree_secureid_api_version:
        h["x-api-version"] = s.cashfree_secureid_api_version
    body: dict = {"bank_account": account_number, "ifsc": ifsc, "name": name}
    if phone:
        body["phone"] = phone
    try:
        with client(PROVIDER, s.cashfree_base_url) as c:
            resp = c.post("/verification/bank-account/sync", json=body, headers=h)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ProviderUncertain(f"Secure ID request failed: {type(exc).__name__}") from exc
    try:
        data = resp.json()
    except ValueError:
        data = {"raw_text": resp.text[:500]}
    if resp.status_code >= 500:
        raise ProviderUncertain(f"Secure ID returned {resp.status_code}")
    if resp.status_code >= 400:
        raise ProviderRejected(resp.status_code, data.get("code"), data.get("message"))

    def _s(key):
        return str(data[key]) if data.get(key) is not None else None

    return VerificationResult(
        account_status=data.get("account_status"),
        name_match_result=data.get("name_match_result"),
        name_at_bank=data.get("name_at_bank"),
        reference_id=_s("reference_id"),
        account_status_code=data.get("account_status_code"),
        name_match_score=_s("name_match_score"),
        bank_name=data.get("bank_name"),
        request_id=request_id,
        raw=redact(data),
    )
