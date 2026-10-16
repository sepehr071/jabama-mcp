"""Jabama Events (jabama.events) tools: events and experiences, sessions and seats, theater plays."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from pydantic import Field

from .http import EVENTS, EVENTS_WEB, ApiError, fetch
from .info import html_text
from .registry import tool
from .search import TEHRAN, gregorian
from .stay import FullText, clip, fa_float

Slug = Annotated[str | None, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=60)]
EventId = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9_-]{1,40}$", description="Event id from jb_search_events, e.g. '69063962' or 'legacy-622'."
    ),
]
PlayId = Annotated[str, Field(pattern=r"^\d{1,9}$", description="Play id from jb_theaters, e.g. '65745'.")]
SORTS = {"newest": "new", "cheapest": "price"}
BOOK_ROUTES = {"_routes": "routes/theaters,routes/theaters.$eventId_.book"}


@tool("Search events")
async def jb_search_events(
    query: Annotated[str | None, Field(max_length=100, description="Words, e.g. 'آفرود' or 'کنسرت'.")] = None,
    city: Annotated[
        Slug, Field(description="City slug from jb_event_filters, e.g. 'tehran', 'ramsar', 'bandar_anzali'.")
    ] = None,
    category: Annotated[
        Slug, Field(description="Category slug from jb_event_filters, e.g. 'offroad', 'pottery', 'event-and-workshop'.")
    ] = None,
    kind: Annotated[
        Literal["all", "events", "experiences"],
        Field(description="events = shows, workshops, exhibitions; experiences = tours, boats, riding, food, ..."),
    ] = "all",
    on_date: Annotated[
        date | None, Field(description="Only events with a session that day, Gregorian YYYY-MM-DD, e.g. '2026-10-10'.")
    ] = None,
    price: Annotated[Literal["all", "free", "paid"], Field(description="Free or paid events.")] = "all",
    rated_4_plus: Annotated[bool, Field(description="Only events rated 4 or more.")] = False,
    sort: Annotated[
        Literal["recommended", "newest", "cheapest"], Field(description="cheapest puts free events first.")
    ] = "recommended",
    limit: Annotated[int, Field(ge=1, le=100, description="Events per page.")] = 20,
    cursor: Annotated[
        str | None, Field(max_length=500, description="next_cursor of the previous page, to get the next one.")
    ] = None,
) -> dict[str, Any]:
    """Search Jabama Events: concerts, shows, workshops, exhibitions and experiences (offroad, boats, riding, food tours) with prices in Toman.

    Only events with an upcoming session are returned. Theater plays are separate: jb_theaters.
    For seated=true, price is the "from" base; seat sections cost more (jb_event_seats). on_date is one
    day; for a weekend call once per day. Next: jb_event for sessions, seats left, details and reviews.
    """
    gregorian(on_date)
    params: dict[str, Any] = {"hasActiveSession": "true", "limit": limit}
    for key, value in (("q", query), ("city", city), ("category", category), ("cursor", cursor)):
        if value:
            params[key] = value
    if kind != "all":
        params["type"] = kind
    if on_date:
        params["date"] = on_date.isoformat()
    if price != "all":
        params["price"] = price
    if rated_4_plus:
        params["rating"] = "4"
    if sort in SORTS:
        params["sort"] = SORTS[sort]
    data = await fetch(f"{EVENTS}/events", params) or {}
    return {
        "total": data.get("total"),
        "next_cursor": data.get("nextCursor"),
        "events": [_card(e) for e in data.get("items") or []],
    }


@tool("Event details")
async def jb_event(
    event_id: EventId,
    max_sessions: Annotated[int, Field(ge=1, le=100, description="Max sessions (showtimes or days) to list.")] = 20,
    full_text: FullText = False,
) -> dict[str, Any]:
    """One event or experience: what it is, exact address, rules, what to bring, price, refundability, sessions with seats left, reviews and the organizer.

    Times are Tehran local; a start with no time is a day session (hours are in `when`). A session
    with seated=true has a seat map, and its price is only a "from" base: call jb_event_seats with its
    session_id for the free seats and real section prices. Buying a ticket needs login on jabama.events.
    """
    try:
        e, sessions = await asyncio.gather(
            fetch(f"{EVENTS}/events/{event_id}"), fetch(f"{EVENTS}/events/{event_id}/sessions")
        )
    except ApiError as err:
        if err.status == 404:
            raise ApiError(f"Unknown event id {event_id}. Get ids from jb_search_events.", 404) from err
        raise
    e = e or {}
    org = e.get("organizer") or {}
    organizer = None
    if org.get("id"):
        try:
            o = await fetch(f"{EVENTS}/organizers/{org['id']}") or {}
        except ApiError:
            o = {}
        # The organizer object can carry a personal phone number; it is deliberately left out.
        organizer = {
            "name": org.get("name"),
            "verified": o.get("verified"),
            "rating": _rating(o.get("rating"), o.get("reviewCount")),
            "review_count": o.get("reviewCount"),
            "open_events": len(o.get("experiences") or []),
            "links": [s.get("url") for s in o.get("socials") or [] if s.get("url")],
            "page": f"{EVENTS_WEB}/{org.get('username')}" if org.get("username") else None,
        }
    loc = e.get("location") or {}
    per_unit = e.get("pricingMode") == "per_unit"
    return {
        "id": e.get("id"),
        "title": e.get("title"),
        "url": f"{EVENTS_WEB}/events/{e.get('id')}",
        "category": (e.get("category") or {}).get("label"),
        "when": e.get("duration"),
        "duration_hours": e.get("durationHours"),
        "venue": loc.get("label"),
        "address": loc.get("address"),
        "city": (loc.get("city") or {}).get("label"),
        "location": loc.get("exact") or None,
        "pricing": "per_unit" if per_unit else "per_person",
        "price": e.get("unitPrice") if per_unit else e.get("pricePerPerson"),
        "price_after_discount": e.get("minPriceAfterDiscount"),
        "discount_pct": e.get("maxDiscountPct") or 0,
        "party_size": {"min": e.get("minPartySize"), "max": e.get("maxPartySize")},
        "refundable": e.get("refundable"),
        "vat_pct": (e.get("ticketVatRateBps") or 0) / 100 if e.get("ticketVatEnabled") else 0,
        "rating": _rating(e.get("rating"), e.get("reviewCount")),
        "review_count": e.get("reviewCount"),
        "description": clip(html_text(e.get("description")), 1500, full_text),
        "program": clip(html_text(e.get("itinerary")), 1000, full_text),
        "terms": clip(html_text(e.get("termsAndConditions")), 800, full_text),
        "what_to_bring": [x.get("label") for x in e.get("whatToBring") or []],
        "included": [x.get("label") for x in e.get("includedItems") or []],
        "artists": [a.get("name") for a in e.get("artists") or []],
        "sessions": [_session(s) for s in (sessions or [])[:max_sessions]],
        "more_sessions": max(0, len(sessions or []) - max_sessions),
        "reviews": [
            {
                "rating": r.get("rating"),
                "text": clip(r.get("text"), 400, full_text),
                "author": r.get("userName"),
                "date": (r.get("createdAt") or "")[:10] or None,
                "organizer_reply": r.get("organizerReply"),
            }
            for r in (e.get("reviews") or [])[:8]
        ],
        "organizer": organizer,
    }


@tool("Event seats")
async def jb_event_seats(
    session_id: Annotated[
        str, Field(pattern=r"^\d{1,20}$", description="session_id of a seated session from jb_event, e.g. '47540640'.")
    ],
) -> dict[str, Any]:
    """Live seat availability of one seated event session: free seats per section and row, with the section prices in Toman.

    Only for sessions with seated=true in jb_event; others are general admission (just seats_left).
    Section prices override the session `price`; cheapest_free_seat_price is what the cheapest free
    seat really costs. Holding or buying seats needs login on jabama.events.
    """
    try:
        layout, status = await asyncio.gather(
            fetch(f"{EVENTS}/sessions/{session_id}/seat-layout"), fetch(f"{EVENTS}/sessions/{session_id}/seat-status")
        )
    except ApiError as e:
        if e.status == 404:
            raise ApiError(
                f"No seat map for session {session_id}: unknown session, or general admission (use seats_left from jb_event).",
                404,
            ) from e
        raise
    layout, status = layout or {}, status or {}
    manifest = layout.get("manifest") or {}
    pricing = layout.get("pricing") or {}
    section_price = {p.get("sectionId"): p.get("priceToman") for p in layout.get("sectionPrices") or []}
    count = status.get("seatCount") or len(manifest.get("seats") or [])
    taken = set()
    for key in ("soldBitmap", "blockedBitmap", "reservedBitmap", "heldBitmap"):
        taken |= _bits(status.get(key), count)
    names = {s.get("key"): s.get("name") for s in manifest.get("sections") or []}
    sections: dict[str, dict[str, Any]] = {}
    for s in manifest.get("sections") or []:
        sections[s.get("key")] = {
            "name": s.get("name"),
            "price": section_price.get(s.get("id"), pricing.get("finalPriceToman")),
            "seats": len(s.get("seatIndexes") or []),
            "free": 0,
        }
    rows: dict[str, list[str]] = {}
    for seat in manifest.get("seats") or []:
        if seat.get("index") in taken or not seat.get("visible", True):
            continue
        sec = sections.get(seat.get("sectionKey"))
        if sec:
            sec["free"] += 1
        rows.setdefault(
            f"{names.get(seat.get('sectionKey'), seat.get('sectionKey'))} / row {seat.get('row')}", []
        ).append(str(seat.get("number")))
    return {
        "session_id": session_id,
        "starts": local(layout.get("startIso")),
        "price": pricing.get("finalPriceToman"),
        "price_before_discount": pricing.get("basePriceToman"),
        "cheapest_free_seat_price": min(
            (s["price"] for s in sections.values() if s["free"] and s["price"] is not None), default=None
        ),
        "seat_count": count,
        "free_seats": count - len(taken),
        "sections": list(sections.values()),
        "free_by_row": dict(list(rows.items())[:60]),
    }


@tool("Theater plays")
async def jb_theaters(
    limit: Annotated[int, Field(ge=1, le=20, description="Plays per page.")] = 10,
    cursor: Annotated[str | None, Field(pattern=r"^\d{1,9}$", description="next_cursor of the previous page.")] = None,
) -> dict[str, Any]:
    """List theater plays on sale on Jabama Events (Tehran, via the partner Zirbana): venue, address, from-price in Toman, rating, writer, director and cast.

    Next: jb_theater for showtimes, seats left and the free seats of a showtime.
    """
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    data = await fetch(f"{EVENTS}/zirbana/events", params) or {}
    return {"next_cursor": data.get("nextCursor"), "plays": [_play(p) for p in data.get("items") or []]}


@tool("Theater play")
async def jb_theater(
    play_id: PlayId,
    showtime_id: Annotated[
        str | None,
        Field(
            pattern=r"^\d{1,9}:\d{1,12}$",
            description="A showtime id from this tool's showtimes, e.g. '65745:642277', to list its free seats.",
        ),
    ] = None,
    full_text: FullText = False,
) -> dict[str, Any]:
    """One theater play: venue, cast and crew, reviews, showtimes with live seats left; with showtime_id also the free seats by row and their prices.

    Prices are Toman per seat. The seat map is fetched live from the partner and is sometimes
    unavailable for a while (seats then holds the error); seats_left still works. Buying needs login.
    """
    try:
        play, book = await asyncio.gather(
            fetch(f"{EVENTS}/zirbana/events/{play_id}"),
            fetch(f"{EVENTS_WEB}/theaters/{play_id}/book.data", BOOK_ROUTES, text=True),
        )
    except ApiError as e:
        if e.status == 404:
            raise ApiError(f"Unknown play id {play_id}. Get ids from jb_theaters.", 404) from e
        raise
    try:
        page = turbo_decode(book).get("routes/theaters.$eventId_.book", {}).get("data") or {}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        page = {}
    instances = [i for i in page.get("instances") or [] if i.get("active")]
    live: dict[str, Any] = {}
    off_sale: set[str] = set()
    if instances:
        try:
            s = (
                await fetch(
                    f"{EVENTS}/zirbana/events/{play_id}/session-summaries",
                    {"ids": ",".join(i["id"] for i in instances)},
                )
                or {}
            )
            live = {x.get("instanceId"): x for x in s.get("items") or []}
            off_sale = set(s.get("missingIds") or []) | set(s.get("unavailableIds") or [])
        except ApiError:
            pass  # fall back to the page snapshot
    showtimes = []
    for i in sorted(instances, key=lambda x: x.get("startsAt") or ""):
        now = live.get(i.get("id"))
        showtimes.append(
            {
                "showtime_id": i.get("id"),
                "starts": local(i.get("startsAt")),
                "title": i.get("title"),
                "price": ((now or {}).get("fromPrice") or {}).get("amount") or i.get("price"),
                "seats_left": (now or i).get("remaining"),
                "capacity": (now or i).get("capacity"),
                "live": now is not None,
                "note": i.get("label")
                or ("not on sale now (missing from the live check)" if i.get("id") in off_sale else None),
            }
        )
    out = {**_play(play or {}, cast=12), "on_sale": (play or {}).get("salesEnabled"), "showtimes": showtimes}
    out["reviews"] = [clip(r.get("text"), 400, full_text) for r in ((play or {}).get("reviews") or [])[:5]]
    if showtime_id and showtime_id not in {s["showtime_id"] for s in showtimes}:
        out["seats"] = {
            "error": f"Unknown showtime {showtime_id} for play {play_id}; pick a showtime_id from showtimes."
        }
    elif showtime_id:
        out["seats"] = await _theater_seats(play_id, showtime_id)
    return out


@tool("Event cities and categories")
async def jb_event_filters(
    city: Annotated[
        Slug, Field(description="City slug to count categories in, e.g. 'tehran'; default all cities.")
    ] = None,
) -> dict[str, Any]:
    """Cities and categories of Jabama Events with event counts, for the city and category filters of jb_search_events."""
    cities, categories = await asyncio.gather(
        fetch(f"{EVENTS}/events/cities"), fetch(f"{EVENTS}/events/categories", {"city": city} if city else None)
    )
    return {
        "cities": [
            {
                "slug": c.get("slug"),
                "name": c.get("label"),
                "province": c.get("provinceLabel"),
                "events": c.get("eventCount"),
                "something_today": c.get("todayAvailable"),
            }
            for c in cities or []
        ],
        "categories": [
            {"slug": c.get("slug"), "name": c.get("label"), "events": c.get("eventCount")}
            for c in categories or []
            if c.get("eventCount")
        ],
    }


async def _theater_seats(play_id: str, showtime_id: str) -> dict[str, Any]:
    inst = quote(showtime_id, safe="")
    try:
        seatmap, avail = await asyncio.gather(
            fetch(f"{EVENTS}/zirbana/events/{play_id}/instances/{inst}/seatmap"),
            fetch(f"{EVENTS}/zirbana/events/{play_id}/instances/{inst}/availability"),
        )
    except ApiError as e:
        return {"error": f"Seat map not available right now ({e}). Try again later or another showtime."}
    avail = avail or {}
    taken = (
        set(avail.get("unavailableValues") or [])
        | set(avail.get("reservedValues") or [])
        | set(avail.get("pendingValues") or [])
    )
    rows: dict[str, list[str]] = {}
    prices = set()
    for s in (seatmap or {}).get("seats") or []:
        if s.get("blocked") or s.get("value") in taken:
            continue
        section, row, number = ([*str(s.get("label") or "").split(" · "), "", ""])[:3]
        rows.setdefault(f"section {section} / row {row}", []).append(number)
        prices.add(s.get("price"))
    return {
        "free_seats": sum(len(v) for v in rows.values()),
        "prices": sorted(p for p in prices if p is not None),
        "free_by_row": rows,
    }


def _card(e: dict[str, Any]) -> dict[str, Any]:
    per_unit = e.get("pricingMode") == "per_unit"
    return {
        "id": e.get("id"),
        "title": e.get("title"),
        "category": (e.get("category") or {}).get("label"),
        "city": (e.get("city") or {}).get("label"),
        "venue": e.get("locationLabel"),
        "when": e.get("duration"),
        "next_session": local(e.get("firstAvailableSessionStartIso")),
        "pricing": "per_unit" if per_unit else "per_person",
        "price": e.get("unitPrice") if per_unit else e.get("pricePerPerson"),
        "price_after_discount": e.get("minPriceAfterDiscount"),
        "rating": _rating(e.get("rating"), e.get("reviewCount")),
        "review_count": e.get("reviewCount"),
        "seated": e.get("inventoryMode") == "assigned_seat",
        "organizer": e.get("organizerName"),
        "url": f"{EVENTS_WEB}/events/{e.get('id')}",
    }


def _session(s: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "session_id": s.get("id"),
        "starts": local(s.get("startIso")),
        "duration_hours": s.get("durationHours"),
        "bookable": s.get("enabled"),
        "seated": s.get("inventoryMode") == "assigned_seat",
        "seats_left": s.get("remainingSeats"),
        "capacity": s.get("capacityLimit"),
    }
    if s.get("pricePerPerson") is not None or s.get("unitPrice") is not None:
        out["price"] = s.get("pricePerPerson") if s.get("pricePerPerson") is not None else s.get("unitPrice")
    if s.get("timeslots"):
        out["timeslots"] = [
            {
                "starts": local(t.get("startAt")),
                "ends": local(t.get("endAt")),
                "seats_left": t.get("remainingSeats"),
                "bookable": t.get("enabled"),
            }
            for t in s["timeslots"][:10]
        ]
    return out


def _play(p: dict[str, Any], cast: int = 6) -> dict[str, Any]:
    crew = {}
    for c in p.get("contributors") or []:
        names = [x.get("name") for x in c.get("people") or []]
        if names and len(crew) < cast:
            crew[c.get("label")] = names[:8]
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "url": f"{EVENTS_WEB}/theaters/{p.get('id')}",
        "venue": p.get("venueText"),
        "address": p.get("address"),
        "from_price": (p.get("fromPrice") or {}).get("amount"),
        "rating": round(p["rating"], 1) if p.get("rating") and p.get("ratingCount") else None,
        "rating_count": p.get("ratingCount"),
        "crew": crew,
    }


def _rating(value: Any, count: Any) -> float | None:
    """Events send ratings as Persian-digit strings ('۴.۸'); '۰.۰' with no reviews means not rated."""
    return round(fa_float(value), 1) if count and fa_float(value) else None


def _bits(b64: Any, count: int) -> set[int]:
    """Indexes of set bits in a base64 msb-first bitmap (bit i = seat index i)."""
    if not b64:
        return set()
    raw = base64.b64decode(b64)
    return {i for i in range(min(count, len(raw) * 8)) if raw[i // 8] >> (7 - i % 8) & 1}


def local(iso: Any) -> str | None:
    """UTC ISO time -> Tehran local 'YYYY-MM-DD HH:MM'; just the date for 04:00, the placeholder of day sessions."""
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TEHRAN)
    except ValueError:
        return None
    # Galleries and exhibitions store a day as 00:30Z (04:00 Tehran); the real hours are in the event's `when`.
    return t.strftime("%Y-%m-%d" if t.strftime("%H:%M") == "04:00" else "%Y-%m-%d %H:%M")


SPECIAL = {-1: None, -2: float("nan"), -3: float("-inf"), -4: 0, -5: None, -6: float("inf"), -7: None}


def turbo_decode(text: str) -> dict[str, Any]:
    """Decode a React Router turbo-stream `.data` body (first line is a flat value table with index refs)."""
    vals: list[Any] = json.loads(text.split("\n", 1)[0])
    memo: dict[int, Any] = {}

    def d(i: int) -> Any:
        if i in SPECIAL:
            return SPECIAL[i]
        if i in memo:
            return memo[i]
        v = vals[i]
        if isinstance(v, dict):
            out: dict[str, Any] = {}
            memo[i] = out
            for k, vi in v.items():
                out[vals[int(k[1:])]] = d(vi)
            return out
        if isinstance(v, list):
            if v and isinstance(v[0], str):  # typed value, e.g. ["D", ms] for a date
                memo[i] = v[1] if v[0] == "D" else {v[0]: [d(x) for x in v[1:]]}
                return memo[i]
            lst: list[Any] = []
            memo[i] = lst
            lst.extend(d(x) for x in v)
            return lst
        memo[i] = v
        return v

    result = d(0)
    return result if isinstance(result, dict) else {}
