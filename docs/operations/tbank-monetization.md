# T-Bank monetization operations

Real RUB payment movement is off by default and must remain off until the owner
explicitly accepts the quality and commercial checks below. This service uses
T-Bank only. It does not use Telegram Stars or the optional T-Bank `Чеки`
service.

## Merchant setup

Configure the terminal key and password only in the server deployment secret
store. Set the HTTPS notification URL to the authenticated T-Bank webhook
endpoint, and set HTTPS success/failure return URLs. Enable recurrent payments
with T-Bank only after the consent and cancellation flow has been reviewed.
Never place merchant credentials, webhook signatures, raw T-Bank notifications,
customer card data, PANs, or images in source control, tickets, logs, or this
document.

## Mandatory pre-enable gate

Keep `PAYMENTS_PRODUCTION_ENABLED=false` and `PAYMENTS_OWNER_APPROVED=false`
until all of the following are complete:

1. Recalculate D-26 through D-28 using the live T-Bank merchant tariff, tax
   treatment, and actual OpenRouter cost per successfully delivered image.
2. Produce the exact-bound Phase 1 evidence at
   `eval/reports/phase-01.json`; it must validate with `verdict: pass` and no
   failed rules.
3. The owner deliberately accepts the quality evidence and commercial result,
   then sets both production flags to `true` in the deployment secret store.
4. Run the payment activation and full automated regression checks before
   deploying. A missing, malformed, incomplete, failing, or noncanonical
   report blocks T-Bank Init and Charge.

Do not enable either flag as a troubleshooting step. Both controls and valid
evidence are required for every Init and Charge request.

## Customer support and subscriptions

Monthly checkout must show the price, monthly period, and renewal consent.
Customers can disable future renewal through the bot cancellation control;
this does not remove separately purchased package balance. On a recurring
failure, keep the user informed with the fixed safe failure message, retry only
within the bounded lifecycle, and never create a second charge for the same
subscription period. Escalate a failed renewal only with durable order and
payment identifiers, never raw provider data.

Ultima creates a manager-contact lead rather than checkout. The manager contact
is deployment configuration, not hard-coded documentation or frontend data.

## Reconciliation and incident handling

Reconcile T-Bank events by the stored provider payment ID, provider order ID,
and local order ID. Confirm an entitlement only after signed, confirmed webhook
facts match the server-owned product, amount, terminal, and pending order.
Duplicate webhooks/callbacks must be treated as no-ops.

For a disputed or missing result, inspect the local order, payment, immutable
ledger, allowance reservation, generation job, and Telegram delivery receipt.
Allowances are consumed only after a delivery receipt; terminal failure or
unresolved ambiguity releases the reservation once. Do not regenerate blindly
after an ambiguous provider or delivery outcome.

When debugging, record only IDs, status, amounts, timestamps, and safe error
codes. Do not collect image bytes, base64, URLs, payment secrets, raw webhook
payloads, or card credentials. Rotate merchant credentials through T-Bank and
the deployment secret store if exposure is suspected, then keep payment flags
disabled until reconciliation and owner re-approval complete.
