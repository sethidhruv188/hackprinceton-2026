"""Evals: 12 realistic owner questions on the demo, scored on
  tool     the expected tool was called (by the main agent or a sub-agent)
  args     with the right arguments
  numbers  no invented numbers: every number in the reply appears in a tool output or the question (list numbering
           like "1." or "#2" is ignored)
  format   the model's own iMessage reply (before markdown stripping and the cap) has no markdown and fits 900 characters
Each row also has the error (if any), seconds, input / output tokens, the largest single prompt and an estimated cost.
Runs on a scratch copy of data_demo/ + outputs_demo/ (so memory or overrides written during an eval never touch the
demo), with the real model from agent/llm.py. It costs API calls: run it on purpose.

  python run.py --generate --demo-harvest --today 2026-11-14              # once, to build outputs_demo/
  python -m agent.evals                                                     # LLM_MODEL from .env
  python -m agent.evals --model anthropic/claude-haiku-5.5 --budget 2       # stop once the estimated spend passes $2
"""
import re
import shutil
import statistics
import tempfile
import time
from pathlib import Path

import pandas as pd
from langchain_core.callbacks import BaseCallbackHandler

from agent.run import CAPS, answer_detail, has_markdown

ROOT = Path(__file__).resolve().parent.parent
TODAY = "2026-11-14"
BIG_PROMPT = 100_000


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
ENUMERATOR = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|#\d{1,2})\s+")


def numbers(text):
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(ENUMERATOR.sub(" ", str(text)))}


def invented(reply, evidence):
    """Numbers in the reply that appear in no tool output and no question (commas and list numbering ignored)."""
    pool = re.sub(r"(?<=\d),(?=\d)", "", " ".join(evidence))
    have = numbers(pool) | {n.replace(",", "") for n in NUM.findall(pool)}
    return sorted(n for n in numbers(reply) if n not in have and n.split(".")[0] not in have)


class Recorder(BaseCallbackHandler):
    """Every tool call (main agent and sub-agents) with its input and output, and every model call's tokens."""

    def __init__(self):
        self.calls, self.outputs, self.llm = [], [], []

    def on_tool_start(self, serialized, input_str, inputs=None, **kw):
        self.calls.append(((serialized or {}).get("name") or kw.get("name"), inputs if isinstance(inputs, dict) else {}, input_str))

    def on_tool_end(self, output, **kw):
        self.outputs.append(str(getattr(output, "content", output)))

    def on_llm_end(self, response, **kw):
        for gens in response.generations:
            for g in gens:
                u = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                if not u:
                    t = (response.llm_output or {}).get("token_usage") or {}
                    u = dict(input_tokens=t.get("prompt_tokens", 0), output_tokens=t.get("completion_tokens", 0))
                self.llm.append((int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)))


def score(case, recorder, reply, raw=None):
    tool_ok = any(any(name == t for name, _, _ in recorder.calls) for t, _ in case["checks"])
    args_ok = any(name == t and pred(args) for name, args, _ in recorder.calls for t, pred in case["checks"])
    bad = invented(reply, recorder.outputs + case["messages"])
    raw = reply if raw is None else raw
    format_ok = not has_markdown(raw) and len(raw) <= CAPS["imessage"]
    return dict(tool_ok=tool_ok, args_ok=args_ok, numbers_ok=not bad, format_ok=format_ok, invented=" ".join(bad))


def openrouter_price(model_id):
    """(USD per input token, USD per output token) from OpenRouter's public model list, or None if not listed."""
    import httpx
    try:
        r = httpx.get("https://openrouter.ai/api/v1/models", timeout=20)
        for m in r.json().get("data", []):
            if m.get("id") == model_id:
                p = m.get("pricing") or {}
                return float(p.get("prompt") or 0), float(p.get("completion") or 0)
    except Exception:
        return None
    return None


