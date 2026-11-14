"""Brand of each product, from its name.

Order of rules (first that applies wins; `how_assigned` records which):
  override        data/raw/brand_overrides.csv (product_id, brand)
  known brand     the first two words, then the first word, of the normalized name match ALIASES
  first word      the first word starts at least MIN_FIRST_WORD product names and is not a
                  commodity word ("Cloves", "Cumin", "Black"...): used as the brand as written
  item-code prefix  nothing in the name: if at least PREFIX_SHARE of the named products sharing the
                  item-code letters have one brand, that brand (MM codes -> MDH); otherwise the
                  letters themselves, e.g. "BS (item code)" (mostly bulk commodity bags)

Normalizing: lower case, "'s" dropped, punctuation turned into spaces, so "MDH.", "MDH" and
"MDH.Butter Chicken" all start with "mdh", and "Haldiram's" is "haldiram".
ALIASES was built from the most common first one/two words in the real export (product counts
and volume); add to it, or use the override file, when a new brand shows up.
"""
import re
from pathlib import Path

import pandas as pd

ALIASES = {
    # two words first
    "india gate": "India Gate", "organic india": "Organic India", "tata sampann": "Tata Sampann",
    "coco india": "Coco India", "mysore sandal": "Mysore Sandal", "quality street": "Quality Street",
    "red label": "Red Label", "mc vitie": "McVitie's", "smith jones": "Smith & Jones", "wagh bakri": "Wagh Bakri",
    "tata tea": "Tata", "golden temple": "Golden Temple",
    # one word
    "aara": "Aara", "haldiram": "Haldiram", "priya": "Priya", "mother": "Mother's Recipe", "chings": "Ching's",
    "ching": "Ching's", "gits": "Gits", "grb": "GRB", "himalaya": "Himalaya", "amul": "Amul", "parachute": "Parachute",
    "mdh": "MDH", "ck": "CK", "tata": "Tata", "everest": "Everest", "girnar": "Girnar", "hem": "HEM",
    "rubicon": "Rubicon", "parle": "Parle", "asli": "Asli", "badshah": "Badshah", "tetley": "Tetley",
    "cadbury": "Cadbury", "lays": "Lay's", "lay": "Lay's", "nandita": "Nandita", "saffola": "Saffola", "surti": "Surti",
    "tamicon": "Tamicon", "maggi": "Maggi", "zafrani": "Zafrani", "shan": "Shan", "aksar": "Aksar",
    "gramiyaa": "Gramiyaa", "horlicks": "Horlicks", "narasu": "Narasu's", "nasaru": "Narasu's", "vicco": "Vicco",
    "iyengars": "Iyengar's", "funflips": "Funflips", "smith": "Smith & Jones", "deep": "Deep", "swad": "Swad",
    "laxmi": "Laxmi", "britannia": "Britannia", "kurkure": "Kurkure", "bru": "Bru", "nescafe": "Nescafe",
    "boost": "Boost", "dabur": "Dabur", "mtr": "MTR", "catch": "Catch", "ramdev": "Ramdev", "bikaji": "Bikaji",
    "uncle": "Uncle Chips", "limca": "Limca", "thums": "Thums Up", "maaza": "Maaza", "frooti": "Frooti",
    "kissan": "Kissan", "lijjat": "Lijjat", "patanjali": "Patanjali", "vadilal": "Vadilal", "simba": "Simba",
}
# Words that describe the goods, not a maker: these never become a brand by the first-word rule.
GENERIC = {
    "black", "red", "green", "white", "yellow", "brown", "golden", "whole", "organic", "millet", "printed", "plastic",
    "eco", "diya", "south", "north", "ss", "sw", "cricket", "spanish", "raisins", "kabuli", "quinoa", "sesame",
    "dried", "fresh", "premium", "pure", "special", "new", "old", "the", "sri", "shri", "steel", "paper", "big",
    "small", "large", "dry", "crushed", "roasted", "salted", "sweet", "hot", "mild", "extra", "fancy", "bold",
    "chicken", "citric", "set", "gathering",
}
PREFIX_SHARE = 0.7   # an item-code prefix stands for a brand when this share of its named products have it
MIN_FIRST_WORD = 3


def normalize(name):
    s = str(name).lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _commodity_words():
    from .ingest import CATEGORIES
    words = set()
    for _, _, _, kws in CATEGORIES:
        for k in kws:
            words.add(k.split()[0])
            words.add(k.split()[0] + "s")
    return words - set(ALIASES)


