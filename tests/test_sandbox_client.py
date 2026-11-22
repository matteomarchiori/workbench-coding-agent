import asyncio

import pytest

from agent.sandbox_client import SandboxClient


class FakeResponse:
    content = (
        b'{"passed":true,"checks":[{"name":"pytest","exit_code":0,'
        b'"passed":true,"timed_out":false,"duration_seconds":0},'
        b'{"name":"python_syntax","exit_code":0,"passed":true,'
        b'"timed_out":false,"duration_seconds":0}]}'
    )

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "passed": True,
            "checks": [
                {
                    "name": "pytest",
                    "exit_code": 0,
                    "passed": True,
                    "timed_out": False,
                    "duration_seconds": 0,
                },
                {
                    "name": "python_syntax",
                    "exit_code": 0,
                    "passed": True,
                    "timed_out": False,
                    "duration_seconds": 0,
                },
            ],
        }


def test_sandbox_client_sends_bearer_token(monkeypatch):
    observed = {}

    class FakeAsyncClient:
        def __init__(self, timeout):
            observed["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers):
            observed["url"] = url
            observed["headers"] = headers
            return FakeResponse()

    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox:8002/")
    monkeypatch.setenv("SANDBOX_TOKEN", "a" * 40)
    monkeypatch.setattr("agent.sandbox_client.httpx.AsyncClient", FakeAsyncClient)

    client = SandboxClient()
    result = asyncio.run(client.run_checks())

    assert observed["url"] == "http://sandbox:8002/checks"
    assert observed["headers"]["Authorization"] == f"Bearer {'a' * 40}"
    assert '"passed": true' in result


def test_sandbox_client_rejects_disabled_or_invalid_configuration(monkeypatch):
    monkeypatch.setenv("SANDBOX_ENABLED", "maybe")

    with pytest.raises(ValueError, match="must be true or false"):
        SandboxClient()

    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_TOKEN", "short")

    with pytest.raises(ValueError, match="at least 32 ASCII characters"):
        SandboxClient()


def test_sandbox_client_rejects_invalid_response(monkeypatch):
    class InvalidResponse(FakeResponse):
        def json(self):
            return {"checks": []}

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers):
            return InvalidResponse()

    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_TOKEN", "a" * 40)
    monkeypatch.setattr("agent.sandbox_client.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(RuntimeError, match="invalid response"):
        asyncio.run(SandboxClient().run_checks())
