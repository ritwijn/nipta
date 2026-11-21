"""Bulk preparation: proposals only, never approval or payout."""
from __future__ import annotations

from backend import db
from backend.payments import approval, execution


def test_plan_scope_respects_budget(client, conn):
    r = client.post(f"/api/batches/{conn.batch_id}/proposals", json={"scope": "plan", "cash_budget_paise": 900000}).json()
    assert [c["invoice_id"] for c in r["created"]] == ["INV-E"]  # A (₹8,200) doesn't fit after E
    assert r["total_paise"] == 320000
    states = {x["state"] for x in db.rows(conn, "SELECT state FROM payment_proposals")}
    assert states == {"READY_FOR_APPROVAL"}
    assert db.rows(conn, "SELECT * FROM payment_attempts") == []  # nothing approved or sent


def test_all_ready_scope_and_idempotent_repeat(client, conn):
    r = client.post(f"/api/batches/{conn.batch_id}/proposals", json={"scope": "all_ready"}).json()
    assert {c["invoice_id"] for c in r["created"]} == {"INV-A", "INV-E"}
    again = client.post(f"/api/batches/{conn.batch_id}/proposals", json={"scope": "all_ready"}).json()
    assert again["created"] == [] and {a["invoice_id"] for a in again["already_prepared"]} == {"INV-A", "INV-E"}
    assert db.row(conn, "SELECT COUNT(*) AS n FROM payment_proposals")["n"] == 2


def test_paid_bill_is_skipped_with_reason(client, conn, cashfree):
    from .conftest import add_live_verification, payouts_live
    payouts_live()
    add_live_verification(conn, "BEN-KF")
    p = approval.create_proposal(conn, "INV-A", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    execution.execute_approved_payout(conn, p["proposal_id"], "OP")  # now PENDING at the provider
    r = client.post(f"/api/batches/{conn.batch_id}/proposals", json={"scope": "all_ready"}).json()
    assert {c["invoice_id"] for c in r["created"]} == {"INV-E"}
    assert r["already_prepared"] == []
    assert [(s["invoice_id"], s["code"]) for s in r["skipped"]] == [("INV-A", "PAYMENT_EXISTS")]
    assert len(cashfree.posts) == 1  # bulk prepare never sends anything


def test_requires_session(client, conn):
    client.headers.pop("Authorization")
    assert client.post(f"/api/batches/{conn.batch_id}/proposals", json={"scope": "plan"}).status_code == 401


def test_valid_bills_answer_moves_sent_bills_out_of_ready(conn, cashfree):
    from backend import agent
    from .conftest import add_live_verification, payouts_live
    payouts_live()
    add_live_verification(conn, "BEN-KF")
    p = approval.create_proposal(conn, "INV-A", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    for lang, ready, sent in (("en", "1 ready to pay", "1 already paid or sent"),
                              ("kn", "1 ಪಾವತಿಗೆ ಸಿದ್ಧ", "ಈಗಾಗಲೇ ಪಾವತಿಸಲಾಗಿದೆ"),
                              ("hi", "1 भुगतान के लिए तैयार", "भुगतान हो चुका")):
        reply = agent.handle_message(conn, "OP", conn.batch_id, {"en": "Which bills are valid?",
                                     "kn": "ಯಾವ ಬಿಲ್‌ಗಳು ಸರಿಯಾಗಿವೆ?", "hi": "कौन से बिल सही हैं?"}[lang], lang)["reply"]
        assert ready in reply and sent in reply, reply
        line_a = next(l for l in reply.splitlines() if "INV-A" in l)
        assert "Eligible for payment" not in line_a and "ಪಾವತಿಗೆ ಅರ್ಹ" not in line_a


def _prepared(client, conn):
    client.post(f"/api/batches/{conn.batch_id}/proposals", json={"scope": "all_ready"})
    return client.get(f"/api/batches/{conn.batch_id}/approval-queue").json()


def test_bulk_approve_then_send(client, conn, cashfree):
    from .conftest import add_live_verification, payouts_live
    payouts_live()
    add_live_verification(conn, "BEN-CP")
    add_live_verification(conn, "BEN-KF")
    q = _prepared(client, conn)
    items = [{"proposal_id": i["proposal_id"], "snapshot_hash": i["snapshot_hash"]} for i in q["to_approve"]]
    total = sum(i["amount_paise"] for i in q["to_approve"])
    assert total == 1140000 and len(items) == 2
    r = client.post(f"/api/batches/{conn.batch_id}/approve-bulk", json={"items": items, "confirm_total_paise": total}).json()
    assert r["approved"] == 2 and cashfree.posts == []          # approval sends nothing
    q2 = client.get(f"/api/batches/{conn.batch_id}/approval-queue").json()
    assert len(q2["to_approve"]) == 0 and len(q2["to_send"]) == 2
    ids = [i["proposal_id"] for i in q2["to_send"]]
    s = client.post(f"/api/batches/{conn.batch_id}/execute-bulk", json={"proposal_ids": ids}).json()
    assert {x["outcome"] for x in s["results"]} == {"PENDING"} and len({x["transfer_id"] for x in s["results"]}) == 2
    assert len(cashfree.posts) == 2
    client.post(f"/api/batches/{conn.batch_id}/execute-bulk", json={"proposal_ids": ids})  # repeat: no new transfers
    assert len(cashfree.posts) == 2
    assert db.row(conn, "SELECT COUNT(*) AS n FROM payment_attempts")["n"] == 2


def test_bulk_approve_total_mismatch_approves_nothing(client, conn):
    q = _prepared(client, conn)
    items = [{"proposal_id": i["proposal_id"], "snapshot_hash": i["snapshot_hash"]} for i in q["to_approve"]]
    r = client.post(f"/api/batches/{conn.batch_id}/approve-bulk", json={"items": items, "confirm_total_paise": 1})
    assert r.status_code == 409 and r.json()["error"] == "TOTAL_MISMATCH"
    assert {x["state"] for x in db.rows(conn, "SELECT state FROM payment_proposals")} == {"READY_FOR_APPROVAL"}


def test_bulk_approve_refuses_stale_one_individually(client, conn):
    q = _prepared(client, conn)
    by_inv = {i["invoice_id"]: i for i in q["to_approve"]}
    conn.execute("UPDATE invoices SET stated_total_paise=330000 WHERE invoice_id='INV-E'")  # E's snapshot is now stale
    items = [{"proposal_id": i["proposal_id"], "snapshot_hash": i["snapshot_hash"]} for i in q["to_approve"]]
    total = sum(i["amount_paise"] for i in q["to_approve"])
    r = client.post(f"/api/batches/{conn.batch_id}/approve-bulk", json={"items": items, "confirm_total_paise": total}).json()
    outcome = {x["invoice_id"]: x["outcome"] for x in r["results"]}
    assert outcome == {"INV-A": "AUTHORIZED", "INV-E": "APPROVAL_STALE"} and r["approved"] == 1
    assert by_inv  # both were listed for review


def test_bulk_routes_reject_other_batches_and_need_session(client, conn):
    r = client.post(f"/api/batches/{conn.batch_id}/approve-bulk",
                    json={"items": [{"proposal_id": "prop_nope", "snapshot_hash": "x"}], "confirm_total_paise": 0})
    assert r.status_code == 404
    client.headers.pop("Authorization")
    assert client.get(f"/api/batches/{conn.batch_id}/approval-queue").status_code == 401
    assert client.post(f"/api/batches/{conn.batch_id}/execute-bulk", json={"proposal_ids": ["x"]}).status_code == 401
