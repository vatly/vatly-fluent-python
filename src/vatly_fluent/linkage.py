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
    for line in order.lines:
        # Never mistake historical subscription_plan IDs for subscription IDs.
        if line.product_type != "subscription" or not line.product_id:
            continue
        await records.link(record.checkout_id, "subscription", line.product_id)
        if wiring.subscriptions:
            sub = await wiring.subscriptions.find_by_vatly_id(line.product_id)
            if sub:
                await wiring.subscriptions.update(
                    line.product_id, {"entity": preserve_entity(sub.entity, record.entity)}
                )
