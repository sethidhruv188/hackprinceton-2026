"""Ordering rule (owner's policy): order early enough that new stock lands before we run out,
with a cushion, and order enough to last a while so we don't reorder every week.

Per product, at a decision date t, with the model's mean monthly forecast (Diwali is inside it):

  lead time      LEAD_DAYS = 130 (worst case; lead_time_months = 130 / 30.4)
  reorder_point  = forecast demand over [t, t + lead) + over the next cushion_months after that
                 (so when the order lands, the next cushion_months of forecast sales are on hand)
  order_up_to    = reorder_point + forecast demand over the next cover_months after that
  rule           order when (on_hand + on_order) < reorder_point;
                 quantity = ceil(order_up_to - (on_hand + on_order)) whole cases

Months are converted to days at 30.4 days per month; demand inside a calendar month is spread
evenly over its days, so partial months are prorated by days. Owner's rule: keep at least 3 months
of forward forecast sales on hand at all times (the forecast of the next 3 months from that date,
so Diwali months count higher), so the defaults are a 3-month cushion and 2 months of cover per order.

`stock_status()` turns these levels into "order now: N cases" or "OK until <date>" for a stock
table (position = on hand + on order - held); inventory/stock_plan.py does the same vectorized for
the brand orders.
"""
import math

import numpy as np
import pandas as pd

LEAD_DAYS = 130
DAYS_PER_MONTH = 30.4
CUSHION_MONTHS = 3.0      # owner: keep at least 3 months of forward forecast sales on hand
COVER_MONTHS = 2.0


def month_starts(first, n):
    return pd.date_range(pd.Timestamp(first), periods=n, freq="MS")


def overlap_weights(starts, begin, days):
    """Fraction of each month's demand that falls in [begin, begin + days). starts: month starts."""
    starts = pd.DatetimeIndex(starts)
    ends = starts + pd.offsets.MonthBegin(1)
    b = pd.Timestamp(begin)
    e = b + pd.Timedelta(days=float(days))
    lo = np.maximum(starts.values, np.datetime64(b))
    hi = np.minimum(ends.values, np.datetime64(e))
    overlap = np.clip((hi - lo) / np.timedelta64(1, "D"), 0, None)
    return overlap / ((ends - starts) / pd.Timedelta(days=1)).values


def demand_over(F, starts, begin, days):
    """Forecast demand in [begin, begin + days) for every row of F (products x months).
    Raises if the forecast does not reach far enough."""
    starts = pd.DatetimeIndex(starts)
    end = pd.Timestamp(begin) + pd.Timedelta(days=float(days))
    if end > starts[-1] + pd.offsets.MonthBegin(1) + pd.Timedelta(seconds=1):
        raise ValueError(f"forecast ends {starts[-1]:%Y-%m}; need it through {end:%Y-%m-%d}")
    return np.asarray(F, float) @ overlap_weights(starts, begin, days)


def horizon_days(cushion_months=CUSHION_MONTHS, cover_months=COVER_MONTHS, lead_days=LEAD_DAYS):
    return lead_days + (cushion_months + cover_months) * DAYS_PER_MONTH


def levels(F, starts, as_of, cushion_months=CUSHION_MONTHS, cover_months=COVER_MONTHS, lead_days=LEAD_DAYS):
    """Returns dict of arrays: lead_demand, cushion_demand, reorder_point, cover_demand, order_up_to."""
    c, v = cushion_months * DAYS_PER_MONTH, cover_months * DAYS_PER_MONTH
    lead = demand_over(F, starts, as_of, lead_days)
    rop = demand_over(F, starts, as_of, lead_days + c)
    out = demand_over(F, starts, as_of, lead_days + c + v)
    return dict(lead_demand=lead, cushion_demand=rop - lead, reorder_point=rop, cover_demand=out - rop, order_up_to=out)


def order_quantity(position, reorder_point, order_up_to):
    """Whole cases to order now (0 when the position is at or above the reorder point)."""
    position, rop, out = np.asarray(position, float), np.asarray(reorder_point, float), np.asarray(order_up_to, float)
    need = np.where(position < rop, np.ceil(np.round(out - position, 9)), 0.0)
    return np.maximum(need, 0.0)


def extend_seasonally(fc, months_needed):
    """fc: product_id, month, forecast (consecutive months). Months past the end are filled with
    the same calendar month one year earlier inside the forecast (returns the wide matrix and the
    month starts, plus which months were extended)."""
    wide = fc.pivot_table(index="product_id", columns="month", values="forecast", aggfunc="sum").sort_index(axis=1)
    starts = pd.DatetimeIndex(wide.columns)
    extra = pd.date_range(starts[-1] + pd.offsets.MonthBegin(1), periods=max(0, months_needed - len(starts)), freq="MS")
    for m in extra:
        src = m - pd.DateOffset(years=1)
        wide[m] = wide[src] if src in wide else wide[starts[-1]]
    return wide.sort_index(axis=1), set(extra)


