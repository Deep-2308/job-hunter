"""Pipeline orchestrator.

Flow:
1. Load config + resume signals + history DB.
2. For each enabled source: fetch candidate jobs.
3. Normalize, dedupe against history.
4. Filter by max_days_since_posted.
5. Score each job; keep those >= min_score_to_draft.
6. Rank; keep top max_matches_per_run.
7. For each top match:
   - Draft cover letter to out/cover_letters/<slug>.txt
   - If apply_email present AND score >= min_score_to_send AND email.auto_send
     AND we haven't hit the daily send cap:
       - Send email, mark 'sent'
   - Else: mark 'draft'
8. Write out/daily.md with today's summary.
"""

from __future__ import annotations

import logging
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import dedupe
from .draft import generate_cover_letter
from .match import ResumeSignals, load_resume_signals, matches_max_days, score_job
from .schema import Job, load_config
from .sources import SOURCES
from .submit.email import EmailSender
from .submit.form_fill import prepare_manual_submit

log = logging.getLogger(__name__)


def _slugify(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:maxlen] or "job"


def _run_query_terms(cfg: dict) -> list[str]:
    """Combine role_keywords + location into search queries."""
    role_terms = cfg.get("role_keywords", [])
    if not role_terms:
        return ["Software Engineer"]
    # Pick the top 3 role keywords for broad coverage
    return role_terms[:3]


def _run_location_variants(cfg: dict) -> list[str]:
    """Build a list of location query variants for LinkedIn/Naukri.

    LinkedIn defaults to US/Worldwide when no location is given. To get
    India results we must pass location explicitly. Kept to a small set
    (home city + India + Remote) because LinkedIn rate-limits aggressively
    and each query takes ~2-3s.
    """
    cities = cfg.get("location", {}).get("cities", [])
    seen: set[str] = set()
    ordered: list[str] = []
    # Priority order: home city first, then country-level, then remote.
    # Keep it to at most 3 queries to stay well under LinkedIn's rate limit.
    for c in cities:
        key = c.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(c.strip())
        if len(ordered) >= 3:
            break
    # Always include "India" and "Remote" if not already present
    if "india" not in seen:
        ordered.append("India")
    if "remote" not in seen:
        ordered.append("Remote")
    return ordered


