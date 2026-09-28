"""
main.py
-------
Entry point for the Warehouse Picking Optimization Engine.

Pipeline:
    1. Load warehouse layout, inventory, and orders from CSV.
    2. Plan picking batches (OR-Tools CP-SAT bin packing vs. FFD baseline).
    3. Optimize a walking route for every batch (Greedy vs. NN vs. 2-opt).
    4. Write CSV reports and PNG visualizations to output/.
    5. Print a console summary of distance and batch-count savings.

Usage:
    python main.py
    python main.py --data-dir ../data --output-dir ../output
    python main.py --max-route-plots 5 --verbose
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Dict

from batch_planner import BatchPlanner
from csv_loader import load_inventory, load_orders, load_warehouse_layout, validate_cross_references
from models import RouteResult
from report_generator import ReportGenerator
from route_optimizer import RouteOptimizer
from utils import format_seconds, setup_logger
from warehouse import WarehouseGrid


def _non_negative_int(value: str) -> int:
    """argparse type: rejects negative integers with a clear message instead
    of silently accepting them and producing zero plots later."""
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or greater, got {parsed}")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Warehouse Picking Optimization Engine — batch planning and route optimization."
    )
    default_data = Path(__file__).resolve().parent.parent / "src" / "data"
    default_output = Path(__file__).resolve().parent.parent / "output"

    parser.add_argument("--data-dir", type=Path, default=default_data,
                         help="Directory containing orders.csv, inventory.csv, warehouse_layout.csv")
    parser.add_argument("--output-dir", type=Path, default=default_output,
                         help="Directory to write reports and plots to")
    parser.add_argument("--max-route-plots", type=_non_negative_int, default=6,
                         help="Max PNGs to render for individual routes (highest-priority batches first)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    logger = setup_logger(verbose=args.verbose)
    start_time = time.perf_counter()

    logger.info("=" * 70)
    logger.info("Warehouse Picking Optimization Engine")
    logger.info("=" * 70)

    # ---------------------------------------------------------------- #
    # 1. Load data
    # ---------------------------------------------------------------- #
    try:
        layout = load_warehouse_layout(args.data_dir / "warehouse_layout.csv")
        inventory = load_inventory(args.data_dir / "inventory.csv")
        orders = load_orders(args.data_dir / "orders.csv")
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Failed to load input data: %s", exc)
        return 1

    if not orders:
        logger.error("No orders were loaded; nothing to optimize.")
        return 1

    validate_cross_references(layout, inventory, orders)
    warehouse = WarehouseGrid(layout)

    # ---------------------------------------------------------------- #
    # 2. Batch planning
    # ---------------------------------------------------------------- #
    planner = BatchPlanner(inventory)
    try:
        batches = planner.plan_batches(orders)
    except ValueError as exc:
        logger.error("Batch planning failed: %s", exc)
        return 1

    stats = planner.last_stats
    logger.info(
        "Planned %d batches for %d orders (FFD baseline would have used %d batches, "
        "%.1f%% fewer batches with CP-SAT).",
        len(batches), stats.num_orders, stats.ffd_batch_count, stats.percent_saved,
    )

    # ---------------------------------------------------------------- #
    # 3. Route optimization per batch
    # ---------------------------------------------------------------- #
    optimizer = RouteOptimizer(warehouse)
    all_results: Dict[str, Dict[str, RouteResult]] = {}

    for batch in batches:
        location_ids = batch.location_ids(inventory)
        results = optimizer.optimize_batch(batch.batch_id, location_ids)
        all_results[batch.batch_id] = results

    total_baseline = sum(r["greedy"].total_distance_m for r in all_results.values())
    total_optimized = sum(r["two_opt"].total_distance_m for r in all_results.values())
    total_saved = total_baseline - total_optimized
    pct_distance_saved = (100.0 * total_saved / total_baseline) if total_baseline > 0 else 0.0

    logger.info(
        "Route optimization complete: %.1fm (baseline) -> %.1fm (optimized), "
        "%.1fm saved (%.1f%%).",
        total_baseline, total_optimized, total_saved, pct_distance_saved,
    )

    # ---------------------------------------------------------------- #
    # 4. Reports and visualizations
    # ---------------------------------------------------------------- #
    reporter = ReportGenerator(args.output_dir)
    reporter.write_batch_report(batches, inventory)
    reporter.write_route_report(all_results)
    reporter.write_distance_summary(all_results)

    reporter.plot_warehouse_layout(warehouse)

    # Render individual route maps for the highest-priority batches only,
    # to keep output/ manageable on large order volumes.
    plotted = 0
    for batch in batches:
        if plotted >= args.max_route_plots:
            break
        reporter.plot_route(warehouse, all_results[batch.batch_id]["two_opt"], "NN + 2-opt (optimized)")
        plotted += 1

    reporter.plot_before_after(all_results)

    # ---------------------------------------------------------------- #
    # 5. Console summary
    # ---------------------------------------------------------------- #
    elapsed = time.perf_counter() - start_time
    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info("Orders processed:        %d", len(orders))
    logger.info("Batches (optimized):     %d  (FFD baseline: %d, -%d batches)",
                 len(batches), stats.ffd_batch_count, stats.batches_saved)
    logger.info("Total distance baseline: %.1f m", total_baseline)
    logger.info("Total distance optimized:%.1f m", total_optimized)
    logger.info("Total distance saved:    %.1f m (%.1f%%)", total_saved, pct_distance_saved)
    logger.info("Route maps rendered:     %d", plotted)
    logger.info("Reports written to:      %s", args.output_dir.resolve())
    logger.info("Total runtime:           %s", format_seconds(elapsed))
    logger.info("=" * 70)

    return 0


def main() -> None:
    args = parse_args()
    try:
        exit_code = run(args)
    except Exception as exc:  # noqa: BLE001 - intentional last-resort guard
        # run() already handles the failure modes we expect (bad/missing
        # CSVs, infeasible batching). Anything that reaches here is
        # unexpected, so it's surfaced as a clean one-line error rather than
        # a raw traceback -- unless --verbose was passed, since the full
        # traceback is exactly what you want while debugging.
        logger = logging.getLogger("warehouse_optimizer")
        logger.error("Unexpected error: %s", exc, exc_info=args.verbose)
        if not args.verbose:
            logger.error("Re-run with --verbose for the full traceback.")
        exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
