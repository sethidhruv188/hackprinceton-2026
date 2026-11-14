"""Turn the raw NetSuite exports into the data contract (data/sales.csv, data/products.csv).

Input: every .xlsx in data/raw/sales/ ("Custom Sales Orders by Item Summary", Show Zeros):
    6 header rows, then Date | Item | Description | Qty. Sold, one row per invoice line,
    and a final "Total" row. Quantities are in the item's selling unit (usually cases),
    fractions are partial cases, negatives are returns/corrections.

What this does, in order:
  1. Read and stack all files; drop the header, the Total row and non-product lines
     (pallet charges, unloading, discounts, miscellaneous).
  2. Merge re-coded items. The ERP renamed hundreds of codes mid-stream
     ("XX123 - OLD" for a while, then "XX123"). Left alone, that looks like one product
     dying and a new one launching. Codes that differ only by an OLD/TBD suffix are merged
     when the pack numbers in their descriptions agree (e.g. {30 oz, 850 g, 6 tins});
     if the pack differs ("MDH Kitchen King 100 X 60" vs "100 X 1") they stay separate.
  3. Sum to product x month (returns net out; a month never goes below zero).
  4. Months before a product's first sale are written as status "not_listed" so they are
     treated as unknown, not as zero demand.
  5. Guess category, shelf life and lead time from the description. These are defaults:
     put real values in data/raw/product_overrides.csv (product_id + any products.csv
     column) and they win. unit_cost is unknown until that file provides it.

Outputs (in data/): sales.csv, products.csv, item_map.csv (raw code -> product_id, for audit).
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import packs

# Lines that are fees, not products. Matched against item code and description.
NON_PRODUCT = re.compile(r"pallet|unloading|strip the container|discount|miscellaneous|freight|shipping charge",
                         re.IGNORECASE)
OLD_MARK = re.compile(r"^\s*OLD\b[\s-]*|[\s-]*\bOLD\s*$", re.IGNORECASE)   # "XX123 - OLD", "XX123 -OLD", "OLD-XX123"
TBD_MARK = re.compile(r"[\s-]*\bTBD\s*$", re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:\.\d+)?")

# Bulk bags (one bag of >= 20 lb per "case"): many codes record Qty Sold in POUNDS, not bags
# (e.g. a "50 Lb" bag code with lines of 500 = 10 bags). A code is
# treated as pounds when at least LB_SHARE of its lines are whole multiples of the bag weight,
# or its median line is at least one bag weight (a typical order of 50+ bags / 2,200+ totes),
# or its median line read as bags would weigh more than a full container (LB_MAX_LINE_KG).
# Set LB_FIX = False to take every quantity as cases.
LB_FIX = True
LB_MIN_BAG = 20
LB_SHARE = 0.8
LB_MAX_LINE_KG = 20_000
# Codes recorded in single packs: pack count >= PACKS_MIN, at least PACKS_SHARE of lines whole
# multiples of the pack count, and a median line of at least one full case of packs.
PACKS_FIX = True
PACKS_MIN = 24
PACKS_SHARE = 0.5

# (category, shelf_life_days, lead_time_days, keywords). First match wins, so specific
# categories come before broad ones. Shelf lives are rough defaults for imported Indian
# groceries; the per-category lead times are superseded by LEAD_TIME_* below. Replace either
# with real numbers via product_overrides.csv.
# Whole words only (an optional plural "s"/"es" is allowed), so "lassi" never matches "Classic".
# Order matters, first match wins: masala brands first (their names often lack a spice word, e.g.
# "MDH Butter Chicken 100 GM"), then ready food and snacks (so "Maggi Masala Noodles" is not a
# spice), then spices, and only then frozen and dairy (so "Shahi Paneer Masala" is a spice).
CATEGORIES = [
    ("spices",          365, 60, ["mdh", "everest", "shan", "badshah", "catch", "ramdev", "priya masala"]),
    ("noodles_ready",   270, 60, ["noodle", "maggi", "ready to eat", "instant", "dhokla", "mix", "gits", "pasta",
                                  "vermicelli", "soup", "noddle", "choley", "chole", "sambar", "saag", "kofta", "pao bhaji",
                                  "pav bhaji", "dal makhani", "rajma masala curry", "ready meal"]),
    ("snacks",          180, 45, ["bhujia", "namkeen", "chip", "kurkure", "lays", "sev", "mixture", "bhungra", "biscuit",
                                  "rusk", "cookie", "parle", "cadbury", "chocolate", "khakhra", "mathri", "chakli", "murukku",
                                  "papad", "wafer", "quality street", "snack", "khari", "fryum", "murmura", "vathal", "sakariya",
                                  "sakaria", "khatta meetha", "boondi", "navrattan", "navratan", "all in one", "nut cracker",
                                  "bhelpuri", "bhel puri", "pani puri", "panipuri", "gol gappe", "pakoda", "digestive"]),
    ("spices",          365, 60, ["masala", "cumin", "jeera", "chilli", "chili", "chillies", "turmeric", "haldi", "coriander",
                                  "dhania", "pepper", "cinnamon", "clove", "cardamom", "elaichi", "fenugreek", "methi",
                                  "saffron", "mustard", "hing", "asafoetida", "paprika", "seed", "spice", "garam", "ajwain",
                                  "fennel", "saunf", "bay leaf", "bay leaves", "curry", "mint leaves", "pudina", "kasuri",
                                  "amchur", "anardana", "zing factor", "garlic", "ginger", "onion", "nutmeg", "mace", "star anise", "kalonji", "biryani",
                                  "tandoori"]),
    ("frozen",          270, 60, ["frozen", "paratha", "roti", "kabab", "kebab", "samosa", "spring roll", "paneer", "naan",
                                  "chapati", "tikki", "soya chaap", "momo", "kulcha", "bhatura", "puff", "manchurian"]),
    ("dairy",           120, 45, ["amul", "butter", "cheese", "buttermilk", "butter milk", "curd", "dahi", "lassi", "mawa",
                                  "khoya", "shrikhand"]),
    ("sweets",           90, 45, ["burfee", "burfi", "barfi", "gulab jamun", "rasmalai", "rasgulla", "laddu", "ladoo",
                                  "laddoo", "ladu", "chikki", "soan papdi", "halwa", "mithai", "peda", "kaju katli", "sweet", "milk cake"]),
    ("beverages",       365, 45, ["tea", "coffee", "juice", "drink", "cola", "sharbat", "horlicks", "boost", "bournvita",
                                  "rooh afza", "soda", "nescafe", "bru", "chai", "premix", "limca", "agni"]),
    ("pickles_sauces",  365, 60, ["pickle", "achar", "sauce", "chutney", "paste", "concentrate", "thokku", "pulp", "ketchup",
                                  "vinegar", "tamarind", "imli", "soy", "tamicon", "pupl", "mother's", "mothers"]),
    ("oils_ghee",       365, 60, ["oil", "ghee", "vanaspati"]),
    ("lentils_beans",   365, 60, ["dal", "chana", "moong", "toor", "tuvar", "masoor", "urad", "rajma", "bean", "chickpea",
                                  "chick pea", "lentil", "moth", "lobia", "vatana", "pea", "chori", "val", "horse gram", "gram"]),
    ("rice_flour",      365, 60, ["rice", "basmati", "masoori", "atta", "flour", "besan", "sooji", "suji", "rava", "poha",
                                  "mamra", "dalia", "millet", "wheat", "jowar", "bajra", "ragi", "corn meal", "semolina",
                                  "farina", "sabudana", "sago", "oats", "juwar", "corn", "rajagro", "sella", "zafrani"]),
    ("nuts_dry_fruits", 270, 60, ["peanut", "cashew", "almond", "makhana", "fox nut", "raisin", "kishmish", "date", "pista",
                                  "walnut", "coconut", "anjeer", "fig", "apricot", "prune", "bukhara"]),
    ("sugar_jaggery",   365, 60, ["sugar", "jaggery", "gur", "bura", "mishri", "salt", "palm crystal"]),
    ("personal_care",   730, 60, ["soap", "face wash", "facewash", "capsule", "shampoo", "hair", "cream", "toothpaste",
                                  "tooth", "lotion", "himalaya", "dabur", "ayurvedic", "tablet"]),
    ("puja_household", 1095, 60, ["agarbatti", "incense", "dhoop", "diya", "idol", "hem", "nandita", "camphor", "kapoor",
                                  "pooja", "puja", "cricket", "pouch", "wick", "cotton", "steel", "box"]),
]
DEFAULT = ("other", 365, 60)
# Lead time from the owner, same for every product: 130 days worst case (the safe order-by date)
# and 105 days typical. Overrides per product still win.
LEAD_TIME_WORST_DAYS = 130
LEAD_TIME_TYPICAL_DAYS = 105
_CAT_RX = [(c, s, l, re.compile(r"\b(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in words) + r")(?:s|es)?\b",
                                re.IGNORECASE)) for c, s, l, words in CATEGORIES]


def read_raw(raw_dir):
    """Stack every export into one table: date, raw_item, description, qty, file."""
    files = sorted(Path(raw_dir).glob("*.xlsx"))
    if not files:
        raise FileNotFoundError(f"no .xlsx files in {raw_dir}")
    frames = []
    for f in files:
        x = pd.read_excel(f, header=None, dtype=object)
        hdr = x.index[x[0].astype(str).str.strip().eq("Date")]
        if not len(hdr):
            raise ValueError(f"{f.name}: no 'Date' header row found")
        x = x.iloc[hdr[0] + 1:, :4]
        x.columns = ["date", "raw_item", "description", "qty"]
        x["file"] = f.name
        frames.append(x)
    d = pd.concat(frames, ignore_index=True)
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["qty"] = pd.to_numeric(d["qty"], errors="coerce")
    d = d[d["date"].notna() & d["raw_item"].notna()]  # drops the "Total" row and blank lines
    d["raw_item"] = d["raw_item"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    d["description"] = d["description"].fillna("").astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    return d.reset_index(drop=True)


def drop_non_products(d):
    is_fee = d["raw_item"].str.contains(NON_PRODUCT) | d["description"].str.contains(NON_PRODUCT)
    bad_qty = d["qty"].isna()
    return d[~is_fee & ~bad_qty].copy(), d[is_fee | bad_qty]


def pack_signature(desc):
    """The set of numbers in a description: pack size, weight, count per case."""
    return frozenset(float(n) for n in NUMBER.findall(desc))


def build_item_map(d):
    """Map each raw item code to a product_id and a factor that converts its cases into the
    product's current cases.

    Rule 1 (owner): a code carrying an OLD marker whose stripped code exists as a current code
    is the SAME product. Always merged, whatever the pack size; its history is converted by
    weight per case (old_cases * old_case_kg / new_case_kg, i.e. old_packs / new_packs when the
    pack weight is the same).
    Rule 2: other variants sharing a base code (TBD codes, OLD codes with no current code) are
    merged only when the pack numbers in their names agree, as before.
    """
    codes = d.sort_values("date").groupby("raw_item").agg(
        first_sale=("date", "min"), last_sale=("date", "max"), lines=("qty", "size"),
        cases_sold=("qty", "sum"), description=("description", "last"),  # most recent wording
    ).reset_index()
    codes["has_old"] = codes["raw_item"].str.contains(OLD_MARK)
    codes["base"] = (codes["raw_item"].str.replace(OLD_MARK, "", regex=True)
                     .str.replace(TBD_MARK, "", regex=True).str.strip(" -"))
    codes["is_plain"] = ~codes["has_old"] & ~codes["raw_item"].str.contains(TBD_MARK)
    codes["sig"] = codes["description"].map(pack_signature)

    plain = codes[codes["is_plain"]].assign(key=lambda c: c["raw_item"].str.upper()).set_index("key")["raw_item"]
    mapping, rule = {}, {}
    for r in codes[codes["has_old"]].itertuples():
        target = plain.get(r.base.upper())
        if target is not None:
            mapping[r.raw_item], rule[r.raw_item] = target, "OLD -> current code"

    for _, g in codes[~codes["raw_item"].isin(mapping)].groupby("base"):
        g = g.sort_values(["is_plain", "last_sale"], ascending=[False, False])  # plain code, then most recent
        clusters = []  # [(product_id, signature)]
        for r in g.itertuples():
            for pid, sig in clusters:
                if r.sig <= sig or sig <= r.sig:
                    mapping[r.raw_item], rule[r.raw_item] = pid, "same pack numbers"
                    break
            else:
                clusters.append((r.raw_item, r.sig))
                mapping[r.raw_item] = r.raw_item
    codes["product_id"] = codes["raw_item"].map(mapping)
    codes["merged"] = codes["product_id"] != codes["raw_item"]
    codes["merge_rule"] = codes["raw_item"].map(rule).fillna("")

    parsed = packs.parse_all(codes["description"])
    codes = pd.concat([codes, parsed], axis=1)

    # Unit the quantity was recorded in: cases, or pounds for some bulk-bag codes.
    bag = codes.set_index("raw_item")
    bag = bag[(bag["pack_unit"] == "lb") & (bag["packs_per_case"] == 1) & (bag["pack_weight"] >= LB_MIN_BAG)]["pack_weight"]
    lines = d[d["raw_item"].isin(bag.index) & (d["qty"] > 0)]
    w = lines["raw_item"].map(bag)
    whole = ((lines["qty"] / w).round(6) % 1 == 0) & (lines["qty"] >= w)
    share = whole.groupby(lines["raw_item"]).mean()
    median_bags = (lines["qty"] / w).groupby(lines["raw_item"]).median()  # typical line, if it were bags
    median_qty = lines["qty"].groupby(lines["raw_item"]).median()
    median_kg = median_qty * share.index.map(bag).to_numpy() * packs.KG_PER["lb"]   # typical line weight, if it were bags
    in_lb = share[(share >= LB_SHARE) | (median_bags >= 1) | (median_kg > LB_MAX_LINE_KG)].index if LB_FIX else []
    codes["qty_recorded_in"] = np.where(codes["raw_item"].isin(in_lb), "lb (detected)", "cases")
    codes["lb_multiple_share"] = codes["raw_item"].map(share).round(2)
    codes["qty_to_cases"] = np.where(codes["raw_item"].isin(in_lb), 1 / codes["pack_weight"], 1.0)

    # Codes sold by the single pack although the name describes a case (e.g. a code named
    # "100 X 60" that sells lines of 60, 120, 180 units while its sibling codes sell a few cases).
    # Big packs only, so normal orders of 10 or 20 cases are never mistaken for packs.
    pk = codes.set_index("raw_item")["packs_per_case"]
    pl = d[d["raw_item"].isin(pk[pk >= PACKS_MIN].index) & (d["qty"] > 0)]
    pw = pl["raw_item"].map(pk)
    pstats = pd.DataFrame({"whole": ((pl["qty"] / pw).round(6) % 1 == 0), "over": pl["qty"] / pw}).groupby(pl["raw_item"]).agg(
        lines=("whole", "size"), share=("whole", "mean"), median_cases_if_packs=("over", "median"))
    in_packs = pstats[(pstats["lines"] >= 5) & (pstats["share"] >= PACKS_SHARE) & (pstats["median_cases_if_packs"] >= 1)].index if PACKS_FIX else []
    in_packs = [c for c in in_packs if c not in set(in_lb)]
    codes.loc[codes["raw_item"].isin(in_packs), "qty_recorded_in"] = "packs (detected)"
    codes["qty_to_cases"] = np.where(codes["raw_item"].isin(in_packs), 1 / codes["packs_per_case"], codes["qty_to_cases"])
    tgt = codes.set_index("raw_item").loc[codes["product_id"], ["case_weight_kg", "parse_note"]]
    tgt_kg, tgt_note = tgt["case_weight_kg"].to_numpy(), tgt["parse_note"].to_numpy()
    factor = codes["case_weight_kg"].to_numpy() / tgt_kg
    ok = np.isfinite(factor) & (factor > 0)
    codes["to_current_cases"] = np.where(codes["merged"] & ok, factor, 1.0)
    note = np.where(~codes["merged"], "",
           np.where(~ok, "FLAG: merged without conversion (name not parsed)",
           np.where(np.isclose(codes["to_current_cases"], 1.0), "same case size",
                    "converted by weight per case")))
    assumed = codes["merged"] & ok & ((codes["parse_note"] != "parsed") | (tgt_note != "parsed"))
    codes["conversion_note"] = np.where(assumed, pd.Series(note) + " (relies on an assumed unit/pack count)", note)
    return codes.drop(columns=["sig", "is_plain"])


def categorize(desc):
    for cat, shelf, lead, rx in _CAT_RX:
        if rx.search(desc):
            return cat, shelf, lead
    return DEFAULT


def build_products(d, item_map, overrides_path=None):
    # Name and pack size come from the current code itself, so "cases" mean current cases.
    own = item_map[item_map["raw_item"] == item_map["product_id"]].set_index("raw_item")
    p = pd.DataFrame({"product_id": sorted(d["product_id"].unique())})
    p["name"] = p["product_id"].map(own["description"])
    p[["category", "shelf_life_days", "lead_time_days"]] = pd.DataFrame(
        p["name"].map(categorize).tolist(), index=p.index)
    p["shelf_life_days"] = np.nan   # unknown: never guessed, so nothing is capped by shelf life
    p["lead_time_days"] = LEAD_TIME_WORST_DAYS          # worst case: drives order-by dates and cover
    p["lead_time_typical_days"] = LEAD_TIME_TYPICAL_DAYS
    p["unit_cost"] = np.nan
    p["primary_source"] = ""
    p["backup_source"] = ""
    codes = item_map.groupby("product_id")["raw_item"].apply(lambda s: " | ".join(sorted(s)))
    p["raw_codes"] = p["product_id"].map(codes)
    for col in ("pack_weight", "pack_unit", "packs_per_case", "case_weight_kg", "parse_note"):
        p[col] = p["product_id"].map(own[col])
    if overrides_path and Path(overrides_path).exists():
        o = pd.read_csv(overrides_path, dtype={"product_id": str}).set_index("product_id")
        p = p.set_index("product_id")
        for col in o.columns:
            vals = o[col].dropna()
            p.loc[p.index.intersection(vals.index), col] = vals
        p = p.reset_index()
        print(f"applied overrides for {len(o)} products from {overrides_path}")
    return p


def build_sales(d):
    m = d.assign(month=d["date"].dt.to_period("M").dt.to_timestamp())
    s = m.groupby(["product_id", "month"], as_index=False)["qty_current"].sum().rename(columns={"qty_current": "qty"})
    returns_heavy = s[s["qty"] < 0]
    s["units_sold"] = s["qty"].clip(lower=0).round(3)
    s["status"] = "sellable"

    # Months before a product's first sale: unknown, not zero demand.
    start = s["month"].min()
    first = s[s["units_sold"] > 0].groupby("product_id")["month"].min()
    pre = [dict(product_id=pid, month=mm, units_sold=0.0, status="not_listed")
           for pid, f in first.items() for mm in pd.date_range(start, f, freq="MS", inclusive="left")]
    s = pd.concat([s[["product_id", "month", "units_sold", "status"]], pd.DataFrame(pre)], ignore_index=True)
    s["month"] = s["month"].dt.strftime("%Y-%m")
    return s.sort_values(["product_id", "month"]).reset_index(drop=True), returns_heavy


def order_lines(d, item_map):
    """Invoice lines in current-code cases: date, product_id, raw_item, cases (returns kept, negative).
    Used to separate large (bulk) orders from regular demand."""
    im = item_map.set_index("raw_item")
    out = d[["date", "raw_item"]].copy()
    out["product_id"] = d["raw_item"].map(im["product_id"])
    out["cases"] = (d["qty"] * d["raw_item"].map(im["qty_to_cases"]) * d["raw_item"].map(im["to_current_cases"])).round(4)
    return out.dropna(subset=["product_id"])


def monthly_volume(d):
    """Total volume per month: raw cases, current-code cases and kg (where the name parses)."""
    m = d.assign(month=d["date"].dt.to_period("M"))
    v = m.groupby("month").agg(qty_as_reported=("qty", "sum"), cases=("cases", "sum"),
                               current_cases=("qty_current", "sum"),
                               kg=("kg", "sum"), cases_with_weight=("has_kg", "sum"))
    v["weight_coverage_%"] = 100 * v["cases_with_weight"] / v["cases"]
    return v.drop(columns="cases_with_weight").round(1)


def run(raw_dir="data/raw/sales", out_dir="data", overrides="data/raw/product_overrides.csv"):
    out = Path(out_dir)
    out.mkdir(exist_ok=True)
    raw = read_raw(raw_dir)
    d, dropped = drop_non_products(raw)
    item_map = build_item_map(d)
    im = item_map.set_index("raw_item")
    d["product_id"] = d["raw_item"].map(im["product_id"])
    d["cases"] = d["qty"] * d["raw_item"].map(im["qty_to_cases"])          # own cases (lb-recorded bulk -> bags)
    d["qty_current"] = d["cases"] * d["raw_item"].map(im["to_current_cases"])
    d["kg"] = (d["cases"] * d["raw_item"].map(im["case_weight_kg"])).fillna(0.0)
    d["has_kg"] = np.where(d["raw_item"].map(im["case_weight_kg"]).notna(), d["cases"], 0.0)
    sales, neg = build_sales(d)

    # Products that never had a positive month carry no signal; leave them out.
    sold = sales.loc[sales["units_sold"] > 0, "product_id"].unique()
    sales = sales[sales["product_id"].isin(sold)]
    products = build_products(d[d["product_id"].isin(sold)], item_map, overrides)
    volume = monthly_volume(d)

    sales.to_csv(out / "sales.csv", index=False)
    products.to_csv(out / "products.csv", index=False)
    item_map.to_csv(out / "item_map.csv", index=False)
    volume.to_csv(out / "monthly_volume.csv")
    order_lines(d, item_map).to_csv(out / "order_lines.csv", index=False)

    months = pd.to_datetime(sales["month"])
    merged = item_map[item_map["merged"]]
    print(f"read {len(raw):,} invoice lines from {raw['file'].nunique()} files, "
          f"{raw['date'].min():%Y-%m-%d} to {raw['date'].max():%Y-%m-%d}")
    print(f"dropped {len(dropped)} non-product / blank-qty lines "
          f"({', '.join(dropped['raw_item'].value_counts().index[:5])})")
    print(f"{item_map['raw_item'].nunique():,} raw codes -> {len(products):,} products; "
          f"{len(merged)} codes merged ({', '.join(f'{k}: {v}' for k, v in merged['merge_rule'].value_counts().items())})")
    print("merge conversions: " + ", ".join(f"{k}: {v}" for k, v in merged["conversion_note"].value_counts().items()))
    unmatched_old = item_map[item_map["has_old"] & (item_map["merge_rule"] != "OLD -> current code")]
    lb = item_map[item_map["qty_recorded_in"] != "cases"]
    print(f"{len(lb)} bulk codes record Qty Sold in pounds (detected; divided by bag weight; "
          f"{int(lb['has_old'].sum())} OLD, {int((~lb['has_old']).sum())} current)" if LB_FIX else "LB_FIX off: all qty taken as cases")
    pk_codes = item_map[item_map["qty_recorded_in"] == "packs (detected)"]["raw_item"].tolist()
    print(f"{len(pk_codes)} codes record Qty Sold in single packs (detected; divided by packs per case): {', '.join(pk_codes) or 'none'}")
    print(f"{len(unmatched_old)} OLD codes have no current code to merge into (kept as their own product)")
    pc = products["parse_note"].value_counts()
    print(f"pack size parse coverage ({len(products):,} products): "
          + ", ".join(f"{k} {v} ({100 * v / len(products):.0f}%)" for k, v in pc.items()))
    if len(neg):
        print(f"{len(neg)} product-months had net returns > sales (set to 0): "
              + ", ".join(f"{r.product_id} {r.month:%Y-%m} ({r.qty:g})" for r in neg.itertuples()))
    print(f"{months.min():%b %Y} to {months.max():%b %Y}; categories: "
          + ", ".join(f"{k} {v}" for k, v in products["category"].value_counts().items()))
    if products["unit_cost"].isna().any():
        print(f"unit_cost unknown for {int(products['unit_cost'].isna().sum())} products: "
              f"add it to {overrides} to get $ figures")
    print("\nmonthly volume (cases in current-code cases; kg where the name parses):")
    print(volume.to_string())
    return out


def recategorize(data_dir="data", overrides="data/raw/product_overrides.csv"):
    """Re-derive `category` in products.csv from names (keeps everything else; overrides still win).
    Returns (n_changed, products)."""
    path = Path(data_dir) / "products.csv"
    p = pd.read_csv(path, dtype={"product_id": str})
    old = p["category"].copy()
    p["category"] = [categorize(str(n))[0] for n in p["name"]]
    if overrides and Path(overrides).exists():
        o = pd.read_csv(overrides, dtype={"product_id": str})
        if "category" in o:
            o = o.dropna(subset=["category"]).set_index("product_id")["category"]
            p.loc[p["product_id"].isin(o.index), "category"] = p["product_id"].map(o)
    p.to_csv(path, index=False)
    return int((old != p["category"]).sum()), p
