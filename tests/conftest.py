import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from mcp import Client

from jabama_mcp import http, search
from jabama_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = date(2026, 10, 4)  # the day the fixtures were recorded


def fixture(name: str):
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


async def live_call(client, name: str, args: dict) -> dict:
    """Call a tool against the real site, about one request per second, and fail on tool errors."""
    await asyncio.sleep(1)
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client(monkeypatch):
    """The shared httpx client is bound to one event loop; each test gets a new loop."""
    monkeypatch.setattr(http, "RETRY_DELAY", 0)
    monkeypatch.setattr(http, "JITTER", (0, 0))
    http.set_transport(None)
    yield
    http.set_transport(None)


@pytest.fixture
def api(monkeypatch):
    """Route table for a fake upstream keyed by URL path: api["/path"] = JSON body, (status, JSON body),
    a text body (turbo-stream pages), or a callable(request) -> httpx.Response.

    Unknown paths return 404. Every request is appended to api.calls so tests can assert on what was sent.
    """

    class Routes(dict):
        calls: list[httpx.Request]

    routes = Routes()
    routes.calls = []
    # Fixture dates are judged against the day they were recorded.
    monkeypatch.setattr(search, "today", lambda: RECORDED)

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(404, json={"error_msg": "404 Route Not Found"})
        if callable(target):
            return target(request)
        if isinstance(target, tuple):
            return httpx.Response(target[0], json=target[1])
        if isinstance(target, str):
            return httpx.Response(200, text=target, headers={"content-type": "text/x-script"})
        return httpx.Response(200, json=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def compact_as_structured(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = compact_as_structured
        yield c
