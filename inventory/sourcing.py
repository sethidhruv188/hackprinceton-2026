"""Sourcing intelligence: which products are at risk, where else they grow, who might supply them, and what
one order could cover. Pure data: reads files, never searches the web, never contacts anyone.

Inputs (all optional; missing data is stated, never guessed):
  <data>/supplier_leads.csv   lead_id, company, country, commodities ("a;b"), website, source_url, found_on,
                              status (unverified_lead / confirmed / rejected), owner_note, is_demo.
                              Real leads live only in data/ (gitignored); data_demo/ holds clearly fake ones.
                              Filled later by the agent's supplier_scout through add_leads(); a vendor list can be
                              loaded with import_vendor_list(). No contact fields exist, by design.
  <data>/country_alerts.csv   alert_id, commodity (or "all"), country, issue, source_url, reported_on, expires_on
                              (default reported_on + 60 days), status (active / dismissed), is_demo. Filled through
                              add_alerts(). Demo alerts are ignored in the real data folder.
  outputs from run.py         product_status, brand_order_lines, brand_orders, harvest_orders, harvest_plan,
                              stock_projection, commodity_map_used, holds_now; config/harvest_calendar.csv.

Ranking of sources for an at-risk product (labels always shown): confirmed supplier, then unverified lead, then a
producing country with no lead yet; rejected leads are listed, never ranked. Within a tier: a country with an active
alert ranks below the others; then arrival in time (105-130 days vs the run-out date: the same for every source until
suppliers' own lead times are known), harvest timing (in harvest, just after, sooner first, unknown last),
consolidation value (other commodities the lead lists that we need), and whether the owner left a note.

The design of the lead research (search per commodity and producing country, record the source of every company,
dedupe, label reliability) is inspired by the open-source "harvey" outreach agent (github.com/ethanplusai/harvey,
AGPL-3.0); no code is taken from it, and unlike it nothing here sends anything.
"""
import hashlib
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from . import harvest as H

ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = ROOT / "data"
DEMO_DATA = ROOT / "data_demo"
LEAD_COLUMNS = ["lead_id", "company", "country", "commodities", "website", "source_url", "found_on", "status",
                "owner_note", "is_demo"]
ALERT_COLUMNS = ["alert_id", "commodity", "country", "issue", "source_url", "reported_on", "expires_on", "status", "is_demo"]
LEAD_STATUSES = {"unverified_lead", "confirmed", "rejected"}
CONTACT_WORDS = re.compile(r"e-?mail|phone|mobile|whatsapp|contact|person|telephone|fax", re.IGNORECASE)
ALERT_DAYS = 60
SOON_DAYS = 60                   # brand-order needs due within this many days count for consolidation
PLANNED_DAYS = 365               # confirmed harvest buys within a year are listed too (with their date)
JUST_AFTER_MONTHS = 2
LEAD_EARLY, LEAD_LATE = 105, 130
TIER = {"confirmed": 0, "unverified_lead": 1, "country": 2}
TIER_LABEL = {"confirmed": "confirmed supplier", "unverified_lead": "unverified lead (not contacted)",
              "country": "producing country, no lead yet", "rejected": "rejected by owner"}
OPTION_COLUMNS = ["product_id", "name", "commodity", "rank", "tier", "label", "country", "lead_id", "company",
                  "harvest_timing", "alert", "consolidation", "owner_note", "source_url"]
SUMMARY_COLUMNS = ["product_id", "name", "brand", "commodity", "risk", "run_out_date", "arrival_if_ordered_today",
                   "alternative_countries", "suppliers_and_leads", "consolidation", "alerts", "unknowns"]


def country_key(text):
    """'Côte d'Ivoire' and 'Cote d'Ivoire' -> 'cote_d_ivoire'."""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")


def _id(prefix, *parts):
    return prefix + hashlib.sha1("|".join(country_key(p) for p in parts).encode()).hexdigest()[:8]


def _is_real(data_dir):
    return Path(data_dir).resolve() == REAL_DATA.resolve()


