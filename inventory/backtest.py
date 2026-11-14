"""Replay recent months at the real decision point: what would averaging have done vs. our model?

An order for month M is placed ~130 days ahead, so the forecast for M may use data only through
M-5 (lead_gap = 4 months between the last known month and M). For each test month M we cut the
history there, clean it, train the direct horizon-5 models, forecast M, and compare with what sold. The
averaging baseline uses the same cutoff (mean of raw sales over the `baseline_months` before it).

Scored rows: product-months where the product was sellable (not on hold, not a suspected gap,
already listed) AND had at least one sellable month in the baseline window, so both the model
and averaging had real history to work from.

Metrics per forecaster: bias = forecast total / actual total, WAPE = sum|f - a| / sum a,
units short = sum max(a - f, 0), units over = sum max(f - a, 0). Units are current-code cases
(or $ when every product has a unit cost; there are none yet).
"""
import numpy as np
import pandas as pd

from . import spikes
from .gaps import clean_demand, detect_unflagged_gaps
from .forecast import train, forecast, average_baseline, festival_flags, month_kind

LEAD_GAP = 4


def decision_cutoff(month, lead_gap=LEAD_GAP):
    """First month NOT known when ordering for `month` (data through month - lead_gap - 1)."""
    return pd.Timestamp(month) - pd.DateOffset(months=lead_gap)


def fold_history(panel, month, lead_gap=LEAD_GAP):
    return panel[panel["month"] < decision_cutoff(month, lead_gap)]


def _clean(hist, fill_gaps):
    return clean_demand(hist, detect_unflagged_gaps(hist) if fill_gaps else None)


def run(panel, products, test_months, variants, lead_gap=LEAD_GAP, baseline_months=6, fill_gaps=True,
        item_map=None, cache=None, log=print):
    """variants: list of dicts(name, powers, cap_one_off). Inside each fold, gaps and spikes are
    found on that fold's own history (data through M-5) and feed the spike-rate feature; variant
    with cap_one_off=True caps one-off spike months at 2 x baseline in the training target only.
    Returns (rows, folds) DataFrames."""
    cache = {} if cache is None else cache
    rows, folds = [], []
    h = lead_gap + 1
    for t in test_months:
        t = pd.Timestamp(t)
        cutoff = decision_cutoff(t, lead_gap)
        if t not in cache:
            hist = fold_history(panel, t, lead_gap)
            clean = _clean(hist, fill_gaps)
            sp = spikes.detect(hist, clean, item_map)
            clean["spike"] = spikes.spike_flags(clean, sp)
            clean["cap"] = spikes.one_off_caps(clean, sp)
            cache[t] = (clean, sp)
        clean, sp = cache[t]
        tm = sorted(clean["month"].unique())
        fl = festival_flags(tm)
        folds.append(dict(test_month=f"{t:%Y-%m}", train_from=f"{tm[0]:%Y-%m}", train_through=f"{tm[-1]:%Y-%m}",
                          train_months=len(tm), diwali_months_in_training=int(fl["fest_diwali_this"].sum()),
                          pre_diwali_months_in_training=int(fl["fest_diwali_next"].sum()),
                          spikes_in_training=len(sp), one_off_in_training=int((sp["cause"] == "one_off").sum())))
        avg = average_baseline(panel, cutoff, baseline_months)
        win = clean[clean["month"] >= cutoff - pd.DateOffset(months=baseline_months)]
        had = win.groupby("product_id")["unavailable"].apply(lambda s: bool((~s).any())).rename("had_history")
        r = panel[panel["month"] == t][["product_id", "month", "units_sold"]].merge(avg, on="product_id", how="left")
        r = r.merge(had, on="product_id", how="left")
        for v in variants:
            c = clean.drop(columns=["cap"]).assign(target_cap=clean["cap"]) if v.get("cap_one_off") else clean.drop(columns=["cap"])
            models = train(c, products, powers=v["powers"], h=h)
            f = forecast(c, products, models, h, driver=f"mean_{v['powers'][0]}")
            f = f[f["month"] == t].set_index("product_id")
            for p in v["powers"]:
                r[f"{v['name']}_mean_{p}"] = r["product_id"].map(f[f"mean_{p}"])
            r[f"{v['name']}_p90"] = r["product_id"].map(f["p90"])
            r[f"{v['name']}_p50"] = r["product_id"].map(f["p50"])
        rows.append(r)
        log(f"  fold {t:%Y-%m}: trained on {tm[0]:%Y-%m}..{tm[-1]:%Y-%m} ({len(tm)} months, {len(sp)} spikes found in it)")
    res = pd.concat(rows, ignore_index=True)
    scored = _clean(panel, fill_gaps)[["product_id", "month", "unavailable"]]
    res = res.merge(scored, on=["product_id", "month"], how="left")
    res = res[~res["unavailable"].fillna(False).astype(bool) & res["had_history"].fillna(False).astype(bool)]
    res = res.drop(columns=["unavailable", "had_history"])
    res["avg"] = res["avg"].fillna(0.0)
    return res.reset_index(drop=True), pd.DataFrame(folds)


