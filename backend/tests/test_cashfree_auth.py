"""Shared Cashfree auth headers, the 2FA signature, and the Secure ID adapter against a contract mock."""
from __future__ import annotations

import base64
import json

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from backend import config, db
from backend.integrations import cashfree_auth, cashfree_payouts, cashfree_secureid
from backend.integrations import http as http_mod


def _keypair(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = tmp_path / "cf_public.pem"
    pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo))
    return key, str(pub)


def test_signature_decrypts_to_client_id_and_timestamp(tmp_path):
    key, pub = _keypair(tmp_path)
    sig = cashfree_auth.cf_signature("CID123", pub, now=1763712000)
    plain = key.decrypt(base64.b64decode(sig),
                        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
    assert plain == b"CID123.1763712000"


def test_payout_headers(tmp_path):
    config.override(cashfree_payouts_client_id="cid", cashfree_payouts_client_secret="sec")
    h = cashfree_payouts.headers("req-1")
    assert h["x-client-id"] == "cid" and h["x-client-secret"] == "sec"
    assert h["x-api-version"] == "2024-01-01" and h["x-request-id"] == "req-1" and "x-cf-signature" not in h
    _, pub = _keypair(tmp_path)
    config.override(cashfree_2fa_public_key_path=pub)
    assert "x-cf-signature" in cashfree_payouts.headers()


def test_secureid_adapter_and_verify_route(client, conn):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"reference_id": 991, "name_at_bank": "EASTERN HARDWARE", "bank_name": "SBI",
                                         "account_status": "VALID", "account_status_code": "ACCOUNT_IS_VALID",
                                         "name_match_score": 96.5, "name_match_result": "DIRECT_MATCH"})

    http_mod.TRANSPORTS["cashfree_secureid"] = httpx.MockTransport(handler)
    config.override(cashfree_secureid_enabled=True, cashfree_secureid_client_id="sid", cashfree_secureid_client_secret="ssec")
    r = client.post("/api/vendors/V-EASTERN-HARDWARE/verify")
    assert r.status_code == 200, r.text
    assert r.json()["verification"]["source"] == "LIVE_SECURE_ID_BAV"
    req = seen[0]
    assert req.url.path == "/verification/bank-account/sync"
    assert req.headers["x-client-id"] == "sid" and req.headers["x-request-id"].startswith("nipta-bav-")
    assert json.loads(req.content) == {"bank_account": "30200456781234", "ifsc": "SBIN0003456", "name": "EASTERN HARDWARE"}
    audit = json.loads(db.row(conn, "SELECT detail_json FROM audit_events WHERE action='BENEFICIARY_VERIFIED'")["detail_json"])
    assert audit["bank_name"] == "SBI" and audit["name_match_score"] == "96.5" and audit["reference_id"] == "991"
    # A good verification never lifts the changed-account block on INV-D.
    assert db.row(conn, "SELECT decision FROM invoices WHERE invoice_id='INV-D'")["decision"] == "BLOCKED"


def test_secureid_rejection_is_not_verified(client, conn):
    http_mod.TRANSPORTS["cashfree_secureid"] = httpx.MockTransport(
        lambda r: httpx.Response(401, json={"code": "authentication_failed", "message": "Invalid clientId"}))
    config.override(cashfree_secureid_enabled=True, cashfree_secureid_client_id="x", cashfree_secureid_client_secret="y")
    r = client.post("/api/vendors/V-EASTERN-HARDWARE/verify")
    assert r.status_code == 502 and r.json()["error"] == "SECURE_ID_ERROR"
    assert not db.rows(conn, "SELECT * FROM beneficiary_verifications WHERE source='LIVE_SECURE_ID_BAV'")


def test_missing_2fa_key_file_is_misconfigured_not_live():
    s = config.override(cashfree_payouts_enabled=True, cashfree_payouts_client_id="cid",
                        cashfree_payouts_client_secret="sec", cashfree_2fa_public_key_path="secrets/does-not-exist.pem")
    assert s.payouts_live is False
    caps = config.capability_report(s)
    assert caps["cashfree_payouts"] == "MISCONFIGURED" and caps["cashfree_2fa_signature"] == "KEY_FILE_MISSING"
