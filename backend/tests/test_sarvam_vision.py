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
