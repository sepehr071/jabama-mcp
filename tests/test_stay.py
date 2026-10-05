import asyncio

import httpx
import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

PID = "6087a42d15391d001b93b512"  # placeId of listing 328353
COMPLEX_PID = "6a312e9191a55de407f0256f"  # placeId of complex unit 813269
DETAIL = "/api/v1/accommodations/"
GUEST = "/api/v1/accommodation/public/guest/"
QUOTE = "/api/v1/accommodations/orders/preview"


async def test_jb_stay(client, api):
    api[DETAIL + "328353"] = fixture("detail.json")
    api[GUEST + "accommodations/amenities/328353"] = fixture("amenities.json")
    api[GUEST + f"accommodations/cancellation-policy/{PID}"] = fixture("cancellation.json")
    data = (await client.call_tool("jb_stay", {"code": 328353})).structured_content
    assert {k: data[k] for k in ("code", "url", "type", "active", "instant_booking", "city", "province")} == {
        "code": 328353,
        "url": "https://www.jabama.com/stay/ecotourism-328353",
        "type": "بوم گردی",
        "active": True,
        "instant_booking": False,
        "city": "تنکابن",
        "province": "مازندران",
    }
    assert (data["base_guests"], data["max_guests"], data["beds"]) == (2, 4, {"mattress": 4})
    assert (data["check_in_time"], data["check_out_time"], data["min_nights"]) == ("14:00", "12:00", 1)
    assert (data["rating"], data["rating_count"]) == (3.6, 7)
    assert data["from_per_night"] == 520000 and data["extra_guest_per_night"] == 150000  # Rial / 10
    assert data["review_scores"]["نظافت"] == 4.4
    assert "استعمال دخانیات مجاز نیست." in data["house_rules"]  # fragments joined
    assert data["amenities"]["رفاهی"] == ["سیستم گرمایشی (بخاری برقی)"]
    assert "پارکینگ" in data["missing_amenities"] and "استخر" in data["missing_amenities"]
    own, peak = data["cancellation"]
    assert own["policy"] == "متعادل" and own["phases"][0]["when"] == "4 روز قبل از ورود مهمان"
    assert peak["phases"][2] == {
        "when": "روز ورود به بعد",
        "deduction": "کل مبلغ رزرو کسر می\N{ZERO WIDTH NON-JOINER}شود.",
    }
    assert data["host"] == {
        "host_id": 1288856,
        "name": "فهیمه شفیعی",
        "member_since": "2021-04-20",
        "rating": 3.9,
        "review_count": 18,
        "successful_bookings": 22,
    }
    assert len(data["images"]) == 5


async def test_jb_stay_unknown_code(client, api):
    async def slow_detail(request):  # live, the amenities 404 can arrive first
        await asyncio.sleep(0.05)
        return httpx.Response(400, json=fixture("detail_bad.json"))

    api[DETAIL + "999999999"] = slow_detail
    api[GUEST + "accommodations/amenities/999999999"] = (
        404,
        {"result": {"errorCode": 404, "message": "accommodation not found"}},
    )
    result = await client.call_tool("jb_stay", {"code": 999999999})
    assert result.is_error and "Unknown listing code 999999999" in result.content[0].text
    assert len(api.calls) == 1


async def test_jb_stay_calendar(client, api):
    api[DETAIL + "328353"] = fixture("detail.json")
    api["/api/taraaz/v1/pricing/calendar/day-type"] = fixture("daytype.json")
    data = (
        await client.call_tool("jb_stay_calendar", {"code": 328353, "start": "2026-10-20", "nights": 3})
    ).structured_content
    assert data["calendar_ends"] == "2026-12-18" and (data["base_guests"], data["max_guests"]) == (2, 4)
    assert data["nights"][0] == {
        "date": "2026-10-20",
        "jalali": "1405-07-28",
        "available": True,
        "day_type": "weekday",
        "price": 600000,
        "price_before_discount": 600000,
        "extra_guest": 150000,
        "host_set_price": False,
        "min_nights": 1,
        "instant": False,
    }
    assert [n["day_type"] for n in data["nights"]] == ["weekday", "weekend", "weekend"]
    assert [n["price"] for n in data["nights"]] == [600000, 520000, 520000]


async def test_jb_stay_calendar_blocked_night_is_short(client, api):
    detail = fixture("detail.json")
    night = next(c for c in detail["result"]["item"]["calendar"] if c["date"] == "2026-10-21")
    night["status"] = "reserved"
    api[DETAIL + "328353"] = detail
    api["/api/taraaz/v1/pricing/calendar/day-type"] = fixture("daytype.json")
    data = (
        await client.call_tool("jb_stay_calendar", {"code": 328353, "start": "2026-10-20", "nights": 2})
    ).structured_content
    assert data["nights"][1] == {"date": "2026-10-21", "available": False}


