"""Find each product's sales spikes and the likely reason for each one.

The product's own sales come first; festivals are only checked as possible explanations afterwards.

Spike (products with >= MIN_SOLD_MONTHS months with sales):
  baseline = median of the product's other months in a centered 7-month window (t-3..t+3),
             leaving out month t and months that were gaps (recorded or suspected) or not listed
  spike    = sales >= max(2 x baseline, baseline + 3 x MAD) and sales >= MIN_CASES

Cause = the first of these that matches:
  post_gap_rebound    one of the previous 2 months was a recorded or suspected gap
  code_switch         within 1 month of an OLD-code merge for this product (old code's last sale or
                      the current code's first sale)
  diwali              the month overlaps the 60 days before Diwali through Diwali (main date)
  other_festival      the month overlaps a tier-0 festival window (named): a possible link only
  recurring_seasonal  the product also spiked in the same calendar month in another year
  one_off             none of the above: possibly a promotion or bulk order; ask the owner
Recurrence: "seen in N of M years" = years with a spike in that calendar month / years in which
that calendar month was observed (sellable) for the product.

All functions take whatever history they are given, so a backtest fold can run them on its own
training data only.
"""
import numpy as np
import pandas as pd

from .forecast import CAL

MIN_SOLD_MONTHS = 12
MIN_CASES = 10
WINDOW = 3
DIWALI_DAYS_BEFORE = 60
CAUSES = ["post_gap_rebound", "code_switch", "diwali", "other_festival", "recurring_seasonal", "one_off"]


def code_switch_months(item_map):
    """{product_id: set of month starts} where an OLD code stopped or its current code started."""
    if item_map is None or not len(item_map):
        return {}
    im = item_map.copy()
    for c in ("first_sale", "last_sale"):
        im[c] = pd.to_datetime(im[c]).dt.to_period("M").dt.to_timestamp()
    old = im[im["merge_rule"] == "OLD -> current code"]
    own = im[im["raw_item"] == im["product_id"]].set_index("product_id")["first_sale"]
    out = {}
    for r in old.itertuples():
        s = out.setdefault(r.product_id, set())
        s.add(r.last_sale)
        if r.product_id in own.index:
            s.add(own[r.product_id])
    return out


def _diwali_months():
    out = set()
    for d in CAL["diwali_main"]:
        out.update(pd.period_range(d - pd.Timedelta(days=DIWALI_DAYS_BEFORE), d, freq="M").to_timestamp())
    return out


def _tier0_by_month():
    out = {}
    for r in CAL["tier0"].itertuples():
        for m in pd.period_range(r.start_date, r.end_date, freq="M").to_timestamp():
            out.setdefault(m, set()).add(r.festival)
    return out


