# Engine issues and proposals

For Nakul. Status as of 2026-11-14. Two small fixes are already in (approved by Dhruv). Two
proposals need your approval before anything changes in the engine. The last section lists README
text that is out of date.

| # | Issue | Status |
|---|---|---|
| 1 | Weights like "(400 Gram)" make a product a dal; AR202 Basmati Rice misses the rice harvest rule | **Proposal below: needs your OK** |
| 2 | `owner_answers.csv` is written by the dashboard but the engine never reads it | **Proposal below: needs your OK** |
| 3 | `brand_orders.csv` had two columns named `containers` | Fixed |
| 4 | `run.py --generate` wrote synthetic data into the real `data/` | Fixed |
| 5 | `run.py` rewrites `data/sales.csv`; `--retest` rewrites tracked `config/forecaster.txt` | Documented in README (no code change) |

---

## Fixed

### #4: synthetic runs can no longer overwrite the real data

- `run.py --generate` (and `--source synthetic`) now defaults to `data_demo/`, `outputs_demo/`,
  `models_demo/`. `--data`, `--out` and `--models` default to `None` and are filled in from the source.
- A synthetic run that would write into `data/`, `outputs/` or `models/` stops with an argparse error
  unless `--allow-overwrite-real-data` is passed.
- `synthetic.generate()` now defaults to `out_dir="data_demo"` and raises if pointed at the real `data/`
  (unless `allow_real=True`), so direct callers are covered too.
- Real runs are unchanged: `python run.py --source real ...` still uses `data/`, `outputs/`, `models/`.

### #3: `containers` column appeared twice

`ORDER_COLUMNS` and `STOCK_ORDER_EXTRA` both contained `containers`, so pandas read the second as
`containers.1`. Both held the same value (`summary["containers"]` = max(CBM / usable CBM, kg / max kg)).

- The second one is now `containers_by_volume` = CBM / usable CBM only (weight ignored). It equals
  `containers` while `config/containers.csv` has no `max_kg`; once a weight limit is set, `containers` can be
  larger and `container_bound` says why.
- `containers` itself is unchanged, so the API (`stock_flags`: `orders_now_container_volume`) and the
  dashboard read the same numbers as before.
- Verified on the real data: re-running `python -m inventory.brand_orders --today 2026-11-14` gives a
  `brand_orders.csv` identical to the previous one apart from the renamed column; `brand_order_lines.csv`,
  `product_status.csv` and `harvest_orders.csv` are byte-for-byte identical.

Tests: `tests/test_safety_and_columns.py` (unique column names; `containers_by_volume` with and without a
weight limit, via the first test of `build_with_stock`; CSV round trip; generate/run refuse the real folders).

---

## Proposal #1: weights must not decide the category

### The problem

`ingest.CATEGORIES` has `"gram"` in the `lentils_beans` keywords (for Horse Gram, Bengal Gram...). The
keyword regex allows a plural, so it also matches weight units: "(400 Gram)", "908 Grams". `lentils_beans`
comes before `nuts_dry_fruits`, `rice_flour` and most others, so any product with a weight written in grams
and no earlier keyword becomes a dal.

Category is used in four places, so this isn't cosmetic:
1. **Harvest rules**: `config/harvest.csv` rice rule requires `category = rice_flour`. **AR202 Aara Basmati Rice
   2lb (908 Gram) is not treated as an annual rice buy** (missing from `harvest_orders.csv`).
2. **Estimated CBM** (`config/densities.csv`): 650 kg/CBM for dals vs 450 for nuts, 400 for other.
3. **Model feature** `cat_code`, and the category-average Diwali lift used when a product has no history.
4. Display (`festival_readiness.csv`, product page).

### Proposed change (recommended: strip weights before matching)

Remove weight expressions (`<number> <weight/volume unit>`) from the name before the category regexes run.
Keywords stay as they are, so real gram dals still match "gram" ("Horse Gram", "Gram Flour",
"Roasted Split Gram"), and no other keyword can collide with a unit in future.

