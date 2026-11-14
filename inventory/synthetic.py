"""Synthetic data shaped like Shakti's, so everything can be built before the real export arrives.

Includes the patterns the real data should show: Diwali spikes, summer drinks, steady staples,
spiky bulk orders, one product on FDA hold for 9 months, one recorded stockout, and one
UNRECORDED gap (zeros marked "sellable") that the system should catch on its own.
"""
from pathlib import Path
import numpy as np
import pandas as pd
import holidays

START, END = "2024-07", "2026-10"
CATEGORIES = {
    #  name          count shelf_life lead_time
    "noodles":       (8,   270,  45),
    "sweets":        (8,   60,   20),
    "snacks":        (10,  120,  30),
    "spices":        (10,  365,  60),
    "beverages":     (8,   180,  30),
    "frozen":        (6,   270,  30),
    "rice_flour":    (6,   365,  45),
    "bulk_catering": (4,   180,  30),
}


def diwali_months(years):
    ind = holidays.India(years=years)
    return {pd.Timestamp(d).to_period("M").to_timestamp() for d, n in ind.items() if "Diwali" in n}


DEMO_BRANDS = ["Sunrise", "Lotus", "Peacock", "Monsoon", "Banyan"]   # made-up names for the public demo


def generate(out_dir="data", seed=42, write_stock=False):
    """Writes sales.csv and products.csv. stock.csv only with write_stock=True (off by default:
    the pipeline runs on gross need and no stock data is created unless asked for)."""
    rng = np.random.default_rng(seed)
    months = pd.date_range(START + "-01", END + "-01", freq="MS")
    diwali = diwali_months(range(months[0].year, months[-1].year + 2))
    products, rows, stock = [], [], []
    pid = 0
    for cat, (n, shelf, lead) in CATEGORIES.items():
        for i in range(n):
            pid += 1
            p = f"P{pid:03d}"
            name = f"{DEMO_BRANDS[(pid - 1) % len(DEMO_BRANDS)]} {cat.replace('_', ' ').title()} item {i + 1}"
            if cat == "noodles" and i == 0:
                name = "Sunrise Schezwan Noodles 150g (FDA hold demo)"
            if cat == "spices" and i == 0:
                name = "Lotus Garam Masala 100g (unrecorded gap demo)"
            base = rng.lognormal(4.3, 0.6)
            trend = rng.normal(0.005, 0.01)
            cost = round(float(rng.uniform(0.8, 6.0)), 2)
            products.append(dict(product_id=p, name=name, category=cat, shelf_life_days=shelf,
                                 lead_time_days=lead, unit_cost=cost,
                                 primary_source="Country A", backup_source="Country B"))
            for k, m in enumerate(months):
                lam = base * (1 + trend) ** k
                nxt = m + pd.DateOffset(months=1)
                if cat in ("sweets", "snacks"):
                    lam *= 2.6 if m in diwali else (1.5 if nxt in diwali else 1.0)
                if cat == "rice_flour" and m in diwali:
                    lam *= 1.3
                if cat == "beverages" and m.month in (5, 6, 7, 8):
                    lam *= 1.6
                if cat == "frozen" and m.month in (11, 12):
                    lam *= 1.4
                if cat == "noodles" and m.month in (12, 1, 2):
                    lam *= 1.2
                demand = rng.poisson(lam * rng.gamma(8, 1 / 8))
                if cat == "bulk_catering":  # mostly quiet, occasional big orders
                    demand = int(rng.poisson(base * 4)) if rng.random() < 0.3 else 0
                sold, status = demand, "sellable"
                if "Schezwan" in name and pd.Timestamp("2026-02-01") <= m <= pd.Timestamp("2026-10-01"):
                    sold, status = 0, "fda_hold"
                if "Garam Masala 100g" in name and pd.Timestamp("2026-05-01") <= m <= pd.Timestamp("2026-07-01"):
                    sold = 0  # status stays "sellable": nobody recorded this stockout
                if cat == "beverages" and i == 0 and m == pd.Timestamp("2025-07-01"):
                    sold, status = int(demand * 0.4), "stockout"
                rows.append(dict(product_id=p, month=m.strftime("%Y-%m"), units_sold=int(sold), status=status))
            on_hand = int(base * rng.uniform(0.5, 2.0))
            held = 800 if "Schezwan" in name else 0
            expiry = (months[-1] + pd.DateOffset(months=1) + pd.Timedelta(days=int(rng.uniform(10, shelf)))).date()
            stock.append(dict(product_id=p, on_hand_units=0 if held else on_hand, held_units=held,
                              earliest_expiry=str(expiry)))
    out = Path(out_dir)
    out.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "sales.csv", index=False)
    pd.DataFrame(products).to_csv(out / "products.csv", index=False)
    if write_stock:
        pd.DataFrame(stock).to_csv(out / "stock.csv", index=False)
    return out
