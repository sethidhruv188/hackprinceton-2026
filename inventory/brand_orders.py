"""Group the per-product plan into one order per brand.

A brand order covers `cover_months` (default 2) consecutive arrival months of outputs/order_plan.csv,
starting with the first plan month. It includes every product of the brand needed in those months
(cases rounded up to whole cases). Its order-by date is the earliest order_by_safe of its products
(and the earliest order_by_latest). Products currently on hold are listed in the order with
"confirm release before ordering" and are NOT added to its totals.

Status (today = the plan date):
  "Too late for full lead time: expedite"  the latest order-by date has passed
  "Order now"           the safe order-by date is within 14 days, or passed while the latest has not
  "Order this month"    the safe order-by date is within 31 days
  "Coming up"           later

Containers: when data/raw/brands.csv gives a container_kg for the brand, the order shows
total kg / container_kg (one decimal) and, if the last container would be under 60% full, suggests
covering one more month.

Gross need only. When stock data exists (--use-stock), `build(..., stock=...)` subtracts on hand +
on order (held units never count) from the earliest orders first.

  python -m inventory.brand_orders --today 2026-11-14         # writes outputs/brands.csv, brand_orders.csv, brand_order_lines.csv
"""
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import brands as B
from . import policy
from .schema import load

INCLUDE_WITHIN_DAYS = 61

ORDER_COLUMNS = ["order_id", "brand", "covers", "first_month", "last_month", "order_by_safe", "order_by_latest",
                 "days_to_order_by", "status", "products", "cases", "kg", "containers", "container_note",
                 "products_on_hold", "stock_netted", "reason"]
LINE_COLUMNS = ["order_id", "brand", "product_id", "name", "cases", "kg", "on_hold", "note", "reason"]
STATUSES = ["Order now", "Order this month", "Coming up", "Too late for full lead time: expedite"]
NOW_DAYS, MONTH_DAYS, FILL_SUGGEST = 14, 31, 0.6


def status(today, safe, latest):
    today, safe, latest = pd.Timestamp(today), pd.Timestamp(safe), pd.Timestamp(latest)
    if latest < today:
        return "Too late for full lead time: expedite"
    if safe <= today + pd.Timedelta(days=NOW_DAYS):
        return "Order now"
    if safe <= today + pd.Timedelta(days=MONTH_DAYS):
        return "Order this month"
    return "Coming up"


def covers_label(months):
    a, b = pd.Timestamp(months[0]), pd.Timestamp(months[-1])
    if a == b:
        return f"{a:%b %Y}"
    return f"{a:%b}-{b:%b %Y}" if a.year == b.year else f"{a:%b %Y}-{b:%b %Y}"


def _net_stock(lines, stock):
    """Subtract available stock (on hand + on order, never held) from each product's needs, earliest
    order first. lines: product_id, first_month, need."""
    s = stock.set_index("product_id")
    avail = (s.get("on_hand_units", 0).fillna(0) + s.get("on_order_units", 0).fillna(0)).to_dict()
    out = []
    for r in lines.sort_values(["product_id", "first_month"]).itertuples():
        left = avail.get(r.product_id, 0.0)
        use = min(left, r.need)
        avail[r.product_id] = left - use
        out.append(r.need - use)
    return pd.Series(out, index=lines.sort_values(["product_id", "first_month"]).index).reindex(lines.index)


