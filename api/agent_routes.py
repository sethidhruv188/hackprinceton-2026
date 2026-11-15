"""Dashboard endpoints added for the new frontend: overview, settings (demo / real mode and "plan as of" date).
Mounted by api/main.py with one include line. Read-only over run.py's outputs, like the rest of the API.

Settings live in this local server process (one owner, 127.0.0.1): the mode switches DASH_DATA / DASH_OUT, and
"plan as of" sets DASH_TODAY, which only changes statuses and deadlines; quantities come from the last engine run.
"""
import json
import re
import os
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
MODES = {"real": ("data", "outputs"), "demo": ("data_demo", "outputs_demo")}
CANDIDATES = ["avg", "ml", "ml_regular", "ml_simple", "seasonal", "blend_0.25", "blend_0.5", "blend_0.75", "blend3"]
_START = {k: os.environ.get(k) for k in ("DASH_DATA", "DASH_OUT", "DASH_TODAY")}


def _M():
    import api.main as M
    return M


def current_mode():
    d = Path(os.environ.get("DASH_DATA", ROOT / "data")).resolve()
    for mode, (data, _) in MODES.items():
        if d == (ROOT / data).resolve():
            return mode
    return "custom"


def run_date():
    """The date the outputs were built for (brand_orders: order-by minus days-to-order-by)."""
    p = _M().out_dir() / "brand_orders.csv"
    if not p.exists():
        return None
    o = pd.read_csv(p, usecols=["order_by_safe", "days_to_order_by"]).dropna()
    if o.empty:
        return None
    r = o.iloc[0]
    return f"{pd.Timestamp(r['order_by_safe']) - pd.Timedelta(days=int(r['days_to_order_by'])):%Y-%m-%d}"


def settings_payload():
    M = _M()
    as_of = os.environ.get("DASH_TODAY")
    return dict(mode=current_mode(), as_of=M.today().strftime("%Y-%m-%d"), as_of_set=bool(as_of and as_of != run_date()),
                run_date=run_date(), data_dir=str(M.data_dir()), out_dir=str(M.out_dir()),
                modes={m: (ROOT / o / "brand_orders.csv").exists() for m, (_, o) in MODES.items()})


class SettingsIn(BaseModel):
    mode: str | None = None
    as_of: str | None = None          # "YYYY-MM-DD", or "" to go back to the engine run date


@router.get("/api/settings")
def get_settings():
    return settings_payload()


@router.post("/api/settings")
def set_settings(s: SettingsIn):
    if s.mode is not None:
        if s.mode not in MODES:
            raise HTTPException(422, f"mode must be one of {sorted(MODES)}")
        data, out = MODES[s.mode]
        if not (ROOT / out / "brand_orders.csv").exists():
            raise HTTPException(409, f"{out}/ has no outputs yet: run the engine for {s.mode} data first")
        os.environ.update(DASH_DATA=str(ROOT / data), DASH_OUT=str(ROOT / out))
        os.environ.pop("DASH_TODAY", None)                   # a new folder: back to its own run date
    if s.as_of is not None:
        if s.as_of == "":
            os.environ.pop("DASH_TODAY", None)
        else:
            try:
                os.environ["DASH_TODAY"] = f"{pd.Timestamp(s.as_of):%Y-%m-%d}"
            except ValueError:
                raise HTTPException(422, "as_of must be a date (YYYY-MM-DD)")
    if "DASH_TODAY" not in os.environ and run_date():
        os.environ["DASH_TODAY"] = run_date()
    return settings_payload()