def _is_demo_dir(data_dir):
    return Path(data_dir).resolve() == DEMO_DATA.resolve()


def _yes(v):
    return str(v).strip().lower() in ("yes", "true", "1")


# ------------------------------------------------------------------ loading
def _read(path, columns):
    if not Path(path).exists():
        return pd.DataFrame(columns=columns)
    return pd.read_csv(path, dtype=str, keep_default_na=False).reindex(columns=columns).fillna("")


def load_leads(data_dir, answers=None):
    """Leads with a `label`, after the owner's supplier:LEAD_ID answers. In the real data folder demo rows are dropped;
    in data_demo/ non-demo rows are an error."""
    leads = _read(Path(data_dir) / "supplier_leads.csv", LEAD_COLUMNS)
    demo = leads["is_demo"].map(_yes)
    if _is_demo_dir(data_dir) and (~demo).any():
        raise ValueError("data_demo/supplier_leads.csv holds non-demo rows: real company names never go into the repo")
    if _is_real(data_dir):
        leads = leads[~demo]
    leads = leads.copy()
    if answers is not None:
        from . import answers as A
        leads = A.supplier_answers(answers, leads)
    leads["status"] = leads["status"].where(leads["status"].isin(LEAD_STATUSES), "unverified_lead")
    leads["label"] = leads["status"].map(TIER_LABEL)
    leads["commodity_list"] = leads["commodities"].map(lambda s: [c.strip() for c in str(s).split(";") if c.strip()])
    leads["country_key"] = leads["country"].map(country_key)
    return leads.reset_index(drop=True)


def _demo_commodities(data_dir):
    """Commodities the products in a data folder map to (config/commodity_map.csv)."""
    p = Path(data_dir) / "products.csv"
    if not p.exists():
        return set()
    mapped, _ = H.map_products(pd.read_csv(p, dtype={"product_id": str}), H.load_map())
    return set(mapped["commodity"]) - {""}


def load_alerts(data_dir, today, answers=None):
    """Active, unexpired alerts, after the owner's alert:ALERT_ID answers (no = dismissed), each with a `label`:
    "demo" (fake, demo folder only) or "real news". Demo alerts never count in the real data folder. In the demo folder,
    real-news alerts from data/country_alerts.csv are shown too (public information) for the demo's commodities."""
    al = _read(Path(data_dir) / "country_alerts.csv", ALERT_COLUMNS)
    if _is_real(data_dir):
        al = al[~al["is_demo"].map(_yes).astype(bool)]
    elif _is_demo_dir(data_dir) and (REAL_DATA / "country_alerts.csv").exists():
        real = _read(REAL_DATA / "country_alerts.csv", ALERT_COLUMNS)
        real = real[(~real["is_demo"].map(_yes).astype(bool)) & real["commodity"].isin(_demo_commodities(data_dir) | {"all"})]
        al = pd.concat([al, real[~real["alert_id"].isin(al["alert_id"])]], ignore_index=True)
    al = al.copy()
    al["label"] = np.where(al["is_demo"].map(_yes).astype(bool), "demo", "real news") if len(al) else pd.Series(dtype=str)
    if answers is not None:
        from . import answers as A
        al = A.alert_answers(answers, al)
    rep = pd.to_datetime(al["reported_on"], errors="coerce")
    exp = pd.to_datetime(al["expires_on"], errors="coerce").fillna(rep + pd.Timedelta(days=ALERT_DAYS))
    al["expires"] = exp
    al["active"] = (al["status"].str.strip().str.lower() != "dismissed") & (exp >= pd.Timestamp(today).normalize())
    al["country_key"] = al["country"].map(country_key)
    return al.reset_index(drop=True)


def alerts_for(alerts, commodity, country):
    k = country_key(country)
    hit = alerts[alerts["active"] & (alerts["country_key"] == k) & alerts["commodity"].isin([commodity, "all"])]
    return "; ".join(f"alert: {r.issue} ({r.source_url})" for r in hit.itertuples())


