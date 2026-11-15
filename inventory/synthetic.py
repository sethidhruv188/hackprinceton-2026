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
REAL_DATA_DIR = Path(__file__).resolve().parent.parent / "data"      # the real business data: never overwritten by default


def generate(out_dir="data_demo", seed=42, write_stock=False, allow_real=False):
    """Writes sales.csv and products.csv. stock.csv only with write_stock=True (off by default:
    the pipeline runs on gross need and no stock data is created unless asked for).
    Refuses to write into the real data/ folder unless allow_real=True."""
    if Path(out_dir).resolve() == REAL_DATA_DIR and not allow_real:
        raise ValueError(f"refusing to write synthetic data into the real data folder {REAL_DATA_DIR}; "
                         "use data_demo/ or pass allow_real=True")
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
    # Harvest-commodity demo products (dals, rice, nuts), with item-code prefixes like the real data so the
    # commodity map applies. Own random stream, so the products above are unchanged.
    hr = np.random.default_rng(seed + 1)
    for pid_, name, cat, base in HARVEST_DEMO:
        products.append(dict(product_id=pid_, name=name, category=cat, shelf_life_days=365, lead_time_days=130,
                             unit_cost=round(float(hr.uniform(0.8, 6.0)), 2), primary_source="Country A", backup_source="Country B"))
        for m in months:
            lam = base * (1.25 if m in diwali else 1.0)
            rows.append(dict(product_id=pid_, month=m.strftime("%Y-%m"), units_sold=int(hr.poisson(lam * hr.gamma(8, 1 / 8))),
                             status="sellable"))
        stock.append(dict(product_id=pid_, on_hand_units=int(base * hr.uniform(2.0, 5.0)), held_units=0,
                          earliest_expiry=str((months[-1] + pd.DateOffset(months=10)).date())))
    # Case weights, so containers can be estimated (CBM per case = weight / category density, marked
    # "estimated from weight" like the real data). Named packs are parsed the engine's way ("4 lb X 10"
    # = 18.1 kg); the generic demo items get a plausible weight for their category.
    from . import packs
    prod = pd.DataFrame(products)
    parsed = packs.parse_all(prod["name"])
    named = prod["product_id"].isin({h[0] for h in HARVEST_DEMO})        # real-looking pack names
    prod["case_weight_kg"] = parsed["case_weight_kg"].where(named).fillna(prod["category"].map(DEMO_CASE_KG)).round(3)
    out = Path(out_dir)
    out.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "sales.csv", index=False)
    prod.to_csv(out / "products.csv", index=False)
    if write_stock:
        pd.DataFrame(stock).to_csv(out / "stock.csv", index=False)
    return out


# Plausible case weights (kg) for the generic demo items, by category (e.g. noodles: 30 packs of 200 g).
DEMO_CASE_KG = {"noodles": 6.0, "sweets": 8.0, "snacks": 4.0, "spices": 6.0, "beverages": 12.0, "frozen": 9.0,
                "rice_flour": 18.0, "bulk_catering": 20.0, "lentils_beans": 18.0, "nuts_dry_fruits": 8.0}

# (product_id, name, category, typical cases a month): real-looking names so config/commodity_map.csv maps them
HARVEST_DEMO = [
    ("AD201", "Sunrise Toor Dal 4 lb X 10", "lentils_beans", 60.0),
    ("AD202", "Sunrise Moong Dal 4 lb X 10", "lentils_beans", 35.0),
    ("BD301", "Chana Dal 55 Lb X", "lentils_beans", 25.0),
    ("AD299", "Sunrise Panchratna Dal 2 lb X 20", "lentils_beans", 8.0),           # mixed pack: asked to the owner
    ("AN401", "Lotus Almond 14 oz X 20", "nuts_dry_fruits", 40.0),
    ("AN402", "Lotus Cashew Whole 14 oz X 20", "nuts_dry_fruits", 30.0),
    ("AN403", "Lotus Green Raisin 14 oz X 20", "nuts_dry_fruits", 20.0),
    ("AN499", "Lotus Alo Bukhara 14 oz X 20", "nuts_dry_fruits", 6.0),             # no commodity keyword: asked
    ("AR501", "Peacock Basmati Rice 10 lb X 4", "rice_flour", 50.0),                # owner's rice rule (config/harvest.csv)
    ("AR502", "Peacock Idli Rice 20 lb X 2", "rice_flour", 15.0),                   # not in the rice rule: calendar
]

# Sample owner answers for the demo: confirm cited harvest calendar rows (rule_id = commodity_country), so the
# demo shows the loop: cited calendar row -> owner confirms -> harvest order in the brand orders.
DEMO_ANSWERS = [
    ("harvest:toor_india", "yes", "confirmed on the dashboard (demo)"),
    ("harvest:almond_usa", "yes", "confirmed on the dashboard (demo)"),
]


def write_demo_answers(out_dir, timestamp="2026-11-10T09:00:00+00:00"):
    """Write the demo owner answers (owner_answers.csv format) into a DEMO data folder."""
    out = Path(out_dir)
    if out.resolve() == REAL_DATA_DIR:
        raise ValueError("demo answers never go into the real data folder")
    pd.DataFrame([dict(question_id=q, answer=a, note=n, timestamp=timestamp) for q, a, n in DEMO_ANSWERS],
                 columns=["question_id", "answer", "note", "timestamp"]).to_csv(out / "owner_answers.csv", index=False)
    return out / "owner_answers.csv"
