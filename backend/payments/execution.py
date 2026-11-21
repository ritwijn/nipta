"""Attempt reservation and dispatch. `execute_approved_payout` is the only function that moves money.

It is not an agent tool. It runs only for an unexpired, authenticated approval whose snapshot hash still
matches live data, and it never creates a second transfer_id for a proposal.
"""
from __future__ import annotations

import hashlib
import sqlite3

from .. import db
from ..config import get_settings
from ..errors import NiptaError
from ..integrations import cashfree_payouts as cf
from . import approval, status


def transfer_id_for(proposal_id: str) -> str:
    return "NPT_" + hashlib.sha256(proposal_id.encode()).hexdigest()[:24]


def _attempt_for_proposal(conn: sqlite3.Connection, proposal_id: str) -> dict | None:
    return db.row(conn, "SELECT * FROM payment_attempts WHERE proposal_id=?", (proposal_id,))


def _reserve(conn: sqlite3.Connection, proposal_id: str, operator_id: str) -> tuple[dict, bool]:
    """Returns (attempt, created). Raises NiptaError when the proposal cannot be executed."""
    error: NiptaError | None = None
    created = False
    with db.immediate(conn):
        existing = _attempt_for_proposal(conn, proposal_id)
        if existing:
            return existing, False
        p = db.row(conn, "SELECT * FROM payment_proposals WHERE proposal_id=?", (proposal_id,))
        if p is None:
            raise NiptaError("NOT_FOUND", f"Proposal {proposal_id} not found", 404)
        if p["state"] != "AUTHORIZED":
            raise NiptaError("NOT_AUTHORIZED", f"Proposal is {p['state']}; it must be approved first")
        code = approval.freshness_error(conn, p)
        if code:
            approval.mark_unusable(conn, p, code, operator_id)
            error = NiptaError(code, "The approval no longer matches live data; create a fresh proposal")
        else:
            snap = approval.get_proposal(conn, proposal_id)["snapshot"]
            mode = snap["dispatch_mode"]
            if mode == "CASHFREE_SANDBOX" and snap["verification_source"] != "LIVE_SECURE_ID_BAV" \
                    and get_settings().require_live_verification:
                raise NiptaError("LIVE_VERIFICATION_REQUIRED",
                                 "A real Cashfree payout needs a live Secure ID verification of the saved account")
            attempt_id = db.new_id("att")
            initial = "SIMULATED" if mode == "SIMULATED" else "REQUEST_RESERVED"
            try:
                conn.execute(
                    "INSERT INTO payment_attempts(attempt_id, proposal_id, invoice_id, transfer_id, amount_paise,"
                    " mode, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (attempt_id, proposal_id, p["invoice_id"], transfer_id_for(proposal_id), snap["amount_paise"],
                     mode, initial, db.now()),
                )
            except sqlite3.IntegrityError:
                return _attempt_for_proposal(conn, proposal_id), False  # type: ignore[return-value]
            created = True
            conn.execute("UPDATE payment_proposals SET state=? WHERE proposal_id=?",
                         ("FINISHED" if mode == "SIMULATED" else "EXECUTED", proposal_id))
            status._record(conn, attempt_id, None, initial, "EXECUTE", True,
                           {"mode": mode, "operator_id": operator_id})
            db.audit(conn, operator_id, "PAYMENT_RESERVED" if mode != "SIMULATED" else "PAYMENT_SIMULATED",
                     attempt_id, {"proposal_id": proposal_id, "transfer_id": transfer_id_for(proposal_id),
                                  "mode": mode, "amount_paise": snap["amount_paise"],
                                  "note": "Simulated: no Cashfree transfer was made" if mode == "SIMULATED" else None})
    if error:
        raise error
    return _attempt_for_proposal(conn, proposal_id), created  # type: ignore[return-value]


def _claim_dispatch(conn: sqlite3.Connection, attempt_id: str) -> bool:
    """Commit dispatch_started_at before any network I/O. Only one caller can win the claim.

    The approval is re-checked in the same transaction: an un-sent reservation can be resumed long after
    reserve, when the approval may have expired or the beneficiary rotated. Then nothing is sent and the
    attempt is FAILED so a fresh proposal can be made.
    """
    error: NiptaError | None = None
    with db.immediate(conn):
        a = status._attempt(conn, attempt_id)
        if a["status"] != "REQUEST_RESERVED" or a["dispatch_started_at"] is not None:
            return False
        p = db.row(conn, "SELECT * FROM payment_proposals WHERE proposal_id=?", (a["proposal_id"],))
        # The rebuild uses the vendor's active beneficiary, so a deactivated or rotated account is stale too.
        code = approval.freshness_error(conn, p)  # type: ignore[arg-type]
        if code is None:
            conn.execute("UPDATE payment_attempts SET dispatch_started_at=? WHERE attempt_id=?",
                         (db.now(), attempt_id))
            return True
        conn.execute("UPDATE payment_attempts SET last_error=? WHERE attempt_id=?",
                     (f"{code}: stale approval, nothing sent", attempt_id))
        status.transition_locked(conn, attempt_id, "FAILED", "EXECUTE",
                                 {"reason": "stale approval, nothing sent", "code": code})
        error = NiptaError(code, "The approval no longer matches live data; nothing was sent."
                                 " Create a fresh proposal")
    raise error


