import pytest

from models import Batch, Priority


def test_priority_from_str_accepts_case_and_whitespace_variants():
    assert Priority.from_str("high") == Priority.HIGH
    assert Priority.from_str(" High ") == Priority.HIGH
    assert Priority.from_str("LOW") == Priority.LOW


def test_priority_from_str_rejects_unknown_value():
    with pytest.raises(ValueError):
        Priority.from_str("Urgent")


def test_priority_weight_ordering():
    assert Priority.HIGH.weight > Priority.MEDIUM.weight > Priority.LOW.weight


def test_order_totals_ignore_items_missing_from_inventory(small_orders, small_inventory):
    order = small_orders[0]  # 1x SKU-1, weight 1.0kg
    assert order.total_weight_kg(small_inventory) == 1.0
    assert order.total_units() == 1

    # An inventory dict that doesn't have SKU-1 at all -> totals silently
    # exclude it rather than raising, per the documented behavior.
    assert order.total_weight_kg({}) == 0.0


def test_batch_totals_sum_across_orders(small_orders, small_inventory):
    batch = Batch(batch_id="BATCH-001", orders=small_orders)
    assert batch.total_weight_kg(small_inventory) == pytest.approx(1.0 + 2.0 + 3.0)
    assert batch.total_units() == 3
    assert set(batch.location_ids(small_inventory)) == {"A", "B", "C"}


def test_batch_average_priority_weight_empty_batch_is_zero():
    assert Batch(batch_id="BATCH-EMPTY").average_priority_weight() == 0.0
