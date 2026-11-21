"""Sarvam integration paths against a mock that follows the documented contracts (docs.sarvam.ai, 2026-11-21):
Document AI extract jobs for handwritten / regional invoices, speech-to-text questions, TTS replies, chat routing.
"""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest

from backend import agent, config, db
from backend.integrations import http as http_mod
from backend.integrations import sarvam


class SarvamMock:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status_sequence = ["pending", "running", "completed"]
        self.result = {}
        self.annotations = {}
        self.transcript = ("எந்த பில்கள் சரியானவை?", "ta-IN")
        self.tool_calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/doc-ai/v1/job/extract" and request.method == "POST":
            return httpx.Response(201, json={"job_id": "job-1", "status": "pending", "run_id": "run-1"})
        if path == "/doc-ai/v1/job/job-1/status":
            st = self.status_sequence.pop(0) if len(self.status_sequence) > 1 else self.status_sequence[0]
            return httpx.Response(200, json={"job_id": "job-1", "status": st, "usage": {}, "created_at": "",
                                             "updated_at": ""})
        if path == "/doc-ai/v1/job/job-1/results":
            assert request.url.params["format"] == "json"
            return httpx.Response(200, json={"type": "extract", "job_id": "job-1", "status": "completed",
                                             "result": self.result, "annotations": self.annotations, "version": 1})
        if path == "/speech-to-text":
            text, code = self.transcript
            return httpx.Response(200, json={"request_id": "r1", "transcript": text, "language_code": code})
        if path == "/text-to-speech":
            return httpx.Response(200, json={"request_id": "r2", "audios": [base64.b64encode(b"RIFFwav").decode()]})
        if path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None,
                                                                      "tool_calls": self.tool_calls}}]})
        return httpx.Response(404, json={"error": {"message": "unexpected", "code": "not_found_error"}})

    def bodies(self, path):
        return [r for r in self.requests if r.url.path == path]


@pytest.fixture
def sarvam_mock():
    m = SarvamMock()
    http_mod.TRANSPORTS["sarvam"] = httpx.MockTransport(m.handler)
    sarvam.POLL_SECONDS = 0
    return m


def live(**kw):
    return config.override(sarvam_api_key="sk_test", **kw)


HANDWRITTEN_TAMIL = {
    "language": "ta-IN",
    "vendor_name": "கோயம்புத்தூர் பேக்கிங்",
    "invoice_number": "CP-215",
    "invoice_date": "௧௮/௧௧/௨௦௨௬",
    "due_date": "03/12/2026",
    "line_items": [{"description": "அட்டைப் பெட்டிகள் (boxes)", "quantity": "௨௫", "unit_price": "₹80",
                    "line_total": 2000}],  # Doc AI may return a number; it must be stringified
    "subtotal": "₹2,000",
    "tax_lines": [],
    "tax_inclusive": False,
    "stated_total": "₹௨,௦௦௦",
    "amount_in_words": "Rupees Two Thousand Only",
    "bank_account": "XXXX6011",
    "ifsc": None,
    "notes": "கையால் எழுதப்பட்டது",
}


def _upload_and_process(client, name="bill.jpg", mime="image/jpeg"):
    r = client.post("/api/batches", files={"files": (name, io.BytesIO(b"\xff\xd8\xff fake jpeg " + name.encode()), mime)})
    assert r.status_code == 200, r.text
    bid = r.json()["batch_id"]
    client.post(f"/api/batches/{bid}/process")
    return client.get(f"/api/batches/{bid}").json()


def test_doc_ai_handwritten_tamil_invoice(client, conn, sarvam_mock):
    live(sarvam_vision_enabled=True)
    sarvam_mock.result = HANDWRITTEN_TAMIL
    sarvam_mock.annotations = {"stated_total": {"confidence": 0.93, "sources": []},
                               "line_items": [{"line_total": {"confidence": 0.9}, "quantity": {"confidence": 0.88}}]}
    batch = _upload_and_process(client)
    inv = batch["invoices"][0]
    assert inv["extraction_source"] == "LIVE_SARVAM_VISION"
    assert inv["vendor_id"] == "V-COIMBATORE-PACKING"          # Tamil-script vendor resolved
    assert inv["stated_total_paise"] == 200000                  # Tamil digits parsed
    assert inv["decision"] == "ELIGIBLE", inv["findings"]
    submit = sarvam_mock.bodies("/doc-ai/v1/job/extract")[0]
    assert submit.headers["api-subscription-key"] == "sk_test"
    body = submit.content.decode(errors="replace")
    assert 'name="schema"' in body and '"type": "object"' in body and 'name="file"' in body
    assert len(sarvam_mock.bodies("/doc-ai/v1/job/job-1/status")) >= 2  # polled until terminal


