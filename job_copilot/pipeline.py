"""Daily run as LangGraph graphs: discover -> freshness -> de-dupe -> analyze -> score -> tailor -> (review | apply) -> report.

Four graphs, each behind a plain async function so the CLI, MCP server and API are unchanged:
  POSTING_GRAPH   analyze -> score -> (filter_out | tailor -> render -> save)
  DISCOVER_GRAPH  search -> pick -> fetch -> process (runs POSTING_GRAPH) -> pick ... until done
  APPLY_GRAPH     plan -> apply_portal (repeats per portal)
  DAILY_GRAPH     discover -> (apply, auto mode only) -> report
Per-run objects that are not part of the state (tracker, llm, portal, settings) travel in `config["configurable"]`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from job_copilot.agents import emailer, reporter
from job_copilot.agents.freshness import is_fresh
from job_copilot.agents.honesty_guard import GuardReport
from job_copilot.agents.jd_analyzer import analyze_jd
from job_copilot.agents.matcher import score_job
from job_copilot.agents.resume_builder import build_resume, tailor
from job_copilot.agents.skill_bank import SkillBank
from job_copilot.config import IST, get_profile, get_settings
from job_copilot.db import Tracker, job_id
from job_copilot.llm.chat import LLM
from job_copilot.llm.factory import get_llm
from job_copilot.models import JDAnalysis, JobPosting, JobStatus, MatchResult, TailoredContent
from job_copilot.portals.base import PortalBlocked, get_portal

log = logging.getLogger(__name__)

# The discover loop takes ~4 graph steps per job; LangGraph's default limit of 25 would stop it after a few jobs.
DISCOVER_RECURSION_LIMIT = 2000


def _llm() -> LLM | None:
    return get_llm()


def _deps(config: RunnableConfig) -> dict[str, Any]:
    return config["configurable"]


# ---------------------------------------------------------------- one posting

class PostingState(TypedDict, total=False):
    posting: JobPosting
    jid: str
    analysis: JDAnalysis
    match: MatchResult
    content: TailoredContent
    guard_report: GuardReport
    pdf: Path
    url: str
    status: JobStatus


def _analyze(state: PostingState, config: RunnableConfig) -> PostingState:
    d, posting = _deps(config), state["posting"]
    analysis = analyze_jd(posting, d["bank"], d["llm"])
    posting.recruiter_email = posting.recruiter_email or analysis.recruiter_email
    posting.recruiter_phone = posting.recruiter_phone or analysis.recruiter_phone
    return {"analysis": analysis, "jid": job_id(posting.portal, posting.external_id)}


def _score(state: PostingState, config: RunnableConfig) -> PostingState:
    d, posting = _deps(config), state["posting"]
    match = score_job(d["profile"], state["analysis"], d["bank"], d["settings"].score_weights)
    log.info("%s @ %s -> %s", posting.title, posting.company, match.explanation)
    return {"match": match}


def _route_score(state: PostingState, config: RunnableConfig) -> str:
    return "tailor" if state["match"].score >= _deps(config)["settings"].match_threshold else "filter_out"


def _filter_out(state: PostingState, config: RunnableConfig) -> PostingState:
    d, match = _deps(config), state["match"]
    d["tracker"].upsert_job(state["posting"], JobStatus.FILTERED_OUT,
                            f"score {match.score} < {d['settings'].match_threshold}",
                            analysis_json=state["analysis"], match_score=match.score, match_json=match)
    return {"status": JobStatus.FILTERED_OUT}


def _tailor(state: PostingState, config: RunnableConfig) -> PostingState:
    d = _deps(config)
    content, guard_report = tailor(d["profile"], state["posting"], state["analysis"], state["match"], d["bank"],
                                   d["llm"], d["settings"].max_keyword_repeats)
    if guard_report.violations:
        log.warning("Honesty Guard fixed %d issue(s): %s", len(guard_report.violations), guard_report.violations)
    return {"content": content, "guard_report": guard_report}


async def _render(state: PostingState, config: RunnableConfig) -> PostingState:
    pdf, url = await build_resume(_deps(config)["profile"], state["posting"], state["content"], state["jid"],
                                  state["analysis"], state["guard_report"])
    return {"pdf": pdf, "url": url}


def _save(state: PostingState, config: RunnableConfig) -> PostingState:
    d, posting, match = _deps(config), state["posting"], state["match"]
    status = JobStatus.APPROVED if d["settings"].mode == "auto" else JobStatus.PENDING_REVIEW
    d["tracker"].upsert_job(posting, status, "auto mode" if status == JobStatus.APPROVED else "awaiting your review",
                            analysis_json=state["analysis"], match_score=match.score, match_json=match,
                            resume_path=str(state["pdf"]), resume_url=state["url"], guard_json=state["guard_report"])
    if d["settings"].email_each_resume:
        emailer.send_email(f"Tailored resume: {posting.title} @ {posting.company}",
                           f"{match.explanation}\n\n{posting.url}", [state["pdf"]])
    return {"status": status}


def _build_posting_graph():
    g = StateGraph(PostingState)
    g.add_node("analyze", _analyze)
    g.add_node("score", _score)
    g.add_node("filter_out", _filter_out)
    g.add_node("tailor", _tailor)
    g.add_node("render", _render)
    g.add_node("save", _save)
    g.add_edge(START, "analyze")
    g.add_edge("analyze", "score")
    g.add_conditional_edges("score", _route_score, ["filter_out", "tailor"])
    g.add_edge("tailor", "render")
    g.add_edge("render", "save")
    g.add_edge("filter_out", END)
    g.add_edge("save", END)
    return g.compile()


POSTING_GRAPH = _build_posting_graph()


async def process_posting(posting: JobPosting, tracker: Tracker, llm: LLM | None) -> tuple[str, JobStatus]:
    """Analyze, score and (if good enough) tailor a resume for a posting that has its description."""
    profile = get_profile()
    config = {"configurable": {"tracker": tracker, "llm": llm, "profile": profile, "bank": SkillBank(profile),
                               "settings": get_settings()}}
    final = await POSTING_GRAPH.ainvoke({"posting": posting}, config=config)
    return final["jid"], final["status"]


# ---------------------------------------------------------------- discovery

class DiscoverState(TypedDict, total=False):
    cards: list[JobPosting]
    index: int          # next card to look at
    processed: int      # job pages fully processed, counted against the limit
    current: JobPosting | None
    posting: JobPosting | None
    blocked: bool
    stats: dict[str, int]


async def _search(state: DiscoverState, config: RunnableConfig) -> DiscoverState:
    d = _deps(config)
    settings, profile, portal = d["settings"], d["profile"], d["portal"]
    stats = dict(state["stats"])
    seen: set[str] = set()
    cards: list[JobPosting] = []
    for role in profile.preferences.roles:
        for location in profile.preferences.locations or [""]:
            try:
                found = await portal.search(role, location, settings.max_job_age_days, profile.total_experience_years)
            except PortalBlocked as e:
                log.error("%s", e)
                stats["errors"] += 1
                continue
            for c in found:
                if c.external_id not in seen:
                    seen.add(c.external_id)
                    cards.append(c)
    stats["found"] = len(cards)
    return {"cards": cards, "index": 0, "processed": 0, "stats": stats}


def _pick(state: DiscoverState, config: RunnableConfig) -> DiscoverState:
    """Advance to the next card that survives the blacklist, de-dupe and search-card freshness checks."""
    d = _deps(config)
    settings, tracker = d["settings"], d["tracker"]
    blacklist = {c.lower() for c in settings.blacklist_companies}
    stats, index, cards = dict(state["stats"]), state["index"], state["cards"]
    while index < len(cards) and state["processed"] < d["limit"]:
        card, index = cards[index], index + 1
        if card.company.lower() in blacklist:
            stats["blacklisted"] += 1
        elif tracker.is_known(card):
            stats["duplicate"] += 1
        elif _fresh_enough(card, tracker, stats, settings):  # freshness check 1: search card date
            return {"current": card, "index": index, "stats": stats}
    return {"current": None, "index": index, "stats": stats}


def _route_pick(state: DiscoverState) -> str:
    return END if state["current"] is None else "fetch"


async def _fetch(state: DiscoverState, config: RunnableConfig) -> DiscoverState:
    d, card = _deps(config), state["current"]
    stats = dict(state["stats"])
    try:
        posting = await d["portal"].fetch_details(card)
        # freshness check 2: the job page's own posted date
        if not _fresh_enough(posting, d["tracker"], stats, d["settings"]):
            posting = None
    except PortalBlocked as e:
        log.error("%s", e)
        stats["errors"] += 1
        return {"posting": None, "blocked": True, "stats": stats}
    except Exception:
        log.exception("Failed to process %s", card.url)
        stats["errors"] += 1
        posting = None
    return {"posting": posting, "stats": stats}


def _route_fetch(state: DiscoverState) -> str:
    if state.get("blocked"):
        return END
    return "pick" if state["posting"] is None else "process"


async def _process(state: DiscoverState, config: RunnableConfig) -> DiscoverState:
    d, posting = _deps(config), state["posting"]
    stats = dict(state["stats"])
    try:
        _, status = await process_posting(posting, d["tracker"], d["llm"])
        stats[str(status)] += 1
    except Exception:
        log.exception("Failed to process %s", posting.url)
        stats["errors"] += 1
    return {"processed": state["processed"] + 1, "stats": stats}


def _build_discover_graph():
    g = StateGraph(DiscoverState)
    g.add_node("search", _search)
    g.add_node("pick", _pick)
    g.add_node("fetch", _fetch)
    g.add_node("process", _process)
    g.add_edge(START, "search")
    g.add_edge("search", "pick")
    g.add_conditional_edges("pick", _route_pick, ["fetch", END])
    g.add_conditional_edges("fetch", _route_fetch, ["pick", "process", END])
    g.add_edge("process", "pick")
    return g.compile()


DISCOVER_GRAPH = _build_discover_graph()


async def discover(source: str = "naukri", limit: int | None = None) -> dict:
    """Search the portal, filter and process new jobs. Returns counts."""
    settings, profile = get_settings(), get_profile()
    stats = {"found": 0, "stale": 0, "undated": 0, "duplicate": 0, "blacklisted": 0,
             "filtered_out": 0, "pending_review": 0, "approved": 0, "errors": 0}
    async with get_portal(source, settings.portals.get(source)) as portal:
        config = {"recursion_limit": DISCOVER_RECURSION_LIMIT,
                  "configurable": {"tracker": Tracker(), "llm": _llm(), "portal": portal, "profile": profile,
                                   "settings": settings, "limit": limit or settings.max_jobs_per_run}}
        final = await DISCOVER_GRAPH.ainvoke({"stats": stats}, config=config)
    return final["stats"]


def _fresh_enough(p: JobPosting, tracker: Tracker, stats: dict, settings) -> bool:
    fresh = is_fresh(p.posted_at, settings.max_job_age_days)
    if fresh is False:
        stats["stale"] += 1
        tracker.upsert_job(p, JobStatus.FILTERED_OUT, f"posted '{p.posted_text}' (> {settings.max_job_age_days} days)")
        return False
    if fresh is None and settings.skip_if_date_unknown:
        stats["undated"] += 1
        tracker.upsert_job(p, JobStatus.FILTERED_OUT, "posting date unknown")
        return False
    return True


# ---------------------------------------------------------------- applying

class ApplyState(TypedDict, total=False):
    approved: int
    batches: list[tuple[str, list[dict]]]   # (portal name, jobs to apply to), already trimmed to the caps
    results: list[dict]
    remaining: int


def _plan(state: ApplyState, config: RunnableConfig) -> ApplyState:
    d = _deps(config)
    settings, tracker = d["settings"], d["tracker"]
    approved = tracker.list(status=str(JobStatus.APPROVED))
    remaining = settings.max_applications_per_day - tracker.applied_count_today()
    if d["max_count"] is not None:
        remaining = min(remaining, d["max_count"])
    by_portal: dict[str, list[dict]] = {}
    for j in approved:
        by_portal.setdefault(j["portal"], []).append(j)
    batches = []
    for portal_name, jobs in by_portal.items():
        portal_left = settings.per_portal_cap - tracker.applied_count_today(portal_name)
        if batch := jobs[: max(0, min(remaining, portal_left))]:
            batches.append((portal_name, batch))
    return {"approved": len(approved), "batches": batches, "results": [], "remaining": remaining}


def _route_plan(state: ApplyState) -> str:
    return "apply_portal" if state["batches"] else END


async def _apply_portal(state: ApplyState, config: RunnableConfig) -> ApplyState:
    """Apply to the first pending portal's batch in one browser session."""
    d = _deps(config)
    (portal_name, batch), *rest = state["batches"]
    results, remaining = list(state["results"]), state["remaining"]
    async with get_portal(portal_name, d["settings"].portals.get(portal_name)) as portal:
        for j in batch:
            try:
                status, reason = await portal.apply(j["url"])
            except Exception as e:  # keep going with the rest
                log.exception("Apply failed for %s", j["url"])
                status, reason = JobStatus.FAILED, str(e)[:200]
            d["tracker"].set_status(j["id"], status, reason)
            results.append({"id": j["id"], "title": j["title"], "company": j["company"],
                            "status": str(status), "reason": reason})
            if status == JobStatus.APPLIED:
                remaining -= 1
    return {"batches": rest, "results": results, "remaining": remaining}


