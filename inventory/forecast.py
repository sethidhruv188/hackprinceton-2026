"""Demand forecasting: one LightGBM model across all products, plus the 'average' baseline.

Each product's demand is divided by its own historical mean, so one model can learn shared
patterns (Diwali, summer, December) across 3,000 products. Two quantile models give a
median (p50) and a high case (p90); the gap between them sizes the safety stock.
"""
import numpy as np
import pandas as pd
import holidays
import lightgbm as lgb

# Festivals whose date MOVES each year (lunar calendars), looked up in the `holidays` library.
# Fixed-date ones (Christmas in Dec, Thanksgiving in Nov) are covered by month flags below.
# Edit this list to the festivals the CEO says drive demand; each adds a this-month and a
# next-month flag (people stock up the month before).
FESTIVALS = {
    "diwali": "Diwali",
    "holi": "Holi",
    "dussehra": "Dussehra",
    "eid_fitr": "Id-ul-Fitr",
    "eid_adha": "Id-ul-Zuha",
}

FEATURES = (["lag1", "lag2", "lag3", "lag12", "roll3", "roll6", "month_num", "is_nov", "is_dec", "cat_code"]
            + [f"{k}_{w}" for k in FESTIVALS for w in ("m", "next")])


def festival_months(years, needle):
    """Month starts that contain a festival whose name includes `needle` (India calendar)."""
    ind = holidays.India(years=years)
    return {pd.Timestamp(d).to_period("M").to_timestamp() for d, n in ind.items() if needle in n}


def build_features(df, products):
    """df: product_id, month, demand (NaN allowed for future rows). Returns df with features + scale."""
    d = df.merge(products[["product_id", "category"]], on="product_id", how="left").sort_values(["product_id", "month"])
    d["cat_code"] = d["category"].astype("category").cat.codes
    g = d.groupby("product_id")["demand"]
    d["scale"] = g.transform(lambda s: s.shift(1).expanding().mean()).clip(lower=1.0)
    for k in (1, 2, 3, 12):
        d[f"lag{k}"] = g.shift(k) / d["scale"]
    d["roll3"] = g.transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean()) / d["scale"]
    d["roll6"] = g.transform(lambda s: s.shift(1).rolling(6, min_periods=1).mean()) / d["scale"]
    years = range(d["month"].min().year, d["month"].max().year + 2)
    d["month_num"] = d["month"].dt.month
    nxt = d["month"] + pd.DateOffset(months=1)
    for key, needle in FESTIVALS.items():
        fm = festival_months(years, needle)
        d[f"{key}_m"] = d["month"].isin(fm).astype(int)
        d[f"{key}_next"] = nxt.isin(fm).astype(int)
    d["is_nov"] = (d["month_num"] == 11).astype(int)
    d["is_dec"] = (d["month_num"] == 12).astype(int)
    return d


def train(clean, products):
    d = build_features(clean[["product_id", "month", "demand", "imputed"]], products)
    d = d[d["demand"].notna() & d["lag1"].notna()]
    y = d["demand"] / d["scale"]
    w = np.where(d["imputed"], 0.5, 1.0)  # trust filled-in months a bit less
    params = dict(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=10, verbose=-1)
    models = {}
    for q in (0.5, 0.9):
        m = lgb.LGBMRegressor(objective="quantile", alpha=q, **params)
        m.fit(d[FEATURES], y, sample_weight=w)
        models[q] = m
    return models


def forecast(clean, products, models, horizon=1):
    """Recursive forecast for the next `horizon` months after the data ends. Returns long df."""
    hist = clean[["product_id", "month", "demand"]].copy()
    last = hist["month"].max()
    out = []
    for h in range(1, horizon + 1):
        m = last + pd.DateOffset(months=h)
        fut = pd.DataFrame({"product_id": products["product_id"], "month": m, "demand": np.nan})
        d = build_features(pd.concat([hist, fut], ignore_index=True), products)
        row = d[d["month"] == m].copy()
        row["p50"] = np.clip(models[0.5].predict(row[FEATURES]) * row["scale"], 0, None)
        row["p90"] = np.maximum(np.clip(models[0.9].predict(row[FEATURES]) * row["scale"], 0, None), row["p50"])
        out.append(row[["product_id", "month", "p50", "p90"]])
        fut["demand"] = row.set_index("product_id").loc[fut["product_id"], "p50"].values
        hist = pd.concat([hist, fut], ignore_index=True)
    return pd.concat(out, ignore_index=True)


def average_baseline(panel, before, window=6):
    """What the business does today: average of raw sales over the last `window` months, zeros included."""
    h = panel[(panel["month"] < before) & (panel["month"] >= before - pd.DateOffset(months=window))]
    return h.groupby("product_id")["units_sold"].mean().rename("avg")
