"""Fixed English, Tamil and Hindi templates filled only from stored state.

Every number, invoice ID, payee and status in user-facing text comes from here, never from model output.
"""
from __future__ import annotations

import json
import sqlite3

from . import db
from .rules.normalize import format_inr

DECISION = {
    "ELIGIBLE": {"en": "Eligible for payment", "ta": "பணம் செலுத்தத் தகுதியானது", "hi": "भुगतान के योग्य"},
    "REVIEW": {"en": "Needs review", "ta": "மறுபரிசீலனை தேவை", "hi": "समीक्षा आवश्यक"},
    "BLOCKED": {"en": "Blocked", "ta": "தடுக்கப்பட்டது", "hi": "रोका गया"},
}

STATUS = {
    "REQUEST_RESERVED": {"en": "Payment reserved; not yet sent to Cashfree",
                         "ta": "பணம் ஒதுக்கப்பட்டது; இன்னும் Cashfree-க்கு அனுப்பப்படவில்லை",
                         "hi": "भुगतान आरक्षित; अभी Cashfree को नहीं भेजा गया"},
    "INITIATED": {"en": "Cashfree accepted the transfer request",
                  "ta": "Cashfree பரிமாற்றக் கோரிக்கையை ஏற்றது",
                  "hi": "Cashfree ने ट्रांसफ़र अनुरोध स्वीकार किया"},
    "PENDING": {"en": "Transfer in progress; not yet confirmed as credited",
                "ta": "பரிமாற்றம் நடைபெறுகிறது; வரவு இன்னும் உறுதிசெய்யப்படவில்லை",
                "hi": "ट्रांसफ़र जारी है; जमा होने की पुष्टि अभी नहीं हुई"},
    "COMPLETED": {"en": "Payment completed and confirmed by Cashfree",
                  "ta": "பணம் செலுத்தப்பட்டது, Cashfree உறுதிசெய்தது",
                  "hi": "भुगतान पूरा हुआ, Cashfree ने पुष्टि की"},
    "FAILED": {"en": "Payment failed; no money was settled",
               "ta": "பணம் செலுத்துதல் தோல்வியடைந்தது; பணம் செல்லவில்லை",
               "hi": "भुगतान विफल; कोई राशि नहीं गई"},
    "UNKNOWN": {"en": "Outcome not established; do not re-pay",
                "ta": "முடிவு உறுதியாகவில்லை; மீண்டும் செலுத்த வேண்டாம்",
                "hi": "परिणाम स्पष्ट नहीं; दोबारा भुगतान न करें"},
    "REVERSED": {"en": "The bank reversed this payment; it needs review",
                 "ta": "வங்கி இந்தப் பணத்தைத் திருப்பியது; மறுபரிசீலனை தேவை",
                 "hi": "बैंक ने यह भुगतान वापस कर दिया; समीक्षा आवश्यक"},
    "SIMULATED": {"en": "Simulated: no Cashfree transfer was made",
                  "ta": "உருவகப்படுத்தப்பட்டது: Cashfree பரிமாற்றம் எதுவும் செய்யப்படவில்லை",
                  "hi": "सिम्युलेटेड: कोई Cashfree ट्रांसफ़र नहीं हुआ"},
}

