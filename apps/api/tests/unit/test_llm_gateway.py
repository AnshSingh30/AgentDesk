import json
from types import SimpleNamespace

import pytest
from app.llm import gateway as g


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": [1, {"b": "x}"}', {"a": [1, {"b": "x}"}]}),  # tail dropped by server
        ('text {"a":1} tail', {"a": 1}),
        ('```json\n{"a":"q\\""}\n```', {"a": 'q"'}),
        ('{"a":{"b":1}} trailing }', {"a": {"b": 1}}),
        ('{"r": "cut', {"r": "cut"}),
    ],
)
def test_extract_json_repairs(raw: str, expected: dict) -> None:
    assert json.loads(g.extract_json(raw)) == expected


def _resp(content: str) -> SimpleNamespace:
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=None)


async def test_json_mode_empty_body_retries_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    async def fake_acompletion(**kw: object) -> SimpleNamespace:
        calls.append(kw)
        return _resp("" if "response_format" in kw else '{"ok": true}')

    monkeypatch.setattr(g.litellm, "acompletion", fake_acompletion)
    rm = g.ResolvedModel(model="openai/x", provider="custom", credential_mode="custom", api_base="http://h")
    result = await g.chat(rm, [{"role": "user", "content": "hi"}], response_format={"type": "json_object"})
    assert result.content == '{"ok": true}'
    assert "response_format" in calls[0] and "response_format" not in calls[1]


def test_platform_api_base_only_for_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g.settings, "default_llm_api_base", "http://llm:8000/v1")
    monkeypatch.setattr(g.settings, "default_chat_model", "openai/my-model")
    assert g.platform_model().api_base == "http://llm:8000/v1"
    monkeypatch.setattr(g.settings, "default_chat_model", "anthropic/claude-sonnet-5")
    assert g.platform_model().api_base is None


async def test_empty_completion_is_a_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_acompletion(**kw: object) -> SimpleNamespace:
        return _resp("")

    monkeypatch.setattr(g.litellm, "acompletion", fake_acompletion)
    rm = g.ResolvedModel(model="openai/x", provider="custom", credential_mode="custom", api_base="http://h")
    with pytest.raises(g.LLMError) as e:
        await g.chat(rm, [{"role": "user", "content": "hi"}])
    assert e.value.code == "provider_error" and e.value.retryable
