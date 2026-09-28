"""
report_generator.py
--------------------
Turns the results of batch planning and route optimization into CSV reports
and Matplotlib visualizations saved under output/.

Reports produced:
    output/batch_report.csv        one row per batch
    output/route_report.csv        one row per (batch, algorithm)
    output/distance_summary.csv    before/after distance comparison per batch

Visualizations produced:
    output/warehouse_layout.png            full layout, colored by zone
    output/route_batch_<id>.png            depot + stops + route path
    output/before_after_comparison.png     total distance, all algorithms
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")  # headless-safe backend, no display required
import matplotlib.pyplot as plt
import pandas as pd

from models import Batch, InventoryItem, RouteResult
from utils import DEPOT_ID, ensure_dir
from warehouse import WarehouseGrid

logger = logging.getLogger("warehouse_optimizer")

_ZONE_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B2", "#937860", "#DA8BC3"]


class ReportGenerator:
    """Writes CSV reports and PNG visualizations for a picking optimization run."""

    def __init__(self, output_dir: str | Path = "output"):
        self.output_dir = ensure_dir(output_dir)

    # ------------------------------------------------------------------ #
    # CSV reports
    # ------------------------------------------------------------------ #

    def write_batch_report(self, batches: List[Batch], inventory: Dict[str, InventoryItem]) -> Path:
        rows = []
        for batch in batches:
            rows.append({
                "batch_id": batch.batch_id,
                "num_orders": len(batch.orders),
                "order_ids": ";".join(o.order_id for o in batch.orders),
                "num_stops": len(batch.location_ids(inventory)),
                "total_units": batch.total_units(),
                "total_weight_kg": round(batch.total_weight_kg(inventory), 2),
                "total_volume_m3": round(batch.total_volume_m3(inventory), 4),
                "avg_priority_weight": round(batch.average_priority_weight(), 2),
                "earliest_due_date": batch.earliest_due_date().isoformat(),
            })
        df = pd.DataFrame(rows)
        out_path = self.output_dir / "batch_report.csv"
        df.to_csv(out_path, index=False)
        logger.info("Wrote batch report: %s (%d batches)", out_path, len(df))
        return out_path

    def write_route_report(self, all_results: Dict[str, Dict[str, RouteResult]]) -> Path:
        rows = []
        for batch_id, algo_results in all_results.items():
            for algo_name, result in algo_results.items():
                rows.append({
                    "batch_id": batch_id,
                    "algorithm": algo_name,
                    "num_stops": result.num_stops,
                    "total_distance_m": round(result.total_distance_m, 2),
                    "estimated_time_min": round(result.estimated_time_sec / 60.0, 2),
                    "stop_sequence": ";".join(result.stop_sequence),
                })
        df = pd.DataFrame(rows)
        out_path = self.output_dir / "route_report.csv"
        df.to_csv(out_path, index=False)
        logger.info("Wrote route report: %s (%d rows)", out_path, len(df))
        return out_path

    def write_distance_summary(self, all_results: Dict[str, Dict[str, RouteResult]]) -> Path:
        rows = []
        for batch_id, algo_results in all_results.items():
            baseline = algo_results["greedy"].total_distance_m
            optimized = algo_results["two_opt"].total_distance_m
            saved = baseline - optimized
            pct = (100.0 * saved / baseline) if baseline > 0 else 0.0
            rows.append({
                "batch_id": batch_id,
                "baseline_distance_m": round(baseline, 2),
                "nearest_neighbor_distance_m": round(algo_results["nearest_neighbor"].total_distance_m, 2),
                "optimized_distance_m": round(optimized, 2),
                "distance_saved_m": round(saved, 2),
                "percent_saved": round(pct, 1),
            })
        df = pd.DataFrame(rows)
        out_path = self.output_dir / "distance_summary.csv"
        df.to_csv(out_path, index=False)
        logger.info("Wrote distance summary: %s", out_path)
        return out_path

    # ------------------------------------------------------------------ #
    # Visualizations
    # ------------------------------------------------------------------ #

    def plot_warehouse_layout(self, warehouse: WarehouseGrid) -> Path:
        fig, ax = plt.subplots(figsize=(10, 7))
        zones = warehouse.zones()
        color_map = {zone: _ZONE_COLORS[i % len(_ZONE_COLORS)] for i, zone in enumerate(zones)}

        for zone in zones:
            xs = [loc.x for loc in warehouse.locations.values() if loc.zone == zone]
            ys = [loc.y for loc in warehouse.locations.values() if loc.zone == zone]
            ax.scatter(xs, ys, s=14, color=color_map[zone], label=zone, alpha=0.75)

        depot = warehouse.get(DEPOT_ID)
        ax.scatter([depot.x], [depot.y], s=180, color="black", marker="*",
                   label="Depot / Packing Station", zorder=5)

        ax.set_title("Warehouse Layout by Zone")
        ax.set_xlabel("X position (m, across aisles)")
        ax.set_ylabel("Y position (m, along aisle)")
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=8)
        ax.grid(True, linestyle="--", alpha=0.3)
        fig.tight_layout()

        out_path = self.output_dir / "warehouse_layout.png"
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        logger.info("Saved warehouse layout plot: %s", out_path)
        return out_path

    def plot_route(self, warehouse: WarehouseGrid, result: RouteResult, algorithm_label: str) -> Path:
        fig, ax = plt.subplots(figsize=(9, 7))

        # Faint backdrop of every warehouse location for spatial context.
        all_x = [loc.x for loc in warehouse.locations.values() if loc.location_id != DEPOT_ID]
        all_y = [loc.y for loc in warehouse.locations.values() if loc.location_id != DEPOT_ID]
        ax.scatter(all_x, all_y, s=8, color="lightgray", zorder=1)

        depot = warehouse.get(DEPOT_ID)
        path_ids = [DEPOT_ID] + result.stop_sequence + [DEPOT_ID]
        xs = [warehouse.get(loc_id).x for loc_id in path_ids]
        ys = [warehouse.get(loc_id).y for loc_id in path_ids]

        ax.plot(xs, ys, color="#4C72B0", linewidth=1.5, zorder=2, marker="o", markersize=4)
        ax.scatter([depot.x], [depot.y], s=160, color="black", marker="*", zorder=4, label="Depot")

        for idx, loc_id in enumerate(result.stop_sequence, start=1):
            loc = warehouse.get(loc_id)
            ax.annotate(str(idx), (loc.x, loc.y), fontsize=7, xytext=(3, 3), textcoords="offset points")

        ax.set_title(
            f"{result.batch_id} — {algorithm_label}\n"
            f"{result.num_stops} stops, {result.total_distance_m:.1f} m, "
            f"~{result.estimated_time_sec / 60.0:.1f} min"
        )
        ax.set_xlabel("X position (m)")
        ax.set_ylabel("Y position (m)")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, linestyle="--", alpha=0.3)
        fig.tight_layout()

        out_path = self.output_dir / f"route_{result.batch_id.lower()}.png"
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        return out_path

    def plot_before_after(self, all_results: Dict[str, Dict[str, RouteResult]]) -> Path:
        batch_ids = list(all_results.keys())
        baseline = [all_results[b]["greedy"].total_distance_m for b in batch_ids]
        nn = [all_results[b]["nearest_neighbor"].total_distance_m for b in batch_ids]
        optimized = [all_results[b]["two_opt"].total_distance_m for b in batch_ids]

        fig, ax = plt.subplots(figsize=(max(8, len(batch_ids) * 0.6), 6))
        x = range(len(batch_ids))
        width = 0.27

        ax.bar([i - width for i in x], baseline, width=width, label="Greedy (baseline)", color="#C44E52")
        ax.bar(list(x), nn, width=width, label="Nearest Neighbor", color="#DD8452")
        ax.bar([i + width for i in x], optimized, width=width,
               label="NN + 2-opt (optimized)", color="#55A868")

        ax.set_xticks(list(x))
        ax.set_xticklabels(batch_ids, rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("Total route distance (m)")
        ax.set_title("Route Distance: Before vs. After Optimization, by Batch")
        ax.legend()
        ax.grid(True, axis="y", linestyle="--", alpha=0.3)
        fig.tight_layout()

        out_path = self.output_dir / "before_after_comparison.png"
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        logger.info("Saved before/after comparison plot: %s", out_path)
        return out_path