FINDING = {
    "ARITHMETIC_MISMATCH": {
        "en": "Totals do not add up: stated {stated}, computed {computed}.",
        "ta": "மொத்தங்கள் பொருந்தவில்லை: குறிப்பிட்டது {stated}, கணக்கிட்டது {computed}.",
        "hi": "जोड़ मेल नहीं खाता: लिखा {stated}, गणना {computed}।"},
    "ARITHMETIC_UNVERIFIABLE": {
        "en": "The total could not be verified: {reason}",
        "ta": "மொத்தத்தைச் சரிபார்க்க முடியவில்லை: {reason}",
        "hi": "कुल राशि सत्यापित नहीं हो सकी: {reason}"},
    "WORDS_FIGURES_MISMATCH": {
        "en": "Amount in words ({words}) does not match the figures ({figures}).",
        "ta": "எழுத்தில் உள்ள தொகை ({words}) எண்களுடன் ({figures}) பொருந்தவில்லை.",
        "hi": "शब्दों में राशि ({words}) अंकों ({figures}) से मेल नहीं खाती।"},
    "WORDS_UNPARSABLE": {
        "en": "The amount in words could not be read, so it was not compared with {figures}.",
        "ta": "எழுத்தில் உள்ள தொகையைப் படிக்க முடியவில்லை; {figures} உடன் ஒப்பிடப்படவில்லை.",
        "hi": "शब्दों में राशि पढ़ी नहीं जा सकी, इसलिए {figures} से तुलना नहीं हुई।"},
    "POSSIBLE_DUPLICATE": {
        "en": "Possible duplicate of {matched} (score {score}): {signals}.",
        "ta": "{matched} இன் நகலாக இருக்கலாம் (மதிப்பெண் {score}): {signals}.",
        "hi": "{matched} की संभावित डुप्लिकेट (स्कोर {score}): {signals}।"},
    "CONFIRMED_DUPLICATE": {
        "en": "Same vendor and invoice number as {matched}.",
        "ta": "{matched} போலவே அதே விற்பனையாளர், அதே விலைப்பட்டியல் எண்.",
        "hi": "{matched} जैसा ही विक्रेता और इनवॉइस नंबर।"},
    "CHANGED_BENEFICIARY": {
        "en": "Printed bank account {printed} differs from the saved account {saved}. The saved account was not changed.",
        "ta": "அச்சிடப்பட்ட வங்கிக் கணக்கு {printed}, சேமித்த கணக்கு {saved} இலிருந்து வேறுபடுகிறது. சேமித்த கணக்கு மாற்றப்படவில்லை.",
        "hi": "छपा बैंक खाता {printed} सहेजे गए खाते {saved} से अलग है। सहेजा गया खाता नहीं बदला गया।"},
    "NO_APPROVED_BENEFICIARY": {
        "en": "This vendor has no approved bank account on file.",
        "ta": "இந்த விற்பனையாளருக்கு அங்கீகரிக்கப்பட்ட வங்கிக் கணக்கு இல்லை.",
        "hi": "इस विक्रेता का कोई स्वीकृत बैंक खाता दर्ज नहीं है।"},
    "VENDOR_UNRESOLVED": {
        "en": "Vendor \"{vendor}\" is not in the approved vendor list.",
        "ta": "விற்பனையாளர் \"{vendor}\" அங்கீகரிக்கப்பட்ட பட்டியலில் இல்லை.",
        "hi": "विक्रेता \"{vendor}\" स्वीकृत सूची में नहीं है।"},
    "VERIFICATION_MISSING": {
        "en": "The saved bank account has no current verification.",
        "ta": "சேமித்த வங்கிக் கணக்கிற்கு தற்போதைய சரிபார்ப்பு இல்லை.",
        "hi": "सहेजे गए बैंक खाते का कोई वर्तमान सत्यापन नहीं है।"},
    "VERIFICATION_FAILED": {
        "en": "Bank verification reports the saved account as invalid.",
        "ta": "வங்கிச் சரிபார்ப்பு சேமித்த கணக்கை செல்லாதது எனக் காட்டுகிறது.",
        "hi": "बैंक सत्यापन के अनुसार सहेजा गया खाता अमान्य है।"},
    "ACCOUNT_NAME_MISMATCH": {
        "en": "The name at the bank only partly matches the vendor ({result}).",
        "ta": "வங்கியில் உள்ள பெயர் விற்பனையாளருடன் பகுதியாக மட்டுமே பொருந்துகிறது ({result}).",
        "hi": "बैंक में दर्ज नाम विक्रेता से आंशिक रूप से ही मेल खाता है ({result})।"},
    "DOCUMENT_INCOMPLETE": {
        "en": "Some required fields are missing or unreadable: {fields}.",
        "ta": "சில தேவையான புலங்கள் இல்லை அல்லது படிக்க முடியவில்லை: {fields}.",
        "hi": "कुछ आवश्यक फ़ील्ड गायब या अपठनीय हैं: {fields}।"},
    "LOW_CONFIDENCE_FIELD": {
        "en": "Low reading confidence on: {fields}.",
        "ta": "இவற்றைப் படிப்பதில் குறைந்த நம்பகத்தன்மை: {fields}.",
        "hi": "इन्हें पढ़ने में कम विश्वास: {fields}।"},
    "DOCUMENT_UNREADABLE": {
        "en": "The document could not be read.",
        "ta": "ஆவணத்தைப் படிக்க முடியவில்லை.",
        "hi": "दस्तावेज़ पढ़ा नहीं जा सका।"},
    "PAYMENT_REVERSED": {
        "en": "A completed payment ({transfer_id}) was reversed by the bank. Nothing is re-paid automatically.",
        "ta": "முடிந்த பணம் ({transfer_id}) வங்கியால் திருப்பப்பட்டது. தானாக மீண்டும் செலுத்தப்படாது.",
        "hi": "पूरा हुआ भुगतान ({transfer_id}) बैंक ने वापस किया। अपने-आप दोबारा भुगतान नहीं होगा।"},
}

