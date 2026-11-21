"""Ingestion and the deterministic check pipeline (architecture section 4)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from . import db
from .config import get_settings
from .integrations import sarvam
from .rules import arithmetic, beneficiaries, decision, duplicates
from .rules import normalize as N
from .rules.extraction import validate_extraction
from .rules.model import normalize_extraction
from .schemas import ExtractionRaw, Finding

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_INVOICES = ROOT / "fixtures" / "invoices"
ALLOWED_UPLOAD_MIME = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}
# Findings written by other modules that a re-run of the checks must not delete.
NON_PIPELINE_CODES = ("PAYMENT_REVERSED",)


class UploadRejected(Exception):
    pass


# ---- batches and documents -------------------------------------------------


def create_batch(conn: sqlite3.Connection, source: str, actor: str) -> str:
    batch_id = db.new_id("batch")
    conn.execute("INSERT INTO batches(batch_id, created_at, created_by, source) VALUES (?,?,?,?)",
                 (batch_id, db.now(), actor, source))
    db.audit(conn, actor, "BATCH_CREATED", batch_id, {"source": source}, batch_id=batch_id)
    return batch_id


def store_document(conn: sqlite3.Connection, batch_id: str, content: bytes, original_name: str,
                   mime: str) -> tuple[dict, bool]:
    """Store the original bytes. An identical file (same SHA-256) links to the existing document."""
    sha = hashlib.sha256(content).hexdigest()
    existing = db.row(conn, "SELECT * FROM documents WHERE sha256=?", (sha,))
    if existing:
        db.audit(conn, "pipeline", "DUPLICATE_UPLOAD", existing["document_id"],
                 {"original_name": original_name, "sha256": sha,
                  "note": "Identical file already stored; no second payable created."}, batch_id=batch_id)
        return existing, False
    upload_dir = Path(get_settings().upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name).suffix or ALLOWED_UPLOAD_MIME.get(mime, ".bin")
    path = upload_dir / f"{sha}{suffix}"
    path.write_bytes(content)
    doc_id = db.new_id("doc")
    conn.execute(
        "INSERT INTO documents(document_id, batch_id, file_path, original_name, mime, sha256, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (doc_id, batch_id, str(path), original_name, mime, sha, db.now()),
    )
    return db.row(conn, "SELECT * FROM documents WHERE document_id=?", (doc_id,)), True  # type: ignore[return-value]


def validate_upload(name: str, mime: str, size: int) -> None:
    if mime not in ALLOWED_UPLOAD_MIME:
        raise UploadRejected(f"Unsupported file type {mime!r}; only PDF, PNG and JPEG are accepted")
    if size > get_settings().max_upload_bytes:
        raise UploadRejected("File is too large")
    if size == 0:
        raise UploadRejected("File is empty")


def load_fixture_batch(conn: sqlite3.Connection, actor: str) -> str:
    """Create (once) the batch holding the six printable fixture invoices."""
    existing = db.row(conn, "SELECT batch_id FROM batches WHERE source='FIXTURE' ORDER BY created_at LIMIT 1")
    if existing:
        return existing["batch_id"]
    batch_id = create_batch(conn, "FIXTURE", actor)
    for path in sorted(FIXTURE_INVOICES.glob("*.txt")):
        store_document(conn, batch_id, path.read_bytes(), path.name, "text/plain")
    return batch_id


# ---- invoice materialisation ----------------------------------------------


def _fixture_invoice_id(name: str) -> str | None:
    p = sarvam.FIXTURE_DIR / f"{name}.json"
    if p.exists():
        return json.loads(p.read_text()).get("_fixture_invoice_id")
    return None


def materialize_invoice(conn: sqlite3.Connection, batch_id: str, document_id: str, raw: ExtractionRaw | None,
                        invoice_id: str | None = None) -> str:
    invoice_id = invoice_id or db.new_id("INV")
    conn.execute(
        "INSERT INTO invoices(invoice_id, batch_id, document_id, ingested_seq, ingested_at, canonical)"
        " VALUES (?,?,?,?,?,0)",
        (invoice_id, batch_id, document_id, db.next_seq(conn), db.now()),
    )
    if raw is not None:
        write_invoice_fields(conn, invoice_id, raw)
    return invoice_id


def write_invoice_fields(conn: sqlite3.Connection, invoice_id: str, raw: ExtractionRaw) -> None:
    """(Re)write an invoice's normalized fields and line items from a raw extraction. Caller holds a txn."""
    inv = normalize_extraction(raw)
    vendor_id, _ = beneficiaries.resolve_vendor(conn, inv.vendor_name_raw)
    canonical = 1
    if vendor_id and inv.invoice_number_norm and db.row(
        conn, "SELECT 1 AS x FROM invoices WHERE vendor_id=? AND invoice_number_norm=? AND canonical=1"
              " AND invoice_id<>?",
        (vendor_id, inv.invoice_number_norm, invoice_id),
    ):
        canonical = 0  # exact duplicate of an existing canonical invoice
    conn.execute(
        "UPDATE invoices SET vendor_id=?, vendor_name_raw=?, invoice_number_raw=?, invoice_number_norm=?,"
        " invoice_date=?, due_date=?, language=?, subtotal_paise=?, tax_paise=?, stated_total_paise=?,"
        " computed_total_paise=?, words_total_paise=?, amount_in_words=?, claimed_account=?, claimed_ifsc=?,"
        " notes=?, canonical=? WHERE invoice_id=?",
        (
            vendor_id, inv.vendor_name_raw, inv.invoice_number_raw, inv.invoice_number_norm,
            inv.invoice_date.isoformat() if inv.invoice_date else None,
            inv.due_date.isoformat() if inv.due_date else None,
            inv.language, inv.subtotal_paise, inv.tax_paise, inv.stated_total_paise, inv.computed_total_paise,
            inv.words_total_paise, raw.amount_in_words, inv.claimed_account, inv.claimed_ifsc, inv.notes,
            canonical, invoice_id,
        ),
    )
    conn.execute("DELETE FROM invoice_lines WHERE invoice_id=?", (invoice_id,))
    for line in inv.lines:
        computed = (line.quantity_milli * line.unit_price_paise // 1000
                    if line.quantity_milli is not None and line.unit_price_paise is not None else None)
        conn.execute(
            "INSERT INTO invoice_lines(invoice_id, position, description, quantity_milli, unit_price_paise,"
            " line_total_paise, computed_total_paise) VALUES (?,?,?,?,?,?,?)",
            (invoice_id, line.position, line.description, line.quantity_milli, line.unit_price_paise,
             line.line_total_paise, computed),
        )


