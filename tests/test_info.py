import pytest
from conftest import body, fixture

from jabama_mcp.info import html_text

pytestmark = pytest.mark.anyio

FAQ = "/help/wp-json/faq/v1/search"
SUPPORT = "/support/api/v1/chat/faqs/public"
BLOG = "/mag/api/site-search"


async def test_jb_help(client, api):
    api[FAQ] = fixture("faq_search.json")
    api[SUPPORT] = fixture("support_faq.json")
    data = (await client.call_tool("jb_help", {"query": "كنسلي", "limit": 2})).structured_content
    assert [h["question"] for h in data["help_center"]] == [
        "آیا برای پرداخت و کنسلی  رزرو باید مالیات پرداخت کنم ؟",
        "در صورت لغو رزرو از جانب میزبان کنسلی رزرو به چه صورت خواهد شد؟",
    ]
    assert data["help_center"][0]["answer"].startswith("زمان ثبت رزرو مساوی با 1.5 درصد")
    quick = data["quick_answers"]  # 3 of the 5 support answers mention it; limit keeps 2, in score order
    assert len(quick) == 2 and quick[0]["question"].startswith("مالیات مربوط به رزرو و کنسلی")
    assert [q["category"] for q in quick] == ["پرداخت", "رزرو"]
    faq_call = next(r for r in api.calls if r.url.path == FAQ)
    assert body(faq_call) == {"keyword": "کنسلی"}  # Arabic ي/ك normalised to Persian


async def test_jb_help_no_help_center_match(client, api):
    api[FAQ] = (404, fixture("faq_none.json"))
    api[SUPPORT] = fixture("support_faq.json")
    data = (await client.call_tool("jb_help", {"query": "zzqxw"})).structured_content
    assert data == {"help_center": [], "quick_answers": []}


async def test_jb_travel_guide(client, api):
    api[BLOG] = fixture("blog.json")
    data = (
        await client.call_tool("jb_travel_guide", {"query": "رامسر", "limit": 2, "text_chars": 120})
    ).structured_content
    assert data["total"] == 115 and len(data["articles"]) == 2
    a = data["articles"][0]
    assert a["url"] == "https://www.jabama.com/mag/travel-to-ramsar-by-plane/"
    assert a["published"] == "2026-10-03"
    assert a["topics"] == ["استان مازندران", "ایرانگردی با ما", "رامسر", "شمال"]
    assert a["text"].startswith("سفر به رامسر با هواپیما") and len(a["text"]) <= 120 and "<" not in a["text"]
    assert dict(api.calls[0].url.params) == {"q": "رامسر", "type": "post"}


async def test_jb_travel_guide_summary_only(client, api):
    api[BLOG] = {"results": []}  # the shape for a too-short query
    data = (await client.call_tool("jb_travel_guide", {"query": "zz"})).structured_content
    assert data == {"total": 0, "articles": []}


def test_html_text():
    assert html_text("<p>a&amp;b</p><p>c<br/>d</p>") == "a&b\nc\nd"
