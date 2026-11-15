"""The overview and settings endpoints (api/agent_routes.py) on the synthetic demo; no real data, no server."""
import json

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.timeout(300)       # demo_full builds the synthetic pipeline once per session


@pytest.fixture
def client(copy_demo, monkeypatch):
    monkeypatch.setenv("DASH_DATA", str(copy_demo["data"]))
    monkeypatch.setenv("DASH_OUT", str(copy_demo["out"]))
    monkeypatch.delenv("DASH_TODAY", raising=False)
    from api.main import app
    return TestClient(app), copy_demo


def test_settings_default_to_the_run_date_and_time_travel(client):
    c, _ = client
    s = c.get("/api/settings").json()
    assert s["run_date"] == "2026-11-14" and s["as_of"] == "2026-11-14" and s["as_of_set"] is False
    assert s["mode"] == "custom"                                  # a temp folder is neither demo nor real
    s = c.post("/api/settings", json={"as_of": "2027-02-01"}).json()
    assert s["as_of"] == "2027-02-01" and s["as_of_set"] is True
    assert c.get("/api/overview").json()["settings"]["as_of"] == "2027-02-01"
    s = c.post("/api/settings", json={"as_of": ""}).json()        # back to the engine run date
    assert s["as_of"] == "2026-11-14" and s["as_of_set"] is False


def test_settings_reject_bad_input(client):
    c, _ = client
    assert c.post("/api/settings", json={"mode": "production"}).status_code == 422
    assert c.post("/api/settings", json={"as_of": "not a date"}).status_code == 422


def test_overview_today_figures(client):
    c, _ = client
    o = c.get("/api/overview").json()
    k = o["counts"]
    assert set(k) == {"order_now", "this_month", "coming_up", "too_late", "order_now_cases"}
    assert all(isinstance(v, int) and v >= 0 for v in k.values())
    assert (k["order_now"] == 0) == (k["order_now_cases"] == 0)
    d = o["diwali"]
    assert d and d["order_by_safe"] <= d["order_by_latest"] and d["days_to_safe"] <= d["days_to_latest"]
    assert d["order_by_latest"] >= "2026-11-14"
    h = o["next_harvest"]
    assert h and h["order_by"] >= "2026-11-14" and h["arrival_earliest"] <= h["arrival_latest"]
    assert o["holds"] >= 1                                        # the synthetic FDA-hold product


def test_overview_alerts_are_labeled_with_sources(client):
    c, _ = client
    alerts = c.get("/api/overview").json()["alerts"]
    assert alerts, "the demo has an active DEMO alert"
    assert all(a["label"] in ("demo", "real news") and a["source_url"].startswith("http") for a in alerts)


def test_overview_replay_falls_back_to_the_simulation_summary(client):
    c, demo = client
    assert not (demo["out"] / "forecaster_tournament.json").exists()
    r = c.get("/api/overview").json()["replay"]
    assert r["source"] == "simulation_summary.json"
    assert {"model", "avg"} <= set(r["overall"]) and r["verdict"]


def test_overview_replay_reads_the_forecaster_test(client):
    c, demo = client
    row = lambda apr, aug, diw, stock: dict(points_vs_avg_apr_jul=apr, points_vs_avg_aug_oct=aug,
                                            fill_pre_diwali_cushion3=diw, avg_stock_cushion3_aug_oct=stock)
    table = {"avg": row(0.0, 0.0, 84.6, 1000.0), "blend_0.5": row(2.06, 0.23, 84.84, 1053.0), "ml": row(1.0, -0.5, 84.0, 1100.0)}
    (demo["out"] / "forecaster_tournament.json").write_text(json.dumps(dict(
        chosen="blend_0.5", table=table, products=12, selection_period="2026-04..2026-07",
        confirmation_period="2026-08..2026-10", pre_diwali_month=["2026-10"])))
    r = c.get("/api/overview").json()["replay"]
    assert r["source"] == "forecaster_tournament.json" and r["chosen"] == "blend_0.5"
    assert r["selection"] == {"period": "2026-04..2026-07", "points_vs_avg": 2.06}
    assert r["confirmation"]["points_vs_avg"] == 0.23
    assert r["pre_diwali"] == {"months": ["2026-10"], "chosen": 84.84, "avg": 84.6}
    assert r["stock"]["pct_more"] == pytest.approx(5.3)
    assert "seasonal" in r["not_completed"] and "blend_0.5" not in r["not_completed"]
