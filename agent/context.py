"""Per-conversation context the tools read: data/output folders, the plan date, the caller's role and channel.

answer() sets it for one call; LangChain copies the context into the threads that run sync tools. The dashboard
API (api.main) reads DASH_DATA / DASH_OUT / DASH_TODAY from the environment, so use() sets those too: one answer
at a time (agent.run holds a lock).
"""
import contextvars
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ROLES = ("owner", "guest")
CHANNELS = ("imessage", "dashboard")


@dataclass(frozen=True)
class Context:
    data: Path
    out: Path
    today: pd.Timestamp
    role: str = "guest"
    channel: str = "imessage"

    @property
    def is_owner(self):
        return self.role == "owner"


_CTX = contextvars.ContextVar("agent_context", default=None)


def current():
    c = _CTX.get()
    if c is None:
        raise RuntimeError("no agent context: call tools through agent.run.answer() or context.use()")
    return c


@contextmanager
def use(data="data", out="outputs", today=None, role="guest", channel="imessage"):
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}")
    data, out = Path(data), Path(out)
    t = pd.Timestamp(today).normalize() if today else pd.Timestamp.today().normalize()
    ctx = Context(data=data if data.is_absolute() else ROOT / data, out=out if out.is_absolute() else ROOT / out,
                  today=t, role=role, channel=channel)
    saved = {k: os.environ.get(k) for k in ("DASH_DATA", "DASH_OUT", "DASH_TODAY")}
    os.environ.update(DASH_DATA=str(ctx.data), DASH_OUT=str(ctx.out), DASH_TODAY=f"{t:%Y-%m-%d}")
    token = _CTX.set(ctx)
    try:
        yield ctx
    finally:
        _CTX.reset(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
