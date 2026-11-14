# Shakti inventory agent (HackPrinceton F26)

Forecasts demand for every product, treats months a product **couldn't** be sold (FDA hold,
stockout, recall, unrecorded gaps) as missing instead of as zero demand, and turns forecasts
into a per-product monthly order plan with order-by dates (gross need; no stock netting yet). The AI agent,
iMessage (Photon) and dashboard layers come next and call into this core.

## Run it

```bash
pip install -r requirements.txt
python run.py --generate                                   # synthetic demo data -> data/
python run.py --source real --today 2026-11-14              # real data already ingested into data/
python run.py --source real --ingest --today 2026-11-14     # rebuild data/ from data/raw/sales/*.xlsx first
python -m pytest tests                                      # tests (fake data only)
```

Options: `--retest` (re-run the forecaster test and update the choice), `--baseline-months 6`, `--sim-from 2025-12`, `--cushion-months 2`, `--cover-months 2`,
`--seed 7`, `--power 1.5`, `--[no-]cap-one-off`, `--fill-gaps`, `--plan-months 12`, `--use-stock`.

The model learns each product's behaviour from its own sales first; festivals are not assumed to
cause spikes. **Diwali is the only festival used as a model feature** (`fest_diwali_this`,
`fest_diwali_next`, `days_to_diwali`, product Diwali lift). Every other row in
`config/festivals.csv` is tier 0: kept only to suggest a possible reason for a spike. Change the
`tier` column to change that.

What `run.py` does (all figures in `outputs/backtest_report.md` come from the code):

1. **Holds.** The owner confirmed the suspected unrecorded gaps are products on hold.
   `gaps.relabel_holds()` writes them into `data/sales.csv` as status `fda_hold` with
   `hold_source = gap_detection` (deterministic: detection always runs on the history without
   earlier gap-detection holds, so re-running changes nothing). Hold months are filled (not zero
   demand) and never scored. Gaps detected only after the holds are recorded are listed in
   `outputs/suspected_gaps.csv` but not filled unless `--fill-gaps`.
2. **Spikes** (`inventory/spikes.py`): spike = sales >= max(2 x baseline, baseline + 3 x MAD) and
   >= 10 cases (baseline = median of the other months in a centered 7-month window). Cause =
   first match of post_gap_rebound, code_switch, diwali, other_festival (possible link only),
   recurring_seasonal, one_off; plus "seen in N of M years". `outputs/spikes.csv`, `outputs/product_profiles.csv`.
3. **Ordering rule** (`inventory/policy.py`): reorder_point = forecast demand over the 130-day lead
   time + `--cushion-months` (default 3: owner's rule, keep at least 3 months of forward forecast
   sales on hand); order_up_to = reorder_point + `--cover-months` (default 2);
   order when on hand + on order < reorder_point, up to order_up_to, in whole cases. Months are
   30.4 days; demand inside a month is prorated by days.
