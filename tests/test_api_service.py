"""
Tests for src/api_service.py (the API's service layer).

These use the project's own recorded engine output
(frontend/data/dashboard_data.json) as the input payload, so they check the
response shaping against real data without needing to re-run CP-SAT or a web
server.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import api_service
from api_service import (
    ALGO_KEYS,
    UnknownAlgorithmError,
    build_response,
    normalize_algorithm,
    run_optimization,
)

PAYLOAD_PATH = Path(__file__).resolve().parent.parent / "frontend" / "data" / "dashboard_data.json"


def _payload() -> dict:
    return json.loads(PAYLOAD_PATH.read_text())


@pytest.mark.parametrize("value,expected", [
    (None, "two_opt"),
    ("", "two_opt"),
    ("greedy", "greedy"),
    ("Nearest_Neighbor", "nearest_neighbor"),
    ("nearest-neighbor", "nearest_neighbor"),
    ("2-opt", "two_opt"),
    ("two_opt", "two_opt"),
])
def test_normalize_algorithm_accepts_supported_names(value, expected):
    assert normalize_algorithm(value) == expected


def test_normalize_algorithm_rejects_unknown_name():
    with pytest.raises(UnknownAlgorithmError):
        normalize_algorithm("simulated_annealing")


def test_build_response_keeps_dashboard_shape():
    payload = _payload()
    resp = build_response(payload, "two_opt")
    # Exactly what the dashboard reads, unchanged.
    assert resp["meta"] == payload["meta"]
    assert resp["warehouse"] == payload["warehouse"]
    assert len(resp["batches"]) == len(payload["batches"])
    for original, enriched in zip(payload["batches"], resp["batches"]):
        assert {k: v for k, v in enriched.items() if k != "selected_route"} == original


def test_build_response_comparison_matches_batch_routes():
    payload = _payload()
    resp = build_response(payload, "nearest_neighbor")
    by_algo = {row["algorithm"]: row for row in resp["algorithm_comparison"]}
    assert set(by_algo) == set(ALGO_KEYS)
    for key in ALGO_KEYS:
        expected = sum(b["routes"][key]["distance_m"] for b in payload["batches"])
        assert by_algo[key]["total_distance_m"] == pytest.approx(expected, abs=0.05)
    assert by_algo["greedy"]["saved_vs_greedy_m"] == 0.0


def test_build_response_selected_algorithm_drives_metrics_and_selected_route():
    payload = _payload()
    resp = build_response(payload, "greedy")
    assert resp["algorithm"] == "greedy"
    assert resp["metrics"]["distance_saved_m"] == 0.0
    for batch in resp["batches"]:
        assert batch["selected_route"]["algorithm"] == "greedy"
        assert batch["selected_route"]["sequence"] == batch["routes"]["greedy"]["sequence"]


def test_run_optimization_uses_injected_builder_and_validates_first():
    calls = []

    def fake_builder():
        calls.append(1)
        return _payload()

    resp = run_optimization("2-opt", payload_builder=fake_builder)
    assert resp["algorithm"] == "two_opt" and len(calls) == 1

    with pytest.raises(UnknownAlgorithmError):
        run_optimization("bogus", payload_builder=fake_builder)
    assert len(calls) == 1  # bad input never triggers a run
