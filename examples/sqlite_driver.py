"""Small runnable driver, not a production database recommendation.

One connection and asyncio lock per instance; do not share it across event loops.
Use async-native DB drivers for high-throughput servers. Every driver participates
in WebhookCalls.process's transaction. scope must include tenant AND storefront.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from vatly.models import Chargeback, Order, Refund
from vatly.webhooks import VatlyWebhookEvent

from vatly_fluent import (
    CheckoutRecord,
    DomainEvent,
    EntityRef,
    FluentError,
    LocalEvent,
    LocalSubscription,
    Stored,
)

T = TypeVar("T", bound=Stored[Any])


class SQLiteRepository(Generic[T]):
    def __init__(self, driver: SQLiteDriver, kind: str, model: type[T]) -> None:
        self.driver, self.kind, self.model = driver, kind, model

    async def find_by_vatly_id(self, id: str) -> T | None:
        row = self.driver.db.execute(
            "SELECT payload FROM resources WHERE scope=? AND kind=? AND id=?",
            (self.driver.scope, self.kind, id),
        ).fetchone()
        return self.model.model_validate_json(row[0]) if row else None

    async def store(self, data: T) -> T | None:
        self.driver.db.execute(
            "INSERT INTO resources VALUES (?, ?, ?, ?)",
            (self.driver.scope, self.kind, data.resource.id, data.model_dump_json()),
        )
        return data

    async def update(self, id: str, data: dict[str, Any]) -> T:
        current = await self.find_by_vatly_id(id)
        if current is None:
            raise FluentError("Missing resource")
        merged = self.model.model_validate({**current.model_dump(), **data})
        self.driver.db.execute(
            "UPDATE resources SET payload=? WHERE scope=? AND kind=? AND id=?",
            (merged.model_dump_json(), self.driver.scope, self.kind, id),
        )
        return merged

    def _all(self) -> list[T]:
        return [
            self.model.model_validate_json(row[0])
            for row in self.driver.db.execute(
                "SELECT payload FROM resources WHERE scope=? AND kind=?",
                (self.driver.scope, self.kind),
            )
        ]

    async def list_for_order(self, id: str) -> list[T]:
        return [record for record in self._all() if record.resource.original_order_id == id]

    async def list_for_customer(self, id: str) -> list[T]:
        return [record for record in self._all() if record.resource.customer_id == id]


class SQLiteCheckouts:
    def __init__(self, driver: SQLiteDriver) -> None:
        self.driver = driver

    async def record(self, data: CheckoutRecord) -> None:
        existing = await self.find(data.checkout_id)
        if existing is not None and existing != data:
            raise FluentError("Checkout ownership cannot be reassigned")
        self.driver.db.execute(
            "INSERT OR IGNORE INTO checkouts VALUES (?, ?, ?, ?, ?)",
            (self.driver.scope, data.checkout_id, data.entity.kind, data.entity.id, data.testmode),
        )

    async def find(self, checkout_id: str) -> CheckoutRecord | None:
        row = self.driver.db.execute(
            "SELECT entity_kind, entity_id, testmode FROM checkouts WHERE scope=? AND id=?",
            (self.driver.scope, checkout_id),
        ).fetchone()
        return CheckoutRecord(checkout_id, EntityRef(row[0], row[1]), bool(row[2])) if row else None

    async def link(self, checkout_id: str, resource_type: str, resource_id: str) -> None:
        if await self.find(checkout_id) is None:
            raise FluentError("Missing checkout record")
        existing = await self.find_for_resource(resource_type, resource_id)
        if existing is not None and existing.checkout_id != checkout_id:
            raise FluentError("Resource already belongs to another checkout")
        self.driver.db.execute(
            "INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?)",
            (self.driver.scope, resource_type, resource_id, checkout_id),
        )

    async def find_for_resource(
        self, resource_type: str, resource_id: str
    ) -> CheckoutRecord | None:
        row = self.driver.db.execute(
            "SELECT checkout_id FROM links WHERE scope=? AND kind=? AND resource_id=?",
            (self.driver.scope, resource_type, resource_id),
        ).fetchone()
        return await self.find(row[0]) if row else None


class SQLiteBindings:
    def __init__(self, driver: SQLiteDriver) -> None:
        self.driver = driver

    async def bind(self, vatly_id: str, host_id: str) -> None:
        old_host = await self.host_customer_id_for(vatly_id)
        old_vatly = await self.vatly_customer_id_for(host_id)
        if (old_host is not None and old_host != host_id) or (
            old_vatly is not None and old_vatly != vatly_id
        ):
            raise FluentError("Customer binding cannot be reassigned")
        await self.record(vatly_id)
        self.driver.db.execute(
            "UPDATE bindings SET host_id=? WHERE scope=? AND vatly_id=?",
            (host_id, self.driver.scope, vatly_id),
        )

    async def record(self, vatly_id: str) -> None:
        self.driver.db.execute(
            "INSERT OR IGNORE INTO bindings VALUES (?, ?, NULL)", (self.driver.scope, vatly_id)
        )

    async def vatly_customer_id_for(self, host_id: str) -> str | None:
        row = self.driver.db.execute(
            "SELECT vatly_id FROM bindings WHERE scope=? AND host_id=?",
            (self.driver.scope, host_id),
        ).fetchone()
        return str(row[0]) if row else None

    async def host_customer_id_for(self, vatly_id: str) -> str | None:
        row = self.driver.db.execute(
            "SELECT host_id FROM bindings WHERE scope=? AND vatly_id=?",
            (self.driver.scope, vatly_id),
        ).fetchone()
        return str(row[0]) if row and row[0] is not None else None


class SQLiteDriver:
    def __init__(self, path: str, *, scope: str, testmode: bool) -> None:
        self.scope = f"{scope}:{'test' if testmode else 'live'}"
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS resources (scope TEXT, kind TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(scope,kind,id));
            CREATE TABLE IF NOT EXISTS checkouts (scope TEXT, id TEXT, entity_kind TEXT, entity_id TEXT, testmode INTEGER, PRIMARY KEY(scope,id));
            CREATE TABLE IF NOT EXISTS links (scope TEXT, kind TEXT, resource_id TEXT, checkout_id TEXT, PRIMARY KEY(scope,kind,resource_id));
            CREATE TABLE IF NOT EXISTS bindings (scope TEXT, vatly_id TEXT, host_id TEXT, PRIMARY KEY(scope,vatly_id), UNIQUE(scope,host_id));
            CREATE TABLE IF NOT EXISTS webhook_calls (scope TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(scope,id));
            CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, scope TEXT, payload TEXT NOT NULL);
        """)
        self._lock = asyncio.Lock()
        self.checkouts = SQLiteCheckouts(self)
        self.bindings = SQLiteBindings(self)
        self.orders = SQLiteRepository(self, "order", Stored[Order])
        self.subscriptions = SQLiteRepository(self, "subscription", LocalSubscription)
        self.refunds = SQLiteRepository(self, "refund", Stored[Refund])
        self.chargebacks = SQLiteRepository(self, "chargeback", Stored[Chargeback])

    async def process(self, event: VatlyWebhookEvent, work: Callable[[], Awaitable[None]]) -> bool:
        async with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.db.execute(
                    "SELECT 1 FROM webhook_calls WHERE scope=? AND id=?", (self.scope, event.id)
                ).fetchone():
                    self.db.execute("ROLLBACK")
                    return False
                await work()
                self.db.execute(
                    "INSERT INTO webhook_calls VALUES (?, ?, ?)",
                    (self.scope, event.id, event.model_dump_json(by_alias=True)),
                )
                self.db.execute("COMMIT")
                return True
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    async def dispatch(self, event: DomainEvent) -> None:
        payload = (
            json.dumps(
                {
                    "eventName": event.event_name,
                    "known": True,
                    "object": event.object.model_dump(mode="json"),
                }
            )
            if isinstance(event, LocalEvent)
            else event.model_dump_json(by_alias=True)
        )
        self.db.execute("INSERT INTO outbox (scope, payload) VALUES (?, ?)", (self.scope, payload))

    def close(self) -> None:
        self.db.close()