4. **Simulation backtest (headline)** (`inventory/simulate.py`): month by month with stock carried
   over. At each month start each method forecasts the coming months from data through M-5
   (model: direct Tweedie mean models; averaging: mean of the 6 months up to M-5), the same rule
   places orders, each order arrives after a lead time drawn uniformly from 105-130 days (seeded,
   the same draws for both methods), demand is spread over the month's days and unmet demand is
   lost. Both start from the same stock (averaging's reorder point). Months before the first
   possible arrival are identical for both methods and are not scored. Reported: fill rate,
   product-months with a stockout, units short, average stock (cases, months of cover) overall,
   pre-Diwali/Diwali months and by regularity group; the tradeoff for cushions 1/2/3; and averaging's
   fill rate interpolated at the model's stock level.
5. **Secondary**: single-month bias/WAPE/short/over of the month-M forecast.
6. **Final direct models** for horizons 1..12 -> `models/lgbm_{mean,p50,p90}_hNN.txt` + `meta.json`.
7. **Outputs**: `outputs/reorder_points.csv` (product_id, name, month, forecast_cases,
   reorder_point, order_up_to, reason): the gross plan today. `outputs/order_plan.csv`,
   `outputs/festival_readiness.csv` (Diwali), `outputs/inactive_products.csv`.
   With `--use-stock` and a `data/stock.csv` (product_id, on_hand_units, held_units, on_order_units,
   expected_arrival), `policy.stock_status()` writes `outputs/stock_actions.csv`: "order now: N cases"
   or "OK until <date>". Held units never count. No stock data is created by this code.

The model learns each product's behaviour from its own sales; Diwali is the only festival used as
a model feature (`fest_diwali_this/next`, `days_to_diwali`, product Diwali lift). Other rows in
`config/festivals.csv` are tier 0: used only to suggest a possible reason for a spike.

Forecasts are direct: the horizon-h model predicts month T from data through T-h. An earlier
recursive version fed forecasts back in and compounded festival uplifts (one fold reached 15x
actual), so it was replaced.

## Dashboard (local only)

A local web page for the owner: what to order, how much and by when, grouped by brand. Nothing
leaves the computer: the API and the web app listen on 127.0.0.1 only and make no outside calls.

```bash
pip install -r requirements.txt            # adds fastapi, uvicorn
python run.py --source real --today 2026-11-14              # writes outputs/ (the dashboard only reads files)
./dashboard.sh                              # real data   -> open http://127.0.0.1:5173
./dashboard.sh demo                         # synthetic demo, after:
python run.py --generate --data data_demo --out outputs_demo --models models_demo --today 2026-11-14
```

`./dashboard.sh` starts the API (`api/main.py`, FastAPI on 127.0.0.1:8765; set `DASH_API_PORT` to change)
and the web app (`web/`, React + Vite on 127.0.0.1:5173), installing `web/node_modules` the first time
(needs Node 18+). `DASH_TODAY=YYYY-MM-DD` pins the date the page uses.

Pages:
1. **What to order**: one card per brand, sorted by order-by date, with status (Order now / Order this
   month / Coming up / Too late: expedite, always as text, not just color), cases, weight, products,
   and what months the order covers. Filter by status; search a brand or product.
2. **Brand**: the order as a table, one row per product with cases, weight and the reason; products
   on hold greyed and not counted; Export to CSV.
3. **Product**: 28 months of sales, the next 12 months (likely, and "could be as high as"), hold
   months shaded, spikes with their likely cause, Diwali dates, the reorder point in words.
4. **Diwali**: total sold per month with Diwali marked, the 2027 order window, top brands and products.
5. **Questions for you**: holds, possible gaps, big one-off orders, unmerged item codes, new products,
   biggest first; Yes / No / Not sure + note, saved to `data/owner_answers.csv`.
6. **How good is the forecast**: the replay result in plain words (it ties averaging at equal stock).

Brands and brand orders (written by `run.py`, or `python -m inventory.brand_orders --today ...`):
- `inventory/brands.py`: brand from the item name (known brands, then the first word, then the
  item-code prefix: the brand most items with that code have, else the code letters).
  `data/raw/brand_overrides.csv` (product_id, brand) wins; optional `data/raw/brands.csv`
  (brand, supplier, container_kg, notes) adds container estimates. -> `outputs/brands.csv`,
  `outputs/product_brands.csv`.
- `inventory/brand_orders.py`: one order per brand per 2 months of arrivals; order-by date = the
  earliest of its products; on-hold products listed but not counted; with `--use-stock` (and a
  `data/stock.csv`) it subtracts on hand + on order. -> `outputs/brand_orders.csv`,
  `outputs/brand_order_lines.csv` (stable columns: the iMessage agent reads them).

Checks: `python -m pytest tests` (includes every API endpoint on synthetic data);
`cd web && npm run build`; with the dashboard running, `cd web && npm run smoke` clicks through every
page in the locally installed Chrome (puppeteer-core, no browser download) at desktop and phone width.

## Which forecast feeds the ordering rule (chosen by data)

`python -m inventory.tournament` (or `python run.py --retest`, which also rebuilds the simulation
forecasts on the latest data; meant to be run monthly) compares these forecasters, every one using
data through M-5 for month M:

| Candidate | What it is |
| --- | --- |
| `avg` | average of the last 6 months (the business today) |
| `ml` | the LightGBM Tweedie model on all demand |
| `ml_regular` | the same model trained on regular demand, plus the product's typical large-order cases per month |
| `ml_simple` | a simpler model (7 leaves, 50 rows per leaf, L2 1.0) on the better of the two above |
| `seasonal` | same month last year x (last 6 months / same months a year earlier), capped 0.5-2x |
| `blend_w` | w x best model + (1 - w) x `avg`, w = 0.25, 0.5, 0.75 |
| `blend3` | equal mix of best model, `avg` and `seasonal` |

**Large orders** (`inventory/large_orders.py`): an invoice line larger than 3x the product's median line
and more than 20 cases, judged only from lines before the decision. Regular demand = sales without them;
the ordering rule adds back the product's average large-order cases per month (last 12 months), so stock
still covers bulk orders. `outputs/large_orders.csv` lists the last 12 months of them for the agent to
ask about upcoming bulk orders. (`data/order_lines.csv` keeps the invoice lines in current-code cases.)

**Fair test**: the carry-over simulation at cushions 1-4 (cover 2, lead time 105-130 days, seed 7, same
starting stock). Each candidate's fill rate is read at the same average stock (what `avg` holds at
cushion 3). The winner is picked on Apr-Jul 2026 and must also beat `avg` on Aug-Oct 2026, otherwise
`avg` is used. Results: `outputs/forecaster_tournament.{json,csv}`, curves in `forecaster_curves.csv`;
the choice is written to `config/forecaster.txt`, which `run.py` uses for the plan, reorder points,
brand orders and the dashboard. Expensive candidate forecasts are checkpointed in
`outputs/tournament_cache_*.csv` (delete them to rebuild).

