"""Vendor approval: operator-only, account typed twice, never copied from an invoice, starts unverified."""
from __future__ import annotations

import json

import pytest

from backend import agent, db

from .conftest import add_invoice, decision, findings

SHAKTI_INVOICE = {
    "language": "hi-IN", "vendor_name": "शक्ति हार्डवेयर सप्लायर्स", "invoice_number": "INV-2026-117",
    "invoice_date": "20-11-2026", "due_date": "05-12-2026",
    "line_items": [{"description": "एम.एस. नट बोल्ट सेट", "quantity": "20", "unit_price": "150", "line_total": "3,000"}],
    "subtotal": "3,000", "tax_lines": [], "stated_total": "₹3,000", "amount_in_words": "तीन हजार रुपये मात्र",
    "bank_account": "50200011876543", "ifsc": "HDFC0001234", "notes": None, "field_confidence": {},
}


def _shakti(conn):
    from backend.schemas import ExtractionRaw
    return add_invoice(conn, ExtractionRaw.model_validate(SHAKTI_INVOICE))


def _body(**kw):
    b = {"display_name": "Shakti Hardware Suppliers", "aliases": ["ಶಕ್ತಿ ಹಾರ್ಡ್‌ವೇರ್ ಸಪ್ಲೈಯರ್ಸ್"],
         "account_holder_name": "SHAKTI HARDWARE SUPPLIERS", "bank_account_number": "50200011876543",
         "bank_account_number_confirm": "50200011876543", "ifsc": "hdfc0001234", "payment_terms_days": 15}
    return b | kw


def test_approve_vendor_from_unresolved_invoice(client, conn):
    iid = _shakti(conn)
    assert set(findings(conn, iid)) == {"VENDOR_UNRESOLVED"}
    r = client.post("/api/vendors", json=_body(source_invoice_id=iid))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verification"] == "NOT_VERIFIED" and out["matches_invoice_print"] is True
    assert {"invoice_id": iid, "vendor_id": out["vendor_id"], "decision": "REVIEW"} in out["rechecked_invoices"]
    assert "शक्ति हार्डवेयर सप्लायर्स" in out["vendor"]["aliases"]  # invoice script added as an alias
    assert out["vendor"]["beneficiary"]["account"] == "••6543" and out["vendor"]["beneficiary"]["ifsc"] == "HDFC0001234"
    # Resolved, bank matches, but the new account is unverified -> REVIEW until verified or reviewed.
    assert set(findings(conn, iid)) == {"VERIFICATION_MISSING"}
    fid = findings(conn, iid)["VERIFICATION_MISSING"]["finding_id"]
    client.post(f"/api/findings/{fid}/resolve", json={"reason": "Called Shakti, confirmed account ending 6543"})
    assert decision(conn, iid) == "ELIGIBLE"
    audit = db.row(conn, "SELECT detail_json FROM audit_events WHERE action='VENDOR_APPROVED'")
    assert "50200011876543" not in audit["detail_json"]  # full account never written to the audit log


def test_typed_account_differs_from_print_blocks_invoice(client, conn):
    iid = _shakti(conn)
    out = client.post("/api/vendors", json=_body(bank_account_number="50200099990000",
                                                 bank_account_number_confirm="50200099990000",
                                                 source_invoice_id=iid)).json()
    assert out["matches_invoice_print"] is False
    assert findings(conn, iid)["CHANGED_BENEFICIARY"]["severity"] == "BLOCK"


@pytest.mark.parametrize("override,code", [
    ({"bank_account_number_confirm": "50200011876544"}, "ACCOUNT_CONFIRMATION_MISMATCH"),
    ({"display_name": "Kaveri Fasteners"}, "VENDOR_EXISTS"),
    ({"aliases": ["ಕಾವೇರಿ ಫಾಸ್ಟೆನರ್ಸ್"]}, "VENDOR_EXISTS"),
    ({"bank_account_number": "50100234569012", "bank_account_number_confirm": "50100234569012"}, "ACCOUNT_IN_USE"),
])
def test_refusals(client, conn, override, code):
    r = client.post("/api/vendors", json=_body(**override))
    assert r.json()["error"] == code
    assert db.row(conn, "SELECT COUNT(*) AS n FROM vendors")["n"] == 4


@pytest.mark.parametrize("override", [{"ifsc": "HDFC1234"}, {"bank_account_number": "12ab",
                                                            "bank_account_number_confirm": "12ab"}])
def test_validation(client, override):
    assert client.post("/api/vendors", json=_body(**override)).status_code == 422


def test_requires_session(client):
    client.headers.pop("Authorization")
    assert client.post("/api/vendors", json=_body()).status_code == 401


def test_agent_cannot_approve_vendors(conn):
    _shakti(conn)
    out = agent.handle_message(conn, "OP", conn.batch_id, "approve vendor Shakti", planned_calls=[
        {"name": "approve_vendor", "arguments": {"display_name": "Shakti"}}])
    assert out["tool_calls"][0]["reason_codes"] == ["TOOL_NOT_ALLOWED"]
    assert db.row(conn, "SELECT COUNT(*) AS n FROM vendors")["n"] == 4


def test_vendor_list_masks_accounts(client):
    vendors = client.get("/api/vendors").json()
    assert len(vendors) == 4
    assert all(v["beneficiary"]["account"].startswith("••") for v in vendors)
    assert "50100234569012" not in json.dumps(vendors)


def test_vendor_draft_prefills_everything_for_signed_in_operator(client, conn):
    iid = _shakti(conn)
    d = client.get(f"/api/invoices/{iid}/vendor-draft").json()
    assert d["display_name"] == "शक्ति हार्डवेयर सप्लायर्स" and d["account_holder_name"] == d["display_name"]
    assert d["bank_account_number"] == "50200011876543" and d["ifsc"] == "HDFC0001234"
    assert d["account_is_masked_on_invoice"] is False
    # Public invoice view still never carries the full account.
    assert "50200011876543" not in client.get(f"/api/invoices/{iid}/findings").text
    # Approving with the prefilled values records where the account came from; it still starts unverified.
    out = client.post("/api/vendors", json={"display_name": d["display_name"], "aliases": [],
                                             "account_holder_name": d["account_holder_name"],
                                             "bank_account_number": d["bank_account_number"],
                                             "bank_account_number_confirm": d["bank_account_number"],
                                             "ifsc": d["ifsc"], "source_invoice_id": iid}).json()
    audit = json.loads(db.row(conn, "SELECT detail_json FROM audit_events WHERE action='VENDOR_APPROVED'")["detail_json"])
    assert audit["account_source"] == "MATCHES_INVOICE_PRINT"
    assert set(findings(conn, iid)) == {"VERIFICATION_MISSING"}
    assert out["matches_invoice_print"] is True


def test_vendor_draft_masked_print_is_not_filled(client, conn):
    from backend.schemas import ExtractionRaw
    iid = add_invoice(conn, ExtractionRaw.model_validate(SHAKTI_INVOICE | {"invoice_number": "INV-2026-118",
                                                                            "bank_account": "XXXXXX6543"}))
    d = client.get(f"/api/invoices/{iid}/vendor-draft").json()
    assert d["bank_account_number"] is None and d["account_is_masked_on_invoice"] is True
    assert d["printed_account_masked"] == "••6543"


def test_vendor_draft_requires_session(client, conn):
    iid = _shakti(conn)
    client.headers.pop("Authorization")
    assert client.get(f"/api/invoices/{iid}/vendor-draft").status_code == 401