def test_doc_ai_low_confidence_goes_to_review(client, conn, sarvam_mock):
    live(sarvam_vision_enabled=True)
    sarvam_mock.result = HANDWRITTEN_TAMIL | {"invoice_number": "CP-216"}
    sarvam_mock.annotations = {"stated_total": {"confidence": 0.41}}
    inv = _upload_and_process(client, "smudged.jpg")["invoices"][0]
    assert inv["decision"] == "REVIEW"
    assert any(f["code"] == "LOW_CONFIDENCE_FIELD" for f in inv["findings"])


def test_doc_ai_partial_job_forces_review(client, conn, sarvam_mock):
    live(sarvam_vision_enabled=True)
    sarvam_mock.status_sequence = ["partially_completed"]
    sarvam_mock.result = HANDWRITTEN_TAMIL | {"invoice_number": "CP-217"}
    inv = _upload_and_process(client, "partial.jpg")["invoices"][0]
    assert any(f["code"] == "LOW_CONFIDENCE_FIELD" for f in inv["findings"])


def test_doc_ai_failed_job_is_unreadable(client, conn, sarvam_mock):
    live(sarvam_vision_enabled=True)
    sarvam_mock.status_sequence = ["failed"]
    inv = _upload_and_process(client, "blank.jpg")["invoices"][0]
    assert [f["code"] for f in inv["findings"]] == ["DOCUMENT_UNREADABLE"]
    assert inv["decision"] == "REVIEW"


def test_doc_ai_timeout_is_unreadable(client, conn, sarvam_mock):
    live(sarvam_vision_enabled=True, sarvam_doc_ai_timeout_seconds=0)
    sarvam_mock.status_sequence = ["running"]
    inv = _upload_and_process(client, "slow.jpg")["invoices"][0]
    assert inv["decision"] == "REVIEW" and inv["findings"][0]["code"] == "DOCUMENT_UNREADABLE"


def test_chat_extraction_mode_uses_v1_with_image(client, conn, sarvam_mock, monkeypatch):
    live(sarvam_vision_enabled=True, sarvam_extraction_mode="chat")
    content = json.dumps(HANDWRITTEN_TAMIL | {"invoice_number": "CP-218", "field_confidence": {"stated_total": 0.95}})

    def handler(request):
        sarvam_mock.requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http_mod.TRANSPORTS["sarvam"] = httpx.MockTransport(handler)
    inv = _upload_and_process(client, "chat.png", "image/png")["invoices"][0]
    req = sarvam_mock.requests[-1]
    assert req.url.path == "/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk_test" and req.headers["api-subscription-key"] == "sk_test"
    payload = json.loads(req.content)
    assert payload["model"] == "sarvam-105b"
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert inv["decision"] == "ELIGIBLE", inv["findings"]


