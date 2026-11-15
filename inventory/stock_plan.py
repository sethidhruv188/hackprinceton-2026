"""Ordering with stock: per-product status, annual harvest buys, brand orders and containers.

Per product (non-harvest), with the model's likely monthly forecast (outputs/forecast.csv):
  position      on_hand + on_order - held   (missing columns count as 0 and are reported)
  reorder point forecast over the 130-day lead time + cushion (policy.levels)
  floor         at any date, the forecast of the next FLOOR_MONTHS (3) months from that date. Stock is
                projected week by week (on hand - held + on-order arrivals, on their date or after the
                130-day lead time when no date is given, - forecast sales); a product that dips below
                the floor before a new order could arrive gets FLOOR_STATUS and the dip date
  status        "on hold: confirm release" | FLOOR_STATUS | "order now: N cases" (position < reorder point,
                N = order-up-to - position, whole cases) | "OK until <date>" (the first week the
                projected position, after forecast sales, falls below that day's reorder point)

Brand orders: when any product of a brand is at "order now", the brand order includes every product
of that brand projected to reach its reorder point within the next 2 months, each topped up to its
order-up-to level. Brands with nothing due get their next order on the earliest "OK until" date.

Containers (mixed products): CBM per case from data/raw/case_cbm.csv, else the inventory file,
else ESTIMATED from case weight and a per-category density (config/densities.csv). Containers =
max(CBM / usable CBM, kg / max_kg if set). If the last container is under 70% full, the brand's next
needs (earliest first) are pulled forward to fill it, up to 100%. Needed quantities are never cut.

Harvest products (config/harvest.csv, confirmed rows only): one order a year in the order window.
Quantity = forecast from arrival until the next harvest arrival + cushion - projected stock at
arrival (on hand + on order - forecast until arrival), floored at 0. Months past the forecast reuse
the same calendar month a year earlier ("extended"); the model is never called past the calendar.
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import policy

ROOT = Path(__file__).resolve().parent.parent
PULL_IF_UNDER = 0.70
FLOOR_MONTHS = policy.CUSHION_MONTHS     # owner: at least 3 months of forward forecast sales on hand
FLOOR_STATUS = "below 3-month floor before restock: expedite or air-ship"
INCLUDE_WITHIN_DAYS = 61          # 2 months
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ------------------------------------------------------------------ inputs
def forecast_matrix(fc, months_needed):
    """fc: product_id, month (YYYY-MM), likely. Returns (F, starts, product index, extended months)."""
    f = fc.rename(columns={"likely": "forecast"})[["product_id", "month", "forecast"]].copy()
    f["month"] = pd.to_datetime(f["month"])
    wide, extended = policy.extend_seasonally(f, months_needed)
    return wide.to_numpy(float), pd.DatetimeIndex(wide.columns), list(wide.index), extended


def stock_frame(stock):
    """Accept *_cases (stock.csv) or *_units column names; report which are present."""
    s = stock.copy()
    for base in ("on_hand", "on_order", "held"):
        if f"{base}_cases" not in s and f"{base}_units" in s:
            s[f"{base}_cases"] = s[f"{base}_units"]
    present = {b: (f"{b}_cases" in s and s[f"{b}_cases"].notna().any()) for b in ("on_hand", "on_order", "held")}
    for b in ("on_hand", "on_order", "held"):
        s[f"{b}_cases"] = pd.to_numeric(s.get(f"{b}_cases"), errors="coerce") if f"{b}_cases" in s else np.nan
    return s, present


def containers_config(path=ROOT / "config" / "containers.csv"):
    c = pd.read_csv(path).iloc[0]
    max_kg = pd.to_numeric(c.get("max_kg"), errors="coerce")
    return dict(cbm=float(c["container_cbm"]), usable=float(c.get("usable_fraction", 1.0) or 1.0),
                max_kg=None if pd.isna(max_kg) else float(max_kg))


def cbm_table(products, stock=None, densities_path=ROOT / "config" / "densities.csv", overrides_path=None, answers=None):
    """product_id, cbm_per_case, cbm_source. Precedence: case_cbm.csv (overrides_path) > owner answers
    ({product_id: cbm}) > inventory file > estimate from weight."""
    p = products[["product_id", "category", "case_weight_kg"]].copy()
    dens = pd.read_csv(densities_path).set_index("category")["kg_per_cbm"]
    est = p["case_weight_kg"] / p["category"].map(dens).fillna(dens.get("other", np.nan))
    p["cbm_per_case"], p["cbm_source"] = est, np.where(est.notna(), "estimated from weight", "missing")
    if stock is not None and "cbm_per_case" in stock and stock["cbm_per_case"].notna().any():
        inv = stock.set_index("product_id")["cbm_per_case"].dropna()
        hit = p["product_id"].isin(inv.index)
        p.loc[hit, "cbm_per_case"] = p.loc[hit, "product_id"].map(inv)
        p.loc[hit, "cbm_source"] = "inventory file"
    if answers:
        hit = p["product_id"].isin(set(answers))
        p.loc[hit, "cbm_per_case"] = p.loc[hit, "product_id"].map(answers)
        p.loc[hit, "cbm_source"] = "owner answer"
    if overrides_path and Path(overrides_path).exists():
        o = pd.read_csv(overrides_path, dtype={"product_id": str}).dropna(subset=["cbm_per_case"]).set_index("product_id")["cbm_per_case"]
        hit = p["product_id"].isin(o.index)
        p.loc[hit, "cbm_per_case"] = p.loc[hit, "product_id"].map(o)
        p.loc[hit, "cbm_source"] = "case_cbm.csv"
    return p[["product_id", "cbm_per_case", "cbm_source"]]


# ------------------------------------------------------------------ harvest
def harvest_rules(path=ROOT / "config" / "harvest.csv"):
    return pd.read_csv(path, keep_default_na=False, dtype=str)


def harvest_members(products, rules):
    """{product_id: rule row} for confirmed rules; unconfirmed rule matches returned separately.
    A rule matches by name regex (`match`, `category`, `exclude`), or lists its products explicitly in
    `product_ids` ("A|B|C", used by inventory/harvest.py). The first rule that claims a product wins."""
    confirmed, candidates = {}, {}
    for r in rules.to_dict("records"):
        ids = r.get("product_ids")
        if isinstance(ids, str) and ids.strip():
            m = products[products["product_id"].isin(set(ids.split("|")))]
        else:
            m = products[products["name"].astype(str).str.contains(r["match"], case=False, regex=True, na=False)]
            if r.get("category"):
                m = m[m["category"] == r["category"]]
            if r.get("exclude"):
                m = m[~m["name"].astype(str).str.contains(r["exclude"], case=False, regex=True, na=False)]
        target = confirmed if str(r["confirmed"]).strip().lower() == "yes" else candidates
        for pid in m["product_id"]:
            target.setdefault(pid, r)
    return confirmed, {k: v for k, v in candidates.items() if k not in confirmed}


def _month_range(text):
    """'Apr-May' -> [4, 5]; 'Jan' -> [1]."""
    parts = [MONTHS[t.strip().lower()[:3]] for t in str(text).split("-") if t.strip()]
    if len(parts) == 1:
        return parts
    a, b = parts
    return list(range(a, b + 1)) if a <= b else list(range(a, 13)) + list(range(1, b + 1))


def harvest_window(rule, today):
    """Next order window (start, end) on or after today, and the expected arrival date (1st of the
    first arrival month after the order)."""
    om = _month_range(rule["order_months"])
    today = pd.Timestamp(today).normalize()
    year = today.year if pd.Timestamp(today.year, om[-1], 1) + pd.offsets.MonthEnd(0) >= today else today.year + 1
    start = pd.Timestamp(year, om[0], 1)
    end = pd.Timestamp(year, om[-1], 1) + pd.offsets.MonthEnd(0)
    am = _month_range(rule["arrival_months"])[0]
    arrival = pd.Timestamp(year if am >= om[0] else year + 1, am, 1)
    return start, end, arrival


def harvest_orders(F, starts, pids, extended, positions, members, today, cushion_months=policy.CUSHION_MONTHS):
    """One row per confirmed harvest product."""
    idx = {p: i for i, p in enumerate(pids)}
    rows = []
    for pid, rule in members.items():
        if pid not in idx:
            continue
        i = idx[pid]
        start, end, arrival = harvest_window(rule, today)
        cover = float(rule.get("cover_months") or 12)
        until = arrival + pd.DateOffset(months=int(cover))
        need_days = (until - arrival).days + cushion_months * policy.DAYS_PER_MONTH
        before = (arrival - pd.Timestamp(today)).days
        Fi = F[i:i + 1]
        demand_until_arrival = float(policy.demand_over(Fi, starts, today, before)[0])
        need = float(policy.demand_over(Fi, starts, arrival, need_days)[0])
        projected = positions.get(pid, 0.0) - demand_until_arrival
        qty = max(0.0, math.ceil(round(need - projected, 6)))
        ext = sorted(m for m in extended if arrival <= m < arrival + pd.Timedelta(days=need_days))
        rows.append(dict(product_id=pid, rule=rule["rule_id"], order_from=f"{start:%Y-%m-%d}", order_by=f"{end:%Y-%m-%d}",
                         expected_arrival=f"{arrival:%Y-%m-%d}", covers_until=f"{until:%Y-%m-%d}",
                         position_now=round(positions.get(pid, 0.0), 1), forecast_until_arrival=round(demand_until_arrival, 1),
                         projected_stock_at_arrival=round(projected, 1), need_after_arrival=round(need, 1), order_cases=int(qty),
                         extended_months=", ".join(f"{m:%b %Y}" for m in ext),
                         label=f"Annual harvest buy: order in {rule['order_months']}, arrives {rule['arrival_months']}"))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ product status
def stock_on_shelf(stock, pids, today, lead_days=policy.LEAD_DAYS):
    """(on hand - held, on-order quantity, on-order arrival date) arrays. Without arrival dates,
    stock on order is assumed to land after the full lead time."""
    s, _ = stock_frame(stock)
    s = s.set_index("product_id").reindex(pids)
    shelf = (s["on_hand_cases"].fillna(0) - s["held_cases"].fillna(0)).to_numpy(float)
    on_order = s["on_order_cases"].fillna(0).to_numpy(float)
    arr = pd.to_datetime(s["on_order_arrival"], errors="coerce") if "on_order_arrival" in s else pd.Series(pd.NaT, index=s.index)
    arrival = arr.fillna(pd.Timestamp(today).normalize() + pd.Timedelta(days=lead_days)).to_numpy()
    return shelf, on_order, arrival


LASTS_MONTHS = 3          # owner: an order should last about 3 months after it arrives
PULL_MAX_MONTHS = 4       # container pull-forward may not push a product above 4 months after arrival


def owner_quantity(F, starts, positions, when, lead_days=policy.LEAD_DAYS, lasts_months=LASTS_MONTHS):
    """Owner's rule, per row of F: quantity = forecast for `lasts_months` after arrival minus the stock
    expected at arrival (position - forecast until arrival, floored at 0); whole cases, floored at 0.
    Sales during shipping are NOT added (the lead time only sets the dates). Returns dict with qty,
    stock_at_arrival, arrival, next_order_by (arrival + lasts_months - lead time) and runs_out (the
    first week stock reaches 0 before arrival, else None)."""
    when = pd.Timestamp(when).normalize()
    pos = np.asarray(positions, float)
    arrival = when + pd.Timedelta(days=lead_days)
    left = np.maximum(0.0, pos - policy.demand_over(F, starts, when, lead_days))
    need = policy.demand_over(F, starts, arrival, (arrival + pd.DateOffset(months=lasts_months) - arrival).days)  # calendar months
    qty = np.maximum(0.0, np.ceil(np.round(need - left, 6)))
    runs = np.full(len(pos), None, dtype=object)
    for d in range(0, lead_days + 1, 7):
        hit = (pos - policy.demand_over(F, starts, when, d) <= 0) & (runs == None)  # noqa: E711
        runs[hit] = when + pd.Timedelta(days=d)
    nxt = arrival + pd.DateOffset(months=lasts_months) - pd.Timedelta(days=lead_days)
    return dict(qty=qty, stock_at_arrival=left, arrival=arrival, next_order_by=nxt, runs_out=runs, need=need)


def floor_check(F, starts, pids, stock, today, floor_months=FLOOR_MONTHS, lead_days=policy.LEAD_DAYS, step_days=7):
    """Weekly from today until a new order could arrive: projected stock vs. the forecast of the next
    floor_months. Returns dict(floor_now, below_now, dip_date per product)."""
    today = pd.Timestamp(today).normalize()
    shelf, on_order, arrival = stock_on_shelf(stock, pids, today, lead_days)
    fd = floor_months * policy.DAYS_PER_MONTH
    dip = np.full(len(pids), None, dtype=object)
    floor_now = policy.demand_over(F, starts, today, fd)
    for d in range(0, lead_days + 1, step_days):
        day = today + pd.Timedelta(days=d)
        stock_d = shelf + np.where(arrival <= np.datetime64(day), on_order, 0.0) - policy.demand_over(F, starts, today, d)
        floor_d = floor_now if d == 0 else policy.demand_over(F, starts, day, fd)
        hit = (stock_d < floor_d) & (dip == None)  # noqa: E711
        dip[hit] = day
    return dict(floor_now=floor_now, below_now=shelf < floor_now, dip=dip)


def product_status(F, starts, pids, stock, holds, harvest_ids, today, cushion_months=policy.CUSHION_MONTHS,
                   cover_months=policy.COVER_MONTHS, weeks=52, floor_months=FLOOR_MONTHS):
    """Vectorized over products. Returns one row per product."""
    today = pd.Timestamp(today).normalize()
    s, _ = stock_frame(stock)
    s = s.set_index("product_id").reindex(pids)
    on_hand = s["on_hand_cases"].fillna(0).to_numpy(float)
    on_order = s["on_order_cases"].fillna(0).to_numpy(float)
    held = s["held_cases"].fillna(0).to_numpy(float)
    pos = on_hand + on_order - held
    lv = policy.levels(F, starts, today, cushion_months, cover_months)
    oq = owner_quantity(F, starts, pos, today)
    qty = np.where(pos < lv["reorder_point"], oq["qty"], 0.0)          # trigger: reorder point; size: owner's rule
    until = np.full(len(pids), None, dtype=object)
    for w in range(1, weeks + 1):
        day = today + pd.Timedelta(days=7 * w)
        left = pos - policy.demand_over(F, starts, today, 7 * w)
        rop = policy.levels(F, starts, day, cushion_months, cover_months)["reorder_point"]
        hit = (left < rop) & (until == None)  # noqa: E711
        until[hit] = day
    fl = floor_check(F, starts, pids, stock, today, floor_months)
    out = []
    for i, pid in enumerate(pids):
        dip = fl["dip"][i]
        if pid in holds:
            st = "on hold: confirm release"
        elif dip is not None and F[i].sum() > 0:
            st = (f"{FLOOR_STATUS} (below from {dip:%Y-%m-%d})"
                  + (f"; order now: {int(qty[i])} cases" if qty[i] > 0 and pid not in harvest_ids else "")
                  + ("; annual harvest buy" if pid in harvest_ids else ""))
        elif pid in harvest_ids:
            st = "annual harvest buy"
        elif qty[i] > 0:
            st = f"order now: {int(qty[i])} cases"
        elif until[i] is not None:
            st = f"OK until {until[i]:%Y-%m-%d}"
        else:
            st = f"OK beyond {weeks * 7} days"
        out.append(dict(product_id=pid, on_hand_cases=round(on_hand[i], 1), on_order_cases=round(on_order[i], 1),
                        held_cases=round(held[i], 1), position=round(pos[i], 1),
                        reorder_point=round(lv["reorder_point"][i], 1), order_up_to=round(lv["order_up_to"][i], 1),
                        order_now_cases=int(qty[i]) if pid not in holds and pid not in harvest_ids else 0,
                        ok_until=f"{until[i]:%Y-%m-%d}" if until[i] is not None else None,
                        floor_cases=round(float(fl["floor_now"][i]), 1),
                        below_floor_now=bool(fl["below_now"][i] and F[i].sum() > 0 and pid not in holds),
                        floor_dip_date=f"{dip:%Y-%m-%d}" if dip is not None and F[i].sum() > 0 and pid not in holds else None,
                        status=st))
    return pd.DataFrame(out)


def projection(F, starts, pids, status, today, months=12, cushion_months=policy.CUSHION_MONTHS,
               cover_months=policy.COVER_MONTHS, lead_days=policy.LEAD_DAYS, stock=None, floor_months=FLOOR_MONTHS):
    """Projected stock on the 1st of each coming month with no new orders, and with the order
    suggested today arriving after the full lead time; plus the reorder point then."""
    today = pd.Timestamp(today).normalize()
    st = status.set_index("product_id").reindex(pids)
    order = st["order_now_cases"].fillna(0).to_numpy(float)
    shelf, on_order, arrival = stock_on_shelf(stock, pids, today, lead_days) if stock is not None else (
        st["position"].fillna(0).to_numpy(float), np.zeros(len(pids)), np.full(len(pids), np.datetime64(today)))
    arrive = today + pd.Timedelta(days=lead_days)
    rows = []
    for k in range(1, months + 1):
        m = (today + pd.offsets.MonthBegin(k)).normalize()
        used = policy.demand_over(F, starts, today, (m - today).days)
        rop = policy.levels(F, starts, m, cushion_months, cover_months)["reorder_point"]
        floor = policy.demand_over(F, starts, m, floor_months * policy.DAYS_PER_MONTH)
        base = shelf + np.where(arrival <= np.datetime64(m), on_order, 0.0) - used
        withorder = base + np.where(m >= arrive, order, 0.0)
        rows.append(pd.DataFrame({"product_id": pids, "month": f"{m:%Y-%m}", "projected_stock": base.round(1),
                                  "projected_with_suggested_order": withorder.round(1), "reorder_point": rop.round(1),
                                  "floor_3_months": floor.round(1)}))
    return pd.concat(rows, ignore_index=True)


# ------------------------------------------------------------------ containers
def pack(lines, candidates, cfg):
    """lines: current order lines (cases, kg, cbm). candidates: next needs in priority order with
    product_id, cases, kg_per_case, cbm_per_case, needed_by. Returns (summary dict, pulled lines)."""
    usable = cfg["cbm"] * cfg["usable"]
    cbm, kg = float(lines["cbm"].sum(min_count=1) or 0.0), float(lines["kg"].sum(min_count=1) or 0.0)

    def count(c, k):
        n = c / usable if usable else 0.0
        if cfg.get("max_kg"):
            n = max(n, k / cfg["max_kg"])
        return n

    n = count(cbm, kg)
    frac = n - math.floor(n) if n > 0 else 0.0
    pulled = []
    if 0 < frac < PULL_IF_UNDER:
        room = math.ceil(n) - n                                    # fraction of a container still free
        for c in candidates.itertuples():
            if room <= 1e-6:
                break
            per_case = max((c.cbm_per_case or 0) / usable, (c.kg_per_case or 0) / cfg["max_kg"] if cfg.get("max_kg") else 0)
            if not per_case or per_case <= 0 or not np.isfinite(per_case):
                continue
            take = min(int(c.cases), int(math.floor(room / per_case)))
            if take <= 0:
                continue
            pulled.append(dict(product_id=c.product_id, cases=take, kg=round(take * (c.kg_per_case or 0), 1),
                               cbm=round(take * (c.cbm_per_case or 0), 3), needed_by=c.needed_by))
            room -= take * per_case
            cbm += take * (c.cbm_per_case or 0)
            kg += take * (c.kg_per_case or 0)
        n = count(cbm, kg)
        frac = n - math.floor(n) if n > 0 else 0.0
    last_fill = 100.0 if n > 0 and frac < 1e-6 else 100 * frac
    return dict(cbm=round(cbm, 2), kg=round(kg, 1), containers=round(n, 2), containers_needed=math.ceil(n - 1e-9) if n > 0 else 0,
                last_container_fill_pct=round(last_fill, 0) if n > 0 else None,
                bound="weight" if cfg.get("max_kg") and kg / cfg["max_kg"] > cbm / usable else "volume"), pd.DataFrame(pulled)
