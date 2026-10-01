"""Email-based job submission via himalaya CLI.

Design:
- Calls `himalaya message send` (or `himalaya template send`) via subprocess,
  using the user's existing himalaya config at ~/.config/himalaya/config.toml.
- No duplicated credentials: himalaya reads its own App Password file.
- Builds the message in MML (MIME Meta Language) for rich content:
  plain-text + HTML body (alternative), plus resume.pdf as a mixed attachment.
- Returns True on success; raises on any failure. Caller decides retry policy.

Why himalaya instead of smtplib:
- Single source of truth for credentials (himalaya config).
- Already installed and verified against the user's Gmail.
- MML gives us proper MIME structure without re-implementing MIMEMultipart.
- Himalaya is the user's chosen email tool; using it here keeps the agent
  consistent with how the user reads/writes email manually.

What this does NOT do:
- No retry on hard failures (550/553). Log and move on.
- No retry in a tight loop. The dedupe table is the audit trail.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Default himalaya binary location. Overridable via HIMALAYA_BIN env var.
DEFAULT_HIMALAYA_BIN = str(Path.home() / ".hermes" / "bin" / "himalaya.exe")


class HimalayaSender:
    """Send a job application email via the himalaya CLI."""

    def __init__(
        self,
        sender_email: str,
        resume_pdf: Path | None = None,
        himalaya_bin: str | None = None,
        timeout: int = 60,
    ):
        if not sender_email:
            raise ValueError("sender_email is required")
        if resume_pdf and not resume_pdf.exists():
            raise FileNotFoundError(f"resume_pdf not found: {resume_pdf}")
        self.sender_email = sender_email
        self.resume_pdf = resume_pdf
        self.himalaya_bin = himalaya_bin or os.environ.get("HIMALAYA_BIN") or DEFAULT_HIMALAYA_BIN
        self.timeout = timeout
        if not Path(self.himalaya_bin).exists():
            # Fall back to PATH lookup
            found = shutil.which("himalaya")
            if found:
                self.himalaya_bin = found
            else:
                raise FileNotFoundError(
                    f"himalaya binary not found at {self.himalaya_bin} or on PATH"
                )

    def _build_mml(self, to_email: str, subject: str, cover_letter: str,
                   job_url: str, company: str) -> str:
        """Build an MML message: plain+html alternative body, resume PDF attachment.

        MML structure:
          <#multipart type=mixed>
            <#multipart type=alternative>
              plain text body
              <#part type=text/html> HTML body <#/part>
            <#/multipart>
            <#part filename=/path/to/resume.pdf name=resume.pdf><#/part>
          <#/multipart>
        """
        plain_body = cover_letter.strip() + f"\n\n---\nApply via: {job_url}"
        html_body = _cover_letter_to_html(cover_letter, job_url, company)

        mml = f"""From: {self.sender_email}
To: {to_email}
Subject: {subject}
Reply-To: {self.sender_email}

<#multipart type=mixed>
<#multipart type=alternative>
{plain_body}
<#part type=text/html>
{html_body}
<#/part>
<#/multipart>
"""
        if self.resume_pdf and self.resume_pdf.exists():
            mml += (
                f"<#part filename={self.resume_pdf} name={self.resume_pdf.name}><#/part>\n"
            )
        mml += "<#/multipart>"
        return mml

    def send(
        self,
        to_email: str,
        subject: str,
        cover_letter: str,
        job_url: str,
        company: str,
    ) -> bool:
        """Send one application email via himalaya. Returns True on success.

        Raises on subprocess failure; the caller decides how to handle it.
        """
        mml = self._build_mml(to_email, subject, cover_letter, job_url, company)

        try:
            result = subprocess.run(
                [self.himalaya_bin, "message", "send"],
                input=mml,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"himalaya send timed out after {self.timeout}s") from e
        except Exception as e:
            raise RuntimeError(f"himalaya invocation failed: {e}") from e

        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"himalaya send failed (rc={result.returncode}): {err}")

        return True


def _cover_letter_to_html(cover_letter: str, job_url: str, company: str) -> str:
    """Very light markdown-ish -> HTML for readability in Gmail clients."""
    paras = [p.strip() for p in cover_letter.strip().split("\n\n") if p.strip()]
    body = "\n".join(f"<p>{_escape_html(p).replace(chr(10), '<br>')}</p>" for p in paras)
    return f"""<html><body><div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.5;">
{body}
<hr style="border: 0; border-top: 1px solid #ddd; margin: 20px 0;">
<p style="color: #666; font-size: 12px;">
Applying for the <strong>{_escape_html(company)}</strong> role.<br>
Reference: <a href="{_escape_html(job_url)}">{_escape_html(job_url[:80])}</a>
</p>
</div></body></html>"""


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def from_env(resume_pdf: Path | None, himalaya_bin: str | None = None) -> HimalayaSender | None:
    """Build a sender from env vars, or None if not configured."""
    email = os.environ.get("SMTP_EMAIL")
    if not email:
        return None
    try:
        return HimalayaSender(sender_email=email, resume_pdf=resume_pdf, himalaya_bin=himalaya_bin)
    except FileNotFoundError as e:
        log.warning("himalaya not available, falling back to draft-only: %s", e)
        return None


# Backwards-compatible alias (older callers may import EmailSender)
EmailSender = HimalayaSender
