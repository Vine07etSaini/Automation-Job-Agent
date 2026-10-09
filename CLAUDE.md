# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AI Job Copilot: a Python 3.11+ (3.13 recommended) CLI that searches job portals, scores postings against the user's profile, tailors a resume PDF per job with Gemini, applies (review or auto mode), and emails a daily Excel report. Phase 1 supports only the `naukri` portal (Playwright) plus an offline `sample` portal; Indeed/LinkedIn/InstaHire, new-project approvals, recruiter emails and profile sync described in README.md are roadmap items, not implemented.

## Commands

Windows environment; the venv lives in `.venv`.

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

python main.py check                              # validate config, Gemini key, SMTP
python main.py run --source sample --no-email     # full pipeline offline on data/sample_jobs.json
python main.py run                                # real Naukri run (needs `python main.py login naukri` once)
python main.py review | apply --max 5 | jobs --status pending_review | report --send
python main.py chat | ask "..."                   # Gemini agent driving the MCP tools
python main.py mcp                                # MCP server over stdio (also: python -m job_copilot.mcp_server)
python main.py -v <cmd>                           # debug logging

pytest                                            # all tests (tests/test_core.py)
pytest tests/test_core.py::test_guard_blocks_invented_skills
```

No linter/formatter is configured.

## Architecture

- **Entry points**: `main.py` → Typer app in `job_copilot/cli.py`. CLI commands import heavy modules lazily inside each command. `mcp_server.py` exposes the same operations as MCP tools; `gemini_agent.py` spawns that MCP server as a stdio subprocess and hands the `ClientSession` to google-genai as a tool (automatic function calling).
- **Pipeline** (`pipeline.py`): `discover` → per role×location `portal.search` → blacklist / `tracker.is_known` dedupe → freshness check on the search card → `portal.fetch_details` → freshness check again on the job page → `process_posting` (analyze JD → score → if ≥ `match_threshold`: tailor + Honesty Guard + build PDF → `PENDING_REVIEW`, or `APPROVED` in auto mode). `apply_approved` enforces `max_applications_per_day` and `per_portal_cap`. `run_daily` = discover + (auto-mode apply) + report.
- **Agents** (`job_copilot/agents/`) are plain functions. `SkillBank` (built from `profile.yaml` skills/aliases, with learning goals tracked separately) is the canonical skill lookup used by the analyzer, matcher and guard.
- **Honesty Guard** (`agents/honesty_guard.py`) is the core safety invariant: every LLM-tailored `TailoredContent` passes through `guard()`, which drops skills not in the bank, reverts (never "fixes") summaries/project rewrites that introduce unverified JD skills or exceed `max_keyword_repeats`, and only allows rewriting `on_resume` projects. Any new path that writes resume/profile content (including the MCP `update_project_description` tool) must go through these checks. Experience and education are read-only.
- **LLM optional**: `llm/gemini.py` wraps google-genai with structured JSON output (`generate_json(prompt, PydanticModel)`) and retries. When `GEMINI_API_KEY` is unset, functions receive `llm=None` and fall back to heuristics (`heuristic_analysis` in jd_analyzer, reorder-only tailoring in resume_builder) — keep that fallback working since tests and `--source sample` rely on it.
- **Portals** (`portals/base.py`): abstract `Portal` with `search`, `fetch_details`, `apply` (returns `(JobStatus, reason)`), used as a context manager; raise `PortalBlocked` on captcha/denial. Register new portals in `get_portal()`. Naukri uses a persistent browser session in `storage/browser/` and runs non-headless by default.
- **Persistence**: `db.py` `Tracker` is a single SQLite `jobs` table (`storage/tracker.db`). Job id = sha1(portal:external_id); cross-portal dedupe uses a `fingerprint` of normalized company+title. Pydantic models passed as kwargs to `upsert_job`/`update` are JSON-encoded into the `*_json` columns. Status lifecycle is `JobStatus` in `models.py`.
- **Resumes**: `templates/resume.html.j2` rendered with Jinja2, printed to PDF via Playwright Chromium; each PDF gets a sibling JSON "vault" file (job, analysis, guard report, diff) in `storage/resumes/`. The resume URL is currently a local file link.
- **Config** (`config.py`): `config/profile.yaml` is the single source of truth for the user's data; `config/settings.yaml` is validated into `Settings`. `get_settings()` is `lru_cache`d, `get_profile()` is deliberately not (the agent can edit project descriptions at runtime via `save_profile`). All timestamps use the fixed `IST` timezone.
- **Async Playwright only**: portals, `html_to_pdf`/`build_resume` and `pipeline.discover`/`process_posting`/`apply_approved`/`run_daily` are `async` and use `playwright.async_api`. CLI commands wrap them in `asyncio.run`; MCP tools `await` them. Don't reintroduce `sync_playwright`, because it fails inside the MCP server's event loop.
- **Logging goes to stderr only** — stdout is reserved for the MCP stdio protocol; never `print` to stdout from code reachable by the MCP server.
