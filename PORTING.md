# Porting Vatly Fluent to Python

Primary: `vatly/vatly-fluent-js` at `81b80d9ba3dd621d7f66865166046272651836dc`,
including its `PORTING.md` and complete integration guides (read 2026-11-09).
Secondary: `vatly/vatly-fluent-php` at `db211e0cf8505017fcd2eea056628db2f19f5c8b`.

| JS responsibility | Python surface |
| --- | --- |
| Vatly / Wiring | `Vatly(Wiring(api=AsyncVatlyClient(...), ...))` |
| checkoutBuilder / subscribe | `checkout_builder` / `subscribe`, chainable builders |
| CustomerProfile | frozen `CustomerProfile` dataclass |
| checkout.getUrl | SDK-derived `CheckoutResult.get_url()` |
| CustomerService / CustomerHandle | `customers` / `customer(id)` |
| SubscriptionHandle / subscriptionState | `subscription(local)` / `subscription_state` |
| OrderHandle | `order(id)`, memoized full signed-compatible SDK resource |
| Reader/Writer/Repository | async `Repository[T]` protocol |
| Stored / LocalSubscription | Pydantic wrappers: `resource`, `entity`, optional `ends_at` |
| WebhookCalls / reactions / dispatcher | transactional async protocols |
| SDK testHelpers | guarded SDK delegation plus named convenience methods |
| Native SDK errors / FluentError | unchanged SDK errors, local `FluentError` |

Intentional differences: Python snake_case; async protocols rather than mixed sync/
async implementations; Decimal amounts instead of JS bigint minor units; wrappers
instead of intersecting SDK records with host fields. All remote wire keys remain
Vatly's camelCase. Dictionaries supplied to methods/overrides use wire keys.

Ownership deliberately follows host-owned **checkout records**, not the JS port's
customer-binding fallback. `EntityRef(kind, id)` supports teams/users/organizations.
`CheckoutRecords` maps checkout → order → subscription IDs. Customer bindings remain
billing identities and cannot establish entitlement ownership. Drivers enforce
immutable links and scope every lookup by tenant, storefront and API mode.

Pending core [PR #2910](https://github.com/sandervanhooft/vatlify/pull/2910) stamps
initial subscription order lines with intended subscription IDs. Historical
`subscription_plan` lines are preserved but never treated as subscription IDs.
Quantity does not multiply subscription IDs; two checkout plan lines can produce
distinct subscription IDs for the same plan. `reconcile_checkout` explicitly reads
checkout → order → subscriptions with bounded 404 retries. It returns pending IDs
(which may never resolve) and legacy plan IDs. It never grants access or selects a
subscription by customer/plan heuristics. Trial AUTH_REQUEST flows may have no
merchant order.paid event; reconcile the paid checkout's orderId explicitly.

Built-in webhooks preserve signed snapshots and make no API calls. Out-of-order
checkout/order/subscription events converge through persisted linkage; missing
snapshots require an explicit reconciliation job. Unknown events are retained and
dispatched. Optional reversal repositories gate storage, not dispatch. Hosts must
provide atomic dedup/audit/repository/outbox transactions and reconcile stale events.
