"""Tests for the provider boundary, the registry, and the offline adapters.

The HTTP adapters are tested by stubbing the one function that touches the
network (:func:`ashe_lab.providers._http.post_json`). That keeps the suite
offline and credential-free while still exercising the part of each adapter
that actually matters: the translation between our neutral request/response
shape and the vendor's.
"""

from __future__ import annotations

import pytest

from ashe_lab.errors import ProviderError, ProviderNotConfigured, RegistryError
from ashe_lab.providers import (
    EchoProvider,
    FailingProvider,
    Message,
    ModelRequest,
    Provider,
    ProviderCache,
    available_providers,
    get_provider,
    register_provider,
)
from ashe_lab.providers.anthropic_messages import AnthropicMessagesProvider
from ashe_lab.providers.openai_chat import OpenAIChatProvider


def _request(text="hello", model="test-model", **kwargs):
    return ModelRequest(
        model=model, messages=[Message(role="user", content=text)], **kwargs
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_builtin_providers_are_registered():
    names = available_providers()
    for expected in ("echo", "failing", "openai_chat", "anthropic_messages"):
        assert expected in names


def test_unknown_provider_error_lists_the_valid_names():
    with pytest.raises(RegistryError) as excinfo:
        get_provider("gpt5_telepathy")
    message = str(excinfo.value)
    assert "gpt5_telepathy" in message
    assert "echo" in message  # tells the reader what they could have said


def test_register_provider_adds_an_adapter():
    class Stub:
        name = "unit-test-stub"

        def complete(self, request):  # pragma: no cover - not invoked
            raise NotImplementedError

        def describe(self):
            return {"provider": self.name}

    register_provider("unit-test-stub", Stub)
    assert "unit-test-stub" in available_providers()
    assert isinstance(get_provider("unit-test-stub"), Stub)


def test_provider_cache_instantiates_once():
    cache = ProviderCache()
    assert cache.get("echo") is cache.get("echo")


def test_provider_cache_describes_only_what_was_used():
    cache = ProviderCache()
    cache.get("echo")
    described = cache.described()
    assert "echo" in described
    assert "openai_chat" not in described


def test_adapters_satisfy_the_provider_protocol():
    """The Protocol is the contract; verify each shipped adapter matches it."""
    for provider in (
        EchoProvider(),
        FailingProvider(),
        OpenAIChatProvider(api_key="test"),
        AnthropicMessagesProvider(api_key="test"),
    ):
        assert isinstance(provider, Provider)


def test_describe_never_leaks_a_credential():
    """A manifest is committed evidence; a key must never reach it."""
    secret = "sk-do-not-leak-this-value"
    for provider in (
        OpenAIChatProvider(api_key=secret),
        AnthropicMessagesProvider(api_key=secret),
    ):
        described = provider.describe()
        assert secret not in repr(described)
        assert described["credentials_present"] is True


# ---------------------------------------------------------------------------
# Echo provider
# ---------------------------------------------------------------------------


def test_echo_is_deterministic():
    provider = EchoProvider()
    first = provider.complete(_request("same input"))
    second = provider.complete(_request("same input"))
    assert first.text == second.text
    assert first.provider_request_id == second.provider_request_id


def test_echo_differs_for_different_prompts():
    provider = EchoProvider()
    assert provider.complete(_request("a")).text != provider.complete(_request("b")).text


def test_echo_response_varies_with_sampling_parameters():
    """Recording temperature matters only if it can change the output."""
    provider = EchoProvider()
    warm = provider.complete(_request("x", temperature=1.0))
    cold = provider.complete(_request("x", temperature=0.0))
    assert warm.text != cold.text


def test_echo_reports_usage_and_marks_it_estimated():
    response = EchoProvider().complete(_request("some words here"))
    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0
    assert response.usage.total_tokens == (
        response.usage.input_tokens + response.usage.output_tokens
    )
    assert "estimated" in response.usage.extra["token_counts_are"]


def test_echo_honours_sentence_count_option():
    provider = EchoProvider()
    short = provider.complete(_request("q", options={"sentences": 1}))
    long = provider.complete(_request("q", options={"sentences": 6}))
    assert short.text.count(".") == 1
    assert long.text.count(".") == 6


def test_echo_can_echo_the_prompt_for_assertions():
    response = EchoProvider().complete(
        _request("the exact prompt", options={"echo_prompt": True})
    )
    assert "PROMPT_ECHO: the exact prompt" in response.text


# ---------------------------------------------------------------------------
# Failing provider
# ---------------------------------------------------------------------------


def test_failing_provider_raises_retryable_by_default():
    with pytest.raises(ProviderError) as excinfo:
        FailingProvider().complete(_request())
    assert excinfo.value.retryable is True


def test_failing_provider_can_raise_non_retryable():
    with pytest.raises(ProviderError) as excinfo:
        FailingProvider().complete(_request(options={"retryable": False}))
    assert excinfo.value.retryable is False


def test_failing_provider_succeeds_after_configured_failures():
    provider = FailingProvider()
    request = _request(options={"fail_times": 2})
    for _ in range(2):
        with pytest.raises(ProviderError):
            provider.complete(request)
    assert provider.complete(request).text  # third attempt succeeds


# ---------------------------------------------------------------------------
# Missing credentials
# ---------------------------------------------------------------------------


def test_openai_without_a_key_raises_provider_not_configured(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAIChatProvider(api_key=None)
    with pytest.raises(ProviderNotConfigured) as excinfo:
        provider.complete(_request())
    assert "OPENAI_API_KEY" in str(excinfo.value)
    assert excinfo.value.retryable is False  # retrying will not invent a key


def test_anthropic_without_a_key_raises_provider_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderNotConfigured) as excinfo:
        AnthropicMessagesProvider(api_key=None).complete(_request())
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


# ---------------------------------------------------------------------------
# HTTP adapter translation, with the network stubbed
# ---------------------------------------------------------------------------


def test_openai_adapter_translates_a_response(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers, timeout, provider):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return (
            {
                "id": "chatcmpl-abc123",
                "model": "gpt-4o-mini-2024-07-18",
                "system_fingerprint": "fp_test",
                "choices": [
                    {"message": {"content": "The answer is four."}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 5,
                    "total_tokens": 16,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
            {"x-request-id": "req-1", "x-ratelimit-remaining-requests": "99"},
        )

    monkeypatch.setattr("ashe_lab.providers._http.post_json", fake_post)

    provider = OpenAIChatProvider(api_key="sk-test", base_url="https://example.test/v1")
    response = provider.complete(
        _request("What is 2+2?", model="gpt-4o-mini", temperature=0.5, max_output_tokens=50)
    )

    assert response.text == "The answer is four."
    # model_reported is the dated snapshot, not what we asked for. This is the
    # field that answers "was this the same model?" a year later.
    assert response.model_reported == "gpt-4o-mini-2024-07-18"
    assert response.finish_reason == "stop"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 5
    assert response.usage.extra["prompt_tokens_details"] == {"cached_tokens": 0}
    assert response.provider_request_id == "chatcmpl-abc123"
    assert response.raw_metadata["response_headers"]["x-request-id"] == "req-1"

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["payload"]["temperature"] == 0.5
    assert captured["payload"]["max_tokens"] == 50
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_openai_adapter_passes_options_through_but_cannot_override_the_model(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers, timeout, provider):
        captured.update(payload)
        return ({"choices": [{"message": {"content": "x"}}]}, {})

    monkeypatch.setattr("ashe_lab.providers._http.post_json", fake_post)
    OpenAIChatProvider(api_key="k").complete(
        _request(
            "q",
            model="real-model",
            options={"reasoning_effort": "high", "model": "hijacked"},
        )
    )
    assert captured["reasoning_effort"] == "high"
    assert captured["model"] == "real-model"


def test_openai_adapter_treats_a_null_content_as_empty_text_not_an_error(monkeypatch):
    """An empty response is data. Crashing would discard a paid-for trial."""

    def fake_post(url, payload, headers, timeout, provider):
        return (
            {
                "choices": [
                    {
                        "message": {"content": None, "tool_calls": [{"id": "call_1"}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            {},
        )

    monkeypatch.setattr("ashe_lab.providers._http.post_json", fake_post)
    response = OpenAIChatProvider(api_key="k").complete(_request())
    assert response.text == ""
    assert response.raw_metadata["tool_calls_present"] is True


def test_openai_adapter_raises_retryable_when_choices_are_missing(monkeypatch):
    def fake_post(url, payload, headers, timeout, provider):
        return ({"choices": []}, {})

    monkeypatch.setattr("ashe_lab.providers._http.post_json", fake_post)
    with pytest.raises(ProviderError) as excinfo:
        OpenAIChatProvider(api_key="k").complete(_request())
    assert excinfo.value.retryable is True


def test_anthropic_adapter_hoists_the_system_prompt(monkeypatch):
    """The Messages API rejects system messages; the adapter must translate."""
    captured = {}

    def fake_post(url, payload, headers, timeout, provider):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return (
            {
                "id": "msg_01",
                "model": "claude-3-5-haiku-20241022",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": "Four."},
                    {"type": "thinking", "thinking": "hidden"},
                ],
                "usage": {"input_tokens": 20, "output_tokens": 3, "cache_read_input_tokens": 0},
            },
            {"request-id": "req-anthropic"},
        )

    monkeypatch.setattr("ashe_lab.providers._http.post_json", fake_post)

    request = ModelRequest(
        model="claude-3-5-haiku-20241022",
        messages=[
            Message(role="system", content="You are careful."),
            Message(role="user", content="What is 2+2?"),
        ],
        max_output_tokens=64,
    )
    response = AnthropicMessagesProvider(api_key="sk-ant-test").complete(request)

    assert captured["payload"]["system"] == "You are careful."
    assert [m["role"] for m in captured["payload"]["messages"]] == ["user"]
    assert captured["payload"]["max_tokens"] == 64
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert "anthropic-version" in captured["headers"]

    # Only text blocks are concatenated; other block types are noted.
    assert response.text == "Four."
    assert response.raw_metadata["content_block_types"] == ["text", "thinking"]
    assert response.usage.input_tokens == 20
    assert response.usage.total_tokens == 23
    assert response.usage.extra["cache_read_input_tokens"] == 0


def test_anthropic_adapter_supplies_a_default_max_tokens_and_records_that(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers, timeout, provider):
        captured.update(payload)
        return ({"content": [{"type": "text", "text": "ok"}], "usage": {}}, {})

    monkeypatch.setattr("ashe_lab.providers._http.post_json", fake_post)
    response = AnthropicMessagesProvider(api_key="k").complete(_request())
    assert captured["max_tokens"] > 0
    assert response.raw_metadata["max_tokens_defaulted"] is True


def test_anthropic_adapter_records_that_seed_is_unsupported(monkeypatch):
    """Silently dropping a requested parameter would be a reproducibility lie."""

    def fake_post(url, payload, headers, timeout, provider):
        assert "seed" not in payload
        return ({"content": [{"type": "text", "text": "ok"}], "usage": {}}, {})

    monkeypatch.setattr("ashe_lab.providers._http.post_json", fake_post)
    response = AnthropicMessagesProvider(api_key="k").complete(_request(seed=42))
    assert response.raw_metadata["seed_unsupported"] is True


def test_anthropic_adapter_rejects_a_system_only_request(monkeypatch):
    provider = AnthropicMessagesProvider(api_key="k")
    request = ModelRequest(
        model="m", messages=[Message(role="system", content="only system")]
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(request)
    assert excinfo.value.retryable is False
