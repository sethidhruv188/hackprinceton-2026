"""Parse pack size out of item names: "<weight> X <packs per case>".

    "MDH Kitchen King 100 X 60"                          -> 100 g (unit assumed), 60 per case
    "Aara Kesar Mango Pulp - 30 Oz (850 Gm) X 6 Tins"    -> 30 oz, 6 per case
    "TATA Sampann - Toor Dal 1 Case (20 x 2 lb)"         -> 2 lb, 20 per case   (count first)
    "Rubicon Mango Juice Drink 12 X 1L X"                -> 1 l, 12 per case    (count first)
    "Cumin Seeds Fancy 50 Lb X"                          -> 50 lb, 1 per case   (count assumed)
    "Everest Pav Bhaji 100 gm X 12 Trays (10 Pcs per trey)" -> 100 g, 120 per case

Liquids are converted to kg at 1 kg per litre (water density); good enough for volume trends.
"""
import re

import numpy as np
import pandas as pd

MAX_CASE_KG = 1100  # a 2,200 lb tote is the heaviest real case
KG_PER = {"g": 0.001, "kg": 1.0, "lb": 0.45359237, "oz": 0.028349523, "ml": 0.001, "l": 1.0}

_UNIT = (r"(?P<u>kgs?|kilos?|kilograms?|grams?|grms?|gms?|gr|g|lbs?|pounds?|ounces?|oz|"
         r"ml|ltrs?|litres?|liters?|lits?|lts?|l)(?![a-wyz])\.?")
_NUM = r"(?P<w>\d+(?:\.\d+)?|\.\d+)"
_X = r"\s*[x×\*]\s*"
# weight+unit [ (anything) ] X count        e.g. "30 Oz (850 Gm) X 6"
_A = re.compile(_NUM + r"\s*" + _UNIT + r"\s*(?:\([^)]*\)\s*)?(?:FOC\s*)?[-,]?" + _X + r"(?P<n>\d+)(?:\s*(?P<doz>dozen|dz))?",
                re.IGNORECASE)
# count X weight+unit                       e.g. "20 x 2 lb", "12 X 1L"
_B = re.compile(r"(?<![\d.])(?P<n>\d+)\s*(?:\([^)]*\))?" + _X + _NUM + r"\s*" + _UNIT, re.IGNORECASE)
# weight (no unit) X count                  e.g. "100 X 60"
_C = re.compile(r"(?<![\d.])" + _NUM + _X + r"(?P<n>\d+)(?:\s*(?P<doz>dozen|dz))?", re.IGNORECASE)
# weight+unit only, count missing           e.g. "50 Lb X", "10 kg (Bucket)"
_D = re.compile(_NUM + r"\s*" + _UNIT, re.IGNORECASE)
_PER_TRAY = re.compile(r"\(\s*(\d+)\s*pcs?\s*per\s*tr[ae]y", re.IGNORECASE)


def _unit(u):
    u = u.lower().rstrip(".")
    if u.startswith(("kg", "kilo")):
        return "kg"
    if u.startswith(("lb", "pound")):
        return "lb"
    if u.startswith(("oz", "ounce")):
        return "oz"
    if u == "ml":
        return "ml"
    if u.startswith(("l", "lit", "ltr", "lt")):
        return "l"
    return "g"


def parse(name):
    """Return dict(pack_weight, pack_unit, packs_per_case, case_weight_kg, parse_note)."""
    s = str(name)
    out = dict(pack_weight=np.nan, pack_unit=None, packs_per_case=np.nan, case_weight_kg=np.nan, parse_note="unparsed")
    for rx, note in ((_A, "parsed"), (_B, "parsed"), (_C, "unit assumed g"), (_D, "packs assumed 1")):
        m = rx.search(s)
        if not m:
            continue
        w = float(m.group("w"))
        unit = _unit(m.group("u")) if "u" in m.groupdict() and m.group("u") else "g"
        n = float(m.group("n")) if "n" in m.groupdict() and m.group("n") else 1.0
        if m.groupdict().get("doz"):
            n *= 12
        tray = _PER_TRAY.search(s)
        if tray:
            n *= float(tray.group(1))
        if w <= 0 or n <= 0:
            continue
        case_kg = w * KG_PER[unit] * n
        out.update(pack_weight=w, pack_unit=unit, packs_per_case=n, case_weight_kg=case_kg, parse_note=note)
        if case_kg > MAX_CASE_KG or (unit in ("kg", "l") and w > 50 and n > 1):
            out.update(case_weight_kg=np.nan, parse_note=f"implausible ({note}): check name")
        break
    return out


def parse_all(names):
    return pd.DataFrame([parse(n) for n in names], index=getattr(names, "index", None))
