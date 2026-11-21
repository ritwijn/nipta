"""Provider status mapping, guarded transitions, postings, reconciliation and webhook processing."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .. import db
from ..integrations import cashfree_payouts as cf
from ..pipeline import recompute_decision

ALLOWED = {
    "REQUEST_RESERVED": {"INITIATED", "UNKNOWN", "FAILED"},
    "INITIATED": {"PENDING", "COMPLETED", "FAILED", "UNKNOWN"},
    "PENDING": {"COMPLETED", "FAILED", "UNKNOWN"},
    "UNKNOWN": {"PENDING", "COMPLETED", "FAILED"},
    "COMPLETED": {"REVERSED"},
}
TERMINAL = {"COMPLETED", "FAILED", "REVERSED", "SIMULATED"}
# COMPLETED is terminal for the UI but can still be REVERSED by the bank, so reconciliation keeps polling it.
FINAL = TERMINAL - {"COMPLETED"}
# Completed attempts younger than this are re-checked at startup to catch reversals without webhooks.
REVERSAL_WATCH_SECONDS = 7 * 86400
PENDING_STATUSES = {"RECEIVED", "QUEUED", "APPROVAL_PENDING", "VALIDATION_PENDING", "PENDING"}
FAILED_STATUSES = {"FAILED", "REJECTED", "MANUALLY_REJECTED"}
# Seconds a dispatch may legitimately be in flight before recovery treats it as interrupted.
DISPATCH_IN_FLIGHT_SECONDS = 45


def map_provider_status(status: str | None, status_code: str | None, current: str) -> str:
    st = (status or "").upper()
    code = (status_code or "").upper()
    if st in PENDING_STATUSES:
        return "PENDING"
    if st == "SUCCESS":
        if code == "COMPLETED":
            return "COMPLETED"
        return "PENDING"  # e.g. SENT_TO_BENEFICIARY: in flight, not yet credited
    if st in FAILED_STATUSES:
        return "FAILED"
    if st == "REVERSED":
        return "REVERSED" if current in ("COMPLETED", "REVERSED") else "FAILED"
    return "UNKNOWN"


def _attempt(conn: sqlite3.Connection, attempt_id: str) -> dict:
    a = db.row(conn, "SELECT * FROM payment_attempts WHERE attempt_id=?", (attempt_id,))
    if a is None:
        raise KeyError(attempt_id)
    return a


def _record(conn, attempt_id, frm, to, source, applied, evidence) -> None:
    conn.execute(
        "INSERT INTO attempt_transitions(attempt_id, from_status, to_status, source, applied, evidence_json,"
        " created_at) VALUES (?,?,?,?,?,?,?)",
        (attempt_id, frm, to, source, 1 if applied else 0, json.dumps(evidence, default=str), db.now()),
    )


def transition_locked(conn: sqlite3.Connection, attempt_id: str, to: str, source: str, evidence: dict) -> str:
    """Caller holds a write transaction. Returns APPLIED, NO_CHANGE or IGNORED_BACKWARD."""
    a = _attempt(conn, attempt_id)
    cur = a["status"]
    if to == cur:
        return "NO_CHANGE"
    if to not in ALLOWED.get(cur, set()):
        _record(conn, attempt_id, cur, to, source, False, {**evidence, "ignored": "transition not allowed"})
        db.audit(conn, "provider", "TRANSITION_IGNORED", attempt_id, {"from": cur, "to": to, "source": source})
        return "IGNORED_BACKWARD"
    conn.execute("UPDATE payment_attempts SET status=? WHERE attempt_id=?", (to, attempt_id))
    _record(conn, attempt_id, cur, to, source, True, evidence)
    db.audit(conn, "provider" if source in ("POLL", "WEBHOOK") else source.lower(), "ATTEMPT_TRANSITION",
             attempt_id, {"from": cur, "to": to, "source": source, "transfer_id": a["transfer_id"]})

    if to == "COMPLETED":
        try:
            conn.execute(
                "INSERT INTO financial_postings(posting_id, attempt_id, invoice_id, kind, amount_paise, created_at)"
                " VALUES (?,?,?,'SETTLEMENT',?,?)",
                (db.new_id("post"), attempt_id, a["invoice_id"], a["amount_paise"], db.now()),
            )
            db.audit(conn, "ledger", "SETTLEMENT_POSTED", attempt_id,
                     {"invoice_id": a["invoice_id"], "amount_paise": a["amount_paise"]})
        except sqlite3.IntegrityError:
            pass  # already posted; the unique index guarantees exactly one
    elif to == "REVERSED":
        settlement = db.row(conn, "SELECT * FROM financial_postings WHERE attempt_id=? AND kind='SETTLEMENT'",
                            (attempt_id,))
        if settlement:
            try:
                conn.execute(
                    "INSERT INTO financial_postings(posting_id, attempt_id, invoice_id, kind, amount_paise,"
                    " reversal_of, created_at) VALUES (?,?,?,'REVERSAL',?,?,?)",
                    (db.new_id("post"), attempt_id, a["invoice_id"], -settlement["amount_paise"],
                     settlement["posting_id"], db.now()),
                )
            except sqlite3.IntegrityError:
                pass
        conn.execute(
            "INSERT INTO findings(finding_id, invoice_id, code, severity, evidence_json, created_at)"
            " VALUES (?,?,'PAYMENT_REVERSED','REVIEW',?,?)",
            (db.new_id("fnd"), a["invoice_id"],
             json.dumps({"transfer_id": a["transfer_id"], "attempt_id": attempt_id,
                         "note": "Bank reversed a completed payout. Nothing is re-paid automatically."}), db.now()),
        )
        recompute_decision(conn, a["invoice_id"])
        conn.execute("UPDATE payment_proposals SET state='FINISHED' WHERE proposal_id=?", (a["proposal_id"],))
        db.audit(conn, "ledger", "REVERSAL_POSTED", attempt_id, {"invoice_id": a["invoice_id"]})
    elif to == "FAILED":
        conn.execute("UPDATE payment_proposals SET state='FINISHED' WHERE proposal_id=?", (a["proposal_id"],))
    return "APPLIED"


def apply_provider_result_locked(conn: sqlite3.Connection, attempt_id: str, status: str | None,
                                 status_code: str | None, source: str, evidence: dict,
                                 cf_transfer_id: str | None = None) -> str:
    a = _attempt(conn, attempt_id)
    conn.execute(
        "UPDATE payment_attempts SET last_checked_at=?, cf_transfer_id=COALESCE(?, cf_transfer_id) WHERE attempt_id=?",
        (db.now(), cf_transfer_id, attempt_id),
    )
    target = map_provider_status(status, status_code, a["status"])
    ev = {**evidence, "provider_status": status, "provider_status_code": status_code}
    if a["status"] == "REQUEST_RESERVED":
        # A provider record exists, so the request was accepted before it moved anywhere else.
        transition_locked(conn, attempt_id, "INITIATED", source, {**ev, "note": "provider accepted"})
        if target == "INITIATED":
            result = "APPLIED"
        else:
            result = transition_locked(conn, attempt_id, target, source, ev)
    else:
        result = transition_locked(conn, attempt_id, target, source, ev)
    if result != "IGNORED_BACKWARD":
        # A late, out-of-order event must not overwrite the provider status that moved the attempt.
        conn.execute("UPDATE payment_attempts SET last_provider_status=?, last_provider_code=? WHERE attempt_id=?",
                     (status, status_code, attempt_id))
        raw = evidence.get("raw")
        if isinstance(raw, dict) and raw:
            # Merge so fields that appear only on some responses (e.g. the bank UTR) are kept.
            current = db.row(conn, "SELECT provider_details_json FROM payment_attempts WHERE attempt_id=?",
                             (attempt_id,))["provider_details_json"]
            merged = {**(json.loads(current) if current else {}), **raw}
            conn.execute("UPDATE payment_attempts SET provider_details_json=? WHERE attempt_id=?",
                         (json.dumps(merged, ensure_ascii=False, default=str), attempt_id))
    return result


def apply_provider_status(conn: sqlite3.Connection, attempt_id: str, status: str | None, status_code: str | None,
                          source: str, evidence: dict | None = None, cf_transfer_id: str | None = None) -> str:
    with db.immediate(conn):
        return apply_provider_result_locked(conn, attempt_id, status, status_code, source, evidence or {},
                                            cf_transfer_id)


def move_to(conn: sqlite3.Connection, attempt_id: str, to: str, source: str, evidence: dict) -> str:
    with db.immediate(conn):
        return transition_locked(conn, attempt_id, to, source, evidence)


def reconcile(conn: sqlite3.Connection, attempt_id: str, startup: bool = False) -> dict:
    """Bring a non-final Cashfree attempt (incl. COMPLETED, for reversals) up to date. Never dispatches."""
    a = _attempt(conn, attempt_id)
    if a["mode"] != "CASHFREE_SANDBOX" or a["status"] in FINAL:
        return a
    if a["status"] == "REQUEST_RESERVED" and a["dispatch_started_at"] and a["response_recorded_at"] is None:
        if startup or db.now() - a["dispatch_started_at"] > DISPATCH_IN_FLIGHT_SECONDS:
            move_to(conn, attempt_id, "UNKNOWN", "RECOVERY",
                    {"reason": "dispatch started but no provider response was recorded"})
        else:
            return _attempt(conn, attempt_id)  # a dispatch is still legitimately in flight
    try:
        result = cf.get_transfer(a["transfer_id"])
    except (cf.ProviderUncertain, cf.ProviderRejected) as exc:
        conn.execute("UPDATE payment_attempts SET last_error=?, last_checked_at=? WHERE attempt_id=?",
                     (str(exc), db.now(), attempt_id))
        return _attempt(conn, attempt_id)
    if result is None:
        conn.execute("UPDATE payment_attempts SET last_checked_at=? WHERE attempt_id=?", (db.now(), attempt_id))
        a = _attempt(conn, attempt_id)
        if a["status"] in ("INITIATED", "PENDING"):
            move_to(conn, attempt_id, "UNKNOWN", "POLL", {"reason": "provider has no record of an accepted transfer"})
        # REQUEST_RESERVED (never sent) and UNKNOWN stay as they are: "not found" is never a failure.
        return _attempt(conn, attempt_id)
    apply_provider_status(conn, attempt_id, result.status, result.status_code,
                          "RECOVERY" if a["status"] == "REQUEST_RESERVED" else "POLL",
                          {"raw": result.raw}, result.cf_transfer_id)
    return _attempt(conn, attempt_id)


def recover_all(conn: sqlite3.Connection) -> int:
    ids = [r["attempt_id"] for r in conn.execute(
        "SELECT attempt_id FROM payment_attempts WHERE mode='CASHFREE_SANDBOX'"
        " AND (status IN ('REQUEST_RESERVED','INITIATED','PENDING','UNKNOWN')"
        "      OR (status='COMPLETED' AND created_at>?))", (db.now() - REVERSAL_WATCH_SECONDS,))]
    for attempt_id in ids:
        reconcile(conn, attempt_id, startup=True)
    return len(ids)


# ---- webhooks --------------------------------------------------------------


def dedupe_key(body: dict) -> str:
    data = body.get("data") or {}
    parts = [body.get("type"), data.get("transfer_id"), data.get("status"), data.get("status_code"),
             body.get("event_time")]
    return hashlib.sha256("|".join(str(p or "") for p in parts).encode()).hexdigest()


def process_webhook(conn: sqlite3.Connection, raw_body: bytes, timestamp: str | None,
                    signature: str | None) -> tuple[int, dict]:
    if not cf.verify_webhook(raw_body, timestamp, signature):
        db.audit(conn, "provider", "WEBHOOK_REJECTED", None, {"reason": "signature mismatch or missing"})
        return 401, {"outcome": "SIGNATURE_INVALID"}
    try:
        body = json.loads(raw_body)
    except ValueError:
        db.audit(conn, "provider", "WEBHOOK_REJECTED", None, {"reason": "invalid JSON"})
        return 200, {"outcome": "IGNORED_INVALID_JSON"}
    data = body.get("data") or {}
    transfer_id = data.get("transfer_id")
    key = dedupe_key(body)
    with db.immediate(conn):
        if db.row(conn, "SELECT 1 AS x FROM webhook_events WHERE dedupe_key=?", (key,)):
            return 200, {"outcome": "DUPLICATE_IGNORED"}
        attempt = db.row(conn, "SELECT attempt_id, mode FROM payment_attempts WHERE transfer_id=?", (transfer_id,))
        if attempt is None or attempt["mode"] != "CASHFREE_SANDBOX":
            outcome = "UNMATCHED"
        else:
            result = apply_provider_result_locked(
                conn, attempt["attempt_id"], data.get("status"), data.get("status_code"), "WEBHOOK",
                {"event_type": body.get("type"), "event_time": body.get("event_time"), "raw": cf.redact(data)},
                str(data["cf_transfer_id"]) if data.get("cf_transfer_id") is not None else None,
            )
            outcome = "IGNORED_BACKWARD" if result == "IGNORED_BACKWARD" else "APPLIED"
        conn.execute(
            "INSERT INTO webhook_events(dedupe_key, transfer_id, event_type, raw_body, outcome, received_at)"
            " VALUES (?,?,?,?,?,?)",
            (key, transfer_id, body.get("type"), raw_body.decode(errors="replace"), outcome, db.now()),
        )
    return 200, {"outcome": outcome}
