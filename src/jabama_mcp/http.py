"""Shared async HTTP client for the Jabama APIs.

Every tool goes through `fetch`, which caps concurrency, presents a real Chrome (TLS, HTTP/2 and headers via
curl_cffi; the F5 WAF in front of gw.jabama.com and www.jabama.com rejects library clients), unwraps the
gateway's `{result, success, ...}` envelope and turns HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

GW = "https://gw.jabama.com"
WWW = "https://www.jabama.com"
TOURS = "https://api.jabama.tours/api/v1"
EVENTS = "https://api.jabama.events"
EVENTS_WEB = "https://jabama.events"

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    "Accept": "application/json",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "priority": "u=1, i",
}

# host -> (page the call comes from, sec-fetch-site). The web apps live on www.jabama.com and jabama.events;
# gw/api hosts are same-site XHR targets, the www and .data calls are same-origin.
PAGES = {
    "gw.jabama.com": (WWW, "same-site"),
    "www.jabama.com": (WWW, "same-origin"),
    "api.jabama.tours": ("https://jabama.tours", "same-site"),
    "api.jabama.events": (EVENTS_WEB, "same-site"),
    "jabama.events": (EVENTS_WEB, "same-origin"),
}

MAX_CONCURRENCY = 2
JITTER = (0.15, 0.6)  # seconds before each upstream call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds
RETRY_DELAY = 0.5

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_browser_lock: asyncio.Lock | None = None
_cleared: tuple[httpx.AsyncClient, float] | None = None


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them (API calls are XHR)."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        p["headers"] = [*p["headers"], (b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        return p


class ApiError(ToolError):
    """A failed upstream call; `status` is the HTTP status (None for network errors)."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index, _browser_lock, _cleared
    _transport, _client, _limit, _proxy_index, _browser_lock, _cleared = transport, None, None, 0, None, None


def _proxies() -> list[str]:
    """JABAMA_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("JABAMA_MCP_PROXY", "").split(",") if p.strip()]


_CHALLENGE = re.compile(
    r"just a moment|captcha|challenge|arvan|attention required|enable javascript|request rejected", re.I
)


def _is_challenge(r: httpx.Response) -> bool:
    """A bot-check page: 403/429/503 with an HTML body that carries a known marker (F5 "Request Rejected")."""
    return (
        r.status_code in (403, 429, 503)
        and "html" in r.headers.get("content-type", "") + r.text[:20].lower()
        and bool(_CHALLENGE.search(r.text[:5000]))
    )


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate: a slower request that failed on an old client must not skip a good proxy.
    The old client is closed late because other requests may still be using it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(35)  # longer than the 30 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Direct calls are fastest; ignore system proxy settings unless the user opts in
        # with JABAMA_MCP_PROXY. curl_cffi presents Chrome's TLS/HTTP2 fingerprint and default headers; cookies
        # and redirects stay with httpx. The proxy goes to the transport (a client-level proxy would be
        # ignored once a transport is given).
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=httpx.Timeout(30, connect=10),
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=True,
            trust_env=False,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def fetch(
    url: str, params: dict[str, Any] | None = None, *, method: str = "GET", json: Any = None, text: bool = False
) -> Any:
    """Call an endpoint and return the parsed JSON body (gateway envelope unwrapped), or the text with text=True.

    A timeout, network error, 5xx or 429 is retried up to MAX_RETRIES times with jittered exponential backoff,
    or after Retry-After when sent (every call is read-only). Other 4xx (403 too) are never retried.
    """
    host = urlsplit(url).hostname
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = await _request(url, params, method, json, text, host)
            break
        except ApiError as e:
            if attempt == MAX_RETRIES or (e.status is not None and e.status < 500 and e.status != 429):
                raise
            wait = e.retry_after if e.retry_after is not None else RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
    if text:
        return r.text
    if r.status_code == 204 or not r.content:
        return None
    try:
        body = r.json()
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e
    return unwrap(body)


async def _send(
    client: httpx.AsyncClient,
    limit: asyncio.Semaphore,
    url: str,
    params: dict[str, Any] | None,
    method: str,
    json: Any,
    text: bool,
    host: str | None,
) -> httpx.Response:
    page, site = PAGES.get(host or "", (WWW, "same-site"))
    headers = {"Referer": f"{page}/", "sec-fetch-site": site}
    if site != "same-origin" or method != "GET":  # Chrome omits Origin on same-origin GETs
        headers["Origin"] = page
    if text:
        headers["Accept"] = "*/*"
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            return await client.request(method, url, params=params, json=json, headers=headers)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). Check the internet connection, or set JABAMA_MCP_PROXY."
        ) from e


async def _browser_cookies(proxy: str | None) -> list[dict[str, Any]]:
    """Open the home page in headless patchright, wait for the challenge to clear, return the cookies."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ApiError(
            "Jabama served a bot-check page. Install the browser extra (`pip install jabama-mcp[browser]`, or "
            "`uvx --with patchright==1.63.0 jabama-mcp`) and run `patchright install chromium`.",
            403,
        ) from e
    u = httpx.URL(proxy) if proxy else None
    opts = u and {"server": f"{u.scheme}://{u.host}:{u.port}", "username": u.username, "password": u.password}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=opts)
        try:
            page = await browser.new_page()
            await page.goto(WWW, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_function(
                "!/just a moment|request rejected|access denied|attention required/i"
                ".test(document.title + document.body.innerText.slice(0, 300))",
                timeout=30_000,
            )
            return await browser.contexts[0].cookies()
        finally:
            await browser.close()


async def _clear_challenge(client: httpx.AsyncClient) -> None:
    """Rung 2: let a real browser pass the bot check once and share its cookies with the httpx jar."""
    global _browser_lock, _cleared
    _browser_lock = _browser_lock or asyncio.Lock()
    async with _browser_lock:  # one browser at a time; callers that waited reuse the cookies just fetched
        if _cleared and _cleared[0] is client and time.monotonic() - _cleared[1] < 30:
            return
        proxies = _proxies()
        try:
            cookies = await _browser_cookies(proxies[_proxy_index % len(proxies)] if proxies else None)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f"Jabama bot check was not cleared by the browser ({type(e).__name__}).", 403) from e
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        _cleared = (client, time.monotonic())


async def _request(
    url: str, params: dict[str, Any] | None, method: str, json: Any, text: bool, host: str | None
) -> httpx.Response:
    client, limit = _get_client()
    r = await _send(client, limit, url, params, method, json, text, host)
    if _is_challenge(r):
        _next_proxy(client)
        client, limit = _get_client()
        await _clear_challenge(client)
        r = await _send(client, limit, url, params, method, json, text, host)  # one retry; a second block is final
        if _is_challenge(r):
            _next_proxy(client)
    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code, _retry_after(r))
    return r


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


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
