import logging

import pytest

from csv_loader import (
    load_inventory,
    load_orders,
    load_warehouse_layout,
    validate_cross_references,
)

LAYOUT_HEADER = "location_id,aisle,rack,shelf,bin,zone,x,y\n"
INVENTORY_HEADER = "item_id,item_name,location_id,weight_kg,volume_m3,stock_qty,category\n"
ORDERS_HEADER = "order_id,customer,priority,due_date,item_id,quantity\n"


def write_csv(tmp_path, name, header, rows):
    path = tmp_path / name
    path.write_text(header + "\n".join(rows))
    return path


# ---------------------------------------------------------------- #
# Missing files / missing columns
# ---------------------------------------------------------------- #

def test_missing_file_raises_file_not_found_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_warehouse_layout(tmp_path / "does_not_exist.csv")


def test_missing_required_column_raises_value_error(tmp_path):
    path = write_csv(tmp_path, "layout.csv", "location_id,aisle,rack,shelf,bin,zone,x\n", ["L1,A,R,S,B,Z,1"])
    with pytest.raises(ValueError, match="missing required columns"):
        load_warehouse_layout(path)


def test_orders_load_without_customer_column(tmp_path):
    # The customer column is unused by the pipeline, so it must be optional.
    path = write_csv(tmp_path, "orders.csv",
                     "order_id,priority,due_date,item_id,quantity\n",
                     ["ORD-1,High,2026-01-01,SKU-1,2"])
    orders = load_orders(path)
    assert [o.order_id for o in orders] == ["ORD-1"]
    assert orders[0].customer == ""


# ---------------------------------------------------------------- #
# Row-level validation
# ---------------------------------------------------------------- #

def test_inventory_rejects_non_positive_weight_and_volume(tmp_path):
    path = write_csv(tmp_path, "inv.csv", INVENTORY_HEADER, [
        "SKU-OK,Item,L1,1.0,0.01,10,Misc",
        "SKU-BAD-WEIGHT,Item,L1,-1.0,0.01,10,Misc",
        "SKU-BAD-VOLUME,Item,L1,1.0,0,10,Misc",
    ])
    inventory = load_inventory(path)
    assert set(inventory.keys()) == {"SKU-OK"}


def test_inventory_rejects_negative_stock(tmp_path):
    path = write_csv(tmp_path, "inv.csv", INVENTORY_HEADER, [
        "SKU-OK,Item,L1,1.0,0.01,10,Misc",
        "SKU-BAD-STOCK,Item,L1,1.0,0.01,-5,Misc",
    ])
    inventory = load_inventory(path)
    assert set(inventory.keys()) == {"SKU-OK"}


def test_inventory_duplicate_item_id_keeps_last_row_and_warns(tmp_path, caplog):
    path = write_csv(tmp_path, "inv.csv", INVENTORY_HEADER, [
        "SKU-1,First,L1,1.0,0.01,10,Misc",
        "SKU-1,Second,L2,2.0,0.02,20,Misc",
    ])
    with caplog.at_level(logging.WARNING):
        inventory = load_inventory(path)
    assert len(inventory) == 1
    assert inventory["SKU-1"].item_name == "Second"
    assert any("Duplicate item_id" in r.message for r in caplog.records)


def test_orders_rejects_non_positive_quantity(tmp_path):
    path = write_csv(tmp_path, "orders.csv", ORDERS_HEADER, [
        "ORD-1,Cust,High,2026-01-01,SKU-1,2",
        "ORD-1,Cust,High,2026-01-01,SKU-2,0",
    ])
    orders = load_orders(path)
    assert len(orders) == 1
    assert len(orders[0].lines) == 1
    assert orders[0].lines[0].item_id == "SKU-1"


def test_order_with_only_invalid_lines_is_dropped_entirely(tmp_path, caplog):
    path = write_csv(tmp_path, "orders.csv", ORDERS_HEADER, [
        "ORD-1,Cust,High,2026-01-01,SKU-1,0",
        "ORD-2,Cust,High,2026-01-01,SKU-2,3",
    ])
    with caplog.at_level(logging.WARNING):
        orders = load_orders(path)
    assert [o.order_id for o in orders] == ["ORD-2"]
    assert any("zero valid lines" in r.message for r in caplog.records)


def test_orders_unknown_priority_is_skipped(tmp_path):
    path = write_csv(tmp_path, "orders.csv", ORDERS_HEADER, [
        "ORD-1,Cust,Urgent,2026-01-01,SKU-1,2",  # "Urgent" isn't a valid Priority
        "ORD-2,Cust,High,2026-01-01,SKU-2,1",
    ])
    orders = load_orders(path)
    assert [o.order_id for o in orders] == ["ORD-2"]


# ---------------------------------------------------------------- #
# Cross-reference validation
# ---------------------------------------------------------------- #

def test_validate_cross_references_warns_on_unknown_item_and_location(tmp_path, caplog):
    layout = load_warehouse_layout(write_csv(tmp_path, "layout.csv", LAYOUT_HEADER, [
        "L1,A,R,S,B,Zone-1,0,0",
    ]))
    inventory = load_inventory(write_csv(tmp_path, "inv.csv", INVENTORY_HEADER, [
        "SKU-1,Item,L1,1.0,0.01,10,Misc",
        "SKU-2,Item,L-GHOST,1.0,0.01,10,Misc",  # location_id not in layout
    ]))
    orders = load_orders(write_csv(tmp_path, "orders.csv", ORDERS_HEADER, [
        "ORD-1,Cust,High,2026-01-01,SKU-1,1",
        "ORD-2,Cust,High,2026-01-01,SKU-GHOST,1",  # item_id not in inventory
    ]))

    with caplog.at_level(logging.WARNING):
        validate_cross_references(layout, inventory, orders)

    messages = " ".join(r.message for r in caplog.records)
    assert "SKU-GHOST" in messages
    assert "L-GHOST" in messages


def test_validate_cross_references_silent_on_clean_data(tmp_path, caplog):
    layout = load_warehouse_layout(write_csv(tmp_path, "layout.csv", LAYOUT_HEADER, [
        "L1,A,R,S,B,Zone-1,0,0",
    ]))
    inventory = load_inventory(write_csv(tmp_path, "inv.csv", INVENTORY_HEADER, [
        "SKU-1,Item,L1,1.0,0.01,10,Misc",
    ]))
    orders = load_orders(write_csv(tmp_path, "orders.csv", ORDERS_HEADER, [
        "ORD-1,Cust,High,2026-01-01,SKU-1,1",
    ]))

    with caplog.at_level(logging.WARNING):
        validate_cross_references(layout, inventory, orders)

    assert caplog.records == []