# ------------------------------------------------------------------ writing (agent entry points)
def _validate_dir(data_dir, is_demo):
    if _is_real(data_dir) and is_demo:
        return "demo rows never go into the real data folder"
    if _is_demo_dir(data_dir) and not is_demo:
        return "only demo rows go into data_demo/ (it is committed to the public repo)"
    return None


def add_leads(rows, data_dir, today=None, cmap=None):
    """Add supplier leads found by research (the supplier_scout sub-agent). Every lead needs a company, a country,
    commodities known to config/commodity_map.csv, a source_url (http...) and becomes `unverified_lead` (only the owner
    confirms). Contact details are refused. Re-adding a known company+country merges its commodities.
    Returns dict(added, updated, rejected=[(row, reason)]); never raises on a bad row."""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    known = set((H.load_map() if cmap is None else cmap)["commodity"])
    path = Path(data_dir) / "supplier_leads.csv"
    leads = _read(path, LEAD_COLUMNS)
    added, updated, rejected = 0, 0, []
    for row in rows:
        r = {k: ("" if v is None else str(v).strip()) for k, v in dict(row).items()}
        contact = [k for k in r if CONTACT_WORDS.search(k)]
        coms = [c.strip() for c in r.get("commodities", "").split(";") if c.strip()]
        demo = _yes(r.get("is_demo", ""))
        why = (f"contact fields are not stored ({', '.join(contact)})" if contact else
               "company, country and commodities are required" if not (r.get("company") and r.get("country") and coms) else
               f"unknown commodities: {', '.join(c for c in coms if c not in known)}" if any(c not in known for c in coms) else
               "a source_url (http...) is required" if not r.get("source_url", "").startswith("http") else
               _validate_dir(data_dir, demo))
        if why:
            rejected.append((row, why))
            continue
        lid = _id("L", r["company"], r["country"])
        hit = leads["lead_id"] == lid
        if hit.any():
            old = [c for c in leads.loc[hit, "commodities"].iloc[0].split(";") if c]
            leads.loc[hit, "commodities"] = ";".join(dict.fromkeys(old + coms))
            updated += 1
            continue
        leads = pd.concat([leads, pd.DataFrame([dict(lead_id=lid, company=r["company"], country=r["country"],
                                                     commodities=";".join(dict.fromkeys(coms)), website=r.get("website", ""),
                                                     source_url=r["source_url"], found_on=r.get("found_on") or f"{today:%Y-%m-%d}",
                                                     status="unverified_lead", owner_note="", is_demo="yes" if demo else "no")])],
                          ignore_index=True)
        added += 1
    if added or updated:
        leads[LEAD_COLUMNS].to_csv(path, index=False)
    return dict(added=added, updated=updated, rejected=rejected)


def add_alerts(rows, data_dir, today=None, cmap=None):
    """Add country alerts (a producing country has a problem). Each needs a commodity (known, or "all"), a country,
    an issue and a source_url; expires_on defaults to reported_on + 60 days; status starts active.
    Returns dict(added, updated, rejected=[(row, reason)]); never raises on a bad row."""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    known = set((H.load_map() if cmap is None else cmap)["commodity"]) | {"all"}
    path = Path(data_dir) / "country_alerts.csv"
    alerts = _read(path, ALERT_COLUMNS)
    added, updated, rejected = 0, 0, []
    for row in rows:
        r = {k: ("" if v is None else str(v).strip()) for k, v in dict(row).items()}
        demo = _yes(r.get("is_demo", ""))
        rep = pd.to_datetime(r.get("reported_on") or today, errors="coerce")
        why = ("commodity, country and issue are required" if not (r.get("commodity") and r.get("country") and r.get("issue")) else
               f"unknown commodity: {r['commodity']}" if r["commodity"] not in known else
               "a source_url (http...) is required" if not r.get("source_url", "").startswith("http") else
               "reported_on is not a date" if pd.isna(rep) else
               "demo alert issues must start with 'DEMO:'" if demo and not r["issue"].startswith("DEMO:") else
               _validate_dir(data_dir, demo))
        if why:
            rejected.append((row, why))
            continue
        aid = _id("A", r["commodity"], r["country"], r["issue"], f"{rep:%Y-%m-%d}")
        exp = pd.to_datetime(r.get("expires_on") or (rep + pd.Timedelta(days=ALERT_DAYS)), errors="coerce")
        new = dict(alert_id=aid, commodity=r["commodity"], country=r["country"], issue=r["issue"], source_url=r["source_url"],
                   reported_on=f"{rep:%Y-%m-%d}", expires_on=f"{exp:%Y-%m-%d}", status="active", is_demo="yes" if demo else "no")
        same_source = ((alerts["commodity"] == r["commodity"]) & (alerts["country"].map(country_key) == country_key(r["country"]))
                       & (alerts["source_url"] == r["source_url"])) if len(alerts) else pd.Series(dtype=bool)
        if (alerts["alert_id"] == aid).any() or same_source.any():
            updated += 1                      # already known (same id, or same commodity + country + source)
            continue
        alerts = pd.concat([alerts, pd.DataFrame([new])], ignore_index=True)
        added += 1
    if added:
        alerts[ALERT_COLUMNS].to_csv(path, index=False)
    return dict(added=added, updated=updated, rejected=rejected)


