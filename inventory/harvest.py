"""Harvest-season ordering for commodities bought from the new crop (dals, rice, nuts and dry fruits first).

Owner's rule: order about one month before the harvest season in the source country, so the order is
filled from the new crop; once the harvest window has passed buying gets hard, so each harvest order
covers demand until the NEXT harvest order's stock arrives.

Inputs:
  config/commodity_map.csv     product -> commodity: item-code prefix picks the GROUP (AD|BD dals, AR|IG rice,
                               AN|BN nuts), whole-word keywords in the name pick the COMMODITY; rows are tried
                               in file order and the first match wins. harvest = yes / no / mixed.
  config/harvest_calendar.csv  commodity, source_country, harvest months, crops_per_year, source_url, confidence,
                               confirmed_by_owner, order_months_before. One row per producing country, seeded
                               only from cited sources (months left blank when the source doesn't state them).
  owner answers                harvest:<commodity>_<country> confirms or rejects a calendar row (inventory/answers.py).

Rules (no new ordering math): each CONFIRMED calendar row becomes a rule row in the same shape as
config/harvest.csv (order month = harvest start - order_months_before; arrival = the month of order-by +
130 days; cover = 12 / crops_per_year months; members = the commodity's products) and goes through the
existing stock_plan.harvest_orders (quantity = forecast from arrival until the next arrival + the 3-month
cushion - projected stock at arrival) and build_with_stock (brand orders and containers).

Precedence and fallbacks:
  - config/harvest.csv rules come first (the owner's rice rule: order Jan, arrive Apr-May): a product they
    cover is never claimed by a calendar row.
  - an unconfirmed calendar row, a row without harvest months, a rejected row, an unmapped product or a
    commodity with harvest = no stays on the regular reorder rule; outputs/harvest_plan.csv says why.
  - if several countries are confirmed for one commodity, the first row in the calendar is used.

Outputs: outputs/harvest_plan.csv (one row per calendar row, plus mapped commodities without one),
outputs/commodity_map_used.csv, outputs/commodity_unmapped.csv.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import policy
from . import stock_plan as SP

ROOT = Path(__file__).resolve().parent.parent
MAP_CSV = ROOT / "config" / "commodity_map.csv"
CALENDAR_CSV = ROOT / "config" / "harvest_calendar.csv"
ORDER_MONTHS_BEFORE = 1          # owner: order about one month before the harvest starts
LEAD_EARLY, LEAD_LATE = 105, policy.LEAD_DAYS
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
CAL_COLUMNS = ["commodity", "source_country", "harvest_start_month", "harvest_end_month", "crops_per_year", "source_url",
               "confidence", "confirmed_by_owner", "order_months_before", "evidence_quote", "notes"]
PLAN_COLUMNS = ["commodity", "group", "source_country", "rule_id", "products", "product_ids", "harvest_window",
                "order_window_start", "order_by", "arrival_earliest", "arrival_estimate", "covers_until", "quantity_cases",
                "projected_stock_at_arrival", "runs_out_before_arrival", "yearly_cases", "status", "in_orders",
                "confirmed", "rule_source", "confidence", "source_url", "origin_hints", "reason"]
# Origin words in item names: a starting guess for the source country, never a confirmation.
ORIGIN_HINTS = [(r"\bindia gate\b|\bbombay\b|\bindia\b|\bindian\b", "India"), (r"\bpakistan\b", "Pakistan"),
                (r"\bcalifornia\b|\busa\b", "USA"), (r"\bcanad", "Canada"), (r"\bturk", "Turkey"), (r"\bafghan", "Afghanistan"),
                (r"\bvietnam", "Vietnam"), (r"\bsri lanka|\bceylon", "Sri Lanka"), (r"\biran", "Iran"), (r"\baustralia", "Australia")]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def month_num(m):
    m = str(m).strip()[:3].title()
    return MON.index(m) + 1 if m in MON else None


# ------------------------------------------------------------------ mapping
def load_map(path=MAP_CSV):
    m = pd.read_csv(path, dtype=str, keep_default_na=False)
    m["rx"] = [re.compile(rf"\b(?:{p})\b", re.IGNORECASE) for p in m["match"]]
    m["xrx"] = [re.compile(rf"\b(?:{p})\b", re.IGNORECASE) if p else None for p in m["exclude"]]
    return m


def _prefix(pid):
    m = re.match(r"[A-Za-z]+", str(pid))
    return m.group(0).upper() if m else ""


def origin_hint(name):
    return ", ".join(c for rx, c in ORIGIN_HINTS if re.search(rx, str(name), re.IGNORECASE))


def map_products(products, cmap, overrides=None):
    """Returns (mapped, unmapped). mapped: product_id, name, group, commodity, harvest, matched_by, also_matches,
    origin_hint. unmapped: product_id, name, group, reason, candidates (products in a mapped group with no single
    clear commodity: no keyword, several commodities, or a mixed pack). overrides: {product_id: commodity or
    None} from the owner's commodity answers (None = not a harvest crop)."""
    groups = {}
    for r in cmap.itertuples():
        for p in r.prefixes.split("|"):
            groups.setdefault(p.strip().upper(), r.group)
    rows, unmapped = [], []
    by_id = cmap.set_index("commodity")
    for pid, name in zip(products["product_id"], products["name"].fillna("").astype(str)):
        pre = _prefix(pid)
        override = (overrides or {}).get(pid, "missing")
        if pre not in groups and override == "missing":
            continue
        group = groups.get(pre, "")
        hint = origin_hint(name)
        if override != "missing":
            if override is None:
                rows.append(dict(product_id=pid, name=name, group=group, commodity="", harvest="no",
                                 matched_by="owner answer: not a harvest crop", also_matches="", origin_hint=hint))
            else:
                c = by_id.loc[override]
                rows.append(dict(product_id=pid, name=name, group=c["group"], commodity=override, harvest=c["harvest"],
                                 matched_by="owner answer", also_matches="", origin_hint=hint))
            continue
        cand = cmap[(cmap["group"] == group) & cmap["prefixes"].str.upper().str.split("|").map(lambda ps: pre in ps)]
        hits = [r for r in cand.itertuples() if r.rx.search(name) and not (r.xrx and r.xrx.search(name))]
        if not hits:
            unmapped.append(dict(product_id=pid, name=name, group=group, reason="no commodity keyword in the name", candidates=""))
            continue
        first, others = hits[0], [h.commodity for h in hits[1:]]
        if first.harvest == "mixed":
            unmapped.append(dict(product_id=pid, name=name, group=group, reason="mixed pack of several crops",
                                 candidates=first.commodity))
            continue
        if others and first.harvest == "yes" and any(by_id.loc[o, "harvest"] == "yes" for o in others):
            unmapped.append(dict(product_id=pid, name=name, group=group, reason="name matches several commodities",
                                 candidates="|".join([first.commodity] + others)))
            continue
        rows.append(dict(product_id=pid, name=name, group=group, commodity=first.commodity, harvest=first.harvest,
                         matched_by=f"keyword ({first.commodity})", also_matches="|".join(others), origin_hint=hint))
    mapped = pd.DataFrame(rows, columns=["product_id", "name", "group", "commodity", "harvest", "matched_by", "also_matches",
                                         "origin_hint"])
    un = pd.DataFrame(unmapped, columns=["product_id", "name", "group", "reason", "candidates"])
    return mapped, un


