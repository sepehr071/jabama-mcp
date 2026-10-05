import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

FTS = "/api/taraaz/v2/search/fts"
PLP = "/api/taraaz/v4/search/merchandising/legacy-plp/"


async def test_jb_find_destination(client, api):
    api[FTS] = fixture("fts.json")
    data = (await client.call_tool("jb_find_destination", {"query": " رامسر "})).structured_content
    d = data["destinations"]
    assert d[0] == {
        "title": "رامسر",
        "subtitle": "استان مازندران",
        "kind": "city",
        "listings": 2822,
        "search": {"keyword": "city-ramsar"},
    }
    assert d[1]["kind"] == "complex" and d[1]["search"] == {"keyword": "list-99530"}
    assert d[2]["kind"] == "poi" and d[2]["search"] == {
        "keyword": "all",
        "poi": "C8oTQrqPSkLTDAAA2LPYp9it2YQg2LHYp9mF2LPYsQ",
    }
    assert d[3]["kind"] == "area" and d[3]["search"] == {"keyword": "all", "areas": ["north-tehran"]}
    assert d[4]["kind"] == "listing" and d[4]["code"] == 813270
    assert d[5]["kind"] == "text search" and d[5]["search"] == {"keyword": "all", "name_contains": "رامسر"}
    assert api.calls[0].url.params["query"] == "رامسر"


async def test_jb_find_destination_no_match(client, api):
    api[FTS] = {"mode": "flat"}  # the API drops `items` when nothing matches
    data = (await client.call_tool("jb_find_destination", {"query": "32835"})).structured_content
    assert data == {"destinations": []}


async def test_jb_find_destination_short_after_strip(client, api):
    result = await client.call_tool("jb_find_destination", {"query": " ر "})
    assert result.is_error and "at least 2 letters" in result.content[0].text and not api.calls


async def test_jb_parse_request_per_night_price(client, api):
    api["/api/taraaz/v1/search/descriptive/legacy"] = fixture("descriptive.json")
    text = "ویلای استخردار در رامسر از ۲۸ مهر تا ۱ آبان برای ۶ نفر زیر ۱۰ میلیون تومان هر شب"
    data = (await client.call_tool("jb_parse_request", {"text": text})).structured_content
    assert data["search"] == {
        "keyword": "all",
        "amenities": ["swimming-pool"],
        "check_in": "2026-10-20",
        "check_out": "2026-10-23",
        "guests": 6,
        "cities": ["ramsar"],
        "max_price": 10000000,  # 100,000,000 Rial
        "price_per_night": True,  # chip 'شبی تا ...'
        "types": ["villa"],
    }
    assert len(data["understood"]) == 6 and data["not_mapped"] == {}
    assert body(api.calls[0]) == {"requirement": text}


async def test_jb_search_stays(client, api):
    api[PLP + "city-ramsar"] = fixture("plp_search.json")
    args = {
        "keyword": "city-ramsar",
        "check_in": "2026-10-20",
        "check_out": "2026-10-23",
        "guests": 2,
        "types": ["villa", "ecotourism"],
        "amenities": ["swimming-pool"],
        "rooms": 2,
        "min_score": 4.5,
        "max_price": 3000000,
        "price_per_night": True,
        "sort": "cheapest",
        "limit": 3,
    }
    data = (await client.call_tool("jb_search_stays", args)).structured_content
    assert data["total"] == 2497
    assert data["dates"] == {"check_in": "2026-10-20", "check_out": "2026-10-23", "nights": 3}
    assert data["stays"][0] == {
        "code": 328353,
        "title": "بومگردی نسا - افرا",
        "type": "ecotourism",
        "city": "تنکابن",
        "province": "مازندران",
        "region": "rustic",
        "rating": 3.6,
        "rating_count": 7,
        "max_guests": 4,
        "base_guests": 2,
        "instant_booking": False,
        "total_price": 1640000,  # 16,400,000 Rial
        "price_before_discount": 1640000,
        "discount_pct": 0,
        "cheapest_night": 520000,
        "nights": 3,
        "url": "https://www.jabama.com/stay/ecotourism-328353",
    }
    assert data["stays"][2]["discount_pct"] == 10 and data["stays"][2]["total_price"] == 2223000
    assert data["amenity_options"]["swimming-pool"] == "استخر"
    sent = body(api.calls[0])
    assert sent == {
        "page-size": 3,
        "page-number": 1,
        "date": {"start": 20261020, "end": 20261023},
        "capacity": 2,
        "types": ["villa", "ecotourism"],
        "amenities": ["swimming-pool"],
        "rooms-count": "2",
        "scores": ["4.5"],
        "price": {"end": 90000000},  # 3,000,000 Toman per night x 3 nights x 10
        "sort": "price",
        "sort-direction": "false",
    }
    assert api.calls[0].url.params["platform"] == "desktop"


