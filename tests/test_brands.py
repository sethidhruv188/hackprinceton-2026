import pandas as pd
import pytest

from inventory import brand_orders as BO
from inventory import brands as B
from inventory import ingest


@pytest.mark.parametrize("name,brand", [
    ("MDH. Chunky Chat Masala 100 GM X 10", "MDH"),
    ("MDH Kitchen King 100 X 60", "MDH"),
    ("India Gate Classic Basmati Rice 10 lb X 4", "India Gate"),
    ("Everest Pav Bhaji Masala 100 gm X 12 Trays", "Everest"),
    ("Haldiram's Bhujia 400 gms X 24", "Haldiram"),
    ("Ching's Schezwan Chutney 250 gm", "Ching's"),
])
def test_brand_from_name(name, brand):
    assert B.brand_of(name, "X1")[0] == brand


def test_prefix_fallback_and_learning():
    p = pd.DataFrame({"product_id": ["MM1", "MM2", "MM3", "BS1"],
                      "name": ["MDH Garam Masala", "MDH Chana Masala", "Chunky Chat Masala 100 X 60", "Cumin Seeds 50 Lb"]})
    b = B.assign(p, overrides=None).set_index("product_id")
    assert b.loc["MM3", "brand"] == "MDH" and b.loc["MM3", "how_assigned"].startswith("item-code prefix (brand")
    assert b.loc["BS1", "brand"] == "BS (item code)"


def test_category_whole_words():
    assert ingest.categorize("India Gate Classic Basmati Rice 10 lb")[0] == "rice_flour"   # not "lassi"
    assert ingest.categorize("Everest Shahi Paneer Masala 100 gm")[0] == "spices"         # masala before paneer
    assert ingest.categorize("Amul Mango Lassi 1 L")[0] == "dairy"


def plan_rows():
    rows = []
    for pid, brand_name in (("A1", "Aara Cumin"), ("A2", "Aara Peanuts"), ("A3", "Aara Hold")):
        for m in ("2027-03", "2027-04", "2027-05", "2027-06"):
            ms = pd.Timestamp(m + "-01")
            rows.append(dict(product_id=pid, name=brand_name, month=m, cases_needed=10.4, kg_needed=104.0,
                             order_by_safe=f"{ms - pd.Timedelta(days=130):%Y-%m-%d}",
                             order_by_latest=f"{ms - pd.Timedelta(days=105):%Y-%m-%d}", reason="steady; forecast 10"))
    return pd.DataFrame(rows)


def test_grouping_holds_and_status():
    plan = plan_rows()
    bm = pd.DataFrame({"product_id": ["A1", "A2", "A3"], "brand": "Aara"})
    orders, lines = BO.build(plan, bm, "2026-11-14", cover_months=2, holds={"A3"})
    assert list(orders["covers"]) == ["Mar-Apr 2027", "May-Jun 2027"]
    first = orders.iloc[0]
    assert first["order_by_safe"] == "2026-10-22" and first["order_by_latest"] == "2026-11-16"
    assert first["status"] == "Order now"                           # safe passed, latest not
    assert first["cases"] == 2 * 21 and first["products"] == 2       # ceil(20.8) each, hold excluded
    assert first["products_on_hold"] == 1
    held = lines[(lines["order_id"] == first["order_id"]) & lines["on_hold"]]
    assert len(held) == 1 and "confirm release" in held["note"].iloc[0]
    assert orders.iloc[1]["status"] == "Coming up"


@pytest.mark.parametrize("safe,latest,expected", [
    ("2026-11-20", "2026-12-15", "Order now"),
    ("2026-11-01", "2026-11-20", "Order now"),
    ("2026-12-10", "2027-01-05", "Order this month"),
    ("2027-02-01", "2027-02-26", "Coming up"),
    ("2026-10-01", "2026-10-26", "Too late for full lead time: expedite"),
])
def test_status_rules(safe, latest, expected):
    assert BO.status("2026-11-14", safe, latest) == expected


def test_containers_note():
    plan = plan_rows()
    bm = pd.DataFrame({"product_id": ["A1", "A2", "A3"], "brand": "Aara"})
    info = pd.DataFrame({"brand": ["Aara"], "container_kg": [300.0]})
    orders, _ = BO.build(plan, bm, "2026-11-14", 2, holds=(), info=info)
    o = orders.iloc[0]                                      # 3 products x 21 cases x 10 kg = 630 kg
    assert o["containers"] == 2.1 and "consider also covering May 2027" in o["container_note"]


def test_stock_netting_earliest_first():
    plan = plan_rows()
    bm = pd.DataFrame({"product_id": ["A1", "A2", "A3"], "brand": "Aara"})
    stock = pd.DataFrame({"product_id": ["A1"], "on_hand_units": [25.0], "held_units": [100.0], "on_order_units": [5.0]})
    orders, lines = BO.build(plan, bm, "2026-11-14", 2, holds=(), stock=stock)
    a1 = lines[lines["product_id"] == "A1"].set_index("order_id")["cases"]
    assert "Aara|2027-03" not in a1.index                    # 30 available covers Mar-Apr need of 20.8
    assert a1["Aara|2027-05"] == 12                          # 20.8 - 9.2 left = 11.6 -> 12 (held never counts)
    assert orders["stock_netted"].all()
