"""Run the whole pipeline.

  python run.py --source real --today 2026-11-14            # real data in data/ (from --ingest)
  python run.py --source real --ingest --today 2026-11-14   # rebuild data/ from data/raw/sales/*.xlsx first
  python run.py --source real --skip-sim --today 2026-11-14 # refresh models/plans, reuse the saved simulation
  python run.py --generate --today 2026-11-14               # synthetic demo data -> data_demo/, outputs_demo/, models_demo/

Steps: holds -> spikes and product profiles -> simulation backtest of the ordering rule (model vs
6-month averaging, cushion 1/2/3 months) -> single-month accuracy tables (secondary) -> final
models -> order plan, Diwali readiness, reorder points, and brand orders against current stock when
the inventory export exists (data/raw/Inventory/CurrentInventory.xlsx -> data/stock.csv).
Every figure in the report comes from this code.
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

import json

from inventory import schema, synthetic, ingest, gaps, forecast, backtest, plan, checks, spikes, policy, simulate, brand_orders
from inventory import tournament, large_orders

ROOT = Path(__file__).resolve().parent
REAL_DIRS = {"data": "data", "out": "outputs", "models": "models"}            # the real business folders
DEMO_DIRS = {"data": "data_demo", "out": "outputs_demo", "models": "models_demo"}

ap = argparse.ArgumentParser()
ap.add_argument("--source", choices=["real", "synthetic"], default="real")
ap.add_argument("--generate", action="store_true",
                help="write synthetic data first (implies --source synthetic); default folders data_demo/, outputs_demo/, models_demo/")
ap.add_argument("--allow-overwrite-real-data", action="store_true",
                help="let a synthetic run write into the real data/, outputs/ or models/ folders (refused otherwise)")
ap.add_argument("--ingest", action="store_true", help="rebuild data/ from the .xlsx exports in --raw first")
ap.add_argument("--raw", default="data/raw/sales")
ap.add_argument("--data", default=None, help="default: data (real) or data_demo (synthetic)")
ap.add_argument("--models", default=None, help="default: models (real) or models_demo (synthetic)")
ap.add_argument("--out", default=None, help="where outputs are written; default: outputs (real) or outputs_demo (synthetic)")
ap.add_argument("--skip-sim", action="store_true",
                help="reuse <out>/sim_forecast_paths.csv instead of retraining the simulation forecasts")
ap.add_argument("--today", default=None, help="plan date (YYYY-MM-DD); default today")
ap.add_argument("--plan-months", type=int, default=12)
ap.add_argument("--baseline-months", type=int, default=6, help="months the business averages over")
ap.add_argument("--sim-from", default="2025-12", help="first simulated month (first decision trains on data through M-5)")
ap.add_argument("--lead-gap", type=int, default=backtest.LEAD_GAP)
ap.add_argument("--power", type=float, default=1.5, help="Tweedie variance power (chosen in the previous backtest)")
ap.add_argument("--cap-one-off", action=argparse.BooleanOptionalAction, default=True,
                help="cap one-off spike months at 2 x baseline in the training target (chosen in the previous backtest)")
ap.add_argument("--cushion-months", type=float, default=policy.CUSHION_MONTHS)
ap.add_argument("--cover-months", type=float, default=policy.COVER_MONTHS)
ap.add_argument("--seed", type=int, default=7, help="seed for simulated lead times")
ap.add_argument("--fill-gaps", action=argparse.BooleanOptionalAction, default=False,
                help="also fill NEW gaps detected after the confirmed holds are recorded (default off: unconfirmed)")
ap.add_argument("--retest", action="store_true",
                help="re-run the forecaster tournament on the current data (fresh simulation) and update config/forecaster.txt")
ap.add_argument("--use-stock", action=argparse.BooleanOptionalAction, default=None,
                help="order against stock (default: on when data/stock.csv exists)")
ap.add_argument("--inventory", default="data/raw/Inventory/CurrentInventory.xlsx",
                help="current-inventory export; rebuilt into data/stock.csv when present")
a = ap.parse_args()
if a.generate:
    a.source = "synthetic"
defaults = DEMO_DIRS if a.source == "synthetic" else REAL_DIRS
for key, folder in defaults.items():
    if getattr(a, key) is None:
        setattr(a, key, folder)
if a.source == "synthetic" and not a.allow_overwrite_real_data:
    clash = [f"--{k} {getattr(a, k)}" for k, real in REAL_DIRS.items()
             if Path(getattr(a, k)).resolve() == (ROOT / real).resolve()]
    if clash:
        ap.error(f"a synthetic run would write into the real business folders ({', '.join(clash)}). "
                 f"Use the defaults (data_demo/, outputs_demo/, models_demo/) or pass --allow-overwrite-real-data.")

T0 = time.time()
OUT = Path(a.out); OUT.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_colwidth", 90)
REPORT, FAILED = [], []


def say(title, obj=None):
    """Print as it comes out and keep it for outputs/backtest_report.md."""
    txt = obj.to_string() if isinstance(obj, (pd.DataFrame, pd.Series)) else ("" if obj is None else str(obj))
    print(f"\n== {title} ==\n{txt}" if title else txt, flush=True)
    REPORT.append((title, obj))


def md(obj):
    if not isinstance(obj, (pd.DataFrame, pd.Series)):
        return "" if obj is None else str(obj)
    df = obj.to_frame() if isinstance(obj, pd.Series) else obj
    df = df.reset_index() if not isinstance(df.index, pd.RangeIndex) else df
    def fmt(v):
        if isinstance(v, (float, np.floating)):
            return "" if np.isnan(v) else (f"{v:,.3f}" if abs(v) < 10 else f"{v:,.0f}")
        return str(v).replace("|", "/")
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


# ---------- load and holds ----------
if a.generate:
    print("synthetic data ->", synthetic.generate(a.data, allow_real=a.allow_overwrite_real_data))
elif a.ingest:
    print("== Ingest ==")
    ingest.run(a.raw, a.data)
hold = gaps.relabel_holds(a.data)
panel, products, _ = schema.load(a.data)
months = sorted(panel["month"].unique())
im_path = Path(a.data) / "item_map.csv"
item_map = pd.read_csv(im_path) if im_path.exists() else None
say("Data", f"{a.source}: {len(products)} products, {months[0]:%Y-%m} to {months[-1]:%Y-%m} ({len(months)} months); "
            f"baseline = average of last {a.baseline_months} months; decisions use data through M-{a.lead_gap + 1}")
say("1. Holds", f"{hold['relabelled']} product-months in {hold['runs']} suspected gaps relabelled as fda_hold "
                f"(hold_source = gap_detection) in sales.csv; {panel.loc[panel['status'] == 'fda_hold', 'product_id'].nunique()} "
                f"products affected; {int(((panel['status'] == 'fda_hold') & (panel['month'] == months[-1])).sum())} on hold in "
                f"{months[-1]:%Y-%m}. Hold months are filled (not zero demand) and not scored.")
residual = gaps.detect_unflagged_gaps(panel)
residual.to_csv(OUT / "suspected_gaps.csv", index=False)
say("", f"after recording the holds, gap detection finds {len(residual)} further runs (they appear only because the "
        f"holds are now excluded from each product's history; not confirmed by the owner) -> outputs/suspected_gaps.csv; "
        f"{'filled' if a.fill_gaps else 'not filled'} (--fill-gaps).")
say("", forecast.calendar_note())
clean = gaps.clean_demand(panel, residual if a.fill_gaps else None)
reg_groups = checks.regularity_groups(panel)

# ---------- spikes and profiles ----------
spk = spikes.detect(panel, clean, item_map, products)
spk.assign(month=spk["month"].dt.strftime("%Y-%m"))[
    ["product_id", "name", "month", "cases", "baseline", "ratio", "cause", "detail", "recurrence"]].to_csv(OUT / "spikes.csv", index=False)
prof = spikes.profiles(panel, clean, spk, products)
prof.to_csv(OUT / "product_profiles.csv", index=False)
say("2. Spikes by cause", spikes.summary(spk, panel, products))

# ---------- simulation backtest (headline) ----------
sim_months = [m for m in months if m >= pd.Timestamp(a.sim_from)]
n_ahead = max(len(pd.date_range(D, D + pd.Timedelta(days=policy.horizon_days(3, a.cover_months)), freq="MS"))
              for D in sim_months)                                              # enough for cushion 3
saved_paths = OUT / "sim_forecast_paths.csv"
if a.retest:
    a.skip_sim = False          # the re-test needs fresh simulation forecasts on the new data
if a.skip_sim and saved_paths.exists():
    paths = pd.read_csv(saved_paths, dtype={"product_id": str}, parse_dates=["decision", "month"])
    say("3. Simulation forecasts", f"reused {saved_paths} (--skip-sim): the simulation forecasts were not retrained")
else:
    print(f"\n== 3. Simulation: forecasting paths for {len(sim_months)} decisions ({n_ahead} months each) ==", flush=True)
    paths = simulate.forecast_paths(panel, products, sim_months, n_ahead, a.lead_gap, a.power, a.cap_one_off,
                                    item_map, a.baseline_months)
    paths.to_csv(saved_paths, index=False)
ff = paths[paths["flat_fill"]].groupby("decision")["month"].nunique()
if len(ff):
    say("3. Limitation", "model path held flat (too little history for the horizon) at decisions: "
        + ", ".join(f"{d:%Y-%m} ({n} months)" for d, n in ff.items()))
first_cut = pd.Timestamp(sim_months[0]) - pd.DateOffset(months=a.lead_gap)
win = panel[(panel["month"] < first_cut) & (panel["month"] >= first_cut - pd.DateOffset(months=a.baseline_months))]
eligible = sorted(set(win.loc[win["status"] == "sellable", "product_id"]))
p0 = paths[paths["decision"] == sim_months[0]]
eligible = [p for p in eligible if p in set(p0.loc[p0["model"].notna(), "product_id"])]
say("3. Simulation setup", f"{len(eligible)} products (had a sellable month in the {a.baseline_months} months before "
    f"{first_cut:%Y-%m}); months {sim_months[0]:%Y-%m}..{sim_months[-1]:%Y-%m}; first decision trains on data through "
    f"{first_cut - pd.DateOffset(months=1):%Y-%m}; lead times uniform 105-130 days, seed {a.seed}; lost sales, no backorders; "
    f"starting stock = averaging's reorder point for both methods.")

runs = {}
for c in (1.0, 2.0, 3.0):
    runs[c] = simulate.simulate(paths, panel, eligible, sim_months, c, a.cover_months, a.seed)
    runs[c] = runs[c].merge(reg_groups, on="product_id", how="left")
    runs[c]["kind"] = runs[c]["month"].map({m: forecast.month_kind(m) for m in sim_months})
main = runs[a.cushion_months] if a.cushion_months in runs else simulate.simulate(
    paths, panel, eligible, sim_months, a.cushion_months, a.cover_months, a.seed)
main.to_csv(OUT / "simulation_detail.csv", index=False)
# Until the first order can land (start + 130 days) both methods live on the same starting stock,
# so those months are identical by construction and are not scored.
warm_end = (pd.Timestamp(sim_months[0]) + pd.Timedelta(days=policy.LEAD_DAYS)).to_period("M").to_timestamp()
scored_months = [m for m in sim_months if m >= warm_end]
say("3. Scoring window", f"warm-up {sim_months[0]:%Y-%m}..{(warm_end - pd.DateOffset(months=1)):%Y-%m} not scored "
    f"(no order placed in the simulation can arrive yet); scored months {scored_months[0]:%Y-%m}..{scored_months[-1]:%Y-%m}. "
    f"Diwali-season months scored: " + (", ".join(f"{m:%Y-%m} ({forecast.month_kind(m)})" for m in scored_months
                                                 if forecast.month_kind(m) != "other") or "none"))


def sim_segments(r):
    w = r["month"] >= warm_end
    s = {"ALL": w, "pre-Diwali month": w & (r["kind"] == "pre-Diwali month"),
         "Diwali month": w & (r["kind"] == "Diwali month"), "all other months": w & (r["kind"] == "other")}
    s.update({f"regularity {lab}": w & (r["regularity"] == lab) for _, _, lab in checks.REG_BINS})
    return {k: v for k, v in s.items() if v.any()}


headline = simulate.summarize(main, sim_segments(main))
say(f"3. Simulation, cushion {a.cushion_months:g} months, cover {a.cover_months:g} months "
    f"(model vs 6-month averaging, same rule)", headline.round(2))
trade = pd.concat({c: simulate.summarize(r, {"ALL": r["month"] >= warm_end}).droplevel(0) for c, r in runs.items()},
                  names=["cushion_months", "method"]).swaplevel().sort_index()
say("3. Cushion tradeoff (scored months)", trade.round(2))
sm = simulate.stock_matched(trade)
say("3. Fill rate at similar stock (averaging interpolated between its cushion points)", sm.round(2))
valid = sm.dropna(subset=["difference_pts"])
TIE_PTS = 1.0   # fill-rate differences smaller than this at matched stock are called a tie
if len(valid):
    d = valid["difference_pts"]
    detail = (f"model minus averaging: {', '.join(f'{x:+.2f} pts' for x in d)} at cushion "
              f"{', '.join(f'{c:g}' for c in valid['cushion_months'])}")
    if (d.abs() < TIE_PTS).all():
        verdict = f"At similar average stock it is a TIE on fill rate ({detail}; all within {TIE_PTS:g} pt)."
    else:
        verdict = (f"At similar average stock the {'MODEL' if d.mean() > 0 else 'AVERAGING'} forecast gives the "
                   f"higher fill rate ({detail}).")
else:
    verdict = ("The model's stock levels fall outside averaging's range at every cushion, so there is no like-for-like "
               "point; compare the tradeoff table directly.")
say("3. Verdict", verdict)

# ---------- single-month accuracy (secondary) ----------
first = paths[paths["month"] == paths["decision"]][["product_id", "month", "model", "avg"]]
r1 = panel[panel["month"].isin(sim_months) & (panel["status"] == "sellable")][["product_id", "month", "units_sold"]]
r1 = r1[r1["product_id"].isin(eligible)].merge(first, on=["product_id", "month"])
r1 = backtest.annotate(r1, products, reg_groups)
segs1 = {"ALL": r1.index == r1.index, "pre-Diwali month": r1["kind"] == "pre-Diwali month",
         "Diwali month": r1["kind"] == "Diwali month", "all other months": r1["kind"] == "other"}
say("4. Secondary: single-month accuracy of the month-M forecast (data through M-5)",
    backtest.segment_table(r1, segs1, lambda g: {"avg": g["avg"], "model": g["model"]}).round(3))

# ---------- final models ----------
print(f"\n== 5. Training final direct models for horizons 1..{a.plan_months} ==", flush=True)
clean["spike"] = spikes.spike_flags(clean, spk)
final = clean.assign(target_cap=spikes.one_off_caps(clean, spk)) if a.cap_one_off else clean
models_by_h = {h: forecast.train(final, products, powers=[a.power], h=h) for h in range(1, a.plan_months + 1)}
saved = forecast.save_models(models_by_h, a.models, clean, products, a.power,
                             extra=dict(baseline_months=a.baseline_months, lead_gap=a.lead_gap, one_off_capped=a.cap_one_off,
                                        cushion_months=a.cushion_months, cover_months=a.cover_months,
                                        lead_days=policy.LEAD_DAYS, k_festival=plan.K_FESTIVAL, k_normal=plan.K_NORMAL))
say("5. Final model", f"trained on {clean['month'].min():%Y-%m}..{clean['month'].max():%Y-%m}; horizons 1..{a.plan_months}; "
                      f"tweedie power {a.power}; one-off spikes capped: {a.cap_one_off}; saved to {saved}/")

# ---------- plans ----------
today = pd.Timestamp(a.today) if a.today else pd.Timestamp.today().normalize()
fc = forecast.forecast_months(clean, products, models_by_h, driver=f"mean_{a.power}")
lines_path = Path(a.data) / "order_lines.csv"
if a.retest and a.source == "real" and lines_path.exists():
    print("\n== Re-testing the forecasters (inventory/tournament.py) ==", flush=True)
    res_t, tab_t = tournament.run(a.data, OUT, a.lead_gap, log=lambda m: print(m, flush=True))
    say("Forecaster re-test", tab_t.round(2))
choice = tournament.read_choice() if a.source == "real" else dict(name="ml", weight=None, best_ml="ml")
if choice["name"] != "ml":
    ff = tournament.final_forecast(choice, panel, products, fc, item_map,
                                   large_orders.load_lines(lines_path) if lines_path.exists() else None, a.plan_months, a.power,
                                   log=lambda m: None)
    lk = ff.set_index(["product_id", "month"])["likely"]
    fc["mean"] = [lk.get((p_, m_), 0.0) for p_, m_ in zip(fc["product_id"], fc["month"])]
    fc[f"mean_{a.power}"] = fc["mean"]
say("Forecaster", f"forecast used for ordering: {choice['name']}" + (f" (weight {choice['weight']:g} on {choice['best_ml']})"
    if choice["weight"] is not None else "") + " (config/forecaster.txt)")
if a.source == "real" and lines_path.exists():
    lo = large_orders.report(large_orders.load_lines(lines_path), products, brand_orders.B.assign(products))
    lo.to_csv(OUT / "large_orders.csv", index=False)
    say("Large orders", f"{len(lo)} large lines in the last 12 months -> outputs/large_orders.csv")
fc[["product_id", "month", "mean", "p90"]].rename(columns={"mean": "likely", "p90": "high"}).assign(
    month=fc["month"].dt.strftime("%Y-%m"), likely=fc["mean"].round(2), high=np.maximum(fc["p90"], fc["mean"]).round(2)
).to_csv(OUT / "forecast.csv", index=False)
op, inactive = plan.order_plan(panel, clean, products, fc, today, spikes=spk, profiles=prof)
op.to_csv(OUT / "order_plan.csv", index=False)
inactive.to_csv(OUT / "inactive_products.csv", index=False)
fr = plan.festival_readiness(op, clean, today)
fr.to_csv(OUT / "festival_readiness.csv", index=False)
last_status = panel[panel["month"] == months[-1]].set_index("product_id")["status"]
on_hold = last_status[last_status == "fda_hold"].index
first_hold = {}
for pid in on_hold:
    g = panel[panel["product_id"] == pid].sort_values("month")
    run_start = g["month"].iloc[-1]
    for m, st in zip(reversed(list(g["month"])), reversed(list(g["status"]))):
        if st != "fda_hold":
            break
        run_start = m
    first_hold[pid] = f"{run_start:%Y-%m}"
active = set(op["product_id"])
reasons = {(r.product_id, r.month): r.reason.split("; forecast")[0] for r in op.itertuples()}
fcl = fc[fc["product_id"].isin(active)][["product_id", "month", "mean"]].rename(columns={"mean": "forecast"})
rp = policy.reorder_points(fcl, products, sorted(fcl["month"].unique()), a.cushion_months, a.cover_months,
                           reasons=reasons, holds=first_hold)
rp.to_csv(OUT / "reorder_points.csv", index=False)
by_m = rp.groupby("month").agg(products=("product_id", "size"), forecast_cases=("forecast_cases", "sum"),
                               reorder_point=("reorder_point", "sum"), order_up_to=("order_up_to", "sum"))
say(f"6. Reorder points (gross; as of the 1st of each month; cushion {a.cushion_months:g}, cover {a.cover_months:g})", by_m.round(0))
n_flag = len(set(first_hold) & active)
say("", f"{len(rp)} rows -> outputs/reorder_points.csv; {len(first_hold)} products are on hold at {months[-1]:%Y-%m}: "
        f"{n_flag} are in the plan and flagged in the reason, {len(first_hold) - n_flag} have no sales in the last 6 months "
        f"and are in inactive_products.csv; order_plan.csv, festival_readiness.csv also written")
inv_path = Path(a.inventory)
if inv_path.exists() and a.source == "real":
    from inventory import stock_ingest
    inv = stock_ingest.run(inv_path, a.data, OUT, log=lambda m: say("", m))
    say("7. Inventory", f"data/stock.csv rebuilt from {inv_path.name}: columns found {list(inv['columns_found'])}")
use_stock = (Path(a.data) / "stock.csv").exists() if a.use_stock is None else a.use_stock
if a.use_stock and not (Path(a.data) / "stock.csv").exists():
    FAILED.append("--use-stock given but data/stock.csv does not exist (no stock data is created)")
    use_stock = False

say("Comparison table (headline, scored months)", headline.round(2))
say("Verdict", verdict)
bo, bl = brand_orders.run(a.data, OUT, today, int(a.cover_months), use_stock)
say("8. Brand orders", f"{len(bo)} brand orders ({bo['brand'].nunique()} brands), {len(bl)} lines -> "
    f"{OUT}/brand_orders.csv, brand_order_lines.csv, brands.csv; statuses: "
    + ", ".join(f"{k} {v}" for k, v in bo["status"].value_counts().items()))


def _row(t, seg, m):
    r = t.loc[(seg, m)]
    return {k: (None if pd.isna(v) else float(v)) for k, v in r.items()}


sim_json = dict(
    scored_from=f"{scored_months[0]:%Y-%m}", scored_to=f"{scored_months[-1]:%Y-%m}", products=len(eligible),
    cushion_months=a.cushion_months, cover_months=a.cover_months, lead_days_range=[105, 130], seed=a.seed,
    starting_stock="the averaging method's reorder point, the same for both methods",
    overall={m: _row(headline, "ALL", m) for m in ("model", "avg")},
    pre_diwali={m: _row(headline, "pre-Diwali month", m) for m in ("model", "avg")} if "pre-Diwali month" in headline.index.get_level_values(0) else None,
    tradeoff=[dict(method=m, cushion_months=float(c), **{k: float(v) for k, v in r.items()}) for (m, c), r in trade.iterrows()],
    stock_matched=[{k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in r.items()}
                   for r in sm.to_dict("records")], verdict=verdict)
(OUT / "simulation_summary.json").write_text(json.dumps(sim_json, indent=2, default=lambda x: None if pd.isna(x) else x))
say("Failed or not done", "\n".join(f"- {f}" for f in FAILED) or "nothing")
say("Runtime", f"{(time.time() - T0) / 60:.1f} minutes")
with open(OUT / "backtest_report.md", "w") as fh:
    fh.write("# Backtest and plan report\n\nGenerated by `run.py`; every figure comes from the code.\n")
    for title, obj in REPORT:
        fh.write(f"\n## {title}\n\n" if title else "\n")
        fh.write(md(obj) + "\n")
print(f"\nreport -> {OUT / 'backtest_report.md'}")