async def test_jb_search_stays_instant_only_and_geo_filters(client, api):
    page = fixture("plp_instant.json")
    page["result"]["items"][1]["reservation_type"] = "preApprove"  # tagged instant but needs the host
    api[PLP + "all"] = page
    args = {
        "cities": ["ramsar"],
        "areas": ["sadaf-kish"],
        "poi": "abc",
        "codes": [403515],
        "instant_only": True,
        "page": 2,
    }
    data = (await client.call_tool("jb_search_stays", args)).structured_content
    assert [s["code"] for s in data["stays"]] == [403515, 596453]
    assert data["dropped_not_instant"] == 1 and data["dates"] is None and "amenity_options" not in data
    sent = body(api.calls[0])
    assert sent["just-guarantees"] == "true" and sent["location-cities"] == ["ramsar"]
    assert sent["areas"] == ["sadaf-kish"] and sent["poi"] == "abc" and sent["codes"] == [403515]
    assert sent["page-number"] == 2 and "sort" not in sent and "date" not in sent


async def test_jb_search_stays_slash_keyword_and_unknown(client, api):
    api[PLP + "landing-categories"] = fixture("plp_search.json")
    await client.call_tool("jb_search_stays", {"keyword": "landing/categories", "tags": ["AmazingPoolCategory"]})
    assert api.calls[0].url.path.endswith("/landing-categories")
    api[PLP + "city-nowhere"] = (404, fixture("plp_404.json"))
    result = await client.call_tool("jb_search_stays", {"keyword": "city-nowhere"})
    assert result.is_error and "jb_find_destination" in result.content[0].text


async def test_jb_search_stays_retired_keyword(client, api):
    api[PLP + "all-villa"] = fixture("plp_retired.json")  # 200, no items, metadata.redirectCode 301
    result = await client.call_tool("jb_search_stays", {"keyword": "all-villa"})
    assert result.is_error and "'all-villa' is retired" in result.content[0].text


@pytest.mark.parametrize(
    "dates, message",
    [
        ({"check_in": "1405-07-28", "check_out": "1405-08-01"}, "Gregorian"),
        ({"check_in": "2026-10-23", "check_out": "2026-10-20"}, "after check_in"),
        ({"check_in": "2026-09-01", "check_out": "2026-09-03"}, "past"),
        ({"check_in": "2026-10-20"}, "both"),
    ],
)
async def test_jb_search_stays_rejects_bad_dates(client, api, dates, message):
    result = await client.call_tool("jb_search_stays", {"keyword": "city-ramsar", **dates})
    assert result.is_error and message in result.content[0].text and not api.calls


async def test_jb_categories(client, api):
    api["/api/taraaz/v1/search/merchandising/categorize/all-simple"] = fixture("categories.json")
    data = (await client.call_tool("jb_categories", {})).structured_content
    cats = data["categories"]
    assert cats[0] == {
        "group": "دسته بندی اقامتگاه",
        "title": "استخر خاص",
        "search": {"keyword": "landing-categories", "tags": ["AmazingPoolCategory"]},
    }
    assert {"keyword": "all", "types": ["villa"]} in [c["search"] for c in cats]
    assert {"keyword": "all-competitive-pricing"} in [c["search"] for c in cats]
    assert len(cats) == 6  # the two web links (events, tours) are left out