def test_voice_question_tamil(client, conn, sarvam_mock):
    live(sarvam_stt_enabled=True, sarvam_tts_enabled=True)
    r = client.post("/api/agent/voice", data={"batch_id": conn.batch_id, "language": "auto"},
                    files={"audio": ("question.webm", io.BytesIO(b"webm-bytes"), "audio/webm")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["transcript"] == "எந்த பில்கள் சரியானவை?" and out["reply_language"] == "ta"
    assert [c["tool"] for c in out["tool_calls"]] == ["rank_eligible_invoices"]
    assert "செலுத்தத் தயார்" in out["reply"] and "INV-E" in out["reply"]  # grounded Tamil overview
    assert base64.b64decode(out["reply_audio_b64"]) == b"RIFFwav"
    stt = sarvam_mock.bodies("/speech-to-text")[0].content.decode(errors="replace")
    assert "saaras:v4" in stt and "unknown" in stt and 'name="file"' in stt
    tts = json.loads(sarvam_mock.bodies("/text-to-speech")[0].content)
    assert tts["language_code"] == "ta-IN" and tts["model"] == "bulbul:v3" and len(tts["text"]) <= 2500
    assert db.rows(conn, "SELECT * FROM payment_proposals") == []


def test_voice_pay_request_only_prepares_proposal(client, conn, sarvam_mock):
    live(sarvam_stt_enabled=True)
    sarvam_mock.transcript = ("कोयंबटूर पैकिंग का भुगतान करो", "hi-IN")
    out = client.post("/api/agent/voice", data={"batch_id": conn.batch_id},
                      files={"audio": ("q.webm", io.BytesIO(b"x"), "audio/webm")}).json()
    assert [c["tool"] for c in out["tool_calls"]] == ["prepare_payment_proposal"]
    assert out["proposals"][0]["invoice_id"] == "INV-E"
    assert db.rows(conn, "SELECT * FROM payment_attempts") == []  # nothing sent; approval still needs a click


def test_voice_disabled(client, sarvam_mock):
    r = client.post("/api/agent/voice", data={"batch_id": "x"}, files={"audio": ("q.webm", io.BytesIO(b"x"))})
    assert r.status_code == 409 and r.json()["error"] == "STT_UNAVAILABLE"


@pytest.mark.parametrize("text,lang,expect", [
    ("Which invoices are valid?", "en", "ready to pay"),
    ("कौन से इनवॉइस सही हैं?", "hi", "भुगतान के लिए तैयार"),
    ("எந்த பில்கள் சரியானவை?", "ta", "செலுத்தத் தயார்"),
])
def test_which_invoices_are_valid(conn, text, lang, expect):
    out = agent.handle_message(conn, "OP", conn.batch_id, text, lang)
    assert [c["tool"] for c in out["tool_calls"]] == ["rank_eligible_invoices"]
    assert expect in out["reply"]
    assert "INV-D" in out["reply"] and "INV-B" in out["reply"]  # blocked ones listed with their reason


def test_chat_router_endpoint(conn, sarvam_mock):
    live(sarvam_chat_enabled=True)
    sarvam_mock.tool_calls = [{"id": "call-1", "type": "function",
                               "function": {"name": "explain_findings", "arguments": '{"invoice_id": "INV-C"}'}}]
    out = agent.handle_message(conn, "OP", conn.batch_id, "why is the kaveri bill held?")
    assert out["router"] == "SARVAM_CHAT" and out["tool_calls"][0]["invoice_id"] == "INV-C"
    req = sarvam_mock.bodies("/v1/chat/completions")[0]
    assert req.headers["authorization"] == "Bearer sk_test"
    assert json.loads(req.content)["tools"][0]["type"] == "function"


def test_batch_summary(client, conn):
    s = client.get(f"/api/batches/{conn.batch_id}").json()["summary"]
    assert s["by_decision"] == {"ELIGIBLE": 2, "REVIEW": 2, "BLOCKED": 2} and s["total"] == 6
    assert s["by_finding_code"]["CHANGED_BENEFICIARY"] == 1


def test_absent_field_zero_confidence_is_not_a_review(client, conn, sarvam_mock):
    """Observed live: Document AI scores absent fields 0.0. A bill with no bank details must stay eligible (T09)."""
    live(sarvam_vision_enabled=True)
    sarvam_mock.result = HANDWRITTEN_TAMIL | {"invoice_number": "CP-219", "bank_account": None, "ifsc": None}
    sarvam_mock.annotations = {"stated_total": {"confidence": 1.0}, "bank_account": {"confidence": 0.0},
                               "ifsc": {"confidence": 0.0}, "notes": {"confidence": 0.0}}
    inv = _upload_and_process(client, "nobank.jpg")["invoices"][0]
    assert inv["decision"] == "ELIGIBLE", inv["findings"]


@pytest.mark.parametrize("field", ["bank_account", "ifsc", "invoice_number"])
def test_doc_ai_numeric_identifier_is_refused(field):
    # 001234567890 as a number would come back as 1234567890 and look like a changed account.
    with pytest.raises(sarvam.ExtractionFailed, match="as a number"):
        sarvam.doc_ai_result_to_raw({"vendor_name": "Kaveri", field: 1234567890}, {}, partial=False)


def test_doc_ai_language_sent_only_when_configured(client, conn, sarvam_mock):
    live(sarvam_vision_enabled=True, sarvam_doc_ai_language="ta-IN")
    sarvam_mock.result = HANDWRITTEN_TAMIL
    _upload_and_process(client, "lang.jpg")
    assert b'name="language"' in sarvam_mock.bodies("/doc-ai/v1/job/extract")[0].content
