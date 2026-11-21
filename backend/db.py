"""SQLite storage: connections, explicit transactions and the audit writer."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or get_settings().database_path
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_PATH.read_text())
    # Databases created before vendors.phone existed.
    if "phone" not in {r[1] for r in conn.execute("PRAGMA table_info(vendors)")}:
        conn.execute("ALTER TABLE vendors ADD COLUMN phone TEXT")
    # Databases created before payment_attempts.provider_details_json existed.
    if "provider_details_json" not in {r[1] for r in conn.execute("PRAGMA table_info(payment_attempts)")}:
        conn.execute("ALTER TABLE payment_attempts ADD COLUMN provider_details_json TEXT")


@contextmanager
def immediate(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT, rolling back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    object_id: str | None,
    detail: dict,
    dedupe_key: str | None = None,
    batch_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO audit_events(actor, action, object_id, batch_id, detail_json, dedupe_key, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (actor, action, object_id, batch_id, json.dumps(detail, ensure_ascii=False, default=str), dedupe_key, now()),
    )


def row(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def next_seq(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT COALESCE(MAX(ingested_seq), 0) + 1 AS n FROM invoices").fetchone()
    return int(r["n"])
