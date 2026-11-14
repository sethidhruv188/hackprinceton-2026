import numpy as np
import pandas as pd

from inventory import gaps, plan


def test_order_plan_columns_and_rules(panel_products):
    panel, products = panel_products
    # P2: occasional (sells in < 60% of months); P3: new (listed 3 months); P1: inactive for 6 months
    panel.loc[(panel["product_id"] == "P2") & (panel["month"].dt.month % 2 == 0), "units_sold"] = 0
    panel.loc[(panel["product_id"] == "P2") & (panel["month"].dt.month == 3), "units_sold"] = 0
    p3 = panel["product_id"] == "P3"
    panel.loc[p3 & (panel["month"] < "2026-08-01"), ["units_sold", "status"]] = [0.0, "not_listed"]
    panel.loc[(panel["product_id"] == "P1") & (panel["month"] >= "2026-05-01"), "units_sold"] = 0
    clean = gaps.clean_demand(panel)
    months = pd.date_range("2026-11-01", periods=6, freq="MS")
    fc = pd.DataFrame([(p, m) for p in products["product_id"] for m in months], columns=["product_id", "month"])
    fc["mean"], fc["p90"] = 10.0, 20.0
    op, inactive = plan.order_plan(panel, clean, products, fc, "2026-11-14", k_festival=1.0, k_normal=0.5)
    assert list(op.columns) == plan.ORDER_PLAN_COLUMNS
    assert "P1" not in set(op["product_id"]) and "P1" in set(inactive["product_id"])
    occ = op[op["product_id"] == "P2"]
    assert (occ["cases_needed"] == 10.0).all() and occ["reason"].str.startswith("occasional bulk orders").all()
    new = op[op["product_id"] == "P3"]
    assert new["reason"].str.startswith("new product").all()
    reg = op[op["product_id"] == "P0"].set_index("month")
    assert reg.loc["2026-11", "cases_needed"] == 20.0         # Diwali month: k_festival 1.0 -> 10 + 1 * 10
    assert reg.loc["2027-01", "cases_needed"] == 15.0         # other month: k_normal 0.5 -> 10 + 0.5 * 10
    assert reg["reason"].str.contains("forecast 10 + buffer", regex=False).all()
    assert list(op["order_by_safe"]) == sorted(op["order_by_safe"])
    apr = reg.loc["2027-04"]
    assert apr["order_by_safe"] == "2026-11-22"      # 130 days before 2027-04-01
    assert apr["order_by_latest"] == "2026-12-17"
