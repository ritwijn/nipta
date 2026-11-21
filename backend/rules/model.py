"""Parsed (normalized) view of a raw extraction. Built from stored raw JSON so checks can be re-run."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ..schemas import ExtractionRaw
from . import normalize as N


@dataclass
class NormLine:
    position: int
    description: str | None
    quantity_raw: str | None
    unit_price_raw: str | None
    line_total_raw: str | None
    quantity_milli: int | None
    unit_price_paise: int | None
    line_total_paise: int | None


@dataclass
class NormTax:
    label: str | None
    rate_raw: str | None
    amount_raw: str | None
    rate: Decimal | None
    amount_paise: int | None


@dataclass
class NormInvoice:
    raw: ExtractionRaw
    language: str | None
    vendor_name_raw: str | None
    invoice_number_raw: str | None
    invoice_number_norm: str | None
    invoice_date: date | None
    due_date: date | None
    lines: list[NormLine] = field(default_factory=list)
    taxes: list[NormTax] = field(default_factory=list)
    subtotal_paise: int | None = None
    stated_total_paise: int | None = None
    words_total_paise: int | None = None
    claimed_account: str | None = None
    claimed_ifsc: str | None = None
    notes: str | None = None

    @property
    def tax_paise(self) -> int | None:
        if any(t.amount_paise is None for t in self.taxes):
            return None
        return sum(t.amount_paise for t in self.taxes)  # type: ignore[misc]

    @property
    def computed_total_paise(self) -> int | None:
        if not self.lines or any(l.line_total_paise is None for l in self.lines):
            return None
        sub = sum(l.line_total_paise for l in self.lines)  # type: ignore[misc]
        tax = self.tax_paise
        return None if tax is None else sub + tax


def _parse_rate(s: str | None) -> Decimal | None:
    if not s:
        return None
    t = N.to_ascii_digits(s).replace("%", "").strip()
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def normalize_extraction(raw: ExtractionRaw) -> NormInvoice:
    lines = [
        NormLine(
            position=i + 1,
            description=li.description,
            quantity_raw=li.quantity,
            unit_price_raw=li.unit_price,
            line_total_raw=li.line_total,
            quantity_milli=N.parse_quantity_milli(li.quantity),
            unit_price_paise=N.parse_amount_paise(li.unit_price),
            line_total_paise=N.parse_amount_paise(li.line_total),
        )
        for i, li in enumerate(raw.line_items)
    ]
    taxes = [
        NormTax(
            label=t.label,
            rate_raw=t.rate,
            amount_raw=t.amount,
            rate=_parse_rate(t.rate),
            amount_paise=N.parse_amount_paise(t.amount),
        )
        for t in raw.tax_lines
    ]
    ifsc = raw.ifsc.strip().upper() if raw.ifsc else None
    return NormInvoice(
        raw=raw,
        language=raw.language,
        vendor_name_raw=raw.vendor_name,
        invoice_number_raw=raw.invoice_number,
        invoice_number_norm=N.normalize_invoice_number(raw.invoice_number),
        invoice_date=N.parse_date(raw.invoice_date),
        due_date=N.parse_date(raw.due_date),
        lines=lines,
        taxes=taxes,
        subtotal_paise=N.parse_amount_paise(raw.subtotal),
        stated_total_paise=N.parse_amount_paise(raw.stated_total),
        words_total_paise=N.words_to_paise(raw.amount_in_words),
        claimed_account=raw.bank_account.strip() if raw.bank_account else None,
        claimed_ifsc=ifsc,
        notes=raw.notes,
    )
