"""The data contract. Real exports get mapped into these three tables.

sales.csv     product_id, month (YYYY-MM), units_sold, status (optional)
products.csv  product_id, name, category, shelf_life_days, lead_time_days, unit_cost,
              primary_source (optional), backup_source (optional)
stock.csv     product_id, on_hand_units, held_units, earliest_expiry (YYYY-MM-DD, optional)

status values: sellable | fda_hold | stockout | recall. Anything other than
"sellable" means sales that month do NOT reflect demand.
"""
from pathlib import Path
import pandas as pd

UNAVAILABLE = {"fda_hold", "stockout", "recall"}


def load(data_dir="data"):
    d = Path(data_dir)
    sales = pd.read_csv(d / "sales.csv", dtype={"product_id": str})
    products = pd.read_csv(d / "products.csv", dtype={"product_id": str})
    stock = pd.read_csv(d / "stock.csv", dtype={"product_id": str}) if (d / "stock.csv").exists() else None

    for col in ["product_id", "month", "units_sold"]:
        if col not in sales:
            raise ValueError(f"sales.csv is missing column '{col}'")
    if "status" not in sales:
        sales["status"] = "sellable"
    sales["status"] = sales["status"].fillna("sellable").str.lower().str.strip()
    sales["month"] = pd.to_datetime(sales["month"].astype(str).str[:7] + "-01")
    sales["units_sold"] = pd.to_numeric(sales["units_sold"], errors="coerce").fillna(0).clip(lower=0)
    sales = sales.groupby(["product_id", "month"], as_index=False).agg(
        units_sold=("units_sold", "sum"), status=("status", "first"))

    # Full product x month grid: a missing row means nothing sold that month.
    months = pd.date_range(sales["month"].min(), sales["month"].max(), freq="MS")
    grid = pd.MultiIndex.from_product([products["product_id"], months], names=["product_id", "month"])
    panel = sales.set_index(["product_id", "month"]).reindex(grid).reset_index()
    panel["units_sold"] = panel["units_sold"].fillna(0)
    panel["status"] = panel["status"].fillna("sellable")
    return panel.sort_values(["product_id", "month"]).reset_index(drop=True), products, stock
