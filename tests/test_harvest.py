"""Harvest-season ordering (inventory/harvest.py): commodity mapping, the cited calendar (partly empty),
order windows and statuses, precedence of the owner's rice rule, the harvest plan, and the loop on demo
data: calendar row -> owner confirms -> harvest order in the brand orders. Fake data only."""
import csv
import shutil

import numpy as np
import pandas as pd
import pytest

from inventory import answers as A
from inventory import brand_orders as BO
from inventory import harvest as H
from inventory import synthetic

TODAY = "2026-11-14"
TS = "2026-11-10T09:00:00+00:00"


def answers(tmp_path, rows):
    with open(tmp_path / "owner_answers.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(A.FIELDS)
        w.writerows([[q, a, n, TS] for q, a, n in rows])
    return A.load(tmp_path)


def _normalize(cal):
    cal = cal.copy()
    for c in ("harvest_start_month", "harvest_end_month"):
        cal[c] = [H.MON[H.month_num(v) - 1] if H.month_num(v) else "" for v in cal[c]]
    cal["rule_id"] = [f"{H.slug(c)}_{H.slug(k)}" for c, k in zip(cal["commodity"], cal["source_country"])]
    return cal


def products(*rows):
    return pd.DataFrame(rows, columns=["product_id", "name", "category"])


# ------------------------------------------------------------------ mapping
def test_map_products_keywords_prefixes_and_flags():
    p = products(("AD201", "Aara Toor Dal 4 lb X 10", "lentils_beans"),
                 ("BD301", "Chana Dal 55 Lb X", "lentils_beans"),
                 ("AD210", "Aara Masoor Whole (Matki) - 2 Lbs X 20", "lentils_beans"),     # masoor and moth
                 ("AD299", "Aara Panchratna Dal 2 lb X 20", "lentils_beans"),               # mixed
                 ("AD298", "Aara Chora Dal 2 lb X 20", "lentils_beans"),                    # no keyword
                 ("AR401", "Aara Khichdi Mix - 2 Lbs X 20", "noodles_ready"),               # harvest = no
                 ("AR417", "Aara (Bombay Import) Premium Basmati Rice 20 lbs X", "rice_flour"),
                 ("IG104", "India Gate Classic Basmati Rice 10 lb X 4 X", "rice_flour"),
                 ("AN1417", "Aara Milk Mawa Powder 14 oz X 10", "dairy"),
                 ("AN709", "Aara Green Raisin 7 OZ (200 Gram) X 20 X", "nuts_dry_fruits"),
                 ("AP016", "Aara Toor Dal Roasted 400 gm", "snacks"))                       # outside the groups
    m, un = H.map_products(p, H.load_map())
    c = m.set_index("product_id")
    assert c.at["AD201", "commodity"] == "toor" and c.at["BD301", "commodity"] == "chickpea"
    assert c.at["AR401", "commodity"] == "khichdi_mix" and c.at["AR401", "harvest"] == "no"
    assert c.at["AN1417", "harvest"] == "no" and c.at["AN709", "commodity"] == "raisin"
    assert c.at["AR417", "commodity"] == "basmati" and c.at["AR417", "origin_hint"] == "India"
    assert c.at["IG104", "origin_hint"] == "India"
    assert "AP016" not in c.index and "AP016" not in set(un["product_id"])                # prefix outside the groups
    u = un.set_index("product_id")
    assert u.at["AD210", "reason"] == "name matches several commodities" and u.at["AD210", "candidates"] == "moth|masoor"
    assert u.at["AD299", "reason"] == "mixed pack of several crops"
    assert u.at["AD298", "reason"] == "no commodity keyword in the name"


def test_owner_overrides_map_or_unmap():
    p = products(("AD298", "Aara Chora Dal 2 lb X 20", "lentils_beans"), ("AD201", "Aara Toor Dal 4 lb X 10", "lentils_beans"))
    m, un = H.map_products(p, H.load_map(), overrides={"AD298": "black_eye", "AD201": None})
    c = m.set_index("product_id")
    assert c.at["AD298", "commodity"] == "black_eye" and c.at["AD298", "matched_by"] == "owner answer"
    assert c.at["AD201", "harvest"] == "no" and un.empty


def test_shipped_commodity_map_is_valid():
    m = H.load_map()
    assert m["commodity"].is_unique and set(m["harvest"]) <= {"yes", "no", "mixed"}
    assert set(m["group"]) == {"dals", "rice", "nuts"}


# ------------------------------------------------------------------ calendar
def test_load_calendar_missing_file_and_normalizing(tmp_path):
    assert H.load_calendar(tmp_path / "nope.csv").empty
    pd.DataFrame([dict(commodity="cashew", source_country="Cote d'Ivoire", harvest_start_month="february",
                       harvest_end_month="", confirmed_by_owner="no")]).to_csv(tmp_path / "c.csv", index=False)
    cal = H.load_calendar(tmp_path / "c.csv")
    assert cal.loc[0, "rule_id"] == "cashew_cote_d_ivoire"
    assert cal.loc[0, "harvest_start_month"] == "Feb" and cal.loc[0, "harvest_end_month"] == ""


def test_shipped_calendar_rows_are_cited_and_unconfirmed():
    cal = H.load_calendar()
    if cal.empty:
        pytest.skip("no calendar shipped")
    with_months = cal[cal["harvest_start_month"] != ""]
    assert with_months["source_url"].str.startswith("http").all()             # every seeded month has a source
    assert set(cal["confirmed_by_owner"].str.lower()) <= {"no", ""}            # the owner confirms, never the seed
    assert cal["rule_id"].is_unique
    for q, _, _ in synthetic.DEMO_ANSWERS:                                     # the demo confirms rows that exist
        assert q.split(":", 1)[1] in set(with_months["rule_id"])


@pytest.mark.parametrize("today, status, order_by", [
    ("2026-09-15", "upcoming", "2026-10-31"),          # harvest Nov-Jan: order in October
    ("2026-10-10", "ORDER NOW", "2026-10-31"),
    ("2026-12-01", "window closed", "2027-10-31"),     # harvest under way: next window next year
    ("2027-02-15", "upcoming", "2027-10-31")])         # after the harvest ended (Jan 31)
def test_windows_and_status(today, status, order_by):
    row = dict(harvest_start_month="Nov", harvest_end_month="Jan", order_months_before="")
    w = H.windows(row, today)
    assert w["status"] == status
    assert f"{(w['order_by'] if status == 'ORDER NOW' else w['next_order_by']):%Y-%m-%d}" == order_by


def test_windows_wrap_year_and_months_before():
    w = H.windows(dict(harvest_start_month="Jan", harvest_end_month="Mar", order_months_before="2"), "2026-11-14")
    assert w["status"] == "ORDER NOW" and f"{w['order_by']:%Y-%m-%d}" == "2026-11-30"    # Jan - 2 = Nov


def test_window_label():
    assert H.window_label("Dec", "Apr") == "Dec-Apr" and H.window_label("Sep", "Sep") == "Sep"
    assert H.window_label("Aug", "") == "Aug (end month not stated)" and H.window_label("", "") == ""


def test_rule_row_months_and_cover():
    row = dict(rule_id="toor_india", source_country="India", harvest_start_month="Dec", harvest_end_month="Mar",
               crops_per_year="2", order_months_before="")
    r = H.rule_row(row, ["A1", "A2"], TODAY)
    assert r["order_months"] == "Nov" and r["arrival_months"] == "Apr"   # Nov 30 + 130 days = Apr 9
    assert r["cover_months"] == "6" and r["product_ids"] == "A1|A2" and r["confirmed"] == "yes"
    assert H.rule_row(dict(row, crops_per_year=""), ["A1"], TODAY)["cover_months"] == "12"


# ------------------------------------------------------------------ prepare: answers and precedence
CAL = [dict(commodity="toor", source_country="India", harvest_start_month="Dec", harvest_end_month="Mar",
            source_url="https://example.org/toor", confidence="high", confirmed_by_owner="no"),
       dict(commodity="toor", source_country="Myanmar", harvest_start_month="", harvest_end_month="",
            confirmed_by_owner="no"),
       dict(commodity="almond", source_country="USA", harvest_start_month="Aug", harvest_end_month="Oct",
            source_url="https://example.org/almond", confidence="high", confirmed_by_owner="no"),
       dict(commodity="basmati", source_country="India", harvest_start_month="Oct", harvest_end_month="Nov",
            source_url="https://example.org/rice", confidence="medium", confirmed_by_owner="no")]
PRODUCTS = products(("AD201", "Sunrise Toor Dal 4 lb X 10", "lentils_beans"), ("AN401", "Lotus Almond 14 oz X 20", "nuts_dry_fruits"),
                    ("AR501", "Peacock Basmati Rice 10 lb X 4", "rice_flour"), ("AR502", "Peacock Idli Rice 20 lb X 2", "rice_flour"))


@pytest.mark.parametrize("answer, note, rule, st", [
    ("yes", "", True, A.APPLIED), ("yes", "harvest is from January to April", True, A.APPLIED),
    ("no", "", False, A.APPLIED), ("not sure", "", False, A.NO_CHANGE)])
def test_owner_confirms_calendar_row(tmp_path, answer, note, rule, st):
    a = answers(tmp_path, [("harvest:toor_india", answer, note)])
    prep = H.prepare(PRODUCTS, a, TODAY, cmap=H.load_map(), calendar=_normalize(pd.DataFrame(CAL).reindex(columns=H.CAL_COLUMNS).fillna("")))
    assert ("toor_india" in set(prep.calendar_rules["rule_id"])) is rule
    row = prep.calendar.set_index("rule_id").loc["toor_india"]
    assert row["confirmed"] == {"yes": "yes", "no": "rejected", "not sure": "no"}[answer]
    if note:
        assert (row["harvest_start_month"], row["harvest_end_month"], row["month_source"]) == ("Jan", "Apr", "owner answer")
    assert a.report().set_index("question_id").at["harvest:toor_india", "status"] == st


def test_confirming_a_row_without_months_needs_them(tmp_path):
    a = answers(tmp_path, [("harvest:toor_myanmar", "yes", "")])
    prep = H.prepare(PRODUCTS, a, TODAY, cmap=H.load_map(), calendar=_normalize(pd.DataFrame(CAL).reindex(columns=H.CAL_COLUMNS).fillna("")))
    assert prep.calendar_rules.empty
    assert a.report().set_index("question_id").at["harvest:toor_myanmar", "status"] == A.NOT_UNDERSTOOD


def test_owner_rice_rule_wins_over_calendar(tmp_path):
    a = answers(tmp_path, [("harvest:basmati_india", "yes", "")])
    prep = H.prepare(PRODUCTS, a, TODAY, cmap=H.load_map(), calendar=_normalize(pd.DataFrame(CAL).reindex(columns=H.CAL_COLUMNS).fillna("")))
    assert "AR501" in prep.config_members and prep.config_members["AR501"]["rule_id"] == "rice"
    assert prep.calendar_rules.empty                     # its only basmati product follows the rice rule
    from inventory import stock_plan as SP
    confirmed, _ = SP.harvest_members(PRODUCTS, prep.rules)
    assert confirmed["AR501"]["rule_id"] == "rice"


def test_first_confirmed_country_is_used(tmp_path):
    cal = CAL + [dict(commodity="toor", source_country="Tanzania", harvest_start_month="Jul", harvest_end_month="Sep",
                      source_url="https://example.org/tz", confirmed_by_owner="no")]
    a = answers(tmp_path, [("harvest:toor_tanzania", "yes", ""), ("harvest:toor_india", "yes", "")])
    prep = H.prepare(PRODUCTS, a, TODAY, cmap=H.load_map(), calendar=_normalize(pd.DataFrame(cal).reindex(columns=H.CAL_COLUMNS).fillna("")))
    assert list(prep.calendar_rules["rule_id"]) == ["toor_india"]


# ------------------------------------------------------------------ plan
def fc_for(pids, per_month=30.0):
    months = pd.date_range("2026-11-01", periods=12, freq="MS")
    return pd.DataFrame([(p, f"{m:%Y-%m}", per_month) for p in pids for m in months], columns=["product_id", "month", "likely"])


def test_plan_with_a_partly_empty_calendar(tmp_path):
    a = answers(tmp_path, [("harvest:toor_india", "yes", "")])
    prep = H.prepare(PRODUCTS, a, TODAY, cmap=H.load_map(), calendar=_normalize(pd.DataFrame(CAL).reindex(columns=H.CAL_COLUMNS).fillna("")))
    stock = pd.DataFrame({"product_id": ["AD201", "AN401", "AR502"], "on_hand_cases": [500.0, 10.0, 40.0]})
    plan = H.plan(prep, fc_for(["AD201", "AN401", "AR501", "AR502"]), PRODUCTS, stock, TODAY).set_index("rule_id", drop=False)
    toor = plan.loc["toor_india"]
    assert toor["status"] == "ORDER NOW" and toor["order_by"] == "2026-11-30" and toor["in_orders"] == "yes"
    assert toor["rule_source"] == "calendar (confirmed)" and toor["quantity_cases"] > 0
    assert "order by Nov 30, 2026" in toor["reason"] and "assumed one harvest a year" in toor["reason"]
    assert plan.loc["toor_myanmar", "status"] == "no harvest data" and "regular reorder rule" in plan.loc["toor_myanmar", "reason"]
    alm = plan.loc["almond_usa"]
    assert alm["in_orders"] == "no" and "not confirmed" in alm["reason"] and alm["quantity_cases"] > 0   # what WOULD be ordered
    assert "bridge order" in alm["reason"]                    # 10 cases on hand can't last until the Aug harvest order lands
    idli = plan[plan["commodity"] == "idli_rice"].iloc[0]
    assert idli["status"] == "no harvest data" and idli["rule_source"] == "no harvest calendar row"
    bas = plan.loc["basmati_india"]
    assert bas["status"] == "no products" and "rice rule" in bas["reason"]


def test_plan_without_stock_is_gross_and_places_nothing(tmp_path):
    a = answers(tmp_path, [("harvest:toor_india", "yes", "")])
    prep = H.prepare(PRODUCTS, a, TODAY, cmap=H.load_map(), calendar=_normalize(pd.DataFrame(CAL).reindex(columns=H.CAL_COLUMNS).fillna("")))
    plan = H.plan(prep, fc_for(["AD201"]), PRODUCTS, None, TODAY).set_index("rule_id")
    assert plan.at["toor_india", "in_orders"] == "no" and "gross need" in plan.at["toor_india", "reason"]


def test_plan_with_an_empty_calendar_falls_back(tmp_path):
    prep = H.prepare(PRODUCTS, A.empty(), TODAY, cmap=H.load_map(), calendar=_normalize(pd.DataFrame(columns=H.CAL_COLUMNS)))
    plan = H.plan(prep, fc_for(["AD201", "AN401"]), PRODUCTS, None, TODAY)
    assert set(plan["status"]) <= {"no harvest data", "no products"} and (plan["in_orders"] == "no").all()


# ------------------------------------------------------------------ the loop on demo data
@pytest.mark.timeout(180)
def test_confirmed_harvest_row_reaches_brand_orders(demo_run, tmp_path):
    data, out = tmp_path / "data", tmp_path / "out"
    shutil.copytree(demo_run["data"], data)
    shutil.copytree(demo_run["out"], out)
    (data / "owner_answers.csv").unlink(missing_ok=True)         # other tests answer in the shared demo folder
    synthetic.generate(tmp_path / "stockgen", write_stock=True)                 # same demo data, plus stock
    shutil.copy(tmp_path / "stockgen" / "stock.csv", data / "stock.csv")
    cal_path = tmp_path / "harvest_calendar.csv"
    pd.DataFrame(CAL).reindex(columns=H.CAL_COLUMNS).fillna("").to_csv(cal_path, index=False)

    BO.run(data, out, TODAY, harvest_calendar=cal_path)                          # before: not confirmed
    lines = pd.read_csv(out / "brand_order_lines.csv", dtype={"product_id": str})
    assert not lines.loc[lines["product_id"] == "AD201", "harvest"].astype(str).eq("True").any()
    plan = pd.read_csv(out / "harvest_plan.csv").set_index("rule_id")
    assert plan.at["toor_india", "in_orders"] == "no"

    synthetic.write_demo_answers(data)                                           # the owner confirms toor (and almond)
    BO.run(data, out, TODAY, harvest_calendar=cal_path)                          # after
    ho = pd.read_csv(out / "harvest_orders.csv", dtype={"product_id": str}).set_index("product_id")
    assert ho.at["AD201", "rule"] == "toor_india" and ho.at["AD201", "order_cases"] > 0
    assert ho.at["AR501", "rule"] == "rice"                                      # the owner's rice rule is untouched
    lines = pd.read_csv(out / "brand_order_lines.csv", dtype={"product_id": str})
    toor = lines[(lines["product_id"] == "AD201")]
    assert len(toor) and toor["harvest"].astype(str).eq("True").all()
    orders = pd.read_csv(out / "brand_orders.csv")
    assert orders["order_id"].isin(toor["order_id"]).any()                       # inside a Sunrise brand order
    plan = pd.read_csv(out / "harvest_plan.csv").set_index("rule_id")
    assert plan.at["toor_india", "in_orders"] == "yes" and plan.at["almond_usa", "in_orders"] == "yes"
    rep = pd.read_csv(out / "harvest_plan.csv")
    assert (rep["commodity"] == "idli_rice").any()                               # no calendar row: regular rule, says so


def test_arrival_dates_follow_the_lead_time(tmp_path):
    """arrival_latest = order-by + 130 days; arrival_earliest = (window start, or today if the window is open) + 105."""
    a = answers(tmp_path, [("harvest:toor_india", "yes", "")])
    prep = H.prepare(PRODUCTS, a, TODAY, cmap=H.load_map(), calendar=_normalize(pd.DataFrame(CAL).reindex(columns=H.CAL_COLUMNS).fillna("")))
    plan = H.plan(prep, fc_for(["AD201", "AN401"]), PRODUCTS, None, TODAY)
    rows = plan[plan["arrival_latest"].fillna("") != ""]
    assert len(rows) >= 2 and "arrival_estimate" not in plan.columns
    for r in rows.itertuples():
        assert pd.Timestamp(r.arrival_latest) == pd.Timestamp(r.order_by) + pd.Timedelta(days=130)
        start = max(pd.Timestamp(r.order_window_start), pd.Timestamp(TODAY))
        assert pd.Timestamp(r.arrival_earliest) == start + pd.Timedelta(days=105)
    toor = plan.set_index("rule_id").loc["toor_india"]                    # window open on Nov 14: counted from today
    assert toor["status"] == "ORDER NOW" and toor["arrival_earliest"] == "2027-02-27" and "ordered today" in toor["reason"]