# ------------------------------------------------------------------ calendar
def load_calendar(path=CALENDAR_CSV):
    """The calendar with rule_id = commodity_country. A missing file is an empty calendar."""
    if not Path(path).exists():
        cal = pd.DataFrame(columns=CAL_COLUMNS)
    else:
        cal = pd.read_csv(path, dtype=str, keep_default_na=False).reindex(columns=CAL_COLUMNS).fillna("")
    for c in ("harvest_start_month", "harvest_end_month"):
        cal[c] = [MON[month_num(v) - 1] if month_num(v) else "" for v in cal[c]]
    cal["rule_id"] = [f"{slug(c)}_{slug(k)}" for c, k in zip(cal["commodity"], cal["source_country"])]
    return cal.reset_index(drop=True)


def _months_before(row):
    v = pd.to_numeric(row.get("order_months_before"), errors="coerce")
    return int(v) if pd.notna(v) and v >= 0 else ORDER_MONTHS_BEFORE


def _crops(row):
    v = pd.to_numeric(row.get("crops_per_year"), errors="coerce")
    return int(v) if pd.notna(v) and v >= 1 else None


def windows(row, today):
    """Order windows and harvest seasons around `today` for one calendar row with a harvest start month.
    Returns dict(status, window_start, order_by, harvest_start, harvest_end, next_window_start, next_order_by)."""
    today = pd.Timestamp(today).normalize()
    hs, he = month_num(row["harvest_start_month"]), month_num(row["harvest_end_month"]) or month_num(row["harvest_start_month"])
    before = _months_before(row)
    seasons = []
    for y in (today.year - 1, today.year, today.year + 1, today.year + 2):
        h_start = pd.Timestamp(y, hs, 1)
        h_end = pd.Timestamp(y if he >= hs else y + 1, he, 1) + pd.offsets.MonthEnd(0)
        ws = h_start - pd.DateOffset(months=before)
        we = (ws + pd.offsets.MonthEnd(0)) if before > 0 else ws
        seasons.append((ws, we, h_start, h_end))
    nxt = next(s for s in seasons if s[0] > today)
    for ws, we, h_start, h_end in seasons:
        if ws <= today <= we:
            return dict(status="ORDER NOW", window_start=ws, order_by=we, harvest_start=h_start, harvest_end=h_end,
                        next_window_start=ws, next_order_by=we)
        if we < today <= h_end:
            return dict(status="window closed", window_start=ws, order_by=we, harvest_start=h_start, harvest_end=h_end,
                        next_window_start=nxt[0], next_order_by=nxt[1])
    return dict(status="upcoming", window_start=nxt[0], order_by=nxt[1], harvest_start=nxt[2], harvest_end=nxt[3],
                next_window_start=nxt[0], next_order_by=nxt[1])


