"""Small fake panels for tests (no real data)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MONTHS = pd.date_range("2024-07-01", "2026-10-01", freq="MS")


def make_panel(n=4, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        for m in MONTHS:
            lam = 20 * (2.5 if m.month in (10, 11) else 1.0)
            rows.append(dict(product_id=f"P{i}", month=m, units_sold=float(rng.poisson(lam)), status="sellable"))
    panel = pd.DataFrame(rows)
    products = pd.DataFrame(dict(product_id=[f"P{i}" for i in range(n)], name=[f"Item {i}" for i in range(n)],
                                 category=["spices" if i % 2 else "snacks" for i in range(n)],
                                 lead_time_days=130, lead_time_typical_days=105, unit_cost=np.nan,
                                 shelf_life_days=np.nan, case_weight_kg=10.0))
    return panel, products


@pytest.fixture
def panel_products():
    return make_panel()
