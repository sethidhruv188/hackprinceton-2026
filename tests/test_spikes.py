import pandas as pd

from inventory import gaps, spikes
from conftest import MONTHS


def series(pid, spike_months=(), gap_months=()):
    rows = []
    for m in MONTHS:
        ms = f"{m:%Y-%m}"
        if ms in gap_months:
            rows.append(dict(product_id=pid, month=m, units_sold=0.0, status="stockout"))
        else:
            rows.append(dict(product_id=pid, month=m, units_sold=100.0 if ms in spike_months else 20.0, status="sellable"))
    return rows


def test_each_cause():
    panel = pd.DataFrame(
        series("GAP", ["2025-07"], gap_months=["2025-05", "2025-06"])
        + series("SW", ["2025-07"])
        + series("DIW", ["2025-10"])
        + series("FEST", ["2025-03"])            # Holi / Ramadan / Eid window, not Diwali
        + series("SEAS", ["2025-07", "2026-07"])  # July: no festival window
        + series("ONE", ["2025-12"]))
    clean = gaps.clean_demand(panel)
    item_map = pd.DataFrame([
        dict(raw_item="SW - OLD", product_id="SW", merge_rule="OLD -> current code", first_sale="2024-07-03", last_sale="2025-07-20"),
        dict(raw_item="SW", product_id="SW", merge_rule="", first_sale="2025-08-01", last_sale="2026-10-30")])
    sp = spikes.detect(panel, clean, item_map).set_index("product_id")
    assert sp.loc["GAP", "cause"] == "post_gap_rebound"
    assert sp.loc["SW", "cause"] == "code_switch"
    assert sp.loc["DIW", "cause"] == "diwali"
    assert sp.loc["FEST", "cause"] == "other_festival" and "possible link" in sp.loc["FEST", "detail"]
    seas = sp.loc[["SEAS"]]
    assert (seas["cause"] == "recurring_seasonal").all() and (seas["recurrence"] == "seen in 2 of 3 years").all()
    assert sp.loc["ONE", "cause"] == "one_off" and sp.loc["ONE", "recurrence"] == "seen in 1 of 2 years"
    assert (sp["baseline"] == 20).all() and (sp["ratio"] == 5).all()


def test_no_spike_below_min_cases_or_thin_history():
    small = pd.DataFrame(series("S"))
    small["units_sold"] = small["units_sold"] / 10      # 2 cases a month, spike to 10 is >= 10 only at spike
    small.loc[small["month"] == "2025-12-01", "units_sold"] = 9.0
    assert spikes.detect(small, gaps.clean_demand(small)).empty


def test_empty_spikes_give_typed_features():
    """Early folds have < 12 months, so no spikes: features must still be bool / float."""
    short = pd.DataFrame(series("S")[:10])
    sp = spikes.detect(short, gaps.clean_demand(short))
    assert sp.empty
    assert spikes.spike_flags(short, sp).dtype == bool
    assert spikes.one_off_caps(short, sp).dtype == float