def import_vendor_list(path, data_dir, columns=None, today=None):
    """Hook for a vendor list, should one become available: rows become CONFIRMED suppliers (source: the file).
    columns: {our column: their column} for company, country, commodities (defaults try common names)."""
    v = pd.read_csv(path, dtype=str).fillna("")
    guess = {"company": ["company", "vendor", "supplier", "name"], "country": ["country", "origin"],
             "commodities": ["commodities", "commodity", "products", "items"]}
    cols = {k: (columns or {}).get(k) or next((c for c in v.columns if c.strip().lower() in g), None) for k, g in guess.items()}
    missing = [k for k, c in cols.items() if c is None]
    if missing:
        raise ValueError(f"vendor list needs columns for: {', '.join(missing)}")
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out = _read(Path(data_dir) / "supplier_leads.csv", LEAD_COLUMNS)
    rows = []
    for r in v.itertuples(index=False):
        d = r._asdict()
        company, country = d[cols["company"]].strip(), d[cols["country"]].strip()
        if not company or not country:
            continue
        coms = ";".join(c.strip() for c in re.split(r"[;,/]", d[cols["commodities"]]) if c.strip())
        rows.append(dict(lead_id=_id("L", company, country), company=company, country=country, commodities=coms, website="",
                         source_url=f"vendor list: {Path(path).name}", found_on=f"{today:%Y-%m-%d}", status="confirmed",
                         owner_note="", is_demo="no"))
    new = pd.DataFrame(rows, columns=LEAD_COLUMNS)
    out = pd.concat([out[~out["lead_id"].isin(new["lead_id"])], new], ignore_index=True)
    out[LEAD_COLUMNS].to_csv(Path(data_dir) / "supplier_leads.csv", index=False)
    return len(new)


# ------------------------------------------------------------------ analysis
def _csv(out, name, **kw):
    p = Path(out) / name
    return pd.read_csv(p, dtype={"product_id": str}, **kw) if p.exists() else pd.DataFrame()


