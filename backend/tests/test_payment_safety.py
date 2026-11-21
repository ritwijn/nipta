"""PRD tests T11–T17 and T21, plus webhook, simulation and test-provider behaviour."""
from __future__ import annotations

import pytest

from backend import agent, config, db, pipeline
from backend.errors import NiptaError
from backend.payments import approval, execution, status

from .conftest import (add_live_verification, decision, findings, payouts_live, post_webhook, webhook_body)


def _approved(conn, invoice_id="INV-E"):
    p = approval.create_proposal(conn, invoice_id, "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    return p


@pytest.fixture
def live(conn, cashfree):
    payouts_live()
    add_live_verification(conn, "BEN-CP")
    add_live_verification(conn, "BEN-KF")
    return cashfree


def postings(conn, kind=None):
    sql = "SELECT * FROM financial_postings" + (" WHERE kind=?" if kind else "")
    return db.rows(conn, sql, (kind,) if kind else ())


def test_snapshot_dispatch_mode_live(conn, live):
    p = approval.create_proposal(conn, "INV-E", "OP")
    assert p["snapshot"]["dispatch_mode"] == "CASHFREE_SANDBOX"
    assert p["snapshot"]["verification_source"] == "LIVE_SECURE_ID_BAV"


def test_seeded_verification_never_reaches_real_payout(conn, cashfree):
    payouts_live()  # Payouts live, but verification is only the seeded mock
    p = _approved(conn)
    assert p["snapshot"]["dispatch_mode"] == "SIMULATED"
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert a["mode"] == "SIMULATED" and a["status"] == "SIMULATED"
    assert cashfree.posts == [] and postings(conn) == []


def test_not_eligible_cannot_be_proposed(conn):
    for iid in ("INV-B", "INV-C", "INV-D", "INV-F"):
        with pytest.raises(NiptaError) as e:
            approval.create_proposal(conn, iid, "OP")
        assert e.value.code == "NOT_ELIGIBLE"


def test_execute_requires_approval(conn, live):
    p = approval.create_proposal(conn, "INV-E", "OP")
    with pytest.raises(NiptaError) as e:
        execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert e.value.code == "NOT_AUTHORIZED"
    assert live.posts == []


def test_T11_agent_pay_creates_proposal_only(conn, live):
    reply = agent.handle_message(conn, "OP", conn.batch_id, "pay INV-E")
    assert [c["tool"] for c in reply["tool_calls"]] == ["prepare_payment_proposal"]
    assert reply["proposals"] and "approval card" in reply["reply"]
    reply = agent.handle_message(conn, "OP", conn.batch_id, "ಇದನ್ನು ಪಾವತಿಸಿ, pay all")
    assert live.posts == [] and live.gets == []
    states = {r["state"] for r in conn.execute("SELECT state FROM payment_proposals")}
    assert states == {"READY_FOR_APPROVAL"}
    assert db.rows(conn, "SELECT * FROM payment_attempts") == []


def test_T11_status_question_does_not_prepare_payment(conn, live):
    reply = agent.handle_message(conn, "OP", conn.batch_id, "what is the payment status of INV-E?")
    assert [c["tool"] for c in reply["tool_calls"]] == ["get_payment_status"]
    assert db.rows(conn, "SELECT * FROM payment_proposals") == []


def test_T12_stale_snapshot_amount(conn, live):
    p = approval.create_proposal(conn, "INV-E", "OP")
    conn.execute("UPDATE invoices SET stated_total_paise=330000 WHERE invoice_id='INV-E'")
    with pytest.raises(NiptaError) as e:
        approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    assert e.value.code == "APPROVAL_STALE"
    assert approval.get_proposal(conn, p["proposal_id"])["state"] == "STALE"


def test_T12_stale_after_approval_beneficiary_change(conn, live):
    p = _approved(conn)
    with db.immediate(conn):
        conn.execute("UPDATE beneficiaries SET active=0 WHERE beneficiary_id='BEN-CP'")
        conn.execute("INSERT INTO beneficiaries(beneficiary_id, version, vendor_id, account_holder_name,"
                     " bank_account_number, account_last4, ifsc, active, created_at)"
                     " VALUES ('BEN-CP', 2, 'V-COIMBATORE-PACKING', 'COIMBATORE PACKING', '99998888777766',"
                     " '7766', 'CNRB0004567', 1, 0)")
    with pytest.raises(NiptaError) as e:
        execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert e.value.code == "APPROVAL_STALE"
    assert live.posts == [] and db.rows(conn, "SELECT * FROM payment_attempts") == []


def test_T12_displayed_hash_must_match(conn, live):
    p = approval.create_proposal(conn, "INV-E", "OP")
    with pytest.raises(NiptaError) as e:
        approval.approve(conn, p["proposal_id"], "OP", "0" * 64)
    assert e.value.code == "APPROVAL_STALE"


def test_approval_expired(conn, live):
    config.override(approval_ttl_seconds=-1)
    p = approval.create_proposal(conn, "INV-E", "OP")
    with pytest.raises(NiptaError) as e:
        approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    assert e.value.code == "APPROVAL_EXPIRED"


def test_T13_double_execute(conn, live):
    p = _approved(conn)
    a1 = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    a2 = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert a1["attempt_id"] == a2["attempt_id"] and a1["transfer_id"] == a2["transfer_id"]
    assert len(live.posts) == 1 and live.posts[0]["transfer_id"] == a1["transfer_id"]
    assert a1["transfer_id"].startswith("NPT_") and len(a1["transfer_id"]) == 28
    assert live.posts[0]["transfer_amount"] == 3200.0
    assert a2["status"] == "PENDING"  # RECEIVED -> PENDING, never COMPLETED


def test_T14_timeout_is_unknown_and_never_retried(conn, live):
    live.post_mode = "timeout"
    p = _approved(conn)
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert a["status"] == "UNKNOWN"
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert a["status"] == "UNKNOWN" and len(live.posts) == 1
    status.reconcile(conn, a["attempt_id"])  # provider has no record: stays UNKNOWN, never FAILED
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "UNKNOWN"
    live.set_status(a["transfer_id"], "SUCCESS", "COMPLETED")
    status.reconcile(conn, a["attempt_id"])
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "COMPLETED"
    assert set(live.gets) == {a["transfer_id"]}
    assert len({r["transfer_id"] for r in conn.execute("SELECT transfer_id FROM payment_attempts")}) == 1
    assert len(postings(conn, "SETTLEMENT")) == 1


def test_5xx_is_unknown(conn, live):
    live.post_mode = "500"
    a = execution.execute_approved_payout(conn, _approved(conn)["proposal_id"], "OP")
    assert a["status"] == "UNKNOWN"


def test_4xx_rejection_is_failed_without_transfer(conn, live):
    live.post_mode = "400"
    p = _approved(conn)
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert a["status"] == "FAILED" and "beneficiary_invalid" in a["last_error"]
    assert postings(conn) == []


def test_T15_webhook_replay_and_out_of_order(client, conn, live):
    p = _approved(conn)
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    tid = a["transfer_id"]
    done = webhook_body(tid, "SUCCESS", "COMPLETED")
    assert post_webhook(client, done).json()["outcome"] == "APPLIED"
    assert post_webhook(client, done).json()["outcome"] == "DUPLICATE_IGNORED"
    late = webhook_body(tid, "PENDING", None, event_type="TRANSFER_PENDING", event_time="2026-11-21T11:59:00")
    assert post_webhook(client, late).json()["outcome"] == "IGNORED_BACKWARD"
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "COMPLETED"
    assert len(postings(conn, "SETTLEMENT")) == 1
    ignored = db.rows(conn, "SELECT * FROM attempt_transitions WHERE applied=0")
    assert ignored and ignored[-1]["to_status"] == "PENDING"


def test_webhook_bad_signature_rejected(client, conn, live):
    a = execution.execute_approved_payout(conn, _approved(conn)["proposal_id"], "OP")
    r = post_webhook(client, webhook_body(a["transfer_id"], "SUCCESS", "COMPLETED"), secret="wrong")
    assert r.status_code == 401
    assert postings(conn) == []


def test_webhook_unknown_transfer_unmatched(client, live):
    r = post_webhook(client, webhook_body("NPT_doesnotexist", "SUCCESS", "COMPLETED"))
    assert r.status_code == 200 and r.json()["outcome"] == "UNMATCHED"


def test_T16_failure_no_posting_and_new_proposal_allowed(client, conn, live):
    p = _approved(conn)
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    post_webhook(client, webhook_body(a["transfer_id"], "FAILED", "INVALID_ACCOUNT", event_type="TRANSFER_FAILED"))
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "FAILED"
    assert postings(conn) == []
    assert approval.get_proposal(conn, p["proposal_id"])["state"] == "FINISHED"
    p2 = approval.create_proposal(conn, "INV-E", "OP")
    assert p2["proposal_id"] != p["proposal_id"]


def test_T17_reversal_after_success(client, conn, live):
    a = execution.execute_approved_payout(conn, _approved(conn)["proposal_id"], "OP")
    post_webhook(client, webhook_body(a["transfer_id"], "SUCCESS", "COMPLETED"))
    post_webhook(client, webhook_body(a["transfer_id"], "REVERSED", "ACCOUNT_CLOSED",
                                      event_type="TRANSFER_REVERSED", event_time="2026-11-22T09:00:00"))
    s = postings(conn, "SETTLEMENT")
    r = postings(conn, "REVERSAL")
    assert len(s) == 1 and len(r) == 1
    assert r[0]["amount_paise"] == -320000 and r[0]["reversal_of"] == s[0]["posting_id"]
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "REVERSED"
    assert "PAYMENT_REVERSED" in findings(conn, "INV-E") and decision(conn, "INV-E") == "REVIEW"
    pipeline.run_checks(conn, "INV-E")  # re-running checks keeps the reversal finding
    assert "PAYMENT_REVERSED" in findings(conn, "INV-E")
    assert len(live.posts) == 1  # nothing re-paid


def test_reversed_before_completed_is_failed(client, conn, live):
    a = execution.execute_approved_payout(conn, _approved(conn)["proposal_id"], "OP")
    post_webhook(client, webhook_body(a["transfer_id"], "REVERSED", None, event_type="TRANSFER_REVERSED"))
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "FAILED"
    assert postings(conn) == []


def test_T21_sent_to_beneficiary_is_not_completed(client, conn, live):
    live.accept_status = ("SUCCESS", "SENT_TO_BENEFICIARY")
    a = execution.execute_approved_payout(conn, _approved(conn)["proposal_id"], "OP")
    assert a["status"] == "PENDING"
    post_webhook(client, webhook_body(a["transfer_id"], "SUCCESS", "SENT_TO_BENEFICIARY"))
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "PENDING"
    assert postings(conn) == []
    view = client.get(f"/api/proposals/{a['proposal_id']}/status").json()
    assert view["ledger_posted"] is False and "COMPLETED" != view["attempt"]["status"]


def test_payouts_disabled_is_simulated(conn, cashfree):
    p = _approved(conn)
    assert p["snapshot"]["dispatch_mode"] == "SIMULATED"
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert a["status"] == "SIMULATED" and cashfree.posts == [] and postings(conn) == []


def test_test_provider_and_injection(client, conn):
    config.override(test_mode=True)
    p = client.post("/api/invoices/INV-E/proposal").json()
    assert p["dispatch_mode"] == "TEST_PROVIDER"
    client.post(f"/api/proposals/{p['proposal_id']}/approve", json={"snapshot_hash": p["snapshot_hash"]})
    v = client.post(f"/api/proposals/{p['proposal_id']}/execute").json()
    assert v["attempt"]["status"] == "PENDING"
    tid = v["attempt"]["transfer_id"]
    r = client.post("/api/dev/provider-event", json={"transfer_id": tid, "status": "SUCCESS",
                                                    "status_code": "COMPLETED"}).json()
    assert r["proposal"]["ledger_posted"] is True
    assert "Test provider" in r["proposal"]["status_text"]
    client.post("/api/dev/provider-event", json={"transfer_id": tid, "status": "SUCCESS", "status_code": "COMPLETED"})
    assert len(postings(conn, "SETTLEMENT")) == 1


def test_injection_refused_for_sandbox_attempt(client, conn, live):
    config.override(test_mode=True)
    a = execution.execute_approved_payout(conn, _approved(conn)["proposal_id"], "OP")
    r = client.post("/api/dev/provider-event", json={"transfer_id": a["transfer_id"], "status": "SUCCESS",
                                                    "status_code": "COMPLETED"})
    assert r.status_code == 403 and postings(conn) == []


def test_injection_route_hidden_outside_test_mode(client):
    r = client.post("/api/dev/provider-event", json={"transfer_id": "x", "status": "SUCCESS"})
    assert r.status_code == 404


def test_mutating_routes_need_session(client):
    client.headers.pop("Authorization")
    assert client.post("/api/invoices/INV-E/proposal").status_code == 401
    assert client.post("/api/proposals/x/approve", json={"snapshot_hash": "x"}).status_code == 401
    assert client.post("/api/proposals/x/execute").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_single_process_lock(settings):
    import subprocess
    import sys
    import textwrap

    from backend import runtime_lock

    runtime_lock.acquire(settings.database_path)
    try:
        runtime_lock.acquire(settings.database_path)  # re-entrant within one process
        code = textwrap.dedent(f"""
            from backend import runtime_lock
            try:
                runtime_lock.acquire({settings.database_path!r})
            except runtime_lock.AlreadyRunning:
                raise SystemExit(3)
        """)
        r = subprocess.run([sys.executable, "-c", code], cwd=str(__import__("pathlib").Path(__file__).parents[2]))
        assert r.returncode == 3  # a second process on the same database is refused
    finally:
        runtime_lock.release(settings.database_path)
