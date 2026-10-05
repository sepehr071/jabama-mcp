import httpx
import pytest
from conftest import fixture

from jabama_mcp.events import turbo_decode

pytestmark = pytest.mark.anyio

EVENT = "67058754"
SESSION = "90611730"
PLAY = "65745"
SHOWTIME = "65745:642277"


async def test_jb_search_events(client, api):
    page = fixture("events.json")
    page["items"][1].update({"rating": "۴.۵", "reviewCount": 2, "pricingMode": "per_unit", "unitPrice": 3000000})
    api["/events"] = page
    args = {
        "query": "کنسرت",
        "city": "tehran",
        "category": "event-and-workshop",
        "kind": "events",
        "on_date": "2026-10-10",
        "price": "paid",
        "rated_4_plus": True,
        "sort": "cheapest",
        "limit": 3,
        "cursor": "abc",
    }
    data = (await client.call_tool("jb_search_events", args)).structured_content
    assert data["total"] == 104 and data["next_cursor"].startswith("eyJzIjoi")
    assert data["events"][0] == {
        "id": "02607532",
        "title": "وضعیت سفید",
        "category": "ایونت و ورکشاپ",
        "city": "تهران",
        "venue": "برج گذر سامان",
        "when": "ساعات بازدید: ۱0 - ۲2",
        "next_session": "2026-10-04 11:00",  # 07:30 UTC = 11:00 Tehran
        "pricing": "per_person",
        "price": 0,
        "price_after_discount": 0,
        "rating": None,  # '۰.۰'
        "review_count": 0,
        "seated": False,
        "organizer": "جاباما ایونت",
        "url": "https://jabama.events/events/02607532",
    }
    second = data["events"][1]
    assert second["rating"] == 4.5 and second["pricing"] == "per_unit" and second["price"] == 3000000
    assert dict(api.calls[0].url.params) == {
        "hasActiveSession": "true",
        "limit": "3",
        "q": "کنسرت",
        "city": "tehran",
        "category": "event-and-workshop",
        "cursor": "abc",
        "type": "events",
        "date": "2026-10-10",
        "price": "paid",
        "rating": "4",
        "sort": "price",
    }


async def test_jb_search_events_rejects_jalali(client, api):
    result = await client.call_tool("jb_search_events", {"on_date": "1405-07-28"})
    assert result.is_error and "Gregorian" in result.content[0].text and not api.calls


async def test_jb_search_events_day_placeholder_time(client, api):
    page = fixture("events.json")
    page["items"][0]["firstAvailableSessionStartIso"] = "2026-10-05T00:30:00.000Z"  # gallery day session
    api["/events"] = page
    data = (await client.call_tool("jb_search_events", {})).structured_content
    assert data["events"][0]["next_session"] == "2026-10-05" and data["events"][1]["next_session"] == "2026-10-21 16:00"


async def test_jb_event(client, api):
    api[f"/events/{EVENT}"] = fixture("event.json")
    api[f"/events/{EVENT}/sessions"] = fixture("sessions.json")
    api["/organizers/or-0918848"] = fixture("organizer.json")
    data = (await client.call_tool("jb_event", {"event_id": EVENT})).structured_content
    assert data["url"] == f"https://jabama.events/events/{EVENT}"
    assert (data["price"], data["pricing"], data["refundable"], data["vat_pct"]) == (400000, "per_person", False, 10.0)
    assert (data["rating"], data["review_count"]) == (4.8, 6)
    assert data["venue"] == "پردیس سینمایی بام نیایش" and data["location"]["lat"] == pytest.approx(35.7647, abs=1e-3)
    assert "<" not in data["description"] and data["artists"][0] == "هومن خیاط"
    assert data["sessions"] == [
        {
            "session_id": SESSION,
            "starts": "2026-10-09 15:00",
            "duration_hours": 2.5,
            "bookable": True,
            "seated": True,
            "seats_left": 398,
            "capacity": 413,
        }
    ]
    assert data["more_sessions"] == 0 and len(data["reviews"]) == 3
    assert data["organizer"] == {
        "name": "تهران دابشو",
        "verified": False,
        "rating": 4.8,
        "review_count": 6,
        "open_events": 1,
        "links": [
            "https://www.instagram.com/tehrandubshow/",
            "https://tehrandubshow.com/",
            "https://www.youtube.com/@TehranDubShow",
        ],
        "page": "https://jabama.events/tehrandubshow",
    }