@pytest.mark.parametrize(
    "start, message",
    [("1405-07-28", "Gregorian"), ("2027-06-01", "ends 2026-12-18")],
)
async def test_jb_stay_calendar_rejects_bad_start(client, api, start, message):
    api[DETAIL + "328353"] = fixture("detail.json")
    api["/api/taraaz/v1/pricing/calendar/day-type"] = fixture("daytype.json")
    result = await client.call_tool("jb_stay_calendar", {"code": 328353, "start": start})
    assert result.is_error and message in result.content[0].text


async def test_jb_price_quote(client, api):
    api[DETAIL + "328353"] = fixture("detail.json")
    api[QUOTE] = fixture("quote.json")
    args = {"code": 328353, "check_in": "2026-10-20", "check_out": "2026-10-23", "guests": 4}
    data = (await client.call_tool("jb_price_quote", args)).structured_content
    assert {k: data[k] for k in ("check_in", "check_out", "nights", "guests", "payable", "price_before_discount")} == {
        "check_in": "2026-10-20 14:00",
        "check_out": "2026-10-23 12:00",
        "nights": 3,
        "guests": 4,
        "payable": 2540000,  # 25,400,000 Rial = 3 nights + 2 extra guests x 3 nights x 1.5M
        "price_before_discount": 2540000,
    }
    assert data["installment_4x"] == 635000 and data["instant_booking"] is False
    assert data["per_night"][1] == {
        "date": "2026-10-21",
        "day_type": "weekend",
        "night": 520000,
        "extra_guest_each": 150000,
        "total": 820000,
    }
    assert data["cancellation"]["phases"][0]["when"] == "تا 24 مهر"
    assert [p["until"] for p in data["cancellation"]["phases"]] == ["2026-10-16", "2026-10-20", "2026-10-23"]
    assert "unit_options" not in data
    assert body(api.calls[1]) == {
        "accommodationId": PID,
        "checkIn": "2026-10-20",
        "checkOut": "2026-10-23",
        "passengers": {"adults": 4, "children": 0},
    }


async def test_jb_price_quote_complex_unit_options(client, api):
    api[DETAIL + "813269"] = fixture("detail_complex.json")
    api[QUOTE] = fixture("quote_complex.json")
    api[GUEST + "orders/variant"] = fixture("variants.json")
    args = {"code": 813269, "check_in": "2026-10-20", "check_out": "2026-10-23", "guests": 4}
    data = (await client.call_tool("jb_price_quote", args)).structured_content
    assert data["payable"] == 83385000 and data["instant_booking"] is True
    assert [(u["units"], u["price"]) for u in data["unit_options"]] == [(1, 83385000), (2, 94770000)]
    variant = next(r for r in api.calls if r.url.path.endswith("/orders/variant"))
    assert body(variant) == {
        "accommodationId": COMPLEX_PID,
        "checkIn": "2026-10-20",
        "checkOut": "2026-10-23",
        "passengersCount": 4,
    }


async def test_jb_price_quote_unbookable_dates(client, api):
    api[DETAIL + "328353"] = fixture("detail.json")
    api[QUOTE] = (400, fixture("quote_past.json"))
    args = {"code": 328353, "check_in": "2026-12-16", "check_out": "2026-12-18"}
    result = await client.call_tool("jb_price_quote", args)
    text = result.content[0].text
    assert result.is_error and "امکان ثبت سفارش" in text and "jb_stay_calendar" in text and ".." not in text


async def test_jb_price_quote_beyond_calendar(client, api):
    api[DETAIL + "328353"] = fixture("detail.json")  # calendar ends 2026-12-18
    args = {"code": 328353, "check_in": "2026-12-17", "check_out": "2026-12-20"}
    result = await client.call_tool("jb_price_quote", args)
    assert result.is_error and "check_out can be 2026-12-18 at the latest" in result.content[0].text
    assert len(api.calls) == 1  # no quote request


async def test_jb_similar_stays_with_dates(client, api):
    api[DETAIL + "328353"] = fixture("detail.json")
    api[f"/api/v2/accommodations/{PID}/similar"] = fixture("similar.json")
    api[GUEST + f"accommodations/{PID}/other-rooms"] = fixture("other_rooms.json")
    api["/api/taraaz/v4/search/merchandising/legacy-plp/all"] = fixture("plp_codes.json")
    args = {"code": 328353, "check_in": "2026-10-20", "check_out": "2026-10-23", "guests": 2, "limit": 5}
    data = (await client.call_tool("jb_similar_stays", args)).structured_content
    assert [(s["code"], s["total_price"]) for s in data["similar"]] == [
        (364106, 1960000),
        (363623, 5400000),
        (364107, 6000000),
        (83440, 6150000),
    ]
    assert data["not_free_for_dates"] == [328354]
    assert data["same_property"]["keyword"] == "list-33183"
    assert data["same_property"]["units"][0] == {
        "code": 328354,
        "title": "نسا - بارانک",
        "max_guests": 8,
        "from_per_night": 3000000,
        "rating": 4.1,
        "rating_count": 11,
    }
    plp = body(next(r for r in api.calls if r.url.path.endswith("/legacy-plp/all")))
    assert plp["codes"] == [328354, 363623, 364106, 364107, 83440] and plp["capacity"] == 2
    assert plp["date"] == {"start": 20261020, "end": 20261023}


