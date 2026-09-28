from utils import estimate_travel_time_sec, format_seconds, manhattan_distance


def test_manhattan_distance_basic():
    # 3-4-5 triangle in Euclidean terms, but Manhattan distance is 3+4=7.
    assert manhattan_distance(0, 0, 3, 4) == 7


def test_manhattan_distance_same_point_is_zero():
    assert manhattan_distance(5.5, -2.0, 5.5, -2.0) == 0


def test_manhattan_distance_is_symmetric():
    assert manhattan_distance(1, 2, 8, -3) == manhattan_distance(8, -3, 1, 2)


def test_estimate_travel_time_includes_pick_time_per_stop():
    # 0 distance, 2 stops -> pure pick time, no walking time.
    from utils import PICK_TIME_SEC
    assert estimate_travel_time_sec(0.0, 2) == 2 * PICK_TIME_SEC


def test_estimate_travel_time_zero_stops_is_zero():
    assert estimate_travel_time_sec(0.0, 0) == 0.0


def test_format_seconds_rounds_to_mmss():
    assert format_seconds(125) == "02m 05s"
    assert format_seconds(59.6) == "01m 00s"
