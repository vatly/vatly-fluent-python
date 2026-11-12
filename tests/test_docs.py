"""Execute every guide example with the real SDK and SQLite driver."""

import asyncio
import hashlib
import hmac
import inspect
import json
import re
import time
from pathlib import Path

import httpx
import pytest
from test_fluent import KEY, TEAM, resource
from vatly import AsyncVatlyClient
from vatly.models import Subscription

from examples.sqlite_driver import SQLiteDriver
from vatly_fluent import CheckoutRecord, LocalSubscription, Vatly, Wiring

DOCS = Path(__file__).parents[1] / "docs"
CASES = [
    pytest.param(page, index, code, id=f"{page.stem}-{index}")
    for page in sorted(DOCS.glob("*.md"))
    for index, code in enumerate(re.findall(r"```python\n(.*?)```", page.read_text(), re.S))
]


@pytest.mark.parametrize("page,index,code", CASES)
def test_documented_examples(page, index, code, monkeypatch, tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.url.host == "api.vatly.com"
        path = request.url.path.removeprefix("/v1/").split("/")
        if path[-1] == "portal-sessions":
            return httpx.Response(
                200,
                json={
                    "url": "https://portal.example.com/session",
                    "expiresAt": "2027-01-01T00:00:00Z",
                },
            )
        name = {
            "customers": "Customer",
            "checkouts": "Checkout",
            "orders": "Order",
            "subscriptions": "Subscription",
        }[path[0]]
        if "refunds" in path:
            name = "Refund"
        if request.method == "DELETE":
            return httpx.Response(204)
        value = resource(
            name,
            id={
                "Customer": "customer_example",
                "Checkout": "checkout_example",
                "Order": "order_example",
                "Subscription": "subscription_example",
                "Refund": "refund_example",
            }[name],
        )
        if name == "Checkout":
            value["orderId"] = "order_example"
        if name == "Order":
            value["lines"][0].update(productType="subscription", productId="subscription_example")
        if len(path) == 1 and request.method == "GET":
            value = {
                "data": [value],
                "count": 1,
                "links": {
                    "self": {"href": str(request.url), "type": "application/json"},
                    "next": None,
                    "previous": None,
                },
            }
        return httpx.Response(200, json=value)

    original = httpx.AsyncClient.__init__

    def initialize(self, *args, **kwargs):
        kwargs["transport"] = kwargs.get("transport") or httpx.MockTransport(respond)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", initialize)
    monkeypatch.setenv("VATLY_API_KEY", KEY)
    stamp = int(time.time())
    raw = json.dumps(
        {
            "id": "event_docs",
            "resource": "webhook_event",
            "eventName": "future.event",
            "reason": None,
            "entityType": "future",
            "entityId": "future_1",
            "object": {},
            "testmode": True,
            "createdAt": "2026-01-01T00:00:00Z",
        }
    ).encode()
    digest = hmac.new(b"secret", str(stamp).encode() + b"." + raw, hashlib.sha256).hexdigest()

    async def run():
        async with AsyncVatlyClient(KEY) as api:
            db = SQLiteDriver(
                str(tmp_path / "docs.sqlite"), scope="tenant/storefront", testmode=True
            )
            try:
                local = LocalSubscription(
                    resource=Subscription.model_validate(
                        resource("Subscription", id="subscription_example")
                    ),
                    entity=TEAM,
                )
                await db.subscriptions.store(local)
                await db.checkouts.record(CheckoutRecord("checkout_example", TEAM, True))
                if page.stem == "CustomerPortal":
                    await db.bindings.bind("customer_example", "user-42")
                vatly = Vatly(
                    Wiring(
                        api,
                        "https://app.example.com/ok",
                        "https://app.example.com/no",
                        webhook_secret="secret",
                        checkouts=db.checkouts,
                        bindings=db.bindings,
                        orders=db.orders,
                        subscriptions=db.subscriptions,
                        refunds=db.refunds,
                        chargebacks=db.chargebacks,
                        webhook_calls=db,
                        events=db,
                    )
                )
                namespace = {"__name__": "docs_example"}
                exec(compile(code, f"{page}:{index}", "exec"), namespace)
                function = namespace["example"]
                available = {
                    "vatly": vatly,
                    "api": api,
                    "driver": db,
                    "local": local,
                    "raw_body": raw,
                    "signature": f"t={stamp},v1={digest}",
                }
                args = {name: available[name] for name in inspect.signature(function).parameters}
                result = function(**args)
                if inspect.isawaitable(result):
                    result = await result
                if page.stem == "Webhooks":
                    assert result == "processed"
                    assert await function(**args) == "duplicate"
                    assert db.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
                elif page.stem == "CoreConcepts":
                    assert (await db.checkouts.find(result.id)).entity == TEAM
                elif page.stem == "Subscriptions" and index == 3:
                    assert [sub.id for sub in result[0]] == ["subscription_example"]
                    assert result[1:] == ((), ())
            finally:
                db.close()

    asyncio.run(run())


def test_overview_links_every_flat_page():
    overview = (DOCS / "README.md").read_text()
    assert not any(path.is_dir() for path in DOCS.iterdir())
    for page in DOCS.glob("*.md"):
        if page.name != "README.md":
            assert f"]({page.name})" in overview