async def test_jb_similar_stays_default_night(client, api):
    api[DETAIL + "328353"] = fixture("detail.json")
    api[f"/api/v2/accommodations/{PID}/similar"] = fixture("similar.json")
    api[GUEST + f"accommodations/{PID}/other-rooms"] = fixture("other_rooms.json")
    data = (await client.call_tool("jb_similar_stays", {"code": 328353, "limit": 2})).structured_content
    assert data["similar"][0] == {
        "code": 328354,
        "title": "نسا - بارانک",
        "type": "ecotourism",
        "city": "تنکابن",
        "rating": 4.1,
        "rating_count": 11,
        "max_guests": 8,
        "instant_booking": False,
        "price_one_night": 3000000,
        "url": "https://www.jabama.com/stay/ecotourism-328354",
    }
    assert len(data["similar"]) == 2


async def test_jb_similar_stays_guests_without_dates(client, api):
    api[DETAIL + "328353"] = fixture("detail.json")
    api[f"/api/v2/accommodations/{PID}/similar"] = fixture("similar.json")
    api[GUEST + f"accommodations/{PID}/other-rooms"] = fixture("other_rooms.json")
    data = (await client.call_tool("jb_similar_stays", {"code": 328353, "guests": 9})).structured_content
    assert [(s["code"], s["max_guests"]) for s in data["similar"]] == [(363623, 10), (364107, 10)]


async def test_jb_similar_stays_inactive_listing(client, api):
    detail = fixture("detail.json")
    detail["result"]["item"]["status"] = "disabled"
    api[DETAIL + "328353"] = detail
    result = await client.call_tool("jb_similar_stays", {"code": 328353})
    assert result.is_error and "inactive" in result.content[0].text and len(api.calls) == 1


async def test_jb_reviews_unknown_code(client, api):
    api["/api/v2/reviews/place/999999999"] = {"reviews": []}
    api[DETAIL + "999999999"] = (400, fixture("detail_bad.json"))
    result = await client.call_tool("jb_reviews", {"code": 999999999})
    assert result.is_error and "Unknown listing code 999999999" in result.content[0].text


async def test_jb_reviews(client, api):
    api["/api/v2/reviews/place/328353"] = fixture("place_reviews.json")
    data = (
        await client.call_tool("jb_reviews", {"code": 328353, "sort": "lowest_rated", "limit": 3})
    ).structured_content
    first, second, _ = data["reviews"]
    assert first["rating"] == 2 and first["author"] == "طاهره" and first["host_reply"] is None
    assert first["stayed"] == "فروردین 1403" and first["stay_length"] == "1 شب اقامت در اقامتگاه"
    assert second["host_reply"].startswith("سپاس از نظریات")
    assert data["has_more"] is True
    assert dict(api.calls[0].url.params) == {"page": "1", "size": "3", "sortBy": "LowestRate"}


async def test_jb_host(client, api):
    api["/api/v2/reviews/host/1288856"] = fixture("host_reviews.json")
    api["/api/v1/guest/host/1288856/accommodation"] = fixture("host_listings.json")
    data = (await client.call_tool("jb_host", {"host_id": 1288856, "limit": 3})).structured_content
    assert data["review_count"] == 18 and data["listings_count"] == 2 and data["has_more"] is True
    assert data["listings"][0] == {
        "code": 328353,
        "title": "نسا - افرا",
        "type": "ecotourism",
        "place": "استان مازندران، تنکابن",
        "rating": 3.6,
        "rating_count": 7,
        "base_guests": 2,
        "max_guests": 4,
        "from_per_night": 520000,
    }
    assert data["reviews"][0]["rating"] == 1 and data["reviews"][0]["listing"] == "نسا - بارانک"


async def test_jb_host_unknown(client, api):
    api["/api/v2/reviews/host/999999999"] = fixture("host_unknown_reviews.json")
    api["/api/v1/guest/host/999999999/accommodation"] = fixture("host_unknown_listings.json")
    result = await client.call_tool("jb_host", {"host_id": 999999999})
    assert result.is_error and "Unknown host" in result.content[0].text