```python
# inventory/ingest.py
WEIGHT = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?\s*(?:kgs?|kilos?|kilograms?|grams?|grms?|gms?|gr|g|lbs?|pounds?|"
                    r"ounces?|oz|ml|l|ltrs?|litres?|liters?)\b\.?", re.IGNORECASE)

def categorize(desc):
    text = WEIGHT.sub(" ", desc)       # "(400 Gram)" is a weight, not the dal "gram"
    for cat, shelf, lead, rx in _CAT_RX:
        if rx.search(text):
            return cat, shelf, lead
    return DEFAULT
```

The alternative (drop the bare keyword `gram`, add named grams: horse/bengal/green/black/red/split/roasted
gram, gram flour, gram dal) gives **exactly the same result on today's data** (checked), but needs the list
kept up to date, and it also changes `brands._commodity_words()`, which reads the same keyword list.

### Every product whose category changes (real `data/products.csv`, 2,916 products)

| product_id | name | before | after |
|---|---|---|---|
| AN1406 | Aara Coconut Powder 14 Oz (400 Gram) X 20 (Premium Quality) | lentils_beans | nuts_dry_fruits |
| AN1407 | Aara Coconut Powder Medium 14 Oz (400 Gram) X 20 (Premium Quality) | lentils_beans | nuts_dry_fruits |
| AN1410 | Aara Green Raisin 14 OZ (400 Gram) X 20 X | lentils_beans | nuts_dry_fruits |
| AN2807 | Aara Green Raisin 28 OZ (800 Gram) X 10 X | lentils_beans | nuts_dry_fruits |
| AN709 | Aara Green Raisin 7 OZ (200 Gram) X 20 X | lentils_beans | nuts_dry_fruits |
| AN1412 | Aara Pistachio Unshelled 14 OZ (400 Gram) X 20 X | lentils_beans | other |
| AP013 | Aara Corn Poha 400 Gram (14 OZ) X 10 | lentils_beans | rice_flour |
| AP014 | Aara Corn Poha 800 Gram (28 OZ) X 5 X | lentils_beans | rice_flour |
| AR202 | Aara Basmati Rice 2lb (908 Gram) X 20 X | lentils_beans | rice_flour |
| AS750 | Aara Panchpuran 7 Oz (200 Gram) X 20 Packets | lentils_beans | other |
| HF505 | Haldiram Vada Pav 256 Gram X 20 PKT | lentils_beans | other |

11 products change; nothing moves *into* `lentils_beans`. Products that correctly stay dals because "gram"
is part of the name, not a weight: Aara Besan (Gram Flour) 2 lb and 4 lb, Aara Dalia (Roasted Split Gram)
14/28/56 oz, and the other real gram dals.

**Downstream effects after re-categorizing and re-running:**
- **AR202 joins the confirmed rice harvest rule** (ordered once a year in January) instead of the regular
  reorder rule. This is the one change that moves an order. No other product enters or leaves a harvest rule.
- Estimated CBM per case rises for the 5 nuts products (650 to 450 kg/CBM, +44% volume) and the 3 "other"
  products (650 to 400, +63%); unchanged for the 3 rice_flour ones. Container counts move slightly for those brands.
- `cat_code` changes for these 11 products, and the dal/nuts category Diwali-lift fallback is recomputed,
  so forecasts shift slightly once the models are retrained. The category set itself is unchanged, so other
  products' `cat_code` values stay the same.

**Side findings (not part of this fix; separate keyword gaps, your call):** "Pistachio" doesn't match the
keyword "pista" (whole-word match), so AN1412 lands in `other`; Panchpuran is a spice blend; Vada Pav is a
frozen snack. AR201/AR406 Khichdi Mix and AR211/AR411 Kanki already sit outside `rice_flour`, which keeps
them out of the rice harvest rule (probably right for khichdi mix; kanki is broken rice, so worth asking the owner).

### Applying it (after approval)

