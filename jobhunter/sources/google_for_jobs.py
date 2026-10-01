"""Google for Jobs source - DISABLED.

Google discontinued their public Jobs frontend endpoint in 2024.
- jobs.google.com now returns 404 for query URLs.
- google.com/search?...&tbm=jobgt returns 400 to non-browser clients.

If/when Google exposes a real public API again, replace this stub
with that implementation. For now this source is intentionally a no-op
so the pipeline knows it's disabled, not broken.

Recommended primary sources: Naukri + LinkedIn.
"""

from __future__ import annotations

import logging

from ..schema import Job

log = logging.getLogger(__name__)


def fetch(query: str, location: str = "", page: int = 1, limit: int = 50, timeout: int = 20) -> list[Job]:
    """Disabled source. Returns [] always."""
    log.info("google_for_jobs: disabled (Google ended public endpoint in 2024). Skipping.")
    return []
