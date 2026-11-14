"""Every API endpoint on synthetic data (generated into a temp folder; no real data)."""
import os
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

# The demo_run session fixture (conftest.py) runs the real pipeline once (about 1 minute with its short simulation),
# and pytest-timeout charges fixture setup to the first test that uses it, so this module gets a longer limit than
# the 60 s default in pytest.ini.
pytestmark = pytest.mark.timeout(180)


@pytest.fixture(scope="session")
def client(demo_run):
    # demo_run (tests/conftest.py) runs the pipeline once on synthetic data. In-process TestClient: no server, no port.
    data, out = demo_run["data"], demo_run["out"]
    assert not (data / "stock.csv").exists()                       # no stock data is ever created
    os.environ.update(DASH_DATA=str(data), DASH_OUT=str(out), DASH_TODAY="2026-11-14")
    from api.main import app
    return TestClient(app)


def test_summary(client):
    s = client.get("/api/summary").json()
    assert s["today"] == "2026-11-14" and s["gross_need"] is True
    assert "stock" in s["gross_need_note"].lower()
    assert len(s["next_orders"]) <= 3 and all(o["status"] != "Too late for full lead time: expedite" for o in s["next_orders"])
    assert s["holds"] >= 1                                          # the synthetic FDA-hold product


def test_brands_and_detail(client):
    b = client.get("/api/brands").json()["brands"]
    names = {x["brand"] for x in b}
    assert {"Sunrise", "Lotus", "Peacock", "Monsoon", "Banyan"} <= names
    dates = [x["next_order"]["order_by_safe"] for x in b if x["next_order"]]
    assert dates == sorted(dates)
    d = client.get("/api/brands/Sunrise").json()
    o = d["orders"][0]
    live = [l for l in o["lines"] if not l["on_hold"]]
    assert o["cases"] == sum(l["cases"] for l in live)              # holds are not in the total
    assert client.get("/api/brands/NoSuchBrand").status_code == 404


def test_product(client):
    pid = client.get("/api/brands/Sunrise").json()["orders"][0]["lines"][0]["product_id"]
    p = client.get(f"/api/products/{pid}").json()
    assert len(p["history"]) == 28 and len(p["forecast"]) == 12
    assert all(f["high"] >= f["likely"] for f in p["forecast"])
    assert p["reorder"]["reorder_point"] > 0 and p["diwali_dates"]
    assert client.get("/api/products/NOPE").status_code == 404
    held = client.get("/api/search?q=schezwan").json()["products"][0]["product_id"]
    h = client.get(f"/api/products/{held}").json()
    assert h["on_hold_now"] and h["hold_months"]


def test_diwali(client):
    d = client.get("/api/diwali").json()
    assert len(d["monthly"]) == 28
    years = [s["year"] for s in d["seasons"]]
    assert 2027 in years
    s27 = next(s for s in d["seasons"] if s["year"] == 2027)
    assert s27["in_warehouse_by"] == "2027-09-01" and s27["order_by_safe"] == "2027-04-24"


def test_questions_and_answers(client):
    q = client.get("/api/questions").json()
    assert q["questions"] and "On hold" in q["counts"]
    impacts = [x["impact_cases"] for x in q["questions"]]
    assert impacts == sorted(impacts, reverse=True)
    qid = q["questions"][0]["id"]
    r = client.post("/api/answers", json={"question_id": qid, "answer": "yes", "note": "released last week"})
    assert r.status_code == 200 and r.json()["answer"] == "yes"
    assert client.post("/api/answers", json={"question_id": qid, "answer": "maybe"}).status_code == 422
    again = client.get("/api/questions").json()
    assert next(x for x in again["questions"] if x["id"] == qid)["answer"] == "yes"
    saved = pd.read_csv(Path(os.environ["DASH_DATA"]) / "owner_answers.csv")
    assert list(saved.columns) == ["question_id", "answer", "note", "timestamp"] and len(saved) == 1


def test_forecast_quality(client):
    f = client.get("/api/forecast-quality").json()
    assert {"model", "avg"} == set(f["overall"]) and 0 <= f["overall"]["model"]["fill_rate_%"] <= 100
