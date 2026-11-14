import numpy as np
import pandas as pd
import pytest

from inventory import policy, simulate


def const_matrix(per_day=1.0, start="2026-01-01", n=12):
    starts = policy.month_starts(start, n)
    days = ((starts + pd.offsets.MonthBegin(1)) - starts).days.to_numpy()
    return np.array([per_day * days]), starts


def test_demand_prorated_by_days():
    F, starts = const_matrix(1.0)                 # exactly 1 case per day
    assert policy.demand_over(F, starts, "2026-01-01", 130)[0] == pytest.approx(130)
    assert policy.demand_over(F, starts, "2026-01-16", 31)[0] == pytest.approx(31)   # spans Jan/Feb


def test_levels_and_order_quantity():
    F, starts = const_matrix(1.0)
    lv = policy.levels(F, starts, "2026-01-01", cushion_months=2, cover_months=2)
    assert lv["reorder_point"][0] == pytest.approx(130 + 2 * 30.4)
    assert lv["order_up_to"][0] == pytest.approx(130 + 4 * 30.4)
    rop, out = lv["reorder_point"][0], lv["order_up_to"][0]
    assert policy.order_quantity(rop, rop, out) == 0                       # at the point: no order
    assert policy.order_quantity(rop - 0.5, rop, out) == np.ceil(out - rop + 0.5)
    assert float(policy.order_quantity(10.2, 100, 150.0)) == 140            # rounded up to whole cases


def test_forecast_too_short_raises():
    F, starts = const_matrix(1.0, n=3)
    with pytest.raises(ValueError):
        policy.demand_over(F, starts, "2026-01-01", 130)


def test_simulation_carries_stock_and_lands_orders_by_day():
    months = list(policy.month_starts("2026-01-01", 3))
    demand = np.array([31.0, 28.0, 31.0])          # 1 per day
    rop = np.array([0.0, 100.0, 0.0])               # only order at the start of February
    out = np.array([0.0, 140.0, 0.0])
    lead = np.array([0, 14, 0])                      # February order lands Feb 15
    r = simulate.run_product(demand, months, rop, out, init_stock=40, lead_days=lead)
    assert r["shipped"][0] == pytest.approx(31) and r["end_stock"][0] == pytest.approx(9)   # carryover
    assert r["ordered"][1] == 131                                                            # 140 - 9
    # Feb: 9 days covered by carried stock, days 10-14 short (5), order lands day 15
    assert r["short"][1] == pytest.approx(5)
    assert r["end_stock"][1] == pytest.approx(131 - 14)
    assert r["short"][2] == 0


def test_lead_time_draws_are_seeded_and_in_range():
    a = simulate.lead_time_draws(50, 12, seed=3)
    assert (a == simulate.lead_time_draws(50, 12, seed=3)).all()
    assert a.min() >= 105 and a.max() <= 130
    assert not (a == simulate.lead_time_draws(50, 12, seed=4)).all()


def test_stock_status_actions():
    months = policy.month_starts("2026-01-01", 24)
    fc = pd.DataFrame([(p, m, 30.0) for p in ("A", "B") for m in months], columns=["product_id", "month", "forecast"])
    stock = pd.DataFrame({"product_id": ["A", "B"], "on_hand_units": [10.0, 400.0], "held_units": [50.0, 0.0],
                          "on_order_units": [0.0, 0.0]})
    s = policy.stock_status(fc, stock, "2026-01-01").set_index("product_id")
    assert s.loc["A", "action"].startswith("order now:")                  # held units don't count
    assert s.loc["B", "action"].startswith("OK until")