Categories are written into `products.csv` at ingest time, so after the code change run either
`python run.py --source real --ingest --today ...` or `python -c "from inventory import ingest; print(ingest.recategorize())"`
followed by `python run.py --source real --today ...` (retrains and rebuilds the orders).

### Tests to add (`tests/test_brands.py::test_category_whole_words` style, fake names only)

```python
@pytest.mark.parametrize("name, cat", [
    ("Aara Green Raisin 14 OZ (400 Gram) X 20 X", "nuts_dry_fruits"),     # weight in grams is not a dal
    ("Aara Basmati Rice 2lb (908 Gram) X 20 X", "rice_flour"),
    ("Aara Corn Poha 800 Gram (28 OZ) X 5 X", "rice_flour"),
    ("Brand Vada Pav 256 Grams X 20 PKT", "other"),                         # plural unit too
    ("Brand Coconut Powder 1.5 grams X 10", "nuts_dry_fruits"),            # decimals
    ("Aara Horse Gram 2 lb X 20", "lentils_beans"),                         # named grams stay dals
    ("Brand Bengal Gram Whole 4 lb X 10", "lentils_beans"),
    ("Brand Green Gram Split 908 Gm X 20", "lentils_beans"),
    ("Aara Besan (Gram Flour) - 2 Lbs (908 Gm) X 20 Packets", "lentils_beans"),
    ("Aara Dalia (Roasted Split Gram) - 28 Oz (800 Gm) X 10", "lentils_beans"),
])
def test_weights_do_not_set_category(name, cat):
    assert ingest.categorize(name)[0] == cat


def test_basmati_rice_joins_rice_harvest_rule():
    products = pd.DataFrame({"product_id": ["R1"], "name": ["Aara Basmati Rice 2lb (908 Gram) X 20 X"]})
    products["category"] = products["name"].map(lambda n: ingest.categorize(n)[0])
    confirmed, _ = SP.harvest_members(products, SP.harvest_rules())
    assert "R1" in confirmed and confirmed["R1"]["rule_id"] == "rice"
```

---

## Proposal #2: the engine reads `owner_answers.csv` on every run

### Today

The dashboard's "Questions for you" page appends answers to `data/owner_answers.csv`
(`question_id, answer, note, timestamp`; answer is `yes`, `no` or `not sure`; the latest answer per question
wins). Nothing in `inventory/` or `run.py` reads it. Answering "Yes, released" on a hold doesn't lift the
hold, and there is no input at all for releasing one: holds come from `sales.csv` status (recorded, or
written by `gaps.relabel_holds()`), and `brand_orders.current_holds()` takes the last data month's status.

### Principles

1. **Never edit source files.** Answers are applied in memory on each run. `sales.csv`, `item_map.csv`,
   `config/*.csv` stay as they are, so removing or changing an answer undoes its effect on the next run.
2. **One place.** A new `inventory/answers.py`: `load(data)` returns the latest answer per question;
   `apply_*` functions return overrides that `run.py` passes to existing functions at the points listed below.
3. **Audit.** Each run writes `outputs/answers_applied.csv`: question_id, answer, note, timestamp, what the
   engine did, and `status` (`applied`, `no change`, `needs ingest`, `note not understood`).
4. **Explicit files win.** Hand-written override files (`data/raw/brand_overrides.csv`,
   `product_overrides.csv`, `case_cbm.csv`) take precedence over answers; answers take precedence over defaults.
5. **"Not sure" never changes anything.** The question stays open (and keeps its place by impact).
6. **Free text is parsed strictly.** If a note is needed (a code, a size, months) and can't be parsed, nothing
   changes, the status says `note not understood`, and the question is asked again.

### What each question type does

