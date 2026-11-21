"""Relay vendor call: registry phone only, secret-checked callback, and the invoice never unblocks."""
from __future__ import annotations

import json

import httpx
import pytest

from backend import config
from backend.integrations import http as http_mod

SECRET = "relay-secret"


@pytest.fixture
def relay_calls():
    sent: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    http_mod.TRANSPORTS["relay"] = httpx.MockTransport(handler)
    config.override(relay_vendor_call_url="https://relay.test/hook", relay_callback_secret=SECRET,
                    public_base_url="https://nipta.test")
    return sent


def result(client, call_id, outcome, secret=SECRET):
    return client.post("/api/relay/vendor-call-result", json={"call_id": call_id, "outcome": outcome,
                                                              "summary": "Vendor answered"},
                       headers={"x-nipta-relay-secret": secret})


def test_call_uses_registry_phone_and_result_keeps_block(client, relay_calls):
    r = client.post("/api/invoices/INV-D/vendor-call")  # INV-D: printed account differs from saved one
    assert r.status_code == 200 and r.json()["status"] == "REQUESTED"
    sent = relay_calls[0]
    assert sent["customer"]["phone"] == "+919000000003"  # Eastern Hardware's number on file
    assert sent["callback_url"] == "https://nipta.test/api/relay/vendor-call-result"
    call_id = r.json()["call_id"]

    assert result(client, call_id, "denied change", secret="wrong").status_code == 401
    assert result(client, call_id, "denied change").status_code == 200
    assert result(client, call_id, "CONFIRMED_CHANGE").status_code == 200  # replay ignored: first result wins

    d = client.get("/api/invoices/INV-D/findings?lang=kn").json()
    assert d["vendor_call"]["status"] == "DENIED_CHANGE"
    assert "ವಂಚನೆ" in d["vendor_call"]["text"]
    assert d["decision"] == "BLOCKED"


def test_unknown_outcome_is_unclear(client, relay_calls):
    call_id = client.post("/api/invoices/INV-D/vendor-call").json()["call_id"]
    result(client, call_id, "maybe?")
    assert client.get("/api/invoices/INV-D/findings").json()["vendor_call"]["status"] == "UNCLEAR"


def test_demo_phone_override(client, relay_calls):
    config.override(demo_call_phone="+919999999999")
    client.post("/api/invoices/INV-D/vendor-call")
    assert relay_calls[0]["customer"]["phone"] == "+919999999999"


def test_no_call_without_bank_change_or_config(client, relay_calls):
    assert client.post("/api/invoices/INV-A/vendor-call").json()["error"] == "NO_BANK_CHANGE"
    config.override(relay_vendor_call_url=None)
    assert client.post("/api/invoices/INV-D/vendor-call").json()["error"] == "RELAY_UNAVAILABLE"
    assert relay_calls == []
