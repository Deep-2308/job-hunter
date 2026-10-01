"""Naukri source - DISABLED (JS-rendered, requires real browser).

As of Sep 2026, https://www.naukri.com/search?keyword=... returns 200
but the actual job cards are rendered client-side by JavaScript.
The initial HTML contains navigation chrome but zero job listings.
Standard requests + BeautifulSoup sees 0 cards.

To fix this we would need Playwright/Selenium to render the page, which
adds ~50MB of dependencies and makes each run 20-60s slower. Not
worth it for now. Naukri also has anti-scraping defenses that will
eventually block Playwright too.

If you want Naukri coverage, options are:
1. Apply for an Indeed Partner API key (free for individuals) and use
   their job-search API instead - clean JSON, no scraping.
2. Manually save Naukri search alerts and treat them as a manual source.
3. Write a Playwright-based fetcher (heavy lift, brittle).

This source is intentionally a no-op stub so the pipeline knows it's
disabled, not broken.
"""

from __future__ import annotations

import logging

from ..schema import Job

log = logging.getLogger(__name__)


def fetch(query: str, location: str = "", page: int = 1, limit: int = 50, timeout: int = 25) -> list[Job]:
    """Disabled source. Returns [] always."""
    log.info("naukri: disabled (JS-rendered; needs Playwright, not installed). Skipping.")
    return []
