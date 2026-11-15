"""agent/llm.py: one place creates the model (OpenRouter today); tracing only with a LangSmith key. No network."""
import pytest

from agent import llm


def test_make_model_needs_settings(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "LLM_MODEL", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        llm.make_model()
    with pytest.raises(RuntimeError, match="not supported"):
        llm.make_model(provider="bedrock", model="x")


def test_make_model_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("LLM_MODEL", "some/model")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    m = llm.make_model()
    assert m.model_name == "some/model" and str(m.openai_api_base).startswith("https://openrouter.ai/api/v1")
    assert m.request_timeout == llm.LLM_TIMEOUT_S and m.max_retries == llm.LLM_MAX_RETRIES
    assert llm.tracing() is False


def test_tracing_with_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert llm.tracing() is True
    import os
    assert os.environ["LANGSMITH_TRACING"] == "true"
