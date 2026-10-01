"""Common data models and config loader."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class Job:
    """Canonical job posting shape shared across all sources."""

    source: str                     # "google_for_jobs", "naukri", "linkedin"
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    salary: str = ""
    posted_at: Optional[str] = None # ISO date string or None
    apply_email: Optional[str] = None
    apply_url: Optional[str] = None  # direct application form URL if exposed
    raw: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable dedupe key (title+company+source, case-insensitive)."""
        return f"{self.source}::{self.company.strip().lower()}::{self.title.strip().lower()}"

    def short(self) -> str:
        return f"[{self.source}] {self.title} @ {self.company} ({self.location})"


def load_config(project_root: Path) -> dict:
    """Load config.yaml from project root."""
    cfg_path = project_root / "config.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.yaml not found at {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_env(project_root: Path) -> dict:
    """Load .env into os.environ if present. No-op if absent.

    Minimal parser: KEY=VALUE lines, # comments, no quoting rules.
    """
    env_path = project_root / ".env"
    if not env_path.exists():
        return {}
    env = {}
    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            env[k] = v
            os.environ.setdefault(k, v)
    return env


def get_secret(name: str) -> Optional[str]:
    return os.environ.get(name) or None
