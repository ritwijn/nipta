"""Fixed English, Tamil and Hindi templates filled only from stored state.

Every number, invoice ID, payee and status in user-facing text comes from here, never from model output.
"""
from __future__ import annotations

import json
import sqlite3

from . import db
from .rules.normalize import format_inr

DECISION = {
    "ELIGIBLE": {"en": "Eligible for payment", "kn": "ಪಾವತಿಗೆ ಅರ್ಹ", "hi": "भुगतान के योग्य"},
    "REVIEW": {"en": "Needs review", "kn": "ಪರಿಶೀಲನೆ ಅಗತ್ಯ", "hi": "समीक्षा आवश्यक"},
    "BLOCKED": {"en": "Blocked", "kn": "ತಡೆಹಿಡಿಯಲಾಗಿದೆ", "hi": "रोका गया"},
}

STATUS = {
    "REQUEST_RESERVED": {"en": "Payment reserved; not yet sent to Cashfree",
                         "kn": "ಪಾವತಿ ಕಾಯ್ದಿರಿಸಲಾಗಿದೆ; ಇನ್ನೂ Cashfree ಗೆ ಕಳುಹಿಸಿಲ್ಲ",
                         "hi": "भुगतान आरक्षित; अभी Cashfree को नहीं भेजा गया"},
    "INITIATED": {"en": "Cashfree accepted the transfer request",
                  "kn": "Cashfree ವರ್ಗಾವಣೆ ವಿನಂತಿಯನ್ನು ಸ್ವೀಕರಿಸಿದೆ",
                  "hi": "Cashfree ने ट्रांसफ़र अनुरोध स्वीकार किया"},
    "PENDING": {"en": "Transfer in progress; not yet confirmed as credited",
                "kn": "ವರ್ಗಾವಣೆ ಪ್ರಗತಿಯಲ್ಲಿದೆ; ಜಮಾ ಆಗಿರುವುದು ಇನ್ನೂ ದೃಢಪಟ್ಟಿಲ್ಲ",
                "hi": "ट्रांसफ़र जारी है; जमा होने की पुष्टि अभी नहीं हुई"},
    "COMPLETED": {"en": "Payment completed and confirmed by Cashfree",
                  "kn": "ಪಾವತಿ ಪೂರ್ಣಗೊಂಡಿದೆ, Cashfree ದೃಢಪಡಿಸಿದೆ",
                  "hi": "भुगतान पूरा हुआ, Cashfree ने पुष्टि की"},
    "FAILED": {"en": "Payment failed; no money was settled",
               "kn": "ಪಾವತಿ ವಿಫಲವಾಗಿದೆ; ಯಾವುದೇ ಹಣ ಹೋಗಿಲ್ಲ",
               "hi": "भुगतान विफल; कोई राशि नहीं गई"},
    "UNKNOWN": {"en": "Outcome not established; do not re-pay",
                "kn": "ಫಲಿತಾಂಶ ಖಚಿತವಾಗಿಲ್ಲ; ಮತ್ತೆ ಪಾವತಿಸಬೇಡಿ",
                "hi": "परिणाम स्पष्ट नहीं; दोबारा भुगतान न करें"},
    "REVERSED": {"en": "The bank reversed this payment; it needs review",
                 "kn": "ಬ್ಯಾಂಕ್ ಈ ಪಾವತಿಯನ್ನು ಹಿಂತಿರುಗಿಸಿದೆ; ಪರಿಶೀಲನೆ ಅಗತ್ಯ",
                 "hi": "बैंक ने यह भुगतान वापस कर दिया; समीक्षा आवश्यक"},
    "SIMULATED": {"en": "Simulated: no Cashfree transfer was made",
                  "kn": "ಅನುಕರಣೆ: ಯಾವುದೇ Cashfree ವರ್ಗಾವಣೆ ಮಾಡಲಾಗಿಲ್ಲ",
                  "hi": "सिम्युलेटेड: कोई Cashfree ट्रांसफ़र नहीं हुआ"},
}

