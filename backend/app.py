"""Nipta HTTP API and static UI.  Run: uvicorn backend.app:app --port 8000"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import agent, db, explain, pipeline, runtime_lock
from .config import capability_report, get_settings
from .errors import NiptaError
from .integrations import cashfree_secureid, sarvam
from .integrations.cashfree_payouts import ProviderRejected, ProviderUncertain
from .payments import approval, execution, status
from .rules import beneficiaries, priority
from .rules.normalize import format_inr, mask_account
from .schemas import (AgentMessageRequest, ApproveRequest, ProviderEventRequest, ResolveFindingRequest,
                      SessionRequest, StructuredInvoiceRequest, TTSRequest)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _bootstrap() -> None:
    conn = db.connect()
    try:
        db.init_db(conn)
        if not db.row(conn, "SELECT 1 AS x FROM vendors LIMIT 1"):
            from .seed import seed_all
            seed_all(conn)
        status.recover_all(conn)
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db_path = get_settings().database_path
    runtime_lock.acquire(db_path)  # refuse to run beside another process on the same database
    try:
        _bootstrap()
        yield
    finally:
        runtime_lock.release(db_path)


app = FastAPI(title="Nipta", version="1.0", lifespan=lifespan)


@app.exception_handler(NiptaError)
async def nipta_error_handler(_: Request, exc: NiptaError):
    return JSONResponse(status_code=exc.http_status,
                        content={"error": exc.code, "message": exc.message, "detail": exc.detail})


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


# ---- session auth ----------------------------------------------------------


def _sign(payload: str) -> str:
    key = get_settings().session_secret.encode()
    return base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def issue_token(operator_id: str) -> tuple[str, int]:
    expires = db.now() + get_settings().session_ttl_seconds
    payload = f"{operator_id}|{expires}"
    return f"{payload}|{_sign(payload)}", expires


def require_session(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Operator session required")
    token = authorization[7:]
    try:
        operator_id, expires, sig = token.split("|")
    except ValueError:
        raise HTTPException(401, "Malformed session token")
    if not hmac.compare_digest(sig, _sign(f"{operator_id}|{expires}")):
        raise HTTPException(401, "Invalid session token")
    if int(expires) < db.now():
        raise HTTPException(401, "Session expired")
    return operator_id


# ---- helpers ---------------------------------------------------------------

OVERLAY = {"COMPLETED": "PAID", "REQUEST_RESERVED": "PAYMENT_PENDING", "INITIATED": "PAYMENT_PENDING",
           "PENDING": "PAYMENT_PENDING", "UNKNOWN": "PAYMENT_UNKNOWN", "FAILED": "PAYMENT_FAILED",
           "REVERSED": "REVERSED_REVIEW", "SIMULATED": "SIMULATED"}


def _latest_attempt(conn, invoice_id: str) -> dict | None:
    return db.row(conn, "SELECT * FROM payment_attempts WHERE invoice_id=? ORDER BY created_at DESC, rowid DESC"
                        " LIMIT 1", (invoice_id,))


def _active_proposal(conn, invoice_id: str) -> dict | None:
    p = db.row(conn, "SELECT proposal_id, state, snapshot_hash, expires_at FROM payment_proposals WHERE invoice_id=?"
                     " ORDER BY created_at DESC, rowid DESC LIMIT 1", (invoice_id,))
    return p


def _public_attempt(a: dict | None) -> dict | None:
    if a is None:
        return None
    keys = ("attempt_id", "proposal_id", "invoice_id", "transfer_id", "amount_paise", "mode", "status",
            "cf_transfer_id", "last_provider_status", "last_provider_code", "last_error", "dispatch_started_at",
            "response_recorded_at", "last_checked_at", "created_at")
    return {k: a[k] for k in keys}


def invoice_summary(conn, inv: dict, lang: str = "en") -> dict:
    findings = db.rows(conn, "SELECT * FROM findings WHERE invoice_id=? ORDER BY status, severity, created_at",
                       (inv["invoice_id"],))
    attempt = _latest_attempt(conn, inv["invoice_id"])
    doc = db.row(conn, "SELECT extraction_source, original_name, mime FROM documents WHERE document_id=?",
                 (inv["document_id"],))
    return {
        "invoice_id": inv["invoice_id"],
        "vendor_id": inv["vendor_id"],
        "vendor": inv.get("display_name") or inv["vendor_name_raw"],
        "vendor_name_raw": inv["vendor_name_raw"],
        "invoice_number": inv["invoice_number_raw"],
        "invoice_date": inv["invoice_date"],
        "due_date": inv["due_date"],
        "language": inv["language"],
        "stated_total_paise": inv["stated_total_paise"],
        "computed_total_paise": inv["computed_total_paise"],
        "decision": inv["decision"],
        "decision_label": explain.DECISION[inv["decision"]][lang],
        "payment_overlay": OVERLAY.get(attempt["status"]) if attempt else None,
        "attempt": _public_attempt(attempt),
        "proposal": _active_proposal(conn, inv["invoice_id"]),
        "extraction_source": doc["extraction_source"] if doc else None,
        "document_name": doc["original_name"] if doc else None,
        "findings": [
            {
                "finding_id": f["finding_id"], "code": f["code"], "severity": f["severity"], "status": f["status"],
                "matched_invoice_id": f["matched_invoice_id"], "evidence": json.loads(f["evidence_json"]),
                "resolved_by": f["resolved_by"], "resolved_reason": f["resolved_reason"],
                "explanation": explain.explain_finding(f["code"], json.loads(f["evidence_json"]),
                                                       f["matched_invoice_id"], lang),
            }
            for f in findings
        ],
    }


def _invoice_or_404(conn, invoice_id: str) -> dict:
    inv = db.row(conn, "SELECT i.*, v.display_name FROM invoices i LEFT JOIN vendors v ON v.vendor_id=i.vendor_id"
                       " WHERE i.invoice_id=?", (invoice_id,))
    if inv is None:
        raise NiptaError("NOT_FOUND", f"Invoice {invoice_id} not found", 404)
    return inv


def _proposal_view(conn, proposal_id: str, lang: str = "en") -> dict:
    p = approval.get_proposal(conn, proposal_id)
    inv = _invoice_or_404(conn, p["invoice_id"])
    snap = p["snapshot"]
    ver = db.row(conn, "SELECT source FROM beneficiary_verifications WHERE verification_id=?",
                 (snap["verification_ref"],)) if snap.get("verification_ref") else None
    attempt = db.row(conn, "SELECT * FROM payment_attempts WHERE proposal_id=?", (proposal_id,))
    postings = db.rows(conn, "SELECT posting_id, kind, amount_paise, reversal_of, created_at FROM financial_postings"
                             " WHERE attempt_id=? ORDER BY created_at", (attempt["attempt_id"],)) if attempt else []
    transitions = db.rows(conn, "SELECT from_status, to_status, source, applied, evidence_json, created_at"
                                " FROM attempt_transitions WHERE attempt_id=? ORDER BY transition_id",
                          (attempt["attempt_id"],)) if attempt else []
    for t in transitions:
        ev = json.loads(t.pop("evidence_json"))
        t["note"] = ev.get("reason") or ev.get("note") or ev.get("ignored")
        t["provider_status"] = ev.get("provider_status")
        t["provider_status_code"] = ev.get("provider_status_code")
    return {
        "proposal_id": p["proposal_id"],
        "invoice_id": p["invoice_id"],
        "state": p["state"],
        "snapshot": snap,
        "snapshot_hash": p["snapshot_hash"],
        "approver_id": p["approver_id"],
        "approved_at": p["approved_at"],
        "expires_at": p["expires_at"],
        "vendor": inv["display_name"] or inv["vendor_name_raw"],
        "invoice_number": inv["invoice_number_raw"],
        "amount_display": format_inr(snap["amount_paise"]),
        "beneficiary_display": f"••{snap['beneficiary_account_last4']}",
        "verification_label": ("Live Secure ID result" if ver and ver["source"] == "LIVE_SECURE_ID_BAV"
                               else "Seeded mock, not a Secure ID result"),
        "dispatch_mode": snap["dispatch_mode"],
        "attempt": _public_attempt(attempt),
        "ledger_posted": any(x["kind"] == "SETTLEMENT" for x in postings),
        "postings": postings,
        "transitions": transitions,
        "status_text": explain.explain_proposal(conn, proposal_id, lang),
    }


# ---- routes ----------------------------------------------------------------


@app.get("/api/health")
def health():
    return {"ok": True, "capabilities": capability_report()}


@app.post("/api/auth/session")
def create_session(body: SessionRequest, conn: sqlite3.Connection = Depends(get_conn)):
    s = get_settings()
    if not hmac.compare_digest(body.pin, s.operator_pin):
        db.audit(conn, "anonymous", "SESSION_DENIED", None, {})
        raise HTTPException(401, "Wrong PIN")
    token, expires = issue_token(s.operator_id)
    db.audit(conn, s.operator_id, "SESSION_ISSUED", None, {"expires_at": expires})
    return {"token": token, "operator_id": s.operator_id, "expires_at": expires}


@app.get("/api/batches")
def list_batches(conn: sqlite3.Connection = Depends(get_conn)):
    return db.rows(conn, "SELECT b.*, (SELECT COUNT(*) FROM invoices i WHERE i.batch_id=b.batch_id) AS invoice_count"
                         " FROM batches b ORDER BY created_at DESC")


@app.post("/api/batches")
async def create_batch(
    files: list[UploadFile] | None = File(default=None),
    fixture: bool = Form(default=False),
    operator: str = Depends(require_session),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if fixture:
        with db.immediate(conn):
            batch_id = pipeline.load_fixture_batch(conn, operator)
        return {"batch_id": batch_id, "source": "FIXTURE"}
    if not files:
        raise NiptaError("NO_FILES", "Upload at least one PDF, PNG or JPEG, or load the fixture batch", 400)
    payloads = []
    for f in files:
        content = await f.read()
        try:
            pipeline.validate_upload(f.filename or "upload", f.content_type or "", len(content))
        except pipeline.UploadRejected as exc:
            raise NiptaError("UPLOAD_REJECTED", f"{f.filename}: {exc}", 400)
        payloads.append((f.filename or "upload", f.content_type, content))
    with db.immediate(conn):
        batch_id = pipeline.create_batch(conn, "UPLOAD", operator)
        stored = []
        for name, mime, content in payloads:
            doc, is_new = pipeline.store_document(conn, batch_id, content, name, mime)
            stored.append({"name": name, "document_id": doc["document_id"], "duplicate_upload": not is_new})
    return {"batch_id": batch_id, "source": "UPLOAD", "documents": stored}


@app.post("/api/batches/{batch_id}/process")
def process_batch(batch_id: str, operator: str = Depends(require_session),
                  conn: sqlite3.Connection = Depends(get_conn)):
    if not db.row(conn, "SELECT 1 AS x FROM batches WHERE batch_id=?", (batch_id,)):
        raise NiptaError("NOT_FOUND", "Batch not found", 404)
    return {"batch_id": batch_id, "invoices": pipeline.process_batch(conn, batch_id)}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str, lang: str = "en", conn: sqlite3.Connection = Depends(get_conn)):
    b = db.row(conn, "SELECT * FROM batches WHERE batch_id=?", (batch_id,))
    if b is None:
        raise NiptaError("NOT_FOUND", "Batch not found", 404)
    invs = db.rows(conn, "SELECT i.*, v.display_name FROM invoices i LEFT JOIN vendors v ON v.vendor_id=i.vendor_id"
                         " WHERE i.batch_id=? ORDER BY i.ingested_seq", (batch_id,))
    pending_docs = db.rows(conn, "SELECT document_id, original_name FROM documents d WHERE d.batch_id=? AND NOT EXISTS"
                                 " (SELECT 1 FROM invoices i WHERE i.document_id=d.document_id)", (batch_id,))
    return {**b, "invoices": [invoice_summary(conn, i, lang) for i in invs], "unprocessed_documents": pending_docs,
            "summary": explain.batch_summary(conn, batch_id),
            "cash_budget_paise": int(db.get_setting(conn, "cash_budget_paise", "900000"))}


@app.post("/api/invoices")
def add_invoice(body: StructuredInvoiceRequest, operator: str = Depends(require_session),
                conn: sqlite3.Connection = Depends(get_conn)):
    with db.immediate(conn):
        batch_id = body.batch_id
        if batch_id and not db.row(conn, "SELECT 1 AS x FROM batches WHERE batch_id=?", (batch_id,)):
            raise NiptaError("NOT_FOUND", "Batch not found", 404)
        if not batch_id:
            batch_id = pipeline.create_batch(conn, "UPLOAD", operator)
        invoice_id = pipeline.add_structured_invoice(conn, batch_id, body.extraction, operator)
    pipeline.run_checks(conn, invoice_id)
    return invoice_summary(conn, _invoice_or_404(conn, invoice_id))


@app.post("/api/invoices/{invoice_id}/checks")
def rerun_checks(invoice_id: str, operator: str = Depends(require_session),
                 conn: sqlite3.Connection = Depends(get_conn)):
    _invoice_or_404(conn, invoice_id)
    pipeline.run_checks(conn, invoice_id)
    return invoice_summary(conn, _invoice_or_404(conn, invoice_id))


@app.get("/api/invoices/{invoice_id}/findings")
def invoice_findings(invoice_id: str, lang: str = "en", conn: sqlite3.Connection = Depends(get_conn)):
    inv = _invoice_or_404(conn, invoice_id)
    out = invoice_summary(conn, inv, lang)
    out["lines"] = db.rows(conn, "SELECT position, description, quantity_milli, unit_price_paise, line_total_paise,"
                                 " computed_total_paise FROM invoice_lines WHERE invoice_id=? ORDER BY position",
                           (invoice_id,))
    out["notes"] = inv["notes"]
    out["notes_label"] = "Document text, shown as data. Never treated as an instruction."
    out["amount_in_words"] = inv["amount_in_words"]
    ben = beneficiaries.active_beneficiary(conn, inv["vendor_id"]) if inv["vendor_id"] else None
    out["bank_comparison"] = {
        "printed_account": mask_account(inv["claimed_account"]) if inv["claimed_account"] else None,
        "printed_ifsc": inv["claimed_ifsc"],
        "saved_account": f"••{ben['account_last4']}" if ben else None,
        "saved_ifsc": ben["ifsc"] if ben else None,
        "saved_beneficiary": f"{ben['beneficiary_id']} v{ben['version']}" if ben else None,
    }
    out["explanation"] = explain.explain_invoice(conn, invoice_id, lang)
    return out


@app.get("/api/invoices/{invoice_id}/source")
def invoice_source(invoice_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    inv = _invoice_or_404(conn, invoice_id)
    doc = db.row(conn, "SELECT * FROM documents WHERE document_id=?", (inv["document_id"],))
    path = Path(doc["file_path"])
    if not path.exists():
        raise NiptaError("NOT_FOUND", "Original document is not on disk", 404)
    if doc["mime"] in ("text/plain", "application/json"):
        return PlainTextResponse(path.read_text(errors="replace"))
    return FileResponse(path, media_type=doc["mime"])


@app.post("/api/findings/{finding_id}/resolve")
def resolve_finding(finding_id: str, body: ResolveFindingRequest, operator: str = Depends(require_session),
                    conn: sqlite3.Connection = Depends(get_conn)):
    with db.immediate(conn):
        f = db.row(conn, "SELECT * FROM findings WHERE finding_id=?", (finding_id,))
        if f is None:
            raise NiptaError("NOT_FOUND", "Finding not found", 404)
        if f["severity"] == "BLOCK":
            raise NiptaError("BLOCK_NOT_OVERRIDABLE", "BLOCK findings cannot be overridden in this version", 403)
        if f["status"] != "OPEN":
            raise NiptaError("ALREADY_RESOLVED", "Finding is already resolved")
        conn.execute("UPDATE findings SET status='RESOLVED', resolved_by=?, resolved_reason=? WHERE finding_id=?",
                     (operator, body.reason, finding_id))
        decision = pipeline.recompute_decision(conn, f["invoice_id"])
        db.audit(conn, operator, "FINDING_RESOLVED", finding_id,
                 {"invoice_id": f["invoice_id"], "code": f["code"], "reason": body.reason, "decision": decision})
    return invoice_summary(conn, _invoice_or_404(conn, f["invoice_id"]))


@app.get("/api/vendors/{vendor_id}/verification")
def vendor_verification(vendor_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    v = db.row(conn, "SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,))
    if v is None:
        raise NiptaError("NOT_FOUND", "Vendor not found", 404)
    ben = beneficiaries.active_beneficiary(conn, vendor_id)
    ver = beneficiaries.latest_verification(conn, ben) if ben else None
    return {
        "vendor_id": vendor_id, "display_name": v["display_name"],
        "beneficiary": {"beneficiary_id": ben["beneficiary_id"], "version": ben["version"],
                        "account": f"••{ben['account_last4']}", "ifsc": ben["ifsc"],
                        "holder": ben["account_holder_name"]} if ben else None,
        "verification": ver and {k: ver[k] for k in ("verification_id", "source", "account_status",
                                                     "name_match_result", "name_at_bank", "verified_at")},
        "verification_label": None if not ver else ("Live Secure ID result" if ver["source"] == "LIVE_SECURE_ID_BAV"
                                                    else "Seeded mock, not a Secure ID result"),
    }


@app.post("/api/vendors/{vendor_id}/verify")
def verify_vendor(vendor_id: str, operator: str = Depends(require_session),
                  conn: sqlite3.Connection = Depends(get_conn)):
    if not get_settings().secureid_live:
        raise NiptaError("SECURE_ID_UNAVAILABLE", "Cashfree Secure ID is not enabled for this deployment", 409)
    ben = beneficiaries.active_beneficiary(conn, vendor_id)
    if ben is None:
        raise NiptaError("NO_APPROVED_BENEFICIARY", "Vendor has no saved beneficiary", 409)
    try:
        r = cashfree_secureid.verify_bank_account(ben["bank_account_number"], ben["ifsc"], ben["account_holder_name"])
    except (ProviderUncertain, ProviderRejected) as exc:
        raise NiptaError("SECURE_ID_ERROR", str(exc), 502)
    with db.immediate(conn):
        conn.execute(
            "INSERT INTO beneficiary_verifications(verification_id, beneficiary_id, beneficiary_version, source,"
            " account_status, name_match_result, name_at_bank, provider_ref, verified_at)"
            " VALUES (?,?,?,'LIVE_SECURE_ID_BAV',?,?,?,?,?)",
            (db.new_id("ver"), ben["beneficiary_id"], ben["version"], r.account_status, r.name_match_result,
             r.name_at_bank, r.reference_id, db.now()),
        )
        db.audit(conn, operator, "BENEFICIARY_VERIFIED", vendor_id,
                 {"source": "LIVE_SECURE_ID_BAV", "account_status": r.account_status,
                  "name_match_result": r.name_match_result})
    for inv in db.rows(conn, "SELECT invoice_id FROM invoices WHERE vendor_id=?", (vendor_id,)):
        pipeline.run_checks(conn, inv["invoice_id"])
    return vendor_verification(vendor_id, conn)


@app.get("/api/payment-plan")
def payment_plan(batch_id: str, cash_budget_paise: int | None = Query(default=None, ge=0),
                 conn: sqlite3.Connection = Depends(get_conn)):
    budget = cash_budget_paise if cash_budget_paise is not None else int(db.get_setting(conn, "cash_budget_paise",
                                                                                        "900000"))
    return priority.payment_plan(conn, batch_id, budget)


@app.post("/api/invoices/{invoice_id}/proposal")
def create_proposal(invoice_id: str, lang: str = "en", operator: str = Depends(require_session),
                    conn: sqlite3.Connection = Depends(get_conn)):
    p = approval.create_proposal(conn, invoice_id, operator)
    return _proposal_view(conn, p["proposal_id"], lang)


@app.get("/api/proposals/{proposal_id}")
def get_proposal(proposal_id: str, lang: str = "en", conn: sqlite3.Connection = Depends(get_conn)):
    return _proposal_view(conn, proposal_id, lang)


@app.post("/api/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str, body: ApproveRequest, lang: str = "en",
                     operator: str = Depends(require_session), conn: sqlite3.Connection = Depends(get_conn)):
    approval.approve(conn, proposal_id, operator, body.snapshot_hash)
    return _proposal_view(conn, proposal_id, lang)


@app.post("/api/proposals/{proposal_id}/execute")
def execute_proposal(proposal_id: str, lang: str = "en", operator: str = Depends(require_session),
                     conn: sqlite3.Connection = Depends(get_conn)):
    execution.execute_approved_payout(conn, proposal_id, operator)
    return _proposal_view(conn, proposal_id, lang)


@app.get("/api/proposals/{proposal_id}/status")
def proposal_status(proposal_id: str, lang: str = "en", conn: sqlite3.Connection = Depends(get_conn)):
    attempt = db.row(conn, "SELECT attempt_id FROM payment_attempts WHERE proposal_id=?", (proposal_id,))
    if attempt:
        status.reconcile(conn, attempt["attempt_id"])
    return _proposal_view(conn, proposal_id, lang)


@app.post("/api/webhooks/cashfree")
async def cashfree_webhook(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    raw = await request.body()  # raw bytes before any JSON parsing
    code, body = status.process_webhook(conn, raw, request.headers.get("x-webhook-timestamp"),
                                        request.headers.get("x-webhook-signature"))
    return JSONResponse(status_code=code, content=body)


@app.post("/api/agent/message")
def agent_message(body: AgentMessageRequest, operator: str = Depends(require_session),
                  conn: sqlite3.Connection = Depends(get_conn)):
    return agent.handle_message(conn, operator, body.batch_id, body.text, body.language)


MAX_VOICE_BYTES = 10 * 1024 * 1024
NOT_HEARD = {"en": "I didn't catch that. Please try again or type your question.",
             "ta": "சரியாகக் கேட்கவில்லை. மீண்டும் முயற்சிக்கவும் அல்லது தட்டச்சு செய்யவும்.",
             "hi": "मैं समझ नहीं पाया। कृपया दोबारा बोलें या टाइप करें।"}


@app.post("/api/agent/voice")
def agent_voice(
    audio: UploadFile = File(...),
    batch_id: str = Form(...),
    language: str = Form(default="auto"),
    operator: str = Depends(require_session),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Spoken question -> Sarvam speech-to-text -> the same constrained agent as typed questions.

    Voice can only ask questions or prepare proposals; approval stays an authenticated click on the card.
    """
    s = get_settings()
    if not s.stt_live:
        raise NiptaError("STT_UNAVAILABLE", "Sarvam speech-to-text is not enabled; type the question instead", 409)
    content = audio.file.read()
    if not content or len(content) > MAX_VOICE_BYTES:
        raise NiptaError("AUDIO_REJECTED", "Audio is empty or larger than 10 MB", 400)
    try:
        stt = sarvam.transcribe(content, audio.filename or "question.webm", audio.content_type or "",
                                None if language == "auto" else language)
    except sarvam.SarvamError as exc:
        raise NiptaError("STT_ERROR", f"Speech-to-text failed: {exc}", 502)
    detected = sarvam.CODE_TO_LANG.get(stt["language_code"] or "")
    reply_lang = detected or (language if language in ("en", "ta", "hi") else "en")
    db.audit(conn, operator, "VOICE_TRANSCRIBED", None,
             {"language_code": stt["language_code"], "chars": len(stt["transcript"]),
              "transcript": stt["transcript"][:300], "request_id": stt["request_id"]}, batch_id=batch_id)
    if not stt["transcript"].strip():
        result = {"router": None, "router_note": None, "reply": NOT_HEARD[reply_lang], "tool_calls": [],
                  "stopped_early": False, "proposals": []}
    else:
        result = agent.handle_message(conn, operator, batch_id, stt["transcript"], reply_lang)
    out = {"transcript": stt["transcript"], "language_code": stt["language_code"], "reply_language": reply_lang,
           **result, "reply_audio_b64": None}
    if s.tts_live:
        try:  # the spoken reply is the server's own template text, never client-supplied text
            wav = sarvam.text_to_speech(result["reply"].replace("•", ""), reply_lang)
            out["reply_audio_b64"] = base64.b64encode(wav).decode()
        except Exception as exc:  # speech is optional; the text reply stands
            out["router_note"] = (out.get("router_note") or "") + f" (spoken reply unavailable: {type(exc).__name__})"
    return out


