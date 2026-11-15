"""The answer loop end to end on demo data: the owner answers a question on the dashboard, the engine runs
again, and the result changes (a released hold is no longer a hold and is ordered again). Bad rows in the
same file are reported and don't stop the run."""
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.timeout(180)          # shares the demo_run pipeline (about a minute) and re-runs the engine


def test_released_hold_is_no_longer_a_hold(demo_run, tmp_path, monkeypatch):
    for k in ("data", "out", "models"):                                   # a private copy: other tests keep theirs
        shutil.copytree(demo_run[k], tmp_path / k)
    data, out, models = tmp_path / "data", tmp_path / "out", tmp_path / "models"
    (data / "owner_answers.csv").unlink(missing_ok=True)         # other tests answer in the shared demo folder
    monkeypatch.setenv("DASH_DATA", str(data))
    monkeypatch.setenv("DASH_OUT", str(out))
    monkeypatch.setenv("DASH_TODAY", "2026-11-14")
    from api.main import app
    client = TestClient(app)

    # before: the demo's FDA-hold product is on hold, asked about, left out of the plan
    held = client.get("/api/search?q=schezwan").json()["products"][0]["product_id"]
    assert client.get(f"/api/products/{held}").json()["on_hold_now"]
    assert client.get("/api/summary").json()["holds"] == 1
    assert any(q["id"] == f"hold:{held}" for q in client.get("/api/questions").json()["questions"])
    assert held not in set(pd.read_csv(out / "order_plan.csv")["product_id"])

    # the owner answers on the dashboard; a hand-edited bad row and an unknown question sit in the same file
    r = client.post("/api/answers", json={"question_id": f"hold:{held}", "answer": "yes", "note": "released last week"})
    assert r.status_code == 200
    with open(data / "owner_answers.csv", "a", newline="") as fh:
        fh.write("not-a-question:X,yes,,2026-11-14T10:00:00+00:00\n")
        fh.write("hold:P002,perhaps\n")

    # the engine runs again (reusing the saved simulation)
    run = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "run.py"), "--source", "synthetic", "--data", str(data),
                          "--out", str(out), "--models", str(models), "--today", "2026-11-14", "--sim-from", "2026-06",
                          "--skip-sim"], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-2000:]

    # after: no longer a hold anywhere, planned and counted in its brand order
    assert held not in set(pd.read_csv(out / "holds_now.csv")["product_id"])
    assert not client.get(f"/api/products/{held}").json()["on_hold_now"]
    assert client.get("/api/summary").json()["holds"] == 0
    assert not any(q["id"] == f"hold:{held}" for q in client.get("/api/questions").json()["questions"])
    assert held in set(pd.read_csv(out / "order_plan.csv")["product_id"])
    lines = pd.read_csv(out / "brand_order_lines.csv")
    mine = lines[lines["product_id"] == held]
    assert len(mine) and not mine["on_hold"].any() and mine["cases"].sum() > 0

    # every answer row is accounted for, the bad ones included
    rep = pd.read_csv(out / "answers_applied.csv").set_index("question_id")
    assert rep.at[f"hold:{held}", "status"] == "applied"
    assert rep.at["not-a-question:X", "status"] == "unknown question"
    assert rep.at["hold:P002", "status"] == "malformed row"
    assert "9. Owner answers" in (out / "backtest_report.md").read_text()
