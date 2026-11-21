"""Session, PIN, read-route auth, language fallback, agent matching and STT error handling."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import time

import httpx
import pytest

from backend import agent, app as app_module, config, db
from backend.integrations import http as http_mod


@pytest.fixture(autouse=True)
def reset_pin_guard():
    app_module._pin_guard.update(fails=0, locked_until=0)
    yield
    app_module._pin_guard.update(fails=0, locked_until=0)


def _forge(secret: str) -> str:
    p = f"ATTACKER|{int(time.time()) + 999}"
    sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), p.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{p}|{sig}"


@pytest.mark.parametrize("secret", ["", config.DEV_SESSION_SECRET])
def test_default_session_secret_is_replaced(client, secret):
    config.override(session_secret=secret)
    assert config.get_settings().session_secret not in ("", config.DEV_SESSION_SECRET)
    r = client.get("/api/batches", headers={"Authorization": "Bearer " + _forge(config.DEV_SESSION_SECRET)})
    assert r.status_code == 401
    token = client.post("/api/auth/session", json={"pin": "1234"}).json()["token"]
    assert client.get("/api/batches", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_explicit_session_secret_kept():
    assert config.Settings(session_secret="s3cret").session_secret == "s3cret"


def test_pin_lockout_and_expiry(client, conn, monkeypatch):
    for _ in range(app_module.PIN_MAX_FAILS):
        assert client.post("/api/auth/session", json={"pin": "0000"}).status_code == 401
    assert client.post("/api/auth/session", json={"pin": "1234"}).status_code == 429  # correct PIN still locked
    denied = db.rows(conn, "SELECT 1 FROM audit_events WHERE action='SESSION_DENIED'")
    assert len(denied) == app_module.PIN_MAX_FAILS
    later = db.now() + app_module.PIN_LOCK_SECONDS + 1
    monkeypatch.setattr(db, "now", lambda: later)
    assert client.post("/api/auth/session", json={"pin": "1234"}).status_code == 200


@pytest.mark.parametrize("pin,code", [("೧೨೩೪", 200), ("१२३४", 200), (" 1234 ", 200), ("ಅಬಕ", 401), ("12345", 401)])
def test_native_digit_pin(client, pin, code):
    assert client.post("/api/auth/session", json={"pin": pin}).status_code == code


@pytest.mark.parametrize("path", ["/api/audit", "/api/invoices/INV-A/source", "/api/invoices/INV-A/findings",
                                  "/api/batches", "/api/batches/{bid}", "/api/vendors/V-KAVERI-FASTENERS/verification",
                                  "/api/payment-plan?batch_id={bid}", "/api/proposals/x", "/api/proposals/x/status"])
def test_read_routes_need_session(client, path):
    path = path.format(bid=client.batch_id)
    assert client.get(path).status_code in (200, 404)  # authed: works (x is an unknown proposal)
    assert client.get(path, headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/health", headers={"Authorization": ""}).status_code == 200


@pytest.mark.parametrize("lang", ["ta", "fr", ""])
def test_unknown_lang_falls_back_to_english(client, lang):
    b = client.get(f"/api/batches/{client.batch_id}?lang={lang}")
    assert b.status_code == 200
    f = client.get(f"/api/invoices/INV-A/findings?lang={lang}")
    assert f.status_code == 200 and f.json()["decision_label"] == b.json()["invoices"][0]["decision_label"]


def test_agent_unknown_language(conn):
    out = agent.handle_message(conn, "OP", conn.batch_id, "nonsense", "fr")
    assert out["reply"]


def test_pay_exact_invoice_number_only(conn):
    out = agent.handle_message(conn, "OP", conn.batch_id, "pay KF/182-A")
    assert [(c["tool"], c["invoice_id"]) for c in out["tool_calls"]] == [("prepare_payment_proposal", "INV-C")]
    out = agent.handle_message(conn, "OP", conn.batch_id, "why is KF-182 on hold?")
    assert [c["invoice_id"] for c in out["tool_calls"]] == ["INV-A"]
    out = agent.handle_message(conn, "OP", conn.batch_id, "explain KF 182 A")  # spoken with spaces
    assert [c["invoice_id"] for c in out["tool_calls"]] == ["INV-C"]


def test_short_invoice_number_ignored(conn):
    conn.execute("UPDATE invoices SET invoice_number_raw='a' WHERE invoice_id='INV-E'")
    out = agent.handle_message(conn, "OP", conn.batch_id, "why is the tamil tools bill on hold?")
    assert [c["invoice_id"] for c in out["tool_calls"]] == ["INV-B"]
    assert [c["tool"] for c in agent.handle_message(conn, "OP", conn.batch_id, "which bills are valid")
            ["tool_calls"]] == ["rank_eligible_invoices"]


@pytest.mark.parametrize("resp", [httpx.Response(200, text="<html>gateway</html>"),
                                  httpx.Response(429, json={"error": "rate limited"}),
                                  httpx.Response(503, text="oops"),
                                  httpx.Response(200, json=["not", "an", "object"])])
def test_voice_bad_stt_response_is_handled(client, resp):
    config.override(sarvam_api_key="k", sarvam_stt_enabled=True)
    http_mod.TRANSPORTS["sarvam"] = httpx.MockTransport(lambda req: resp)
    r = client.post("/api/agent/voice", data={"batch_id": client.batch_id},
                    files={"audio": ("q.webm", io.BytesIO(b"abc"), "audio/webm")})
    assert r.status_code == 502 and r.json()["error"] == "STT_ERROR"


def test_bank_change_requests_need_a_session(conn):
    from fastapi.testclient import TestClient

    from backend.app import app
    with TestClient(app) as c:
        assert c.get("/api/vendors/V-KAVERI-FASTENERS/bank-changes").status_code == 401
