"""supplier:LEAD_ID and alert:ALERT_ID answers, and their questions on the dashboard. Fake / demo data only."""
import csv
import shutil

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from inventory import answers as A
from inventory import brand_orders as BO
from inventory import sourcing as S
from inventory import synthetic

TS = "2026-11-12T09:00:00+00:00"
TODAY = "2026-11-14"


def answers(tmp_path, rows):
    with open(tmp_path / "owner_answers.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(A.FIELDS)
        w.writerows([[q, a, n, TS] for q, a, n in rows])
    return A.load(tmp_path)


def test_question_ids_parse():
    assert A.parse_question("supplier:L0f44b75a") == ("supplier", "L0f44b75a", None)
    assert A.parse_question("alert:Affbd935e") == ("alert", "Affbd935e", None)


LEADS = pd.DataFrame([dict(lead_id="L1", company="Demo A", country="India", commodities="cashew", status="unverified_lead",
                           owner_note="", is_demo="yes")]).reindex(columns=S.LEAD_COLUMNS).fillna("")


@pytest.mark.parametrize("answer, note, status, st", [("yes", "good quality", "confirmed", A.APPLIED),
                                                     ("no", "", "rejected", A.APPLIED),
                                                     ("not sure", "ask Ravi", "unverified_lead", A.NO_CHANGE)])
def test_supplier_answer(tmp_path, answer, note, status, st):
    a = answers(tmp_path, [("supplier:L1", answer, note), ("supplier:L9", "yes", "")])
    out = A.supplier_answers(a, LEADS).set_index("lead_id")
    assert out.at["L1", "status"] == status and out.at["L1", "owner_note"] == note
    rep = a.report().set_index("question_id")
    assert rep.at["supplier:L1", "status"] == st and rep.at["supplier:L9", "status"] == A.NOT_FOUND


@pytest.mark.parametrize("answer, active, st", [("yes", True, A.CONFIRMED), ("no", False, A.APPLIED), ("not sure", True, A.NO_CHANGE)])
def test_alert_answer(tmp_path, answer, active, st):
    S.add_alerts([dict(commodity="cashew", country="India", issue="DEMO: x", source_url="https://example.org/a",
                       reported_on="2026-11-01", is_demo="yes")], tmp_path, today=TODAY)
    aid = pd.read_csv(tmp_path / "country_alerts.csv", dtype=str).loc[0, "alert_id"]
    a = answers(tmp_path, [(f"alert:{aid}", answer, ""), ("alert:Anope", "no", "")])
    al = S.load_alerts(tmp_path, TODAY, a)
    assert bool(al.loc[0, "active"]) is active
    rep = a.report().set_index("question_id")
    assert rep.at[f"alert:{aid}", "status"] == st and rep.at["alert:Anope", "status"] == A.NOT_FOUND


@pytest.mark.timeout(180)
def test_dashboard_supplier_and_alert_loop(demo_run, tmp_path, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "out"
    shutil.copytree(demo_run["data"], data)
    shutil.copytree(demo_run["out"], out)
    (data / "owner_answers.csv").unlink(missing_ok=True)
    synthetic.generate(tmp_path / "gen", write_stock=True)
    shutil.copy(tmp_path / "gen" / "stock.csv", data / "stock.csv")
    synthetic.write_demo_sourcing(data)
    BO.run(data, out, TODAY)
    S.run(data, out, TODAY, answers=A.load(data))
    leads = pd.read_csv(data / "supplier_leads.csv", dtype=str).set_index("company")
    a_id, b_id = (leads.at[f"Demo Exporter {x}", "lead_id"] for x in "AB")
    c_id, d_id = leads.at["Demo Supplier C", "lead_id"], leads.at["Demo Exporter D", "lead_id"]
    alert_id = pd.read_csv(data / "country_alerts.csv", dtype=str).loc[0, "alert_id"]
    monkeypatch.setenv("DASH_DATA", str(data))
    monkeypatch.setenv("DASH_OUT", str(out))
    monkeypatch.setenv("DASH_TODAY", TODAY)
    from api.main import app
    c = TestClient(app)
    qs = {q["id"]: q for q in c.get("/api/questions").json()["questions"]}
    assert {f"supplier:{a_id}", f"supplier:{b_id}", f"alert:{alert_id}"} <= set(qs)
    assert f"supplier:{c_id}" not in qs and f"supplier:{d_id}" not in qs   # confirmed / rejected: never asked
    assert "Unverified: nobody has contacted them" in qs[f"supplier:{b_id}"]["detail"]
    assert qs[f"alert:{alert_id}"]["question"].startswith("Is this still a problem: DEMO:")

    assert c.post("/api/answers", json={"question_id": f"supplier:{b_id}", "answer": "yes", "note": "bought from them in 2025"}).status_code == 200
    assert c.post("/api/answers", json={"question_id": f"alert:{alert_id}", "answer": "no", "note": ""}).status_code == 200
    res = S.run(data, out, TODAY, answers=A.load(data))
    cash = res["summary"].set_index("product_id").loc["AN402"]
    assert cash["suppliers_and_leads"].startswith("#1 Demo Exporter B (India) [confirmed supplier]")
    assert cash["alerts"] == "" and "DEMO:" not in cash["suppliers_and_leads"]        # dismissed
    b_rows = res["lines"][res["lines"]["company"] == "Demo Exporter B"]
    assert len(b_rows) and (b_rows["owner_note"] == "bought from them in 2025").all()
    qs = {q["id"] for q in c.get("/api/questions").json()["questions"]}
    assert f"supplier:{b_id}" not in qs and f"alert:{alert_id}" not in qs and f"supplier:{a_id}" in qs
