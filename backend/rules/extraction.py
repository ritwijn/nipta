"""Extraction validation: missing or low-confidence fields become REVIEW findings, never guesses."""
from __future__ import annotations

from ..schemas import Finding
from .model import NormInvoice

MONETARY_FIELDS = ("stated_total", "subtotal", "line_items", "tax_lines", "amount_in_words", "bank_account")


def validate_extraction(inv: NormInvoice, min_confidence: float) -> list[Finding]:
    raw = inv.raw
    findings: list[Finding] = []
    missing: list[dict] = []

    if raw.stated_total is None:
        missing.append({"field": "stated_total", "problem": "absent"})
    elif inv.stated_total_paise is None:
        missing.append({"field": "stated_total", "problem": "unparsable", "raw": raw.stated_total})

    if raw.subtotal is not None and inv.subtotal_paise is None:
        missing.append({"field": "subtotal", "problem": "unparsable", "raw": raw.subtotal})

    if not inv.lines:
        missing.append({"field": "line_items", "problem": "absent"})
    for line in inv.lines:
        if line.line_total_raw is None:
            missing.append({"field": f"line_items[{line.position}].line_total", "problem": "absent"})
        elif line.line_total_paise is None:
            missing.append({"field": f"line_items[{line.position}].line_total", "problem": "unparsable",
                            "raw": line.line_total_raw})
        if line.quantity_raw is not None and line.quantity_milli is None:
            missing.append({"field": f"line_items[{line.position}].quantity", "problem": "unparsable",
                            "raw": line.quantity_raw})
        if line.unit_price_raw is not None and line.unit_price_paise is None:
            missing.append({"field": f"line_items[{line.position}].unit_price", "problem": "unparsable",
                            "raw": line.unit_price_raw})

    for i, tax in enumerate(inv.taxes, start=1):
        if tax.amount_paise is None:
            missing.append({"field": f"tax_lines[{i}].amount", "problem": "absent" if tax.amount_raw is None
                            else "unparsable", "raw": tax.amount_raw})

    if inv.invoice_number_norm is None:
        missing.append({"field": "invoice_number", "problem": "absent"})

    if missing:
        findings.append(Finding(code="DOCUMENT_INCOMPLETE", severity="REVIEW", evidence={"fields": missing}))

    low = {
        name: conf
        for name, conf in raw.field_confidence.items()
        if name.split("[")[0].split(".")[0] in MONETARY_FIELDS and conf < min_confidence
    }
    if low:
        findings.append(Finding(
            code="LOW_CONFIDENCE_FIELD",
            severity="REVIEW",
            evidence={"fields": low, "threshold": min_confidence},
        ))
    return findings
