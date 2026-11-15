"""Sourcing intelligence (inventory/sourcing.py): leads and alerts (validation, demo/real guards), harvest timing,
ranking, at-risk products, consolidation, and the demo story. Fake data only; nothing here touches the web."""
import shutil
from pathlib import Path

import pandas as pd
import pytest

from inventory import brand_orders as BO
from inventory import harvest as H
from inventory import sourcing as S
from inventory import synthetic

TODAY = "2026-11-14"
ROOT = Path(__file__).resolve().parent.parent


def lead(**kw):
    return dict(dict(company="Demo Co", country="India", commodities="cashew", source_url="https://example.org/x", is_demo="yes"), **kw)


# ------------------------------------------------------------------ leads
def test_country_key():
    assert S.country_key("Côte d'Ivoire") == S.country_key("Cote d'Ivoire") == "cote_d_ivoire"


def test_add_leads_validates_and_dedupes(tmp_path):
    res = S.add_leads([lead(status="confirmed"),                                   # the scout can't confirm
                       lead(commodities="almond;cashew"),                          # same company+country: merged
                       lead(company="Demo No Source", source_url=""),
                       lead(company="Demo Bad Crop", commodities="kiwi"),
                       lead(company="Demo Contact", contact_email="x@example.org"),
                       lead(company="", commodities="cashew")], tmp_path, today=TODAY)
    assert res["added"] == 1 and res["updated"] == 1 and len(res["rejected"]) == 4
    reasons = " | ".join(r for _, r in res["rejected"])
    assert "source_url" in reasons and "kiwi" in reasons and "contact" in reasons and "required" in reasons
    leads = pd.read_csv(tmp_path / "supplier_leads.csv", dtype=str).fillna("")
    assert list(leads.columns) == S.LEAD_COLUMNS
    assert leads.loc[0, "status"] == "unverified_lead" and leads.loc[0, "commodities"] == "cashew;almond"
    assert leads.loc[0, "found_on"] == TODAY and not any(S.CONTACT_WORDS.search(c) for c in leads.columns)


def test_real_and_demo_folder_guards(tmp_path, monkeypatch):
    real, demo = tmp_path / "real", tmp_path / "demo"
    real.mkdir(), demo.mkdir()
    monkeypatch.setattr(S, "REAL_DATA", real)
    monkeypatch.setattr(S, "DEMO_DATA", demo)
    assert S.add_leads([lead()], real)["rejected"]                                # demo rows never go into real data
    assert S.add_leads([lead(company="Real Co", is_demo="no")], demo)["rejected"]   # real rows never go into data_demo
    assert S.add_leads([lead(company="Real Co", is_demo="no")], real)["added"] == 1
    pd.DataFrame([dict(lead_id="L1", company="Demo X", country="India", commodities="cashew", is_demo="yes"),
                  dict(lead_id="L2", company="Real Co", country="India", commodities="cashew", is_demo="no")]
                 ).reindex(columns=S.LEAD_COLUMNS).fillna("").to_csv(real / "supplier_leads.csv", index=False)
    assert list(S.load_leads(real)["company"]) == ["Real Co"]                     # demo rows dropped in real data
    shutil.copy(real / "supplier_leads.csv", demo / "supplier_leads.csv")
    with pytest.raises(ValueError, match="non-demo"):
        S.load_leads(demo)


def test_committed_demo_leads_and_alerts_are_fake():
    leads = pd.read_csv(ROOT / "data_demo" / "supplier_leads.csv", dtype=str).fillna("")
    assert (leads["is_demo"] == "yes").all() and leads["company"].str.startswith("Demo ").all()
    assert leads["source_url"].str.startswith("https://example.org/").all()
    al = pd.read_csv(ROOT / "data_demo" / "country_alerts.csv", dtype=str).fillna("")
    assert (al["is_demo"] == "yes").all() and al["issue"].str.startswith("DEMO:").all()
    S.load_leads(ROOT / "data_demo")                                               # the guard passes


def test_import_vendor_list(tmp_path):
    pd.DataFrame({"Vendor": ["Demo Vendor"], "Origin": ["India"], "Products": ["toor, moong"]}).to_csv(tmp_path / "v.csv", index=False)
    assert S.import_vendor_list(tmp_path / "v.csv", tmp_path, today=TODAY) == 1
    l = S.load_leads(tmp_path).iloc[0]
    assert l["status"] == "confirmed" and l["commodities"] == "toor;moong" and l["source_url"] == "vendor list: v.csv"


