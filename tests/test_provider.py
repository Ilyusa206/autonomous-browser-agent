from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from browser_agent.provider import OllamaProvider


def _provider() -> OllamaProvider:
    with patch.dict(
        "os.environ",
        {
            "OLLAMA_BASE_URL": "http://10.10.50.158:11434/v1",
            "OLLAMA_MODEL": "qwen3:14b",
        },
        clear=False,
    ):
        return OllamaProvider()


def test_ollama_request_disables_thinking_and_removes_groq_reasoning_effort():
    provider = _provider()
    response = Mock()
    response.status_code = 200

    with patch("browser_agent.provider.requests.post", return_value=response) as post:
        provider._post_with_rate_limit_retry(
            headers={"Authorization": "Bearer ollama"},
            json={
                "model": "qwen3:14b",
                "messages": [{"role": "user", "content": "test"}],
                "reasoning_effort": "low",
            },
            timeout=90,
        )

    payload = post.call_args.kwargs["json"]
    assert payload["think"] is False
    assert "reasoning_effort" not in payload
    assert payload["model"] == "qwen3:14b"


def test_ollama_preserves_tool_call_payload():
    provider = _provider()
    response = Mock()
    response.status_code = 200
    tools = [{"type": "function", "function": {"name": "navigate", "parameters": {"type": "object"}}}]

    with patch("browser_agent.provider.requests.post", return_value=response) as post:
        provider._post_with_rate_limit_retry(
            json={
                "model": "qwen3:14b",
                "tools": tools,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            },
            timeout=90,
        )

    payload = post.call_args.kwargs["json"]
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"
    assert payload["parallel_tool_calls"] is False
    assert payload["think"] is False


def test_ollama_timeout_has_local_runtime_error():
    provider = _provider()

    with patch("browser_agent.provider.requests.post", side_effect=requests.ReadTimeout("slow")):
        try:
            provider._post_with_rate_limit_retry(json={"model": "qwen3:14b"}, timeout=90)
        except RuntimeError as exc:
            assert "Local Ollama timed out" in str(exc)
            assert "qwen3:14b" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError")