FINDING = {
    "ARITHMETIC_MISMATCH": {
        "en": "Totals do not add up: stated {stated}, computed {computed}.",
        "kn": "ಮೊತ್ತಗಳು ಹೊಂದಿಕೆಯಾಗುತ್ತಿಲ್ಲ: ನಮೂದಿಸಿದ್ದು {stated}, ಲೆಕ್ಕಹಾಕಿದ್ದು {computed}.",
        "hi": "जोड़ मेल नहीं खाता: लिखा {stated}, गणना {computed}।"},
    "ARITHMETIC_UNVERIFIABLE": {
        "en": "The total could not be verified: {reason}",
        "kn": "ಒಟ್ಟು ಮೊತ್ತವನ್ನು ಪರಿಶೀಲಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ: {reason}",
        "hi": "कुल राशि सत्यापित नहीं हो सकी: {reason}"},
    "WORDS_FIGURES_MISMATCH": {
        "en": "Amount in words ({words}) does not match the figures ({figures}).",
        "kn": "ಅಕ್ಷರಗಳಲ್ಲಿನ ಮೊತ್ತ ({words}) ಅಂಕಿಗಳೊಂದಿಗೆ ({figures}) ಹೊಂದಿಕೆಯಾಗುತ್ತಿಲ್ಲ.",
        "hi": "शब्दों में राशि ({words}) अंकों ({figures}) से मेल नहीं खाती।"},
    "WORDS_UNPARSABLE": {
        "en": "The amount in words could not be read, so it was not compared with {figures}.",
        "kn": "ಅಕ್ಷರಗಳಲ್ಲಿನ ಮೊತ್ತವನ್ನು ಓದಲಾಗಲಿಲ್ಲ; {figures} ಜೊತೆ ಹೋಲಿಸಲಾಗಿಲ್ಲ.",
        "hi": "शब्दों में राशि पढ़ी नहीं जा सकी, इसलिए {figures} से तुलना नहीं हुई।"},
    "POSSIBLE_DUPLICATE": {
        "en": "Possible duplicate of {matched} (score {score}): {signals}.",
        "kn": "{matched} ನ ನಕಲು ಆಗಿರಬಹುದು (ಅಂಕ {score}): {signals}.",
        "hi": "{matched} की संभावित डुप्लिकेट (स्कोर {score}): {signals}।"},
    "CONFIRMED_DUPLICATE": {
        "en": "Same vendor and invoice number as {matched}.",
        "kn": "{matched} ನಂತೆಯೇ ಅದೇ ಮಾರಾಟಗಾರ ಮತ್ತು ಅದೇ ಇನ್‌ವಾಯ್ಸ್ ಸಂಖ್ಯೆ.",
        "hi": "{matched} जैसा ही विक्रेता और इनवॉइस नंबर।"},
    "CHANGED_BENEFICIARY": {
        "en": "Printed bank account {printed} differs from the saved account {saved}. The saved account was not changed.",
        "kn": "ಮುದ್ರಿತ ಬ್ಯಾಂಕ್ ಖಾತೆ {printed}, ಉಳಿಸಿದ ಖಾತೆ {saved} ಗಿಂತ ಭಿನ್ನವಾಗಿದೆ. ಉಳಿಸಿದ ಖಾತೆಯನ್ನು ಬದಲಾಯಿಸಲಾಗಿಲ್ಲ.",
        "hi": "छपा बैंक खाता {printed} सहेजे गए खाते {saved} से अलग है। सहेजा गया खाता नहीं बदला गया।"},
    "NO_APPROVED_BENEFICIARY": {
        "en": "This vendor has no approved bank account on file.",
        "kn": "ಈ ಮಾರಾಟಗಾರರಿಗೆ ಅನುಮೋದಿತ ಬ್ಯಾಂಕ್ ಖಾತೆ ದಾಖಲಾಗಿಲ್ಲ.",
        "hi": "इस विक्रेता का कोई स्वीकृत बैंक खाता दर्ज नहीं है।"},
    "VENDOR_UNRESOLVED": {
        "en": "Vendor \"{vendor}\" is not in the approved vendor list.",
        "kn": "ಮಾರಾಟಗಾರ \"{vendor}\" ಅನುಮೋದಿತ ಪಟ್ಟಿಯಲ್ಲಿ ಇಲ್ಲ.",
        "hi": "विक्रेता \"{vendor}\" स्वीकृत सूची में नहीं है।"},
    "VERIFICATION_MISSING": {
        "en": "The saved bank account has no current verification.",
        "kn": "ಉಳಿಸಿದ ಬ್ಯಾಂಕ್ ಖಾತೆಗೆ ಪ್ರಸ್ತುತ ಪರಿಶೀಲನೆ ಇಲ್ಲ.",
        "hi": "सहेजे गए बैंक खाते का कोई वर्तमान सत्यापन नहीं है।"},
    "VERIFICATION_FAILED": {
        "en": "Bank verification reports the saved account as invalid.",
        "kn": "ಬ್ಯಾಂಕ್ ಪರಿಶೀಲನೆಯ ಪ್ರಕಾರ ಉಳಿಸಿದ ಖಾತೆ ಅಮಾನ್ಯವಾಗಿದೆ.",
        "hi": "बैंक सत्यापन के अनुसार सहेजा गया खाता अमान्य है।"},
    "ACCOUNT_NAME_MISMATCH": {
        "en": "The name at the bank only partly matches the vendor ({result}).",
        "kn": "ಬ್ಯಾಂಕ್‌ನಲ್ಲಿರುವ ಹೆಸರು ಮಾರಾಟಗಾರರ ಹೆಸರಿಗೆ ಭಾಗಶಃ ಮಾತ್ರ ಹೊಂದುತ್ತದೆ ({result}).",
        "hi": "बैंक में दर्ज नाम विक्रेता से आंशिक रूप से ही मेल खाता है ({result})।"},
    "DOCUMENT_INCOMPLETE": {
        "en": "Some required fields are missing or unreadable: {fields}.",
        "kn": "ಕೆಲವು ಅಗತ್ಯ ವಿವರಗಳು ಇಲ್ಲ ಅಥವಾ ಓದಲಾಗುತ್ತಿಲ್ಲ: {fields}.",
        "hi": "कुछ आवश्यक फ़ील्ड गायब या अपठनीय हैं: {fields}।"},
    "LOW_CONFIDENCE_FIELD": {
        "en": "Low reading confidence on: {fields}.",
        "kn": "ಇವುಗಳನ್ನು ಓದುವಲ್ಲಿ ಕಡಿಮೆ ವಿಶ್ವಾಸ: {fields}.",
        "hi": "इन्हें पढ़ने में कम विश्वास: {fields}।"},
    "DOCUMENT_UNREADABLE": {
        "en": "The document could not be read.",
        "kn": "ದಾಖಲೆಯನ್ನು ಓದಲಾಗಲಿಲ್ಲ.",
        "hi": "दस्तावेज़ पढ़ा नहीं जा सका।"},
    "PAYMENT_REVERSED": {
        "en": "A completed payment ({transfer_id}) was reversed by the bank. Nothing is re-paid automatically.",
        "kn": "ಪೂರ್ಣಗೊಂಡ ಪಾವತಿ ({transfer_id}) ಬ್ಯಾಂಕ್‌ನಿಂದ ಹಿಂತಿರುಗಿಸಲಾಗಿದೆ. ಸ್ವಯಂಚಾಲಿತವಾಗಿ ಮತ್ತೆ ಪಾವತಿಸಲಾಗುವುದಿಲ್ಲ.",
        "hi": "पूरा हुआ भुगतान ({transfer_id}) बैंक ने वापस किया। अपने-आप दोबारा भुगतान नहीं होगा।"},
}

