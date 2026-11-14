"""Simulation backtest: run the ordering rule month by month with stock carried over.

For each decision month D (the 1st of each test month) each method forecasts months D..D+N-1
from data through D-5 only (the same 4-month decision gap as the single-month backtest):
  model     direct Tweedie mean models for horizons 5..(5+N-1), trained on that history; when the
            history is too short for a horizon (< MIN_TARGET_MONTHS months of targets), the path
            is held flat at the mean of the last three trained horizons (column flat_fill)
  averaging mean of raw sales over the `baseline_months` months up to D-5, flat
Both feed the same rule (inventory/policy.py). Per product:
  start     stock = reorder point at the first decision, using the averaging forecast (the same
            starting stock for both methods)
  each month  check the rule at the 1st (position = stock + on order), place the order; the
            order arrives after a lead time drawn uniformly from 105..130 days (seeded, the
            same draws for both methods); inside the month, demand is spread evenly over the
            days, arrivals land on their day, and demand that finds no stock is lost (units short)
  holds     months a product was on hold have no demand and are not scored
Products enter the simulation if they had a sellable month in the baseline window before the
first decision (so both methods had history).
"""
import numpy as np
import pandas as pd

from . import policy, spikes
from .gaps import clean_demand
from .forecast import train, forecast, average_baseline, month_kind

MIN_TARGET_MONTHS = 6      # a horizon-h model needs at least this many months of training targets


def forecast_paths(panel, products, decisions, n_ahead=10, lead_gap=4, power=1.5, cap_one_off=True,
                   item_map=None, baseline_months=6, log=print):
    """Long df: decision, product_id, month, model, avg (forecast cases for each future month)."""
    out = []
    for D in decisions:
        D = pd.Timestamp(D)
        cutoff = D - pd.DateOffset(months=lead_gap)
        hist = panel[panel["month"] < cutoff]
        clean = clean_demand(hist)                      # recorded holds/stockouts are filled
        sp = spikes.detect(hist, clean, item_map)
        clean["spike"] = spikes.spike_flags(clean, sp)
        if cap_one_off:
            clean["target_cap"] = spikes.one_off_caps(clean, sp)
        avg = average_baseline(panel, cutoff, baseline_months)
        n_hist = clean["month"].nunique()
        got, filled = [], 0
        for h in range(lead_gap + 1, lead_gap + 1 + n_ahead):
            target = cutoff - pd.DateOffset(months=1) + pd.DateOffset(months=h)
            if n_hist - h >= MIN_TARGET_MONTHS:
                models = train(clean, products, powers=[power], quantiles=(), h=h)
                f = forecast(clean, products, models, h, driver=f"mean_{power}")
                got.append(f.set_index("product_id")["mean"])
                vals = f["mean"].to_numpy()
                pids = f["product_id"]
            else:
                # Too little history to train a horizon-h model: hold the path flat at the mean of
                # the last three trained horizons (logged as a limitation).
                vals = pd.concat(got[-3:], axis=1).mean(axis=1).reindex(pids).to_numpy()
                filled += 1
            out.append(pd.DataFrame({"decision": D, "product_id": pids.to_numpy(), "month": target,
                                     "model": vals, "avg": pids.map(avg).to_numpy(), "flat_fill": n_hist - h < MIN_TARGET_MONTHS}))
        log(f"  decision {D:%Y-%m}: data through {cutoff - pd.DateOffset(months=1):%Y-%m} ({n_hist} months), "
            f"forecast {D:%Y-%m}..{D + pd.DateOffset(months=n_ahead - 1):%Y-%m}"
            + (f"; last {filled} month(s) held flat (too little history for those horizons)" if filled else ""))
    return pd.concat(out, ignore_index=True)


def lead_time_draws(n_products, n_months, seed=7, lo=105, hi=130):
    """Integer days, uniform on [lo, hi], one per product per decision (shared by both methods)."""
    return np.random.default_rng(seed).integers(lo, hi + 1, size=(n_products, n_months))


