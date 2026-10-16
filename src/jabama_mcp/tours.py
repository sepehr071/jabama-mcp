"""Jabama Tours (jabama.tours) tools: group tour search, one tour with departures and seats, place ids."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import TOURS, ApiError, fetch
from .registry import tool
from .search import gregorian, rating
from .stay import FullText, clip

TourId = Annotated[
    str, Field(pattern=r"^\d{8}$", description="Tour id (8 digits) from jb_search_tours, e.g. '26314786'.")
]
SORTS = {
    "recommended": ("recommended", "desc"),
    "cheapest": ("price", "asc"),
    "most_expensive": ("price", "desc"),
    "soonest": ("start_date", "asc"),
    "best_selling": ("bookings_count", "desc"),
    "newest": ("created_at", "desc"),
    "top_rated": ("rating_avg", "desc"),
}


@tool("Search tours")
async def jb_search_tours(
    query: Annotated[
        str | None,
        Field(
            max_length=100,
            description="Loose text match over all tour text, e.g. 'کویر'; it also returns tours to other places, so for a place use destination_id.",
        ),
    ] = None,
    destination_id: Annotated[
        int | None, Field(ge=1, description="Destination place id from jb_tour_places, e.g. 50 (Yazd city).")
    ] = None,
    origin_id: Annotated[
        int | None, Field(ge=1, description="Start place id from jb_tour_places, e.g. 4 (Tehran).")
    ] = None,
    start_date: Annotated[
        date | None, Field(description="Departure on or after this Gregorian date, e.g. '2026-10-20'.")
    ] = None,
    end_date: Annotated[
        date | None, Field(description="Departure on or before this Gregorian date, e.g. '2026-10-31'.")
    ] = None,
    category: Annotated[
        Literal["nature", "adventure", "culinary", "historical-cultural", "sport"] | None,
        Field(description="Tour category."),
    ] = None,
    tags: Annotated[
        list[Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")]] | None,
        Field(
            max_length=10,
            description="Tag slugs (any of), e.g. ['desert'], ['forest', 'lake']. Others: plain, coast, safari, Mountainous, offroad, rafting, camping, historical, village-tour, city-tour, nomad-eco, food-tour, ritual, yoga.",
        ),
    ] = None,
    duration: Annotated[
        Literal["any", "one_day", "multi_day"], Field(description="One-day or multi-day trips.")
    ] = "any",
    min_price: Annotated[int | None, Field(ge=0, description="Lowest price per person in Toman.")] = None,
    max_price: Annotated[
        int | None, Field(ge=0, description="Highest price per person in Toman, e.g. 5000000.")
    ] = None,
    difficulty: Annotated[
        list[Literal["easy", "moderate", "hard"]] | None, Field(max_length=3, description="Difficulty levels (any of).")
    ] = None,
    min_rating: Annotated[float | None, Field(ge=1, le=5, description="Minimum traveller rating 1-5, e.g. 4.")] = None,
    min_free_seats: Annotated[int | None, Field(ge=1, le=100, description="Group size that must fit, e.g. 4.")] = None,
    available_only: Annotated[
        bool, Field(description="Only tours with an upcoming departure that can be booked.")
    ] = True,
    sort: Annotated[
        Literal["recommended", "cheapest", "most_expensive", "soonest", "best_selling", "newest", "top_rated"],
        Field(description="Order; bookable tours always come first."),
    ] = "recommended",
    page: Annotated[int, Field(ge=1, le=100, description="1-based page number.")] = 1,
    limit: Annotated[int, Field(ge=1, le=50, description="Tours per page.")] = 12,
) -> dict[str, Any]:
    """Search Jabama group tours (nature, desert, hiking, history, food tours across Iran) with price per person in Toman.

    For tours to a place, get its id from jb_tour_places (has_tours false = no tours there) and pass
    destination_id; `query` is fuzzy and matches unrelated tours. Cards show the next departure dates;
    free seats and the price of each package per departure only come from jb_tour. A few tours carry
    obviously wrong prices (a 1,850 Toman tour): set min_price when sorting by cheapest.
    """
    gregorian(start_date)
    gregorian(end_date)
    params: dict[str, Any] = {"page": page, "per_page": limit}
    params["sort_by"], params["sort_order"] = SORTS[sort]
    if available_only:
        params["available_only"] = "true"
    for key, value in (
        ("search", query),
        ("destination_location_ids", destination_id),
        ("origin_location_ids", origin_id),
        ("start_date", start_date),
        ("end_date", end_date),
        ("categories", category),
        ("min_price", min_price),
        ("max_price", max_price),
        ("min_rating", min_rating),
        ("capacity", min_free_seats),
    ):
        if value is not None and value != "":
            params[key] = str(value)
    if tags:
        params["tags"] = ",".join(tags)
    if difficulty:
        params["difficulty_levels"] = ",".join(difficulty)
    if duration != "any":
        params["duration_type"] = duration
    data = await fetch(f"{TOURS}/tours", params) or {}
    meta = data.get("meta") or {}
    return {
        "total": meta.get("total"),
        "page": page,
        "last_page": meta.get("last_page"),
        "tours": [_card(t) for t in data.get("data") or []],
    }


@tool("Tour details")
async def jb_tour(
    tour_id: TourId,
    include_past: Annotated[bool, Field(description="Also list past (completed) departures.")] = False,
    full_text: FullText = False,
) -> dict[str, Any]:
    """One group tour: packages, every departure with free seats and the per-person price of each package, day plan, meals, what is included, cancellation rules, guide, operator and reviews.

    Prices are Toman per person; a group pays the package price times the number of people. Pick a
    departure with free_seats >= group size. Booking needs login on jabama.tours.
    """
    try:
        detail, reviews = await asyncio.gather(
            fetch(f"{TOURS}/tours/{tour_id}"), fetch(f"{TOURS}/tours/{tour_id}/reviews", {"page": 1})
        )
    except ApiError as e:
        if e.status == 404:
            raise ApiError(f"Unknown tour id {tour_id}. Get ids from jb_search_tours.", 404) from e
        raise
    t = (detail or {}).get("data") or {}
    packages = {v.get("id"): v.get("name") for v in t.get("variants") or []}
    departures = []
    for d in t.get("departure_dates") or []:
        if d.get("departure_type") != "normal" and not include_past:
            continue
        departures.append(
            {
                "date": d.get("date"),
                "time": d.get("time"),
                "past": d.get("departure_type") != "normal",
                "free_seats": d.get("available_seats"),
                "capacity": d.get("defined_capacity"),
                "minimum_group": d.get("minimum_participants"),
                "minimum_reached": d.get("minimum_reached"),
                "sales_close_hours_before": d.get("sales_close_hours"),
                "package_prices": {
                    packages.get(p.get("variant_id"), p.get("variant_id")): p.get("price")
                    for p in d.get("variant_prices") or []
                },
            }
        )
    rv = t.get("reviews") or {}
    plans = {p.get("day_number"): p for p in t.get("day_plans") or []}
    return {
        "id": t.get("public_id"),
        "title": t.get("title"),
        "url": f"https://jabama.tours/tour/{t.get('public_id')}",
        "bookable": t.get("is_available"),
        "price_per_person": (t.get("price") or {}).get("amount"),
        "days": (t.get("duration") or {}).get("days"),
        "nights": (t.get("duration") or {}).get("nights"),
        "difficulty": (t.get("difficulty") or {}).get("label"),
        "age_group": t.get("age_group"),
        "transport": t.get("transportation"),
        "accommodation": t.get("accommodation_type"),
        "categories": [c.get("name") for c in t.get("categories") or []],
        "tags": [x.get("name") for x in t.get("tags") or []],
        "origin": _place((t.get("location") or {}).get("origin")),
        "destinations": [_place(x) for x in (t.get("location") or {}).get("destinations") or []],
        "summary": clip(t.get("short_description"), 600, full_text),
        # A package's own price is only a default; the real one is per departure (package_prices).
        "packages": [{"name": v.get("name"), "description": v.get("description")} for v in t.get("variants") or []],
        "departures": departures,
        "itinerary": [
            {
                "day": i.get("day_number"),
                "title": i.get("title"),
                "plan": clip(i.get("description"), 500, full_text),
                "meals": [
                    m
                    for m in ("breakfast", "lunch", "dinner")
                    if ((plans.get(i.get("day_number")) or {}).get(m) or {}).get("status") == "included"
                ],
                "sleep": (plans.get(i.get("day_number")) or {}).get("accommodation"),
            }
            for i in t.get("itineraries") or []
        ],
        "included": [x.get("title") for x in (t.get("inclusions") or {}).get("included") or []],
        "not_included": [x.get("title") for x in (t.get("inclusions") or {}).get("excluded") or []],
        "faqs": [
            {"q": f.get("question"), "a": clip(f.get("answer"), 400, full_text)} for f in (t.get("faqs") or [])[:8]
        ],
        "cancellation": [
            {"when": r.get("title"), "penalty_pct": r.get("penalty_percent"), "refundable": r.get("refundable")}
            for r in (t.get("cancellation_policy") or {}).get("rules") or []
        ],
        "guide": _person(t.get("guide")),
        "operator": _person(t.get("operator")),
        "rating": rating(rv.get("rating_avg"), rv.get("count")),  # avg is a string ('4.5')
        "rating_count": rv.get("count"),
        "rating_by_aspect": {m.get("label"): m.get("score") for m in rv.get("metrics") or [] if m.get("score")},
        "reviews": [
            {
                "rating": r.get("rating"),
                "text": clip(r.get("comment"), 400, full_text),
                "author": r.get("author_name"),
                "travelled": r.get("departure_date"),
                "verified": r.get("is_verified"),
            }
            for r in ((reviews or {}).get("data") or [])[:5]
        ],
    }


@tool("Tour places")
async def jb_tour_places(
    query: Annotated[
        str, Field(min_length=1, max_length=60, description="Place name, e.g. 'یزد', 'تهران' or 'masal'.")
    ],
    direction: Annotated[
        Literal["destination", "origin"], Field(description="destination = where tours go; origin = where they start.")
    ] = "destination",
    limit: Annotated[int, Field(ge=1, le=30, description="Max places.")] = 10,
) -> dict[str, Any]:
    """Turn a place name into a Jabama Tours place id for jb_search_tours (destination_id / origin_id).

    Cities and provinces come first, then areas and attractions. has_tours is false for places no
    tour visits yet.
    """
    data = await fetch(f"{TOURS}/locations/search", {"q": query.strip(), "direction": direction, "limit": limit})
    return {
        "places": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "type": p.get("type"),
                "in": (p.get("parent") or {}).get("name"),
                "has_tours": p.get("has_tours"),
            }
            for p in ((data or {}).get("data") or [])[:limit]
        ]
    }


def _card(t: dict[str, Any]) -> dict[str, Any]:
    rv = t.get("reviews") or {}
    loc = t.get("location") or {}
    return {
        "id": t.get("public_id"),
        "title": t.get("title"),
        "price_per_person": (t.get("price") or {}).get("amount"),
        "days": (t.get("duration") or {}).get("days"),
        "nights": (t.get("duration") or {}).get("nights"),
        "difficulty": (t.get("difficulty") or {}).get("label"),
        "origin": (loc.get("origin") or {}).get("name"),
        "destinations": [d.get("name") for d in loc.get("destinations") or []],
        # list seat counts are multiplied by the number of packages; free seats come from jb_tour
        "next_departures": [
            f"{d.get('date')} {d.get('time') or ''}".strip() for d in (t.get("departure_dates") or [])[:5]
        ],
        "bookable": t.get("is_available"),
        "rating": rating(rv.get("rating_avg"), rv.get("count")),  # avg is a string ('4.5')
        "rating_count": rv.get("count"),
        "operator": (t.get("operator") or {}).get("name"),
        "url": f"https://jabama.tours/tour/{t.get('public_id')}",
    }


def _place(p: Any) -> str | None:
    return (p or {}).get("name")


def _person(p: Any) -> dict[str, Any] | None:
    if not p:
        return None
    return {
        "name": p.get("name"),
        "experience_years": p.get("experience"),
        "rating": p.get("rating") or None,
        "review_count": p.get("review_count"),
        "verified": p.get("is_verified"),
    }
