import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio

TOURS = "/api/v1/tours"


async def test_jb_search_tours(client, api):
    api[TOURS] = fixture("tours.json")
    args = {
        "query": "کویر",
        "destination_id": 50,
        "origin_id": 4,
        "start_date": "2026-10-20",
        "end_date": "2026-10-31",
        "category": "nature",
        "tags": ["desert", "forest"],
        "duration": "multi_day",
        "min_price": 1000000,
        "max_price": 5000000,
        "difficulty": ["easy", "moderate"],
        "min_rating": 4,
        "min_free_seats": 3,
        "sort": "cheapest",
        "limit": 5,
    }
    data = (await client.call_tool("jb_search_tours", args)).structured_content
    assert (data["total"], data["last_page"]) == (113, 23)
    assert data["tours"][0] == {
        "id": "17363692",
        "title": "تور دوره کارآموزی کوهپیمایی",
        "price_per_person": 1500000,
        "days": 3,
        "nights": 0,
        "difficulty": "متوسط",
        "origin": "کرج",
        "destinations": ["کرج"],
        "next_departures": ["2026-10-08 07:00"],
        "bookable": True,
        "rating": None,  # '0.0' with no reviews
        "rating_count": 0,
        "operator": "باشگاه کوهنوردی ایلتای",
        "url": "https://jabama.tours/tour/17363692",
    }
    assert dict(api.calls[0].url.params) == {
        "page": "1",
        "per_page": "5",
        "sort_by": "price",
        "sort_order": "asc",
        "available_only": "true",
        "search": "کویر",
        "destination_location_ids": "50",
        "origin_location_ids": "4",
        "start_date": "2026-10-20",
        "end_date": "2026-10-31",
        "categories": "nature",
        "min_price": "1000000",
        "max_price": "5000000",
        "min_rating": "4.0",
        "capacity": "3",
        "tags": "desert,forest",
        "difficulty_levels": "easy,moderate",
        "duration_type": "multi_day",
    }


async def test_jb_search_tours_defaults(client, api):
    api[TOURS] = fixture("tours.json")
    await client.call_tool("jb_search_tours", {"available_only": False})
    assert dict(api.calls[0].url.params) == {
        "page": "1",
        "per_page": "12",
        "sort_by": "recommended",
        "sort_order": "desc",
    }


@pytest.mark.parametrize("key", ["start_date", "end_date"])
async def test_jb_search_tours_rejects_jalali(client, api, key):
    result = await client.call_tool("jb_search_tours", {key: "1405-07-28"})
    assert result.is_error and "Gregorian" in result.content[0].text and not api.calls


async def test_jb_tour(client, api):
    api[TOURS + "/20635501"] = fixture("tour.json")
    api[TOURS + "/20635501/reviews"] = fixture("tour_reviews.json")
    data = (await client.call_tool("jb_tour", {"tour_id": "20635501"})).structured_content
    assert data["title"] == "تور سفر به یزد | شهر خشت، بادگیر و تاریخ" and data["bookable"] is True
    assert (data["price_per_person"], data["days"], data["nights"]) == (13300000, 4, 3)
    assert data["destinations"] == ["یزد", "میبد"] and data["origin"] == "تهران"
    assert data["packages"] == [{"name": "اتاق دبل", "description": None}, {"name": "اتاق سینگل", "description": None}]
    assert len(data["departures"]) == 4
    assert data["departures"][0] == {
        "date": "2026-10-06",
        "time": "22:00",
        "past": False,
        "free_seats": 20,
        "capacity": 20,
        "minimum_group": 1,
        "minimum_reached": False,
        "sales_close_hours_before": 24,
        "package_prices": {"اتاق دبل": 13300000, "اتاق سینگل": 13700000},
    }
    assert data["itinerary"][1]["meals"] == ["breakfast"] and data["itinerary"][1]["sleep"] == "هتل"
    assert data["cancellation"][0] == {"when": "بیشتر از ۱۴ روز مانده به اجرا", "penalty_pct": 10, "refundable": True}
    assert data["guide"]["name"] == "فرزانه پورچیت ساز" and data["guide"]["experience_years"] == 10
    assert data["rating"] is None and data["rating_by_aspect"] == {}
    assert data["reviews"][0] == {
        "rating": 5,
        "text": data["reviews"][0]["text"],
        "author": "کاربر جاباماتور",
        "travelled": "2026-08-11",
        "verified": True,
    }
    assert len(data["reviews"]) == 3


async def test_jb_tour_unknown(client, api):
    api[TOURS + "/00000324"] = (404, fixture("tour_bad.json"))
    api[TOURS + "/00000324/reviews"] = (404, {"message": "تور مورد نظر یافت نشد"})
    result = await client.call_tool("jb_tour", {"tour_id": "00000324"})
    assert result.is_error and "Unknown tour id 00000324" in result.content[0].text


async def test_jb_tour_rejects_numeric_id(client, api):
    result = await client.call_tool("jb_tour", {"tour_id": "324"})  # the internal id is not accepted
    assert result.is_error and not api.calls


async def test_jb_tour_places(client, api):
    api["/api/v1/locations/search"] = fixture("tour_places.json")
    data = (
        await client.call_tool("jb_tour_places", {"query": "یزد", "direction": "origin", "limit": 3})
    ).structured_content
    assert data["places"] == [
        {"id": 50, "name": "یزد", "type": "city", "in": "یزد", "has_tours": True},
        {"id": 49, "name": "یزد", "type": "province", "in": "مرکز", "has_tours": True},
        {"id": 1302, "name": "ایزدخواست", "type": "city", "in": "فارس", "has_tours": False},
    ]
    assert dict(api.calls[0].url.params) == {"q": "یزد", "direction": "origin", "limit": "3"}
