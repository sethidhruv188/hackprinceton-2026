"""Owner-taught demand adjustments (inventory/adjustments.py, the agent tools and the planning layer in
brand_orders.run) on the demo: parse, propose / confirm / cancel, applied only when confirmed and not expired, every
affected order line shows the reason, owner-only, and listed on the Activity page. Demo data, no network."""
import pandas as pd
import pytest

from agent import tools as T
from agent.context import use
from inventory import adjustments as ADJ
from inventory import brand_orders as BO

TODAY = "2026-11-14"
MONTHS_12 = "2026-12..2027-11"


def tool(name, args, d, role="owner", today=TODAY):
    with use(d["data"], d["out"], today, role=role):
        return getattr(T, name).invoke(args)


def lines(d):
    return pd.read_csv(d["out"] / "brand_order_lines.csv", dtype={"product_id": str})


def a_product_with_an_order(d):
    """A product with a normal (not harvest, not held, not pulled forward) order line in the demo plan."""
    l = lines(d)
    l = l[~l["harvest"].astype(bool) & ~l["on_hold"].astype(bool) & ~l["pulled_forward"].astype(bool)]
    return l["product_id"].iloc[0]


# ------------------------------------------------------------------ parsing
def test_parse_effect():
    assert ADJ.parse_effect("+30%") == ("pct", 30.0)
    assert ADJ.parse_effect("-20 %") == ("pct", -20.0)
    assert ADJ.parse_effect("+50 cases") == ("cases", 50.0)
    assert ADJ.parse_effect("40") == ("cases", 40.0)
    for bad in ("lots", "0%", "-150%", "+900%"):
        with pytest.raises(ValueError):
            ADJ.parse_effect(bad)


def test_parse_months_next_occurrence_and_ranges():
    assert ADJ.parse_months("December", TODAY) == ["2026-12"]
    assert ADJ.parse_months("Dec-Jan", TODAY) == ["2026-12", "2027-01"]
    assert ADJ.parse_months("Oct", TODAY) == ["2027-10"]                 # October 2026 is over: next October
    assert ADJ.parse_months("2026-12..2027-02", TODAY) == ["2026-12", "2027-01", "2027-02"]
    assert ADJ.parse_months("Dec, Feb", TODAY) == ["2026-12", "2027-02"]
    with pytest.raises(ValueError):
        ADJ.parse_months("2026-01..2027-06", TODAY)                     # more than 12 months
    with pytest.raises(ValueError):
        ADJ.parse_months("someday", TODAY)


def test_label():
    row = dict(effect_kind="pct", effect_value="30", reason="promo", months="2026-12")
    assert ADJ.label(row) == "+30% owner promo, Dec"
    row = dict(effect_kind="cases", effect_value="50", reason="big order", months="2026-12;2027-01")
    assert ADJ.label(row) == "+50 cases/month owner big order, Dec-Jan"


# ------------------------------------------------------------------ the planning layer
def test_apply_pct_and_cases_only_inside_scope_and_months():
    products = pd.DataFrame({"product_id": ["A", "B", "C"], "name": ["Moong Dal 2lb", "Moong Dal 4lb", "Toor Dal"]})
    cmap = pd.DataFrame({"product_id": ["A", "B", "C"], "commodity": ["moong", "moong", "toor"]})
    fc = pd.DataFrame({"product_id": ["A", "B", "C", "A"], "month": ["2026-12", "2026-12", "2026-12", "2027-01"],
                       "likely": [10.0, 30.0, 50.0, 10.0], "high": [12.0, 36.0, 60.0, 12.0]})
    adj = pd.DataFrame([dict(adj_id="adj1", scope="commodity", target="moong", months="2026-12", effect_kind="pct",
                             effect_value="30", reason="promo")])
    out, notes, rep = ADJ.apply(fc, adj, products, None, cmap)
    assert out["likely"].tolist() == pytest.approx([13.0, 39.0, 50.0, 10.0])          # toor and January untouched
    assert out["high"].tolist() == pytest.approx([15.6, 46.8, 60.0, 12.0])
    assert notes == {"A": "+30% owner promo, Dec", "B": "+30% owner promo, Dec"}
    assert rep.iloc[0][["before", "after"]].tolist() == [40.0, 52.0]
    assert fc["likely"].tolist() == [10.0, 30.0, 50.0, 10.0]                         # input not modified
    adj2 = adj.assign(effect_kind="cases", effect_value="40")                          # +40 cases split 1:3
    out2, _, _ = ADJ.apply(fc, adj2, products, None, cmap)
    assert out2["likely"].tolist() == pytest.approx([20.0, 60.0, 50.0, 10.0])
    adj3 = adj.assign(effect_kind="cases", effect_value="-500")                        # never below zero
    assert (ADJ.apply(fc, adj3, products, None, cmap)[0]["likely"] >= 0).all()


