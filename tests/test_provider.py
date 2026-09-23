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


def _ok(payload: dict) -> Mock:
    response = Mock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = payload
    return response


def test_ollama_normalizes_openai_style_base_url_to_native_chat():
    provider = _provider()
    assert provider.endpoint == "http://10.10.50.158:11434/api/chat"


def test_ollama_plan_uses_native_json_mode_and_disables_thinking():
    provider = _provider()
    response = _ok(
        {
            "message": {
                "role": "assistant",
                "content": '{"objective":"open page","steps":["navigate"],"success_criteria":["page loaded"]}',
            }
        }
    )

    with patch("browser_agent.provider.requests.post", return_value=response) as post:
        plan = provider.create_plan(task="Open the page", observation="URL: about:blank")

    payload = post.call_args.kwargs["json"]
    assert post.call_args.args[0] == "http://10.10.50.158:11434/api/chat"
    assert payload["think"] is False
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload["options"]["num_predict"] == 320
    assert plan["objective"] == "open page"


def test_ollama_decide_preserves_native_tool_call():
    provider = _provider()
    response = _ok(
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "navigate",
                            "arguments": {"url": "https://example.com"},
                        }
                    }
                ],
            }
        }
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "navigate",
                "description": "Navigate",
                "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
            },
        }
    ]

    with patch("browser_agent.provider.requests.post", return_value=response) as post:
        decision = provider.decide(
            task="Open example.com",
            observation="URL: about:blank",
            history=[],
            tools=tools,
        )

    payload = post.call_args.kwargs["json"]
    assert payload["think"] is False
    assert payload["tools"] == tools
    assert decision.kind == "tool"
    assert decision.name == "navigate"
    assert decision.arguments == {"url": "https://example.com"}


def test_ollama_verifier_uses_native_json_mode():
    provider = _provider()
    response = _ok(
        {
            "message": {
                "role": "assistant",
                "content": '{"complete":true,"summary":"done","missing":[]}',
            }
        }
    )

    with patch("browser_agent.provider.requests.post", return_value=response) as post:
        result = provider.verify_goal(
            task="Read heading",
            observation="Example Domain",
            candidate_answer="Example Domain",
        )

    payload = post.call_args.kwargs["json"]
    assert payload["think"] is False
    assert payload["format"] == "json"
    assert result.complete is True
    assert result.summary == "done"


def test_ollama_timeout_has_local_runtime_error():
    provider = _provider()

    with patch("browser_agent.provider.requests.post", side_effect=requests.ReadTimeout("slow")):
        try:
            provider.create_plan(task="test", observation="test")
        except RuntimeError as exc:
            assert "Local Ollama timed out" in str(exc)
            assert "qwen3:14b" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError")


def test_ollama_plan_falls_back_when_json_is_truncated():
    provider = _provider()
    response = _ok(
        {
            "message": {
                "role": "assistant",
                "content": '{"objective":"open page","steps":["navigate"],"success_criteria":["page',
            }
        }
    )

    with patch("browser_agent.provider.requests.post", return_value=response):
        plan = provider.create_plan(task="Open the page", observation="URL: about:blank")

    assert plan["objective"] == "Open the page"
    assert len(plan["steps"]) == 3
    assert "final page observation" in plan["success_criteria"][0]
