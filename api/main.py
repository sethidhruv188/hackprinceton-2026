"""Local, read-only API over the files run.py writes (no model training here).

  uvicorn api.main:app --host 127.0.0.1 --port 8000

Folders come from environment variables (defaults: the real data):
  DASH_DATA=data  DASH_OUT=outputs  DASH_TODAY=YYYY-MM-DD (default: today)
The only write is POST /answers, which appends to <DASH_DATA>/owner_answers.csv.
"""
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from inventory import schema  # noqa: E402
from inventory.forecast import CAL, month_kind  # noqa: E402

app = FastAPI(title="Shakti ordering dashboard (local)")
from api.agent_routes import router as _new_routes  # noqa: E402  (overview, settings, ask, sourcing, harvest, activity)
app.include_router(_new_routes)
ANSWER_FIELDS = ["question_id", "answer", "note", "timestamp"]
TOP_PER_TYPE = 30


def data_dir():
    return Path(os.environ.get("DASH_DATA", ROOT / "data"))


def out_dir():
    return Path(os.environ.get("DASH_OUT", ROOT / "outputs"))


def today():
    t = os.environ.get("DASH_TODAY")
    return pd.Timestamp(t).normalize() if t else pd.Timestamp.today().normalize()


_cache = {}


def _cached(key, paths, fn):
    """Re-run fn only when one of the files changed."""
    stamp = tuple((str(p), p.stat().st_mtime if p.exists() else None) for p in paths)
    hit = _cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    val = fn()
    _cache[key] = (stamp, val)
    return val


def csv_out(name, required=True, **kw):
    p = out_dir() / name
    if not p.exists():
        if required:
            raise HTTPException(503, f"{name} not found in {out_dir()}: run `python run.py` first")
        return pd.DataFrame()
    return _cached(("out", name), [p], lambda: pd.read_csv(p, dtype={"product_id": str}, **kw))


def panel():
    d = data_dir()
    return _cached(("panel",), [d / "sales.csv", d / "products.csv"], lambda: schema.load(d)[:2])


