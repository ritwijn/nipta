-- Nipta sprint schema. The unique indexes carry the exactly-once guarantees.

CREATE TABLE IF NOT EXISTS batches (
  batch_id     TEXT PRIMARY KEY,
  created_at   INTEGER NOT NULL,
  created_by   TEXT NOT NULL,
  source       TEXT NOT NULL CHECK (source IN ('UPLOAD','FIXTURE'))
);

CREATE TABLE IF NOT EXISTS vendors (
  vendor_id               TEXT PRIMARY KEY,
  display_name            TEXT NOT NULL,
  aliases_json            TEXT NOT NULL,
  statutory_risk          INTEGER NOT NULL DEFAULT 0,
  deadline_basis_verified INTEGER NOT NULL DEFAULT 0,
  payment_terms_days      INTEGER,
  phone                   TEXT
);

CREATE TABLE IF NOT EXISTS beneficiaries (
  beneficiary_id           TEXT NOT NULL,
  version                  INTEGER NOT NULL,
  vendor_id                TEXT NOT NULL REFERENCES vendors(vendor_id),
  account_holder_name      TEXT NOT NULL,
  bank_account_number      TEXT NOT NULL,
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
  original_name      TEXT,
  mime               TEXT NOT NULL,
  sha256             TEXT NOT NULL,
  extraction_source  TEXT CHECK (extraction_source IN ('LIVE_SARVAM_VISION','PRE_EXTRACTED_FIXTURE','STRUCTURED_JSON')),
  raw_json           TEXT,
  created_at         INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_document_hash ON documents(sha256);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_id            TEXT PRIMARY KEY,
  batch_id              TEXT NOT NULL REFERENCES batches(batch_id),
  document_id           TEXT NOT NULL REFERENCES documents(document_id),
  vendor_id             TEXT REFERENCES vendors(vendor_id),
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
  amount_in_words       TEXT,
  claimed_account       TEXT,
  claimed_ifsc          TEXT,
  notes                 TEXT,
  decision              TEXT NOT NULL DEFAULT 'REVIEW'
                        CHECK (decision IN ('ELIGIBLE','REVIEW','BLOCKED')),
  canonical             INTEGER NOT NULL DEFAULT 1,
  version               INTEGER NOT NULL DEFAULT 1,
  ingested_seq          INTEGER NOT NULL,
  ingested_at           INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_canonical_invoice
  ON invoices(vendor_id, invoice_number_norm) WHERE canonical = 1;

CREATE TABLE IF NOT EXISTS invoice_lines (
  line_id              INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id           TEXT NOT NULL REFERENCES invoices(invoice_id),
  position             INTEGER NOT NULL,
  description          TEXT,
  quantity_milli       INTEGER,
  unit_price_paise     INTEGER,
  line_total_paise     INTEGER,
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
  name_at_bank         TEXT,
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
                   ('READY_FOR_APPROVAL','AUTHORIZED','EXECUTED','STALE','EXPIRED','CANCELLED','FINISHED')),
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
  invoice_id            TEXT NOT NULL REFERENCES invoices(invoice_id),
  transfer_id           TEXT NOT NULL,
  amount_paise          INTEGER NOT NULL,
  -- TEST_PROVIDER exists only with NIPTA_TEST_MODE=true: an in-process stand-in driven by labelled test events.
  mode                  TEXT NOT NULL CHECK (mode IN ('CASHFREE_SANDBOX','SIMULATED','TEST_PROVIDER')),
  status                TEXT NOT NULL CHECK (status IN
                          ('REQUEST_RESERVED','INITIATED','PENDING','COMPLETED',
                           'FAILED','UNKNOWN','REVERSED','SIMULATED')),
  cf_transfer_id        TEXT,
  last_provider_status  TEXT,
  last_provider_code    TEXT,
  last_error            TEXT,
  dispatch_started_at   INTEGER,
  response_recorded_at  INTEGER,
  last_checked_at       INTEGER,
  provider_details_json TEXT,                 -- latest Cashfree transfer response (redacted, merged)
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
  applied        INTEGER NOT NULL,
  evidence_json  TEXT NOT NULL,
  created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS financial_postings (
  posting_id    TEXT PRIMARY KEY,
  attempt_id    TEXT NOT NULL REFERENCES payment_attempts(attempt_id),
  invoice_id    TEXT NOT NULL REFERENCES invoices(invoice_id),
  kind          TEXT NOT NULL CHECK (kind IN ('SETTLEMENT','REVERSAL')),
  amount_paise  INTEGER NOT NULL,
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
  actor         TEXT NOT NULL,
  action        TEXT NOT NULL,
  object_id     TEXT,
  batch_id      TEXT,
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

-- A person's request to change a vendor's saved bank account. Entered by an operator after confirming
-- with the firm outside the invoice; never created from invoice text. Approval creates a new beneficiary version.
CREATE TABLE IF NOT EXISTS bank_change_requests (
  request_id           TEXT PRIMARY KEY,
  vendor_id            TEXT NOT NULL REFERENCES vendors(vendor_id),
  beneficiary_id       TEXT NOT NULL,
  from_version         INTEGER NOT NULL,
  account_holder_name  TEXT NOT NULL,
  bank_account_number  TEXT NOT NULL,
  ifsc                 TEXT NOT NULL,
  confirmation_note    TEXT NOT NULL,
  account_status       TEXT,
  name_match_result    TEXT,
  name_at_bank         TEXT,
  provider_ref         TEXT,
  verified_at          INTEGER NOT NULL,
  state                TEXT NOT NULL CHECK (state IN ('PENDING_APPROVAL','VERIFICATION_FAILED','APPROVED','REJECTED')),
  requested_by         TEXT NOT NULL,
  decided_by           TEXT,
  decided_reason       TEXT,
  decided_at           INTEGER,
  created_at           INTEGER NOT NULL
);
