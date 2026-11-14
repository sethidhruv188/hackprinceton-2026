import numpy as np
import pandas as pd
import pytest
from openpyxl import Workbook

from inventory import policy, stock_ingest as SI, stock_plan as SP


def fake_inventory(path):
    wb = Workbook()
    ws = wb.active
    rows = [["EXAMPLE TRADING LLC"], ["Parent Co : Example Trading"], ["Product list Inventory"], [], [],
            ["Options: Show Zeros"],
            ["Item", "Description", "Current Quantity Available", "Example - Warehouse ( NJ )", "Total"],
            ["\xa0", None, None, "Current Quantity On Hand", "Current Quantity On Hand"],
            ["Assembly"],
            ["AS1", "Aara Cumin 200 Gm X 20 Packets", 400, 400, 400],          # 400 packs = 20 cases
            ["Inventory Item"],
            ["MM1 - OLD", "MDH Masala 100 X 60", 120, 120, 120],               # OLD code -> MM1, 60 packs -> 10 per case
            ["BS1 - OLD", "Cumin Seeds 50 Lb X", 500, 500, 500],               # pounds -> 10 bags
            ["AS1", "Aara Cumin 200 Gm X 20 Packets", 100, 100, 100],          # duplicate row, summed
            ["ZZ9", "Packaging film", 50, 50, 50]]                              # matches nothing
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_inventory_loading(tmp_path):
    f = tmp_path / "inv.xlsx"
    fake_inventory(f)
    inv, found = SI.read_inventory(f)
    assert set(found) == {"on_hand", "available"}                       # no on order / held / expiry / cbm
    assert inv.set_index("raw_item").loc["AS1", "on_hand"] == 500 and inv.set_index("raw_item").loc["AS1", "rows"] == 2
    item_map = pd.DataFrame({"raw_item": ["AS1", "MM1 - OLD", "MM1", "BS1 - OLD", "BS1"],
                             "product_id": ["AS1", "MM1", "MM1", "BS1", "BS1"],
                             "qty_to_cases": [1, 1, 1, 1 / 50, 1], "to_current_cases": [1, 6.0, 1, 1, 1],
                             "qty_recorded_in": ["cases", "cases", "cases", "lb (detected)", "cases"],
                             "packs_per_case": [20, 60, 10, 1, 1], "description": ["", "", "", "", ""]})
    products = pd.DataFrame({"product_id": ["AS1", "MM1", "BS1"]})
    stock, detail = SI.map_to_products(inv, item_map, products)
    s = stock.set_index("product_id")["on_hand_cases"]
    assert s["AS1"] == pytest.approx(25)          # 500 packs / 20
    assert s["MM1"] == pytest.approx(12)          # 120 packs / 60 = 2 old cases x 6 = 12 current cases
    assert s["BS1"] == pytest.approx(10)          # 500 lb / 50 lb bags
    assert stock["on_order_cases"].isna().all() and stock["held_cases"].isna().all()   # missing stays empty
    assert set(detail.loc[detail["match"] == "unmatched", "raw_item"]) == {"ZZ9"}
    stock2, _ = SI.map_to_products(inv, item_map, products, prefix_unit={"AS": "cases"})
    assert stock2.set_index("product_id").loc["AS1", "on_hand_cases"] == pytest.approx(500)


def test_pack_pull_forward_and_never_cut():
    cfg = dict(cbm=80.0, usable=1.0, max_kg=None)
    lines = pd.DataFrame({"product_id": ["A"], "cases": [100], "kg": [1000.0], "cbm": [90.0]})     # 1.125 containers
    cand = pd.DataFrame({"product_id": ["B", "C"], "cases": [100, 1000], "kg_per_case": [1.0, 1.0],
                         "cbm_per_case": [0.5, 0.5], "needed_by": ["2027-01-01", "2027-02-01"]})
    s, pulled = SP.pack(lines, cand, cfg)
    assert s["cbm"] <= 160 + 1e-9 and s["containers_needed"] == 2 and s["last_container_fill_pct"] >= 99
    assert list(pulled["product_id"]) == ["B", "C"] and pulled["cases"].iloc[0] == 100          # earliest first
    assert pulled["cases"].sum() == 140                                                          # 70 CBM / 0.5
    full = pd.DataFrame({"product_id": ["A"], "cases": [100], "kg": [1000.0], "cbm": [150.0]})   # 1.875: no pull
    s2, p2 = SP.pack(full, cand, cfg)
    assert p2.empty and s2["cbm"] == 150


