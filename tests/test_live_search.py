from datetime import timedelta

import pytest
from conftest import live_call as call

from jabama_mcp.search import today

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_jb_find_destination(client):
    data = await call(client, "jb_find_destination", {"query": "رامسر"})
    assert data["destinations"][0]["search"] == {"keyword": "city-ramsar"}


async def test_jb_parse_request(client):
    data = await call(client, "jb_parse_request", {"text": "ویلای استخردار در رامسر برای ۶ نفر"})
    s = data["search"]
    assert s["types"] == ["villa"] and s["cities"] == ["ramsar"] and s["guests"] == 6


async def test_jb_search_stays(client):
    start = today() + timedelta(days=16)
    args = {
        "keyword": "city-ramsar",
        "check_in": str(start),
        "check_out": str(start + timedelta(days=3)),
        "guests": 2,
        "sort": "cheapest",
        "limit": 10,
    }
    data = await call(client, "jb_search_stays", args)
    prices = [s["total_price"] for s in data["stays"]]
    assert data["total"] > 100 and prices == sorted(prices) and prices[0] > 0
    assert all(s["nights"] == 3 for s in data["stays"])


async def test_jb_categories(client):
    data = await call(client, "jb_categories", {})
    assert {"keyword": "all", "types": ["villa"]} in [c["search"] for c in data["categories"]]
