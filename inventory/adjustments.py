"""Owner-taught demand adjustments: a planning layer on top of the forecast.

The owner says something like "expect 30% more moong in December, we're running a promo". The agent turns it into a
structured adjustment, reads it back, and it is saved as `proposed`. Only after the owner confirms does it become
`confirmed` and count in the plan. The forecast model is never changed: apply() scales or adds to the forecast table
(outputs/forecast.csv) or the gross order plan just before order quantities are computed (brand_orders.run).

File: <data>/owner_adjustments.csv (one row per adjustment; status changes rewrite the row in place)
  adj_id, created_at, channel, scope (product | brand | commodity), target, months (YYYY-MM;YYYY-MM),
  effect_kind (pct | cases), effect_value, reason, status (proposed | confirmed | cancelled), confirmed_at,
  cancelled_at
An adjustment expires on its own once its last month is before the plan month; it then stops counting.
Effects: pct multiplies the likely and high forecast by (1 + value/100) in each month; cases adds that many cases per
month, split across the scope's products in proportion to their forecast (all to one product for a product scope).
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

FILE = "owner_adjustments.csv"
COLUMNS = ["adj_id", "created_at", "channel", "scope", "target", "months", "effect_kind", "effect_value", "reason",
           "status", "confirmed_at", "cancelled_at"]
SCOPES = ("product", "brand", "commodity")
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MAX_PCT, MAX_MONTHS = 500, 12        # sanity limits: at most +500% / -100%, at most 12 months per adjustment


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(data):
    p = Path(data) / FILE
    if not p.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(p, dtype=str, keep_default_na=False).reindex(columns=COLUMNS).fillna("")


def _save(data, df):
    df.reindex(columns=COLUMNS).to_csv(Path(data) / FILE, index=False)


# ------------------------------------------------------------------ parsing
def parse_effect(text):
    """'+30%', '30 %', '-20%', '+50 cases', '50' -> ('pct' | 'cases', float). Raises ValueError."""
    t = str(text).strip().lower().replace(" ", "")
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(%|percent|pct|cases?|cs)?", t)
    if not m:
        raise ValueError(f"effect '{text}' is not a percentage (+30%) or a number of cases (+50 cases)")
    v, unit = float(m.group(1)), m.group(2) or "cases"
    kind = "pct" if unit in ("%", "percent", "pct") else "cases"
    if kind == "pct" and not -100 <= v <= MAX_PCT:
        raise ValueError(f"a percentage must be between -100% and +{MAX_PCT}%")
    if v == 0:
        raise ValueError("an effect of zero changes nothing")
    return kind, v


def _month_of(token, today):
    """'2026-12' or 'dec' / 'december' (next occurrence on or after the plan month) -> Period."""
    t = token.strip().lower()
    if re.fullmatch(r"\d{4}-\d{2}", t):
        return pd.Period(t, "M")
    for i, name in enumerate(MONTHS):
        if t.startswith(name):
            now = pd.Period(today, "M")
            year = now.year if i + 1 >= now.month else now.year + 1
            return pd.Period(f"{year}-{i + 1:02d}", "M")
    raise ValueError(f"month '{token}' not understood (use Dec, December or 2026-12)")


def parse_months(text, today):
    """'Dec', 'Dec-Jan', 'Dec to Feb', '2026-12', '2026-12..2027-01', 'Dec, Jan' -> ['2026-12', ...]."""
    parts = [p for p in re.split(r"\s*(?:,|;|\band\b)\s*", str(text).strip()) if p]
    if not parts:
        raise ValueError("no months given")
    out = []
    for p in parts:
        r = re.split(r"\s*(?:\.\.|\bto\b|\bthrough\b|–|(?<=[a-z])-(?=[a-z])|(?<=\d{2})-(?=[a-z]))\s*", p, flags=re.I)
        if len(r) == 1 and re.fullmatch(r"\d{4}-\d{2}\s*-\s*\d{4}-\d{2}", p):
            r = re.split(r"\s*-\s*(?=\d{4}-)", p)
        a = _month_of(r[0], today)
        b = _month_of(r[-1], today) if len(r) > 1 else a
        if b < a:                                   # 'Dec-Jan': the end wraps into the next year
            b = pd.Period(f"{b.year + 1}-{b.month:02d}", "M") if b.year == a.year else b
        if b < a:
            raise ValueError(f"'{p}' ends before it starts")
        out += [f"{m:%Y-%m}" for m in pd.period_range(a, b, freq="M")]
    out = sorted(set(out))
    if len(out) > MAX_MONTHS:
        raise ValueError(f"at most {MAX_MONTHS} months per adjustment")
    return out


def label(row):
    """Short reason shown on order lines: '+30% owner promo, Dec' / '+50 cases owner: big order, Dec-Jan 2027'."""
    v = float(row["effect_value"])
    eff = f"{v:+g}%" if row["effect_kind"] == "pct" else f"{v:+g} cases/month"
    ms = [pd.Period(m, "M") for m in str(row["months"]).split(";") if m]
    when = f"{ms[0]:%b}" if len(ms) == 1 else f"{ms[0]:%b}-{ms[-1]:%b}" if ms else ""
    return f"{eff} owner {row['reason']}".strip() + (f", {when}" if when else "")


# ------------------------------------------------------------------ scope -> products
def resolve(scope, target, products, brand_map=None, commodity_map=None):
    """Product ids an adjustment covers. products: product_id, name; brand_map: product_id, brand;
    commodity_map: product_id, commodity. Matching is case-insensitive: exact first, then 'contains'."""
    t = str(target).strip().lower()
    if not t:
        return []
    if scope == "product":
        ids = products["product_id"].astype(str)
        hit = products[ids.str.lower() == t]
        if hit.empty:
            hit = products[products["name"].astype(str).str.lower().str.contains(re.escape(t), regex=True)]
        return sorted(hit["product_id"].astype(str))
    table, col = (brand_map, "brand") if scope == "brand" else (commodity_map, "commodity")
    if table is None or table.empty:
        return []
    vals = table[col].astype(str).str.lower()
    hit = table[vals == t]
    if hit.empty:
        hit = table[vals.str.contains(re.escape(t), regex=True)]
    return sorted(hit["product_id"].astype(str).unique())


# ------------------------------------------------------------------ lifecycle
def propose(data, scope, target, months, effect, reason, today, products, brand_map=None, commodity_map=None,
            channel=""):
    """Validate and store a `proposed` adjustment (not counted until confirmed). Returns dict(row, product_ids)."""
    scope = str(scope).strip().lower()
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    reason = re.sub(r"\s+", " ", str(reason)).strip()[:80]
    if not reason:
        raise ValueError("give a short reason (for example 'promo')")
    kind, value = parse_effect(effect)
    ms = parse_months(months, today)
    if ms[-1] < f"{pd.Timestamp(today):%Y-%m}":
        raise ValueError("those months are already over")
    ids = resolve(scope, target, products, brand_map, commodity_map)
    if not ids:
        raise ValueError(f"no products match {scope} '{target}'")
    df = load(data)
    n = 1 + max([int(x[3:]) for x in df["adj_id"] if re.fullmatch(r"adj\d+", x)] or [0])
    row = dict(adj_id=f"adj{n}", created_at=_now(), channel=channel, scope=scope, target=str(target).strip(),
               months=";".join(ms), effect_kind=kind, effect_value=f"{value:g}", reason=reason, status="proposed",
               confirmed_at="", cancelled_at="")
    _save(data, pd.concat([df, pd.DataFrame([row])], ignore_index=True))
    return dict(row=row, product_ids=ids)


def _set_status(data, adj_id, new, allowed):
    df = load(data)
    hit = df["adj_id"] == str(adj_id).strip()
    if not hit.any():
        raise ValueError(f"no adjustment {adj_id}")
    cur = df.loc[hit, "status"].iloc[0]
    if cur not in allowed:
        raise ValueError(f"{adj_id} is {cur}")
    df.loc[hit, "status"] = new
    df.loc[hit, "confirmed_at" if new == "confirmed" else "cancelled_at"] = _now()
    _save(data, df)
    return df[hit].iloc[0].to_dict()


def confirm(data, adj_id):
    return _set_status(data, adj_id, "confirmed", ("proposed",))


def cancel(data, adj_id):
    return _set_status(data, adj_id, "cancelled", ("proposed", "confirmed"))


def with_state(df, today):
    """Adds `state`: proposed / active / expired / cancelled (active = confirmed with a month not yet over)."""
    m0 = f"{pd.Timestamp(today):%Y-%m}"
    last = df["months"].astype(str).str.split(";").str[-1]
    state = df["status"].where(df["status"] != "confirmed", "active")
    state = state.where(~((df["status"] == "confirmed") & (last < m0)), "expired")
    return df.assign(state=state)


def active(data, today):
    df = with_state(load(data), today)
    return df[df["state"] == "active"].reset_index(drop=True)


# ------------------------------------------------------------------ the planning layer
def apply(fc, adjustments, products, brand_map=None, commodity_map=None, value_col="likely", extra_cols=("high",)):
    """Apply active adjustments to a forecast-like table (product_id, month YYYY-MM, value_col[, extra_cols]).
    Returns (adjusted copy, notes {product_id: '; '-joined labels}, applied rows for the report). The input is not
    modified; rows outside an adjustment's products and months are unchanged."""
    out = fc.copy()
    out["product_id"] = out["product_id"].astype(str)
    month = out["month"].astype(str).str[:7]
    notes, report = {}, []
    for _, a in adjustments.iterrows():
        ids = set(resolve(a["scope"], a["target"], products, brand_map, commodity_map))
        ms = set(str(a["months"]).split(";"))
        sel = out["product_id"].isin(ids) & month.isin(ms)
        if not sel.any():
            report.append(dict(adj_id=a["adj_id"], label=label(a), products=len(ids), rows=0, before=0.0, after=0.0))
            continue
        before = float(out.loc[sel, value_col].sum())
        v = float(a["effect_value"])
        if a["effect_kind"] == "pct":
            f = 1 + v / 100
            for c in (value_col, *[c for c in extra_cols if c in out]):
                out.loc[sel, c] = out.loc[sel, c] * f
        else:                                       # +cases per month, split by forecast share within each month
            for m in ms:
                s = sel & (month == m)
                if not s.any():
                    continue
                base = out.loc[s, value_col].clip(lower=0)
                share = base / base.sum() if base.sum() > 0 else pd.Series(1 / s.sum(), index=base.index)
                add = share * v
                for c in (value_col, *[c for c in extra_cols if c in out]):
                    out.loc[s, c] = (out.loc[s, c] + add).clip(lower=0)
        after = float(out.loc[sel, value_col].sum())
        for p in sorted(set(out.loc[sel, "product_id"])):
            notes[p] = "; ".join(x for x in (notes.get(p), label(a)) if x)
        report.append(dict(adj_id=a["adj_id"], label=label(a), products=len(ids), rows=int(sel.sum()),
                           before=round(before, 1), after=round(after, 1)))
    return out, notes, pd.DataFrame(report, columns=["adj_id", "label", "products", "rows", "before", "after"])