def _prefix(product_id):
    m = re.match(r"[A-Za-z]+", str(product_id))
    return f"{m.group(0).upper()} (item code)" if m else "Unbranded"


def brand_of(name, product_id="", first_word_counts=None, commodity=None):
    """(brand, how_assigned) for one name."""
    words = normalize(name).split()
    if len(words) >= 2 and " ".join(words[:2]) in ALIASES:
        return ALIASES[" ".join(words[:2])], "known brand"
    if words and words[0] in ALIASES:
        return ALIASES[words[0]], "known brand"
    commodity = _commodity_words() if commodity is None else commodity
    if (words and first_word_counts is not None and first_word_counts.get(words[0], 0) >= MIN_FIRST_WORD
            and words[0] not in GENERIC and words[0] not in commodity and not words[0].isdigit() and len(words[0]) > 1):
        w = words[0]
        return (w.upper() if len(w) <= 3 else w.title()), "first word"
    return _prefix(product_id), "item-code prefix"


def assign(products, overrides="data/raw/brand_overrides.csv"):
    """products: product_id, name. Returns product_id, brand, how_assigned."""
    norm = products["name"].map(normalize)
    counts = norm.str.split().str[0].value_counts().to_dict()
    commodity = _commodity_words()
    rows = [(pid, *brand_of(n, pid, counts, commodity)) for pid, n in zip(products["product_id"], products["name"])]
    out = pd.DataFrame(rows, columns=["product_id", "brand", "how_assigned"])
    # An item-code prefix whose named products are mostly one brand stands for that brand
    # (e.g. MM codes are MDH even when the name starts "Chunky Chat Masala").
    out["prefix"] = out["product_id"].map(_prefix)
    named = out[out["how_assigned"] != "item-code prefix"]
    share = named.groupby("prefix")["brand"].agg(lambda b: (b.value_counts().index[0], b.value_counts().iloc[0] / len(b)))
    learned = {k: v[0] for k, v in share.items() if v[1] >= PREFIX_SHARE}
    fb = (out["how_assigned"] == "item-code prefix") & out["prefix"].isin(learned)
    out.loc[fb, "brand"] = out.loc[fb, "prefix"].map(learned)
    out.loc[fb, "how_assigned"] = "item-code prefix (brand of most items with that code)"
    out = out.drop(columns="prefix")
    if overrides and Path(overrides).exists():
        o = pd.read_csv(overrides, dtype={"product_id": str}).dropna(subset=["brand"]).set_index("product_id")["brand"]
        hit = out["product_id"].isin(o.index)
        out.loc[hit, "brand"] = out.loc[hit, "product_id"].map(o)
        out.loc[hit, "how_assigned"] = "override"
    return out


def brand_info(path="data/raw/brands.csv"):
    """Optional brand, supplier, container_kg, notes. Empty frame when the file is absent."""
    cols = ["brand", "supplier", "container_kg", "notes"]
    if path and Path(path).exists():
        b = pd.read_csv(path)
        return b.reindex(columns=cols)
    return pd.DataFrame(columns=cols)


def summary(brand_map, panel, products, active_ids, months=12):
    """outputs/brands.csv: brand, products, active_products, monthly_cases, monthly_kg, how_assigned.
    monthly_cases / monthly_kg = average actual sales per month over the last `months` months."""
    last = panel["month"].max()
    recent = panel[panel["month"] > last - pd.DateOffset(months=months)]
    per = recent.groupby("product_id")["units_sold"].sum() / months
    kgw = products.set_index("product_id")["case_weight_kg"] if "case_weight_kg" in products else pd.Series(dtype=float)
    b = brand_map.copy()
    b["cases"] = b["product_id"].map(per).fillna(0.0)
    b["kg"] = b["cases"] * b["product_id"].map(kgw)
    b["active"] = b["product_id"].isin(set(active_ids))
    out = b.groupby("brand").agg(products=("product_id", "size"), active_products=("active", "sum"),
                                 monthly_cases=("cases", "sum"), monthly_kg=("kg", lambda s: s.sum(min_count=1)),
                                 how_assigned=("how_assigned", lambda s: s.value_counts().index[0]))
    return out.sort_values("monthly_cases", ascending=False).round(1).reset_index()
