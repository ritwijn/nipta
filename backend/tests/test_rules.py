"""PRD tests T01–T10, T18–T20, T22 plus rule-level checks."""
from __future__ import annotations

from datetime import date

from backend import agent, db, pipeline
from backend.rules import normalize as N
from backend.rules.priority import payment_plan

from .conftest import add_invoice, add_live_verification, curveball, decision, findings


def test_fixture_batch_decisions(conn):
    got = {r["invoice_id"]: r["decision"] for r in conn.execute("SELECT invoice_id, decision FROM invoices")}
    assert got == {"INV-A": "ELIGIBLE", "INV-B": "BLOCKED", "INV-C": "REVIEW",
                   "INV-D": "BLOCKED", "INV-E": "ELIGIBLE", "INV-F": "REVIEW"}
    assert set(findings(conn, "INV-F")) == {"VENDOR_UNRESOLVED"}


def test_T01_clean_kannada_invoice(conn):
    inv = db.row(conn, "SELECT * FROM invoices WHERE invoice_id='INV-E'")
    assert inv["vendor_id"] == "V-COIMBATORE-PACKING"
    assert inv["stated_total_paise"] == 320000 and inv["computed_total_paise"] == 320000
    assert inv["invoice_date"] == "2026-11-05" and inv["decision"] == "ELIGIBLE"
    raw = pipeline.sarvam.load_fixture_extraction("inv_e")
    raw.stated_total = "೩,೨೦೦.?"  # an unreadable Kannada total
    raw.invoice_number = "CP-201-X"
    iid = add_invoice(conn, raw)
    f = findings(conn, iid)
    assert decision(conn, iid) == "REVIEW"
    assert any(x["field"] == "stated_total" and x["problem"] == "unparsable"
               for x in f["DOCUMENT_INCOMPLETE"]["evidence"]["fields"])


def test_T02_hindi_numerals_and_lakh(conn):
    assert N.parse_amount_paise("₹१,२०,०००.००") == 12_000_000
    assert N.parse_amount_paise("₹१.२ लाख") == 12_000_000
    assert N.parse_amount_paise("2.5 crore") == 2_500_000_000
    assert N.words_to_paise("एक लाख बीस हज़ार रुपये मात्र") == 12_000_000
    assert N.parse_amount_paise("₹೧,೨೦,೦೦೦.೫೦") == 12_000_050   # Kannada digits
    assert N.parse_amount_paise("೧.೨ ಲಕ್ಷ") == 12_000_000
    assert N.parse_amount_paise("ರೂ ೩,೨೦೦") == 320_000
    iid = add_invoice(conn, curveball("hindi_lakh"))
    inv = db.row(conn, "SELECT * FROM invoices WHERE invoice_id=?", (iid,))
    assert inv["stated_total_paise"] == inv["subtotal_paise"] == 12_000_000
    assert decision(conn, iid) == "ELIGIBLE", findings(conn, iid)


def test_T03_words_figures_mismatch(conn):
    iid = add_invoice(conn, curveball("words_mismatch"))
    f = findings(conn, iid)["WORDS_FIGURES_MISMATCH"]
    assert f["severity"] == "BLOCK"
    assert f["evidence"]["words_paise"] == 940000 and f["evidence"]["figures_paise"] == 490000
    assert decision(conn, iid) == "BLOCKED"


def test_T04_itemized_total_differs(conn):
    f = findings(conn, "INV-B")["ARITHMETIC_MISMATCH"]
    m = f["evidence"]["mismatches"][0]
    assert f["severity"] == "BLOCK" and m["stated_paise"] == 450000 and m["computed_paise"] == 430000


def test_T05_disguised_duplicate(conn):
    f = findings(conn, "INV-C")["POSSIBLE_DUPLICATE"]
    assert f["matched_invoice_id"] == "INV-A" and f["severity"] == "REVIEW"
    signals = {s["signal"] for s in f["evidence"]["signals"]}
    assert {"SAME_STATED_TOTAL", "LINE_ITEMS_OVERLAP", "INVOICE_NUMBER_RELATED"} <= signals
    assert "POSSIBLE_DUPLICATE" not in findings(conn, "INV-A")  # only the later one is flagged


