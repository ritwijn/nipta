"""Cashfree Secure ID: automatic verification during checks, and person-approved bank account changes."""
from __future__ import annotations

import json

import httpx
import pytest

from backend import config, pipeline, verification
from backend.integrations import http as http_mod


class SecureIdMock:
    """Answers /verification/bank-account/sync; results keyed by account number."""

    def __init__(self, default=("VALID", "DIRECT_MATCH"), fail=False):
        self.calls: list[dict] = []
        self.results: dict[str, tuple[str, str]] = {}
        self.default, self.fail = default, fail

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/verification/bank-account/sync"
        body = json.loads(req.content)
        self.calls.append(body)
        if self.fail:
            return httpx.Response(503, json={"message": "down"})
        status, match = self.results.get(body["bank_account"], self.default)
        return httpx.Response(200, json={"account_status": status, "name_match_result": match,
                                         "name_at_bank": body["name"], "reference_id": 77})


@pytest.fixture
def secureid(settings):
    config.override(cashfree_secureid_enabled=True, cashfree_secureid_client_id="cid",
                    cashfree_secureid_client_secret="csecret")
    verification._last_failure.clear()
    m = SecureIdMock()
    http_mod.TRANSPORTS["cashfree_secureid"] = httpx.MockTransport(m.handler)
    return m


def _inv(conn, number):
    return conn.execute("SELECT * FROM invoices WHERE invoice_number_raw=?", (number,)).fetchone()


def _codes(conn, invoice_id):
    return {r["code"] for r in conn.execute("SELECT code FROM findings WHERE invoice_id=? AND status='OPEN'",
                                            (invoice_id,))}


def test_checks_verify_saved_account_automatically(conn, secureid):
    inv = _inv(conn, "KF-182")
    assert pipeline.run_checks(conn, inv["invoice_id"]) == "ELIGIBLE"
    assert secureid.calls == [{"bank_account": "50100234569012", "ifsc": "HDFC0001234", "name": "KAVERI FASTENERS"}]
    pipeline.run_checks(conn, inv["invoice_id"])
    assert len(secureid.calls) == 1  # fresh live result is reused


def test_name_mismatch_from_secure_id_goes_to_review(conn, secureid):
    secureid.results["50100234569012"] = ("VALID", "NO_MATCH")
    inv = _inv(conn, "KF-182")
    assert pipeline.run_checks(conn, inv["invoice_id"]) == "REVIEW"
    assert "ACCOUNT_NAME_MISMATCH" in _codes(conn, inv["invoice_id"])


def test_secure_id_down_means_review_not_seeded_mock(conn, secureid):
    secureid.fail = True
    inv = _inv(conn, "KF-182")
    assert pipeline.run_checks(conn, inv["invoice_id"]) == "REVIEW"
    assert "VERIFICATION_MISSING" in _codes(conn, inv["invoice_id"])
    pipeline.run_checks(conn, inv["invoice_id"])
    assert len(secureid.calls) == 1  # cooldown: no retry storm while it is down


def test_secure_id_off_keeps_current_behaviour(conn):
    assert pipeline.run_checks(conn, _inv(conn, "KF-182")["invoice_id"]) == "ELIGIBLE"


def _request(client, **over):
    body = {"account_number": "91999888776655", "ifsc": "KKBK0009999",
            "confirmation_note": "Called owner on saved number, confirmed new Kotak account"} | over
    return client.post("/api/vendors/V-EASTERN-HARDWARE/bank-changes", json=body)


def test_approved_bank_change_unblocks_invoice_with_new_details(client, conn, secureid):
    inv = _inv(conn, "EH-040")
    assert "CHANGED_BENEFICIARY" in _codes(conn, inv["invoice_id"])
    r = _request(client)
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["state"] == "PENDING_APPROVAL" and req["account"] == "••6655"
    assert secureid.calls[-1] == {"bank_account": "91999888776655", "ifsc": "KKBK0009999", "name": "EASTERN HARDWARE"}
    # Nothing changes until a person approves.
    assert "CHANGED_BENEFICIARY" in _codes(conn, inv["invoice_id"])

    a = client.post(f"/api/bank-changes/{req['request_id']}/approve")
    assert a.status_code == 200, a.text
    assert a.json()["state"] == "APPROVED"
    ben = conn.execute("SELECT * FROM beneficiaries WHERE vendor_id='V-EASTERN-HARDWARE' AND active=1").fetchone()
    assert (ben["version"], ben["account_last4"], ben["ifsc"]) == (2, "6655", "KKBK0009999")
    assert ben["cashfree_beneficiary_id"] is None  # pay the new details, not an old Cashfree beneficiary
    assert "CHANGED_BENEFICIARY" not in _codes(conn, inv["invoice_id"])
    assert "VERIFICATION_MISSING" not in _codes(conn, inv["invoice_id"])
    assert client.post(f"/api/bank-changes/{req['request_id']}/approve").status_code == 409


def test_bank_change_with_poor_name_match_cannot_be_approved(client, conn, secureid):
    secureid.results["91999888776655"] = ("VALID", "POOR_PARTIAL_MATCH")
    req = _request(client).json()
    assert req["state"] == "VERIFICATION_FAILED" and not req["approvable"]
    assert client.post(f"/api/bank-changes/{req['request_id']}/approve").status_code == 409
    ben = conn.execute("SELECT version FROM beneficiaries WHERE vendor_id='V-EASTERN-HARDWARE' AND active=1").fetchone()
    assert ben["version"] == 1


def test_rejected_bank_change_leaves_account_alone(client, conn, secureid):
    req = _request(client).json()
    r = client.post(f"/api/bank-changes/{req['request_id']}/reject", json={"reason": "Owner denied it"})
    assert r.json()["state"] == "REJECTED"
    assert client.post(f"/api/bank-changes/{req['request_id']}/approve").status_code == 409


def test_bank_change_needs_secure_id(client):
    r = _request(client)
    assert r.status_code == 409 and r.json()["error"] == "SECURE_ID_UNAVAILABLE"


@pytest.mark.parametrize("over,code", [({"ifsc": "KKBK9009999"}, "INVALID_IFSC"),
                                       ({"account_number": "12-34x56789"}, "INVALID_ACCOUNT"),
                                       ({"account_number": "30200456781234", "ifsc": "SBIN0003456"}, "NO_CHANGE")])
def test_bank_change_input_checks(client, secureid, over, code):
    r = _request(client, **over)
    assert r.json()["error"] == code


def test_stale_request_cannot_be_approved(client, conn, secureid):
    first = _request(client).json()
    second = _request(client, account_number="91999888770000").json()
    assert client.post(f"/api/bank-changes/{second['request_id']}/approve").status_code == 200
    r = client.post(f"/api/bank-changes/{first['request_id']}/approve")
    assert r.status_code == 409 and r.json()["error"] == "REQUEST_STALE"
