# Nipta Implementation Guide

**Version:** 1.1
**Implements:** `architecture.md` v2.1, which implements `PRD.md` v4.1 ("Pay the right bill, to the right person")
**Date:** 21 November 2026
**Audience:** The solo builder, working inside the 3.5-hour event window

This guide turns the architecture into a build sequence. For each part it lists the files to create, the functions they expose, the database schema, configuration, and the tests that prove each step works. Where this guide and `architecture.md` disagree, the architecture wins. Function names and environment variables marked *proposed* are suggestions this guide adds, not contracts from the architecture.

### Changes in v1.1 (PRD v4.1)

Three small additions, none of which changes the build order or cut order:

- **Phase 2 (rules):** after findings are stored, `GET /api/batches/{id}` returns a `summary` with counts by decision and by finding code. Pure aggregation over `invoice_findings`.
- **Phase 3 (approval):** render the approval card in the operator's language from the existing templates, with `amount_words` generated from `amount_paise` by a deterministic function (*proposed* name: `amount_to_words(amount_paise, lang)`) for `ta-IN`, `hi-IN` and `en-IN`.
- **Tests:** add PRD T23–T28 to `backend/tests/`, next to T01–T22.

---

## 1. Ground rules

Keep these in mind while writing every file:

1. **All money is integer paise.** No `float` anywhere in amounts. Parse strings straight into paise.
2. **The model never decides.** Sarvam returns raw strings and picks read-only tools. Python rules decide eligibility, arithmetic, duplicates, beneficiary match, priority and payment state.
3. **One function moves money.** That function is `execute_approved_payout(proposal_id)` in `backend/payments/execution.py`. It is not an agent tool, and it runs only on an unexpired, authenticated approval whose snapshot hash still matches live data.
4. **When unsure, stop.** Missing fields, low confidence, ambiguous vendors and unknown provider outcomes become `REVIEW` or `UNKNOWN`. They never become a guess.
5. **Label the source of every claim.** Fixture vs live extraction, seeded vs live verification, and simulated vs sandbox payout are stored in columns and shown in the UI.
6. **Let the database enforce exactly-once.** Unique indexes, not application logic, stop a second attempt, a second settlement or a second reversal.

---

## 2. Repository layout

```text
nipta/
├── .env.example
├── README.md
├── integration_decisions.md          # results of the integration gate (§4)
├── requirements.txt
├── backend/
│   ├── __init__.py
│   ├── app.py                        # FastAPI routes, session auth, static UI mount
│   ├── config.py                     # flags, credentials, capability report
│   ├── db.py                         # schema, connection, transactions, audit writer
│   ├── schema.sql                    # DDL (§6)
│   ├── seed.py                       # vendors, beneficiaries, seeded verifications, fixture batch
│   ├── schemas.py                    # Pydantic models
│   ├── explain.py                    # en / ta / hi templates filled from stored state
│   ├── agent.py                      # tool allowlist, routing, call cap, grounded answers
│   ├── rules/
│   │   ├── normalize.py
│   │   ├── arithmetic.py
│   │   ├── duplicates.py
│   │   ├── beneficiaries.py
│   │   ├── decision.py
│   │   └── priority.py
│   ├── integrations/
│   │   ├── sarvam.py
│   │   ├── cashfree_payouts.py
│   │   └── cashfree_secureid.py
│   ├── payments/
│   │   ├── approval.py
│   │   ├── execution.py
│   │   └── status.py
│   └── tests/
│       ├── conftest.py               # temp SQLite, httpx.MockTransport fixtures
│       ├── test_rules.py             # T01–T10, T18–T20, T22
│       ├── test_payment_safety.py    # T11–T17, T21
│       └── test_safety_extra.py      # S01–S08
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── fixtures/
    ├── vendors.json                  # vendors, aliases, beneficiaries
    ├── invoices/                     # synthetic PDFs / images for INV-A … INV-F
    ├── extracted/                    # pre-extracted JSON per fixture (offline path)
    └── curveballs/                   # test-only adversarial set
```

**Dependencies (`requirements.txt`):** `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `httpx`, `python-multipart`, `pypdfium2`, `cryptography` (for the 2FA signature), `python-dotenv`, `pytest`.

The UI is a single no-build page served by FastAPI. This is the architecture's deliberate change from the PRD's Vite + React, made to avoid toolchain failures on venue Wi-Fi.

---

## 3. Build order

This follows PRD §12. Every phase ends with an exit check. Don't start the next phase until the current one passes.

| # | Window | Build | Exit check |
|---|---|---|---|
| 0 | 0:00–0:20 | Integration gate (§4), `config.py`, `/api/health` | `integration_decisions.md` records what is live; health endpoint reports it |
| 1 | 0:20–0:55 | `schema.sql`, `db.py`, `seed.py`, fixtures, `schemas.py`, `normalize.py`, Sarvam extraction adapter with fixture fallback | Fixture batch loads; at least one document becomes validated, normalized fields |
| 2 | 0:55–1:30 | `arithmetic.py`, `duplicates.py`, `beneficiaries.py`, `decision.py`, batch processing route | A and E `ELIGIBLE`, B and D `BLOCKED`, C and F `REVIEW` |
| 3 | 1:30–2:05 | `approval.py`, `execution.py`, `cashfree_payouts.py`, proposal/approve/execute routes, approval card UI | Exactly one approved sandbox payout request (or a labelled simulation) |
| 4 | 2:05–2:35 | `status.py`, polling, webhook route, audit timeline UI | The real provider state is shown, with no false `COMPLETED` |
| 5 | 2:35–3:30 | `agent.py`, `explain.py`, TTS, Secure ID, priority, curveball tests, rehearsal | Safety tests green; demo runs end to end |

**Cut order if behind** (PRD §12): (1) drop voice; (2) cut to three invoices, one clean and two traps; (3) skip priority; (4) keep polling and skip webhooks if signature checking isn't wired; (5) never fake verification or payout completion. Explicit approval and beneficiary checks are never cut.

---

## 4. Phase 0: integration gate

Spend the first 20 minutes confirming each item and write the answers to `integration_decisions.md`.

| Item | Check | If unavailable |
|---|---|---|
| Payouts V2 | Product enabled, sandbox balance, IP whitelist or 2FA key, whether a beneficiary must be pre-registered | `SIMULATED` branch, labelled in the UI; ask Cashfree engineers for enablement |
| Duplicate `transfer_id` | Response when an already-used `transfer_id` is sent again | Recovery path treats any duplicate-ID response as "reconcile", never as a failure |
| Webhooks | Public URL through a tunnel | Status polling is the source of truth |
| Secure ID BAV | Enabled, separate credentials, sandbox test account numbers | Seeded verification labelled "Seeded mock, not a Secure ID result"; real payouts refused while `NIPTA_REQUIRE_LIVE_VERIFICATION=true` |
| Sarvam vision | Enabled image model ID, image message format, latency on Tamil scans | Pre-extracted fixtures labelled `PRE_EXTRACTED_FIXTURE` |
| Sarvam TTS | Speaker names for `ta-IN` and `hi-IN` | Text-only explanations |
| Organizer rule | Does an initiated or pending sandbox payout count as "verified payout", or only `COMPLETED`? | Demo ends honestly at whatever state the provider reports |

### `backend/config.py`

Every provider is **off by default**. Load the values from `.env` and expose a capability report:

```python
@dataclass(frozen=True)
class Settings:
    sarvam_api_key: str | None
    sarvam_chat_model: str = "sarvam-105b"
    sarvam_vision_language: str = "en-IN"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_vision_enabled: bool = False
    sarvam_chat_enabled: bool = False
    sarvam_tts_enabled: bool = False

    cashfree_payouts_enabled: bool = False
    cashfree_base_url: str = "https://sandbox.cashfree.com"
    cashfree_payouts_client_id: str | None = None
    cashfree_payouts_client_secret: str | None = None
    cashfree_webhook_secret: str | None = None
    cashfree_2fa_public_key_path: str | None = None
    cashfree_secureid_enabled: bool = False
    cashfree_secureid_client_id: str | None = None
    cashfree_secureid_client_secret: str | None = None

    min_field_confidence: float = 0.85
    arith_tolerance_paise: int = 0
    verification_max_age_days: int = 30
    require_live_verification: bool = True
    approval_ttl_seconds: int = 600
    agent_max_tool_calls: int = 6
    test_mode: bool = False
    operator_pin: str = ...
    session_secret: str = ...
    database_path: str = "nipta.db"
    upload_dir: str = "uploads"