def window_label(start, end):
    """'Dec', 'Apr' -> 'Dec-Apr'; 'Sep', 'Sep' -> 'Sep'; 'Aug', '' -> 'Aug (end month not stated)'."""
    if not start:
        return ""
    if not end:
        return f"{start} (end month not stated)"
    return start if start == end else f"{start}-{end}"


def rule_row(row, product_ids, today):
    """A config/harvest.csv-shaped rule for one calendar row (for stock_plan.harvest_orders)."""
    w = windows(row, today)
    order_month = w["next_window_start"].month
    arrival = w["next_order_by"] + pd.Timedelta(days=LEAD_LATE)
    crops = _crops(row) or 1
    return dict(rule_id=row["rule_id"], category="", match="", exclude="", order_months=MON[order_month - 1],
                arrival_months=MON[arrival.month - 1], cover_months=str(max(1, round(12 / crops))), confirmed="yes",
                note=f"harvest {row['harvest_start_month']}-{row['harvest_end_month'] or row['harvest_start_month']} "
                     f"in {row['source_country']}", product_ids="|".join(product_ids))


@dataclass
class Prepared:
    mapped: pd.DataFrame
    unmapped: pd.DataFrame
    calendar: pd.DataFrame          # with confirmed (yes / no / rejected), month_source, usable, chosen
    config_rules: pd.DataFrame      # config/harvest.csv after owner answers
    calendar_rules: pd.DataFrame    # rule rows for confirmed, chosen calendar rows (they place orders)
    config_members: dict            # {product_id: config rule row} claimed by config/harvest.csv rules

    @property
    def rules(self):
        """All harvest rules for build_with_stock: config rules first, so they win."""
        return pd.concat([self.config_rules, self.calendar_rules], ignore_index=True)


