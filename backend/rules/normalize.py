"""Normalization of raw extracted strings. Every function returns None rather than guessing."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

_DIGIT_MAP = {}
for _base in (0x0966, 0x0BE6):  # Devanagari ०-९, Tamil ௦-௯
    for _i in range(10):
        _DIGIT_MAP[_base + _i] = str(_i)


def to_ascii_digits(s: str) -> str:
    return s.translate(_DIGIT_MAP)


_CURRENCY = re.compile(r"(₹|rs\.?|inr|रु\.?|रुपये|रुपए|ரூ\.?)", re.IGNORECASE)
_SCALES = {
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "लाख": 100_000, "லட்சம்": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "करोड़": 10_000_000, "करोड": 10_000_000,
    "கோடி": 10_000_000,
    "thousand": 1_000, "k": 1_000, "हज़ार": 1_000, "हजार": 1_000,
}
_GROUPED = re.compile(r"^\d{1,3}(,\d{2})*,\d{3}$|^\d{1,3}(,\d{3})+$|^\d+$")


def _to_paise(d: Decimal) -> int | None:
    paise = d * 100
    if paise != paise.to_integral_value():
        return None  # sub-paise precision is not a real amount
    return int(paise)


def parse_amount_paise(s: str | None) -> int | None:
    """'₹1,20,000.50' -> 12000050; '1.2 lakh' -> 12000000; '₹ 3,200/-' -> 320000."""
    if s is None:
        return None
    t = to_ascii_digits(unicodedata.normalize("NFC", str(s))).strip()
    if not t:
        return None
    t = _CURRENCY.sub(" ", t)
    t = t.replace("/-", " ").replace("only", " ").replace("Only", " ")
    t = t.strip().rstrip(".").strip()
    if t.startswith("-"):
        return None
    m = re.fullmatch(r"([\d.,]+)\s*([^\d\s.,]+)?", t)
    if not m:
        return None
    number, scale_word = m.group(1), m.group(2)
    scale = 1
    if scale_word:
        scale = _SCALES.get(scale_word.lower()) or _SCALES.get(scale_word)
        if scale is None:
            return None
    if "." in number:
        int_part, _, frac = number.rpartition(".")
        if "." in int_part or len(frac) > 2 and scale == 1:
            return None
    else:
        int_part, frac = number, ""
    if int_part and not _GROUPED.match(int_part):
        return None
    clean = int_part.replace(",", "") + ("." + frac if frac else "")
    if clean in ("", "."):
        return None
    try:
        value = Decimal(clean) * scale
    except InvalidOperation:
        return None
    return _to_paise(value)


def parse_quantity_milli(s: str | None) -> int | None:
    if s is None:
        return None
    t = to_ascii_digits(str(s)).strip().replace(",", "")
    m = re.match(r"^(\d+(?:\.\d{1,3})?)", t)
    if not m:
        return None
    rest = t[m.end():].strip()
    if rest and re.search(r"\d", rest):
        return None
    milli = Decimal(m.group(1)) * 1000
    return int(milli)


def parse_date(s: str | None) -> date | None:
    """dd/mm/yyyy, dd-mm-yy, dd.mm.yyyy, yyyy-mm-dd. Ambiguous or invalid -> None."""
    if s is None:
        return None
    t = to_ascii_digits(str(s)).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})", t)
        if not m:
            return None
        d, mo, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def normalize_invoice_number(s: str | None) -> str | None:
    if s is None:
        return None
    t = re.sub(r"[^A-Z0-9]", "", to_ascii_digits(str(s)).upper())
    return t or None


def normalize_vendor_name(s: str | None) -> str | None:
    if s is None:
        return None
    t = unicodedata.normalize("NFC", str(s)).casefold()
    t = "".join(ch if (ch.isalnum() or unicodedata.category(ch).startswith("M") or ch.isspace()) else " " for ch in t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


# ---- amount in words ------------------------------------------------------

_EN_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_EN_SCALES = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
              "crore": 10_000_000, "crores": 10_000_000}

_HI_UNITS = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "सोलह": 16,
    "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "इक्कीस": 21, "बाईस": 22, "तेईस": 23, "चौबीस": 24,
    "पच्चीस": 25, "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28, "उनतीस": 29, "तीस": 30, "इकतीस": 31,
    "बत्तीस": 32, "तैंतीस": 33, "चौंतीस": 34, "पैंतीस": 35, "छत्तीस": 36, "सैंतीस": 37, "अड़तीस": 38,
    "उनतालीस": 39, "चालीस": 40, "इकतालीस": 41, "बयालीस": 42, "तैंतालीस": 43, "चवालीस": 44, "पैंतालीस": 45,
    "छियालीस": 46, "सैंतालीस": 47, "अड़तालीस": 48, "उनचास": 49, "पचास": 50, "इक्यावन": 51, "बावन": 52,
    "तिरेपन": 53, "चौवन": 54, "पचपन": 55, "छप्पन": 56, "सत्तावन": 57, "अट्ठावन": 58, "उनसठ": 59, "साठ": 60,
    "इकसठ": 61, "बासठ": 62, "तिरेसठ": 63, "चौंसठ": 64, "पैंसठ": 65, "छियासठ": 66, "सड़सठ": 67, "अड़सठ": 68,
    "उनहत्तर": 69, "सत्तर": 70, "इकहत्तर": 71, "बहत्तर": 72, "तिहत्तर": 73, "चौहत्तर": 74, "पचहत्तर": 75,
    "छिहत्तर": 76, "सतहत्तर": 77, "अठहत्तर": 78, "उन्यासी": 79, "अस्सी": 80, "इक्यासी": 81, "बयासी": 82,
    "तिरासी": 83, "चौरासी": 84, "पचासी": 85, "छियासी": 86, "सत्तासी": 87, "अट्ठासी": 88, "नवासी": 89,
    "नब्बे": 90, "इक्यानवे": 91, "बानवे": 92, "तिरानवे": 93, "चौरानवे": 94, "पचानवे": 95, "छियानवे": 96,
    "सत्तानवे": 97, "अट्ठानवे": 98, "निन्यानवे": 99,
}
_HI_SCALES = {"सौ": 100, "हज़ार": 1_000, "हजार": 1_000, "लाख": 100_000, "करोड़": 10_000_000, "करोड": 10_000_000}
_FILLER = {"rupees", "rupee", "rs", "inr", "and", "only", "रुपये", "रुपए", "रुपया", "मात्र", "केवल", "और"}
_PAISE_WORDS = {"paise", "paisa", "पैसे", "पैसा"}


def _words_to_int(tokens: list[str]) -> int | None:
    total = 0
    current = 0
    seen = False
    for tok in tokens:
        if tok in _EN_UNITS or tok in _HI_UNITS:
            current += _EN_UNITS.get(tok, _HI_UNITS.get(tok, 0))
            seen = True
        elif tok in ("hundred", "सौ"):
            current = (current or 1) * 100
            seen = True
        elif tok in _EN_SCALES or tok in _HI_SCALES:
            scale = _EN_SCALES.get(tok) or _HI_SCALES[tok]
            total += (current or 1) * scale
            current = 0
            seen = True
        else:
            return None
    return total + current if seen else None


def words_to_paise(s: str | None) -> int | None:
    """English and Hindi amount-in-words to paise. Anything unrecognised (e.g. Tamil) -> None."""
    if not s:
        return None
    t = unicodedata.normalize("NFC", str(s)).casefold()
    t = re.sub(r"[-,./()]", " ", t)
    tokens = [tok for tok in t.split() if tok]
    paise_idx = next((i for i, tok in enumerate(tokens) if tok in _PAISE_WORDS), None)
    if paise_idx is None:
        rupee_part, paise_part = tokens, []
    else:
        # The paise amount is the run of words between the last "and" and the word "paise".
        and_idx = max((i for i in range(paise_idx) if tokens[i] in ("and", "और")), default=None)
        if and_idx is None:
            return None
        rupee_part, paise_part = tokens[:and_idx], tokens[and_idx + 1:paise_idx]
        if any(tok not in _FILLER for tok in tokens[paise_idx + 1:]):
            return None
    rupee_tokens = [tok for tok in rupee_part if tok not in _FILLER]
    paise_tokens = [tok for tok in paise_part if tok not in _FILLER]
    rupees = _words_to_int(rupee_tokens) if rupee_tokens else None
    if rupees is None:
        return None
    paise = 0
    if paise_tokens:
        p = _words_to_int(paise_tokens)
        if p is None or p > 99:
            return None
        paise = p
    return rupees * 100 + paise


def format_inr(paise: int | None) -> str:
    """Indian digit grouping: 12000050 -> '₹1,20,000.50'."""
    if paise is None:
        return "—"
    neg = paise < 0
    paise = abs(paise)
    rupees, p = divmod(paise, 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    out = f"₹{s}" + (f".{p:02d}" if p else "")
    return ("-" if neg else "") + out


def mask_account(account: str | None) -> str:
    if not account:
        return "—"
    digits = re.sub(r"[^0-9]", "", to_ascii_digits(account))
    return "••" + digits[-4:] if len(digits) >= 4 else "••"


def last4(account: str | None) -> str | None:
    if not account:
        return None
    digits = re.sub(r"[^0-9]", "", to_ascii_digits(account))
    return digits[-4:] if len(digits) >= 4 else None


def is_masked(account: str | None) -> bool:
    return bool(account) and bool(re.search(r"[xX*•]", account))