# ---------------------------------------------------------------- overview
def _replay(out):
    """The forecaster vs the 6-month average from replaying real history: the forecaster test (tournament) when its
    file exists, else simulation_summary.json. Plain numbers; the wording is the frontend's."""
    t = out / "forecaster_tournament.json"
    if t.exists():
        d = json.loads(t.read_text())
        tab, chosen = d.get("table", {}), d.get("chosen")
        c, a = tab.get(chosen, {}), tab.get("avg", {})
        stock_c, stock_a = c.get("avg_stock_cushion3_aug_oct"), a.get("avg_stock_cushion3_aug_oct")
        return dict(source="forecaster_tournament.json", chosen=chosen, best_ml=d.get("best_ml"), weight=d.get("weight"),
                    products=d.get("products"), confirmed=d.get("confirmed_aug_oct"), note=d.get("note"),
                    selection=dict(period=d.get("selection_period"), points_vs_avg=c.get("points_vs_avg_apr_jul")),
                    confirmation=dict(period=d.get("confirmation_period"), points_vs_avg=c.get("points_vs_avg_aug_oct")),
                    pre_diwali=dict(months=d.get("pre_diwali_month"), chosen=c.get("fill_pre_diwali_cushion3"),
                                    avg=a.get("fill_pre_diwali_cushion3")),
                    stock=dict(chosen=stock_c, avg=stock_a,
                               pct_more=(100 * (stock_c / stock_a - 1)) if stock_c and stock_a else None),
                    not_completed=[k for k in CANDIDATES if tab.get(k) is None])
    s = out / "simulation_summary.json"
    if s.exists():
        d = json.loads(s.read_text())
        return dict(source="simulation_summary.json", scored_from=d.get("scored_from"), scored_to=d.get("scored_to"),
                    products=d.get("products"), cushion_months=d.get("cushion_months"), overall=d.get("overall"),
                    verdict=d.get("verdict"))
    return None


@router.get("/api/overview")
def overview():
    M = _M()
    out, today = M.out_dir(), M.today()
    orders = M.csv_out("brand_orders.csv")
    status = orders["status"].value_counts().to_dict() if len(orders) else {}
    late = orders[orders["status"].astype(str).str.startswith("Too late")] if len(orders) else orders
    counts = dict(order_now=int(status.get("Order now", 0)), this_month=int(status.get("Order this month", 0)),
                  coming_up=int(status.get("Coming up", 0)), too_late=int(len(late)),
                  order_now_cases=int(orders.loc[orders["status"] == "Order now", "cases"].sum()) if len(orders) else 0)
    # next harvest window: confirmed buys first, else the earliest proposed one (labeled)
    nxt = None
    hp = M.csv_out("harvest_plan.csv", required=False)
    if len(hp) and "order_by" in hp:
        h = hp[hp["order_by"].notna() & (hp["order_by"].astype(str) >= today.strftime("%Y-%m-%d"))].copy()
        h["_c"] = (h["in_orders"] != "yes").astype(int)
        h = h.sort_values(["_c", "order_window_start", "order_by"])
        if len(h):
            r = h.iloc[0]
            nxt = M.clean(dict(commodity=r["commodity"], country=r["source_country"], status=r["status"],
                               window_start=r["order_window_start"], order_by=r["order_by"],
                               arrival_earliest=r.get("arrival_earliest"), arrival_latest=r.get("arrival_latest"),
                               cases=r.get("quantity_cases"), confirmed=bool(r["in_orders"] == "yes")))
    # Diwali: the next order deadline still ahead
    diwali = None
    try:
        seasons = M.diwali()["seasons"]
        ahead = [s for s in seasons if s["order_by_latest"] >= today.strftime("%Y-%m-%d")]
        if ahead:
            s = ahead[0]
            diwali = dict(year=s["year"], diwali_date=s["diwali_date"], order_by_safe=s["order_by_safe"],
                          order_by_latest=s["order_by_latest"], in_warehouse_by=s["in_warehouse_by"],
                          days_to_safe=(pd.Timestamp(s["order_by_safe"]) - today).days,
                          days_to_latest=(pd.Timestamp(s["order_by_latest"]) - today).days)
    except HTTPException:
        pass
    # active alerts with their sources and labels
    from inventory import answers as A
    from inventory import sourcing as S
    al = S.load_alerts(M.data_dir(), today, A.load(M.data_dir()))
    al = al[al["active"]] if len(al) else al
    alerts = [dict(alert_id=r.alert_id, label=r.label, commodity=r.commodity, country=r.country, issue=r.issue,
                   source_url=r.source_url, reported_on=r.reported_on, expires=f"{r.expires:%Y-%m-%d}") for r in al.itertuples()]
    return M.clean(dict(settings=settings_payload(), replay=_replay(out), counts=counts, next_harvest=nxt, diwali=diwali,
                        alerts=alerts, holds=len(M.holds_now())))


