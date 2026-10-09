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
python main.py import-resume resume.pdf         # draft profile from a resume PDF -> config/profile.imported.yaml
python main.py import-resume --apply            # promote the reviewed draft to profile.yaml (backup kept)
python main.py chat | ask "..."                   # Gemini agent driving the MCP tools
python main.py mcp                                # MCP server over stdio (also: python -m job_copilot.mcp_server)
python main.py -v <cmd>                           # debug logging

pytest                                            # all tests (tests/test_core.py)
pytest tests/test_core.py::test_guard_blocks_invented_skills
```

Web UI (separate learning notes in `backend/LEARNING.md` and `frontend/LEARNING.md`; update them when changing those folders):

```powershell
pip install -r backend
equirements.txt
python -m backend.run            # FastAPI on :8000 (docs at /docs); not uvicorn --reload (Playwright needs the Proactor loop)
pytest backend                   # API tests
cd frontend; npm install; npm run dev   # React + Zustand on :5173, proxies /api to :8000
```

No linter/formatter is configured. Coding standards live in the `coding-standards` skill (`.claude/skills/coding-standards/SKILL.md`); follow it for any code change.

This is the user's learning project: after each meaningful change, update `LEARNING.md` (add or tick items in the revision checklist, add a dated log entry with the lesson, note open questions). Explain the "why" briefly when introducing a new concept.

## Architecture

- **Entry points**: `main.py` → Typer app in `job_copilot/cli.py`. CLI commands import heavy modules lazily inside each command. `mcp_server.py` exposes the same operations as MCP tools; `gemini_agent.py` spawns that MCP server as a stdio subprocess and hands the `ClientSession` to google-genai as a tool (automatic function calling).
- **Pipeline** (`pipeline.py`) is four LangGraph `StateGraph`s behind plain async functions (`process_posting`, `discover`, `apply_approved`, `run_daily`), so CLI/MCP/API callers are unchanged. `POSTING_GRAPH`: analyze → score → (filter_out | tailor + Honesty Guard → render PDF → save as `PENDING_REVIEW`, or `APPROVED` in auto mode). `DISCOVER_GRAPH`: search → pick (blacklist / `tracker.is_known` dedupe / freshness check on the search card) → fetch (`portal.fetch_details` + freshness check on the job page) → process (runs `POSTING_GRAPH`) → pick … until the limit. `APPLY_GRAPH`: plan (enforces `max_applications_per_day` and `per_portal_cap`) → apply_portal, repeated per portal. `DAILY_GRAPH` = discover → (auto-mode apply only) → report. Nodes return state updates (never mutate state); per-run objects (tracker, llm, portal, settings) go in `config["configurable"]`; the discover loop sets a high `recursion_limit` because LangGraph's default of 25 steps is too low.
- **Agents** (`job_copilot/agents/`) are plain functions. `SkillBank` (built from `profile.yaml` skills/aliases, with learning goals tracked separately) is the canonical skill lookup used by the analyzer, matcher and guard.
- **Computer-use agent** (`agents/cua.py`): opt-in via `apply_engine: cua` in settings (default `script`). After Apply opens the Naukri screening drawer, Gemini (Interactions API, `computer_use` tool, 0-999 coordinates) drives the page from screenshots; `execute_action` maps actions to Playwright and is the safety boundary: step cap `cua_max_steps`, host must stay naukri.com, `navigate`/back/forward excluded, any `safety_decision` confirmation stops the run (never auto-confirmed), and `type` only accepts text returned by the `lookup_answer` tool, which is backed by `agents/screening.answer_question` (profile facts only). No answer -> stops with `NEEDS_MANUAL`. Falls back to the scripted drawer handler when the key is missing. Preview feature: test on one approved job first; screenshots are saved in `storage/cua/`.
- **Honesty Guard** (`agents/honesty_guard.py`) is the core safety invariant: every LLM-tailored `TailoredContent` passes through `guard()`, which drops skills not in the bank, reverts (never "fixes") summaries/project rewrites that introduce unverified JD skills or exceed `max_keyword_repeats`, and only allows rewriting `on_resume` projects. Any new path that writes resume/profile content (including the MCP `update_project_description` tool) must go through these checks. Experience and education are read-only.
- **LLM optional, provider-neutral**: agents take `llm: LLM | None` (`llm/chat.py` Protocol with `generate_json(prompt, PydanticModel)` / `generate_text`). `llm/factory.get_llm()` picks the provider from `LLM_PROVIDER` in .env (`auto` = first configured of Gemini key, Groq key, `OLLAMA_MODEL`; or `gemini|groq|ollama|none`). `llm/gemini.py` wraps google-genai; Groq and Ollama share `ChatCompletionsLLM` (OpenAI-compatible `/chat/completions`, JSON mode + schema in the prompt, one validation retry). `llm/providers.py` holds the read-only checks behind `python main.py check` (Groq key/models, downloaded Ollama models). `chat`/`ask` and the CUA stay Gemini-only. When no provider is configured, functions receive `llm=None` and fall back to heuristics (`heuristic_analysis` in jd_analyzer, reorder-only tailoring in resume_builder) — keep that fallback working since tests and `--source sample` rely on it.
- **Portals** (`portals/base.py`): abstract `Portal` with `search`, `fetch_details`, `apply` (returns `(JobStatus, reason)`), used as a context manager; raise `PortalBlocked` on captcha/denial. Register new portals in `get_portal()`. Naukri uses a persistent browser session in `storage/browser/` and runs non-headless by default; when `apply` hits "Login to apply" it calls `NaukriPortal.login()` with `NAUKRI_EMAIL`/`NAUKRI_PASSWORD` (`config.get_portal_credentials`), at most once per session, and falls back to `NEEDS_MANUAL` on OTP/captcha.
- **Persistence**: `db.py` `Tracker` is a single SQLite `jobs` table (`storage/tracker.db`). Job id = sha1(portal:external_id); cross-portal dedupe uses a `fingerprint` of normalized company+title. Pydantic models passed as kwargs to `upsert_job`/`update` are JSON-encoded into the `*_json` columns. Status lifecycle is `JobStatus` in `models.py`.
- **Resumes**: `templates/resume.html.j2` rendered with Jinja2, printed to PDF via Playwright Chromium; each PDF gets a sibling JSON "vault" file (job, analysis, guard report, diff) in `storage/resumes/`. The resume URL is currently a local file link.
- **Resume import** (`agents/profile_importer.py`, `import-resume`): pypdf text + hyperlink URLs -> Gemini -> `ResumeExtract` -> `verify_skills` drops any skill not literally in the PDF text -> `merge_into_profile` keeps `preferences`/`equivalents`/`learning_goals` and never infers skill years. Writes a draft first; `--apply` promotes the (user-edited) draft. No offline fallback and no MCP tool by design: it rewrites locked sections, so it is user-run only.
- **Config** (`config.py`): `config/profile.yaml` is the single source of truth for the user's data; `config/settings.yaml` is validated into `Settings`. `get_settings()` is `lru_cache`d, `get_profile()` is deliberately not (the agent can edit project descriptions at runtime via `save_profile`). All timestamps use the fixed `IST` timezone.
- **Async Playwright only**: portals, `html_to_pdf`/`build_resume` and `pipeline.discover`/`process_posting`/`apply_approved`/`run_daily` are `async` and use `playwright.async_api`. CLI commands wrap them in `asyncio.run`; MCP tools `await` them. Don't reintroduce `sync_playwright`, because it fails inside the MCP server's event loop.
- **Logging goes to stderr only** — stdout is reserved for the MCP stdio protocol; never `print` to stdout from code reachable by the MCP server.
