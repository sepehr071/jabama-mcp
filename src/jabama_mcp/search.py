"""Stay search tools: place lookup, free-text parsing, listing search, theme categories."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import GW, WWW, ApiError, fetch
from .registry import tool

TEHRAN = timezone(timedelta(hours=3, minutes=30))  # Iran has no daylight saving time since 2022
PLP = f"{GW}/api/taraaz/v4/search/merchandising/legacy-plp"
PLP_QS = {"platform": "desktop", "allowEmptyCity": "true", "hasUnitRoom": "true", "guarantees": "false"}

StayType = Literal["villa", "suite", "apartment", "cottage", "ecotourism", "traditional", "inn", "complex", "hostel"]
Region = Literal["mountainous", "coastal", "desert", "jungle", "rustic", "city"]
Slugs = Annotated[list[Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]] | None, Field(max_length=20)]
CheckIn = Annotated[date | None, Field(description="Check-in date, Gregorian YYYY-MM-DD, e.g. '2026-10-20'.")]
CheckOut = Annotated[date | None, Field(description="Check-out date, Gregorian YYYY-MM-DD, e.g. '2026-10-23'.")]
Guests = Annotated[
    int | None,
    Field(ge=1, le=100, description="Number of guests; count children too (the site prices every guest), e.g. 4."),
]

# Body fields of plp-search (and keys in place URLs / AI patches) -> jb_search_stays argument names.
ARG_NAMES = {
    "types": "types",
    "amenities": "amenities",
    "regions": "regions",
    "tags": "tags",
    "location-cities": "cities",
    "location-provinces": "provinces",
    "zones": "zones",
    "areas": "areas",
    "poi": "poi",
    "fts": "name_contains",
}
SORTS = {"cheapest": ("price", "false"), "most_expensive": ("price", "true"), "rating": ("rate", "true")}


@tool("Find destination")
async def jb_find_destination(
    query: Annotated[
        str,
        Field(
            min_length=2,
            max_length=100,
            description="Place name in Persian: city, province, area, landmark or complex, e.g. 'رامسر' or 'کیش مال'. English names do not match.",
        ),
    ],
) -> dict[str, Any]:
    """Turn a place name into ready jb_search_stays arguments (keyword and filters).

    Returns up to 20 matches (cities, provinces, areas, points of interest such as a beach or mall,
    host complexes, single listings), each with `search` = the arguments to pass to jb_search_stays.
    `listings` is an approximate listing count, for ranking only. A `listing` match carries its code
    for jb_stay. Persian only; for English place names try the Persian spelling. A city keyword
    (city-ramsar) also covers nearby towns (Tonekabon); for the city alone use keyword 'all' with
    cities=['ramsar'].
    """
    query = query.strip()
    if len(query) < 2:
        raise ToolError("query needs at least 2 letters, e.g. 'رامسر'.")
    data = await fetch(f"{GW}/api/taraaz/v2/search/fts", {"query": query})
    return {"destinations": [_destination(i) for i in (data or {}).get("items") or []]}


@tool("Parse a stay request")
async def jb_parse_request(
    text: Annotated[
        str,
        Field(
            min_length=2,
            max_length=500,
            description="What the user wants, in Persian (English works partly), e.g. 'ویلای استخردار در رامسر برای ۶ نفر زیر ۱۰ میلیون تومان هر شب'.",
        ),
    ],
) -> dict[str, Any]:
    """Turn a free-text stay request into jb_search_stays arguments with Jabama's own AI parser.

    Understands type, amenities, region, city, guests, dates (Jalali in the text becomes Gregorian) and
    price. Pass `search` straight to jb_search_stays (add dates if the user gave none). `understood`
    lists what it picked up; anything else in the request must be filtered by you. Parsing only: it
    does not search.
    """
    data = await fetch(f"{GW}/api/taraaz/v1/search/descriptive/legacy", method="POST", json={"requirement": text})
    patch, chips = (data or {}).get("search_patch") or {}, (data or {}).get("titles") or []
    args: dict[str, Any] = {"keyword": "all"}
    unmapped = {}
    for key, value in patch.items():
        if key in ARG_NAMES:
            args[ARG_NAMES[key]] = value
        elif key == "capacity":
            args["guests"] = int(value)
        elif key == "date" and isinstance(value, dict):
            args["check_in"], args["check_out"] = _iso(value.get("start")), _iso(value.get("end"))
        elif key == "price" and isinstance(value, dict):
            if value.get("start"):
                args["min_price"] = toman(value["start"])
            if value.get("end"):
                args["max_price"] = toman(value["end"])
            # The same `price` key comes back for "per night" (chip 'شبی تا ...') and "whole stay" phrasing.
            args["price_per_night"] = any(
                c.get("key", "").startswith("price") and "شب" in (c.get("title") or "") for c in chips
            )
        elif key == "rooms-count":
            args["rooms"] = int(value)
        else:
            unmapped[key] = value
    return {"search": args, "understood": [c.get("title") for c in chips], "not_mapped": unmapped}


@tool("Search stays")
async def jb_search_stays(
    keyword: Annotated[
        str,
        Field(
            pattern=r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)?$",
            description="Keyword from jb_find_destination or jb_categories, e.g. 'city-ramsar' (with nearby towns), 'province-gilan', 'list-33183', 'landing-categories', or 'all' with filters (cities=['ramsar'] is the city alone).",
        ),
    ] = "all",
    check_in: CheckIn = None,
    check_out: CheckOut = None,
    guests: Guests = None,
    types: Annotated[list[StayType] | None, Field(max_length=9, description="Listing types, e.g. ['villa'].")] = None,
    amenities: Annotated[
        Slugs,
        Field(
            description="Amenity slugs (all required), e.g. ['swimming-pool', 'jacuzzi']. Others: sauna, billiard, barbeque, internet-wifi, parking, kitchen, air-conditioning-system, elevator (see amenity_options)."
        ),
    ] = None,
    regions: Annotated[list[Region] | None, Field(max_length=6, description="Setting, e.g. ['jungle'].")] = None,
    cities: Annotated[Slugs, Field(description="English city slugs, with keyword 'all', e.g. ['ramsar'].")] = None,
    provinces: Annotated[
        Slugs, Field(description="English province slugs, with keyword 'all', e.g. ['gilan'].")
    ] = None,
    zones: Annotated[Slugs, Field(description="Zone slugs, with keyword 'all', e.g. ['north'].")] = None,
    areas: Annotated[Slugs, Field(description="Area slugs from jb_find_destination, e.g. ['sadaf-kish'].")] = None,
    poi: Annotated[
        str | None, Field(max_length=200, description="Point-of-interest token from jb_find_destination, unchanged.")
    ] = None,
    tags: Annotated[
        Slugs,
        Field(
            description="Tags from jb_categories, e.g. ['AmazingPoolCategory'], ['pricing-affordable'], ['LongStaysDiscount']."
        ),
    ] = None,
    codes: Annotated[
        list[Annotated[int, Field(ge=1)]] | None,
        Field(max_length=50, description="Only these listing codes (keyword 'all'), e.g. [403515, 328353]."),
    ] = None,
    name_contains: Annotated[
        str | None, Field(max_length=100, description="Words in the listing title (full-text), not a place filter.")
    ] = None,
    rooms: Annotated[int | None, Field(ge=0, le=5, description="Bedrooms, 0 = studio, 5 = five or more.")] = None,
    instant_only: Annotated[bool, Field(description="Only instantly bookable listings.")] = False,
    min_score: Annotated[
        Literal[3, 3.5, 4, 4.5, 5] | None, Field(description="Minimum guest rating: 3, 3.5, 4, 4.5 or 5.")
    ] = None,
    min_price: Annotated[int | None, Field(ge=0, description="Lowest price in Toman for the whole stay.")] = None,
    max_price: Annotated[
        int | None, Field(ge=0, description="Highest price in Toman for the whole stay, e.g. 30000000.")
    ] = None,
    price_per_night: Annotated[
        bool,
        Field(description="Treat min_price / max_price as per night (whole-stay price divided by nights)."),
    ] = False,
    sort: Annotated[
        Literal["default", "cheapest", "most_expensive", "rating"],
        Field(
            description="default = Jabama's ranking; cheapest / most_expensive by whole-stay price; rating = weighted by review count."
        ),
    ] = "default",
    page: Annotated[int, Field(ge=1, le=200, description="1-based page number.")] = 1,
    limit: Annotated[int, Field(ge=1, le=50, description="Stays per page.")] = 20,
) -> dict[str, Any]:
    """Search Jabama stays (villas, suites, apartments, cottages, eco-lodges) with prices for your dates and guests.

    With dates, total_price is the whole stay in Toman for `guests` (extra-guest charges included) and
    cheapest_night is the cheapest single night; without dates, prices are for one default night.
    image is the cover photo; with dates url opens the listing for those dates and guests.
    Get the keyword from jb_find_destination, themes from jb_categories, or free text via jb_parse_request.
    total 0 with dates can mean nothing is free; try other dates. For the exact payable amount call
    jb_price_quote with a code; for details jb_stay.
    """
    nights = check_dates(check_in, check_out)
    body: dict[str, Any] = {"page-size": limit, "page-number": page}
    if check_in and check_out:
        body["date"] = {"start": ymd(check_in), "end": ymd(check_out)}
    if guests:
        body["capacity"] = guests
    filters = {
        "types": types,
        "amenities": amenities,
        "regions": regions,
        "tags": tags,
        "location-cities": cities,
        "location-provinces": provinces,
        "zones": zones,
        "areas": areas,
        "poi": poi,  # a plain string, like fts
        "codes": codes,
        "fts": name_contains,
    }
    body.update({k: v for k, v in filters.items() if v})
    if rooms is not None:
        body["rooms-count"] = str(rooms)  # a scalar string; an array is a 422
    if instant_only:
        body["just-guarantees"] = "true"
    if min_score:
        body["scores"] = [f"{min_score:g}"]
    if min_price is not None or max_price is not None:
        factor = 10 * (nights if price_per_night and nights else 1)  # Toman -> Rial, whole stay
        body["price"] = {k: v * factor for k, v in (("start", min_price), ("end", max_price)) if v is not None}
    if sort in SORTS:
        body["sort"], body["sort-direction"] = SORTS[sort]
    kw = keyword.replace("/", "-")
    try:
        data = await fetch(f"{PLP}/{kw}", PLP_QS, method="POST", json=body)
    except ApiError as e:
        if e.status == 404:
            raise ApiError(f"Unknown search keyword '{kw}'. Get one from jb_find_destination or jb_categories.") from e
        raise
    data = data or {}
    if (data.get("metadata") or {}).get("redirectCode"):  # retired keywords answer 200 with no items
        raise ApiError(
            f"Search keyword '{kw}' is retired. Get a current one from jb_find_destination or jb_categories."
        )
    items = data.get("items") or []
    stays = [stay_card(i, guests) for i in items]
    out: dict[str, Any] = {"total": data.get("total"), "page": page}
    if instant_only:
        # just-guarantees follows the 'instant' tag; a few host-approval listings carry it too.
        out["dropped_not_instant"] = sum(not s["instant_booking"] for s in stays)
        stays = [s for s in stays if s["instant_booking"]]
    out["dates"] = {"check_in": str(check_in), "check_out": str(check_out), "nights": nights} if nights else None
    out["stays"] = stays
    if page == 1:
        out["amenity_options"] = {
            f.get("key"): f.get("persian-name")
            for flt in data.get("filters") or []
            if flt.get("field") == "amenities"
            for f in flt.get("filters") or []
        }
    return out


@tool("Stay categories")
async def jb_categories() -> dict[str, Any]:
    """List Jabama's themed stay categories (special pool, A-frame, luxury, cheap, jungle, sea view, pet friendly, last-minute deals, instant booking, ...).

    Each has `search` = the jb_search_stays arguments that open it; add dates, guests and a city
    filter (cities=[...]) as needed.
    """
    data = await fetch(f"{GW}/api/taraaz/v1/search/merchandising/categorize/all-simple")
    out = []
    for group in (data or {}).get("items") or []:
        for t in group.get("items") or []:
            if t.get("resultType") != "plp":
                continue  # links to jabama.events / jabama.tours / JaFund
            args: dict[str, Any] = {"keyword": (t.get("keyword") or "all").replace("/", "-")}
            for f in t.get("filters") or []:
                if f.get("key") in ARG_NAMES:
                    args[ARG_NAMES[f["key"]]] = f.get("value")
            out.append({"group": group.get("title"), "title": t.get("title"), "search": args})
    return {"categories": out}


def stay_card(it: dict[str, Any], guests: int | None = None) -> dict[str, Any]:
    """Compact listing card from plp-search items; a dated card links to the listing page with its dates."""
    p = it.get("price") or {}
    rr = it.get("rate_review") or {}
    loc = it.get("location") or {}
    main, final = p.get("mainPrice") or 0, p.get("discountedPrice") or 0
    return {
        "code": it.get("code"),
        "title": it.get("name"),
        "type": it.get("type"),
        "city": loc.get("city"),
        "province": loc.get("province"),
        "region": it.get("region"),
        "rating": rating(rr.get("score"), rr.get("count")),
        "rating_count": rr.get("count"),
        "max_guests": (it.get("capacity") or {}).get("base"),
        "base_guests": (it.get("rawCapacity") or {}).get("base"),
        "instant_booking": it.get("reservation_type") == "instant",
        "total_price": toman(final),
        "price_before_discount": toman(main),
        "discount_pct": round(100 * (main - final) / main) if main and final < main else 0,
        "cheapest_night": toman(p.get("perNight")),
        "nights": 1 if p.get("isDefaultDate") else p.get("nights_count"),
        "image": it.get("image") or (it.get("images") or [None])[0],
        "url": stay_url(
            it.get("type"),
            it.get("code"),
            None if p.get("isDefaultDate") else p.get("fa_checkin"),
            None if p.get("isDefaultDate") else p.get("fa_checkout"),
            guests,
        ),
    }


def check_dates(check_in: date | None, check_out: date | None) -> int | None:
    """Validate a stay's dates; return the number of nights (None without dates)."""
    if not check_in and not check_out:
        return None
    if not check_in or not check_out:
        raise ToolError("Give both check_in and check_out, or neither.")
    gregorian(check_in)
    if check_out <= check_in:
        raise ToolError("check_out must be after check_in.")
    if check_in < today():
        raise ToolError(f"check_in {check_in} is in the past (today in Iran is {today()}).")
    return (check_out - check_in).days