async def test_jb_event_timeslots_and_unknown(client, api):
    event = fixture("event.json")
    event["organizer"] = {}
    api["/events/legacy-622"] = event
    api["/events/legacy-622/sessions"] = fixture("sessions_timeslots.json")
    data = (await client.call_tool("jb_event", {"event_id": "legacy-622", "max_sessions": 1})).structured_content
    assert data["more_sessions"] == 1 and data["organizer"] is None
    assert data["sessions"][0]["timeslots"] == [
        {"starts": "2026-10-04 08:00", "ends": "2026-10-04 19:00", "seats_left": 50, "bookable": True}
    ]
    api["/events/00000000"] = (404, fixture("event_bad.json"))
    api["/events/00000000/sessions"] = (404, fixture("event_bad.json"))
    result = await client.call_tool("jb_event", {"event_id": "00000000"})
    assert result.is_error and "Unknown event id" in result.content[0].text


async def test_jb_event_seats(client, api):
    api[f"/sessions/{SESSION}/seat-layout"] = fixture("seat_layout.json")
    api[f"/sessions/{SESSION}/seat-status"] = fixture("seat_status.json")
    data = (await client.call_tool("jb_event_seats", {"session_id": SESSION})).structured_content
    assert (data["seat_count"], data["free_seats"]) == (413, 398)  # 15 sold bits, = remainingSeats
    assert data["starts"] == "2026-10-09 15:00" and data["price"] == 400000
    assert data["cheapest_free_seat_price"] == 500000  # the base price buys no seat
    assert data["sections"] == [
        {"name": "بخش ۱ — ردیف ۱", "price": 550000, "seats": 23, "free": 18},
        {"name": data["sections"][1]["name"], "price": 500000, "seats": 46, "free": 41},
    ]
    row1 = data["free_by_row"]["بخش ۱ — ردیف ۱ / row 1"]
    assert row1 == ["21", "20", "19", "18", "17", "16", "15", "11", "10", "9", "8", "7", "6", "5", "4", "3", "2", "1"]


async def test_jb_event_seats_general_admission(client, api):
    api["/sessions/1/seat-layout"] = (404, fixture("seat_404.json"))
    api["/sessions/1/seat-status"] = (404, fixture("seat_404.json"))
    result = await client.call_tool("jb_event_seats", {"session_id": "1"})
    assert result.is_error and "general admission" in result.content[0].text


async def test_jb_theaters(client, api):
    api["/zirbana/events"] = fixture("theaters.json")
    data = (await client.call_tool("jb_theaters", {"limit": 2, "cursor": "64000"})).structured_content
    assert data["next_cursor"] == "65165"
    play = data["plays"][0]
    assert {k: play[k] for k in ("id", "title", "url", "from_price", "rating", "rating_count")} == {
        "id": "64327",
        "title": "منو یادت میاد؟",
        "url": "https://jabama.events/theaters/64327",
        "from_price": 400000,
        "rating": 4.1,
        "rating_count": 199,
    }
    assert play["crew"]["بازیگران"] == ["مرتضی درویش زاده"] and len(play["crew"]) <= 6
    assert dict(api.calls[0].url.params) == {"limit": "2", "cursor": "64000"}


