"""UI "Reset demo data": wipes everything and reloads the six demo bills."""
from __future__ import annotations

from backend import config, db

from .test_vendors import _body, _shakti


def test_reset_clears_everything_and_reloads_demo(client, conn):
    iid = _shakti(conn)
    client.post("/api/vendors", json=_body(source_invoice_id=iid))
    client.post("/api/batches/%s/proposals" % conn.batch_id, json={"scope": "all_ready"})
    p = client.get("/api/demo/reset-preview").json()
    assert p["bills"] == 7 and p["approved_vendors"] == 1 and p["sandbox_in_flight"] == 0
    r = client.post("/api/demo/reset", json={"confirm": "RESET"})
    assert r.status_code == 200 and r.json()["bills"] == 6
    assert db.row(conn, "SELECT COUNT(*) AS n FROM vendors")["n"] == 4
    assert db.row(conn, "SELECT COUNT(*) AS n FROM payment_proposals")["n"] == 0
    assert db.row(conn, "SELECT COUNT(*) AS n FROM batches")["n"] == 1
    decisions = {r["invoice_id"]: r["decision"] for r in conn.execute("SELECT invoice_id, decision FROM invoices")}
    assert decisions == {"INV-A": "ELIGIBLE", "INV-B": "BLOCKED", "INV-C": "REVIEW",
                         "INV-D": "BLOCKED", "INV-E": "ELIGIBLE", "INV-F": "REVIEW"}
    assert db.row(conn, "SELECT COUNT(*) AS n FROM audit_events WHERE action='DEMO_RESET'")["n"] == 1
    # The app keeps working on the fresh data.
    assert client.get(f"/api/batches/{r.json()['batch_id']}").status_code == 200


def test_reset_requires_typed_confirmation(client, conn):
    r = client.post("/api/demo/reset", json={"confirm": "yes"})
    assert r.status_code == 400 and r.json()["error"] == "CONFIRMATION_REQUIRED"
    assert db.row(conn, "SELECT COUNT(*) AS n FROM invoices")["n"] == 6


def test_reset_requires_session_and_can_be_disabled(client):
    config.override(demo_reset_enabled=False)
    assert client.post("/api/demo/reset", json={"confirm": "RESET"}).status_code == 404
    config.override(demo_reset_enabled=True)
    client.headers.pop("Authorization")
    assert client.post("/api/demo/reset", json={"confirm": "RESET"}).status_code == 401