def run_product(demand, month_starts, rop, out, init_stock, lead_days):
    """Simulate one product. demand, rop, out, lead_days: arrays over months. Returns dict of arrays
    shipped, short, end_stock, ordered."""
    n = len(demand)
    stock, orders = float(init_stock), []          # orders: [arrival timestamp, qty]
    shipped, short, end, ordered = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    for j in range(n):
        start = month_starts[j]
        nxt = start + pd.offsets.MonthBegin(1)
        position = stock + sum(q for _, q in orders)
        q = float(policy.order_quantity(position, rop[j], out[j]))
        if q > 0:
            orders.append([start + pd.Timedelta(days=int(lead_days[j])), q])
            ordered[j] = q
        days = (nxt - start).days
        rate = demand[j] / days
        t = 0.0
        for arr in sorted([o for o in orders if o[0] < nxt], key=lambda o: o[0]):
            d = max(0.0, (arr[0] - start) / pd.Timedelta(days=1))
            need = rate * (d - t)
            give = min(stock, need)
            shipped[j] += give
            short[j] += need - give
            stock -= give
            stock += arr[1]
            t = d
        orders = [o for o in orders if o[0] >= nxt]
        need = rate * (days - t)
        give = min(stock, need)
        shipped[j] += give
        short[j] += need - give
        stock -= give
        end[j] = stock
    return dict(shipped=shipped, short=short, end_stock=end, ordered=ordered)


def simulate(paths, panel, products_in, sim_months, cushion_months=2.0, cover_months=2.0, seed=7, methods=("model", "avg")):
    """Run the rule for each method. Returns long df: method, product_id, month, demand, scored,
    shipped, short, end_stock, ordered."""
    sim_months = pd.DatetimeIndex(sim_months)
    pids = list(products_in)
    act = panel[panel["month"].isin(sim_months) & panel["product_id"].isin(pids)]
    A = act.pivot_table(index="product_id", columns="month", values="units_sold").reindex(index=pids, columns=sim_months)
    status = act.pivot_table(index="product_id", columns="month", values="status", aggfunc="first").reindex(
        index=pids, columns=sim_months)
    scored = (status == "sellable").to_numpy()
    demand = np.where(scored, A.fillna(0).to_numpy(float), 0.0)       # holds: no demand, not scored
    LT = lead_time_draws(len(pids), len(sim_months), seed)
    levels = {m: {} for m in methods}
    for j, D in enumerate(sim_months):
        p = paths[paths["decision"] == D]
        for m in methods:
            w = p.pivot_table(index="product_id", columns="month", values=m).reindex(index=pids).fillna(0.0)
            lv = policy.levels(w.to_numpy(float), pd.DatetimeIndex(w.columns), D, cushion_months, cover_months)
            levels[m][j] = (lv["reorder_point"], lv["order_up_to"])
    init = levels["avg"][0][0] if "avg" in levels else levels[methods[0]][0][0]
    rows = []
    for m in methods:
        rop = np.column_stack([levels[m][j][0] for j in range(len(sim_months))])
        out = np.column_stack([levels[m][j][1] for j in range(len(sim_months))])
        for i, pid in enumerate(pids):
            r = run_product(demand[i], list(sim_months), rop[i], out[i], init[i], LT[i])
            rows.append(pd.DataFrame({"method": m, "product_id": pid, "month": sim_months, "demand": demand[i],
                                      "scored": scored[i], **r}))
    return pd.concat(rows, ignore_index=True)


def summarize(sim, segments):
    """segments: {label: boolean mask over sim rows}. Per method: fill rate, product-months with a
    stockout, units short, average stock held (cases per month) and months of cover."""
    rows = {}
    for label, mask in segments.items():
        for m, g in sim[mask].groupby("method"):
            s = g[g["scored"]]
            n_months = s["month"].nunique()
            stock = g["end_stock"].sum() / max(g["month"].nunique(), 1)
            rows[(label, m)] = {"product_months": len(s), "demand": s["demand"].sum(),
                                "fill_rate_%": 100 * s["shipped"].sum() / s["demand"].sum() if s["demand"].sum() else np.nan,
                                "stockout_product_months": int((s["short"] > 0.01).sum()),
                                "units_short": s["short"].sum(), "avg_stock_cases": stock,
                                "months_of_cover": stock / (s["demand"].sum() / max(n_months, 1)) if s["demand"].sum() else np.nan}
    return pd.DataFrame(rows).T


def stock_matched(tradeoff):
    """tradeoff: rows (method, cushion) with fill_rate_% and avg_stock_cases. For each model point,
    averaging's fill rate at the same average stock (linear between its cushion points; NaN outside)."""
    avg = tradeoff.xs("avg", level="method").sort_values("avg_stock_cases")
    out = []
    for c, r in tradeoff.xs("model", level="method").iterrows():
        x = r["avg_stock_cases"]
        inside = avg["avg_stock_cases"].min() <= x <= avg["avg_stock_cases"].max()
        f = float(np.interp(x, avg["avg_stock_cases"], avg["fill_rate_%"])) if inside else np.nan
        out.append(dict(cushion_months=c, model_avg_stock=x, model_fill_rate_pct=r["fill_rate_%"],
                        averaging_fill_rate_pct_at_same_stock=f,
                        difference_pts=r["fill_rate_%"] - f if inside else np.nan))
    return pd.DataFrame(out)
