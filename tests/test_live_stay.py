from datetime import timedelta

import pytest
from conftest import live_call as call

from jabama_mcp.search import today

pytestmark = [pytest.mark.anyio, pytest.mark.live]

CODE = 328353  # eco-lodge in Tonekabon, host 1288856


def stay_dates(offset: int = 16, nights: int = 3) -> dict:
    start = today() + timedelta(days=offset)
    return {"check_in": str(start), "check_out": str(start + timedelta(days=nights))}


async def test_jb_stay(client):
    data = await call(client, "jb_stay", {"code": CODE})
    assert data["code"] == CODE and data["max_guests"] >= data["base_guests"] > 0
    assert data["cancellation"] and data["host"]["host_id"] and data["house_rules"]


async def test_jb_stay_calendar(client):
    data = await call(client, "jb_stay_calendar", {"code": CODE, "nights": 14})
    assert len(data["nights"]) == 14 and data["nights"][0]["date"] == str(today())
    assert all(n["day_type"] for n in data["nights"] if n["available"])  # blocked nights are just date + available


async def test_jb_price_quote(client):
    found = await call(client, "jb_search_stays", {"keyword": "city-ramsar", **stay_dates(), "guests": 2, "limit": 1})
    code = found["stays"][0]["code"]
    data = await call(client, "jb_price_quote", {"code": code, **stay_dates(), "guests": 2})
    assert data["nights"] == 3 and len(data["per_night"]) == 3 and data["payable"] > 0


async def test_jb_similar_stays(client):
    data = await call(client, "jb_similar_stays", {"code": CODE, "limit": 5})
    assert data["similar"] and all(s["code"] for s in data["similar"])


async def test_jb_reviews(client):
    data = await call(client, "jb_reviews", {"code": CODE, "sort": "lowest_rated", "limit": 3})
    ratings = [r["rating"] for r in data["reviews"]]
    assert ratings and ratings == sorted(ratings)


async def test_jb_host(client):
    data = await call(client, "jb_host", {"host_id": 1434447, "limit": 5})
    assert data["review_count"] > 100 and len(data["reviews"]) == 5 and data["listings"]
