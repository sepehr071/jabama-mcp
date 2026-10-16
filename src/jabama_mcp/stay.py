"""One listing: details, nightly calendar, exact price quote, alternatives, reviews, host."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import GW, ApiError, fetch
from .registry import tool
from .search import (
    PLP,
    PLP_QS,
    CheckIn,
    CheckOut,
    check_dates,
    gregorian,
    rating,
    stay_card,
    stay_url,
    toman,
    ymd,
)

Code = Annotated[int, Field(ge=1, le=999_999_999, description="Listing code from a search or URL, e.g. 328353.")]
FullText = Annotated[bool, Field(description="Return long text (reviews, descriptions) unclipped, not cut at …")]
Page = Annotated[int, Field(ge=1, le=100, description="1-based page number.")]
REVIEW_SORT = {"newest": "Newest", "highest_rated": "HighestRate", "lowest_rated": "LowestRate"}
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")


@tool("Stay details")
async def jb_stay(code: Code, full_text: FullText = False) -> dict[str, Any]:
    """Get one listing: capacity, beds, rooms, check-in/out times, house rules, amenities (and what is missing), cancellation policy, host, review scores.

    Use after jb_search_stays to answer "is it right for us". from_per_night is the cheapest night in
    the next ~76 days (Toman, base guests). For nightly prices and free dates call jb_stay_calendar;
    for the exact price of a stay, jb_price_quote; for reviews, jb_reviews; for the host, jb_host.
    """
    data = await stay_detail(code)  # first, so an unknown code gets its own message, not the amenities 404
    item, meta = data["item"], data.get("meta") or {}
    amen, policies = await asyncio.gather(
        fetch(f"{GW}/api/v1/accommodation/public/guest/accommodations/amenities/{code}", {"important": "false"}),
        fetch(f"{GW}/api/v1/accommodation/public/guest/accommodations/cancellation-policy/{item['id']}"),
    )
    cap = item.get("capacity") or {}
    guests = cap.get("guests") or {}
    metrics = item.get("accommodationMetrics") or {}
    price = item.get("price") or {}
    city = ((item.get("placeOfResidence") or {}).get("area") or {}).get("city") or {}
    loc = (item.get("placeOfResidence") or {}).get("location") or {}
    rr = item.get("rateAndReview") or {}
    discounts = price.get("longStaysDiscount") or {}
    groups: dict[str, list[str]] = {}
    missing: list[str] = []
    for g in (amen or {}).get("amenities") or []:
        for a in g.get("items") or []:
            name = (a.get("title") or {}).get("fa")
            if not a.get("state"):
                missing.append(name)
            else:
                extra = "، ".join(a.get("items") or [])
                groups.setdefault((g.get("title") or {}).get("fa"), []).append(f"{name} ({extra})" if extra else name)
    host = meta.get("hostInfo") or {}
    return {
        "code": item.get("code"),
        "title": item.get("title"),
        "url": meta.get("link") or stay_url(item.get("type"), item.get("code")),
        "type": (item.get("typeDetails") or {}).get("title_fa") or item.get("type"),
        "active": item.get("status") == "confirmed",
        "instant_booking": item.get("reservationType") == "instant",
        "city": (city.get("name") or {}).get("fa"),
        "province": ((city.get("province") or {}).get("name") or {}).get("fa"),
        "approx_location": {"lat": loc.get("lat"), "lng": loc.get("lng"), "radius_m": loc.get("radius")},
        "base_guests": guests.get("base"),
        "max_guests": (guests.get("base") or 0) + (guests.get("extra") or 0),
        "beds": {k: v for k, v in (cap.get("beds") or {}).items() if v},
        "bedrooms": metrics.get("bedroomsCount"),
        "bathrooms": metrics.get("bathroomsCount"),
        "area_m2": metrics.get("areaSize"),
        "building_m2": metrics.get("buildingSize"),
        "units_of_this_type": item.get("unitCount") if item.get("placeType") == "unitRoom" else None,
        "check_in_time": item.get("checkIn"),
        "check_out_time": item.get("checkOut"),
        "min_nights": item.get("minNight"),
        "rating": rating(rr.get("score"), rr.get("count")),
        "rating_count": rr.get("count"),
        "review_scores": {
            (r.get("ratingItem") or {}).get("title"): r.get("rating")
            for r in (meta.get("reviews") or {}).get("items") or []
        },
        "from_per_night": toman(item.get("minDiscountedPrice") or item.get("mainPrice")),
        "extra_guest_per_night": toman((price.get("extraPeople") or {}).get("base")),
        "long_stay_discount_pct": {
            label: discounts[key]
            for key, label in (
                ("threeDay", "3+ nights"),
                ("short", "7+ nights"),
                ("fourteenDay", "14+ nights"),
                ("long", "30 nights"),
            )
            if discounts.get(key)
        },
        "meals_offered": [m for m, on in (item.get("meal") or {}).items() if on],
        "description": clip(
            "\n\n".join(d.get("text") or "" for d in item.get("extraDescription") or []), 2000, full_text
        ),
        "house_rules": [
            clean("".join(t.get("text") or "" for t in r.get("texts") or [])) for r in item.get("rules") or []
        ],
        "amenities": groups,
        "missing_amenities": missing,
        "cancellation": [
            {
                "policy": clean(p.get("title")),
                "phases": [
                    {"when": clean((p.get(k) or {}).get("title")), "deduction": clean((p.get(k) or {}).get("text"))}
                    for k in ("beforeCheckIn", "untilCheckIn", "afterCheckIn")
                    if p.get(k)
                ],
            }
            for p in (policies or {}).get("cancellationPolicy") or []
        ],
        "host": {
            "host_id": host.get("uun"),
            "name": " ".join(x for x in (host.get("fistName"), host.get("lastName")) if x),  # API spells it fistName
            "member_since": (host.get("registerDate") or "")[:10] or None,
            **host_stats(item.get("hostProfile")),
        },
        "images": [i.get("url") for i in (item.get("placeImages") or [])[:5]],
    }


@tool("Stay calendar")
async def jb_stay_calendar(
    code: Code,
    start: Annotated[
        CheckIn, Field(description="First night to show, Gregorian YYYY-MM-DD; default today, e.g. '2026-10-20'.")
    ] = None,
    nights: Annotated[int, Field(ge=1, le=80, description="How many nights to show.")] = 30,
) -> dict[str, Any]:
    """Nightly availability and price of one listing for the next ~76 days, with weekend and holiday flags.

    price is Toman for one night for base_guests; each guest above that adds extra_guest. Weekend
    (Wednesday and Thursday nights) and holiday nights can cost more or less. Use it to find free
    dates or the cheapest nights; then confirm a stay with jb_price_quote (it adds long-stay discounts).
    Dates past the calendar cannot be booked yet. Blocked nights show only date and available=false.
    """
    gregorian(start)
    data, day_types = await asyncio.gather(stay_detail(code), fetch(f"{GW}/api/taraaz/v1/pricing/calendar/day-type"))
    item = data["item"]
    cal = item.get("calendar") or []
    end = cal[-1].get("date") if cal else None
    first = str(start) if start else None
    if first and end and first > end:
        raise ToolError(
            f"start {first} is past the calendar of {code}, which ends {end}. Later dates cannot be booked yet."
        )
    kinds = {d.get("date"): d for d in (day_types or {}).get("calendar") or []}
    rows = [c for c in cal if not first or (c.get("date") or "") >= first][:nights]
    out = []
    for c in rows:
        day = kinds.get(c.get("date")) or {}
        free = c.get("status") == "available"
        if not free:
            out.append({"date": c.get("date"), "available": False})
            continue
        out.append(
            {
                "date": c.get("date"),
                "jalali": day.get("jalali_date") or c.get("jalaliDateString"),
                "available": True,
                "day_type": day.get("day_type") or c.get("type"),
                "price": toman(c.get("price")),
                "price_before_discount": toman(c.get("basePrice")),
                "extra_guest": toman(c.get("extraPeople")),
                "host_set_price": c.get("type") == "custom",
                "min_nights": c.get("minNight"),
                "instant": c.get("instant"),
            }
        )
    guests = (item.get("capacity") or {}).get("guests") or {}
    return {
        "code": item.get("code"),
        "title": item.get("title"),
        "base_guests": guests.get("base"),
        "max_guests": (guests.get("base") or 0) + (guests.get("extra") or 0),
        "calendar_ends": end,
        "nights": out,
    }


@tool("Price quote")
async def jb_price_quote(
    code: Code,
    check_in: Annotated[date, Field(description="Check-in date, Gregorian YYYY-MM-DD, e.g. '2026-10-20'.")],
    check_out: Annotated[date, Field(description="Check-out date, Gregorian YYYY-MM-DD, e.g. '2026-10-23'.")],
    guests: Annotated[
        int,
        Field(
            ge=1, le=100, description="Everyone who stays, children included (the quote has no child price), e.g. 4."
        ),
    ] = 2,
) -> dict[str, Any]:
    """Get the exact amount to pay for one listing, dates and guest count (Toman), with per-night lines and the cancellation windows.

    The same quote the listing page shows; it creates no booking. payable is after long-stay and host
    discounts, extra-guest charges included. For a multi-unit complex, unit_options shows how the group
    can split across units. An error means the dates cannot be booked (blocked nights, past or beyond
    the calendar, too many guests): check jb_stay_calendar. per_night day_type 'custom' means a
    host-set price (jb_stay_calendar shows weekday/weekend). Each cancellation phase runs until its
    Gregorian `until` date. Booking itself happens on the site.
    """
    nights = check_dates(check_in, check_out)
    item = (await stay_detail(code))["item"]
    cal = item.get("calendar") or []
    end = cal[-1].get("date") if cal else None
    if end and str(check_out) > end:  # the calendar's last date works only as a check-out
        raise ToolError(
            f"{check_in} to {check_out} is beyond the bookable calendar of {code}: check_out can be {end} at the latest. Later dates cannot be booked yet."
        )
    pid = item["id"]
    body = {
        "accommodationId": pid,
        "checkIn": str(check_in),
        "checkOut": str(check_out),
        "passengers": {"adults": guests, "children": 0},  # children are ignored by the quote
    }
    calls = [fetch(f"{GW}/api/v1/accommodations/orders/preview", method="POST", json=body)]
    complex_unit = item.get("placeType") == "unitRoom"
    if complex_unit:
        variant = {
            "accommodationId": pid,
            "checkIn": str(check_in),
            "checkOut": str(check_out),
            "passengersCount": guests,
        }
        calls.append(fetch(f"{GW}/api/v1/accommodation/public/guest/orders/variant", method="POST", json=variant))
    try:
        results = await asyncio.gather(*calls)
    except ApiError as e:
        if e.status == 400:
            raise ApiError(
                f"These dates or guests cannot be booked for {code}: {str(e).rstrip('.')}. Check free nights and capacity with jb_stay_calendar.",
                400,
            ) from e
        raise
    q = results[0] or {}
    line = (q.get("lineItems") or [{}])[0]
    paid, total = q.get("paidAmount") or 0, q.get("totalPrice") or 0
    policy = line.get("cancellationPolicyDetails") or {}  # Jalali window titles; the days are in cancellationPolicy
    before_days = ((line.get("cancellationPolicy") or {}).get("beforeCheckIn") or {}).get("days")
    until = {
        "beforeCheckIn": check_in - timedelta(days=before_days) if before_days is not None else None,
        "untilCheckIn": check_in,
        "afterCheckIn": check_out,
    }
    out: dict[str, Any] = {
        "code": item.get("code"),
        "title": item.get("title"),
        "check_in": f"{check_in} {(line.get('checkIn') or '')[11:16] or item.get('checkIn') or ''}".strip(),
        "check_out": f"{check_out} {(line.get('checkOut') or '')[11:16] or item.get('checkOut') or ''}".strip(),
        "nights": nights,
        "guests": guests,
        "payable": toman(paid),
        "price_before_discount": toman(total),
        "discount": toman(q.get("providerDiscountAmount") or total - paid),
        "discount_pct": q.get("discountPercentage") or 0,
        "installment_4x": toman(math.ceil(paid / 4 / 10000) * 10000),  # the site's 4-part payment, per part
        "instant_booking": bool(line.get("isInstant")),
        "per_night": [
            {
                "date": d.get("date"),
                "day_type": d.get("type"),
                "night": toman(d.get("price")),
                "extra_guest_each": toman(d.get("extraPeople")),
                "total": toman(d.get("totalDayPrice")),
            }
            for d in (line.get("pricePerDay") or {}).get("days") or []
        ],
        "cancellation": {
            "policy": clean(policy.get("title")),
            "phases": [
                {
                    "when": clean((policy.get(k) or {}).get("title")),
                    "until": str(until[k]) if until[k] else None,
                    "deduction": clean((policy.get(k) or {}).get("text")),
                }
                for k in ("beforeCheckIn", "untilCheckIn", "afterCheckIn")
                if policy.get(k)
            ],
        },
    }
    if complex_unit:
        out["unit_options"] = [
            {
                "units": v.get("unitCount"),
                "split": v.get("title"),
                "price": fa_int(v.get("price")),  # already Toman text with Persian digits
                "label": v.get("badge"),
            }
            for v in (results[1] or {}).get("items") or []
        ]
    return out


@tool("Similar stays")
async def jb_similar_stays(
    code: Code,
    check_in: CheckIn = None,
    check_out: CheckOut = None,
    guests: Annotated[
        int | None, Field(ge=1, le=100, description="Number of guests, children included, e.g. 4.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=25, description="Max similar stays.")] = 10,
) -> dict[str, Any]:
    """Find alternatives to a listing: similar stays nearby, and other units of the same property.

    Use when a listing is full, too small or too expensive. With dates, prices are the whole stay in
    Toman for those dates and guests (stays not free then are listed in not_free_for_dates); without
    dates, one default night (stays too small for `guests` are left out). same_property.keyword lists
    every unit of the complex in jb_search_stays. Inactive listings have no alternatives: search the area.
    """
    nights = check_dates(check_in, check_out)
    item = (await stay_detail(code))["item"]
    if item.get("status") != "confirmed":  # similar answers a bare 400 for disabled listings
        raise ApiError(f"Listing {code} is inactive; search its area with jb_search_stays instead.")
    pid = item["id"]
    calls = [fetch(f"{GW}/api/v2/accommodations/{pid}/similar", method="POST", json={})]
    if item.get("hasOtherRoom"):
        calls.append(fetch(f"{GW}/api/v1/accommodation/public/guest/accommodations/{pid}/other-rooms"))
    results = await asyncio.gather(*calls)
    similar = results[0] or []
    if not nights:
        similar = [s for s in similar if not guests or _similar_card(s)["max_guests"] >= guests]
    similar = similar[:limit]
    out: dict[str, Any] = {"code": code}
    if nights and similar:  # an empty `codes` filter would search everything
        codes = [s.get("code") for s in similar]
        body: dict[str, Any] = {
            "page-size": len(codes),
            "page-number": 1,
            "date": {"start": ymd(check_in), "end": ymd(check_out)},  # type: ignore[arg-type]
            "codes": codes,
        }
        if guests:
            body["capacity"] = guests
        found = (await fetch(f"{PLP}/all", PLP_QS, method="POST", json=body) or {}).get("items") or []
        cards = sorted((stay_card(i, guests) for i in found), key=lambda c: c["total_price"] or 0)
        out["similar"] = cards
        out["not_free_for_dates"] = [c for c in codes if c not in {x["code"] for x in cards}]
    else:
        out["similar"] = [_similar_card(s) for s in similar]
    if len(results) > 1 and results[1]:
        o = results[1]
        out["same_property"] = {
            "keyword": (o.get("plpLink") or "").lstrip("/") or None,
            "units": [
                {
                    "code": r.get("code"),
                    "title": r.get("title"),
                    "max_guests": r.get("totalCapacity"),
                    "from_per_night": toman((r.get("price") or {}).get("minDiscounted")),
                    "rating": rating(r.get("rate"), r.get("rateCount")),
                    "rating_count": r.get("rateCount"),
                }
                for r in o.get("rooms") or []
            ],
        }
    return out


@tool("Stay reviews")
async def jb_reviews(
    code: Code,
    sort: Annotated[Literal["newest", "highest_rated", "lowest_rated"], Field(description="Order.")] = "newest",
    page: Page = 1,
    limit: Annotated[int, Field(ge=1, le=50, description="Reviews per page.")] = 10,
    full_text: FullText = False,
) -> dict[str, Any]:
    """Read guest reviews of a listing: stars 1-5, text, when they stayed, nights, and the host's reply.

    sort=lowest_rated surfaces complaints. The overall score and per-category scores are in jb_stay.
    Reviews carry no exact date, only the Jalali month or "N days ago".
    """
    data = await fetch(f"{GW}/api/v2/reviews/place/{code}", {"page": page, "size": limit, "sortBy": REVIEW_SORT[sort]})
    reviews = (data or {}).get("reviews") or []
    if page == 1 and not reviews:
        await stay_detail(code)  # an unknown code also answers an empty list; this raises "Unknown listing code"
    return {
        "page": page,
        "has_more": len(reviews) == limit,
        "reviews": [
            {
                "rating": r.get("overalRating"),
                "text": clip(r.get("comment"), 400, full_text),
                "author": r.get("name"),
                "stayed": (r.get("subTitles") or [None])[0],
                "stay_length": ((r.get("reviewInfo") or [{}])[0]).get("text"),
                "host_reply": ((r.get("response") or {}).get("comment") or None),
            }
            for r in reviews
        ],
    }


@tool("Host profile")
async def jb_host(
    host_id: Annotated[int, Field(ge=1, description="Host id (host.host_id from jb_stay), e.g. 1288856.")],
    page: Page = 1,
    limit: Annotated[int, Field(ge=1, le=50, description="Reviews per page.")] = 10,
    full_text: FullText = False,
) -> dict[str, Any]:
    """A host's listings (with ratings and a from-price per night) and guest reviews across all of them, newest first.

    Use to judge a host before booking. The host's average score and successful-booking count are in
    jb_stay's host block. listings_count can exceed the listings returned (inactive ones).
    """
    reviews, listings = await asyncio.gather(
        fetch(f"{GW}/api/v2/reviews/host/{host_id}", {"page": page, "size": limit}),
        fetch(f"{GW}/api/v1/guest/host/{host_id}/accommodation", {"offset": 0, "limit": 50}),
    )
    reviews, listings = reviews or {}, listings or {}
    if not reviews.get("totalCount") and not listings.get("items"):
        raise ApiError(f"Unknown host {host_id}, or the host has no listings or reviews. Get host_id from jb_stay.")
    items = reviews.get("items") or []
    return {
        "host_id": host_id,
        "url": f"https://www.jabama.com/host/{host_id}",
        "review_count": reviews.get("totalCount"),
        "listings_count": listings.get("count"),
        "listings": [
            {
                "code": x.get("code"),
                "title": x.get("name"),
                "type": x.get("type"),
                "place": (x.get("info") or [None, None])[-1],
                "rating": rating((x.get("rateReview") or {}).get("score"), (x.get("rateReview") or {}).get("count")),
                "rating_count": (x.get("rateReview") or {}).get("count"),
                "base_guests": x.get("baseGuests"),
                "max_guests": (x.get("baseGuests") or 0) + (x.get("extraGuests") or 0),
                "from_per_night": toman(
                    (x.get("price") or {}).get("discountedPrice") or (x.get("price") or {}).get("perNight")
                ),
            }
            for x in listings.get("items") or []
        ],
        "page": page,
        "has_more": page * limit < (reviews.get("totalCount") or 0),
        "reviews": [
            {
                "rating": r.get("overalRating"),
                "listing": r.get("placeName"),
                "text": clip(r.get("comment"), 400, full_text),
                "author": r.get("name"),
                "stayed": (r.get("subTitles") or [None])[0],
                "host_reply": (r.get("response") or {}).get("comment") or None,
            }
            for r in items
        ],
    }


async def stay_detail(code: int) -> dict[str, Any]:
    """Listing detail by code: {item, meta}. item.id is the 24-hex placeId other endpoints need."""
    try:
        data = await fetch(f"{GW}/api/v1/accommodations/{code}", {"reversePeriods": "true", "withPanoramic": "true"})
    except ApiError as e:
        if e.status in (400, 404):  # an unknown code answers 400 "mongo: no documents in result"
            raise ApiError(f"Unknown listing code {code}. Get codes from jb_search_stays.", e.status) from e
        raise
    if not (data or {}).get("item"):
        raise ApiError(f"Unknown listing code {code}. Get codes from jb_search_stays.")
    return data


def host_stats(profile: Any) -> dict[str, Any]:
    """hostProfile lines: 'میانگین ۳.۹' / 'از ۵ امتیاز' and '۱۸ نظر' / 'از ۲۲ رزرو موفق'."""
    out: dict[str, Any] = {"rating": None, "review_count": None, "successful_bookings": None}
    for p in (profile or {}).get("items") or []:
        text, sub = p.get("text") or "", p.get("subText") or ""
        if "میانگین" in text:
            out["rating"] = fa_float(text)
        if "نظر" in text:
            out["review_count"] = fa_int(text)
        if "رزرو" in sub:
            out["successful_bookings"] = fa_int(sub)
    return out


def fa_float(s: Any) -> float | None:
    """First number in a text with Persian or Arabic digits ('میانگین ۳.۹' -> 3.9)."""
    m = re.search(r"\d+(?:\.\d+)?", str(s or "").translate(DIGITS))
    return float(m[0]) if m else None


def fa_int(s: Any) -> int | None:
    """All digits of a text as one number ('۸۳,۳۸۵,۰۰۰ تومان' -> 83385000)."""
    digits = re.sub(r"\D", "", str(s or "").translate(DIGITS))
    return int(digits) if digits else None


def clean(s: Any) -> str | None:
    """Drop the bidi isolation marks (U+2066..U+2069) the API puts around numbers, and outer spaces."""
    return (
        re.sub("[\N{LEFT-TO-RIGHT ISOLATE}-\N{POP DIRECTIONAL ISOLATE}]", "", s).strip() if isinstance(s, str) else None
    )


def clip(s: Any, n: int, full: bool = False) -> str | None:
    s = (s or "").strip()
    return (s[: n - 1] + "…" if len(s) > n and not full else s) or None


def _similar_card(s: dict[str, Any]) -> dict[str, Any]:
    rr = s.get("rate_review") or {}
    cap = s.get("rawCapacity") or {}
    return {
        "code": s.get("code"),
        "title": s.get("name"),
        "type": s.get("type"),
        "city": (s.get("location") or {}).get("city"),
        "rating": rating(rr.get("score"), rr.get("count")),
        "rating_count": rr.get("count"),
        "max_guests": (cap.get("base") or 0) + (cap.get("extra") or 0),
        "instant_booking": s.get("reservationType") == "instant",
        "price_one_night": toman(
            (s.get("price") or {}).get("discountedPrice") or (s.get("price") or {}).get("perNight")
        ),
        "url": stay_url(s.get("type"), s.get("code")),
    }
