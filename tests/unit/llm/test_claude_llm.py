import json

import httpx2 as httpx
import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from target_ai.config import Settings
from target_ai.llm import (
    ClaudeLLM,
    LLMConfigError,
    LLMRefused,
    LLMRequestError,
    LLMSchemaError,
    LLMUnavailable,
)


class Pong(BaseModel):
    answer: str


def message(text="", stop="end_turn", content=None, extra=None):
    body = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
        "content": content if content is not None else [{"type": "text", "text": text}],
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    body.update(extra or {})
    return httpx.Response(200, json=body)


def error(status, headers=None):
    return httpx.Response(
        status, headers=headers or {},
        json={"type": "error", "error": {"type": "api_error", "message": "boom"}},
    )


class Harness:
    def __init__(self, replies, strong="claude-strong-test", cheap="claude-cheap-test"):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        transport = httpx.MockTransport(self._handle)
        client = AsyncAnthropic(
            api_key="test-key", max_retries=0, http_client=httpx.AsyncClient(transport=transport)
        )
        settings = Settings(_env_file=None, model_strong=strong, model_cheap=cheap)
        self.llm = ClaudeLLM(settings, client=client, sleep=self._sleep)

    def _handle(self, request):
        self.requests.append(request)
        return self.replies.pop(0)

    async def _sleep(self, seconds):
        self.sleeps.append(seconds)

    def body(self, index=0):
        return json.loads(self.requests[index].content)


async def ask(h, tier="strong", tools=None, content="вопрос"):
    return await h.llm.structured(tier, "система", content, Pong, tools)


async def test_sends_model_by_tier_with_adaptive_thinking_effort_and_cached_system():
    h = Harness([message('{"answer": "ok"}'), message('{"answer": "ok"}')])

    result = await ask(h, "strong")
    await ask(h, "cheap")

    assert result == Pong(answer="ok")
    strong, cheap = h.body(0), h.body(1)
    assert strong["model"] == "claude-strong-test" and cheap["model"] == "claude-cheap-test"
    assert strong["thinking"] == {"type": "adaptive"}
    assert strong["output_config"]["effort"] == "high" and cheap["output_config"]["effort"] == "low"
    assert strong["output_config"]["format"]["type"] == "json_schema"
    assert strong["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in strong and "top_p" not in strong


async def test_opus_requests_server_side_fallback_with_beta_header():
    h = Harness([message('{"answer": "a"}')], strong="claude-opus-5-5")

    await ask(h, "strong")

    assert h.body()["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in h.requests[0].headers["anthropic-beta"]


async def test_other_models_do_not_send_fallbacks():
    h = Harness([message('{"answer": "a"}')])

    await ask(h, "strong")

    assert "fallbacks" not in h.body()


async def test_refusal_raises_llm_refused():
    h = Harness([message("", stop="refusal", content=[])])

    with pytest.raises(LLMRefused):
        await ask(h)


async def test_max_tokens_is_retried_with_larger_limit_then_succeeds():
    h = Harness([message('{"answ', stop="max_tokens"), message('{"answer": "long"}')])

    result = await ask(h)

    assert result.answer == "long"
    assert h.body(1)["max_tokens"] > h.body(0)["max_tokens"]


async def test_max_tokens_at_cap_raises_schema_error():
    h = Harness([message("{", stop="max_tokens")] * 5)

    with pytest.raises(LLMSchemaError):
        await ask(h)


async def test_pause_turn_continues_with_assistant_turn_appended():
    paused = message(
        "", stop="pause_turn", content=[{"type": "text", "text": "ищу..."}]
    )
    h = Harness([paused, message('{"answer": "done"}')])

    result = await ask(h, tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}])

    assert result.answer == "done"
    second = h.body(1)["messages"]
    assert [m["role"] for m in second] == ["user", "assistant"]
    assert h.body(1)["tools"][0]["type"] == "web_search_20260209"


async def test_invalid_schema_is_retried_once():
    h = Harness([message("не json"), message('{"answer": "fixed"}')])

    assert (await ask(h)).answer == "fixed"
    assert len(h.requests) == 2


async def test_invalid_schema_twice_raises_schema_error():
    h = Harness([message('{"wrong": 1}'), message('{"wrong": 2}')])

    with pytest.raises(LLMSchemaError):
        await ask(h)
    assert len(h.requests) == 2


async def test_rate_limit_is_retried_after_pause_from_header():
    h = Harness([error(429, {"retry-after": "7"}), message('{"answer": "ok"}')])

    result = await ask(h)

    assert result.answer == "ok" and h.sleeps == [7.0]


async def test_server_errors_exhaust_retries_into_unavailable():
    h = Harness([error(500)] * 4)

    with pytest.raises(LLMUnavailable):
        await ask(h)
    assert len(h.requests) == 4 and len(h.sleeps) == 3


@pytest.mark.parametrize("status", [400, 401])
async def test_client_errors_are_not_retried_and_have_russian_message(status):
    h = Harness([error(status)])

    with pytest.raises(LLMRequestError) as info:
        await ask(h)

    assert len(h.requests) == 1 and info.value.status == status
    assert any("а" <= ch <= "я" for ch in info.value.user_message_ru.lower())


async def test_network_failure_is_retried():
    def broken(request):
        raise httpx.ConnectError("нет сети")

    replies = [message('{"answer": "ok"}')]
    h = Harness(replies)
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("нет сети")
        return replies.pop(0)

    h.llm._client = AsyncAnthropic(
        api_key="k", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    )

    assert (await ask(h)).answer == "ok" and calls["n"] == 2


def test_missing_api_key_without_client_raises_config_error():
    with pytest.raises(LLMConfigError):
        ClaudeLLM(Settings(_env_file=None, anthropic_api_key=None))