# ---------------------------------------------------------------- products list
ATTENTION = ("below 3-month floor", "order now", "on hold", "expedite")


@router.get("/api/products")
def products(q: str = "", show: str = "all", limit: int = 200):
    """Every product with its brand and stock status, for the Products page. show: all | attention."""
    M = _M()
    _, prods = M.panel()
    t = prods[["product_id", "name"] + [c for c in ("category",) if c in prods]].copy()
    pb = M.csv_out("product_brands.csv", required=False)
    t = t.merge(pb[["product_id", "brand"]], on="product_id", how="left") if len(pb) else t.assign(brand=None)
    st = M.csv_out("product_status.csv", required=False)
    cols = [c for c in ("on_hand_cases", "order_now_cases", "ok_until", "below_floor_now", "floor_dip_date", "status")
            if c in st]
    if len(st):
        t = t.merge(st[["product_id"] + cols], on="product_id", how="left")
    plan = M.csv_out("order_plan.csv")
    nxt = (plan.sort_values("month").groupby("product_id").head(1)[["product_id", "month", "cases_needed", "order_by_safe"]]
           .rename(columns={"month": "next_month", "cases_needed": "next_cases"}))
    t = t.merge(nxt, on="product_id", how="left")
    t["on_hold"] = t["product_id"].isin(M.holds_now())
    t["in_plan"] = t["product_id"].isin(set(plan["product_id"]))
    if "below_floor_now" in t:
        t["below_floor_now"] = t["below_floor_now"].astype(str).str.lower().eq("true")
    total = len(t)
    needle = q.strip().lower()
    if needle:
        t = t[t["name"].astype(str).str.lower().str.contains(needle, regex=False)
              | t["product_id"].astype(str).str.lower().str.contains(needle, regex=False)
              | t["brand"].astype(str).str.lower().str.contains(needle, regex=False)]
    if show == "attention":
        s = t["status"].astype(str).str.lower() if "status" in t else pd.Series("", index=t.index)
        flag = t["on_hold"] | s.apply(lambda x: any(a in x for a in ATTENTION))
        if "order_now_cases" in t:
            flag = flag | (pd.to_numeric(t["order_now_cases"], errors="coerce").fillna(0) > 0)
        t = t[flag]
    t = t.sort_values(["brand", "name"], na_position="last")
    return M.clean(dict(total=total, matched=int(len(t)), products=M.records(t.head(max(1, min(limit, 1000)))),
                        stock_used="status" in t))


# ---------------------------------------------------------------- harvest timeline
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\b", re.I)


def harvest_months(window):
    """Calendar month numbers in a window like 'Dec-Apr', 'Sep' or 'Aug (end month not stated)'."""
    found = [MONTHS.index(m.title()) + 1 for m in _MONTH_RE.findall(str(window))]
    if not found:
        return []
    a, b = found[0], found[1] if len(found) > 1 else found[0]
    return [((a - 1 + i) % 12) + 1 for i in range(((b - a) % 12) + 1)]