def clean(v):
    """JSON-safe values: NaN -> None, numpy -> python."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return v


def records(df):
    return clean(df.to_dict("records"))


def holds_now():
    """Products on hold now. The engine writes outputs/holds_now.csv after applying the owner's answers
    (a released hold is no longer listed); older outputs without it fall back to sales.csv."""
    h = csv_out("holds_now.csv", required=False)
    if "product_id" in h:
        return set(h["product_id"].dropna())
    pan, _ = panel()
    last = pan["month"].max()
    return set(pan.loc[(pan["month"] == last) & (pan["status"] == "fda_hold"), "product_id"])


def answers():
    p = data_dir() / "owner_answers.csv"
    if not p.exists():
        return {}
    a = pd.read_csv(p, dtype=str).fillna("")
    return {r["question_id"]: r for r in a.to_dict("records")}       # last answer wins


# ---------------------------------------------------------------- summary
@app.get("/api/summary")
def summary():
    orders = csv_out("brand_orders.csv")
    lines = csv_out("brand_order_lines.csv")
    pan, _ = panel()
    live = orders[orders["status"] != "Too late for full lead time: expedite"].sort_values(["order_by_safe", "cases"],
                                                                                           ascending=[True, False])
    decide = orders[orders["status"].isin(["Order now", "Order this month"])]["order_id"]
    n_decide = lines[lines["order_id"].isin(decide) & ~lines["on_hold"].astype(bool)]["product_id"].nunique()
    late = orders[orders["status"] == "Too late for full lead time: expedite"]
    plan = csv_out("order_plan.csv")
    h = holds_now()
    return clean(dict(
        today=today().strftime("%Y-%m-%d"), data_through=pan["month"].max().strftime("%Y-%m"),
        next_orders=records(live.head(3)), products_needing_decision=int(n_decide),
        brands=int(orders["brand"].nunique()), orders_too_late=int(len(late)), cases_too_late=int(late["cases"].sum()),
        holds=len(h), holds_in_plan=len(h & set(plan["product_id"])), **stock_flags(orders)))


def floor_counts():
    """{brand: below_floor_now, below_floor_soon} from product_status.csv (3-month floor)."""
    st = csv_out("product_status.csv", required=False)
    if not len(st) or "floor_dip_date" not in st:
        return {}
    now = st["below_floor_now"].astype(str).str.lower().eq("true")
    soon = st["floor_dip_date"].notna() & ~now
    t = pd.DataFrame({"brand": st["brand"], "now": now, "soon": soon}).groupby("brand").sum()
    return {b: dict(below_floor_now=int(r["now"]), below_floor_soon=int(r["soon"])) for b, r in t.iterrows()}


def stock_flags(orders):
    """What the plan includes: gross need, or stock on hand (with or without stock on order)."""
    used = bool(orders["stock_netted"].astype(bool).any()) if len(orders) and "stock_netted" in orders else False
    warn = ""
    if used and "warning" in orders:
        w = orders["warning"].dropna()
        warn = str(w.iloc[0]) if len(w) and str(w.iloc[0]).strip() else ""
    now = orders[(orders["status"] == "Order now")] if len(orders) else orders
    est = None
    if used and "cbm_estimated_pct" in orders and now["cbm"].sum() > 0:
        est = float((now["cbm"] * now["cbm_estimated_pct"].fillna(0) / 100).sum() / now["cbm"].sum() * 100)
    note = ("Showing how much you need. Your current stock isn't included yet." if not used else
            "Includes the stock on hand in the warehouse." + (f" {warn[0].upper() + warn[1:]}" if warn else ""))
    return dict(gross_need=not used, stock_used=used, on_order_missing=bool(warn), gross_need_note=note, stock_note=note,
                orders_now=int(len(now)), orders_now_cases=int(now["cases"].sum()) if len(now) else 0,
                orders_now_cbm=float(now["cbm"].sum()) if used and len(now) else None,
                orders_now_containers=float(now["containers_needed"].sum()) if used and len(now) else None,
                orders_now_container_volume=float(now["containers"].sum()) if used and len(now) else None,
                cbm_estimated_pct=est)


# ---------------------------------------------------------------- brands
@app.get("/api/brands")
def brands():
    orders = csv_out("brand_orders.csv")
    info = csv_out("brands.csv", required=False)
    late = orders["status"] == "Too late for full lead time: expedite"
    rows = []
    for brand, g in orders.groupby("brand"):
        nxt = g[g["status"] != "Too late for full lead time: expedite"].sort_values("order_by_safe")
        lt = g[g["status"] == "Too late for full lead time: expedite"]
        row = dict(brand=brand, next_order=records(nxt.head(1))[0] if len(nxt) else None,
                   orders=int(len(g)), late_orders=int(len(lt)), late_cases=int(lt["cases"].sum()), **floor_counts().get(brand, {}))
        if len(info):
            i = info[info["brand"] == brand]
            if len(i):
                row.update(records(i[[c for c in ["products", "active_products", "monthly_cases", "monthly_kg", "on_hand_cases",
                                                  "months_of_cover"] if c in i]])[0])
        rows.append(row)
    rows.sort(key=lambda r: (r["next_order"]["order_by_safe"] if r["next_order"] else "9999",
                             -(r["next_order"]["cases"] if r["next_order"] else 0)))
    return clean(dict(today=today().strftime("%Y-%m-%d"), brands=rows, late_orders_total=int(late.sum()), **stock_flags(orders)))


@app.get("/api/brands/{brand}")
def brand_detail(brand: str):
    orders = csv_out("brand_orders.csv")
    lines = csv_out("brand_order_lines.csv")
    g = orders[orders["brand"] == brand].sort_values("order_by_safe")
    if g.empty:
        raise HTTPException(404, f"no orders for brand {brand!r}")
    out = []
    for o in records(g):  # noqa: B007
        l = lines[lines["order_id"] == o["order_id"]]
        st = csv_out("product_status.csv", required=False)
        if len(st) and "floor_dip_date" in st:
            l = l.merge(st[["product_id", "floor_dip_date"]], on="product_id", how="left")
        # flags after the merge: merge renumbers rows, and assign aligns on the index
        flags = {c: l[c].astype(str).str.lower().eq("true") for c in ("on_hold", "pulled_forward", "harvest") if c in l}
        o["lines"] = records(l.assign(**flags))
        out.append(o)
    info = csv_out("brands.csv", required=False)
    b = records(info[info["brand"] == brand])[0] if len(info) and (info["brand"] == brand).any() else {}
    return clean(dict(brand=brand, today=today().strftime("%Y-%m-%d"), orders=out, info=b, **stock_flags(orders)))


# ---------------------------------------------------------------- product
@app.get("/api/products/{product_id}")
def product(product_id: str):
    pan, products = panel()
    p = products[products["product_id"] == product_id]
    if p.empty:
        raise HTTPException(404, f"unknown product {product_id!r}")
    info = records(p)[0]
    h = pan[pan["product_id"] == product_id].sort_values("month")
    history = [dict(month=m.strftime("%Y-%m"), cases=float(c), state=("on hold" if s == "fda_hold" else
                    "not listed yet" if s == "not_listed" else "sold" if s == "sellable" else s))
               for m, c, s in zip(h["month"], h["units_sold"], h["status"])]
    spk = csv_out("spikes.csv", required=False)
    spikes = records(spk[spk["product_id"] == product_id]) if len(spk) else []
    fc = csv_out("forecast.csv", required=False)
    forecast = records(fc[fc["product_id"] == product_id][["month", "likely", "high"]]) if len(fc) else []
    rp = csv_out("reorder_points.csv", required=False)
    r = rp[rp["product_id"] == product_id].sort_values("month") if len(rp) else rp
    plan = csv_out("order_plan.csv")
    pl = plan[plan["product_id"] == product_id].sort_values("month")
    prof = csv_out("product_profiles.csv", required=False)
    pb = csv_out("product_brands.csv", required=False)
    brand = pb.loc[pb["product_id"] == product_id, "brand"].iloc[0] if len(pb) and (pb["product_id"] == product_id).any() else None
    lo, hi = h["month"].min(), pd.Timestamp(forecast[-1]["month"] + "-01") if forecast else h["month"].max()
    diwali = [d.strftime("%Y-%m-%d") for d in CAL["diwali_main"] if lo <= d <= hi + pd.offsets.MonthEnd(0)]
    return clean(dict(
        product=info, brand=brand, history=history, hold_months=[x["month"] for x in history if x["state"] == "on hold"],
        on_hold_now=product_id in holds_now(), spikes=spikes, forecast=forecast, diwali_dates=diwali,
        reorder=records(r.head(1))[0] if len(r) else None, plan=records(pl),
        profile=records(prof[prof["product_id"] == product_id])[0] if len(prof) and (prof["product_id"] == product_id).any() else None,
        inactive=bool(pl.empty), stock=_row("product_status.csv", product_id), harvest=_row("harvest_orders.csv", product_id),
        projection=records(_rows("stock_projection.csv", product_id)), cbm=_row("case_cbm_used.csv", product_id),
        **stock_flags(csv_out("brand_orders.csv"))))


def _rows(name, product_id):
    t = csv_out(name, required=False)
    return t[t["product_id"] == product_id] if len(t) else t


def _row(name, product_id):
    t = _rows(name, product_id)
    return records(t)[0] if len(t) else None


@app.get("/api/search")
def search(q: str = ""):
    """Products and brands whose name contains q (for the search box)."""
    _, products = panel()
    pb = csv_out("product_brands.csv", required=False)
    q = q.strip().lower()
    if len(q) < 2:
        return dict(products=[], brands=[])
    hit = products[products["name"].astype(str).str.lower().str.contains(q, regex=False)
                   | products["product_id"].str.lower().str.contains(q, regex=False)].head(30)
    hit = hit.merge(pb, on="product_id", how="left") if len(pb) else hit.assign(brand=None)
    brands_hit = sorted({b for b in (pb["brand"] if len(pb) else []) if q in str(b).lower()})[:20]
    return clean(dict(products=records(hit[["product_id", "name", "brand"]]), brands=brands_hit))


# ---------------------------------------------------------------- Diwali
@app.get("/api/diwali")
def diwali():
    pan, products = panel()
    kgw = products.set_index("product_id")["case_weight_kg"] if "case_weight_kg" in products else pd.Series(dtype=float)
    m = pan.assign(kg=pan["units_sold"] * pan["product_id"].map(kgw)).groupby("month").agg(
        cases=("units_sold", "sum"), kg=("kg", lambda s: s.sum(min_count=1)))
    monthly = [dict(month=k.strftime("%Y-%m"), cases=float(r.cases), kg=None if pd.isna(r.kg) else float(r.kg),
                    kind=month_kind(k)) for k, r in m.iterrows()]
    fr = csv_out("festival_readiness.csv", required=False)
    plan = csv_out("order_plan.csv")
    pb = csv_out("product_brands.csv", required=False)
    prof = csv_out("product_profiles.csv", required=False)
    plan = plan.assign(m=pd.to_datetime(plan["month"]))
    plan["kind"] = plan["m"].map({x: month_kind(x) for x in plan["m"].unique()})
    other = plan[plan["kind"] == "other"].groupby("product_id")["cases_needed"].median()
    seasons = []
    for (g, yr), fm in sorted(CAL["first_month"].items(), key=lambda x: x[1]):
        if g != "diwali":
            continue
        main = [d for d in CAL["diwali_main"] if d.year == yr]
        pre = fm - pd.DateOffset(months=1)
        safe, latest = pre - pd.Timedelta(days=130), pre - pd.Timedelta(days=105)
        months_in = [x for x in (pre, fm) if x in set(plan["m"])]
        season = dict(year=int(yr), diwali_date=main[0].strftime("%Y-%m-%d") if main else None,
                      festival_month=fm.strftime("%Y-%m"), in_warehouse_by=pre.strftime("%Y-%m-%d"),
                      order_by_safe=safe.strftime("%Y-%m-%d"), order_by_latest=latest.strftime("%Y-%m-%d"),
                      in_plan=bool(months_in), plan_months=[x.strftime("%Y-%m") for x in months_in])
        if months_in:
            s = plan[plan["m"].isin(months_in)].groupby("product_id")["cases_needed"].sum()
            extra = (s - len(months_in) * other.reindex(s.index).fillna(0)).clip(lower=0)
            e = extra.rename("extra_cases").reset_index()
            if len(pb):
                e = e.merge(pb[["product_id", "brand"]], on="product_id", how="left")
                tb = e.groupby("brand")["extra_cases"].sum().sort_values(ascending=False).head(15)
                season["top_brands"] = [dict(brand=b, extra_cases=round(float(v), 0)) for b, v in tb.items() if v > 0]
            tp = fr[fr["festival_month"] == fm.strftime("%Y-%m")] if len(fr) else pd.DataFrame()
            if len(tp):
                tp = tp.merge(prof[["product_id", "diwali_lift_pre_month", "diwali_lift_diwali_month"]], on="product_id", how="left") \
                    if len(prof) else tp
                season["top_products"] = records(tp[[c for c in ["product_id", "name", "cases_pre_month", "cases_festival_month",
                                                                 "extra_cases_vs_normal", "diwali_lift_pre_month",
                                                                 "diwali_lift_diwali_month"] if c in tp]])
        seasons.append(season)
    seasons = [s for s in seasons if s["year"] >= today().year]
    return clean(dict(today=today().strftime("%Y-%m-%d"), monthly=monthly, seasons=seasons,
                      diwali_dates=[d.strftime("%Y-%m-%d") for d in CAL["diwali_main"]]))


# ---------------------------------------------------------------- questions
@app.get("/api/questions")
def questions():
    pan, products = panel()
    names = products.set_index("product_id")["name"].to_dict()
    prof = csv_out("product_profiles.csv", required=False)
    typical = prof.set_index("product_id")["typical_monthly_cases"].to_dict() if len(prof) else {}
    qs = []
    # products on hold now
    last = pan["month"].max()
    for pid in sorted(holds_now()):
        g = pan[(pan["product_id"] == pid)].sort_values("month")
        since = last
        for mth, st in zip(reversed(list(g["month"])), reversed(list(g["status"]))):
            if st != "fda_hold":
                break
            since = mth
        impact = 6 * (typical.get(pid) or 0)
        qs.append(dict(id=f"hold:{pid}", type="On hold", product_id=pid, impact_cases=round(impact, 1),
                       question=f"Is {names.get(pid, pid)} released from hold?",
                       detail=f"On hold since {since:%b %Y}. It usually sells about {typical.get(pid) or 0:,.0f} cases a month; "
                              f"we will not order it until you confirm."))
    # gaps still suspected after the confirmed holds
    sg = csv_out("suspected_gaps.csv", required=False)
    if len(sg):
        sg = sg.assign(impact=sg["months"] * sg["usual_monthly"]).nlargest(TOP_PER_TYPE, "impact")
        for r in sg.itertuples():
            s, e = pd.Timestamp(r.start), pd.Timestamp(r.end)
            qs.append(dict(id=f"gap:{r.product_id}:{s:%Y-%m}", type="Possible gap", product_id=r.product_id,
                           impact_cases=round(float(r.impact), 1),
                           question=f"Was {names.get(r.product_id, r.product_id)} unavailable {s:%b %Y}-{e:%b %Y}?",
                           detail=f"It sold nothing for {int(r.months)} months after usually selling about "
                                  f"{r.usual_monthly:,.0f} cases a month."))
    # large one-off orders
    spk = csv_out("spikes.csv", required=False)
    if len(spk):
        one = spk[spk["cause"] == "one_off"].assign(extra=lambda d: d["cases"] - d["baseline"]).nlargest(TOP_PER_TYPE, "extra")
        for r in one.itertuples():
            m = pd.Timestamp(r.month + "-01")
            qs.append(dict(id=f"oneoff:{r.product_id}:{r.month}", type="One-off order", product_id=r.product_id,
                           impact_cases=round(float(r.extra), 1),
                           question=f"Was the {m:%b %Y} sale of {r.cases:,.0f} cases of {names.get(r.product_id, r.product_id)} "
                                    f"a one-off (promotion or bulk order)?",
                           detail=(f"Usually about {r.baseline:,.0f} cases that month ({r.ratio:.1f}x)."
                                   if r.baseline > 0 and np.isfinite(r.ratio) else "It usually sells almost nothing that month.")
                                  + " The forecast does not repeat it."))
    # item codes not merged
    imp = data_dir() / "item_map.csv"
    if imp.exists():
        im = pd.read_csv(imp, dtype=str)
        conv = pd.to_numeric(im.get("qty_to_cases", 1), errors="coerce").fillna(1.0)
        im["cases"] = pd.to_numeric(im["cases_sold"], errors="coerce").fillna(0) * conv      # lb-recorded bulk -> bags
        merged = im["merged"].astype(str).str.lower() == "true"
        codes = set(im["raw_item"].str.upper())
        un = im[~merged & im["raw_item"].str.contains(r"OLD|TBD", case=False, regex=True)]
        un = un.nlargest(TOP_PER_TYPE, "cases")
        for r in un.itertuples():
            if str(r.base).upper() in codes:
                q = f"Is item code {r.raw_item} the same product as {r.base}?"
                d = f"{r.description}. Sold {r.cases:,.0f} cases under this code; it is kept separate until you confirm."
            else:
                q = f"Item code {r.raw_item} has no current code. Does it sell under another code now, or is it discontinued?"
                d = (f"{r.description}. Sold {r.cases:,.0f} cases under this code, last in "
                     f"{pd.Timestamp(r.last_sale):%b %Y}. If it moved to another code, tell us which (in the note).")
            qs.append(dict(id=f"code:{r.raw_item}", type="Item code", product_id=r.product_id,
                           impact_cases=round(float(r.cases), 1), question=q, detail=d))
    # new products
    plan = csv_out("order_plan.csv")
    new = plan[plan["reason"].str.startswith("new product")].groupby("product_id")["cases_needed"].sum()
    for pid, c in new.sort_values(ascending=False).head(TOP_PER_TYPE).items():
        qs.append(dict(id=f"new:{pid}", type="New product", product_id=pid, impact_cases=round(float(c), 1),
                       question=f"Will you keep selling {names.get(pid, pid)}?",
                       detail=f"It has less than 6 months of sales, so the plan uses its average selling month "
                              f"({c / 12:,.0f} cases a month). Is that about right?"))
    qs += stock_questions(names)
    qs += harvest_questions(names)
    qs += sourcing_questions(names)
    ans = answers()
    for q in qs:
        a = ans.get(q["id"])
        q["answer"] = a["answer"] if a else None
        q["note"] = a["note"] if a else ""
    qs.sort(key=lambda q: -q["impact_cases"])
    counts = pd.Series([q["type"] for q in qs]).value_counts().to_dict() if qs else {}
    return clean(dict(questions=qs, counts=counts, answered=sum(1 for q in qs if q["answer"])))


def stock_questions(names):
    """Questions that come from the inventory file, containers and harvest buys."""
    qs = []
    orders = csv_out("brand_orders.csv")
    flags = stock_flags(orders)
    if flags["stock_used"] and flags["on_order_missing"]:
        qs.append(dict(id="stock:on_order", type="Stock data", product_id=None, impact_cases=float(flags["orders_now_cases"]),
                       question="Your inventory file has no 'on order' column. Is stock already ordered or at sea missing from the plan?",
                       detail="Every order assumes nothing is on the way. If containers are at sea, the plan orders too much. "
                              "An export with quantity on order (and expected arrival) fixes this."))
    ev = csv_out("inventory_unit_evidence.csv", required=False)
    if len(ev):
        cases = sorted(ev.loc[ev["unit"] == "cases", "prefix"])
        qs.append(dict(id="stock:units", type="Stock data", product_id=None, impact_cases=float(flags["orders_now_cases"]),
                       question="Does the inventory count single packs (not cases) for most items"
                                + (f", but whole cases for codes starting {', '.join(cases)}?" if cases else "?"),
                       detail="We read it that way because it gives sensible stock levels (median about 3 months of sales). "
                              "If that is wrong, every order changes."))
    cov = csv_out("stock_cover_check.csv", required=False)
    if len(cov):
        bad = cov[cov["flag"].fillna("").str.startswith(("over", "negative"))].copy()
        bad["impact"] = bad["on_hand_cases"].abs()
        for r in bad.nlargest(TOP_PER_TYPE, "impact").itertuples():
            qs.append(dict(id=f"units:{r.product_id}", type="Stock looks wrong", product_id=r.product_id,
                           impact_cases=round(float(r.impact), 1),
                           question=f"Is the stock of {names.get(r.product_id, r.product_id)} right: {r.on_hand_cases:,.0f} cases?",
                           detail=(f"That is {r.months_of_cover:,.0f} months of its recent sales." if r.months_of_cover == r.months_of_cover
                                   else "It has not sold in the last 6 months.") + " It may be counted in a different unit."))
    un = csv_out("inventory_unmatched_codes.csv", required=False)
    if len(un):
        un = un[un["on_hand"].fillna(0) > 0].nlargest(TOP_PER_TYPE, "on_hand")
        for r in un.itertuples():
            qs.append(dict(id=f"invcode:{r.raw_item}", type="Inventory code", product_id=None, impact_cases=0.0,
                           question=f"Inventory item {r.raw_item} ({str(r.description)[:60]}) has no sales under that code. "
                                    f"Is it sold under another code, or is it packaging/material?",
                           detail=f"{r.on_hand:,.0f} units on hand. It is left out of the plan until you say which product it is."))
    cbm = csv_out("case_cbm_used.csv", required=False)
    if len(cbm):
        lines = csv_out("brand_order_lines.csv")
        vol = lines.groupby("product_id")["cases"].sum() if "cases" in lines else pd.Series(dtype=float)
        est = cbm[cbm["cbm_source"] != "case_cbm.csv"].assign(cases=lambda d: d["product_id"].map(vol).fillna(0))
        est = est[est["cases"] > 0].nlargest(TOP_PER_TYPE, "cases")
        for r in est.itertuples():
            qs.append(dict(id=f"cbm:{r.product_id}", type="Case size", product_id=r.product_id, impact_cases=float(r.cases),
                           question=f"What is the case size (CBM, or length x width x height) of {names.get(r.product_id, r.product_id)}?",
                           detail=(f"Container counts use an estimate of {r.cbm_per_case:.3f} CBM per case from its weight."
                                   if r.cbm_per_case == r.cbm_per_case else "We have no weight or size for it, so it is not in the container counts.")))
        qs.append(dict(id="container:limits", type="Containers", product_id=None, impact_cases=float(flags["orders_now_cases"]),
                       question="Is 80 CBM the right usable space per container, and what is the weight limit?",
                       detail="Containers are counted by volume only until a weight limit is set (config/containers.csv)."))
    hc = csv_out("harvest_candidates.csv", required=False)
    if len(hc):
        prof = csv_out("product_profiles.csv", required=False)
        typ = prof.set_index("product_id")["typical_monthly_cases"] if len(prof) else pd.Series(dtype=float)
        for rule, g in hc.groupby("rule"):
            yearly = float(12 * g["product_id"].map(typ).fillna(0).sum())
            qs.append(dict(id=f"harvest:{rule}", type="Annual buy", product_id=None, impact_cases=round(yearly, 0),
                           question=f"Is {rule.replace('_', ' ')} bought once a year after the harvest? In which months do you order it, and when does it arrive?",
                           detail=f"{len(g)} products match (about {yearly:,.0f} cases a year). Until you confirm, they are ordered like other products."))
    return qs


def harvest_questions(names):
    """Harvest calendar rows the owner hasn't confirmed (harvest:<commodity>_<country>) and products the
    commodity map couldn't place (commodity:<PID>), from outputs/harvest_plan.csv and commodity_unmapped.csv."""
    qs = []
    hp = csv_out("harvest_plan.csv", required=False)
    if len(hp) and "confirmed" in hp:
        todo = hp[(hp["confirmed"].astype(str) == "no") & hp["rule_id"].notna()
                  & (pd.to_numeric(hp["products"], errors="coerce") > 0)]
        todo = todo.assign(impact=pd.to_numeric(todo["yearly_cases"], errors="coerce").fillna(0)).nlargest(TOP_PER_TYPE, "impact")
        for r in todo.itertuples():
            crop, country = str(r.commodity).replace("_", " "), r.source_country
            hint = f" Item names suggest {r.origin_hints}." if isinstance(r.origin_hints, str) and r.origin_hints else ""
            if isinstance(r.harvest_window, str) and r.harvest_window:
                q = f"Is {crop} bought from {country} once a season, from the new crop (harvest {r.harvest_window})?"
                d = (f"Source: {r.source_url} ({r.confidence} confidence). If yes, we order by {r.order_by} "
                     f"({int(r.quantity_cases):,} cases, to last until the next harvest arrives). If the months are wrong, "
                     f"write them in the note, e.g. 'harvest Oct-Dec'. No = keep ordering it the regular way.{hint}")
            else:
                q = f"Is {crop} bought from {country} by harvest? Which months is the harvest there?"
                d = (f"We found no source that states the harvest months. If yes, write them in the note, e.g. "
                     f"'harvest Oct-Dec'. No = keep ordering it the regular way.{hint}")
            qs.append(dict(id=f"harvest:{r.rule_id}", type="Harvest season", product_id=None, impact_cases=float(r.impact),
                           question=q, detail=d))
    un = csv_out("commodity_unmapped.csv", required=False)
    if len(un) and "reason" in un:
        prof = csv_out("product_profiles.csv", required=False)
        typ = prof.set_index("product_id")["typical_monthly_cases"] if len(prof) else pd.Series(dtype=float)
        un = un.assign(impact=12 * un["product_id"].map(typ).fillna(0)).nlargest(TOP_PER_TYPE, "impact")
        for r in un.itertuples():
            name = names.get(r.product_id, r.name)
            cands = [c.replace("_", " ") for c in str(r.candidates).split("|") if c and c != "nan"]
            if r.reason == "name matches several commodities":
                q = f"Which crop is {name}: {' or '.join(cands)}?"
            elif r.reason == "mixed pack of several crops":
                q = f"{name} mixes several crops. Should it be bought by harvest with one of them? Which?"
            else:
                q = f"Is {name} a harvest crop (bought once a season, from the new crop)? If yes, which one?"
            qs.append(dict(id=f"commodity:{r.product_id}", type="Which crop", product_id=r.product_id,
                           impact_cases=round(float(r.impact), 1), question=q,
                           detail="Write the crop in the note (e.g. toor, moong, chana, basmati, almond, cashew). "
                                  "No = it isn't a harvest crop: keep ordering it the regular way."))
    return qs


