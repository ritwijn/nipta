"""Vendor approval: an authenticated operator adds a vendor and its saved bank destination.

Safety rules:
- Only an authenticated operator action creates a vendor or beneficiary. No agent tool or voice path can.
- The account number is typed twice by the operator; it is never copied from an invoice automatically.
  Invoice bank details remain a claim that the rules compare against the saved beneficiary.
- An account number already saved for another vendor is refused (a common payment-diversion pattern).
- A new beneficiary starts unverified: Secure ID verifies it when live, otherwise invoices stay in REVIEW
  (VERIFICATION_MISSING) until an operator resolves that finding with a recorded reason.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3

from pydantic import BaseModel, Field, field_validator

from . import db, pipeline
from .errors import NiptaError
from .rules import beneficiaries
from .rules import normalize as N

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


class VendorCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    account_holder_name: str = Field(min_length=2, max_length=120)
    bank_account_number: str
    bank_account_number_confirm: str
    ifsc: str
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    source_invoice_id: str | None = None

    @field_validator("bank_account_number", "bank_account_number_confirm")
    @classmethod
    def _digits(cls, v: str) -> str:
        digits = re.sub(r"[\s-]", "", N.to_ascii_digits(v or ""))
        if not re.fullmatch(r"\d{9,18}", digits):
            raise ValueError("account number must be 9 to 18 digits")
        return digits

    @field_validator("ifsc")
    @classmethod
    def _ifsc(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if not IFSC_RE.fullmatch(v):
            raise ValueError("IFSC must look like HDFC0001234 (4 letters, 0, 6 letters/digits)")
        return v

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        return [a.strip() for a in v if a and a.strip()]


def _vendor_id(name: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "-", N.to_ascii_digits(name).upper()).strip("-")[:24] or "VENDOR"
    return f"V-{slug}-{secrets.token_hex(2).upper()}"


def list_vendors(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for v in db.rows(conn, "SELECT * FROM vendors ORDER BY display_name"):
        ben = beneficiaries.active_beneficiary(conn, v["vendor_id"])
        ver = beneficiaries.latest_verification(conn, ben) if ben else None
        out.append({
            "vendor_id": v["vendor_id"],
            "display_name": v["display_name"],
            "aliases": json.loads(v["aliases_json"]),
            "payment_terms_days": v["payment_terms_days"],
            "beneficiary": {"beneficiary_id": ben["beneficiary_id"], "version": ben["version"],
                            "account": f"••{ben['account_last4']}", "ifsc": ben["ifsc"],
                            "holder": ben["account_holder_name"]} if ben else None,
            "verification": None if not ver else {
                "source": ver["source"], "account_status": ver["account_status"],
                "name_match_result": ver["name_match_result"],
                "label": "Live Secure ID result" if ver["source"] == "LIVE_SECURE_ID_BAV"
                         else "Seeded mock, not a Secure ID result"},
        })
    return out


def approve_vendor(conn: sqlite3.Connection, body: VendorCreate, operator: str) -> dict:
    if body.bank_account_number != body.bank_account_number_confirm:
        raise NiptaError("ACCOUNT_CONFIRMATION_MISMATCH", "The two account numbers do not match", 400)
    names = list(dict.fromkeys([body.display_name.strip(), *body.aliases]))
    source = None
    with db.immediate(conn):
        if body.source_invoice_id:
            source = db.row(conn, "SELECT invoice_id, vendor_id, vendor_name_raw, claimed_account, claimed_ifsc"
                                  " FROM invoices WHERE invoice_id=?", (body.source_invoice_id,))
            if source is None:
                raise NiptaError("NOT_FOUND", "Source invoice not found", 404)
            if source["vendor_id"]:
                raise NiptaError("VENDOR_ALREADY_RESOLVED", "That invoice already belongs to an approved vendor")
            if source["vendor_name_raw"] and source["vendor_name_raw"] not in names:
                names.append(source["vendor_name_raw"])  # so the invoice's own script resolves
        for n in names:
            existing, _ = beneficiaries.resolve_vendor(conn, n)
            if existing:
                raise NiptaError("VENDOR_EXISTS", f"\"{n}\" already matches approved vendor {existing}",
                                 detail={"vendor_id": existing})
        clash = db.row(conn, "SELECT b.vendor_id, v.display_name FROM beneficiaries b JOIN vendors v"
                             " ON v.vendor_id=b.vendor_id WHERE b.bank_account_number=? AND b.active=1",
                       (body.bank_account_number,))
        if clash:
            raise NiptaError("ACCOUNT_IN_USE", f"This account is already saved for {clash['display_name']}",
                             detail={"vendor_id": clash["vendor_id"]})
        vendor_id = _vendor_id(body.display_name)
        beneficiary_id = f"BEN-{secrets.token_hex(4).upper()}"
        conn.execute(
            "INSERT INTO vendors(vendor_id, display_name, aliases_json, statutory_risk, deadline_basis_verified,"
            " payment_terms_days) VALUES (?,?,?,0,0,?)",
            (vendor_id, body.display_name.strip(), json.dumps(names, ensure_ascii=False), body.payment_terms_days),
        )
        conn.execute(
            "INSERT INTO beneficiaries(beneficiary_id, version, vendor_id, account_holder_name, bank_account_number,"
            " account_last4, ifsc, active, created_at) VALUES (?,1,?,?,?,?,?,1,?)",
            (beneficiary_id, vendor_id, body.account_holder_name.strip(), body.bank_account_number,
             body.bank_account_number[-4:], body.ifsc, db.now()),
        )
        matches_invoice = None
        if source and source["claimed_account"]:
            claimed_last4 = N.last4(source["claimed_account"])
            matches_invoice = claimed_last4 == body.bank_account_number[-4:] and (
                not source["claimed_ifsc"] or source["claimed_ifsc"].upper() == body.ifsc)
        db.audit(conn, operator, "VENDOR_APPROVED", vendor_id, {
            "display_name": body.display_name, "aliases": names, "beneficiary_id": beneficiary_id,
            "account": f"••{body.bank_account_number[-4:]}", "ifsc": body.ifsc,
            "source_invoice_id": body.source_invoice_id, "matches_invoice_print": matches_invoice,
            "account_source": "MATCHES_INVOICE_PRINT" if matches_invoice else "OPERATOR_ENTERED",
            "note": "Printed bank details are a claim; the account starts unverified until Secure ID or a "
                    "recorded review confirms it.",
        })
    return {"vendor_id": vendor_id, "beneficiary_id": beneficiary_id, "matches_invoice_print": matches_invoice}


def recheck_unresolved(conn: sqlite3.Connection) -> list[dict]:
    """After a new vendor is approved, re-resolve and re-check every invoice that had no vendor."""
    changed = []
    for inv in db.rows(conn, "SELECT i.invoice_id, d.raw_json FROM invoices i JOIN documents d"
                             " ON d.document_id=i.document_id WHERE i.vendor_id IS NULL AND d.raw_json IS NOT NULL"):
        with db.immediate(conn):
            pipeline.write_invoice_fields(conn, inv["invoice_id"], pipeline.ExtractionRaw.model_validate_json(inv["raw_json"]))
        decision = pipeline.run_checks(conn, inv["invoice_id"])
        row = db.row(conn, "SELECT vendor_id FROM invoices WHERE invoice_id=?", (inv["invoice_id"],))
        if row["vendor_id"]:
            changed.append({"invoice_id": inv["invoice_id"], "vendor_id": row["vendor_id"], "decision": decision})
    return changed
