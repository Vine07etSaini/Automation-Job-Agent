"""Daily run: discover -> freshness -> de-dupe -> analyze -> score -> tailor -> (review | apply) -> report."""

from __future__ import annotations

import logging
from datetime import datetime

from job_copilot.agents import emailer, reporter
from job_copilot.agents.freshness import is_fresh
from job_copilot.agents.jd_analyzer import analyze_jd
from job_copilot.agents.matcher import score_job
from job_copilot.agents.resume_builder import build_resume, tailor
from job_copilot.agents.skill_bank import SkillBank
from job_copilot.config import IST, get_profile, get_settings
from job_copilot.db import Tracker, job_id
from job_copilot.llm.gemini import Gemini, gemini_available
from job_copilot.models import JobPosting, JobStatus
from job_copilot.portals.base import PortalBlocked, get_portal

log = logging.getLogger(__name__)


def _llm() -> Gemini | None:
    return Gemini() if gemini_available() else None


async def process_posting(posting: JobPosting, tracker: Tracker, llm: Gemini | None) -> tuple[str, JobStatus]:
    """Analyze, score and (if good enough) tailor a resume for a posting that has its description."""
    settings, profile = get_settings(), get_profile()
    bank = SkillBank(profile)
    jid = job_id(posting.portal, posting.external_id)

    analysis = analyze_jd(posting, bank, llm)
    posting.recruiter_email = posting.recruiter_email or analysis.recruiter_email
    posting.recruiter_phone = posting.recruiter_phone or analysis.recruiter_phone
    match = score_job(profile, analysis, bank, settings.score_weights)
    log.info("%s @ %s -> %s", posting.title, posting.company, match.explanation)

    if match.score < settings.match_threshold:
        tracker.upsert_job(posting, JobStatus.FILTERED_OUT, f"score {match.score} < {settings.match_threshold}",
                           analysis_json=analysis, match_score=match.score, match_json=match)
        return jid, JobStatus.FILTERED_OUT

    content, guard_report = tailor(profile, posting, analysis, match, bank, llm, settings.max_keyword_repeats)
    if guard_report.violations:
        log.warning("Honesty Guard fixed %d issue(s): %s", len(guard_report.violations), guard_report.violations)
    pdf, url = await build_resume(profile, posting, content, jid, analysis, guard_report)

    status = JobStatus.APPROVED if settings.mode == "auto" else JobStatus.PENDING_REVIEW
    tracker.upsert_job(posting, status, "auto mode" if status == JobStatus.APPROVED else "awaiting your review",
                       analysis_json=analysis, match_score=match.score, match_json=match,
                       resume_path=str(pdf), resume_url=url, guard_json=guard_report)
    if settings.email_each_resume:
        emailer.send_email(f"Tailored resume: {posting.title} @ {posting.company}",
                           f"{match.explanation}\n\n{posting.url}", [pdf])
    return jid, status


async def discover(source: str = "naukri", limit: int | None = None) -> dict:
    """Search the portal, filter and process new jobs. Returns counts."""
    settings, profile = get_settings(), get_profile()
    tracker, llm = Tracker(), _llm()
    blacklist = {c.lower() for c in settings.blacklist_companies}
    limit = limit or settings.max_jobs_per_run
    stats = {"found": 0, "stale": 0, "undated": 0, "duplicate": 0, "blacklisted": 0,
             "filtered_out": 0, "pending_review": 0, "approved": 0, "errors": 0}

    async with get_portal(source, settings.portals.get(source)) as portal:
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

        processed = 0
        for card in cards:
            if processed >= limit:
                break
            if card.company.lower() in blacklist:
                stats["blacklisted"] += 1
                continue
            if tracker.is_known(card):
                stats["duplicate"] += 1
                continue
            # freshness check 1: search card date
            if not _fresh_enough(card, tracker, stats, settings):
                continue
            try:
                posting = await portal.fetch_details(card)
                # freshness check 2: the job page's own posted date
                if not _fresh_enough(posting, tracker, stats, settings):
                    continue
                processed += 1
                _, status = await process_posting(posting, tracker, llm)
                stats[str(status)] += 1
            except PortalBlocked as e:
                log.error("%s", e)
                stats["errors"] += 1
                break
            except Exception:
                log.exception("Failed to process %s", card.url)
                stats["errors"] += 1
    return stats


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


async def apply_approved(max_count: int | None = None) -> dict:
    """Apply to approved jobs, respecting daily and per-portal caps."""
    settings, tracker = get_settings(), Tracker()
    approved = tracker.list(status=str(JobStatus.APPROVED))
    remaining = settings.max_applications_per_day - tracker.applied_count_today()
    if max_count is not None:
        remaining = min(remaining, max_count)
    results = []
    by_portal: dict[str, list[dict]] = {}
    for j in approved:
        by_portal.setdefault(j["portal"], []).append(j)

    for portal_name, jobs in by_portal.items():
        portal_left = settings.per_portal_cap - tracker.applied_count_today(portal_name)
        batch = jobs[: max(0, min(remaining, portal_left))]
        if not batch:
            continue
        async with get_portal(portal_name, settings.portals.get(portal_name)) as portal:
            for j in batch:
                try:
                    status, reason = await portal.apply(j["url"])
                except Exception as e:  # keep going with the rest
                    log.exception("Apply failed for %s", j["url"])
                    status, reason = JobStatus.FAILED, str(e)[:200]
                tracker.set_status(j["id"], status, reason)
                results.append({"id": j["id"], "title": j["title"], "company": j["company"],
                                "status": str(status), "reason": reason})
                if status == JobStatus.APPLIED:
                    remaining -= 1
    return {"attempted": len(results), "results": results,
            "remaining_today": max(0, remaining), "cap_reached": remaining <= 0 and len(approved) > len(results)}


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


async def run_daily(source: str = "naukri", limit: int | None = None, send_report: bool = True) -> dict:
    stats = await discover(source, limit)
    applied = await apply_approved() if get_settings().mode == "auto" else None
    report = daily_report(send=send_report)
    return {"discovery": stats, "apply": applied, "report": report}
