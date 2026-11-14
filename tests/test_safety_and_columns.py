"""Engine fixes #3 and #4: brand_orders.csv has unique column names, and synthetic runs never write
into the real data/, outputs/ or models/ folders unless explicitly allowed. Fake data only."""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from inventory import brand_orders as BO, synthetic

ROOT = Path(__file__).resolve().parent.parent


def test_stock_order_columns_are_unique():
    cols = BO.ORDER_COLUMNS + BO.STOCK_ORDER_EXTRA
    assert len(cols) == len(set(cols)), [c for c in cols if cols.count(c) > 1]
    assert "containers" in cols and "containers_by_volume" in cols


def _stock_inputs():
    months = pd.date_range("2026-11-01", periods=12, freq="MS")
    fc = pd.DataFrame([(p, f"{m:%Y-%m}", 30.0) for p in ("A1", "A2") for m in months],
                      columns=["product_id", "month", "likely"])
    products = pd.DataFrame({"product_id": ["A1", "A2"], "name": ["Brand One Dal 4 lb X 10", "Brand One Rice 10 lb X 4"],
                             "category": ["lentils_beans", "snacks"], "case_weight_kg": [18.0, 18.0]})
    brand_map = pd.DataFrame({"product_id": ["A1", "A2"], "brand": ["BrandOne", "BrandOne"]})
    stock = pd.DataFrame({"product_id": ["A1", "A2"], "on_hand_cases": [10.0, 400.0]})
    harvest = pd.DataFrame(columns=["rule_id", "category", "match", "exclude", "order_months", "arrival_months",
                                    "cover_months", "confirmed", "note"])
    return fc, products, brand_map, stock, harvest


@pytest.mark.parametrize("max_kg", [None, 300.0])
def test_containers_by_volume_is_volume_only(max_kg):
    fc, products, brand_map, stock, harvest = _stock_inputs()
    cfg = dict(cbm=80.0, usable=0.9, max_kg=max_kg)
    r = BO.build_with_stock(fc, products, brand_map, stock, "2026-11-14", harvest_rules=harvest, cfg=cfg)
    o = r["orders"]
    assert len(o) == 1 and list(o.columns).count("containers") == 1
    row = o.iloc[0]
    assert row["containers_by_volume"] == pytest.approx(row["cbm"] / 72.0, abs=0.01)
    if max_kg is None:
        assert row["containers"] == pytest.approx(row["containers_by_volume"], abs=0.01)
    else:                                                       # the weight limit binds: more containers than by volume
        assert row["containers"] > row["containers_by_volume"] and row["container_bound"] == "weight"


def test_written_csv_reads_back_without_duplicate_columns(tmp_path):
    fc, products, brand_map, stock, harvest = _stock_inputs()
    r = BO.build_with_stock(fc, products, brand_map, stock, "2026-11-14", harvest_rules=harvest,
                            cfg=dict(cbm=80.0, usable=1.0, max_kg=None))
    r["orders"].to_csv(tmp_path / "brand_orders.csv", index=False)
    back = pd.read_csv(tmp_path / "brand_orders.csv")
    assert not any(c.endswith(".1") for c in back.columns)


def test_generate_defaults_to_demo_folder():
    import inspect
    assert inspect.signature(synthetic.generate).parameters["out_dir"].default == "data_demo"


def test_generate_refuses_real_data_folder():
    with pytest.raises(ValueError, match="real data folder"):
        synthetic.generate(ROOT / "data")


def _run(*args):
    return subprocess.run([sys.executable, str(ROOT / "run.py"), *args], cwd=ROOT, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("args", [["--generate", "--data", "data"], ["--generate", "--out", "outputs"],
                                  ["--generate", "--models", "models"], ["--source", "synthetic", "--data", "./data"]])
def test_run_refuses_synthetic_into_real_folders(args):
    # argparse rejects before anything is loaded or written
    r = _run(*args)
    assert r.returncode == 2 and "allow-overwrite-real-data" in r.stderr
