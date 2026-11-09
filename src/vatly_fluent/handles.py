from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from typing_extensions import Self
from vatly import RequestOptions
from vatly.models import Chargeback, Order, OrderLine, Refund

from .contracts import (
    LocalSubscription,
    Stored,
    SubscriptionState,
    Wiring,
    required,
    subscription_state,
)


class SubscriptionHandle:
    def __init__(self, wiring: Wiring, record: LocalSubscription) -> None:
        self._wiring = wiring
        self._record = record

    def model(self) -> LocalSubscription:
        return self._record

    def state(self) -> SubscriptionState:
        return subscription_state(self._record.ends_at)

    async def _save(self, data: dict[str, Any]) -> Self:
        self._record = await required(self._wiring.subscriptions, "subscriptions").update(
            self._record.resource.id, data
        )
        return self

    async def swap(
        self,
        plan_id: str,
        body: dict[str, Any] | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> Self:
        required(self._wiring.subscriptions, "subscriptions")
        response = await self._wiring.api.subscriptions.update(
            self._record.resource.id,
            {**(body or {}), "subscriptionPlanId": plan_id},
            options=options,
        )
        return await self._save({"resource": response})

    async def swap_and_invoice(
        self,
        plan_id: str,
        body: dict[str, Any] | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> Self:
        return await self.swap(
            plan_id,
            {**(body or {}), "applyImmediately": True, "invoiceImmediately": True},
            options=options,
        )

    async def swap_without_charge(
        self,
        plan_id: str,
        body: dict[str, Any] | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> Self:
        return await self.swap(
            plan_id,
            {**(body or {}), "applyImmediately": True, "prorationCharge": False},
            options=options,
        )

    async def resume(self, *, options: RequestOptions | None = None) -> Self:
        required(self._wiring.subscriptions, "subscriptions")
        response = await self._wiring.api.subscriptions.resume(
            self._record.resource.id, options=options
        )
        return await self._save({"resource": response, "ends_at": None})

    async def cancel(
        self, *, immediately: bool | None = None, options: RequestOptions | None = None
    ) -> None:
        params = {"immediately": immediately} if immediately is not None else {}
        await self._wiring.api.subscriptions.cancel(
            self._record.resource.id, params, options=options
        )

    async def sync(self) -> Self:
        required(self._wiring.subscriptions, "subscriptions")
        response = await self._wiring.api.subscriptions.get(self._record.resource.id)
        return await self._save(
            {"resource": response, "ends_at": response.ended_at or response.canceled_at}
        )

    async def scheduled_update(self) -> Any:
        return (await self._wiring.api.subscriptions.get(self._record.resource.id)).scheduled_update

    async def has_scheduled_update(self) -> bool:
        return (await self.scheduled_update()) is not None

    async def cancellation_reason(self) -> str | None:
        return (
            await self._wiring.api.subscriptions.get(self._record.resource.id)
        ).cancellation_reason

    async def update_billing(
        self, body: dict[str, Any] | None = None, *, options: RequestOptions | None = None
    ) -> str:
        payload = {
            "redirectUrlSuccess": self._wiring.redirect_url_success,
            "redirectUrlCanceled": self._wiring.redirect_url_canceled,
            **(body or {}),
        }
        from .contracts import FluentError

        if not payload["redirectUrlSuccess"] or not payload["redirectUrlCanceled"]:
            raise FluentError("Both billing redirect URLs are required")
        return (
            await self._wiring.api.subscriptions.create_billing_update_link(
                self._record.resource.id, payload, options=options
            )
        ).href


class OrderHandle:
    def __init__(self, wiring: Wiring, id: str) -> None:
        self._wiring = wiring
        self.id = id
        self._cached: Order | None = None

    async def model(self) -> Order:
        if self._cached is None:
            self._cached = await self._wiring.api.orders.get(self.id)
        return self._cached

    async def sync(self) -> Self:
        self._cached = await self._wiring.api.orders.get(self.id)
        return self

    async def invoice_url(self) -> str | None:
        link = (await self.model()).links.customer_invoice
        return link.href if link else None

    async def reversed_subtotal(self) -> Decimal:
        return Decimal((await self.model()).reversed_subtotal.root.value)

    async def refundable_subtotal(self) -> Decimal:
        return Decimal((await self.model()).refundable_subtotal.root.value)

    async def is_reversed(self) -> bool:
        return await self.reversed_subtotal() > 0

    async def is_fully_reversed(self) -> bool:
        return await self.is_reversed() and await self.refundable_subtotal() == 0

    async def is_partially_reversed(self) -> bool:
        return await self.is_reversed() and not await self.is_fully_reversed()

    async def refunds(self) -> list[Stored[Refund]]:
        return await self._wiring.refunds.list_for_order(self.id) if self._wiring.refunds else []

    async def chargebacks(self) -> list[Stored[Chargeback]]:
        return (
            await self._wiring.chargebacks.list_for_order(self.id)
            if self._wiring.chargebacks
            else []
        )

    async def lines(self) -> Sequence[OrderLine]:
        return (await self.model()).lines
