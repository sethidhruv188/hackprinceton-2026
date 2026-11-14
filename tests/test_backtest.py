import numpy as np
import pandas as pd

from inventory import backtest, gaps
from inventory import forecast as F


def test_decision_cutoff_is_m_minus_5():
    assert backtest.decision_cutoff("2025-09-01") - pd.DateOffset(months=1) == pd.Timestamp("2025-04-01")


def test_no_fold_sees_data_after_m_minus_5(panel_products, monkeypatch):
    panel, products = panel_products
    seen = []

    def fake_train(clean, products, powers=(1.3,), quantiles=(0.5, 0.9), h=1):
        assert h == 5
        seen.append((clean["month"].max(), clean.loc[clean["spike"], "month"].max() if clean["spike"].any() else None,
                     "target_cap" in clean))
        return {"p50": None, "p90": None, **{f"mean_{p}": None for p in powers}}

    def fake_forecast(clean, products, models, h, driver="mean_1.3"):
        f = pd.DataFrame({"product_id": products["product_id"], "month": clean["month"].max() + pd.DateOffset(months=h)})
        for k in models:
            f[k] = 1.0
        f["mean"] = 1.0
        return f

    monkeypatch.setattr(backtest, "train", fake_train)
    monkeypatch.setattr(backtest, "forecast", fake_forecast)
    tests = [pd.Timestamp("2025-09-01"), pd.Timestamp("2026-10-01")]
    variants = [dict(name="A", powers=[1.3], cap_one_off=False), dict(name="B", powers=[1.3], cap_one_off=True)]
    res, folds = backtest.run(panel, products, tests, variants, log=lambda *_: None)
    limits = [t - pd.DateOffset(months=5) for t in tests for _ in variants]
    assert [s[0] for s in seen] == limits
    assert all(s[1] is None or s[1] <= lim for s, lim in zip(seen, limits))   # spikes only from the fold's data
    assert [s[2] for s in seen] == [False, True, False, True]
    assert list(folds["train_through"]) == ["2025-04", "2026-05"]


def test_bias_math():
    m = backtest.metrics([10, 30], [20, 20])
    assert m["bias"] == 1.0 and m["WAPE_pct"] == 50.0 and m["units_short"] == 10 and m["units_over"] == 10
    assert backtest.metrics([10, 10], [5, 5])["bias"] == 0.5
    g = pd.DataFrame({"units_sold": [10.0, 30.0], "avg": [10.0, 10.0]})
    row = backtest.compare(g, {"avg": g["avg"]})
    assert row["avg_bias"] == 0.5 and row["actual"] == 40


def test_features_ignore_data_after_t_minus_h(panel_products):
    """Horizon-5 features for month T (incl. Diwali lift and spike rate) must not change when
    demand or spike flags after T-5 change."""
    panel, products = panel_products
    clean = gaps.clean_demand(panel)[["product_id", "month", "demand", "imputed"]]
    clean["spike"] = clean["month"].dt.month.isin([10]) & (clean["product_id"] == "P0")
    T = pd.Timestamp("2026-03-01")
    base = F.build_features(clean, products, h=5)
    changed = clean.copy()
    late = changed["month"] > T - pd.DateOffset(months=5)
    changed.loc[late, "demand"] *= 7
    changed.loc[late, "spike"] = True
    alt = F.build_features(changed, products, h=5)
    cols = ["lag1", "lag2", "lag3", "lag12", "roll3", "roll6", "trend", "scale"] + F.LIFT_FEATURES + F.SPIKE_FEATURES
    pd.testing.assert_frame_equal(base[base["month"] == T].set_index("product_id")[cols],
                                  alt[alt["month"] == T].set_index("product_id")[cols])


def test_spike_rate_counts_past_years():
    panel = pd.DataFrame({"product_id": "P", "month": pd.date_range("2024-07-01", "2026-10-01", freq="MS")})
    panel["demand"] = 10.0
    panel["spike"] = panel["month"].isin(pd.to_datetime(["2024-10-01"]))
    products = pd.DataFrame({"product_id": ["P"], "category": ["x"]})
    d = F.build_features(panel, products, h=5).set_index("month")
    assert d.loc["2025-10-01", "product_month_spike_rate"] == 1.0     # spiked in Oct 2024 (1 of 1 past year)
    assert d.loc["2026-10-01", "product_month_spike_rate"] == 0.5     # Oct 2024 yes, Oct 2025 no


def test_k_choice_meets_averaging_short():
    g = pd.DataFrame({"units_sold": [10.0, 20.0, 30.0], "avg": [20.0, 20.0, 20.0],
                      "m": [8.0, 15.0, 25.0], "p": [12.0, 25.0, 40.0]})
    k = backtest.choose_k(g, "m", "p")
    s = backtest.stock(g["m"], g["p"], k)
    assert backtest.metrics(g["units_sold"], s)["units_short"] <= backtest.metrics(g["units_sold"], g["avg"])["units_short"]


def test_training_rows_clip_and_history():
    """Mean-model target is clipped at Y_CLIP; rows need 3 known months behind the scale."""
    months = pd.date_range("2024-07-01", "2025-12-01", freq="MS")
    clean = pd.DataFrame({"product_id": "P", "month": months, "demand": 1.0, "imputed": False})
    clean.loc[clean["month"] == "2025-06-01", "demand"] = 500.0          # ratio ~500 to its scale
    products = pd.DataFrame({"product_id": ["P"], "category": ["x"]})
    X, y, y_mean, w = F.training_rows(clean, products, h=1)
    assert y.max() > F.Y_CLIP and y_mean.max() == F.Y_CLIP
    assert X["lag3"].notna().all() and len(X) == len(months) - 3
