"""Budget-constrained greedy ordering of ELIGIBLE invoices. A decision aid, not an optimal allocation."""
from __future__ import annotations

import sqlite3
from datetime import date

DISCLAIMER = ("Decision aid only: a greedy ordering within the budget, not an optimal allocation, "
              "and not legal or tax advice.")
ACTIVE_OR_DONE = ("REQUEST_RESERVED", "INITIATED", "PENDING", "UNKNOWN", "COMPLETED", "SIMULATED")


def payment_plan(conn: sqlite3.Connection, batch_id: str, cash_budget_paise: int, today: date | None = None) -> dict:
    today = today or date.today()
    invoices = [dict(r) for r in conn.execute(
        "SELECT i.*, v.display_name AS vendor_display_name, v.statutory_risk, v.deadline_basis_verified"
        " FROM invoices i LEFT JOIN vendors v ON v.vendor_id = i.vendor_id"
        " WHERE i.batch_id=? ORDER BY i.ingested_seq", (batch_id,))]

    candidates, excluded = [], []
    for inv in invoices:
        attempt = conn.execute(
            f"SELECT status FROM payment_attempts WHERE invoice_id=? AND status IN ({','.join('?' * len(ACTIVE_OR_DONE))})",
            (inv["invoice_id"], *ACTIVE_OR_DONE),
        ).fetchone()
        if inv["decision"] != "ELIGIBLE":
            codes = [r["code"] for r in conn.execute(
                "SELECT code FROM findings WHERE invoice_id=? AND status='OPEN' AND severity<>'INFO'",
                (inv["invoice_id"],))]
            excluded.append(_entry(inv, today, reason=f"{inv['decision']}: " + ", ".join(codes)))
        elif attempt:
            excluded.append(_entry(inv, today, reason=f"Payment already {attempt['status'].lower()}"))
        else:
            candidates.append(inv)

    def sort_key(inv: dict):
        statutory = 1 if (inv["statutory_risk"] and inv["deadline_basis_verified"]) else 0
        due = date.fromisoformat(inv["due_date"]) if inv["due_date"] else None
        overdue = (today - due).days if due and due < today else 0
        return (-statutory, -overdue, due or date.max, inv["invoice_id"])

    candidates.sort(key=sort_key)
    remaining = cash_budget_paise
    selected, skipped = [], []
    for rank, inv in enumerate(candidates, start=1):
        e = _entry(inv, today, rank=rank)
        if inv["stated_total_paise"] is not None and inv["stated_total_paise"] <= remaining:
            remaining -= inv["stated_total_paise"]
            e["remaining_after_paise"] = remaining
            selected.append(e)
        else:
            e["reason"] = "Exceeds remaining budget"
            e["remaining_budget_paise"] = remaining
            skipped.append(e)

    return {
        "batch_id": batch_id,
        "cash_budget_paise": cash_budget_paise,
        "remaining_paise": remaining,
        "as_of": today.isoformat(),
        "selected": selected,
        "skipped": skipped,
        "excluded": excluded,
        "disclaimer": DISCLAIMER,
    }


def _entry(inv: dict, today: date, rank: int | None = None, reason: str | None = None) -> dict:
    due = date.fromisoformat(inv["due_date"]) if inv["due_date"] else None
    statutory_flag = bool(inv.get("statutory_risk"))
    basis_verified = bool(inv.get("deadline_basis_verified"))
    e = {
        "invoice_id": inv["invoice_id"],
        "vendor": inv.get("vendor_display_name") or inv.get("vendor_name_raw"),
        "invoice_number": inv["invoice_number_raw"],
        "amount_paise": inv["stated_total_paise"],
        "due_date": inv["due_date"],
        "overdue_days": (today - due).days if due and due < today else 0,
        "deadline_note": (
            "Statutory supplier deadline applies (basis verified)" if statutory_flag and basis_verified
            else "Due-date basis unverified" if statutory_flag
            else None
        ),
    }
    if rank is not None:
        e["rank"] = rank
    if reason:
        e["reason"] = reason
    return e