def gregorian(d: date | None) -> None:
    """Jalali dates (1405-07-28) parse as valid far-past dates and silently give wrong results: reject them."""
    if d and d.year < 1900:
        raise ToolError("Dates must be Gregorian (YYYY-MM-DD), not Jalali: e.g. 1405-07-28 is 2026-10-20.")


def today() -> date:
    return datetime.now(TEHRAN).date()


def ymd(d: date) -> int:
    """Search bodies take Gregorian dates as YYYYMMDD integers (Jalali ones silently match nothing)."""
    return d.year * 10000 + d.month * 100 + d.day


def toman(rial: Any) -> int | None:
    """gw.jabama.com prices are Rial; the site shows Toman (Rial / 10)."""
    return round(rial / 10) if isinstance(rial, (int, float)) else None


def rating(score: Any, count: Any) -> float | None:
    """Scores are 0-5; 0 or no reviews means not rated yet."""
    return round(float(score), 1) if score and count else None


def stay_url(
    kind: str | None,
    code: Any,
    fa_check_in: str | None = None,
    fa_check_out: str | None = None,
    guests: int | None = None,
) -> str | None:
    """Listing page; with Jalali dates (plp `fa_checkin`, '1405-07-28') and guests it opens priced like the site's cards."""
    if not code:
        return None
    url = f"{WWW}/stay/{kind or 'stay'}-{code}"
    if fa_check_in and fa_check_out:
        url += f"?checkIn={fa_check_in}&checkOut={fa_check_out}" + (f"&passengersCount={guests}" if guests else "")
    return url


def _iso(n: Any) -> str | None:
    s = str(n or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else None


def _destination(item: dict[str, Any]) -> dict[str, Any]:
    url = item.get("url") or ""
    path, _, query = url.partition("?")
    out: dict[str, Any] = {"title": item.get("title"), "subtitle": item.get("subtitle")}
    if item.get("type") == "pdp":
        m = re.search(r"-(\d+)$", path)
        return {**out, "kind": "listing", "code": int(m[1]) if m else None}
    args: dict[str, Any] = {"keyword": path}
    for key, value in parse_qsl(query):
        if key in ARG_NAMES:
            args[ARG_NAMES[key]] = value if key in ("poi", "fts") else value.split(",")
    prefix = path.split("-")[0]
    kinds = {"city": "city", "province": "province", "list": "complex", "zone": "zone", "stays": "collection"}
    kind = kinds.get(prefix) or next(
        (k for k in ("poi", "areas", "zones", "fts") if f"{k}=" in query), "filtered search"
    )
    kind = {"areas": "area", "zones": "zone", "fts": "text search"}.get(kind, kind)
    return {**out, "kind": kind, "listings": item.get("liquidity"), "search": args}
