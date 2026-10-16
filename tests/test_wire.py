import json

import httpx
import pytest
from mcp import Client
from mcp.types import TextContent

from jabama_mcp import http
from jabama_mcp.server import mcp

pytestmark = pytest.mark.anyio


async def test_wire_format_is_one_compact_text_block():
    """Real client without the conftest shim: one TextContent, no structuredContent, no outputSchema."""
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})))
    async with Client(mcp, raise_exceptions=True) as c:
        tools = (await c.list_tools()).tools
        assert tools and all(t.output_schema is None for t in tools)
        assert not any("title" in p for t in tools for p in t.input_schema.get("properties", {}).values())
        result = await c.call_tool("jb_travel_guide", {"query": "رامسر"})
    assert result.structured_content is None
    assert len(result.content) == 1 and isinstance(result.content[0], TextContent)
    text = result.content[0].text
    assert text == json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))


async def test_gateway_error_is_retried_once(monkeypatch):
    calls = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr(http.asyncio, "sleep", no_sleep)

    def handler(r):
        calls.append(r)
        return httpx.Response(502 if len(calls) == 1 else 200, json={"ok": 1})

    http.set_transport(httpx.MockTransport(handler))
    assert await http.fetch("https://x.test/a") == {"ok": 1} and len(calls) == 2
