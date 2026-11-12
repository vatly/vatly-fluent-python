"""Async, framework-neutral driver contracts. Hosts own storage and transactions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict
from vatly import AsyncVatlyClient
from vatly.models import Chargeback, Order, Refund, Subscription
from vatly.webhooks import VatlyWebhookEvent

T = TypeVar("T")


class FluentError(Exception):
    """Invalid local input or missing wiring; SDK errors are not wrapped."""


@dataclass(frozen=True)
class EntityRef:
    """An authorized host billable: user, team, organization, purchase, etc."""

    kind: str
    id: str


@dataclass(frozen=True)
class CustomerProfile:
    vatly_id: str | None = None
    email: str | None = None
    name: str | None = None


class Stored(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    resource: T
    entity: EntityRef | None = None


class LocalSubscription(Stored[Subscription]):
    ends_at: datetime | None = None


def subscription_ends_at(
    resource: Subscription, previous: datetime | None = None
) -> datetime | None:
    """Map lifecycle snapshots to access expiry, not cancellation request time.

    Core's grace deadline is the trial/cycle end. canceledAt is raisedAt; the
    internal ends_at is not exposed by the current API. Missing expiry fails
    processing instead of silently granting unbounded access or ending it now.
    """
    if resource.ended_at is not None:
        return resource.ended_at
    if resource.status == "on_grace_period":
        # Historical dates and pre-fix cancellation timestamps are not deadlines.
        cycle, renewed, trial, prior = (
            end
            if end is not None and (resource.canceled_at is None or end > resource.canceled_at)
            else None
            for end in (
                resource.next_renewal_at,
                resource.renewed_until,
                resource.trial_until,
                previous,
            )
        )
        cycle = cycle or renewed
        # Core chooses trial or cycle end using an onTrial flag absent from the API.
        if trial is not None and cycle is not None and trial != cycle:
            raise FluentError("Ambiguous grace deadline; reconcile before granting access")
        if end := cycle or trial or prior:
            return end
        raise FluentError("Grace-period expiry is unavailable; reconcile before granting access")
    return resource.canceled_at if resource.status == "canceled" else None


@dataclass(frozen=True)
class CheckoutRecord:
    checkout_id: str
    entity: EntityRef
    testmode: bool


class CustomerBindings(Protocol):
    """Enforce uniqueness on both sides, scoped by tenant/storefront/mode."""

    async def bind(self, vatly_id: str, host_id: str) -> None: ...
    async def record(self, vatly_id: str) -> None: ...
    async def vatly_customer_id_for(self, host_id: str) -> str | None: ...
    async def host_customer_id_for(self, vatly_id: str) -> str | None: ...


class CheckoutRecords(Protocol):
    """Immutable ownership from host records, NEVER inferred from customerId.

    record/link must be idempotent for identical values and reject reassignment.
    link and lookups are scoped to the wiring's tenant/storefront/mode.
    """

    async def record(self, data: CheckoutRecord) -> None: ...
    async def find(self, checkout_id: str) -> CheckoutRecord | None: ...
    async def link(self, checkout_id: str, resource_type: str, resource_id: str) -> None: ...
    async def find_for_resource(
        self, resource_type: str, resource_id: str
    ) -> CheckoutRecord | None: ...


class Repository(Protocol[T]):
    async def find_by_vatly_id(self, id: str) -> T | None: ...
    async def store(self, data: T) -> T | None:
        """Unique resource id required; None deliberately declines persistence."""
        ...

    async def update(self, id: str, data: dict[str, Any]) -> T:
        """Omitted fields stay unchanged, explicit None clears a nullable field."""
        ...


class ReversalRepository(Repository[Stored[T]], Protocol[T]):
    async def list_for_order(self, id: str) -> list[Stored[T]]: ...
    async def list_for_customer(self, id: str) -> list[Stored[T]]: ...


@dataclass(frozen=True)
class LocalEvent:
    event_name: Literal["local.order_created", "local.subscription_created"]
    object: Stored[Order] | LocalSubscription
    known: Literal[True] = True


DomainEvent = VatlyWebhookEvent | LocalEvent


class EventDispatcher(Protocol):
    async def dispatch(self, event: DomainEvent) -> None: ...


class WebhookReaction(Protocol):
    def supports(self, event: VatlyWebhookEvent) -> bool: ...
    async def handle(self, event: VatlyWebhookEvent) -> None: ...


class WebhookCalls(Protocol):
    async def process(self, event: VatlyWebhookEvent, work: Callable[[], Awaitable[None]]) -> bool:
        """Dedup signed id + retain envelope + run work + mark processed atomically.

        Share the transaction with repositories, checkout records, bindings and
        outbox. Return False for committed duplicates; rollback everything on error.
        """
        ...


@dataclass
class Wiring:
    api: AsyncVatlyClient
    redirect_url_success: str = ""
    redirect_url_canceled: str = ""
    webhook_secret: str | Sequence[str] | None = None
    bindings: CustomerBindings | None = None
    checkouts: CheckoutRecords | None = None
    subscriptions: Repository[LocalSubscription] | None = None
    orders: Repository[Stored[Order]] | None = None
    refunds: ReversalRepository[Refund] | None = None
    chargebacks: ReversalRepository[Chargeback] | None = None
    webhook_calls: WebhookCalls | None = None
    events: EventDispatcher | None = None
    additional_webhook_reactions: Sequence[WebhookReaction] = field(default_factory=tuple)


def required(value: T | None, name: str) -> T:
    if value is None:
        raise FluentError(f"Missing wiring: {name}")
    return value


@dataclass(frozen=True)
class SubscriptionState:
    cancelled: bool
    on_grace_period: bool
    active: bool
    valid: bool
    recurring: bool
    ended: bool


def subscription_state(ends_at: datetime | None, now: datetime | None = None) -> SubscriptionState:
    """PHP endsAt predicates, NOT an entitlement policy or substitute for status."""
    current = now if now is not None else datetime.now(timezone.utc)
    cancelled = ends_at is not None
    grace = ends_at is not None and ends_at > current
    return SubscriptionState(
        cancelled,
        grace,
        not cancelled or grace,
        not cancelled or grace,
        not cancelled,
        cancelled and not grace,
    )
