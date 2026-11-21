"""Sarvam AI adapter: vision extraction, chat tool selection and text-to-speech.

Vision is Sarvam's async Document AI extract job (submit -> poll status -> fetch results). It returns raw
strings for our schema plus per-field confidence; it never supplies arithmetic results.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx

from ..config import get_settings
from ..schemas import ExtractionRaw
from .http import client

PROVIDER = "sarvam"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "extracted"


def _text(desc: str) -> dict:
    return {"type": "string", "description": f"{desc} Copy exactly as printed, native digits included; "
                                             "never calculate or correct. Null if absent or illegible."}


def _rows(*keys: str) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": {k: _text(k.replace("_", " ")) for k in keys}}}


# Mirrors ExtractionRaw. Invoice text is data only: the extract job fills fields, it does not follow instructions.
INVOICE_SCHEMA = {"type": "object", "properties": {
    "language": _text("Main language as a code: ta-IN, hi-IN or en-IN."),
    "vendor_name": _text("Supplier (seller) name."),
    "invoice_number": _text("Invoice or bill number."),
    "invoice_date": _text("Invoice date."),
    "due_date": _text("Payment due date."),
    "line_items": _rows("description", "quantity", "unit_price", "line_total"),
    "subtotal": _text("Subtotal before tax."),
    "tax_lines": _rows("label", "rate", "amount"),
    "tax_inclusive": {"type": "boolean", "description": "True only if the invoice states prices include tax."},
    "stated_total": _text("Grand total in figures."),
    "amount_in_words": _text("Grand total written in words."),
    "bank_account": _text("Payee bank account number."),
    "ifsc": _text("Payee IFSC code."),
    "notes": _text("Any other printed note."),
}}
TERMINAL = {"completed", "partially_completed", "failed", "rejected"}
# Identifiers where a number would silently drop leading zeros (e.g. account 001234567890 -> 1234567890).
IDENTIFIER_FIELDS = ("bank_account", "ifsc", "invoice_number")
JOB_TIMEOUT_S = 90


class ExtractionFailed(Exception):
    pass


def _headers() -> dict:
    return {"api-subscription-key": get_settings().sarvam_api_key or "", "Content-Type": "application/json"}


def _str_leaves(v):
    # The job may return numbers despite the string schema; the rules expect raw strings.
    if isinstance(v, dict):
        return {k: _str_leaves(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_str_leaves(x) for x in v]
    return str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _confidence(value, ann, path: str, out: dict) -> None:
    """Flatten annotations to keys like "stated_total" / "line_items[1].line_total".
    A present value without a confidence gets 0.0, so it shows as uncertain instead of silently passing."""
    if isinstance(value, dict):
        for k, v in value.items():
            _confidence(v, ann.get(k) if isinstance(ann, dict) else None, f"{path}.{k}" if path else k, out)
    elif isinstance(value, list):
        for i, v in enumerate(value, start=1):
            _confidence(v, ann[i - 1] if isinstance(ann, list) and i <= len(ann) else None, f"{path}[{i}]", out)
    elif value is not None:
        c = ann.get("confidence") if isinstance(ann, dict) else None
        out[path] = float(c) if isinstance(c, (int, float)) else 0.0


def extract_invoice_live(path: Path, mime: str) -> ExtractionRaw:
    s = get_settings()
    h = {"api-subscription-key": s.sarvam_api_key or ""}  # no JSON Content-Type: the submit is multipart
    try:
        with client(PROVIDER, s.sarvam_base_url) as c:
            job = c.post("/doc-ai/v1/job/extract", headers=h,
                         files={"file": (path.name, path.read_bytes(), mime)},
                         data={"schema": json.dumps(INVOICE_SCHEMA), "language": s.sarvam_vision_language,
                               "output_format": "json"})
            job_id = job.raise_for_status().json()["job_id"]
            deadline = time.monotonic() + JOB_TIMEOUT_S
            while (status := c.get(f"/doc-ai/v1/job/{job_id}/status", headers=h)
                   .raise_for_status().json()["status"].lower()) not in TERMINAL:
                if time.monotonic() > deadline:
                    raise ExtractionFailed(f"job {job_id} still {status} after {JOB_TIMEOUT_S}s")
                time.sleep(2)
            # partially_completed means some pages failed; a missing page could hide bank details, so refuse it.
            if status != "completed":
                raise ExtractionFailed(f"job {job_id} ended {status}")
            body = c.get(f"/doc-ai/v1/job/{job_id}/results", headers=h).raise_for_status().json()
        result = body.get("result") or {}
        numeric_ids = [f for f in IDENTIFIER_FIELDS
                       if isinstance(result.get(f), (int, float)) and not isinstance(result.get(f), bool)]
        if numeric_ids:
            raise ExtractionFailed(f"job {job_id} returned {', '.join(numeric_ids)} as a number; "
                                   "leading zeros may be lost")
        result = _str_leaves(result)
        conf: dict[str, float] = {}
        _confidence(result, body.get("annotations") or {}, "", conf)
        return ExtractionRaw.model_validate({**result, "field_confidence": conf})
    except Exception as exc:  # any failure -> DOCUMENT_UNREADABLE, never a looser parse
        raise ExtractionFailed(f"{type(exc).__name__}: {exc}") from exc


def load_fixture_extraction(name: str) -> ExtractionRaw | None:
    p = FIXTURE_DIR / f"{name}.json"
    if not p.exists():
        return None
    return ExtractionRaw.model_validate(json.loads(p.read_text()))


def extract_invoice(path: Path, mime: str, fixture_name: str | None = None) -> tuple[ExtractionRaw, str]:
    """Returns (raw, source). source is LIVE_SARVAM_VISION or PRE_EXTRACTED_FIXTURE."""
    s = get_settings()
    if s.vision_live and mime in ("application/pdf", "image/png", "image/jpeg"):
        return extract_invoice_live(path, mime), "LIVE_SARVAM_VISION"
    raw = load_fixture_extraction(fixture_name) if fixture_name else None
    if raw is None:
        raise ExtractionFailed("Sarvam vision is not enabled and no pre-extracted fixture matches this document")
    return raw, "PRE_EXTRACTED_FIXTURE"


def chat_tool_call(system: str, user: str, tools: list[dict]) -> list[dict]:
    """Ask the chat model which read-only tools to call. Returns [{name, arguments}]."""
    s = get_settings()
    payload = {
        "model": s.sarvam_chat_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0,
    }
    with client(PROVIDER, s.sarvam_base_url) as c:
        resp = c.post("/v2/chat/completions", json=payload, headers=_headers())
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(args) if isinstance(args, str) else dict(args)
        except (ValueError, TypeError):
            parsed = {"__invalid__": args}
        calls.append({"name": fn.get("name"), "arguments": parsed})
    return calls


TTS_LANG = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}


def text_to_speech(text: str, language: str, speaker: str | None = None) -> bytes:
    s = get_settings()
    payload = {"text": text, "language_code": TTS_LANG.get(language, "en-IN"), "model": s.sarvam_tts_model}
    if speaker:
        payload["speaker"] = speaker
    with client(PROVIDER, s.sarvam_base_url) as c:
        resp = c.post("/text-to-speech", json=payload, headers=_headers())
    resp.raise_for_status()
    return base64.b64decode(resp.json()["audios"][0])