# ------------------------------------------------------------------ alerts
def test_add_alerts_and_expiry(tmp_path):
    res = S.add_alerts([dict(commodity="cashew", country="India", issue="DEMO: x", source_url="https://example.org/a",
                             reported_on="2026-10-01", is_demo="yes"),
                        dict(commodity="all", country="Vietnam", issue="DEMO: y", source_url="https://example.org/b",
                             reported_on="2026-11-01", is_demo="yes"),
                        dict(commodity="cashew", country="India", issue="no source", is_demo="yes"),
                        dict(commodity="cashew", country="India", issue="missing prefix", source_url="https://example.org/c", is_demo="yes")],
                       tmp_path, today=TODAY)
    assert res["added"] == 2 and len(res["rejected"]) == 2
    al = pd.read_csv(tmp_path / "country_alerts.csv", dtype=str)
    assert al.loc[0, "expires_on"] == "2026-11-30" and al.loc[0, "status"] == "active"     # reported_on + 60 days
    act = S.load_alerts(tmp_path, TODAY).set_index("country")["active"]
    assert act["India"] and act["Vietnam"]
    assert not S.load_alerts(tmp_path, "2026-12-15").set_index("country")["active"]["India"]     # expired
    al.loc[1, "status"] = "dismissed"
    al.to_csv(tmp_path / "country_alerts.csv", index=False)
    assert not S.load_alerts(tmp_path, TODAY).set_index("country")["active"]["Vietnam"]
    assert S.alerts_for(S.load_alerts(tmp_path, TODAY), "almond", "India") == ""            # other commodity


def test_demo_alerts_never_count_in_real_data(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "REAL_DATA", tmp_path)
    shutil.copy(ROOT / "data_demo" / "country_alerts.csv", tmp_path / "country_alerts.csv")
    assert S.load_alerts(tmp_path, TODAY).empty


# ------------------------------------------------------------------ timing and ranking
@pytest.mark.parametrize("start, end, label", [("Oct", "Dec", "in harvest now"), ("Aug", "Oct", "just after harvest"),
                                               ("Feb", "May", "next harvest in 3 month(s)"), ("", "", "harvest months unknown")])
def test_harvest_timing(start, end, label):
    assert S.harvest_timing(dict(harvest_start_month=start, harvest_end_month=end), TODAY)[0].startswith(label)


def cal(rows):
    c = pd.DataFrame(rows).reindex(columns=H.CAL_COLUMNS).fillna("")
    c["rule_id"] = [f"{H.slug(a)}_{H.slug(b)}" for a, b in zip(c["commodity"], c["source_country"])]
    return c


CAL = cal([dict(commodity="cashew", source_country="India", harvest_start_month="Feb", harvest_end_month="May"),
           dict(commodity="cashew", source_country="Cote d'Ivoire", harvest_start_month="Oct", harvest_end_month="Dec"),
           dict(commodity="cashew", source_country="Vietnam")])


def leads_frame(tmp_path, rows):
    pd.DataFrame(rows).reindex(columns=S.LEAD_COLUMNS).fillna("").to_csv(tmp_path / "supplier_leads.csv", index=False)
    return S.load_leads(tmp_path)


def test_ranking_tiers_alerts_and_rejected(tmp_path):
    L = leads_frame(tmp_path, [
        dict(lead_id="LA", company="Demo A", country="Côte d'Ivoire", commodities="cashew;almond", status="unverified_lead", is_demo="yes"),
        dict(lead_id="LB", company="Demo B", country="India", commodities="cashew", status="unverified_lead", is_demo="yes"),
        dict(lead_id="LC", company="Demo C", country="India", commodities="cashew", status="confirmed", is_demo="yes"),
        dict(lead_id="LD", company="Demo D", country="Vietnam", commodities="cashew", status="rejected", is_demo="yes"),
        dict(lead_id="LE", company="Demo E", country="USA", commodities="cashew", status="unverified_lead", is_demo="yes")])
    need = pd.DataFrame([dict(commodity="almond", what="planned harvest buy", due=pd.Timestamp("2027-07-31"), cases=100.0)])
    no_alerts = S.load_alerts(tmp_path, TODAY)
    o = S.options_for(dict(product_id="P1", name="Cashew"), "cashew", TODAY, CAL, L, no_alerts, need)
    ranked = o[o["rank"].notna()]
    assert list(ranked["company"].fillna("")) == ["Demo C", "Demo A", "Demo B", "Demo E", ""]   # confirmed, then leads by harvest timing
    assert ranked.iloc[1]["consolidation"].startswith("almond (planned harvest buy")
    assert ranked.iloc[-1]["tier"] == "country" and ranked.iloc[-1]["country"] == "Vietnam"     # rejected lead doesn't count
    assert o[o["company"] == "Demo D"]["rank"].isna().all()
    assert o.set_index("company").at["Demo E", "harvest_timing"] == "country not in the harvest calendar"
    S.add_alerts([dict(commodity="cashew", country="Cote d'Ivoire", issue="DEMO: strike", source_url="https://example.org/s",
                       reported_on="2026-11-10", is_demo="yes")], tmp_path, today=TODAY)
    o2 = S.options_for(dict(product_id="P1", name="Cashew"), "cashew", TODAY, CAL, L, S.load_alerts(tmp_path, TODAY), need)
    r2 = o2[o2["rank"].notna()]
    assert list(r2["company"].fillna(""))[:4] == ["Demo C", "Demo B", "Demo E", "Demo A"]       # the alert drops A within its tier
    assert r2.set_index("company").at["Demo A", "alert"].startswith("alert: DEMO: strike (https://example.org/s)")


