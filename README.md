# Shakti inventory agent (HackPrinceton F26)

Forecasts demand for every product, treats months a product **couldn't** be sold (FDA hold,
stockout, recall, unrecorded gaps) as missing instead of as zero demand, and turns forecasts
into order recommendations that respect lead times, shelf life and held stock. The AI agent,
iMessage (Photon) and dashboard layers come next and call into this core.

## Run it

```bash
pip install -r requirements.txt
python run.py --generate     # synthetic data shaped like Shakti's -> data/
python run.py                # gaps -> backtest -> recommendations -> outputs/
python run.py --test-months 12
```

## Swap in the real data

Put three CSVs in `data/` (never committed; see `.gitignore`):

| File | Columns |
| --- | --- |
| `sales.csv` | product_id, month (YYYY-MM), units_sold, status (optional: sellable, fda_hold, stockout, recall) |
| `products.csv` | product_id, name, category, shelf_life_days, lead_time_days, unit_cost, primary_source, backup_source |
| `stock.csv` | product_id, on_hand_units, held_units, earliest_expiry (YYYY-MM-DD) |

Missing months for a product are treated as zero sales. If the export is weekly or per invoice,
aggregate to months first (or extend `schema.load` to weekly).

## Modules

| File | What it does |
| --- | --- |
| `inventory/schema.py` | Data contract, loading, full product x month grid |
| `inventory/synthetic.py` | Fake data with Diwali spikes, summer drinks, bulk orders, an FDA hold, a recorded stockout and an unrecorded gap |
| `inventory/gaps.py` | Flags unrecorded gaps; fills unsellable months from the prior year's pattern adjusted for trend |
| `inventory/forecast.py` | One LightGBM model across products (p50 and p90), moving festivals (Diwali, Holi, Dussehra, both Eids; edit `FESTIVALS`) plus Nov/Dec flags; the "average of last 6 months" baseline |
| `inventory/backtest.py` | Train only on the past, forecast each of the last N months, compare with averaging (WAPE, overstock $, missed sales $) |
| `inventory/orders.py` | Order = demand over lead time + 1 month + safety - sellable stock, capped by shelf life; held stock excluded; reasons for every number |
| `run.py` | Runs everything and saves CSVs to `outputs/` |

## Known limits (be honest in the pitch)

- On synthetic data the model beats averaging only modestly on overall error and cuts overstock,
  but it misses more sales (the median forecast runs low). Tune on the real data before quoting numbers.
- The backtest is a single-month simulation: no stock carried between months.
- `stock.csv` has one expiry date per product, so expiry risk is a conservative estimate.

## Next

Agent layer (deepagents) with tools over `forecast`, `orders` and `gaps`; Photon iMessage;
dashboard; time-travel demo; supplier-risk research (stretch).