def products_at_risk(out_dir, today):
    """At-risk products from the engine's own flags (no new math): below the 3-month floor now or before a restock
    (expedite), running out before the order arrives, a harvest buy needing a bridge order, or an order too late for
    the full lead time. Products on hold are left out. Returns (DataFrame, note); without stock data the frame is
    empty and the note says why."""
    today = pd.Timestamp(today).normalize()
    st = _csv(out_dir, "product_status.csv")
    if st.empty:
        return pd.DataFrame(columns=["product_id", "risk", "run_out_date"]), "no stock data: risk can't be assessed"
    holds = set(_csv(out_dir, "holds_now.csv").get("product_id", pd.Series(dtype=str)))
    risk = {}

    def add(pid, text, date=None):
        e = risk.setdefault(pid, dict(flags=[], dates=[]))
        if text not in e["flags"]:
            e["flags"].append(text)
        if date is not None and pd.notna(date):
            e["dates"].append(pd.Timestamp(date))

    below = st["below_floor_now"].astype(str).str.lower().eq("true")
    for r in st[below].itertuples():
        add(r.product_id, "below the 3-month floor now")
    for r in st[st["floor_dip_date"].notna() & ~below].itertuples():
        add(r.product_id, f"dips below the 3-month floor before a restock can land (from {r.floor_dip_date}): expedite")
    lines = _csv(out_dir, "brand_order_lines.csv")
    if "runs_out_before_arrival" in lines:
        for r in lines[lines["runs_out_before_arrival"].notna() & ~lines["on_hold"].astype(str).str.lower().eq("true")].itertuples():
            add(r.product_id, f"runs out around {r.runs_out_before_arrival}, before its order arrives", r.runs_out_before_arrival)
    ho = _csv(out_dir, "harvest_orders.csv")
    if len(ho):
        for r in ho[pd.to_numeric(ho["projected_stock_at_arrival"], errors="coerce") < 0].itertuples():
            add(r.product_id, f"harvest buy: runs out before the harvest order lands (arrives in {pd.Timestamp(r.expected_arrival):%b %Y}): "
                              f"bridge order needed")
    orders = _csv(out_dir, "brand_orders.csv")
    if len(orders) and len(lines):
        late = set(orders.loc[orders["status"].str.startswith("Too late"), "order_id"])
        for r in lines[lines["order_id"].isin(late)].itertuples():
            add(r.product_id, "its order is already too late for the full lead time")
    proj = _csv(out_dir, "stock_projection.csv")
    first_zero = {}
    if len(proj):
        z = proj[pd.to_numeric(proj["projected_stock"], errors="coerce") <= 0]
        first_zero = z.groupby("product_id")["month"].min().to_dict()
    rows = []
    for pid, e in risk.items():
        if pid in holds:
            continue
        date = min(e["dates"]) if e["dates"] else (pd.Timestamp(first_zero[pid] + "-01") if pid in first_zero else None)
        rows.append(dict(product_id=pid, risk="; ".join(e["flags"]), run_out_date=f"{date:%Y-%m-%d}" if date is not None else ""))
    return pd.DataFrame(rows, columns=["product_id", "risk", "run_out_date"]), ""


def arrival_check(run_out_date, today):
    """Order placed today, sea freight 105-130 days, vs the run-out date. The same for every source."""
    if not run_out_date:
        return "run-out date unknown"
    today, ro = pd.Timestamp(today).normalize(), pd.Timestamp(run_out_date)
    early, late = today + pd.Timedelta(days=LEAD_EARLY), today + pd.Timedelta(days=LEAD_LATE)
    if late <= ro:
        return f"in time even at {LEAD_LATE} days (arrives by {late:%b %d, %Y}; runs out {ro:%b %d, %Y})"
    if early <= ro:
        return f"in time only if shipping takes about {LEAD_EARLY} days (runs out {ro:%b %d, %Y})"
    return f"too late for sea freight (earliest arrival {early:%b %d, %Y}; runs out {ro:%b %d, %Y}): expedite or air"


def harvest_timing(row, today):
    """(label, sort key) for one calendar row: in harvest now, just after harvest, harvest in N months, unknown."""
    if not row.get("harvest_start_month"):
        return "harvest months unknown", 9.0
    today = pd.Timestamp(today).normalize()
    w = H.windows(dict(row, order_months_before="0"), today)
    if w["harvest_start"] <= today <= w["harvest_end"]:
        return f"in harvest now ({H.window_label(row['harvest_start_month'], row['harvest_end_month'])})", 0.0
    last_end = max(e for e in [w["harvest_end"] - pd.DateOffset(years=k) for k in range(3)] if e < today)
    months_after = (today.year - last_end.year) * 12 + today.month - last_end.month
    if months_after <= JUST_AFTER_MONTHS:
        return f"just after harvest (ended {last_end:%b %Y})", 1.0
    nxt = w["harvest_start"] if w["harvest_start"] > today else w["harvest_start"] + pd.DateOffset(years=1)
    months = (nxt.year - today.year) * 12 + nxt.month - today.month
    return f"next harvest in {months} month(s) ({nxt:%b %Y})", 2.0 + months / 12


