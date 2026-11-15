"""Entry point: answer(thread_id, text, today=None, role=..., channel=...) -> str.

- One conversation per (channel, thread_id), kept by an async SQLite checkpointer in <data>/agent_threads.sqlite
  (gitignored with the data folder), so history survives restarts.
- Memory: /memories/ is a folder on disk, <data>/agent_memory/ (gitignored); /memories/AGENTS.md (owner preferences
  and confirmed context) is loaded into every conversation. Other paths are per-conversation scratch space.
- Roles: "owner" gets the write tools and may edit memory; "guest" gets read-only tools and a deny-write rule on
  /memories/.
- Limits: LangGraph recursion_limit 25, LLM timeout 30 s with 2 retries (agent/llm.py), 60 s wall clock per answer.
  Any limit gives a short honest reply and a line in <data>/agent_log.csv.
- Channels: "imessage" (aim 480 characters, hard cap 900) or "dashboard" (up to about 2,500).
"""
import asyncio
import csv
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import HumanMessage

from agent import context, prompts, subagents
from agent import tools as T

RECURSION_LIMIT = 25
WALL_CLOCK_S = 60
CAPS = {"imessage": 900, "dashboard": 2500}
LIMIT_REPLY = "I couldn't finish that one. Try asking about a single brand or product."
ERROR_REPLY = "Something went wrong on my side, sorry. Please try again in a minute."
MEMORY_FILE = "AGENTS.md"
MEMORY_SEED = ("# Owner preferences and confirmed context\n\n"
               "Only what the owner has said or confirmed goes here, one dated line each.\n")
LOG_FIELDS = ["logged_at", "channel", "thread_id", "role", "event", "detail"]
_LOCK = threading.Lock()          # one answer at a time: the dashboard API reads DASH_* from the environment


class LLMTimeout(Exception):
    pass


def default_dirs():
    return os.environ.get("AGENT_DATA", "data"), os.environ.get("AGENT_OUT", "outputs")


def log(data, channel, thread_id, role, event, detail=""):
    p = Path(data) / "agent_log.csv"
    new = not p.exists()
    with open(p, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(dict(logged_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), channel=channel,
                        thread_id=thread_id, role=role, event=event, detail=str(detail)[:300]))


def memory_dir(data):
    d = Path(data) / "agent_memory"
    d.mkdir(parents=True, exist_ok=True)
    f = d / MEMORY_FILE
    if not f.exists():
        f.write_text(MEMORY_SEED, encoding="utf-8")
    return d


def build_agent(model, role, channel, data, checkpointer=None):
    """The deep agent for one caller role and channel."""
    from deepagents import FilesystemPermission, create_deep_agent
    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
    backend = CompositeBackend(default=StateBackend(),
                               routes={"/memories/": FilesystemBackend(root_dir=memory_dir(data), virtual_mode=True)})
    permissions = [] if role == "owner" else [FilesystemPermission(operations=["write"], paths=["/memories/**"], mode="deny")]
    main_tools = T.for_role(T.READ_TOOLS + [T.record_answer, T.log_event, T.log_override], role)
    return create_deep_agent(model=model, tools=main_tools, system_prompt=prompts.main_prompt(role, channel),
                             subagents=subagents.build(role), backend=backend, memory=[f"/memories/{MEMORY_FILE}"],
                             permissions=permissions, checkpointer=checkpointer, name="shakti_agent")


def _text(message):
    c = message.content
    if isinstance(c, list):
        c = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c).strip()


def cap(text, channel):
    """Trim to the channel's hard cap at a sentence or line end."""
    limit = CAPS[channel]
    if len(text) <= limit:
        return text
    more = " ...more on the dashboard." if channel == "imessage" else " ...(shortened)"
    cut = text[:limit - len(more)]
    for sep in ("\n", ". ", "; "):
        i = cut.rfind(sep)
        if i > limit * 0.5:
            cut = cut[:i + (1 if sep != "\n" else 0)]
            break
    return cut.rstrip() + more


async def _run(agent, text, config):
    import httpx
    import openai
    try:
        return await agent.ainvoke({"messages": [HumanMessage(content=text)]}, config=config)
    except (openai.APITimeoutError, httpx.TimeoutException, TimeoutError) as e:
        raise LLMTimeout(str(e)) from e


async def _answer(model, thread_id, text, role, channel, data, wall_clock, callbacks):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.errors import GraphRecursionError
    config = {"configurable": {"thread_id": f"{channel}:{thread_id}"}, "recursion_limit": RECURSION_LIMIT}
    if callbacks:
        config["callbacks"] = callbacks
    async with AsyncSqliteSaver.from_conn_string(str(Path(data) / "agent_threads.sqlite")) as saver:
        agent = build_agent(model, role, channel, data, saver)
        try:
            result = await asyncio.wait_for(_run(agent, text, config), timeout=wall_clock)
        except GraphRecursionError as e:
            log(data, channel, thread_id, role, "limit:recursion", e)
            return LIMIT_REPLY
        except LLMTimeout as e:
            log(data, channel, thread_id, role, "limit:llm_timeout", e)
            return LIMIT_REPLY
        except asyncio.TimeoutError:
            log(data, channel, thread_id, role, "limit:wall_clock", f"{wall_clock}s")
            return LIMIT_REPLY
        except Exception as e:                  # anything else: honest reply, logged
            log(data, channel, thread_id, role, "error", f"{type(e).__name__}: {e}")
            return ERROR_REPLY
    msgs = [m for m in result["messages"] if getattr(m, "type", "") == "ai" and _text(m)]
    reply = _text(msgs[-1]) if msgs else ""
    return cap(reply or "I don't have an answer for that.", channel)


def answer(thread_id, text, today=None, role="guest", channel="imessage", data=None, out=None, model=None,
           wall_clock=WALL_CLOCK_S, callbacks=None):
    """Answer one message in a conversation. role: "owner" (allowlisted iMessage numbers, the dashboard) or "guest";
    channel: "imessage" or "dashboard". today: the plan date (time travel); default today."""
    d0, o0 = default_dirs()
    data, out = data or d0, out or o0
    with _LOCK, context.use(data, out, today, role=role, channel=channel) as ctx:
        if model is None:
            from agent.llm import make_model
            model = make_model()
        return asyncio.run(_answer(model, str(thread_id), text, ctx.role, ctx.channel, ctx.data, wall_clock, callbacks))
