"""Help and content tools: FAQ (rules, cancellation, payment) and the travel-guide blog."""

from __future__ import annotations

import asyncio
import html
import re
from typing import Annotated, Any

from pydantic import Field

from .http import WWW, ApiError, fetch
from .registry import tool
from .stay import FullText, clip


@tool("Help and rules")
async def jb_help(
    query: Annotated[
        str,
        Field(
            min_length=2,
            max_length=100,
            description="Topic in Persian, e.g. 'کنسلی' (cancellation), 'استرداد' (refund), 'مالیات' (tax), 'پرداخت' (payment).",
        ),
    ],
    limit: Annotated[int, Field(ge=1, le=10, description="Max answers from each source.")] = 5,
    full_text: FullText = False,
) -> dict[str, Any]:
    """Answer questions about Jabama's rules: cancellation and refunds, payment, tax, check-in, host and guest duties.

    Searches the official help center (long answers) and the support app's short tagged answers.
    For one listing's own cancellation windows use jb_stay or jb_price_quote.
    """
    q = query.strip().replace("ي", "ی").replace("ك", "ک")
    search, support = await asyncio.gather(_faq_search(q), fetch(f"{WWW}/support/api/v1/chat/faqs/public"))
    words = [w for w in q.split() if len(w) > 1]
    scored = []
    for f in (support or {}).get("faqs") or []:
        hay = " ".join([f.get("question") or "", f.get("answer") or "", *(f.get("tags") or [])])
        score = sum(w in hay for w in words)
        if score:
            scored.append((score, f))
    scored.sort(key=lambda x: -x[0])
    return {
        "help_center": [
            {
                "question": (f.get("post_title") or "").strip(),
                "answer": clip(html_text(f.get("post_content")), 1200, full_text),
            }
            for f in search[:limit]
        ],
        "quick_answers": [
            {
                "question": f.get("question"),
                "answer": clip(f.get("answer"), 800, full_text),
                "category": f.get("category"),
            }
            for _, f in scored[:limit]
        ],
    }


@tool("Travel guide")
async def jb_travel_guide(
    query: Annotated[
        str,
        Field(
            min_length=2,
            max_length=100,
            description="Destination or topic in Persian, e.g. 'رامسر' or 'جاهای دیدنی کیش'.",
        ),
    ],
    limit: Annotated[int, Field(ge=1, le=10, description="Max articles.")] = 5,
    text_chars: Annotated[
        int, Field(ge=0, le=8000, description="Characters of article text to include per article; 0 = summary only.")
    ] = 0,
) -> dict[str, Any]:
    """Search Jabama's travel magazine (/mag): guides on attractions, best time to visit, how to get there, food.

    Returns the top matches (the site gives at most 10) with a summary and link; set text_chars to
    read the articles themselves. Use for trip planning next to jb_search_stays.
    """
    data = await fetch(f"{WWW}/mag/api/site-search", {"q": query.strip(), "type": "post"})
    res = (data or {}).get("results")
    if not isinstance(res, dict):  # a too-short query answers {"results": []}
        res = {}
    out = []
    for a in (res.get("results") or [])[:limit]:
        article = {
            "title": a.get("title"),
            "url": f"{WWW}/mag/{a.get('slug')}/",
            "summary": a.get("excerpt"),
            "published": (a.get("publishedAt") or "")[:10] or None,
            "topics": [(c.get("category") or {}).get("fa") for c in a.get("categories") or []],
        }
        if text_chars:
            article["text"] = clip(html_text(a.get("content")), text_chars)
        out.append(article)
    return {"total": res.get("totalCount", 0), "articles": out}


async def _faq_search(q: str) -> list[dict[str, Any]]:
    try:
        data = await fetch(f"{WWW}/help/wp-json/faq/v1/search", method="POST", json={"keyword": q})
    except ApiError as e:
        if e.status == 404:  # "no_faqs": nothing matched
            return []
        raise
    return data if isinstance(data, list) else []


def html_text(s: Any) -> str:
    """Plain text from WordPress / Lexical HTML: tags dropped, entities decoded, blank lines collapsed."""
    text = re.sub(r"<(br|/p|/div|/li|/h\d)\s*/?>", "\n", s or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t\r]+", " ", text)).strip()