| Question id | Question (dashboard wording) | Yes | No | Not sure |
|---|---|---|---|---|
| `hold:PID` | Is X released from hold? | **Release the hold** from the answer date: PID leaves the current-holds set, gets a normal status and is included (and counted) in brand orders. Past hold months stay unavailable (filled, not zero demand). `relabel_holds` must not re-hold it for months after the release. | Stays on hold (today's behaviour); question stays answered until a new hold run starts. | No change. |
| `gap:PID:YYYY-MM` | Was X unavailable start to end? | Treat those months as unavailable (status `fda_hold`, source `owner`): filled from the usual pattern, not scored. Same as `--fill-gaps` for that run only. | They were real zero-sales months: keep as demand, and drop the run from `suspected_gaps.csv` so it isn't asked again. | No change (not filled, as today). |
| `oneoff:PID:YYYY-MM` | Was the sale a one-off (promotion or bulk order)? | Confirms today's behaviour: the spike stays capped in training (`one_off_caps`). Stop asking. | It's real demand: don't cap that month, and stop asking. | No change (capped, as today). |
| `code:RAW` (same product) | Is code RAW the same product as BASE? | Merge RAW into BASE at the next ingest (rule 1 conversion by weight per case). Needs `--ingest`. | Keep separate (today's behaviour); stop asking. | No change. |
| `code:RAW` (no current code) | Does RAW sell under another code now, or is it discontinued? | Note must contain the new code: merge into it at the next ingest. No parseable code means `note not understood`. | Discontinued: drop from the plan and brand orders (like inactive products). | No change. |
| `new:PID` | Will you keep selling X? | Keep (today's behaviour). Optional: a note like "about 20 a month" becomes its monthly forecast (your call). | Discontinued: drop from the plan and brand orders. | No change. |
| `stock:on_order` | Is stock already ordered or at sea missing from the plan? | Keep the warning and make it louder on "Order now" orders: the plan over-orders until an export with on-order arrives. | Nothing is on the way: on-order = 0 is confirmed, so remove the warning. | No change (warning stays). |
| `stock:units` | Does the inventory count single packs for most items (cases for some prefixes)? | Confirms `INVENTORY_UNIT = "packs"` and the per-prefix exceptions. | **No automatic change** (every order would move): flag in the report and on the dashboard until the unit is confirmed by hand. | No change. |
| `units:PID` | Is the stock of X right: N cases? | Accept; stop flagging it in `stock_cover_check`. | Treat its stock as unknown: order on gross need for that product and flag it. If the note is a number, use it as on-hand cases. | No change. |
| `invcode:RAW` | Inventory item RAW has no sales: other code, or packaging? | Note must contain the product code: map its on-hand stock to that product. | Packaging/material: ignore permanently and drop from `inventory_unmatched_codes.csv`. | No change. |
| `cbm:PID` | What is the case size (CBM or L x W x H)? | The note carries the size, whatever the answer: "0.045", "0.045 cbm", "40x30x25 cm", "16x12x10 in" become `cbm_per_case` (source `owner`), ahead of the weight estimate. | Same: the note is what matters. | No change. |
| `container:limits` | Is 80 CBM right, and what is the weight limit? | Keep 80 CBM; a kg number in the note becomes `max_kg`. | Numbers in the note replace `container_cbm` and/or `max_kg` (CBM vs kg told apart by unit). | No change. |
| `harvest:RULE` | Is RULE bought once a year? Which months? | Confirm the rule only if the note gives order and arrival months ("order Jan, arrives Apr-May"); otherwise `note not understood`. | Not an annual buy: remove the candidate; its products stay on the regular rule and the question stops. | No change. |

### Where it hooks in

- `run.py`, after `gaps.relabel_holds()` and `schema.load()`: hold releases and confirmed gaps adjust
  `panel` statuses in memory (before `clean_demand`, spikes and training).
- `spikes.one_off_caps()`: skip months answered "no".
- After `plan.order_plan()`: drop products answered "discontinued".
- `stock_ingest.run()`: units corrections and inventory-code mappings.
- `brand_orders.run()` / `build_with_stock()`: current holds minus releases, CBM overrides, container
  config, harvest rule overrides.
- `ingest.build_item_map()`: code merges (`status = needs ingest` until `--ingest` runs).

**API note:** `api/main.py` computes holds from `sales.csv` itself (`holds_now()`), so a released hold
would still show as "on hold" on the dashboard. The clean fix is for the engine to write the effective
holds (e.g. `outputs/holds_now.csv`) and the API to read it. That touches the API too, so it's your call.

### Tests (synthetic data)

One test per question type, each on the demo panel: write an `owner_answers.csv` with a yes / no / not sure
row, run the relevant `apply_*`, and check the effect and the `answers_applied.csv` status. Plus: the latest
answer wins; deleting an answer undoes its effect; "not sure" never changes outputs; unparseable notes change nothing.

---

## #5: files a run changes (documented in README)

- Every `run.py` run rewrites `data/sales.csv` via `gaps.relabel_holds()` (deterministic, but rewritten).
- `--retest` rewrites `config/forecaster.txt`, which is tracked in git.
- `--ingest` rebuilds `data/`; the inventory export rebuilds `data/stock.csv`; `outputs/` and `models/` are
  overwritten every run.

---

## README text that is out of date (to fix later)

- **Intro**: "gross need; no stock netting yet" and "dashboard layers come next": stock netting and the
  dashboard both exist.
- **Step 3 (Ordering rule)**: says orders top up to `order_up_to`. Real orders use the owner's rule
  (3 months after arrival minus stock at arrival); `order_up_to` is only what the simulation uses.
- **Step 7 (Outputs)**: describes `--use-stock` writing `stock_actions.csv` via `policy.stock_status()`, and
  "No stock data is created by this code". In fact stock mode is on by default when `data/stock.csv` exists,
  writes `product_status.csv`, `stock_projection.csv`, `harvest_orders.csv` etc., and `stock_ingest` creates `data/stock.csv`.
- **Brand orders**: only the gross mode is described (one order per brand per 2 months). The stock mode
  (reorder-point trigger, products reaching it within 61 days, owner's quantity, container pull-forward,
  harvest orders as separate brand orders) is missing, as are the different order id formats:
  `Brand|YYYY-MM` (gross), `Brand|YYYY-MM-DD` (stock), `Brand|harvest-YYYY-MM`.
- **Real data section**: "Stock on hand is never read: the plan is gross need."
- **Data contract**: lists `stock.csv` as `on_hand_units, held_units, earliest_expiry`; the real file is
  `on_hand_cases, on_order_cases, on_order_arrival, held_cases, earliest_expiry, cbm_per_case, raw_codes, unit_note`.
  The `inventory/schema.py` docstring has the same old columns.
- **Dashboard page 6**: "it ties averaging at equal stock". The tournament now picks `blend_0.5`, which beat
  averaging on Aug-Oct.
- **Duplicates / leftovers**: the festivals paragraph appears twice; "Next" still lists the dashboard.
- **Mismatch**: `plan.py` docstring says the iMessage agent reads `order_plan.csv`; the README says
  `brand_order_lines.csv` (the agent will read the brand orders and product status).
- **`.env.example`** doesn't match the code: `BEDROCK_MODEL_ID`, `AWS_REGION`, `PHOTON_TEST_SENDER`,
  `PHOTON_AGENT_HANDLE` are missing; `LLM_API_KEY`, `LLM_MODEL` are unused (the agent will use
  `OPENROUTER_API_KEY` and `LLM_MODEL`).

Other things worth knowing:
- `order_plan.csv`, `reorder_points.csv` and `festival_readiness.csv` are gross need even in stock mode.
- `data/order_lines.csv` has no customer column, so `large_orders.csv` can't say who placed a bulk order.
- `product_id` is not always a clean code: unmerged OLD codes (`AR413 - OLD`) and free-text TBD names
  (`New Brown and polson Powder 100g x 96ct TBD`) are product ids; 2 products have blank names.
- `outputs/forecaster_tournament.json` isn't present unless `--retest` has been run on that machine, so the
  forecast-quality page shows no tournament there.
