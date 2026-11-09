"""Verified signed snapshots only: built-in reactions never perform API reads."""

from __future__ import annotations

from typing import Any, TypeVar

from vatly.models import Chargeback, Checkout, Order, Refund, Subscription
from vatly.webhooks import KnownWebhookEvent, construct_webhook_event

from .contracts import (
    FluentError,
    LocalEvent,
    LocalSubscription,
    Repository,
    Stored,
    Wiring,
    required,
)
from .linkage import checkout_for, link_order, preserve_entity

R = TypeVar("R", Order, Refund, Chargeback)


class WebhookProcessor:
    def __init__(self, wiring: Wiring) -> None:
        self._wiring = wiring
        required(wiring.webhook_secret, "webhook_secret")
        required(wiring.webhook_calls, "webhook_calls")

    async def handle(
        self,
        raw_body: bytes | str,
        signature_header: str | None,
        *,
        tolerance: float = 300,
        now: float | None = None,
    ) -> bool:
        event = construct_webhook_event(
            raw_body,
            signature_header,
            required(self._wiring.webhook_secret, "webhook_secret"),
            tolerance=tolerance,
            now=now,
        )
        if event.testmode != (self._wiring.api.mode == "test"):
            raise FluentError("Webhook mode does not match the API key")

        async def work() -> None:
            if event.known:
                await self._react(event)
            for reaction in self._wiring.additional_webhook_reactions:
                if reaction.supports(event):
                    await reaction.handle(event)
            if self._wiring.events:
                await self._wiring.events.dispatch(event)

        return await required(self._wiring.webhook_calls, "webhook_calls").process(event, work)

    async def _persist(
        self, repository: Repository[Stored[R]], resource: R, resource_type: str
    ) -> tuple[bool, Stored[R] | None]:
        checkout = await checkout_for(self._wiring, resource_type, resource.id)
        if isinstance(resource, (Refund, Chargeback)):
            checkout = await checkout_for(self._wiring, "order", resource.original_order_id)
        candidate = checkout.entity if checkout else None
        existing = await repository.find_by_vatly_id(resource.id)
        if existing:
            return False, await repository.update(
                resource.id,
                {"resource": resource, "entity": preserve_entity(existing.entity, candidate)},
            )
        record = await repository.store(Stored[R](resource=resource, entity=candidate))
        return record is not None, record

    async def _react(self, event: KnownWebhookEvent) -> None:
        wiring = self._wiring
        resource = event.object
        name = event.event_name
        # Pydantic narrows object by resource, not by the envelope entityType.
        if name.startswith("subscription."):
            if not isinstance(resource, Subscription):
                raise FluentError("Subscription event has a non-subscription object")
            if not wiring.subscriptions or name == "subscription.update_scheduled":
                return
            existing = await wiring.subscriptions.find_by_vatly_id(resource.id)
            if existing is None and name != "subscription.started":
                return
            checkout = await checkout_for(wiring, "subscription", resource.id)
            entity = checkout.entity if checkout else None
            if existing:
                # Match JS's incremental mandate semantics; sync() reconciles removal.
                snapshot = (
                    resource
                    if resource.mandate is not None
                    else resource.model_copy(update={"mandate": existing.resource.mandate})
                )
                fields: dict[str, Any] = {
                    "resource": snapshot,
                    "entity": preserve_entity(existing.entity, entity),
                }
                if name == "subscription.resumed":
                    fields["ends_at"] = None
                elif name.startswith("subscription.canceled") or name in {
                    "subscription.cancellation_grace_period_completed",
                    "subscription.started",
                }:
                    fields["ends_at"] = resource.ended_at or resource.canceled_at
                await wiring.subscriptions.update(resource.id, fields)
            else:
                local = await wiring.subscriptions.store(
                    LocalSubscription(
                        resource=resource,
                        entity=entity,
                        ends_at=resource.ended_at or resource.canceled_at,
                    )
                )
                if local and wiring.events:
                    await wiring.events.dispatch(LocalEvent("local.subscription_created", local))
        elif name in {"order.paid", "order.payment_failed", "order.canceled"}:
            if not isinstance(resource, Order):
                raise FluentError("Order event has a non-order object")
            checkout = await checkout_for(wiring, "order", resource.id)
            if checkout:
                await link_order(wiring, checkout, resource)
            if not wiring.orders:
                return
            if name == "order.canceled":
                existing_order = await wiring.orders.find_by_vatly_id(resource.id)
                if existing_order:
                    await wiring.orders.update(resource.id, {"resource": resource})
            else:
                created, record = await self._persist(wiring.orders, resource, "order")
                if created and record and name == "order.paid" and wiring.events:
                    await wiring.events.dispatch(LocalEvent("local.order_created", record))
        elif name.startswith("refund."):
            if not isinstance(resource, Refund):
                raise FluentError("Refund event has a non-refund object")
            if wiring.refunds:
                await self._persist(wiring.refunds, resource, "refund")
        elif name in {"order.chargeback_received", "order.chargeback_reversed"}:
            if not isinstance(resource, Chargeback):
                raise FluentError("Chargeback event has a non-chargeback object")
            if wiring.chargebacks:
                await self._persist(wiring.chargebacks, resource, "chargeback")
        elif name == "checkout.paid":
            if not isinstance(resource, Checkout):
                raise FluentError("Checkout event has a non-checkout object")
            if not wiring.checkouts or not resource.order_id:
                return
            checkout_record = await wiring.checkouts.find(resource.id)
            if checkout_record is None:
                return
            if checkout_record.testmode != resource.testmode:
                raise FluentError("Checkout record mode mismatch")
            await wiring.checkouts.link(resource.id, "order", resource.order_id)
            if wiring.orders:
                order = await wiring.orders.find_by_vatly_id(resource.order_id)
                if order:
                    await link_order(wiring, checkout_record, order.resource)
