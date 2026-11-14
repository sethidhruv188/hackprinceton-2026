"""Current inventory export (NetSuite "Product list Inventory") -> data/stock.csv.

Layout (found by scanning, not hard-coded): a title block, then a two-row header such as
  Item | Description | Current Quantity Available | <location> / Current Quantity On Hand | Total / Current Quantity On Hand
then rows grouped under section rows ("Assembly", "Inventory Item"). Columns are recognised by
name: on hand, available, on order, committed/held, expiry, CBM/volume. Missing ones stay empty.

Units: the export mostly counts individual packs, not cases (read as cases, products would hold
implausibly many months of sales, rising with the pack count; read as packs, a few months). A few
item-code prefixes count cases instead (decided per prefix from the same evidence: prefix_units()).
Pack-counted units are divided by the item's packs per case, then converted like sales:
pounds -> bags for lb-recorded bulk codes, OLD-code cases -> current-code cases. Codes whose pack
count is unknown are kept as reported and flagged. Set INVENTORY_UNIT = "cases" to skip the
pack division if the owner says the export is in cases.

  python -m inventory.stock_ingest        # writes data/stock.csv and prints the checks
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import packs as P
from .schema import load

INVENTORY_UNIT = "packs"
STOCK_COLUMNS = ["product_id", "on_hand_cases", "on_order_cases", "on_order_arrival", "held_cases",
                 "earliest_expiry", "cbm_per_case", "raw_codes", "unit_note"]
COLUMN_PATTERNS = {             # combined (two-row) header text -> field; first match wins
    "on_hand": r"on\s*hand", "available": r"available", "on_order": r"on\s*order|in\s*transit|incoming",
    "held": r"committed|on\s*hold|held|quarantine", "expiry": r"expir|best\s*before|bbd",
    "cbm": r"\bcbm\b|cubic|volume|dimension",
}


def find_header(raw, scan=25):
    for i in range(min(scan, len(raw))):
        vals = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        if "item" in vals and any(v.startswith("description") for v in vals):
            return i
    raise ValueError("no header row with 'Item' and 'Description' in the first rows")


def read_inventory(path):
    """Returns (table per raw item code, columns_found dict). Rows from all sections and
    locations are summed per item code."""
    raw = pd.read_excel(path, header=None, dtype=object)
    h = find_header(raw)
    top = raw.iloc[h].ffill()                      # location names span their sub-columns
    sub = raw.iloc[h + 1] if h + 1 < len(raw) else pd.Series([None] * raw.shape[1])
    names = [" / ".join(str(x).strip() for x in (a, b) if pd.notna(x) and str(x).strip() not in ("", "\xa0"))
             for a, b in zip(top, sub)]
    sub_is_header = sub.notna().any() and not pd.to_numeric(sub, errors="coerce").notna().any()
    body = raw.iloc[h + (2 if sub_is_header else 1):].copy()
    body.columns = range(raw.shape[1])
    lower = [n.lower() for n in names]
    col = {"item": lower.index("item"), "description": next(i for i, n in enumerate(lower) if n.startswith("description"))}
    found = {}
    for field, pat in COLUMN_PATTERNS.items():
        hits = [i for i, n in enumerate(lower) if re.search(pat, n)]
        if field == "on_hand" and hits:
            tot = [i for i in hits if lower[i].startswith("total")]
            found[field] = tot[0] if tot else hits            # total column, else every location
        elif hits:
            found[field] = hits[0]
    item = body[col["item"]]
    nums = [c for c in range(raw.shape[1]) if c not in col.values()]
    numeric = body[nums].apply(pd.to_numeric, errors="coerce")
    is_section = item.notna() & numeric.isna().all(axis=1) & body[col["description"]].isna()
    section = item.where(is_section).ffill()
    rows = body[~is_section & item.notna()]
    rows = rows[~rows[col["item"]].astype(str).str.strip().str.lower().str.startswith("total")]
    out = pd.DataFrame({"raw_item": rows[col["item"]].astype(str).str.strip().str.replace(r"\s+", " ", regex=True),
                        "description": rows[col["description"]].astype(str).str.strip(),
                        "section": section.loc[rows.index]})

    def num(c):
        if isinstance(c, list):
            return rows[c].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
        return pd.to_numeric(rows[c], errors="coerce")

    for field in ("on_hand", "available", "on_order", "held", "cbm"):
        out[field] = num(found[field]).to_numpy() if field in found else np.nan
    out["expiry"] = pd.to_datetime(rows[found["expiry"]], errors="coerce").to_numpy() if "expiry" in found else pd.NaT
    agg = out.groupby("raw_item").agg(description=("description", "last"), section=("section", "last"),
                                      rows=("raw_item", "size"),
                                      **{f: (f, lambda s: s.sum(min_count=1)) for f in ("on_hand", "available", "on_order", "held")},
                                      cbm=("cbm", "max"), expiry=("expiry", "min")).reset_index()
    columns_found = {f: (names[c] if not isinstance(c, list) else " + ".join(names[i] for i in c)) for f, c in found.items()}
    return agg, columns_found


def prefix_units(detail, panel, min_items=8, months=6):
    """Per item-code prefix, does the export count packs or cases? For prefixes with at least
    `min_items` stocked, selling items: 'cases' when dividing by packs gives a median cover under
    0.5 months while the reported number gives 0.5-24 months; otherwise 'packs'. Returns
    (dict prefix -> unit, evidence table)."""
    last = panel["month"].max()
    avg = panel[panel["month"] > last - pd.DateOffset(months=months)].groupby("product_id")["units_sold"].mean()
    d = detail[(detail["match"] != "unmatched") & (detail["on_hand"] > 0)].copy()
    d["avg6"] = d["product_id"].map(avg)
    d = d[d["avg6"] > 1]
    d["prefix"] = d["raw_item"].str.extract(r"^([A-Za-z]+)")[0].str.upper()
    d["as_reported"] = d["on_hand"] * d["qty_to_cases"] * d["to_current_cases"] / d["avg6"]
    d["as_packs"] = d["as_reported"] / d["packs"].where(d["packs"] > 0)
    ev = d.groupby("prefix").agg(items=("raw_item", "size"), cover_if_cases=("as_reported", "median"),
                                 cover_if_packs=("as_packs", "median"))
    ev["unit"] = np.where((ev["items"] >= min_items) & (ev["cover_if_packs"] < 0.5) & ev["cover_if_cases"].between(0.5, 24),
                          "cases", "packs")
    return ev["unit"].to_dict(), ev.round(2).reset_index()


def map_to_products(inv, item_map, products, unit=INVENTORY_UNIT, prefix_unit=None, code_map=None, ignore=None):
    """Map raw codes to current products and convert to current-code cases.
    code_map: {RAW_UPPER: product_id} from owner answers for codes with no sales; ignore: {RAW_UPPER} the
    owner says are packaging (match "ignored (owner: packaging)", never counted).
    Returns (stock per product, per-code detail incl. unmatched)."""
    im = item_map.copy()
    im["key"] = im["raw_item"].str.upper()
    inv = inv.copy()
    inv["key"] = inv["raw_item"].str.upper()
    d = inv.merge(im[["key", "product_id", "qty_to_cases", "to_current_cases", "qty_recorded_in", "packs_per_case",
                      "description"]].rename(columns={"description": "sales_name"}), on="key", how="left")
    pids = set(products["product_id"])
    direct = d["product_id"].isna() & d["raw_item"].isin(pids)
    d.loc[direct, "product_id"] = d.loc[direct, "raw_item"]
    owner = d["product_id"].isna() & d["key"].isin(set(code_map or {}))
    d.loc[owner, "product_id"] = d.loc[owner, "key"].map(code_map or {})
    d["match"] = np.where(d["product_id"].isin(pids),
                          np.where(direct, "current code", np.where(owner, "owner answer", "via item_map")), "unmatched")
    d.loc[d["key"].isin(set(ignore or ())) & (d["match"] == "unmatched"), "match"] = "ignored (owner: packaging)"
    # packs per case of the code itself (from the sales name if known, else from the inventory description)
    parsed = P.parse_all(d["description"])["packs_per_case"]
    d["packs"] = pd.to_numeric(d["packs_per_case"], errors="coerce").fillna(parsed)
    lb = d["qty_recorded_in"].astype(str).str.startswith("lb")
    pre = d["raw_item"].str.extract(r"^([A-Za-z]+)")[0].str.upper()
    in_cases_by_prefix = pre.map(prefix_unit or {}).eq("cases")
    # only the pounds fix carries over from sales; the inventory's own unit is decided below
    d["qty_to_cases"] = np.where(lb, pd.to_numeric(d["qty_to_cases"], errors="coerce").fillna(1.0), 1.0)
    d["to_current_cases"] = pd.to_numeric(d["to_current_cases"], errors="coerce").fillna(1.0)
    divide = (unit == "packs") & ~lb & ~in_cases_by_prefix
    d["unit_note"] = np.where(lb, "pounds -> bags (bulk code recorded in lb)",
                     np.where(in_cases_by_prefix, "taken as cases (this item-code prefix counts cases)",
                     np.where(~divide, "taken as cases",
                     np.where(d["packs"].notna(), "packs -> cases", "pack count unknown: taken as cases"))))
    factor = d["qty_to_cases"] * d["to_current_cases"] / np.where(divide & d["packs"].notna(), d["packs"], 1.0)
    for f in ("on_hand", "on_order", "held"):
        d[f"{f}_cases"] = d[f] * factor
    m = d[~d["match"].isin(["unmatched", "ignored (owner: packaging)"])]
    stock = m.groupby("product_id").agg(
        on_hand_cases=("on_hand_cases", lambda s: s.sum(min_count=1)),
        on_order_cases=("on_order_cases", lambda s: s.sum(min_count=1)),
        held_cases=("held_cases", lambda s: s.sum(min_count=1)),
        earliest_expiry=("expiry", "min"), cbm_per_case=("cbm", "max"),
        raw_codes=("raw_item", lambda s: " | ".join(sorted(s))),
        unit_note=("unit_note", lambda s: "; ".join(sorted(set(s))))).reset_index()
    stock["on_order_arrival"] = pd.NaT
    stock["earliest_expiry"] = pd.to_datetime(stock["earliest_expiry"]).dt.strftime("%Y-%m-%d")
    for c in ("on_hand_cases", "on_order_cases", "held_cases"):
        stock[c] = stock[c].round(2)
    return stock[STOCK_COLUMNS], d


def cover_check(stock, panel, months=6):
    """Months of cover = on hand / average monthly sales over the last `months` months."""
    last = panel["month"].max()
    avg = panel[panel["month"] > last - pd.DateOffset(months=months)].groupby("product_id")["units_sold"].mean()
    s = stock.set_index("product_id")
    c = pd.DataFrame({"on_hand_cases": s["on_hand_cases"], "avg_monthly_sales": avg.reindex(s.index)})
    c["months_of_cover"] = c["on_hand_cases"] / c["avg_monthly_sales"].where(c["avg_monthly_sales"] > 0)
    c["flag"] = np.where(c["on_hand_cases"] < 0, "negative stock",
                np.where(c["months_of_cover"] > 36, "over 36 months of cover: unit mismatch?",
                np.where((c["on_hand_cases"] > 0) & (c["avg_monthly_sales"].fillna(0) == 0), "stock but no sales in 6 months", "")))
    return c.reset_index()


def run(path="data/raw/Inventory/CurrentInventory.xlsx", data="data", out="outputs", unit=INVENTORY_UNIT, log=print,
        answers=None):
    """answers: inventory.answers.Answers (default: read <data>/owner_answers.csv): inventory codes the owner
    identified or called packaging, and stock counts the owner accepted (no longer flagged)."""
    from . import answers as A
    data, out = Path(data), Path(out)
    ans = A.ensure(answers, data)
    inv, found = read_inventory(path)
    panel, products, _ = load(data)
    item_map = pd.read_csv(data / "item_map.csv")
    code_map, ignore = A.inventory_codes(ans, products["product_id"])
    _, first = map_to_products(inv, item_map, products, unit, code_map=code_map, ignore=ignore)
    pu, evidence = prefix_units(first, panel) if unit == "packs" else ({}, pd.DataFrame())
    evidence.to_csv(out / "inventory_unit_evidence.csv", index=False)
    stock, detail = map_to_products(inv, item_map, products, unit, pu, code_map=code_map, ignore=ignore)
    stock.to_csv(data / "stock.csv", index=False)
    log("item-code prefixes counted in cases (evidence in outputs/inventory_unit_evidence.csv): "
        + (", ".join(sorted(k for k, v in pu.items() if v == "cases")) or "none"))
    cov = A.cover_flags(cover_check(stock, panel), ans)
    cov.to_csv(out / "stock_cover_check.csv", index=False)
    un = detail[detail["match"] == "unmatched"]
    un[["raw_item", "description", "section", "on_hand"]].to_csv(out / "inventory_unmatched_codes.csv", index=False)
    log(f"inventory columns found: {found}; missing: "
        + ", ".join(f for f in ("on_order", "held", "expiry", "cbm") if f not in found))
    log(f"{len(inv)} item codes ({int((inv['rows'] > 1).sum())} appeared more than once and were summed); sections: "
        + ", ".join(f"{k} {v}" for k, v in inv["section"].value_counts().items()))
    log("matched: " + ", ".join(f"{k} {v}" for k, v in detail["match"].value_counts().items())
        + f"; unmatched codes with stock: {int((un['on_hand'].fillna(0) != 0).sum())} -> {out}/inventory_unmatched_codes.csv")
    log("units: " + ", ".join(f"{k} {v}" for k, v in detail.loc[detail['match'] != 'unmatched', 'unit_note'].value_counts().items()))
    log(f"{len(stock)} products with a stock row; months of cover (on hand / last-6-month sales): median "
        f"{cov['months_of_cover'].median():.1f}; flagged: " + ", ".join(f"{k} {v}" for k, v in cov.loc[cov['flag'] != '', 'flag'].value_counts().items()))
    return dict(stock=stock, detail=detail, cover=cov, columns_found=found)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/raw/Inventory/CurrentInventory.xlsx")
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--unit", choices=["packs", "cases"], default=INVENTORY_UNIT)
    a = ap.parse_args()
    run(a.file, a.data, a.out, a.unit)
