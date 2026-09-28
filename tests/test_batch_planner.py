from datetime import date

import pytest

from batch_planner import BatchPlanner
from models import InventoryItem, Order, OrderLine, Priority


def _order(
    order_id: str, weight_kg: float, priority: Priority = Priority.MEDIUM
) -> tuple[Order, InventoryItem]:
    """Build a one-line order and the matching inventory item, sized only by weight_kg."""
    item = InventoryItem(
        item_id=f"SKU-{order_id}", item_name="Test Item", location_id="LOC-1",
        weight_kg=weight_kg, volume_m3=0.001, stock_qty=1000, category="Misc",
    )
    order = Order(
        order_id=order_id, customer="Test", priority=priority, due_date=date(2026, 1, 1),
        lines=[OrderLine(item_id=item.item_id, quantity=1)],
    )
    return order, item


def test_no_batch_exceeds_capacity():
    """
    Every batch CP-SAT (or its FFD fallback) produces must independently
    respect the weight/volume/unit-count limits -- this is the actual
    constraint the whole module exists to enforce.
    """
    orders, inventory = [], {}
    # Orders sized so they don't divide evenly into totes, forcing the
    # planner to actually make placement decisions rather than every
    # order trivially fitting alone.
    for i, weight in enumerate([4.0, 3.5, 3.0, 5.0, 2.5, 6.0, 1.5, 4.5]):
        order, item = _order(f"ORD-{i}", weight)
        orders.append(order)
        inventory[item.item_id] = item

    planner = BatchPlanner(inventory, max_items=100, max_weight_kg=9.0, max_volume_m3=10.0)
    batches = planner.plan_batches(orders)

    assert sum(len(b.orders) for b in batches) == len(orders)  # every order placed exactly once
    for batch in batches:
        assert batch.total_weight_kg(inventory) <= 9.0 + 1e-9


def test_single_order_exceeding_tote_capacity_raises_clear_error():
    order, item = _order("ORD-BIG", weight_kg=50.0)
    planner = BatchPlanner({item.item_id: item}, max_weight_kg=9.0)
    with pytest.raises(ValueError, match="exceeds single-tote capacity"):
        planner.plan_batches([order])


def test_cp_sat_never_uses_more_batches_than_ffd_baseline():
    orders, inventory = [], {}
    for i, weight in enumerate([4.0, 3.5, 3.0, 5.0, 2.5, 6.0, 1.5, 4.5, 2.0, 3.0]):
        order, item = _order(f"ORD-{i}", weight)
        orders.append(order)
        inventory[item.item_id] = item

    planner = BatchPlanner(inventory, max_items=100, max_weight_kg=9.0, max_volume_m3=10.0)
    planner.plan_batches(orders)
    stats = planner.last_stats

    assert stats.optimized_batch_count <= stats.ffd_batch_count
    assert stats.batches_saved >= 0


def test_empty_order_list_produces_no_batches():
    planner = BatchPlanner({})
    batches = planner.plan_batches([])
    assert batches == []
    assert planner.last_stats.num_orders == 0


def test_batches_are_numbered_by_priority_then_due_date():
    high, high_item = _order("ORD-H", weight_kg=1.0, priority=Priority.HIGH)
    low, low_item = _order("ORD-L", weight_kg=1.0, priority=Priority.LOW)
    inventory = {high_item.item_id: high_item, low_item.item_id: low_item}

    # Tiny capacity forces each order into its own batch, so batch order
    # reflects priority ordering directly.
    planner = BatchPlanner(inventory, max_items=1, max_weight_kg=2.0, max_volume_m3=1.0)
    batches = planner.plan_batches([low, high])

    assert batches[0].orders[0].priority == Priority.HIGH
    assert batches[0].batch_id == "BATCH-001"
