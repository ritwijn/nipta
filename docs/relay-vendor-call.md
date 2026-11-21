# Relay vendor call

When an invoice's bank account differs from the saved one, Nipta can ask Relay to phone the vendor, in their language, on the number **in our vendor registry** (never one printed on the invoice). Relay reports the answer back. The invoice stays **BLOCKED** whatever the vendor says; the call is evidence, not approval.

```
Nipta "Call vendor" ──POST──▶ Relay inbound webhook ──▶ voice call (Sarvam) ──▶ HTTP action ──POST──▶ Nipta /api/relay/vendor-call-result
```

## 1. Nipta side

```bash
cloudflared tunnel --url http://localhost:8000     # copy the https://….trycloudflare.com URL
```

In `.env`:

```
RELAY_VENDOR_CALL_URL=<inbound webhook URL from step 2>
RELAY_CALLBACK_SECRET=<long random string>
NIPTA_PUBLIC_URL=<tunnel URL>
NIPTA_DEMO_CALL_PHONE=<your phone, so the demo call rings you>   # never commit this
```

Restart with `./run.sh --reset` (seeds vendor phone numbers).

## 2. Relay side (merchant.cashfree.com → test mode → Relay)

1. **Agents → Create Agent**, or start from the *Failed payment* voice template and open it on the canvas.
2. **Trigger: Inbound Webhook.** Copy its URL into `RELAY_VENDOR_CALL_URL`.
3. **Secret properties:** add `NIPTA_SECRET` = the same value as `RELAY_CALLBACK_SECRET`.
4. **Voice call step:** call `customer.phone`, in `customer.language`, with the prompt below.
5. **HTTP action** after the call:
   - `POST` to `callback_url` from the trigger payload
   - header `x-nipta-relay-secret: <NIPTA_SECRET>`
   - body:
     ```json
     {"call_id": "<order_id>", "outcome": "<CONFIRMED_CHANGE | DENIED_CHANGE | NO_ANSWER | UNCLEAR>", "summary": "<one line>"}
     ```
6. **Test flow**, check **Runs**, then turn off **Draft**.

The exact variable syntax and how the call outcome reaches the HTTP action depend on Relay's canvas. Ask the Relay desk if a field isn't offered.

### Payload Nipta sends

```json
{
  "event": "vendor_bank_change_check",
  "order_id": "CALL_3f2a…",
  "amount": 6000.0,
  "customer": {"name": "Eastern Hardware", "phone": "+9190000000xx", "language": "kn-IN"},
  "invoice_number": "EH-040",
  "saved_account": "••1234",
  "printed_account": "XXXXXXXX9876",
  "callback_url": "https://….trycloudflare.com/api/relay/vendor-call-result"
}
```

### Call prompt

```
You are calling {customer.name} on behalf of Raghu Hardware Wholesale, Coimbatore. Speak only in {customer.language}.
Start by saying this call is recorded.

Say: we received invoice {invoice_number} for ₹{amount}. It asks us to pay a bank account ending {last 4 digits of printed_account}.
The account we have on file for you ends {last 4 digits of saved_account}.
Ask one question: did you change your bank account for this invoice?

Rules:
- Never ask for OTPs, PINs, passwords or full account numbers. Never read out a full account number.
- Do not promise or schedule any payment, and do not change anything during the call.
- Be polite and brief. If they are unsure, thank them and end the call.

Classify the answer as exactly one of:
CONFIRMED_CHANGE (they say the new account is theirs), DENIED_CHANGE (they say it is not theirs),
NO_ANSWER (no one picked up), UNCLEAR (anything else).
```

## What Nipta does with the result

| Outcome | Shown to the owner (en / kn / hi) | Invoice |
|---|---|---|
| `DENIED_CHANGE` | Likely fraud, do not pay | BLOCKED |
| `CONFIRMED_CHANGE` | Verify with the bank and update the saved account first | BLOCKED |
| `NO_ANSWER` / `UNCLEAR` | Stays blocked | BLOCKED |

Results need the shared secret, are recorded once per call (replays are ignored) and appear in the audit log. An unrecognised outcome is stored as `UNCLEAR`.
