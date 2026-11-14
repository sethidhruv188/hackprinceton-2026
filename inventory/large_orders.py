"""Large (bulk) orders, separated from regular demand.

A line is large when it is more than LARGE_X times the product's median line size AND more than
LARGE_MIN cases. The median comes only from lines before the decision date, so a backtest fold
never uses later data.

  regular demand  monthly sales without the large lines (what the model learns)
  typical large   average large-order cases per month over the last 12 months available before the
                  decision; the ordering rule adds it back so stock still covers bulk orders
"""
import numpy as np
import pandas as pd

LARGE_X = 3.0
LARGE_MIN = 20.0


def load_lines(path="data/order_lines.csv"):
    l = pd.read_csv(path, dtype={"product_id": str}, parse_dates=["date"])
    l["month"] = l["date"].dt.to_period("M").dt.to_timestamp()
    return l


def flag(lines, cutoff):
    """Lines before `cutoff` with a boolean `large`, judged by medians from those lines only."""
    h = lines[lines["date"] < pd.Timestamp(cutoff)].copy()
    med = h[h["cases"] > 0].groupby("product_id")["cases"].median()
    h["median_line"] = h["product_id"].map(med)
    h["large"] = (h["cases"] > LARGE_X * h["median_line"]) & (h["cases"] > LARGE_MIN)
    return h


def regular_panel(panel, lines, cutoff):
    """Panel rows before `cutoff` with units_sold reduced by that month's large lines, plus each
    product's typical large-order cases per month (last up-to-12 months before the cutoff)."""
    cutoff = pd.Timestamp(cutoff)
    h = flag(lines, cutoff)
    big = h[h["large"]].groupby(["product_id", "month"])["cases"].sum()
    p = panel[panel["month"] < cutoff].copy()
    sub = pd.Series(list(zip(p["product_id"], p["month"]))).map(big).fillna(0.0).to_numpy()
    p["units_sold"] = np.maximum(0.0, p["units_sold"].to_numpy() - sub)
    start = max(cutoff - pd.DateOffset(months=12), panel["month"].min())
    n = len(pd.date_range(start, cutoff - pd.DateOffset(months=1), freq="MS"))
    recent = h[h["large"] & (h["month"] >= start)].groupby("product_id")["cases"].sum() / max(n, 1)
    return p, recent.rename("typical_large_per_month")


def report(lines, products, brand_map=None, months=12):
    """outputs/large_orders.csv: large lines of the last `months` months (judged with all data up to
    the end), with each product's typical large-order cases per month."""
    end = lines["date"].max() + pd.Timedelta(days=1)
    h = flag(lines, end)
    start = h["month"].max() - pd.DateOffset(months=months - 1)
    big = h[h["large"] & (h["month"] >= start)].copy()
    typ = big.groupby("product_id")["cases"].sum() / months
    big["typical_large_per_month"] = big["product_id"].map(typ).round(1)
    big["times_median_line"] = (big["cases"] / big["median_line"]).round(1)
    big = big.merge(products[["product_id", "name"]], on="product_id", how="left")
    if brand_map is not None:
        big = big.merge(brand_map[["product_id", "brand"]], on="product_id", how="left")
    big["date"] = big["date"].dt.strftime("%Y-%m-%d")
    big["month"] = big["month"].dt.strftime("%Y-%m")
    cols = ["product_id", "name"] + (["brand"] if brand_map is not None else []) + [
        "date", "month", "cases", "median_line", "times_median_line", "typical_large_per_month"]
    return big[cols].sort_values(["cases"], ascending=False).reset_index(drop=True)
