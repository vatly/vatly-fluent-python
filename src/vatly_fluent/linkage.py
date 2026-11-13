"""Own-record linkage, shared by signed reactions and explicit reconciliation."""

from vatly.models import Order

from .contracts import CheckoutRecord, EntityRef, FluentError, Wiring, required


def preserve_entity(existing: EntityRef | None, candidate: EntityRef | None) -> EntityRef | None:
    if existing is not None and candidate is not None and existing != candidate:
        raise FluentError("Refusing to reassign a billing resource to another entity")
    return existing if existing is not None else candidate


async def checkout_for(wiring: Wiring, resource_type: str, id: str) -> CheckoutRecord | None:
    if wiring.checkouts is None:
        return None
    record = await wiring.checkouts.find_for_resource(resource_type, id)
    if record is not None and record.testmode != (wiring.api.mode == "test"):
        raise FluentError("Checkout record mode does not match the API key")
    return record


async def link_order(wiring: Wiring, record: CheckoutRecord, order: Order) -> None:
    if record.testmode != order.testmode or order.testmode != (wiring.api.mode == "test"):
        raise FluentError("Order and checkout record modes do not match")
    records = required(wiring.checkouts, "checkouts")
    await records.link(record.checkout_id, "order", order.id)
    if wiring.orders:
        existing = await wiring.orders.find_by_vatly_id(order.id)
        if existing:
            await wiring.orders.update(
                order.id, {"entity": preserve_entity(existing.entity, record.entity)}
            )
    for sub_id in subscription_ids(order):
        await records.link(record.checkout_id, "subscription", sub_id)
        if wiring.subscriptions:
            sub = await wiring.subscriptions.find_by_vatly_id(sub_id)
            if sub:
                await wiring.subscriptions.update(
                    sub_id, {"entity": preserve_entity(sub.entity, record.entity)}
                )


def subscription_ids(order: Order) -> tuple[str, ...]:
    """Distinct subscription IDs the order's lines reference, in line order.

    Signup lines carry their subscription in ``relatedResources.subscriptionId``
    (null until core resolves it); renewal lines also reference it as their
    product. A ``subscription_plan`` product ID is a plan, never a subscription.
    """
    ids: dict[str, None] = {}
    for line in order.lines:
        related = line.related_resources
        if related is not None and related.subscription_id:
            ids[related.subscription_id] = None
        if line.product_type == "subscription" and line.product_id:
            ids[line.product_id] = None
    return tuple(ids)