def test_T06_same_amount_different_invoice(conn):
    iid = add_invoice(conn, curveball("same_amount_different"))
    assert decision(conn, iid) == "ELIGIBLE", findings(conn, iid)
    assert decision(conn, "INV-A") == "ELIGIBLE"


def test_exact_duplicate_blocked(conn):
    raw = pipeline.sarvam.load_fixture_extraction("inv_a")
    raw.notes = "resent copy"
    iid = add_invoice(conn, raw)
    f = findings(conn, iid)["CONFIRMED_DUPLICATE"]
    assert f["severity"] == "BLOCK" and f["matched_invoice_id"] == "INV-A"


def test_T07_changed_beneficiary(conn):
    before = db.rows(conn, "SELECT * FROM beneficiaries ORDER BY beneficiary_id")
    f = findings(conn, "INV-D")["CHANGED_BENEFICIARY"]
    assert f["severity"] == "BLOCK"
    assert f["evidence"]["printed_account"] == "••6655" and f["evidence"]["saved_account"] == "••1234"
    pipeline.run_checks(conn, "INV-D")
    assert db.rows(conn, "SELECT * FROM beneficiaries ORDER BY beneficiary_id") == before


def test_changed_beneficiary_not_lifted_by_good_verification(conn):
    add_live_verification(conn, "BEN-EH")
    pipeline.run_checks(conn, "INV-D")
    assert decision(conn, "INV-D") == "BLOCKED"


def test_T08_bank_name_mismatch(conn):
    add_live_verification(conn, "BEN-EH", name_match="POOR_PARTIAL_MATCH")
    iid = add_invoice(conn, curveball("omitted_bank"))
    f = findings(conn, iid)["ACCOUNT_NAME_MISMATCH"]
    assert f["severity"] == "REVIEW" and f["evidence"]["source"] == "LIVE_SECURE_ID_BAV"
    assert decision(conn, iid) == "REVIEW"


def test_verification_invalid_blocks(conn):
    add_live_verification(conn, "BEN-EH", account_status="INVALID", name_match="NO_MATCH")
    iid = add_invoice(conn, curveball("omitted_bank"))
    assert findings(conn, iid)["VERIFICATION_FAILED"]["severity"] == "BLOCK"


def test_T09_omitted_bank_details(conn):
    iid = add_invoice(conn, curveball("omitted_bank"))
    assert decision(conn, iid) == "ELIGIBLE", findings(conn, iid)


def test_T10_tax_inclusive_ambiguous(conn):
    iid = add_invoice(conn, curveball("tax_inclusive_ambiguous"))
    f = findings(conn, iid)
    assert "ARITHMETIC_UNVERIFIABLE" in f and "ARITHMETIC_MISMATCH" not in f
    assert decision(conn, iid) == "REVIEW"


def test_gst_arithmetic(conn):
    iid = add_invoice(conn, curveball("gst_correct"))
    assert decision(conn, iid) == "ELIGIBLE", findings(conn, iid)
    raw = curveball("gst_correct")
    raw.invoice_number = "KF-261"
    raw.tax_lines[1].amount = "₹540.00"
    raw.stated_total = "₹5,990.00"
    raw.amount_in_words = "Rupees Five Thousand Nine Hundred Ninety Only"
    iid2 = add_invoice(conn, raw)
    rules = {m["rule"] for m in findings(conn, iid2)["ARITHMETIC_MISMATCH"]["evidence"]["mismatches"]}
    assert rules == {"TAX_RATE"}


