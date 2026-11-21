# Integration decisions

Fill this in during the first 20 minutes of the event (implementation.md §4). Until an item is
confirmed here, Nipta keeps that provider OFF and uses its labelled fallback.

| Item | Status | Evidence / answer | Date |
|---|---|---|---|
| Payouts V2 enabled for the event account | ☐ unconfirmed | | |
| Sandbox balance available | ☐ unconfirmed | | |
| IP whitelist or 2FA public key (`x-cf-signature`) required | ☐ unconfirmed | | |
| Beneficiary must be pre-registered (`cashfree_beneficiary_id`) | ☐ unconfirmed | | |
| Standard transfer body: `beneficiary_details.beneficiary_instrument_details` field names | ☐ unconfirmed | | |
| Response when an already-used `transfer_id` is sent again | ☐ unconfirmed | Adapter treats 409 / "already exists" as reconcile | |
| Webhook public URL (tunnel) available | ☐ unconfirmed | Polling is the source of truth without it | |
| Webhook signature scheme matches HMAC-SHA256(timestamp + raw body), base64 | ☐ unconfirmed | | |
| Secure ID BAV enabled; credentials; sandbox test account numbers | ☐ unconfirmed | | |
| Secure ID `x-api-version` header required? | ☐ unconfirmed | | |
| Sarvam Vision: Document AI extract job (`/doc-ai/v1/job/extract`) enabled; `language` value for Tamil/Hindi invoices | ☐ unconfirmed | Adapter submits file + JSON schema, polls status, reads `result` + `annotations`. Check: max 20 pages per PDF; typical job time vs the 90 s limit | |
| Sarvam TTS speaker names for `ta-IN`, `hi-IN`; field name `language_code` | ☐ unconfirmed | | |
| Organizer rule: does INITIATED/PENDING count as "verified payout", or only COMPLETED? | ☐ unconfirmed | | |

## Manual sandbox payout test

| transfer_id | amount | final status | provider ref | notes |
|---|---|---|---|---|
| | | | | |
