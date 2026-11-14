"""Data checks on the ingested sales (aggregates only, no raw order rows).

regularity()      products by how many of the months they sold in, with share of volume (kg)
suffix_patterns() every suffix pattern found in item codes, with counts and examples
lb_switch_ratios() for bulk codes converted from lb: converted old-code sales vs new-code sales
                   in the 3 months each side of the switch
"""
import re

import numpy as np
import pandas as pd

from . import ingest

REG_BINS = [(24, 99, ">=24 months"), (18, 23, "18-23 months"), (6, 17, "6-17 months"), (0, 5, "<6 months")]


def months_sold(panel):
    return panel.assign(sold=panel["units_sold"] > 0).groupby("product_id")["sold"].sum()


def regularity_groups(panel):
    n = months_sold(panel)
    lab = pd.Series(index=n.index, dtype=object)
    for lo, hi, name in REG_BINS:
        lab[(n >= lo) & (n <= hi)] = name
    return lab.rename("regularity").reset_index()


def regularity(panel, products):
    n_months = panel["month"].nunique()
    kg = (panel.merge(products[["product_id", "case_weight_kg"]], on="product_id", how="left")
          .assign(kg=lambda d: d["units_sold"] * d["case_weight_kg"]))
    per = kg.groupby("product_id").agg(kg=("kg", "sum"), cases=("units_sold", "sum"),
                                       has_weight=("case_weight_kg", lambda s: s.notna().all()))
    per = per.join(regularity_groups(panel).set_index("product_id"))
    t = per.groupby("regularity").agg(products=("kg", "size"), cases=("cases", "sum"), kg=("kg", "sum"),
                                      products_without_weight=("has_weight", lambda s: int((~s).sum())))
    t["share_of_kg_%"] = 100 * t["kg"] / t["kg"].sum()
    t["share_of_cases_%"] = 100 * t["cases"] / t["cases"].sum()
    t = t.reindex([b[2] for b in REG_BINS])
    t.index.name = f"sold in (of {n_months} months)"
    return t.round(1)


_CODE = re.compile(r"^([A-Za-z]+\d+)(.*)$")


def suffix_patterns(item_map):
    """Group raw codes by what follows the letters+digits stem; letters are kept so OLD/TBD/NEW show."""
    rows = []
    for code in item_map["raw_item"]:
        m = _CODE.match(code)
        if code.upper().startswith("OLD"):
            pat = "OLD prefix (e.g. OLD-XX123)"
        elif not m:
            pat = "non-standard code (no letters+digits stem)"
        elif not m.group(2).strip():
            pat = "(none)"
        else:
            pat = "<stem>" + re.sub(r"\d", "9", m.group(2))   # keep spacing/hyphens/case exactly
        rows.append((pat, code))
    t = pd.DataFrame(rows, columns=["pattern", "code"])
    out = t.groupby("pattern")["code"].agg(codes="size", examples=lambda s: ", ".join(sorted(s)[:3]))
    return out.sort_values("codes", ascending=False)


def lb_switch_ratios(raw_dir="data/raw/sales", item_map_path="data/item_map.csv", window=3):
    """For each lb-recorded code merged into a current code: mean monthly converted sales of the old
    code in the `window` months up to its last sale, divided by mean monthly sales of the current code
    in the `window` months after. Calendar months with no sales count as 0."""
    raw = ingest.read_raw(raw_dir)
    d, _ = ingest.drop_non_products(raw)
    im = pd.read_csv(item_map_path).set_index("raw_item")
    d = d[d["raw_item"].isin(im.index)]
    d["cases_current"] = d["qty"] * d["raw_item"].map(im["qty_to_cases"]) * d["raw_item"].map(im["to_current_cases"])
    d["month"] = d["date"].dt.to_period("M")
    monthly = d.groupby(["raw_item", "month"])["cases_current"].sum()
    lb = im[(im["qty_recorded_in"] != "cases")]
    rows = []
    for code, r in lb.iterrows():
        if not r["merged"]:
            rows.append(dict(code=code, product_id=r["product_id"], note="current code recorded in lb: no switch to compare"))
            continue
        old = monthly.loc[code] if code in monthly.index.get_level_values(0) else pd.Series(dtype=float)
        new = monthly.loc[r["product_id"]] if r["product_id"] in monthly.index.get_level_values(0) else pd.Series(dtype=float)
        if old.empty:
            continue
        last_old = old[old > 0].index.max()
        before = pd.period_range(last_old - (window - 1), last_old, freq="M")
        after = pd.period_range(last_old + 1, last_old + window, freq="M")
        b = old.reindex(before, fill_value=0).mean()
        a = new.reindex(after, fill_value=0).mean()
        ratio = b / a if a > 0 else np.inf
        rows.append(dict(code=code, product_id=r["product_id"], switch_month=str(last_old + 1),
                         old_monthly_before=round(b, 1), new_monthly_after=round(a, 1),
                         ratio=round(ratio, 2) if np.isfinite(ratio) else np.inf,
                         flag=bool(not (0.5 <= ratio <= 2)),
                         note="" if a > 0 else "current code has no sales in the 3 months after"))
    return pd.DataFrame(rows)
