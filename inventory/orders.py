"""Turn forecasts into order recommendations, each with plain-language reasons.

order = demand over (lead time + 1 month review) + safety stock - sellable stock,
capped so we never hold more than we can sell before it expires.
Held stock (e.g. FDA hold) is never counted as sellable.
"""
import math
import numpy as np
import pandas as pd

from .schema import UNAVAILABLE
from .forecast import average_baseline


def recommend(panel, clean, products, stock, fc):
    as_of = panel["month"].max()
    next_month = as_of + pd.DateOffset(months=1)
    avg = average_baseline(panel, next_month)
    last_status = panel[panel["month"] == as_of].set_index("product_id")["status"]
    stock = (stock if stock is not None else pd.DataFrame({"product_id": products["product_id"]})).set_index("product_id")
    out = []
    for p in products.itertuples():
        f = fc[fc["product_id"] == p.product_id].sort_values("month")
        cover = max(1, math.ceil((p.lead_time_days + 30) / 30))
        f = f.head(cover)
        p50_next, p90_next = f["p50"].iloc[0], f["p90"].iloc[0]
        need = f["p50"].sum()
        safety = (p90_next - p50_next) * math.sqrt(cover)
        on_hand = float(stock["on_hand_units"].get(p.product_id, 0)) if "on_hand_units" in stock else 0.0
        held = float(stock["held_units"].get(p.product_id, 0)) if "held_units" in stock else 0.0
        reasons = []

        g = clean[clean["product_id"] == p.product_id]
        imputed = g[g["imputed"]]
        if len(imputed):
            reasons.append(f"ignored {len(imputed)} month(s) when it couldn't be sold "
                           f"({imputed['month'].min():%b %Y} to {imputed['month'].max():%b %Y}); used prior-year pattern instead")
        if held:
            reasons.append(f"{held:.0f} units on hold are NOT counted as stock")

        sellable = on_hand
        exp = stock["earliest_expiry"].get(p.product_id) if "earliest_expiry" in stock else None
        if isinstance(exp, str) and on_hand > 0:
            months_left = max(0.0, (pd.Timestamp(exp) - next_month).days / 30)
            can_sell = p50_next * months_left
            if on_hand > can_sell:
                at_risk = on_hand - can_sell
                sellable = can_sell
                reasons.append(f"~{at_risk:.0f} units may expire by {exp}: consider a discount")

        order = max(0.0, need + safety - sellable)
        max_stock = p50_next * p.shelf_life_days / 30 + safety
        if order + sellable > max_stock:
            order = max(0.0, max_stock - sellable)
            reasons.append(f"capped by {p.shelf_life_days}-day shelf life")
        avg_order = max(0.0, avg.get(p.product_id, 0) * cover - on_hand)

        status = last_status.get(p.product_id, "sellable")
        if status in UNAVAILABLE:
            reasons.insert(0, f"currently {status.replace('_', ' ')}: no order until released; "
                              f"expected demand once released ~{p50_next:.0f}/month "
                              f"(averaging says ~{avg.get(p.product_id, 0):.0f}/month)")
            order = 0.0
        out.append(dict(product_id=p.product_id, name=p.name, category=p.category,
                        next_month=f"{next_month:%Y-%m}", forecast_p50=round(p50_next), forecast_p90=round(p90_next),
                        cover_months=cover, sellable_stock=round(sellable), held_units=round(held),
                        recommended_order=math.ceil(order), averaging_would_order=math.ceil(avg_order),
                        reasons="; ".join(reasons)))
    return pd.DataFrame(out)
