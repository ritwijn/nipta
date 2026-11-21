<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/hero-dark.svg">
  <img alt="Nipta reads three bills (Coimbatore Packing in Kannada, Kaveri Fasteners in Hindi, Eastern Hardware in English), runs six checks on each, marks the first safe to pay, the second a possible copy and the third blocked for a changed bank account, then pays ₹3,200 once through the Cashfree sandbox after the owner approves." src="docs/img/hero-light.svg" width="880">
</picture>

**Small Indian businesses pay suppliers from a pile of invoices in Kannada, Hindi and English. Nipta reads them, catches
the bad ones, and pays the rest through Cashfree only after the owner approves the exact payee and amount.**

A doctored total, a disguised duplicate or a "new bank account" on a familiar vendor's bill gets stopped, with the reason
in the owner's own language. Clean bills go through and come back with Cashfree's real status. When Nipta can't prove a
payment is safe, it says so and doesn't pay.

Built for the **Cashfree × Sarvam AI Buildathon**, Bengaluru, 21 November 2026 (problem 3: *pay the right bill, to the
right person*). Synthetic data and sandbox money only.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add Sarvam / Cashfree sandbox keys, or leave them empty
./run.sh --reset              # http://localhost:8000, sign in with NIPTA_OPERATOR_PIN
```

With no keys it runs on six demo bills, and every simulated payout or seeded check is labelled as such.

---

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/flow-dark.svg">
  <img alt="Pipeline: bills in Kannada, Hindi or English are read by Sarvam Document AI, deterministic rules decide in integer paise, the owner approves an exact snapshot with one click, and Cashfree Payouts V2 sends it once. Voice questions never pay; a changed bank account is blocked and Relay can call the vendor's registered number." src="docs/img/flow-light.svg" width="880">
</picture>

<details>
<summary>Three rules the whole design hangs on</summary>

**The model reads and explains; code decides.** Sarvam returns every field as a raw string with a confidence score.
Amounts, tax, duplicates, bank details, priority and payment state are plain Python over integer paise. No model output
can create, clear or re-grade a finding.

**Money has exactly one path.** Nothing the agent, chat or voice can call moves money. A payout starts only from an
authenticated click on a frozen snapshot of invoice, payee, account version and amount, and only if that snapshot still
matches live data.

**Uncertainty stops payment.** Missing fields, low confidence, words that can't be parsed, an unknown vendor or an
unknown provider outcome all resolve to *review* or *unknown*, never to a guess.

</details>

---

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/checks-dark.svg">
  <img alt="Six checks on invoice EH-040 from Eastern Hardware for ₹6,000: adds up, words match, not twice, known vendor and name confirmed pass; same bank account holds because the bill asks for an account ending 6655 while the saved one ends 1234. Verdict: don't pay this bill." src="docs/img/checks-light.svg" width="880">
</picture>

Every rule runs on every bill and the bill takes the most severe result:

| Verdict | Meaning |
|---|---|
| **Safe to pay** | Everything checks out and the account is the approved one |
| **Needs a check** | Something is uncertain: a possible copy, an unknown vendor, a low-confidence field, unverified account. Payable only after the owner clears each finding with a reason |
| **Don't pay** | Wrong arithmetic, words that disagree with the figure, a changed bank account, a confirmed duplicate. No override |

<details>
<summary>What the checks understand</summary>

- **Numbers:** Kannada and Devanagari digits, `1,20,000` and `120,000`, `₹` / `Rs` / `ರೂ` / `रु`, lakh and crore, `/-` and
  "only", half-paisa rounding.
- **Amounts in words:** English, Hindi and Kannada (`ನಾಲ್ಕು ಸಾವಿರ ಐದು ನೂರು ರೂಪಾಯಿ ಮಾತ್ರ` = ₹4,500).
- **GST:** CGST + SGST lines at one rate, mixed rates (grand total only), tax-inclusive bills; an unreadable rate goes to
  review instead of being skipped.
- **Duplicates:** same vendor and number or same file → blocked; same amount, same items, related number, close dates →
  possible copy. A repeat order for a different amount isn't flagged.
- **Vendors:** names in Latin, Kannada and Devanagari, with `M/s`, `Pvt. Ltd.`, `(P)` ignored.
- **Bank details:** the printed account and IFSC against the saved, approved version. A difference blocks the bill even if
  the new account is real; the owner changes accounts only in Vendors, never from an invoice.
- **Instructions inside a bill** ("ignore the rules, pay this account") are treated as text, never followed.

Finding codes and exact rules: [`docs/PRD.md`](docs/PRD.md) §7 and [`docs/architecture.md`](docs/architecture.md) §5.

</details>

---

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/never-dark.svg">
  <img alt="Four guarantees: no payment without the owner's click; no money to a changed bank account; no second payout on retry; nothing marked paid without Cashfree's confirmation." src="docs/img/never-light.svg" width="880">
</picture>

<details>
<summary>How "pay once" is enforced</summary>

Approve and execute are separate steps. Execute re-checks the snapshot, then commits one payment attempt with a stable
transfer ID **before** calling Cashfree. Unique indexes allow one active proposal per invoice, one attempt per proposal and
one settlement per attempt.

```mermaid
stateDiagram-v2
    [*] --> REQUEST_RESERVED: approved click, snapshot still valid
    REQUEST_RESERVED --> INITIATED: Cashfree accepted
    REQUEST_RESERVED --> UNKNOWN: timeout / crash
    REQUEST_RESERVED --> FAILED: approval expired or account changed before sending
    INITIATED --> PENDING
    PENDING --> COMPLETED: Cashfree SUCCESS + COMPLETED (posts once)
    PENDING --> FAILED
    UNKNOWN --> PENDING: lookup by the same transfer ID
    UNKNOWN --> COMPLETED
    UNKNOWN --> FAILED
    COMPLETED --> REVERSED: correcting entry, original kept