PAID_PREFIX = {
    "ALREADY_PAID": {"en": "Already paid ({transfer_id}). ", "kn": "ಈಗಾಗಲೇ ಪಾವತಿಸಲಾಗಿದೆ ({transfer_id}). ",
                     "hi": "पहले ही भुगतान हो चुका ({transfer_id})। "},
    "PAYMENT_IN_FLIGHT": {"en": "Payment in flight ({transfer_id}). ", "kn": "ಪಾವತಿ ಪ್ರಕ್ರಿಯೆಯಲ್ಲಿದೆ ({transfer_id}). ",
                          "hi": "भुगतान प्रक्रिया में ({transfer_id})। "},
}

SIGNAL_NAMES = {
    "SAME_STATED_TOTAL": {"en": "same total", "kn": "ಅದೇ ಒಟ್ಟು ಮೊತ್ತ", "hi": "समान कुल राशि"},
    "LINE_ITEMS_OVERLAP": {"en": "same items", "kn": "ಅದೇ ವಸ್ತುಗಳು", "hi": "समान सामान"},
    "INVOICE_NUMBER_RELATED": {"en": "similar invoice number", "kn": "ಹೋಲುವ ಇನ್‌ವಾಯ್ಸ್ ಸಂಖ್ಯೆ",
                               "hi": "मिलता-जुलता इनवॉइस नंबर"},
    "DATES_WITHIN_14_DAYS": {"en": "dates within 14 days", "kn": "14 ದಿನಗಳೊಳಗಿನ ದಿನಾಂಕಗಳು",
                             "hi": "14 दिनों के भीतर की तारीखें"},
}
TEST_PROVIDER_NAME = {"en": "the test provider", "kn": "ಪರೀಕ್ಷಾ ಪೂರೈಕೆದಾರ", "hi": "टेस्ट प्रोवाइडर"}


