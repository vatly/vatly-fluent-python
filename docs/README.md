# Getting started

> **Alpha — under active development. Every alpha release may include breaking changes. Pin exact versions during alpha.**

Use Fluent for a billing integration; use the raw SDK for direct API calls. Fluent
depends on `vatly-api-python==0.1.0a2` and Python 3.10+. Follow the [quick start](../README.md)
for your first hosted checkout, then add storage for signed events and ownership.

| Guide | Responsibility |
| --- | --- |
| [Configuration](Configuration.md) | Keys, lifecycle, dependency bootstrap, release |
| [Core concepts](CoreConcepts.md) | Driver contracts, entities, scope and transactions |
| [Checkouts](Checkouts.md) | One-off/mixed/subscription builders and retries |
| [Customers](Customers.md) | Billing identity and immutable bindings |
| [Customer portal](CustomerPortal.md) | Short-lived hosted portal sessions |
| [Subscriptions](Subscriptions.md) | Swaps, cancellation, state, reconciliation |
| [Orders](Orders.md) | Lines, money and reversals |
| [Webhooks](Webhooks.md) | Signed snapshots, atomic processing and outbox |
| [Testing](Testing.md) | SQLite example, mock transport and local checks |
| [Porting](Porting.md) | Source provenance and intentional differences |

Pushes to `main` affecting `docs/**` notify `vatly/vatly-docs`, which imports
these guides automatically. The notifier requires the Actions secret
`VATLY_DOCS_DISPATCH_TOKEN` with Contents: write access to `vatly/vatly-docs`.
