"""Per-product monthly order plan and festival readiness. Gross need only (no stock netting).

Stocking level per product-month:
  regular product   cases = mean + k * max(p90 - mean, 0); k = K_FESTIVAL in Diwali and
                    pre-Diwali months, K_NORMAL in all other months (chosen by the backtest)
  occasional        sells in < OCCASIONAL_SHARE of its listed months: cases = mean, no buffer
  new               < NEW_MONTHS listed months: mean of its selling months, no ML, low confidence
  inactive          no sales in the last INACTIVE_MONTHS months: left out, listed separately

Order-by dates: safe = month start - 130 days (worst-case lead time), latest = month start - 105.
No shelf-life value is used anywhere here. Quantities are in current-code cases.
The iMessage agent reads order_plan.csv and festival_readiness.csv: keep the columns stable.
"""
import numpy as np
import pandas as pd

from .forecast import TIER1, CAL, festival_flags, month_kind

K_FESTIVAL = 0.5   # overwritten by run.py with the backtest's choice; also stored in models/meta.json
K_NORMAL = 0.5
OCCASIONAL_SHARE = 0.6
NEW_MONTHS = 6
INACTIVE_MONTHS = 6
LEAD_SAFE_DAYS, LEAD_LATEST_DAYS = 130, 105

ORDER_PLAN_COLUMNS = ["product_id", "name", "category", "month", "cases_needed", "kg_needed",
                      "order_by_safe", "order_by_latest", "status", "reason"]
READINESS_COLUMNS = ["season", "festival_month", "in_warehouse_by", "order_by_safe", "order_by_latest", "status",
                     "rank", "product_id", "name", "category", "cases_pre_month", "cases_festival_month",
                     "extra_cases_vs_normal", "kg_extra", "last_lift", "reason"]
LABEL = {"diwali": "Diwali", "navratri_dussehra": "Navratri/Dussehra", "holi": "Holi", "ramadan": "Ramadan",
         "eid_al_fitr": "Eid al-Fitr", "eid_al_adha": "Eid al-Adha", "raksha_bandhan": "Raksha Bandhan",
         "ganesh_chaturthi": "Ganesh Chaturthi", "sankranti": "Sankranti"}


def status(today, safe, latest):
    if today <= safe:
        return f"order by {safe:%Y-%m-%d} ({(safe - today).days} days)"
    if today <= latest:
        return "past safe date: in time only if shipping takes <= 105 days"
    return "too late even at 105 days: expedite or expect a shortfall"


def product_profile(panel, inactive_months=INACTIVE_MONTHS):
    """Per product: listed months, months sold, share sold, last sale, recent sales."""
    listed = panel[panel["status"] != "not_listed"]
    last = panel["month"].max()
    recent = listed[listed["month"] > last - pd.DateOffset(months=inactive_months)]
    p = listed.groupby("product_id").agg(listed_months=("month", "size"),
                                         months_sold=("units_sold", lambda s: int((s > 0).sum())))
    p["share_sold"] = p["months_sold"] / p["listed_months"]
    p["last_sale"] = listed[listed["units_sold"] > 0].groupby("product_id")["month"].max()
    p["recent_units"] = recent.groupby("product_id")["units_sold"].sum()
    nz = listed[listed["units_sold"] > 0].groupby("product_id")["units_sold"].mean()
    p["mean_selling_month"] = nz
    return p.fillna({"recent_units": 0.0})


def last_occurrence_lifts(clean):
    """For every product, festival group and kind ('this'/'next'): the most recent past month with
    that flag and the product's sales there relative to its normal month (median of months with no
    tier-1 festival). Returns dict (product_id, group, kind) -> (month, ratio)."""
    fl = festival_flags(sorted(clean["month"].unique()))
    d = clean[["product_id", "month", "demand"]].join(fl, on="month")
    any_this = d[[f"fest_{g}_this" for g in TIER1]].max(axis=1)
    normal = d[any_this == 0].groupby("product_id")["demand"].median()
    d["ratio"] = d["demand"] / d["product_id"].map(normal.where(normal > 0))
    out = {}
    for g in TIER1:
        for w in ("this", "next"):
            occ = d[(d[f"fest_{g}_{w}"] == 1) & d["ratio"].notna()].sort_values("month")
            for r in occ.groupby("product_id").tail(1).itertuples():
                out[(r.product_id, g, w)] = (r.month, float(r.ratio))
    return out


