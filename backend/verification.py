"""Cashfree Secure ID: automatic checks of saved accounts, and person-approved bank account changes.

Bank details printed on an invoice never change where money goes. A change of account is entered by an
operator after confirming it with the firm, checked by Secure ID (account valid, name matches), and only
takes effect when an operator approves it. Approval creates a new saved-account version, so invoices that
still print the old details are blocked and any payment proposal built on the old version goes stale.
"""
from __future__ import annotations

import re
import sqlite3
import time

from . import db
from .config import get_settings
from .errors import NiptaError
from .integrations import cashfree_secureid
from .integrations.cashfree_payouts import ProviderRejected, ProviderUncertain
from .rules import beneficiaries
from .rules import normalize as N

GOOD_NAME_MATCH = ("DIRECT_MATCH", "GOOD_PARTIAL_MATCH")
FAILURE_COOLDOWN_S = 60  # after a Secure ID error, don't call again for this vendor on every invoice
_last_failure: dict[str, float] = {}

_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def needs_verification(conn: sqlite3.Connection, ben: dict, now: int, max_age_days: int) -> bool:
    ver = beneficiaries.latest_verification(conn, ben)
    return (ver is None or ver["source"] != "LIVE_SECURE_ID_BAV"
            or now - ver["verified_at"] > max_age_days * 86400)


def record_verification(conn: sqlite3.Connection, ben: dict, r: cashfree_secureid.VerificationResult,
                        actor: str, verified_at: int | None = None) -> None:
    conn.execute(
        "INSERT INTO beneficiary_verifications(verification_id, beneficiary_id, beneficiary_version, source,"
        " account_status, name_match_result, name_at_bank, provider_ref, verified_at)"
        " VALUES (?,?,?,'LIVE_SECURE_ID_BAV',?,?,?,?,?)",
        (db.new_id("ver"), ben["beneficiary_id"], ben["version"], r.account_status, r.name_match_result,
         r.name_at_bank, r.reference_id, verified_at or db.now()),
    )
    db.audit(conn, actor, "BENEFICIARY_VERIFIED", ben["vendor_id"],
             {"source": "LIVE_SECURE_ID_BAV", "beneficiary_id": ben["beneficiary_id"],
              "beneficiary_version": ben["version"], "account": "••" + ben["account_last4"],
              "account_status": r.account_status, "name_match_result": r.name_match_result})


def verify_saved_account(conn: sqlite3.Connection, vendor_id: str, actor: str) -> None:
    """Run Secure ID on the vendor's saved account now and store the result. Raises on provider errors."""
    ben = beneficiaries.active_beneficiary(conn, vendor_id)
    if ben is None:
        raise NiptaError("NO_APPROVED_BENEFICIARY", "Vendor has no saved beneficiary", 409)
    r = cashfree_secureid.verify_bank_account(ben["bank_account_number"], ben["ifsc"], ben["account_holder_name"])
    with db.immediate(conn):
        record_verification(conn, ben, r, actor)


def ensure_verified(conn: sqlite3.Connection, vendor_id: str | None) -> str:
    """Called before checks run. If Secure ID is on and the saved account has no recent live result, verify it.

    Returns OFF, NOT_NEEDED, VERIFIED or ERROR. Never raises: on an error the checks see no live result and
    the invoice goes to review (VERIFICATION_MISSING).
    """
    s = get_settings()
    if not vendor_id or not s.secureid_live:
        return "OFF"
    ben = beneficiaries.active_beneficiary(conn, vendor_id)
    if ben is None or not needs_verification(conn, ben, db.now(), s.verification_max_age_days):
        return "NOT_NEEDED"
    if time.monotonic() - _last_failure.get(vendor_id, -1e9) < FAILURE_COOLDOWN_S:
        return "ERROR"
    try:
        verify_saved_account(conn, vendor_id, "pipeline")
    except (ProviderUncertain, ProviderRejected) as exc:
        _last_failure[vendor_id] = time.monotonic()
        with db.immediate(conn):
            db.audit(conn, "pipeline", "BENEFICIARY_VERIFICATION_ERROR", vendor_id,
                     {"beneficiary_id": ben["beneficiary_id"], "error": str(exc)[:200]})
        return "ERROR"
    _last_failure.pop(vendor_id, None)
    return "VERIFIED"