PAID_PREFIX = {
    "ALREADY_PAID": {"en": "Already paid ({transfer_id}). ", "ta": "ஏற்கனவே செலுத்தப்பட்டது ({transfer_id}). ",
                     "hi": "पहले ही भुगतान हो चुका ({transfer_id})। "},
    "PAYMENT_IN_FLIGHT": {"en": "Payment in flight ({transfer_id}). ", "ta": "பணம் செல்லும் வழியில் ({transfer_id}). ",
                          "hi": "भुगतान प्रक्रिया में ({transfer_id})। "},
}

SIGNAL_NAMES = {
    "SAME_STATED_TOTAL": {"en": "same total", "ta": "அதே மொத்தம்", "hi": "समान कुल राशि"},
    "LINE_ITEMS_OVERLAP": {"en": "same items", "ta": "அதே பொருட்கள்", "hi": "समान सामान"},
    "INVOICE_NUMBER_RELATED": {"en": "similar invoice number", "ta": "ஒத்த விலைப்பட்டியல் எண்",
                               "hi": "मिलता-जुलता इनवॉइस नंबर"},
    "DATES_WITHIN_14_DAYS": {"en": "dates within 14 days", "ta": "14 நாட்களுக்குள் தேதிகள்",
                             "hi": "14 दिनों के भीतर की तारीखें"},
}
TEST_PROVIDER_NAME = {"en": "the test provider", "ta": "சோதனை வழங்குநர்", "hi": "टेस्ट प्रोवाइडर"}


def _lang(lang: str | None) -> str:
    return lang if lang in ("en", "ta", "hi") else "en"


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
        return {"en": "Invoice not found.", "ta": "விலைப்பட்டியல் இல்லை.", "hi": "इनवॉइस नहीं मिला।"}[lang]
    head = f"{invoice_id} · {inv['display_name'] or inv['vendor_name_raw'] or '—'} · " \
           f"{format_inr(inv['stated_total_paise'])}: {DECISION[inv['decision']][lang]}."
    lines = [head]
    for f in db.rows(conn, "SELECT * FROM findings WHERE invoice_id=? AND status='OPEN' ORDER BY severity, created_at",
                     (invoice_id,)):
        lines.append("• " + explain_finding(f["code"], json.loads(f["evidence_json"]), f["matched_invoice_id"], lang))
    return "\n".join(lines)


def explain_attempt(attempt: dict | None, lang: str = "en") -> str:
    lang = _lang(lang)
    if attempt is None:
        return {"en": "No payment has been made.", "ta": "பணம் எதுவும் செலுத்தப்படவில்லை.",
                "hi": "कोई भुगतान नहीं हुआ।"}[lang]
    text = STATUS.get(attempt["status"], {}).get(lang, attempt["status"])
    if attempt["mode"] == "TEST_PROVIDER":
        text = text.replace("Cashfree-க்கு", TEST_PROVIDER_NAME["ta"] + "க்கு").replace("Cashfree", TEST_PROVIDER_NAME[lang])
    text = f"{format_inr(attempt['amount_paise'])} · {attempt['transfer_id']}: {text}."
    if attempt["mode"] == "TEST_PROVIDER":
        text += {"en": " (Test provider: no money moved.)", "ta": " (சோதனை வழங்குநர்: பணம் நகரவில்லை.)",
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
                               "ta": "முன்மொழிவு தயார்: ஒப்புதல் அட்டையில் அங்கீகரிக்கவும்.",
                               "hi": "प्रस्ताव तैयार: अनुमोदन कार्ड पर स्वीकृत करें।"},
        "AUTHORIZED": {"en": "Approved; press Execute to send it.", "ta": "அங்கீகரிக்கப்பட்டது; அனுப்ப Execute அழுத்தவும்.",
                       "hi": "स्वीकृत; भेजने के लिए Execute दबाएँ।"},
    }.get(p["state"], {}).get(lang, p["state"])
    return f"{format_inr(snap['amount_paise'])} → ••{snap['beneficiary_account_last4']}: {msg}"
