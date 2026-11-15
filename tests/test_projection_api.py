"""Read-only API additions for the dashboard redesign: /api/projection sums outputs/stock_projection.csv (all products or
one brand) without recalculating anything, and /api/products can return more than 1,000 rows. Demo data, no network."""
import pandas as pd

from agent.context import use
from api import agent_routes as AR

TODAY = "2026-11-14"


def test_projection_totals_match_the_projection_file(demo_full):
    d = demo_full
    pr = pd.read_csv(d["out"] / "stock_projection.csv", dtype={"product_id": str})
    with use(d["data"], d["out"], TODAY):
        r = AR.projection(months=6)
        b = AR.projection(brand="Sunrise", months=12)
        none = AR.projection(brand="No such brand")
    first = sorted(pr["month"].unique())[:6]
    assert r["available"] and [m["month"] for m in r["months"]] == first
    want = pr[pr["month"] == first[0]][["projected_stock", "floor_3_months"]].sum()
    assert abs(r["months"][0]["projected_stock"] - want["projected_stock"]) < 0.1
    assert abs(r["months"][0]["floor_3_months"] - want["floor_3_months"]) < 0.1
    pb = pd.read_csv(d["out"] / "product_brands.csv", dtype={"product_id": str})
    ids = set(pb.loc[pb["brand"] == "Sunrise", "product_id"])
    assert b["products"] == pr[pr["product_id"].isin(ids)]["product_id"].nunique()
    assert none["available"] is False and none["months"] == []


def test_projection_without_stock_data(copy_demo):
    (copy_demo["out"] / "stock_projection.csv").unlink()
    with use(copy_demo["data"], copy_demo["out"], TODAY):
        assert AR.projection() == dict(available=False, brand=None, months=[])


def test_products_page_size_cap(demo_full):
    with use(demo_full["data"], demo_full["out"], TODAY):
        r = AR.products(show="all", limit=5000)
        small = AR.products(show="all", limit=3)
    assert len(r["products"]) == r["matched"] == r["total"]
    assert len(small["products"]) == 3                       # the default contract (limit) still applies
