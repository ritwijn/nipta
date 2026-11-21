"""Line, subtotal, tax and grand-total recomputation in integer paise; words versus figures."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..schemas import Finding
from .model import NormInvoice


def check_arithmetic(inv: NormInvoice, tolerance_paise: int = 0) -> list[Finding]:
    findings: list[Finding] = []
    mismatches: list[dict] = []
    unverifiable: list[str] = []

    # 1. Each line: qty x unit price = line total, the product rounded half-up to the paisa (1.5 x 333.33 = 500.00).
    for line in inv.lines:
        if line.quantity_milli is None or line.unit_price_paise is None or line.line_total_paise is None:
            continue
        product_milli = line.quantity_milli * line.unit_price_paise
        rounded = (product_milli + 500) // 1000
        if abs(rounded - line.line_total_paise) > tolerance_paise:
            mismatches.append({
                "rule": "LINE_TOTAL",
                "line": line.position,
                "description": line.description,
                "stated_paise": line.line_total_paise,
                "computed_paise": rounded,
                "computed_exact_milli_paise": product_milli,
            })

    line_totals = [l.line_total_paise for l in inv.lines]
    lines_complete = bool(line_totals) and all(v is not None for v in line_totals)
    lines_sum = sum(line_totals) if lines_complete else None  # type: ignore[arg-type]
    tax_sum = inv.tax_paise
    inclusive = bool(inv.raw.tax_inclusive and inv.taxes)

    # 2. Sum of lines = subtotal (when a subtotal is printed). A tax-inclusive bill may print the taxable value.
    if lines_sum is not None and inv.subtotal_paise is not None:
        taxable_printed = inclusive and tax_sum is not None and abs(lines_sum - tax_sum - inv.subtotal_paise) <= tolerance_paise
        if abs(lines_sum - inv.subtotal_paise) > tolerance_paise and not taxable_printed:
            mismatches.append({
                "rule": "SUBTOTAL",
                "stated_paise": inv.subtotal_paise,
                "computed_paise": lines_sum,
            })
    base = inv.subtotal_paise if inv.subtotal_paise is not None else lines_sum
    if inclusive and lines_sum is not None:
        base = lines_sum  # inclusive lines carry the gross amount
    if (inclusive and base is not None and tax_sum is not None and inv.stated_total_paise is not None
            and abs(base + tax_sum - inv.stated_total_paise) <= tolerance_paise):
        inclusive = False  # marked inclusive, but the printed figures add tax on top: check them as printed

    # 3. Tax basis.
    if inv.raw.tax_inclusive and not inv.taxes:
        unverifiable.append("Invoice is marked tax-inclusive but shows no tax line, so the tax basis is ambiguous.")
    if tax_sum is None:
        unverifiable.append("A tax line has no readable amount.")
    for i, tax in enumerate(inv.taxes, start=1):
        if tax.rate is None and tax.rate_raw and tax.rate_raw.strip():
            unverifiable.append(f"Tax line {i} rate {tax.rate_raw!r} could not be read.")
    # Per-line rate checks need one readable rate for every line (IGST 18%, or CGST 9% + SGST 9%): mixed rates
    # cannot be attributed to lines, so then only the grand total is checked.
    rates = {t.rate for t in inv.taxes if t.rate_raw and t.rate_raw.strip()}
    taxable = base
    if inclusive and base is not None:
        taxable = base - tax_sum if tax_sum is not None else None
    if taxable is not None and len(rates) == 1 and None not in rates:
        for i, tax in enumerate(inv.taxes, start=1):
            if tax.rate is None or tax.amount_paise is None:
                continue
            expected = int((Decimal(taxable) * tax.rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if abs(expected - tax.amount_paise) <= max(tolerance_paise, 1):
                continue
            if inclusive:  # back-calculated tax depends on the vendor's rounding: review, never block
                unverifiable.append(f"Tax line {i} ({tax.label}) is {tax.amount_paise} paise; {tax.rate}% of the "
                                    f"taxable value inside the inclusive total is {expected} paise.")
                continue
            mismatches.append({
                "rule": "TAX_RATE",
                "tax_line": i,
                "label": tax.label,
                "rate_percent": str(tax.rate),
                "stated_paise": tax.amount_paise,
                "computed_paise": expected,
            })

    # 4. Subtotal + tax = stated total (tax-inclusive: the lines already contain the tax).
    if base is None:
        unverifiable.append("Line totals are incomplete, so the total cannot be recomputed.")
    elif inv.stated_total_paise is None:
        unverifiable.append("The stated total is missing or unreadable.")
    elif tax_sum is not None and not (inv.raw.tax_inclusive and not inv.taxes):
        computed = base if inclusive else base + tax_sum
        if abs(computed - inv.stated_total_paise) > tolerance_paise:
            mismatches.append({
                "rule": "GRAND_TOTAL",
                "stated_paise": inv.stated_total_paise,
                "computed_paise": computed,
            })

    if mismatches:
        findings.append(Finding(
            code="ARITHMETIC_MISMATCH",
            severity="BLOCK",
            evidence={"mismatches": mismatches, "tolerance_paise": tolerance_paise},
        ))
    if unverifiable:
        findings.append(Finding(
            code="ARITHMETIC_UNVERIFIABLE",
            severity="REVIEW",
            evidence={"reasons": unverifiable},
        ))
    return findings


def check_words(inv: NormInvoice) -> list[Finding]:
    words = inv.raw.amount_in_words
    if not words:
        return []
    if inv.words_total_paise is None:
        return [Finding(
            code="WORDS_UNPARSABLE",
            severity="REVIEW",
            evidence={"amount_in_words": words, "stated_paise": inv.stated_total_paise},
        )]
    if inv.stated_total_paise is not None and inv.words_total_paise != inv.stated_total_paise:
        return [Finding(
            code="WORDS_FIGURES_MISMATCH",
            severity="BLOCK",
            evidence={
                "amount_in_words": words,
                "words_paise": inv.words_total_paise,
                "figures_paise": inv.stated_total_paise,
            },
        )]
    return []
