import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_jb_search_tours(client):
    data = await call(client, "jb_search_tours", {"sort": "cheapest", "min_price": 100000, "limit": 10})
    prices = [t["price_per_person"] for t in data["tours"]]
    assert data["total"] > 10 and prices == sorted(prices) and all(t["bookable"] for t in data["tours"])


async def test_jb_tour(client):
    found = await call(client, "jb_search_tours", {"sort": "soonest", "limit": 1})
    data = await call(client, "jb_tour", {"tour_id": found["tours"][0]["id"]})
    assert data["departures"] and data["departures"][0]["free_seats"] is not None
    assert data["departures"][0]["package_prices"]


async def test_jb_tour_places(client):
    data = await call(client, "jb_tour_places", {"query": "یزد"})
    assert data["places"][0]["id"] == 50 and data["places"][0]["has_tours"]
