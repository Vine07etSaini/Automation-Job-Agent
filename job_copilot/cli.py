"""Command-line interface:  python main.py --help"""

from __future__ import annotations

import asyncio
import json
import os
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from job_copilot.config import setup_logging

app = typer.Typer(add_completion=False, no_args_is_help=True, help="AI Job Copilot - Phase 1 MVP")
console = Console()


@app.callback()
def _init(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    import logging

    setup_logging(logging.DEBUG if verbose else logging.INFO)


@app.command()
def check() -> None:
    """Validate config, profile, Gemini API key and email settings."""
    from job_copilot.config import get_email_config, get_profile, get_settings
    from job_copilot.llm.gemini import Gemini, gemini_available, model_name

    profile, settings = get_profile(), get_settings()
    console.print(f"[green]OK[/] profile: {profile.personal.name}, {len(profile.skills)} skills, "
                  f"{len(profile.projects)} projects")
    console.print(f"[green]OK[/] settings: mode={settings.mode}, threshold={settings.match_threshold}, "
                  f"max_age={settings.max_job_age_days}d")
    if not gemini_available():
        console.print("[red]MISSING[/] GEMINI_API_KEY in .env (LLM features will use offline heuristics)")
    else:
        try:
            reply = Gemini().generate_text("Reply with the single word: ready")
            console.print(f"[green]OK[/] Gemini {model_name()}: {reply.strip()[:40]}")
        except Exception as e:
            console.print(f"[red]FAIL[/] Gemini {model_name()}: {e}")
    email = get_email_config()
    console.print(f"[green]OK[/] email -> {email.to}" if email.configured
                  else "[yellow]SKIP[/] email not configured (reports saved locally only)")


@app.command()
def run(source: str = typer.Option("naukri", help="naukri | sample"),
        limit: int = typer.Option(None, help="Max job pages to open"),
        email: bool = typer.Option(True, help="Email the daily report")) -> None:
    """Daily run: discover, filter, tailor resumes, (auto-apply), report."""
    from job_copilot import pipeline

    result = asyncio.run(pipeline.run_daily(source, limit, send_report=email))
    console.print_json(json.dumps(result["discovery"]))
    if result["apply"]:
        console.print_json(json.dumps(result["apply"]))
    console.print(result["report"]["summary"])
    console.print(f"Report: {result['report']['report_path']}")


@app.command()
def jobs(status: str = typer.Option(None, help="Filter by status"), limit: int = 30) -> None:
    """List tracked jobs."""
    from job_copilot.db import Tracker

    t = Table("id", "score", "status", "title", "company", "portal", "why")
    for j in Tracker().list(status=status, limit=limit):
        why = (j.get("match") or {}).get("explanation") or j["status_reason"] or ""
        t.add_row(j["id"], str(j["match_score"] or "-"), j["status"], j["title"], j["company"], j["portal"], why)
    console.print(t)


@app.command()
def review() -> None:
    """Review pending jobs one by one: approve, reject, skip or open the resume."""
    from job_copilot.db import Tracker
    from job_copilot.models import JobStatus

    tracker = Tracker()
    pending = tracker.list(status=str(JobStatus.PENDING_REVIEW))
    if not pending:
        console.print("Nothing to review.")
        return
    for j in pending:
        console.rule(f"[bold]{j['title']}[/] @ {j['company']}  ({j['portal']}, score {j['match_score']})")
        console.print((j.get("match") or {}).get("explanation", ""))
        console.print(f"Job: {j['url']}\nResume: {j['resume_path']}")
        if (g := j.get("guard")) and g.get("violations"):
            console.print(f"[yellow]Honesty Guard fixed:[/] {'; '.join(g['violations'])}")
        while True:
            choice = typer.prompt("[a]pprove / [r]eject / [s]kip / [o]pen resume / [j]ob page / [q]uit",
                                  default="s").lower()[:1]
            if choice == "o" and j["resume_path"]:
                os.startfile(j["resume_path"]) if os.name == "nt" else webbrowser.open(Path(j["resume_path"]).as_uri())
            elif choice == "j":
                webbrowser.open(j["url"])
            elif choice == "a":
                tracker.set_status(j["id"], JobStatus.APPROVED, "approved by user")
                console.print("[green]approved[/]")
                break
            elif choice == "r":
                tracker.set_status(j["id"], JobStatus.REJECTED, typer.prompt("reason", default="not interested"))
                console.print("[red]rejected[/]")
                break
            elif choice == "q":
                return
            else:
                break
    console.print("Apply to approved jobs with:  python main.py apply")


@app.command()
def apply(max_count: int = typer.Option(None, "--max", help="Max applications this run")) -> None:
    """Apply to approved jobs (respects daily and per-portal caps)."""
    from job_copilot import pipeline

    console.print_json(json.dumps(asyncio.run(pipeline.apply_approved(max_count))))


@app.command()
def report(send: bool = typer.Option(False, help="Email the report")) -> None:
    """Build today's Excel report."""
    from job_copilot import pipeline

    r = pipeline.daily_report(send=send)
    console.print(r["summary"])
    console.print(f"Report: {r['report_path']}  (emailed: {r['emailed']})")


@app.command()
def login(portal: str = typer.Argument("naukri")) -> None:
    """Log in to a portal once in a visible browser; the session is reused later."""
    if portal != "naukri":
        raise typer.BadParameter("Only 'naukri' is supported in Phase 1")
    from job_copilot.portals.naukri import interactive_login

    asyncio.run(interactive_login())
    console.print("Session saved.")


@app.command()
def chat() -> None:
    """Chat with the Gemini agent (it uses the MCP tools)."""
    from job_copilot.gemini_agent import chat as run_chat

    asyncio.run(run_chat())


@app.command()
def ask(question: str) -> None:
    """Ask the Gemini agent a single question, e.g. ask "show my pending jobs"."""
    from rich.markdown import Markdown

    from job_copilot.gemini_agent import ask as run_ask

    console.print(Markdown(asyncio.run(run_ask(question))))


@app.command("mcp")
def mcp_serve() -> None:
    """Run the MCP server over stdio (for Claude Desktop, Claude Code, Cursor, ...)."""
    from job_copilot.mcp_server import server

    server.run()
