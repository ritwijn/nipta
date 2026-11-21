"""Vendor resolution, saved-beneficiary comparison and verification sufficiency.

Invoice bank details are a *claim*. They are compared with the saved beneficiary and never written to it.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..schemas import Finding
from . import normalize as N

_SUFFIXES = {"pvt", "ltd", "private", "limited", "llp", "co", "company", "inc", "प्रा", "लि", "ಪ್ರೈವೇಟ್", "ಲಿಮಿಟೆಡ್", "பிரைவேட்", "லிமிடெட்"}


def _vendor_key(name: str | None) -> str | None:
    norm = N.normalize_vendor_name(name)
    if not norm:
        return None
    tokens = [t for t in norm.split() if t not in _SUFFIXES]
    return " ".join(tokens) or None


def resolve_vendor(conn: sqlite3.Connection, vendor_name_raw: str | None) -> tuple[str | None, list[Finding]]:
    key = _vendor_key(vendor_name_raw)
    if key is None:
        return None, [Finding(code="VENDOR_UNRESOLVED", severity="REVIEW",
                              evidence={"vendor_name": vendor_name_raw, "reason": "Vendor name missing"})]
    matches: list[str] = []
    for v in conn.execute("SELECT vendor_id, display_name, aliases_json FROM vendors"):
        names = [v["display_name"], *json.loads(v["aliases_json"])]
        if any(_vendor_key(n) == key for n in names):
            matches.append(v["vendor_id"])
    if len(matches) == 1:
        return matches[0], []
    reason = "No approved vendor matches this name" if not matches else "More than one approved vendor matches"
    return None, [Finding(code="VENDOR_UNRESOLVED", severity="REVIEW",
                          evidence={"vendor_name": vendor_name_raw, "reason": reason, "candidates": matches})]


def active_beneficiary(conn: sqlite3.Connection, vendor_id: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM beneficiaries WHERE vendor_id=? AND active=1", (vendor_id,)
    ).fetchone()
    return dict(r) if r else None


def compare_beneficiary(conn: sqlite3.Connection, vendor_id: str, claimed_account: str | None,
                        claimed_ifsc: str | None) -> list[Finding]:
    ben = active_beneficiary(conn, vendor_id)
    if ben is None:
        return [Finding(code="NO_APPROVED_BENEFICIARY", severity="BLOCK",
                        evidence={"vendor_id": vendor_id, "reason": "No approved saved bank destination"})]
    if not claimed_account and not claimed_ifsc:
        return []  # T09: nothing printed, saved beneficiary stands

    differences: list[str] = []
    if claimed_account:
        claimed_digits = re.sub(r"[^0-9]", "", N.to_ascii_digits(claimed_account))
        if N.is_masked(claimed_account):
            if N.last4(claimed_account) != ben["account_last4"]:
                differences.append("account_last4")
        elif claimed_digits != ben["bank_account_number"]:
            differences.append("account_number")
    if claimed_ifsc and claimed_ifsc.upper() != ben["ifsc"].upper():
        differences.append("ifsc")

    if differences:
        return [Finding(
            code="CHANGED_BENEFICIARY",
            severity="BLOCK",
            evidence={
                "differences": differences,
                "printed_account": N.mask_account(claimed_account),
                "printed_ifsc": claimed_ifsc,
                "saved_account": "••" + ben["account_last4"],
                "saved_ifsc": ben["ifsc"],
                "beneficiary_id": ben["beneficiary_id"],
                "beneficiary_version": ben["version"],
                "note": "Printed bank details are a claim; the saved beneficiary was not changed.",
            },
        )]
    return []


def latest_verification(conn: sqlite3.Connection, ben: dict) -> dict | None:
    r = conn.execute(
        "SELECT * FROM beneficiary_verifications WHERE beneficiary_id=? AND beneficiary_version=?"
        " ORDER BY verified_at DESC, rowid DESC LIMIT 1",
        (ben["beneficiary_id"], ben["version"]),
    ).fetchone()
    return dict(r) if r else None


def check_verification(conn: sqlite3.Connection, vendor_id: str, now: int, max_age_days: int) -> list[Finding]:
    ben = active_beneficiary(conn, vendor_id)
    if ben is None:
        return []  # NO_APPROVED_BENEFICIARY already raised
    ver = latest_verification(conn, ben)
    base = {"beneficiary_id": ben["beneficiary_id"], "beneficiary_version": ben["version"]}
    if ver is None or now - ver["verified_at"] > max_age_days * 86400:
        return [Finding(code="VERIFICATION_MISSING", severity="REVIEW",
                        evidence={**base, "reason": "No verification" if ver is None else "Verification is stale",
                                  "max_age_days": max_age_days})]
    ev = {**base, "verification_id": ver["verification_id"], "source": ver["source"],
          "account_status": ver["account_status"], "name_match_result": ver["name_match_result"]}
    if ver["account_status"] == "INVALID":
        return [Finding(code="VERIFICATION_FAILED", severity="BLOCK", evidence=ev)]
    if ver["name_match_result"] in ("POOR_PARTIAL_MATCH", "NO_MATCH"):
        return [Finding(code="ACCOUNT_NAME_MISMATCH", severity="REVIEW", evidence={**ev, "strength": "strong"})]
    if ver["name_match_result"] == "MODERATE_PARTIAL_MATCH":
        return [Finding(code="ACCOUNT_NAME_MISMATCH", severity="REVIEW", evidence={**ev, "strength": "weak"})]
    if ver["account_status"] == "VALID" and ver["name_match_result"] in ("DIRECT_MATCH", "GOOD_PARTIAL_MATCH"):
        return []
    return [Finding(code="VERIFICATION_MISSING", severity="REVIEW",
                    evidence={**ev, "reason": "Verification result is inconclusive"})]