def test_pack_weight_bound():
    cfg = dict(cbm=80.0, usable=1.0, max_kg=20000.0)
    heavy = pd.DataFrame({"product_id": ["R"], "cases": [2000], "kg": [45000.0], "cbm": [60.0]})
    s, _ = SP.pack(heavy, pd.DataFrame(columns=["product_id", "cases", "kg_per_case", "cbm_per_case", "needed_by"]), cfg)
    assert s["bound"] == "weight" and s["containers"] == pytest.approx(2.25) and s["containers_needed"] == 3


def monthly(per_month=30.0, start="2026-11", n=12, pids=("R",)):
    months = pd.date_range(start + "-01", periods=n, freq="MS")
    return pd.DataFrame([(p, f"{m:%Y-%m}", per_month) for p in pids for m in months], columns=["product_id", "month", "likely"])


def test_harvest_window_extension_and_projection():
    fc = monthly(30.0)
    F, starts, pids, extended = SP.forecast_matrix(fc, 24)
    assert pd.Timestamp("2027-11-01") in extended and pd.Timestamp("2027-10-01") not in extended
    rule = dict(rule_id="rice", order_months="Jan", arrival_months="Apr-May", cover_months="12")
    start, end, arrival = SP.harvest_window(rule, "2026-11-14")
    assert (start, end, arrival) == (pd.Timestamp("2027-01-01"), pd.Timestamp("2027-01-31"), pd.Timestamp("2027-04-01"))
    h = SP.harvest_orders(F, starts, pids, extended, {"R": 200.0}, {"R": rule}, "2026-11-14").iloc[0]
    until_arrival = policy.demand_over(F, starts, "2026-11-14", (pd.Timestamp("2027-04-01") - pd.Timestamp("2026-11-14")).days)[0]
    assert h["projected_stock_at_arrival"] == pytest.approx(200 - until_arrival, abs=0.1)
    need = policy.demand_over(F, starts, "2027-04-01",
                              (pd.Timestamp("2028-04-01") - pd.Timestamp("2027-04-01")).days + policy.CUSHION_MONTHS * 30.4)[0]
    assert h["order_cases"] == int(np.ceil(need - (200 - until_arrival)))
    assert "Nov 2027" in h["extended_months"] and h["covers_until"] == "2028-04-01"
    rich = SP.harvest_orders(F, starts, pids, extended, {"R": 10000.0}, {"R": rule}, "2026-11-14").iloc[0]
    assert rich["order_cases"] == 0                                        # floored at 0


def test_status_with_stock():
    fc = monthly(30.4, pids=("LOW", "HIGH", "HOLD"))
    F, starts, pids, _ = SP.forecast_matrix(fc, 24)
    stock = pd.DataFrame({"product_id": ["LOW", "HIGH", "HOLD"], "on_hand_cases": [50.0, 400.0, 0.0],
                          "on_order_cases": [np.nan, 0.0, np.nan], "held_cases": [np.nan, 100.0, np.nan]})
    st = SP.product_status(F, starts, pids, stock, holds={"HOLD"}, harvest_ids=set(), today="2026-11-14").set_index("product_id")
    assert "order now:" in st.loc["LOW", "status"] and st.loc["LOW", "order_now_cases"] > 0     # (also below the floor)
    assert st.loc["HIGH", "position"] == 300.0                             # held units never count
    assert st.loc["HIGH", "status"].startswith("OK until")
    ok = pd.Timestamp(st.loc["HIGH", "ok_until"])
    assert pd.Timestamp("2026-11-14") < ok < pd.Timestamp("2027-06-01")
    assert st.loc["HOLD", "status"] == "on hold: confirm release" and st.loc["HOLD", "order_now_cases"] == 0


