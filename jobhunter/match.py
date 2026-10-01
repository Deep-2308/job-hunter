"""Score jobs against resume.md.

Scoring is deliberately simple and explainable:
- Skill keyword hits in title/description = weighted matches.
- Skill keyword in title counts 3x; in description counts 1x.
- Role keyword hits in title = bonus.
- Location match = bonus.
- Exclusion keywords (e.g. "5+ years") = hard 0.

Score is clamped to [0, 100]. No magic; every point is inspectable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .schema import Job


@dataclass
class ResumeSignals:
    """Extracted resume content for matching."""

    raw_text: str
    skills: set[str]
    role_signals: set[str]
    location: set[str]


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9+#.]", "", s.lower()).strip()


def load_resume_signals(resume_md_path: Path) -> ResumeSignals:
    """Parse resume.md into a structured signal set."""
    text = resume_md_path.read_text(encoding="utf-8")

    # Extract a Skills block (lines under a "## Skills" heading until next "## ")
    skills: set[str] = set()
    skills_block_match = re.search(r"^##\s+Skills\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if skills_block_match:
        block = skills_block_match.group(1)
        # Split on commas, dashes, newlines; keep meaningful tokens
        for tok in re.split(r"[,\n;|]", block):
            tok = tok.strip().strip("-").strip()
            if 2 <= len(tok) <= 40 and not tok.lower().startswith(("###", "##", "#")):
                skills.add(tok)

    # Role signals: pull from Summary + role_keywords from config later
    role_signals: set[str] = set()
    summary_match = re.search(r"^##\s+Summary\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if summary_match:
        summary = summary_match.group(1)
        for kw in ["full-stack", "full stack", "backend", "frontend", "ai", "ml",
                   "software engineer", "engineer", "intern", "automation"]:
            if kw in summary.lower():
                role_signals.add(kw)

    # Location
    location: set[str] = set()
    loc_match = re.search(r"Location:\s*(.+)", text, re.IGNORECASE)
    if loc_match:
        for part in loc_match.group(1).split(","):
            p = part.strip().lower()
            if p:
                location.add(p)

    return ResumeSignals(
        raw_text=text,
        skills=skills,
        role_signals=role_signals,
        location=location,
    )


def score_job(job: Job, signals: ResumeSignals, cfg: dict) -> tuple[int, list[str]]:
    """Return (score, reasons). reasons are human-readable explanations."""
    reasons: list[str] = []

    # Hard exclusion check
    exclude_kws = [kw.lower() for kw in cfg.get("exclude_keywords", [])]
    blob = f"{job.title} {job.company} {job.location} {job.description} {job.salary}".lower()
    for kw in exclude_kws:
        if kw in blob:
            return 0, [f"EXCLUDED: matches '{kw}'"]

    score = 0

    # Skill matching
    skill_kws = [kw.lower() for kw in cfg.get("skill_keywords", [])]
    title_blob = job.title.lower()
    desc_blob = job.description.lower()

    for sk in skill_kws:
        sk_norm = _normalize(sk)
        if not sk_norm:
            continue
        if sk in title_blob:
            score += 3
            reasons.append(f"title:{sk}")
        elif sk in desc_blob:
            score += 1
            reasons.append(f"desc:{sk}")

    # Skill keywords in the resume itself that appear in the job
    resume_skill_kws = {s.lower() for s in signals.skills if len(s) >= 3}
    for rs in resume_skill_kws:
        rs_norm = _normalize(rs)
        if not rs_norm or rs_norm in {_normalize(k) for k in skill_kws}:
            continue  # already counted
        if rs in title_blob:
            score += 2
            reasons.append(f"resume-skill in title:{rs}")
        elif rs in desc_blob:
            score += 0.5
            reasons.append(f"resume-skill in desc:{rs}")

    # Role keyword bonus
    role_kws = [kw.lower() for kw in cfg.get("role_keywords", [])]
    for rk in role_kws:
        if rk in title_blob:
            score += 2
            reasons.append(f"role:{rk}")
            break

    # Location match
    loc_kws = [loc.lower() for loc in cfg.get("location", {}).get("cities", [])]
    job_loc = job.location.lower()
    for lk in loc_kws:
        if lk and lk in job_loc:
            score += 4
            reasons.append(f"loc:{lk}")
            break

    # Salary string present = small bonus (jobs that list salary are usually real)
    if job.salary and any(ch.isdigit() for ch in job.salary):
        score += 1

    # Cap at 100
    score = min(100, int(score * 10) / 10)
    return score, reasons


def matches_max_days(job: Job, max_days: int) -> bool:
    """Return True if job.posted_at is within max_days. Missing date -> True (keep)."""
    if not job.posted_at or max_days <= 0:
        return True
    # We don't parse ISO strictly; a simple string heuristic is enough.
    # If it looks like "YYYY-MM-DD" or "Xd ago" style:
    if re.match(r"^\d{4}-\d{2}-\d{2}", job.posted_at):
        try:
            from datetime import datetime
            d = datetime.fromisoformat(job.posted_at[:10])
            return (datetime.utcnow() - d).days <= max_days
        except Exception:
            return True
    m = re.search(r"(\d+)\s*(day|week|month)", job.posted_at.lower())
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n if unit == "day" else n * 7 if unit == "week" else n * 30
        return days <= max_days
    return True
