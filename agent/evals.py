"""Evals: 12 realistic owner questions on the demo, scored on
  tool     the expected tool was called (by the main agent or a sub-agent)
  args     with the right arguments
  numbers  no invented numbers: every number in the reply appears in a tool output or the question
Runs on a scratch copy of data_demo/ + outputs_demo/ (so memory or overrides written during an eval never touch the
demo), with the real model from agent/llm.py. It costs API calls: run it on purpose.

  python run.py --generate --demo-harvest --today 2026-11-14     # once, to build outputs_demo/
  python -m agent.evals                                            # -> outputs_demo/agent_evals.csv
"""
import re
import shutil
import tempfile
from pathlib import Path

import pandas as pd
from langchain_core.callbacks import BaseCallbackHandler

from agent.run import answer

ROOT = Path(__file__).resolve().parent.parent
TODAY = "2026-11-14"


def _arg(args, key):
    return str((args or {}).get(key, "")).lower()


# Each case: messages (several = one conversation), and `checks`: alternatives, each (tool name, args predicate).
CASES = [
    dict(id="order_aara", messages=["What should I order from Aara this week?"],
         checks=[("what_to_order", lambda a: "aara" in _arg(a, "brand"))],
         note="no Aara brand in the demo: the honest answer says there are no Aara orders"),
    dict(id="order_sunrise", messages=["What should I order from Sunrise this week?"],
         checks=[("what_to_order", lambda a: "sunrise" in _arg(a, "brand"))]),
    dict(id="why_cashew", messages=["Why is cashew flagged?"],
         checks=[("explain_product", lambda a: "cashew" in _arg(a, "product")), ("products_at_risk", lambda a: True)]),
    dict(id="toor_harvest", messages=["When do we order toor for the next harvest?"],
         checks=[("harvest_plan", lambda a: "toor" in _arg(a, "commodity"))]),
    dict(id="cashew_sources", messages=["Cashews are running low, where else can we get them?"],
         checks=[("alternative_sources", lambda a: "cashew" in _arg(a, "commodity"))]),
    dict(id="remember_diwali", messages=["Diwali is our biggest season, remember that"],
         checks=[("edit_file", lambda a: "/memories/" in _arg(a, "file_path")),
                 ("write_file", lambda a: "/memories/" in _arg(a, "file_path"))]),
    dict(id="override_toor", messages=["Order 50 cases instead of 40 of toor dal, a restaurant placed a big order"],
         checks=[("log_override", lambda a: "50" in _arg(a, "qty") and "toor" in _arg(a, "product"))]),
    dict(id="forecast_quality", messages=["How good is the forecast?"], checks=[("forecast_quality", lambda a: True)]),
    dict(id="questions", messages=["Any questions for me?"], checks=[("pending_questions", lambda a: True)]),
    dict(id="follow_up_moong", messages=["how many", "of moong dal"],
         checks=[("explain_product", lambda a: "moong" in _arg(a, "product")), ("what_to_order", lambda a: True)]),
    dict(id="diwali_window", messages=["When should we order for Diwali next year?"], checks=[("festival_window", lambda a: True)]),
    dict(id="at_risk", messages=["Which products are at risk of running out?"], checks=[("products_at_risk", lambda a: True)]),
]

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text):
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(str(text))}


def invented(reply, evidence):
    """Numbers in the reply that appear in no tool output and no question (commas ignored)."""
    pool = re.sub(r"(?<=\d),(?=\d)", "", " ".join(evidence))
    have = numbers(pool)
    return sorted(n for n in numbers(reply) if n not in have and n.split(".")[0] not in have)


class Recorder(BaseCallbackHandler):
    """Every tool call (main agent and sub-agents) with its input and output."""

    def __init__(self):
        self.calls, self.outputs = [], []

    def on_tool_start(self, serialized, input_str, inputs=None, **kw):
        self.calls.append(((serialized or {}).get("name") or kw.get("name"), inputs if isinstance(inputs, dict) else {}, input_str))

    def on_tool_end(self, output, **kw):
        self.outputs.append(str(getattr(output, "content", output)))


def score(case, recorder, reply):
    tool_ok = any(any(name == t for name, _, _ in recorder.calls) for t, _ in case["checks"])
    args_ok = any(name == t and pred(args) for name, args, _ in recorder.calls for t, pred in case["checks"])
    bad = invented(reply, recorder.outputs + case["messages"])
    return dict(tool_ok=tool_ok, args_ok=args_ok, numbers_ok=not bad, invented=" ".join(bad))


def run_evals(model=None, cases=CASES, data="data_demo", out="outputs_demo", report=None):
    """Run every case on a scratch copy of the demo; returns a DataFrame (one row per case)."""
    if not (ROOT / out / "brand_orders.csv").exists():
        raise FileNotFoundError(f"{out}/ is empty: run `python run.py --generate --demo-harvest --today {TODAY}` first")
    scratch = Path(tempfile.mkdtemp(prefix="agent_evals_"))
    shutil.copytree(ROOT / data, scratch / "data", ignore=shutil.ignore_patterns("agent_*"))
    shutil.copytree(ROOT / out, scratch / "out")
    rows = []
    try:
        for case in cases:
            rec, reply = Recorder(), ""
            for msg in case["messages"]:
                reply = answer(f"eval-{case['id']}", msg, today=TODAY, role="owner", channel="imessage",
                               data=scratch / "data", out=scratch / "out", model=model, callbacks=[rec])
            s = score(case, rec, reply)
            rows.append(dict(id=case["id"], question=" / ".join(case["messages"]), **s,
                             tools=" ".join(n for n, _, _ in rec.calls), reply=reply))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    res = pd.DataFrame(rows)
    if report:
        res.to_csv(report, index=False)
    return res


def summary(res):
    n = len(res)
    return (f"{n} cases: right tool {int(res['tool_ok'].sum())}/{n}, right arguments {int(res['args_ok'].sum())}/{n}, "
            f"no invented numbers {int(res['numbers_ok'].sum())}/{n}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    r = run_evals(report=ROOT / "outputs_demo" / "agent_evals.csv")
    print(r[["id", "tool_ok", "args_ok", "numbers_ok", "invented"]].to_string(index=False))
    print(summary(r), "-> outputs_demo/agent_evals.csv")
