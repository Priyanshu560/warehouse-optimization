"""
csv_loader.py
-------------
Reads the three input CSV files (warehouse layout, inventory, orders) and
converts them into the domain objects defined in models.py.

Each loader validates its input defensively: missing files raise a clear
FileNotFoundError, and malformed rows are logged and skipped rather than
crashing the whole pipeline, since real-world warehouse data exports are
rarely perfectly clean.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

from models import InventoryItem, Location, Order, OrderLine, Priority

logger = logging.getLogger("warehouse_optimizer")

# Number of missing IDs to name explicitly in a warning before summarizing
# the rest as "+N more" -- keeps the log readable on messy data exports.
_MAX_IDS_NAMED_IN_WARNING = 5


def _require_file(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Required input file not found: {p}")
    return p


def load_warehouse_layout(path: str | Path) -> Dict[str, Location]:
    """Load warehouse_layout.csv into a dict of location_id -> Location."""
    p = _require_file(path)
    df = pd.read_csv(p)

    required_cols = {"location_id", "aisle", "rack", "shelf", "bin", "zone", "x", "y"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"warehouse_layout.csv is missing required columns: {missing}")

    locations: Dict[str, Location] = {}
    duplicates = 0
    for row in df.itertuples(index=False):
        try:
            loc = Location(
                location_id=str(row.location_id),
                aisle=str(row.aisle),
                rack=str(row.rack),
                shelf=str(row.shelf),
                bin=str(row.bin),
                zone=str(row.zone),
                x=float(row.x),
                y=float(row.y),
            )
            if loc.location_id in locations:
                duplicates += 1
                logger.warning(
                    "Duplicate location_id %r in warehouse_layout.csv; keeping the last row seen.",
                    loc.location_id,
                )
            locations[loc.location_id] = loc
        except (ValueError, AttributeError) as exc:
            logger.warning("Skipping malformed layout row %s: %s", row, exc)

    logger.info(
        "Loaded %d warehouse locations from %s%s",
        len(locations), p.name,
        f", {duplicates} duplicate location_id(s) overwritten" if duplicates else "",
    )
    return locations


def load_inventory(path: str | Path) -> Dict[str, InventoryItem]:
    """Load inventory.csv into a dict of item_id -> InventoryItem."""
    p = _require_file(path)
    df = pd.read_csv(p)

    required_cols = {
        "item_id", "item_name", "location_id", "weight_kg",
        "volume_m3", "stock_qty", "category",
    }
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"inventory.csv is missing required columns: {missing}")

    inventory: Dict[str, InventoryItem] = {}
    duplicates = 0
    for row in df.itertuples(index=False):
        try:
            weight_kg = float(row.weight_kg)
            volume_m3 = float(row.volume_m3)
            stock_qty = int(row.stock_qty)
            if weight_kg <= 0 or volume_m3 <= 0:
                raise ValueError(
                    f"weight_kg and volume_m3 must be positive, got "
                    f"weight_kg={weight_kg}, volume_m3={volume_m3}"
                )
            if stock_qty < 0:
                raise ValueError(f"stock_qty cannot be negative, got {stock_qty}")

            item = InventoryItem(
                item_id=str(row.item_id),
                item_name=str(row.item_name),
                location_id=str(row.location_id),
                weight_kg=weight_kg,
                volume_m3=volume_m3,
                stock_qty=stock_qty,
                category=str(row.category),
            )
            if item.item_id in inventory:
                duplicates += 1
                logger.warning(
                    "Duplicate item_id %r in inventory.csv; keeping the last row seen.",
                    item.item_id,
                )
            inventory[item.item_id] = item
        except (ValueError, AttributeError) as exc:
            logger.warning("Skipping malformed inventory row %s: %s", row, exc)

    logger.info(
        "Loaded %d inventory items from %s%s",
        len(inventory), p.name,
        f", {duplicates} duplicate item_id(s) overwritten" if duplicates else "",
    )
    return inventory


def load_orders(path: str | Path) -> List[Order]:
    """
    Load orders.csv into a list of Order objects.

    orders.csv is stored one row per order *line* (order_id repeats across
    rows for multi-item orders), which mirrors how order data is typically
    exported from a WMS/OMS. Rows are grouped by order_id here.
    """
    p = _require_file(path)
    df = pd.read_csv(p)

    # "customer" is deliberately NOT required: it is not used by batching,
    # routing, reports or the dashboard, so the tool should not force users
    # to supply personal data (data minimization). If the column is present
    # it is read for backward compatibility but never exported.
    required_cols = {
        "order_id", "priority", "due_date", "item_id", "quantity",
    }
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"orders.csv is missing required columns: {missing}")

    orders_by_id: Dict[str, Order] = {}
    skipped_lines = 0
    for row in df.itertuples(index=False):
        try:
            order_id = str(row.order_id)
            if order_id not in orders_by_id:
                orders_by_id[order_id] = Order(
                    order_id=order_id,
                    customer=str(getattr(row, "customer", "") or ""),
                    priority=Priority.from_str(str(row.priority)),
                    due_date=datetime.strptime(str(row.due_date), "%Y-%m-%d").date(),
                )
        except (ValueError, AttributeError) as exc:
            skipped_lines += 1
            logger.warning("Skipping malformed order row %s: %s", row, exc)
            continue

        try:
            quantity = int(row.quantity)
            if quantity <= 0:
                raise ValueError(f"quantity must be positive, got {quantity}")
            orders_by_id[order_id].lines.append(
                OrderLine(item_id=str(row.item_id), quantity=quantity)
            )
        except (ValueError, AttributeError) as exc:
            skipped_lines += 1
            logger.warning("Skipping malformed order row %s: %s", row, exc)

    # A row's quantity can fail validation independently of its order_id/
    # customer/priority/due_date, so an order can legitimately end up with
    # an order shell but zero valid lines (e.g. its one line had a bad
    # quantity). That has nothing to pick, so drop it here rather than
    # passing an empty order through to batching.
    orders = [o for o in orders_by_id.values() if o.lines]
    dropped_empty = len(orders_by_id) - len(orders)
    if dropped_empty:
        logger.warning(
            "Dropped %d order(s) left with zero valid lines after skipping bad rows.",
            dropped_empty,
        )

    logger.info(
        "Loaded %d orders (%d lines) from %s%s",
        len(orders), len(df), p.name,
        f", skipped {skipped_lines} bad rows" if skipped_lines else "",
    )
    return orders


def _format_id_list(ids: set[str]) -> str:
    sample = sorted(ids)[:_MAX_IDS_NAMED_IN_WARNING]
    remainder = len(ids) - len(sample)
    text = ", ".join(sample)
    return f"{text} (+{remainder} more)" if remainder else text


def validate_cross_references(
    layout: Dict[str, Location],
    inventory: Dict[str, InventoryItem],
    orders: List[Order],
) -> None:
    """
    Cross-check the three datasets against each other and warn about
    references that don't resolve.

    Neither Order nor Batch raise an error for an item_id missing from
    inventory -- they simply exclude it from weight/volume totals (see
    models.py), which is the right behavior for a picking run that must
    still complete. But that also means a mismatch would otherwise be
    invisible. This is purely diagnostic: it does not remove or modify
    any data, it only logs what the rest of the pipeline is silently
    working around.
    """
    unknown_item_ids: set[str] = set()
    for order in orders:
        for line in order.lines:
            if line.item_id not in inventory:
                unknown_item_ids.add(line.item_id)
    if unknown_item_ids:
        logger.warning(
            "%d order line item_id(s) not found in inventory.csv and will be "
            "excluded from weight/volume/location totals: %s",
            len(unknown_item_ids), _format_id_list(unknown_item_ids),
        )

    unknown_location_ids: set[str] = set()
    for item in inventory.values():
        if item.location_id not in layout:
            unknown_location_ids.add(item.location_id)
    if unknown_location_ids:
        logger.warning(
            "%d inventory item(s) reference location_id(s) not found in "
            "warehouse_layout.csv and cannot be routed to: %s",
            len(unknown_location_ids), _format_id_list(unknown_location_ids),
        )