async def test_jb_theater_with_seats(client, api):
    api[f"/zirbana/events/{PLAY}"] = fixture("theater.json")
    api[f"/theaters/{PLAY}/book.data"] = fixture("theater_book.data")
    api[f"/zirbana/events/{PLAY}/session-summaries"] = fixture("theater_summaries.json")
    api[f"/zirbana/events/{PLAY}/instances/{SHOWTIME}/seatmap"] = fixture("seatmap.json")
    api[f"/zirbana/events/{PLAY}/instances/{SHOWTIME}/availability"] = fixture("availability.json")
    data = (await client.call_tool("jb_theater", {"play_id": PLAY, "showtime_id": SHOWTIME})).structured_content
    assert data["on_sale"] is True and data["from_price"] == 500000 and data["rating"] is None
    assert [s["showtime_id"] for s in data["showtimes"]] == ["65745:642277", "65745:642278", "65745:642279"]
    assert data["showtimes"][0] == {
        "showtime_id": SHOWTIME,
        "starts": "2026-10-11 19:45",
        "title": data["showtimes"][0]["title"],
        "price": 500000,
        "seats_left": 40,
        "capacity": 52,
        "live": True,
        "note": data["showtimes"][0]["note"],
    }
    seats = data["seats"]
    assert seats["free_seats"] == 40 and seats["prices"] == [500000]  # 52 - 4 blocked - 8 taken
    assert seats["free_by_row"]["section 1 / row 1"] == ["8", "7", "2", "1"]
    summaries = next(r for r in api.calls if r.url.path.endswith("/session-summaries"))
    assert summaries.url.params["ids"] == "65745:642279,65745:642277,65745:642278"


async def test_jb_theater_seatmap_unavailable(client, api):
    api[f"/zirbana/events/{PLAY}"] = fixture("theater.json")
    api[f"/theaters/{PLAY}/book.data"] = fixture("theater_book.data")
    api[f"/zirbana/events/{PLAY}/session-summaries"] = (503, {"code": "ZIRBANA_UNAVAILABLE", "message": "x"})

    def unavailable(request):
        return httpx.Response(503, json={"code": "ZIRBANA_UNAVAILABLE", "message": "partner down"})

    api[f"/zirbana/events/{PLAY}/instances/{SHOWTIME}/seatmap"] = unavailable
    api[f"/zirbana/events/{PLAY}/instances/{SHOWTIME}/availability"] = unavailable
    data = (await client.call_tool("jb_theater", {"play_id": PLAY, "showtime_id": SHOWTIME})).structured_content
    assert "Seat map not available" in data["seats"]["error"]
    assert data["showtimes"][0]["live"] is False and data["showtimes"][0]["seats_left"] == 40  # page snapshot


async def test_jb_theater_unknown_showtime_and_off_sale(client, api):
    summaries = fixture("theater_summaries.json")
    summaries["items"].pop()  # 65745:642279 missing from the live check
    summaries["missingIds"] = ["65745:642279"]
    api[f"/zirbana/events/{PLAY}"] = fixture("theater.json")
    api[f"/theaters/{PLAY}/book.data"] = fixture("theater_book.data")
    api[f"/zirbana/events/{PLAY}/session-summaries"] = summaries
    data = (await client.call_tool("jb_theater", {"play_id": PLAY, "showtime_id": "99999:1"})).structured_content
    assert "Unknown showtime 99999:1" in data["seats"]["error"]
    assert not any("/instances/" in r.url.path for r in api.calls)
    last = data["showtimes"][2]
    assert last["showtime_id"] == "65745:642279" and last["live"] is False and "not on sale" in last["note"]


async def test_jb_theater_unknown(client, api):
    api["/zirbana/events/1"] = (404, fixture("theater_bad.json"))
    api["/theaters/1/book.data"] = "[]"
    result = await client.call_tool("jb_theater", {"play_id": "1"})
    assert result.is_error and "Unknown play id 1" in result.content[0].text


async def test_jb_event_filters(client, api):
    api["/events/cities"] = fixture("event_cities.json")
    api["/events/categories"] = fixture("event_categories.json")
    data = (await client.call_tool("jb_event_filters", {"city": "tehran"})).structured_content
    assert data["cities"][0] == {
        "slug": "tehran",
        "name": "تهران",
        "province": "تهران",
        "events": 104,
        "something_today": True,
    }
    assert len(data["categories"]) == 8 and all(c["events"] for c in data["categories"])
    assert {"slug": "event-and-workshop", "name": "ایونت و ورکشاپ", "events": 37} in data["categories"]
    categories = next(r for r in api.calls if r.url.path == "/events/categories")
    assert dict(categories.url.params) == {"city": "tehran"}


def test_turbo_decode():
    page = turbo_decode(fixture("theater_book.data"))["routes/theaters.$eventId_.book"]["data"]
    assert page["canonical"] == "https://jabama.events/theaters/65745"
    assert [i["remaining"] for i in page["instances"]] == [48, 40, 41]
