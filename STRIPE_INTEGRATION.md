# Stripe integration guide

[Project overview](README.md) | [Contributing](CONTRIBUTING.md) | [Security](SECURITY.md)

This document describes the current Stripe subscription boundary, configuration, and local testing flow for PredixLab API. It reflects `app/routers/subscription.py`, `app/services/stripe_service.py`, and related tests.

## Capabilities and boundaries

- Supports one monthly and one yearly recurring Stripe Price.
- `STRIPE_TRIAL_PERIOD_DAYS` applies only to users who have never used a trial or subscription. Its default is `30`; a trial is not guaranteed for every Checkout Session.
- Signed webhooks synchronize checkout completion, invoice payment, and subscription changes to the user's role and membership expiry.
- Cancellation sets `cancel_at_period_end=True`; it does not immediately remove current-period entitlement.
- Deleting a PredixLab account deletes identifiable Stripe subscriptions before deleting the Customer.

Unit tests mock the Stripe SDK; they do not replace end-to-end verification in Stripe test mode.

## Prerequisites

### 1. Create Stripe resources

Create monthly and yearly recurring Prices in the Stripe Dashboard and record their `price_...` IDs. Use test keys, test Prices, and test webhooks together; use live resources together in production. Do not mix modes.

### 2. Configure environment variables

```env
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_MONTHLY_PRICE_ID=price_...
STRIPE_YEARLY_PRICE_ID=price_...
STRIPE_TRIAL_PERIOD_DAYS=30
```

| Variable | Source | Used by |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | Developers → API keys | Stripe server SDK |
| `STRIPE_PUBLISHABLE_KEY` | Developers → API keys | Returned to clients by `/publishable-key` |
| `STRIPE_WEBHOOK_SECRET` | Webhook Endpoint signing secret | Validates `Stripe-Signature` |
| `STRIPE_MONTHLY_PRICE_ID` | Monthly Price | Monthly Checkout and billing-period detection |
| `STRIPE_YEARLY_PRICE_ID` | Yearly Price | Yearly Checkout and billing-period detection |
| `STRIPE_TRIAL_PERIOD_DAYS` | Local configuration | First-subscription trial days; `0` disables trials |

Keep secrets only in deployment variables or a local `.env`; never commit them.

### 3. Register a webhook

Production endpoint:

```text
https://<api-domain>/api/v1/subscription/webhook
```

Subscribe to:

- `checkout.session.completed`
- `invoice.paid`
- `customer.subscription.updated`
- `customer.subscription.deleted`

The application validates `Stripe-Signature`. Unrecognized events are logged and acknowledged without business-data changes.

## API

All paths start with `/api/v1/subscription`.

| Method and path | Authentication | Purpose |
| --- | --- | --- |
| `POST /create-checkout-session` | Bearer token | Create a Checkout Session |
| `GET /info` | Bearer token | Get the current user's subscription; returns `null` when absent |
| `POST /cancel` | Bearer token | Cancel at the end of the current period |
| `GET /prices` | Public | Read configured monthly/yearly Prices from Stripe |
| `GET /publishable-key` | Public | Return the configured publishable key |
| `POST /webhook` | Stripe signature | Receive and process Stripe events |

### Create a Checkout Session

```http
POST /api/v1/subscription/create-checkout-session
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "billing_interval": "month",
  "success_url": "https://app.example.com/billing/success?session_id={CHECKOUT_SESSION_ID}",
  "cancel_url": "https://app.example.com/billing"
}
```

`billing_interval` accepts only `month` or `year`. A successful response is:

```json
{
  "checkout_url": "https://checkout.stripe.com/c/pay/...",
  "session_id": "cs_test_..."
}
```

Users with an `active` or `trialing` subscription cannot create another valid Checkout Session. Clients must redirect to the server-provided `checkout_url`, not construct a Stripe URL themselves.

### Query and cancel

```http
GET /api/v1/subscription/info
Authorization: Bearer <access_token>
```

The response includes `status`, `billing_interval`, `current_period_end`, `trial_end`, and `cancel_at_period_end`.

```http
POST /api/v1/subscription/cancel
Authorization: Bearer <access_token>
```

After cancellation, Stripe stops renewal at period end. Subsequent state is authoritative only after Stripe webhook synchronization.

### Prices and publishable key

```http
GET /api/v1/subscription/prices
GET /api/v1/subscription/publishable-key
```

`/prices` returns only configured Prices that Stripe can read successfully; amounts are in Stripe's smallest currency unit. Neither public endpoint requires user authentication or may return a secret key.

## Webhook state synchronization

| Stripe event | Current handling |
| --- | --- |
| `checkout.session.completed` | Store subscription, billing period, and expiry; set role to `premium`; attempt a success email |
| `invoice.paid` | Set `active` and update the current-period end; attempt an email for renewal |
| `customer.subscription.updated` | Map `active`, `trialing`, `canceled`, `past_due`, and `unpaid`; synchronize role and expiry |
| `customer.subscription.deleted` | Reuse subscription-status handling; downgrade non-active states to `free` |

Email failure is logged and does not roll back completed Stripe or database state changes.

## Local testing

1. Start the API and use `STRIPE_SECRET_KEY` and both Price IDs from one Stripe test account.
2. Install and sign in to the [Stripe CLI](https://docs.stripe.com/stripe-cli).
3. Forward test events:

   ```bash
   stripe listen --forward-to localhost:8000/api/v1/subscription/webhook
   ```

4. Put the CLI's `whsec_...` value in local `STRIPE_WEBHOOK_SECRET`, then restart the API.
5. Complete Checkout with an official [Stripe test payment method](https://docs.stripe.com/testing), and check Stripe CLI output, API standard output, and the user's subscription state.

The focused repository test is:

```bash
uv run python -m pytest tests/routers/test_subscription.py -v
```

## Production checklist

- The API uses HTTPS and the webhook URL is publicly reachable.
- Secret key, Price IDs, and webhook secret belong to the same Stripe account and mode.
- Success and cancellation URLs are controlled frontend domains.
- The webhook subscribes to the four event types above and has no persistent Stripe Dashboard failures.
- `EMAIL_BACKEND` and ACS are configured, or membership email logging is explicitly accepted.
- `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are never exposed in clients, logs, error responses, or the repository.

## Troubleshooting order

1. Check delivery and HTTP status for the Event in the Stripe Dashboard.
2. Confirm the request has the raw-payload `Stripe-Signature` and verify `STRIPE_WEBHOOK_SECRET`.
3. Confirm the selected interval's Price ID is configured and available.
4. Inspect API standard output for `Stripe` logs; this repository has no fixed `app.log`.
5. Replay a test event with Stripe CLI to distinguish network, signature, and business-mapping failures.
