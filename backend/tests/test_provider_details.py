"""Cashfree transaction details are stored (redacted, merged) and shown against the paid invoice."""
from __future__ import annotations

from backend.payments import approval, execution, status

from .conftest import add_live_verification, payouts_live, post_webhook, webhook_body


def _paid(conn, cashfree):
    payouts_live()
    add_live_verification(conn, "BEN-CP")
    p = approval.create_proposal(conn, "INV-E", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    return p, execution.execute_approved_payout(conn, p["proposal_id"], "OP")


def test_details_from_execute_and_poll_are_stored_masked_and_merged(client, conn, cashfree):
    p, a = _paid(conn, cashfree)
    view = client.get(f"/api/proposals/{p['proposal_id']}").json()["attempt"]["provider_details"]
    assert view["status"] == "RECEIVED" and view["transfer_id"] == a["transfer_id"]

    # A later status response from Cashfree with the full transfer record, including the bank UTR.
    def full(request):
        import httpx
        return httpx.Response(200, json={
            "transfer_id": a["transfer_id"], "cf_transfer_id": "667851513", "status": "SUCCESS", "status_code": "COMPLETED",
            "status_description": "Transfer completed", "transfer_utr": "UTR123456789", "transfer_amount": 3200,
            "transfer_mode": "imps", "fundsource_id": "CASHFREE_1",
            "beneficiary_details": {"beneficiary_id": "B_1", "beneficiary_instrument_details": {
                "bank_account_number": "60001122336011", "bank_ifsc": "CNRB0004567"}},
            "added_on": "2026-11-21T15:22:26", "updated_on": "2026-11-21T16:18:50"})
    import httpx
    from backend.integrations import http as http_mod
    http_mod.TRANSPORTS["cashfree"] = httpx.MockTransport(full)
    status.reconcile(conn, a["attempt_id"])
    d = client.get(f"/api/proposals/{p['proposal_id']}").json()["attempt"]["provider_details"]
    assert d["status"] == "SUCCESS" and d["status_code"] == "COMPLETED" and d["utr"] == "UTR123456789"
    assert d["transfer_mode"] == "imps" and d["cf_transfer_id"] == "667851513" and d["beneficiary_ifsc"] == "CNRB0004567"
    assert d["beneficiary_account"] == "••6011" and "60001122336011" not in str(d["raw"])  # masked
    # Shown on the invoice view too.
    inv = client.get("/api/invoices/INV-E/findings").json()
    assert inv["attempt"]["provider_details"]["utr"] == "UTR123456789"


def test_out_of_order_event_does_not_overwrite_details(client, conn, cashfree):
    p, a = _paid(conn, cashfree)
    post_webhook(client, webhook_body(a["transfer_id"], "SUCCESS", "COMPLETED"))
    late = webhook_body(a["transfer_id"], "PENDING", None, event_type="TRANSFER_PENDING", event_time="2026-11-21T11:00:00")
    assert post_webhook(client, late).json()["outcome"] == "IGNORED_BACKWARD"
    d = client.get(f"/api/proposals/{p['proposal_id']}").json()["attempt"]["provider_details"]
    assert d["status"] == "SUCCESS" and d["status_code"] == "COMPLETED"
