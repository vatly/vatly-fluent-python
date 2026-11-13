# Vatly Fluent for Python

> **Alpha — under active development. Every alpha release may include breaking changes. Pin exact versions during alpha.**

Integrate billing with Vatly, the European Merchant of Record for AI, software and
digital products, without tying your application to a framework or database.
Fluent adds chainable checkout/subscription builders, customer and subscription
handles, pluggable persistence, and a signed webhook-to-domain-event pipeline on
top of `vatly-api-python`. Your application owns authentication, entitlements, storage,
transactions and jobs. Python 3.10+, fully typed, **async-first**.

## Install

```bash
pip install vatly-fluent-python==0.1.0a2 vatly-api-python==0.1.0a2
```

For development, run `.agents/setup` or `uv sync --locked`. The retained versioned
wheel under `vendor/` pins the SDK for uv; normal package metadata depends on the
exact SDK version from PyPI. It is not copied into Fluent's wheel.
See [provenance](vendor/README.md).

## First checkout

```python
from vatly import AsyncVatlyClient, RequestOptions
from vatly_fluent import Vatly, Wiring


async def start_subscription(plan_id: str, purchase_id: str):
    async with AsyncVatlyClient() as api:  # reads VATLY_API_KEY
        vatly = Vatly(
            Wiring(
                api=api,
                redirect_url_success="https://app.example.com/billing/return",
                redirect_url_canceled="https://app.example.com/billing/canceled",
            )
        )
        checkout = await (
            vatly.subscribe()
            .to_plan(plan_id)
            .with_quantity(3)
            .with_trial_days(14)
            .with_metadata({"purchaseId": purchase_id})
            .create(options=RequestOptions(idempotency_key=f"subscribe:{purchase_id}"))
        )
        return checkout.get_url()
```

This creates a **checkout**, not a subscription. Confirm payment through signed
webhooks, not redirects. No API calls happen until `create()`. Use a new builder
per purchase and persist a stable operation ID before sending. SDK errors pass
through unchanged; `FluentError` means invalid local input or missing wiring.

## Connect your own entities and storage

Wire `CheckoutRecords`, `Repository[LocalSubscription]`, `Repository[Stored[Order]]`,
and `WebhookCalls` to your application's database. For entity-linked checkouts:

```python
from vatly_fluent import EntityRef

# In an authorized handler, with vatly.wiring.checkouts configured:
builder = vatly.subscribe().to_plan(plan_id).for_entity(EntityRef("team", team_id))
```

Ownership follows **your stored checkout → order → subscription links**. It never
falls back to `customerId`, which is only billing identity and may be shared or null.
Metadata is correlation data, not authorization. A signed webhook authenticates
Vatly's snapshot; it does not authorize a browser-provided entity ID.

Current linkage follows `productType="subscription"` lines and their subscription
IDs; `subscription_plan` lines remain plan references. The revised core PR #2910
proposes additive `relatedResources.subscriptionId` instead of changing initial
product references. This alpha does not consume that new field yet: initial
subscription ownership remains unresolved. See the [compatibility notes](docs/Subscriptions.md).
Fluent never guesses a subscription from a customer, plan or quantity.

## Learn the integration

- [Getting started](docs/README.md) and [configuration](docs/Configuration.md)
- [Checkouts](docs/Checkouts.md), [customers](docs/Customers.md), [customer portal](docs/CustomerPortal.md)
- [Subscriptions](docs/Subscriptions.md) and [orders](docs/Orders.md)
- [Driver contracts](docs/CoreConcepts.md) and [transactional webhooks](docs/Webhooks.md)
- [Testing](docs/Testing.md), [porting decisions](PORTING.md), [release notes](CHANGELOG.md)
- [SQLite driver example](examples/sqlite_driver.py), with atomic dedup, rollback and outbox tests

## Develop

See the [development guide](docs/Development.md) for setup and contributor checks.

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build
uv run twine check dist/*
uv run python scripts/check_package.py
```

CI runs these checks across Python 3.10–3.14. Release workflows are prepared but
require PyPI trusted-publisher configuration and an explicitly published GitHub
release. No release is triggered by pushing `main`.