def test_proposed_does_nothing_confirmed_applies_cancelled_reverts(copy_demo):
    d = copy_demo
    pid = a_product_with_an_order(d)
    before = lines(d)
    fc0 = pd.read_csv(d["out"] / "forecast.csv")
    cases0 = before.loc[before["product_id"] == pid, "cases"].sum()
    _, products, _ = BO.load(d["data"])
    r = ADJ.propose(d["data"], "product", pid, MONTHS_12, "+100%", "promo", TODAY, products, channel="dashboard")
    adj_id = r["row"]["adj_id"]
    assert r["product_ids"] == [pid] and ADJ.load(d["data"])["status"].tolist() == ["proposed"]
    BO.run(d["data"], d["out"], TODAY)
    assert lines(d).equals(before)                                                    # proposed: plan unchanged
    ADJ.confirm(d["data"], adj_id)
    BO.run(d["data"], d["out"], TODAY)
    after = lines(d)
    mine = after[after["product_id"] == pid]
    assert mine["cases"].sum() > cases0                                              # more demand, more cases
    assert (mine["owner_adjustment"] == "+100% owner promo, Dec-Nov").all()
    assert (after.loc[after["product_id"] != pid, "owner_adjustment"].fillna("") == "").all()
    assert (d["out"] / "adjustments_applied.csv").exists()
    pd.testing.assert_frame_equal(pd.read_csv(d["out"] / "forecast.csv"), fc0)    # the model's forecast file is untouched
    ADJ.cancel(d["data"], adj_id)
    BO.run(d["data"], d["out"], TODAY)
    assert lines(d)["cases"].sum() == before["cases"].sum() and not (d["out"] / "adjustments_applied.csv").exists()
    with pytest.raises(ValueError):
        ADJ.confirm(d["data"], adj_id)                                               # cancelled can't be confirmed


def test_adjustments_expire_after_their_months(copy_demo):
    d = copy_demo
    _, products, _ = BO.load(d["data"])
    adj_id = ADJ.propose(d["data"], "product", a_product_with_an_order(d), "Dec", "+30%", "promo", TODAY, products)["row"]["adj_id"]
    ADJ.confirm(d["data"], adj_id)
    assert ADJ.active(d["data"], TODAY)["adj_id"].tolist() == [adj_id]
    assert ADJ.active(d["data"], "2027-01-05").empty
    assert ADJ.with_state(ADJ.load(d["data"]), "2027-01-05")["state"].tolist() == ["expired"]
    with pytest.raises(ValueError):                                                  # can't propose months already over
        ADJ.propose(d["data"], "product", a_product_with_an_order(d), "2026-10", "+30%", "promo", TODAY, products)


def test_scope_by_commodity_and_brand_resolves_on_the_demo(copy_demo):
    d = copy_demo
    with use(d["data"], d["out"], TODAY, role="owner"):
        products, bm, cmap = T._adj_lookups()
    assert ADJ.resolve("commodity", "moong", products, bm, cmap)                       # the demo moong product
    assert len(ADJ.resolve("brand", "sunrise", products, bm, cmap)) > 1
    assert ADJ.resolve("product", "zzzz", products, bm, cmap) == []


# ------------------------------------------------------------------ agent tools
def test_tools_read_back_then_apply_only_on_confirm(copy_demo):
    d = copy_demo
    s = tool("propose_adjustment", dict(scope="commodity", target="moong", months="December", effect="+30%",
                                        reason="promo"), d)
    assert s.startswith("Proposed adj1 (not saved to the plan yet): +30% owner promo, Dec") and "confirm_adjustment('adj1')" in s
    assert "Forecast for those months:" in s
    assert ADJ.load(d["data"]).iloc[0][["status", "channel"]].tolist() == ["proposed", "imessage"]
    assert "proposed" in tool("list_adjustments", {}, d)
    c = tool("confirm_adjustment", {"adj_id": "adj1"}, d)
    assert c.startswith("Confirmed adj1: +30% owner promo, Dec. The plan is updated.")
    assert "active" in tool("list_adjustments", {}, d)
    assert tool("cancel_adjustment", {"adj_id": "adj1"}, d).startswith("Cancelled adj1")
    assert "Not confirmed: adj1 is cancelled" in tool("confirm_adjustment", {"adj_id": "adj1"}, d)
    assert "Not saved: no products match" in tool("propose_adjustment", dict(scope="brand", target="zzz", months="Dec",
                                                                              effect="+10%", reason="x"), d)
    assert "Not saved: scope must be" in tool("propose_adjustment", dict(scope="aisle", target="moong", months="Dec",
                                                                          effect="+10%", reason="x"), d)


def test_adjustment_tools_are_owner_only(copy_demo):
    d = copy_demo
    args = dict(scope="commodity", target="moong", months="Dec", effect="+30%", reason="promo")
    assert tool("propose_adjustment", args, d, role="guest") == T.OWNER_ONLY
    assert tool("confirm_adjustment", {"adj_id": "adj1"}, d, role="guest") == T.OWNER_ONLY
    assert tool("cancel_adjustment", {"adj_id": "adj1"}, d, role="guest") == T.OWNER_ONLY
    assert tool("list_adjustments", {}, d, role="guest") == T.OWNER_ONLY
    assert not (d["data"] / ADJ.FILE).exists()
    names = {t.name for t in T.for_role(T.ALL_TOOLS, "guest")}
    assert not names & {"propose_adjustment", "confirm_adjustment", "cancel_adjustment"}


def test_activity_lists_adjustments(copy_demo):
    d = copy_demo
    tool("propose_adjustment", dict(scope="commodity", target="moong", months="Dec", effect="+30%", reason="promo"), d)
    tool("confirm_adjustment", {"adj_id": "adj1"}, d)
    from api import agent_routes as AR
    with use(d["data"], d["out"], TODAY, role="owner"):
        a = AR.activity()
    assert a["adjustments"][0]["adj_id"] == "adj1" and a["adjustments"][0]["state"] == "active"
    assert a["adjustments"][0]["label"] == "+30% owner promo, Dec"
    assert {"Adjustment proposed", "Adjustment confirmed"} <= set(a["counts"])
