"""Sarvam Document AI extract job adapter, against a mocked job API."""
from __future__ import annotations

import httpx
import pytest

from backend.integrations import http as http_mod
from backend.integrations import sarvam


def _mock(status: str, results: dict | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/doc-ai/v1/job/extract":
            assert b'name="schema"' in req.content  # multipart submit with our schema
            return httpx.Response(200, json={"job_id": "J1", "status": "pending"})
        if req.url.path == "/doc-ai/v1/job/J1/status":
            return httpx.Response(200, json={"job_id": "J1", "status": status})
        if req.url.path == "/doc-ai/v1/job/J1/results":
            return httpx.Response(200, json=results)
        return httpx.Response(404)
    http_mod.TRANSPORTS["sarvam"] = httpx.MockTransport(handler)


def test_extract_maps_result_and_confidence(tmp_path):
    _mock("completed", {
        "status": "completed",
        "result": {"vendor_name": "Kaveri Fasteners", "stated_total": 8200, "bank_account": "XXXX9012",
                   "line_items": [{"description": "bolts", "line_total": 5000}]},
        "annotations": {"vendor_name": {"confidence": 0.97}, "stated_total": {"confidence": 0.91},
                        "line_items": [{"description": {"confidence": 0.9}, "line_total": {"confidence": 0.6}}]},
    })
    doc = tmp_path / "inv.png"
    doc.write_bytes(b"png")
    raw = sarvam.extract_invoice_live(doc, "image/png")
    assert raw.stated_total == "8200"  # numbers come back as raw strings
    assert raw.line_items[0].line_total == "5000"
    assert raw.field_confidence["stated_total"] == 0.91
    assert raw.field_confidence["line_items[1].line_total"] == 0.6
    assert raw.field_confidence["bank_account"] == 0.0  # no annotation -> treated as uncertain


@pytest.mark.parametrize("status", ["partially_completed", "failed", "rejected"])
def test_non_completed_job_is_unreadable(tmp_path, status):
    _mock(status)
    doc = tmp_path / "inv.pdf"
    doc.write_bytes(b"%PDF")
    with pytest.raises(sarvam.ExtractionFailed):
        sarvam.extract_invoice_live(doc, "application/pdf")


@pytest.mark.parametrize("field", ["bank_account", "ifsc", "invoice_number"])
def test_numeric_identifier_is_unreadable(tmp_path, field):
    # A number would drop leading zeros (account 001234567890 -> 1234567890), so refuse it.
    _mock("completed", {"status": "completed", "result": {"vendor_name": "Kaveri", field: 1234567890},
                        "annotations": {field: {"confidence": 0.99}}})
    doc = tmp_path / "inv.png"
    doc.write_bytes(b"png")
    with pytest.raises(sarvam.ExtractionFailed, match="as a number"):
        sarvam.extract_invoice_live(doc, "image/png")


def test_job_that_never_finishes_times_out(tmp_path, monkeypatch):
    _mock("processing")
    clock = iter(range(0, 1000, 30))  # each check advances 30 s
    monkeypatch.setattr(sarvam.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(sarvam.time, "sleep", lambda _s: None)
    doc = tmp_path / "inv.pdf"
    doc.write_bytes(b"%PDF")
    with pytest.raises(sarvam.ExtractionFailed, match="still processing"):
        sarvam.extract_invoice_live(doc, "application/pdf")
