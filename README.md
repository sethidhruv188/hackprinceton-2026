# Shakti Group Ordering

**An AI-run ordering desk for an import and distribution business.** Built at HackPrinceton F26 on real sales history.

Shakti Group USA imports Indian groceries by sea. An order takes 105 to 130 days to arrive, sales swing with Diwali and
harvest seasons, and a late order means empty shelves for months. Shakti Group Ordering forecasts demand for every
product, decides what to order, how much and by when, and explains every recommendation. A buyer can work through it
on a local dashboard, by asking an AI agent, or by text message.

Every number shown comes from the engine's output. The agent recommends; the owner decides. Nothing is ever ordered,
sent or contacted automatically.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [The dashboard](#the-dashboard)
- [The AI agent](#the-ai-agent)
- [iMessage](#imessage-photon)
- [Owner-taught demand adjustments](#owner-taught-demand-adjustments)
- [The forecasting and ordering engine](#the-forecasting-and-ordering-engine)
- [Owner answers](#owner-answers)
- [Real data (NetSuite exports)](#real-data-netsuite-exports)
- [Privacy and safety](#privacy-and-safety)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Known limits](#known-limits)
- [Credits](#credits)

---

## What it does

| Capability | In one line |
| --- | --- |
| **Demand forecasting** | LightGBM models per product, trained on real NetSuite history; months a product could not be sold (FDA hold, stockout, gaps) are treated as missing, not as zero demand. |
| **Forecaster tournament** | The forecast that feeds ordering is chosen by data: candidates are replayed month by month against the business's own 6-month average, at equal stock. |
| **Ordering plan** | One order per brand, with order-by dates (safe at 130 days, latest at 105), cases, weight, CBM and containers, ordered against stock on hand. |
| **Risk detection** | Products below the 3-month stock floor now or before a restock, products that run out before their order lands, orders already too late for sea freight. |
| **Seasons** | Diwali as a modelled demand driver; harvest-season buying for dals, rice, nuts and dry fruits from a cited harvest calendar. |
| **Sourcing intelligence** | For at-risk products: other producing countries, labelled supplier leads, country alerts and what else the same container could carry. |
| **AI agent** | LangChain Deep Agents with tools over the engine's outputs, sub-agents for planning, explaining and research, memory, and owner/guest roles. |
| **One conversation, two channels** | The owner talks to the same agent on the dashboard and by iMessage; the history is shared. |
| **Owner-taught adjustments** | "Expect 30% more moong in December, we're running a promo" becomes a structured adjustment, read back, applied only after the owner confirms. |
| **Control-room dashboard** | Local web app with a live 3D agent, a shipping timeline, a 12-month look-ahead and printable draft purchase orders. |

---

## Architecture

```
NetSuite exports (.xlsx) ──► inventory/ingest.py ──► data/            (sales, products, stock; never committed)
                                                       │
                                                       ▼
                                     run.py: forecast · tournament · plan · brand orders · harvest · sourcing
                                                       │
                                                       ▼
                                                   outputs/            (CSV/JSON the rest of the system reads)
                                                       │
                ┌──────────────────────────────────────┼──────────────────────────────────────┐
                ▼                                      ▼                                      ▼
   api/ (FastAPI, 127.0.0.1)              agent/ (LangChain Deep Agents)            imessage/ (Photon 2.0)
                │                                      ▲                                      │
                ▼                                      │                                      │
   web/ (React + Vite dashboard) ───── Ask page ───────┴────── one owner conversation ────────┘
```

- **Engine** (`inventory/`, `run.py`): Python, pandas, LightGBM. Writes everything to `outputs/`.
- **API** (`api/`): FastAPI, reads `outputs/` and `data/`, listens on 127.0.0.1 only.
- **Dashboard** (`web/`): React 18, Vite, Recharts, Three.js / React Three Fiber, Lucide icons.
- **Agent** (`agent/`): LangChain Deep Agents (deepagents 0.7.38), LangGraph SQLite checkpointer, OpenRouter models,
  optional Tavily search and LangSmith tracing.
- **iMessage** (`imessage/`): Photon 2.0 bot on top of the same agent.

---

## Quick start

**Requirements:** Python 3.12, Node 18+, and PowerShell 7 (`pwsh`) on macOS/Linux to use `dashboard.ps1`.

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # OPENROUTER_API_KEY, LLM_MODEL, PHOTON_* (see below)

# 2. Build the plan from real data (data/raw/sales/*.xlsx)
python run.py --source real --ingest --today 2026-11-14

# 3. Start everything: API, dashboard, AI agent and iMessage bot
pwsh ./dashboard.ps1                                    # Windows: .\dashboard.ps1
```

Open **http://127.0.0.1:5173**. Press Ctrl+C to stop everything.

`dashboard.ps1` works on Windows and macOS. It installs web packages when they change and starts the API, the web app
and the iMessage bot (set `DASH_NO_IMESSAGE=1` to skip the bot). `./dashboard.sh` starts the API and web app only.

### Synthetic data (development and tests)

```bash
python run.py --generate --demo-harvest --today 2026-11-14   # writes data_demo/, outputs_demo/, models_demo/
pwsh ./dashboard.ps1 demo
```

Synthetic runs never touch the real folders. The dashboard itself always shows real data; the synthetic set exists
for development and for the test suite.

### Useful `run.py` options

| Option | Effect |
| --- | --- |
| `--ingest` | Rebuild `data/` from `data/raw/sales/*.xlsx` before planning |
| `--retest` | Re-run the forecaster tournament and update `config/forecaster.txt` (monthly) |
| `--use-stock` / `--no-use-stock` | Order against `data/stock.csv` (default when it exists) or show gross need |
| `--today YYYY-MM-DD` | Plan date |
| `--cushion-months`, `--cover-months` | Ordering rule parameters (defaults 3 and 2) |
| `--baseline-months`, `--plan-months`, `--seed`, `--power`, `--[no-]cap-one-off`, `--fill-gaps`, `--sim-from` | Forecast and backtest settings |

What each run changes: `outputs/` and `models/` are rebuilt; `data/sales.csv` is rewritten deterministically (suspected
gaps are labelled as holds); `--ingest` rebuilds `data/`; `--retest` rewrites the tracked `config/forecaster.txt`
(review `git diff config/forecaster.txt` before committing it).

---

## The dashboard

A local web app for the buyer. The API and web app listen on 127.0.0.1 only and make no outside calls (the agent's
model and optional web search are the only network traffic, and only when the agent is asked something).

### The agent at the centre

A 3D robot modelled in code with Three.js (brushed-metal shell, glass visor, emissive lights, studio lighting; no
third-party model). Its state is driven by the app and always shown in words as well:

| State | When |
| --- | --- |
| Ready | Idle; breathes and glances around |
| Listening | The user is typing to it |
| Thinking | A request to the agent is pending |
| Speaking | A reply or the briefing is being revealed |
| Needs your attention | Orders are due now, an order is too late, or questions are waiting |
| Offline | The model isn't configured or the API is unreachable; Ask is disabled with the reason |

The head follows the cursor and clicking the robot opens Ask. On Overview it gives a briefing built only from the data
("4 brand orders to place now, 3,739 cases. Most urgent: Sunrise…") with brand names as links; voice is optional and off
by default. On every other page a docked robot opens a compact chat. When a reply names brands, those rows are
highlighted on What to order and Brands. The 3D model is lazy-loaded, renders only while visible, holds still under
"reduce motion", and falls back to a static image without WebGL.

### Pages

| Page | What it answers |
| --- | --- |
| **Overview** | What do I need to do today: briefing, top actions, cases / containers / CBM to order now, brands at risk (each expands in place), stock coverage for the next 6 months. |
| **What to order** | Every brand's next order: status, order-by date, cases, weight, CBM, containers (filled to the real level), SKUs at risk. Shipping timeline (order date, 105 to 130 days at sea, months covered), a 12-month look-ahead scrubber, review progress with undo, a "Why?" panel with the engine's inputs, totals, CSV export. Opening a brand shows its runway, projected stock, at-risk products, the order by product, a printable **draft purchase order** (marked "Draft, not sent"), Adjust and Approve. |
| **Brands** | Table or cards; stock runway against the 3-month floor and the split of products below it, so "plenty of stock overall, many SKUs below the floor" is visible at once. Sorting, search, saved views, CSV. |
| **Brand** | The order product by product with reasons, holds, pulled-forward lines and owner adjustments. |
| **Products** | Every product sorted by urgency (Expedite, Order now / this month, Healthy, On hold, Not in plan), virtualized for thousands of rows, hover for monthly sales. Saved views, CSV. |
| **Product** | Sales history, 12-month forecast with the high estimate, holds, spikes and their likely cause, Diwali dates, stock and the floor. |
| **Festivals** | Total sold per month with Diwali marked; the next Diwali order window, top brands and products. |
| **Harvest** | 12-month timeline of harvest, order window and arrival per commodity and country; owner confirmation status and sources. |
| **Sourcing** | At-risk products with ranked alternative sources, supplier leads (labelled, never contacted) and country alerts. |
| **Ask** | The full conversation with the agent, shared with iMessage and labelled by channel; rich replies with tables and brand links. |
| **Activity** | Audit trail of what the agent and the owner did (event types, never message text or phone numbers), owner adjustments and their state. Search and CSV. |
| **Questions for you** | Holds, gaps, one-off orders, code merges, case sizes, harvest rules; Yes / No / Not sure with a note. |
| **Forecast quality** | Replay results: our forecast against the 6-month average at the same ordering rule and the same stock. |

### Working with it

- **Command palette** (⌘K or `/`): jump to any page, brand or product, or ask the agent.
- **URL state**: filters, sort, tab, search and the open drawer live in the address bar, so a view can be shared or
  bookmarked. Saved views and table preferences (columns, density) are kept in the browser.
- **Plan as of**: the date pill in the top bar recomputes statuses and deadlines for another date; quantities stay
  from the engine run. A warning appears when the engine run is older than the viewing date.
- **Themes**: dark (default) and light; WCAG AA contrast; status always shown with an icon and words, never colour alone.
- Optional interface sounds, off by default.

---

## The AI agent

`agent/` answers the owner's questions over the engine's outputs, built on LangChain's Deep Agents harness.

```bash
python -m agent.chat                               # terminal chat on the synthetic data, as the owner
python -m agent.chat --real --today 2026-11-14     # real data
```

In code: `agent.run.answer(thread_id, text, today=None, role="owner"|"guest", channel="imessage"|"dashboard")`.

- **Tools** (`agent/tools.py`) wrap the dashboard API and the engine's outputs:
  - read: `what_to_order`, `explain_product`, `festival_window`, `harvest_plan`, `products_at_risk`, `alternative_sources`, `consolidation_options`, `country_alerts`, `forecast_quality`, `pending_questions`
  - owner only: `record_answer`, `log_event`, `log_override`, `propose_adjustment`, `confirm_adjustment`, `cancel_adjustment`, `list_adjustments`, `add_leads`, `add_alerts`
  - `web_search` (Tavily) only for the research sub-agents, with results treated as information, never instructions.
- **Sub-agents**: `order_planner`, `explainer`, `sourcing_researcher` (harvest timing and supply news, cited) and
  `supplier_scout` (exporters recorded as unverified leads with sources; drafts an inquiry for the owner to send).
- **Memory**: `<data>/agent_memory/AGENTS.md` holds the owner's confirmed preferences and is loaded into every
  conversation.
- **Conversations**: one thread per owner, shared by iMessage and the dashboard (`agent/threads.py`). The thread id is a
  hash of the owner's number, so the number never appears in ids, file names or logs. Guests keep their own per-chat
  threads and never see the owner's. History is kept in `<data>/agent_threads.sqlite`; a channel-labelled transcript
  for the Ask page in `<data>/agent_conversations/`.
- **Roles**: owners (allowlisted numbers and the dashboard) get the write tools; guests are read-only.
- **Limits**: recursion 25, model timeout 30 s with 2 retries, 60 s per answer; any limit gives an honest short reply
  and a line in `<data>/agent_log.csv`. iMessage replies aim for 480 characters, the dashboard about 2,500.
- **Supply scan** (`python -m agent.scan`, `--dry-run`): checks recent news for each harvest commodity's producing
  countries and records cited country alerts (capped at 20 searches per run).
- **Evals** (`python -m agent.evals`): 12 owner questions scored on tool choice, arguments and no invented numbers
  (uses the real model; costs API calls).

---

## iMessage (Photon)

`imessage/bot.py` puts the agent on iMessage. `dashboard.ps1` starts it automatically.

```bash
python -m imessage.bot                  # run on its own; Ctrl+C to stop
python -m imessage.alerts               # print today's digest (sends nothing)
python -m imessage.alerts --send        # send it to owners who have texted, at most once a day
```

`.env`: `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`, `PHOTON_AGENT_HANDLE`, `PHOTON_OWNER_NUMBERS` (comma-separated E.164),
optional `PHOTON_ALLOW_GUESTS=false`, plus `OPENROUTER_API_KEY` and `LLM_MODEL`. `AGENT_DATA` / `AGENT_OUT` pick the
folders.

- **Roles**: owner numbers get the owner agent; anyone else is a read-only guest (useful for judges texting during a
  demo).
- **Batching**: texts sent within about 4 seconds become one turn with one reply; a typing indicator shows while it
  works and "Looking into that…" is sent only after 8 seconds. Long replies are split into at most 3 bubbles.
- **Reliability**: answered message ids are remembered, every bubble has its own idempotency key, and the cursor only
  advances past answered messages, so a restart replays unanswered texts and skips answered ones.
- **Privacy**: logs mask numbers to the last 4 digits and never contain message text.
- **Daily digest** (`imessage/alerts.py`): orders due now, orders too late, harvest windows opening within 30 days and
  active country alerts, built directly from the data (no model, exact numbers).

---

## Owner-taught demand adjustments

The owner can teach the plan things the history can't know.

1. The owner says *"Expect 30% more moong in December, we're running a promo"* (dashboard or iMessage).
2. The agent turns it into a structured adjustment (scope: product, brand or commodity; months; effect as a
   percentage or extra cases per month; reason) and reads it back with its effect on the forecast.
3. It is stored as **proposed** and changes nothing until the owner says yes; then it is **confirmed** and the brand
   orders are rebuilt in about a second (no retraining).

A planning layer (`inventory/adjustments.py`, applied in `brand_orders.run`) changes the forecast table just before
order quantities are computed; the forecast model and `outputs/forecast.csv` are never modified. Every affected order
line shows the reason (for example "+30% owner promo, Dec-Jan"). Adjustments expire after their months, can be cancelled
by text, are owner-only, and appear on the Activity page. Stored in `data/owner_adjustments.csv`.

Note: the order quantity covers the 3 months after the order arrives. Extra demand in months before arrival only
changes the order if stock would still be left at arrival; a product that already runs out before its order lands is
flagged for an expedited or air order instead.

---

## The forecasting and ordering engine

### Forecasting

- **Holds and gaps.** Months a product couldn't be sold are labelled (`fda_hold`, `stockout`, `recall`, `not_listed`)
  and filled from the product's own pattern, never treated as zero demand and never scored. Suspected unrecorded gaps
  are detected (`inventory/gaps.py`), confirmed by the owner, and listed in `outputs/suspected_gaps.csv`.
- **Spikes** (`inventory/spikes.py`): a month at least 2x the product's baseline (and 3 MADs above it, at least 10
  cases) is a spike; its likely cause is attributed (rebound after a gap, code switch, Diwali, recurring seasonal or
  one-off). One-off spikes are capped in training unless the owner says they were real demand.
- **Models** (`inventory/forecast.py`): direct LightGBM Tweedie models per horizon 1 to 12 (mean, p50, p90) with lags,
  rolling means, trend and Diwali features. Diwali is the only festival used as a model feature; other rows in
  `config/festivals.csv` only suggest a possible reason for a spike. Direct (not recursive) forecasting avoids
  compounding festival uplifts.
- **Large orders** (`inventory/large_orders.py`): invoice lines over 3x the product's median and over 20 cases are
  modelled separately and added back as an average, so stock still covers bulk orders.

### Choosing the forecast (tournament)

`python -m inventory.tournament` (or `run.py --retest`) replays the ordering rule month by month with stock carried
over, lead times drawn from 105 to 130 days and unmet demand lost, for each candidate: the 6-month average (what the
business does today), the model on all demand, the model on regular demand plus large orders, a simpler model, a
seasonal naive, and blends. Each is compared with the average **at the same average stock**. The winner is picked on
April to July 2026 and must also beat the average on August to October 2026, or the average is used. The choice is
written to `config/forecaster.txt`; results to `outputs/forecaster_tournament.{json,csv}`.

### Ordering with stock

When `data/raw/Inventory/CurrentInventory.xlsx` exists, `run.py` rebuilds `data/stock.csv` (`inventory/stock_ingest.py`)
and orders against it.

- **When to order**: reorder point = forecast over the 130-day lead time + 3 months; order when on hand + on order falls
  below it.
- **How much (owner's rule)**: forecast sales for the 3 months after the order arrives, minus the stock expected at
  arrival (floored at 0), in whole cases.
- **3-month floor**: stock is projected week by week until a new order could land; products that dip below 3 months of
  forward sales are flagged "expedite or air-ship" with the date.
- **Brand orders** (`inventory/brand_orders.py`): one order per brand; triggered when any product reaches its reorder
  point and including every product that reaches it within 2 months; products on hold are listed but not counted.
- **Containers**: `config/containers.csv`; CBM per case from `data/raw/case_cbm.csv`, the inventory file, or estimated
  from case weight (marked "estimated"). If the last container is under 70% full, the brand's next needs are pulled
  forward to fill it, never above 4 months of stock after arrival.
- The current inventory export has on-hand quantities only, so stock already ordered or at sea is not included. The
  dashboard says so on every order.

### Harvest-season buying

Dals, rice, nuts and dry fruits are bought about a month before the harvest in the source country and sized to last
until the next harvest arrives (`inventory/harvest.py`). Products are mapped to commodities by `config/commodity_map.csv`;
`config/harvest_calendar.csv` holds harvest months per commodity and country, seeded only from cited sources and used
only after the owner confirms a row. Output: `outputs/harvest_plan.csv`.

### Sourcing intelligence

`inventory/sourcing.py` ranks alternative sources for at-risk products: confirmed suppliers, then unverified leads, then
producing countries without a lead; countries with an active alert rank lower; harvest timing and consolidation value
break ties. It never searches the web and never contacts anyone; leads and alerts require a source URL. Outputs:
`outputs/sourcing_options.csv`, `outputs/sourcing_option_lines.csv`.

---

## Owner answers

Answers from Questions for you, the agent or iMessage are saved to `data/owner_answers.csv` and applied in memory on
every run (`inventory/answers.py`); source files are never edited, so changing an answer undoes its effect next run.
The latest answer per question wins; "Not sure" changes nothing.

| Question | Yes | No |
| --- | --- | --- |
| `hold:PID` released from hold? | planned and ordered again from the answer date | stays on hold |
| `gap:PID:YYYY-MM` unavailable then? | months filled, not scored | real zero sales |
| `oneoff:PID:YYYY-MM` a one-off? | stays capped in training | real demand, not capped |
| `code:RAW` same product as BASE? | merged at the next `--ingest` | kept separate |
| `new:PID` keep selling? | kept | discontinued: out of the plan |
| `stock:on_order` stock at sea missing? | stronger warning on every order | warning removed |
| `units:PID` stock right? | accepted | ordered as if none on hand, or the note's number |
| `cbm:PID` case size? | the note's size ("0.045 cbm", "40x30x25 cm") | same |
| `container:limits` 80 CBM right? | weight limit from the note | the note's numbers replace the limits |
| `harvest:RULE` annual buy? | confirmed with the note's months | regular rule |
| `alert:ID` | kept | dismissed |

Each run writes `outputs/answers_applied.csv` with a status for every row (applied, conflict, not understood, …);
anything needing attention is listed in `outputs/backtest_report.md`.

---

## Real data (NetSuite exports)

`inventory/ingest.py` reads every "Sales Orders by Item Summary" export in `data/raw/sales/`:

- drops fee lines; merges `OLD` codes into their current product (converting history by weight per case);
- parses pack size from the name into `pack_weight`, `packs_per_case`, `case_weight_kg`;
- detects bulk codes whose quantities were recorded in pounds and converts them to bags;
- sums to product x month in current-code cases; months before a product's first sale are `not_listed`;
- sets lead time to 130 days (worst case) and 105 (typical); guesses category from whole-word keywords.

Audit files: `data/item_map.csv` (every raw code, its target and conversion) and `data/monthly_volume.csv`.

Data contract (`inventory/schema.py`):

| File | Columns |
| --- | --- |
| `sales.csv` | product_id, month (YYYY-MM), units_sold, status (sellable, fda_hold, stockout, recall, not_listed) |
| `products.csv` | product_id, name, category, shelf_life_days, lead_time_days, unit_cost, primary_source, backup_source, pack fields |
| `stock.csv` | product_id, on_hand / held / on-order quantities in cases, expected arrival when known |

---

## Privacy and safety

- `data/`, `models/`, `outputs/`, raw exports, agent memory, conversations and iMessage state are gitignored and never
  leave the machine, except the text of a question sent to the configured model when the agent is asked.
- The dashboard and API listen on 127.0.0.1 only.
- The agent never places, sends or confirms an order; "Approve" on the dashboard only marks an order as reviewed in the
  browser, and draft purchase orders are marked "Draft, not sent."
- Supplier leads and country alerts require a source; there are no contact fields anywhere; only the owner confirms a
  supplier.
- Phone numbers are hashed in thread ids and masked in logs; the Activity page never shows message text.

---

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest                    # full suite: synthetic data only, a scripted fake model, network blocked
python -m pytest -m "not slow"      # everyday check, under a minute
cd web && npm run build             # production build of the dashboard
cd web && npm run smoke             # with the dashboard running: clicks through every page in local Chrome/Edge
```

Tests cover ingest, gaps, spikes, forecasting, the tournament, ordering with stock, containers, harvest, sourcing, owner
answers, every API endpoint, the agent (tools, roles, limits, prompt injection, shared conversations), owner
adjustments and the iMessage bot. Any test that opens a non-local connection fails.

---

## Project structure

| Path | Purpose |
| --- | --- |
| `run.py` | Runs the whole engine: ingest, forecast, tournament, plans, brand orders, harvest, sourcing, report |
| `inventory/ingest.py`, `schema.py`, `packs.py`, `stock_ingest.py` | NetSuite and inventory exports to the data contract |
| `inventory/gaps.py`, `spikes.py`, `large_orders.py`, `checks.py` | Data quality: holds, gaps, spikes, bulk orders |
| `inventory/forecast.py`, `tournament.py`, `simulate.py`, `backtest.py` | Models, forecaster choice, replay and backtests |
| `inventory/policy.py`, `plan.py`, `stock_plan.py`, `brand_orders.py`, `brands.py` | Ordering rule, plans, stock, containers, brand orders |
| `inventory/harvest.py`, `sourcing.py` | Harvest-season buying and sourcing intelligence |
| `inventory/answers.py`, `adjustments.py` | Owner answers and owner-taught demand adjustments |
| `api/main.py`, `api/agent_routes.py` | Local FastAPI server for the dashboard |
| `web/src/` | Dashboard (`pages/`, `robot/` for the 3D agent, `agent.jsx` for its state, `ui.jsx` shared components) |
| `agent/` | Deep agent: `run.py`, `tools.py`, `subagents.py`, `prompts.py`, `threads.py`, `scan.py`, `evals.py`, `chat.py` |
| `imessage/` | Photon bot, parsing, state and daily digest |
| `config/` | Festivals, harvest rules and calendar, commodity map, containers, densities, forecaster choice |
| `dashboard.ps1`, `dashboard.sh` | Start the dashboard (PowerShell on Windows and macOS; shell script without the bot) |
| `tests/` | Test suite |

---

## Known limits

- **The forecast doesn't beat averaging yet.** At equal stock, the chosen forecast and the 6-month average are close;
  `outputs/backtest_report.md` and the Forecast quality page show where each wins. The value today is in when to order,
  how much and why.
- **The replay scores only from the first possible arrival** (about 4 months in), so the October 2025 Diwali month
  falls in the warm-up; the pre-Diwali month of October 2026 is scored.
- **The simulation is simplified:** lost sales are assumed (no backorders), with a single lead time per order drawn
  uniformly from 105 to 130 days.
- **The inventory export has on-hand stock only;** stock already ordered or at sea is not included.
- **Case sizes are estimated** for most products, from weight and category density, until real case sizes are entered.
- **Bulk pound detection and OLD-code conversions** need the owner's confirmation.

---

## Credits

- The supplier-research design (search per commodity and country, cite every company, dedupe, label reliability) is
  inspired by the open-source [harvey](https://github.com/ethanplusai/harvey) agent (AGPL-3.0). No code is taken from
  it, and nothing here sends email or contacts anyone.
- Built with LightGBM, pandas, FastAPI, React, Vite, Recharts, Three.js, React Three Fiber, Lucide, LangChain Deep
  Agents, LangGraph, OpenRouter, Tavily and Photon.
