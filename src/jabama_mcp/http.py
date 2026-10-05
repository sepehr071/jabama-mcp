"""Shared async HTTP client for the Jabama APIs.

Every tool goes through `fetch`, which caps concurrency, sends a browser User-Agent (the F5 WAF in
front of gw.jabama.com and www.jabama.com rejects library User-Agents), unwraps the gateway's
`{result, success, ...}` envelope and turns HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.mcpserver.exceptions import ToolError

GW = "https://gw.jabama.com"
WWW = "https://www.jabama.com"
TOURS = "https://api.jabama.tours/api/v1"
EVENTS = "https://api.jabama.events"
EVENTS_WEB = "https://jabama.events"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

MAX_CONCURRENCY = 4

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None


class ApiError(ToolError):
    """A failed upstream call; `status` is the HTTP status (None for network errors)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit
    _transport, _client, _limit = transport, None, None


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Direct calls are fastest; ignore system proxy settings unless the user opts in
        # with JABAMA_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=30,
            follow_redirects=True,
            trust_env=False,
            proxy=os.environ.get("JABAMA_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def fetch(
    url: str, params: dict[str, Any] | None = None, *, method: str = "GET", json: Any = None, text: bool = False
) -> Any:
    """Call an endpoint and return the parsed JSON body (gateway envelope unwrapped), or the text with text=True."""
    client, limit = _get_client()
    host = urlsplit(url).hostname
    r = None
    # A connection reset on the first try is usually transient; one retry after a short pause fixes it.
    for attempt in range(2):
        try:
            async with limit:
                r = await client.request(method, url, params=params, json=json)
            break
        except httpx.TimeoutException as e:
            raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
        except httpx.RequestError as e:
            if attempt == 0 and isinstance(e, httpx.ConnectError):
                await asyncio.sleep(1)
                continue
            raise ApiError(
                f"Could not reach {host} ({type(e).__name__}). Check the internet connection, or set JABAMA_MCP_PROXY."
            ) from e
    assert r is not None

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code)
    if text:
        return r.text
    if r.status_code == 204 or not r.content:
        return None
    try:
        body = r.json()
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e
    return unwrap(body)


def unwrap(body: Any) -> Any:
    """gw.jabama.com wraps most bodies in {result, success, error, ...}; return `result`."""
    if isinstance(body, dict) and "result" in body and ("success" in body or "__wrapped" in body):
        return body["result"]
    return body


def _status_message(r: httpx.Response, host: str | None) -> str:
    code = r.status_code
    if code == 403 and "Request Rejected" in r.text:
        return (
            f"{host} blocked the request (HTTP 403, web firewall). Wait a minute; if it persists, "
            "turn off the VPN or set JABAMA_MCP_PROXY to another proxy."
        )
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    detail = error_text(r)
    if code == 404:
        return f"Not found on {host} (HTTP 404): {detail}. Check the id or code."
    if code >= 500:
        return f"{host} had a server error (HTTP {code}): {detail}. Try again later."
    return f"{host} rejected the request (HTTP {code}): {detail}"


def error_text(r: httpx.Response) -> str:
    """The most useful message from the many error shapes of the Jabama hosts."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    if isinstance(body, dict):
        err, res = body.get("error"), body.get("result")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(res, dict):
            errors = res.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                return f"{errors[0].get('message')} ({errors[0].get('location')})"[:300]
            if res.get("message"):
                return str(res["message"])[:300]
        if isinstance(res, str):
            return res[:300]
        if body.get("message"):
            return str(body["message"])[:300]
    return str(body)[:300]
