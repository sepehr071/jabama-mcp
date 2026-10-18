import asyncio
import json
import sys

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


def test_is_challenge():
    html = {"content-type": "text/html"}
    assert http._is_challenge(httpx.Response(403, text="<html>Request Rejected</html>", headers=html))
    assert http._is_challenge(httpx.Response(503, text="<!doctype html><title>ArvanCloud</title>"))
    assert not http._is_challenge(httpx.Response(403, text="<html>Forbidden</html>", headers=html))
    assert not http._is_challenge(httpx.Response(403, json={"error": "no"}))
    assert not http._is_challenge(httpx.Response(200, text="<html>Just a moment</html>", headers=html))


async def test_frontend_headers_per_host():
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={"ok": 1})

    http.set_transport(httpx.MockTransport(handler))
    for url in ("https://gw.jabama.com/a", "https://www.jabama.com/b", "https://jabama.events/c.data"):
        await http.fetch(url)
    gw, www, ev = (c.headers for c in calls)
    assert (gw["origin"], gw["referer"], gw["sec-fetch-site"]) == (http.WWW, http.WWW + "/", "same-site")
    assert "origin" not in www and www["sec-fetch-site"] == "same-origin"
    assert ev["referer"] == "https://jabama.events/" and ev["sec-fetch-site"] == "same-origin"
    assert gw["accept-language"].startswith("fa-IR") and gw["sec-fetch-mode"] == "cors"


async def test_retries_are_capped_and_403_is_not_retried():
    n = []
    http.set_transport(httpx.MockTransport(lambda r: n.append(1) or httpx.Response(503, json={})))
    with pytest.raises(http.ApiError):
        await http.fetch("https://x.test/a")
    assert len(n) == 3
    n.clear()
    http.set_transport(httpx.MockTransport(lambda r: n.append(1) or httpx.Response(403, text="nope")))
    with pytest.raises(http.ApiError):
        await http.fetch("https://x.test/a")
    assert len(n) == 1


async def test_proxy_list_rotates_and_stale_client_does_not(monkeypatch):
    monkeypatch.setenv("JABAMA_MCP_PROXY", "http://p1:1, http://p2:2")
    assert http._proxies() == ["http://p1:1", "http://p2:2"]
    old, _ = http._get_client()
    http._next_proxy(old)
    new, _ = http._get_client()
    assert http._proxy_index == 1 and new is not old
    http._next_proxy(old)  # a late failure on the retired client must not skip the proxy now in use
    assert http._proxy_index == 1 and http._client is new


async def test_challenge_without_patchright(monkeypatch):
    monkeypatch.setitem(sys.modules, "patchright.async_api", None)
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(403, text="<html>Request Rejected</html>")))
    with pytest.raises(http.ApiError, match=r"jabama-mcp\[browser\].*patchright install chromium"):
        await http.fetch("https://gw.jabama.com/a")


async def test_challenge_cleared_by_browser_once(monkeypatch):
    runs = []

    async def fake_browser(proxy):
        runs.append(proxy)
        await asyncio.sleep(0.01)
        return [{"name": "clearance", "value": "ok", "domain": ".jabama.com", "path": "/"}]

    monkeypatch.setattr(http, "_browser_cookies", fake_browser)

    def guarded(r):
        if "clearance=ok" not in r.headers.get("cookie", ""):
            return httpx.Response(403, text="<html>Request Rejected</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, json={"ok": 1})

    http.set_transport(httpx.MockTransport(guarded))
    await asyncio.gather(http.fetch("https://gw.jabama.com/a"), http.fetch("https://gw.jabama.com/b"))
    assert len(runs) == 1


async def test_retry_after_is_honoured_and_capped(monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)
    hits = []

    def limited(r):
        hits.append(1)
        if len(hits) == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "3"})
        if len(hits) == 2:
            return httpx.Response(503, json={}, headers={"Retry-After": "999"})
        return httpx.Response(200, json={"ok": 1})

    http.set_transport(httpx.MockTransport(limited))
    assert await http.fetch("https://x.test/a") == {"ok": 1}
    assert [w for w in waits if w] == [3, http.MAX_RETRY_WAIT]
