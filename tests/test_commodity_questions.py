"""The commodity:PID question (which crop is this product?) and the harvest questions on the dashboard's
"Questions for you" list. Fake / demo data only."""
import csv
import shutil

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from inventory import answers as A
from inventory import brand_orders as BO
from inventory import harvest as H

TS = "2026-11-10T09:00:00+00:00"


def answers(tmp_path, rows):
    with open(tmp_path / "owner_answers.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(A.FIELDS)
        w.writerows([[q, a, n, TS] for q, a, n in rows])
    return A.load(tmp_path)


@pytest.mark.parametrize("answer, note, result, st", [
    ("yes", "toor", "toor", A.APPLIED),
    ("yes", "it's pigeon pea", "toor", A.APPLIED),                # a keyword of the commodity
    ("yes", "sona masoori", "sona_masoori", A.APPLIED),          # id written with spaces
    ("yes", "some crop", "missing", A.NOT_UNDERSTOOD),
    ("no", "", None, A.APPLIED),
    ("not sure", "", "missing", A.NO_CHANGE)])
def test_commodity_answer(tmp_path, answer, note, result, st):
    a = answers(tmp_path, [("commodity:AD298", answer, note)])
    out = A.commodity_overrides(a, H.load_map())
    assert out.get("AD298", "missing") == result
    assert a.report().set_index("question_id").at["commodity:AD298", "status"] == st


def test_commodity_question_id_parses():
    assert A.parse_question("commodity:AD298") == ("commodity", "AD298", None)


def test_answer_maps_the_product_in_prepare(tmp_path):
    p = pd.DataFrame({"product_id": ["AD298"], "name": ["Aara Chora Dal 2 lb X 20"], "category": ["lentils_beans"]})
    prep = H.prepare(p, answers(tmp_path, [("commodity:AD298", "yes", "black eye beans")]), "2026-11-14",
                     calendar=H.load_calendar(tmp_path / "none.csv"))
    assert prep.unmapped.empty and prep.mapped.set_index("product_id").at["AD298", "commodity"] == "black_eye"


@pytest.mark.timeout(180)
def test_dashboard_lists_and_resolves_harvest_questions(demo_run, tmp_path, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "out"
    shutil.copytree(demo_run["data"], data)
    shutil.copytree(demo_run["out"], out)
    (data / "owner_answers.csv").unlink(missing_ok=True)         # other tests answer in the shared demo folder
    cal = tmp_path / "harvest_calendar.csv"
    pd.DataFrame([dict(commodity="toor", source_country="India", harvest_start_month="Dec", harvest_end_month="Mar",
                       source_url="https://example.org/toor", confidence="high", confirmed_by_owner="no"),
                  dict(commodity="almond", source_country="USA", source_url="", confirmed_by_owner="no")]
                 ).reindex(columns=H.CAL_COLUMNS).fillna("").to_csv(cal, index=False)
    BO.run(data, out, "2026-11-14", harvest_calendar=cal)
    monkeypatch.setenv("DASH_DATA", str(data))
    monkeypatch.setenv("DASH_OUT", str(out))
    monkeypatch.setenv("DASH_TODAY", "2026-11-14")
    from api.main import app
    c = TestClient(app)
    qs = {q["id"]: q for q in c.get("/api/questions").json()["questions"]}
    assert "harvest:toor_india" in qs and "harvest Dec-Mar" in qs["harvest:toor_india"]["question"]
    assert "example.org/toor" in qs["harvest:toor_india"]["detail"]
    assert "Which months" in qs["harvest:almond_usa"]["question"]              # no cited months: the owner gives them
    assert {"commodity:AD299", "commodity:AN499"} <= set(qs)                   # mixed pack and no keyword (demo)
    assert qs["commodity:AD299"]["type"] == "Which crop"

    # the owner answers on the dashboard; the engine step re-runs; the questions are resolved
    assert c.post("/api/answers", json={"question_id": "commodity:AD299", "answer": "yes", "note": "toor"}).status_code == 200
    assert c.post("/api/answers", json={"question_id": "harvest:almond_usa", "answer": "yes",
                                        "note": "harvest is August to October"}).status_code == 200
    BO.run(data, out, "2026-11-14", harvest_calendar=cal)
    used = pd.read_csv(out / "commodity_map_used.csv").set_index("product_id")
    assert used.at["AD299", "commodity"] == "toor"
    qs = {q["id"]: q for q in c.get("/api/questions").json()["questions"]}
    assert "commodity:AD299" not in qs and "harvest:almond_usa" not in qs and "harvest:toor_india" in qs
    plan = pd.read_csv(out / "harvest_plan.csv").set_index("rule_id")
    assert plan.at["almond_usa", "harvest_window"] == "Aug-Oct" and plan.at["almond_usa", "rule_source"] == "calendar (confirmed)"