def capability_report(s: Settings) -> dict: ...   # served by GET /api/health
```

`GET /api/health` returns which providers are live, simulated or off. It never returns secrets.

### `.env.example`

```bash
# Sarvam AI
SARVAM_API_KEY=
SARVAM_VISION_ENABLED=false
SARVAM_VISION_LANGUAGE=en-IN    # Document AI extract job
SARVAM_CHAT_ENABLED=false
SARVAM_CHAT_MODEL=sarvam-105b
SARVAM_TTS_ENABLED=false
SARVAM_TTS_MODEL=bulbul:v3

# Cashfree Payouts V2
CASHFREE_PAYOUTS_ENABLED=false
CASHFREE_BASE_URL=https://sandbox.cashfree.com
CASHFREE_PAYOUTS_CLIENT_ID=
CASHFREE_PAYOUTS_CLIENT_SECRET=
CASHFREE_WEBHOOK_SECRET=
CASHFREE_2FA_PUBLIC_KEY_PATH=

# Cashfree Secure ID
CASHFREE_SECUREID_ENABLED=false
CASHFREE_SECUREID_CLIENT_ID=
CASHFREE_SECUREID_CLIENT_SECRET=

# Rules
NIPTA_MIN_FIELD_CONFIDENCE=0.85
NIPTA_ARITH_TOLERANCE_PAISE=0
NIPTA_VERIFICATION_MAX_AGE_DAYS=30
NIPTA_REQUIRE_LIVE_VERIFICATION=true
NIPTA_APPROVAL_TTL_SECONDS=600
NIPTA_AGENT_MAX_TOOL_CALLS=6
NIPTA_TEST_MODE=false

# App
NIPTA_OPERATOR_PIN=
NIPTA_SESSION_SECRET=
NIPTA_DATABASE_PATH=nipta.db
NIPTA_UPLOAD_DIR=uploads
```

`NIPTA_MIN_FIELD_CONFIDENCE`, `NIPTA_ARITH_TOLERANCE_PAISE`, `NIPTA_VERIFICATION_MAX_AGE_DAYS`, `NIPTA_REQUIRE_LIVE_VERIFICATION`, `NIPTA_TEST_MODE` and `CASHFREE_2FA_PUBLIC_KEY_PATH` come from the architecture. The other names are *proposed*, and the default values (0.85, 30 days, 600 s) are *proposed* starting points.

---

## 5. Phase 1: storage, fixtures, extraction

### 5.1 `backend/db.py`

```python
def connect() -> sqlite3.Connection         # WAL mode, foreign_keys=ON, row_factory
def init_db(conn) -> None                    # executes schema.sql
@contextmanager
def immediate(conn):                         # BEGIN IMMEDIATE … COMMIT / ROLLBACK
def audit(conn, actor, action, object_id, detail: dict, dedupe_key=None) -> None
```

Use `immediate()` for every write that matters for money: proposal creation, approval, attempt reservation, status transitions and postings. Set `isolation_level=None` so the code controls transactions explicitly.

### 5.2 Fixtures and `seed.py`

`fixtures/vendors.json` seeds vendors, aliases in Latin, Tamil and Devanagari scripts, one active beneficiary version per vendor, and the deadline fields (`statutory_risk`, `deadline_basis_verified`).

Fixture batch (architecture §12):

| Fixture | Language | Planted condition | Expected |
|---|---|---|---|
| INV-A Kaveri Fasteners KF-182 | English | Clean, matches saved beneficiary | `ELIGIBLE` |
| INV-B Tamil Tools TT-091 | Tamil | Stated ₹4,500, computed ₹4,300 | `BLOCKED ARITHMETIC_MISMATCH` |
| INV-C Kaveri Fasteners KF/182-A | Hindi | Same lines and amount as A; number and date reformatted | `REVIEW POSSIBLE_DUPLICATE` of A |
| INV-D Eastern Hardware EH-040 | English | Printed account differs from saved | `BLOCKED CHANGED_BENEFICIARY` |
| INV-E Coimbatore Packing CP-201 | Tamil | Clean, earlier due date | `ELIGIBLE`, ranked first |
| INV-F Sri Murugan Traders SMT-77 | English | Not in vendor registry | `REVIEW VENDOR_UNRESOLVED` |

Each fixture has a document in `fixtures/invoices/` and a pre-extracted JSON in `fixtures/extracted/` that uses the extraction contract (§5.4). Seed one `beneficiary_verifications` row per beneficiary with `source = 'SEEDED_MOCK'`.

**Rule:** detection code must never refer to fixture IDs. The curveball set in `fixtures/curveballs/` is used only by tests, to measure false holds as well as catches.

### 5.3 `backend/schemas.py`

```python
class LineItemRaw(BaseModel):
    description: str | None
    quantity: str | None
    unit_price: str | None
    line_total: str | None

class TaxLineRaw(BaseModel):
    label: str | None
    rate: str | None
    amount: str | None

class ExtractionRaw(BaseModel):            # what Sarvam (or the fixture) returns
    language: str | None
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    line_items: list[LineItemRaw] = []
    subtotal: str | None
    tax_lines: list[TaxLineRaw] = []
    stated_total: str | None
    amount_in_words: str | None
    bank_account: str | None
    ifsc: str | None
    notes: str | None
    field_confidence: dict[str, float] = {}

class Finding(BaseModel):
    code: str
    severity: Literal["BLOCK", "REVIEW", "INFO"]
    evidence: dict
    matched_invoice_id: str | None = None
```

Add API request and response models (proposal, approve, execute, status, agent message) in the same file. All money fields are `int` paise.

### 5.4 `backend/rules/normalize.py`

```python
def to_ascii_digits(s: str) -> str                 # Devanagari ०-९, Tamil ௦-௯ → 0-9
def parse_amount_paise(s: str | None) -> int | None  # "₹1,20,000.50", "1.2 lakh", "₹ 3,200/-"
def parse_quantity_milli(s: str | None) -> int | None
def parse_date(s: str | None) -> date | None       # dd/mm/yyyy, dd-mm-yy, dd.mm.yyyy; ambiguous → None
def normalize_invoice_number(s: str | None) -> str | None   # uppercase, A-Z0-9 only
def normalize_vendor_name(s: str | None) -> str | None      # casefold, strip punctuation
def words_to_paise(s: str | None) -> int | None    # English + Hindi; unparsable → None
```

Parsing rules:

- Accept Indian grouping (`1,20,000`) and Western grouping (`120,000`). Strip `₹`, `Rs`, `INR` and `/-`.
- `lakh`/`लाख` = 1,00,000 and `crore`/`करोड़` = 1,00,00,000. Don't lose powers of ten (T02).
- `words_to_paise` handles English (crore, lakh, thousand, hundred, "and", "only", paise) and Hindi (a lexicon for 1–99 plus सौ, हज़ार, लाख, करोड़). Tamil compound number words return `None`, which leads to `REVIEW`.
- Any function returns `None` rather than guessing.

### 5.5 `backend/integrations/sarvam.py` (extraction)

```python
def extract_invoice(document_path: Path, mime: str) -> tuple[ExtractionRaw, str]
    # returns (raw, source) where source ∈ {"LIVE_SARVAM_VISION", "PRE_EXTRACTED_FIXTURE"}