def prepare(products, answers, today, config_rules=None, cmap=None, calendar=None, overrides=None):
    """Map products, apply the owner's answers to the calendar and to config/harvest.csv, and build the rule
    rows that place harvest orders."""
    from . import answers as A
    cmap = load_map() if cmap is None else cmap
    cal = load_calendar() if calendar is None else calendar
    cal = A.harvest_calendar(cal, answers)
    cfg = A.harvest_rules(SP.harvest_rules() if config_rules is None else config_rules, answers, skip=set(cal["rule_id"]))
    if overrides is None:
        overrides = A.commodity_overrides(answers, cmap)
    mapped, unmapped = map_products(products, cmap, overrides)
    claimed, _ = SP.harvest_members(products, cfg)
    cal["usable"] = (cal["confirmed"] == "yes") & (cal["harvest_start_month"] != "")
    cal["chosen"] = False
    rules = []
    for commodity, g in cal[cal["usable"]].groupby("commodity", sort=False):
        ids = [p for p in mapped.loc[(mapped["commodity"] == commodity) & (mapped["harvest"] == "yes"), "product_id"]
               if p not in claimed]
        first = g.index[0]
        cal.loc[first, "chosen"] = True
        if ids:
            rules.append(rule_row(cal.loc[first], ids, today))
    rule_cols = list(cfg.columns) + ["product_ids"]
    return Prepared(mapped, unmapped, cal, cfg, pd.DataFrame(rules).reindex(columns=rule_cols).fillna(""), claimed)


