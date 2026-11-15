"""The chat model, in one place: OpenRouter through langchain-openai today; other providers (Bedrock) can be added
here behind LLM_PROVIDER without touching the agent.

Environment:
  LLM_PROVIDER        openrouter (default)
  LLM_MODEL           the model id on that provider (must support tool calling)
  OPENROUTER_API_KEY  key for OpenRouter
  LANGSMITH_API_KEY   when set, LangSmith tracing is switched on (LANGSMITH_PROJECT optional)
"""
import os

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_TIMEOUT_S = 30
LLM_MAX_RETRIES = 2


def tracing():
    """Switch on LangSmith tracing when a key is present. Returns True if tracing is on."""
    if os.environ.get("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", "shakti-agent")
        return True
    return False


def make_model(provider=None, model=None):
    """Create the chat model. Raises a clear error when the provider or its settings are missing."""
    provider = (provider or os.environ.get("LLM_PROVIDER") or "openrouter").strip().lower()
    model = model or os.environ.get("LLM_MODEL")
    tracing()
    if provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key or not model:
            raise RuntimeError("set OPENROUTER_API_KEY and LLM_MODEL (a tool-calling model on OpenRouter)")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, base_url=OPENROUTER_BASE_URL, api_key=key, timeout=LLM_TIMEOUT_S,
                          max_retries=LLM_MAX_RETRIES, temperature=0)
    raise RuntimeError(f"LLM_PROVIDER={provider!r} is not supported yet (only openrouter); add it in agent/llm.py")