## Stock, containers and harvest buys

When `data/raw/Inventory/CurrentInventory.xlsx` exists, `run.py` rebuilds `data/stock.csv` from it
(`python -m inventory.stock_ingest` does just that step) and orders against stock by default
(`--no-use-stock` to show gross need only).

- **Inventory** (`inventory/stock_ingest.py`): title block and two-row header found by scanning;
  columns recognised by name (on hand, available, on order, committed/held, expiry, CBM). The current
  export has **on hand only** (no on order, held, expiry or CBM), so stock already ordered or at sea
  is not included, and the dashboard says so. Codes are mapped through `data/item_map.csv` (OLD codes
  -> current products, unit conversion as for sales); unmatched codes -> `outputs/inventory_unmatched_codes.csv`.
  **Units**: the export counts single packs for most items, so quantities are divided by packs per case;
  item-code prefixes whose evidence says they count whole cases are taken as cases (`outputs/inventory_unit_evidence.csv`). Sanity check: months of cover = on hand /
  last-6-month sales (`outputs/stock_cover_check.csv`; over 36 months or negative are flagged).
- **Ordering with stock** (`inventory/stock_plan.py`): position = on hand + on order - held; reorder point
  = forecast over 130 days + 3 months (so 3 months of stock are on hand when an order lands); order-up-to
  = + 2 months. Per product: "order now: N cases", "OK until <date>", "on hold: confirm release", or
  "below 3-month floor before restock: expedite or air-ship" with the date it dips below
  (`outputs/product_status.csv`, projection with the floor in `outputs/stock_projection.csv`).
