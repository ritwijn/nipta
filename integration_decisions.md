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
| Sarvam invoice reading | ☑ documented, ☐ live-tested | Default: Document AI Extract job (`/doc-ai/v1/job/extract` → status → results) with our JSON schema. Alternative `chat` mode: `/v1/chat/completions`, `sarvam-105b`, `image_url` data URL. Handwriting support not stated on the Vision model page. | 2026-11-21 |
| Sarvam TTS | ☑ documented, ☐ live-tested | `POST /text-to-speech`, `text` ≤ 2,500 chars, `language_code`, `bulbul:v3`, speakers e.g. `shubh` (default) | 2026-11-21 |
| Sarvam STT (voice questions) | ☑ documented, ☐ live-tested | `POST /speech-to-text` multipart, `saaras:v4`, `language_code=unknown` auto-detects; WebM accepted; REST meant for < 30 s | 2026-11-21 |
| Sarvam chat (agent) | ☑ documented, ☐ live-tested | `POST /v1/chat/completions` (not v2); Bearer + `api-subscription-key`; tools supported | 2026-11-21 |
| Organizer rule: does INITIATED/PENDING count as "verified payout", or only COMPLETED? | ☐ unconfirmed | | |

## Manual sandbox payout test

| transfer_id | amount | final status | provider ref | notes |
|---|---|---|---|---|
| | | | | |
