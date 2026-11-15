"""The eval harness (agent/evals.py) itself, with scripted fake models: a model that uses the right tools and quotes
them passes all 12 cases; one that invents numbers is caught. No network."""
import pytest

from agent import evals as E
from fake_llm import ScriptedModel, call, say

ROUTES = [  # keyword in the question -> the tool call a good model makes
    ("Aara", call("what_to_order", brand="Aara")), ("Sunrise", call("what_to_order", brand="Sunrise")),
    ("cashew flagged", call("explain_product", product="cashew")), ("toor for the next harvest", call("harvest_plan", commodity="toor")),
    ("where else", call("alternative_sources", commodity="cashew")),
    ("remember that", call("edit_file", file_path="/memories/AGENTS.md", old_string="one dated line each.\n",
                           new_string="one dated line each.\n- 2026-11-14: Diwali is our biggest season.\n")),
    ("instead of 40", call("log_override", product="Sunrise Toor Dal", qty=50, reason="restaurant order")),
    ("forecast", call("forecast_quality")), ("questions for me", call("pending_questions")),
    ("of moong dal", call("explain_product", product="moong dal")), ("Diwali next year", call("festival_window")),
    ("at risk", call("products_at_risk")),
]


def good(messages, tools):
    last = messages[-1]
    if getattr(last, "type", "") == "tool":
        return say(last.content.splitlines()[0][:300])                 # quotes the tool: every number is grounded
    text = last.content
    if text == "how many":
        return say("How many of which product?")
    for key, c in ROUTES:
        if key in text:
            return say("", dict(c, id=c["id"] + "x" + str(len(messages))))
    return say("I don't know.")


def bad(messages, tools):
    if getattr(messages[-1], "type", "") == "tool":
        return say("Order 98765 cases by 2031-02-30.")                   # invented numbers
    return good(messages, tools)


def test_numbers_check():
    assert E.invented("Order 1,001 cases by 2026-11-30", ["... 1001 cases ... order by 2026-11-30"]) == []
    assert E.invented("Order 50 cases", ["Order 40 cases"], ) == ["50"]
    assert E.invented("about 3.5 months", ["3.5 months"]) == []


def test_good_model_passes_all_cases(copy_demo):
    res = E.run_evals(model=ScriptedModel(respond=good), data=copy_demo["data"], out=copy_demo["out"])
    assert len(res) == 12
    failed = res[~(res["tool_ok"] & res["args_ok"] & res["numbers_ok"])]
    assert failed.empty, failed[["id", "tools", "invented", "reply"]].to_string()
    assert E.summary(res).startswith("12 cases: right tool 12/12, right arguments 12/12, no invented numbers 12/12, format 12/12")
    assert (res["error"] == "").all() and (res["llm_calls"] > 0).all() and "seconds" in res
    assert not (copy_demo["data"] / "agent_memory").exists()          # evals ran on a scratch copy


def test_list_numbering_is_not_a_number():
    assert E.invented("1. Containers: 80 CBM\n2. Case size?\n#3 Lotus", ["80 CBM"]) == []


def markdown_model(messages, tools):
    if getattr(messages[-1], "type", "") == "tool":
        return say("**Order now**: " + messages[-1].content.splitlines()[0][:100])
    return good(messages, tools)


def test_format_column_and_errors(copy_demo):
    res = E.run_evals(model=ScriptedModel(respond=markdown_model), cases=E.CASES[1:2], data=copy_demo["data"], out=copy_demo["out"])
    assert not res["format_ok"].iloc[0] and "**" not in res["reply"].iloc[0]          # sent stripped, scored on the raw
    def boom(messages, tools):
        raise RuntimeError("Error code: 429 - rate limited upstream")
    res = E.run_evals(model=ScriptedModel(respond=boom), cases=E.CASES[:1], data=copy_demo["data"], out=copy_demo["out"],
                      report=copy_demo["out"] / "ev.csv", price=(1e-6, 2e-6), budget=0.0)
    assert "429" in res["error"].iloc[0] and (copy_demo["out"] / "ev_log.csv").exists()


def test_budget_stops_remaining_cases(copy_demo):
    res = E.run_evals(model=ScriptedModel(respond=good), cases=E.CASES[:3], data=copy_demo["data"], out=copy_demo["out"],
                      price=(1.0, 1.0), budget=0.5)                                     # absurd price: over budget after one case
    assert list(res["skipped"]) == [False, True, True] and "skipped" in E.summary(res)


def test_invented_numbers_are_caught(copy_demo):
    res = E.run_evals(model=ScriptedModel(respond=bad), cases=E.CASES[:3], data=copy_demo["data"], out=copy_demo["out"])
    assert res["tool_ok"].all() and not res["numbers_ok"].any()
    assert all("98765" in i for i in res["invented"])
