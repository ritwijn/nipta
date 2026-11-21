"""Regressions from the payment-safety review: stale re-execute, reversal by polling, late-event provider fields."""
from __future__ import annotations

import pytest

from backend import db
from backend.errors import NiptaError
from backend.payments import approval, execution, status

from .conftest import add_live_verification, payouts_live


@pytest.fixture
def live(conn, cashfree):
    payouts_live()
    add_live_verification(conn, "BEN-CP")
    add_live_verification(conn, "BEN-KF")
    return cashfree


def _approved(conn, invoice_id="INV-E"):
    p = approval.create_proposal(conn, invoice_id, "OP")
    approval.approve(conn, p["proposal_id"], "OP", p["snapshot_hash"])
    return p


def _rotate_beneficiary(conn):
    with db.immediate(conn):
        conn.execute("UPDATE beneficiaries SET active=0 WHERE beneficiary_id='BEN-CP'")
        conn.execute("INSERT INTO beneficiaries(beneficiary_id, version, vendor_id, account_holder_name,"
                     " bank_account_number, account_last4, ifsc, active, created_at)"
                     " VALUES ('BEN-CP', 2, 'V-COIMBATORE-PACKING', 'COIMBATORE PACKING', '99998888777766',"
                     " '7766', 'CNRB0004567', 1, 0)")


@pytest.mark.parametrize("rotate, expire, code", [
    (True, True, "APPROVAL_EXPIRED"),
    (True, False, "APPROVAL_STALE"),
    (False, True, "APPROVAL_EXPIRED"),
])
def test_reexecute_of_unsent_reservation_rechecks_approval(conn, live, monkeypatch, rotate, expire, code):
    p = _approved(conn)
    attempt, created = execution._reserve(conn, p["proposal_id"], "OP")  # crash before the dispatch claim
    assert created and attempt["dispatch_started_at"] is None
    if rotate:
        _rotate_beneficiary(conn)
    if expire:
        real_now = db.now
        monkeypatch.setattr(db, "now", lambda: real_now() + 86400)
    with pytest.raises(NiptaError) as exc:
        execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert exc.value.code == code
    assert live.posts == []  # nothing sent to the deactivated account
    a = db.row(conn, "SELECT * FROM payment_attempts")
    assert a["status"] == "FAILED" and a["dispatch_started_at"] is None and "nothing sent" in a["last_error"]
    assert a["transfer_id"] == attempt["transfer_id"]
    assert db.rows(conn, "SELECT * FROM financial_postings") == []
    assert approval.get_proposal(conn, p["proposal_id"])["state"] == "FINISHED"  # frees the invoice
    # Pressing Execute again stays a no-op.
    assert execution.execute_approved_payout(conn, p["proposal_id"], "OP")["status"] == "FAILED"
    assert live.posts == []


def test_reexecute_of_unsent_fresh_reservation_still_dispatches_same_transfer_id(conn, live):
    p = _approved(conn)
    attempt, _ = execution._reserve(conn, p["proposal_id"], "OP")
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    assert [x["transfer_id"] for x in live.posts] == [attempt["transfer_id"]] and a["status"] == "PENDING"


def _completed(conn, live):
    p = _approved(conn)
    a = execution.execute_approved_payout(conn, p["proposal_id"], "OP")
    live.set_status(a["transfer_id"], "SUCCESS", "COMPLETED")
    assert status.reconcile(conn, a["attempt_id"])["status"] == "COMPLETED"
    return a


def _count(conn, kind):
    return db.row(conn, "SELECT COUNT(*) n FROM financial_postings WHERE kind=?", (kind,))["n"]


def test_polling_records_reversal_of_completed_attempt(conn, live):
    a = _completed(conn, live)
    live.set_status(a["transfer_id"], "REVERSED", "ACCOUNT_CLOSED")
    assert status.reconcile(conn, a["attempt_id"])["status"] == "REVERSED"
    status.reconcile(conn, a["attempt_id"])
    status.recover_all(conn)
    assert _count(conn, "SETTLEMENT") == 1 and _count(conn, "REVERSAL") == 1
    assert live.posts and len(live.posts) == 1


def test_recover_all_records_reversal_of_recent_completed_attempt(conn, live):
    a = _completed(conn, live)
    live.set_status(a["transfer_id"], "REVERSED", "ACCOUNT_CLOSED")
    status.recover_all(conn)
    assert db.row(conn, "SELECT status FROM payment_attempts")["status"] == "REVERSED"
    assert _count(conn, "REVERSAL") == 1


def test_recover_all_skips_old_completed_attempts(conn, live, monkeypatch):
    a = _completed(conn, live)
    live.set_status(a["transfer_id"], "REVERSED", "ACCOUNT_CLOSED")
    real_now = db.now
    monkeypatch.setattr(db, "now", lambda: real_now() + status.REVERSAL_WATCH_SECONDS + 60)
    n = len(live.gets)
    assert status.recover_all(conn) == 0 and len(live.gets) == n


def test_late_pending_does_not_overwrite_provider_fields(conn, live):
    a = _completed(conn, live)
    assert status.apply_provider_status(conn, a["attempt_id"], "PENDING", None, "WEBHOOK") == "IGNORED_BACKWARD"
    row = db.row(conn, "SELECT * FROM payment_attempts")
    assert row["status"] == "COMPLETED"
    assert (row["last_provider_status"], row["last_provider_code"]) == ("SUCCESS", "COMPLETED")