ARTIFACTS = ("post_gap_rebound", "code_switch")


def _profile_reason(pid, month, kind, spk_by, prof):
    """Plain-language reason from the product's own history for this calendar month."""
    sp = spk_by.get((pid, month.month))
    season = {"Diwali month": ", Diwali", "pre-Diwali month": ", before Diwali"}.get(kind, "")
    if sp is not None and len(sp):
        last = sp.iloc[-1]
        n, of = int(last["years_spiked"]), int(last["years_seen"])
        when = "last year" if last["month"].year == month.year - 1 else f"in {last['month']:%b %Y}"
        if n >= 2:
            return f"rises every {month:%B} ({n} of {of} years{season}): {when} {last['ratio']:.1f}x normal"
        if last["cause"] == "one_off":
            return f"one-off spike in {last['month']:%b %Y} not repeated in forecast"
        return f"spiked once in {last['month']:%b %Y} ({n} of {of} years, {last['ratio']:.1f}x normal; {last['detail']})"
    if kind != "other" and prof is not None:
        col = "diwali_lift_pre_month" if kind == "pre-Diwali month" else "diwali_lift_diwali_month"
        v = prof.get(col)
        if v is not None and np.isfinite(v):
            return f"{kind}: past {kind}s averaged {v:.1f}x normal"
    return "no recurring spike in this month"


def order_plan(panel, clean, products, fc, today, k_festival=None, k_normal=None, spikes=None, profiles=None, keep=None):
    """fc: forecast long df with product_id, month, mean, p90. k_festival applies to Diwali and
    pre-Diwali months, k_normal to all other months. keep: products never treated as inactive (holds the
    owner released: no recent sales because they were on hold). Returns (order_plan, inactive)."""
    kf = K_FESTIVAL if k_festival is None else k_festival
    kn = K_NORMAL if k_normal is None else k_normal
    today = pd.Timestamp(today).normalize()
    prof = product_profile(panel)
    inactive = prof[(prof["recent_units"] <= 0) & ~prof.index.isin(set(keep or ()))].index
    imputed = clean[clean["imputed"]].groupby("product_id").size()
    spk_by = {}
    if spikes is not None and len(spikes):
        s = spikes[~spikes["cause"].isin(ARTIFACTS)].sort_values("month")
        spk_by = {k: g for k, g in s.groupby(["product_id", s["month"].dt.month])}
    profs = profiles.set_index("product_id").to_dict("index") if profiles is not None else {}
    p = fc.merge(products[["product_id", "name", "category", "case_weight_kg"]], on="product_id", how="left")
    p = p[~p["product_id"].isin(inactive)].copy()
    p = p.join(prof[["listed_months", "share_sold", "mean_selling_month"]], on="product_id")
    kinds = {m: month_kind(m) for m in p["month"].unique()}
    p["kind"] = p["month"].map(kinds)
    p["k"] = np.where(p["kind"] == "other", kn, kf)
    buf = np.maximum(p["p90"] - p["mean"], 0)
    is_new = p["listed_months"] < NEW_MONTHS
    occasional = ~is_new & (p["share_sold"] < OCCASIONAL_SHARE)
    p["cases_needed"] = np.where(is_new, p["mean_selling_month"],
                                 np.where(occasional, p["mean"], p["mean"] + p["k"] * buf)).round(1)
    p["kg_needed"] = (p["cases_needed"] * p["case_weight_kg"]).round(1)
    safe = p["month"] - pd.Timedelta(days=LEAD_SAFE_DAYS)
    latest = p["month"] - pd.Timedelta(days=LEAD_LATEST_DAYS)
    p["status"] = [status(today, s, l) for s, l in zip(safe, latest)]
    reasons = []
    for r, b, new, occ in zip(p.itertuples(), buf, is_new, occasional):
        if new:
            txt = f"new product ({int(r.listed_months)} months of history): mean of its selling months, low confidence"
        elif occ:
            txt = "occasional bulk orders: confirm upcoming orders with customers"
        else:
            txt = (_profile_reason(r.product_id, r.month, r.kind, spk_by, profs.get(r.product_id))
                   + f"; forecast {r.mean:.0f} + buffer {r.k * b:.0f} cases")
        n_imp = imputed.get(r.product_id, 0)
        if n_imp:
            txt += f"; {n_imp} month(s) it couldn't be sold were filled from its usual pattern"
        reasons.append(txt)
    p["reason"] = reasons
    p["order_by_safe"] = safe.dt.strftime("%Y-%m-%d")
    p["order_by_latest"] = latest.dt.strftime("%Y-%m-%d")
    p["month"] = p["month"].dt.strftime("%Y-%m")
    p = p.sort_values(["order_by_safe", "product_id"])[ORDER_PLAN_COLUMNS].reset_index(drop=True)
    inact = prof.loc[inactive, ["listed_months", "months_sold", "last_sale"]].reset_index()
    inact["last_sale"] = inact["last_sale"].dt.strftime("%Y-%m")
    inact = inact.merge(products[["product_id", "name", "category"]], on="product_id", how="left")
    return p, inact


