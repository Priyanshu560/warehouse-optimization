"""
models.py
---------
Core domain model for the warehouse picking optimization engine.

These are plain, immutable-where-sensible dataclasses with no I/O or
optimization logic of their own -- that logic lives in warehouse.py,
batch_planner.py and route_optimizer.py. Keeping the domain model separate
makes each layer independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Set


class Priority(Enum):
    """Order priority tiers, ordered from most to least urgent."""

    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"

    @property
    def weight(self) -> int:
        """Numeric weight used for sorting/scoring (higher = more urgent)."""
        return {"High": 3, "Medium": 2, "Low": 1}[self.value]

    @classmethod
    def from_str(cls, value: str) -> "Priority":
        normalized = value.strip().capitalize()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unknown priority value: {value!r}")


@dataclass(frozen=True)
class Location:
    """A single storage slot in the warehouse (aisle -> rack -> shelf -> bin)."""

    location_id: str
    aisle: str
    rack: str
    shelf: str
    bin: str
    zone: str
    x: float  # meters, position across aisles
    y: float  # meters, position along an aisle

    def coordinates(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass
class InventoryItem:
    """A stock keeping unit (SKU) and where it lives in the warehouse."""

    item_id: str
    item_name: str
    location_id: str
    weight_kg: float
    volume_m3: float
    stock_qty: int
    category: str


@dataclass
class OrderLine:
    """A single item + quantity requested within an order."""

    item_id: str
    quantity: int


@dataclass
class Order:
    """A customer order composed of one or more order lines."""

    order_id: str
    customer: str
    priority: Priority
    due_date: date
    lines: List[OrderLine] = field(default_factory=list)

    def total_weight_kg(self, inventory: Dict[str, InventoryItem]) -> float:
        return sum(
            inventory[line.item_id].weight_kg * line.quantity
            for line in self.lines
            if line.item_id in inventory
        )

    def total_volume_m3(self, inventory: Dict[str, InventoryItem]) -> float:
        return sum(
            inventory[line.item_id].volume_m3 * line.quantity
            for line in self.lines
            if line.item_id in inventory
        )

    def total_units(self) -> int:
        return sum(line.quantity for line in self.lines)

    def location_ids(self, inventory: Dict[str, InventoryItem]) -> Set[str]:
        """Unique set of warehouse locations that must be visited for this order."""
        return {
            inventory[line.item_id].location_id
            for line in self.lines
            if line.item_id in inventory
        }


@dataclass
class Batch:
    """A group of orders assigned to a single picker tote/route."""

    batch_id: str
    orders: List[Order] = field(default_factory=list)

    def total_weight_kg(self, inventory: Dict[str, InventoryItem]) -> float:
        return sum(order.total_weight_kg(inventory) for order in self.orders)

    def total_volume_m3(self, inventory: Dict[str, InventoryItem]) -> float:
        return sum(order.total_volume_m3(inventory) for order in self.orders)

    def total_units(self) -> int:
        return sum(order.total_units() for order in self.orders)

    def location_ids(self, inventory: Dict[str, InventoryItem]) -> List[str]:
        """Unique, order-independent list of locations required for this batch."""
        locs: Set[str] = set()
        for order in self.orders:
            locs |= order.location_ids(inventory)
        return sorted(locs)

    def average_priority_weight(self) -> float:
        if not self.orders:
            return 0.0
        return sum(o.priority.weight for o in self.orders) / len(self.orders)

    def earliest_due_date(self) -> date:
        return min(o.due_date for o in self.orders)


@dataclass
class RouteResult:
    """Output of a route optimization algorithm run on a single batch."""

    algorithm: str
    batch_id: str
    stop_sequence: List[str]        # ordered location ids, depot excluded
    total_distance_m: float
    num_stops: int
    estimated_time_sec: float
