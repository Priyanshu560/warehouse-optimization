"""
api_service.py
--------------
Thin service layer between the FastAPI app (../api.py) and the existing
optimization engine.

It contains NO optimization logic. The heavy lifting is done by
``export_dashboard_data.build_payload()``, which already runs the real
pipeline (csv_loader -> warehouse -> BatchPlanner/CP-SAT -> RouteOptimizer)
and returns the exact structure the dashboard renders. This module only:

  * validates / normalizes the requested routing algorithm,
  * serializes runs (CP-SAT is CPU heavy; one solve at a time is enough),
  * adds a few summary blocks (metrics, algorithm comparison, per-batch
    "selected_route") derived from numbers the engine already produced.

It deliberately imports nothing from FastAPI so it can be unit-tested (and
reused from a script) without the web dependencies.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

# Internal algorithm keys -- these match RouteOptimizer.optimize_batch() and
# the keys already used in dashboard_data.json / frontend/app.js.
ALGO_KEYS = ("greedy", "nearest_neighbor", "two_opt")

ALGO_LABELS = {
    "greedy": "Greedy",
    "nearest_neighbor": "Nearest neighbor",
    "two_opt": "2-opt",
}

DEFAULT_ALGORITHM = "two_opt"

# Accepted spellings -> internal key. "2-opt" is the public name; "two_opt"
# is what the engine and the dashboard use internally.
_ALGO_ALIASES = {
    "greedy": "greedy",
    "nearest_neighbor": "nearest_neighbor",
    "nearest-neighbor": "nearest_neighbor",
    "2-opt": "two_opt",
    "2_opt": "two_opt",
    "two_opt": "two_opt",
    "two-opt": "two_opt",
}

# One optimization at a time: CP-SAT already uses every core, so running two
# solves concurrently on a small instance only makes both slower.
_run_lock = threading.Lock()


class UnknownAlgorithmError(ValueError):
    """Raised when the requested routing algorithm is not supported."""


def normalize_algorithm(value: Optional[str]) -> str:
    """Map a user-supplied algorithm name to an internal key (default: 2-opt)."""
    if value is None or not str(value).strip():
        return DEFAULT_ALGORITHM
    key = _ALGO_ALIASES.get(str(value).strip().lower())
    if key is None:
        raise UnknownAlgorithmError(
            f"Unknown algorithm {value!r}. Use one of: greedy, nearest_neighbor, 2-opt."
        )
    return key


def build_response(payload: dict, algorithm: str) -> dict:
    """
    Wrap a dashboard payload with API-level summaries.

    ``meta``, ``warehouse`` and ``batches`` are returned exactly as the
    engine produced them (same shape as frontend/data/dashboard_data.json),
    so the dashboard can consume the response unchanged. Extra keys are only
    added, never renamed.
    """
    batches = payload["batches"]
    meta = payload["meta"]

    totals: Dict[str, float] = {
        k: sum(b["routes"][k]["distance_m"] for b in batches) for k in ALGO_KEYS
    }
    times: Dict[str, float] = {
        k: sum(b["routes"][k]["time_min"] for b in batches) for k in ALGO_KEYS
    }
    baseline = totals["greedy"]

    def _entry(key: str) -> dict:
        saved = baseline - totals[key]
        return {
            "algorithm": key,
            "label": ALGO_LABELS[key],
            "total_distance_m": round(totals[key], 1),
            "total_time_min": round(times[key], 1),
            "saved_vs_greedy_m": round(saved, 1),
            "improvement_vs_greedy_pct": round(100.0 * saved / baseline, 1) if baseline > 0 else 0.0,
        }

    comparison = [_entry(k) for k in ALGO_KEYS]
    selected = _entry(algorithm)

    # Per-batch route for the requested algorithm (all three are still
    # present under batch["routes"], which is what the dashboard reads).
    enriched_batches = [
        {**b, "selected_route": {"algorithm": algorithm, **b["routes"][algorithm]}}
        for b in batches
    ]

    return {
        "algorithm": algorithm,
        "algorithm_label": ALGO_LABELS[algorithm],
        "metrics": {
            "orders_processed": meta["orders_processed"],
            "batches_ffd": meta["batches_ffd"],
            "batches_optimized": meta["batches_optimized"],
            "batches_saved": meta["batches_saved"],
            "baseline_distance_m": round(baseline, 1),
            "selected_distance_m": selected["total_distance_m"],
            "distance_saved_m": selected["saved_vs_greedy_m"],
            "improvement_pct": selected["improvement_vs_greedy_pct"],
            "runtime_sec": meta["runtime_sec"],
        },
        "algorithm_comparison": comparison,
        "meta": meta,
        "warehouse": payload["warehouse"],
        "batches": enriched_batches,
    }


def run_optimization(
    algorithm: Optional[str] = None,
    payload_builder: Optional[Callable[[], dict]] = None,
) -> dict:
    """
    Run the existing engine on the bundled CSV data and return the API response.

    ``payload_builder`` exists only so tests can inject a stand-in; in normal
    use it is ``export_dashboard_data.build_payload``.
    """
    key = normalize_algorithm(algorithm)  # validate before doing any work

    if payload_builder is None:
        # Imported lazily so importing this module never pulls in OR-Tools.
        from export_dashboard_data import build_payload as payload_builder

    with _run_lock:
        payload = payload_builder()
    return build_response(payload, key)