def annotate(res, products, regularity=None):
    """Add category, month kind (Diwali month / pre-Diwali month / other) and regularity group."""
    kinds = {m: month_kind(m) for m in res["month"].unique()}
    out = res.merge(products[["product_id", "category", "unit_cost"]], on="product_id", how="left")
    out["kind"] = out["month"].map(kinds)
    if regularity is not None:
        out = out.merge(regularity, on="product_id", how="left")
    return out


# ---------- metrics ----------

def stock(mean, p90, k):
    return mean + k * np.maximum(p90 - mean, 0)


def metrics(actual, f):
    a, f = np.asarray(actual, float), np.asarray(f, float)
    tot = a.sum()
    return dict(bias=f.sum() / tot if tot else np.nan, WAPE_pct=100 * np.abs(f - a).sum() / tot if tot else np.nan,
                units_short=np.maximum(a - f, 0).sum(), units_over=np.maximum(f - a, 0).sum())


def compare(g, forecasters):
    """One row: rows, actual, then bias/WAPE/short/over per forecaster. forecasters: {label: array}."""
    out = {"rows": len(g), "actual": g["units_sold"].sum()}
    for label, f in forecasters.items():
        for k, v in metrics(g["units_sold"], f).items():
            out[f"{label}_{k}"] = v
    return out


def segment_table(res, segments, forecasters_fn):
    """segments: {row label: boolean mask}; forecasters_fn(g) -> {label: array}."""
    rows = {}
    for label, mask in segments.items():
        g = res[mask]
        if len(g):
            rows[label] = compare(g, forecasters_fn(g))
    return pd.DataFrame(rows).T


# ---------- stocking level ----------

K_GRID = np.round(np.arange(0, 4.001, 0.01), 2)


def k_curve(g, mean_col, p90_col, grid=K_GRID):
    a = g["units_sold"].to_numpy(float)
    m = g[mean_col].to_numpy(float)
    buf = np.maximum(g[p90_col].to_numpy(float) - m, 0)
    S = m[None, :] + grid[:, None] * buf[None, :]
    return pd.DataFrame({"k": grid, "short": np.maximum(a - S, 0).sum(1), "over": np.maximum(S - a, 0).sum(1),
                         "stock": S.sum(1)})


def matched(g, mean_col, p90_col):
    """(a) model overstock at the smallest k where model short <= averaging short;
    (b) model short at the largest k where model over <= averaging over.
    Fairness check: averaging scaled up/down to the model's total stock at each point."""
    curve = k_curve(g, mean_col, p90_col)
    a = g["units_sold"].to_numpy(float)
    avg = metrics(a, g["avg"])
    out = dict(rows=len(g), actual=a.sum(), avg_short=avg["units_short"], avg_over=avg["units_over"])

    def scaled_avg(total):
        s = g["avg"].to_numpy(float)
        f = s * (total / s.sum()) if s.sum() else s
        return metrics(a, f)

    ok = curve[curve["short"] <= avg["units_short"]]
    if len(ok):
        r = ok.iloc[0]
        fair = scaled_avg(r["stock"])
        out.update(a_k=r["k"], a_model_short=r["short"], a_model_over=r["over"],
                   a_fair_avg_short=fair["units_short"], a_fair_avg_over=fair["units_over"])
    else:
        out.update(a_k=np.nan, a_model_short=curve["short"].iloc[-1], a_model_over=curve["over"].iloc[-1],
                   a_fair_avg_short=np.nan, a_fair_avg_over=np.nan, a_note="even k=4 misses more than averaging")
    ok = curve[curve["over"] <= avg["units_over"]]
    if len(ok):
        r = ok.iloc[-1]
        fair = scaled_avg(r["stock"])
        out.update(b_k=r["k"], b_model_short=r["short"], b_model_over=r["over"],
                   b_fair_avg_short=fair["units_short"], b_fair_avg_over=fair["units_over"])
    else:
        out.update(b_k=np.nan, b_note="model mean alone overstocks more than averaging")
    return out


def choose_k(g, mean_col, p90_col):
    """Smallest k with model units short <= averaging units short (least overstock among those)."""
    curve = k_curve(g, mean_col, p90_col)
    ok = curve[curve["short"] <= metrics(g["units_sold"], g["avg"])["units_short"]]
    return float(ok["k"].iloc[0]) if len(ok) else float(K_GRID[-1])


def spikes_seen(res, spk, mean_col, factor=1.5):
    """For spikes (found on the full data) in the test months: did the forecast reach factor x the
    spike's baseline? Model vs averaging, by cause."""
    m = spk.merge(res[["product_id", "month", mean_col, "avg"]], on=["product_id", "month"], how="inner")
    m["model_saw_it"] = m[mean_col] >= factor * m["baseline"]
    m["avg_saw_it"] = m["avg"] >= factor * m["baseline"]
    t = m.groupby("cause").agg(spikes=("cases", "size"), model_saw_it=("model_saw_it", "sum"), avg_saw_it=("avg_saw_it", "sum"))
    t.loc["all"] = t.sum()
    t["model_%"] = (100 * t["model_saw_it"] / t["spikes"]).round(1)
    t["avg_%"] = (100 * t["avg_saw_it"] / t["spikes"]).round(1)
    return t