@app.post("/api/speech/tts")
def speech(body: TTSRequest, operator: str = Depends(require_session), conn: sqlite3.Connection = Depends(get_conn)):
    if not get_settings().tts_live:
        raise NiptaError("TTS_UNAVAILABLE", "Sarvam TTS is not enabled; text explanations only", 409)
    # Only server-rendered template text is spoken, never client-supplied text.
    if body.kind == "invoice":
        _invoice_or_404(conn, body.object_id)
        text = explain.explain_invoice(conn, body.object_id, body.language)
    else:
        approval.get_proposal(conn, body.object_id)
        text = explain.explain_proposal(conn, body.object_id, body.language)
    try:
        audio = sarvam.text_to_speech(text.replace("•", ""), body.language)
    except Exception as exc:  # noqa: BLE001
        raise NiptaError("TTS_ERROR", f"{type(exc).__name__}", 502)
    return Response(content=audio, media_type="audio/wav")


@app.get("/api/audit")
def audit_log(batch_id: str | None = None, object_id: str | None = None, limit: int = Query(200, le=1000),
              conn: sqlite3.Connection = Depends(get_conn)):
    sql = "SELECT * FROM audit_events"
    clauses, params = [], []
    if batch_id:
        clauses.append("(batch_id=? OR batch_id IS NULL)")
        params.append(batch_id)
    if object_id:
        clauses.append("object_id=?")
        params.append(object_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY event_id DESC LIMIT ?"
    params.append(limit)
    out = db.rows(conn, sql, tuple(params))
    for e in out:
        e["detail"] = json.loads(e.pop("detail_json"))
    return out


@app.post("/api/dev/provider-event")
def inject_provider_event(body: ProviderEventRequest, operator: str = Depends(require_session),
                          conn: sqlite3.Connection = Depends(get_conn)):
    if not get_settings().test_mode:
        raise HTTPException(404, "Not found")
    a = db.row(conn, "SELECT * FROM payment_attempts WHERE transfer_id=?", (body.transfer_id,))
    if a is None:
        raise NiptaError("NOT_FOUND", "Unknown transfer_id", 404)
    if a["mode"] != "TEST_PROVIDER":
        raise NiptaError("TEST_INJECTION_REFUSED",
                         "Test events are only accepted for TEST_PROVIDER attempts, never real sandbox payouts", 403)
    outcome = status.apply_provider_status(conn, a["attempt_id"], body.status, body.status_code, "TEST_INJECTION",
                                           {"label": "TEST_INJECTION", "operator_id": operator})
    return {"outcome": outcome, "proposal": _proposal_view(conn, a["proposal_id"])}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
