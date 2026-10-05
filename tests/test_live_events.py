import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def seated_event(client) -> dict:
    data = await call(client, "jb_search_events", {"city": "tehran", "limit": 100})
    return next(e for e in data["events"] if e["seated"])


async def test_jb_search_events(client):
    data = await call(client, "jb_search_events", {"city": "tehran", "price": "paid", "sort": "cheapest", "limit": 10})
    prices = [e["price_after_discount"] for e in data["events"]]
    assert data["total"] > 0 and prices == sorted(prices) and all(p > 0 for p in prices)


async def test_jb_event(client):
    event = await seated_event(client)
    data = await call(client, "jb_event", {"event_id": event["id"]})
    assert data["title"] and data["sessions"] and data["sessions"][0]["session_id"]


async def test_jb_event_seats(client):
    event = await seated_event(client)
    detail = await call(client, "jb_event", {"event_id": event["id"]})
    session = next(s for s in detail["sessions"] if s["seated"])
    data = await call(client, "jb_event_seats", {"session_id": session["session_id"]})
    assert data["seat_count"] > 0 and 0 <= data["free_seats"] <= data["seat_count"] and data["sections"]


async def test_jb_theaters(client):
    data = await call(client, "jb_theaters", {"limit": 3})
    assert data["plays"] and all(p["from_price"] for p in data["plays"])


async def test_jb_theater(client):
    plays = (await call(client, "jb_theaters", {"limit": 20}))["plays"]
    for play in plays:
        data = await call(client, "jb_theater", {"play_id": play["id"]})
        if data["showtimes"]:
            break
    assert data["showtimes"] and data["showtimes"][0]["showtime_id"].startswith(f"{data['id']}:")


async def test_jb_event_filters(client):
    data = await call(client, "jb_event_filters", {"city": "tehran"})
    assert data["cities"][0]["slug"] == "tehran" and data["categories"]
