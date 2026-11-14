"""Run the whole core pipeline.

  python run.py --generate      # make synthetic data in data/ (until the real export arrives)
  python run.py                 # gaps -> backtest -> order recommendations, saved to outputs/
"""
import argparse
from pathlib import Path
import pandas as pd

from inventory import schema, synthetic, gaps, forecast, backtest, orders

ap = argparse.ArgumentParser()
ap.add_argument("--generate", action="store_true", help="write synthetic data to data/ first")
ap.add_argument("--data", default="data")
ap.add_argument("--test-months", type=int, default=6)
a = ap.parse_args()

if a.generate:
    print("synthetic data ->", synthetic.generate(a.data))
panel, products, stock = schema.load(a.data)
out = Path("outputs"); out.mkdir(exist_ok=True)
pd.set_option("display.width", 200, "display.max_columns", 20, "display.max_colwidth", 90)
print(f"\n{len(products)} products, {panel['month'].min():%b %Y} to {panel['month'].max():%b %Y}")

suspected = gaps.detect_unflagged_gaps(panel)
known = panel[panel["status"] != "sellable"].groupby(["product_id", "status"])["month"].agg(["min", "max", "count"])
print("\n== Known gaps (from status column) ==\n" + (known.to_string() if len(known) else "none"))
print("\n== Suspected UNRECORDED gaps (agent should ask about these) ==\n" + (suspected.to_string(index=False) if len(suspected) else "none"))
suspected.to_csv(out / "suspected_gaps.csv", index=False)

print(f"\n== Backtest: last {a.test_months} months, averaging vs model ==")
res = backtest.run(panel, products, a.test_months)
summary = backtest.summarize(res)
print(summary.to_string())
res.to_csv(out / "backtest_detail.csv", index=False)
summary.to_csv(out / "backtest_summary.csv")

clean = gaps.clean_demand(panel, suspected)
models = forecast.train(clean, products)
horizon = max(1, int(((products["lead_time_days"] + 30) / 30).apply(lambda x: -(-x // 1)).max()))
fc = forecast.forecast(clean, products, models, horizon)
recs = orders.recommend(panel, clean, products, stock, fc)
recs.to_csv(out / "recommendations.csv", index=False)
print("\n== Order recommendations with reasons (showing those with notes) ==")
cols = ["product_id", "name", "forecast_p50", "recommended_order", "averaging_would_order", "reasons"]
print(recs[recs["reasons"] != ""][cols].to_string(index=False))
print(f"\nSaved to {out}/")
