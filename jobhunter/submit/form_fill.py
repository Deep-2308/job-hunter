"""Form-fill submission layer (draft-only).

By design, this layer does NOT click the final Submit button. It prepares
the job as a "ready for manual submit" item so Deep reviews and clicks
submit himself. This avoids ToS violations and account bans on LinkedIn
and Naukri.

Two paths:
1. URL-only (default, safe): just record the job URL and instructions.
   Deep opens the link himself and hits apply. This is what runs today.
2. Browser-automation fill (opt-in via config): use Playwright to open
   the job page and pre-fill the form fields. The final submit is still
   manual. Requires `pip install playwright && playwright install chromium`
   and is disabled by default because it's slow and brittle.

This module currently implements path 1 only, cleanly. Path 2 is stubbed
in `form_fill_optin()` for future work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..schema import Job

log = logging.getLogger(__name__)


def prepare_manual_submit(job: Job, cover_letter_path: Path) -> dict:
    """Return a structured record for a job that needs manual submit."""
    return {
        "source": job.source,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "apply_url": job.apply_url or job.url,
        "cover_letter_path": str(cover_letter_path),
        "instructions": _instructions_for(job.source),
    }


def _instructions_for(source: str) -> str:
    hints = {
        "linkedin": "Open link, click 'Easy Apply' or 'Apply', paste cover letter, upload resume.pdf, submit.",
        "naukri": "Open link, click 'Apply Now', paste cover letter in the message box, upload resume.pdf, submit.",
        "google_for_jobs": "Open link, follow the posted apply link to the actual company careers page, apply manually.",
        "default": "Open link and apply manually. Paste the cover letter, upload resume.pdf.",
    }
    return hints.get(source, hints["default"])


def form_fill_optin(job: Job, cover_letter: str, resume_pdf: Path, cfg: dict) -> Optional[Path]:
    """Stub for Playwright-based pre-fill. Returns None if not enabled."""
    return None
