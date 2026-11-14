"""Sales are not demand. Find months where the product could not be sold, and fill them in.

Known gaps come from the status column (FDA hold, stockout, recall). Unknown gaps are long
runs of zeros for a product that normally sells every month: likely a supply problem nobody
recorded. The agent should ASK about those instead of silently trusting them.
"""
import numpy as np
import pandas as pd

from .schema import UNAVAILABLE


def detect_unflagged_gaps(panel, min_run=2, min_prior_months=6, min_nonzero_rate=0.8, min_prior_mean=5):
    flags = []
    for pid, g in panel.groupby("product_id"):
        g = g.reset_index(drop=True)
        zero = (g["units_sold"] == 0) & (~g["status"].isin(UNAVAILABLE))
        i = 0
        while i < len(g):
            if not zero[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(g) and zero[j + 1]:
                j += 1
            prior = g.loc[: i - 1]
            prior = prior[~prior["status"].isin(UNAVAILABLE)]
            if (j - i + 1 >= min_run and len(prior) >= min_prior_months
                    and (prior["units_sold"] > 0).mean() >= min_nonzero_rate
                    and prior["units_sold"].mean() >= min_prior_mean):
                flags.append(dict(product_id=pid, start=g.loc[i, "month"], end=g.loc[j, "month"],
                                  months=j - i + 1, usual_monthly=round(prior["units_sold"].tail(6).mean(), 1),
                                  reason="sells every month, then zero: likely an unrecorded supply gap"))
            i = j + 1
    return pd.DataFrame(flags, columns=["product_id", "start", "end", "months", "usual_monthly", "reason"])


def clean_demand(panel, suspected=None):
    """Add `unavailable`, `demand` (sales with gaps filled) and `imputed` columns."""
    p = panel.copy()
    p["unavailable"] = p["status"].isin(UNAVAILABLE)
    if suspected is not None and len(suspected):
        for r in suspected.itertuples():
            m = (p["product_id"] == r.product_id) & p["month"].between(r.start, r.end)
            p.loc[m, "unavailable"] = True
    p["demand"] = np.where(p["unavailable"], np.nan, p["units_sold"]).astype(float)
    p["imputed"] = False
    out = []
    for pid, g in p.groupby("product_id"):
        g = g.set_index("month").sort_index()
        for m in g.index[g["unavailable"]]:
            ly = m - pd.DateOffset(years=1)
            avail = g.loc[g.index < m, "demand"].dropna()
            est = np.nan
            if ly in g.index and not np.isnan(g.at[ly, "demand"]):
                recent = avail.tail(6)
                year_ago = g["demand"].reindex(recent.index - pd.DateOffset(years=1)).dropna() if len(recent) else pd.Series(dtype=float)
                ratio = recent.mean() / year_ago.mean() if len(year_ago) and year_ago.mean() > 0 else 1.0
                est = g.at[ly, "demand"] * float(np.clip(ratio, 0.5, 2.0))
            elif len(avail):
                est = avail.tail(6).median()
            if not np.isnan(est):
                g.at[m, "demand"] = est
                g.at[m, "imputed"] = True
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True).sort_values(["product_id", "month"]).reset_index(drop=True)