def alternative_sources(commodity, today, calendar=None, leads=None, alerts=None):
    """Producing countries for a commodity (config/harvest_calendar.csv) with harvest timing and alerts, plus the
    leads/suppliers in those countries; leads in other countries that list the commodity are added too."""
    cal = H.load_calendar() if calendar is None else calendar
    leads = pd.DataFrame(columns=LEAD_COLUMNS + ["label", "commodity_list", "country_key"]) if leads is None else leads
    alerts = pd.DataFrame(columns=ALERT_COLUMNS + ["active", "country_key"]) if alerts is None else alerts
    rows = []
    rows_cal = cal[cal["commodity"] == commodity]
    for _, c in rows_cal.iterrows():
        label, key = harvest_timing(c, today)
        rows.append(dict(country=c["source_country"], country_key=country_key(c["source_country"]), harvest_timing=label,
                         timing_key=key, calendar_source=c.get("source_url", ""), alert=alerts_for(alerts, commodity, c["source_country"])))
    have = {r["country_key"] for r in rows}
    for _, l in leads[leads["commodity_list"].map(lambda cs: commodity in cs)].iterrows():
        if l["country_key"] not in have:
            rows.append(dict(country=l["country"], country_key=l["country_key"], harvest_timing="country not in the harvest calendar",
                             timing_key=9.5, calendar_source="", alert=alerts_for(alerts, commodity, l["country"])))
            have.add(l["country_key"])
    countries = pd.DataFrame(rows, columns=["country", "country_key", "harvest_timing", "timing_key", "calendar_source", "alert"])
    mine = leads[leads["commodity_list"].map(lambda cs: commodity in cs)]
    return countries, mine


def needs(out_dir, today, mapped=None):
    """What we need soon, by commodity: brand-order lines due within SOON_DAYS and confirmed harvest buys within
    PLANNED_DAYS. Returns commodity, due, cases, what."""
    today = pd.Timestamp(today).normalize()
    mapped = _csv(out_dir, "commodity_map_used.csv") if mapped is None else mapped
    cmap = mapped.set_index("product_id")["commodity"].to_dict() if len(mapped) else {}
    rows = []
    orders, lines = _csv(out_dir, "brand_orders.csv"), _csv(out_dir, "brand_order_lines.csv")
    if len(orders) and len(lines):
        due = orders[["order_id", "order_by_safe"]].assign(d=lambda d: pd.to_datetime(d["order_by_safe"], errors="coerce"))
        soon = due[due["d"] <= today + pd.Timedelta(days=SOON_DAYS)]
        l = lines[lines["order_id"].isin(soon["order_id"]) & ~lines["on_hold"].astype(str).str.lower().eq("true")]
        l = l.merge(soon[["order_id", "d"]], on="order_id")
        l = l[~l.get("harvest", pd.Series(False, index=l.index)).astype(str).str.lower().eq("true")]
        for r in l.itertuples():
            c = cmap.get(r.product_id)
            if c:
                rows.append(dict(commodity=c, due=r.d, cases=float(r.cases), what="brand order"))
    hp = _csv(out_dir, "harvest_plan.csv")
    if len(hp):
        h = hp[(hp["in_orders"] == "yes")].assign(d=lambda d: pd.to_datetime(d["order_by"], errors="coerce"))
        for r in h[h["d"] <= today + pd.Timedelta(days=PLANNED_DAYS)].itertuples():
            rows.append(dict(commodity=r.commodity, due=r.d, cases=float(r.quantity_cases or 0), what="planned harvest buy"))
    n = pd.DataFrame(rows, columns=["commodity", "due", "cases", "what"])
    if n.empty:
        return n
    return n.groupby(["commodity", "what"]).agg(due=("due", "min"), cases=("cases", "sum")).reset_index().sort_values("due")