def festival_readiness(plan, clean, today, top_n=15):
    """Top products per upcoming festival season. Festival month = first month the window touches;
    festivals with the same first month form one season; in warehouse by = 1st of the month before."""
    today = pd.Timestamp(today).normalize()
    months = pd.to_datetime(plan["month"]).dt.to_period("M").dt.to_timestamp()
    pl = plan.assign(m=months)
    first, last = pl["m"].min(), pl["m"].max()
    seasons = {}
    for (g, _yr), fm in CAL["first_month"].items():
        if first <= fm <= last:
            seasons.setdefault(fm, []).append(g)
    lifts = last_occurrence_lifts(clean)
    normal_months = [m for m in pl["m"].unique() if month_kind(m) == "other"]
    base = pl[pl["m"].isin(normal_months)] if normal_months else pl
    normal_level = base.groupby("product_id")["cases_needed"].median()
    wide = pl.pivot_table(index="product_id", columns="m", values="cases_needed", aggfunc="sum")
    info = pl.drop_duplicates("product_id").set_index("product_id")[["name", "category"]]
    kg_per_case = (pl["kg_needed"] / pl["cases_needed"]).groupby(pl["product_id"]).max()
    rows = []
    for fm, groups in sorted(seasons.items()):
        pre = fm - pd.DateOffset(months=1)
        inw = pre
        safe, latest = inw - pd.Timedelta(days=LEAD_SAFE_DAYS), inw - pd.Timedelta(days=LEAD_LATEST_DAYS)
        c_f = wide.get(fm, pd.Series(0.0, index=wide.index)).fillna(0)
        c_p = wide[pre].fillna(0) if pre in wide else pd.Series(np.nan, index=wide.index)
        n_months = 1 + int(pre in wide)
        extra = c_f + c_p.fillna(0) - n_months * normal_level.reindex(wide.index).fillna(0)
        top = extra[extra > 0].sort_values(ascending=False).head(top_n)
        label = " + ".join(LABEL[g] for g in groups)
        for rank, (pid, ex) in enumerate(top.items(), 1):
            lift = max([lifts.get((pid, g, "this"), (None, np.nan))[1] for g in groups], default=np.nan)
            why = (f"sold {lift:.1f}x its normal last {label} season" if np.isfinite(lift) else
                   f"no past {label} sales for this product; category pattern used")
            rows.append(dict(season=label, festival_month=f"{fm:%Y-%m}", in_warehouse_by=f"{inw:%Y-%m-%d}",
                             order_by_safe=f"{safe:%Y-%m-%d}", order_by_latest=f"{latest:%Y-%m-%d}",
                             status=status(today, safe, latest), rank=rank, product_id=pid,
                             name=info.at[pid, "name"], category=info.at[pid, "category"],
                             cases_pre_month=round(float(c_p.get(pid, np.nan)), 1),
                             cases_festival_month=round(float(c_f[pid]), 1), extra_cases_vs_normal=round(float(ex), 1),
                             kg_extra=round(float(ex * kg_per_case.get(pid, np.nan)), 1),
                             last_lift=round(lift, 2) if np.isfinite(lift) else np.nan, reason=why))
    return pd.DataFrame(rows, columns=READINESS_COLUMNS)