def retry_extraction(conn: sqlite3.Connection, invoice_id: str) -> bool:
    """Re-extract an invoice whose document has no extraction yet (e.g. uploaded while vision was off).

    Returns True when a new extraction was stored. Documents that already have an extraction are left alone.
    """
    inv = db.row(conn, "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,))
    doc = db.row(conn, "SELECT * FROM documents WHERE document_id=?", (inv["document_id"],)) if inv else None
    if not doc or doc["raw_json"] or doc["mime"] not in ALLOWED_UPLOAD_MIME:
        return False
    try:  # network call outside any write transaction
        raw, source = sarvam.extract_invoice(Path(doc["file_path"]), doc["mime"],
                                             Path(doc["original_name"] or "").stem or None)
    except sarvam.ExtractionFailed as exc:
        db.audit(conn, "pipeline", "PIPELINE_STEP", invoice_id,
                 {"step": "extract", "invoice_id": invoice_id, "document_id": doc["document_id"],
                  "outcome": "ERROR", "error": str(exc), "summary": f"Re-reading {invoice_id}: unreadable"},
                 batch_id=inv["batch_id"])
        return False
    with db.immediate(conn):
        if db.row(conn, "SELECT raw_json FROM documents WHERE document_id=?", (doc["document_id"],))["raw_json"]:
            return False  # another request got there first
        conn.execute("UPDATE documents SET extraction_source=?, raw_json=? WHERE document_id=?",
                     (source, raw.model_dump_json(), doc["document_id"]))
        write_invoice_fields(conn, invoice_id, raw)
        db.audit(conn, "pipeline", "PIPELINE_STEP", invoice_id,
                 {"step": "extract", "invoice_id": invoice_id, "document_id": doc["document_id"],
                  "outcome": "OK", "source": source, "summary": f"Re-reading {invoice_id} ({source})"},
                 batch_id=inv["batch_id"])
    return True


def extract_and_materialize(conn: sqlite3.Connection, batch_id: str, doc: dict) -> str:
    """Extract one stored document and create its invoice row (unreadable documents still get a row)."""
    fixture_name = Path(doc["original_name"] or "").stem or None
    path = Path(doc["file_path"])
    raw: ExtractionRaw | None = None
    source: str | None = None
    error: str | None = None
    try:  # network call: deliberately outside any write transaction
        raw, source = sarvam.extract_invoice(path, doc["mime"], fixture_name)
    except sarvam.ExtractionFailed as exc:
        error = str(exc)
    with db.immediate(conn):
        if db.row(conn, "SELECT invoice_id FROM invoices WHERE document_id=?", (doc["document_id"],)):
            return db.row(conn, "SELECT invoice_id FROM invoices WHERE document_id=?",
                          (doc["document_id"],))["invoice_id"]  # type: ignore[index]
        if raw is not None:
            conn.execute("UPDATE documents SET extraction_source=?, raw_json=? WHERE document_id=?",
                         (source, raw.model_dump_json(), doc["document_id"]))
        invoice_id = _fixture_invoice_id(fixture_name) if raw is not None and fixture_name else None
        if invoice_id and db.row(conn, "SELECT 1 AS x FROM invoices WHERE invoice_id=?", (invoice_id,)):
            invoice_id = None
        invoice_id = materialize_invoice(conn, batch_id, doc["document_id"], raw, invoice_id)
        db.audit(conn, "pipeline", "PIPELINE_STEP", invoice_id,
                 {"step": "extract", "invoice_id": invoice_id, "document_id": doc["document_id"],
                  "outcome": "OK" if raw else "ERROR", "source": source, "error": error,
                  "summary": f"Reading {invoice_id}" + (f" ({source})" if source else ": unreadable")},
                 batch_id=batch_id)
    return invoice_id


def structured_duplicate_of(conn: sqlite3.Connection, raw: ExtractionRaw) -> str | None:
    """Invoice already created from this exact structured JSON, if any: the same SHA-256 check file uploads get,
    so the caller can flag a resubmission (`duplicate_upload`) instead of silently showing the original."""
    sha = hashlib.sha256(raw.model_dump_json().encode()).hexdigest()
    r = db.row(conn, "SELECT i.invoice_id FROM documents d JOIN invoices i ON i.document_id=d.document_id"
                     " WHERE d.sha256=?", (sha,))
    return r["invoice_id"] if r else None


def add_structured_invoice(conn: sqlite3.Connection, batch_id: str, raw: ExtractionRaw, actor: str) -> str:
    """Judge-curveball path: one invoice as structured JSON, checked exactly like an extracted document.

    Identical JSON returns the existing invoice (no second payable); use structured_duplicate_of to flag it."""
    content = raw.model_dump_json().encode()
    doc, is_new = store_document(conn, batch_id, content, "structured.json", "application/json")
    if not is_new:
        existing = db.row(conn, "SELECT invoice_id FROM invoices WHERE document_id=?", (doc["document_id"],))
        if existing:
            return existing["invoice_id"]
    conn.execute("UPDATE documents SET extraction_source='STRUCTURED_JSON', raw_json=? WHERE document_id=?",
                 (raw.model_dump_json(), doc["document_id"]))
    invoice_id = materialize_invoice(conn, batch_id, doc["document_id"], raw)
    db.audit(conn, actor, "INVOICE_ADDED", invoice_id, {"source": "STRUCTURED_JSON"}, batch_id=batch_id)
    return invoice_id


# ---- checks ----------------------------------------------------------------


def _step(conn, batch_id, invoice_id, step, findings: list[Finding], summary: str) -> None:
    db.audit(conn, "pipeline", "PIPELINE_STEP", invoice_id, {
        "step": step, "invoice_id": invoice_id, "outcome": "OK",
        "reason_codes": [f.code for f in findings], "summary": summary,
    }, batch_id=batch_id)


def compute_findings(conn: sqlite3.Connection, invoice: dict) -> list[Finding]:
    s = get_settings()
    invoice_id, batch_id = invoice["invoice_id"], invoice["batch_id"]
    doc = db.row(conn, "SELECT * FROM documents WHERE document_id=?", (invoice["document_id"],))
    if not doc or not doc["raw_json"]:
        f = [Finding(code="DOCUMENT_UNREADABLE", severity="REVIEW",
                     evidence={"document_id": invoice["document_id"],
                               "reason": "No extraction is available for this document"})]
        _step(conn, batch_id, invoice_id, "extract", f, "Document could not be read")
        return f

    raw = ExtractionRaw.model_validate_json(doc["raw_json"])
    inv = normalize_extraction(raw)
    all_findings: list[Finding] = []

    f = validate_extraction(inv, s.min_field_confidence)
    all_findings += f
    _step(conn, batch_id, invoice_id, "validate_fields", f, "Checked required fields and confidence")

    f = arithmetic.check_arithmetic(inv, s.arith_tolerance_paise) + arithmetic.check_words(inv)
    all_findings += f
    computed = inv.computed_total_paise
    _step(conn, batch_id, invoice_id, "arithmetic", f,
          f"Rechecking total: stated {N.format_inr(inv.stated_total_paise)}, computed {N.format_inr(computed)}")

    vendor_id = invoice["vendor_id"]
    if vendor_id is None:
        _, f = beneficiaries.resolve_vendor(conn, inv.vendor_name_raw)
        all_findings += f
        _step(conn, batch_id, invoice_id, "resolve_vendor", f, f"Vendor {inv.vendor_name_raw!r} not resolved")
        return all_findings
    _step(conn, batch_id, invoice_id, "resolve_vendor", [], f"Vendor resolved to {vendor_id}")

    f = duplicates.check_duplicates(conn, invoice)
    all_findings += f
    _step(conn, batch_id, invoice_id, "duplicates", f,
          "Compared with earlier invoices from the same vendor"
          + (f": matches {f[0].matched_invoice_id}" if f else ": no match"))

    f = beneficiaries.compare_beneficiary(conn, vendor_id, inv.claimed_account, inv.claimed_ifsc)
    all_findings += f
    ben = beneficiaries.active_beneficiary(conn, vendor_id)
    _step(conn, batch_id, invoice_id, "beneficiary", f,
          ("Printed bank details differ from the saved account" if f
           else f"Saved account ••{ben['account_last4']} matches" if ben and (inv.claimed_account or inv.claimed_ifsc)
           else "No bank details printed; saved beneficiary stands"))

    f = beneficiaries.check_verification(conn, vendor_id, db.now(), s.verification_max_age_days,
                                         require_live=s.secureid_live)
    all_findings += f
    _step(conn, batch_id, invoice_id, "verification", f, "Checked saved beneficiary verification")
    return all_findings


def recompute_decision(conn: sqlite3.Connection, invoice_id: str) -> str:
    open_findings = db.rows(conn, "SELECT severity, status FROM findings WHERE invoice_id=?", (invoice_id,))
    d = decision.decide(open_findings)
    conn.execute("UPDATE invoices SET decision=? WHERE invoice_id=?", (d, invoice_id))
    return d


def run_checks(conn: sqlite3.Connection, invoice_id: str) -> str:
    """Idempotent: replaces open pipeline findings, keeps resolved ones, recomputes the decision."""
    from . import verification

    pre = db.row(conn, "SELECT vendor_id FROM invoices WHERE invoice_id=?", (invoice_id,))
    if pre is not None:
        verification.ensure_verified(conn, pre["vendor_id"])  # network call, outside the write transaction
    with db.immediate(conn):
        invoice = db.row(conn, "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,))
        if invoice is None:
            raise KeyError(invoice_id)
        new = compute_findings(conn, invoice)
        placeholders = ",".join("?" * len(NON_PIPELINE_CODES))
        conn.execute(
            f"DELETE FROM findings WHERE invoice_id=? AND status='OPEN' AND code NOT IN ({placeholders})",
            (invoice_id, *NON_PIPELINE_CODES),
        )
        resolved = {(r["code"], r["matched_invoice_id"]) for r in conn.execute(
            "SELECT code, matched_invoice_id FROM findings WHERE invoice_id=? AND status='RESOLVED'", (invoice_id,))}
        for f in new:
            if f.severity == "REVIEW" and (f.code, f.matched_invoice_id) in resolved:
                continue  # an operator already resolved this exact finding
            conn.execute(
                "INSERT INTO findings(finding_id, invoice_id, code, severity, evidence_json, matched_invoice_id,"
                " created_at) VALUES (?,?,?,?,?,?,?)",
                (db.new_id("fnd"), invoice_id, f.code, f.severity,
                 json.dumps(f.evidence, ensure_ascii=False, default=str), f.matched_invoice_id, db.now()),
            )
        d = recompute_decision(conn, invoice_id)
        db.audit(conn, "pipeline", "PIPELINE_STEP", invoice_id,
                 {"step": "decision", "invoice_id": invoice_id, "outcome": "OK", "decision": d,
                  "reason_codes": [f.code for f in new],
                  "summary": {"ELIGIBLE": "Eligible for payment", "REVIEW": "Marked for review",
                              "BLOCKED": "Blocked"}[d]},
                 batch_id=invoice["batch_id"])
    return d


def process_batch(conn: sqlite3.Connection, batch_id: str) -> list[dict]:
    docs = db.rows(
        conn,
        "SELECT d.* FROM documents d WHERE d.batch_id=? AND NOT EXISTS"
        " (SELECT 1 FROM invoices i WHERE i.document_id = d.document_id) ORDER BY d.original_name, d.created_at",
        (batch_id,),
    )
    for doc in docs:
        extract_and_materialize(conn, batch_id, doc)
    invoices = db.rows(conn, "SELECT invoice_id FROM invoices WHERE batch_id=? ORDER BY ingested_seq", (batch_id,))
    for inv in invoices:
        retry_extraction(conn, inv["invoice_id"])  # no-op unless the document has no extraction yet
        run_checks(conn, inv["invoice_id"])
    return db.rows(conn, "SELECT invoice_id, decision FROM invoices WHERE batch_id=? ORDER BY ingested_seq",
                   (batch_id,))
