<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/hero-dark.svg">
  <img alt="Nipta reads three bills (Coimbatore Packing in Kannada, Kaveri Fasteners in Hindi, Eastern Hardware in English), runs six checks on each, marks the first safe to pay, the second a possible copy and the third blocked for a changed bank account, then pays ₹3,200 once through the Cashfree sandbox after the owner approves." src="docs/img/hero-light.svg" width="880">
</picture>

<p align="center">
  <a href="#the-trailer">Trailer</a> ·
  <a href="#one-friday-in-nipta">How it works</a> ·
  <a href="#the-six-checks">Six checks</a> ·
  <a href="#where-sarvam-ai-and-cashfree-are-used">Sarvam AI and Cashfree</a> ·
  <a href="#run-it">Run it</a> ·
  <a href="#documents">Documents</a>
</p>

Raghu runs a hardware wholesale shop. Every Friday his suppliers' bills arrive in Kannada, Hindi and English, some printed,
some handwritten, some with bank details scribbled at the bottom. One of them had a new bank account on it. It looked fine.
It wasn't the supplier's.

**Nipta reads the bills, catches the bad ones, tells the owner in their own language what is safe to pay, and pays only the
bill they approve, to the account they saved, through Cashfree. When it can't be sure a payment is safe, it stops and says
why.**

Built for the **Cashfree × Sarvam AI Buildathon**, Bengaluru, 21 November 2026 (Problem Statement 3: *Pay the right bill,
to the right person*). Synthetic data and sandbox money only.

<p>
  <img src="docs/readme/tag-sarvam.svg" alt="Sarvam AI" height="22"> reads, listens and speaks ·
  <img src="docs/readme/tag-cashfree.svg" alt="Cashfree" height="22"> verifies and pays ·
  <img src="docs/readme/tag-nipta.svg" alt="Nipta code" height="22"> decides ·
  <img src="docs/readme/tag-you.svg" alt="You" height="22"> approve
</p>

---

## The trailer

https://github.com/user-attachments/assets/6925b763-9586-47de-86de-72062bc27522

<p align="center"><b>Two minutes, one Friday.</b> Plays above on GitHub · <a href="https://github.com/user-attachments/assets/0def9dce-348f-4ce8-a7d5-fead0f5463ff">tall version for phones (9:16)</a> · source files in <a href="docs/media">docs/media</a></p>

Lakshmi, who runs the shop with Raghu, walks through one Friday in the real app: the bill that doesn't add up, the changed
bank account, the bill sent twice, a question asked in Kannada, and one approved payment.

> **Her voice is Sarvam AI's [Bulbul v3](https://docs.sarvam.ai) text-to-speech** (speaker `kavitha`, Indian English), the
> same Sarvam TTS that speaks payment status inside Nipta. It is a generated voice, not a recording of a person. The screen
> footage is the actual app on the sample bills. The payment in it is a **labelled simulation**, because Cashfree Payouts was
> not switched on for that recording.

<details>
<summary><b>How the trailer is made</b> (re-make it in about 5 minutes)</summary>

<br>

