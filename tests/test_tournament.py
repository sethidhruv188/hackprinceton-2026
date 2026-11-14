import numpy as np
import pandas as pd
import pytest

from inventory import large_orders as LO, tournament as T


def lines_frame():
    rows = []
    for m in pd.date_range("2025-01-01", "2026-06-01", freq="MS"):
        for d in (3, 10, 17):
            rows.append(("P", m + pd.Timedelta(days=d), 5.0))
    rows.append(("P", pd.Timestamp("2025-06-20"), 100.0))      # large: > 3 x median (5) and > 20
    rows.append(("P", pd.Timestamp("2025-07-20"), 18.0))       # > 3 x median but not > 20 cases
    rows.append(("P", pd.Timestamp("2026-05-20"), 500.0))      # after the cutoff used below
    l = pd.DataFrame(rows, columns=["product_id", "date", "cases"])
    l["month"] = l["date"].dt.to_period("M").dt.to_timestamp()
    return l


def test_large_flags_use_only_earlier_lines():
    l = lines_frame()
    h = LO.flag(l, "2026-01-01")
    assert h["date"].max() < pd.Timestamp("2026-01-01")
    assert h.loc[h["cases"] == 100, "large"].all() and not h.loc[h["cases"] == 18, "large"].any()
    assert (h["median_line"] == 5.0).all()


def test_regular_panel_and_typical_large():
    l = lines_frame()
    months = pd.date_range("2025-01-01", "2026-06-01", freq="MS")
    tot = l.groupby("month")["cases"].sum()
    panel = pd.DataFrame({"product_id": "P", "month": months, "units_sold": tot.reindex(months).to_numpy(), "status": "sellable"})
    p, typ = LO.regular_panel(panel, l, "2026-01-01")
    jun = p.set_index("month").loc["2025-06-01", "units_sold"]
    assert jun == pytest.approx(15.0)                          # 115 sold, 100 of it one large order
    assert p["month"].max() < pd.Timestamp("2026-01-01")
    assert typ["P"] == pytest.approx(100 / 12)                 # one 100-case large order in the last 12 months


def test_seasonal_never_reads_past_cutoff():
    months = pd.date_range("2024-07-01", "2026-10-01", freq="MS")
    panel = pd.DataFrame({"product_id": "S", "month": months, "units_sold": [float(m.month) for m in months], "status": "sellable"})
    panel.loc[panel["month"] >= "2025-12-01", "units_sold"] = 1000.0                   # data after the cutoff
    s = T.seasonal_paths(panel, [pd.Timestamp("2026-04-01")], 10, lead_gap=4)          # data through 2025-11
    assert s["seasonal"].max() < 100                                                   # nothing from Dec 2025 on
    jan = s.set_index("month").loc["2027-01-01", "seasonal"]
    assert jan == pytest.approx(1.0 * 1.0)                                             # Jan 2025 x ratio 1 (same pattern)


def test_equal_stock_interpolation():
    t = pd.DataFrame({"method": ["avg"] * 4 + ["m"] * 4, "cushion": [1, 2, 3, 4] * 2,
                      "fill": [60, 65, 70, 74, 62, 68, 72, 75], "stock": [100, 200, 300, 400, 150, 250, 350, 450]})
    e, S = T.at_equal_stock(t)
    assert S == 300 and e["avg"] == pytest.approx(70) and e["m"] == pytest.approx(70)   # m: halfway 68 -> 72
    t2 = t.assign(stock=np.where(t["method"] == "m", t["stock"] + 1000, t["stock"]))
    assert np.isnan(T.at_equal_stock(t2)[0]["m"])                                        # no fair point


def test_read_choice(tmp_path):
    f = tmp_path / "forecaster.txt"
    f.write_text("blend_0.5\nweight=0.5\nbest_ml=ml_regular\n# comment\n")
    assert T.read_choice(f) == dict(name="blend_0.5", weight=0.5, best_ml="ml_regular")
    assert T.read_choice(tmp_path / "missing.txt")["name"] == "ml"


def test_extend_flat_repeats_last_month_for_every_candidate():
    p = pd.DataFrame({"decision": pd.Timestamp("2026-01-01"), "product_id": "P",
                      "month": pd.date_range("2026-01-01", periods=3, freq="MS"), "a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
    e = T.extend_flat(p, 2).sort_values("month")
    assert len(e) == 5 and list(e["a"].iloc[-2:]) == [3.0, 3.0] and list(e["b"].iloc[-2:]) == [6.0, 6.0]


def test_gap_vs_avg_compares_at_the_same_stock():
    t = pd.DataFrame({"method": ["avg"] * 4 + ["hi"] * 4, "cushion": [1, 2, 3, 4] * 2,
                      "fill": [60, 65, 70, 74, 69, 73, 76, 78], "stock": [100, 200, 300, 400, 300, 400, 500, 600]})
    g = T.gap_vs_avg(t)
    assert g["avg"] == pytest.approx(0)
    assert g["hi"] == pytest.approx(((69 - 70) + (73 - 74)) / 2)     # only points at stock 300 and 400 overlap