def _span(start, end):
    if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
        return set()
    return {p.strftime("%Y-%m") for p in pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M")}


@router.get("/api/harvest")
def harvest():
    """The harvest plan on a 12-month timeline from the plan date: harvest months, the order window and the arrival
    window per commodity and country. Rows without products come last."""
    M = _M()
    today = M.today()
    months = [p.strftime("%Y-%m") for p in pd.period_range(today, periods=12, freq="M")]
    hp = M.csv_out("harvest_plan.csv", required=False)
    rows = []
    for r in M.records(hp) if len(hp) else []:
        hm = set(harvest_months(r.get("harvest_window")))
        order = _span(r.get("order_window_start"), r.get("order_by"))
        arrive = _span(r.get("arrival_earliest"), r.get("arrival_latest"))
        cells = [dict(month=m, harvest=int(m[5:]) in hm, order=m in order, arrive=m in arrive) for m in months]
        rows.append(dict(commodity=r["commodity"], group=r.get("group"), country=r.get("source_country"),
                         products=int(r.get("products") or 0), product_ids=r.get("product_ids"),
                         harvest_window=r.get("harvest_window"), status=r.get("status"),
                         order_window_start=r.get("order_window_start"), order_by=r.get("order_by"),
                         arrival_earliest=r.get("arrival_earliest"), arrival_latest=r.get("arrival_latest"),
                         covers_until=r.get("covers_until"), cases=r.get("quantity_cases"),
                         runs_out_before_arrival=r.get("runs_out_before_arrival"),
                         in_orders=r.get("in_orders") == "yes", confirmed=r.get("confirmed") == "yes",
                         confidence=r.get("confidence"), source_url=r.get("source_url"), reason=r.get("reason"),
                         cells=cells))
    rank = {"ORDER NOW": 0, "upcoming": 1, "window closed": 2, "no harvest data": 3, "no products": 4}
    rows.sort(key=lambda x: (x["products"] == 0, rank.get(x["status"], 5), x["order_by"] or "9999", x["commodity"]))
    return M.clean(dict(today=f"{today:%Y-%m-%d}", months=months, rows=rows, has_plan=bool(len(hp))))


# ---------------------------------------------------------------- sourcing
@router.get("/api/sourcing")
def sourcing():
    """At-risk products with their sourcing options, the supplier leads and the active country alerts."""
    M = _M()
    from inventory import answers as A
    from inventory import sourcing as S
    today = M.today()
    opts = M.csv_out("sourcing_options.csv", required=False)
    lines = M.csv_out("sourcing_option_lines.csv", required=False)
    by_pid = {}
    for l in M.records(lines) if len(lines) else []:
        by_pid.setdefault(l["product_id"], []).append(l)
    products = []
    for o in M.records(opts) if len(opts) else []:
        o["options"] = sorted(by_pid.get(o["product_id"], []), key=lambda x: x.get("rank") or 99)
        products.append(o)
    p = M.data_dir() / "supplier_leads.csv"
    leads = pd.read_csv(p, dtype=str).fillna("") if p.exists() else pd.DataFrame()
    al = S.load_alerts(M.data_dir(), today, A.load(M.data_dir()))
    al = al[al["active"]] if len(al) else al
    alerts = [dict(alert_id=r.alert_id, label=r.label, commodity=r.commodity, country=r.country, issue=r.issue,
                   source_url=r.source_url, reported_on=r.reported_on) for r in al.itertuples()]
    commodities = sorted({o["commodity"] for o in products if o.get("commodity")})
    return M.clean(dict(today=f"{today:%Y-%m-%d}", products=products, leads=M.records(leads) if len(leads) else [],
                        alerts=alerts, commodities=commodities, ask=ask_status(), research=research_status()))


# ---------------------------------------------------------------- activity
def _read(path):
    return pd.read_csv(path, dtype=str).fillna("") if path.exists() else pd.DataFrame()


def _when(s):
    try:
        t = pd.Timestamp(s)
    except (ValueError, TypeError):
        return None
    if pd.isna(t):
        return None
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t


@router.get("/api/activity")
def activity(limit: int = 200):
    """What the agent and the owner did, newest first. Never message text, notes or phone numbers: event kinds, tool
    names, question ids, products and quantities only."""
    M = _M()
    d = M.data_dir()
    ev = []
    for r in _read(d / "agent_log.csv").to_dict("records"):
        kind = r.get("event", "")
        if kind == "answered":
            what, detail = "Answered a message", (f"Tools: {r['detail']}" if r.get("detail") else "No tools needed")
        elif kind.startswith("limit:"):
            what, detail = "Stopped at a limit", kind.split(":", 1)[1].replace("_", " ")
        elif kind.startswith("scan:"):
            what, detail = "Supply scan problem", kind.split(":", 1)[1].replace("_", " ")
        elif kind == "error":
            what, detail = "Error", (r.get("detail", "").split(":", 1)[0])[:60]     # the exception type only
        else:
            what, detail = kind, ""
        ev.append(dict(at=r.get("logged_at"), source="agent", channel=r.get("channel"), role=r.get("role"), what=what,
                       detail=detail))
    for r in _read(d / "owner_answers.csv").to_dict("records"):
        ev.append(dict(at=r.get("timestamp"), source="owner", channel="", role="owner", what="Answered a question",
                       detail=f"{r.get('question_id')}: {r.get('answer')}"))
    for r in _read(d / "agent_events.csv").to_dict("records"):
        span = " to ".join(x for x in (r.get("start"), r.get("end")) if x)
        ev.append(dict(at=r.get("logged_at"), source="agent", channel="", role="owner", what="Recorded a demand note",
                       detail=", ".join(x for x in (r.get("products"), span) if x)))
    for r in _read(d / "agent_overrides.csv").to_dict("records"):
        ev.append(dict(at=r.get("logged_at"), source="agent", channel="", role="owner", what="Recorded a quantity change",
                       detail=f"{r.get('product')}: {r.get('qty')} cases"))
    for r in _read(d / "supplier_leads.csv").to_dict("records"):
        ev.append(dict(at=r.get("found_on"), source="research", channel="", role="", what="Supplier lead recorded",
                       detail=f"{r.get('company')} ({r.get('country')}): {r.get('commodities', '').replace(';', ', ')}",
                       url=r.get("source_url"), demo=r.get("is_demo") == "yes"))
    for r in _read(d / "country_alerts.csv").to_dict("records"):
        ev.append(dict(at=r.get("reported_on"), source="research", channel="", role="", what="Country alert recorded",
                       detail=f"{r.get('commodity')} in {r.get('country')}", url=r.get("source_url"),
                       demo=r.get("is_demo") == "yes"))
    timed = [(t, e) for e in ev if (t := _when(e["at"])) is not None]
    timed.sort(key=lambda x: x[0], reverse=True)
    counts = {}
    for _, e in timed:
        counts[e["what"]] = counts.get(e["what"], 0) + 1
    return M.clean(dict(events=[e for _, e in timed[:max(1, min(limit, 1000))]], total=len(timed), counts=counts))


# ---------------------------------------------------------------- ask the agent
ASK_MODEL = None            # tests put a fake chat model here; None = the configured model (agent/llm.py)


class AskIn(BaseModel):
    text: str
    thread_id: str = "dashboard"


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass


def fake_agent():
    return os.environ.get("DASH_FAKE_AGENT") == "1"


def ask_status():
    if ASK_MODEL is not None or fake_agent():
        return dict(available=True, model="offline test agent", reason="")
    _env()
    key, model = os.environ.get("OPENROUTER_API_KEY"), os.environ.get("LLM_MODEL")
    if not key or not model:
        return dict(available=False, model=model or "",
                    reason="The agent is offline: set OPENROUTER_API_KEY and LLM_MODEL in .env, then restart the dashboard.")
    return dict(available=True, model=model, reason="")


def research_status():
    if ASK_MODEL is not None or fake_agent():
        return dict(web_search=True)
    _env()
    return dict(web_search=bool(os.environ.get("TAVILY_API_KEY")))


@router.get("/api/ask/status")
def get_ask_status():
    return dict(**ask_status(), research=research_status())


@router.post("/api/ask")
def ask(q: AskIn):
    """One message to the agent as the owner on the dashboard channel, in the current mode and plan date."""
    text = q.text.strip()
    if not text:
        raise HTTPException(422, "empty message")
    if len(text) > 2000:
        raise HTTPException(422, "message too long (2,000 characters at most)")
    thread = "".join(ch for ch in q.thread_id if ch.isalnum() or ch in "-_")[:64] or "dashboard"
    st = ask_status()
    if not st["available"]:
        raise HTTPException(503, st["reason"])
    if fake_agent() and ASK_MODEL is None:
        return dict(reply=f"(Offline test agent) You asked: {text[:200]}", tools=[{"name": "what_to_order"}],
                    sources=["https://example.org/demo-source"], error=None, thread_id=thread)
    from agent import run as R
    M = _M()
    d = R.answer_detail(thread, text, today=f"{M.today():%Y-%m-%d}", role="owner", channel="dashboard",
                        data=str(M.data_dir()), out=str(M.out_dir()), model=ASK_MODEL)
    return dict(reply=d["reply"], tools=d.get("tools", []), sources=d.get("sources", []), error=d.get("error"),
                thread_id=thread)
