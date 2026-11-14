import pandas as pd

from inventory import gaps


def test_relabel_holds(tmp_path):
    months = pd.date_range("2024-07-01", "2026-10-01", freq="MS")
    rows = []
    for m in months:
        if m in (pd.Timestamp("2026-03-01"), pd.Timestamp("2026-04-01")):
            continue                                  # no sales line: an unrecorded gap
        rows.append(dict(product_id="P", month=f"{m:%Y-%m}", units_sold=20.0, status="sellable"))
    pd.DataFrame(rows).to_csv(tmp_path / "sales.csv", index=False)
    pd.DataFrame({"product_id": ["P"], "name": ["x"], "category": ["c"], "shelf_life_days": [None],
                  "lead_time_days": [130], "unit_cost": [None]}).to_csv(tmp_path / "products.csv", index=False)
    r = gaps.relabel_holds(tmp_path)
    assert r["relabelled"] == 2 and r["total"] == 2
    s = pd.read_csv(tmp_path / "sales.csv")
    held = s[s["status"] == "fda_hold"]
    assert set(held["month"]) == {"2026-03", "2026-04"} and (held["hold_source"] == "gap_detection").all()
    assert (held["units_sold"] == 0).all()
    assert gaps.relabel_holds(tmp_path)["total"] == 2          # idempotent
    clean = gaps.clean_demand(__import__("inventory.schema", fromlist=["load"]).load(tmp_path)[0])
    assert clean.loc[clean["month"] == "2026-03-01", "unavailable"].all()     # not zero demand
