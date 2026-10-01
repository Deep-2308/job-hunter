"""Source registry. Each module exposes `fetch(...) -> list[Job]`."""

from . import google_for_jobs, linkedin, naukri

SOURCES = {
    "google_for_jobs": google_for_jobs,
    "naukri": naukri,
    "linkedin": linkedin,
}
