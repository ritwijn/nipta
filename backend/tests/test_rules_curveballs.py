"""Curveball regressions from the rules review: parsing, arithmetic, beneficiary, vendor and duplicate edge cases."""
from __future__ import annotations

import json

from datetime import date

from backend import db, pipeline
from backend.rules import beneficiaries as B
from backend.rules import normalize as N
from backend.rules.arithmetic import check_arithmetic
from backend.rules.model import normalize_extraction
from backend.schemas import ExtractionRaw

from .conftest import CURVEBALLS, add_invoice, curveball, decision, findings


def _arith(**fields) -> dict[str, dict]:
    raw = ExtractionRaw.model_validate({"invoice_number": "Z-1", **fields})
    return {f.code: f.evidence for f in check_arithmetic(normalize_extraction(raw))}


def _one_line(price: str, **fields) -> dict[str, dict]:
    return _arith(line_items=[{"description": "a", "quantity": "1", "unit_price": price, "line_total": price}],
                  **fields)


def _kaveri(number: str, invoice_date: str):
    """INV-A's vendor and bank details, new number/date/items so it is not a duplicate of INV-A."""
    raw = pipeline.sarvam.load_fixture_extraction("inv_a")
    raw.invoice_number, raw.invoice_date = number, invoice_date
    raw.line_items[0].description, raw.line_items[1].description = "washers", "split pins"
    return raw


def test_kannada_amount_in_words():
    assert N.words_to_paise("ನಾಲ್ಕು ಸಾವಿರ ಐದು ನೂರು ರೂಪಾಯಿ ಮಾತ್ರ") == 450000
    assert N.words_to_paise("ರೂ. ಇಪ್ಪತ್ತೈದು ಸಾವಿರದ ಐನೂರು ಮೂವತ್ತೆರಡು ರೂಪಾಯಿ ಮತ್ತು ಐವತ್ತು ಪೈಸೆ ಮಾತ್ರ") == 2553250
    assert N.words_to_paise("ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ಮೂರು ಸಾವಿರ") == 12300000
    assert N.words_to_paise("Rupees Eight Thousand Two Hundred & Fifty Paise Only") == 820050
    assert N.words_to_paise("रु. आठ हज़ार दो सौ मात्र") == 820000
    assert N.words_to_paise("₹ Four Thousand Only") == 400000
    assert N.words_to_paise("ನಾಲ್ಕು ಸಾವಿರ ಬ್ಲಾ") is None  # unknown word -> REVIEW, never a guess


def test_amount_only_and_rupees_words():
    for s in ("₹4,300 ONLY", "4300 rupees", "Rupees 4,300", "Rs 4,300 Only"):
        assert N.parse_amount_paise(s) == 430000, s


def test_month_name_dates():
    for s in ("21-Nov-26", "21-Nov-2026", "Nov 21, 2026", "21 Nov 2026", "21 November 2026"):
        assert N.parse_date(s) == date(2026, 11, 21), s
    assert N.parse_date("21 Nox 2026") is None and N.parse_date("31-Feb-2026") is None
    assert N.parse_date("21/11/2026") == date(2026, 11, 21)


def test_tax_inclusive_with_gst_lines_is_not_blocked():
    gst = [{"label": "CGST", "rate": "9%", "amount": "₹90.00"}, {"label": "SGST", "rate": "9%", "amount": "₹90.00"}]
    assert _one_line("₹1,180.00", subtotal="₹1,180.00", stated_total="₹1,180.00", tax_inclusive=True,
                     tax_lines=gst) == {}
    # Taxable value printed as the subtotal under inclusive line prices also adds up.
    assert _one_line("₹1,180.00", subtotal="₹1,000.00", stated_total="₹1,180.00", tax_inclusive=True,
                     tax_lines=gst) == {}
    # A wrong back-calculated tax on an inclusive bill is a review, not a block.
    gst[0]["amount"] = "₹100.00"
    gst[1]["amount"] = "₹80.00"
    f = _one_line("₹1,180.00", subtotal="₹1,180.00", stated_total="₹1,180.00", tax_inclusive=True, tax_lines=gst)
    assert set(f) == {"ARITHMETIC_UNVERIFIABLE"}


def test_mixed_tax_rates_check_only_the_total():
    lines = [{"description": "a", "quantity": "1", "unit_price": "1000", "line_total": "₹1,000.00"},
             {"description": "b", "quantity": "1", "unit_price": "1000", "line_total": "₹1,000.00"}]
    taxes = [{"label": "IGST", "rate": "18%", "amount": "₹180.00"}, {"label": "IGST", "rate": "5%", "amount": "₹50.00"}]
    assert _arith(line_items=lines, subtotal="₹2,000.00", stated_total="₹2,230.00", tax_lines=taxes) == {}
    f = _arith(line_items=lines, subtotal="₹2,000.00", stated_total="₹2,300.00", tax_lines=taxes)
    assert [m["rule"] for m in f["ARITHMETIC_MISMATCH"]["mismatches"]] == ["GRAND_TOTAL"]


