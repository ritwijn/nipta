"""Pydantic models for extraction output and API payloads. All money is integer paise."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["BLOCK", "REVIEW", "INFO"]
Decision = Literal["ELIGIBLE", "REVIEW", "BLOCKED"]
Language = Literal["en", "ta", "hi"]


class LineItemRaw(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    line_total: str | None = None


class TaxLineRaw(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str | None = None
    rate: str | None = None
    amount: str | None = None


class ExtractionRaw(BaseModel):
    """What Sarvam (or a pre-extracted fixture) returns: raw strings, never computed numbers."""

    model_config = ConfigDict(extra="ignore")
    language: str | None = None
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    line_items: list[LineItemRaw] = Field(default_factory=list)
    subtotal: str | None = None
    tax_lines: list[TaxLineRaw] = Field(default_factory=list)
    tax_inclusive: bool | None = None
    stated_total: str | None = None
    amount_in_words: str | None = None
    bank_account: str | None = None
    ifsc: str | None = None
    notes: str | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)


class Finding(BaseModel):
    code: str
    severity: Severity
    evidence: dict = Field(default_factory=dict)
    matched_invoice_id: str | None = None


# ---- API payloads ---------------------------------------------------------


class SessionRequest(BaseModel):
    pin: str


class SessionResponse(BaseModel):
    token: str
    operator_id: str
    expires_at: int


class ApproveRequest(BaseModel):
    snapshot_hash: str


class ResolveFindingRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class BankChangeRequest(BaseModel):
    account_number: str = Field(min_length=9, max_length=40)
    ifsc: str = Field(min_length=11, max_length=11)
    account_holder_name: str | None = Field(default=None, max_length=200)
    confirmation_note: str = Field(min_length=5, max_length=500)  # how the change was confirmed with the firm


class RejectBankChangeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class StructuredInvoiceRequest(BaseModel):
    batch_id: str | None = None
    extraction: ExtractionRaw


class AgentMessageRequest(BaseModel):
    batch_id: str
    text: str = Field(min_length=1, max_length=2000)
    language: Language = "en"


class TTSRequest(BaseModel):
    kind: Literal["invoice", "proposal"]
    object_id: str
    language: Language = "en"


class ProviderEventRequest(BaseModel):
    transfer_id: str
    status: str
    status_code: str | None = None


class AgentToolCall(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
