# Gmail invoice draft policy

## Source message

Search the most recent sent email matching `has:attachment filename:pdf 請求書`. Use it only as the organization-specific template for recipient addresses, subject, and body style. Replace the billing period with the requested `YYYY-MM` and keep a generic fallback only when the prior subject or body is empty.

Do not print extracted addresses or body text. Script output reports counts and status only.

## Recipient plan

Read the expanded message-detail rows, not arbitrary addresses visible in the thread body.

- `To`: keep the first external prior To recipient as the sole primary recipient.
- `CC`: combine additional prior To recipients with prior CC recipients, preserving order and removing duplicates.
- `BCC`: require none. Stop if a prior invoice contains BCC instead of silently dropping or exposing it.
- Exclude the configured Google account and no-reply addresses from inferred external recipients.

When correcting an existing draft, treat recipient chips and active recipient inputs as recipients. Do not classify addresses quoted in the message body as recipient chips.

## Draft-only invariant

The scripts may open Compose, populate fields, attach a local PDF, wait for upload, use `Save & close`, search `in:drafts`, and inspect recipient chips. They must not locate or activate Send, schedule delivery, or use a sending API.

Successful output must end with both draft verification and `sent=false`.
