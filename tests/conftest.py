"""
Shared fixtures for the test suite.

Tests use a small, hand-built warehouse (not the bundled sample CSVs) so
each test is fast, self-contained, and its expected result can be worked
out by hand -- e.g. a 3-stop route on a simple grid has an obviously
correct shortest path, which makes a good check for the routing algorithms.
"""

from __future__ import annotations

from datetime import date

import pytest

from models import InventoryItem, Location, Order, OrderLine, Priority
from warehouse import WarehouseGrid


@pytest.fixture
def small_warehouse() -> WarehouseGrid:
    """
    Depot at the origin, three stops laid out so that visiting them in
    the "wrong" order (as greedy would, given out-of-order input) is
    obviously longer than the shortest route:

        DEPOT (0,0) -- A (10,0) -- B (10,10) -- C (0,10)

    Visiting A, C, B in that order backtracks; A, B, C does not.
    """
    locations = {
        "A": Location("A", "1", "1", "1", "1", "Zone-1", x=10.0, y=0.0),
        "B": Location("B", "1", "1", "1", "2", "Zone-1", x=10.0, y=10.0),
        "C": Location("C", "1", "1", "1", "3", "Zone-1", x=0.0, y=10.0),
    }
    return WarehouseGrid(locations)


@pytest.fixture
def small_inventory() -> dict[str, InventoryItem]:
    return {
        "SKU-1": InventoryItem("SKU-1", "Widget", "A", weight_kg=1.0, volume_m3=0.01,
                                stock_qty=100, category="Misc"),
        "SKU-2": InventoryItem("SKU-2", "Gadget", "B", weight_kg=2.0, volume_m3=0.02,
                                stock_qty=100, category="Misc"),
        "SKU-3": InventoryItem("SKU-3", "Gizmo", "C", weight_kg=3.0, volume_m3=0.03,
                                stock_qty=100, category="Misc"),
    }


def make_order(order_id: str, item_id: str, quantity: int, priority: Priority = Priority.MEDIUM) -> Order:
    return Order(
        order_id=order_id,
        customer="Test Customer",
        priority=priority,
        due_date=date(2026, 1, 1),
        lines=[OrderLine(item_id=item_id, quantity=quantity)],
    )


@pytest.fixture
def small_orders() -> list[Order]:
    return [
        make_order("ORD-1", "SKU-1", 1),
        make_order("ORD-2", "SKU-2", 1),
        make_order("ORD-3", "SKU-3", 1),
    ]