```

- PDFs are rendered to PNG pages with `pypdfium2` first.
- Call `POST https://api.sarvam.ai/v2/chat/completions` with header `api-subscription-key`, the confirmed image-capable model, and `response_format` set to the extraction schema. Confirm the image content-part shape at the gate.
- Ask for **raw strings only**. The prompt puts document text inside a delimited, untrusted data block and tells the model not to calculate anything.
- Validate the response with `ExtractionRaw`. If validation fails, record a `DOCUMENT_UNREADABLE` finding instead of retrying with looser parsing.
- When vision is disabled, load `fixtures/extracted/<name>.json` and label it `PRE_EXTRACTED_FIXTURE`.
- Use `httpx` with bounded timeouts (for example, connect 5 s, read 30 s).

### 5.6 Ingestion

`POST /api/batches` stores each upload under `NIPTA_UPLOAD_DIR` and records its SHA-256. It rejects unsupported extensions (only PDF, JPEG and PNG are allowed) and oversized files. Re-uploading an identical file links to the existing `documents` row and does not create a second payable.

---

## 6. Database schema (`backend/schema.sql`)

This has the 15 tables from architecture §8. The constraints are where the safety guarantees live, so keep them exactly as written.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS batches (
  batch_id     TEXT PRIMARY KEY,
  created_at   INTEGER NOT NULL,
  created_by   TEXT NOT NULL,
  source       TEXT NOT NULL CHECK (source IN ('UPLOAD','FIXTURE'))
);

CREATE TABLE IF NOT EXISTS vendors (
  vendor_id               TEXT PRIMARY KEY,
  display_name            TEXT NOT NULL,
  aliases_json            TEXT NOT NULL,          -- Latin, Tamil, Devanagari
  statutory_risk          INTEGER NOT NULL DEFAULT 0,
  deadline_basis_verified INTEGER NOT NULL DEFAULT 0,
  payment_terms_days      INTEGER
);