def detect(panel, clean, item_map=None, products=None):
    """Return one row per spike: product_id, name, month, cases, baseline, ratio, cause, detail, recurrence.
    panel: product_id, month, units_sold, status. clean: product_id, month, unavailable (from gaps.py)."""
    d = panel[["product_id", "month", "units_sold", "status"]].merge(
        clean[["product_id", "month", "unavailable"]], on=["product_id", "month"], how="left")
    d["unavailable"] = d["unavailable"].fillna(False).astype(bool)
    d["gap"] = d["unavailable"] & (d["status"] != "not_listed")
    d = d.sort_values(["product_id", "month"])
    sold = d[d["units_sold"] > 0].groupby("product_id").size()
    keep = sold[sold >= MIN_SOLD_MONTHS].index
    last_known = d["month"].max()     # a fold only knows switches that already happened
    switches = {p: {m for m in ms if m <= last_known} for p, ms in code_switch_months(item_map).items()}
    diwali, tier0 = _diwali_months(), _tier0_by_month()

    rows = []
    for pid, g in d[d["product_id"].isin(keep)].groupby("product_id", sort=False):
        x, excl, gap = g["units_sold"].to_numpy(float), g["unavailable"].to_numpy(), g["gap"].to_numpy()
        months = list(g["month"])
        n = len(x)
        for t in range(n):
            if excl[t] or x[t] < MIN_CASES:
                continue
            idx = [i for i in range(max(0, t - WINDOW), min(n, t + WINDOW + 1)) if i != t and not excl[i]]
            if not idx:
                continue
            vals = x[idx]
            b = float(np.median(vals))
            mad = float(np.median(np.abs(vals - b)))
            if x[t] >= max(2 * b, b + 3 * mad):
                rows.append(dict(product_id=pid, month=months[t], cases=x[t], baseline=b,
                                 ratio=x[t] / b if b > 0 else np.inf,
                                 after_gap=bool(gap[max(0, t - 2):t].any())))
    sp = pd.DataFrame(rows, columns=["product_id", "month", "cases", "baseline", "ratio", "after_gap"])
    if sp.empty:
        return sp.assign(name=[], cause=[], detail=[], recurrence=[])

    # recurrence: years with a spike in this calendar month / years that month was observed
    sp["cal_month"] = sp["month"].dt.month
    hits = sp.groupby(["product_id", "cal_month"])["month"].agg(lambda s: s.dt.year.nunique())
    obs = d[~d["unavailable"]].assign(cal_month=lambda x: x["month"].dt.month).groupby(
        ["product_id", "cal_month"])["month"].agg(lambda s: s.dt.year.nunique())
    sp["years_spiked"] = [hits[(p, m)] for p, m in zip(sp["product_id"], sp["cal_month"])]
    sp["years_seen"] = [obs.get((p, m), 1) for p, m in zip(sp["product_id"], sp["cal_month"])]
    sp["recurrence"] = [f"seen in {a} of {b} years" for a, b in zip(sp["years_spiked"], sp["years_seen"])]

    causes, details = [], []
    for r in sp.itertuples():
        if r.after_gap:
            c, det = "post_gap_rebound", "previous 2 months include a gap (product couldn't be sold)"
        elif any(abs((r.month.year - s.year) * 12 + r.month.month - s.month) <= 1 for s in switches.get(r.product_id, ())):
            c, det = "code_switch", "within 1 month of an OLD-code merge"
        elif r.month in diwali:
            c, det = "diwali", f"within the {DIWALI_DAYS_BEFORE} days before Diwali"
        elif r.month in tier0:
            c, det = "other_festival", "possible link: " + ", ".join(sorted(tier0[r.month]))
        elif r.years_spiked >= 2:
            c, det = "recurring_seasonal", f"also spiked in {r.month:%B} in another year"
        else:
            c, det = "one_off", "no pattern or festival match: possible promotion or bulk order, ask the owner"
        causes.append(c)
        details.append(det)
    sp["cause"], sp["detail"] = causes, details
    if products is not None:
        sp = sp.merge(products[["product_id", "name"]], on="product_id", how="left")
    else:
        sp["name"] = ""
    sp["baseline"] = sp["baseline"].round(1)
    sp["ratio"] = sp["ratio"].round(2)
    return sp[["product_id", "name", "month", "cases", "baseline", "ratio", "cause", "detail", "recurrence",
               "years_spiked", "years_seen"]].sort_values(["product_id", "month"]).reset_index(drop=True)


def spike_flags(panel, spikes, causes=("diwali", "other_festival", "recurring_seasonal", "one_off")):
    """Boolean `spike` per product-month for the model feature. Rebounds after gaps and code
    switches are bookkeeping artifacts, not demand patterns, so they are left out."""
    s = spikes[spikes["cause"].isin(causes)][["product_id", "month"]].assign(spike=True)
    m = panel[["product_id", "month"]].merge(s, on=["product_id", "month"], how="left")["spike"]
    return m.notna().to_numpy() if len(s) else np.zeros(len(panel), dtype=bool)


def one_off_caps(panel, spikes):
    """target_cap per product-month: 2 x baseline on one-off spike months, NaN elsewhere (variant B)."""
    s = spikes[spikes["cause"] == "one_off"][["product_id", "month", "baseline"]]
    m = panel[["product_id", "month"]].merge(s, on=["product_id", "month"], how="left")
    return (2 * pd.to_numeric(m["baseline"], errors="coerce")).to_numpy(dtype=float)