def _lang(lang: str | None) -> str:
    return lang if lang in ("en", "kn", "hi") else "en"


def explain_finding(code: str, evidence: dict, matched_invoice_id: str | None, lang: str = "en") -> str:
    lang = _lang(lang)
    tmpl = FINDING.get(code, {}).get(lang) or FINDING.get(code, {}).get("en") or code
    v: dict = {}
    if code == "ARITHMETIC_MISMATCH":
        m = (evidence.get("mismatches") or [{}])[0]
        v = {"stated": format_inr(m.get("stated_paise")), "computed": format_inr(m.get("computed_paise"))}
    elif code == "ARITHMETIC_UNVERIFIABLE":
        v = {"reason": " ".join(evidence.get("reasons", []))}
    elif code == "WORDS_FIGURES_MISMATCH":
        v = {"words": format_inr(evidence.get("words_paise")), "figures": format_inr(evidence.get("figures_paise"))}
    elif code == "WORDS_UNPARSABLE":
        v = {"figures": format_inr(evidence.get("stated_paise"))}
    elif code == "POSSIBLE_DUPLICATE":
        v = {"matched": matched_invoice_id, "score": evidence.get("score"),
             "signals": ", ".join(SIGNAL_NAMES.get(s["signal"], {}).get(lang, s["signal"])
                                  for s in evidence.get("signals", []))}
    elif code == "CONFIRMED_DUPLICATE":
        v = {"matched": matched_invoice_id}
    elif code == "CHANGED_BENEFICIARY":
        v = {"printed": evidence.get("printed_account"), "saved": evidence.get("saved_account")}
    elif code == "VENDOR_UNRESOLVED":
        v = {"vendor": evidence.get("vendor_name")}
    elif code == "ACCOUNT_NAME_MISMATCH":
        v = {"result": evidence.get("name_match_result")}
    elif code == "DOCUMENT_INCOMPLETE":
        v = {"fields": ", ".join(f["field"] for f in evidence.get("fields", []))}
    elif code == "LOW_CONFIDENCE_FIELD":
        v = {"fields": ", ".join(evidence.get("fields", {}).keys())}
    elif code == "PAYMENT_REVERSED":
        v = {"transfer_id": evidence.get("transfer_id")}
    try:
        text = tmpl.format(**v)
    except (KeyError, IndexError):
        text = tmpl
    pay = evidence.get("payment")
    if pay and pay.get("flag") in PAID_PREFIX:
        text = PAID_PREFIX[pay["flag"]][lang].format(transfer_id=pay.get("transfer_id")) + text
    return text


