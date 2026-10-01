"""LinkedIn Jobs source - HTML scraping of linkedin.com/jobs search.

We scrape the public, unauthenticated search results at
https://www.linkedin.com/jobs/search/?keywords=...
No login, no session cookie required. LinkedIn aggressively rate-limits
and fingerprints scraping; expect occasional 429s and layout changes.

Verified working as of Sep 2026: cards are wrapped in `[data-entity-type]`
containers or `<li>` wrappers; the title lives inside an
`<a href*="/jobs/view/">` element. The old `.job-search-card` and
`.trk-result-card` selectors are stale.

This is source-of-record for LinkedIn, but if it 429s the pipeline just
logs the error and continues. Do NOT log in here; keep LinkedIn login
in a separate session for your own use.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, Tag

from ..schema import Job

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
)


def _abs_url(href: str) -> str:
    if href and href.startswith("/"):
        return "https://www.linkedin.com" + href
    return href


def _strip_linkedin_query(url: str) -> str:
    """Strip LinkedIn's tracking query params so duplicate jobs dedupe.

    LinkedIn URLs look like:
      /jobs/view/slug-12345?position=2&pageNum=0&refId=...&trackingId=...
    The job identity is the path up to `?`. Strip everything after.
    """
    if not url:
        return url
    if "?" in url:
        return url.split("?", 1)[0]
    return url


def _card_root(tag: Tag) -> Tag | None:
    """Walk up from a title anchor to find the enclosing job card container."""
    node = tag.parent
    for _ in range(10):
        if node is None:
            return None
        classes = node.get("class") or []
        attrs = node.attrs or {}
        if any(c in classes for c in ("base-card", "job-search-card", "trk-result-card")):
            return node
        if attrs.get("data-entity-type", "").endswith("posting"):
            return node
        if attrs.get("data-occluder") in ("job-search-card", "base-card"):
            return node
        if attrs.get("data-view-name") == "job-search-card":
            return node
        node = node.parent
    return None


def _parse_company(card: Tag) -> str:
    for sel in [
        "a.job-search-card__company-name",
        ".job-search-card__company-name",
        "[data-view-name='job-search-card__company-name']",
    ]:
        el = card.select_one(sel)
        if el:
            return el.get_text(strip=True)
    # Heuristic: find any link pointing to /company/
    for a in card.select("a[href*='/company/']"):
        t = a.get_text(strip=True)
        if t and len(t) < 80:
            return t
    return "Unknown"


def _parse_location(card: Tag) -> str:
    for sel in [
        "span.job-search-card__location",
        ".job-search-card__location",
        "[data-view-name='job-search-card__location']",
    ]:
        el = card.select_one(sel)
        if el:
            return el.get_text(strip=True)
    # LinkedIn public cards often don't expose a location field.
    # Return "Unknown" rather than guessing from arbitrary text.
    return "Unknown"


def _parse_posted(card: Tag) -> str | None:
    el = card.select_one("time.job-search-card__listdate")
    if el:
        return el.get("datetime")
    text = card.get_text(" ", strip=True)
    m = re.search(r"(?:posted\s+)?(\d+)\s*(day|week|hour|minute)s?\s+ago", text, re.IGNORECASE)
    if m:
        return f"{m.group(1)} {m.group(2)}s ago"
    return None


def _parse_salary(card: Tag) -> str:
    for sel in ["span.job-search-card__salary-info", "[data-view-name='job-search-card__salary-info']"]:
        el = card.select_one(sel)
        if el:
            return el.get_text(strip=True)
    return ""


def fetch(
    query: str,
    location: str = "",
    page: int = 1,
    limit: int = 50,
    timeout: int = 25,
) -> list[Job]:
    """Scrape a LinkedIn public jobs search page. Returns [] on any failure.

    Confirmed working URL: https://www.linkedin.com/jobs/search/?keywords=<query>
    """
    params = {"keywords": query}
    if location:
        params["location"] = location
    url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"

    try:
        r = SESSION.get(url, timeout=timeout)
        if r.status_code == 429:
            log.warning("linkedin: rate-limited (429) - backing off this run")
            return []
        if r.status_code == 403:
            log.warning("linkedin: blocked (403)")
            return []
        r.raise_for_status()
    except Exception as e:
        log.warning("linkedin: request failed: %s", e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    jobs: list[Job] = []
    seen_urls: set[str] = set()

    # Strategy: find all title anchors first, then walk up to card containers.
    # LinkedIn appends position/refId/trackingId to job URLs so the same
    # job appears with different full URLs. Normalize by stripping those.
    title_anchors = soup.select('a[href*="/jobs/view/"]')
    for a in title_anchors:
        title = a.get_text(strip=True)
        href = _abs_url(a.get("href", ""))
        if not title:
            continue
        normalized = _strip_linkedin_query(href)
        if normalized in seen_urls:
            continue
        seen_urls.add(normalized)

        card = _card_root(a)
        company = _parse_company(card) if card else "Unknown"
        location_txt = _parse_location(card) if card else "Unknown"
        posted = _parse_posted(card) if card else None
        salary = _parse_salary(card) if card else ""

        jobs.append(
            Job(
                source="linkedin",
                title=title,
                company=company,
                location=location_txt,
                url=href or url,
                apply_url=href or url,
                salary=salary,
                posted_at=posted,
                raw={"search_url": url},
            )
        )
        if len(jobs) >= limit:
            break

    # Politely back off so we don't get IP-banned.
    time.sleep(2)
    return jobs
