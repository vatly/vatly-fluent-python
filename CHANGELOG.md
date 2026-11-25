# Changelog

## 0.1.0a4 — 2026-11-25

- Require `vatly-api-python==0.1.0a4`.
- Stored refunds and refund webhook snapshots carry `Refund.metadata` (None when
  the refund was created without metadata).
- Checkout builders reject invalid metadata (non-string values, more than 50 keys,
  keys over 40 or values over 500 characters) and `trialDays` below 1 with
  `FluentError("Invalid checkout request")` before calling Vatly, instead of the
  API's `VatlyValidationError`.
- Behaviour change from the SDK: dictionary bodies passed to customer and
  subscription calls are validated before sending, so invalid input raises Pydantic's
  `ValidationError` instead of `VatlyValidationError`. Fields the API schema does not
  define are dropped, and `None` is sent only on nullable fields.

## 0.1.0a3 — 2026-11-13

- Link initial orders to their subscriptions through
  `lines[].relatedResources.subscriptionId` in `order.paid`/`checkout.paid` reactions
  and `reconcile_checkout`.
- `reconcile_checkout` reports signup lines with a still-null `subscriptionId` as
  `unresolved_line_ids`; `legacy_plan_ids` now lists only plan lines without
  `relatedResources` (historical orders).
- Require `vatly-api-python==0.1.0a3` from PyPI; drop the vendored SDK wheel.
- Refresh installation, European positioning and published-package documentation.

## 0.1.0a2 — 2026-11-12

- Use distribution `vatly-fluent-python`; imports remain `vatly_fluent`.
- Depend on the renamed SDK distribution `vatly-api-python` (import `vatly`).
- Derive grace expiry from period/trial deadlines, not cancellation request time.
- Share lifecycle mapping across signed reactions, sync and Django reconciliation;
  repair legacy expiry on sync and clear it after missed resume.
- Reject unknown grace expiry transactionally rather than guessing a deadline.
- Reject conflicting trial/cycle grace deadlines; the API omits core's trial flag.
- Require corrected SDK 0.1.0a2; refresh the unpublished bootstrap wheel.

## 0.1.0a1 — Unpublished development snapshot

> Alpha — under active development. Every alpha release may include breaking changes. Pin exact versions during alpha.

- Initial async-first Fluent Python port: builders, handles, customer services,
  pluggable storage, transactional webhook reactions and signed snapshots.
- Host-owned checkout/entity linkage, PR #2910-compatible subscription references,
  bounded recovery, historical plan-line reporting, and no customerId ownership fallback.
- Typed package, SQLite transaction/outbox example, tests, guides, CI, trusted
  publishing workflow and idempotent orb setup.
