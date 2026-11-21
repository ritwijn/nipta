"""Architecture safety tests S01–S08."""
from __future__ import annotations

import threading

from backend import agent, db, pipeline
from backend.errors import NiptaError
from backend.payments import approval, execution, status

from .conftest import add_live_verification, findings, payouts_live, post_webhook, webhook_body


def _run_threads(fn, n=2):
    results, errors = [], []
    barrier = threading.Barrier(n)

    def worker():
        c = db.connect()
        try:
            barrier.wait()
            results.append(fn(c))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def _live(conn):
    payouts_live()
    add_live_verification(conn, "BEN-CP")
    add_live_verification(conn, "BEN-KF")


def test_S01_concurrent_proposals(conn):
    results, errors = _run_threads(lambda c: approval.create_proposal(c, "INV-E", "OP"))
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], NiptaError) and errors[0].code == "PROPOSAL_EXISTS"
    assert db.row(conn, "SELECT COUNT(*) AS n FROM payment_proposals")["n"] == 1


def test_S02_concurrent_execute(conn, cashfree):
    _live(conn)
    p = approval.create_proposal(conn, "INV-E", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    cashfree.post_delay = 0.2  # keep the first dispatch in flight while the other clicks arrive
    results, errors = _run_threads(lambda c: execution.execute_approved_payout(c, p["proposal_id"], "OP"), n=4)
    assert not errors
    assert len({r["attempt_id"] for r in results}) == 1
    assert len(cashfree.posts) == 1
    assert db.row(conn, "SELECT COUNT(*) AS n FROM payment_attempts")["n"] == 1


def test_S03_webhook_and_poll_race(client, conn, cashfree):
    _live(conn)
    p = approval.create_proposal(conn, "INV-E", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    cashfree.set_status(a["transfer_id"], "SUCCESS", "COMPLETED")
    raw = webhook_body(a["transfer_id"], "SUCCESS", "COMPLETED")

    def poll(c):
        return status.reconcile(c, a["attempt_id"])

    def hook(c):
        from backend.integrations.cashfree_payouts import sign_webhook
        return status.process_webhook(c, raw, "1", sign_webhook(raw, "1", "whsec_test"))

    barrier = threading.Barrier(2)
    out = []

    def run(fn):
        c = db.connect()
        barrier.wait()
        out.append(fn(c))
        c.close()

    ts = [threading.Thread(target=run, args=(f,)) for f in (poll, hook)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert db.row(conn, "SELECT COUNT(*) AS n FROM financial_postings WHERE kind='SETTLEMENT'")["n"] == 1
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "COMPLETED"


def test_S04_stop_after_reservation_before_dispatch(conn, cashfree):
    _live(conn)
    p = approval.create_proposal(conn, "INV-E", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    attempt, created = execution._reserve(conn, p["proposal_id"], "OP")  # process "stops" here
    assert created and attempt["status"] == "REQUEST_RESERVED" and attempt["dispatch_started_at"] is None
    status.recover_all(conn)  # startup recovery: lookup finds nothing, stays reserved
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "REQUEST_RESERVED"
    assert cashfree.gets == [attempt["transfer_id"]] and cashfree.posts == []
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")  # owner presses Execute again
    assert len(cashfree.posts) == 1 and cashfree.posts[0]["transfer_id"] == attempt["transfer_id"]
    assert a["status"] == "PENDING"


def test_S05_stop_after_dispatch_started(conn, cashfree):
    _live(conn)
    p = approval.create_proposal(conn, "INV-E", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    attempt, _ = execution._reserve(conn, p["proposal_id"], "OP")
    assert execution._claim_dispatch(conn, attempt["attempt_id"])  # crash happens after this commit
    cashfree.set_status(attempt["transfer_id"], "SUCCESS", "COMPLETED")  # the request did reach Cashfree
    status.recover_all(conn)
    a = db.row(conn, "SELECT * FROM payment_attempts")
    assert a["status"] == "COMPLETED" and a["transfer_id"] == attempt["transfer_id"]
    path = [r["to_status"] for r in conn.execute("SELECT to_status FROM attempt_transitions WHERE applied=1"
                                                 " ORDER BY transition_id")]
    assert path == ["REQUEST_RESERVED", "UNKNOWN", "COMPLETED"]
    assert cashfree.posts == []
    # Re-execute never sends anything for an attempt that left REQUEST_RESERVED.
    execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert cashfree.posts == []


def test_S05_not_found_stays_unknown(conn, cashfree):
    _live(conn)
    p = approval.create_proposal(conn, "INV-E", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    attempt, _ = execution._reserve(conn, p["proposal_id"], "OP")
    execution._claim_dispatch(conn, attempt["attempt_id"])
    status.recover_all(conn)
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "UNKNOWN"
    execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert cashfree.posts == []


def test_S06_candidate_duplicate_of_paid_invoice(client, conn, cashfree):
    _live(conn)
    p = approval.create_proposal(conn, "INV-A", "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    post_webhook(client, webhook_body(a["transfer_id"], "SUCCESS", "COMPLETED"))
    pipeline.run_checks(conn, "INV-C")
    f = findings(conn, "INV-C")["POSSIBLE_DUPLICATE"]
    assert f["severity"] == "REVIEW"
    assert f["evidence"]["payment"] == {"flag": "ALREADY_PAID", "transfer_id": a["transfer_id"],
                                        "status": "COMPLETED", "posting_id": f["evidence"]["payment"]["posting_id"]}


def test_S07_agent_call_cap(conn):
    plan = [{"name": "explain_findings", "arguments": {"invoice_id": i}}
            for i in ["INV-A", "INV-B", "INV-C", "INV-D", "INV-E", "INV-F", "INV-A", "INV-B"]]
    reply = agent.handle_message(conn, "OP", conn.batch_id, "explain everything", planned_calls=plan)
    assert len(reply["tool_calls"]) == 6 and reply["stopped_early"]
    assert "stopped early" in reply["reply"]
    assert db.row(conn, "SELECT COUNT(*) AS n FROM audit_events WHERE action='AGENT_CAP_REACHED'")["n"] == 1
    assert db.row(conn, "SELECT COUNT(*) AS n FROM audit_events WHERE action='AGENT_TOOL'")["n"] == 6


def test_S08_disallowed_tool_and_unknown_id(conn):
    before = db.rows(conn, "SELECT * FROM payment_proposals")
    plan = [
        {"name": "execute_approved_payout", "arguments": {"proposal_id": "prop_x"}},
        {"name": "approve", "arguments": {"proposal_id": "prop_x"}},
        {"name": "explain_findings", "arguments": {"invoice_id": "INV-ZZZ"}},
        {"name": "prepare_payment_proposal", "arguments": {"invoice_id": "INV-E", "amount_paise": 1}},
    ]
    reply = agent.handle_message(conn, "OP", conn.batch_id, "do it", planned_calls=plan)
    assert [c["outcome"] for c in reply["tool_calls"]] == ["REJECTED"] * 4
    assert [c["reason_codes"][0] for c in reply["tool_calls"]] == [
        "TOOL_NOT_ALLOWED", "TOOL_NOT_ALLOWED", "UNKNOWN_INVOICE", "INVALID_ARGUMENTS"]
    assert db.rows(conn, "SELECT * FROM payment_proposals") == before
    assert db.rows(conn, "SELECT * FROM payment_attempts") == []