def explain_invoice(conn: sqlite3.Connection, invoice_id: str, lang: str = "en") -> str:
    lang = _lang(lang)
    inv = db.row(conn, "SELECT i.*, v.display_name FROM invoices i LEFT JOIN vendors v ON v.vendor_id=i.vendor_id"
                       " WHERE i.invoice_id=?", (invoice_id,))
    if inv is None:
        return {"en": "Invoice not found.", "kn": "ಇನ್‌ವಾಯ್ಸ್ ಕಂಡುಬಂದಿಲ್ಲ.", "hi": "इनवॉइस नहीं मिला।"}[lang]
    head = f"{invoice_id} · {inv['display_name'] or inv['vendor_name_raw'] or '—'} · " \
           f"{format_inr(inv['stated_total_paise'])}: {DECISION[inv['decision']][lang]}."
    lines = [head]
    for f in db.rows(conn, "SELECT * FROM findings WHERE invoice_id=? AND status='OPEN' ORDER BY severity, created_at",
                     (invoice_id,)):
        lines.append("• " + explain_finding(f["code"], json.loads(f["evidence_json"]), f["matched_invoice_id"], lang))
    return "\n".join(lines)


SUMMARY_HEAD = {
    "en": "{n} invoices: {e} ready to pay, {r} need review, {b} blocked.",
    "kn": "{n} ಇನ್‌ವಾಯ್ಸ್‌ಗಳು: {e} ಪಾವತಿಗೆ ಸಿದ್ಧ, {r} ಪರಿಶೀಲನೆ ಅಗತ್ಯ, {b} ತಡೆಹಿಡಿಯಲಾಗಿದೆ.",
    "hi": "{n} इनवॉइस: {e} भुगतान के लिए तैयार, {r} समीक्षा आवश्यक, {b} रोके गए।",
}


def batch_summary(conn: sqlite3.Connection, batch_id: str) -> dict:
    """Batch scorecard (PRD v4.1): counts by decision and by open finding code. Pure aggregation."""
    by_decision = {"ELIGIBLE": 0, "REVIEW": 0, "BLOCKED": 0}
    for r in conn.execute("SELECT decision, COUNT(*) AS n FROM invoices WHERE batch_id=? GROUP BY decision",
                          (batch_id,)):
        by_decision[r["decision"]] = r["n"]
    by_code = {r["code"]: r["n"] for r in conn.execute(
        "SELECT f.code, COUNT(*) AS n FROM findings f JOIN invoices i ON i.invoice_id=f.invoice_id"
        " WHERE i.batch_id=? AND f.status='OPEN' GROUP BY f.code ORDER BY n DESC, f.code", (batch_id,))}
    return {"total": sum(by_decision.values()), "by_decision": by_decision, "by_finding_code": by_code}


