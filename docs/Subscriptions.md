# Subscriptions

Authorize a `LocalSubscription` from your repository, then call
`vatly.subscription(local)`. `swap(plan_id, body)` defaults to scheduled changes;
`swap_and_invoice` forces immediate application/invoicing; `swap_without_charge`
forces immediate application and disables the proration charge. Local data follows
the API response, so a scheduled swap does not prematurely replace the active plan.

`cancel(immediately=True)` sends the API mutation and waits for signed events to
update local cancellation state. `resume()` clears local `ends_at` after API success.
`sync()` saves the complete snapshot and sets `ends_at=ended_at or canceled_at`.
`scheduled_update`, `has_scheduled_update`, and `cancellation_reason` read remotely.
`update_billing(body)` returns a hosted URL; both redirect defaults/overrides are required.

`model()` is local. `state()`/`subscription_state(ends_at, now)` implements PHP's
endsAt predicates: active/valid through grace period, recurring before cancellation,
ended at or beyond the end time. **Check Vatly status separately before granting
access**; these convenience predicates do not account for paused/unpaid states.

## Initial subscription linking and PR #2910

The core PR is not merged at implementation time. Before it, an initial order may
have `subscription_plan` plus plan ID; there is no deterministic subscription ID
in that line. Fluent reports `legacy_plan_ids` and does not guess using customerId,
plan matching or quantity. A trial AUTH_REQUEST order may emit no merchant
order.paid event; explicitly reconcile the paid checkout's orderId.

After the change, initial lines have `subscription` plus intended subscription ID,
like renewal/update lines. `await vatly.reconcile_checkout(checkout_id)` follows
host records → checkout → order → distinct subscription IDs, retries each 404 up
to three times (configurable 1–10), and returns `CheckoutResolution` with resolved
resources, `pending_subscription_ids`, and legacy IDs. Two lines for the same plan
can identify different subscriptions; quantity never multiplies IDs.

An intended subscription may temporarily be unprojected or never exist for a
skipped start. Return pending state, enqueue bounded recovery, inspect status,
and do not grant access from an ID alone. The caller authorizes the checkout and
wraps local reconciliation writes in its driver transaction. Built-in webhook
reactions do not make API reads. Historical lines are not backfilled by core.
