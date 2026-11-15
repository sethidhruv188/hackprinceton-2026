"""A scripted chat model for agent tests: no network. Each call runs `respond(messages, tools)`, which returns an
AIMessage (text and/or tool calls). Records the tools it was bound with and every prompt it saw."""
import itertools
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_ids = itertools.count(1)


def call(name, **args):
    """A tool call."""
    return {"name": name, "args": args, "id": f"call_{next(_ids)}", "type": "tool_call"}


def say(text="", *calls):
    return AIMessage(content=text, tool_calls=list(calls))


class ScriptedModel(BaseChatModel):
    respond: Callable[..., Any]
    bound: list = []          # the tools of this (bound) copy
    binds: list = []          # every tool set bound so far (main agent and each sub-agent), shared by all copies
    seen: list = []

    def model_post_init(self, __context):
        self.binds, self.seen = [], []

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):
        names = []
        for t in tools:
            names.append(getattr(t, "name", None) or (t.get("function", {}).get("name") if isinstance(t, dict) else None)
                         or (t.get("name") if isinstance(t, dict) else str(t)))
        self.binds.append(names)
        clone = self.model_copy()
        clone.bound, clone.binds, clone.seen = names, self.binds, self.seen     # share the logs with the original
        return clone

    @property
    def all_bound(self):
        return set().union(*self.binds) if self.binds else set()

    @property
    def main_bound(self):
        """The main agent's tools: the tool set that has `task` and the engine tools."""
        return next((set(b) for b in self.binds if "task" in b and "what_to_order" in b), set())

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        msg = self.respond(messages, self.bound)
        if getattr(msg, "usage_metadata", None) is None:          # rough token counts, like a real provider reports
            tin = sum(len(str(m.content)) for m in messages) // 4 + 1
            tout = len(str(msg.content)) // 4 + 1
            msg.usage_metadata = {"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout}
        return ChatResult(generations=[ChatGeneration(message=msg)])