# ---- person-approved bank account change ------------------------------------------------------------


def _public_request(r: dict) -> dict:
    return {
        "request_id": r["request_id"], "vendor_id": r["vendor_id"], "state": r["state"],
        "account": N.mask_account(r["bank_account_number"]), "ifsc": r["ifsc"],
        "account_holder_name": r["account_holder_name"], "confirmation_note": r["confirmation_note"],
        "account_status": r["account_status"], "name_match_result": r["name_match_result"],
        "name_at_bank": r["name_at_bank"], "verified_at": r["verified_at"],
        "requested_by": r["requested_by"], "decided_by": r["decided_by"], "decided_reason": r["decided_reason"],
        "approvable": r["state"] == "PENDING_APPROVAL",
    }


def list_requests(conn: sqlite3.Connection, vendor_id: str) -> list[dict]:
    return [_public_request(r) for r in db.rows(
        conn, "SELECT * FROM bank_change_requests WHERE vendor_id=? ORDER BY created_at DESC, rowid DESC",
        (vendor_id,))]


def request_change(conn: sqlite3.Connection, vendor_id: str, account_number: str, ifsc: str,
                   holder_name: str | None, confirmation_note: str, operator: str) -> dict:
    s = get_settings()
    if not s.secureid_live:
        raise NiptaError("SECURE_ID_UNAVAILABLE",
                         "Bank details can only be changed when Cashfree Secure ID is on, to check the name", 409)
    ben = beneficiaries.active_beneficiary(conn, vendor_id)
    if ben is None:
        raise NiptaError("NO_APPROVED_BENEFICIARY", "Vendor has no saved beneficiary", 409)
    account = re.sub(r"[\s-]", "", N.to_ascii_digits(account_number))
    if not re.fullmatch(r"\d{9,18}", account):
        raise NiptaError("INVALID_ACCOUNT", "Account number must be 9 to 18 digits", 400)
    ifsc = ifsc.strip().upper()
    if not _IFSC.fullmatch(ifsc):
        raise NiptaError("INVALID_IFSC", "IFSC must look like ABCD0123456", 400)
    holder = (holder_name or "").strip() or ben["account_holder_name"]
    if account == ben["bank_account_number"] and ifsc == ben["ifsc"].upper():
        raise NiptaError("NO_CHANGE", "These are already the saved bank details", 409)
    try:
        r = cashfree_secureid.verify_bank_account(account, ifsc, holder)
    except (ProviderUncertain, ProviderRejected) as exc:
        raise NiptaError("SECURE_ID_ERROR", f"Secure ID could not check these details: {exc}", 502)
    ok = r.account_status == "VALID" and r.name_match_result in GOOD_NAME_MATCH
    req_id = db.new_id("bcr")
    with db.immediate(conn):
        conn.execute(
            "INSERT INTO bank_change_requests(request_id, vendor_id, beneficiary_id, from_version,"
            " account_holder_name, bank_account_number, ifsc, confirmation_note, account_status, name_match_result,"
            " name_at_bank, provider_ref, verified_at, state, requested_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (req_id, vendor_id, ben["beneficiary_id"], ben["version"], holder, account, ifsc,
             confirmation_note.strip(), r.account_status, r.name_match_result, r.name_at_bank, r.reference_id,
             db.now(), "PENDING_APPROVAL" if ok else "VERIFICATION_FAILED", operator, db.now()),
        )
        db.audit(conn, operator, "BANK_CHANGE_REQUESTED", vendor_id,
                 {"request_id": req_id, "from": "••" + ben["account_last4"], "to": N.mask_account(account),
                  "ifsc": ifsc, "account_status": r.account_status, "name_match_result": r.name_match_result,
                  "confirmation_note": confirmation_note.strip()})
    return _public_request(db.row(conn, "SELECT * FROM bank_change_requests WHERE request_id=?", (req_id,)))


