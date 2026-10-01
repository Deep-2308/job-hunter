# job-hunter

Automated job search + tailoring + (safe) application pipeline.

## What this is

A daily-run agent that:
1. Fetches fresh job postings from Google for Jobs API (free, no auth), Naukri search HTML, and LinkedIn search HTML.
2. Scores each posting against your resume (`resume.md`) on skill/keyword overlap.
3. Keeps the top matches per run.
4. Generates a tailored cover letter per job using Gemini or Groq.
5. Auto-submits to jobs that expose a direct email address (Gmail + App Password, SMTP).
6. Prepares form-based jobs (LinkedIn Easy Apply, Naukri, ATS) as draft-only "manual submit" items in `out/daily.md` so you click submit yourself.

## What this is NOT

- No LinkedIn/Naukri auto-submit. Their ToS bans it. Draft-only keeps you safe.
- No "any platform any means" claim. Each ATS has its own form schema; draft-only covers them all.

## Quick start

```powershell
cd C:\Users\01\Projects\job-hunter
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

1. **Enable 2FA on Gmail** (required for App Passwords): https://myaccount.google.com/security
2. **Generate an App Password**: https://myaccount.google.com/apppasswords -> label it `job-hunter`.
3. Copy `.env.example` to `.env` and fill:
   ```
   SMTP_EMAIL=deepkabariya2308@gmail.com
   SMTP_APP_PASSWORD=xxxx xxxx xxxx xxxx
   GEMINI_API_KEY=      # optional; use Groq if empty
   GROQ_API_KEY=        # optional
   ```
4. Copy `resume.pdf` next to `resume.md` in the project root. That PDF gets attached to auto-sent emails.
5. Run once:
   ```powershell
   python -m jobhunter run --once
   ```
6. Schedule daily at 07:00 IST (uses Windows Task Scheduler):
   ```powershell
   schtasks /Create /SC DAILY /ST 07:00 /TN "job-hunter" /TR "C:\Users\01\Projects\job-hunter\.venv\Scripts\python.exe -m jobhunter run --once"
   ```

## Daily run

1. Source fetchers (each degrades gracefully on failure):
   - `google_for_jobs.py` — public API, no key, no ToS risk. Primary source.
   - `naukri.py` — HTML scrape of naukri.com search. Fragile.
   - `linkedin.py` — HTML scrape of linkedin.com/jobs. Fragile.
2. Normalize to a common `Job` schema (`jobhunter/schema.py`).
3. Filter by `config.yaml`: location, keywords, max_days_since_posted.
4. Score 0-100 against `resume.md` keywords + skills (`jobhunter/match.py`).
5. Dedupe against SQLite `history.db` so the same job doesn't get re-applied.
6. Draft cover letter to `out/cover_letters/<slug>.txt`.
7. If the job exposes a direct email and score >= threshold:
   - Send via SMTP (Gmail App Password) with `resume.pdf` attached.
   - Log status `sent` in `history.db`.
8. Else:
   - Log status `draft` in `history.db`.
   - Add the job to today's `out/daily.md` under `READY FOR MANUAL SUBMIT`.

## Output files

- `out/daily.md` — today's run summary: what was sent, what's waiting for manual submit.
- `out/cover_letters/<job-slug>.txt` — per-job cover letter.
- `out/runs/<timestamp>.log` — full run log including source errors.
- `history.db` — SQLite, dedupe + audit trail.

## Config (`config.yaml`)

Key fields: `location`, `keywords`, `exclude_keywords`, `min_score_to_send`, `min_score_to_draft`, `max_matches_per_run`, `sources_enabled`, `email.auto_send`, `drafting.provider`.

See `config.yaml` for defaults.

## Honest limitations

- Google for Jobs API does not reliably expose `apply_email` fields. Expect 10-20% of matches to be email-auto-submit eligible.
- Naukri and LinkedIn HTML scraping break every few weeks when their markup changes. When that happens the agent logs the error and continues with the other sources.
- Draft-only is by design. The whole point is to be ready to submit in one click, not to submit without you.
- Resume is stored as Markdown (`resume.md`) plus a PDF (`resume.pdf`). Keep them in sync.

## Extending

- **Add a source**: create `jobhunter/sources/<name>.py` implementing `fetch(query, page) -> list[Job]`. Register it in `pipeline.py`.
- **Swap the LLM provider**: implement `draft.py::generate_cover_letter` against another API (OpenAI, Anthropic, local Ollama).
- **Different email provider**: implement `submit/email.py::send` against Zoho, Outlook, SendGrid.
