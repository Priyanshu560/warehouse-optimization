"""
warehouse.py
------------
Represents the physical warehouse as a coordinate grid and provides
distance/matrix utilities used by both the batch planner (indirectly, via
weight/volume it doesn't need distance) and, primarily, the route optimizer.

Design notes
------------
Distance is modeled with Manhattan (L1) distance rather than Euclidean,
because pickers walk along aisles and cross-aisles -- they cannot cut
diagonally through racking. Vertical shelf/bin position affects reach/pick
time, not walking distance, since multi-level racks share the same aisle
floor position.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

import numpy as np

from models import Location
from utils import DEPOT_ID, manhattan_distance


class WarehouseGrid:
    """Holds every storage location plus the packing/staging depot."""

    def __init__(self, locations: Dict[str, Location]):
        if DEPOT_ID not in locations:
            locations = dict(locations)
            locations[DEPOT_ID] = Location(
                location_id=DEPOT_ID,
                aisle="-",
                rack="-",
                shelf="-",
                bin="-",
                zone="DEPOT",
                x=0.0,
                y=0.0,
            )
        self.locations: Dict[str, Location] = locations

    def __len__(self) -> int:
        return len(self.locations)

    def get(self, location_id: str) -> Location:
        try:
            return self.locations[location_id]
        except KeyError as exc:
            raise KeyError(f"Unknown warehouse location id: {location_id!r}") from exc

    def distance(self, location_id_a: str, location_id_b: str) -> float:
        """Manhattan walking distance in meters between two locations."""
        a = self.get(location_id_a)
        b = self.get(location_id_b)
        return manhattan_distance(a.x, a.y, b.x, b.y)

    def distance_matrix(self, location_ids: Iterable[str]) -> np.ndarray:
        """
        Build a symmetric distance matrix (in meters) for the given ordered
        list of location ids. Used by route optimization algorithms so they
        don't repeatedly recompute pairwise distances.
        """
        ids: List[str] = list(location_ids)
        n = len(ids)
        matrix = np.zeros((n, n), dtype=float)
        coords = [self.get(loc_id).coordinates() for loc_id in ids]
        for i in range(n):
            xi, yi = coords[i]
            for j in range(i + 1, n):
                xj, yj = coords[j]
                d = manhattan_distance(xi, yi, xj, yj)
                matrix[i, j] = d
                matrix[j, i] = d
        return matrix

    def zones(self) -> List[str]:
        """Distinct storage zones present in the layout (excluding the depot)."""
        return sorted({loc.zone for loc in self.locations.values() if loc.location_id != DEPOT_ID})

    def bounds(self) -> tuple[float, float, float, float]:
        """(min_x, max_x, min_y, max_y) across all locations, for plotting."""
        xs = [loc.x for loc in self.locations.values()]
        ys = [loc.y for loc in self.locations.values()]
        return min(xs), max(xs), min(ys), max(ys)
