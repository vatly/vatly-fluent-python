import asyncio
import itertools
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from vatly import AsyncVatlyClient, RequestOptions, VatlyForbiddenError, VatlyInvalidSignatureError
from vatly.models import Subscription
from vatly.webhooks import compute_signature_v1

from examples.sqlite_driver import SQLiteDriver
from vatly_fluent import (
    CheckoutRecord,
    CustomerProfile,
    EntityRef,
    FluentError,
    LocalSubscription,
    Vatly,
    Wiring,
    subscription_state,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures/resources.json").read_text())
KEY = "test_" + "x" * 20
TEAM = EntityRef("team", "42")
NOW = 1800000000


def resource(name, **overrides):
    return {**deepcopy(FIXTURES[name]), "testmode": True, **overrides}


def signed(name, object, id=None):
    payload = {
        "id": id or "event_" + name,
        "resource": "webhook_event",
        "eventName": name,
        "reason": None,
        "entityType": "order" if "chargeback" in name else object.get("resource", "future"),
        "entityId": object.get("id", "future_1"),
        "object": object,
        "createdAt": "2026-01-01T00:00:00Z",
        "testmode": object.get("testmode", True),
    }
    raw = json.dumps(payload).encode()
    return raw, f"t={NOW},v1=" + compute_signature_v1(raw, "secret", NOW)


@pytest.fixture
async def harness(tmp_path):
    calls = []
    responses = []

    def handler(request):
        calls.append(request)
        if not responses:
            raise AssertionError("Unexpected API request")
        value = responses.pop(0)
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

    async with AsyncVatlyClient(KEY, transport=httpx.MockTransport(handler), max_retries=0) as api:
        driver = SQLiteDriver(
            str(tmp_path / "billing.sqlite"), scope="tenant/storefront", testmode=True
        )
        wiring = Wiring(
            api,
            redirect_url_success="https://a.test/ok",
            redirect_url_canceled="https://a.test/no",
            webhook_secret="secret",
            checkouts=driver.checkouts,
            bindings=driver.bindings,
            orders=driver.orders,
            subscriptions=driver.subscriptions,
            refunds=driver.refunds,
            chargebacks=driver.chargebacks,
            webhook_calls=driver,
            events=driver,
        )
        yield Vatly(wiring), driver, calls, responses
        driver.close()


async def deliver(vatly, name, obj, id=None):
    raw, signature = signed(name, obj, id)
    return await vatly.webhook_processor().handle(raw, signature, now=NOW)


async def test_builders_reuse_detached_payload_and_record_entity(harness):
    vatly, db, calls, responses = harness
    responses.extend([resource("Checkout", id="checkout_a"), resource("Checkout", id="checkout_b")])
    items = [{"id": "one_off_product_a", "quantity": 2}]
    mixed = vatly.checkout_builder().with_items(items)
    items[0]["quantity"] = 99
    assert mixed.payload()["products"][0]["quantity"] == 2
    builder = (
        vatly.subscribe(CustomerProfile(vatly_id="customer_shared"))
        .to_plan("subscription_plan_a")
        .with_quantity(3)
        .with_trial_ends_at(
            datetime(2026, 1, 2, tzinfo=timezone.utc) + timedelta(microseconds=1),
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        .for_entity(TEAM)
        .with_metadata({"purchaseId": "p1"})
        .in_testmode()
    )
    detached = builder.payload()
    detached["products"][0]["quantity"] = 8
    a = await builder.create(options=RequestOptions(idempotency_key="purchase:p1"))
    b = await builder.create()
    assert a.get_url() == a.links.checkout_url.href
    assert b.id == "checkout_b"
    for call in calls:
        body = json.loads(call.content)
        assert body["products"] == [{"id": "subscription_plan_a", "quantity": 3, "trialDays": 2}]
        assert body["customerId"] == "customer_shared"
    assert calls[0].headers["Idempotency-Key"] == "purchase:p1"
    assert (await db.checkouts.find(a.id)).entity == TEAM


async def test_validation_fails_before_remote_call(harness):
    vatly, db, calls, responses = harness
    for value in [0, -1, True, 1.5]:
        with pytest.raises(FluentError):
            vatly.subscribe().with_quantity(value)
        with pytest.raises(FluentError):
            vatly.subscribe().with_trial_days(value)
    with pytest.raises(FluentError):
        vatly.subscribe().in_live_mode()
    with pytest.raises(FluentError):
        await vatly.subscribe().create()
    vatly.wiring.checkouts = None
    with pytest.raises(FluentError):
        await vatly.subscribe().to_plan("subscription_plan_a").for_entity(TEAM).create()
    assert not calls


async def test_customer_cache_bindings_portal_and_conflicts(harness):
    vatly, db, calls, responses = harness
    customer = resource("Customer", id="customer_a")
    responses.extend([customer, {**customer, "name": "Updated"}])
    handle = vatly.customer("customer_a")
    assert await handle.email() == customer["email"]
    assert await handle.name() is None
    assert len(calls) == 1
    await handle.update({"name": "Updated"})
    assert (await handle.identity())["name"] == "Updated"
    await vatly.customers.attribute("customer_a", "user_a")
    await vatly.customers.attribute("customer_a", "user_a")
    await db.bindings.record("customer_a")
    assert await vatly.customers.host_customer_id_for("customer_a") == "user_a"
    with pytest.raises(FluentError):
        await vatly.customers.attribute("customer_b", "user_a")
    with pytest.raises(FluentError):
        await vatly.customers.attribute("customer_a", "user_b")
    responses.append({"url": "https://portal.test/one-use", "expiresAt": "2026-01-01T01:00:00Z"})
    portal = await handle.portal_session()
    assert portal.url == "https://portal.test/one-use"


async def test_errors_are_preserved(harness):
    vatly, db, calls, responses = harness
    responses.append(httpx.Response(403, json={"message": "not allowed"}))
    with pytest.raises(VatlyForbiddenError):
        await vatly.customer("a").model()


@pytest.mark.parametrize(
    "order_of_events", list(itertools.permutations(["checkout", "order", "subscription"]))
)
async def test_out_of_order_own_record_linkage_not_customer_id(harness, order_of_events):
    vatly, db, calls, responses = harness
    await db.checkouts.record(CheckoutRecord("checkout_a", TEAM, True))
    await db.bindings.bind("customer_shared", "WRONG_ENTITY")
    order = resource("Order", id="order_a", customerId="customer_shared", status="paid")
    # Signup lines reference the plan; relatedResources names each line's subscription.
    signup = {**order["lines"][0], "productType": "subscription_plan", "quantity": 3}
    signup.update(
        productId="subscription_plan_pro", relatedResources={"subscriptionId": "subscription_a"}
    )
    # Two plan lines may be the same plan, but own distinct subscription IDs.
    second = {**signup, "id": "line_b", "relatedResources": {"subscriptionId": "subscription_b"}}
    order["lines"] = [signup, second]
    checkout = resource(
        "Checkout", id="checkout_a", orderId="order_a", customerId="customer_shared", status="paid"
    )
    sub = resource("Subscription", id="subscription_a", customerId="customer_shared")
    for kind in order_of_events:
        name, obj = {
            "checkout": ("checkout.paid", checkout),
            "order": ("order.paid", order),
            "subscription": ("subscription.started", sub),
        }[kind]
        assert await deliver(vatly, name, obj)
        assert not await deliver(vatly, name, obj)
    assert (await db.orders.find_by_vatly_id("order_a")).entity == TEAM
    assert (await db.subscriptions.find_by_vatly_id("subscription_a")).entity == TEAM
    assert (await db.checkouts.find_for_resource("subscription", "subscription_b")).entity == TEAM
    assert await db.checkouts.find_for_resource("subscription", "subscription_plan_pro") is None
    assert db.db.execute("SELECT COUNT(*) FROM webhook_calls").fetchone()[0] == 3
    assert db.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 5
    assert not calls


async def test_unlinked_events_never_use_customer_bindings(harness):
    vatly, db, calls, responses = harness
    await db.bindings.bind("customer_shared", "user_a")
    await deliver(
        vatly,
        "subscription.started",
        resource("Subscription", id="sub_a", customerId="customer_shared"),
    )
    assert (await db.subscriptions.find_by_vatly_id("sub_a")).entity is None


async def test_transaction_rolls_back_resources_audit_outbox_and_retries(harness):
    vatly, db, calls, responses = harness

    class FailOnce:
        fail = True

        def supports(self, e):
            return True

        async def handle(self, e):
            if self.fail:
                self.fail = False
                raise RuntimeError("reaction failed")

    vatly.wiring.additional_webhook_reactions = [FailOnce()]
    event = resource("Order", id="order_a", status="paid")
    with pytest.raises(RuntimeError):
        await deliver(vatly, "order.paid", event)
    assert await db.orders.find_by_vatly_id("order_a") is None
    assert db.db.execute("SELECT COUNT(*) FROM webhook_calls").fetchone()[0] == 0
    assert db.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    assert await deliver(vatly, "order.paid", event)
    assert db.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2


async def test_concurrent_signed_duplicate_processes_once(harness):
    vatly, db, calls, responses = harness
    obj = resource("Order", id="order_a", status="paid")
    results = await asyncio.gather(*(deliver(vatly, "order.paid", obj) for _ in range(8)))
    assert results.count(True) == 1
    assert db.db.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 1


async def test_bad_signature_and_mode_do_not_touch_storage(harness):
    vatly, db, calls, responses = harness
    raw, signature = signed("order.paid", resource("Order", status="paid"))
    with pytest.raises(VatlyInvalidSignatureError):
        await vatly.webhook_processor().handle(raw + b" ", signature, now=NOW)
    with pytest.raises(FluentError):
        await deliver(vatly, "order.paid", resource("Order", testmode=False))
    assert db.db.execute("SELECT COUNT(*) FROM webhook_calls").fetchone()[0] == 0


async def test_unsupported_events_audited_and_dispatched(harness):
    vatly, db, calls, responses = harness
    assert await deliver(
        vatly, "future.new", {"id": "future_a", "testmode": True, "payload": {"a": 1}}
    )
    row = db.db.execute("SELECT payload FROM webhook_calls").fetchone()[0]
    assert json.loads(row)["known"] is False
    assert json.loads(row)["object"]["payload"] == {"a": 1}
    assert db.db.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0


@pytest.mark.parametrize(
    "name,fixture",
    [
        ("refund.completed", "Refund"),
        ("refund.failed", "Refund"),
        ("refund.canceled", "Refund"),
        ("order.chargeback_received", "Chargeback"),
        ("order.chargeback_reversed", "Chargeback"),
    ],
)
async def test_financial_reversal_snapshots_persist_and_dispatch(harness, name, fixture):
    vatly, db, calls, responses = harness
    await db.checkouts.record(CheckoutRecord("checkout_a", TEAM, True))
    await db.checkouts.link("checkout_a", "order", "order_a")
    obj = resource(fixture, originalOrderId="order_a")
    assert await deliver(vatly, name, obj)
    repo = db.refunds if fixture == "Refund" else db.chargebacks
    saved = await repo.find_by_vatly_id(obj["id"])
    assert saved.entity == TEAM
    assert saved.resource.tax_summary
    assert len(await repo.list_for_order("order_a")) == 1
    assert not calls


async def test_subscription_cancellation_resume_and_null_mandate(harness):
    vatly, db, calls, responses = harness
    sub = resource("Subscription", id="subscription_a")
    await deliver(vatly, "subscription.started", sub)
    end = "2027-01-01T00:00:00Z"
    canceled = {
        **sub,
        "canceledAt": "2026-11-09T21:19:57Z",
        "endedAt": None,
        "renewedUntil": None,
        "nextRenewalAt": end,
        "status": "on_grace_period",
    }
    await deliver(
        vatly,
        "subscription.canceled_with_grace_period",
        canceled,
    )
    local = await db.subscriptions.find_by_vatly_id("subscription_a")
    assert local.ends_at.isoformat().startswith("2027-01-01")
    responses.extend([canceled, canceled])
    handle = vatly.subscription(local)
    for _ in range(2):
        await handle.sync()
        assert handle.model().ends_at == datetime(2027, 1, 1, tzinfo=timezone.utc)
    # A missed resume delivery must clear the prior cancellation deadline on GET.
    responses.append(sub)
    await handle.sync()
    assert handle.model().ends_at is None
    await deliver(vatly, "subscription.billing_updated", {**sub, "mandate": None})
    assert (await db.subscriptions.find_by_vatly_id("subscription_a")).resource.mandate is not None
    await deliver(vatly, "subscription.resumed", sub)
    assert (await db.subscriptions.find_by_vatly_id("subscription_a")).ends_at is None


@pytest.mark.parametrize("old_end", [None, "2026-11-09T21:19:57Z", "2026-12-09T21:19:19Z"])
async def test_sync_repairs_legacy_deadline_and_clears_it_after_missed_resume(harness, old_end):
    vatly, db, calls, responses = harness
    canceled = resource(
        "Subscription",
        id="subscription_a",
        status="on_grace_period",
        canceledAt="2026-11-09T21:19:57Z",
        endedAt=None,
        renewedUntil=None,
        nextRenewalAt="2026-12-09T21:19:19Z",
    )
    local = await db.subscriptions.store(
        LocalSubscription(resource=canceled, ends_at=old_end, entity=TEAM)
    )
    responses.extend([canceled, canceled, {**canceled, "status": "active", "canceledAt": None}])
    handle = vatly.subscription(local)
    for _ in range(2):
        await handle.sync()
        assert handle.model().ends_at == datetime(2026, 12, 9, 21, 19, 19, tzinfo=timezone.utc)
    await handle.sync()
    assert handle.model().ends_at is None


@pytest.mark.parametrize("period", ["nextRenewalAt", "renewedUntil", "trialUntil", None])
async def test_grace_period_deadline_and_missing_deadline_rollback(harness, period):
    vatly, db, calls, responses = harness
    sub = resource("Subscription", id="subscription_a")
    await deliver(vatly, "subscription.started", sub)
    canceled = {
        **sub,
        "status": "on_grace_period",
        "endedAt": None,
        "canceledAt": "2026-11-09T21:19:57Z",
        "nextRenewalAt": None,
        "renewedUntil": None,
        "trialUntil": None,
    }
    if period is not None:
        canceled[period] = "2026-12-09T21:19:19Z"
        await deliver(vatly, "subscription.canceled_with_grace_period", canceled)
        local = await db.subscriptions.find_by_vatly_id("subscription_a")
        expiry = datetime(2026, 12, 9, 21, 19, 19, tzinfo=timezone.utc)
        assert local.ends_at == expiry
        assert subscription_state(local.ends_at, expiry - timedelta(seconds=1)).valid
        assert subscription_state(local.ends_at, expiry).ended
    else:
        with pytest.raises(FluentError, match="expiry"):
            await deliver(vatly, "subscription.canceled_with_grace_period", canceled)
        assert (
            await db.subscriptions.find_by_vatly_id("subscription_a")
        ).resource.status == "active"
        assert db.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2
        assert db.db.execute("SELECT COUNT(*) FROM webhook_calls").fetchone()[0] == 1


@pytest.mark.parametrize("cycle_field", ["nextRenewalAt", "renewedUntil"])
async def test_conflicting_trial_and_cycle_deadlines_roll_back(harness, cycle_field):
    vatly, db, calls, responses = harness
    sub = resource("Subscription", id="subscription_a")
    await deliver(vatly, "subscription.started", sub)
    local = await db.subscriptions.find_by_vatly_id("subscription_a")
    grace = {
        **sub,
        "status": "on_grace_period",
        "endedAt": None,
        "canceledAt": "2026-11-09T21:19:57Z",
        "nextRenewalAt": None,
        "renewedUntil": None,
        "trialUntil": "2026-11-20T00:00:00Z",
        cycle_field: "2026-12-09T21:19:19Z",
    }
    with pytest.raises(FluentError, match="Ambiguous grace deadline"):
        await deliver(vatly, "subscription.canceled_with_grace_period", grace)
    responses.append(grace)
    handle = vatly.subscription(local)
    with pytest.raises(FluentError, match="Ambiguous grace deadline"):
        await handle.sync()
    assert handle.model().resource.status == "active"
    assert (await db.subscriptions.find_by_vatly_id("subscription_a")).resource.status == "active"
    assert db.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2
    assert db.db.execute("SELECT COUNT(*) FROM webhook_calls").fetchone()[0] == 1

    # Equal trial/cycle deadlines are unambiguous, even with different offsets.
    grace["trialUntil"] = "2026-12-09T22:19:19+01:00"
    await deliver(vatly, "subscription.canceled_with_grace_period", grace)
    saved = await db.subscriptions.find_by_vatly_id("subscription_a")
    assert saved.ends_at == datetime(2026, 12, 9, 21, 19, 19, tzinfo=timezone.utc)


async def test_grace_preserves_known_expiry_and_immediate_cancellation_uses_actual_end(harness):
    vatly, db, calls, responses = harness
    sub = resource("Subscription", id="subscription_a")
    end = datetime(2026, 12, 9, tzinfo=timezone.utc)
    local = await db.subscriptions.store(LocalSubscription(resource=sub, ends_at=end))
    grace = {
        **sub,
        "status": "on_grace_period",
        "canceledAt": "2026-11-09T21:19:57Z",
        "nextRenewalAt": None,
        "renewedUntil": None,
        "trialUntil": "2026-10-01T00:00:00Z",  # Historical trial is not current grace.
    }
    responses.append(grace)
    handle = await vatly.subscription(local).sync()
    assert handle.model().ends_at == end
    immediate = {**grace, "status": "canceled", "endedAt": "2026-11-09T21:20:00Z"}
    await deliver(vatly, "subscription.canceled_immediately", immediate)
    saved = await db.subscriptions.find_by_vatly_id("subscription_a")
    assert saved.ends_at == datetime(2026, 11, 9, 21, 20, tzinfo=timezone.utc)


async def test_handles_use_remote_response_for_scheduled_swap_and_decimal_money(harness):
    vatly, db, calls, responses = harness
    sub = Subscription.model_validate(resource("Subscription", id="subscription_a"))
    local = await db.subscriptions.store(LocalSubscription(resource=sub, entity=TEAM))
    responses.extend([resource("Subscription", id="subscription_a"), httpx.Response(204)])
    handle = await vatly.subscription(local).swap("subscription_plan_new", {"quantity": 4})
    assert handle.model().resource.subscription_plan_id == sub.subscription_plan_id
    assert handle.model().entity == TEAM
    await handle.cancel(immediately=True)
    assert calls[-1].url.params["immediately"] == "1"
    obj = resource("Order", id="order_a")
    obj["reversedSubtotal"] = {"value": "1.23", "currency": "EUR"}
    obj["refundableSubtotal"] = {"value": "98.76", "currency": "EUR"}
    responses.append(obj)
    order = vatly.order("order_a")
    assert str(await order.reversed_subtotal()) == "1.23"
    assert await order.is_partially_reversed()
    assert not await order.is_fully_reversed()


@pytest.mark.parametrize("delay_first", [False, True])
async def test_reconcile_related_subscription_ids_bounded_404_unresolved_and_legacy(
    harness, monkeypatch, delay_first
):
    vatly, db, calls, responses = harness

    async def no_sleep(t):
        pass

    monkeypatch.setattr("vatly_fluent.client.asyncio.sleep", no_sleep)
    await db.checkouts.record(CheckoutRecord("checkout_a", TEAM, True))
    order = resource("Order", id="order_a")
    line = order["lines"][0]
    plan_line = {**line, "productType": "subscription_plan", "productId": "subscription_plan_pro"}
    order["lines"] = [
        {**plan_line, "quantity": 7, "relatedResources": {"subscriptionId": "subscription_a"}},
        {
            **line,
            "id": "line_b",
            "productType": "subscription",
            "productId": "subscription_missing",
            "relatedResources": {"subscriptionId": "subscription_missing"},
        },
        # Historical plan lines: relatedResources omitted or null.
        {**plan_line, "id": "line_c", "productId": "subscription_plan_legacy"},
        {
            **plan_line,
            "id": "line_d",
            "productId": "subscription_plan_old",
            "relatedResources": None,
        },
        # A signup whose subscription core has not resolved yet.
        {**plan_line, "id": "line_e", "relatedResources": {"subscriptionId": None}},
    ]
    responses.extend([resource("Checkout", id="checkout_a", orderId="order_a"), order])
    if delay_first:
        responses.append(httpx.Response(404, json={"message": "not ready"}))
    responses.extend(
        [resource("Subscription", id="subscription_a"), httpx.Response(404), httpx.Response(404)]
    )
    result = await vatly.reconcile_checkout("checkout_a", attempts=2)
    assert [sub.id for sub in result.subscriptions] == ["subscription_a"]
    assert result.pending_subscription_ids == ("subscription_missing",)
    assert result.legacy_plan_ids == ("subscription_plan_legacy", "subscription_plan_old")
    assert result.unresolved_line_ids == ("line_e",)
    assert not any("subscription_plan" in str(call.url) for call in calls)
    assert not responses
    assert (await db.checkouts.find_for_resource("subscription", "subscription_a")).entity == TEAM
    assert (
        await db.checkouts.find_for_resource("subscription", "subscription_missing")
    ).entity == TEAM
    assert await db.checkouts.find_for_resource("subscription", "subscription_plan_pro") is None


def test_state_boundaries():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert subscription_state(None, now).recurring
    assert subscription_state(now + timedelta(seconds=1), now).on_grace_period
    assert subscription_state(now, now).ended
    assert not subscription_state(now - timedelta(seconds=1), now).valid


async def test_unique_links_and_tenant_mode_isolation(harness, tmp_path):
    vatly, db, calls, responses = harness
    await db.checkouts.record(CheckoutRecord("checkout_a", TEAM, True))
    await db.checkouts.record(CheckoutRecord("checkout_b", EntityRef("team", "other"), True))
    await db.checkouts.link("checkout_a", "order", "order_a")
    with pytest.raises(FluentError):
        await db.checkouts.link("checkout_b", "order", "order_a")
    with pytest.raises(FluentError):
        await db.checkouts.record(CheckoutRecord("checkout_a", EntityRef("team", "other"), True))
    other = SQLiteDriver(
        str(tmp_path / "billing.sqlite"), scope="tenant/storefront", testmode=False
    )
    assert await other.checkouts.find("checkout_a") is None
    other.close()


async def test_customer_creation_lookup_and_detached_identity(harness):
    vatly, db, calls, responses = harness
    customer = {**resource("Customer", id="customer_new"), "name": "Original"}
    with pytest.raises(FluentError, match="email"):
        await vatly.customers.create_for("user_new", CustomerProfile())
    assert not calls
    responses.append(customer)
    created = await vatly.customers.create_for(
        "user_new",
        CustomerProfile(email="new@example.test", name="Original"),
        options=RequestOptions(idempotency_key="new-user"),
    )
    assert created.id == "customer_new"
    assert calls[-1].headers["Idempotency-Key"] == "new-user"
    assert json.loads(calls[-1].content)["email"] == "new@example.test"
    with pytest.raises(FluentError, match="already bound"):
        await vatly.customers.create_for("user_new", CustomerProfile(email="other@example.test"))
    assert await vatly.customers.find_by_host_customer_id("missing") is None
    responses.extend([customer, customer, customer, {**customer, "name": "Synced"}])
    assert (await vatly.customers.find_by_host_customer_id("user_new")).id == created.id
    assert (await vatly.customers.find_by_vatly_customer_id(created.id)).id == created.id
    handle = vatly.customer(created.id)
    assert (await handle.identity())["name"] == "Original"
    await handle.sync()
    assert await handle.name() == "Synced"
    responses.append(resource("Customer", id="customer_unattributed"))
    await vatly.customers.create_unattributed(CustomerProfile(email="unbound@example.test"))
    assert await db.bindings.host_customer_id_for("customer_unattributed") is None


async def test_checkout_customer_claim_missing_and_not_found(harness):
    vatly, db, calls, responses = harness
    responses.append(httpx.Response(404))
    assert await vatly.customer_id_from_checkout("missing") is None
    responses.append(resource("Checkout", customerId=None))
    assert not await vatly.claim_customer_from_checkout("checkout_pending", "host")
    responses.append(resource("Checkout", customerId="customer_claimed"))
    assert await vatly.claim_customer_from_checkout("checkout_owned", "host")
    assert await db.bindings.vatly_customer_id_for("host") == "customer_claimed"


@pytest.mark.parametrize(
    "reversed,remaining,partial,full",
    [
        ("0.00", "12.00", False, False),
        ("3.25", "8.75", True, False),
        ("12.00", "0.00", False, True),
    ],
)
async def test_order_reversal_state_uses_decimal_amounts(
    harness, reversed, remaining, partial, full
):
    vatly, db, calls, responses = harness
    responses.append(
        resource(
            "Order",
            reversedSubtotal={"value": reversed, "currency": "EUR"},
            refundableSubtotal={"value": remaining, "currency": "EUR"},
        )
    )
    order = vatly.order("order_a")
    assert await order.is_partially_reversed() is partial
    assert await order.is_fully_reversed() is full
    assert len(calls) == 1
    assert await order.refunds() == []
    assert await order.chargebacks() == []
    assert await order.lines()


async def test_sqlite_driver_rejects_missing_records_and_binding_reassignment(harness):
    vatly, db, calls, responses = harness
    with pytest.raises(FluentError, match="Missing resource"):
        await db.subscriptions.update("missing", {"ends_at": None})
    with pytest.raises(FluentError, match="Missing checkout"):
        await db.checkouts.link("missing", "order", "order_a")
    await db.bindings.bind("customer_a", "host_a")
    with pytest.raises(FluentError, match="reassigned"):
        await db.bindings.bind("customer_b", "host_a")
    with pytest.raises(FluentError, match="reassigned"):
        await db.bindings.bind("customer_a", "host_b")
    assert await db.bindings.vatly_customer_id_for("host_a") == "customer_a"
    assert await db.bindings.host_customer_id_for("customer_a") == "host_a"


async def test_email_lookup_matches_and_empty_results(harness):
    vatly, db, calls, responses = harness
    links = {
        "self": {"href": "https://api.vatly.com/v1/customers", "type": "application/json"},
        "next": None,
        "previous": None,
    }
    responses.extend(
        [
            {"data": [resource("Customer", id="customer_found")], "count": 1, "links": links},
            {"data": [], "count": 0, "links": links},
        ]
    )
    assert (await vatly.customers.find_one_by_email("found@example.test")).id == "customer_found"
    assert await vatly.customers.find_one_by_email("missing@example.test") is None
    assert calls[0].url.params["email"] == "found@example.test"
