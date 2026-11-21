"""Approval snapshots: construction, canonical hash, approve, staleness (architecture section 6.1)."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .. import db
from ..config import get_settings
from ..errors import NiptaError
from ..rules.beneficiaries import active_beneficiary, latest_verification

RISK_CHECK_VERSION = "nipta-rules-2026-11-21.v4"
BLOCKING_ATTEMPT_STATES = ("REQUEST_RESERVED", "INITIATED", "PENDING", "UNKNOWN", "COMPLETED")


def snapshot_hash(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def dispatch_mode_for(verification_source: str | None) -> str:
    s = get_settings()
    if s.payouts_live and (verification_source == "LIVE_SECURE_ID_BAV" or not s.require_live_verification):
        return "CASHFREE_SANDBOX"
    if not s.payouts_live and s.test_mode:
        return "TEST_PROVIDER"
    return "SIMULATED"


def open_findings_digest(conn: sqlite3.Connection, invoice_id: str) -> str:
    items = sorted(
        f"{r['code']}|{r['severity']}|{r['matched_invoice_id'] or ''}"
        for r in conn.execute("SELECT code, severity, matched_invoice_id FROM findings"
                              " WHERE invoice_id=? AND status='OPEN'", (invoice_id,))
    )
    return hashlib.sha256("\n".join(items).encode()).hexdigest()


def build_snapshot(conn: sqlite3.Connection, invoice_id: str, proposal_id: str, expires_at: int) -> dict:
    inv = db.row(conn, "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,))
    if inv is None:
        raise NiptaError("NOT_FOUND", f"Invoice {invoice_id} not found", 404)
    doc = db.row(conn, "SELECT sha256 FROM documents WHERE document_id=?", (inv["document_id"],))
    ben = active_beneficiary(conn, inv["vendor_id"]) if inv["vendor_id"] else None
    ver = latest_verification(conn, ben) if ben else None
    return {
        "proposal_id": proposal_id,
        "buyer_id": get_settings().buyer_id,
        "invoice_id": invoice_id,
        "invoice_version": inv["version"],
        "document_sha256": doc["sha256"] if doc else None,
        "vendor_id": inv["vendor_id"],
        "beneficiary_id": ben["beneficiary_id"] if ben else None,
        "beneficiary_version": ben["version"] if ben else None,
        "beneficiary_account_last4": ben["account_last4"] if ben else None,
        "amount_paise": inv["stated_total_paise"],
        "currency": "INR",
        "verification_ref": ver["verification_id"] if ver else None,
        "verification_source": ver["source"] if ver else None,
        "risk_check_version": RISK_CHECK_VERSION,
        "invoice_decision": inv["decision"],
        "open_findings_digest": open_findings_digest(conn, invoice_id),
        "dispatch_mode": dispatch_mode_for(ver["source"] if ver else None),
        "expires_at": expires_at,
    }


def create_proposal(conn: sqlite3.Connection, invoice_id: str, actor: str) -> dict:
    with db.immediate(conn):
        inv = db.row(conn, "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,))
        if inv is None:
            raise NiptaError("NOT_FOUND", f"Invoice {invoice_id} not found", 404)
        if inv["decision"] != "ELIGIBLE":
            raise NiptaError("NOT_ELIGIBLE", f"Invoice is {inv['decision']}; only ELIGIBLE invoices can be proposed")
        blocking = db.row(
            conn,
            f"SELECT attempt_id, transfer_id, status FROM payment_attempts WHERE invoice_id=?"
            f" AND status IN ({','.join('?' * len(BLOCKING_ATTEMPT_STATES))})",
            (invoice_id, *BLOCKING_ATTEMPT_STATES),
        )
        if blocking:
            raise NiptaError("PAYMENT_EXISTS",
                             f"A payment for this invoice is already {blocking['status']}", detail=blocking)
        ts = db.now()
        conn.execute(
            "UPDATE payment_proposals SET state='EXPIRED' WHERE invoice_id=? AND expires_at<?"
            " AND state IN ('READY_FOR_APPROVAL','AUTHORIZED')",
            (invoice_id, ts),
        )
        proposal_id = db.new_id("prop")
        expires_at = ts + get_settings().approval_ttl_seconds
        snapshot = build_snapshot(conn, invoice_id, proposal_id, expires_at)
        if snapshot["beneficiary_id"] is None or snapshot["amount_paise"] is None:
            raise NiptaError("NOT_ELIGIBLE", "No approved beneficiary or amount for this invoice")
        h = snapshot_hash(snapshot)
        try:
            conn.execute(
                "INSERT INTO payment_proposals(proposal_id, invoice_id, snapshot_json, snapshot_hash, state,"
                " expires_at, created_at) VALUES (?,?,?,?,?,?,?)",
                (proposal_id, invoice_id, json.dumps(snapshot, sort_keys=True), h, "READY_FOR_APPROVAL",
                 expires_at, ts),
            )
        except sqlite3.IntegrityError as exc:
            existing = db.row(conn, "SELECT proposal_id, state FROM payment_proposals WHERE invoice_id=? AND state IN"
                                    " ('READY_FOR_APPROVAL','AUTHORIZED','EXECUTED')", (invoice_id,))
            raise NiptaError("PROPOSAL_EXISTS", "An active proposal already exists for this invoice",
                             detail=existing or {}) from exc
        db.audit(conn, actor, "PROPOSAL_CREATED", proposal_id,
                 {"invoice_id": invoice_id, "snapshot_hash": h, "amount_paise": snapshot["amount_paise"],
                  "dispatch_mode": snapshot["dispatch_mode"]}, batch_id=inv["batch_id"])
    return get_proposal(conn, proposal_id)


def get_proposal(conn: sqlite3.Connection, proposal_id: str) -> dict:
    p = db.row(conn, "SELECT * FROM payment_proposals WHERE proposal_id=?", (proposal_id,))
    if p is None:
        raise NiptaError("NOT_FOUND", f"Proposal {proposal_id} not found", 404)
    p["snapshot"] = json.loads(p.pop("snapshot_json"))
    return p


def freshness_error(conn: sqlite3.Connection, proposal: dict) -> str | None:
    """Rebuild the snapshot from live rows. Returns APPROVAL_EXPIRED / APPROVAL_STALE or None."""
    if db.now() > proposal["expires_at"]:
        return "APPROVAL_EXPIRED"
    rebuilt = build_snapshot(conn, proposal["invoice_id"], proposal["proposal_id"], proposal["expires_at"])
    if snapshot_hash(rebuilt) != proposal["snapshot_hash"]:
        return "APPROVAL_STALE"
    return None


def _changed_fields(conn: sqlite3.Connection, proposal: dict) -> list[str]:
    stored = json.loads(proposal["snapshot_json"]) if "snapshot_json" in proposal else proposal["snapshot"]
    rebuilt = build_snapshot(conn, proposal["invoice_id"], proposal["proposal_id"], proposal["expires_at"])
    return sorted(k for k in set(stored) | set(rebuilt) if stored.get(k) != rebuilt.get(k))


def mark_unusable(conn: sqlite3.Connection, proposal: dict, code: str, actor: str) -> None:
    state = "EXPIRED" if code == "APPROVAL_EXPIRED" else "STALE"
    changed = _changed_fields(conn, proposal) if code == "APPROVAL_STALE" else []
    conn.execute("UPDATE payment_proposals SET state=? WHERE proposal_id=?", (state, proposal["proposal_id"]))
    db.audit(conn, actor, f"PROPOSAL_{state}", proposal["proposal_id"], {"code": code, "changed_fields": changed})


def approve(conn: sqlite3.Connection, proposal_id: str, operator_id: str, displayed_hash: str) -> dict:
    error: NiptaError | None = None
    with db.immediate(conn):
        p = db.row(conn, "SELECT * FROM payment_proposals WHERE proposal_id=?", (proposal_id,))
        if p is None:
            raise NiptaError("NOT_FOUND", f"Proposal {proposal_id} not found", 404)
        if p["state"] == "AUTHORIZED" and p["snapshot_hash"] == displayed_hash:
            pass  # idempotent repeat of the same approval
        elif p["state"] != "READY_FOR_APPROVAL":
            raise NiptaError("NOT_APPROVABLE", f"Proposal is {p['state']}")
        else:
            code = freshness_error(conn, p)
            if code is None and displayed_hash != p["snapshot_hash"]:
                code = "APPROVAL_STALE"
            if code:
                mark_unusable(conn, p, code, operator_id)
                error = NiptaError(code, "The approval no longer matches live data; create a fresh proposal"
                                   if code == "APPROVAL_STALE" else "The proposal expired; create a fresh one")
            else:
                conn.execute(
                    "UPDATE payment_proposals SET state='AUTHORIZED', approver_id=?, approved_at=? WHERE proposal_id=?",
                    (operator_id, db.now(), proposal_id),
                )
                db.audit(conn, operator_id, "PROPOSAL_APPROVED", proposal_id,
                         {"snapshot_hash": p["snapshot_hash"], "invoice_id": p["invoice_id"]})
    if error:
        raise error
    return get_proposal(conn, proposal_id)