def reorder_points(fc, products, months, cushion_months=CUSHION_MONTHS, cover_months=COVER_MONTHS,
                   reasons=None, holds=None):
    """One row per product per month: product_id, name, month, forecast_cases, reorder_point,
    order_up_to, reason. Levels are as of the 1st of each month. Without stock data this is the
    gross plan. fc: product_id, month, forecast. reasons: optional {(product_id, 'YYYY-MM'): text}.
    holds: optional {product_id: 'YYYY-MM'} for products currently on hold."""
    months = pd.DatetimeIndex(sorted(pd.to_datetime(months)))
    span = math.ceil(horizon_days(cushion_months, cover_months) / 28) + 1
    wide, extended = extend_seasonally(fc, len(pd.date_range(fc["month"].min(), months[-1], freq="MS")) + span)
    starts = pd.DatetimeIndex(wide.columns)
    F = wide.to_numpy(float)
    names = products.set_index("product_id")["name"]
    rows = []
    for m in months:
        lv = levels(F, starts, m, cushion_months, cover_months)
        horizon_end = m + pd.Timedelta(days=horizon_days(cushion_months, cover_months))
        uses_ext = any(e < horizon_end for e in extended)
        for i, pid in enumerate(wide.index):
            txt = (f"reorder when stock + on order < {lv['reorder_point'][i]:.0f}: {lv['lead_demand'][i]:.0f} cases "
                   f"sell during the 130-day lead time, and {lv['cushion_demand'][i]:.0f} more keeps {cushion_months:g} months "
                   f"of stock on hand when it arrives; each order tops up to {lv['order_up_to'][i]:.0f} ({cover_months:g} more months)")
            if uses_ext:
                txt += "; months past the 12-month forecast use the same month a year earlier"
            if holds and pid in holds:
                txt += f"; on hold since {holds[pid]}: confirm release before ordering"
            if reasons and (pid, f"{m:%Y-%m}") in reasons:
                txt += f"; {reasons[(pid, f'{m:%Y-%m}')]}"
            rows.append(dict(product_id=pid, name=names.get(pid, ""), month=f"{m:%Y-%m}",
                             forecast_cases=round(float(wide.at[pid, m]), 1),
                             reorder_point=round(float(lv["reorder_point"][i]), 1),
                             order_up_to=round(float(lv["order_up_to"][i]), 1), reason=txt))
    return pd.DataFrame(rows, columns=["product_id", "name", "month", "forecast_cases", "reorder_point",
                                       "order_up_to", "reason"])


def stock_status(fc, stock, today, cushion_months=CUSHION_MONTHS, cover_months=COVER_MONTHS, horizon_days_out=365):
    """For when a stock file exists. stock: product_id, on_hand_units, held_units (excluded),
    on_order_units, expected_arrival (YYYY-MM-DD; on-order stock counts in the position either way,
    as the rule says). Returns product_id, position, reorder_point, order_up_to, action, where action
    is "order now: N cases" or "OK until <date>" (the first day the projected position, after
    forecast demand, falls below that day's reorder point)."""
    today = pd.Timestamp(today).normalize()
    need = math.ceil((horizon_days_out + horizon_days(cushion_months, cover_months)) / 28) + 2
    wide, _ = extend_seasonally(fc, need)
    starts = pd.DatetimeIndex(wide.columns)
    if today < starts[0]:
        raise ValueError("forecast starts after today")
    F = wide.to_numpy(float)
    from .stock_plan import stock_frame                     # accepts *_cases or *_units columns
    s = stock_frame(stock)[0].set_index("product_id").reindex(wide.index)
    pos = (s["on_hand_cases"].fillna(0) + s["on_order_cases"].fillna(0) - s["held_cases"].fillna(0)).to_numpy(float)
    lv = levels(F, starts, today, cushion_months, cover_months)
    qty = order_quantity(pos, lv["reorder_point"], lv["order_up_to"])
    actions = []
    for i in range(len(pos)):
        if qty[i] > 0:
            actions.append(f"order now: {int(qty[i])} cases")
            continue
        until = None
        for d in range(7, horizon_days_out + 1, 7):              # weekly check is plenty for ordering
            day = today + pd.Timedelta(days=d)
            left = pos[i] - demand_over(F[i:i + 1], starts, today, d)[0]
            if left < levels(F[i:i + 1], starts, day, cushion_months, cover_months)["reorder_point"][0]:
                until = day
                break
        actions.append(f"OK until {until:%Y-%m-%d}" if until is not None else f"OK beyond {horizon_days_out} days")
    return pd.DataFrame({"product_id": wide.index, "position": pos.round(1),
                         "reorder_point": lv["reorder_point"].round(1), "order_up_to": lv["order_up_to"].round(1),
                         "action": actions})
