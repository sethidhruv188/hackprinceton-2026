"""Demand forecasting: one LightGBM model family across all products, plus the 'average' baseline.

The model learns each product's own behaviour from its sales (lags, rolling means, trend, how
often it has spiked in this calendar month, how it behaved around past Diwalis). Diwali is the
only festival used as a feature; every other festival in config/festivals.csv is tier 0 and is
used only to suggest a possible reason for a spike (inventory/spikes.py).

Each product's demand is divided by its own historical mean (`scale`) so one model can learn
shared shapes across ~3,000 products. Models per horizon h:

  mean_<power>  Tweedie objective: the central forecast the plan stocks to
  p50           median, kept for comparison
  p90           quantile 0.9: sizes the buffer, stock = mean + k * max(p90 - mean, 0)

Forecasts are DIRECT: the horizon-h model predicts month T from data through T-h only. Nothing is
fed back (feeding forecasts back compounded festival uplifts across months and blew up).

Features for month T (all built from data through T-h, except the calendar):
  lag1..lag3       the three most recent known months (T-h, T-h-1, T-h-2) / scale
  lag12            same month last year / scale (known for h <= 12)
  roll3, roll6     mean of the last 3 / 6 known months / scale
  trend            mean of last 3 known months / mean of last 12 known months
  month_num, is_nov, is_dec, cat_code
  fest_diwali_this / fest_diwali_next   Diwali window touches month T / month T+1
  days_to_diwali   days from the 15th of month T to the next Diwali main date; when that is more
                   than 180 days away, minus the days since the last one; capped at +-180
  diwali_lift_this / diwali_lift_next   the product's past sales in Diwali / pre-Diwali months
                   relative to its normal month (category average when it has no history)
  product_month_spike_rate  share of past years (T-12, T-24) in which this product spiked in
                   this calendar month; spikes come from the history passed in (a fold's own
                   training data in the backtest), never from later data
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

FESTIVALS_CSV = Path(__file__).resolve().parent.parent / "config" / "festivals.csv"
LIFT_CLIP = 10.0      # cap one occurrence's lift so a single bulk order can't dominate
Y_CLIP = 10.0         # mean model: training ratio demand/scale clipped here (99th percentile ~13.5)
MIN_KNOWN_MONTHS = 3  # training rows need this many known months behind the scale
DAYS_CAP = 180


def load_calendar(path=FESTIVALS_CSV):
    """Read the festival file. Tier-1 groups become model features (only Diwali today); tier-0 rows
    are kept as reference windows for spike attribution. Change the `tier` column to change this."""
    f = pd.read_csv(path, parse_dates=["main_date", "start_date", "end_date"])
    tier1 = sorted(f.loc[f["tier"] == 1, "group"].unique())
    groups = {g: set() for g in tier1}
    first_month = {}
    for r in f[f["tier"] == 1].itertuples():
        months = pd.period_range(r.start_date, r.end_date, freq="M").to_timestamp()
        groups[r.group].update(months)
        first_month[(r.group, r.start_date.year)] = months[0]
    projected = f[f["status"] != "confirmed"][["festival", "main_date", "status", "tier"]]
    return dict(table=f, tier1=tier1, groups=groups, first_month=first_month, end=f["end_date"].max(),
                diwali_main=sorted(f.loc[f["group"] == "diwali", "main_date"]),
                tier0=f[f["tier"] == 0][["festival", "group", "start_date", "end_date"]].reset_index(drop=True),
                projected=projected, sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())


CAL = load_calendar()
TIER1 = CAL["tier1"]                       # ["diwali"]
FEST_FEATURES = [f"fest_{g}_{w}" for g in TIER1 for w in ("this", "next")] + ["days_to_diwali"]
BASE_FEATURES = ["lag1", "lag2", "lag3", "lag12", "roll3", "roll6", "trend", "month_num", "is_nov", "is_dec", "cat_code"]
LIFT_FEATURES = ["diwali_lift_this", "diwali_lift_next"]
SPIKE_FEATURES = ["product_month_spike_rate"]
FEATURES = BASE_FEATURES + FEST_FEATURES + LIFT_FEATURES + SPIKE_FEATURES


def calendar_note():
    p = CAL["projected"]
    used = p[p["tier"] == 1]
    return (f"festival calendar {FESTIVALS_CSV.name}: model uses tier 1 = {', '.join(TIER1)}; "
            f"{len(CAL['tier0'])} tier-0 rows kept for spike attribution only; covers through {CAL['end']:%Y-%m-%d}; "
            f"non-confirmed tier-1 rows in use: "
            + (", ".join(f"{r.festival} {r.main_date:%Y-%m-%d} ({r.status})" for r in used.itertuples()) or "none"))


def days_to_diwali(month):
    """Days from the 15th of `month` to the next Diwali main date; if that is more than 180 days
    away, minus the days since the last one; capped at +-180."""
    mid = pd.Timestamp(month).replace(day=15)
    nxt = [d for d in CAL["diwali_main"] if d >= mid]
    prev = [d for d in CAL["diwali_main"] if d < mid]
    if nxt and (nxt[0] - mid).days <= DAYS_CAP:
        return (nxt[0] - mid).days
    if prev:
        return -min((mid - prev[-1]).days, DAYS_CAP)
    if nxt:
        return DAYS_CAP
    raise ValueError(f"no Diwali date near {month:%Y-%m} in {FESTIVALS_CSV.name}")


def festival_flags(months):
    """Festival feature table for a list of month starts (raises past the calendar's end)."""
    months = pd.DatetimeIndex(pd.to_datetime(months))
    if len(months) and months.max() + pd.DateOffset(months=1) > CAL["end"]:
        raise ValueError(f"month {months.max():%Y-%m} needs festival dates through "
                         f"{months.max() + pd.DateOffset(months=1):%Y-%m}, but {FESTIVALS_CSV.name} ends "
                         f"{CAL['end']:%Y-%m-%d}. Add the next year's festival dates.")
    nxt = months + pd.DateOffset(months=1)
    t = pd.DataFrame(index=months)
    for g in TIER1:
        t[f"fest_{g}_this"] = months.isin(CAL["groups"][g]).astype(int)
        t[f"fest_{g}_next"] = nxt.isin(CAL["groups"][g]).astype(int)
    t["days_to_diwali"] = [days_to_diwali(m) for m in months]
    return t


def month_kind(month):
    """'Diwali month' (Diwali window touches it), 'pre-Diwali month' (touches next month) or 'other'."""
    f = festival_flags([month]).iloc[0]
    if f["fest_diwali_this"]:
        return "Diwali month"
    if f["fest_diwali_next"]:
        return "pre-Diwali month"
    return "other"


def _lift_asof(d, h):
    """diwali_lift_this / _next for each row month T from months <= T - h only. d: sorted by product,
    month, full grid, with demand, category, fest_diwali_* flags and `forecasted`."""
    actual = d["demand"].where(~d["forecasted"])
    pid = d["product_id"]
    nf = actual.where((d["fest_diwali_this"] == 0) & (d["fest_diwali_next"] == 0))
    normal = nf.groupby(pid).transform(lambda s: s.expanding().median().shift(1))
    ratio = (actual / normal.where(normal > 0)).clip(upper=LIFT_CLIP)
    out = {}
    for w in ("this", "next"):
        flag = d[f"fest_diwali_{w}"] == 1
        occ = (flag & ratio.notna()).astype(float)
        num = (ratio * occ).fillna(0.0)
        cnum, ccnt = num.groupby(pid).cumsum(), occ.groupby(pid).cumsum()
        prod = (cnum / ccnt.where(ccnt > 0)).groupby(pid).shift(h)
        tmp = pd.DataFrame({"category": d["category"], "month": d["month"], "num": num, "cnt": occ})
        cm = tmp.groupby(["category", "month"])[["num", "cnt"]].sum().sort_index().groupby(level="category").cumsum()
        cat_asof = (cm["num"] / cm["cnt"].where(cm["cnt"] > 0)).rename("cat").reset_index()
        cat_asof["month"] = cat_asof["month"] + pd.DateOffset(months=h)     # known h months later
        cat = tmp[["category", "month"]].merge(cat_asof, on=["category", "month"], how="left")["cat"].values
        lift = prod.where(prod.notna(), cat)
        out[f"diwali_lift_{w}"] = lift.where(flag).fillna(1.0)   # only meaningful in Diwali-season months
    return d.assign(**out)


def build_features(df, products, h=1):
    """Direct features for each row's month T from data through T - h (see module docstring).
    df: product_id, month, demand (NaN = unavailable or future), optional `forecasted` and `spike`
    (bool, spikes detected on the same history), one row per product-month on a full grid.
    Missing lags stay NaN, never 0."""
    d = df.merge(products[["product_id", "category"]], on="product_id", how="left").sort_values(["product_id", "month"])
    d = d.reset_index(drop=True)
    if "forecasted" not in d:
        d["forecasted"] = False
    d["cat_code"] = d["category"].astype("category").cat.codes
    g = d.groupby("product_id")["demand"]
    d["scale"] = g.transform(lambda s: s.shift(h).expanding().mean()).clip(lower=1.0)
    for i, k in enumerate((h, h + 1, h + 2), 1):
        d[f"lag{i}"] = g.shift(k) / d["scale"]
    d["lag12"] = g.shift(12) / d["scale"] if h <= 12 else np.nan
    r3 = g.transform(lambda s: s.shift(h).rolling(3, min_periods=1).mean())
    r12 = g.transform(lambda s: s.shift(h).rolling(12, min_periods=3).mean())
    d["roll3"] = r3 / d["scale"]
    d["roll6"] = g.transform(lambda s: s.shift(h).rolling(6, min_periods=1).mean()) / d["scale"]
    d["trend"] = r3 / r12.where(r12 > 0)
    d["month_num"] = d["month"].dt.month
    d["is_nov"] = (d["month_num"] == 11).astype(int)
    d["is_dec"] = (d["month_num"] == 12).astype(int)
    d = d.join(festival_flags(sorted(d["month"].unique())), on="month")
    d = _lift_asof(d, h)
    # spike rate in this calendar month over the past years (T-12, T-24): always <= T-h for h <= 12
    if "spike" in d:
        sp = d["spike"].fillna(False).astype(float).where(~d["forecasted"])
        seen = (d["demand"].notna() & ~d["forecasted"]).astype(float)
        gs, gn = sp.groupby(d["product_id"]), seen.groupby(d["product_id"])
        hits = gs.shift(12).fillna(0) + gs.shift(24).fillna(0)
        years = gn.shift(12).fillna(0) + gn.shift(24).fillna(0)
        d["product_month_spike_rate"] = (hits / years.where(years > 0)) if h <= 12 else np.nan
    else:
        d["product_month_spike_rate"] = np.nan
    return d


DEFAULT_PARAMS = dict(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=10, verbose=-1)
SIMPLE_PARAMS = dict(DEFAULT_PARAMS, num_leaves=7, min_child_samples=50, reg_lambda=1.0)   # candidate "simpler ML"


def train(clean, products, powers=(1.3,), quantiles=(0.5, 0.9), h=1, params=None):
    """Fit the horizon-h models on one feature build. `clean` may carry `spike` (feature input) and
    `target_cap` (training target is min(demand, target_cap): variant B caps one-off spikes).
    Returns {"mean_1.3": ..., "p50": ..., "p90": ...}."""
    X, y, y_mean, w = training_rows(clean, products, h)
    params = dict(params or DEFAULT_PARAMS)
    models = {}
    for q in quantiles:
        models[f"p{int(q * 100)}"] = lgb.LGBMRegressor(objective="quantile", alpha=q, **params).fit(X, y, sample_weight=w)
    for p in powers:
        models[f"mean_{p}"] = lgb.LGBMRegressor(objective="tweedie", tweedie_variance_power=p, **params).fit(
            X, y_mean, sample_weight=w)
    return models


def training_rows(clean, products, h):
    """Training set for horizon h: rows with a known target and at least MIN_KNOWN_MONTHS known
    months behind the scale. Target y = demand / scale (one-off spikes capped when `target_cap` is
    given). The mean model's target is clipped at Y_CLIP: a product's first sales divided by a tiny
    prior mean give ratios in the hundreds, and a mean model averages those into tree leaves that
    large products share (one fold forecast 15x actual before this). Returns X, y, y_mean, weights."""
    cols = ["product_id", "month", "demand", "imputed"] + [c for c in ("spike", "target_cap") if c in clean]
    d = build_features(clean[cols], products, h)
    d = d[d["demand"].notna() & d["lag1"].notna()]
    if MIN_KNOWN_MONTHS >= 3:
        d = d[d["lag3"].notna()]
    target = d["demand"] if "target_cap" not in d else np.fmin(d["demand"], d["target_cap"].fillna(np.inf))
    y = target / d["scale"]
    w = np.where(d["imputed"], 0.5, 1.0)  # trust filled-in months a bit less
    return d[FEATURES], y, y.clip(upper=Y_CLIP), w


def forecast(clean, products, models, h, driver="mean_1.3"):
    """Direct forecast of the single month h months after the data ends, with the horizon-h models.
    Returns df: product_id, month, one column per model, `mean` (= driver) and the spike/lift features."""
    cols = ["product_id", "month", "demand"] + (["spike"] if "spike" in clean else [])
    hist = clean[cols].copy()
    hist["forecasted"] = False
    last = hist["month"].max()
    fut = [pd.DataFrame({"product_id": products["product_id"], "month": last + pd.DateOffset(months=i),
                         "demand": np.nan, "forecasted": True}) for i in range(1, h + 1)]
    d = build_features(pd.concat([hist, *fut], ignore_index=True), products, h)
    row = d[d["month"] == last + pd.DateOffset(months=h)].copy()
    for name, mdl in models.items():
        row[name] = np.clip(mdl.predict(row[FEATURES]) * row["scale"], 0, None)
    row["mean"] = row[driver]
    return row[["product_id", "month", *models, "mean"] + LIFT_FEATURES + SPIKE_FEATURES].reset_index(drop=True)


def forecast_months(clean, products, models_by_h, driver="mean_1.3"):
    """Forecast months 1..H after the data ends, each with its own horizon's models."""
    return pd.concat([forecast(clean, products, m, h, driver) for h, m in sorted(models_by_h.items())],
                     ignore_index=True)


def feature_importance(models, key):
    """Gain importance of one model, as a share of total gain."""
    b = models[key].booster_
    imp = pd.Series(b.feature_importance("gain"), index=b.feature_name())
    return (imp / imp.sum()).sort_values(ascending=False)


def average_baseline(panel, before, window=6):
    """What the business does today: average of raw sales over the last `window` months, zeros included."""
    h = panel[(panel["month"] < before) & (panel["month"] >= before - pd.DateOffset(months=window))]
    return h.groupby("product_id")["units_sold"].mean().rename("avg")


def save_models(models_by_h, out_dir, clean, products, power, extra=None):
    """Write lgbm_mean_hNN.txt, lgbm_p50_hNN.txt, lgbm_p90_hNN.txt per horizon and meta.json."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("lgbm_*.txt"):
        old.unlink()
    for h, models in models_by_h.items():
        for key, fname in {f"mean_{power}": "lgbm_mean", "p50": "lgbm_p50", "p90": "lgbm_p90"}.items():
            models[key].booster_.save_model(str(out / f"{fname}_h{h:02d}.txt"))
    meta = dict(created=datetime.now().isoformat(timespec="seconds"),
                training_window=[f"{clean['month'].min():%Y-%m}", f"{clean['month'].max():%Y-%m}"],
                horizons=sorted(int(h) for h in models_by_h),
                method="direct: one model set per horizon h, features from data through T-h",
                n_products=int(products["product_id"].nunique()), features=FEATURES,
                tweedie_variance_power=power, categories=sorted(products["category"].astype(str).unique()),
                festivals_csv=FESTIVALS_CSV.name, festivals_sha256=CAL["sha256"], festival_features=TIER1,
                festivals_known_until=f"{CAL['end']:%Y-%m-%d}", lightgbm_version=lgb.__version__, **(extra or {}))
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=float))
    return out


def load_models(model_dir):
    """Load saved boosters as {h: {"mean": ..., "p50": ..., "p90": ...}}; use with forecast_months(driver="mean")."""
    d = Path(model_dir)
    meta = json.loads((d / "meta.json").read_text())
    if meta["features"] != FEATURES:
        raise ValueError("saved models were trained with different features; retrain")
    if meta["festivals_sha256"] != CAL["sha256"]:
        raise ValueError("festivals.csv changed since these models were trained; retrain")
    return {h: {k: lgb.Booster(model_file=str(d / f"lgbm_{k}_h{h:02d}.txt")) for k in ("mean", "p50", "p90")}
            for h in meta["horizons"]}, meta
