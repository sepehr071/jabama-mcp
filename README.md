<!-- mcp-name: io.github.sepehr071/jabama-mcp -->

<div align="center">

<img src="https://raw.githubusercontent.com/sepehr071/jabama-mcp/main/.github/banner.png" alt="jabama-mcp: let your AI agent price stays, tours and events on Jabama" width="100%">

# 🏡 jabama-mcp

**Let your AI agent plan the trip on Jabama.**<br>
Search villas, suites and eco-lodges with the exact price for your dates and guests, read rules and reviews,<br>
and find group tours, events and theater seats, all from Claude, Cursor or Copilot.

[![PyPI](https://img.shields.io/pypi/v/jabama-mcp?color=2563eb)](https://pypi.org/project/jabama-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/jabama-mcp)](https://pypi.org/project/jabama-mcp/)
[![CI](https://github.com/sepehr071/jabama-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/jabama-mcp/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.sepehr071%2Fjabama--mcp-7c3aed)](https://registry.modelcontextprotocol.io/?q=jabama-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/jabama-mcp/blob/main/LICENSE)

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=jabama&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJqYWJhbWEtbWNwIl19)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_jabama--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=jabama&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22jabama-mcp%22%5D%7D)

[Quick start](#quick-start) · [What it can do](#what-it-can-do) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## Why

On Jabama (jabama.com) a search card shows one price, but the real cost of a stay depends on the nights you pick
(weekends and holidays are priced differently), how many guests come (every guest above the base count adds a
nightly charge) and long-stay discounts. An agent with `jabama-mcp` reads the search, the nightly calendar and the
site's own price quote, so the number it gives you is the number you pay:

> **You:** A cheap eco-lodge near Ramsar for 4 people, 20 to 23 October?
>
> **Agent:** *calls* `jb_find_destination(query="رامسر")` → `jb_search_stays(keyword="city-ramsar", check_in="2026-10-20", check_out="2026-10-23", guests=4, types=["ecotourism"], sort="cheapest")` → `jb_price_quote(code=328353, check_in="2026-10-20", check_out="2026-10-23", guests=4)`
>
> | Night | Type | Night price | 2 extra guests | Total |
> |---|---|---:|---:|---:|
> | 20 Oct | weekday | 600,000 | 300,000 | 900,000 |
> | 21 Oct | weekend | 520,000 | 300,000 | 820,000 |
> | 22 Oct | weekend | 520,000 | 300,000 | 820,000 |
>
> نسا - افرا in Tonekabon: **2,540,000 Toman** for 3 nights and 4 guests (or 4 parts of 635,000). The host must
> accept the booking first; cancelling up to 24 Mehr costs 10% of the first night and 10% of the rest.

<sub>Real tool output from 2026-10-04; prices change all the time. Prices are in Toman.</sub>

## What it can do

- 📍 **Find a place**: Persian city, area or landmark names (a beach, a mall, an airport) to search filters
- 🗣️ **Understand a request**: free text like «ویلای استخردار در رامسر برای ۶ نفر» becomes filters, with Jabama's own AI parser
- 🔎 **Search stays** by dates and guests with type, amenities, region, rooms, price, instant booking and rating filters
- 🧾 **Get the exact price** for dates and guests, night by night, with discounts and the cancellation windows
- 📅 **See the calendar**: free nights and nightly prices for the next ~76 days, with weekends and holidays
- ⭐ **Check quality**: house rules, amenities (and what is missing), reviews, host reputation, similar stays
- 🚌 **Group tours** (jabama.tours): search, departures with free seats and per-package prices, day plans
- 🎭 **Events and theater** (jabama.events): what is on, sessions, seats left, free seats by row and price
- 🔒 **Read-only by design**: no login, no booking, no seat holds, no payment

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/).

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add jabama -- uvx jabama-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "jabama": { "command": "uvx", "args": ["jabama-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

Click **Install in Cursor** above, or add the Claude Desktop block to `~/.cursor/mcp.json`.
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "jabama": { "type": "stdio", "command": "uvx", "args": ["jabama-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Anything else</b></summary>

It's a standard stdio MCP server: run `uvx jabama-mcp`, or `pip install jabama-mcp` and run `jabama-mcp`.
</details>

Then just ask:

- "Cheapest villa with a pool in Ramsar for 6 people next weekend, and the exact total?"
- "Which nights is this cottage free in November, and which are cheapest?"
- "A one-day nature tour from Tehran this month, with free seats for 3."
- "What's on in Tehran on Friday evening under 500,000 Toman? Show the free seats."
- <span dir="rtl">قوانین کنسلی جاباما چیست و چقدر از پول برمی&zwnj;گردد؟</span>

## How it works

```text
  AI agent  (Claude, Cursor, Copilot, ...)
      │
      │  MCP over stdio
      ▼
  jabama-mcp  (runs on your machine)
      │
      │  HTTPS (JSON)
      ├──────▶  gw.jabama.com, www.jabama.com   (stays, help, magazine)
      ├──────▶  api.jabama.tours                 (group tours)
      └──────▶  api.jabama.events, jabama.events (events, theater)
```

`jabama-mcp` runs locally and calls the same public endpoints the Jabama websites use.
There's no hosted server in between, no API key, and nothing about you is sent anywhere else.

## Tools

Listings are identified by their numeric code (`328353`, the number in `jabama.com/stay/ecotourism-328353`);
tours by an 8-digit id, events and plays by the id in their page URL.

<details open>
<summary><b>🔎 Stay search</b> (4)</summary>

| Tool | What it does |
|---|---|
| `jb_find_destination` | Persian place name → ready search arguments (city, area, landmark, complex) |
| `jb_parse_request` | Free-text request → search arguments, using Jabama's AI parser |
| `jb_search_stays` | Search stays for dates and guests with filters, whole-stay prices, sorting |
| `jb_categories` | Themed lists (special pool, luxury, cheap, jungle, pet friendly, last-minute, ...) |
</details>

<details open>
<summary><b>🏡 One stay</b> (6)</summary>

| Tool | What it does |
|---|---|
| `jb_stay` | Capacity, beds, rules, amenities and missing ones, cancellation policy, host, review scores |
| `jb_stay_calendar` | Free nights and nightly prices with weekend and holiday flags (~76 days) |
| `jb_price_quote` | Exact payable amount for dates and guests, night by night; unit options for complexes |
| `jb_similar_stays` | Similar stays nearby (priced for your dates) and other units of the same property |
| `jb_reviews` | Guest reviews, newest or best/worst first, with host replies |
| `jb_host` | A host's listings and reviews across all of them |
</details>

<details open>
<summary><b>ℹ️ Help and guides</b> (2)</summary>

| Tool | What it does |
|---|---|
| `jb_help` | Jabama's rules: cancellation, refunds, payment, tax (help center + support answers) |
| `jb_travel_guide` | Jabama magazine travel guides for a destination |
</details>

<details open>
<summary><b>🎭 Events and theater</b> (6)</summary>

| Tool | What it does |
|---|---|
| `jb_search_events` | Events and experiences by city, category, date, price and rating |
| `jb_event` | One event: details, address, rules, sessions with seats left, reviews, organizer |
| `jb_event_seats` | Free seats of a seated session by section and row, with section prices |
| `jb_theaters` | Theater plays on sale: venue, from-price, rating, cast |
| `jb_theater` | One play: showtimes with live seats left; free seats of a showtime |
| `jb_event_filters` | Event cities and categories with counts |
</details>

<details open>
<summary><b>🚌 Group tours</b> (3)</summary>

| Tool | What it does |
|---|---|
| `jb_search_tours` | Tours by place, dates, category, tags, duration, price, difficulty, rating |
| `jb_tour` | One tour: departures with free seats and package prices, day plan, inclusions, cancellation, reviews |
| `jb_tour_places` | Place name → place id for the tour search |
</details>

All 21 tools are annotated `readOnlyHint: true` and return compact structured JSON, so they don't flood the agent's context.

## Good to know

- **Prices are in Toman** everywhere (1 Toman = 10 Rial). The stays API answers in Rial; the server divides by 10. Tours are Toman **per person**, events and theater Toman per ticket (or per unit, e.g. one boat, when `pricing` is `per_unit`).
- **Stay prices are for the whole stay** for the searched dates and guests (`total_price`); `cheapest_night` is the cheapest single night, not the average. Without dates a search prices one default night.
- **Stay cards** (`jb_search_stays`, `jb_similar_stays`) carry `image`, the cover photo, and `url`; with dates the `url` opens the listing on those dates and guests, priced like the quote.
- **Count children as guests.** The quote has no child price, so `guests` is everyone who stays.
- **Dates are Gregorian** `YYYY-MM-DD` (1405-07-28 = 2026-10-20); times are Tehran local. Stays can be quoted for about the next 76 days.
- **`jb_price_quote` is the number to trust.** It is the same read-only quote the listing page shows; it creates no booking. Quotes show no tax line; Jabama's help center says 1.5% tax is added when booking.
- **Instant vs request:** `instant_booking: false` means the host must accept first. `instant_only` keeps only instant listings.
- **Tour seats:** only `jb_tour` has correct free seats; tour lists count seats once per package.
- **Ratings are 0–5**; `null` means not rated yet. Stay reviews carry only a Jalali month, not a date.

## FAQ

<details>
<summary><b>Can it book a villa or buy a ticket for me?</b></summary>

No, and that's deliberate. It has no login and never calls booking, order, payment, seat-hold or wallet endpoints.
`jb_price_quote` only asks for a price. The agent finds the best option; you book on the site.
</details>

<details>
<summary><b>A search with dates returns 0 results</b></summary>

Usually nothing is free for those dates with those filters, or the dates are past the ~76-day calendar. Try other
dates or fewer filters. Dates must be Gregorian; the server rejects Jalali-looking dates instead of searching.
</details>

<details>
<summary><b>Do I need an Iranian IP?</b></summary>

No geo block was seen: direct calls and calls through a proxy in Turkey both worked (2026-10-04). Cloud servers
were not tested; if Jabama blocks one, set `JABAMA_MCP_PROXY`.
</details>

<details>
<summary><b>I get "blocked the request (HTTP 403)"</b></summary>

Jabama's web firewall rejects requests that don't look like a browser; the server already sends a browser
User-Agent. If it still happens, wait a minute, turn off a VPN, or set `JABAMA_MCP_PROXY`. Normal system proxy
variables are ignored on purpose, because direct calls are the fastest.
</details>

<details>
<summary><b>The theater seat map says "not available right now"</b></summary>

The theater seat map comes live from Jabama's ticketing partner, which sometimes refuses for a while. The showtimes and
`seats_left` still work; try the seat map again later or for another showtime.
</details>

<details>
<summary><b>Claude Desktop says <code>uvx</code> is not found</b></summary>

Use the full path to `uvx` (`where uvx` on Windows, `which uvx` on macOS/Linux) as `command`.
</details>

<details>
<summary><b>How do I debug what the agent sees?</b></summary>

```bash
npx @modelcontextprotocol/inspector uvx jabama-mcp
```
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `JABAMA_MCP_PROXY` | unset | HTTP proxy for every request, e.g. `http://user:pass@host:port` |

## فارسی

<div dir="rtl">

**jabama-mcp** به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد در جاباما ویلا، سوئیت و
اقامتگاه بوم&zwnj;گردی پیدا کند، قیمت دقیق را برای تاریخ و تعداد نفرات شما بگیرد، قوانین و نظرات را بخواند و تورها،
رویدادها و صندلی&zwnj;های خالی تئاتر را هم پیدا کند.

- فقط خواندنی است: وارد حساب نمی&zwnj;شود، رزرو ثبت نمی&zwnj;کند، صندلی نگه نمی&zwnj;دارد و پرداخت نمی&zwnj;کند.
- قیمت هر شب، هزینه نفر اضافه، تخفیف اقامت طولانی و قوانین کنسلی را نشان می&zwnj;دهد.
- همه قیمت&zwnj;ها به تومان است.
- روی سیستم خود شما اجرا می&zwnj;شود و به هیچ سرور واسطی داده نمی&zwnj;فرستد.

**نصب در Claude Code:**

</div>

```bash
claude mcp add jabama -- uvx jabama-mcp
```

<div dir="rtl">

بعد بپرسید: «ارزان&zwnj;ترین ویلای استخردار رامسر برای ۶ نفر از ۲۸ مهر تا ۱ آبان، با قیمت نهایی»

</div>

## Development

```bash
git clone https://github.com/sepehr071/jabama-mcp && cd jabama-mcp
uv sync
uv run pytest            # offline, against recorded responses
uv run pytest -m live    # real APIs
uv run ruff check .
```

Tools live in `src/jabama_mcp/search.py`, `stay.py`, `info.py`, `tours.py` and `events.py`; each is a typed async
function with a docstring that tells the agent when to use it. Issues and PRs are welcome, especially new tools and
fixes for API changes.

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag. GitHub Actions tests,
publishes to PyPI and the [MCP Registry](https://registry.modelcontextprotocol.io), and creates the GitHub Release.

## Disclaimer

Unofficial and not affiliated with or endorsed by Jabama. It uses the public endpoints of the jabama.com,
jabama.tours and jabama.events websites, which can change without notice. Please keep request rates reasonable.

## License

[MIT](https://github.com/sepehr071/jabama-mcp/blob/main/LICENSE)
