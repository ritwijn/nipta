"""Exact duplicate detection and candidate scoring, with already-paid evidence."""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from ..schemas import Finding
from . import normalize as N

CANDIDATE_THRESHOLD = 6
IN_FLIGHT = ("REQUEST_RESERVED", "INITIATED", "PENDING", "UNKNOWN")


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def line_tokens(descriptions: list[str | None]) -> set[str]:
    tokens: set[str] = set()
    for d in descriptions:
        if not d:
            continue
        t = N.to_ascii_digits(d).casefold()
        tokens.update(tok for tok in re.split(r"[^\w]+", t) if tok)
    return tokens


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def numbers_related(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a in b or b in a or levenshtein(a, b) <= 2


def payment_evidence(conn: sqlite3.Connection, invoice_id: str) -> dict | None:
    """ALREADY_PAID / PAYMENT_IN_FLIGHT evidence for an invoice, if any."""
    posted = conn.execute(
        "SELECT p.posting_id, a.transfer_id, a.status FROM financial_postings p"
        " JOIN payment_attempts a ON a.attempt_id = p.attempt_id"
        " WHERE p.invoice_id=? AND p.kind='SETTLEMENT'"
        " AND NOT EXISTS (SELECT 1 FROM financial_postings r WHERE r.reversal_of = p.posting_id)",
        (invoice_id,),
    ).fetchone()
    if posted:
        return {"flag": "ALREADY_PAID", "transfer_id": posted["transfer_id"], "status": posted["status"],
                "posting_id": posted["posting_id"]}
    inflight = conn.execute(
        f"SELECT transfer_id, status FROM payment_attempts WHERE invoice_id=? AND status IN ({','.join('?' * len(IN_FLIGHT))})",
        (invoice_id, *IN_FLIGHT),
    ).fetchone()
    if inflight:
        return {"flag": "PAYMENT_IN_FLIGHT", "transfer_id": inflight["transfer_id"], "status": inflight["status"]}
    return None


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def check_duplicates(conn: sqlite3.Connection, inv: dict) -> list[Finding]:
    """`inv` is an invoices row. Only earlier-ingested invoices from the same vendor are compared."""
    if not inv.get("vendor_id"):
        return []
    findings: list[Finding] = []

    earlier = [dict(r) for r in conn.execute(
        "SELECT * FROM invoices WHERE vendor_id=? AND invoice_id<>? AND ingested_seq < ? ORDER BY ingested_seq",
        (inv["vendor_id"], inv["invoice_id"], inv["ingested_seq"]),
    )]

    # Exact match: same vendor and the same normalized invoice number.
    exact = [e for e in earlier if inv["invoice_number_norm"] and e["invoice_number_norm"] == inv["invoice_number_norm"]]
    for e in exact:
        ev = {"rule": "SAME_VENDOR_SAME_INVOICE_NUMBER", "matched_invoice_number": e["invoice_number_raw"],
              "invoice_number_norm": inv["invoice_number_norm"]}
        pay = payment_evidence(conn, e["invoice_id"])
        if pay:
            ev["payment"] = pay
        findings.append(Finding(code="CONFIRMED_DUPLICATE", severity="BLOCK", evidence=ev,
                                matched_invoice_id=e["invoice_id"]))
    exact_ids = {e["invoice_id"] for e in exact}

    my_tokens = line_tokens([r["description"] for r in conn.execute(
        "SELECT description FROM invoice_lines WHERE invoice_id=?", (inv["invoice_id"],))])
    my_date = _date(inv.get("invoice_date"))

    for e in earlier:
        if e["invoice_id"] in exact_ids:
            continue
        signals: list[dict] = []
        score = 0
        if inv["stated_total_paise"] is not None and inv["stated_total_paise"] == e["stated_total_paise"]:
            score += 3
            signals.append({"signal": "SAME_STATED_TOTAL", "points": 3, "amount_paise": e["stated_total_paise"]})
        their_tokens = line_tokens([r["description"] for r in conn.execute(
            "SELECT description FROM invoice_lines WHERE invoice_id=?", (e["invoice_id"],))])
        j = jaccard(my_tokens, their_tokens)
        if j >= 0.8:
            score += 3
            signals.append({"signal": "LINE_ITEMS_OVERLAP", "points": 3, "jaccard": round(j, 2)})
        if numbers_related(inv["invoice_number_norm"], e["invoice_number_norm"]):
            score += 2
            signals.append({"signal": "INVOICE_NUMBER_RELATED", "points": 2,
                            "numbers": [e["invoice_number_raw"], inv["invoice_number_raw"]]})
        their_date = _date(e.get("invoice_date"))
        if my_date and their_date and abs((my_date - their_date).days) <= 14:
            score += 1
            signals.append({"signal": "DATES_WITHIN_14_DAYS", "points": 1,
                            "dates": [e["invoice_date"], inv["invoice_date"]]})
        if score >= CANDIDATE_THRESHOLD:
            ev = {"score": score, "threshold": CANDIDATE_THRESHOLD, "signals": signals,
                  "matched_invoice_number": e["invoice_number_raw"]}
            pay = payment_evidence(conn, e["invoice_id"])
            if pay:
                ev["payment"] = pay
            findings.append(Finding(code="POSSIBLE_DUPLICATE", severity="REVIEW", evidence=ev,
                                    matched_invoice_id=e["invoice_id"]))
    return findings
