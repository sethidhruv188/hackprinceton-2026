"""Replay recent months: what would averaging have done vs. our model?

For each test month we train only on earlier months (no peeking), forecast that month, and
compare with what actually sold. Months where the product could not be sold (holds,
stockouts, suspected gaps) are left out of the scoring, since their sales are not demand.

Business view (single-month simulation): if you order exactly the forecast, how many units
are left over (waste risk) and how many sales are missed (stockouts)? Valued at unit cost.
"""
import numpy as np
import pandas as pd

from .gaps import clean_demand, detect_unflagged_gaps
from .forecast import train, forecast, average_baseline


def run(panel, products, test_months=6):
    months = sorted(panel["month"].unique())[-test_months:]
    suspected_all = detect_unflagged_gaps(panel)
    scored = clean_demand(panel, suspected_all)[["product_id", "month", "unavailable"]]
    rows = []
    for t in months:
        hist = panel[panel["month"] < t]
        clean = clean_demand(hist, detect_unflagged_gaps(hist))
        models = train(clean, products)
        f = forecast(clean, products, models, horizon=1)
        avg = average_baseline(panel, t)
        actual = panel[panel["month"] == t][["product_id", "units_sold"]]
        r = f.merge(actual, on="product_id").merge(avg, on="product_id", how="left")
        r["month"] = t
        rows.append(r)
    res = pd.concat(rows, ignore_index=True)
    res = res.merge(scored, on=["product_id", "month"]).merge(
        products[["product_id", "name", "category", "unit_cost"]], on="product_id")
    res = res[~res["unavailable"]].drop(columns="unavailable")
    for who, col in (("model", "p50"), ("avg", "avg")):
        res[f"{who}_err"] = (res[col] - res["units_sold"]).abs()
        res[f"{who}_over"] = (res[col] - res["units_sold"]).clip(lower=0)
        res[f"{who}_short"] = (res["units_sold"] - res[col]).clip(lower=0)
    return res


def summarize(res):
    def block(g):
        a = g["units_sold"].sum()
        return pd.Series({
            "actual_units": a,
            "avg_WAPE_%": 100 * g["avg_err"].sum() / a,
            "model_WAPE_%": 100 * g["model_err"].sum() / a,
            "avg_overstock_$": (g["avg_over"] * g["unit_cost"]).sum(),
            "model_overstock_$": (g["model_over"] * g["unit_cost"]).sum(),
            "avg_missed_sales_$": (g["avg_short"] * g["unit_cost"]).sum(),
            "model_missed_sales_$": (g["model_short"] * g["unit_cost"]).sum(),
        })
    overall = block(res).to_frame("ALL").T
    by_cat = res.groupby("category")[res.columns].apply(block)
    return pd.concat([overall, by_cat]).round(1)