def _pending(conn: sqlite3.Connection, request_id: str) -> dict:
    r = db.row(conn, "SELECT * FROM bank_change_requests WHERE request_id=?", (request_id,))
    if r is None:
        raise NiptaError("NOT_FOUND", "Bank change request not found", 404)
    if r["state"] != "PENDING_APPROVAL":
        raise NiptaError("NOT_APPROVABLE", f"This request is {r['state']} and cannot be approved", 409)
    return r


def approve_change(conn: sqlite3.Connection, request_id: str, operator: str) -> dict:
    from . import pipeline

    s = get_settings()
    with db.immediate(conn):
        r = _pending(conn, request_id)
        ben = beneficiaries.active_beneficiary(conn, r["vendor_id"])
        if ben is None or ben["beneficiary_id"] != r["beneficiary_id"] or ben["version"] != r["from_version"]:
            raise NiptaError("REQUEST_STALE", "The saved account changed after this request; start a new one", 409)
        if db.now() - r["verified_at"] > s.verification_max_age_days * 86400:
            raise NiptaError("REQUEST_STALE", "The Secure ID check on this request is too old; start a new one", 409)
        new_version = ben["version"] + 1
        conn.execute("UPDATE beneficiaries SET active=0 WHERE vendor_id=? AND active=1", (r["vendor_id"],))
        conn.execute(
            "INSERT INTO beneficiaries(beneficiary_id, version, vendor_id, account_holder_name, bank_account_number,"
            " account_last4, ifsc, cashfree_beneficiary_id, active, created_at) VALUES (?,?,?,?,?,?,?,NULL,1,?)",
            (ben["beneficiary_id"], new_version, r["vendor_id"], r["account_holder_name"], r["bank_account_number"],
             N.last4(r["bank_account_number"]), r["ifsc"], db.now()),
        )
        new_ben = beneficiaries.active_beneficiary(conn, r["vendor_id"])
        result = cashfree_secureid.VerificationResult(r["account_status"], r["name_match_result"],
                                                      r["name_at_bank"], r["provider_ref"])
        record_verification(conn, new_ben, result, operator, verified_at=r["verified_at"])
        conn.execute("UPDATE bank_change_requests SET state='APPROVED', decided_by=?, decided_at=? WHERE request_id=?",
                     (operator, db.now(), request_id))
        db.audit(conn, operator, "BENEFICIARY_CHANGED", r["vendor_id"],
                 {"request_id": request_id, "beneficiary_id": ben["beneficiary_id"],
                  "from_version": ben["version"], "to_version": new_version,
                  "from": "••" + ben["account_last4"], "to": N.mask_account(r["bank_account_number"]),
                  "ifsc": r["ifsc"], "name_match_result": r["name_match_result"]})
    for inv in db.rows(conn, "SELECT invoice_id FROM invoices WHERE vendor_id=?", (r["vendor_id"],)):
        pipeline.run_checks(conn, inv["invoice_id"])
    return _public_request(db.row(conn, "SELECT * FROM bank_change_requests WHERE request_id=?", (request_id,)))


def reject_change(conn: sqlite3.Connection, request_id: str, operator: str, reason: str) -> dict:
    with db.immediate(conn):
        r = db.row(conn, "SELECT * FROM bank_change_requests WHERE request_id=?", (request_id,))
        if r is None:
            raise NiptaError("NOT_FOUND", "Bank change request not found", 404)
        if r["state"] in ("APPROVED", "REJECTED"):
            raise NiptaError("ALREADY_DECIDED", f"This request is already {r['state']}", 409)
        conn.execute("UPDATE bank_change_requests SET state='REJECTED', decided_by=?, decided_reason=?, decided_at=?"
                     " WHERE request_id=?", (operator, reason, db.now(), request_id))
        db.audit(conn, operator, "BANK_CHANGE_REJECTED", r["vendor_id"], {"request_id": request_id, "reason": reason})
    return _public_request(db.row(conn, "SELECT * FROM bank_change_requests WHERE request_id=?", (request_id,)))
