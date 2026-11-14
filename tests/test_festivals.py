import pandas as pd
import pytest

from inventory import forecast as F


@pytest.mark.parametrize("month,days", [("2024-10-01", 16), ("2025-10-01", 5), ("2026-10-01", 24), ("2026-11-01", -7)])
def test_days_to_diwali(month, days):
    # from the 15th: 2024-10-15 -> Oct 31 = 16; 2025-10-15 -> Oct 20 = 5; 2026-10-15 -> Nov 8 = 24;
    # 2026-11-15 is 348 days before Diwali 2027, so it reports days since Nov 8 2026 = -7
    assert F.days_to_diwali(pd.Timestamp(month)) == days


def test_days_capped():
    vals = F.festival_flags(pd.date_range("2024-07-01", "2027-10-01", freq="MS"))["days_to_diwali"]
    assert vals.between(-180, 180).all()


def test_only_diwali_is_a_model_feature():
    assert F.TIER1 == ["diwali"]
    assert not any(c.startswith("minor_fest") for c in F.FEATURES)
    assert {"fest_diwali_this", "fest_diwali_next", "days_to_diwali"} <= set(F.FEATURES)


@pytest.mark.parametrize("m", ["2024-10-01", "2024-11-01", "2025-10-01", "2026-11-01"])
def test_diwali_this(m):
    assert F.festival_flags([pd.Timestamp(m)])["fest_diwali_this"].iloc[0] == 1


@pytest.mark.parametrize("m", ["2024-09-01", "2025-09-01", "2026-10-01"])
def test_diwali_next(m):
    assert F.festival_flags([pd.Timestamp(m)])["fest_diwali_next"].iloc[0] == 1


def test_past_calendar_end_raises():
    with pytest.raises(ValueError):
        F.festival_flags([F.CAL["end"] + pd.DateOffset(months=1)])
