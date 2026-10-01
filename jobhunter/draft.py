"""Cover letter generation.

Provider-agnostic: Gemini or Groq, picked from config + first non-empty env key.
Falls back to a deterministic template if no LLM key is configured.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import requests

from .match import ResumeSignals
from .schema import Job

log = logging.getLogger(__name__)


# --- Template fallback (no LLM key) ---

FALLBACK_TEMPLATE = """Dear Hiring Manager,

I'm writing to apply for the {title} role at {company}. I'm a Computer Engineering undergraduate (B.E., GTU / SSASIT) with hands-on experience in full-stack development, AI/ML, and automation.

Recent work I've shipped that aligns with this role:
{projects}

I'm based in Surat, Gujarat and open to remote roles globally. I'd welcome the chance to discuss how my background in {skills_summary} maps to the {company} team.

Thanks for considering my application.

Best,
Deep Kabariya
GitHub: https://github.com/Deep-2308
"""


def _extract_projects(resume_md_path: Path, n: int = 3) -> list[str]:
    """Pull project headings + stack lines from resume.md."""
    text = resume_md_path.read_text(encoding="utf-8")
    # Match "### <Name> — <desc>" followed by lines starting with "- Stack:"
    projects: list[str] = []
    for m in re.finditer(r"^###\s+(.+?)\s*[—\-]\s*(.+?)\s*$", text, re.MULTILINE):
        name = m.group(1).strip()
        desc = m.group(2).strip()
        projects.append(f"- **{name}**: {desc}")
        if len(projects) >= n:
            break
    return projects[:n]


def _skills_summary(signals: ResumeSignals, n: int = 5) -> str:
    """Return a comma-joined list of top resume skills."""
    return ", ".join(sorted(signals.skills)[:n])


def _build_prompt(job: Job, signals: ResumeSignals, resume_md_path: Path, cfg: dict) -> str:
    projects = _extract_projects(resume_md_path, n=3)
    project_block = "\n".join(projects) if projects else "- (no projects listed)"
    skills = _skills_summary(signals)
    tone = cfg.get("drafting", {}).get("tone", "professional")

    return f"""You are a professional career coach writing a cover letter.

Job posting:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Description: {job.description[:2000]}

Applicant:
- Name: Deep Kabariya
- Location: Surat, Gujarat, India
- Role: Computer Engineering undergrad, B.E. (GTU / SSASIT)
- Top skills: {skills}
- Relevant projects:
{project_block}

Write a cover letter that is:
- Tone: {tone}
- Length: 150-220 words
- References 2-3 specific projects from the list above that map to skills in the job description
- Concrete: no fluff, no "I'm passionate", no "I would be an asset"
- Ends with a call to action and sign-off as Deep Kabariya
- No email, no phone, no fake URL at the end
- Return ONLY the cover letter text, no headers, no commentary."""


def _call_gemini(prompt: str, model: str, api_key: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 600},
    }
    r = requests.post(url, json=payload, headers=headers, timeout=45)
    r.raise_for_status()
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"gemini: unexpected response shape: {e}")


def _call_groq(prompt: str, model: str, api_key: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
        "max_tokens": 700,
    }
    r = requests.post(url, json=payload, headers=headers, timeout=45)
    r.raise_for_status()
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"groq: unexpected response shape: {e}")


def generate_cover_letter(
    job: Job,
    signals: ResumeSignals,
    resume_md_path: Path,
    cfg: dict,
) -> str:
    """Generate a tailored cover letter. Falls back to template if no LLM key."""
    provider = (cfg.get("drafting", {}).get("provider") or "gemini").lower()
    models = cfg.get("drafting", {}).get("model", {})

    # Try Gemini first if configured, else Groq, else template
    gemini_key = os.environ.get("GEMINI_API_KEY") or None
    groq_key = os.environ.get("GROQ_API_KEY") or None

    if provider == "gemini" and gemini_key:
        try:
            return _call_gemini(
                _build_prompt(job, signals, resume_md_path, cfg),
                models.get("gemini", "gemini-2.0-flash"),
                gemini_key,
            )
        except Exception as e:
            log.warning("gemini failed, falling back: %s", e)

    if groq_key:
        try:
            return _call_groq(
                _build_prompt(job, signals, resume_md_path, cfg),
                models.get("groq", "llama-3.3-70b-versatile"),
                groq_key,
            )
        except Exception as e:
            log.warning("groq failed, falling back: %s", e)

    # Template fallback
    projects = _extract_projects(resume_md_path, n=3)
    return FALLBACK_TEMPLATE.format(
        title=job.title,
        company=job.company,
        projects="\n".join(projects) if projects else "- (see resume)",
        skills_summary=_skills_summary(signals),
    )
