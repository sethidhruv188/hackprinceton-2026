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
import re
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


def _table_row(m):
    return " - ".join(c.strip() for c in m.group(1).split("|") if c.strip())


_MD = [(r"\*\*(.+?)\*\*", r"\1"), (r"__(.+?)__", r"\1"), (r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1"),
       (r"`([^`]*)`", r"\1"), (r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)"), (r"(?m)^\s{0,3}#{1,6}\s+", ""),
       (r"(?m)^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$\n?", ""), (r"(?m)^\s*\|(.*)\|\s*$", _table_row)]
MARKDOWN = re.compile(r"\*\*|__|`|(?m:^\s{0,3}#{1,6}\s)|\[[^\]]+\]\(https?://|(?m:^\s*\|.*\|\s*$)")


def has_markdown(text):
    """Bold, code, headings, links or table rows (plain '- ' bullets are fine in a text message)."""
    return bool(MARKDOWN.search(str(text)))


def strip_markdown(text):
    """Plain text for a text message: bold / italic / code / headings / links / tables become plain text."""
    out = str(text)
    for pat, rep in _MD:
        out = re.sub(pat, rep, out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


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
            return dict(raw=LIMIT_REPLY, reply=LIMIT_REPLY, error="limit:recursion")
        except LLMTimeout as e:
            log(data, channel, thread_id, role, "limit:llm_timeout", e)
            return dict(raw=LIMIT_REPLY, reply=LIMIT_REPLY, error=f"limit:llm_timeout: {e}")
        except asyncio.TimeoutError:
            log(data, channel, thread_id, role, "limit:wall_clock", f"{wall_clock}s")
            return dict(raw=LIMIT_REPLY, reply=LIMIT_REPLY, error=f"limit:wall_clock ({wall_clock}s)")
        except Exception as e:                  # anything else: honest reply, logged
            log(data, channel, thread_id, role, "error", f"{type(e).__name__}: {e}")
            return dict(raw=ERROR_REPLY, reply=ERROR_REPLY, error=f"{type(e).__name__}: {str(e)[:300]}")
    turn = this_turn(result["messages"])
    msgs = [m for m in turn if getattr(m, "type", "") == "ai" and _text(m)]
    raw = (_text(msgs[-1]) if msgs else "") or "I don't have an answer for that."
    tools, sources = tools_used(turn), sources_cited(turn, raw)
    log(data, channel, thread_id, role, "answered", ", ".join(t["name"] + (f":{t['subagent']}" if t.get("subagent") else "")
                                                                 for t in tools))
    return dict(raw=raw, reply=finish(raw, channel), tools=tools, sources=sources)


def this_turn(messages):
    """The messages of the latest turn: everything after the last human message (the thread keeps its history)."""
    idx = [i for i, m in enumerate(messages) if getattr(m, "type", "") == "human"]
    return messages[idx[-1] + 1:] if idx else list(messages)


def tools_used(turn):
    """[{name, subagent?}] for every tool the main agent called this turn, in order (names only, no arguments)."""
    out = []
    for m in turn:
        for c in getattr(m, "tool_calls", None) or []:
            t = dict(name=c.get("name", "?"))
            if t["name"] == "task":
                t["subagent"] = (c.get("args") or {}).get("subagent_type", "")
            out.append(t)
    return out


URL = re.compile(r"https?://[^\s)\]>\"'|,]+")


def sources_cited(turn, raw):
    """URLs seen in this turn's tool results and in the reply, first seen first, without duplicates."""
    seen = []
    for text in [_text(m) for m in turn if getattr(m, "type", "") == "tool"] + [raw]:
        for u in URL.findall(text):
            u = u.rstrip(".;:")
            if u not in seen:
                seen.append(u)
    return seen[:20]


def finish(raw, channel):
    """What is sent: plain text on iMessage (markdown stripped), capped per channel."""
    return cap(strip_markdown(raw) if channel == "imessage" else raw, channel)


def answer(thread_id, text, **kw):
    """Answer one message in a conversation. role: "owner" (allowlisted iMessage numbers, the dashboard) or "guest";
    channel: "imessage" or "dashboard". today: the plan date (time travel); default today. Returns the reply text."""
    return answer_detail(thread_id, text, **kw)["reply"]


def answer_detail(thread_id, text, today=None, role="guest", channel="imessage", data=None, out=None, model=None,
                  wall_clock=WALL_CLOCK_S, callbacks=None):
    """Like answer(), returning dict(reply, raw, tools, sources, error?): raw is the model's own text before markdown
    stripping and the length cap (the evals check its format); tools lists the tools called this turn (names only) and
    sources the URLs seen in tool results and the reply."""
    d0, o0 = default_dirs()
    data, out = data or d0, out or o0
    with _LOCK, context.use(data, out, today, role=role, channel=channel) as ctx:
        if model is None:
            from agent.llm import make_model
            model = make_model()

        async def job():
            context._CTX.set(ctx)                 # the loop thread doesn't inherit the caller's context
            return await _answer(model, str(thread_id), text, ctx.role, ctx.channel, ctx.data, wall_clock, callbacks)
        return asyncio.run_coroutine_threadsafe(job(), _agent_loop()).result()


_LOOP = None
_LOOP_GUARD = threading.Lock()


def _agent_loop():
    """One long-lived event loop (in a daemon thread) for all agent work. A fresh loop per call breaks the model's
    HTTP client, which keeps its connection pool on the loop it was first used on ("Event loop is closed")."""
    global _LOOP
    with _LOOP_GUARD:
        if _LOOP is None or _LOOP.is_closed():
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, name="agent-loop", daemon=True).start()
        return _LOOP
