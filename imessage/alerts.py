"""Daily digest by iMessage: Order now / Too late: expedite / harvest windows opening / active country alerts.

  python -m imessage.alerts                  # print today's digest (nothing is sent)
  python -m imessage.alerts --send           # send it to owner spaces that have texted the bot, at most once a day
  python -m imessage.alerts --send --today 2026-11-14

Deterministic: built from the same data the agent's tools read (the dashboard API, harvest_plan.csv, country alerts),
no model call, so every number is exact and it costs nothing. Schedule it with Windows Task Scheduler (see README).
"""
import argparse
import asyncio

import pandas as pd

from agent.context import use
from imessage import config as C
from imessage.state import State

OPENING_DAYS = 30


def build_digest(data, out, today):
    """The digest text, or None when there is nothing to report."""
    from agent import tools as T
    with use(data, out, today, role="owner") as ctx:
        M = T._api()
        b, err = T._call(M.brands)
        orders = [r for r in (b or {}).get("brands", []) if r.get("next_order")]
        now = [r for r in orders if r["next_order"]["status"] == "Order now"]
        late_orders = _late(ctx.out)
        hp = T._csv("harvest_plan.csv")
        from inventory import answers as A
        from inventory import sourcing as S
        alerts = S.load_alerts(ctx.data, ctx.today, A.load(ctx.data))
        alerts = alerts[alerts["active"]] if len(alerts) else alerts
        lines = []
        if now:
            lines.append("Order now: " + "; ".join(f"{r['brand']} {T._num(r['next_order']['cases'])} cases" for r in now[:5])
                         + (f" (+{len(now) - 5} more)" if len(now) > 5 else ""))
        if late_orders:
            lines.append("Too late, expedite: " + "; ".join(late_orders[:4]))
        if len(hp):
            h = hp[(hp["in_orders"] == "yes") & (hp["order_window_start"] != "")].copy()
            h["start"] = pd.to_datetime(h["order_window_start"], errors="coerce")
            soon = h[(h["status"] == "ORDER NOW") | ((h["start"] - ctx.today).dt.days.between(0, OPENING_DAYS))]
            if len(soon):
                lines.append("Harvest buys: " + "; ".join(
                    f"{r.commodity} ({r.source_country}) {'order now' if r.status == 'ORDER NOW' else 'window opens ' + r.order_window_start}"
                    f", by {r.order_by}, {T._num(r.quantity_cases)} cases" for r in soon.head(4).itertuples()))
        if len(alerts):
            lines.append("Alerts: " + "; ".join(f"{r.country} {r.commodity}: {r.issue}" for r in alerts.head(3).itertuples()))
        if not lines:
            return None
        return f"Daily digest {ctx.today:%b %d}:\n" + "\n".join(lines) + "\nAsk me about any of these."


def _late(out):
    p = out / "brand_orders.csv"
    if not p.exists():
        return []
    o = pd.read_csv(p)
    late = o[o["status"].astype(str).str.startswith("Too late")]
    return [f"{r.brand} {int(r.cases):,} cases (was due {r.order_by_safe})" for r in late.itertuples()]


async def send_digest(cfg, sender, state, today, text):
    """Send `text` to every owner space that has texted and hasn't had a digest today. Returns the spaces sent to."""
    from imessage.bot import split_reply
    if not state.agent_user_id:
        raise RuntimeError("the bot hasn't received a message yet, so it doesn't know its own sender id: text it first")
    day = f"{pd.Timestamp(today):%Y-%m-%d}"
    sent = []
    for space, meta in sorted(state.owner_spaces.items()):
        if meta.get("last_digest") == day:
            continue
        for i, part in enumerate(split_reply(text, cfg.bubble_chars, cfg.max_bubbles)):
            await sender.send_text(space, state.agent_user_id, None, part, f"digest:{space}:{day}:{i}")
        state.owner_spaces[space]["last_digest"] = day
        state.save_meta()
        sent.append(space)
    return sent


def main():
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser(description="Daily digest by iMessage")
    ap.add_argument("--send", action="store_true", help="send it (default: print only)")
    ap.add_argument("--today", default=None)
    a = ap.parse_args()
    cfg = C.load()
    today = pd.Timestamp(a.today or pd.Timestamp.today()).normalize()
    text = build_digest(cfg.data, cfg.out, today)
    if text is None:
        print("Nothing to report today.")
        return
    print(text)
    if not a.send:
        return
    from imessage.bot import PhotonSender, setup_logging
    setup_logging(cfg.state_dir)
    state = State(cfg.state_dir)
    sent = asyncio.run(send_digest(cfg, PhotonSender(cfg), state, today, text))
    print(f"sent to {len(sent)} owner space(s): " + ", ".join(C.mask(s) for s in sent) if sent else
          "already sent today, or no owner has texted the bot yet")


if __name__ == "__main__":
    main()
