from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from browser_agent.provider import AnthropicProvider, EXECUTOR_PROMPT, OllamaProvider, _compact_observation


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
    assert payload["options"]["num_predict"] == 220
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


def test_compact_observation_hard_bounds_large_element_header():
    observation = (
        "URL: https://example.com\nTITLE: Example\n\nINTERACTIVE ELEMENTS:\n"
        + ("[e1] name='very long element'\n" * 500)
        + "\nVISIBLE TEXT:\n"
        + ("useful visible evidence " * 500)
    )

    compact = _compact_observation(observation, 1200)

    assert len(compact) <= 1200
    assert compact.startswith("URL: https://example.com")
    assert "VISIBLE TEXT:" in compact
    assert "useful visible evidence" in compact


def test_compact_observation_preserves_extension_viewport_text() -> None:
    observation = (
        "URL: https://example.com\nTITLE: Example\n\nINTERACTIVE ELEMENTS:\n"
        + ("[e1] name='very long element'\n" * 500)
        + "\nVIEWPORT TEXT:\n"
        + ("target viewport evidence " * 500)
        + "\nPAGE START:\nintro"
    )
    compact = _compact_observation(observation, 1200)
    assert len(compact) <= 1200
    assert "VIEWPORT TEXT:" in compact
    assert "target viewport evidence" in compact


def test_ollama_executor_hard_bounds_observation_context():
    provider = _provider()
    response = _ok({"message": {"role": "assistant", "content": "done"}})
    huge = (
        "URL: https://example.com\nTITLE: Example\n\nINTERACTIVE ELEMENTS:\n"
        + ("[e1] name='noise'\n" * 1000)
        + "\nVISIBLE TEXT:\n"
        + ("evidence " * 1000)
    )

    with patch("browser_agent.provider.requests.post", return_value=response) as post:
        provider.decide(task="Read the page", observation=huge, history=[], tools=[])

    payload = post.call_args.kwargs["json"]
    user_message = payload["messages"][1]["content"]
    page = user_message.split("CURRENT PAGE:\n", 1)[1]
    assert len(page) <= 3600
    assert "VISIBLE TEXT:" in page


def test_executor_prompt_distinguishes_locating_from_reading() -> None:
    assert "find_text only locates text" in EXECUTOR_PROMPT
    assert "read_page reads it and stores grounded evidence" in EXECUTOR_PROMPT
    assert "Do not repeat a no-progress action" in EXECUTOR_PROMPT


def test_anthropic_requires_explicit_model_id() -> None:
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "ANTHROPIC_MODEL": ""}, clear=False):
        try:
            AnthropicProvider()
        except RuntimeError as exc:
            assert "ANTHROPIC_MODEL is missing" in str(exc)
        else:
            raise AssertionError("Expected explicit ANTHROPIC_MODEL requirement")