- **Order quantity (owner's rule)**: forecast sales for the 3 calendar months after the order arrives,
  minus the stock expected at arrival (on hand + on order - forecast until arrival, floored at 0); whole
  cases, floored at 0. Sales during the ~130 days of shipping are not added (the lead time only sets the
  dates). "Next order by" = arrival + 3 months - 130 days. A product that runs out before the order lands
  gets the note "runs out around <date> before this order arrives" (not added to the quantity).
  Container pull-forward never takes a product above 4 months of stock after arrival. The reorder point
  (130 days + 3 months) still decides WHEN to order; rice/harvest buys keep their own annual rule.
- **3-month floor**: at any date, the forecast of the next 3 months from that date (Diwali months count
  higher, not a flat average). Stock is projected week by week (on hand - held + on-order arrivals, on
  their date or after the 130-day lead time - forecast sales) until a new order could arrive; products
  that dip below the floor first are flagged. Brand cards count them; the product chart draws the floor. A brand order is triggered when any product is at its reorder point
  and includes every product of the brand that reaches it within 2 months.
- **Containers**: `config/containers.csv` (80 CBM, usable fraction, max kg when known). CBM per case:
  `data/raw/case_cbm.csv` > inventory file > ESTIMATED from case weight and `config/densities.csv`
  (marked "estimated" everywhere). Containers = max(CBM / usable CBM, kg / max kg). If the last container
  is under 70% full, the brand's next needs (earliest first) are pulled forward to fill it; needed
  quantities are never cut.
- **Harvest buys** (`config/harvest.csv`): confirmed rules only (rice: order in Jan, arrives Apr-May,
  12 months). One order a year: forecast from arrival to the next arrival + 3 months (still on hand at the
  next harvest arrival) minus projected
  stock at arrival. Months past the 12-month forecast reuse the same month a year earlier ("extended").
  Unconfirmed rows (mango pulp, chilli, turmeric, cumin) only appear as questions to the owner.

## Real data (NetSuite exports)

```bash
python run.py --ingest       # data/raw/sales/*.xlsx -> data/sales.csv, products.csv, item_map.csv, then the pipeline
```

`inventory/ingest.py` reads every "Custom Sales Orders by Item Summary" export in `data/raw/sales/`
(Date, Item, Description, Qty. Sold per invoice line) and:

- drops fee lines (pallet charges, unloading, discounts, `001` miscellaneous);
- **OLD codes** (`XX123 - OLD`, `XX123 -OLD`, `OLD-XX123`, any case/spacing) whose stripped code exists
  as a current code are the same product (owner's rule) and are always merged. History is converted
  into current cases by weight per case (`old_cases * old_packs / new_packs` when pack weight matches).
  TBD codes, and OLD codes with no current code, merge only when the pack numbers in the names agree;
- parses pack size from the name (`<weight> X <packs per case>`, `inventory/packs.py`) into
  `pack_weight, pack_unit, packs_per_case, case_weight_kg, parse_note` in `products.csv`;
  no unit = grams (noted), bulk bags with no count = 1 per case (noted). Liquids count 1 kg per litre;
- **bulk bags recorded in pounds**: many bulk codes (bags >= 20 lb, mostly OLD ones) record Qty Sold in
  lb, not bags (e.g. a line of 500 on a 50 lb bag means 10 bags). A code is treated as lb when
  80% of its lines are whole multiples of the bag weight, its median line is at least one bag weight, or
  its median line read as bags would exceed 20 t. Those quantities are divided by the bag weight.
  `LB_FIX = False` in `ingest.py` turns this off;
- sums to product x month in current-code cases (returns net out, floored at 0);
- marks months before a product's first sale `not_listed` (unknown, not zero demand);
- sets lead time to 130 days worst case (`lead_time_days`, used for order-by dates) and 105 typical
  (`lead_time_typical_days`) for every product, and guesses category from the name. Shelf life is
  left blank: no guessed value caps any quantity.
  Real values go in `data/raw/product_overrides.csv` (`product_id` plus any `products.csv` column) and win.

Audit files: `data/item_map.csv` (every raw code: target product, merge rule, conversion factor,
flags, whether qty was in lb) and `data/monthly_volume.csv` (reported qty, cases, current cases, kg).

Stock on hand is never read: the plan is gross need. Trained models load with `forecast.load_models`.

## Data contract

`schema.load` reads three CSVs from `data/` (never committed; see `.gitignore`):

| File | Columns |
| --- | --- |
| `sales.csv` | product_id, month (YYYY-MM), units_sold, status (optional: sellable, fda_hold, stockout, recall, not_listed) |
| `products.csv` | product_id, name, category, shelf_life_days, lead_time_days, unit_cost, primary_source, backup_source |
| `stock.csv` | product_id, on_hand_units, held_units, earliest_expiry (YYYY-MM-DD) |

Missing months for a product are treated as zero sales. If the export is weekly or per invoice,
aggregate to months first (or extend `schema.load` to weekly).

## Modules

| File | What it does |
| --- | --- |
| `inventory/ingest.py` | Raw NetSuite .xlsx exports -> data contract (cleaning, re-code merging, category guesses) |
| `inventory/schema.py` | Data contract, loading, full product x month grid |
| `inventory/synthetic.py` | Fake data with Diwali spikes, summer drinks, bulk orders, an FDA hold, a recorded stockout and an unrecorded gap |
| `inventory/gaps.py` | Flags unrecorded gaps; fills unsellable months from the prior year's pattern adjusted for trend |
| `inventory/packs.py` | Parses pack weight, unit and packs per case from item names |
| `inventory/forecast.py` | Direct LightGBM models per horizon: Tweedie mean, p50, p90; lags, rolling means, trend, Diwali features (raises past the calendar end), Diwali lift and spike rate built only from data through T-h; feature importance; save/load models; the averaging baseline |
| `inventory/spikes.py` | Spike detection, cause attribution, recurrence, product profiles, one-off caps |
| `inventory/brands.py`, `inventory/brand_orders.py` | Brand per product; one order per brand per 2 months with status and holds excluded |
| `api/main.py`, `web/`, `dashboard.sh` | Local dashboard (FastAPI + React), see "Dashboard" |
| `inventory/stock_ingest.py`, `inventory/stock_plan.py` | Inventory export -> stock.csv; status, projection, containers, harvest buys |
| `inventory/policy.py` | Reorder point / order-up-to rule, day-prorated demand, `reorder_points()`, `stock_status()` for when stock data exists |
| `inventory/simulate.py` | Carry-over simulation of the rule with model vs averaging forecasts, seeded lead times, summaries |
| `inventory/backtest.py` | 4-month-gap backtest folds (variants A/B), bias/WAPE/short/over metrics, k sweep, matched comparison, fairness check, spikes seen coming |
| `inventory/checks.py` | Regularity, suffix patterns, lb-switch ratios |
| `inventory/plan.py` | `order_plan()` and `festival_readiness()`: gross cases per product per month, order-by dates, reasons from each product's profile |
| `inventory/orders.py` | Lump-sum order with stock netting, for when a stock export exists (not used by `run.py`); shelf-life cap applies only when a real shelf life is given |
| `run.py` | Runs everything and saves CSVs to `outputs/`, models to `models/` |

## Known limits (be honest in the pitch)

- See `outputs/backtest_report.md` for where the model wins and loses against averaging.
- The simulation can only score months after the first order lands (~4 months in), so the Diwali month itself (Oct 2025) falls in the warm-up; the pre-Diwali month Oct 2026 is scored.
- Lost sales are assumed (no backorders); a single lead time per order, uniform 105-130 days.
- Tier-0 festival windows touch most months, so "other_festival" absorbs spikes before "recurring_seasonal"/"one_off" can; it is a possible link, not a cause.
- Bulk lb detection and OLD->current conversions need the owner's confirmation (see the report's step 1).

## Next

Agent layer (deepagents) with tools over `forecast`, `orders` and `gaps`; Photon iMessage;
dashboard; time-travel demo; supplier-risk research (stretch).