def run_evals(model=None, cases=CASES, data="data_demo", out="outputs_demo", report=None, price=None, budget=None,
              spent=0.0):
    """Run every case on a scratch copy of the demo; returns a DataFrame (one row per case). price: (USD per input
    token, per output token) for the cost estimate; budget: remaining cases are skipped once spent + this run's
    estimate passes it. The scratch agent log is copied next to the report, or kept and its path printed."""
    if not (ROOT / out / "brand_orders.csv").exists():
        raise FileNotFoundError(f"{out}/ is empty: run `python run.py --generate --demo-harvest --today {TODAY}` first")
    scratch = Path(tempfile.mkdtemp(prefix="agent_evals_"))
    shutil.copytree(ROOT / data, scratch / "data", ignore=shutil.ignore_patterns("agent_*"))
    shutil.copytree(ROOT / out, scratch / "out")
    rows, cost = [], 0.0
    try:
        for case in cases:
            if budget is not None and spent + cost > budget:
                rows.append(dict(id=case["id"], question=" / ".join(case["messages"]), skipped=True,
                                 error=f"skipped: estimated spend passed ${budget:g}"))
                continue
            rec, d, errors = Recorder(), {}, []
            t0 = time.perf_counter()
            for msg in case["messages"]:
                d = answer_detail(f"eval-{case['id']}", msg, today=TODAY, role="owner", channel="imessage",
                                  data=scratch / "data", out=scratch / "out", model=model, callbacks=[rec])
                if d.get("error"):
                    errors.append(d["error"])
            secs = time.perf_counter() - t0
            s = score(case, rec, d.get("reply", ""), d.get("raw"))
            tin, tout = sum(a for a, _ in rec.llm), sum(b for _, b in rec.llm)
            big = max((a for a, _ in rec.llm), default=0)
            est = (tin * price[0] + tout * price[1]) if price else None
            cost += est or 0.0
            rows.append(dict(id=case["id"], question=" / ".join(case["messages"]), **s, seconds=round(secs, 1),
                             llm_calls=len(rec.llm), input_tokens=tin, output_tokens=tout, largest_prompt=big,
                             big_prompt=big > BIG_PROMPT, est_cost_usd=None if est is None else round(est, 5),
                             error=" | ".join(errors), tools=" ".join(n for n, _, _ in rec.calls),
                             raw_chars=len(d.get("raw", "")), reply=d.get("reply", ""), skipped=False))
    finally:
        log = scratch / "data" / "agent_log.csv"
        if log.exists() and report:
            dest = Path(report).with_name(Path(report).stem + "_log.csv")
            shutil.copy(log, dest)
            print(f"agent log of this run -> {dest}")
            shutil.rmtree(scratch, ignore_errors=True)
        elif log.exists():
            print(f"agent log of this run kept at {log}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)
    res = pd.DataFrame(rows)
    res.attrs["est_cost_usd"] = cost if price else None
    if report:
        res.to_csv(report, index=False)
    return res


def summary(res):
    skipped = res["skipped"].fillna(False).astype(bool) if "skipped" in res else pd.Series(False, index=res.index)
    ran = res[~skipped]
    n = len(ran)
    if not n:
        return "no cases ran"
    out = (f"{n} cases: right tool {int(ran['tool_ok'].sum())}/{n}, right arguments {int(ran['args_ok'].sum())}/{n}, "
           f"no invented numbers {int(ran['numbers_ok'].sum())}/{n}, format {int(ran['format_ok'].sum())}/{n}")
    if "seconds" in ran:
        out += (f"; time {ran['seconds'].sum():.0f}s total, {statistics.median(ran['seconds']):.1f}s median; tokens in "
                f"{int(ran['input_tokens'].sum()):,} / out {int(ran['output_tokens'].sum()):,}; largest prompt "
                f"{int(ran['largest_prompt'].max()):,}" + (" (OVER 100K)" if ran["big_prompt"].any() else ""))
    if res.attrs.get("est_cost_usd") is not None:
        out += f"; estimated cost ${res.attrs['est_cost_usd']:.4f}"
    if skipped.any():
        out += f"; {int(skipped.sum())} skipped (budget)"
    return out


if __name__ == "__main__":
    import argparse
    import os
    from dotenv import load_dotenv
    from agent.llm import make_model
    load_dotenv()
    ap = argparse.ArgumentParser(description="Run the agent evals (costs API calls)")
    ap.add_argument("--model", default=None, help="OpenRouter model id (default: LLM_MODEL from .env)")
    ap.add_argument("--budget", type=float, default=None, help="stop once the estimated spend passes this many USD")
    ap.add_argument("--spent", type=float, default=0.0, help="USD already spent by earlier runs (counts toward --budget)")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()
    model_id = a.model or os.environ.get("LLM_MODEL")
    price = openrouter_price(model_id)
    print(f"model {model_id}; listed price per 1M tokens: "
          + (f"${price[0] * 1e6:.3f} in / ${price[1] * 1e6:.3f} out" if price else "not listed on OpenRouter"))
    report = a.report or ROOT / "outputs_demo" / f"agent_evals_{re.sub(r'[^A-Za-z0-9]+', '_', model_id)}.csv"
    r = run_evals(model=make_model(model=model_id), report=report, price=price, budget=a.budget, spent=a.spent)
    cols = [c for c in ["id", "tool_ok", "args_ok", "numbers_ok", "format_ok", "seconds", "input_tokens", "output_tokens",
                        "largest_prompt", "est_cost_usd", "error", "invented"] if c in r]
    print(r[cols].to_string(index=False))
    print(summary(r), f"-> {report}")
