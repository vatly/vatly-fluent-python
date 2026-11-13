from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from vatly import RequestOptions, VatlyNotFoundError
from vatly.models import Order, Subscription
from vatly.resources import AsyncOneOffProducts, AsyncSubscriptionPlans, AsyncTestHelpers

from .builders import CheckoutBuilder, SubscriptionBuilder
from .contracts import CustomerProfile, FluentError, LocalSubscription, Wiring, required
from .customers import CustomerHandle, CustomerService
from .handles import OrderHandle, SubscriptionHandle
from .linkage import link_order, subscription_ids
from .webhooks import WebhookProcessor


@dataclass(frozen=True)
class CheckoutResolution:
    order: Order | None
    subscriptions: tuple[Subscription, ...] = ()
    pending_subscription_ids: tuple[str, ...] = ()
    legacy_plan_ids: tuple[str, ...] = ()
    unresolved_line_ids: tuple[str, ...] = ()


class TestHelpers:
    def __init__(self, api: AsyncTestHelpers) -> None:
        self._api = api

    async def fast_forward_renewal(
        self, id: str, body: dict[str, Any] | None = None, *, options: RequestOptions | None = None
    ) -> Subscription:
        return await self._api.fast_forward_renewal(id, body or {}, options=options)

    async def simulate_order_payment(
        self, id: str, body: dict[str, Any], *, options: RequestOptions | None = None
    ) -> Order:
        return await self._api.simulate_order_payment(id, body, options=options)

    async def advance_renewal(
        self, id: str, *, options: RequestOptions | None = None
    ) -> Subscription:
        return await self.fast_forward_renewal(id, options=options)

    async def force_renewal_paid(
        self, id: str, *, options: RequestOptions | None = None
    ) -> Subscription:
        return await self.fast_forward_renewal(id, {"paymentStatus": "paid"}, options=options)

    async def force_renewal_failed(
        self, id: str, failure_reason: str | None = None, *, options: RequestOptions | None = None
    ) -> Subscription:
        body: dict[str, Any] = {"paymentStatus": "failed"}
        if failure_reason is not None:
            body["failureReason"] = failure_reason
        return await self.fast_forward_renewal(id, body, options=options)

    async def force_order_payment_paid(
        self, id: str, *, options: RequestOptions | None = None
    ) -> Order:
        return await self.simulate_order_payment(id, {"paymentStatus": "paid"}, options=options)

    async def force_order_payment_failed(
        self, id: str, failure_reason: str | None = None, *, options: RequestOptions | None = None
    ) -> Order:
        body: dict[str, Any] = {"paymentStatus": "failed"}
        if failure_reason is not None:
            body["failureReason"] = failure_reason
        return await self.simulate_order_payment(id, body, options=options)


class Vatly:
    """Composes an existing async SDK client; the caller owns its lifecycle."""

    def __init__(self, wiring: Wiring) -> None:
        self.wiring = wiring
        self.api = wiring.api
        self.customers = CustomerService(wiring)

    def one_off_products(self) -> AsyncOneOffProducts:
        return self.api.one_off_products

    def subscription_plans(self) -> AsyncSubscriptionPlans:
        return self.api.subscription_plans

    def customer(self, id: str) -> CustomerHandle:
        return CustomerHandle(self.wiring, id)

    def checkout_builder(self, profile: CustomerProfile | None = None) -> CheckoutBuilder:
        return CheckoutBuilder(self.wiring, profile)

    def subscribe(self, profile: CustomerProfile | None = None) -> SubscriptionBuilder:
        return SubscriptionBuilder(self.wiring, profile)

    def subscription(self, record: LocalSubscription) -> SubscriptionHandle:
        return SubscriptionHandle(self.wiring, record)

    def order(self, id: str) -> OrderHandle:
        return OrderHandle(self.wiring, id)

    def webhook_processor(self) -> WebhookProcessor:
        return WebhookProcessor(self.wiring)

    def test_helpers(self) -> TestHelpers:
        return TestHelpers(self.api.test_helpers)

    async def customer_id_from_checkout(self, id: str) -> str | None:
        """Billing identity only. Never authorizes or assigns a host entity."""
        try:
            return (await self.api.checkouts.get(id)).customer_id
        except VatlyNotFoundError:
            return None

    async def claim_customer_from_checkout(self, id: str, host_id: str) -> bool:
        """Call only after the host verifies checkout ownership."""
        customer = await self.customer_id_from_checkout(id)
        if not customer:
            return False
        await self.customers.attribute(customer, host_id)
        return True

    async def reconcile_checkout(self, id: str, *, attempts: int = 3) -> CheckoutResolution:
        """Explicit bounded reads for trial/initial-order IDs; does not grant access.

        Caller authorizes the checkout and runs own-record writes in a transaction.
        No customerId fallback and no plan->subscription guessing. A pending ID can
        remain absent forever when the core deliberately skips subscription start.
        ``unresolved_line_ids`` are signup lines whose subscription core has not
        resolved yet; they may resolve on a later call, or never.
        ``legacy_plan_ids`` are plan lines on historical orders without a reference.
        """
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 10:
            raise FluentError("attempts must be an integer between 1 and 10")
        records = required(self.wiring.checkouts, "checkouts")
        record = await records.find(id)
        if record is None:
            raise FluentError("No host-owned checkout record exists")
        checkout = await self.api.checkouts.get(id)
        if record.testmode != checkout.testmode or checkout.testmode != (self.api.mode == "test"):
            raise FluentError("Checkout mode mismatch")
        if not checkout.order_id:
            return CheckoutResolution(None)
        order = await self.api.orders.get(checkout.order_id)
        await link_order(self.wiring, record, order)
        found = []
        pending = []
        for sub_id in subscription_ids(order):
            for attempt in range(attempts):
                try:
                    subscription = await self.api.subscriptions.get(sub_id)
                    if subscription.testmode != record.testmode:
                        raise FluentError("Subscription mode mismatch")
                    found.append(subscription)
                    break
                except VatlyNotFoundError:
                    if attempt == attempts - 1:
                        pending.append(sub_id)
                    else:
                        await asyncio.sleep(min(0.25 * 2**attempt, 2))
        legacy = tuple(
            dict.fromkeys(
                line.product_id
                for line in order.lines
                if line.product_type == "subscription_plan"
                and line.product_id
                and line.related_resources is None
            )
        )
        unresolved = tuple(
            line.id
            for line in order.lines
            if line.related_resources is not None and not line.related_resources.subscription_id
        )
        return CheckoutResolution(order, tuple(found), tuple(pending), legacy, unresolved)