def build(plan, brand_map, today, cover_months=2, holds=(), info=None, stock=None):
    """plan: order_plan rows (product_id, name, month, cases_needed, kg_needed, order_by_safe,
    order_by_latest, reason). Returns (orders, lines) DataFrames with ORDER_COLUMNS / LINE_COLUMNS."""
    today = pd.Timestamp(today).normalize()
    p = plan.merge(brand_map[["product_id", "brand"]], on="product_id", how="left")
    p["brand"] = p["brand"].fillna("Unbranded")
    p["m"] = pd.to_datetime(p["month"])
    months = sorted(p["m"].unique())
    window = {m: months[(i // cover_months) * cover_months] for i, m in enumerate(months)}
    p["first_month"] = p["m"].map(window)
    holds = set(holds)
    # one line per brand x window x product
    g = p.groupby(["brand", "first_month", "product_id"])
    lines = g.agg(name=("name", "first"), need=("cases_needed", "sum"), kg_need=("kg_needed", lambda s: s.sum(min_count=1)),
                  order_by_safe=("order_by_safe", "min"), order_by_latest=("order_by_latest", "min")).reset_index()
    top = p.sort_values("cases_needed", ascending=False).groupby(["brand", "first_month", "product_id"])["reason"].first()
    lines["reason"] = [top.get((b, f, pid), "") for b, f, pid in zip(lines["brand"], lines["first_month"], lines["product_id"])]
    lines["reason"] = lines["reason"].str.split("; forecast").str[0]
    kg_per_case = lines["kg_need"] / lines["need"].where(lines["need"] > 0)
    netted = stock is not None
    if netted:
        lines["need"] = _net_stock(lines, stock)
    lines = lines[lines["need"] > 0.05].copy()
    lines["cases"] = np.ceil(np.round(lines["need"], 6)).astype(int)
    lines["kg"] = (lines["cases"] * kg_per_case.reindex(lines.index)).round(1)
    lines["on_hold"] = lines["product_id"].isin(holds)
    lines["note"] = np.where(lines["on_hold"], "on hold: confirm release before ordering (not in the total)", "")
    last_of = {f: max(m for m in months if window[m] == f) for f in set(window.values())}
    info = info if info is not None else pd.DataFrame(columns=["brand", "container_kg"])
    ckg = info.dropna(subset=["container_kg"]).set_index("brand")["container_kg"].to_dict() if len(info) else {}
    next_kg = p.groupby(["brand", "m"])["kg_needed"].sum()
    orders = []
    for (brand, first), l in lines.groupby(["brand", "first_month"]):
        live = l[~l["on_hold"]]
        if live.empty:
            safe, latest = l["order_by_safe"].min(), l["order_by_latest"].min()
        else:
            safe, latest = live["order_by_safe"].min(), live["order_by_latest"].min()
        last = last_of[first]
        kg = live["kg"].sum(min_count=1)
        containers, note = np.nan, ""
        if brand in ckg and pd.notna(kg) and ckg[brand] > 0:
            containers = round(kg / ckg[brand], 1)
            frac = kg / ckg[brand] - math.floor(kg / ckg[brand])
            nxt = last + pd.DateOffset(months=1)
            if 0 < frac < FILL_SUGGEST:
                add = next_kg.get((brand, nxt), np.nan)
                note = (f"last container about {100 * frac:.0f}% full: consider also covering {nxt:%b %Y}"
                        + (f" (adds about {add:,.0f} kg)" if pd.notna(add) else ""))
        orders.append(dict(order_id=f"{brand}|{first:%Y-%m}", brand=brand, covers=covers_label([first, last]),
                           first_month=f"{first:%Y-%m}", last_month=f"{last:%Y-%m}", order_by_safe=safe,
                           order_by_latest=latest, days_to_order_by=(pd.Timestamp(safe) - today).days,
                           status=status(today, safe, latest), products=int(len(live)), cases=int(live["cases"].sum()),
                           kg=round(float(kg), 1) if pd.notna(kg) else np.nan, containers=containers, container_note=note,
                           products_on_hold=int(l["on_hold"].sum()), stock_netted=netted,
                           reason=f"covers {covers_label([first, last])} arrivals"))
    orders = pd.DataFrame(orders, columns=ORDER_COLUMNS).sort_values(["order_by_safe", "brand"]).reset_index(drop=True)
    lines["order_id"] = lines["brand"] + "|" + lines["first_month"].dt.strftime("%Y-%m")
    lines = lines.sort_values(["order_id", "on_hold", "cases"], ascending=[True, True, False])[LINE_COLUMNS].reset_index(drop=True)
    return orders, lines


def current_holds(panel):
    """Products whose latest data month is on hold."""
    last = panel["month"].max()
    return set(panel.loc[(panel["month"] == last) & (panel["status"] == "fda_hold"), "product_id"])


STOCK_ORDER_EXTRA = ["kind", "next_order_by", "runs_out_before_arrival", "on_hand_cases", "months_of_cover", "cbm", "containers", "containers_needed",
                     "last_container_fill_pct", "container_bound", "pulled_forward_cases", "cbm_estimated_pct", "warning"]
STOCK_LINE_EXTRA = ["stock_at_arrival", "runs_out_before_arrival", "on_hand_cases", "position", "reorder_point", "cbm", "cbm_source", "pulled_forward", "harvest"]
NO_ON_ORDER = "stock already ordered or at sea is not included; check before ordering."


def _lines_frame(rows, pinfo, cbm):
    l = pd.DataFrame(rows)
    if l.empty:
        return l
    for c in ("on_hold", "pulled_forward", "harvest"):
        l[c] = l[c].astype(bool)              # real booleans: ~ on Python bools would give -1/-2
    l = l.merge(pinfo, on="product_id", how="left").merge(cbm, on="product_id", how="left")
    l["kg"] = (l["cases"] * l["case_weight_kg"]).round(1)
    l["cbm"] = (l["cases"] * l["cbm_per_case"]).round(3)
    return l


def build_with_stock(fc, products, brand_map, stock, today, holds=(), cover_months=2, cushion_months=policy.CUSHION_MONTHS,
                     harvest_rules=None, cbm=None, cfg=None):
    """Brand orders when stock data exists (see inventory/stock_plan.py for the rules).
    Returns dict(orders, lines, status, projection, harvest, candidates_unconfirmed, on_order_known)."""
    from . import stock_plan as SP
    today = pd.Timestamp(today).normalize()
    months_needed = len(pd.date_range(pd.to_datetime(fc["month"]).min(), today + pd.DateOffset(months=30), freq="MS"))
    F, starts, pids, extended = SP.forecast_matrix(fc, months_needed)
    s, present = SP.stock_frame(stock)
    holds = set(holds)
    rules = harvest_rules if harvest_rules is not None else SP.harvest_rules()
    members, candidates = SP.harvest_members(products, rules)
    pstatus = SP.product_status(F, starts, pids, s, holds, set(members), today, cushion_months, cover_months)
    positions = pstatus.set_index("product_id")["position"].to_dict()
    harvest = SP.harvest_orders(F, starts, pids, extended, positions, members, today, cushion_months)
    proj = SP.projection(F, starts, pids, pstatus, today, cushion_months=cushion_months, cover_months=cover_months, stock=s)
    cfg = cfg or SP.containers_config()
    cbm = cbm if cbm is not None else SP.cbm_table(products, s)
    pinfo = products[["product_id", "name", "case_weight_kg"]]
    bmap = brand_map.set_index("product_id")["brand"]
    st = pstatus.assign(brand=pstatus["product_id"].map(bmap).fillna("Unbranded"))
    idx = {p: i for i, p in enumerate(pids)}
    sales6 = None
    orders, lines = [], []

    def top_up_at(pid_list, when):
        """Owner's rule at `when`: 3 months of forecast after arrival minus stock left at arrival.
        Returns (qty, reorder point, owner_quantity details)."""
        rows = [idx[p] for p in pid_list]
        Fi = F[rows]
        days = max(0, (when - today).days)
        lv = policy.levels(Fi, starts, when, cushion_months, cover_months)
        proj_pos = np.array([positions[p] for p in pid_list]) - policy.demand_over(Fi, starts, today, days)
        oq = SP.owner_quantity(Fi, starts, proj_pos, when)
        return oq["qty"], lv["reorder_point"], oq

    for brand, g in st.groupby("brand"):
        regular = g[~g["product_id"].isin(holds) & ~g["product_id"].isin(set(members))]
        if regular.empty:
            continue
        due_now = regular[regular["order_now_cases"] > 0]
        okd = pd.to_datetime(regular["ok_until"], errors="coerce")
        if len(due_now):
            when = today
        elif okd.notna().any():
            when = okd.min()
        else:
            continue
        horizon = when + pd.Timedelta(days=INCLUDE_WITHIN_DAYS)
        inc = regular[(regular["order_now_cases"] > 0) | (okd <= horizon)]
        qty, rop, oq = top_up_at(list(inc["product_id"]), when)
        rows = []
        for k, (r, q, rp) in enumerate(zip(inc.itertuples(), qty, rop)):
            if q <= 0:
                continue
            ro = oq["runs_out"][k]
            run_note = f"runs out around {ro:%b %d, %Y} before this order arrives" if ro is not None else ""
            why = ("at or below its reorder point now" if r.order_now_cases > 0 and when == today else
                   f"reaches its reorder point by {pd.Timestamp(r.ok_until):%b %d, %Y}")
            rows.append(dict(product_id=r.product_id, cases=int(q), on_hand_cases=r.on_hand_cases, position=r.position,
                             reorder_point=round(float(rp), 1), on_hold=False, pulled_forward=False, harvest=False,
                             note=run_note, reason=why + "; lasts about 3 months after arrival",
                             stock_at_arrival=round(float(oq["stock_at_arrival"][k]), 1),
                             runs_out_before_arrival=f"{ro:%Y-%m-%d}" if ro is not None else None))
        # harvest products of this brand whose order window contains this order date ride along
        hv = harvest[harvest["product_id"].isin(g["product_id"])] if len(harvest) else harvest
        ride = hv[(pd.to_datetime(hv["order_from"]) <= when) & (when <= pd.to_datetime(hv["order_by"]))] if len(hv) else hv
        for r in ride.itertuples():
            if r.order_cases > 0:
                rows.append(dict(product_id=r.product_id, cases=int(r.order_cases), on_hand_cases=positions.get(r.product_id),
                                 position=positions.get(r.product_id), reorder_point=None, on_hold=False, pulled_forward=False,
                                 harvest=True, note=r.label, reason=f"{r.label}; covers until {r.covers_until}"))
        for pid in g[g["product_id"].isin(holds)]["product_id"]:
            rows.append(dict(product_id=pid, cases=0, on_hand_cases=positions.get(pid), position=positions.get(pid),
                             reorder_point=None, on_hold=True, pulled_forward=False, harvest=False,
                             note="on hold: confirm release before ordering (not in the total)", reason="on hold"))
        if not rows:
            continue
        l = _lines_frame(rows, pinfo, cbm)
        counted = l[~l["on_hold"]]
        # pull-forward candidates: the brand's other regular products, earliest reorder date first
        rest = regular.assign(ok=okd)                      # assign first: assigning to an empty frame adds ghost rows
        rest = rest[~rest["product_id"].isin(counted["product_id"])].sort_values("ok")
        if len(rest):
            cq, _, oq_r = top_up_at(list(rest["product_id"]), when)
            cap = np.floor(policy.demand_over(F[[idx[p] for p in rest["product_id"]]], starts, oq_r["arrival"],
                                              SP.PULL_MAX_MONTHS * policy.DAYS_PER_MONTH) - oq_r["stock_at_arrival"])
            cq = np.minimum(cq, np.maximum(cap, 0))          # never above 4 months of stock after arrival
        else:
            cq = np.array([])
        cand = rest.assign(cases=cq).merge(pinfo, on="product_id", how="left").merge(cbm, on="product_id", how="left")
        cand = cand[cand["cases"] > 0].rename(columns={"case_weight_kg": "kg_per_case"})
        cand["needed_by"] = cand["ok_until"]
        # second tier: the NEXT order of products already in this one (the following cover_months of
        # forecast after this order's coverage), most urgent products first
        inc_ids = [p for p in counted.loc[~counted["harvest"], "product_id"]]
        if inc_ids:
            rows_i = [idx[p] for p in inc_ids]
            span = policy.LEAD_DAYS + (cushion_months + cover_months) * policy.DAYS_PER_MONTH
            nxt_start = when + pd.Timedelta(days=span)
            arrival = when + pd.Timedelta(days=policy.LEAD_DAYS)
            four = policy.demand_over(F[rows_i], starts, arrival, SP.PULL_MAX_MONTHS * policy.DAYS_PER_MONTH)
            have = counted.set_index("product_id").reindex(inc_ids)
            left = have["stock_at_arrival"].fillna(0).to_numpy(float) if "stock_at_arrival" in have else np.zeros(len(inc_ids))
            nq = np.maximum(0, np.floor(four - left - have["cases"].fillna(0).to_numpy(float)))   # up to 4 months, no more
            urg = st.set_index("product_id").loc[inc_ids]
            t2 = pd.DataFrame({"product_id": inc_ids, "cases": nq,
                               "urgency": (urg["position"] / urg["reorder_point"].where(urg["reorder_point"] > 0)).to_numpy()})
            t2 = t2.merge(pinfo, on="product_id", how="left").merge(cbm, on="product_id", how="left")
            t2 = t2.rename(columns={"case_weight_kg": "kg_per_case"}).sort_values("urgency")
            t2["needed_by"] = "its next order (capped at 4 months of stock after arrival)"
            cand = pd.concat([cand, t2[t2["cases"] > 0]], ignore_index=True)
        summary, pulled = SP.pack(counted, cand[["product_id", "cases", "kg_per_case", "cbm_per_case", "needed_by"]], cfg)
        if len(pulled):
            pl = _lines_frame([dict(product_id=r.product_id, cases=int(r.cases), on_hand_cases=positions.get(r.product_id),
                                    position=positions.get(r.product_id), reorder_point=None, on_hold=False, pulled_forward=True,
                                    harvest=False, note="pulled forward to fill the container",
                                    reason=f"pulled forward to fill the last container (otherwise needed around {r.needed_by})")
                               for r in pulled.itertuples()], pinfo, cbm)
            l = pd.concat([l, pl], ignore_index=True)
        counted = l[~l["on_hold"]]
        est = counted.loc[counted["cbm_source"] == "estimated from weight", "cbm"].sum()
        oid = f"{brand}|{when:%Y-%m-%d}"
        arrival = when + pd.Timedelta(days=policy.LEAD_DAYS)
        latest = when + pd.Timedelta(days=policy.LEAD_DAYS - 105)
        nob = arrival + pd.DateOffset(months=SP.LASTS_MONTHS) - pd.Timedelta(days=policy.LEAD_DAYS)
        n_run = int(l["runs_out_before_arrival"].notna().sum()) if "runs_out_before_arrival" in l else 0
        orders.append(dict(order_id=oid, brand=brand, kind="regular", covers=f"arrives by {arrival:%b %d, %Y}",
                           next_order_by=f"{nob:%Y-%m-%d}", runs_out_before_arrival=n_run,
                           first_month=f"{when:%Y-%m}", last_month=f"{arrival:%Y-%m}", order_by_safe=f"{when:%Y-%m-%d}",
                           order_by_latest=f"{latest:%Y-%m-%d}", days_to_order_by=(when - today).days,
                           status="Order now" if when == today else status(today, when, latest),
                           products=int(len(counted)), cases=int(counted["cases"].sum()),
                           kg=round(float(counted["kg"].sum(min_count=1) or 0), 1), containers=summary["containers"],
                           container_note=(f"{int(l['pulled_forward'].sum())} product(s) pulled forward to fill the last container"
                                           if l["pulled_forward"].any() else ""),
                           products_on_hold=int(l["on_hold"].sum()), stock_netted=True,
                           reason=(f"{len(due_now)} product(s) at or below the reorder point now; "
                                   if when == today else "") + f"{len(counted) - int(l['pulled_forward'].sum()) - len(due_now if when == today else [])} "
                                  f"more reach it within 2 months" if when == today else
                                  f"next product reaches its reorder point on {when:%b %d, %Y}",
                           on_hand_cases=round(float(g["on_hand_cases"].sum()), 1), cbm=summary["cbm"],
                           containers_needed=summary["containers_needed"], last_container_fill_pct=summary["last_container_fill_pct"],
                           container_bound=summary["bound"], pulled_forward_cases=int(l.loc[l["pulled_forward"], "cases"].sum()),
                           cbm_estimated_pct=round(100 * est / summary["cbm"], 0) if summary["cbm"] else None,
                           warning="" if present["on_order"] else NO_ON_ORDER))
        l["order_id"], l["brand"] = oid, brand
        lines.append(l)
    # harvest orders that don't ride with a regular order ship as their own brand order
    riding = set(pd.concat(lines)["product_id"][pd.concat(lines)["harvest"]]) if lines else set()
    if len(harvest):
        hv = harvest[~harvest["product_id"].isin(riding) & (harvest["order_cases"] > 0)].copy()
        hv["brand"] = hv["product_id"].map(bmap).fillna("Unbranded")
        for (brand, start), g in hv.groupby(["brand", "order_from"]):
            rows = [dict(product_id=r.product_id, cases=int(r.order_cases), on_hand_cases=positions.get(r.product_id),
                         position=positions.get(r.product_id), reorder_point=None, on_hold=r.product_id in holds,
                         pulled_forward=False, harvest=True, note=r.label,
                         reason=f"{r.label}; projected stock at arrival {r.projected_stock_at_arrival:,.0f}, need until "
                                f"{r.covers_until} + cushion {r.need_after_arrival:,.0f}"
                                + (f"; months past the forecast reuse last year's ({r.extended_months})" if r.extended_months else ""))
                    for r in g.itertuples()]
            l = _lines_frame(rows, pinfo, cbm)
            counted = l[~l["on_hold"]]
            summary, _ = SP.pack(counted, pd.DataFrame(columns=["product_id", "cases", "kg_per_case", "cbm_per_case", "needed_by"]), cfg)
            st_, en = pd.Timestamp(start), pd.Timestamp(g["order_by"].iloc[0])
            est = counted.loc[counted["cbm_source"] == "estimated from weight", "cbm"].sum()
            oid = f"{brand}|harvest-{st_:%Y-%m}"
            orders.append(dict(order_id=oid, brand=brand, kind="harvest", covers=g["label"].iloc[0],
                               first_month=f"{st_:%Y-%m}", last_month=f"{en:%Y-%m}", order_by_safe=f"{st_:%Y-%m-%d}",
                               order_by_latest=f"{en:%Y-%m-%d}", days_to_order_by=(st_ - today).days, status=status(today, st_, en),
                               products=int(len(counted)), cases=int(counted["cases"].sum()),
                               kg=round(float(counted["kg"].sum(min_count=1) or 0), 1), containers=summary["containers"],
                               container_note="", products_on_hold=int(l["on_hold"].sum()), stock_netted=True,
                               reason=f"annual harvest buy, arrives {pd.Timestamp(g['expected_arrival'].iloc[0]):%b %Y}, "
                                      f"covers until {pd.Timestamp(g['covers_until'].iloc[0]):%b %Y} + cushion",
                               on_hand_cases=round(float(sum(positions.get(p, 0) for p in g["product_id"])), 1), cbm=summary["cbm"],
                               containers_needed=summary["containers_needed"], last_container_fill_pct=summary["last_container_fill_pct"],
                               container_bound=summary["bound"], pulled_forward_cases=0,
                               cbm_estimated_pct=round(100 * est / summary["cbm"], 0) if summary["cbm"] else None,
                               warning="" if present["on_order"] else NO_ON_ORDER))
            l["order_id"], l["brand"] = oid, brand
            lines.append(l)
    cols = ORDER_COLUMNS + STOCK_ORDER_EXTRA
    o = pd.DataFrame(orders).reindex(columns=cols).sort_values(["order_by_safe", "cases"], ascending=[True, False]).reset_index(drop=True)
    L = pd.concat(lines, ignore_index=True) if lines else pd.DataFrame()
    L = L.reindex(columns=LINE_COLUMNS + STOCK_LINE_EXTRA).sort_values(["order_id", "on_hold", "pulled_forward", "cases"],
                                                                       ascending=[True, True, True, False]).reset_index(drop=True)
    unconf = pd.DataFrame([dict(product_id=p, rule=r["rule_id"]) for p, r in candidates.items()])
    return dict(orders=o, lines=L, status=pstatus, projection=proj, harvest=harvest, harvest_candidates=unconf,
                on_order_known=present["on_order"], cbm=cbm, stock_columns=present)


def run(data="data", out="outputs", today=None, cover_months=2, use_stock=None):
    """use_stock=None: use data/stock.csv when it exists (the default)."""
    data, out = Path(data), Path(out)
    panel, products, _ = load(data)
    plan = pd.read_csv(out / "order_plan.csv", dtype={"product_id": str})
    bm = B.assign(products, data / "raw" / "brand_overrides.csv")
    bm.to_csv(out / "product_brands.csv", index=False)
    sp = data / "stock.csv"
    if use_stock is None:
        use_stock = sp.exists()
    if use_stock and not sp.exists():
        raise FileNotFoundError(f"--use-stock given but {sp} does not exist")
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    summ = B.summary(bm, panel, products, set(plan["product_id"]))
    if not use_stock:
        orders, lines = build(plan, bm, today, cover_months, current_holds(panel), B.brand_info(data / "raw" / "brands.csv"))
        summ.to_csv(out / "brands.csv", index=False)
        orders.to_csv(out / "brand_orders.csv", index=False)
        lines.to_csv(out / "brand_order_lines.csv", index=False)
        for f in ("product_status.csv", "stock_projection.csv", "harvest_orders.csv"):
            (out / f).unlink(missing_ok=True)
        return orders, lines
    from . import stock_plan as SP
    stock = pd.read_csv(sp, dtype={"product_id": str})
    fc = pd.read_csv(out / "forecast.csv", dtype={"product_id": str})
    cbm = SP.cbm_table(products, SP.stock_frame(stock)[0], overrides_path=data / "raw" / "case_cbm.csv")
    r = build_with_stock(fc, products, bm, stock, today, current_holds(panel), cover_months, cbm=cbm)
    last = panel["month"].max()
    avg6 = panel[panel["month"] > last - pd.DateOffset(months=6)].groupby("product_id")["units_sold"].mean()
    on_hand = r["status"].set_index("product_id")["on_hand_cases"]
    b = bm.assign(on_hand=bm["product_id"].map(on_hand).fillna(0), avg6=bm["product_id"].map(avg6).fillna(0))
    agg = b.groupby("brand").agg(on_hand_cases=("on_hand", "sum"), avg_monthly_cases_6m=("avg6", "sum"))
    agg["months_of_cover"] = (agg["on_hand_cases"] / agg["avg_monthly_cases_6m"].where(agg["avg_monthly_cases_6m"] > 0)).round(1)
    summ = summ.merge(agg.round(1).reset_index(), on="brand", how="left")
    summ.to_csv(out / "brands.csv", index=False)
    o = r["orders"].merge(agg["months_of_cover"].reset_index(), on="brand", how="left", suffixes=("_drop", ""))
    o = o.drop(columns=[c for c in o.columns if c.endswith("_drop")])
    o.to_csv(out / "brand_orders.csv", index=False)
    r["lines"].to_csv(out / "brand_order_lines.csv", index=False)
    r["status"].assign(brand=r["status"]["product_id"].map(bm.set_index("product_id")["brand"])).to_csv(out / "product_status.csv", index=False)
    r["projection"].to_csv(out / "stock_projection.csv", index=False)
    r["harvest"].to_csv(out / "harvest_orders.csv", index=False)
    r["harvest_candidates"].to_csv(out / "harvest_candidates.csv", index=False)
    r["cbm"].to_csv(out / "case_cbm_used.csv", index=False)
    return o, r["lines"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--today", default=None)
    ap.add_argument("--cover-months", type=int, default=2)
    ap.add_argument("--use-stock", action=argparse.BooleanOptionalAction, default=None,
                    help="default: on when data/stock.csv exists")
    a = ap.parse_args()
    o, l = run(a.data, a.out, a.today, a.cover_months, a.use_stock)
    print(f"{len(o)} brand orders, {len(l)} lines -> {a.out}/brand_orders.csv, brand_order_lines.csv")
    print(o["status"].value_counts().to_string())