def _route_apply(state: ApplyState) -> str:
    return "apply_portal" if state["batches"] else END


def _build_apply_graph():
    g = StateGraph(ApplyState)
    g.add_node("plan", _plan)
    g.add_node("apply_portal", _apply_portal)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", _route_plan, ["apply_portal", END])
    g.add_conditional_edges("apply_portal", _route_apply, ["apply_portal", END])
    return g.compile()


APPLY_GRAPH = _build_apply_graph()


async def apply_approved(max_count: int | None = None) -> dict:
    """Apply to approved jobs, respecting daily and per-portal caps."""
    config = {"configurable": {"tracker": Tracker(), "settings": get_settings(), "max_count": max_count}}
    final = await APPLY_GRAPH.ainvoke({}, config=config)
    results, remaining = final["results"], final["remaining"]
    return {"attempted": len(results), "results": results,
            "remaining_today": max(0, remaining), "cap_reached": remaining <= 0 and final["approved"] > len(results)}


# ---------------------------------------------------------------- report and daily run

def daily_report(send: bool = True) -> dict:
    tracker = Tracker()
    today = datetime.now(IST).date().isoformat()
    jobs = tracker.list(since=today, limit=1000)
    path = reporter.build_report(jobs)
    body = reporter.summary_text(jobs)
    sent = False
    if send and get_settings().send_daily_report:
        sent = emailer.send_email(f"Job Copilot daily report - {today}", body, [path])
    return {"report_path": str(path), "emailed": sent, "summary": body}


