"""
route_optimizer.py
-------------------
Computes picker routes through a batch's required warehouse locations.

Every route starts and ends at the depot (packing/staging station). Three
algorithms are implemented, in increasing order of sophistication:

1. Greedy (baseline) -- visits stops in whatever order they were
   encountered while assembling the batch, i.e. "no optimization applied".
   This is the honest "before" picture pickers experience without a
   routing system, and it's what every other algorithm is measured against.

2. Nearest Neighbor -- from the current position, always walk to the
   closest unvisited stop. Fast (O(n^2)) and typically cuts a large chunk
   of distance versus the unoptimized baseline.

3. 2-opt -- takes the Nearest Neighbor route and repeatedly reverses
   segments whenever doing so shortens the total route, until no further
   improving swap exists (a local optimum). This is the industry-standard
   refinement step for tour-construction heuristics and squeezes out most
   of the remaining slack from NN's occasional poor early choices.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

from models import RouteResult
from utils import DEPOT_ID, TWO_OPT_MAX_ITERATIONS, estimate_travel_time_sec
from warehouse import WarehouseGrid

logger = logging.getLogger("warehouse_optimizer")


class RouteOptimizer:
    """Builds and improves picker routes over a warehouse grid."""

    def __init__(self, warehouse: WarehouseGrid):
        self.warehouse = warehouse
        # Populated per-batch by optimize_batch() and cleared afterward. When
        # present, _dist() reads from this precomputed matrix instead of
        # going through WarehouseGrid.distance()'s dict lookups. 2-opt is the
        # reason this matters: a single batch can issue tens of thousands of
        # distance queries (O(n^2) per sweep, up to TWO_OPT_MAX_ITERATIONS
        # sweeps), and it repeatedly asks about the same pairs of locations
        # as it reorders the same stop set -- exactly the case a precomputed
        # matrix is for.
        self._matrix_index: Optional[Dict[str, int]] = None
        self._matrix: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def optimize_batch(self, batch_id: str, location_ids: List[str]) -> dict[str, RouteResult]:
        """
        Run all three algorithms on the same batch and return their results
        keyed by algorithm name, so callers can report the before/after
        distance comparison directly.
        """
        if not location_ids:
            empty = RouteResult(
                algorithm="none", batch_id=batch_id, stop_sequence=[],
                total_distance_m=0.0, num_stops=0, estimated_time_sec=0.0,
            )
            return {"greedy": empty, "nearest_neighbor": empty, "two_opt": empty}

        all_ids = [DEPOT_ID] + list(location_ids)
        self._matrix_index = {loc_id: i for i, loc_id in enumerate(all_ids)}
        self._matrix = self.warehouse.distance_matrix(all_ids)
        try:
            greedy = self.greedy_route(batch_id, location_ids)
            nn = self.nearest_neighbor_route(batch_id, location_ids)
            improved = self.two_opt(batch_id, nn)
        finally:
            self._matrix_index = None
            self._matrix = None

        return {"greedy": greedy, "nearest_neighbor": nn, "two_opt": improved}

    # ------------------------------------------------------------------ #
    # Algorithm 1: Greedy / unoptimized baseline
    # ------------------------------------------------------------------ #

    def greedy_route(self, batch_id: str, location_ids: List[str]) -> RouteResult:
        """Visit stops in their original (encountered) order -- no optimization."""
        sequence = list(location_ids)
        distance = self._route_distance(sequence)
        return RouteResult(
            algorithm="greedy",
            batch_id=batch_id,
            stop_sequence=sequence,
            total_distance_m=distance,
            num_stops=len(sequence),
            estimated_time_sec=estimate_travel_time_sec(distance, len(sequence)),
        )

    # ------------------------------------------------------------------ #
    # Algorithm 2: Nearest Neighbor
    # ------------------------------------------------------------------ #

    def nearest_neighbor_route(self, batch_id: str, location_ids: List[str]) -> RouteResult:
        """Greedily walk to the closest unvisited stop, starting from the depot."""
        # A list (not a set) keeps iteration order deterministic, so ties are
        # broken consistently by location_id rather than by Python's
        # randomized hash order -- important for reproducible results.
        remaining: List[str] = sorted(location_ids)
        sequence: List[str] = []
        current = DEPOT_ID

        while remaining:
            nearest = min(remaining, key=lambda loc: (self._dist(current, loc), loc))
            sequence.append(nearest)
            remaining.remove(nearest)
            current = nearest

        distance = self._route_distance(sequence)
        return RouteResult(
            algorithm="nearest_neighbor",
            batch_id=batch_id,
            stop_sequence=sequence,
            total_distance_m=distance,
            num_stops=len(sequence),
            estimated_time_sec=estimate_travel_time_sec(distance, len(sequence)),
        )

    # ------------------------------------------------------------------ #
    # Algorithm 3: 2-opt local search refinement
    # ------------------------------------------------------------------ #

    def two_opt(self, batch_id: str, base_route: RouteResult) -> RouteResult:
        """
        Improve an existing route by repeatedly reversing sub-segments
        whenever the swap shortens total travel distance. Terminates at a
        local optimum or after TWO_OPT_MAX_ITERATIONS full sweeps.
        """
        stops = list(base_route.stop_sequence)
        if len(stops) < 3:
            # Nothing to reorder with fewer than 3 stops.
            return RouteResult(
                algorithm="two_opt",
                batch_id=batch_id,
                stop_sequence=stops,
                total_distance_m=base_route.total_distance_m,
                num_stops=base_route.num_stops,
                estimated_time_sec=base_route.estimated_time_sec,
            )

        full_path = [DEPOT_ID] + stops + [DEPOT_ID]
        n = len(full_path)
        improved = True
        iterations = 0

        while improved and iterations < TWO_OPT_MAX_ITERATIONS:
            improved = False
            iterations += 1
            for i in range(1, n - 2):
                for j in range(i + 1, n - 1):
                    if j - i == 1:
                        continue  # adjacent edges, nothing to swap
                    delta = self._two_opt_delta(full_path, i, j)
                    if delta < -1e-9:
                        full_path[i:j + 1] = reversed(full_path[i:j + 1])
                        improved = True

        final_stops = full_path[1:-1]
        distance = self._route_distance(final_stops)
        logger.debug(
            "2-opt converged for %s after %d sweep(s): %.1fm -> %.1fm",
            batch_id, iterations, base_route.total_distance_m, distance,
        )
        return RouteResult(
            algorithm="two_opt",
            batch_id=batch_id,
            stop_sequence=final_stops,
            total_distance_m=distance,
            num_stops=len(final_stops),
            estimated_time_sec=estimate_travel_time_sec(distance, len(final_stops)),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _dist(self, location_id_a: str, location_id_b: str) -> float:
        """
        Distance between two locations, served from the precomputed matrix
        when optimize_batch() has one warmed up, otherwise computed directly.
        The fallback keeps greedy_route/nearest_neighbor_route/two_opt
        usable on their own (e.g. in tests) without going through
        optimize_batch first.
        """
        has_cached_pair = (
            self._matrix_index is not None
            and location_id_a in self._matrix_index
            and location_id_b in self._matrix_index
        )
        if has_cached_pair:
            i = self._matrix_index[location_id_a]
            j = self._matrix_index[location_id_b]
            return float(self._matrix[i, j])
        return self.warehouse.distance(location_id_a, location_id_b)

    def _route_distance(self, stops: List[str]) -> float:
        """Total round-trip distance: depot -> stops in order -> depot."""
        full_path = [DEPOT_ID] + stops + [DEPOT_ID]
        return sum(
            self._dist(full_path[i], full_path[i + 1])
            for i in range(len(full_path) - 1)
        )

    def _two_opt_delta(self, path: List[str], i: int, j: int) -> float:
        """
        Change in total distance from reversing path[i..j], computed from
        only the two edges that change (O(1) instead of recomputing the
        whole route on every candidate swap).
        """
        a, b = path[i - 1], path[i]
        c, d = path[j], path[j + 1]
        before = self._dist(a, b) + self._dist(c, d)
        after = self._dist(a, c) + self._dist(b, d)
        return after - before
