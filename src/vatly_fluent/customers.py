from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from typing_extensions import Self
from vatly import RequestOptions
from vatly.models import CreatePortalSessionRequest, Customer, PortalSession, UpdateCustomerRequest

from .contracts import CustomerProfile, FluentError, Wiring, required


class CustomerHandle:
    def __init__(self, wiring: Wiring, id: str) -> None:
        self._wiring = wiring
        self.id = id
        self._cached: Customer | None = None

    async def model(self) -> Customer:
        if self._cached is None:
            self._cached = await self._wiring.api.customers.get(self.id)
        return self._cached

    async def name(self) -> str | None:
        return (await self.model()).name

    async def email(self) -> str:
        return (await self.model()).email

    async def identity(self) -> dict[str, str | None]:
        customer = await self.model()
        return {"name": customer.name, "email": customer.email}

    async def sync(self) -> Self:
        self._cached = await self._wiring.api.customers.get(self.id)
        return self

    async def update(
        self, data: UpdateCustomerRequest | dict[str, Any], *, options: RequestOptions | None = None
    ) -> Self:
        self._cached = await self._wiring.api.customers.update(self.id, data, options=options)
        return self

    async def portal_session(
        self,
        body: CreatePortalSessionRequest | dict[str, Any] | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> PortalSession:
        return await self._wiring.api.customers.create_portal_session(
            self.id, body or {}, options=options
        )


class CustomerService:
    def __init__(self, wiring: Wiring) -> None:
        self._wiring = wiring

    async def _create(
        self, profile: CustomerProfile, extra: dict[str, Any] | None, options: RequestOptions | None
    ) -> Customer:
        body = {"name": profile.name, **(extra or {})}
        body["email"] = body.get("email") or profile.email
        if not body["email"]:
            raise FluentError("An email is required to create a customer")
        return await self._wiring.api.customers.create(body, options=options)

    async def create_for(
        self,
        host_id: str,
        profile: CustomerProfile,
        extra: dict[str, Any] | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> Customer:
        bindings = required(self._wiring.bindings, "bindings")
        if await bindings.vatly_customer_id_for(host_id):
            raise FluentError("Host customer is already bound")
        customer = await self._create(profile, extra, options)
        await self.attribute(customer.id, host_id)
        return customer

    async def create_unattributed(
        self,
        profile: CustomerProfile,
        extra: dict[str, Any] | None = None,
        *,
        options: RequestOptions | None = None,
    ) -> Customer:
        bindings = required(self._wiring.bindings, "bindings")
        customer = await self._create(profile, extra, options)
        await bindings.record(customer.id)
        return customer

    async def attribute(self, vatly_id: str, host_id: str) -> None:
        bindings = required(self._wiring.bindings, "bindings")
        old_vatly = await bindings.vatly_customer_id_for(host_id)
        old_host = await bindings.host_customer_id_for(vatly_id)
        if (old_vatly and old_vatly != vatly_id) or (old_host and old_host != host_id):
            raise FluentError("Customer is already bound to a different identity")
        await bindings.bind(vatly_id, host_id)

    async def find_by_host_customer_id(self, host_id: str) -> Customer | None:
        id = await required(self._wiring.bindings, "bindings").vatly_customer_id_for(host_id)
        return await self._wiring.api.customers.get(id) if id else None

    async def find_by_vatly_customer_id(self, id: str) -> Customer:
        return await self._wiring.api.customers.get(id)

    def find_by_email(
        self, email: str, params: dict[str, Any] | None = None
    ) -> AsyncIterator[Customer]:
        return self._wiring.api.customers.list_all({**(params or {}), "email": email})

    async def find_one_by_email(self, email: str) -> Customer | None:
        async for customer in self.find_by_email(email):
            return customer
        return None

    async def identity(self, id: str) -> dict[str, str | None]:
        return await CustomerHandle(self._wiring, id).identity()

    async def update(
        self,
        id: str,
        body: UpdateCustomerRequest | dict[str, Any],
        *,
        options: RequestOptions | None = None,
    ) -> Customer:
        return await self._wiring.api.customers.update(id, body, options=options)

    async def host_customer_id_for(self, id: str) -> str | None:
        return await required(self._wiring.bindings, "bindings").host_customer_id_for(id)