class DailyState(TypedDict, total=False):
    discovery: dict
    apply: dict | None
    report: dict


async def _daily_discover(state: DailyState, config: RunnableConfig) -> DailyState:
    d = _deps(config)
    return {"discovery": await discover(d["source"], d["limit"])}


def _route_daily(state: DailyState) -> str:
    return "apply" if get_settings().mode == "auto" else "report"


async def _daily_apply(state: DailyState) -> DailyState:
    return {"apply": await apply_approved()}


def _daily_report(state: DailyState, config: RunnableConfig) -> DailyState:
    return {"report": daily_report(send=_deps(config)["send_report"])}


def _build_daily_graph():
    g = StateGraph(DailyState)
    g.add_node("discover", _daily_discover)
    g.add_node("apply", _daily_apply)
    g.add_node("report", _daily_report)
    g.add_edge(START, "discover")
    # Only auto mode applies by itself; in review mode you approve first (human-in-the-loop).
    g.add_conditional_edges("discover", _route_daily, ["apply", "report"])
    g.add_edge("apply", "report")
    g.add_edge("report", END)
    return g.compile()


DAILY_GRAPH = _build_daily_graph()


async def run_daily(source: str = "naukri", limit: int | None = None, send_report: bool = True) -> dict:
    config = {"configurable": {"source": source, "limit": limit, "send_report": send_report}}
    final = await DAILY_GRAPH.ainvoke({"apply": None}, config=config)
    return {"discovery": final["discovery"], "apply": final["apply"], "report": final["report"]}
