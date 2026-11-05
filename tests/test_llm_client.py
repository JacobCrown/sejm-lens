import json
from decimal import Decimal

import httpx
import pytest

from apps.assets import llm as llm_module
from apps.assets.llm import LLMClient, LLMError, LLMNotConfigured, extract_json, resolve_base_url


def ok_response(content='{"ok": true}', **extra):
    return httpx.Response(200, json={"model": "m-1", "choices": [{"message": {"content": content}}], **extra})


def client(handler, **kwargs):
    kwargs.setdefault("base_url", "https://llm.test/v1/chat/completions")
    kwargs.setdefault("model", "test-model")
    return LLMClient(transport=httpx.MockTransport(handler), **kwargs)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: None)


def test_resolve_base_url():
    assert resolve_base_url("openai", "") == "https://api.openai.com/v1/chat/completions"
    assert resolve_base_url("ollama", "") == "http://localhost:11434/v1/chat/completions"
    assert resolve_base_url("openai", "https://my.host/v1/") == "https://my.host/v1/chat/completions"
    assert resolve_base_url("", "https://x/v1/chat/completions") == "https://x/v1/chat/completions"
    with pytest.raises(LLMNotConfigured):
        resolve_base_url("nieznany", "")


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Oto wynik:\n{"a": 1}\nPozdrawiam',
        '<think>muszę policzyć {x}</think>{"a": 1}',
    ],
)
def test_extract_json_variants(text):
    assert extract_json(text) == {"a": 1}


def test_extract_json_failure():
    with pytest.raises(ValueError):
        extract_json("brak json")


def test_request_shape_and_auth():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["x"] = request.headers.get("x-custom")
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return ok_response(usage={"prompt_tokens": 10, "completion_tokens": 5})

    c = client(handler, api_key="sk-1", extra_headers={"X-Custom": "1"}, extra_body={"reasoning_effort": "low"},
               max_tokens=500, temperature=0)
    r = c.chat([{"role": "user", "content": "hej"}])
    assert seen["auth"] == "Bearer sk-1" and seen["x"] == "1"
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    body = seen["body"]
    assert body["model"] == "test-model" and body["temperature"] == 0 and body["max_tokens"] == 500
    assert body["response_format"] == {"type": "json_object"} and body["reasoning_effort"] == "low"
    assert (r.text, r.prompt_tokens, r.completion_tokens, r.model) == ('{"ok": true}', 10, 5, "m-1")
    assert "usage" not in body and r.cost_usd is None


def test_no_auth_header_without_key_and_optional_params_omitted():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return ok_response()

    client(handler, temperature=None, response_format="none").chat([])
    assert seen["auth"] is None
    assert not {"temperature", "max_tokens", "response_format"} & set(seen["body"])


def test_openrouter_asks_for_cost_and_parses_it():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return ok_response(usage={"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.00125})

    c = client(handler, base_url="https://openrouter.ai/api/v1/chat/completions")
    response = c.chat([{"role": "user", "content": "hej"}])
    assert seen["body"]["usage"] == {"include": True}
    assert response.cost_usd == Decimal("0.00125")
    assert response.prompt_tokens == 100


def test_json_schema_response_format():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return ok_response()

    client(handler, response_format="json_schema").chat([], schema={"type": "object"})
    rf = seen["body"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == {"type": "object"}


def test_unsupported_params_are_dropped_and_reported():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(sorted(body))
        if "temperature" in body:
            return httpx.Response(400, text="Unsupported value: 'temperature' does not support 0 with this model")
        if "response_format" in body:
            return httpx.Response(400, text="response_format json_object is not supported")
        return ok_response()

    r = client(handler).chat([])
    assert r.dropped_params == ["response_format", "temperature"]
    assert len(calls) == 3


def test_retries_on_rate_limit_then_succeeds():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(429, text="slow down") if len(attempts) < 3 else ok_response()

    assert client(handler).chat([]).text
    assert len(attempts) == 3


def test_gives_up_after_retries():
    with pytest.raises(LLMError, match="500"):
        client(lambda r: httpx.Response(500, text="boom"), max_retries=2).chat([])


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_errors_fail_fast(status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, text="nope")

    with pytest.raises(LLMError, match="refused access"):
        client(handler).chat([])
    assert len(calls) == 1


def test_unknown_400_fails_fast():
    with pytest.raises(LLMError, match="rejected the request"):
        client(lambda r: httpx.Response(400, text="context length exceeded")).chat([])


def test_empty_and_malformed_responses():
    with pytest.raises(LLMError, match="empty response"):
        client(lambda r: ok_response("  ")).chat([])
    with pytest.raises(LLMError, match="Malformed"):
        client(lambda r: httpx.Response(200, json={"oops": 1})).chat([])


def test_config_validation():
    with pytest.raises(LLMNotConfigured):
        LLMClient(base_url="https://x", model="")
    with pytest.raises(LLMNotConfigured):
        LLMClient(base_url="https://x", model="m", response_format="xml")


def test_from_settings(settings):
    settings.LLM_PROVIDER, settings.LLM_BASE_URL, settings.LLM_API_KEY = "openai", "", ""
    settings.LLM_MODEL = "gpt-x"
    with pytest.raises(LLMNotConfigured, match="LLM_API_KEY"):
        LLMClient.from_settings()

    settings.LLM_API_KEY = "sk-test"
    settings.LLM_TEMPERATURE, settings.LLM_MAX_TOKENS = "", "800"
    c = LLMClient.from_settings()
    assert c.url == "https://api.openai.com/v1/chat/completions"
    assert c.temperature is None and c.max_tokens == 800 and c.model == "gpt-x"

    settings.LLM_PROVIDER, settings.LLM_API_KEY, settings.LLM_MODEL = "ollama", "", "qwen2.5:14b"
    c = LLMClient.from_settings()
    assert c.url == "http://localhost:11434/v1/chat/completions"

    settings.LLM_PROVIDER, settings.LLM_BASE_URL = "custom", "https://gateway.example/openai"
    assert LLMClient.from_settings().url == "https://gateway.example/openai/chat/completions"
