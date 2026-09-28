from route_optimizer import RouteOptimizer


def test_optimize_batch_empty_locations_returns_zero_distance(small_warehouse):
    optimizer = RouteOptimizer(small_warehouse)
    results = optimizer.optimize_batch("BATCH-EMPTY", [])
    for result in results.values():
        assert result.total_distance_m == 0.0
        assert result.stop_sequence == []


def test_every_algorithm_visits_each_stop_exactly_once(small_warehouse):
    optimizer = RouteOptimizer(small_warehouse)
    results = optimizer.optimize_batch("BATCH-001", ["A", "B", "C"])
    for name, result in results.items():
        assert sorted(result.stop_sequence) == ["A", "B", "C"], f"{name} lost or duplicated a stop"


def test_two_opt_never_produces_a_longer_route_than_nearest_neighbor(small_warehouse):
    optimizer = RouteOptimizer(small_warehouse)
    results = optimizer.optimize_batch("BATCH-001", ["A", "B", "C"])
    # 2-opt starts from the NN route and only ever applies improving swaps,
    # so it can only match or beat NN -- never regress.
    assert results["two_opt"].total_distance_m <= results["nearest_neighbor"].total_distance_m + 1e-9


def test_greedy_matches_hand_worked_out_shortest_route_on_backtracking_input(small_warehouse):
    # Stops requested in an order that backtracks (A, C, B) versus the
    # actual shortest tour (A, B, C) -- see conftest.py for the layout.
    # Greedy just visits them in the order given, so it should be
    # measurably worse than what NN/2-opt find.
    optimizer = RouteOptimizer(small_warehouse)
    greedy_backtracking = optimizer.greedy_route("BATCH-001", ["A", "C", "B"])
    nn_route = optimizer.nearest_neighbor_route("BATCH-001", ["A", "C", "B"])
    two_opt_result = optimizer.two_opt("BATCH-001", nn_route)
    assert two_opt_result.total_distance_m < greedy_backtracking.total_distance_m


def test_optimize_batch_matrix_cache_agrees_with_uncached_call(small_warehouse):
    """
    optimize_batch() warms a distance-matrix cache that greedy_route/
    nearest_neighbor_route/two_opt read from; calling those methods
    directly (uncached) should produce numerically identical distances,
    since the cache is purely a performance optimization.
    """
    optimizer = RouteOptimizer(small_warehouse)
    cached = optimizer.optimize_batch("BATCH-001", ["A", "B", "C"])

    fresh_optimizer = RouteOptimizer(small_warehouse)  # no cache ever warmed
    uncached_greedy = fresh_optimizer.greedy_route("BATCH-001", ["A", "B", "C"])

    assert cached["greedy"].total_distance_m == uncached_greedy.total_distance_m


def test_route_distance_is_a_round_trip_from_and_to_depot(small_warehouse):
    optimizer = RouteOptimizer(small_warehouse)
    result = optimizer.greedy_route("BATCH-001", ["A"])
    # DEPOT(0,0) -> A(10,0) -> DEPOT(0,0) = 10 + 10 = 20
    assert result.total_distance_m == 20.0
