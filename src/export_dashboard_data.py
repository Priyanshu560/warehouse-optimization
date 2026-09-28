"""
export_dashboard_data.py
-------------------------
Runs the existing pipeline (csv_loader -> warehouse -> batch_planner ->
route_optimizer, all unmodified) and serializes the results into a JSON
payload the frontend dashboard can render.

This file adds no optimization logic of its own. It only calls the same
public functions/classes main.py already uses and reshapes their outputs
into a display-friendly structure. If you change batch_planner.py or
route_optimizer.py, just rerun this script to refresh the dashboard.

Usage:
    cd src
    python export_dashboard_data.py

Writes:
    frontend/data/dashboard_data.js    (window.__DASHBOARD_DATA__ = {...})
    frontend/data/dashboard_data.json  (same payload, plain JSON)
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from batch_planner import BatchPlanner
from csv_loader import load_inventory, load_orders, load_warehouse_layout, validate_cross_references
from route_optimizer import RouteOptimizer
from utils import DEPOT_ID, setup_logger
from warehouse import WarehouseGrid

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FRONTEND_DATA_DIR = ROOT / "frontend" / "data"

ALGO_KEYS = ("greedy", "nearest_neighbor", "two_opt")


def build_payload() -> dict:
    logger = setup_logger()
    t_start = time.perf_counter()

    layout = load_warehouse_layout(DATA_DIR / "warehouse_layout.csv")
    inventory = load_inventory(DATA_DIR / "inventory.csv")
    orders = load_orders(DATA_DIR / "orders.csv")

    if not orders:
        # An empty batches list would otherwise reach the frontend, which
        # assumes at least one batch exists (e.g. it selects DATA.batches[0]
        # on load) and would fail with a confusing blank dashboard.
        raise ValueError("No orders were loaded; there is nothing to export to the dashboard.")

    validate_cross_references(layout, inventory, orders)
    warehouse = WarehouseGrid(layout)

    planner = BatchPlanner(inventory)
    batches = planner.plan_batches(orders)
    stats = planner.last_stats

    optimizer = RouteOptimizer(warehouse)

    # Reverse index: location_id -> list of (item_id, item_name) stored there.
    items_by_location: Dict[str, List[dict]] = defaultdict(list)
    for item in inventory.values():
        items_by_location[item.location_id].append(
            {"item_id": item.item_id, "item_name": item.item_name}
        )

    batch_payloads = []
    total_baseline = 0.0
    total_optimized = 0.0

    for batch in batches:
        location_ids = batch.location_ids(inventory)
        results = optimizer.optimize_batch(batch.batch_id, location_ids)

        total_baseline += results["greedy"].total_distance_m
        total_optimized += results["two_opt"].total_distance_m

        # Quantity of each item actually requested within this batch, keyed
        # by the location that item lives at (so the map can label stops).
        qty_by_item: Dict[str, int] = defaultdict(int)
        for order in batch.orders:
            for line in order.lines:
                qty_by_item[line.item_id] += line.quantity

        stops = []
        for loc_id in location_ids:
            loc = warehouse.get(loc_id)
            stop_items = []
            for entry in items_by_location.get(loc_id, []):
                qty = qty_by_item.get(entry["item_id"], 0)
                if qty > 0:
                    stop_items.append({**entry, "quantity": qty})
            stops.append({
                "location_id": loc_id,
                "aisle": loc.aisle,
                "rack": loc.rack,
                "shelf": loc.shelf,
                "bin": loc.bin,
                "x": loc.x,
                "y": loc.y,
                "items": stop_items,
            })

        routes = {}
        for key in ALGO_KEYS:
            r = results[key]
            routes[key] = {
                "sequence": r.stop_sequence,
                "distance_m": round(r.total_distance_m, 1),
                "time_min": round(r.estimated_time_sec / 60.0, 1),
            }

        batch_payloads.append({
            "batch_id": batch.batch_id,
            "order_ids": [o.order_id for o in batch.orders],
            "num_orders": len(batch.orders),
            "total_units": batch.total_units(),
            "total_weight_kg": round(batch.total_weight_kg(inventory), 2),
            "total_volume_m3": round(batch.total_volume_m3(inventory), 4),
            "num_stops": len(location_ids),
            "avg_priority_weight": round(batch.average_priority_weight(), 2),
            "earliest_due_date": batch.earliest_due_date().isoformat(),
            "stops": stops,
            "routes": routes,
        })

    runtime_sec = time.perf_counter() - t_start
    saved = total_baseline - total_optimized
    improvement_pct = (100.0 * saved / total_baseline) if total_baseline > 0 else 0.0

    # Warehouse geometry summary for the map: depot, every rack location,
    # and derived aisle / rack label positions (for axis labeling only —
    # no algorithm output depends on this).
    locations = [
        {
            "location_id": loc.location_id,
            "aisle": loc.aisle,
            "rack": loc.rack,
            "x": loc.x,
            "y": loc.y,
        }
        for loc in warehouse.locations.values()
        if loc.location_id != DEPOT_ID
    ]

    aisle_x: Dict[str, List[float]] = defaultdict(list)
    rack_y: Dict[str, List[float]] = defaultdict(list)
    for loc in locations:
        aisle_x[loc["aisle"]].append(loc["x"])
        rack_y[loc["rack"]].append(loc["y"])

    aisles = [
        {"label": aisle, "x": sum(xs) / len(xs)}
        for aisle, xs in sorted(aisle_x.items())
    ]
    racks = [
        {"label": rack, "y": sum(ys) / len(ys)}
        for rack, ys in sorted(rack_y.items())
    ]

    depot = warehouse.get(DEPOT_ID)

    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "orders_processed": len(orders),
            "batches_ffd": stats.ffd_batch_count,
            "batches_optimized": stats.optimized_batch_count,
            "batches_saved": stats.batches_saved,
            "distance_baseline_m": round(total_baseline, 1),
            "distance_optimized_m": round(total_optimized, 1),
            "distance_saved_m": round(saved, 1),
            "improvement_pct": round(improvement_pct, 1),
            "runtime_sec": round(runtime_sec, 1),
        },
        "warehouse": {
            "depot": {"location_id": depot.location_id, "x": depot.x, "y": depot.y},
            "locations": locations,
            "aisles": aisles,
            "racks": racks,
        },
        "batches": batch_payloads,
    }

    logger.info(
        "Exported dashboard data: %d batches, %d orders, %.1f%% improvement, %.1fs runtime.",
        len(batch_payloads), len(orders), improvement_pct, runtime_sec,
    )
    return payload


def main() -> None:
    logger = setup_logger()
    try:
        payload = build_payload()
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Failed to build dashboard data: %s", exc)
        sys.exit(1)

    FRONTEND_DATA_DIR.mkdir(parents=True, exist_ok=True)

    json_path = FRONTEND_DATA_DIR / "dashboard_data.json"
    js_path = FRONTEND_DATA_DIR / "dashboard_data.js"

    json_text = json.dumps(payload, indent=2)
    json_path.write_text(json_text)
    # A plain <script src> assignment loads fine over file:// (unlike fetch(),
    # which browsers block for local files), so the dashboard works with a
    # simple double-click -- no local server required.
    js_path.write_text(f"window.__DASHBOARD_DATA__ = {json_text};\n")

    print(f"Wrote {json_path}")
    print(f"Wrote {js_path}")


if __name__ == "__main__":
    main()
