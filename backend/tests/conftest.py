from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from backend import config, db
from backend.integrations import http as http_mod
from backend.schemas import ExtractionRaw

ROOT = Path(__file__).resolve().parents[2]
CURVEBALLS = ROOT / "fixtures" / "curveballs"
WEBHOOK_SECRET = "whsec_test"


@pytest.fixture(autouse=True)
def settings(tmp_path):
    s = config.Settings(
        database_path=str(tmp_path / "nipta.db"),
        upload_dir=str(tmp_path / "uploads"),
        operator_pin="1234",
        session_secret="test-secret",
        cashfree_webhook_secret=WEBHOOK_SECRET,
    )
    config.set_settings(s)
    http_mod.TRANSPORTS.clear()
    yield s
    http_mod.TRANSPORTS.clear()


@pytest.fixture
def conn(settings):
    from backend.seed import seed_all

    c = db.connect()
    batch_id = seed_all(c)
    yield _Conn(c, batch_id)
    c.close()


class _Conn:
    """sqlite3 connection wrapper that also carries the fixture batch id."""

    def __init__(self, c, batch_id):
        self._c = c
        self.batch_id = batch_id

    def __getattr__(self, name):
        return getattr(self._c, name)


def payouts_live(**extra):
    return config.override(cashfree_payouts_enabled=True, cashfree_payouts_client_id="cid",
                           cashfree_payouts_client_secret="csecret", **extra)


def add_live_verification(conn, beneficiary_id: str, name_match="DIRECT_MATCH", account_status="VALID"):
    with db.immediate(conn):
        conn.execute(
            "INSERT INTO beneficiary_verifications(verification_id, beneficiary_id, beneficiary_version, source,"
            " account_status, name_match_result, verified_at) VALUES (?,?,1,'LIVE_SECURE_ID_BAV',?,?,?)",
            (db.new_id("ver"), beneficiary_id, account_status, name_match, db.now() + 1),
        )


def curveball(name: str) -> ExtractionRaw:
    return ExtractionRaw.model_validate(json.loads((CURVEBALLS / f"{name}.json").read_text()))


def add_invoice(conn, raw: ExtractionRaw, batch_id: str | None = None) -> str:
    from backend import pipeline

    with db.immediate(conn):
        bid = batch_id or conn.batch_id
        invoice_id = pipeline.add_structured_invoice(conn, bid, raw, "test")
    pipeline.run_checks(conn, invoice_id)
    return invoice_id


def findings(conn, invoice_id: str, status: str = "OPEN") -> dict[str, dict]:
    return {r["code"]: {**dict(r), "evidence": json.loads(r["evidence_json"])}
            for r in conn.execute("SELECT * FROM findings WHERE invoice_id=? AND status=?", (invoice_id, status))}


def decision(conn, invoice_id: str) -> str:
    return conn.execute("SELECT decision FROM invoices WHERE invoice_id=?", (invoice_id,)).fetchone()["decision"]


class CashfreeMock:
    """Programmable stand-in for Cashfree Payouts V2."""

    def __init__(self):
        self.lock = threading.Lock()
        self.posts: list[dict] = []
        self.gets: list[str] = []
        self.transfers: dict[str, tuple[str, str | None]] = {}
        self.post_mode = "accept"          # accept | timeout | timeout_after_create | 500 | 400 | duplicate
        self.accept_status = ("RECEIVED", None)
        self.get_mode = "normal"           # normal | timeout
        self.post_delay = 0.0              # seconds a POST stays "in flight" (widens race windows)

    def set_status(self, transfer_id, status, code=None):
        self.transfers[transfer_id] = (status, code)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/payout/transfers" and request.method == "POST":
            body = json.loads(request.content)
            tid = body["transfer_id"]
            with self.lock:
                self.posts.append(body)
            if self.post_delay:
                import time
                time.sleep(self.post_delay)
            if self.post_mode == "timeout":
                raise httpx.ReadTimeout("simulated timeout", request=request)
            if self.post_mode == "timeout_after_create":
                self.transfers[tid] = self.accept_status
                raise httpx.ReadTimeout("simulated timeout after create", request=request)
            if self.post_mode == "500":
                return httpx.Response(502, json={"message": "bad gateway"})
            if self.post_mode == "400":
                return httpx.Response(400, json={"code": "beneficiary_invalid", "message": "Invalid IFSC"})
            if self.post_mode == "duplicate" or tid in self.transfers:
                return httpx.Response(409, json={"code": "transfer_id_already_exists",
                                                 "message": "Transfer Id already exists"})
            self.transfers[tid] = self.accept_status
            st, code = self.accept_status
            return httpx.Response(200, json={"transfer_id": tid, "cf_transfer_id": f"CF{len(self.posts)}",
                                             "status": st, "status_code": code})
        if request.url.path == "/payout/transfers" and request.method == "GET":
            tid = request.url.params["transfer_id"]
            with self.lock:
                self.gets.append(tid)
            if self.get_mode == "timeout":
                raise httpx.ReadTimeout("simulated lookup timeout", request=request)
            if tid not in self.transfers:
                return httpx.Response(404, json={"code": "transfer_not_found", "message": "Transfer not found"})
            st, code = self.transfers[tid]
            return httpx.Response(200, json={"transfer_id": tid, "cf_transfer_id": "CF-L", "status": st,
                                             "status_code": code})
        return httpx.Response(404, json={"message": "unexpected route"})


@pytest.fixture
def cashfree():
    mock = CashfreeMock()
    http_mod.TRANSPORTS["cashfree"] = httpx.MockTransport(mock.handler)
    return mock


@pytest.fixture
def client(conn):
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app) as c:
        token = c.post("/api/auth/session", json={"pin": "1234"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        c.batch_id = conn.batch_id  # type: ignore[attr-defined]
        yield c


def webhook_body(transfer_id, status, code=None, event_type="TRANSFER_SUCCESS", event_time="2026-11-21T12:00:00"):
    return json.dumps({"type": event_type, "event_time": event_time,
                       "data": {"transfer_id": transfer_id, "cf_transfer_id": "CF-W", "status": status,
                                "status_code": code}}).encode()


def post_webhook(client, raw: bytes, secret: str = WEBHOOK_SECRET, ts: str = "1763712000"):
    from backend.integrations.cashfree_payouts import sign_webhook

    return client.post("/api/webhooks/cashfree", content=raw,
                       headers={"x-webhook-timestamp": ts, "x-webhook-signature": sign_webhook(raw, ts, secret),
                                "content-type": "application/json"})