def test_T18_budget_ranking(conn):
    plan = payment_plan(conn, conn.batch_id, 900000, today=date(2026, 11, 21))
    assert [e["invoice_id"] for e in plan["selected"]] == ["INV-E"]
    assert [e["invoice_id"] for e in plan["skipped"]] == ["INV-A"]
    assert plan["skipped"][0]["remaining_budget_paise"] == 580000
    assert {e["invoice_id"] for e in plan["excluded"]} == {"INV-B", "INV-C", "INV-D", "INV-F"}
    assert "not an optimal allocation" in plan["disclaimer"]


def test_T19_unverified_deadline_basis(conn):
    plan = payment_plan(conn, conn.batch_id, 900000, today=date(2026, 11, 21))
    e = plan["selected"][0]
    assert e["invoice_id"] == "INV-E" and e["deadline_note"] == "Due-date basis unverified"
    # The unverified statutory flag never changes the order: with no overdue invoices, due date decides.
    plan2 = payment_plan(conn, conn.batch_id, 2_000_000, today=date(2026, 11, 1))
    assert [x["invoice_id"] for x in plan2["selected"]] == ["INV-E", "INV-A"]


def test_T20_prompt_injection_ignored(conn):
    before = db.rows(conn, "SELECT * FROM beneficiaries")
    iid = add_invoice(conn, curveball("prompt_injection"))
    assert decision(conn, iid) == "ELIGIBLE"
    inv = db.row(conn, "SELECT notes FROM invoices WHERE invoice_id=?", (iid,))
    assert "ignore previous rules" in inv["notes"]
    reply = agent.handle_message(conn, "OP", conn.batch_id, "explain KF-251")
    assert [c["tool"] for c in reply["tool_calls"]] == ["explain_findings"]
    assert db.rows(conn, "SELECT * FROM beneficiaries") == before
    assert not db.rows(conn, "SELECT * FROM payment_proposals")


def test_T22_unfamiliar_layout_devanagari_vendor(conn):
    iid = add_invoice(conn, curveball("devanagari_known_vendor"))
    inv = db.row(conn, "SELECT vendor_id FROM invoices WHERE invoice_id=?", (iid,))
    assert inv["vendor_id"] == "V-TAMIL-TOOLS"
    assert decision(conn, iid) == "ELIGIBLE", findings(conn, iid)


def test_T22_missing_fields_review_not_block(conn):
    raw = curveball("devanagari_known_vendor")
    raw.invoice_number = "TT-103"
    raw.line_items[0].line_total = None
    raw.amount_in_words = "மூவாயிரம் ரூபாய்"  # Tamil words are not parsed
    iid = add_invoice(conn, raw)
    f = findings(conn, iid)
    assert decision(conn, iid) == "REVIEW"
    assert all(x["severity"] == "REVIEW" for x in f.values())
    assert {"DOCUMENT_INCOMPLETE", "WORDS_UNPARSABLE"} <= set(f)


def test_low_confidence_field_review(conn):
    raw = curveball("omitted_bank")
    raw.invoice_number = "EH-060"
    raw.field_confidence = {"stated_total": 0.4}
    iid = add_invoice(conn, raw)
    assert findings(conn, iid)["LOW_CONFIDENCE_FIELD"]["evidence"]["fields"] == {"stated_total": 0.4}


def test_rerun_checks_idempotent_and_keeps_resolution(conn):
    fid = findings(conn, "INV-C")["POSSIBLE_DUPLICATE"]["finding_id"]
    with db.immediate(conn):
        conn.execute("UPDATE findings SET status='RESOLVED', resolved_by='OP', resolved_reason='checked' "
                     "WHERE finding_id=?", (fid,))
        pipeline.recompute_decision(conn, "INV-C")
    assert decision(conn, "INV-C") == "ELIGIBLE"
    pipeline.run_checks(conn, "INV-C")
    pipeline.run_checks(conn, "INV-C")
    assert decision(conn, "INV-C") == "ELIGIBLE"
    assert len(findings(conn, "INV-C", status="RESOLVED")) == 1
