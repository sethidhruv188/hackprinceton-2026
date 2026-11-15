"""Supply scan: the sourcing researcher checks recent news (last 60 days) for each harvest commodity's producing
countries: floods, droughts, export bans, poor crops, port or shipping disruption. Credible findings become country
alerts (through sourcing.add_alerts: source URL and reported_on required, issue marked "Unverified news:"); the owner
can dismiss them with alert:ALERT_ID. Known alerts (same commodity, country and source) are skipped.

  python -m agent.scan --dry-run              # what would be searched; no network, nothing written
  python -m agent.scan --today 2026-11-14     # real run: web search (Tavily) + the model; writes alerts
  python -m agent.scan --demo                 # the demo folders (alerts can't be written into data_demo/: real news)

Hard caps: at most 20 Tavily searches per run (basic news search), 60 seconds per agent call (one per commodity),
recursion limit 25. No prices are predicted; every alert is cited; web content is information, never instructions.
"""
import argparse
import asyncio
from pathlib import Path

import pandas as pd
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from agent import context, prompts
from agent import run as R
from agent import tools as T

MAX_SEARCHES = 20
WALL_CLOCK_S = 60
NEWS_DAYS = 60
PREFIX = "Unverified news: "

SCAN_TASK = """Supply scan for {commodity}. Producing countries: {countries}.
Look for news from the last {days} days (plan date {today}) about floods, droughts, export bans, poor yields or crop
damage, or port and shipping disruption affecting {commodity} from these countries. Use web_search with short,
specific queries (at most one or two per country; the run has a strict search budget). Existing alerts are listed
below: don't add those again.
For each credible, specific, recent problem, call add_alerts with commodity "{commodity}", the country, a one-line
issue, the source_url of the article and reported_on (the article's date, YYYY-MM-DD). Skip rumours, old news,
general market commentary and price forecasts. If nothing credible turns up, add nothing.
Then answer in one short line per country: what you found (with the URL) or "nothing found".
Existing alerts: {existing}"""


def plan(out_dir, calendar=None):
    """[(commodity, [countries])] in priority order: commodities with an at-risk product, then those in the orders,
    then by yearly volume. Countries from config/harvest_calendar.csv."""
    from inventory import harvest as H
    cal = H.load_calendar() if calendar is None else calendar
    hp = pd.read_csv(Path(out_dir) / "harvest_plan.csv").fillna("") if (Path(out_dir) / "harvest_plan.csv").exists() else pd.DataFrame()
    if hp.empty:
        return []
    hp = hp[pd.to_numeric(hp["products"], errors="coerce").fillna(0) > 0]
    risk_p = Path(out_dir) / "sourcing_options.csv"
    at_risk = set(pd.read_csv(risk_p).fillna("")["commodity"]) if risk_p.exists() else set()
    g = hp.groupby("commodity").agg(in_orders=("in_orders", lambda s: (s == "yes").any()),
                                    yearly=("yearly_cases", lambda s: pd.to_numeric(s, errors="coerce").max())).reset_index()
    g["risk"] = g["commodity"].isin(at_risk)
    g = g.sort_values(["risk", "in_orders", "yearly"], ascending=[False, False, False])
    out = []
    for c in g["commodity"]:
        countries = list(dict.fromkeys(cal.loc[cal["commodity"] == c, "source_country"]))
        if countries:
            out.append((c, countries))
    return out


def _alert_tool(found, dry_run):
    @tool
    def add_alerts(rows: list[dict]) -> str:
        """Record credible, recent problems in producing countries as country alerts. Each row: commodity, country,
        issue (one line), source_url (the article), reported_on (the article's date, YYYY-MM-DD)."""
        from inventory import sourcing as S
        rows = T._rows(rows)
        if rows is None:
            return "rows must be a list of objects."
        clean, missing = [], []
        for r in rows:
            r = {k: str(v).strip() for k, v in r.items() if k in ("commodity", "country", "issue", "source_url", "reported_on")}
            if not r.get("reported_on"):
                missing.append(r.get("country", "?"))
                continue
            if r.get("issue") and not r["issue"].startswith(PREFIX):
                r["issue"] = PREFIX + r["issue"]
            clean.append(r)
        if dry_run:
            found.extend(dict(r, status="would add (dry run)") for r in clean)
            return f"dry run: {len(clean)} alert(s) not written."
        c = context.current()
        res = S.add_alerts(clean, c.data, today=c.today)
        rejected = {id(r) for r, _ in res["rejected"]}
        for r in clean:
            if id(r) not in rejected:
                found.append(dict(r, status="added or already known"))
        msg = f"{res['added']} alert(s) added, {res['updated']} already known."
        if res["rejected"] or missing:
            msg += " Rejected: " + "; ".join([f"{r.get('country', '?')}: {why}" for r, why in res["rejected"]]
                                             + [f"{m}: reported_on (the article date) is required" for m in missing])
        return msg
    return add_alerts