def test_codes_sold_by_the_pack_are_detected():
    from inventory import ingest
    rows = [("XX1 - OLD", "Masala 100 X 60", q) for q in (60, 120, 60, 180, 20, 60, 120)]
    rows += [("XX1", "Masala 100 GM X 10", q) for q in (2, 3, 1, 4, 2)]
    rows += [("YY1", "Spice 100 GM X 24", q) for q in (24, 48, 2, 3, 1)]          # median 3 cases: not packs
    d = pd.DataFrame(rows, columns=["raw_item", "description", "qty"])
    d["date"] = pd.date_range("2025-01-01", periods=len(d), freq="D")
    im = ingest.build_item_map(d).set_index("raw_item")
    assert im.loc["XX1 - OLD", "qty_recorded_in"] == "packs (detected)"
    assert im.loc["XX1", "qty_recorded_in"] == "cases" and im.loc["YY1", "qty_recorded_in"] == "cases"
    # 60 single 100 g packs = 6 kg = 6 current cases of 10 x 100 g
    assert 60 * im.loc["XX1 - OLD", "qty_to_cases"] * im.loc["XX1 - OLD", "to_current_cases"] == pytest.approx(6)



def test_default_cushion_is_three_months():
    assert policy.CUSHION_MONTHS == 3.0
    F = np.array([[31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31.0]])               # 1 case a day in 2027
    starts = policy.month_starts("2027-01-01", 12)
    lv = policy.levels(F, starts, "2027-01-01")
    assert lv["cushion_demand"][0] == pytest.approx(3 * 30.4)
    assert lv["order_up_to"][0] - lv["reorder_point"][0] == pytest.approx(2 * 30.4)   # cover stays 2 months


def test_floor_uses_forward_forecast_and_flags_dip_before_restock():
    months = pd.date_range("2026-11-01", periods=24, freq="MS")
    rate = {m: (300.0 if m.month in (9, 10) else 30.0) for m in months}               # Diwali-season months sell 10x
    fc = pd.DataFrame([(p, f"{m:%Y-%m}", rate[m]) for p in ("SAFE", "DIP", "LOW") for m in months],
                      columns=["product_id", "month", "likely"])
    F, starts, pids, _ = SP.forecast_matrix(fc, 24)
    stock = pd.DataFrame({"product_id": ["SAFE", "DIP", "LOW"], "on_hand_cases": [1000.0, 95.0, 50.0],
                          "on_order_cases": [np.nan] * 3, "held_cases": [np.nan] * 3})
    fl = SP.floor_check(F, starts, pids, stock, "2026-11-14")
    i = {p: k for k, p in enumerate(pids)}
    # the floor is the forecast of the NEXT 3 months, so it rises ahead of Diwali-season months
    assert SP.policy.demand_over(F, starts, "2027-08-01", 3 * 30.4)[0] > 3 * 30 * 3
    assert fl["dip"][i["SAFE"]] is None
    assert not fl["below_now"][i["DIP"]] and fl["dip"][i["DIP"]] is not None            # dips before restock
    assert fl["below_now"][i["LOW"]] and fl["dip"][i["LOW"]] == pd.Timestamp("2026-11-14")
    st = SP.product_status(F, starts, pids, stock, holds=set(), harvest_ids=set(), today="2026-11-14").set_index("product_id")
    assert st.loc["LOW", "status"].startswith("below 3-month floor before restock: expedite or air-ship")
    assert pd.isna(st.loc["SAFE", "floor_dip_date"]) and pd.notna(st.loc["DIP", "floor_dip_date"])
    proj = SP.projection(F, starts, pids, st.reset_index(), "2026-11-14", stock=stock)
    assert "floor_3_months" in proj and (proj["floor_3_months"] > 0).all()



def test_owner_rule_runs_out_before_arrival():
    """99 on hand, 200 a month, runs out before the order lands: order = 3 months after arrival = 600;
    shipping-time sales are not added, and the run-out is a separate note."""
    months = pd.date_range("2026-10-01", periods=12, freq="MS")
    F, starts = np.array([[200.0] * 12]), months
    when = pd.Timestamp("2027-03-01") - pd.Timedelta(days=policy.LEAD_DAYS)           # arrives Mar 1, 2027
    oq = SP.owner_quantity(F, starts, [99.0], when)
    assert oq["arrival"] == pd.Timestamp("2027-03-01")
    assert oq["stock_at_arrival"][0] == 0.0 and oq["qty"][0] == 600
    assert oq["runs_out"][0] is not None and oq["runs_out"][0] < oq["arrival"]
    assert oq["next_order_by"] == pd.Timestamp("2027-06-01") - pd.Timedelta(days=policy.LEAD_DAYS)
    rich = SP.owner_quantity(F, starts, [5000.0], when)                                  # plenty left at arrival
    assert rich["qty"][0] == 0