def _beneficiary_for(conn: sqlite3.Connection, attempt: dict) -> dict:
    snap = approval.get_proposal(conn, attempt["proposal_id"])["snapshot"]
    ben = db.row(conn, "SELECT * FROM beneficiaries WHERE beneficiary_id=? AND version=? AND active=1",
                 (snap["beneficiary_id"], snap["beneficiary_version"]))
    if ben is None:
        raise NiptaError("NOT_ELIGIBLE", "Snapshot beneficiary version is no longer active")
    return ben


def _dispatch(conn: sqlite3.Connection, attempt: dict) -> None:
    attempt_id = attempt["attempt_id"]
    if attempt["mode"] == "TEST_PROVIDER":
        conn.execute("UPDATE payment_attempts SET response_recorded_at=? WHERE attempt_id=?", (db.now(), attempt_id))
        status.apply_provider_status(conn, attempt_id, "RECEIVED", None, "EXECUTE",
                                     {"note": "TEST_PROVIDER stand-in accepted the request; no money moves"},
                                     cf_transfer_id=f"TEST-{attempt['transfer_id'][-8:]}")
        return
    ben = _beneficiary_for(conn, attempt)
    try:
        result = cf.create_transfer(attempt["transfer_id"], attempt["amount_paise"], ben)
    except cf.ProviderUncertain as exc:
        conn.execute("UPDATE payment_attempts SET last_error=? WHERE attempt_id=?", (str(exc), attempt_id))
        status.move_to(conn, attempt_id, "UNKNOWN", "EXECUTE",
                       {"reason": "dispatch outcome uncertain", "error": str(exc)})
        return
    except cf.ProviderDuplicateTransferId as exc:
        conn.execute("UPDATE payment_attempts SET last_error=? WHERE attempt_id=?",
                     (f"duplicate transfer_id: {exc}", attempt_id))
        status.move_to(conn, attempt_id, "UNKNOWN", "EXECUTE",
                       {"reason": "provider reports this transfer_id was already used; reconciling"})
        status.reconcile(conn, attempt_id)
        return
    except cf.ProviderRejected as exc:
        conn.execute("UPDATE payment_attempts SET last_error=?, response_recorded_at=? WHERE attempt_id=?",
                     (f"{exc.code}: {exc.message}", db.now(), attempt_id))
        status.move_to(conn, attempt_id, "FAILED", "EXECUTE",
                       {"reason": "provider rejected the request; no transfer created",
                        "http_status": exc.http_status, "code": exc.code, "message": exc.message})
        return
    conn.execute("UPDATE payment_attempts SET response_recorded_at=? WHERE attempt_id=?", (db.now(), attempt_id))
    status.apply_provider_status(conn, attempt_id, result.status, result.status_code, "EXECUTE",
                                 {"http_status": result.http_status, "raw": result.raw}, result.cf_transfer_id)


def execute_approved_payout(conn: sqlite3.Connection, proposal_id: str, operator_id: str) -> dict:
    attempt, created = _reserve(conn, proposal_id, operator_id)
    if attempt["mode"] == "SIMULATED":
        return attempt
    if attempt["status"] != "REQUEST_RESERVED" or attempt["dispatch_started_at"] is not None:
        return attempt  # already dispatched, or in flight: return the existing attempt, never re-send
    if not created and attempt["mode"] == "CASHFREE_SANDBOX":
        # Re-execute of a reservation that was never sent: check Cashfree first, then send the SAME transfer_id.
        attempt = status.reconcile(conn, attempt["attempt_id"])
        if attempt["status"] != "REQUEST_RESERVED":
            return attempt
    if not _claim_dispatch(conn, attempt["attempt_id"]):
        return _attempt_for_proposal(conn, proposal_id)  # type: ignore[return-value]
    _dispatch(conn, attempt)
    return _attempt_for_proposal(conn, proposal_id)  # type: ignore[return-value]
