"""
utils.py
--------
Shared constants, logging setup, and small pure-function helpers used across
the warehouse picking optimization engine.

Keeping these in one place avoids magic numbers being scattered through the
codebase and makes the operational assumptions of the simulation explicit
and easy to tune.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from pathlib import Path


# --------------------------------------------------------------------------- #
# Warehouse operating constants
#
# These are ASSUMED, illustrative values for a small/medium fulfillment
# warehouse (walking speed, per-stop pick time, tote limits). They are not
# measured from any real site; time estimates derived from them are model
# outputs, not observations. Tune them to match your own operation.
# --------------------------------------------------------------------------- #

# Picker/tote capacity constraints used by the batch planner.
TOTE_MAX_ITEMS: int = 24          # max number of physical units per tote
TOTE_MAX_WEIGHT_KG: float = 9.0   # max combined weight per tote (single-picker ergonomic limit)
TOTE_MAX_VOLUME_M3: float = 0.45  # max combined volume per tote (~standard tote)

# Picker movement assumptions.
WALKING_SPEED_MPS: float = 1.2    # average warehouse walking speed (m/s)
PICK_TIME_SEC: float = 18.0       # average time to locate + pick + scan one stop
DEPOT_ID: str = "DEPOT"           # packing/staging station, start & end of every route

# 2-opt local search cutoff so route optimization stays fast even on large batches.
TWO_OPT_MAX_ITERATIONS: int = 400

# OR-Tools CP-SAT solver time budget (seconds) for the batching model.
CP_SAT_TIME_LIMIT_SEC: float = 20.0


def setup_logger(name: str = "warehouse_optimizer", verbose: bool = False) -> logging.Logger:
    """Configure and return a console logger with a consistent format."""
    logger = logging.getLogger(name)
    if logger.handlers:
        # Avoid duplicate handlers if setup_logger is called more than once.
        return logger

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def manhattan_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """
    Manhattan (grid/L1) distance between two points.

    Warehouse pickers move along aisles and cross-aisles rather than in
    straight diagonal lines, so Manhattan distance models real walking
    distance far more accurately than Euclidean distance.
    """
    return abs(x1 - x2) + abs(y1 - y2)


def euclidean_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Straight-line distance, kept for reference/comparison purposes only."""
    return math.hypot(x1 - x2, y1 - y2)


def estimate_travel_time_sec(distance_m: float, num_stops: int) -> float:
    """
    Estimate total route time: walking time plus a fixed pick time at each stop.
    The depot (start) is not counted as a pick stop.
    """
    walking_time = distance_m / WALKING_SPEED_MPS
    picking_time = max(0, num_stops) * PICK_TIME_SEC
    return walking_time + picking_time


def format_seconds(seconds: float) -> str:
    """Human-readable mm:ss formatting for console summaries."""
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes:02d}m {secs:02d}s"


def ensure_dir(path: str | os.PathLike) -> Path:
    """Create a directory (including parents) if it does not already exist."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_root() -> Path:
    """Return the project root directory (parent of src/)."""
    return Path(__file__).resolve().parent.parent