CREATE TABLE IF NOT EXISTS beneficiaries (
  beneficiary_id           TEXT NOT NULL,
  version                  INTEGER NOT NULL,
  vendor_id                TEXT NOT NULL REFERENCES vendors(vendor_id),
  account_holder_name      TEXT NOT NULL,
  bank_account_number      TEXT NOT NULL,         -- never leaves the server
  account_last4            TEXT NOT NULL,
  ifsc                     TEXT NOT NULL,
  cashfree_beneficiary_id  TEXT,
  active                   INTEGER NOT NULL DEFAULT 1,
  created_at               INTEGER NOT NULL,
  PRIMARY KEY (beneficiary_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_beneficiary_per_vendor
  ON beneficiaries(vendor_id) WHERE active = 1;

CREATE TABLE IF NOT EXISTS documents (
  document_id        TEXT PRIMARY KEY,
  batch_id           TEXT NOT NULL REFERENCES batches(batch_id),
  file_path          TEXT NOT NULL,
  mime               TEXT NOT NULL,
  sha256             TEXT NOT NULL,
  extraction_source  TEXT CHECK (extraction_source IN ('LIVE_SARVAM_VISION','PRE_EXTRACTED_FIXTURE')),
  raw_json           TEXT,
  created_at         INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_document_hash ON documents(sha256);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_id            TEXT PRIMARY KEY,
  batch_id              TEXT NOT NULL REFERENCES batches(batch_id),
  document_id           TEXT NOT NULL REFERENCES documents(document_id),
  vendor_id             TEXT REFERENCES vendors(vendor_id),   -- NULL when unresolved
  vendor_name_raw       TEXT,
  invoice_number_raw    TEXT,
  invoice_number_norm   TEXT,
  invoice_date          TEXT,
  due_date              TEXT,
  language              TEXT,
  subtotal_paise        INTEGER,
  tax_paise             INTEGER,
  stated_total_paise    INTEGER,
  computed_total_paise  INTEGER,
  words_total_paise     INTEGER,
  claimed_account       TEXT,                    -- as printed (may be masked)
  claimed_ifsc          TEXT,
  notes                 TEXT,                    -- data only, never instructions
  decision              TEXT NOT NULL DEFAULT 'REVIEW'
                        CHECK (decision IN ('ELIGIBLE','REVIEW','BLOCKED')),
  canonical             INTEGER NOT NULL DEFAULT 1,
  version               INTEGER NOT NULL DEFAULT 1,
  ingested_at           INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_canonical_invoice
  ON invoices(vendor_id, invoice_number_norm) WHERE canonical = 1;

CREATE TABLE IF NOT EXISTS invoice_lines (
  line_id             INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id          TEXT NOT NULL REFERENCES invoices(invoice_id),
  position            INTEGER NOT NULL,
  description         TEXT,
  quantity_milli      INTEGER,
  unit_price_paise    INTEGER,
  line_total_paise    INTEGER,
  computed_total_paise INTEGER
);

CREATE TABLE IF NOT EXISTS findings (
  finding_id          TEXT PRIMARY KEY,
  invoice_id          TEXT NOT NULL REFERENCES invoices(invoice_id),
  code                TEXT NOT NULL,
  severity            TEXT NOT NULL CHECK (severity IN ('BLOCK','REVIEW','INFO')),
  evidence_json       TEXT NOT NULL,
  matched_invoice_id  TEXT,
  status              TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED')),
  resolved_by         TEXT,
  resolved_reason     TEXT,
  created_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS beneficiary_verifications (
  verification_id      TEXT PRIMARY KEY,
  beneficiary_id       TEXT NOT NULL,
  beneficiary_version  INTEGER NOT NULL,
  source               TEXT NOT NULL CHECK (source IN ('LIVE_SECURE_ID_BAV','SEEDED_MOCK')),
  account_status       TEXT,
  name_match_result    TEXT,
  provider_ref         TEXT,
  verified_at          INTEGER NOT NULL,
  FOREIGN KEY (beneficiary_id, beneficiary_version)
    REFERENCES beneficiaries(beneficiary_id, version)
);

CREATE TABLE IF NOT EXISTS payment_proposals (
  proposal_id    TEXT PRIMARY KEY,
  invoice_id     TEXT NOT NULL REFERENCES invoices(invoice_id),
  snapshot_json  TEXT NOT NULL,
  snapshot_hash  TEXT NOT NULL,
  state          TEXT NOT NULL CHECK (state IN
                   ('READY_FOR_APPROVAL','AUTHORIZED','EXECUTED','STALE','EXPIRED','CANCELLED')),
  approver_id    TEXT,
  approved_at    INTEGER,
  expires_at     INTEGER NOT NULL,
  created_at     INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_proposal_per_invoice
  ON payment_proposals(invoice_id)
  WHERE state IN ('READY_FOR_APPROVAL','AUTHORIZED','EXECUTED');

CREATE TABLE IF NOT EXISTS payment_attempts (
  attempt_id            TEXT PRIMARY KEY,
  proposal_id           TEXT NOT NULL REFERENCES payment_proposals(proposal_id),
  transfer_id           TEXT NOT NULL,
  mode                  TEXT NOT NULL CHECK (mode IN ('CASHFREE_SANDBOX','SIMULATED')),
  status                TEXT NOT NULL CHECK (status IN
                          ('REQUEST_RESERVED','INITIATED','PENDING','COMPLETED',
                           'FAILED','UNKNOWN','REVERSED','SIMULATED')),
  cf_transfer_id        TEXT,
  last_provider_status  TEXT,
  last_provider_code    TEXT,
  dispatch_started_at   INTEGER,
  response_recorded_at  INTEGER,
  created_at            INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_attempt_per_proposal ON payment_attempts(proposal_id);
CREATE UNIQUE INDEX IF NOT EXISTS unique_transfer_id      ON payment_attempts(transfer_id);

CREATE TABLE IF NOT EXISTS attempt_transitions (
  transition_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id     TEXT NOT NULL REFERENCES payment_attempts(attempt_id),
  from_status    TEXT,
  to_status      TEXT NOT NULL,
  source         TEXT NOT NULL CHECK (source IN
                   ('EXECUTE','POLL','WEBHOOK','RECOVERY','TEST_INJECTION')),
  applied        INTEGER NOT NULL,              -- 0 when ignored by the guard
  evidence_json  TEXT NOT NULL,
  created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS financial_postings (
  posting_id    TEXT PRIMARY KEY,
  attempt_id    TEXT NOT NULL REFERENCES payment_attempts(attempt_id),
  invoice_id    TEXT NOT NULL REFERENCES invoices(invoice_id),
  kind          TEXT NOT NULL CHECK (kind IN ('SETTLEMENT','REVERSAL')),
  amount_paise  INTEGER NOT NULL,               -- negative for REVERSAL
  reversal_of   TEXT REFERENCES financial_postings(posting_id),
  created_at    INTEGER NOT NULL,
  UNIQUE (attempt_id, kind)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_reversal_per_posting
  ON financial_postings(reversal_of) WHERE reversal_of IS NOT NULL;

CREATE TABLE IF NOT EXISTS webhook_events (
  dedupe_key     TEXT PRIMARY KEY,
  transfer_id    TEXT,
  event_type     TEXT,
  raw_body       TEXT NOT NULL,
  outcome        TEXT NOT NULL CHECK (outcome IN
                   ('APPLIED','IGNORED_BACKWARD','DUPLICATE_IGNORED','UNMATCHED')),
  received_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
  event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  actor         TEXT NOT NULL,                  -- operator id, 'pipeline', 'agent', 'provider'
  action        TEXT NOT NULL,                  -- e.g. AGENT_TOOL, PROPOSAL_APPROVED
  object_id     TEXT,
  detail_json   TEXT NOT NULL,
  dedupe_key    TEXT,
  created_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS audit_dedupe
  ON audit_events(dedupe_key) WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS settings (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);
```

Column lists beyond the key constraints are this guide's *proposed* realisation of architecture §8.

---

## 7. Phase 2: rules

Each rule is a pure function. It takes normalized invoice data plus whatever DB lookups it needs, and returns `list[Finding]`. `POST /api/batches/{id}/process` and `POST /api/invoices/{id}/checks` call them in pipeline order (architecture §4) and then aggregate.

**Idempotency:** re-running the checks deletes the invoice's `OPEN` findings, inserts the new ones and recomputes the decision. `RESOLVED` findings are kept and carried forward. Each step writes an `audit_events` row with `actor = 'pipeline'`.

### 7.1 Extraction validation

Before any rule runs:

- If a monetary field (`stated_total`, `subtotal`, line totals, tax amounts) is `null`, raise `DOCUMENT_INCOMPLETE` (REVIEW).
- If a monetary field's confidence is below `NIPTA_MIN_FIELD_CONFIDENCE`, raise `LOW_CONFIDENCE_FIELD` (REVIEW).
- Store `notes` and show it, but never pass it into any decision.

### 7.2 `arithmetic.py`

```python
def check_arithmetic(inv, lines, tax_lines, tolerance_paise) -> list[Finding]
def check_words(stated_total_paise, amount_in_words) -> list[Finding]
```

| Condition | Finding | Severity |
|---|---|---|
| `qty_milli * unit_price_paise // 1000` ≠ `line_total_paise` (beyond tolerance) | `ARITHMETIC_MISMATCH`, evidence includes the line | BLOCK |
| Sum of lines ≠ subtotal | `ARITHMETIC_MISMATCH` | BLOCK |
| Subtotal + tax ≠ stated total | `ARITHMETIC_MISMATCH` | BLOCK |
| Any input missing, or ambiguous tax basis (tax-inclusive marker with no tax line) | `ARITHMETIC_UNVERIFIABLE` (*proposed code*) | REVIEW |
| Words parsed and ≠ figures | `WORDS_FIGURES_MISMATCH` | BLOCK |
| Words present but unparsable (including Tamil number words) | `WORDS_UNPARSABLE` (*proposed code*) | REVIEW |

Evidence always records the stated value, the computed value and the source line. For the line check, multiply first and then divide using integer division. Watch for a remainder: a non-zero remainder with tolerance 0 counts as a mismatch.

### 7.3 `beneficiaries.py`

```python
def resolve_vendor(conn, vendor_name_raw) -> tuple[str | None, list[Finding]]
def compare_beneficiary(conn, vendor_id, claimed_account, claimed_ifsc) -> list[Finding]
def check_verification(conn, vendor_id, now) -> list[Finding]
```

**Vendor resolution:** normalize the name and match it against every alias in every script. Exactly one match resolves the vendor. Zero or more than one gives `VENDOR_UNRESOLVED` (REVIEW). A different script alone never causes a hold.

**Account comparison** against the active beneficiary version:

- No printed details and an approved saved beneficiary: no finding (T09).
- A full printed account number is compared in full. A masked one (`XXXX6011`) is compared by its last four digits. IFSC is compared when printed.
- Any difference gives `CHANGED_BENEFICIARY` (BLOCK). Evidence shows masked values only.
- **Nothing on the invoice ever writes to `beneficiaries`.**
- No active beneficiary gives `NO_APPROVED_BENEFICIARY` (BLOCK; *proposed severity*).

**Verification sufficiency** uses the latest `beneficiary_verifications` row for the active version:

| Evidence | Finding | Severity |
|---|---|---|
| None, or older than `NIPTA_VERIFICATION_MAX_AGE_DAYS` | `VERIFICATION_MISSING` | REVIEW |
| `account_status = INVALID` | `VERIFICATION_FAILED` | BLOCK |
| `POOR_PARTIAL_MATCH` or `NO_MATCH` | `ACCOUNT_NAME_MISMATCH` | REVIEW |
| `MODERATE_PARTIAL_MATCH` | `ACCOUNT_NAME_MISMATCH` (weak) | REVIEW |
| `VALID` with `DIRECT_MATCH` or `GOOD_PARTIAL_MATCH` | none | n/a |

A passing verification never lifts a `CHANGED_BENEFICIARY` block.

### 7.4 `duplicates.py`

```python
def check_duplicates(conn, inv) -> list[Finding]
```

1. **Exact match:** the same resolved vendor with the same `invoice_number_norm`, or the same document SHA-256, gives `CONFIRMED_DUPLICATE` (BLOCK).
2. **Candidate score** against every earlier-ingested invoice from the same vendor:

   | Signal | Points |
   |---|---|
   | Same stated total | 3 |
   | Line-item token Jaccard ≥ 0.8 | 3 |
   | Invoice numbers related (one contains the other, or Levenshtein ≤ 2) | 2 |
   | Invoice dates within 14 days | 1 |

   A score of 6 or more gives `POSSIBLE_DUPLICATE` (REVIEW), with the matched invoice and its signals. Only the later-ingested invoice is flagged. Same amount alone (3 points) never qualifies (T06).
3. **Already paid:** if the matched invoice has a `SETTLEMENT` posting, add `ALREADY_PAID`. If it has an attempt in `REQUEST_RESERVED`, `INITIATED`, `PENDING` or `UNKNOWN`, add `PAYMENT_IN_FLIGHT`. Include the matched transfer ID and status in the evidence. An exact match to a paid invoice is BLOCK. A candidate match stays REVIEW, but the UI shows the payment evidence first.

Write a small `levenshtein(a, b)` helper. Line-item tokens are lowercased words from the descriptions, with digits normalized.

### 7.5 `decision.py`

```python
def decide(findings: list[Finding]) -> Literal["ELIGIBLE", "REVIEW", "BLOCKED"]
```

Any open BLOCK finding gives `BLOCKED`. Otherwise any open REVIEW finding gives `REVIEW`. Otherwise `ELIGIBLE`.

`POST /api/findings/{id}/resolve` (session required) can resolve **REVIEW** findings only. It records the reviewer and a reason, writes an audit row, and recomputes the decision. BLOCK findings have no override in the MVP. The agent cannot create, delete or re-grade findings.

### 7.6 `priority.py` (stretch)

```python
def payment_plan(conn, batch_id, cash_budget_paise) -> dict
```

- Candidates are `ELIGIBLE` invoices with no active or completed attempt.
- Sort by: statutory risk (only when `deadline_basis_verified = 1`), then overdue days descending, then earliest due date, then invoice ID.
- Fill the budget greedily. A skipped invoice gets "exceeds remaining budget" and the remaining amount. Excluded invoices carry their decision reason. If the deadline basis is unverified, show "Due-date basis unverified".
- Label the result a decision aid, not an optimal allocation and not legal or tax advice.
- Check: with the fixtures and a ₹9,000 budget, E (₹3,200) is selected, A (₹8,200) is skipped with ₹5,800 remaining, and B, C, D and F are excluded with reasons.

### Phase 2 exit check

Run the fixture batch. A and E are `ELIGIBLE`, B and D are `BLOCKED`, C and F are `REVIEW`. Every finding carries evidence the UI can render.

---

## 8. Phase 3: approval and execution

### 8.1 `payments/approval.py`

```python
def build_snapshot(conn, invoice_id, proposal_id, expires_at) -> dict
def snapshot_hash(snapshot: dict) -> str     # sha256(json.dumps(s, sort_keys=True, separators=(",", ":")))
def create_proposal(conn, invoice_id) -> Proposal
def approve(conn, proposal_id, operator_id, displayed_hash) -> Proposal
def assert_fresh(conn, proposal) -> None     # raises APPROVAL_STALE / APPROVAL_EXPIRED
```

Snapshot fields (architecture §6.1): `proposal_id`, `buyer_id`, `invoice_id`, `invoice_version`, `document_sha256`, `vendor_id`, `beneficiary_id`, `beneficiary_version`, `beneficiary_account_last4`, `amount_paise`, `currency`, `verification_ref`, `verification_source`, `risk_check_version`, `invoice_decision`, `open_findings_digest`, `dispatch_mode`, `expires_at`.

**`create_proposal`**, inside `immediate()`:
1. The invoice decision must be `ELIGIBLE`. Otherwise return `NOT_ELIGIBLE`.
2. Refuse if the invoice has any attempt in `REQUEST_RESERVED`, `INITIATED`, `PENDING`, `UNKNOWN` or `COMPLETED`.
3. Insert the proposal as `READY_FOR_APPROVAL`. If the partial unique index fires, return `PROPOSAL_EXISTS` (S01).
4. Set `dispatch_mode` to `CASHFREE_SANDBOX` only if Payouts is live **and** (verification is live **or** `NIPTA_REQUIRE_LIVE_VERIFICATION=false`). Otherwise use `SIMULATED`.

**`approve`** requires a session token and the hash the UI displayed. It rebuilds the snapshot from live rows, then:
- If the rebuilt hash ≠ the stored hash or ≠ the displayed hash: mark the proposal `STALE` and return `APPROVAL_STALE` (T12).
- If expired: mark it `EXPIRED` and return `APPROVAL_EXPIRED`.
- Otherwise set `AUTHORIZED`, `approver_id` and `approved_at`, and write an audit row.

### 8.2 `payments/execution.py`

```python
def transfer_id_for(proposal_id: str) -> str:
    return "NPT_" + hashlib.sha256(proposal_id.encode()).hexdigest()[:24]   # 28 chars, within the 40-char limit

def execute_approved_payout(conn, proposal_id, operator_id) -> Attempt
```

Steps, following architecture §6.2:

1. **Reserve** inside `immediate()`:
   - Re-run `assert_fresh`. The proposal must be `AUTHORIZED`.
   - If the live snapshot is `CASHFREE_SANDBOX` but the verification is not live and live verification is required, return `LIVE_VERIFICATION_REQUIRED`.
   - `INSERT` into `payment_attempts` with status `REQUEST_RESERVED` (or `SIMULATED` when the mode is simulated) and the fixed `transfer_id`.
   - If `UNIQUE(proposal_id)` fires, read and return the **existing** attempt (T13, S02).
   - Commit.
2. **Simulated mode:** stop here. There is no posting and the invoice is not marked paid. The UI shows "Simulated: no Cashfree transfer was made".
3. **Mark dispatch:** set `dispatch_started_at = now` and **commit before any network I/O**.
4. **Dispatch** through `cashfree_payouts.create_transfer(...)`:
   - HTTP 200: map the returned status (§9.2), record the transition, set `response_recorded_at`.
   - Timeout, network error or 5xx: `UNKNOWN`, keeping the same `transfer_id` (T14).
   - 4xx validation, auth or "not enabled": `FAILED` with the provider error code (no transfer was created).
   - A duplicate-`transfer_id` error: treat as a reason to reconcile (look up the status), never as `FAILED`.
5. Return the attempt state and `transfer_id`.

Never generate a new `transfer_id`. Never re-dispatch automatically.

### 8.3 `integrations/cashfree_payouts.py`

```python
def headers(request_id: str) -> dict
    # x-client-id, x-client-secret, x-api-version: 2024-01-01, x-request-id, [x-cf-signature]
def cf_signature() -> str | None
    # if CASHFREE_2FA_PUBLIC_KEY_PATH: base64(RSA-OAEP(public_key, f"{client_id}.{unix_ts}"))
def create_transfer(transfer_id, amount_paise, beneficiary) -> ProviderResult
    # POST {base}/payout/transfers
def get_transfer(transfer_id) -> ProviderResult | None
    # GET {base}/payout/transfers?transfer_id=…  (None when not found)
def verify_webhook(raw_body: bytes, timestamp: str, signature: str) -> bool
```

- Amounts go to Cashfree in rupees with two decimals. Convert from paise at the boundary with `Decimal`, never `float`.
- **Beneficiary:** if the saved beneficiary has a `cashfree_beneficiary_id`, send only that. Otherwise send `beneficiary_name`, `bank_account_number` and `bank_ifsc` taken from the snapshot's beneficiary version.
- `ProviderResult` holds `http_status`, `status`, `status_code`, `cf_transfer_id` and a redacted raw payload.
- Never log secrets or full account numbers.

### 8.4 Approval card UI

The card shows the vendor, masked beneficiary (`••6011`), amount, verification source label, dispatch mode label (`SANDBOX` or `SIMULATED`), any open-finding digest, expiry and the snapshot hash. The **Approve** button sends the hash shown on the card. **Execute** appears only after approval succeeds.

### Phase 3 exit check

Approving and executing INV-E produces exactly one attempt and one provider request (or a clearly labelled simulation). A double click returns the same attempt.

---

## 9. Phase 4: status, webhooks, postings

### 9.1 `payments/status.py`: transition guard

```python
ALLOWED = {
    "REQUEST_RESERVED": {"INITIATED", "UNKNOWN", "FAILED"},
    "INITIATED":        {"PENDING", "COMPLETED", "FAILED", "UNKNOWN"},
    "PENDING":          {"COMPLETED", "FAILED", "UNKNOWN"},
    "UNKNOWN":          {"PENDING", "COMPLETED", "FAILED"},
    "COMPLETED":        {"REVERSED"},
}

def apply_provider_status(conn, attempt_id, provider_status, provider_code, source, evidence) -> Attempt
```

Inside `immediate()`:
1. Map the provider status to a Nipta status (§9.2).
2. If the transition isn't in `ALLOWED`, record it in `attempt_transitions` with `applied = 0` and stop. This covers a late `PENDING` after `COMPLETED` (T15).
3. Otherwise update the attempt and record the transition with `applied = 1`.
4. Postings:
   - **COMPLETED:** insert a `SETTLEMENT` posting. `UNIQUE(attempt_id, kind)` makes a second insert impossible. Catch the integrity error and treat it as already posted (S03).
   - **REVERSED:** insert a `REVERSAL` posting with a negative amount and `reversal_of` set to the settlement. Add a `PAYMENT_REVERSED` REVIEW finding to the invoice. Never re-pay automatically (T17).
   - **FAILED:** no posting. Mark the proposal finished so a new proposal can be created after review (T16).

### 9.2 Provider status mapping (Payouts V2)

| Cashfree `status` / `status_code` | Nipta status |
|---|---|
| `RECEIVED`, `QUEUED`, `APPROVAL_PENDING`, `VALIDATION_PENDING`, `PENDING` | `PENDING` |
| `SUCCESS` + `SENT_TO_BENEFICIARY` | `PENDING` |
| `SUCCESS` + `COMPLETED` | `COMPLETED` |
| `FAILED`, `REJECTED`, `MANUALLY_REJECTED` | `FAILED` |
| `REVERSED` when current status is `COMPLETED` | `REVERSED` |
| `REVERSED` before `COMPLETED` | `FAILED` |
| anything else | `UNKNOWN` |

Settle **only** on `SUCCESS` + `COMPLETED`. A `TRANSFER_SUCCESS` webhook carrying `SENT_TO_BENEFICIARY` is not proof of credit (T21).

### 9.3 Polling: `GET /api/proposals/{id}/status`

- Terminal attempts (`COMPLETED`, `FAILED`, `REVERSED`, `SIMULATED`) return stored state.
- Non-terminal attempts call `get_transfer(transfer_id)` and `apply_provider_status(..., source="POLL")`.
- **Recovery** for `REQUEST_RESERVED`. Run it here and once at startup:

  | Stored condition | Action |
  |---|---|
  | `dispatch_started_at` empty | Look up the transfer. If not found, stay `REQUEST_RESERVED`; the owner may press Execute again, which dispatches the **same** `transfer_id`. If found, apply its status. |
  | `dispatch_started_at` set, no response recorded | Move to `UNKNOWN` and look up the transfer. If found, apply its status. If not found, stay `UNKNOWN` and show "Outcome not established; do not re-pay". |

  "Not found" is never treated as failure. Recovery never creates a `transfer_id` and never dispatches on its own.

The UI polls this route every few seconds while an attempt is non-terminal.

### 9.4 Webhook: `POST /api/webhooks/cashfree`

1. Read `await request.body()` **before** any JSON parsing.
2. Compute `base64(HMAC-SHA256(webhook_secret, x-webhook-timestamp + raw_body))` and compare with `x-webhook-signature` using `hmac.compare_digest`. On a mismatch, return 401 and audit it.
3. Set `dedupe_key = sha256(f"{type}|{transfer_id}|{status}|{status_code}|{event_time}")`. If the key exists in `webhook_events`, return 200 with `DUPLICATE_IGNORED`.
4. Find the attempt by `data.transfer_id`. An unknown ID is stored as `UNMATCHED`. Otherwise call `apply_provider_status(..., source="WEBHOOK")`.
5. Always return 200 after successful verification, because Cashfree retries any other status.

For the venue, expose the route with `cloudflared tunnel --url http://localhost:8000`. Without a tunnel, polling is the source of truth.

### 9.5 Test injection: `POST /api/dev/provider-event`

This route exists only when `NIPTA_TEST_MODE=true` and requires a session. It feeds a normalized event through `apply_provider_status(..., source="TEST_INJECTION")` for replay and reversal demos. It is refused for attempts in `CASHFREE_SANDBOX` mode, and the UI labels every injected event.

### 9.6 Audit timeline UI

`GET /api/audit` returns rows in time order. The UI shows: proposal created → approved (who, hash) → reserved (`transfer_id`) → each provider transition, with its source → posting. The current status is always the stored status, never something the UI infers.

### Phase 4 exit check

A sandbox payout's state shown in the UI matches what Cashfree reports. Replaying a webhook leaves a single posting. Nothing says "completed" before `SUCCESS` + `COMPLETED`.

---

## 10. Phase 5: agent, language, verification, polish

### 10.1 `agent.py`

```python
TOOLS = {
    "extract_invoice", "resolve_vendor", "check_arithmetic", "find_candidate_duplicates",
    "inspect_beneficiary_change", "get_verification_result", "rank_eligible_invoices",
    "prepare_payment_proposal", "get_payment_status", "explain_findings",
}

def handle_message(conn, operator_id, batch_id, text, language) -> AgentReply
```

- **Routing:** if Sarvam chat is enabled, call `POST /v2/chat/completions` with the read-only tool schemas. Otherwise use a keyword router for en, ta and hi. Label the reply with which router was used.
- **Validation for every call:** the tool must be in `TOOLS`, its arguments must pass Pydantic validation, and every ID it names must exist in the operator's batch. Otherwise reject and log the call (S08).
- **Cap:** at most `NIPTA_AGENT_MAX_TOOL_CALLS` (6) calls per message. When the cap is reached, answer from the evidence gathered so far and say the investigation stopped early (S07).
- `prepare_payment_proposal` only creates a `READY_FOR_APPROVAL` proposal. "Pay all" or "இதை செலுத்து" goes to proposal preparation and a pointer to the approval card. No tool reaches approve or execute (T11).
- Invoice text and notes enter the model context inside a delimited untrusted-data block (T20).
- **Grounding:** every number, ID, payee and status in the reply comes from `explain.py` templates filled from the DB. Model text never contains amounts.
- **Activity stream:** each call appends `audit_events` with `action = 'AGENT_TOOL'` and `batch_id`, `invoice_id`, `tool`, `started_at`, `duration_ms`, `outcome` (`OK`/`REJECTED`/`ERROR`), `reason_codes` and safe evidence pointers. The UI panel renders these rows as plain steps, for example "Rechecking total: stated ₹4,500, computed ₹4,300". It shows only calls that happened, with no secrets, full account numbers or raw provider payloads.

### 10.2 `explain.py`

These are fixed templates keyed by finding code, decision and payment status, in English, Tamil and Hindi. They are filled only from stored values. Format amounts in Indian grouping (`₹8,200`, `₹1,20,000`) and mask accounts (`••6011`).

### 10.3 TTS (optional)

`POST /api/speech/tts` takes the rendered template text for a stored state. It never takes client-supplied text. The adapter calls `POST https://api.sarvam.ai/text-to-speech` with `text`, `language_code` (`ta-IN`/`hi-IN`/`en-IN`), `model` (`bulbul:v3`) and an optional `speaker`. It returns `audios[0]` base64 WAV.

### 10.4 `integrations/cashfree_secureid.py`

```python
def verify_bank_account(account_number, ifsc, name) -> VerificationResult
    # POST {base}/verification/bank-account/sync  body: bank_account, ifsc, name
```

`POST /api/vendors/{id}/verify` (session required) runs it against the **saved** beneficiary version and stores a `beneficiary_verifications` row with `source = 'LIVE_SECURE_ID_BAV'`. It never runs against invoice-claimed details.

### 10.5 Session auth

`POST /api/auth/session` takes the operator PIN and returns an HMAC-signed, expiring token (`operator_id|expiry|sig`). Every mutating route requires it. The demo leaves read routes open, as the architecture's API table marks.

---

## 11. API checklist

| Route | Auth | Phase |
|---|---|---|
| `GET /api/health` | none | 0 |
| `POST /api/auth/session` | PIN | 1 |
| `POST /api/batches` | session | 1 |
| `POST /api/batches/{id}/process` | session | 2 |
| `GET /api/batches/{id}` | none (demo) | 2 |
| `POST /api/invoices` | session | 2 |
| `POST /api/invoices/{id}/checks` | session | 2 |
| `GET /api/invoices/{id}/findings` | none (demo) | 2 |
| `GET /api/invoices/{id}/source` | none (demo) | 2 |
| `POST /api/findings/{id}/resolve` | session | 2 |
| `POST /api/invoices/{id}/proposal` | session | 3 |
| `POST /api/proposals/{id}/approve` | session + hash | 3 |
| `POST /api/proposals/{id}/execute` | session | 3 |
| `GET /api/proposals/{id}/status` | none (demo) | 4 |
| `POST /api/webhooks/cashfree` | signature | 4 |
| `GET /api/audit` | none (demo) | 4 |
| `POST /api/dev/provider-event` | session, test mode | 4 |
| `GET /api/vendors/{id}/verification` | none (demo) | 5 |
| `POST /api/vendors/{id}/verify` | session | 5 |
| `GET /api/payment-plan` | none (demo) | 5 |
| `POST /api/agent/message` | session | 5 |
| `POST /api/speech/tts` | session | 5 |

**Stable error codes:** `ARITHMETIC_MISMATCH`, `WORDS_FIGURES_MISMATCH`, `POSSIBLE_DUPLICATE`, `CONFIRMED_DUPLICATE`, `CHANGED_BENEFICIARY`, `ACCOUNT_NAME_MISMATCH`, `VERIFICATION_MISSING`, `DOCUMENT_UNREADABLE`, `APPROVAL_STALE`, `APPROVAL_EXPIRED`, `NOT_ELIGIBLE`, `LIVE_VERIFICATION_REQUIRED`, `PROVIDER_STATUS_UNKNOWN`, plus `PROPOSAL_EXISTS` (from S01).

All money fields are integer paise. The browser receives last-four digits only.

---

## 12. Tests

`pytest` runs against a temporary SQLite file. Cashfree and Sarvam are replaced by `httpx.MockTransport`, so the real adapter code runs without network access.

### `conftest.py` fixtures (proposed)

- `db`: a fresh temp DB with schema and seed data loaded.
- `cashfree_mock`: a programmable transport. It can return a status sequence, raise a timeout, return 5xx/4xx, or return "not found".
- `sarvam_mock`: returns fixture extraction JSON or malformed output.
- `client`: a FastAPI `TestClient` with a valid session token.
- `sign_webhook(body, ts)`: a helper that produces a valid `x-webhook-signature`.

### PRD tests to test functions

| ID | File | What to assert |
|---|---|---|
| T01 | `test_rules.py` | Clean Tamil invoice: fields parse, or unreadable fields get specific REVIEW findings |
| T02 | `test_rules.py` | `parse_amount_paise` on Hindi digits and lakh notation keeps the powers of ten |
| T03 | `test_rules.py` | Words ≠ figures gives `WORDS_FIGURES_MISMATCH`, BLOCK, both values in evidence |
| T04 | `test_rules.py` | INV-B gives `BLOCKED`, stated 450000 and computed 430000 |
| T05 | `test_rules.py` | INV-C gives `POSSIBLE_DUPLICATE` linked to INV-A, REVIEW |
| T06 | `test_rules.py` | Two different invoices with the same amount both stay `ELIGIBLE` |
| T07 | `test_rules.py` | INV-D gives `CHANGED_BENEFICIARY`, BLOCK; `beneficiaries` unchanged |
| T08 | `test_rules.py` | Name mismatch gives `ACCOUNT_NAME_MISMATCH`, REVIEW |
| T09 | `test_rules.py` | No printed bank info with a verified saved beneficiary: no beneficiary finding |
| T10 | `test_rules.py` | Tax-inclusive with no tax line gives REVIEW, not BLOCK |
| T11 | `test_payment_safety.py` | Agent "pay it" makes zero provider calls and only creates a proposal |
| T12 | `test_payment_safety.py` | Change amount, invoice or beneficiary after approval: `APPROVAL_STALE` |
| T13 | `test_payment_safety.py` | Two execute calls give one attempt, one provider request, same `transfer_id` |
| T14 | `test_payment_safety.py` | Timeout gives `UNKNOWN`; no second `transfer_id` ever created |
| T15 | `test_payment_safety.py` | Webhook replay gives one posting; late `PENDING` after `COMPLETED` is ignored |
| T16 | `test_payment_safety.py` | `FAILED` gives no posting, and a new proposal is allowed |
| T17 | `test_payment_safety.py` | `REVERSED` after `COMPLETED` gives one negative `REVERSAL` row, settlement kept, `PAYMENT_REVERSED` finding |
| T18 | `test_rules.py` | ₹9,000 budget gives E selected, A skipped with ₹5,800 remaining |
| T19 | `test_rules.py` | Unverified deadline basis: "Due-date basis unverified", no statutory claim |
| T20 | `test_rules.py` | Prompt-injection note changes no decision and no tool is called because of it |
| T21 | `test_payment_safety.py` | `SUCCESS` + `SENT_TO_BENEFICIARY` stays `PENDING`, no posting |
| T22 | `test_rules.py` | Unfamiliar valid layout gets parsed or specific REVIEW findings, never an unsupported BLOCK |

### Extra safety tests (`test_safety_extra.py`)

| ID | Scenario | Expected |
|---|---|---|
| S01 | Two threads create proposals for one invoice | One proposal; the other gets `PROPOSAL_EXISTS` |
| S02 | Two threads execute one approved proposal | One attempt, one provider request |
| S03 | Webhook and poll report the same success at once | Exactly one `SETTLEMENT` |
| S04 | Stop after reservation, before dispatch | Recovery finds no transfer; re-execute uses the same `transfer_id` |
| S05 | Stop after `dispatch_started_at`, before response | `UNKNOWN`; lookup applied; no new `transfer_id` |
| S06 | Candidate duplicate of a paid invoice | REVIEW with `ALREADY_PAID` and the original transfer ID |
| S07 | Question needs more than six tool calls | Stops at six, answers from evidence, cap logged |
| S08 | Disallowed tool or unknown invoice ID | Rejected and logged; no state change |

For S01 to S03, use separate SQLite connections on separate threads against the same temp file. That way `BEGIN IMMEDIATE` and the unique indexes do the work, not Python locks.

One manual test runs a real sandbox payout once credentials are confirmed. Record its `transfer_id` and final status in `integration_decisions.md`.

---

## 13. Runtime and deployment

This implements architecture §14. It is one process on one machine, with no build step and no extra services.

### 13.1 Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in only what the integration gate (§4) confirmed
python -m backend.seed --reset    # optional: fresh DB with vendors, beneficiaries, fixture batch
```

`python -m backend.seed` prints the fixture batch and each invoice's decision. Use it as a quick check before going on stage. `--reset` deletes the database (including its `-wal` and `-shm` files) first, and `--no-process` loads the batch without running checks.

### 13.2 One process serves API and UI

```bash
./run.sh            # = uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1 (no reload)
./run.sh --reset    # reset to the fixture batch, then start
./run.sh --check    # print fixture decisions and provider status, then exit
```

- FastAPI serves `/api/*` and mounts `static/` at `/`, so `http://localhost:8000` is the whole app.
- **Bootstrap on startup** (`app._bootstrap`): create the schema if missing, seed the registry and fixture batch if the vendors table is empty, then run `status.recover_all()`. Recovery reconciles every `CASHFREE_SANDBOX` attempt left in `REQUEST_RESERVED`, `INITIATED`, `PENDING` or `UNKNOWN` (the §9.3 recovery table, with startup treating any started dispatch as interrupted).
- Run a **single worker**. SQLite `BEGIN IMMEDIATE` and the unique indexes protect concurrent requests inside this process. This is enforced: on startup, `runtime_lock.acquire()` takes an exclusive OS lock on `<database>.lock`. A second worker or a second copy pointed at the same database exits with "Another Nipta process is already using …".
- Don't use `--reload` during the demo. A reload in the middle of a dispatch is exactly the crash window §9.3 recovers from, and there's no reason to trigger it on stage.

### 13.3 Configuration

- `.env` holds the flags and credentials. It is git-ignored and loaded by `config.py` through `python-dotenv`.
- `.env.example` documents every variable (§4) and is committed. Every provider defaults to **off**.
- `GET /api/health` and the header badges in the UI show which providers are `LIVE`, `MISCONFIGURED` (enabled but missing credentials) or off with their fallback. Check this before every demo run.
- Change `NIPTA_OPERATOR_PIN` and set a long random `NIPTA_SESSION_SECRET` before any shared demo.
- `NIPTA_TEST_MODE=true` enables `/api/dev/provider-event` and the labelled `TEST_PROVIDER` mode (§15). Keep it `false` for any run that uses real sandbox payouts.

### 13.4 Webhooks at the venue

Cashfree can only deliver webhooks to a public URL. Expose the local server through a tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
# then register https://<tunnel-host>/api/webhooks/cashfree in the Cashfree Payouts dashboard
# and copy the webhook secret into CASHFREE_WEBHOOK_SECRET
```

- Without a tunnel, or without `CASHFREE_WEBHOOK_SECRET`, **status polling is the source of truth**. The UI polls `GET /api/proposals/{id}/status` every 4 s while an attempt is non-terminal, and each poll reconciles with `GET /payout/transfers`.
- The health badge reads `SIGNATURE_CHECK_READY` once a webhook secret is configured. Unsigned or mis-signed deliveries get a 401 and are audited.
- A tunnel URL changes every time `cloudflared` restarts, so re-register it if you restart.

### 13.5 Tests

```bash
pytest backend/tests -q
```

Tests run against a temporary SQLite file with `httpx.MockTransport` in place of Cashfree and Sarvam (§12). They never read `.env` values for providers and never touch the network.

### 13.6 Demo-day checklist

1. `./run.sh --check`, and confirm A/E `ELIGIBLE`, B/D `BLOCKED`, C/F `REVIEW` and the provider status.
2. `./run.sh --reset` and open `http://localhost:8000`.
3. Check the health badges match `integration_decisions.md`.
4. If webhooks are used: tunnel up, URL registered, signature badge ready. Otherwise rely on polling.
5. Sign in with the operator PIN, then run one dry pass: propose → approve → execute on INV-E.
6. Reset again before going on stage.

---

## 14. Definition of done

- [ ] `GET /api/health` honestly reports which providers are live, simulated or off.
- [ ] Fixture batch: A and E `ELIGIBLE`, B and D `BLOCKED`, C and F `REVIEW`, each with renderable evidence.
- [ ] Every label is visible in the UI: fixture vs live extraction, seeded vs live verification, simulated vs sandbox payout.
- [ ] Approval binds to the snapshot hash; any change gives `APPROVAL_STALE`.
- [ ] One attempt per proposal, one fixed `transfer_id`, no automatic re-dispatch.
- [ ] Settlement is posted only on `SUCCESS` + `COMPLETED`, exactly once; a reversal adds one compensating row.
- [ ] Webhook signature is checked on the raw body; replays are ignored; backward transitions are recorded and ignored.
- [ ] The agent can't reach approve or execute; it has a call cap; every reply number comes from a template.
- [ ] T01–T22 and S01–S08 pass.
- [ ] Demo rehearsed end to end, including an honest ending if the payout is still `PENDING`.

---

## 15. Implementation notes (deviations from this guide)

Recorded while building v1.0 of the code:

- **`TEST_PROVIDER` attempt mode.** With `NIPTA_TEST_MODE=true` and Payouts off, proposals use an in-process stand-in. It accepts the request (status `PENDING`), and only labelled `/api/dev/provider-event` events move it further. This makes the replay and reversal demo possible while keeping the guide's rules: injection is refused for `CASHFREE_SANDBOX` attempts, and `SIMULATED` attempts still never post. The UI and every explanation label it "Test provider: no money moved".
- **`FINISHED` proposal state.** Added for proposals whose attempt ended `FAILED`, `REVERSED` or `SIMULATED`, so the one-active-proposal index allows a fresh proposal after review.
- **Missing subtotal is allowed** when line totals are present. The lines then act as the subtotal (T22, unfamiliar layouts). A missing stated total or line total is still `DOCUMENT_INCOMPLETE`.
- **Tax-rate check.** Each tax line with a rate is recomputed against the subtotal (±1 paise rounding). A mismatch is an `ARITHMETIC_MISMATCH` with rule `TAX_RATE`.
- **Extra columns:** `documents.original_name`, `invoices.amount_in_words`, `invoices.ingested_seq`, `payment_attempts.invoice_id`, `amount_paise`, `last_error`, `last_checked_at`, `audit_events.batch_id`, and `beneficiary_verifications.name_at_bank`.
- **Extra codes:** `PAYMENT_EXISTS` (a proposal was refused because an attempt is in flight or completed), `NOT_AUTHORIZED`, `SECURE_ID_UNAVAILABLE`, `TTS_UNAVAILABLE`, `UPLOAD_REJECTED`.
- **Recovery window.** `/status` only treats a `REQUEST_RESERVED` attempt with `dispatch_started_at` set as interrupted after 45 s (longer than the 30 s HTTP timeout). Startup recovery treats it as interrupted at once.
- **Fixture documents** are printable `.txt` files. With Sarvam vision off, an upload whose file name matches a fixture (for example `inv_a.pdf`) uses that fixture's pre-extracted JSON, labelled `PRE_EXTRACTED_FIXTURE`. Any other upload becomes `DOCUMENT_UNREADABLE` (REVIEW).
- **Unconfirmed provider shapes** live in one place each: the transfer body in `cashfree_payouts.create_transfer`, 2FA padding in `cf_signature`, the Document AI extract schema in `sarvam.INVOICE_SCHEMA`, and TTS fields in `sarvam.text_to_speech`. Track them in `integration_decisions.md`.