def sourcing_questions(names):
    """Unverified supplier leads that could serve an at-risk product (supplier:<LEAD_ID>) and active country alerts
    (alert:<ALERT_ID>), from outputs/sourcing_option_lines.csv, supplier_leads_used.csv and country_alerts_active.csv."""
    qs = []
    lines = csv_out("sourcing_option_lines.csv", required=False)
    if not len(lines) or "lead_id" not in lines:
        return qs
    prof = csv_out("product_profiles.csv", required=False)
    typ = prof.set_index("product_id")["typical_monthly_cases"] if len(prof) else pd.Series(dtype=float)
    lines = lines.assign(yearly=12 * lines["product_id"].map(typ).fillna(0))
    leads = csv_out("supplier_leads_used.csv", required=False)
    if len(leads) and "status" in leads:
        todo = leads[leads["status"] == "unverified_lead"]
        for l in todo.itertuples():
            mine = lines[lines["lead_id"] == l.lead_id]
            if mine.empty:
                continue                      # only leads that could help an at-risk product
            prods = ", ".join(sorted({names.get(p, p) for p in mine["product_id"]}))[:160]
            qs.append(dict(id=f"supplier:{l.lead_id}", type="Supplier lead", product_id=None,
                           impact_cases=round(float(mine.drop_duplicates("product_id")["yearly"].sum()), 1),
                           question=f"Is {l.company} ({l.country}; lists {str(l.commodities).replace(';', ', ')}) a supplier you "
                                    f"buy from, or would buy from?",
                           detail=f"Could help with: {prods}. Found on {l.found_on} at {l.source_url}. Unverified: nobody has "
                                  f"contacted them. Yes = confirmed supplier (ranked first); No = not a supplier (never "
                                  f"suggested again). Your note is kept with the lead."))
    alerts = csv_out("country_alerts_active.csv", required=False)
    if len(alerts) and "alert_id" in alerts:
        for a in alerts.itertuples():
            hit = lines[lines["alert"].fillna("").str.contains(str(a.issue), regex=False)]
            qs.append(dict(id=f"alert:{a.alert_id}", type="Country alert", product_id=None,
                           impact_cases=round(float(hit.drop_duplicates("product_id")["yearly"].sum()), 1),
                           question=f"Is this still a problem: {a.issue} ({a.country}, {a.commodity})?",
                           detail=f"Reported {a.reported_on} ({a.source_url}); expires {a.expires_on}. While active, sources in "
                                  f"{a.country} rank below the others. Yes = keep it; No = dismiss it."))
    return qs[:2 * TOP_PER_TYPE]


class Answer(BaseModel):
    question_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(pattern="^(yes|no|not sure)$")
    note: str = Field(default="", max_length=2000)


@app.post("/api/answers")
def save_answer(a: Answer):
    p = data_dir() / "owner_answers.csv"
    new = not p.exists()
    row = dict(question_id=a.question_id, answer=a.answer, note=a.note.replace("\n", " "),
               timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    with open(p, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ANSWER_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    return row


# ---------------------------------------------------------------- forecast quality
@app.get("/api/forecast-quality")
def forecast_quality():
    p = out_dir() / "simulation_summary.json"
    if not p.exists():
        raise HTTPException(503, "simulation_summary.json not found: run `python run.py`")
    data = clean(json.loads(p.read_text()))     # older files may hold NaN
    t = out_dir() / "forecaster_tournament.json"
    data["tournament"] = clean(json.loads(t.read_text())) if t.exists() else None
    return data
