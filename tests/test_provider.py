import asyncio

import pytest
import httpx
from unittest.mock import AsyncMock
from agent.models.provider import ModelProvider


class FakeResponse:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class FakeAsyncClient:
    payload = None
    error = None
    request_error = None

    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, json):
        if self.request_error:
            raise self.request_error

        return FakeResponse(self.payload, self.error)

    async def get(self, url):
        if self.request_error:
            raise self.request_error

        return FakeResponse(self.payload, self.error)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "invalid",
        {},
        {"choices": "invalid"},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {}, "finish_reason": 123}]},
        {"choices": [{"message": {"role": 123}}]},
        {"choices": [{"message": {"content": 123}}]},
    ],
)
def test_provider_rejects_malformed_responses(monkeypatch, payload):
    FakeAsyncClient.payload = payload

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()

    with pytest.raises(RuntimeError):
        asyncio.run(provider.complete(messages=[]))


def test_provider_accepts_valid_response(monkeypatch):
    FakeAsyncClient.payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Ciao",
                },
                "finish_reason": "stop",
            }
        ]
    }

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()
    result = asyncio.run(provider.complete(messages=[]))

    assert result["message"]["content"] == "Ciao"
    assert result["finish_reason"] == "stop"
    assert provider.metrics()["requests"] == 1
    assert provider.metrics()["successes"] == 1
    assert provider.metrics()["errors"] == 0


def test_provider_accepts_valid_tool_calls(monkeypatch):
    FakeAsyncClient.payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_workspace_file",
                                "arguments": '{"path":"result.txt"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    FakeAsyncClient.error = None
    FakeAsyncClient.request_error = None

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()
    result = asyncio.run(
        provider.complete(
            messages=[],
            tools=[{"type": "function", "function": {"name": "test"}}],
        )
    )

    assert result["finish_reason"] == "tool_calls"
    assert result["message"]["tool_calls"][0]["id"] == "call_1"


@pytest.mark.parametrize(
    "tool_calls",
    [
        "invalid",
        [None],
        [{"id": None, "function": {}}],
        [{"id": "call_1", "function": {"name": 123}}],
        [{"id": "call_1", "function": {"name": "read", "arguments": 123}}],
    ],
)
def test_provider_rejects_malformed_tool_calls(monkeypatch, tool_calls):
    FakeAsyncClient.payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    FakeAsyncClient.error = None
    FakeAsyncClient.request_error = None

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()

    with pytest.raises(RuntimeError):
        asyncio.run(provider.complete(messages=[], tools=[]))


def test_provider_propagates_http_error(monkeypatch):
    FakeAsyncClient.error = httpx.HTTPStatusError(
        "503 Service Unavailable",
        request=httpx.Request("POST", "http://localhost/v1/chat/completions"),
        response=httpx.Response(503),
    )

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.complete(messages=[]))

    FakeAsyncClient.error = None


def test_provider_propagates_connection_error(monkeypatch):
    request = httpx.Request(
        "POST",
        "http://localhost/v1/chat/completions",
    )
    FakeAsyncClient.request_error = httpx.ConnectError(
        "Connection refused",
        request=request,
    )

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()

    with pytest.raises(httpx.ConnectError):
        asyncio.run(provider.complete(messages=[]))

    FakeAsyncClient.request_error = None


def test_provider_configures_timeout(monkeypatch):
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "45")
    provider = ModelProvider()
    assert provider.timeout == 45


def test_provider_rejects_invalid_timeout(monkeypatch):
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValueError, match="between 1 and 600"):
        ModelProvider()


def test_provider_retries_transient_transport_error(monkeypatch):
    class RetryClient:
        attempts = 0

        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            type(self).attempts += 1
            if type(self).attempts == 1:
                raise httpx.ConnectError(
                    "Temporary connection failure",
                    request=httpx.Request("POST", url),
                )
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Recovered",
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            )

    monkeypatch.setattr("agent.models.provider.httpx.AsyncClient", RetryClient)
    monkeypatch.setattr(
        "agent.models.provider.asyncio.sleep",
        AsyncMock(),
    )

    provider = ModelProvider()
    result = asyncio.run(provider.complete(messages=[]))

    assert result["message"]["content"] == "Recovered"
    assert RetryClient.attempts == 2
    assert provider.metrics()["requests"] == 1
    assert provider.metrics()["attempts"] == 2
    assert provider.metrics()["retries"] == 1


def test_health_check_returns_true(monkeypatch):
    FakeAsyncClient.payload = {"data": []}
    FakeAsyncClient.error = None
    FakeAsyncClient.request_error = None

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()

    assert asyncio.run(provider.health_check()) is True


def test_health_check_returns_false_on_http_error(monkeypatch):
    FakeAsyncClient.error = httpx.HTTPStatusError(
        "500 Internal Server Error",
        request=httpx.Request("GET", "http://localhost/v1/models"),
        response=httpx.Response(500),
    )

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()

    assert asyncio.run(provider.health_check()) is False

    FakeAsyncClient.error = None


def test_health_check_returns_false_on_connection_error(monkeypatch):
    request = httpx.Request("GET", "http://localhost/v1/models")
    FakeAsyncClient.request_error = httpx.ConnectError(
        "Connection refused",
        request=request,
    )

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()

    assert asyncio.run(provider.health_check()) is False

    FakeAsyncClient.request_error = None


def test_health_check_returns_false_on_timeout(monkeypatch):
    request = httpx.Request("GET", "http://localhost/v1/models")
    FakeAsyncClient.request_error = httpx.TimeoutException(
        "Connection timed out",
        request=request,
    )

    monkeypatch.setattr(
        "agent.models.provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = ModelProvider()

    assert asyncio.run(provider.health_check()) is False

    FakeAsyncClient.request_error = None