def _agent(model, alert_tool):
    from deepagents import create_deep_agent
    return create_deep_agent(model=model, tools=[T.web_search, T.country_alerts, alert_tool],
                             system_prompt=prompts.sub_prompt(prompts.SOURCING_RESEARCHER), name="supply_scan")


def _existing(data, today):
    from inventory import sourcing as S
    al = S.load_alerts(data, today)
    al = al[al["active"]] if len(al) else al
    return "; ".join(f"{r.commodity} / {r.country}: {r.issue} ({r.source_url})" for r in al.itertuples()) or "none"


def scan(model=None, data="data", out="outputs", today=None, dry_run=False, max_searches=MAX_SEARCHES,
         wall_clock=WALL_CLOCK_S, calendar=None, log=print):
    """Run the scan; returns a summary dict (searches, alerts, nothing_found, not_scanned, errors)."""
    targets = plan(R.context.ROOT / out if not Path(out).is_absolute() else out, calendar)
    summary = dict(searches=0, alerts=[], nothing_found=[], not_scanned=[], errors=[], targets=targets)
    if dry_run:
        for c, countries in targets:
            log(f"would scan {c}: {', '.join(countries)}")
        log(f"{len(targets)} commodities; at most {max_searches} searches; nothing searched or written (dry run)")
        return summary
    if model is None:
        from agent.llm import make_model
        model = make_model()
    budget = T.SearchBudget(max_searches)
    saved = dict(T._SEARCH)
    T._SEARCH.update(budget=budget, defaults=dict(topic="news", days=NEWS_DAYS, search_depth="basic"))
    try:
        with context.use(data, out, today, role="owner", channel="dashboard") as ctx:
            for commodity, countries in targets:
                if budget.left <= 0:
                    summary["not_scanned"].append(commodity)
                    continue
                found = []
                task = SCAN_TASK.format(commodity=commodity, countries=", ".join(countries), days=NEWS_DAYS,
                                        today=f"{ctx.today:%Y-%m-%d}", existing=_existing(ctx.data, ctx.today))
                agent = _agent(model, _alert_tool(found, dry_run=False))

                async def job(agent=agent, task=task):
                    context._CTX.set(ctx)
                    return await asyncio.wait_for(agent.ainvoke({"messages": [HumanMessage(content=task)]},
                                                                config={"recursion_limit": R.RECURSION_LIMIT}), timeout=wall_clock)
                before = budget.used
                try:
                    asyncio.run_coroutine_threadsafe(job(), R._agent_loop()).result()
                except Exception as e:                       # a limit or provider error: noted, the scan goes on
                    kind = "wall_clock" if isinstance(e, (asyncio.TimeoutError, TimeoutError)) else type(e).__name__
                    summary["errors"].append(f"{commodity}: {kind}")
                    R.log(ctx.data, "scan", commodity, "owner", f"scan:{kind}", str(e)[:200])
                hit = {context_key(r.get("country")) for r in found}
                summary["alerts"] += [dict(r, commodity=r.get("commodity", commodity)) for r in found]
                summary["nothing_found"] += [f"{commodity} / {k}" for k in countries if context_key(k) not in hit]
                log(f"{commodity}: {budget.used - before} search(es), {len(found)} alert(s)")
            summary["searches"] = budget.used
            if any(a["status"].startswith("added") for a in summary["alerts"]):
                from inventory import answers as A
                from inventory import sourcing as S
                S.run(ctx.data, ctx.out, ctx.today, answers=A.load(ctx.data))   # alerts reach the digest, agent, dashboard
    finally:
        T._SEARCH.clear()
        T._SEARCH.update(saved)
    return summary


def context_key(country):
    from inventory.sourcing import country_key
    return country_key(country or "")


def main():
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser(description="Supply scan: recent crop / supply news per producing country")
    ap.add_argument("--dry-run", action="store_true", help="show what would be searched; no network, nothing written")
    ap.add_argument("--today", default=None)
    ap.add_argument("--demo", action="store_true", help="use data_demo/ and outputs_demo/")
    ap.add_argument("--max-searches", type=int, default=MAX_SEARCHES)
    a = ap.parse_args()
    data, out = ("data_demo", "outputs_demo") if a.demo else ("data", "outputs")
    s = scan(data=data, out=out, today=a.today, dry_run=a.dry_run, max_searches=min(a.max_searches, MAX_SEARCHES))
    if a.dry_run:
        return
    added = [x for x in s["alerts"] if x["status"].startswith("added")]
    print(f"\nsearches used: {s['searches']} of {min(a.max_searches, MAX_SEARCHES)}; alerts added: {len(added)}")
    for x in added:
        print(f"  {x['commodity']} / {x['country']}: {x['issue']} ({x['source_url']}, {x['reported_on']})")
    print("nothing found: " + (", ".join(s["nothing_found"]) or "none"))
    if s["not_scanned"]:
        print("not scanned (search budget used up): " + ", ".join(s["not_scanned"]))
    if s["errors"]:
        print("limits / errors: " + "; ".join(s["errors"]))


if __name__ == "__main__":
    main()