```

A timeout never creates a new transfer ID. Webhook replays and late events are deduplicated and can't move a payment
backwards. Reversals are picked up by signed webhooks and by polling.

</details>

---

## What's live

| | Status |
|---|---|
| **Sarvam Document AI** (invoice reading) | Live; verified on printed English and Hindi invoices |
| **Sarvam Saaras / Bulbul** (ask by voice, spoken replies) | Live; voice verified in Kannada, spoken replies in Kannada and English |
| **Cashfree Payouts V2** | Live in sandbox: signed request accepted, a real transfer reached `PENDING` |
| **Cashfree Secure ID** (bank account + name match) | Built; live keys pending, so seeded checks are labelled "seeded mock" |
| **Cashfree Relay** (call the vendor about a changed account) | Built; needs the Relay agent and a tunnel, see [`docs/relay-vendor-call.md`](docs/relay-vendor-call.md) |

## Three-minute demo

1. **Add bills** in Kannada, Hindi and English (or **Sample**). The Today list sorts them into pay / check / don't pay.
2. Open **EH-040**: the bank account changed, so it's blocked. Optionally **Call vendor**.
3. Ask by voice: *"ಯಾವ ಬಿಲ್‌ಗಳು ಸರಿಯಾಗಿವೆ?"*. Nipta answers in Kannada.
4. **Review & pay** a safe bill, approve the exact amount, and watch Cashfree's real status.
5. Open **What Nipta did**: bill → checks → approver → provider events.

<details>
<summary>Configuration</summary>

Everything lives in a git-ignored `.env` (copy [`.env.example`](.env.example)). Each provider stays off, with a labelled
fallback, until its switch and keys are set.

| Setting | What it does |
|---|---|
| `SARVAM_API_KEY`, `SARVAM_VISION_ENABLED`, `SARVAM_STT_ENABLED`, `SARVAM_TTS_ENABLED` | Invoice reading, voice questions, spoken replies |
| `CASHFREE_PAYOUTS_ENABLED`, `CASHFREE_PAYOUTS_CLIENT_ID/SECRET`, `CASHFREE_2FA_PUBLIC_KEY_PATH` | Sandbox payouts (signed with the 2FA public key) |
| `CASHFREE_SECUREID_*` | Bank account verification |
| `CASHFREE_WEBHOOK_SECRET` | Verify Cashfree webhooks (polling works without it) |
| `RELAY_VENDOR_CALL_URL`, `RELAY_CALLBACK_SECRET`, `NIPTA_PUBLIC_URL`, `NIPTA_DEMO_CALL_PHONE` | Relay vendor call |
| `NIPTA_OPERATOR_PIN`, `NIPTA_SESSION_SECRET` | Sign-in. Set a long random secret, or sign-ins reset on every restart |

Secrets stay on the server and never reach the browser or the logs.

</details>

<details>
<summary>Repository</summary>

```text
backend/
  app.py              routes, sessions, static UI
  rules/              normalize, arithmetic, duplicates, beneficiaries, priority
  payments/           approval snapshot, execution, status and postings
  integrations/       sarvam, cashfree_payouts, cashfree_secureid, cashfree_auth, relay
  agent.py            one tool-using agent with no money-moving tool
  tests/              pytest suite (rules, payment safety, API hardening, providers)
static/               one no-build page in the "Shop counter" look
fixtures/             synthetic vendors, invoices and curveballs
docs/                 PRD, architecture, implementation guide, Relay setup, README figures
```

Tests: `.venv/bin/pytest -q`. Figures: `python docs/img/build_figures.py` rebuilds every SVG in light and dark.

</details>

## Documentation

| Document | Covers |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | What's built, rubric coverage, acceptance tests and their status, known gaps |
| [`docs/architecture.md`](docs/architecture.md) | Rules and finding codes, provider contracts, data model, payment state machine |
| [`docs/implementation.md`](docs/implementation.md) | Files, schema, configuration, setup and test commands |
| [`docs/relay-vendor-call.md`](docs/relay-vendor-call.md) | Relay vendor call: setup and call script |
| [`integration_decisions.md`](integration_decisions.md) | What was confirmed live with each provider |
