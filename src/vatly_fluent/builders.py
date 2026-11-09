from __future__ import annotations

import math
from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from typing_extensions import Self
from vatly import RequestOptions
from vatly.models import BillingAddress, Checkout, CheckoutProduct, CreateCheckoutRequest, Metadata

from .contracts import CheckoutRecord, CustomerProfile, EntityRef, FluentError, Wiring, required


class CheckoutResult(Checkout):
    def get_url(self) -> str:
        return self.links.checkout_url.href


class CheckoutBuilder:
    def __init__(self, wiring: Wiring, profile: CustomerProfile | None = None) -> None:
        self._wiring = wiring
        self._entity: EntityRef | None = None
        self._data: dict[str, Any] = {
            "products": [],
            "redirectUrlSuccess": wiring.redirect_url_success,
            "redirectUrlCanceled": wiring.redirect_url_canceled,
        }
        if profile and profile.vatly_id:
            self._data["customerId"] = profile.vatly_id

    def for_entity(self, entity: EntityRef) -> Self:
        """Bind to an already-authorized entity through host-owned checkout records."""
        self._entity = entity
        return self

    def with_redirect_url_success(self, url: str) -> Self:
        self._data["redirectUrlSuccess"] = url
        return self

    def with_redirect_url_canceled(self, url: str) -> Self:
        self._data["redirectUrlCanceled"] = url
        return self

    def with_metadata(self, metadata: Metadata | dict[str, Any] | None) -> Self:
        self._data["metadata"] = (
            metadata.model_dump(mode="json")
            if isinstance(metadata, Metadata)
            else deepcopy(metadata)
        )
        return self

    def with_locale(self, locale: str | None) -> Self:
        self._data["locale"] = locale
        return self

    def with_billing_address(self, address: BillingAddress | dict[str, Any]) -> Self:
        self._data["billingAddress"] = (
            address.model_dump(mode="json", by_alias=True, exclude_unset=True)
            if isinstance(address, BillingAddress)
            else deepcopy(address)
        )
        return self

    def with_items(self, items: Sequence[CheckoutProduct | dict[str, Any]]) -> Self:
        self._data["products"].extend(
            item.model_dump(mode="json", by_alias=True, exclude_unset=True)
            if isinstance(item, CheckoutProduct)
            else deepcopy(item)
            for item in items
        )
        return self

    def with_testmode(self, testmode: bool) -> Self:
        if testmode != (self._wiring.api.mode == "test"):
            raise FluentError("Requested checkout mode does not match the API key")
        return self

    def in_testmode(self) -> Self:
        return self.with_testmode(True)

    def in_live_mode(self) -> Self:
        return self.with_testmode(False)

    def payload(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return deepcopy({**self._data, **(overrides or {})})

    async def create(
        self, overrides: dict[str, Any] | None = None, *, options: RequestOptions | None = None
    ) -> CheckoutResult:
        records = required(self._wiring.checkouts, "checkouts") if self._entity else None
        body = self.payload(overrides)
        if (
            not body.get("products")
            or not body.get("redirectUrlSuccess")
            or not body.get("redirectUrlCanceled")
        ):
            raise FluentError("Products and both redirect URLs are required")
        try:
            request = CreateCheckoutRequest.model_validate(body)
        except ValidationError as exc:
            raise FluentError("Invalid checkout request") from exc
        checkout = await self._wiring.api.checkouts.create(request, options=options)
        if records is not None and self._entity is not None:
            await records.record(CheckoutRecord(checkout.id, self._entity, checkout.testmode))
        return CheckoutResult.model_validate(checkout.model_dump())


class SubscriptionBuilder(CheckoutBuilder):
    def __init__(self, wiring: Wiring, profile: CustomerProfile | None = None) -> None:
        super().__init__(wiring, profile)
        self._item: dict[str, Any] = {"id": "", "quantity": 1}

    def to_plan(self, id: str) -> Self:
        if not id:
            raise FluentError("A subscription-plan ID is required")
        self._item["id"] = id
        return self

    def with_quantity(self, quantity: int) -> Self:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise FluentError("Quantity must be a positive integer")
        self._item["quantity"] = quantity
        return self

    def with_trial_days(self, days: int) -> Self:
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise FluentError("Trial days must be a positive integer")
        self._item["trialDays"] = days
        return self

    def with_trial_ends_at(self, date: datetime, *, now: datetime | None = None) -> Self:
        current = now if now is not None else datetime.now(timezone.utc)
        return self.with_trial_days(math.ceil((date - current).total_seconds() / 86400))

    def payload(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return super().payload({"products": [self._item], **(overrides or {})})
