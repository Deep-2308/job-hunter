"""SQLite history store for dedupe and audit trail.

Schema:
- applied: job_key PRIMARY KEY, source, title, company, location, url, score,
           status ('sent'|'draft'|'skipped'|'error'), applied_at, notes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .schema import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS applied (
    job_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    url TEXT,
    score INTEGER,
    status TEXT,
    applied_at TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_applied_status ON applied(status);
CREATE INDEX IF NOT EXISTS idx_applied_source ON applied(source);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def has_applied(conn: sqlite3.Connection, job_key: str) -> bool:
    cur = conn.execute("SELECT 1 FROM applied WHERE job_key = ?", (job_key,))
    return cur.fetchone() is not None


def record(
    conn: sqlite3.Connection,
    job: Job,
    score: int,
    status: str,
    notes: str = "",
) -> None:
    row = conn.execute(
        "SELECT 1 FROM applied WHERE job_key = ?", (job.key,)
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if row:
        conn.execute(
            "UPDATE applied SET score=?, status=?, notes=?, applied_at=? WHERE job_key=?",
            (score, status, notes, now, job.key),
        )
    else:
        conn.execute(
            "INSERT INTO applied (job_key, source, title, company, location, url, score, status, applied_at, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job.key, job.source, job.title, job.company, job.location, job.url, score, status, now, notes),
        )
    conn.commit()


def count_sent_for_company(conn: sqlite3.Connection, company: str) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) FROM applied WHERE status = 'sent' AND lower(company) = ?",
        (company.lower(),),
    )
    return cur.fetchone()[0]


def recent_sent_count(conn: sqlite3.Connection, since_hours: int = 24) -> int:
    """Count sends within the last N hours (ISO timestamp comparison)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat(timespec="seconds")
    cur = conn.execute(
        "SELECT COUNT(*) FROM applied WHERE status = 'sent' AND applied_at >= ?",
        (since,),
    )
    return cur.fetchone()[0]