# ------------------------------------------------------------------ plan
def plan(prep, fc, products, stock, today, holds=(), panel=None):
    """outputs/harvest_plan.csv: one row per calendar row (and per harvest commodity with no calendar row), with
    the order window, arrival, quantity until the next harvest and why. Quantities come from
    stock_plan.harvest_orders (the same calculation the orders use); unconfirmed rows show what WOULD be
    ordered. Without stock data, quantities are gross need (no stock subtracted)."""
    today = pd.Timestamp(today).normalize()
    fc = fc[fc["product_id"].isin(set(prep.mapped["product_id"]))]
    stock_known = stock is not None
    if len(fc):
        months_needed = len(pd.date_range(pd.to_datetime(fc["month"]).min(), today + pd.DateOffset(months=30), freq="MS"))
        F, starts, pids, extended = SP.forecast_matrix(fc, months_needed)
    else:
        F, starts, pids, extended = np.zeros((0, 1)), pd.DatetimeIndex([today]), [], set()
    if stock_known:
        s, _ = SP.stock_frame(stock)
        s = s.set_index("product_id")
        pos = (s["on_hand_cases"].fillna(0) + s["on_order_cases"].fillna(0) - s["held_cases"].fillna(0)).to_dict()
    else:
        pos = {}
    positions = {p: float(pos.get(p, 0.0)) for p in pids}
    yearly = {}
    if panel is not None and len(panel):
        last = panel["month"].max()
        yearly = panel[panel["month"] > last - pd.DateOffset(months=12)].groupby("product_id")["units_sold"].sum().to_dict()
    hm = prep.mapped[prep.mapped["harvest"] == "yes"]
    rows = []
    cal = prep.calendar
    for commodity in list(dict.fromkeys(list(cal["commodity"]) + list(hm["commodity"]))):
        prods = hm[hm["commodity"] == commodity]
        own = [p for p in prods["product_id"] if p not in prep.config_members]
        by_cfg = [p for p in prods["product_id"] if p in prep.config_members]
        hints = sorted({h for h in prods["origin_hint"] if h})
        crows = cal[cal["commodity"] == commodity]
        if crows.empty:
            crows = pd.DataFrame([dict(commodity=commodity, source_country="", harvest_start_month="", harvest_end_month="",
                                       crops_per_year="", source_url="", confidence="", confirmed="no", usable=False,
                                       chosen=False, rule_id="", month_source="", order_months_before="")])
        for _, c in crows.iterrows():
            base = dict(commodity=commodity, group=prods["group"].iloc[0] if len(prods) else "", source_country=c["source_country"],
                        rule_id=c["rule_id"], products=len(own), product_ids="|".join(own), confidence=c.get("confidence", ""),
                        confirmed=c["confirmed"] if c["rule_id"] else "",
                        source_url=c.get("source_url", ""), yearly_cases=round(sum(yearly.get(p, 0.0) for p in own), 0),
                        origin_hints=(f"{', '.join(hints)} (from item names: a starting guess, unconfirmed)" if hints else ""),
                        harvest_window=window_label(c["harvest_start_month"], c["harvest_end_month"]))
            why = []
            if by_cfg:
                why.append(f"{len(by_cfg)} product(s) follow the owner's {prep.config_members[by_cfg[0]]['rule_id']} rule in "
                           f"config/harvest.csv instead")
            if not own:
                rows.append(dict(base, status="no products", in_orders="no", rule_source="none",
                                 reason="; ".join(why) or "no products of this commodity are sold"))
                continue
            if not c["harvest_start_month"]:
                src = "no harvest months in the calendar" if c["rule_id"] else "no harvest calendar row"
                if str(c.get("crops_per_year", "")).strip().lower() == "continuous":
                    src = "harvested year-round (no harvest season)"
                rows.append(dict(base, status="no harvest data", in_orders="no", rule_source=src,
                                 reason="; ".join(why + [f"{src}: ordered with the regular reorder rule"])))
                continue
            w = windows(c, today)
            rule = rule_row(c, own, today)
            ho = SP.harvest_orders(F, starts, pids, extended, positions,
                                   {p: rule for p in own if p in set(pids)}, today) if pids else pd.DataFrame()
            qty = int(ho["order_cases"].sum()) if len(ho) else 0
            proj = float(ho["projected_stock_at_arrival"].sum()) if len(ho) else 0.0
            short = ho[ho["projected_stock_at_arrival"] < 0]["product_id"].tolist() if len(ho) else []
            arrival = pd.Timestamp(ho["expected_arrival"].iloc[0]) if len(ho) else w["next_order_by"] + pd.Timedelta(days=LEAD_LATE)
            covers = ho["covers_until"].iloc[0] if len(ho) else ""
            active = bool(c["confirmed"] == "yes" and c["chosen"] and stock_known)
            if c["confirmed"] == "rejected":
                src, tail = "calendar (rejected by owner)", "the owner said this crop isn't bought by harvest from here"
            elif c["confirmed"] != "yes":
                src, tail = "calendar (not confirmed)", "not confirmed by the owner yet: ordered with the regular rule until confirmed"
            elif not c["chosen"]:
                src, tail = "calendar (confirmed, another country used)", "another confirmed country for this commodity comes first"
            elif not stock_known:
                src, tail = "calendar (confirmed)", "no stock data: harvest orders are placed only when ordering against stock"
            else:
                src, tail = "calendar (confirmed)", "in the brand orders as a harvest buy"
            order_by = w["next_order_by"] if w["status"] != "ORDER NOW" else w["order_by"]
            window_start = w["next_window_start"] if w["status"] != "ORDER NOW" else w["window_start"]
            text = f"{commodity.replace('_', ' ')} harvest in {c['source_country']} is {base['harvest_window']}"
            if c.get("month_source") == "owner answer":
                text += " (months from the owner's answer)"
            text += (f"; order by {order_by:%b %d, %Y} ({_months_before(c)} month(s) before the harvest) so it comes from "
                     f"the new crop; arrives about {arrival:%b %Y} (lead time {LEAD_EARLY}-{LEAD_LATE} days)")
            if covers:
                text += f"; covers demand until {pd.Timestamp(covers):%b %Y} plus the 3-month cushion"
            if w["status"] == "window closed":
                text += f"; this season's window closed {w['order_by']:%b %d, %Y}: buying is harder until the next window"
            if not _crops(c):
                text += "; crops per year not stated: assumed one harvest a year"
            if short:
                text += (f"; {len(short)} product(s) run out before this order lands: a bridge order is needed "
                         f"(not placed automatically)")
            if not stock_known:
                text += "; quantity is gross need (no stock data)"
            rows.append(dict(base, order_window_start=f"{window_start:%Y-%m-%d}", order_by=f"{order_by:%Y-%m-%d}",
                             arrival_earliest=f"{window_start + pd.Timedelta(days=LEAD_EARLY):%Y-%m-%d}",
                             arrival_estimate=f"{arrival:%Y-%m-%d}", covers_until=covers, quantity_cases=qty,
                             projected_stock_at_arrival=round(proj, 1) if stock_known else np.nan,
                             runs_out_before_arrival=len(short), status=w["status"], in_orders="yes" if active else "no",
                             rule_source=src, reason="; ".join(why + [text, tail])))
    return pd.DataFrame(rows).reindex(columns=PLAN_COLUMNS)


def write(prep, harvest_plan, out):
    out = Path(out)
    harvest_plan.to_csv(out / "harvest_plan.csv", index=False)
    prep.mapped.to_csv(out / "commodity_map_used.csv", index=False)
    prep.unmapped.to_csv(out / "commodity_unmapped.csv", index=False)