def run(
    project_root: Path,
    force: bool = False,
    verbose: bool = True,
) -> dict:
    """Execute one full pipeline run. Returns a summary dict."""
    cfg = load_config(project_root)

    # Resume
    resume_md = project_root / "resume.md"
    if not resume_md.exists():
        raise FileNotFoundError(f"resume.md not found at {resume_md}")
    signals = load_resume_signals(resume_md)

    # History
    db_path = project_root / cfg.get("history_db", "history.db")
    conn = dedupe.connect(db_path)

    # Resume PDF for attachments
    resume_pdf_rel = cfg.get("email", {}).get("resume_pdf", "resume.pdf")
    resume_pdf = project_root / resume_pdf_rel

    # Email sender (optional)
    email_sender: EmailSender | None = None
    if cfg.get("email", {}).get("auto_send", True):
        from .submit.email import from_env
        email_sender = from_env(resume_pdf if resume_pdf.exists() else None)
        if email_sender is None:
            log.warning("email.auto_send=true but SMTP_EMAIL / SMTP_APP_PASSWORD not set. Drafting only.")

    # Output dirs
    cover_letters_dir = project_root / cfg.get("output", {}).get("cover_letters_dir", "out/cover_letters")
    cover_letters_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = project_root / cfg.get("output", {}).get("runs_log_dir", "out/runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    daily_md = project_root / cfg.get("output", {}).get("daily_report", "out/daily.md")
    daily_md.parent.mkdir(parents=True, exist_ok=True)

    # Collect jobs from enabled sources. Run multiple location-specific
    # queries so LinkedIn doesn't default to US-only results. We merge
    # results across queries and dedupe by job.key.
    sources_enabled = cfg.get("sources", {})
    queries = _run_query_terms(cfg)
    location_variants = _run_location_variants(cfg)
    max_per_source = cfg.get("matching", {}).get("top_n_per_source", 50)
    max_days = cfg.get("matching", {}).get("max_days_since_posted", 14)

    all_jobs: list[Job] = []
    seen_keys: set[str] = set()
    source_stats: list[dict] = []

    for name, mod in SOURCES.items():
        if not sources_enabled.get(name, False):
            continue
        source_total = 0
        source_error: str | None = None
        for role_q in queries:
            for loc_q in location_variants:
                try:
                    jobs = mod.fetch(role_q, location=loc_q, limit=max_per_source)
                except Exception as e:
                    log.error("source=%s role=%r loc=%r failed: %s", name, role_q, loc_q, e)
                    source_error = str(e)
                    continue
                for j in jobs:
                    if j.key in seen_keys:
                        continue
                    seen_keys.add(j.key)
                    all_jobs.append(j)
                    source_total += 1
        source_stats.append({"source": name, "count": source_total, "error": source_error})
        log.info("source=%s fetched=%d", name, source_total)

    # Dedupe
    fresh_jobs: list[Job] = []
    for j in all_jobs:
        if dedupe.has_applied(conn, j.key):
            continue
        if not matches_max_days(j, max_days):
            continue
        fresh_jobs.append(j)

    # Score
    scored: list[tuple[int, list[str], Job]] = []
    min_draft = cfg.get("matching", {}).get("min_score_to_draft", 35)
    for j in fresh_jobs:
        score, reasons = score_job(j, signals, cfg)
        if score >= min_draft:
            scored.append((score, reasons, j))

    scored.sort(key=lambda x: -x[0])
    top_n = cfg.get("matching", {}).get("max_matches_per_run", 15)
    top = scored[:top_n]

    # Process each top match
    sent_count = 0
    draft_count = 0
    daily_entries_sent: list[dict] = []
    daily_entries_draft: list[dict] = []
    min_send = cfg.get("matching", {}).get("min_score_to_send", 60)
    max_send_per_run = cfg.get("email", {}).get("max_send_per_run", 5)
    max_send_per_company = cfg.get("email", {}).get("max_send_per_company", 2)
    delay_s = cfg.get("email", {}).get("delay_seconds_between_sends", 20)

    for score, reasons, job in top:
        # Draft cover letter
        try:
            letter = generate_cover_letter(job, signals, resume_md, cfg)
        except Exception as e:
            log.error("draft failed for %s: %s", job.key, e)
            letter = ""

        slug = _slugify(f"{job.company}-{job.title}")
        letter_path = cover_letters_dir / f"{slug}.txt"
        letter_path.write_text(letter, encoding="utf-8")

        # Decide: send or draft?
        status = "draft"
        notes = ""
        if (
            email_sender is not None
            and job.apply_email
            and score >= min_send
            and sent_count < max_send_per_run
            and dedupe.count_sent_for_company(conn, job.company) < max_send_per_company
        ):
            try:
                subject = f"Application - {job.title} - Deep Kabariya"
                email_sender.send(
                    to_email=job.apply_email,
                    subject=subject,
                    cover_letter=letter,
                    job_url=job.url,
                    company=job.company,
                )
                status = "sent"
                sent_count += 1
                notes = f"sent to {job.apply_email}"
                time.sleep(delay_s)
            except Exception as e:
                status = "error"
                notes = f"send failed: {e}"
                log.error("send failed for %s: %s", job.key, e)

        dedupe.record(conn, job, score, status, notes)

        entry = prepare_manual_submit(job, letter_path)
        entry["score"] = score
        entry["status"] = status
        entry["notes"] = notes
        entry["reasons"] = reasons[:8]

        if status == "sent":
            daily_entries_sent.append(entry)
        else:
            daily_entries_draft.append(entry)
            draft_count += 1

    # Write daily report
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _write_daily_md(daily_md, ts, source_stats, len(all_jobs), len(fresh_jobs), top, daily_entries_sent, daily_entries_draft)

    # Write run log
    log_path = runs_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.log"
    with log_path.open("w", encoding="utf-8") as f:
        f.write(f"run_started: {ts}\n")
        f.write(f"queries: {queries}\n")
        for s in source_stats:
            f.write(f"source {s['source']}: count={s['count']} error={s['error']}\n")
        f.write(f"total_fetched={len(all_jobs)} fresh={len(fresh_jobs)} top={len(top)} sent={sent_count} draft={draft_count}\n")
        for score, reasons, job in top:
            f.write(f"  score={score} job={job.key} reasons={reasons[:8]}\n")

    conn.close()

    summary = {
        "timestamp": ts,
        "source_stats": source_stats,
        "total_fetched": len(all_jobs),
        "fresh_after_dedupe": len(fresh_jobs),
        "top_matches": len(top),
        "sent": sent_count,
        "draft": draft_count,
        "daily_md": str(daily_md),
        "run_log": str(log_path),
    }
    if verbose:
        print("\n=== job-hunter run complete ===")
        for s in source_stats:
            print(f"  {s['source']:20s} fetched={s['count']:3d}  error={s['error'] or '-'}")
        print(f"  total_fetched={len(all_jobs)}  fresh={len(fresh_jobs)}  top={len(top)}")
        print(f"  sent={sent_count}  draft={draft_count}")
        print(f"  daily report: {daily_md}")
        print(f"  run log:      {log_path}")

    return summary


def _write_daily_md(
    path: Path,
    ts: str,
    source_stats: list[dict],
    total_fetched: int,
    fresh: int,
    top: list[tuple[int, list[str], Job]],
    entries_sent: list[dict],
    entries_draft: list[dict],
) -> None:
    lines: list[str] = []
    lines.append(f"# job-hunter daily report — {ts}")
    lines.append("")
    source_summary = ", ".join(f"{s['source']}={s['count']}" for s in source_stats)
    lines.append(f"Sources: {source_summary}")
    lines.append(f"Total fetched: {total_fetched}  Fresh after dedupe: {fresh}  Top matches: {len(top)}")
    lines.append(f"Auto-sent via email: {len(entries_sent)}  Waiting for manual submit: {len(entries_draft)}")
    lines.append("")

    if entries_sent:
        lines.append("## AUTO-SENT VIA EMAIL")
        lines.append("")
        for e in entries_sent:
            lines.append(f"- **{e['title']}** @ {e['company']} (score {e['score']})")
            lines.append(f"  - To: {e.get('notes', '')}")
            lines.append(f"  - Apply: {e['apply_url']}")
            lines.append(f"  - Letter: `{e['cover_letter_path']}`")
        lines.append("")

    if entries_draft:
        lines.append("## READY FOR MANUAL SUBMIT")
        lines.append("")
        for e in entries_draft:
            lines.append(f"- **{e['title']}** @ {e['company']} — {e['location']}  (score {e['score']})")
            lines.append(f"  - Apply: {e['apply_url']}")
            lines.append(f"  - Letter: `{e['cover_letter_path']}`")
            lines.append(f"  - Source: {e['source']} | Status: {e['status']}")
            if e['notes']:
                lines.append(f"  - Note: {e['notes']}")
            if e.get('reasons'):
                lines.append(f"  - Match reasons: {', '.join(e['reasons'][:6])}")
            lines.append(f"  - How to submit: {e['instructions']}")
        lines.append("")

    lines.append("## LEGEND")
    lines.append("- **Auto-sent**: email sent to the hiring address exposed on the job listing. Confirm it landed in your Sent folder.")
    lines.append("- **Ready for manual submit**: cover letter drafted, apply URL captured. Open the link, paste the letter, upload `resume.pdf`, click submit.")
    lines.append("- Source scrape errors are logged in `out/runs/*.log`.")

    path.write_text("\n".join(lines), encoding="utf-8")