def test_line_total_rounds_half_up_to_the_paisa():
    line = {"description": "a", "quantity": "1.5", "unit_price": "₹333.33", "line_total": "₹500.00"}
    assert _arith(line_items=[line], subtotal="₹500.00", stated_total="₹500.00") == {}
    line["line_total"] = "₹500.01"
    f = _arith(line_items=[line], stated_total="₹500.01")
    assert f["ARITHMETIC_MISMATCH"]["mismatches"][0]["computed_paise"] == 50000


def test_tax_rate_with_at_sign_is_checked_and_unreadable_rate_reviews():
    wrong = [{"label": "CGST", "rate": "@9%", "amount": "₹100.00"}, {"label": "SGST", "rate": "@ 9 %", "amount": "₹100.00"}]
    f = _one_line("₹1,000.00", subtotal="₹1,000.00", stated_total="₹1,200.00", tax_lines=wrong)
    assert {m["rule"] for m in f["ARITHMETIC_MISMATCH"]["mismatches"]} == {"TAX_RATE"}
    f = _one_line("₹1,000.00", subtotal="₹1,000.00", stated_total="₹1,090.00",
                  tax_lines=[{"label": "CGST", "rate": "nine percent", "amount": "₹90.00"}])
    assert set(f) == {"ARITHMETIC_UNVERIFIABLE"} and "nine percent" in f["ARITHMETIC_UNVERIFIABLE"]["reasons"][0]


def test_non_finite_tax_rate_does_not_crash(conn):
    for i, rate in enumerate(("NaN", "Infinity")):
        raw = curveball("gst_correct")
        raw.invoice_number = f"KF-27{i}"
        raw.tax_lines[0].rate = rate
        iid = add_invoice(conn, raw)
        assert decision(conn, iid) == "REVIEW" and "ARITHMETIC_UNVERIFIABLE" in findings(conn, iid)


def test_ifsc_spacing_and_fully_masked_account(conn):
    raw = _kaveri("KF-900", "01/01/2027")
    raw.ifsc = "hdfc 0001234"
    iid = add_invoice(conn, raw)
    assert decision(conn, iid) == "ELIGIBLE", findings(conn, iid)

    raw = _kaveri("KF-901", "01/02/2027")
    raw.bank_account = "XXXXXXXXXXXX"
    iid = add_invoice(conn, raw)
    f = findings(conn, iid)
    assert decision(conn, iid) == "REVIEW" and "CHANGED_BENEFICIARY" not in f
    assert f["DOCUMENT_INCOMPLETE"]["evidence"]["fields"][0]["field"] == "bank_account"

    raw = _kaveri("KF-902", "01/03/2027")
    raw.bank_account, raw.ifsc = "XXXXXXXXXX6655", "HDFC-0001234"
    iid = add_invoice(conn, raw)
    assert findings(conn, iid)["CHANGED_BENEFICIARY"]["evidence"]["differences"] == ["account_last4"]
    assert decision(conn, iid) == "BLOCKED"


def test_vendor_honorifics_and_company_suffixes(conn):
    for name in ("M/s Kaveri Fasteners", "M/S. Kaveri Fasteners", "Kaveri Fasteners (P) Ltd.",
                 "Kaveri Fasteners Pvt. Ltd."):
        assert B.resolve_vendor(conn, name)[0] == "V-KAVERI-FASTENERS", name
    assert B.resolve_vendor(conn, "Kaveri Bolts")[0] is None


def test_repeat_order_with_different_total_is_not_a_duplicate(conn):
    raw = pipeline.sarvam.load_fixture_extraction("inv_a")  # same items as KF-182, 4 days later
    raw.invoice_number, raw.invoice_date = "KF-190", "14/11/2026"
    raw.line_items[0].quantity, raw.line_items[0].line_total = "300", "₹3,000.00"
    raw.subtotal = raw.stated_total = "₹6,200.00"
    raw.amount_in_words = "Rupees Six Thousand Two Hundred Only"
    iid = add_invoice(conn, raw)
    assert "POSSIBLE_DUPLICATE" not in findings(conn, iid)
    assert decision(conn, iid) == "ELIGIBLE", findings(conn, iid)


def test_identical_structured_json_is_flagged_as_duplicate_upload(conn):
    raw = curveball("gst_correct")
    assert pipeline.structured_duplicate_of(conn, raw) is None
    first = add_invoice(conn, raw)
    assert pipeline.structured_duplicate_of(conn, curveball("gst_correct")) == first
    assert add_invoice(conn, curveball("gst_correct")) == first
    assert db.row(conn, "SELECT COUNT(*) AS n FROM invoices WHERE invoice_number_raw='KF-260'")["n"] == 1


def test_structured_resubmit_is_flagged_by_api(client):
    body = {"extraction": json.loads((CURVEBALLS / "gst_correct.json").read_text())}
    assert client.post("/api/invoices", json=body).json()["duplicate_upload"] is False
    assert client.post("/api/invoices", json=body).json()["duplicate_upload"] is True
