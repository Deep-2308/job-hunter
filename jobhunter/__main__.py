"""CLI entry point.

Usage:
    python -m jobhunter run --once
    python -m jobhunter run --once --verbose
    python -m jobhunter check                 # validate config + deps
    python -m jobhunter fetch                 # fetch jobs only, no drafting/sending
    python -m jobhunter report                # just re-print today's daily.md
    python -m jobhunter schedule              # install Windows Task Scheduler job
    python -m jobhunter schedule --uninstall  # remove it
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_check(args) -> int:
    """Sanity-check environment before a real run."""
    from .schema import load_config, load_env

    load_env(PROJECT_ROOT)
    cfg = load_config(PROJECT_ROOT)

    print("Config: OK")
    print(f"  project_root: {PROJECT_ROOT}")
    print(f"  resume.md:    {'OK' if (PROJECT_ROOT / 'resume.md').exists() else 'MISSING'}")
    print(f"  resume.pdf:   {'OK' if (PROJECT_ROOT / cfg.get('email', {}).get('resume_pdf', 'resume.pdf')).exists() else 'MISSING (attachments will be skipped)'}")
    print(f"  history.db:   {'exists' if (PROJECT_ROOT / cfg.get('history_db', 'history.db')).exists() else 'will be created'}")

    print("Env:")
    print(f"  SMTP_EMAIL:         {'set' if os.environ.get('SMTP_EMAIL') else 'unset'}")
    print(f"  SMTP_APP_PASSWORD:  {'set' if os.environ.get('SMTP_APP_PASSWORD') else 'unset'}")
    print(f"  GEMINI_API_KEY:     {'set' if os.environ.get('GEMINI_API_KEY') else 'unset'}")
    print(f"  GROQ_API_KEY:       {'set' if os.environ.get('GROQ_API_KEY') else 'unset'}")

    print("Deps:")
    missing = []
    for mod, import_name in [("yaml", "PyYAML"), ("requests", "requests"), ("bs4", "beautifulsoup4")]:
        try:
            __import__(mod)
            print(f"  {import_name}: OK")
        except ImportError:
            missing.append(import_name)
            print(f"  {import_name}: MISSING (pip install {import_name})")

    if missing:
        print(f"\nInstall missing deps: pip install {' '.join(missing)}")
        return 1
    print("\nAll checks passed. Ready to run.")
    return 0


def cmd_run(args) -> int:
    _setup_logging(args.verbose)
    from .schema import load_env
    from .pipeline import run as pipeline_run

    load_env(PROJECT_ROOT)
    summary = pipeline_run(PROJECT_ROOT, force=args.force, verbose=True)
    print("\nJSON summary:")
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_fetch(args) -> int:
    """Fetch only — no drafting, no sending. Useful for testing source health."""
    _setup_logging(args.verbose)
    from .schema import load_config
    from .sources import SOURCES

    cfg = load_config(PROJECT_ROOT)
    sources_enabled = cfg.get("sources", {})
    for name, mod in SOURCES.items():
        if not sources_enabled.get(name, False):
            continue
        try:
            jobs = mod.fetch("Software Engineer", limit=5)
            print(f"\n[{name}] {len(jobs)} jobs")
            for j in jobs[:3]:
                print(f"  - {j.title} @ {j.company} ({j.location})")
                print(f"    {j.url}")
        except Exception as e:
            print(f"[{name}] ERROR: {e}")
    return 0


def cmd_report(args) -> int:
    """Print the latest daily.md."""
    from .schema import load_config
    cfg = load_config(PROJECT_ROOT)
    p = PROJECT_ROOT / cfg.get("output", {}).get("daily_report", "out/daily.md")
    if not p.exists():
        print("No daily.md yet. Run 'python -m jobhunter run --once' first.")
        return 1
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_schedule(args) -> int:
    """Install/uninstall Windows Task Scheduler job."""
    if args.uninstall:
        r = subprocess.run(
            ["schtasks", "/Delete", "/TN", "job-hunter", "/F"],
            capture_output=True, text=True,
        )
        print(r.stdout or r.stderr)
        return 0

    from .schema import load_config
    cfg = load_config(PROJECT_ROOT)
    daily_at = cfg.get("schedule", {}).get("daily_at", "07:00")

    venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        print(f"Error: virtualenv not found at {venv_python}")
        print("Run: python -m venv .venv  (see README.md)")
        return 1

    tr = f'"{venv_python}" -m jobhunter run --once'
    r = subprocess.run(
        ["schtasks", "/Create", "/SC", "DAILY", "/ST", daily_at,
         "/TN", "job-hunter", "/TR", tr, "/F"],
        capture_output=True, text=True,
    )
    print(r.stdout or r.stderr)
    return 0 if r.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhunter", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="validate config + deps")
    p_check.set_defaults(func=cmd_check)

    p_run = sub.add_parser("run", help="run the full pipeline")
    p_run.add_argument("--once", action="store_true", help="single run (default)")
    p_run.add_argument("--force", action="store_true", help="bypass dedupe (dev only)")
    p_run.add_argument("--verbose", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_fetch = sub.add_parser("fetch", help="fetch jobs from sources, no sending")
    p_fetch.add_argument("--verbose", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch)

    p_report = sub.add_parser("report", help="print today's daily.md")
    p_report.set_defaults(func=cmd_report)

    p_sched = sub.add_parser("schedule", help="install Windows Task Scheduler job")
    p_sched.add_argument("--uninstall", action="store_true")
    p_sched.set_defaults(func=cmd_schedule)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
