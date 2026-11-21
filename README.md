# Nipta

**Pay the right bill, to the right person.**

Nipta reads a batch of supplier invoices in Tamil, Hindi and English, checks each one, explains what is safe and unsafe in the owner's language, and pays only the bill the owner explicitly approves, through a Cashfree Payouts sandbox transfer. When it cannot establish that a payment is safe, it stops and says why.

Built solo for the **Cashfree × Sarvam AI Buildathon**, Bengaluru, 21 November 2026 (Problem Statement 3).

> **Status:** This repository currently holds the specifications in `docs/`. Application code follows the build order in `docs/implementation.md`. Cashfree and Sarvam product access for the event account is still being confirmed (PRD §15).

## What it does

1. **Reads the batch.** Sarvam AI extracts vendor, invoice number, date, line items, tax, total, amount in words and printed bank details. Uncertain fields are flagged, never guessed.
2. **Checks every invoice** with deterministic code over integer paise:
   - arithmetic and words-versus-figures,
   - exact and disguised duplicates, including bills already paid,
   - bank details that differ from the approved vendor record,
   - vendor or account-holder name mismatch.
3. **Classifies** each invoice as ELIGIBLE, REVIEW or BLOCKED, with every finding and its source evidence, plus a batch scorecard of what was caught.
4. **Orders** eligible bills against a cash budget and due dates (stretch goal).
5. **Gets approval** on a card in the owner's language that shows the exact amount, approved recipient and warnings. Only an authenticated click approves; voice never does.
6. **Pays once** through Cashfree Payouts V2 and reports the provider's real status. Timeouts, failures and reversals never lead to a second payment or a false "completed".

## Principles

- AI reads and explains; deterministic code decides amounts, eligibility and payment state.
- Bank details printed on an invoice are untrusted evidence, never an instruction to change where money goes.
- No payout without an approval bound to the exact invoice, recipient and amount.
- Simulated or mock results are always labelled as such.

## Documentation

| Document | Owns |
|---|---|
| [`docs/problemstatement.txt`](docs/problemstatement.txt) | The organizer's challenge, scenario and judging levels |
| [`docs/PRD.md`](docs/PRD.md) (v4.1) | Product behaviour, rubric coverage map, demo fixtures, acceptance tests T01–T28, demo script |
| [`docs/architecture.md`](docs/architecture.md) (v2.1) | Rule definitions and finding codes, API and provider contracts, data model, payment state machine |
| [`docs/implementation.md`](docs/implementation.md) (v1.1) | Build order, files, schema, configuration, setup and test commands |

If the documents disagree, the problem statement owns the challenge, the PRD owns behaviour, the architecture owns implementation contracts, and the implementation guide owns build steps.

## Stack

Vite + React + TypeScript frontend · FastAPI + Pydantic + SQLite backend · Sarvam AI for invoice understanding, explanations and optional speech · Cashfree Payouts V2 and Secure ID verification via direct server-side APIs.

Secrets stay in a git-ignored `.env` on the server and never reach the frontend or logs. All vendors, invoices and accounts are synthetic, and all payments use the Cashfree sandbox.