def consolidation_options(lead_id, today, out_dir=None, leads=None, need=None, exclude=()):
    """Other commodities this lead lists that we need soon or have planned, so one order / container can cover several.
    Returns commodity, what, due, cases."""
    lead = leads[leads["lead_id"] == lead_id]
    if lead.empty:
        return pd.DataFrame(columns=["commodity", "what", "due", "cases"])
    need = needs(out_dir, today) if need is None else need
    coms = [c for c in lead["commodity_list"].iloc[0] if c not in set(exclude)]
    return need[need["commodity"].isin(coms)].reset_index(drop=True)


def _describe_consolidation(cons):
    return "; ".join(f"{r.commodity.replace('_', ' ')} ({r.what}, {r.cases:,.0f} cases, by {r.due:%b %d, %Y})" for r in cons.itertuples())


def options_for(product, commodity, today, cal, leads, alerts, need):
    """Ranked sources for one at-risk product: one row per lead (confirmed / unverified) and per producing country
    without a lead; rejected leads listed last without a rank."""
    countries, mine = alternative_sources(commodity, today, cal, leads, alerts)
    timing = countries.set_index("country_key")
    rows = []
    for _, l in mine.iterrows():
        t = timing.loc[l["country_key"]] if l["country_key"] in timing.index else None
        cons = consolidation_options(l["lead_id"], today, leads=leads, need=need, exclude=[commodity])
        rows.append(dict(tier=l["status"], label=TIER_LABEL[l["status"]], country=l["country"], lead_id=l["lead_id"],
                         company=l["company"], harvest_timing=t["harvest_timing"] if t is not None else "unknown",
                         timing_key=t["timing_key"] if t is not None else 9.0, alert=alerts_for(alerts, commodity, l["country"]),
                         consolidation=_describe_consolidation(cons), cons_n=len(cons), owner_note=l["owner_note"],
                         source_url=l["source_url"]))
    with_lead = {country_key(r["country"]) for r in rows if r["tier"] != "rejected"}
    for c in countries.itertuples():
        if c.country_key not in with_lead:
            rows.append(dict(tier="country", label=TIER_LABEL["country"], country=c.country, lead_id="", company="",
                             harvest_timing=c.harvest_timing, timing_key=c.timing_key, alert=c.alert, consolidation="", cons_n=0,
                             owner_note="", source_url=c.calendar_source))
    o = pd.DataFrame(rows)
    if o.empty:
        return pd.DataFrame(columns=OPTION_COLUMNS)
    o["tier_key"] = o["tier"].map(TIER).fillna(9)
    o["alert_key"] = (o["alert"] != "").astype(int)
    o["note_key"] = (o["owner_note"] == "").astype(int)
    o = o.sort_values(["tier_key", "alert_key", "timing_key", "cons_n", "note_key", "country"],
                      ascending=[True, True, True, False, True, True]).reset_index(drop=True)
    ranked = o["tier"] != "rejected"
    o["rank"] = np.where(ranked, ranked.cumsum(), np.nan)
    o = o.assign(product_id=product["product_id"], name=product.get("name", ""), commodity=commodity)
    return o.reindex(columns=OPTION_COLUMNS)


