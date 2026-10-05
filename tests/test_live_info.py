import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_jb_help(client):
    data = await call(client, "jb_help", {"query": "کنسلی", "limit": 3})
    assert data["help_center"] and data["quick_answers"]


async def test_jb_travel_guide(client):
    data = await call(client, "jb_travel_guide", {"query": "رامسر", "limit": 2, "text_chars": 300})
    assert data["total"] > 10 and data["articles"][0]["url"].startswith("https://www.jabama.com/mag/")
    assert data["articles"][0]["text"]
