"""Create the database and seed vendors, beneficiaries, seeded verifications and the fixture batch.

Usage: python -m backend.seed [--reset]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from . import db, pipeline
from .config import get_settings
from .rules import normalize as N

VENDORS_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "vendors.json"


def seed_registry(conn: sqlite3.Connection) -> None:
    data = json.loads(VENDORS_PATH.read_text())
    ts = db.now()
    with db.immediate(conn):
        for v in data["vendors"]:
            conn.execute(
                "INSERT OR IGNORE INTO vendors(vendor_id, display_name, aliases_json, statutory_risk,"
                " deadline_basis_verified, payment_terms_days) VALUES (?,?,?,?,?,?)",
                (v["vendor_id"], v["display_name"], json.dumps(v["aliases"], ensure_ascii=False),
                 v["statutory_risk"], v["deadline_basis_verified"], v.get("payment_terms_days")),
            )
            b = v["beneficiary"]
            conn.execute(
                "INSERT OR IGNORE INTO beneficiaries(beneficiary_id, version, vendor_id, account_holder_name,"
                " bank_account_number, account_last4, ifsc, cashfree_beneficiary_id, active, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,1,?)",
                (b["beneficiary_id"], 1, v["vendor_id"], b["account_holder_name"], b["bank_account_number"],
                 N.last4(b["bank_account_number"]), b["ifsc"], b.get("cashfree_beneficiary_id"), ts),
            )
            ver = v.get("verification")
            if ver and not db.row(conn, "SELECT 1 AS x FROM beneficiary_verifications WHERE beneficiary_id=?",
                                  (b["beneficiary_id"],)):
                conn.execute(
                    "INSERT INTO beneficiary_verifications(verification_id, beneficiary_id, beneficiary_version,"
                    " source, account_status, name_match_result, name_at_bank, provider_ref, verified_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (db.new_id("ver"), b["beneficiary_id"], 1, "SEEDED_MOCK", ver["account_status"],
                     ver["name_match_result"], ver.get("name_at_bank"), None, ts),
                )
        db.set_setting(conn, "cash_budget_paise", "900000")
        db.audit(conn, "seed", "SEEDED_REGISTRY", None, {"vendors": len(data["vendors"])})


def seed_all(conn: sqlite3.Connection, process: bool = True) -> str:
    db.init_db(conn)
    seed_registry(conn)
    with db.immediate(conn):
        batch_id = pipeline.load_fixture_batch(conn, "seed")
    if process:
        pipeline.process_batch(conn, batch_id)
    return batch_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete the database first")
    parser.add_argument("--no-process", action="store_true", help="load the fixture batch without running checks")
    args = parser.parse_args()
    path = Path(get_settings().database_path)
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    conn = db.connect()
    batch_id = seed_all(conn, process=not args.no_process)
    print(f"Database: {path}")
    print(f"Fixture batch: {batch_id}")
    for r in db.rows(conn, "SELECT invoice_id, invoice_number_raw, decision FROM invoices WHERE batch_id=?"
                           " ORDER BY ingested_seq", (batch_id,)):
        print(f"  {r['invoice_id']:<8} {r['invoice_number_raw'] or '-':<10} {r['decision']}")


if __name__ == "__main__":
    main()