def test_arrival_check():
    assert S.arrival_check("2027-06-01", TODAY).startswith("in time even at 130 days")
    assert S.arrival_check("2027-03-05", TODAY).startswith("in time only if shipping takes about 105 days")
    assert "expedite or air" in S.arrival_check("2026-12-05", TODAY)
    assert S.arrival_check("", TODAY) == "run-out date unknown"


# ------------------------------------------------------------------ at-risk products
def test_products_at_risk_flags_holds_and_no_stock(tmp_path):
    risk, note = S.products_at_risk(tmp_path, TODAY)
    assert risk.empty and "no stock data" in note
    pd.DataFrame({"product_id": ["P1", "P2", "P3", "P4"], "below_floor_now": [True, False, False, True],
                  "floor_dip_date": [None, "2027-01-08", None, None]}).to_csv(tmp_path / "product_status.csv", index=False)
    pd.DataFrame({"order_id": ["B|2026-11-14"], "product_id": ["P3"], "cases": [5], "on_hold": [False],
                  "runs_out_before_arrival": ["2027-02-01"]}).to_csv(tmp_path / "brand_order_lines.csv", index=False)
    pd.DataFrame({"product_id": ["P4"]}).to_csv(tmp_path / "holds_now.csv", index=False)
    pd.DataFrame({"product_id": ["P1"], "month": ["2027-01"], "projected_stock": [-3.0]}).to_csv(tmp_path / "stock_projection.csv", index=False)
    risk, note = S.products_at_risk(tmp_path, TODAY)
    r = risk.set_index("product_id")
    assert set(r.index) == {"P1", "P2", "P3"}                                     # P4 is on hold
    assert r.at["P1", "risk"] == "below the 3-month floor now" and r.at["P1", "run_out_date"] == "2027-01-01"
    assert "expedite" in r.at["P2", "risk"] and r.at["P2", "run_out_date"] == ""
    assert r.at["P3", "run_out_date"] == "2027-02-01" and note == ""


# ------------------------------------------------------------------ the demo story, end to end
@pytest.mark.timeout(180)
def test_demo_story_cashew(demo_run, tmp_path, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "out"
    shutil.copytree(demo_run["data"], data)
    shutil.copytree(demo_run["out"], out)
    (data / "owner_answers.csv").unlink(missing_ok=True)
    synthetic.generate(tmp_path / "gen", write_stock=True)
    shutil.copy(tmp_path / "gen" / "stock.csv", data / "stock.csv")
    synthetic.write_demo_answers(data)
    synthetic.write_demo_sourcing(data)
    BO.run(data, out, TODAY)
    res = S.run(data, out, TODAY)
    s = res["summary"].set_index("product_id")
    assert s.index[0] == "AN402"                                                  # mapped products first, soonest run-out
    c = s.loc["AN402"]
    assert c["commodity"] == "cashew" and "below the 3-month floor" in c["risk"]
    assert c["suppliers_and_leads"].startswith("#1 Demo Exporter B (India) [unverified lead (not contacted)]")
    assert "Demo Exporter A" in c["suppliers_and_leads"] and "alert: DEMO:" in c["suppliers_and_leads"]
    assert "almond (planned harvest buy" in c["consolidation"]
    assert "harvest months unknown for Vietnam" in c["unknowns"] and "no prices" in c["unknowns"]
    L = res["lines"]
    cash = L[L["product_id"] == "AN402"].set_index("company")
    assert pd.isna(cash.at["Demo Exporter D", "rank"])                            # rejected
    assert (out / "sourcing_options.csv").exists() and (out / "country_alerts_active.csv").exists()

    # the same folder treated as real data: demo leads and the demo alert disappear from the outputs
    monkeypatch.setattr(S, "REAL_DATA", data)
    real = S.run(data, out, TODAY)
    assert real["leads"] == 0 and real["alerts"] == 0
    assert not real["lines"]["alert"].fillna("").str.contains("DEMO").any()
    assert not real["summary"]["alerts"].fillna("").str.contains("DEMO").any()