The trailer is built by [`trailer/`](trailer) with [trailer-kit](https://github.com/IFAKA/trailer-kit), which records a web
page frame by frame in headless Chrome and edits it with [Remotion](https://www.remotion.dev).

| Step | File | What it does |
|---|---|---|
| Script | [`trailer/lines.json`](trailer/lines.json) | Lakshmi's 13 lines, the voice and speed, and the six steps shown on screen |
| Voice | [`trailer/voice.mjs`](trailer/voice.mjs) | <img src="docs/readme/tag-sarvam.svg" alt="Sarvam AI" height="16"> Calls Sarvam `POST /text-to-speech` with `bulbul:v3` for each line and measures loudness per frame for her lip movement |
| Clicks | [`trailer/driver.mjs`](trailer/driver.mjs) | Signs in, opens each bill, asks in Kannada, approves and sends one payment. Waits for each line to finish, so picture and voice stay together |
| Edit | [`trailer/src/`](trailer/src) | The shop counter, the Lakshmi figurine, zoom, pointer, subtitles and the steps strip, in the app's own colours |
| Music | [`trailer/music.mjs`](trailer/music.mjs) | A quiet tanpura drone with plucked notes, generated in code |

```bash
git clone https://github.com/IFAKA/trailer-kit ~/dev/trailer-kit && (cd ~/dev/trailer-kit && npm install)
# needs Docker running, SARVAM_API_KEY in .env, and the app's .venv (see "Run it")
trailer/make.sh                 # writes trailer/out/nipta-trailer-wide.mp4 and -tall.mp4
```

`make.sh` stops any running Nipta server and starts a fresh one with `./run.sh --reset`, so don't run it on a database you
want to keep. Another Bulbul voice: `node trailer/voice.mjs --force --speaker priya`, then `trailer/make.sh`.

</details>

---

## One Friday in Nipta

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/flow-dark.svg">
  <img alt="Pipeline: bills in Kannada, Hindi or English are read by Sarvam Document AI, deterministic rules decide in integer paise, the owner approves an exact snapshot with one click, and Cashfree Payouts V2 sends it once. Voice questions never pay; a changed bank account is blocked and Relay can call the vendor's registered number." src="docs/img/flow-light.svg" width="880">
</picture>

Models read and talk; **plain code decides every amount and every yes or no**; only a signed-in person can approve; only
Cashfree moves money.

<details>
<summary><b>The same flow as a diagram</b> (zoom and pan on GitHub)</summary>

```mermaid
flowchart TD
    A["You add bills<br/>PDF, JPG, PNG"] --> B["Sarvam AI Document AI<br/>reads vendor, items, totals, bank details"]
    B --> C{"Six checks<br/>in Nipta code, integer paise"}
    C -- "a check fails" --> H["BLOCKED or REVIEW<br/>with the reason, in your language"]
    C -- "all pass" --> D["Cashfree Secure ID<br/>confirms the saved account's holder"]
    D --> E["ELIGIBLE<br/>ordered by due date within your cash"]
    E --> F["Approval card<br/>exact amount, saved account, snapshot hash"]
    F -- "you click Approve" --> G["Cashfree Payouts V2<br/>one transfer, one stable transfer ID"]
    G --> S["Webhook or status lookup<br/>PENDING, SUCCESS, FAILED, REVERSED"]
    S --> R["Audit log<br/>bill, checks, approver, bank's answer"]
    H -. "Ask in Kannada, Hindi, English" .-> Q["Sarvam AI chat, Saaras, Bulbul"]
```

</details>

---

## The six checks

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/checks-dark.svg">
  <img alt="Six checks on invoice EH-040 from Eastern Hardware for ₹6,000: adds up, words match, not twice, known vendor and name confirmed pass; same bank account holds because the bill asks for an account ending 6655 while the saved one ends 1234. Verdict: don't pay this bill." src="docs/img/checks-light.svg" width="880">
</picture>

Every rule runs on every bill and the bill takes the most severe result: **safe to pay**, **needs a check** (payable only
after the owner clears each finding with a reason) or **don't pay** (no override).

<details>
<summary><b>What the sample batch catches</b></summary>

<br>

| Bill | Vendor | Language | Amount | Result | Why |
|---|---|---|---:|---|---|
| INV-A | Kaveri Fasteners | English | ₹8,200 | Eligible | Clean; matches the saved account |
| INV-B | Tamil Tools | Kannada | ₹4,500 | Blocked | Items add up to ₹4,300 |
| INV-C | Kaveri Fasteners | Hindi | ₹8,200 | Review | Same items and amount as INV-A, number and date rewritten |
| INV-D | Eastern Hardware | English | ₹6,000 | Blocked | Printed account differs from the saved one |
| INV-E | Coimbatore Packing | Kannada (Kannada digits) | ₹3,200 | Eligible | Clean; due first, so paid first |
| INV-F | Sri Murugan Traders | English | ₹2,750 | Review | Not in the approved vendor list |

The rules read the bill's fields, never its ID, and a set of unseen [`fixtures/curveballs/`](fixtures/curveballs) (lakh
numerals, a Devanagari vendor name, a prompt injection written on the bill, a missing bank line and more) checks that clean
bills are not held by mistake.

</details>

<details>
<summary><b>What the checks understand</b></summary>

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

## Where Sarvam AI and Cashfree are used

Every call to a partner API lives in [`backend/integrations/`](backend/integrations). Each one is **off by default** and
switched on in `.env`. While it is off, Nipta uses a fallback and **labels it on screen**: it never shows a mock as a live
result.

### <img src="docs/readme/tag-sarvam.svg" alt="Sarvam AI" height="24"> Sarvam AI: reading, listening, speaking

| Where in Nipta | Sarvam product | Code | Switch | When it's off |
|---|---|---|---|---|
| Reading each uploaded bill | **Document AI** extract job (or `sarvam-105b` chat with the image) | [`sarvam.py` `extract_invoice_doc_ai`](backend/integrations/sarvam.py), called from [`pipeline.py`](backend/pipeline.py) | `SARVAM_VISION_ENABLED` | Sample bills use pre-extracted fields, labelled `PRE_EXTRACTED_FIXTURE` |
| "Ask Nipta": choosing which read-only tool answers the question | **Chat completions**, `sarvam-105b` with tools | [`sarvam.py` `chat_tool_call`](backend/integrations/sarvam.py), used by [`agent.py`](backend/agent.py) | `SARVAM_CHAT_ENABLED` | A keyword router; the reply says `KEYWORD_ROUTER` |
| Asking by voice in Kannada, Hindi or English | **Saaras v4** speech-to-text | [`sarvam.py` `transcribe`](backend/integrations/sarvam.py), route `/api/agent/voice` in [`app.py`](backend/app.py) | `SARVAM_STT_ENABLED` | Typed questions only |
| Spoken answers and "Speak status" | **Bulbul v3** text-to-speech | [`sarvam.py` `text_to_speech`](backend/integrations/sarvam.py), route `/api/speech/tts` in [`app.py`](backend/app.py) | `SARVAM_TTS_ENABLED` | No sound; the text is still shown |
| Lakshmi's voice in the trailer | **Bulbul v3**, speaker `kavitha` | [`trailer/voice.mjs`](trailer/voice.mjs) | `SARVAM_API_KEY` | — |
| The vendor phone call, inside Relay | **Sarvam voice** in the Relay agent | [`docs/relay-vendor-call.md`](docs/relay-vendor-call.md) | `RELAY_VENDOR_CALL_URL` | No call button |

What Sarvam returns is treated as untrusted data: it is validated, uncertain fields become **REVIEW**, and **no model output
ever sets an amount or approves a payment**. Explanations come from fixed templates filled with the checked values.

### <img src="docs/readme/tag-cashfree.svg" alt="Cashfree" height="24"> Cashfree: verifying and paying

| Where in Nipta | Cashfree product | Code | Switch | When it's off |
|---|---|---|---|---|
| Confirming a saved account belongs to the vendor | **Secure ID**, bank account verification with name match | [`cashfree_secureid.py` `verify_bank_account`](backend/integrations/cashfree_secureid.py), used by [`verification.py`](backend/verification.py) | `CASHFREE_SECUREID_ENABLED` | Seeded record, labelled "Seeded mock, not a Secure ID result"; real payouts refuse it |
| Sending the approved payment | **Payouts V2**, standard transfer | [`cashfree_payouts.py` `create_transfer`](backend/integrations/cashfree_payouts.py), called once from [`execution.py`](backend/payments/execution.py) | `CASHFREE_PAYOUTS_ENABLED` | **SIMULATED**: no transfer is made, nothing is marked paid |
| Finding out what really happened | **Payouts V2** transfer status | [`cashfree_payouts.py` `get_transfer`](backend/integrations/cashfree_payouts.py), from [`status.py`](backend/payments/status.py) | with Payouts | — |
| Hearing back from Cashfree | **Payouts webhooks**, signature checked on the raw body | [`verify_webhook`](backend/integrations/cashfree_payouts.py), route `/api/webhooks/cashfree` in [`app.py`](backend/app.py) | `CASHFREE_WEBHOOK_SECRET` | Status lookups only |
| Signing requests when the IP isn't allow-listed | **2FA public key** (`x-cf-signature`) | [`cashfree_auth.py`](backend/integrations/cashfree_auth.py) | `CASHFREE_2FA_PUBLIC_KEY_PATH` | Unsigned requests |
| Calling a vendor whose bank details changed | **Relay** agent (Cashfree dashboard) | [`relay.py` `start_vendor_call`](backend/integrations/relay.py), route in [`app.py`](backend/app.py) | `RELAY_VENDOR_CALL_URL` | No call; the bill stays blocked either way |

All money is in the **Cashfree sandbox**, and all vendors, bills and accounts are made up. Payment Links are not used: they
collect money, they don't pay suppliers.

<details>
<summary><b>What's been confirmed live</b></summary>

| | Status |
|---|---|
| **Sarvam Document AI** | Live; verified on printed English and Hindi invoices |
| **Sarvam Saaras / Bulbul** | Live; voice verified in Kannada, spoken replies in Kannada and English |
| **Cashfree Payouts V2** | Live in sandbox: signed request accepted, a real transfer reached `PENDING` |
| **Cashfree Secure ID** | Built; live keys pending, so seeded checks are labelled "seeded mock" |
| **Cashfree Relay** | Built; needs the Relay agent and a tunnel, see [`docs/relay-vendor-call.md`](docs/relay-vendor-call.md) |

To see what's switched on in your own setup, run `./run.sh --check` or look at the app's header chip
(`Cashfree sandbox · n/7 live`).

</details>

---

## Rules it never breaks

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/never-dark.svg">
  <img alt="Four guarantees: no payment without the owner's click; no money to a changed bank account; no second payout on retry; nothing marked paid without Cashfree's confirmation." src="docs/img/never-light.svg" width="880">
</picture>

- **Models read and explain; code decides.** Amounts are integer paise in plain Python. A model can't decide that wrong
  arithmetic is fine.
- **A bank account printed on a bill is a claim, not an instruction.** Nipta pays only the account saved for that vendor,
  and a changed account blocks the bill even if it passes verification.
- **No payment without your click.** The approval is bound to the exact bill, account, amount and checks; if any of them
  change, you approve again. Voice can't approve.
- **One payment, however many retries.** One attempt per approval, one transfer ID, saved before the request goes out.
- **"Pending" is not "paid".** Only a verified success is posted as settled. A reversal is a new entry, and history is never
  deleted.
- **Mock, simulated and live are always labelled.**

<details>
<summary><b>What happens to a payment, state by state</b></summary>

```mermaid
stateDiagram-v2
    [*] --> READY_FOR_APPROVAL: eligible bill, frozen snapshot
    READY_FOR_APPROVAL --> AUTHORIZED: you approve (signed in, same snapshot)
    AUTHORIZED --> REQUEST_RESERVED: one attempt saved before any network call
    REQUEST_RESERVED --> INITIATED: Cashfree accepts
    REQUEST_RESERVED --> UNKNOWN: timeout, never re-sent with a new ID
    REQUEST_RESERVED --> FAILED: approval expired or account changed before sending, nothing sent
    INITIATED --> PENDING
    PENDING --> COMPLETED: verified success, posted once
    PENDING --> FAILED: verified failure, nothing posted
    UNKNOWN --> PENDING: checked with Cashfree using the same ID
    COMPLETED --> REVERSED: separate reversal entry, history kept
```

A double click, a replayed webhook or a late "pending" after success never creates a second payment or a second ledger
entry. Unique database indexes enforce this, not just application code. Reversals are found by signed webhooks and by
status lookups.

</details>

---

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # every provider starts OFF; add keys to switch them on
./run.sh --reset              # http://localhost:8000, operator PIN from NIPTA_OPERATOR_PIN
```

Press **Sample** to load the six bills, then **Run checks**.

<details>
<summary><b>Three-minute demo</b></summary>

1. **Add bills** in Kannada, Hindi and English (or **Sample**). The Today list sorts them into pay / check / don't pay.
2. Open **EH-040**: the bank account changed, so it's blocked. Optionally **Call vendor**.
3. Ask by voice: *"ಯಾವ ಬಿಲ್‌ಗಳು ಸರಿಯಾಗಿವೆ?"*. Nipta answers in Kannada.
4. **Review & pay** a safe bill, approve the exact amount, and watch Cashfree's real status.
5. Open **What Nipta did**: bill → checks → approver → provider events.

</details>

<details>
<summary><b>Tests</b></summary>

```bash
.venv/bin/python -m pytest -q
```

The suite covers the rules (arithmetic, GST, lakh numerals, Kannada words, duplicates, changed accounts, prompt injection on
a bill), payment safety (double click, timeout, webhook replay, late events, reversal, stale approval) and API hardening
(sessions, PIN lockout).

</details>

<details>
<summary><b>Configuration</b></summary>

| Variable | What it switches on |
|---|---|
| `SARVAM_API_KEY` | Any Sarvam AI call |
| `SARVAM_VISION_ENABLED`, `SARVAM_EXTRACTION_MODE` | Reading uploaded bills (`doc_ai` or `chat`) |
| `SARVAM_CHAT_ENABLED` | Sarvam chat choosing tools for "Ask Nipta" |
| `SARVAM_STT_ENABLED`, `SARVAM_TTS_ENABLED`, `SARVAM_TTS_SPEAKER` | Voice questions (Saaras) and spoken answers (Bulbul) |
| `CASHFREE_PAYOUTS_ENABLED` + client ID/secret, `CASHFREE_2FA_PUBLIC_KEY_PATH` | Real sandbox transfers instead of the labelled simulation |
| `CASHFREE_SECUREID_ENABLED` + client ID/secret | Live bank account verification |
| `CASHFREE_WEBHOOK_SECRET` | Accepting signed Payouts webhooks |
| `RELAY_VENDOR_CALL_URL`, `RELAY_CALLBACK_SECRET`, `NIPTA_PUBLIC_URL`, `NIPTA_DEMO_CALL_PHONE` | The Relay vendor call |
| `NIPTA_REQUIRE_LIVE_VERIFICATION` | Refuse real payouts unless Secure ID verified the account (default `true`) |
| `NIPTA_OPERATOR_PIN`, `NIPTA_SESSION_SECRET` | Sign-in. Set a long random secret, or sign-ins reset on every restart |

Keys stay in the git-ignored `.env` on the server and never reach the browser or the logs.

</details>

<details>
<summary><b>Repository</b></summary>

```text
backend/
  app.py              routes, sessions, static UI
  rules/              normalize, arithmetic, duplicates, beneficiaries, priority
  payments/           approval snapshot, execution, status and postings
  integrations/       sarvam, cashfree_payouts, cashfree_secureid, cashfree_auth, relay
  agent.py            one tool-using agent with no money-moving tool
  tests/              pytest suite
static/               one no-build page in the "Shop counter" look
fixtures/             synthetic vendors, invoices and curveballs
trailer/              the trailer's script, voice, clicks and edit
docs/                 PRD, architecture, implementation guide, Relay setup
docs/img/             animated README figures (rebuild: python docs/img/build_figures.py)
docs/readme/, docs/media/   tags, trailer poster and videos
```

</details>

**Stack:** FastAPI, Pydantic and SQLite on the server; a single page in plain HTML, CSS and JavaScript (no build step);
Sarvam AI and Cashfree through direct server-side APIs.

---

## Documents

| Document | Covers |
|---|---|
| [`docs/problemstatement.txt`](docs/problemstatement.txt) | The organizer's challenge, scenario and judging levels |
| [`docs/PRD.md`](docs/PRD.md) | What's built, demo bills, acceptance tests and their status, known gaps |
| [`docs/architecture.md`](docs/architecture.md) | Rules and finding codes, API and provider contracts, data model, payment states |
| [`docs/implementation.md`](docs/implementation.md) | Build order, files, schema, configuration, setup and tests |
| [`docs/relay-vendor-call.md`](docs/relay-vendor-call.md) | Setting up the Relay vendor call |
| [`integration_decisions.md`](integration_decisions.md) | What was confirmed with Cashfree and Sarvam at the event |

If the documents disagree, the problem statement owns the challenge, the PRD owns behaviour, the architecture owns contracts,
and the implementation guide owns build steps.
