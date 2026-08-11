# Shared Robokassa monetization operations

CarWrap Bot uses the single Robokassa shop owned by SeoSmith. The bot never
stores `MerchantLogin`, Password #1, Password #2, or allocates provider invoice
IDs. SeoSmith owns those values and the global `InvId` sequence.

## Configuration

Configure only these payment credentials in CarWrap:

```text
PAYMENT_GATEWAY_BASE_URL=https://seo-smith.ru/api/payments/gateway
PAYMENT_GATEWAY_SECRET=<CarWrap-specific 32+ character secret>
PAYMENT_GATEWAY_MAX_CLOCK_SKEW_SECONDS=300
```

The same secret must be stored in SeoSmith as
`PAYMENT_GATEWAY_CAR_WRAP_SECRET`. SeoSmith sends confirmations to:

```text
https://89-167-101-93.sslip.io/api/v1/payments/gateway/result
```

Set that URL in SeoSmith as `PAYMENT_GATEWAY_CAR_WRAP_RESULT_URL`. Robokassa
itself calls only SeoSmith's ResultURL:

```text
https://seo-smith.ru/api/payments/robokassa/result
```

## Payment lifecycle

CarWrap persists a local order first and sends its UUID, catalog-owned amount,
description, and recurring flag to SeoSmith in an HMAC-signed request. SeoSmith
allocates `InvId`, stores the correlation, and returns the Robokassa checkout
URL. A successful provider ResultURL is delivered back to CarWrap as a second
minimal HMAC-signed request. Only that request grants an allowance.

Recurring payments use the first successful monthly `InvId` as
`PreviousInvoiceID`. A gateway response that says the child operation was
submitted never grants a new period; only the later signed result does.

## Activation checks

With the shared gateway URL and HMAC secret configured, test with Robokassa
test mode enabled in SeoSmith:

1. Buy one package and one initial monthly product.
2. Confirm a duplicate SeoSmith callback does not duplicate ledger grants.
3. Confirm stale, missing, or invalid HMAC signatures are rejected.
4. Confirm mismatched order IDs, `InvId` values, and amounts are rejected.
5. After Robokassa enables recurring payments, submit one child operation and
   verify that only its result callback grants the next period.

Logs may contain local order IDs, provider invoice IDs, amounts, states, and
timestamps. Never log the gateway secret, signatures, raw callback bodies,
Robokassa credentials, card data, or image data.