def run(data_dir, out_dir, today, calendar=None, answers=None):
    """outputs/sourcing_options.csv (one row per at-risk product) and sourcing_option_lines.csv (ranked sources)."""
    out_dir, today = Path(out_dir), pd.Timestamp(today).normalize()
    risk, note = products_at_risk(out_dir, today)
    mapped = _csv(out_dir, "commodity_map_used.csv")
    products = _csv(Path(data_dir), "products.csv")
    brands = _csv(out_dir, "product_brands.csv")
    names = products.set_index("product_id")["name"].to_dict() if len(products) else {}
    brand = brands.set_index("product_id")["brand"].to_dict() if len(brands) else {}
    com = mapped[mapped["harvest"] != "no"].set_index("product_id")["commodity"].to_dict() if len(mapped) else {}
    cal = H.load_calendar() if calendar is None else calendar
    leads, alerts = load_leads(data_dir, answers), load_alerts(data_dir, today, answers)
    need = needs(out_dir, today, mapped)
    summary, lines = [], []
    for r in risk.itertuples():
        c = com.get(r.product_id, "")
        unknowns = []
        o = pd.DataFrame(columns=OPTION_COLUMNS)
        if not c:
            unknowns.append("no commodity mapped for this product: no producing countries or leads to suggest")
        else:
            o = options_for(dict(product_id=r.product_id, name=names.get(r.product_id, "")), c, today, cal, leads, alerts, need)
            lines.append(o)
            cal_c = cal[cal["commodity"] == c]
            if cal_c.empty:
                unknowns.append(f"{c} has no rows in the harvest calendar")
            blank = cal_c.loc[cal_c["harvest_start_month"] == "", "source_country"].tolist()
            if blank:
                unknowns.append(f"harvest months unknown for {', '.join(blank)}")
            if not (o["tier"].isin(["confirmed", "unverified_lead"])).any():
                unknowns.append(f"no supplier leads for {c} yet")
        unknowns += ["no supplier-specific lead times (105-130 days assumed for every source)", "no prices"]
        ranked = o[o["rank"].notna()] if len(o) else o
        alts = sorted({(rr.country, rr.harvest_timing) for rr in ranked.itertuples()}, key=lambda x: x[0]) if len(ranked) else []
        top = ranked[ranked["tier"].isin(["confirmed", "unverified_lead"])].head(3) if len(ranked) else ranked
        summary.append(dict(product_id=r.product_id, name=names.get(r.product_id, ""), brand=brand.get(r.product_id, ""),
                            commodity=c, risk=r.risk, run_out_date=r.run_out_date,
                            arrival_if_ordered_today=arrival_check(r.run_out_date, today),
                            alternative_countries="; ".join(f"{k}: {t}" for k, t in alts),
                            suppliers_and_leads="; ".join(f"#{int(x.rank)} {x.company} ({x.country}) [{x.label}]"
                                                          + (f" [{x.alert}]" if x.alert else "") for x in top.itertuples()),
                            consolidation=top["consolidation"].iloc[0] if len(top) and top["consolidation"].iloc[0] else "",
                            alerts="; ".join(sorted({a for a in ranked["alert"] if a})) if len(ranked) else "",
                            unknowns="; ".join(unknowns)))
    s = pd.DataFrame(summary, columns=SUMMARY_COLUMNS)
    if len(s):      # products with a commodity (so with sourcing options) first, then the soonest run-out
        s = s.assign(_m=(s["commodity"] == "").astype(int), _d=s["run_out_date"].replace("", "9999")).sort_values(
            ["_m", "_d", "product_id"]).drop(columns=["_m", "_d"]).reset_index(drop=True)
    L = pd.concat(lines, ignore_index=True) if lines else pd.DataFrame(columns=OPTION_COLUMNS)
    s.to_csv(out_dir / "sourcing_options.csv", index=False)
    L.to_csv(out_dir / "sourcing_option_lines.csv", index=False)
    alerts[alerts["active"]][ALERT_COLUMNS + ["label"]].to_csv(out_dir / "country_alerts_active.csv", index=False)
    leads[LEAD_COLUMNS].to_csv(out_dir / "supplier_leads_used.csv", index=False)       # after guards and owner answers
    return dict(summary=s, lines=L, note=note, leads=len(leads), alerts=int(alerts["active"].sum()))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Sourcing options for at-risk products (no web calls)")
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--today", default=None)
    a = ap.parse_args()
    from . import answers as A
    res = run(a.data, a.out, a.today or pd.Timestamp.today(), answers=A.load(a.data))
    print(f"{len(res['summary'])} at-risk products, {res['leads']} leads, {res['alerts']} active alerts -> {a.out}/sourcing_options.csv"
          + (f" ({res['note']})" if res["note"] else ""))
