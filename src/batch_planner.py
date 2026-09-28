"""
batch_planner.py
-----------------
Groups orders into picking batches (one batch = one tote/one picker trip)
subject to capacity constraints (max units, max weight, max volume).

This is a classic *bin packing* problem: orders are items with a weight and
a volume, totes are bins with fixed capacity, and we want to use as few
totes as possible (fewer batches = fewer picker trips = lower labor cost).

Two strategies are implemented:

1. First-Fit Decreasing (FFD) -- a fast, well-understood greedy heuristic
   used here both as a standalone baseline and to seed a feasible upper
   bound for the exact solver.
2. Google OR-Tools CP-SAT -- an exact constraint solver that is given the
   FFD bin count as its search space and asked to minimize the number of
   totes actually used. It frequently beats FFD by a few batches, which
   directly translates into fewer picker trips.

The two results are both retained so the caller can report the improvement,
mirroring the "before vs after" comparison the route optimizer does for
distance.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List

from ortools.sat.python import cp_model

from models import Batch, InventoryItem, Order
from utils import CP_SAT_TIME_LIMIT_SEC, TOTE_MAX_ITEMS, TOTE_MAX_VOLUME_M3, TOTE_MAX_WEIGHT_KG

logger = logging.getLogger("warehouse_optimizer")


@dataclass
class BatchingStats:
    """Summary of how batching performed, for reporting purposes."""

    num_orders: int
    ffd_batch_count: int
    optimized_batch_count: int

    @property
    def batches_saved(self) -> int:
        return self.ffd_batch_count - self.optimized_batch_count

    @property
    def percent_saved(self) -> float:
        if self.ffd_batch_count == 0:
            return 0.0
        return 100.0 * self.batches_saved / self.ffd_batch_count


class BatchPlanner:
    """Assigns orders to capacity-constrained picking batches."""

    def __init__(
        self,
        inventory: Dict[str, InventoryItem],
        max_items: int = TOTE_MAX_ITEMS,
        max_weight_kg: float = TOTE_MAX_WEIGHT_KG,
        max_volume_m3: float = TOTE_MAX_VOLUME_M3,
    ):
        self.inventory = inventory
        self.max_items = max_items
        self.max_weight_kg = max_weight_kg
        self.max_volume_m3 = max_volume_m3
        self.last_stats: BatchingStats | None = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def plan_batches(self, orders: List[Order]) -> List[Batch]:
        """
        Assign every order to exactly one batch, respecting tote capacity,
        and return the batches ordered so high-priority / earlier-due
        batches are picked first.
        """
        if not orders:
            self.last_stats = BatchingStats(0, 0, 0)
            return []

        self._validate_orders_fit_capacity(orders)

        ffd_assignment = self._first_fit_decreasing(orders)
        ffd_count = len(ffd_assignment)

        optimized_assignment = self._solve_with_cp_sat(orders, upper_bound=ffd_count)
        if optimized_assignment is None:
            logger.warning("CP-SAT batching did not return a solution; falling back to FFD.")
            optimized_assignment = ffd_assignment

        self.last_stats = BatchingStats(
            num_orders=len(orders),
            ffd_batch_count=ffd_count,
            optimized_batch_count=len(optimized_assignment),
        )
        logger.info(
            "Batching: FFD baseline=%d batches, CP-SAT optimized=%d batches (%.1f%% fewer)",
            ffd_count, len(optimized_assignment), self.last_stats.percent_saved,
        )

        return self._to_ordered_batches(optimized_assignment)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _order_metrics(self, order: Order) -> tuple[float, float, int]:
        weight = order.total_weight_kg(self.inventory)
        volume = order.total_volume_m3(self.inventory)
        units = order.total_units()
        return weight, volume, units

    def _validate_orders_fit_capacity(self, orders: List[Order]) -> None:
        """Fail fast with a clear message if a single order can never fit in a tote."""
        for order in orders:
            weight, volume, units = self._order_metrics(order)
            if weight > self.max_weight_kg or volume > self.max_volume_m3 or units > self.max_items:
                raise ValueError(
                    f"Order {order.order_id} ({weight:.2f}kg, {volume:.3f}m3, {units} units) "
                    f"exceeds single-tote capacity "
                    f"({self.max_weight_kg}kg, {self.max_volume_m3}m3, {self.max_items} units) "
                    f"and cannot be split across totes in this planner."
                )

    def _first_fit_decreasing(self, orders: List[Order]) -> List[List[Order]]:
        """Greedy baseline: sort orders largest-first, drop each into the first bin that fits."""
        # Sort by weight utilization descending (a common FFD key for multi-dimensional packing).
        sorted_orders = sorted(
            orders,
            key=lambda o: self._order_metrics(o)[0],
            reverse=True,
        )

        bins: List[List[Order]] = []
        bin_totals: List[tuple[float, float, int]] = []  # (weight, volume, units)

        for order in sorted_orders:
            weight, volume, units = self._order_metrics(order)
            placed = False
            for i, (bw, bv, bu) in enumerate(bin_totals):
                if (
                    bw + weight <= self.max_weight_kg
                    and bv + volume <= self.max_volume_m3
                    and bu + units <= self.max_items
                ):
                    bins[i].append(order)
                    bin_totals[i] = (bw + weight, bv + volume, bu + units)
                    placed = True
                    break
            if not placed:
                bins.append([order])
                bin_totals.append((weight, volume, units))

        return bins

    def _solve_with_cp_sat(
        self, orders: List[Order], upper_bound: int
    ) -> List[List[Order]] | None:
        """
        Exact bin-packing model: minimize the number of totes used, subject
        to weight/volume/unit-count capacity per tote. `upper_bound` bins
        are made available (from the FFD heuristic); CP-SAT is free to
        leave any of them empty if a tighter packing exists.
        """
        n = len(orders)
        num_bins = max(1, upper_bound)

        weights = [int(round(self._order_metrics(o)[0] * 1000)) for o in orders]   # grams
        volumes = [int(round(self._order_metrics(o)[1] * 1_000_000)) for o in orders]  # cm3
        units = [self._order_metrics(o)[2] for o in orders]

        max_weight = int(round(self.max_weight_kg * 1000))
        max_volume = int(round(self.max_volume_m3 * 1_000_000))
        max_units = self.max_items

        model = cp_model.CpModel()

        # x[o][b] = 1 if order o is assigned to bin b
        x = [[model.NewBoolVar(f"x_{o}_{b}") for b in range(num_bins)] for o in range(n)]
        # y[b] = 1 if bin b is used at all
        y = [model.NewBoolVar(f"y_{b}") for b in range(num_bins)]

        # Each order assigned to exactly one bin.
        for o in range(n):
            model.Add(sum(x[o][b] for b in range(num_bins)) == 1)

        # Capacity constraints per bin, and bins can only hold orders if "used".
        for b in range(num_bins):
            model.Add(sum(x[o][b] * weights[o] for o in range(n)) <= max_weight * y[b])
            model.Add(sum(x[o][b] * volumes[o] for o in range(n)) <= max_volume * y[b])
            model.Add(sum(x[o][b] * units[o] for o in range(n)) <= max_units * y[b])
            for o in range(n):
                model.Add(x[o][b] <= y[b])

        # Symmetry breaking: used bins are "filled" from bin 0 upward. This
        # dramatically shrinks the search space for the solver.
        for b in range(num_bins - 1):
            model.Add(y[b] >= y[b + 1])

        model.Minimize(sum(y))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = CP_SAT_TIME_LIMIT_SEC
        # Parallel portfolio search reaches optimality far faster than a
        # single worker on this model. Requesting more workers than the
        # machine actually has doesn't just waste the extra threads -- it
        # makes CP-SAT slower via context-switching overhead, so the worker
        # count is capped to the available CPU cores (falling back to 1 if
        # that can't be determined). A fixed seed is set for as much
        # run-to-run consistency as CP-SAT's parallel search allows; the
        # *number* of batches found is stable, though which specific orders
        # land in which batch can vary by a run when multiple equally-optimal
        # packings exist (a known characteristic of parallel CP-SAT search).
        solver.parameters.num_search_workers = min(8, os.cpu_count() or 1)
        solver.parameters.random_seed = 42

        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None

        bins: List[List[Order]] = [[] for _ in range(num_bins)]
        for o in range(n):
            for b in range(num_bins):
                if solver.Value(x[o][b]) == 1:
                    bins[b].append(orders[o])
                    break
        non_empty = [b for b in bins if b]

        if status == cp_model.OPTIMAL:
            logger.info("CP-SAT found an OPTIMAL batching solution (%d bins).", len(non_empty))
        else:
            logger.info(
                "CP-SAT found a FEASIBLE (not proven optimal) batching solution "
                "within the %.0fs time budget (%d bins).",
                CP_SAT_TIME_LIMIT_SEC, len(non_empty),
            )
        return non_empty

    def _to_ordered_batches(self, assignment: List[List[Order]]) -> List[Batch]:
        """
        Convert raw order groups into Batch objects, numbered so that
        higher-priority / earlier-due batches are picked first -- this is
        what actually makes "priority" meaningful in the schedule.
        """
        raw_batches = [Batch(batch_id="TEMP", orders=orders) for orders in assignment]
        raw_batches.sort(key=lambda b: (-b.average_priority_weight(), b.earliest_due_date()))

        for idx, batch in enumerate(raw_batches, start=1):
            batch.batch_id = f"BATCH-{idx:03d}"
        return raw_batches