def explain_batch(conn: sqlite3.Connection, batch_id: str, lang: str = "en") -> str:
    """Which invoices are valid, which need review, which are blocked, with the reason for each hold."""
    lang = _lang(lang)
    s = batch_summary(conn, batch_id)["by_decision"]
    lines = [SUMMARY_HEAD[lang].format(n=sum(s.values()), e=s["ELIGIBLE"], r=s["REVIEW"], b=s["BLOCKED"])]
    invs = db.rows(conn, "SELECT i.*, v.display_name FROM invoices i LEFT JOIN vendors v ON v.vendor_id=i.vendor_id"
                         " WHERE i.batch_id=? ORDER BY CASE i.decision WHEN 'ELIGIBLE' THEN 0 WHEN 'REVIEW' THEN 1"
                         " ELSE 2 END, i.ingested_seq", (batch_id,))
    for inv in invs:
        head = (f"{DECISION[inv['decision']][lang]}: {inv['invoice_id']} · "
                f"{inv['display_name'] or inv['vendor_name_raw'] or '—'} · {format_inr(inv['stated_total_paise'])}")
        f = db.row(conn, "SELECT * FROM findings WHERE invoice_id=? AND status='OPEN'"
                         " ORDER BY CASE severity WHEN 'BLOCK' THEN 0 ELSE 1 END, created_at LIMIT 1",
                   (inv["invoice_id"],))
        if f and inv["decision"] != "ELIGIBLE":
            head += " — " + explain_finding(f["code"], json.loads(f["evidence_json"]), f["matched_invoice_id"], lang)
        lines.append("• " + head)
    return "\n".join(lines)


def explain_attempt(attempt: dict | None, lang: str = "en") -> str:
    lang = _lang(lang)
    if attempt is None:
        return {"en": "No payment has been made.", "kn": "ಯಾವುದೇ ಪಾವತಿ ಮಾಡಲಾಗಿಲ್ಲ.",
                "hi": "कोई भुगतान नहीं हुआ।"}[lang]
    text = STATUS.get(attempt["status"], {}).get(lang, attempt["status"])
    if attempt["mode"] == "TEST_PROVIDER":
        text = text.replace("Cashfree", TEST_PROVIDER_NAME[lang])
    text = f"{format_inr(attempt['amount_paise'])} · {attempt['transfer_id']}: {text}."
    if attempt["mode"] == "TEST_PROVIDER":
        text += {"en": " (Test provider: no money moved.)", "kn": " (ಪರೀಕ್ಷಾ ಪೂರೈಕೆದಾರ: ಯಾವುದೇ ಹಣ ವರ್ಗಾವಣೆಯಾಗಿಲ್ಲ.)",
                 "hi": " (टेस्ट प्रोवाइडर: कोई पैसा नहीं गया।)"}[lang]
    return text


def explain_proposal(conn: sqlite3.Connection, proposal_id: str, lang: str = "en") -> str:
    attempt = db.row(conn, "SELECT * FROM payment_attempts WHERE proposal_id=?", (proposal_id,))
    if attempt:
        return explain_attempt(attempt, lang)
    p = db.row(conn, "SELECT * FROM payment_proposals WHERE proposal_id=?", (proposal_id,))
    if p is None:
        return explain_attempt(None, lang)
    lang = _lang(lang)
    snap = json.loads(p["snapshot_json"])
    msg = {
        "READY_FOR_APPROVAL": {"en": "Proposal ready: approve it on the approval card.",
                               "kn": "ಪ್ರಸ್ತಾವನೆ ಸಿದ್ಧ: ಅನುಮೋದನೆ ಕಾರ್ಡ್‌ನಲ್ಲಿ ಅನುಮೋದಿಸಿ.",
                               "hi": "प्रस्ताव तैयार: अनुमोदन कार्ड पर स्वीकृत करें।"},
        "AUTHORIZED": {"en": "Approved; press Execute to send it.", "kn": "ಅನುಮೋದಿಸಲಾಗಿದೆ; ಕಳುಹಿಸಲು Execute ಒತ್ತಿರಿ.",
                       "hi": "स्वीकृत; भेजने के लिए Execute दबाएँ।"},
    }.get(p["state"], {}).get(lang, p["state"])
    return f"{format_inr(snap['amount_paise'])} → ••{snap['beneficiary_account_last4']}: {msg}"
