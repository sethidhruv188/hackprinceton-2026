"""Owner answers feeding the engine (inventory/answers.py): every question type with yes / no / not sure,
latest answer wins, and bad rows are reported instead of ignored or crashing. Fake data only."""
import csv

import numpy as np
import pandas as pd
import pytest

from inventory import answers as A
from inventory import brand_orders as BO
from inventory import ingest
from inventory import stock_ingest as SI

TS = "2026-11-14T10:00:00+00:00"


def write(tmp_path, rows, header=True):
    with open(tmp_path / "owner_answers.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        if header:
            w.writerow(A.FIELDS)
        for r in rows:
            w.writerow(r)
    return A.load(tmp_path)


def status(ans, qid):
    rep = ans.report()
    row = rep[rep["question_id"] == qid]
    assert len(row) == 1, rep
    return row["status"].iloc[0], row["action"].iloc[0]


# ------------------------------------------------------------------ loading
def test_no_file_is_empty(tmp_path):
    a = A.load(tmp_path)
    assert a.rows.empty and a.report().empty


def test_latest_answer_wins_and_older_is_reported(tmp_path):
    a = write(tmp_path, [["hold:P1", "yes", "released", "2026-11-14T12:00:00+00:00"],     # later in time, earlier in file
                         ["hold:P1", "no", "still held", "2026-11-14T09:00:00+00:00"],
                         ["new:P2", "no", "", TS], ["new:P2", "yes", "", TS]])           # same time: file order decides
    assert a.get("hold", "P1")["answer"] == "yes"
    assert a.get("new", "P2")["answer"] == "yes"
    rep = a.report()
    assert (rep["status"] == A.SUPERSEDED).sum() == 2
    assert set(rep.loc[rep["status"] == A.SUPERSEDED, "answer"]) == {"no"}


def test_malformed_and_unknown_rows_are_reported_never_dropped(tmp_path):
    rows = [["hold:P1", "yes", "", TS],
            ["hold:P2", "yes", TS],                              # 3 fields
            ["hold:P3", "maybe", "", TS],                        # not yes/no/not sure
            ["hold:P4", "no", "", "last tuesday"],               # bad timestamp
            ["", "yes", "", TS],                                 # no question id
            ["mystery:P5", "yes", "", TS],                       # unknown type
            ["gap:P6:2025-13x", "yes", "", TS],                  # bad month
            ["stock:colour", "no", "", TS]]                      # unknown fixed key
    a = write(tmp_path, rows)
    rep = a.report()
    assert len(rep) == len(rows)                                 # every row is in the report
    assert (rep["status"] == A.MALFORMED).sum() == 4 and (rep["status"] == A.UNKNOWN).sum() == 3
    assert list(a.rows["question_id"]) == ["hold:P1"]


def test_unexpected_header_and_unreadable_content_do_not_crash(tmp_path):
    a = write(tmp_path, [["id", "ans", "note", "when"], ["new:P1", "no", "", TS]], header=False)
    rep = a.report()
    assert rep["status"].iloc[0] == A.MALFORMED and "header" in rep["action"].iloc[0]
    assert a.get("new", "P1")["answer"] == "no"                  # still read positionally
    (tmp_path / "owner_answers.csv").write_bytes(b"\xff\xfe\x00garbage\x00\n")
    A.load(tmp_path).report()                                    # no exception


def test_question_ids_parse():
    assert A.parse_question("gap:AB12 - OLD:2025-03") == ("gap", "AB12 - OLD", "2025-03")
    assert A.parse_question("code:XY9 - TBD") == ("code", "XY9 - TBD", None)
    assert A.parse_question("container:limits") == ("container", "limits", None)
    for bad in ("hold:", "gap:P1", "oneoff:P1:March", "nope", None, "container:size"):
        assert A.parse_question(bad) is None


# ------------------------------------------------------------------ holds and gaps
def hold_panel():
    months = pd.date_range("2026-01-01", "2026-10-01", freq="MS")
    rows = [dict(product_id="P1", month=m, units_sold=0.0 if m >= pd.Timestamp("2026-08-01") else 20.0,
                 status="fda_hold" if m >= pd.Timestamp("2026-08-01") else "sellable") for m in months]
    rows += [dict(product_id="P2", month=m, units_sold=10.0, status="sellable") for m in months]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("answer, held, st", [("yes", False, A.APPLIED), ("no", True, A.CONFIRMED), ("not sure", True, A.NO_CHANGE)])
def test_hold(tmp_path, answer, held, st):
    a = write(tmp_path, [["hold:P1", answer, "", TS]])
    holds, released = A.effective_holds(hold_panel(), a)
    assert ("P1" in holds) is held and ("P1" in released) is (not held)
    assert status(a, "hold:P1")[0] == st
    if held:
        assert holds["P1"] == "2026-08"


def test_hold_release_given_before_the_current_hold_does_not_count(tmp_path):
    a = write(tmp_path, [["hold:P1", "yes", "", "2026-05-01T00:00:00+00:00"], ["hold:P2", "yes", "", TS]])
    holds, released = A.effective_holds(hold_panel(), a)
    assert "P1" in holds and not released
    assert status(a, "hold:P1")[0] == A.NO_CHANGE and status(a, "hold:P2")[0] == A.NOT_FOUND


def suspected():
    return pd.DataFrame([dict(product_id="P2", start=pd.Timestamp("2026-03-01"), end=pd.Timestamp("2026-04-01"), months=2,
                              usual_monthly=10.0, reason="zeros")])


@pytest.mark.parametrize("answer, st, left, unavailable", [("yes", A.APPLIED, 0, 2), ("no", A.APPLIED, 0, 0),
                                                           ("not sure", A.NO_CHANGE, 1, 0)])
def test_gap(tmp_path, answer, st, left, unavailable):
    a = write(tmp_path, [["gap:P2:2026-03", answer, "", TS]])
    panel, sus = A.apply_gaps(hold_panel(), suspected(), a)
    assert len(sus) == left
    assert int(((panel["product_id"] == "P2") & (panel["status"] == "fda_hold")).sum()) == unavailable
    assert status(a, "gap:P2:2026-03")[0] == st


def test_gap_no_longer_suspected_is_reported(tmp_path):
    a = write(tmp_path, [["gap:P2:2025-01", "yes", "", TS]])
    A.apply_gaps(hold_panel(), suspected(), a)
    assert status(a, "gap:P2:2025-01")[0] == A.NOT_FOUND


@pytest.mark.parametrize("answer, capped, st", [("yes", True, A.CONFIRMED), ("no", False, A.APPLIED), ("not sure", True, A.NO_CHANGE)])
def test_one_off(tmp_path, answer, capped, st):
    spk = pd.DataFrame({"product_id": ["P1", "P1"], "month": pd.to_datetime(["2025-05-01", "2025-10-01"]),
                        "cause": ["one_off", "diwali"]})
    a = write(tmp_path, [["oneoff:P1:2025-05", answer, "", TS], ["oneoff:P1:2025-10", "no", "", TS]])
    real = A.real_one_offs(spk, a)
    assert (("P1", "2025-05") not in real) is capped
    assert status(a, "oneoff:P1:2025-05")[0] == st
    assert status(a, "oneoff:P1:2025-10")[0] == A.NOT_FOUND          # a Diwali spike, not a one-off


# ------------------------------------------------------------------ codes and products
@pytest.mark.parametrize("answer, merged, st", [("yes", True, A.NEEDS_INGEST), ("no", False, A.CONFIRMED),
                                                ("not sure", False, A.NO_CHANGE)])
def test_code_same_product(tmp_path, answer, merged, st):
    a = write(tmp_path, [["code:XY9 - TBD", answer, "", TS]])
    m = A.code_merges(a, ["XY9", "XY9 - TBD", "AB1"])
    assert (m == {"XY9 - TBD": "XY9"}) is merged
    assert status(a, "code:XY9 - TBD")[0] == st


def test_code_merge_is_applied_by_ingest(tmp_path):
    d = pd.DataFrame({"date": pd.to_datetime(["2025-01-05", "2025-02-05", "2025-03-05"]),
                      "raw_item": ["XY9", "XY9 - TBD", "XY9"],
                      "description": ["Brand Dal 4 lb X 10", "Brand Dal 2 lb X 20", "Brand Dal 4 lb X 10"], "qty": [5.0, 3.0, 4.0]})
    assert ingest.build_item_map(d).set_index("raw_item").at["XY9 - TBD", "product_id"] == "XY9 - TBD"   # packs differ: kept apart
    im = ingest.build_item_map(d, {"XY9 - TBD": "XY9"}).set_index("raw_item")
    assert im.at["XY9 - TBD", "product_id"] == "XY9" and im.at["XY9 - TBD", "merge_rule"] == "owner answer"
    assert im.at["XY9 - TBD", "to_current_cases"] == pytest.approx(1.0)                  # 2 lb x 20 = 4 lb x 10
    a = write(tmp_path, [["code:XY9 - TBD", "yes", "", TS]])
    A.code_merges(a, im.index, im["product_id"].to_dict())
    assert status(a, "code:XY9 - TBD")[0] == A.APPLIED


def test_code_with_no_current_code(tmp_path):
    im = pd.DataFrame({"raw_item": ["ZZ5 - OLD", "AB1", "QQ7 - OLD"], "product_id": ["ZZ5 - OLD", "AB1", "QQ7 - OLD"]})
    a = write(tmp_path, [["code:ZZ5 - OLD", "yes", "sold as AB1 now", TS], ["code:QQ7 - OLD", "no", "", TS],
                         ["code:ZZ6 - OLD", "yes", "", TS]])
    assert A.code_merges(a, im["raw_item"]) == {"ZZ5 - OLD": "AB1"}
    assert A.discontinued(a, im) == {"QQ7 - OLD"}
    assert status(a, "code:ZZ5 - OLD")[0] == A.NEEDS_INGEST
    assert status(a, "code:QQ7 - OLD")[0] == A.APPLIED and status(a, "code:ZZ6 - OLD")[0] == A.NOT_FOUND
    a2 = write(tmp_path, [["code:ZZ5 - OLD", "yes", "not sure which code", TS]])
    assert A.code_merges(a2, im["raw_item"]) == {} and status(a2, "code:ZZ5 - OLD")[0] == A.NOT_UNDERSTOOD


@pytest.mark.parametrize("answer, gone, st", [("yes", False, A.CONFIRMED), ("no", True, A.APPLIED), ("not sure", False, A.NO_CHANGE)])
def test_new_product(tmp_path, answer, gone, st):
    a = write(tmp_path, [["new:P9", answer, "about 20 a month", TS]])           # a note never sets a forecast
    assert ("P9" in A.discontinued(a)) is gone
    assert status(a, "new:P9")[0] == st


# ------------------------------------------------------------------ stock
@pytest.mark.parametrize("answer, warning, st", [("yes", A.ON_ORDER_YES, A.APPLIED), ("no", "", A.APPLIED),
                                                 ("not sure", BO.NO_ON_ORDER, A.NO_CHANGE)])
def test_stock_on_order(tmp_path, answer, warning, st):
    a = write(tmp_path, [["stock:on_order", answer, "", TS]])
    on_order, extra = A.stock_warnings(a)
    assert BO.order_warning(False, on_order, extra) == warning
    assert BO.order_warning(True, on_order, extra) == ""                       # an export with on-order needs no warning
    assert status(a, "stock:on_order")[0] == st


@pytest.mark.parametrize("answer, warned, st", [("yes", False, A.CONFIRMED), ("no", True, A.APPLIED), ("not sure", False, A.NO_CHANGE)])
def test_stock_units(tmp_path, answer, warned, st):
    a = write(tmp_path, [["stock:units", answer, "", TS]])
    _, extra = A.stock_warnings(a)
    assert (extra == A.UNITS_NO) is warned
    assert status(a, "stock:units")[0] == st


@pytest.mark.parametrize("answer, note, on_hand, flag, st", [
    ("yes", "", 900.0, "confirmed by owner", A.CONFIRMED),
    ("no", "it is 120 cases", 120.0, "over 36 months of cover: unit mismatch?", A.APPLIED),
    ("no", "wrong", 0.0, "over 36 months of cover: unit mismatch?", A.APPLIED),
    ("not sure", "", 900.0, "over 36 months of cover: unit mismatch?", A.NO_CHANGE)])
def test_units_per_product(tmp_path, answer, note, on_hand, flag, st):
    a = write(tmp_path, [["units:P1", answer, note, TS]])
    stock = pd.DataFrame({"product_id": ["P1", "P2"], "on_hand_cases": [900.0, 5.0]})
    cover = pd.DataFrame({"product_id": ["P1"], "on_hand_cases": [900.0], "flag": ["over 36 months of cover: unit mismatch?"]})
    s, notes = A.apply_stock_counts(stock, a)
    c = A.cover_flags(cover, a)
    assert s.set_index("product_id").at["P1", "on_hand_cases"] == on_hand and s.set_index("product_id").at["P2", "on_hand_cases"] == 5.0
    assert c["flag"].iloc[0] == flag and (("P1" in notes) is (answer == "no"))
    assert status(a, "units:P1")[0] == st


@pytest.mark.parametrize("answer, note, mapped, ignored, st", [
    ("yes", "it's AS1 in sales", True, False, A.APPLIED), ("yes", "some spice", False, False, A.NOT_UNDERSTOOD),
    ("no", "", False, True, A.APPLIED), ("not sure", "", False, False, A.NO_CHANGE)])
def test_inventory_code(tmp_path, answer, note, mapped, ignored, st):
    a = write(tmp_path, [["invcode:ZZ9", answer, note, TS]])
    code_map, ignore = A.inventory_codes(a, ["AS1", "MM1"])
    assert (code_map == {"ZZ9": "AS1"}) is mapped and (("ZZ9" in ignore) is ignored)
    assert status(a, "invcode:ZZ9")[0] == st


def test_inventory_code_answers_change_the_stock(tmp_path):
    inv = pd.DataFrame({"raw_item": ["AS1", "ZZ9", "PK1"], "description": ["Aara Cumin 200 Gm X 20", "Cumin 200 Gm X 20", "Film"],
                        "on_hand": [40.0, 20.0, 99.0], "on_order": np.nan, "held": np.nan, "expiry": pd.NaT, "cbm": np.nan,
                        "section": "Inventory Item", "rows": 1})
    im = pd.DataFrame({"raw_item": ["AS1"], "product_id": ["AS1"], "qty_to_cases": [1.0], "to_current_cases": [1.0],
                       "qty_recorded_in": ["cases"], "packs_per_case": [20.0], "description": ["Aara Cumin 200 Gm X 20"]})
    products = pd.DataFrame({"product_id": ["AS1"]})
    stock, detail = SI.map_to_products(inv, im, products, code_map={"ZZ9": "AS1"}, ignore={"PK1"})
    assert stock.set_index("product_id").at["AS1", "on_hand_cases"] == pytest.approx(3.0)   # (40 + 20 packs) / 20
    assert set(detail.loc[detail["match"] == "unmatched", "raw_item"]) == set()              # PK1 ignored, not unmatched


# ------------------------------------------------------------------ case sizes, containers, harvest
@pytest.mark.parametrize("note, cbm", [("0.045", 0.045), ("0.045 cbm", 0.045), ("40x30x25 cm", 0.03), ("16 x 12 x 10 in", 0.03146),
                                       ("about 2 m3", 2.0), ("big box", None), ("40x30x25", None), ("12", None)])
def test_parse_cbm(note, cbm):
    assert A.parse_cbm(note) == (pytest.approx(cbm, abs=1e-4) if cbm is not None else None)


def test_case_size(tmp_path):
    (tmp_path / "raw").mkdir()
    pd.DataFrame({"product_id": ["P3"], "cbm_per_case": [0.02]}).to_csv(tmp_path / "raw" / "case_cbm.csv", index=False)
    a = write(tmp_path, [["cbm:P1", "yes", "40x30x25 cm", TS], ["cbm:P2", "no", "no idea", TS],
                         ["cbm:P3", "yes", "0.05 cbm", TS], ["cbm:P4", "not sure", "", TS]])
    out = A.cbm_overrides(a, tmp_path / "raw" / "case_cbm.csv")
    assert out == {"P1": pytest.approx(0.03)}
    assert [status(a, f"cbm:P{i}")[0] for i in (1, 2, 3, 4)] == [A.APPLIED, A.NOT_UNDERSTOOD, A.CONFLICT, A.NO_CHANGE]
    from inventory import stock_plan as SP
    products = pd.DataFrame({"product_id": ["P1", "P3"], "category": ["spices", "spices"], "case_weight_kg": [10.0, 10.0]})
    t = SP.cbm_table(products, overrides_path=tmp_path / "raw" / "case_cbm.csv", answers=out).set_index("product_id")
    assert t.at["P1", "cbm_source"] == "owner answer" and t.at["P3", "cbm_source"] == "case_cbm.csv"   # the file wins


@pytest.mark.parametrize("answer, note, cbm, max_kg, st", [
    ("yes", "", 80.0, None, A.CONFIRMED), ("yes", "limit is 26,000 kg", 80.0, 26000.0, A.APPLIED),
    ("no", "more like 76 cbm and 25 t", 76.0, 25000.0, A.APPLIED), ("no", "smaller", 80.0, None, A.NOT_UNDERSTOOD),
    ("not sure", "", 80.0, None, A.NO_CHANGE)])
def test_container_limits(tmp_path, answer, note, cbm, max_kg, st):
    a = write(tmp_path, [["container:limits", answer, note, TS]])
    cfg = A.container_config(dict(cbm=80.0, usable=1.0, max_kg=None), a)
    assert cfg["cbm"] == cbm and cfg["max_kg"] == max_kg
    assert status(a, "container:limits")[0] == st


def rules():
    return pd.DataFrame([dict(rule_id="rice", category="rice_flour", match="rice", exclude="", order_months="Jan",
                              arrival_months="Apr-May", cover_months="12", confirmed="yes", note=""),
                         dict(rule_id="cumin", category="spices", match="cumin", exclude="", order_months="",
                              arrival_months="", cover_months="12", confirmed="no", note="")])


@pytest.mark.parametrize("answer, note, st, confirmed, months", [
    ("yes", "order in January, arrives April to May", A.APPLIED, "yes", ("Jan", "Apr-May")),
    ("yes", "every year", A.NOT_UNDERSTOOD, "no", ("", "")),
    ("no", "", A.APPLIED, None, None),
    ("not sure", "", A.NO_CHANGE, "no", ("", ""))])
def test_harvest(tmp_path, answer, note, st, confirmed, months):
    a = write(tmp_path, [["harvest:cumin", answer, note, TS], ["harvest:rice", "no", "", TS], ["harvest:mango", "yes", "", TS]])
    r = A.harvest_rules(rules(), a).set_index("rule_id")
    if confirmed is None:
        assert "cumin" not in r.index
    else:
        assert r.at["cumin", "confirmed"] == confirmed and (r.at["cumin", "order_months"], r.at["cumin", "arrival_months"]) == months
    assert r.at["rice", "confirmed"] == "yes"                                    # config/harvest.csv wins
    assert status(a, "harvest:cumin")[0] == st
    assert status(a, "harvest:rice")[0] == A.CONFLICT and status(a, "harvest:mango")[0] == A.NOT_FOUND


# ------------------------------------------------------------------ report
def test_report_lists_unused_and_not_sure_answers(tmp_path):
    a = write(tmp_path, [["cbm:P1", "yes", "0.04", TS], ["new:P2", "not sure", "", TS]])
    a.skip(["cbm"], "only used when ordering against stock")
    rep = a.report().set_index("question_id")
    assert rep.at["cbm:P1", "status"] == A.NO_CHANGE and "stock" in rep.at["cbm:P1", "action"]
    assert rep.at["new:P2", "status"] == A.NO_CHANGE and "not sure" in rep.at["new:P2", "action"]
    assert "2 answer rows" in A.summary(a.report())