def summary(spikes, panel, products=None):
    """Spike count and share of total volume (cases, and kg where known) by cause."""
    total = panel["units_sold"].sum()
    out = spikes.groupby("cause").agg(spikes=("cases", "size"), products=("product_id", "nunique"),
                                      cases_in_spike_months=("cases", "sum"))
    out["share_of_all_cases_%"] = 100 * out["cases_in_spike_months"] / total
    if products is not None and "case_weight_kg" in products:
        kgw = products.set_index("product_id")["case_weight_kg"]
        kg_total = (panel["units_sold"] * panel["product_id"].map(kgw)).sum()
        kg = (spikes["cases"] * spikes["product_id"].map(kgw)).groupby(spikes["cause"]).sum()
        out["share_of_all_kg_%"] = 100 * kg / kg_total
    out = out.reindex([c for c in CAUSES if c in out.index])
    out.loc["all spikes"] = out.sum(numeric_only=True)
    out.loc["all spikes", "products"] = spikes["product_id"].nunique()
    return out.round(1)


def profiles(panel, clean, spikes, products):
    """One row per product: regularity, typical month, high/low calendar months, Diwali lifts,
    one-off spikes and their share of the product's volume."""
    from .forecast import festival_flags
    n_months = panel["month"].nunique()
    d = panel.merge(clean[["product_id", "month", "unavailable"]], on=["product_id", "month"], how="left")
    d["unavailable"] = d["unavailable"].fillna(False).astype(bool)
    fl = festival_flags(sorted(d["month"].unique()))
    d = d.join(fl[["fest_diwali_this", "fest_diwali_next"]], on="month")
    av = d[~d["unavailable"]]
    typical = av.groupby("product_id")["units_sold"].median()
    normal = av[(av["fest_diwali_this"] == 0) & (av["fest_diwali_next"] == 0)].groupby("product_id")["units_sold"].median()
    cal = av.assign(cm=av["month"].dt.month).groupby(["product_id", "cm"])["units_sold"].mean().unstack()
    rel = cal.div(typical.where(typical > 0), axis=0)
    abbr = {i: pd.Timestamp(2000, i, 1).strftime("%b") for i in range(1, 13)}
    high = rel.apply(lambda r: ", ".join(abbr[m] for m in r.index if r[m] >= 1.5), axis=1)
    low = rel.apply(lambda r: ", ".join(abbr[m] for m in r.index if r[m] <= 0.5), axis=1)

    def lift(col):
        x = av[av[col] == 1].groupby("product_id")["units_sold"].mean()
        return (x / normal.where(normal > 0)).round(2)

    one = spikes[spikes["cause"] == "one_off"].groupby("product_id")["cases"].agg(["size", "sum"])
    tot = d.groupby("product_id")["units_sold"].sum()
    p = pd.DataFrame({
        "months_sold": d.assign(s=d["units_sold"] > 0).groupby("product_id")["s"].sum().astype(int),
        "of_months": n_months,
        "typical_monthly_cases": typical.round(1),
        "high_months": high, "low_months": low,
        "diwali_lift_pre_month": lift("fest_diwali_next"), "diwali_lift_diwali_month": lift("fest_diwali_this"),
        "spikes": spikes.groupby("product_id").size(),
        "one_off_spikes": one["size"],
        "one_off_share_of_volume_%": (100 * one["sum"] / tot.where(tot > 0)).round(1),
    })
    p = p.fillna({"spikes": 0, "one_off_spikes": 0, "one_off_share_of_volume_%": 0, "high_months": "", "low_months": ""})
    p[["spikes", "one_off_spikes"]] = p[["spikes", "one_off_spikes"]].astype(int)
    p.index.name = "product_id"
    p = p.reset_index().merge(products[["product_id", "name", "category"]], on="product_id", how="left")
    cols = ["product_id", "name", "category"] + [c for c in p.columns if c not in ("product_id", "name", "category")]
    return p[cols]
